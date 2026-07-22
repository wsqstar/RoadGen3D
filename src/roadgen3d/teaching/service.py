"""Application service enforcing tenant boundaries and reproducible project state."""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import platform
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from roadgen3d.services.osm_scene_source import fetch_normalized_osm_scene_source, osm_scene_source_response
from roadgen3d.services.asset_manifest_registry import (
    AssetManifestConflictError,
    AssetReferenceError,
    resolve_registered_asset,
)
from roadgen3d.services.osm_road_study import (
    build_osm_road_preview,
    preview_bundle_from_raw,
    select_osm_road_study_area,
)
from roadgen3d.scene_sources import normalize_scene_source
from roadgen3d.scene_layout_edits import apply_scene_layout_edits, scene_revision_for_layout
from roadgen3d.web_viewer_dev import build_layout_manifest_payload

from .artifacts import ArtifactStore, create_artifact_store, safe_object_key
from .auth import digest_secret, hash_password, issue_invite_code, issue_session_token, normalize_email, verify_password
from .database import TeachingDatabase
from .geojson_pipeline import normalize_teaching_geojson, round_trip_report
from .models import (
    Artifact,
    AuditLog,
    AuthSession,
    Course,
    EvaluationProfile,
    EvaluationRun,
    Job,
    Membership,
    Project,
    RegistrationInvite,
    SceneRevisionRecord,
    SceneSourceRecord,
    User,
    new_id,
    now_utc,
)


logger = logging.getLogger(__name__)


PUBLIC_SCENE_GENERATION_FAILURE_MESSAGE = (
    "3D场景生成失败。你的地图和2D标注已经保存，可以重试或返回检查标注。"
)


class TeachingError(RuntimeError):
    status_code = 400
    code = "teaching_error"

    def detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self)}


class NotFound(TeachingError):
    status_code = 404
    code = "not_found"


class Forbidden(TeachingError):
    status_code = 403
    code = "forbidden"


class Conflict(TeachingError):
    status_code = 409
    code = "conflict"


def public_job_failure(
    exc: Exception,
    *,
    job_id: str,
    kind: str,
    attempt: int,
) -> dict[str, Any]:
    """Map an internal exception to the stable student-facing failure contract."""

    debug_reference = f"RG3D-{str(job_id)[:8].upper()}-{max(1, int(attempt)):02d}"
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return {
            "code": "service_unavailable",
            "user_message": "服务暂时不可用。你的已有工作已经保存，请稍后重试。",
            "retryable": True,
            "debug_reference": debug_reference,
        }
    if isinstance(exc, TeachingError) or (kind == "scene_generate" and isinstance(exc, ValueError)):
        return {
            "code": "invalid_scene_source",
            "user_message": "地图或2D标注未通过数据检查。请返回检查标注后再生成。",
            "retryable": False,
            "debug_reference": debug_reference,
        }
    return {
        "code": "scene_generation_failed",
        "user_message": PUBLIC_SCENE_GENERATION_FAILURE_MESSAGE,
        "retryable": int(attempt) <= 1,
        "debug_reference": debug_reference,
    }


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _normalized_weights(value: Mapping[str, Any] | None) -> dict[str, float]:
    source = dict(value or {"walkability": 0.45, "safety": 0.35, "beauty": 0.20})
    allowed = {"walkability", "safety", "beauty"}
    if not source or not set(source).issubset(allowed):
        raise ValueError("Evaluation weights may only use walkability, safety, and beauty.")
    weights: dict[str, float] = {}
    for key, raw in source.items():
        try:
            number = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Evaluation weight '{key}' must be numeric.") from exc
        if number < 0:
            raise ValueError(f"Evaluation weight '{key}' cannot be negative.")
        weights[key] = number
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("At least one evaluation weight must be positive.")
    return {key: round(number / total, 8) for key, number in weights.items()}


def _normalized_asset_palette(value: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(value or {})
    version = str(source.get("schemaVersion") or "roadgen3d.asset-palette.v1")
    if version != "roadgen3d.asset-palette.v1":
        raise ValueError("Unsupported asset palette schemaVersion.")
    raw_assets = source.get("assets") or []
    if not isinstance(raw_assets, list) or len(raw_assets) > 200:
        raise ValueError("Asset palette may contain at most 200 assets.")
    assets: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_assets):
        if not isinstance(raw, Mapping):
            raise ValueError(f"asset_palette.assets[{index}] must be an object.")
        item = {
            "manifestName": str(raw.get("manifestName") or "").strip(),
            "assetId": str(raw.get("assetId") or "").strip(),
            "fingerprint": str(raw.get("fingerprint") or "").strip().lower(),
            "category": str(raw.get("category") or "").strip().lower(),
            "label": str(raw.get("label") or raw.get("assetId") or "Asset").strip()[:240],
        }
        if not all(item.values()) or len(item["fingerprint"]) != 64 or any(ch not in "0123456789abcdef" for ch in item["fingerprint"]):
            raise ValueError(f"asset_palette.assets[{index}] is incomplete or has an invalid fingerprint.")
        identity = (item["manifestName"], item["assetId"])
        if identity in seen:
            continue
        seen.add(identity)
        assets.append(item)
    return {"schemaVersion": "roadgen3d.asset-palette.v1", "assets": assets}


def _parameter_patch_for_design_goals(
    weights: Mapping[str, Any] | None,
    *,
    query: str,
) -> tuple[dict[str, Any], dict[str, float]]:
    """Turn teaching-level objectives into a stable parametric design patch.

    This is deliberately small and inspectable: it is the offline fallback for
    classes that do not configure an LLM service, not a hidden learned model.
    """

    normalized = _normalized_weights(weights)
    walkability = normalized.get("walkability", 0.0)
    safety = normalized.get("safety", 0.0)
    beauty = normalized.get("beauty", 0.0)
    dominant = max(normalized, key=normalized.get)
    objective_profile = "greening" if beauty >= max(walkability, safety) else "balanced"
    return {
        "query": query,
        "design_rule_profile": "pedestrian_priority_v1" if walkability + safety >= 0.60 else "balanced_complete_street_v1",
        "objective_profile": objective_profile,
        "style_preset": "lush_walkable_v1" if beauty + walkability >= 0.55 else "civic_clean_v1",
        "sidewalk_width_m": round(2.4 + (1.25 * walkability) + (0.65 * safety) + (0.15 * beauty), 2),
        "density": round(0.82 + (0.12 * walkability) + (0.05 * safety) + (0.34 * beauty), 2),
        "ped_demand_level": "high" if walkability + safety >= 0.55 else "medium",
        "bike_demand_level": "medium" if dominant in {"walkability", "safety"} else "low",
        "street_furniture_profile": "park_landscape" if dominant == "beauty" else "pedestrian_friendly",
        "street_furniture_profile_source": "manual",
        "skeleton_design_profile": "green_walkable" if dominant == "beauty" else "walkable_commercial",
        "skeleton_design_profile_source": "manual",
        "seed": 42,
    }, normalized


