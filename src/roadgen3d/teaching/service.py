"""Application service enforcing tenant boundaries and reproducible project state."""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import tempfile
import zipfile
from datetime import timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from roadgen3d.osm_ingest import fetch_osm_data
from roadgen3d.scene_layout_edits import apply_scene_layout_edits, scene_revision_for_layout

from .artifacts import ArtifactStore, create_artifact_store, safe_object_key
from .auth import digest_secret, hash_password, issue_invite_code, issue_session_token, normalize_email, verify_password
from .database import TeachingDatabase
from .geojson_pipeline import normalize_teaching_geojson, raw_osm_to_geojson, round_trip_report
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
    SceneRevisionRecord,
    SceneSourceRecord,
    User,
    new_id,
    now_utc,
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
            return self._user(user)

    def login(self, *, email: str, password: str) -> dict[str, Any]:
        with self.database.session() as db:
            user = db.scalar(select(User).where(User.email == normalize_email(email)))
            if user is None or not user.is_active or not verify_password(password, user.password_hash):
                raise Forbidden("Invalid email or password.")
            token, token_hash, expires_at = issue_session_token()
            db.add(AuthSession(user_id=user.id, token_hash=token_hash, expires_at=expires_at))
            return {"access_token": token, "token_type": "bearer", "expires_at": _iso(expires_at), "user": self._user(user)}

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
            return self._user(user)

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
            return self._user(user)

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
                if course is not None:
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

    # ---- sources and artifacts ---------------------------------------------------------
    def import_geojson(self, actor_id: str, project_id: str, payload: Mapping[str, Any], *, kind: str = "geojson", provenance: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self.database.session() as db:
            project, _ = self._require_project(db, actor_id, project_id, write=True)
            source_id = new_id()
            normalized = normalize_teaching_geojson(payload, source_id=source_id, bbox=project.aoi_bbox)
            raw_artifact = self._store_artifact(db, actor_id, project.id, "source_geojson_raw", f"{source_id}-raw.geojson", json_bytes(payload), "application/geo+json")
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
        geojson: Mapping[str, Any],
        actions: Sequence[Mapping[str, Any]] | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        """Persist a reviewed annotation as a new immutable source version."""
        with self.database.session() as db:
            project, _ = self._require_project(db, actor_id, project_id, write=True)
            parent = db.get(SceneSourceRecord, source_id)
            if parent is None or parent.project_id != project.id:
                raise NotFound("Scene source not found in this project.")
            artifact = db.get(Artifact, parent.normalized_artifact_id)
            if artifact is None:
                raise NotFound("The source GeoJSON artifact is missing.")
            parent_key = artifact.object_key
        with self.artifacts.open(parent_key) as handle:
            parent_geojson = json.loads(handle.read().decode("utf-8"))

        action_log = [dict(item) for item in actions or []]
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

    def import_osm(self, actor_id: str, project_id: str, *, force_refetch: bool = False) -> dict[str, Any]:
        with self.database.session() as db:
            project, _ = self._require_project(db, actor_id, project_id, write=True)
            if not project.aoi_bbox:
                raise ValueError("Select an AOI before importing OSM.")
            bbox = tuple(float(item) for item in project.aoi_bbox)
        raw = fetch_osm_data(bbox, Path(os.getenv("ROADGEN_OSM_CACHE", "artifacts/osm_cache")), force_refetch=force_refetch)
        geojson = raw_osm_to_geojson(raw)
        return self.import_geojson(actor_id, project_id, geojson, kind="osm", provenance={
            "provider": "OpenStreetMap/Overpass",
            "attribution": "© OpenStreetMap contributors",
            "bbox": list(bbox),
            "fetched_at": _iso(now_utc()),
            "raw_element_count": len(raw.get("elements", [])),
        })

    def generate_project_scene(
        self,
        actor_id: str,
        project_id: str,
        *,
        source_id: str,
        prompt: str,
        generator: Callable[..., Mapping[str, Any]],
        evaluator: Callable[..., Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        from roadgen3d.llm.design_workflow import parse_design_draft

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
        with self.artifacts.open(annotation_key) as handle:
            annotation = json.loads(handle.read().decode("utf-8"))
        draft = parse_design_draft(
            {
                "normalized_scene_query": query,
                "compose_config_patch": {"query": query},
                "design_summary": f"Course project generation for {query}",
                "risk_notes": [],
            },
            evidence=(),
            fallback_query=query,
            current_patch={},
        )
        result = dict(generator(
            draft,
            scene_context={
                "layout_mode": "reference_annotation",
                "reference_annotation": annotation,
                "source_context": source_context,
            },
            generation_options={"course_project_id": project_id, "skip_llm": True},
        ))
        layout_path = Path(str(result.get("scene_layout_path") or result.get("layout_path") or "")).expanduser().resolve()
        glb_path = Path(str(result.get("scene_glb_path") or result.get("glb_path") or "")).expanduser().resolve()
        if not layout_path.is_file() or not glb_path.is_file():
            raise RuntimeError("Scene generation did not produce scene_layout.json and scene.glb.")
        revision = self.create_revision(
            actor_id,
            project_id,
            layout=json.loads(layout_path.read_text(encoding="utf-8")),
            glb=glb_path.read_bytes(),
            source_id=source_id,
            parent_id=None,
            branch_kind="baseline",
            label="Generated baseline",
            provenance={"generation_method": "course_reference_annotation", "prompt": query, "generator_result": result},
        )
        evaluation = None
        if evaluator is not None:
            profiles = self.list_evaluation_profiles(actor_id, project_id)
            profile = next((item for item in profiles if item["is_default"]), profiles[0] if profiles else None)
            if profile:
                evaluation = self.create_evaluation_run(actor_id, project_id, revision_id=revision["id"], profile_id=profile["id"])
                evaluation = self.run_evaluation(actor_id, evaluation["id"], evaluator)
        return {"revision": revision, "evaluation": evaluation}

    def list_sources(self, actor_id: str, project_id: str) -> list[dict[str, Any]]:
        with self.database.session() as db:
            self._require_project(db, actor_id, project_id)
            return [self._source(item) for item in db.scalars(select(SceneSourceRecord).where(SceneSourceRecord.project_id == project_id).order_by(SceneSourceRecord.created_at.desc())).all()]

    def artifact(self, actor_id: str, artifact_id: str) -> tuple[Artifact, Any]:
        with self.database.session() as db:
            artifact = db.get(Artifact, artifact_id)
            if artifact is None:
                raise NotFound("Artifact not found.")
            self._require_project(db, actor_id, artifact.project_id)
            db.expunge(artifact)
        return artifact, self.artifacts.open(artifact.object_key)

    # ---- scene revisions ---------------------------------------------------------------
    def create_revision(self, actor_id: str, project_id: str, *, layout: Mapping[str, Any], glb: bytes | None, source_id: str | None, parent_id: str | None, branch_kind: str, label: str, commands: Sequence[Mapping[str, Any]] | None = None, provenance: Mapping[str, Any] | None = None) -> dict[str, Any]:
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
                provenance={"schema_version": "roadgen3d.scene_revision.v1", **dict(provenance or {})},
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

    def create_evaluation_run(self, actor_id: str, project_id: str, *, revision_id: str, profile_id: str, weights: Mapping[str, Any] | None = None, seed: int = 20260713) -> dict[str, Any]:
        with self.database.session() as db:
            project, _ = self._require_project(db, actor_id, project_id, write=True)
            revision = db.get(SceneRevisionRecord, revision_id)
            profile = db.get(EvaluationProfile, profile_id)
            if revision is None or revision.project_id != project.id:
                raise NotFound("Revision not found in this project.")
            if profile is None or profile.course_id != project.course_id:
                raise NotFound("Evaluation profile not found in this course.")
            normalized = _normalized_weights(weights or profile.dimensions)
            run = EvaluationRun(project_id=project.id, revision_id=revision.id, profile_id=profile.id, requested_by=actor_id, weights=normalized, seed=int(seed), provenance={"profile_version": profile.version, "metric_contract": "road-metrics", "python": platform.python_version()})
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
        with self.artifacts.open(artifact.object_key) as handle, tempfile.TemporaryDirectory(prefix="roadgen3d-eval-") as tmp:
            layout_path = Path(tmp) / "scene_layout.json"
            layout_path.write_bytes(handle.read())
            try:
                result = dict(evaluator(
                    layout_path=str(layout_path),
                    evaluation_profile="auto",
                    evaluation_config={"aggregation": {"dimension_weights": weights}},
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
                latest = db.scalar(select(EvaluationRun).where(EvaluationRun.revision_id == revision.id, EvaluationRun.status == "succeeded").order_by(EvaluationRun.created_at.desc()))
                items.append({"revision": self._revision(revision), "evaluation": self._evaluation(latest) if latest else None})
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

    def recover_incomplete_jobs(self) -> list[str]:
        """Return durable queued work and reset jobs interrupted during a worker restart."""
        with self.database.session() as db:
            jobs = db.scalars(select(Job).where(Job.status.in_(("queued", "running")))).all()
            for job in jobs:
                if job.status == "running":
                    job.status = "queued"
                    job.progress = 0
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
            job.attempts += 1
            owner_id = job.owner_id
            project_id = job.project_id
            kind = job.kind
            payload = dict(job.payload)
        try:
            if kind == "osm_import":
                result = self.import_osm(owner_id, str(project_id), force_refetch=bool(payload.get("force_refetch")))
            elif kind == "evaluation":
                if evaluator is None:
                    raise RuntimeError("No evaluator is configured for this worker.")
                result = self.run_evaluation(owner_id, str(payload.get("run_id")), evaluator)
            elif kind == "project_export":
                result = self.export_project_package(owner_id, str(project_id))
            elif kind == "scene_generate":
                if generator is None:
                    raise RuntimeError("No scene generator is configured for this worker.")
                result = self.generate_project_scene(owner_id, str(project_id), source_id=str(payload.get("source_id")), prompt=str(payload.get("prompt") or ""), generator=generator, evaluator=evaluator)
            else:
                raise ValueError(f"Unsupported teaching job kind: {kind}")
        except Exception as exc:
            return self.update_job(job_id, status="failed", progress=100, error=str(exc))
        return self.update_job(job_id, status="succeeded", progress=100, result=result)

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
            project_id, kind, payload = job.project_id, job.kind, dict(job.payload)
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

    def _require_membership(self, db: Session, user_id: str, course_id: str) -> Membership:
        user = self._require_user(db, user_id)
        membership = db.scalar(select(Membership).where(Membership.user_id == user.id, Membership.course_id == course_id))
        if membership is None and user.system_role != "admin":
            raise Forbidden("You are not a member of this course.")
        return membership or Membership(course_id=course_id, user_id=user.id, role="admin")

    def _require_project(self, db: Session, user_id: str, project_id: str, *, write: bool = False) -> tuple[Project, str]:
        project = db.get(Project, project_id)
        if project is None:
            raise NotFound("Project not found.")
        membership = self._require_membership(db, user_id, project.course_id)
        if project.owner_id == user_id:
            return project, "owner"
        if membership.role in {"teacher", "admin"}:
            return project, membership.role
        raise Forbidden("Students can only access their own projects.")

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
        return {"id": item.id, "name": item.name, "code": item.code, "role": role, "archived": item.archived, "created_at": _iso(item.created_at)}

    @staticmethod
    def _project(item: Project, role: str) -> dict[str, Any]:
        return {"id": item.id, "course_id": item.course_id, "owner_id": item.owner_id, "name": item.name, "city": item.city, "design_goal": item.design_goal, "aoi_bbox": item.aoi_bbox, "workflow_step": item.workflow_step, "role": role, "archived": item.archived, "created_at": _iso(item.created_at), "updated_at": _iso(item.updated_at)}

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
        return {"id": item.id, "project_id": item.project_id, "kind": item.kind, "status": item.status, "progress": item.progress, "result": item.result, "error": item.error, "attempts": item.attempts, "created_at": _iso(item.created_at), "updated_at": _iso(item.updated_at)}


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
