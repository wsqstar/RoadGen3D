"""Multi-tenant course and project API."""

from __future__ import annotations

import base64
import io
import os
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from roadgen3d.teaching.jobs import enqueue_job
from roadgen3d.teaching.service import TeachingError, TeachingPlatformService
from roadgen3d.eval_engine_ext.road_metrics.evaluators.llm_client import public_llm_capabilities_from_env
from web.api.teaching_schemas import (
    AnnotationReviewRequest,
    BootstrapRequest,
    CourseCreateRequest,
    EvaluationCreateRequest,
    EvaluationProfileCreateRequest,
    GeoJsonImportRequest,
    GuestRecoverRequest,
    LoginRequest,
    OsmImportRequest,
    OsmRoadStudySelectionRequest,
    ProjectCreateRequest,
    PersonalRegisterRequest,
    RegisterRequest,
    RegistrationInviteCreateRequest,
    RevisionCompareRequest,
    RevisionCreateRequest,
    RevisionEditRequest,
    RevisionForkRequest,
    ReferenceAnnotationImportRequest,
    RevisionImportLayoutRequest,
    SceneGenerateRequest,
    SceneJobAdoptRequest,
    SceneAssetPaletteModel,
    UserStatusUpdateRequest,
    WorkflowStepRequest,
    WorkspaceProjectCreateRequest,
)


router = APIRouter(prefix="/api/v1", tags=["teaching-platform"])
bearer = HTTPBearer(auto_error=False)
GUEST_SESSION_COOKIE = "roadgen3d_guest_session"
GUEST_SESSION_MAX_AGE = 365 * 24 * 60 * 60


def _dispatch_job(request: Request, job: dict[str, Any]) -> dict[str, Any]:
    mode = os.getenv("ROADGEN_JOB_MODE", "inline").strip().lower()
    if mode == "rq":
        enqueue_job(job["id"])
        return job
    if mode == "local":
        request.app.state.teaching_job_executor.submit(job["id"])
        return job
    if mode != "inline":
        raise ValueError("ROADGEN_JOB_MODE must be inline, local, or rq.")
    design = request.app.state.design_service
    return _service(request).execute_job(
        job["id"],
        evaluator=design.evaluate_scene_unified,
        generator=design.generate_scene,
    )


def _service(request: Request) -> TeachingPlatformService:
    return request.app.state.teaching_service


def _actor(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict[str, Any]:
    try:
        token = credentials.credentials if credentials else request.cookies.get(GUEST_SESSION_COOKIE, "")
        return _service(request).authenticate(token)
    except TeachingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail()) from exc


def _call(callback):
    try:
        return callback()
    except TeachingError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "validation_error", "message": str(exc)}) from exc


def _auto_evaluate_revision(
    request: Request,
    actor: dict[str, Any],
    project_id: str,
    revision: dict[str, Any],
    *,
    auto_evaluate: bool,
    profile_id: str | None,
    weights: dict[str, float] | None,
    evaluation_mode: str = "structured",
) -> dict[str, Any]:
    if not auto_evaluate:
        return {**revision, "auto_evaluation": None, "evaluation_job": None}
    service = _service(request)
    profiles = service.list_evaluation_profiles(actor["id"], project_id)
    selected = next((item for item in profiles if item["id"] == profile_id), None) if profile_id else next((item for item in profiles if item["is_default"]), profiles[0] if profiles else None)
    if selected is None:
        return {**revision, "auto_evaluation": None, "evaluation_job": None}
    evaluation = service.create_evaluation_run(actor["id"], project_id, revision_id=revision["id"], profile_id=selected["id"], weights=weights, evaluation_mode=evaluation_mode)
    job = service.create_job(actor["id"], project_id, kind="evaluation", payload={"run_id": evaluation["id"]})
    if os.getenv("ROADGEN_JOB_MODE", "inline").strip().lower() in {"rq", "local"}:
        _dispatch_job(request, job)
        return {**revision, "auto_evaluation": evaluation, "evaluation_job": job}
    completed = _dispatch_job(request, job)
    refreshed = next(item for item in service.list_evaluations(actor["id"], project_id) if item["id"] == evaluation["id"])
    return {**revision, "auto_evaluation": refreshed, "evaluation_job": completed}