def _parameter_candidates_for_design_goals(
    weights: Mapping[str, Any] | None,
    *,
    query: str,
    candidate_count: int,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    """Create a small deterministic neighbourhood around the weighted design.

    The variants are intentionally inspectable. They explore pedestrian space,
    amenity/greening density, and a balanced midpoint; they are candidates for
    evaluation rather than claims of an optimum.
    """

    base, normalized = _parameter_patch_for_design_goals(weights, query=query)
    count = max(1, min(int(candidate_count), 5))
    variants = [
        ("weighted_anchor", 0.0, 0.0, None, 42),
        ("pedestrian_capacity", 0.45, -0.06, "pedestrian_friendly", 31415),
        ("amenity_greening", -0.15, 0.15, "park_landscape", 27182),
        ("balanced_midpoint", 0.20, 0.06, "pedestrian_friendly", 16180),
        ("lower_density_safety", 0.30, -0.12, "pedestrian_friendly", 14142),
    ]
    candidates: list[dict[str, Any]] = []
    for index, (profile, sidewalk_delta, density_delta, furniture_profile, seed) in enumerate(variants[:count]):
        patch = dict(base)
        patch["sidewalk_width_m"] = round(max(1.5, float(base["sidewalk_width_m"]) + sidewalk_delta), 2)
        patch["density"] = round(max(0.35, min(1.5, float(base["density"]) + density_delta)), 2)
        patch["seed"] = seed
        if furniture_profile:
            patch["street_furniture_profile"] = furniture_profile
        candidates.append({
            "candidate_index": index,
            "search_profile": profile,
            "seed": seed,
            "compose_config_patch": patch,
        })
    return candidates, normalized


def _normalized_minimum_scores(value: Mapping[str, Any] | None) -> dict[str, float]:
    allowed = {"walkability", "safety", "beauty"}
    result: dict[str, float] = {}
    for key, raw in dict(value or {}).items():
        if key not in allowed:
            raise ValueError(f"Unsupported minimum score: {key}.")
        score = float(raw)
        if not 0.0 <= score <= 100.0:
            raise ValueError(f"Minimum score for {key} must be between 0 and 100.")
        result[key] = score
    return result


def _candidate_selection_row(
    evaluation: Mapping[str, Any] | None,
    *,
    weights: Mapping[str, float],
    minimum_scores: Mapping[str, float],
) -> dict[str, Any]:
    result = dict((evaluation or {}).get("result") or {})
    scores: dict[str, float] = {}
    for key in {"walkability", "safety", "beauty"}:
        try:
            scores[key] = float(result.get(key, 0.0))
        except (TypeError, ValueError):
            scores[key] = 0.0
    violation = round(sum(max(0.0, threshold - scores.get(key, 0.0)) for key, threshold in minimum_scores.items()), 8)
    weighted_score = round(sum(scores.get(key, 0.0) * float(weight) for key, weight in weights.items()), 8)
    succeeded = bool(evaluation and evaluation.get("status") == "succeeded")
    return {
        "scores": scores,
        "weighted_score": weighted_score,
        "constraint_violation": violation,
        "feasible": succeeded and violation <= 1e-9,
        "evaluation_status": str((evaluation or {}).get("status") or "not_configured"),
    }


class TeachingPlatformService:
    def __init__(self, database: TeachingDatabase | None = None, artifact_store: ArtifactStore | None = None) -> None:
        self.database = database or TeachingDatabase()
        self.artifacts = artifact_store or create_artifact_store()

    # ---- identity and course membership -------------------------------------------------
    def bootstrap_admin(self, *, email: str, password: str, display_name: str, token: str) -> dict[str, Any]:
        expected = os.getenv("ROADGEN_BOOTSTRAP_TOKEN", "").strip()
        allow_dev = os.getenv("ROADGEN_ALLOW_DEV_BOOTSTRAP", "0") == "1"
        with self.database.session() as db:
            if db.scalar(select(func.count(User.id))) != 0:
                raise Conflict("The first administrator has already been created.")
            if (not expected or not secrets_equal(token, expected)) and not allow_dev:
                raise Forbidden("A valid ROADGEN_BOOTSTRAP_TOKEN is required.")
            user = User(
                email=normalize_email(email),
                display_name=str(display_name or "Administrator").strip()[:120],
                password_hash=hash_password(password),
                system_role="admin",
            )
            db.add(user)
            db.flush()
            self._audit(db, user.id, None, "auth.bootstrap", {})
            return self._user(user)

    def bootstrap_status(self) -> dict[str, bool]:
        """Safe deployment probe; it deliberately exposes no account details."""

        with self.database.session() as db:
            return {"initialized": bool(db.scalar(select(func.count(User.id))))}

    def login(self, *, email: str, password: str) -> dict[str, Any]:
        with self.database.session() as db:
            user = db.scalar(select(User).where(User.email == normalize_email(email)))
            if user is None or not user.is_active or not verify_password(password, user.password_hash):
                raise Forbidden("Invalid email or password.")
            token, token_hash, expires_at = issue_session_token()
            db.add(AuthSession(user_id=user.id, token_hash=token_hash, expires_at=expires_at))
            self._audit(db, user.id, None, "auth.login", {})
            return {"access_token": token, "token_type": "bearer", "expires_at": _iso(expires_at), "user": self._user(user)}

    def logout(self, token: str) -> None:
        if not token:
            return
        with self.database.session() as db:
            auth = db.scalar(select(AuthSession).where(AuthSession.token_hash == digest_secret(token)))
            if auth is None:
                return
            self._audit(db, auth.user_id, None, "auth.logout", {})
            db.delete(auth)

    def authenticate(self, token: str) -> dict[str, Any]:
        if not token:
            raise Forbidden("Authentication is required.")
        with self.database.session() as db:
            auth = db.scalar(select(AuthSession).where(AuthSession.token_hash == digest_secret(token)))
            if auth is None or auth.expires_at.replace(tzinfo=auth.expires_at.tzinfo or timezone.utc) <= now_utc():
                raise Forbidden("The login session is invalid or expired.")
            user = db.get(User, auth.user_id)
            if user is None or not user.is_active:
                raise Forbidden("The user account is inactive.")
            if user.system_role == "guest":
                # Guest ownership is browser-local. Keep an actively used guest
                # identity alive without turning it into a password account.
                auth.expires_at = now_utc() + timedelta(days=365)
            return self._user(user)

    def create_guest_session(self) -> dict[str, Any]:
        """Create a browser-owned identity whose projects are publicly readable."""

        guest_id = new_id()
        with self.database.session() as db:
            user = User(
                id=guest_id,
                email=f"guest-{guest_id}@public.roadgen3d.invalid",
                display_name=f"访客 {guest_id[:6].upper()}",
                password_hash=hash_password(f"{new_id()}{new_id()}"),
                system_role="guest",
            )
            db.add(user)
            db.flush()
            workspace = self._create_workspace(db, user, scope="public")
            token, token_hash, expires_at = issue_session_token(lifetime_hours=24 * 365)
            db.add(AuthSession(user_id=user.id, token_hash=token_hash, expires_at=expires_at))
            self._audit(db, user.id, None, "auth.guest", {"workspace_id": workspace.id})
            return {
                "access_token": token,
                "token_type": "bearer",
                "expires_at": _iso(expires_at),
                "user": self._user(user),
                "workspace": self._course(workspace, "owner"),
            }

    def register_student(self, *, email: str, password: str, display_name: str, course_code: str, invite_code: str) -> dict[str, Any]:
        with self.database.session() as db:
            course = db.scalar(select(Course).where(Course.code == str(course_code).strip().upper(), Course.archived.is_(False)))
            if course is None or not secrets_equal(course.invite_hash, digest_secret(invite_code.strip())):
                raise Forbidden("The course code or invitation code is invalid.")
            normalized_email = normalize_email(email)
            if db.scalar(select(User).where(User.email == normalized_email)) is not None:
                raise Conflict("An account already exists for this email address.")
            user = User(email=normalized_email, display_name=display_name.strip()[:120], password_hash=hash_password(password), system_role="student")
            db.add(user)
            db.flush()
            db.add(Membership(course_id=course.id, user_id=user.id, role="student"))
            self._audit(db, user.id, None, "auth.course_register", {"course_id": course.id})
            return self._user(user)

    def register_personal(
        self,
        *,
        email: str,
        password: str,
        display_name: str,
        invite_code: str,
    ) -> dict[str, Any]:
        """Consume a bounded invite and create an isolated professional workspace."""

        with self.database.session() as db:
            invite = db.scalar(select(RegistrationInvite).where(
                RegistrationInvite.code_hash == digest_secret(invite_code.strip()),
                RegistrationInvite.is_active.is_(True),
            ))
            if (
                invite is None
                or invite.expires_at.replace(tzinfo=invite.expires_at.tzinfo or timezone.utc) <= now_utc()
                or invite.used_count >= invite.max_uses
            ):
                raise Forbidden("The registration invitation is invalid, expired, or has already been used.")
            normalized_email = normalize_email(email)
            if db.scalar(select(User).where(User.email == normalized_email)) is not None:
                raise Conflict("An account already exists for this email address.")
            user = User(
                email=normalized_email,
                display_name=display_name.strip()[:120],
                password_hash=hash_password(password),
                system_role="student",
            )
            db.add(user)
            db.flush()
            workspace = self._create_personal_workspace(db, user)
            invite.used_count += 1
            if invite.used_count >= invite.max_uses:
                invite.is_active = False
            token, token_hash, expires_at = issue_session_token()
            db.add(AuthSession(user_id=user.id, token_hash=token_hash, expires_at=expires_at))
            self._audit(db, user.id, None, "auth.personal_register", {"invite_id": invite.id, "workspace_id": workspace.id})
            return {
                "access_token": token,
                "token_type": "bearer",
                "expires_at": _iso(expires_at),
                "user": self._user(user),
                "workspace": self._course(workspace, "owner"),
            }

    def workspace(self, actor_id: str) -> dict[str, Any]:
        with self.database.session() as db:
            actor = self._require_user(db, actor_id)
            workspace = self._workspace_for_actor(db, actor, create=True)
            projects = self._list_workspace_projects(db, actor.id, workspace.id)
            return {"workspace": self._course(workspace, "owner"), "projects": projects}

    def create_workspace_project(
        self,
        actor_id: str,
        *,
        name: str,
        city: str,
        design_goal: str,
        aoi_bbox: Sequence[float] | None,
    ) -> dict[str, Any]:
        with self.database.session() as db:
            actor = self._require_user(db, actor_id)
            workspace = self._workspace_for_actor(db, actor, create=True)
            bbox = self._bbox(aoi_bbox) if aoi_bbox else None
            project = Project(
                course_id=workspace.id,
                owner_id=actor.id,
                name=name.strip()[:180],
                city=(city.strip() or "广州")[:120],
                design_goal=(design_goal.strip() or "balanced_street")[:240],
                aoi_bbox=bbox,
            )
            db.add(project)
            db.flush()
            self._audit(db, actor.id, project.id, "workspace.project.create", {"workspace_id": workspace.id, "city": project.city})
            return self._project(project, role="owner")

    def list_workspace_projects(self, actor_id: str) -> list[dict[str, Any]]:
        with self.database.session() as db:
            actor = self._require_user(db, actor_id)
            workspace = self._workspace_for_actor(db, actor, create=True)
            return self._list_workspace_projects(db, actor.id, workspace.id)

    def list_public_projects(self) -> list[dict[str, Any]]:
        with self.database.session() as db:
            workspace_ids = list(db.scalars(select(Course.id).where(
                Course.scope == "public",
                Course.archived.is_(False),
            )).all())
            if not workspace_ids:
                return []
            projects = db.scalars(select(Project).where(
                Project.course_id.in_(workspace_ids),
                Project.archived.is_(False),
            ).order_by(Project.updated_at.desc())).all()
            return [self._public_project_summary(db, project) for project in projects]

    def public_project(self, project_id: str) -> dict[str, Any]:
        with self.database.session() as db:
            project = self._require_public_project(db, project_id)
            return self._public_project_summary(db, project)

    def public_revisions(self, project_id: str) -> list[dict[str, Any]]:
        with self.database.session() as db:
            project = self._require_public_project(db, project_id)
            return [self._revision(item) for item in db.scalars(select(SceneRevisionRecord).where(
                SceneRevisionRecord.project_id == project.id,
            ).order_by(SceneRevisionRecord.revision_number.desc())).all()]

    def public_viewer_manifest(self, project_id: str, revision_id: str) -> dict[str, Any]:
        with self.database.session() as db:
            project = self._require_public_project(db, project_id)
            owner_id = project.owner_id
        return self.viewer_manifest(owner_id, project_id, revision_id)

    def public_artifact(self, artifact_id: str) -> tuple[Artifact, Any]:
        with self.database.session() as db:
            artifact = db.get(Artifact, artifact_id)
            if artifact is None:
                raise NotFound("Artifact not found.")
            self._require_public_project(db, artifact.project_id)
            db.expunge(artifact)
        return artifact, self.artifacts.open(artifact.object_key)

    # ---- administrator operations (metadata only) -------------------------------------
    def admin_overview(self, actor_id: str) -> dict[str, Any]:
        with self.database.session() as db:
            self._require_admin(db, actor_id)
            users = db.scalars(select(User)).all()
            projects = db.scalars(select(Project).where(Project.archived.is_(False))).all()
            jobs = db.scalars(select(Job)).all()
            artifacts = db.scalars(select(Artifact)).all()
            status_counts: dict[str, int] = {}
            for job in jobs:
                status_counts[job.status] = status_counts.get(job.status, 0) + 1
            return {
                "users": {"total": len(users), "active": sum(1 for item in users if item.is_active)},
                "projects": {"total": len(projects), "personal": sum(1 for item in projects if self._project_scope(db, item) == "personal")},
                "jobs": {"total": len(jobs), "by_status": status_counts, "failed": status_counts.get("failed", 0)},
                "storage_bytes": sum(int(item.byte_size or 0) for item in artifacts),
                "recent_activity": [_iso(item.created_at) for item in db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(10)).all()],
            }

    def admin_users(self, actor_id: str, *, query: str = "", active: bool | None = None, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.session() as db:
            self._require_admin(db, actor_id)
            rows = db.scalars(select(User).order_by(User.created_at.desc())).all()
            term = query.strip().lower()
            results = []
            for user in rows:
                if term and term not in user.email.lower() and term not in user.display_name.lower():
                    continue
                if active is not None and user.is_active is not active:
                    continue
                results.append(self._admin_user_summary(db, user))
            return results[:limit]

    def admin_user(self, actor_id: str, user_id: str) -> dict[str, Any]:
        with self.database.session() as db:
            self._require_admin(db, actor_id)
            user = db.get(User, user_id)
            if user is None:
                raise NotFound("User not found.")
            summary = self._admin_user_summary(db, user)
            projects = db.scalars(select(Project).where(Project.owner_id == user.id).order_by(Project.updated_at.desc())).all()
            summary["projects"] = [
                {
                    "id": item.id,
                    "name": item.name,
                    "scope": self._project_scope(db, item),
                    "workflow_step": item.workflow_step,
                    "updated_at": _iso(item.updated_at),
                    "revision_count": int(db.scalar(select(func.count(SceneRevisionRecord.id)).where(SceneRevisionRecord.project_id == item.id)) or 0),
                    "latest_job_status": db.scalar(select(Job.status).where(Job.project_id == item.id).order_by(Job.updated_at.desc()).limit(1)),
                }
                for item in projects
            ]
            return summary

    def set_user_active(self, actor_id: str, user_id: str, *, is_active: bool) -> dict[str, Any]:
        with self.database.session() as db:
            actor = self._require_admin(db, actor_id)
            if actor.id == user_id and not is_active:
                raise ValueError("Administrators cannot suspend their own active account.")
            user = db.get(User, user_id)
            if user is None:
                raise NotFound("User not found.")
            user.is_active = bool(is_active)
            if not user.is_active:
                for session in db.scalars(select(AuthSession).where(AuthSession.user_id == user.id)).all():
                    db.delete(session)
            self._audit(db, actor.id, None, "admin.user.status", {"user_id": user.id, "is_active": user.is_active})
            return self._admin_user_summary(db, user)

    def create_registration_invite(self, actor_id: str, *, expires_in_hours: int = 72, max_uses: int = 1, note: str = "") -> dict[str, Any]:
        if not 1 <= int(expires_in_hours) <= 24 * 30:
            raise ValueError("expires_in_hours must be between 1 and 720.")
        if not 1 <= int(max_uses) <= 1000:
            raise ValueError("max_uses must be between 1 and 1000.")
        invite_code, code_hash = issue_invite_code()
        with self.database.session() as db:
            self._require_admin(db, actor_id)
            invite = RegistrationInvite(
                code_hash=code_hash,
                created_by=actor_id,
                expires_at=now_utc() + timedelta(hours=int(expires_in_hours)),
                max_uses=int(max_uses),
                note=note.strip()[:240],
            )
            db.add(invite)
            db.flush()
            self._audit(db, actor_id, None, "admin.registration_invite.create", {"invite_id": invite.id, "max_uses": invite.max_uses})
            return {**self._registration_invite(invite), "invite_code": invite_code}

    def list_registration_invites(self, actor_id: str) -> list[dict[str, Any]]:
        with self.database.session() as db:
            self._require_admin(db, actor_id)
            return [self._registration_invite(item) for item in db.scalars(select(RegistrationInvite).order_by(RegistrationInvite.created_at.desc())).all()]

    def revoke_registration_invite(self, actor_id: str, invite_id: str) -> dict[str, Any]:
        with self.database.session() as db:
            self._require_admin(db, actor_id)
            invite = db.get(RegistrationInvite, invite_id)
            if invite is None:
                raise NotFound("Registration invitation not found.")
            invite.is_active = False
            self._audit(db, actor_id, None, "admin.registration_invite.revoke", {"invite_id": invite.id})
            return self._registration_invite(invite)

    def create_course(self, actor_id: str, *, name: str, code: str) -> dict[str, Any]:
        invite_code, invite_hash = issue_invite_code()
        with self.database.session() as db:
            actor = self._require_user(db, actor_id)
            if actor.system_role not in {"teacher", "admin"}:
                raise Forbidden("Only teachers and administrators can create courses.")
            normalized_code = str(code).strip().upper()
            if not normalized_code or db.scalar(select(Course).where(Course.code == normalized_code)) is not None:
                raise Conflict("Course code is empty or already in use.")
            course = Course(name=name.strip()[:160], code=normalized_code, invite_hash=invite_hash, owner_id=actor.id)
            db.add(course)
            db.flush()
            db.add(Membership(course_id=course.id, user_id=actor.id, role="teacher"))
            profile = EvaluationProfile(
                course_id=course.id,
                created_by=actor.id,
                name="Complete Street Core",
                dimensions=_normalized_weights(None),
                is_default=True,
            )
            db.add(profile)
            self._audit(db, actor.id, None, "course.create", {"course_id": course.id})
            return {**self._course(course, "teacher"), "invite_code": invite_code}

    def list_courses(self, actor_id: str) -> list[dict[str, Any]]:
        with self.database.session() as db:
            memberships = db.scalars(select(Membership).where(Membership.user_id == actor_id)).all()
            rows = []
            for membership in memberships:
                course = db.get(Course, membership.course_id)
                if course is not None and course.scope == "course":
                    rows.append(self._course(course, membership.role))
            return rows

    # ---- projects ----------------------------------------------------------------------
    def create_project(self, actor_id: str, *, course_id: str, name: str, city: str, design_goal: str, aoi_bbox: Sequence[float] | None) -> dict[str, Any]:
        with self.database.session() as db:
            self._require_membership(db, actor_id, course_id)
            bbox = self._bbox(aoi_bbox) if aoi_bbox else None
            project = Project(course_id=course_id, owner_id=actor_id, name=name.strip()[:180], city=(city.strip() or "广州")[:120], design_goal=(design_goal.strip() or "balanced_street")[:240], aoi_bbox=bbox)
            db.add(project)
            db.flush()
            self._audit(db, actor_id, project.id, "project.create", {"city": project.city})
            return self._project(project, role="owner")

    def list_projects(self, actor_id: str, *, course_id: str | None = None) -> list[dict[str, Any]]:
        with self.database.session() as db:
            memberships = {item.course_id: item.role for item in db.scalars(select(Membership).where(Membership.user_id == actor_id)).all()}
            query = select(Project).where(Project.archived.is_(False)).order_by(Project.updated_at.desc())
            if course_id:
                query = query.where(Project.course_id == course_id)
            rows = []
            for project in db.scalars(query).all():
                role = memberships.get(project.course_id)
                if role in {"teacher", "admin"} or project.owner_id == actor_id:
                    rows.append(self._project(project, role="owner" if project.owner_id == actor_id else role or "student"))
            return rows

    def get_project(self, actor_id: str, project_id: str) -> dict[str, Any]:
        with self.database.session() as db:
            project, role = self._require_project(db, actor_id, project_id)
            return self._project(project, role)

    def update_project_step(self, actor_id: str, project_id: str, workflow_step: str) -> dict[str, Any]:
        allowed = {"area", "data", "annotation", "design", "evaluation", "compare_export"}
        if workflow_step not in allowed:
            raise ValueError(f"workflow_step must be one of {sorted(allowed)}.")
        with self.database.session() as db:
            project, _ = self._require_project(db, actor_id, project_id, write=True)
            project.workflow_step = workflow_step
            self._audit(db, actor_id, project.id, "project.workflow_step", {"workflow_step": workflow_step})
            return self._project(project, "owner" if project.owner_id == actor_id else "teacher")

    def get_asset_palette(self, actor_id: str, project_id: str) -> dict[str, Any]:
        with self.database.session() as db:
            project, _ = self._require_project(db, actor_id, project_id)
            return _normalized_asset_palette(project.asset_palette)

    def update_asset_palette(
        self,
        actor_id: str,
        project_id: str,
        palette: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized = _normalized_asset_palette(palette)
        verified_assets: list[dict[str, str]] = []
        for item in normalized["assets"]:
            try:
                resolved = resolve_registered_asset(
                    item["manifestName"],
                    item["assetId"],
                    expected_fingerprint=item["fingerprint"],
                    require_ready=True,
                )
            except AssetManifestConflictError as exc:
                raise Conflict(str(exc)) from exc
            except (AssetReferenceError, ValueError) as exc:
                raise ValueError(str(exc)) from exc
            public = resolved["public"]
            verified_assets.append({
                "manifestName": str(public["manifestName"]),
                "assetId": str(public["assetId"]),
                "fingerprint": str(public["fingerprint"]),
                "category": str(public["category"]),
                "label": str(item.get("label") or public["label"]),
            })
        verified = {"schemaVersion": "roadgen3d.asset-palette.v1", "assets": verified_assets}
        with self.database.session() as db:
            project, _ = self._require_project(db, actor_id, project_id, write=True)
            project.asset_palette = verified
            self._audit(db, actor_id, project.id, "project.asset_palette.update", {
                "asset_count": len(verified_assets),
                "asset_ids": [item["assetId"] for item in verified_assets],
            })
            db.flush()
            return verified

    # ---- sources and artifacts ---------------------------------------------------------
    def import_geojson(self, actor_id: str, project_id: str, payload: Mapping[str, Any], *, kind: str = "geojson", provenance: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self.database.session() as db:
            project, _ = self._require_project(db, actor_id, project_id, write=True)
            source_id = new_id()
            normalized = normalize_teaching_geojson(payload, source_id=source_id, bbox=project.aoi_bbox)
        return self._persist_normalized_source(
            actor_id,
            project_id,
            source_id=source_id,
            kind=kind,
            raw_payload=payload,
            raw_kind="source_geojson_raw",
            raw_filename=f"{source_id}-raw.geojson",
            raw_content_type="application/geo+json",
            normalized=normalized,
            provenance=provenance,
        )

    def _persist_normalized_source(
        self,
        actor_id: str,
        project_id: str,
        *,
        source_id: str,
        kind: str,
        raw_payload: Mapping[str, Any],
        raw_kind: str,
        raw_filename: str,
        raw_content_type: str,
        normalized: Mapping[str, Any],
        provenance: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist an already-normalized source without running a second converter."""

        with self.database.session() as db:
            project, _ = self._require_project(db, actor_id, project_id, write=True)
            raw_artifact = self._store_artifact(db, actor_id, project.id, raw_kind, raw_filename, json_bytes(raw_payload), raw_content_type)
            normalized_artifact = self._store_artifact(db, actor_id, project.id, "source_geojson_normalized", f"{source_id}.geojson", json_bytes(normalized["geojson"]), "application/geo+json")
            annotation_artifact = self._store_artifact(db, actor_id, project.id, "reference_annotation", f"{source_id}-annotation.json", json_bytes(normalized["annotation"]), "application/json")
            record = SceneSourceRecord(
                id=source_id,
                project_id=project.id,
                created_by=actor_id,
                kind=kind,
                raw_artifact_id=raw_artifact.id,
                normalized_artifact_id=normalized_artifact.id,
                annotation_artifact_id=annotation_artifact.id,
                provenance={
                    "source": kind,
                    "role_counts": normalized["role_counts"],
                    "warnings": normalized["warnings"],
                    "source_alignment": normalized["source_alignment"],
                    **dict(provenance or {}),
                },
                quality_report=normalized["quality_report"],
            )
            db.add(record)
            project.workflow_step = "annotation"
            self._audit(db, actor_id, project.id, "source.import", {"source_id": record.id, "kind": kind})
            db.flush()
            return self._source(record, normalized)

    def approve_source_review(
        self,
        actor_id: str,
        project_id: str,
        source_id: str,
        *,
        annotation: Mapping[str, Any] | None = None,
        geojson: Mapping[str, Any] | None = None,
        actions: Sequence[Mapping[str, Any]] | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        """Persist a reviewed annotation as a new immutable source version."""
        if annotation is None and geojson is None:
            raise ValueError("Review requires annotation or geojson.")
        with self.database.session() as db:
            project, _ = self._require_project(db, actor_id, project_id, write=True)
            parent = db.get(SceneSourceRecord, source_id)
            if parent is None or parent.project_id != project.id:
                raise NotFound("Scene source not found in this project.")
            artifact = db.get(Artifact, parent.normalized_artifact_id)
            if artifact is None:
                raise NotFound("The source GeoJSON artifact is missing.")
            parent_key = artifact.object_key
        action_log = [dict(item) for item in actions or []]
        if annotation is not None:
            normalized = normalize_scene_source({
                "kind": "reference_annotation",
                "source_id": new_id(),
                "producer": "manual",
                "annotation": annotation,
            })
            normalized_payload = normalized.to_graph_payload()
            reviewed_at = _iso(now_utc())
            with self.database.session() as db:
                project, _ = self._require_project(db, actor_id, project_id, write=True)
                parent = db.get(SceneSourceRecord, source_id)
                if parent is None or parent.project_id != project.id:
                    raise NotFound("Scene source not found in this project.")
                reviewed_id = new_id()
                raw_artifact = self._store_artifact(
                    db, actor_id, project.id, "reference_annotation_review",
                    f"{reviewed_id}-review.json", json_bytes(annotation), "application/json",
                )
                normalized_artifact = self._store_artifact(
                    db, actor_id, project.id, "source_geojson_normalized",
                    f"{reviewed_id}.geojson", json_bytes(normalized.geojson), "application/geo+json",
                )
                annotation_artifact = self._store_artifact(
                    db, actor_id, project.id, "reference_annotation",
                    f"{reviewed_id}-annotation.json", json_bytes(normalized.annotation), "application/json",
                )
                record = SceneSourceRecord(
                    id=reviewed_id,
                    project_id=project.id,
                    created_by=actor_id,
                    kind="reviewed_annotation",
                    raw_artifact_id=raw_artifact.id,
                    normalized_artifact_id=normalized_artifact.id,
                    annotation_artifact_id=annotation_artifact.id,
                    provenance={
                        **dict(parent.provenance),
                        "source": "reviewed_annotation",
                        "parent_source_id": source_id,
                        "review_status": "approved",
                        "review_notes": str(notes).strip()[:2_000],
                        "review_actions": action_log,
                        "reviewed_at": reviewed_at,
                        "warnings": list(normalized.warnings),
                        "role_counts": dict(normalized_payload.get("summary") or {}),
                        "annotation_sha256": normalized.source.get("annotation_sha256"),
                    },
                    quality_report={
                        **dict(parent.quality_report),
                        "review_annotation_preserved": True,
                        "review_action_count": len(action_log),
                    },
                )
                db.add(record)
                project.workflow_step = "design"
                self._audit(db, actor_id, project.id, "source.review_approved", {
                    "source_id": record.id,
                    "parent_source_id": source_id,
                    "action_count": len(action_log),
                    "format": "reference_annotation",
                })
                db.flush()
                return self._source(record)

        assert geojson is not None
        with self.artifacts.open(parent_key) as handle:
            parent_geojson = json.loads(handle.read().decode("utf-8"))
        reviewed = self.import_geojson(
            actor_id,
            project_id,
            geojson,
            kind="reviewed_annotation",
            provenance={
                "parent_source_id": source_id,
                "review_status": "approved",
                "review_notes": str(notes).strip()[:2_000],
                "review_actions": action_log,
                "reviewed_at": _iso(now_utc()),
            },
        )
        reviewed_artifact, reviewed_handle = self.artifact(actor_id, reviewed["normalized_artifact_id"])
        try:
            reviewed_geojson = json.loads(reviewed_handle.read().decode("utf-8"))
        finally:
            reviewed_handle.close()
        delta = round_trip_report(parent_geojson, reviewed_geojson)
        with self.database.session() as db:
            record = db.get(SceneSourceRecord, reviewed["id"])
            project = db.get(Project, project_id)
            if record is None or project is None:
                raise NotFound("Reviewed scene source was not persisted.")
            record.quality_report = {**dict(record.quality_report), "review_delta": delta}
            record.provenance = {
                **dict(record.provenance),
                "review_artifact_sha256": reviewed_artifact.sha256,
            }
            project.workflow_step = "design"
            self._audit(db, actor_id, project.id, "source.review_approved", {
                "source_id": record.id,
                "parent_source_id": source_id,
                "action_count": len(action_log),
            })
            db.flush()
            return self._source(record)

    def workflow_source(self, actor_id: str, project_id: str, source_id: str) -> dict[str, Any]:
        """Return a persisted source in the expert workbench's canonical shape."""

        with self.database.session() as db:
            self._require_project(db, actor_id, project_id)
            source = db.get(SceneSourceRecord, source_id)
            if source is None or source.project_id != project_id:
                raise NotFound("Scene source not found in this project.")
            annotation_artifact = db.get(Artifact, source.annotation_artifact_id) if source.annotation_artifact_id else None
            geojson_artifact = db.get(Artifact, source.normalized_artifact_id)
            if annotation_artifact is None or geojson_artifact is None:
                raise NotFound("The normalized source artifacts are missing.")
            annotation_key = annotation_artifact.object_key
            geojson_key = geojson_artifact.object_key
            provenance = dict(source.provenance)
            source_kind = source.kind
        with self.artifacts.open(annotation_key) as handle:
            annotation = json.loads(handle.read().decode("utf-8"))
        with self.artifacts.open(geojson_key) as handle:
            geojson_payload = json.loads(handle.read().decode("utf-8"))
        normalized = normalize_scene_source({
            "kind": "reference_annotation",
            "source_id": source_id,
            "producer": "osm" if source_kind == "osm" else "manual",
            "annotation": annotation,
        })
        payload = normalized.to_graph_payload()
        payload["geojson"] = geojson_payload
        payload["warnings"] = list(provenance.get("warnings") or normalized.warnings)
        payload["source"] = {
            **dict(payload.get("source") or {}),
            "source_id": source_id,
            "persisted_kind": source_kind,
        }
        if isinstance(provenance.get("source_alignment"), Mapping):
            payload["source_alignment"] = dict(provenance["source_alignment"])
        return payload

    def import_osm(
        self,
        actor_id: str,
        project_id: str,
        *,
        force_refetch: bool = False,
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        with self.database.session() as db:
            project, _ = self._require_project(db, actor_id, project_id, write=True)
            if not project.aoi_bbox:
                raise ValueError("Select an AOI before importing OSM.")
            bbox = tuple(float(item) for item in project.aoi_bbox)
        source_id = new_id()
        bundle = fetch_normalized_osm_scene_source(
            aoi_bbox=bbox,
            source_id=source_id,
            cache_dir=Path(os.getenv("ROADGEN_OSM_CACHE", "artifacts/osm_cache")),
            force_refetch=force_refetch,
            progress_callback=progress_callback,
        )
        return self._persist_normalized_source(
            actor_id,
            project_id,
            source_id=source_id,
            kind="osm",
            raw_payload=bundle["raw_osm"],
            raw_kind="source_osm_raw",
            raw_filename=f"{source_id}-overpass.json",
            raw_content_type="application/json",
            normalized=bundle["normalized"],
            provenance=bundle["provenance"],
        )

    def create_osm_preview(
        self,
        actor_id: str,
        project_id: str,
        *,
        force_refetch: bool = False,
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        with self.database.session() as db:
            project, _ = self._require_project(db, actor_id, project_id, write=True)
            if not project.aoi_bbox:
                raise ValueError("Select an OSM retrieval area before importing OSM.")
            bbox = tuple(float(item) for item in project.aoi_bbox)
        preview_id = new_id()
        source_id = new_id()
        bundle = build_osm_road_preview(
            aoi_bbox=bbox,
            source_id=source_id,
            cache_dir=Path(os.getenv("ROADGEN_OSM_CACHE", "artifacts/osm_cache")),
            force_refetch=force_refetch,
            preview_id=preview_id,
            progress_callback=progress_callback,
        )
        with self.database.session() as db:
            project, _ = self._require_project(db, actor_id, project_id, write=True)
            artifact = self._store_artifact(
                db,
                actor_id,
                project.id,
                "osm_preview_raw",
                f"{preview_id}-overpass.json",
                json_bytes(bundle.raw_osm),
                "application/json",
            )
            self._audit(db, actor_id, project.id, "source.osm_preview", {
                "preview_id": preview_id,
                "raw_artifact_id": artifact.id,
                "retrieval_bbox": list(bbox),
            })
            return {**dict(bundle.preview), "raw_artifact_id": artifact.id}

    def select_osm_preview(
        self,
        actor_id: str,
        project_id: str,
        *,
        raw_artifact_id: str,
        preview_id: str,
        seed_logical_road_id: str,
        hop_count: int,
        context_buffer_m: float,
    ) -> dict[str, Any]:
        raw_osm, bundle, selected = self._resolve_osm_preview_selection(
            actor_id,
            project_id,
            raw_artifact_id=raw_artifact_id,
            preview_id=preview_id,
            seed_logical_road_id=seed_logical_road_id,
            hop_count=hop_count,
            context_buffer_m=context_buffer_m,
        )
        source_id = str(selected["normalized"]["annotation"]["plan_id"])
        with self.database.session() as db:
            project, _ = self._require_project(db, actor_id, project_id, write=True)
            bbox = tuple(float(item) for item in (project.aoi_bbox or ()))
        result = self._persist_normalized_source(
            actor_id,
            project_id,
            source_id=source_id,
            kind="osm_road_study",
            raw_payload=raw_osm,
            raw_kind="source_osm_raw",
            raw_filename=f"{source_id}-overpass.json",
            raw_content_type="application/json",
            normalized=selected["normalized"],
            provenance={
                "provider": "OpenStreetMap/Overpass",
                "attribution": "© OpenStreetMap contributors",
                "road_study": selected["study"],
                "retrieval_bbox": list(bbox),
                # This is a RoadGen3D-owned immutable snapshot, never an OSM
                # writeback payload. It lets later 2D edits and regenerated
                # 3D revisions recover the same source frame and selection.
                "osm_annotation_context": selected["osm_annotation_context"],
            },
        )
        return {
            **result,
            "osm_study": selected["study"],
            "osm_annotation_context": selected["osm_annotation_context"],
        }

    def preview_osm_selection(
        self,
        actor_id: str,
        project_id: str,
        **options: Any,
    ) -> dict[str, Any]:
        raw_osm, bundle, selected = self._resolve_osm_preview_selection(actor_id, project_id, **options)
        payload = osm_scene_source_response({
            "bbox": tuple(selected["study"]["annotation_bbox"]),
            "raw_osm": raw_osm,
            "geojson": selected["filtered_geojson"],
            "normalized": selected["normalized"],
            "provenance": {
                "provider": "OpenStreetMap/Overpass",
                "attribution": "© OpenStreetMap contributors",
                "bbox": list(bundle.bbox),
                "raw_element_count": len(raw_osm.get("elements", [])),
            },
        })
        payload["osm_study"] = selected["study"]
        payload["osm_annotation_context"] = selected["osm_annotation_context"]
        payload["warnings"] = list(selected["study"]["warnings"])
        return payload

    def _resolve_osm_preview_selection(
        self,
        actor_id: str,
        project_id: str,
        *,
        raw_artifact_id: str,
        preview_id: str,
        seed_logical_road_id: str,
        hop_count: int,
        context_buffer_m: float,
    ) -> tuple[dict[str, Any], Any, dict[str, Any]]:
        with self.database.session() as db:
            project, _ = self._require_project(db, actor_id, project_id, write=True)
            artifact = db.get(Artifact, raw_artifact_id)
            if artifact is None or artifact.project_id != project.id or artifact.kind != "osm_preview_raw":
                raise NotFound("OSM preview artifact not found in this project.")
            object_key = artifact.object_key
            bbox = tuple(float(item) for item in (project.aoi_bbox or ()))
        with self.artifacts.open(object_key) as handle:
            raw_osm = json.loads(handle.read().decode("utf-8"))
        source_id = new_id()
        bundle = preview_bundle_from_raw(
            raw_osm=raw_osm,
            aoi_bbox=bbox,
            source_id=source_id,
            preview_id=preview_id,
        )
        selected = select_osm_road_study_area(
            bundle,
            seed_logical_road_id=seed_logical_road_id,
            hop_count=hop_count,
            context_buffer_m=context_buffer_m,
            source_id=source_id,
        )
        return raw_osm, bundle, selected

    def generate_project_scene(
        self,
        actor_id: str,
        project_id: str,
        *,
        source_id: str,
        prompt: str,
        generation_mode: str = "baseline",
        parent_revision_id: str | None = None,
        goal_weights: Mapping[str, Any] | None = None,
        candidate_count: int = 1,
        minimum_scores: Mapping[str, Any] | None = None,
        generator: Callable[..., Mapping[str, Any]],
        evaluator: Callable[..., Mapping[str, Any]] | None = None,
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        from roadgen3d.llm.design_workflow import parse_design_draft
        from roadgen3d.eval_engine_ext.road_metrics.evaluators.llm_client import public_llm_capabilities_from_env

        requested_mode = str(generation_mode or "baseline").strip().lower()
        if requested_mode not in {"baseline", "auto", "llm", "parametric"}:
            raise ValueError("generation_mode must be baseline, auto, llm, or parametric.")

        with self.database.session() as db:
            project, _ = self._require_project(db, actor_id, project_id, write=True)
            source = db.get(SceneSourceRecord, source_id)
            if source is None or source.project_id != project.id or not source.annotation_artifact_id:
                raise NotFound("A normalized scene source is required before generation.")
            annotation_artifact = db.get(Artifact, source.annotation_artifact_id)
            if annotation_artifact is None:
                raise NotFound("The normalized annotation artifact is missing.")
            annotation_key = annotation_artifact.object_key
            source_context = {"source_id": source.id, "provenance": source.provenance, "quality_report": source.quality_report}
            query = str(prompt or project.design_goal or "balanced complete street").strip()
            parent = db.get(SceneRevisionRecord, parent_revision_id) if parent_revision_id else None
            if parent_revision_id and (parent is None or parent.project_id != project.id):
                raise NotFound("Parent revision not found in this project.")
            existing_baseline = db.scalar(
                select(SceneRevisionRecord)
                .where(SceneRevisionRecord.project_id == project.id, SceneRevisionRecord.branch_kind == "baseline")
                .order_by(SceneRevisionRecord.revision_number.desc())
            )
        capabilities = public_llm_capabilities_from_env()
        llm_configured = bool(capabilities.get("configured"))
        is_baseline = requested_mode == "baseline" or (not parent_revision_id and existing_baseline is None)
        resolved_mode = "parametric" if is_baseline else requested_mode
        if resolved_mode == "auto":
            # Auto means the stable product default, not "use whatever remote
            # service is configured".  LLM use must be an explicit action.
            resolved_mode = "parametric"
        fallback_reason = ""
        if resolved_mode == "llm" and not llm_configured:
            resolved_mode = "parametric"
            fallback_reason = "llm_not_configured"
        if not is_baseline and parent_revision_id is None:
            raise ValueError("A redesign must specify parent_revision_id.")

        design_query = query
        if not is_baseline:
            normalized_for_prompt = _normalized_weights(goal_weights)
            targets = ", ".join(f"{key} {value:.0%}" for key, value in normalized_for_prompt.items())
            design_query = f"{query}. Redesign this approved street scene for these priorities: {targets}."
        requested_candidate_count = 1 if is_baseline else max(1, min(int(candidate_count), 5))
        candidate_specs, normalized_weights = _parameter_candidates_for_design_goals(
            goal_weights,
            query=design_query,
            candidate_count=requested_candidate_count,
        )
        normalized_minimum_scores = _normalized_minimum_scores(minimum_scores)
        with self.artifacts.open(annotation_key) as handle:
            annotation = json.loads(handle.read().decode("utf-8"))
        def emit(stage: str, progress: int, message: str, **detail: Any) -> None:
            if progress_callback is None:
                return
            progress_callback({
                "stage": stage,
                "progress": progress,
                "message": message,
                "detail": detail,
            })

        def forward_generation_progress(candidate_index: int, event: Mapping[str, Any] | str) -> None:
            payload = dict(event) if isinstance(event, Mapping) else {"message": str(event)}
            try:
                raw_progress = float(payload.get("progress", 0))
            except (TypeError, ValueError):
                raw_progress = 0.0
            # Preserve the legacy 8..82 generation progress contract for a
            # single revision, and divide that same range across a search run.
            span = 74.0 / max(1, len(candidate_specs))
            payload["progress"] = 8 + int(round(candidate_index * span + max(0.0, min(100.0, raw_progress)) * span / 100.0))
            payload["detail"] = {**dict(payload.get("detail") or {}), "candidate_index": candidate_index + 1, "candidate_count": len(candidate_specs)}
            if progress_callback is not None:
                progress_callback(payload)

        source_building_ids = [
            str(feature.get("id") or feature.get("properties", {}).get("stable_id") or "")
            for feature in annotation.get("features", [])
            if isinstance(feature, Mapping)
            and str(feature.get("properties", {}).get("role") or "") in {"building", "building_footprint"}
        ]
        source_building_ids.extend(
            str(region.get("source_region_id") or region.get("id") or "")
            for region in annotation.get("regions", [])
            if isinstance(region, Mapping)
            and str(region.get("region_role") or "") == "building_region"
        )
        source_building_ids.extend(
            str(building.get("osm_id") or building.get("source_id") or building.get("id") or "")
            for building in source_context.get("aligned_buildings", [])
            if isinstance(building, Mapping)
        )
        source_building_ids = list(dict.fromkeys(item for item in source_building_ids if item))
        course_building_patch = {
            "building_representation": "transparent_massing",
            "surrounding_building_mode": "footprint_based",
            "auto_land_use_mode": "off",
            "infill_policy": "off",
            "building_height_mode": "class_only",
        }
        emit("annotation_resolving", 8, "Parsing the approved 2D annotation.", source_id=source_id)
        profiles = self.list_evaluation_profiles(actor_id, project_id) if evaluator is not None else []
        profile = next((item for item in profiles if item["is_default"]), profiles[0] if profiles else None)
        generated_candidates: list[dict[str, Any]] = []

        for candidate_index, candidate_spec in enumerate(candidate_specs):
            compose_patch = dict(candidate_spec["compose_config_patch"])
            draft = parse_design_draft(
                {
                    "normalized_scene_query": design_query,
                    "compose_config_patch": compose_patch,
                    "design_summary": f"Course project {resolved_mode} candidate {candidate_index + 1}/{len(candidate_specs)} for {design_query}",
                    "risk_notes": [],
                },
                evidence=(),
                fallback_query=design_query,
                current_patch=compose_patch,
            )
            result = dict(generator(
                draft,
                patch_overrides=course_building_patch,
                scene_context={
                    "layout_mode": "reference_annotation",
                    "reference_annotation": annotation,
                    "source_context": source_context,
                },
                generation_options={
                    "course_project_id": project_id,
                    "preset_id": "llm" if resolved_mode == "llm" else "skip_llm",
                    "skip_llm": resolved_mode != "llm",
                    "derive_parameters_with_llm": resolved_mode == "llm",
                    "knowledge_source": "none",
                    "random_seed": int(candidate_spec["seed"]),
                    "retain_glb_policy": "always",
                },
                progress_callback=lambda event, index=candidate_index: forward_generation_progress(index, event),
            ))
            emit(
                "artifact_persisting",
                80 + int(round((candidate_index + 1) * 8 / len(candidate_specs))),
                f"Saving candidate {candidate_index + 1}/{len(candidate_specs)}.",
                candidate_index=candidate_index + 1,
            )
            layout_path = Path(str(result.get("scene_layout_path") or result.get("layout_path") or "")).expanduser().resolve()
            glb_path = Path(str(result.get("scene_glb_path") or result.get("glb_path") or "")).expanduser().resolve()
            if not layout_path.is_file() or not glb_path.is_file():
                raise RuntimeError("Scene generation did not produce scene_layout.json and scene.glb.")
            layout_payload = json.loads(layout_path.read_text(encoding="utf-8"))
            production_step_payloads: list[dict[str, Any]] = []
            for step_index, step in enumerate(layout_payload.get("production_steps") or []):
                if not isinstance(step, Mapping):
                    continue
                step_path_text = str(step.get("glb_path") or "").strip()
                if not step_path_text:
                    continue
                step_path = Path(step_path_text).expanduser()
                if not step_path.is_absolute():
                    step_path = (layout_path.parent / step_path).resolve()
                if not step_path.is_file():
                    continue
                production_step_payloads.append({
                    "step_id": str(step.get("step_id") or f"step-{step_index + 1}"),
                    "title": str(step.get("title") or step.get("step_id") or f"Production step {step_index + 1}"),
                    "data": step_path.read_bytes(),
                })
            revision = self.create_revision(
                actor_id,
                project_id,
                layout=layout_payload,
                glb=glb_path.read_bytes(),
                production_steps=production_step_payloads,
                source_id=source_id,
                parent_id=None if is_baseline else parent_revision_id,
                branch_kind="baseline" if is_baseline else "ai_edit",
                label="Generated baseline" if is_baseline else f"C candidate {candidate_index + 1} · {candidate_spec['search_profile']}",
                provenance={
                    "generation_method": "llm_assisted" if resolved_mode == "llm" else ("parametric_search" if len(candidate_specs) > 1 else "parametric"),
                    "requested_generation_mode": requested_mode,
                    "resolved_generation_mode": resolved_mode,
                    "fallback_reason": fallback_reason,
                    "prompt": design_query,
                    "goal_weights": normalized_weights,
                    "minimum_scores": normalized_minimum_scores,
                    "compose_config_patch": compose_patch,
                    "search_strategy": "deterministic_local_neighbourhood_v1",
                    "candidate_index": candidate_index,
                    "candidate_count": len(candidate_specs),
                    "search_profile": candidate_spec["search_profile"],
                    "llm_capabilities": capabilities,
                    "generator_result": result,
                    "building_representation": "transparent_massing",
                    "massing_material": {
                        "base_color": "#F4F7F8", "opacity": 0.42,
                        "roughness": 1.0, "metallic": 0.0, "alpha_mode": "BLEND",
                    },
                    "source_building_ids": [item for item in source_building_ids if item],
                    "source_alignment": source_context.get("source_alignment"),
                },
            )
            evaluation = None
            if evaluator is not None and profile:
                evaluation = self.create_evaluation_run(
                    actor_id,
                    project_id,
                    revision_id=revision["id"],
                    profile_id=profile["id"],
                    weights=normalized_weights if not is_baseline else None,
                )
                evaluation = self.run_evaluation(actor_id, evaluation["id"], evaluator)
            selection = _candidate_selection_row(
                evaluation,
                weights=normalized_weights,
                minimum_scores=normalized_minimum_scores,
            )
            generated_candidates.append({
                "revision": revision,
                "evaluation": evaluation,
                "search_profile": candidate_spec["search_profile"],
                **selection,
            })

        ranked = sorted(
            generated_candidates,
            key=lambda item: (
                0 if item["feasible"] else 1,
                float(item["constraint_violation"]),
                -float(item["weighted_score"]),
                int(item["revision"]["revision_number"]),
            ),
        )
        selected = ranked[0]
        has_scored_candidate = any(item["evaluation_status"] == "succeeded" for item in generated_candidates)
        trace_rows = [{
            "revision_id": item["revision"]["id"],
            "search_profile": item["search_profile"],
            "scores": item["scores"],
            "weighted_score": item["weighted_score"],
            "constraint_violation": item["constraint_violation"],
            "feasible": item["feasible"],
            "evaluation_status": item["evaluation_status"],
            "selected": item is selected,
        } for item in generated_candidates]
        solver_trace = {
            "strategy": "deterministic_local_neighbourhood_v1",
            "selection_status": "evaluated_local_best" if has_scored_candidate else "unscored_fallback",
            "claim_scope": (
                "best evaluated local candidate; not a global optimum"
                if has_scored_candidate
                else "first generated fallback; no candidate evaluation was available"
            ),
            "goal_weights": normalized_weights,
            "minimum_scores": normalized_minimum_scores,
            "candidate_count": len(generated_candidates),
            "selected_revision_id": selected["revision"]["id"],
            "candidates": trace_rows,
        }
        with self.database.session() as db:
            for item in generated_candidates:
                record = db.get(SceneRevisionRecord, item["revision"]["id"])
                if record is not None:
                    record.provenance = {
                        **dict(record.provenance),
                        "solver_trace": solver_trace,
                        "solver_selected": item is selected,
                    }
                    item["revision"] = self._revision(record)
        emit(
            "candidate_selection",
            98,
            (
                f"Selected the best evaluated candidate from {len(generated_candidates)} local variants."
                if has_scored_candidate
                else f"Generated {len(generated_candidates)} local variants; opened the first because evaluation was unavailable."
            ),
            selected_revision_id=selected["revision"]["id"],
        )
        return {
            "revision": selected["revision"],
            "evaluation": selected["evaluation"],
            "candidates": generated_candidates,
            "solver_trace": solver_trace,
        }

    def list_sources(self, actor_id: str, project_id: str) -> list[dict[str, Any]]:
        with self.database.session() as db:
            self._require_project(db, actor_id, project_id)
            return [self._source(item) for item in db.scalars(select(SceneSourceRecord).where(SceneSourceRecord.project_id == project_id).order_by(SceneSourceRecord.created_at.desc())).all()]

    def adopt_generated_scene(
        self,
        actor_id: str,
        project_id: str,
        *,
        source_id: str | None,
        job_id: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Copy a completed professional scene job into durable project storage."""

        with self.database.session() as db:
            project, _ = self._require_project(db, actor_id, project_id, write=True)
            if source_id:
                source = db.get(SceneSourceRecord, source_id)
                if source is None or source.project_id != project.id:
                    raise NotFound("Scene source not found in this project.")
        layout_path = Path(str(result.get("scene_layout_path") or "")).expanduser().resolve()
        glb_path = Path(str(result.get("scene_glb_path") or "")).expanduser().resolve()
        if not layout_path.is_file() or not glb_path.is_file():
            raise NotFound("The completed scene job artifacts are no longer available.")
        layout_payload = json.loads(layout_path.read_text(encoding="utf-8"))
        production_steps: list[dict[str, Any]] = []
        for index, step in enumerate(layout_payload.get("production_steps") or []):
            if not isinstance(step, Mapping):
                continue
            step_path_text = str(step.get("glb_path") or "").strip()
            if not step_path_text:
                continue
            step_path = Path(step_path_text).expanduser()
            if not step_path.is_absolute():
                step_path = (layout_path.parent / step_path).resolve()
            if step_path.is_file():
                production_steps.append({
                    "step_id": str(step.get("step_id") or f"step-{index + 1}"),
                    "title": str(step.get("title") or step.get("step_id") or f"Production step {index + 1}"),
                    "data": step_path.read_bytes(),
                })
        revision = self.create_revision(
            actor_id,
            project_id,
            layout=layout_payload,
            glb=glb_path.read_bytes(),
            source_id=source_id,
            parent_id=None,
            branch_kind="baseline",
            label="Professional public generation",
            provenance={
                "generation_method": "professional_scene_job",
                "professional_job_id": job_id,
                "generator_summary": dict(result.get("summary") or {}),
            },
            production_steps=production_steps,
        )
        return revision

    def artifact(self, actor_id: str, artifact_id: str) -> tuple[Artifact, Any]:
        with self.database.session() as db:
            artifact = db.get(Artifact, artifact_id)
            if artifact is None:
                raise NotFound("Artifact not found.")
            self._require_project(db, actor_id, artifact.project_id)
            db.expunge(artifact)
        return artifact, self.artifacts.open(artifact.object_key)

    # ---- scene revisions ---------------------------------------------------------------
    def import_layout_revision(
        self,
        actor_id: str,
        project_id: str,
        *,
        layout_path: str,
        label: str,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        """Copy an existing local artifact scene into an owned project revision.

        This is the lazy bridge used when a professional user starts editing a
        scene opened through ``?layout=...``.  Both files must stay under the
        configured RoadGen3D artifact root; server paths are never copied into
        the revision provenance or public project metadata.
        """

        with self.database.session() as db:
            self._require_project(db, actor_id, project_id, write=True)

        repository_root = Path(__file__).resolve().parents[3]
        artifact_root = Path(
            os.getenv("ROADGEN_IMPORTABLE_SCENE_ROOT") or repository_root / "artifacts"
        ).expanduser().resolve()
        candidate = Path(str(layout_path or "").strip()).expanduser()
        if not candidate.is_absolute():
            candidate = repository_root / candidate
        candidate = candidate.resolve()
        try:
            candidate.relative_to(artifact_root)
        except ValueError as exc:
            raise Forbidden("Only RoadGen3D artifact scenes can be imported into a project.") from exc
        if not candidate.is_file() or candidate.name != "scene_layout.json":
            raise NotFound("Importable scene_layout.json was not found.")
        try:
            layout = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise Conflict("The selected scene layout is not valid JSON.") from exc
        if not isinstance(layout, dict):
            raise Conflict("The selected scene layout must contain an object.")

        outputs = layout.get("outputs") if isinstance(layout.get("outputs"), Mapping) else {}
        raw_glb = str(outputs.get("scene_glb") or "").strip()
        glb_path = Path(raw_glb).expanduser() if raw_glb else candidate.with_name("scene.glb")
        if not glb_path.is_absolute():
            glb_path = candidate.parent / glb_path
        glb_path = glb_path.resolve()
        try:
            glb_path.relative_to(artifact_root)
        except ValueError as exc:
            raise Forbidden("The scene GLB must remain inside the RoadGen3D artifact root.") from exc
        if not glb_path.is_file() or glb_path.stat().st_size <= 0:
            raise NotFound("The selected scene does not contain a usable scene.glb.")

        return self.create_revision(
            actor_id,
            project_id,
            layout=layout,
            glb=glb_path.read_bytes(),
            source_id=source_id,
            parent_id=None,
            branch_kind="baseline",
            label=label or "Imported professional scene",
            commands=[],
            provenance={
                "import_method": "professional_local_artifact",
                "source_linked": bool(source_id),
            },
        )

    def create_revision(self, actor_id: str, project_id: str, *, layout: Mapping[str, Any], glb: bytes | None, source_id: str | None, parent_id: str | None, branch_kind: str, label: str, commands: Sequence[Mapping[str, Any]] | None = None, provenance: Mapping[str, Any] | None = None, production_steps: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
        if branch_kind not in {"baseline", "human_edit", "ai_edit"}:
            raise ValueError("branch_kind must be baseline, human_edit, or ai_edit.")
        with self.database.session() as db:
            project, _ = self._require_project(db, actor_id, project_id, write=True)
            if source_id and (db.get(SceneSourceRecord, source_id) is None or db.get(SceneSourceRecord, source_id).project_id != project.id):
                raise NotFound("Scene source not found in this project.")
            parent = db.get(SceneRevisionRecord, parent_id) if parent_id else None
            if parent_id and (parent is None or parent.project_id != project.id):
                raise NotFound("Parent revision not found in this project.")
            revision_number = int(db.scalar(select(func.max(SceneRevisionRecord.revision_number)).where(SceneRevisionRecord.project_id == project.id)) or 0) + 1
            layout_artifact = self._store_artifact(db, actor_id, project.id, "scene_layout", f"revision-{revision_number:06d}-scene_layout.json", json_bytes(layout), "application/json")
            glb_artifact = self._store_artifact(db, actor_id, project.id, "scene_glb", f"revision-{revision_number:06d}-scene.glb", glb, "model/gltf-binary") if glb else None
            step_artifacts: list[dict[str, str]] = []
            for index, step in enumerate(production_steps or []):
                data = step.get("data")
                if not isinstance(data, bytes):
                    continue
                raw_step_id = str(step.get("step_id") or f"step-{index + 1}")[:96]
                step_id = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in raw_step_id).strip("-") or f"step-{index + 1}"
                title = str(step.get("title") or step_id)[:180]
                artifact = self._store_artifact(
                    db, actor_id, project.id, "scene_step_glb",
                    f"revision-{revision_number:06d}-{step_id}.glb", data, "model/gltf-binary",
                )
                step_artifacts.append({"step_id": step_id, "title": title, "artifact_id": artifact.id})
            revision_provenance = {
                "schema_version": "roadgen3d.scene_revision.v1",
                **dict(provenance or {}),
                "viewer_artifacts": {
                    "final_scene_artifact_id": glb_artifact.id if glb_artifact else None,
                    "production_steps": step_artifacts,
                },
            }
            revision = SceneRevisionRecord(
                project_id=project.id,
                source_id=source_id,
                parent_id=parent_id,
                created_by=actor_id,
                revision_number=revision_number,
                branch_kind=branch_kind,
                label=label.strip()[:180],
                layout_artifact_id=layout_artifact.id,
                glb_artifact_id=glb_artifact.id if glb_artifact else None,
                commands=[dict(item) for item in commands or []],
                provenance=revision_provenance,
            )
            db.add(revision)
            project.workflow_step = "design"
            db.flush()
            self._audit(db, actor_id, project.id, "revision.create", {"revision_id": revision.id, "branch_kind": branch_kind})
            return self._revision(revision)

    def list_revisions(self, actor_id: str, project_id: str) -> list[dict[str, Any]]:
        with self.database.session() as db:
            self._require_project(db, actor_id, project_id)
            return [self._revision(item) for item in db.scalars(select(SceneRevisionRecord).where(SceneRevisionRecord.project_id == project_id).order_by(SceneRevisionRecord.revision_number.desc())).all()]

    def viewer_manifest(self, actor_id: str, project_id: str, revision_id: str) -> dict[str, Any]:
        """Build a project-safe Viewer manifest backed only by artifact IDs."""

        with self.database.session() as db:
            self._require_project(db, actor_id, project_id)
            revision = db.get(SceneRevisionRecord, revision_id)
            if revision is None or revision.project_id != project_id:
                raise NotFound("Scene revision not found in this project.")
            layout_artifact = db.get(Artifact, revision.layout_artifact_id) if revision.layout_artifact_id else None
            glb_artifact = db.get(Artifact, revision.glb_artifact_id) if revision.glb_artifact_id else None
            if layout_artifact is None or glb_artifact is None:
                raise NotFound("The revision does not contain a viewable scene.")
            layout_key = layout_artifact.object_key
            provenance = dict(revision.provenance)
            revision_number = revision.revision_number
            branch_kind = revision.branch_kind
        with self.artifacts.open(layout_key) as handle:
            layout_payload = json.loads(handle.read().decode("utf-8"))
        viewer_artifacts = provenance.get("viewer_artifacts") if isinstance(provenance.get("viewer_artifacts"), Mapping) else {}
        steps = viewer_artifacts.get("production_steps") if isinstance(viewer_artifacts, Mapping) else []
        production_steps = [
            {
                "step_id": str(item.get("step_id") or "production-step"),
                "title": str(item.get("title") or item.get("step_id") or "Production Step"),
                "artifact_id": str(item.get("artifact_id")),
                "glb_url": "",
            }
            for item in steps or []
            if isinstance(item, Mapping) and item.get("artifact_id")
        ]
        manifest = build_layout_manifest_payload(
            layout_payload,
            layout_identity=f"project-revision:{revision_id}",
            final_scene={"label": "Final Scene", "artifact_id": glb_artifact.id, "glb_url": ""},
            production_steps=production_steps,
        )
        manifest["layout_revision"] = {
            "lineage_id": project_id,
            "revision": int(revision_number),
            "sha256": layout_artifact.sha256,
        }
        manifest["project_revision"] = {
            "project_id": project_id,
            "revision_id": revision_id,
            "branch_kind": branch_kind,
        }
        manifest["context_massing"] = {
            "editable": False,
            "summary": {"building_representation": provenance.get("building_representation")},
            "source": {"source_building_ids": provenance.get("source_building_ids", [])},
            "source_alignment": provenance.get("source_alignment"),
        }
        return manifest

    def edit_revision(self, actor_id: str, project_id: str, revision_id: str, *, commands: Sequence[Mapping[str, Any]], branch_kind: str, label: str, provenance: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self.database.session() as db:
            project, _ = self._require_project(db, actor_id, project_id, write=True)
            revision = db.get(SceneRevisionRecord, revision_id)
            if revision is None or revision.project_id != project.id:
                raise NotFound("Base revision not found in this project.")
            layout_artifact = db.get(Artifact, revision.layout_artifact_id) if revision.layout_artifact_id else None
            glb_artifact = db.get(Artifact, revision.glb_artifact_id) if revision.glb_artifact_id else None
            if layout_artifact is None or glb_artifact is None:
                raise Conflict("3D edits require both scene_layout and scene_glb artifacts.")
            source_id = revision.source_id
            layout_key, glb_key = layout_artifact.object_key, glb_artifact.object_key
        runtime_root = Path(os.getenv("ROADGEN_EDIT_RUNTIME_ROOT") or Path(__file__).resolve().parents[3] / "artifacts" / "teaching" / "edit_runtime")
        runtime_dir = runtime_root / project_id / revision_id
        runtime_dir.mkdir(parents=True, exist_ok=True)
        layout_path = runtime_dir / "scene_layout.json"
        glb_path = runtime_dir / "scene.glb"
        with self.artifacts.open(layout_key) as handle:
            layout = json.loads(handle.read().decode("utf-8"))
        with self.artifacts.open(glb_key) as handle:
            glb_path.write_bytes(handle.read())
        outputs = dict(layout.get("outputs") or {})
        outputs.update({"scene_layout": str(layout_path.resolve()), "scene_glb": str(glb_path.resolve())})
        layout["outputs"] = outputs
        layout_path.write_bytes(json_bytes(layout))
        base = scene_revision_for_layout(layout_path)
        edited = apply_scene_layout_edits(
            layout_path=layout_path,
            base_revision=int(base["revision"]),
            base_sha256=str(base["sha256"]),
            commands=commands,
            transform_policy="course_grounded",
        )
        next_layout_path = Path(edited["revision"]["layout_path"])
        next_glb_path = Path(edited["revision"]["scene_glb_path"])
        next_layout = json.loads(next_layout_path.read_text(encoding="utf-8"))
        return self.create_revision(
            actor_id,
            project_id,
            layout=next_layout,
            glb=next_glb_path.read_bytes(),
            source_id=source_id,
            parent_id=revision_id,
            branch_kind=branch_kind,
            label=label,
            commands=commands,
            provenance={"edit_protocol": "roadgen3d.scene_edit.v1", "edit_result": edited, **dict(provenance or {})},
        )

    # ---- metrics and comparisons -------------------------------------------------------
    def create_evaluation_profile(self, actor_id: str, course_id: str, *, name: str, weights: Mapping[str, Any]) -> dict[str, Any]:
        with self.database.session() as db:
            membership = self._require_membership(db, actor_id, course_id)
            if membership.role not in {"teacher", "admin"} and self._require_user(db, actor_id).system_role != "admin":
                raise Forbidden("Only teachers can publish evaluation profiles.")
            profile = EvaluationProfile(course_id=course_id, created_by=actor_id, name=name.strip()[:160], dimensions=_normalized_weights(weights))
            db.add(profile)
            db.flush()
            return self._profile(profile)

    def list_evaluation_profiles(self, actor_id: str, project_id: str) -> list[dict[str, Any]]:
        with self.database.session() as db:
            project, _ = self._require_project(db, actor_id, project_id)
            return [self._profile(item) for item in db.scalars(select(EvaluationProfile).where(EvaluationProfile.course_id == project.course_id).order_by(EvaluationProfile.is_default.desc(), EvaluationProfile.created_at)).all()]

    def create_evaluation_run(self, actor_id: str, project_id: str, *, revision_id: str, profile_id: str, weights: Mapping[str, Any] | None = None, seed: int = 20260713, evaluation_mode: str = "structured") -> dict[str, Any]:
        mode = str(evaluation_mode or "structured").strip().lower()
        if mode not in {"structured", "full"}:
            raise ValueError("evaluation_mode must be structured or full.")
        with self.database.session() as db:
            project, _ = self._require_project(db, actor_id, project_id, write=True)
            revision = db.get(SceneRevisionRecord, revision_id)
            profile = db.get(EvaluationProfile, profile_id)
            if revision is None or revision.project_id != project.id:
                raise NotFound("Revision not found in this project.")
            if profile is None or profile.course_id != project.course_id:
                raise NotFound("Evaluation profile not found in this course.")
            normalized = _normalized_weights(weights or profile.dimensions)
            run = EvaluationRun(project_id=project.id, revision_id=revision.id, profile_id=profile.id, requested_by=actor_id, weights=normalized, seed=int(seed), provenance={"profile_version": profile.version, "metric_contract": "road-metrics", "metric_implementation_version": "structured-v1" if mode == "structured" else "full-v1", "evaluation_mode": mode, "python": platform.python_version()})
            db.add(run)
            revision.evaluation_status = "queued"
            project.workflow_step = "evaluation"
            db.flush()
            return self._evaluation(run)

    def run_evaluation(self, actor_id: str, run_id: str, evaluator: Callable[..., Mapping[str, Any]]) -> dict[str, Any]:
        with self.database.session() as db:
            run = db.get(EvaluationRun, run_id)
            if run is None:
                raise NotFound("Evaluation run not found.")
            self._require_project(db, actor_id, run.project_id)
            revision = db.get(SceneRevisionRecord, run.revision_id)
            artifact = db.get(Artifact, revision.layout_artifact_id if revision else None)
            if revision is None or artifact is None:
                raise NotFound("Evaluation layout artifact is missing.")
            run.status = "running"
            revision.evaluation_status = "running"
            weights = dict(run.weights)
            evaluation_mode = str((run.provenance or {}).get("evaluation_mode") or "full")
        with self.artifacts.open(artifact.object_key) as handle, tempfile.TemporaryDirectory(prefix="roadgen3d-eval-") as tmp:
            layout_path = Path(tmp) / "scene_layout.json"
            layout_path.write_bytes(handle.read())
            try:
                result = dict(evaluator(
                    layout_path=str(layout_path),
                    evaluation_profile="auto",
                    evaluation_config={"aggregation": {"dimension_weights": weights}},
                    evaluation_mode=evaluation_mode,
                ))
                error = ""
                status = "succeeded"
            except Exception as exc:
                result = {}
                error = str(exc)
                status = "failed"
        with self.database.session() as db:
            run = db.get(EvaluationRun, run_id)
            revision = db.get(SceneRevisionRecord, run.revision_id) if run else None
            if run is None:
                raise NotFound("Evaluation run disappeared.")
            run.status = status
            run.result = result
            run.error = error
            run.provenance = {**dict(run.provenance), "finished_at": _iso(now_utc()), "llm_status": result.get("llm_status", {})}
            if revision:
                revision.evaluation_status = status
            self._audit(db, actor_id, run.project_id, "evaluation.finish", {"run_id": run.id, "status": status})
            return self._evaluation(run)

    def list_evaluations(self, actor_id: str, project_id: str) -> list[dict[str, Any]]:
        with self.database.session() as db:
            self._require_project(db, actor_id, project_id)
            return [self._evaluation(item) for item in db.scalars(select(EvaluationRun).where(EvaluationRun.project_id == project_id).order_by(EvaluationRun.created_at.desc())).all()]

    def compare_revisions(self, actor_id: str, project_id: str, revision_ids: Sequence[str]) -> dict[str, Any]:
        if not (2 <= len(revision_ids) <= 3):
            raise ValueError("Compare two or three revisions.")
        with self.database.session() as db:
            self._require_project(db, actor_id, project_id)
            items = []
            for revision_id in revision_ids:
                revision = db.get(SceneRevisionRecord, revision_id)
                if revision is None or revision.project_id != project_id:
                    raise NotFound("A comparison revision was not found in this project.")
                succeeded = db.scalars(select(EvaluationRun).where(EvaluationRun.revision_id == revision.id, EvaluationRun.status == "succeeded").order_by(EvaluationRun.created_at.desc())).all()
                latest_by_mode: dict[str, EvaluationRun] = {}
                for run in succeeded:
                    mode = str((run.provenance or {}).get("evaluation_mode") or "full")
                    latest_by_mode.setdefault(mode, run)
                evaluations = {mode: self._evaluation(run) for mode, run in latest_by_mode.items()}
                preferred = evaluations.get("full") or evaluations.get("structured")
                items.append({"revision": self._revision(revision), "evaluation": preferred, "evaluations": evaluations})
            baseline_scores = (items[0]["evaluation"] or {}).get("result", {})
            for item in items:
                scores = (item["evaluation"] or {}).get("result", {})
                item["score_delta"] = {key: _numeric_delta(scores.get(key), baseline_scores.get(key)) for key in ("walkability", "safety", "beauty", "overall")}
            return {"schema_version": "roadgen3d.revision_comparison.v1", "claim_scope": "traceable difference and correlation; not causal effect", "items": items}

    # ---- packages ----------------------------------------------------------------------
    def export_project_package(self, actor_id: str, project_id: str) -> dict[str, Any]:
        with self.database.session() as db:
            project, _ = self._require_project(db, actor_id, project_id)
            sources = db.scalars(select(SceneSourceRecord).where(SceneSourceRecord.project_id == project.id)).all()
            revisions = db.scalars(select(SceneRevisionRecord).where(SceneRevisionRecord.project_id == project.id)).all()
            evaluations = db.scalars(select(EvaluationRun).where(EvaluationRun.project_id == project.id)).all()
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                manifest = {
                    "schema_version": "roadgen3d.project_bundle.v1",
                    "project": self._project(project, "export"),
                    "sources": [self._source(item) for item in sources],
                    "revisions": [self._revision(item) for item in revisions],
                    "evaluations": [self._evaluation(item) for item in evaluations],
                    "exported_at": _iso(now_utc()),
                    "attribution": "Contains OpenStreetMap-derived data where source provenance says osm; © OpenStreetMap contributors.",
                }
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                artifact_ids = {item.normalized_artifact_id for item in sources} | {item.annotation_artifact_id for item in sources if item.annotation_artifact_id} | {item.layout_artifact_id for item in revisions if item.layout_artifact_id} | {item.glb_artifact_id for item in revisions if item.glb_artifact_id}
                for artifact_id in artifact_ids:
                    artifact = db.get(Artifact, artifact_id)
                    if artifact is None:
                        continue
                    with self.artifacts.open(artifact.object_key) as handle:
                        archive.writestr(f"artifacts/{artifact.id}-{Path(artifact.object_key).name}", handle.read())
            artifact = self._store_artifact(db, actor_id, project.id, "project_bundle", f"{project.id}-roadgen3d-project.zip", buffer.getvalue(), "application/zip")
            self._audit(db, actor_id, project.id, "project.export", {"artifact_id": artifact.id})
            return self._artifact(artifact)

    # ---- jobs --------------------------------------------------------------------------
    def create_job(self, actor_id: str, project_id: str | None, *, kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        with self.database.session() as db:
            if project_id:
                self._require_project(db, actor_id, project_id, write=True)
            active = int(db.scalar(select(func.count(Job.id)).where(Job.owner_id == actor_id, Job.status.in_(("queued", "running")))) or 0)
            limit = max(1, int(os.getenv("ROADGEN_MAX_ACTIVE_JOBS_PER_USER", "3")))
            if active >= limit:
                raise Conflict(f"Active job quota reached ({active}/{limit}).")
            job = Job(project_id=project_id, owner_id=actor_id, kind=kind, payload=dict(payload))
            db.add(job)
            db.flush()
            return self._job(job)

    def list_jobs(
        self,
        actor_id: str,
        project_id: str,
        *,
        kind: str | None = None,
        statuses: Sequence[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        with self.database.session() as db:
            self._require_project(db, actor_id, project_id)
            query = select(Job).where(Job.project_id == project_id)
            if kind:
                query = query.where(Job.kind == str(kind))
            normalized_statuses = tuple(str(item) for item in statuses or () if str(item))
            if normalized_statuses:
                query = query.where(Job.status.in_(normalized_statuses))
            jobs = db.scalars(query.order_by(Job.created_at.desc()).limit(max(1, min(int(limit), 100)))).all()
            return [self._job(item) for item in jobs]

    def recover_incomplete_jobs(self) -> list[str]:
        """Return durable queued work and reset jobs interrupted during a worker restart."""
        with self.database.session() as db:
            jobs = db.scalars(select(Job).where(Job.status.in_(("queued", "running")))).all()
            for job in jobs:
                if job.status == "running":
                    job.status = "queued"
                    job.progress = 0
                    job.stage = "queued"
                    job.message = "Recovered after worker restart."
                    job.detail = {"recovered": True}
                    job.error = "Recovered after worker restart."
            return [job.id for job in jobs]

    def execute_job(self, job_id: str, *, evaluator: Callable[..., Mapping[str, Any]] | None = None, generator: Callable[..., Mapping[str, Any]] | None = None) -> dict[str, Any]:
        with self.database.session() as db:
            job = db.get(Job, job_id)
            if job is None:
                raise NotFound("Job not found.")
            if job.status == "cancelled":
                return self._job(job)
            job.status = "running"
            job.progress = 5
            job.stage = "starting"
            job.message = "Starting teaching workflow."
            job.detail = {}
            job.attempts += 1
            attempt = int(job.attempts)
            owner_id = job.owner_id
            project_id = job.project_id
            kind = job.kind
            payload = dict(job.payload)
        try:
            if kind == "osm_import":
                result = self.import_osm(
                    owner_id,
                    str(project_id),
                    force_refetch=bool(payload.get("force_refetch")),
                    progress_callback=lambda event: self.update_job_progress(job_id, event),
                )
            elif kind == "osm_preview":
                result = self.create_osm_preview(
                    owner_id,
                    str(project_id),
                    force_refetch=bool(payload.get("force_refetch")),
                    progress_callback=lambda event: self.update_job_progress(job_id, event),
                )
            elif kind == "evaluation":
                if evaluator is None:
                    raise RuntimeError("No evaluator is configured for this worker.")
                result = self.run_evaluation(owner_id, str(payload.get("run_id")), evaluator)
            elif kind == "project_export":
                result = self.export_project_package(owner_id, str(project_id))
            elif kind == "scene_generate":
                if generator is None:
                    raise RuntimeError("No scene generator is configured for this worker.")
                result = self.generate_project_scene(
                    owner_id,
                    str(project_id),
                    source_id=str(payload.get("source_id")),
                    prompt=str(payload.get("prompt") or ""),
                    generation_mode=str(payload.get("generation_mode") or "baseline"),
                    parent_revision_id=(str(payload.get("parent_revision_id")) if payload.get("parent_revision_id") else None),
                    goal_weights=(payload.get("goal_weights") if isinstance(payload.get("goal_weights"), Mapping) else None),
                    candidate_count=int(payload.get("candidate_count") or 1),
                    minimum_scores=(payload.get("minimum_scores") if isinstance(payload.get("minimum_scores"), Mapping) else None),
                    generator=generator,
                    evaluator=evaluator,
                    progress_callback=lambda event: self.update_job_progress(job_id, event),
                )
            else:
                raise ValueError(f"Unsupported teaching job kind: {kind}")
        except Exception as exc:
            failure = public_job_failure(
                exc,
                job_id=job_id,
                kind=kind,
                attempt=attempt + int(payload.get("_retry_count", 0) or 0),
            )
            logger.exception(
                "Teaching job %s failed (debug_reference=%s, kind=%s)",
                job_id,
                failure["debug_reference"],
                kind,
            )
            return self.fail_job(job_id, failure=failure)
        self.update_job_progress(job_id, {
            "stage": "succeeded",
            "progress": 100,
            "message": "Scene generation and baseline evaluation completed." if kind == "scene_generate" else "Task completed.",
        })
        return self.update_job(job_id, status="succeeded", progress=100, result=result)

    def update_job_progress(self, job_id: str, event: Mapping[str, Any] | str) -> dict[str, Any]:
        payload = dict(event) if isinstance(event, Mapping) else {"message": str(event)}
        with self.database.session() as db:
            job = db.get(Job, job_id)
            if job is None:
                raise NotFound("Job not found.")
            if job.status == "cancelled":
                return self._job(job)
            stage = str(payload.get("stage") or job.stage or "running")
            message = str(payload.get("message") or stage.replace("_", " ").title())
            try:
                requested_progress = int(round(float(payload.get("progress", job.progress))))
            except (TypeError, ValueError):
                requested_progress = int(job.progress)
            progress = max(int(job.progress), max(0, min(100, requested_progress)))
            raw_detail = payload.get("detail")
            detail = dict(raw_detail) if isinstance(raw_detail, Mapping) else {
                key: value
                for key, value in payload.items()
                if key not in {"stage", "message", "progress"}
            }
            operation = {
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "stage": stage,
                "progress": progress,
                "message": message,
                "detail": detail,
            }
            job.stage = stage
            job.message = message
            job.progress = progress
            job.detail = detail
            job.operations = [*list(job.operations or []), operation][-50:]
            return self._job(job)

    def cancel_job(self, actor_id: str, job_id: str) -> dict[str, Any]:
        with self.database.session() as db:
            job = db.get(Job, job_id)
            if job is None:
                raise NotFound("Job not found.")
            if job.project_id:
                self._require_project(db, actor_id, job.project_id, write=True)
            elif job.owner_id != actor_id:
                raise Forbidden("This job belongs to another user.")
            if job.status not in {"queued", "running"}:
                raise Conflict("Only queued or running jobs can be cancelled.")
            job.status = "cancelled"
            job.stage = "cancelled"
            job.message = "Cancelled by user."
            job.error = "Cancelled by user."
            return self._job(job)

    def retry_job(self, actor_id: str, job_id: str) -> dict[str, Any]:
        with self.database.session() as db:
            job = db.get(Job, job_id)
            if job is None:
                raise NotFound("Job not found.")
            if job.project_id:
                self._require_project(db, actor_id, job.project_id, write=True)
            elif job.owner_id != actor_id:
                raise Forbidden("This job belongs to another user.")
            if job.status not in {"failed", "cancelled"}:
                raise Conflict("Only failed or cancelled jobs can be retried.")
            failure = dict(job.detail.get("failure") or {}) if isinstance(job.detail, Mapping) else {}
            if job.status == "failed" and failure and not bool(failure.get("retryable")):
                raise Conflict("This task cannot be retried until its input is corrected.")
            project_id, kind, payload = job.project_id, job.kind, dict(job.payload)
            payload["_retry_count"] = int(payload.get("_retry_count", 0) or 0) + 1
        return self.create_job(actor_id, project_id, kind=kind, payload=payload)

    def get_job(self, actor_id: str, job_id: str) -> dict[str, Any]:
        with self.database.session() as db:
            job = db.get(Job, job_id)
            if job is None:
                raise NotFound("Job not found.")
            if job.project_id:
                self._require_project(db, actor_id, job.project_id)
            elif job.owner_id != actor_id:
                raise Forbidden("This job belongs to another user.")
            return self._job(job)

    def update_job(self, job_id: str, *, status: str, progress: int, result: Mapping[str, Any] | None = None, error: str = "") -> dict[str, Any]:
        with self.database.session() as db:
            job = db.get(Job, job_id)
            if job is None:
                raise NotFound("Job not found.")
            if job.status == "cancelled":
                return self._job(job)
            job.status = status
            job.progress = max(0, min(100, int(progress)))
            job.result = dict(result or job.result)
            job.error = str(error)
            job.attempts += 1 if status == "running" else 0
            return self._job(job)

    def fail_job(self, job_id: str, *, failure: Mapping[str, Any]) -> dict[str, Any]:
        """Persist a terminal public failure without exposing internal exceptions."""

        with self.database.session() as db:
            job = db.get(Job, job_id)
            if job is None:
                raise NotFound("Job not found.")
            if job.status == "cancelled":
                return self._job(job)
            last_stage = str(job.stage or "starting")
            last_detail = dict(job.detail or {})
            progress = min(max(0, int(job.progress)), 99)
            public_failure = dict(failure)
            message = str(public_failure.get("user_message") or PUBLIC_SCENE_GENERATION_FAILURE_MESSAGE)
            detail = {
                "last_successful_stage": last_stage,
                "last_successful_detail": last_detail,
                "failure": public_failure,
            }
            operation = {
                "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "stage": "failed",
                "progress": progress,
                "message": message,
                "detail": {"failure": public_failure},
            }
            job.status = "failed"
            job.stage = "failed"
            job.progress = progress
            job.message = message
            job.detail = detail
            job.error = message
            job.operations = [*list(job.operations or []), operation][-50:]
            return self._job(job)

    # ---- authorization and serialization ----------------------------------------------
    @staticmethod
    def _bbox(value: Sequence[float]) -> list[float]:
        if len(value) != 4:
            raise ValueError("aoi_bbox must be [west, south, east, north].")
        west, south, east, north = [float(item) for item in value]
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError("aoi_bbox is reversed or outside WGS84.")
        if (east - west) * (north - south) > 0.25:
            raise ValueError("Course projects must use a bounded AOI no larger than 0.25 square degrees.")
        return [west, south, east, north]

    @staticmethod
    def _require_user(db: Session, user_id: str) -> User:
        user = db.get(User, user_id)
        if user is None or not user.is_active:
            raise Forbidden("User account not found or inactive.")
        return user

    def _require_admin(self, db: Session, user_id: str) -> User:
        user = self._require_user(db, user_id)
        if user.system_role != "admin":
            raise Forbidden("Administrator access is required.")
        return user

    @staticmethod
    def _create_workspace(db: Session, user: User, *, scope: str) -> Course:
        # The unique code is an internal database requirement only. It is never
        # returned through course endpoints or shown to a professional user.
        public = scope == "public"
        workspace = Course(
            name=f"{user.display_name} 的{'公共空间' if public else '专业工作区'}",
            code=f"{'PUB' if public else 'PW'}-{new_id().upper()[:24]}",
            invite_hash=digest_secret(new_id()),
            owner_id=user.id,
            scope=scope,
        )
        db.add(workspace)
        db.flush()
        db.add(Membership(course_id=workspace.id, user_id=user.id, role="student"))
        db.add(EvaluationProfile(
            course_id=workspace.id,
            created_by=user.id,
            name="公共项目结构化评价" if public else "个人项目结构化评价",
            dimensions=_normalized_weights(None),
            is_default=True,
        ))
        return workspace

    @staticmethod
    def _create_personal_workspace(db: Session, user: User) -> Course:
        return TeachingPlatformService._create_workspace(db, user, scope="personal")

    def _personal_workspace(self, db: Session, user_id: str, *, create: bool) -> Course:
        workspace = db.scalar(select(Course).where(
            Course.owner_id == user_id,
            Course.scope == "personal",
            Course.archived.is_(False),
        ))
        if workspace is not None:
            return workspace
        if not create:
            raise NotFound("Personal workspace not found.")
        user = self._require_user(db, user_id)
        return self._create_personal_workspace(db, user)

    def _workspace_for_actor(self, db: Session, user: User, *, create: bool) -> Course:
        scope = "public" if user.system_role == "guest" else "personal"
        workspace = db.scalar(select(Course).where(
            Course.owner_id == user.id,
            Course.scope == scope,
            Course.archived.is_(False),
        ))
        if workspace is not None:
            return workspace
        if not create:
            raise NotFound("Workspace not found.")
        return self._create_workspace(db, user, scope=scope)

    @staticmethod
    def _list_workspace_projects(db: Session, user_id: str, workspace_id: str) -> list[dict[str, Any]]:
        rows = db.scalars(select(Project).where(
            Project.course_id == workspace_id,
            Project.owner_id == user_id,
            Project.archived.is_(False),
        ).order_by(Project.updated_at.desc())).all()
        return [TeachingPlatformService._project(item, "owner") for item in rows]

    @staticmethod
    def _project_scope(db: Session, project: Project) -> str:
        course = db.get(Course, project.course_id)
        return course.scope if course is not None else "course"

    def _admin_user_summary(self, db: Session, user: User) -> dict[str, Any]:
        projects = db.scalars(select(Project).where(Project.owner_id == user.id, Project.archived.is_(False))).all()
        project_ids = [item.id for item in projects]
        jobs = db.scalars(select(Job).where(Job.owner_id == user.id)).all()
        job_statuses: dict[str, int] = {}
        for job in jobs:
            job_statuses[job.status] = job_statuses.get(job.status, 0) + 1
        storage_bytes = int(db.scalar(select(func.coalesce(func.sum(Artifact.byte_size), 0)).where(Artifact.owner_id == user.id)) or 0)
        last_activity = db.scalar(select(func.max(AuditLog.created_at)).where(AuditLog.user_id == user.id))
        return {
            **self._user(user),
            "is_active": user.is_active,
            "project_count": len(project_ids),
            "personal_project_count": sum(1 for item in projects if self._project_scope(db, item) == "personal"),
            "revision_count": int(db.scalar(select(func.count(SceneRevisionRecord.id)).where(SceneRevisionRecord.project_id.in_(project_ids))) or 0) if project_ids else 0,
            "storage_bytes": storage_bytes,
            "jobs_by_status": job_statuses,
            "last_activity_at": _iso(last_activity),
        }

    def _require_membership(self, db: Session, user_id: str, course_id: str) -> Membership:
        user = self._require_user(db, user_id)
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id, Membership.course_id == course_id))
        if membership is None:
            raise Forbidden("You are not a member of this course.")
        return membership

    def _require_project(self, db: Session, user_id: str, project_id: str, *, write: bool = False) -> tuple[Project, str]:
        project = db.get(Project, project_id)
        if project is None:
            raise NotFound("Project not found.")
        scope = self._project_scope(db, project)
        if scope == "public" and project.owner_id != user_id:
            if write:
                raise Forbidden("Only the guest who created this public project can modify it.")
            self._require_user(db, user_id)
            return project, "public_viewer"
        membership = self._require_membership(db, user_id, project.course_id)
        if project.owner_id == user_id:
            return project, "owner"
        if membership.role in {"teacher", "admin"}:
            return project, membership.role
        raise Forbidden("Students can only access their own projects.")

    @staticmethod
    def _require_public_project(db: Session, project_id: str) -> Project:
        project = db.get(Project, project_id)
        if project is None:
            raise NotFound("Public project not found.")
        workspace = db.get(Course, project.course_id)
        if workspace is None or workspace.scope != "public" or workspace.archived or project.archived:
            raise NotFound("Public project not found.")
        return project

    def _public_project_summary(self, db: Session, project: Project) -> dict[str, Any]:
        owner = db.get(User, project.owner_id)
        latest_revision = db.scalar(select(SceneRevisionRecord).where(
            SceneRevisionRecord.project_id == project.id,
        ).order_by(SceneRevisionRecord.revision_number.desc()).limit(1))
        latest_evaluation = None
        if latest_revision is not None:
            latest_evaluation = db.scalar(select(EvaluationRun).where(
                EvaluationRun.revision_id == latest_revision.id,
                EvaluationRun.status == "succeeded",
            ).order_by(EvaluationRun.created_at.desc()).limit(1))
        latest_bundle = db.scalar(select(Artifact).where(
            Artifact.project_id == project.id,
            Artifact.kind == "project_bundle",
        ).order_by(Artifact.created_at.desc()).limit(1))
        public_bundle = None
        if latest_bundle is not None:
            public_bundle = {
                **self._artifact(latest_bundle),
                "download_url": f"/api/v1/public/artifacts/{latest_bundle.id}",
            }
        return {
            "id": project.id,
            "name": project.name,
            "city": project.city,
            "design_goal": project.design_goal,
            "workflow_step": project.workflow_step,
            "author": owner.display_name if owner is not None else "RoadGen3D Guest",
            "updated_at": _iso(project.updated_at),
            "latest_revision": self._revision(latest_revision) if latest_revision is not None else None,
            "latest_evaluation": self._evaluation(latest_evaluation) if latest_evaluation is not None else None,
            "latest_bundle": public_bundle,
        }

    def _store_artifact(self, db: Session, owner_id: str, project_id: str, kind: str, filename: str, data: bytes | None, media_type: str) -> Artifact:
        if data is None:
            raise ValueError("Artifact data is required.")
        artifact = Artifact(id=new_id(), project_id=project_id, owner_id=owner_id, kind=kind, object_key="pending", media_type=media_type, byte_size=len(data), sha256=hashlib.sha256(data).hexdigest())
        artifact.object_key = safe_object_key(project_id, artifact.id, filename)
        self.artifacts.put(artifact.object_key, data, media_type=media_type)
        db.add(artifact)
        db.flush()
        return artifact

    @staticmethod
    def _audit(db: Session, user_id: str, project_id: str | None, action: str, detail: Mapping[str, Any]) -> None:
        db.add(AuditLog(user_id=user_id, project_id=project_id, action=action, detail=dict(detail)))

    @staticmethod
    def _user(item: User) -> dict[str, Any]:
        return {"id": item.id, "email": item.email, "display_name": item.display_name, "system_role": item.system_role, "created_at": _iso(item.created_at)}

    @staticmethod
    def _course(item: Course, role: str) -> dict[str, Any]:
        return {"id": item.id, "name": item.name, "code": item.code, "scope": item.scope, "role": role, "archived": item.archived, "created_at": _iso(item.created_at)}

    @staticmethod
    def _registration_invite(item: RegistrationInvite) -> dict[str, Any]:
        return {
            "id": item.id,
            "expires_at": _iso(item.expires_at),
            "max_uses": item.max_uses,
            "used_count": item.used_count,
            "is_active": item.is_active,
            "note": item.note,
            "created_at": _iso(item.created_at),
        }

    @staticmethod
    def _project(item: Project, role: str) -> dict[str, Any]:
        return {"id": item.id, "course_id": item.course_id, "owner_id": item.owner_id, "name": item.name, "city": item.city, "design_goal": item.design_goal, "aoi_bbox": item.aoi_bbox, "workflow_step": item.workflow_step, "asset_palette": _normalized_asset_palette(item.asset_palette), "role": role, "archived": item.archived, "created_at": _iso(item.created_at), "updated_at": _iso(item.updated_at)}

    @staticmethod
    def _source(item: SceneSourceRecord, normalized: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = {"id": item.id, "project_id": item.project_id, "kind": item.kind, "schema_version": item.schema_version, "raw_artifact_id": item.raw_artifact_id, "normalized_artifact_id": item.normalized_artifact_id, "annotation_artifact_id": item.annotation_artifact_id, "provenance": item.provenance, "quality_report": item.quality_report, "created_at": _iso(item.created_at)}
        payload.update({
            "role_counts": (normalized or {}).get("role_counts", item.provenance.get("role_counts", {})),
            "warnings": (normalized or {}).get("warnings", item.provenance.get("warnings", [])),
        })
        return payload

    @staticmethod
    def _revision(item: SceneRevisionRecord) -> dict[str, Any]:
        return {"id": item.id, "project_id": item.project_id, "source_id": item.source_id, "parent_id": item.parent_id, "revision_number": item.revision_number, "branch_kind": item.branch_kind, "label": item.label, "layout_artifact_id": item.layout_artifact_id, "glb_artifact_id": item.glb_artifact_id, "commands": item.commands, "provenance": item.provenance, "evaluation_status": item.evaluation_status, "created_at": _iso(item.created_at)}

    @staticmethod
    def _profile(item: EvaluationProfile) -> dict[str, Any]:
        return {"id": item.id, "course_id": item.course_id, "name": item.name, "version": item.version, "weights": item.dimensions, "is_default": item.is_default, "created_at": _iso(item.created_at)}

    @staticmethod
    def _evaluation(item: EvaluationRun) -> dict[str, Any]:
        return {"id": item.id, "project_id": item.project_id, "revision_id": item.revision_id, "profile_id": item.profile_id, "status": item.status, "seed": item.seed, "weights": item.weights, "result": item.result, "provenance": item.provenance, "error": item.error, "created_at": _iso(item.created_at), "updated_at": _iso(item.updated_at)}

    @staticmethod
    def _artifact(item: Artifact) -> dict[str, Any]:
        return {"id": item.id, "project_id": item.project_id, "kind": item.kind, "media_type": item.media_type, "byte_size": item.byte_size, "sha256": item.sha256, "download_url": f"/api/v1/artifacts/{item.id}"}

    @staticmethod
    def _job(item: Job) -> dict[str, Any]:
        return {
            "id": item.id,
            "project_id": item.project_id,
            "kind": item.kind,
            "status": item.status,
            "progress": item.progress,
            "stage": item.stage,
            "message": item.message,
            "detail": item.detail,
            "operations": item.operations,
            "result": item.result,
            "error": item.error,
            "attempts": item.attempts,
            "created_at": _iso(item.created_at),
            "updated_at": _iso(item.updated_at),
        }


def json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def secrets_equal(left: str, right: str) -> bool:
    import hmac

    return hmac.compare_digest(str(left), str(right))


def _numeric_delta(value: Any, baseline: Any) -> float | None:
    try:
        if value is None or baseline is None:
            return None
        return round(float(value) - float(baseline), 6)
    except (TypeError, ValueError):
        return None