@router.post("/auth/bootstrap", status_code=201)
def bootstrap(body: BootstrapRequest, request: Request):
    return _call(lambda: _service(request).bootstrap_admin(email=body.email, password=body.password, display_name=body.display_name, token=body.bootstrap_token))


@router.get("/auth/bootstrap-status")
def bootstrap_status(request: Request):
    return _call(lambda: _service(request).bootstrap_status())


@router.post("/auth/login")
def login(body: LoginRequest, request: Request):
    return _call(lambda: _service(request).login(email=body.email, password=body.password))


def _set_guest_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        GUEST_SESSION_COOKIE,
        token,
        max_age=GUEST_SESSION_MAX_AGE,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )


@router.post("/auth/guest", status_code=201)
def guest(request: Request, response: Response):
    result = _call(lambda: _service(request).create_guest_session())
    _set_guest_cookie(response, request, result["access_token"])
    return result


@router.post("/auth/guest/recover")
def recover_guest(body: GuestRecoverRequest, request: Request, response: Response):
    result = _call(lambda: _service(request).recover_guest_session(body.recovery_key))
    _set_guest_cookie(response, request, result["access_token"])
    return result


@router.get("/auth/guest-recovery-key")
def guest_recovery_key(request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).guest_recovery_key(actor["id"]))


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    token = credentials.credentials if credentials else request.cookies.get(GUEST_SESSION_COOKIE, "")
    _call(lambda: _service(request).logout(token))
    response.delete_cookie(GUEST_SESSION_COOKIE, path="/")


@router.post("/auth/register", status_code=201)
def register(body: RegisterRequest, request: Request):
    return _call(lambda: _service(request).register_student(email=body.email, password=body.password, display_name=body.display_name, course_code=body.course_code, invite_code=body.invite_code))


@router.post("/auth/register-personal", status_code=201)
def register_personal(body: PersonalRegisterRequest, request: Request):
    return _call(lambda: _service(request).register_personal(
        email=body.email,
        password=body.password,
        display_name=body.display_name,
        invite_code=body.invite_code,
    ))


@router.get("/me")
def me(actor: dict[str, Any] = Depends(_actor)):
    return actor


@router.get("/workspace")
def workspace(request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).workspace(actor["id"]))


@router.get("/workspace/projects")
def workspace_projects(request: Request, actor: dict[str, Any] = Depends(_actor)):
    return {"items": _call(lambda: _service(request).list_workspace_projects(actor["id"]))}


@router.post("/workspace/projects", status_code=201)
def create_workspace_project(body: WorkspaceProjectCreateRequest, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).create_workspace_project(
        actor["id"],
        name=body.name,
        city=body.city,
        design_goal=body.design_goal,
        aoi_bbox=body.aoi_bbox,
    ))


@router.get("/public/projects")
def public_projects(request: Request):
    return {"items": _call(lambda: _service(request).list_public_projects())}


@router.get("/public/projects/{project_id}")
def public_project(project_id: str, request: Request):
    return _call(lambda: _service(request).public_project(project_id))


@router.get("/public/projects/{project_id}/revisions")
def public_project_revisions(project_id: str, request: Request):
    return {"items": _call(lambda: _service(request).public_revisions(project_id))}


@router.get("/public/projects/{project_id}/revisions/{revision_id}/viewer-manifest")
def public_revision_viewer_manifest(project_id: str, revision_id: str, request: Request):
    return _call(lambda: _service(request).public_viewer_manifest(project_id, revision_id))


@router.get("/public/artifacts/{artifact_id}")
def download_public_artifact(artifact_id: str, request: Request):
    artifact, handle = _call(lambda: _service(request).public_artifact(artifact_id))
    filename = artifact.object_key.rsplit("/", 1)[-1]
    return StreamingResponse(handle, media_type=artifact.media_type, headers={
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Content-SHA256": artifact.sha256,
        "Cache-Control": "public, max-age=300",
    })


@router.get("/admin/overview")
def admin_overview(request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).admin_overview(actor["id"]))


@router.get("/admin/users")
def admin_users(
    request: Request,
    query: str = Query(default="", max_length=160),
    active: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    actor: dict[str, Any] = Depends(_actor),
):
    return {"items": _call(lambda: _service(request).admin_users(actor["id"], query=query, active=active, limit=limit))}


@router.get("/admin/users/{user_id}")
def admin_user(user_id: str, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).admin_user(actor["id"], user_id))


@router.post("/admin/users/{user_id}/status")
def admin_user_status(user_id: str, body: UserStatusUpdateRequest, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).set_user_active(actor["id"], user_id, is_active=body.is_active))


@router.get("/admin/registration-invites")
def admin_registration_invites(request: Request, actor: dict[str, Any] = Depends(_actor)):
    return {"items": _call(lambda: _service(request).list_registration_invites(actor["id"]))}


@router.post("/admin/registration-invites", status_code=201)
def create_registration_invite(body: RegistrationInviteCreateRequest, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).create_registration_invite(
        actor["id"],
        expires_in_hours=body.expires_in_hours,
        max_uses=body.max_uses,
        note=body.note,
    ))


@router.post("/admin/registration-invites/{invite_id}/revoke")
def revoke_registration_invite(invite_id: str, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).revoke_registration_invite(actor["id"], invite_id))


@router.get("/capabilities")
def capabilities(actor: dict[str, Any] = Depends(_actor)):
    """Expose safe runtime choices without returning credentials or paths."""

    llm = public_llm_capabilities_from_env()
    return {
        "llm": llm,
        "design_generation": {
            "baseline": "parametric",
            "redesign_default": "parametric",
            "parametric_fallback": True,
            "llm_parameter_proposals": bool(llm.get("configured")),
        },
        "rag": {
            "mode": "disabled",
            "product_available": False,
            "experimental_api_available": False,
        },
    }


@router.get("/courses")
def courses(request: Request, actor: dict[str, Any] = Depends(_actor)):
    return {"items": _call(lambda: _service(request).list_courses(actor["id"]))}


@router.post("/courses", status_code=201)
def create_course(body: CourseCreateRequest, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).create_course(actor["id"], name=body.name, code=body.code))


@router.get("/projects")
def projects(request: Request, course_id: str | None = Query(default=None), actor: dict[str, Any] = Depends(_actor)):
    return {"items": _call(lambda: _service(request).list_projects(actor["id"], course_id=course_id))}


@router.post("/projects", status_code=201)
def create_project(body: ProjectCreateRequest, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).create_project(actor["id"], course_id=body.course_id, name=body.name, city=body.city, design_goal=body.design_goal, aoi_bbox=body.aoi_bbox))


@router.get("/projects/{project_id}")
def get_project(project_id: str, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).get_project(actor["id"], project_id))


@router.patch("/projects/{project_id}/workflow")
def update_workflow(project_id: str, body: WorkflowStepRequest, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).update_project_step(actor["id"], project_id, body.workflow_step))


@router.get("/projects/{project_id}/asset-palette")
def get_asset_palette(project_id: str, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).get_asset_palette(actor["id"], project_id))


@router.put("/projects/{project_id}/asset-palette")
def update_asset_palette(project_id: str, body: SceneAssetPaletteModel, request: Request, actor: dict[str, Any] = Depends(_actor)):
    payload = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    return _call(lambda: _service(request).update_asset_palette(actor["id"], project_id, payload))


@router.post("/projects/{project_id}/sources/geojson", status_code=201)
def import_geojson(project_id: str, body: GeoJsonImportRequest, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).import_geojson(actor["id"], project_id, body.geojson))


@router.post("/projects/{project_id}/sources/reference-annotation", status_code=201)
def import_reference_annotation(project_id: str, body: ReferenceAnnotationImportRequest, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).import_reference_annotation(actor["id"], project_id, body.annotation))


@router.post("/projects/{project_id}/sources/osm", status_code=202)
def import_osm(project_id: str, body: OsmImportRequest, request: Request, actor: dict[str, Any] = Depends(_actor)):
    def run():
        service = _service(request)
        job = service.create_job(actor["id"], project_id, kind="osm_import", payload={"force_refetch": body.force_refetch})
        return _dispatch_job(request, job)
    return _call(run)


@router.post("/projects/{project_id}/osm-previews", status_code=202)
def create_osm_preview(project_id: str, body: OsmImportRequest, request: Request, actor: dict[str, Any] = Depends(_actor)):
    def run():
        service = _service(request)
        job = service.create_job(actor["id"], project_id, kind="osm_preview", payload={"force_refetch": body.force_refetch})
        return _dispatch_job(request, job)
    return _call(run)


@router.post("/projects/{project_id}/osm-previews/{preview_id}/selection", status_code=201)
def select_osm_preview(
    project_id: str,
    preview_id: str,
    body: OsmRoadStudySelectionRequest,
    request: Request,
    actor: dict[str, Any] = Depends(_actor),
):
    if body.preview_id != preview_id:
        raise HTTPException(status_code=422, detail={"code": "validation_error", "message": "preview_id does not match the route."})
    return _call(lambda: _service(request).select_osm_preview(
        actor["id"],
        project_id,
        raw_artifact_id=body.raw_artifact_id,
        preview_id=preview_id,
        seed_logical_road_id=body.seed_logical_road_id,
        hop_count=body.hop_count,
        context_buffer_m=body.context_buffer_m,
    ))


@router.post("/projects/{project_id}/osm-previews/{preview_id}/selection-preview")
def preview_osm_selection(
    project_id: str,
    preview_id: str,
    body: OsmRoadStudySelectionRequest,
    request: Request,
    actor: dict[str, Any] = Depends(_actor),
):
    if body.preview_id != preview_id:
        raise HTTPException(status_code=422, detail={"code": "validation_error", "message": "preview_id does not match the route."})
    return _call(lambda: _service(request).preview_osm_selection(
        actor["id"],
        project_id,
        raw_artifact_id=body.raw_artifact_id,
        preview_id=preview_id,
        seed_logical_road_id=body.seed_logical_road_id,
        hop_count=body.hop_count,
        context_buffer_m=body.context_buffer_m,
    ))


@router.get("/projects/{project_id}/sources")
def list_sources(project_id: str, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return {"items": _call(lambda: _service(request).list_sources(actor["id"], project_id))}


@router.get("/projects/{project_id}/sources/{source_id}/workflow-source")
def get_workflow_source(project_id: str, source_id: str, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).workflow_source(actor["id"], project_id, source_id))


@router.post("/projects/{project_id}/sources/{source_id}/review", status_code=201)
def approve_source_review(project_id: str, source_id: str, body: AnnotationReviewRequest, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).approve_source_review(
        actor["id"],
        project_id,
        source_id,
        annotation=body.annotation,
        geojson=body.geojson,
        actions=body.actions,
        notes=body.notes,
    ))


@router.post("/projects/{project_id}/generate", status_code=202)
def generate_scene(project_id: str, body: SceneGenerateRequest, request: Request, actor: dict[str, Any] = Depends(_actor)):
    def run():
        service = _service(request)
        job = service.create_job(
            actor["id"],
            project_id,
            kind="scene_generate",
            payload={
                "source_id": body.source_id,
                "prompt": body.prompt,
                "generation_mode": body.generation_mode,
                "parent_revision_id": body.parent_revision_id,
                "goal_weights": body.goal_weights,
                "candidate_count": body.candidate_count,
                "minimum_scores": body.minimum_scores,
            },
            deduplicate_active=True,
        )
        return _dispatch_job(request, job)
    return _call(run)


@router.post("/projects/{project_id}/adopt-scene-job", status_code=201)
def adopt_scene_job(project_id: str, body: SceneJobAdoptRequest, request: Request, actor: dict[str, Any] = Depends(_actor)):
    def run():
        scene_job = request.app.state.design_service.get_scene_job(body.job_id)
        if scene_job is None:
            raise TeachingError(f"Scene job not found: {body.job_id}")
        if scene_job.status != "succeeded" or scene_job.result is None:
            raise TeachingError("Only a completed scene job can be saved to a project.")
        revision = _service(request).adopt_generated_scene(
            actor["id"],
            project_id,
            source_id=body.source_id,
            job_id=body.job_id,
            result=scene_job.result.to_dict(),
        )
        return _auto_evaluate_revision(
            request,
            actor,
            project_id,
            revision,
            auto_evaluate=True,
            profile_id=None,
            weights=None,
            evaluation_mode="structured",
        )
    return _call(run)


@router.post("/projects/{project_id}/revisions", status_code=201)
def create_revision(project_id: str, body: RevisionCreateRequest, request: Request, actor: dict[str, Any] = Depends(_actor)):
    def run():
        glb = base64.b64decode(body.glb_base64, validate=True) if body.glb_base64 else None
        revision = _service(request).create_revision(actor["id"], project_id, layout=body.layout, glb=glb, source_id=body.source_id, parent_id=body.parent_id, branch_kind=body.branch_kind, label=body.label, commands=body.commands, provenance=body.provenance)
        return _auto_evaluate_revision(request, actor, project_id, revision, auto_evaluate=body.auto_evaluate, profile_id=body.evaluation_profile_id, weights=body.evaluation_weights, evaluation_mode=body.auto_evaluate_mode)
    return _call(run)


@router.post("/projects/{project_id}/revisions/import-layout", status_code=201)
def import_layout_revision(project_id: str, body: RevisionImportLayoutRequest, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).import_layout_revision(
        actor["id"],
        project_id,
        layout_path=body.layout_path,
        label=body.label,
        source_id=body.source_id,
    ))


@router.post("/projects/{project_id}/revisions/{revision_id}/edits", status_code=201)
def edit_revision(project_id: str, revision_id: str, body: RevisionEditRequest, request: Request, actor: dict[str, Any] = Depends(_actor)):
    def run():
        service = _service(request)
        revision = service.edit_revision(actor["id"], project_id, revision_id, commands=body.commands, branch_kind=body.branch_kind, label=body.label, provenance=body.provenance)
        return _auto_evaluate_revision(request, actor, project_id, revision, auto_evaluate=body.auto_evaluate, profile_id=body.evaluation_profile_id, weights=body.evaluation_weights, evaluation_mode=body.auto_evaluate_mode)
    return _call(run)


@router.post("/projects/{project_id}/revisions/{revision_id}/fork", status_code=201)
def fork_revision(project_id: str, revision_id: str, body: RevisionForkRequest, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).fork_revision(
        actor["id"],
        project_id,
        revision_id,
        branch_kind=body.branch_kind,
        label=body.label,
        provenance=body.provenance,
    ))


@router.get("/projects/{project_id}/revisions")
def list_revisions(project_id: str, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return {"items": _call(lambda: _service(request).list_revisions(actor["id"], project_id))}


@router.get("/projects/{project_id}/revisions/{revision_id}/viewer-manifest")
def get_revision_viewer_manifest(project_id: str, revision_id: str, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).viewer_manifest(actor["id"], project_id, revision_id))


@router.get("/projects/{project_id}/jobs")
def list_project_jobs(
    project_id: str,
    request: Request,
    kind: str | None = Query(default=None),
    status: list[str] | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    actor: dict[str, Any] = Depends(_actor),
):
    return {"items": _call(lambda: _service(request).list_jobs(
        actor["id"],
        project_id,
        kind=kind,
        statuses=status,
        limit=limit,
    ))}


@router.get("/projects/{project_id}/evaluation-profiles")
def list_profiles(project_id: str, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return {"items": _call(lambda: _service(request).list_evaluation_profiles(actor["id"], project_id))}


@router.post("/courses/{course_id}/evaluation-profiles", status_code=201)
def create_profile(course_id: str, body: EvaluationProfileCreateRequest, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).create_evaluation_profile(actor["id"], course_id, name=body.name, weights=body.weights))


@router.post("/projects/{project_id}/evaluations", status_code=202)
def create_evaluation(project_id: str, body: EvaluationCreateRequest, request: Request, actor: dict[str, Any] = Depends(_actor)):
    def run():
        service = _service(request)
        evaluation = service.create_evaluation_run(actor["id"], project_id, revision_id=body.revision_id, profile_id=body.profile_id, weights=body.weights, seed=body.seed, evaluation_mode=body.evaluation_mode)
        if not body.auto_run:
            return {"evaluation": evaluation, "job": None}
        job = service.create_job(actor["id"], project_id, kind="evaluation", payload={"run_id": evaluation["id"]})
        if os.getenv("ROADGEN_JOB_MODE", "inline").strip().lower() in {"rq", "local"}:
            _dispatch_job(request, job)
            return {"evaluation": evaluation, "job": job}
        completed = _dispatch_job(request, job)
        return {"evaluation": service.list_evaluations(actor["id"], project_id)[0], "job": completed}
    return _call(run)


@router.get("/projects/{project_id}/evaluations")
def list_evaluations(project_id: str, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return {"items": _call(lambda: _service(request).list_evaluations(actor["id"], project_id))}


@router.post("/projects/{project_id}/comparisons")
def compare(project_id: str, body: RevisionCompareRequest, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).compare_revisions(actor["id"], project_id, body.revision_ids))


@router.post("/projects/{project_id}/exports", status_code=202)
def export_project(project_id: str, request: Request, actor: dict[str, Any] = Depends(_actor)):
    def run():
        service = _service(request)
        job = service.create_job(actor["id"], project_id, kind="project_export", payload={})
        return _dispatch_job(request, job)
    return _call(run)


@router.get("/workspace/exports/{scope}")
def export_user_data(scope: str, request: Request, actor: dict[str, Any] = Depends(_actor)):
    if scope not in {"configuration", "full"}:
        raise HTTPException(status_code=422, detail={"code": "validation_error", "message": "Export scope must be configuration or full."})
    filename, content = _call(lambda: _service(request).export_user_data(actor["id"], include_3d=scope == "full"))
    return StreamingResponse(
        io.BytesIO(content),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/workspace/imports/configuration", status_code=201)
def import_user_data(
    request: Request,
    file: UploadFile = File(...),
    actor: dict[str, Any] = Depends(_actor),
):
    filename = str(file.filename or "").lower()
    if not filename.endswith(".zip"):
        raise HTTPException(status_code=422, detail={"code": "validation_error", "message": "Select a RoadGen3D ZIP export."})
    content = file.file.read(256 * 1024 * 1024 + 1)
    return _call(lambda: _service(request).import_user_data(actor["id"], content))


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).get_job(actor["id"], job_id))


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request, actor: dict[str, Any] = Depends(_actor)):
    return _call(lambda: _service(request).cancel_job(actor["id"], job_id))


@router.post("/jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: str, request: Request, actor: dict[str, Any] = Depends(_actor)):
    def run():
        job = _service(request).retry_job(actor["id"], job_id)
        return _dispatch_job(request, job)
    return _call(run)


@router.get("/artifacts/{artifact_id}")
def download_artifact(artifact_id: str, request: Request, actor: dict[str, Any] = Depends(_actor)):
    artifact, handle = _call(lambda: _service(request).artifact(actor["id"], artifact_id))
    filename = artifact.object_key.rsplit("/", 1)[-1]
    return StreamingResponse(handle, media_type=artifact.media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"', "X-Content-SHA256": artifact.sha256})
