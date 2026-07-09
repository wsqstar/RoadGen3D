"""Canonical FastAPI entrypoint for the LLM + RAG design API."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.capture_3d import capture_views_for_layout  # noqa: E402
from roadgen3d.services.branch_benchmarks import BranchBenchmarkBatchService, BranchBenchmarkStore  # noqa: E402
from roadgen3d.services.branch_runs import BranchRunService  # noqa: E402
from roadgen3d.services.design_matrix import DesignMatrixService  # noqa: E402
from roadgen3d.services.scene_context_service import build_osm_semantic_preview  # noqa: E402
from roadgen3d.services.scenario_designs import ScenarioDesignService  # noqa: E402
from roadgen3d.llm.design_workflow import DesignAssistantService  # noqa: E402
from roadgen3d.street_layout import rebuild_glb_from_layout  # noqa: E402
from web.api.routers import catalog as catalog_routes  # noqa: E402
from web.api.routers import diff_capture as diff_capture_routes  # noqa: E402
from web.api.routers.assets import router as assets_router  # noqa: E402
from web.api.routers.branch_benchmarks import router as branch_benchmarks_router  # noqa: E402
from web.api.routers.design import router as design_router  # noqa: E402
from web.api.routers.evaluation import router as evaluation_router  # noqa: E402
from web.api.routers.knowledge import router as knowledge_router  # noqa: E402
from web.api.routers.scene_jobs import router as scene_jobs_router  # noqa: E402
from web.api.routers.scenario_designs import router as scenario_designs_router  # noqa: E402


def create_app(
    *,
    design_service: DesignAssistantService | Any | None = None,
    benchmark_store: BranchBenchmarkStore | None = None,
) -> FastAPI:
    app = FastAPI(title="RoadGen3D Design Assistant API", version="0.2.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.design_service = design_service or DesignAssistantService()
    app.state.benchmark_store = benchmark_store or BranchBenchmarkStore()
    app.state.branch_run_service = BranchRunService(
        design_service=app.state.design_service,
        benchmark_store=app.state.benchmark_store,
    )
    app.state.scenario_design_service = ScenarioDesignService(
        design_service=app.state.design_service,
    )
    app.state.design_matrix_service = DesignMatrixService(
        design_service=app.state.design_service,
        scenario_design_service=app.state.scenario_design_service,
    )
    app.state.benchmark_batch_service = BranchBenchmarkBatchService(
        branch_run_service=app.state.branch_run_service,
        benchmark_store=app.state.benchmark_store,
    )

    # Preserve legacy monkeypatch seams on web.api.main while route handlers live in routers.
    catalog_routes.build_osm_semantic_preview = build_osm_semantic_preview
    diff_capture_routes.capture_views_for_layout = capture_views_for_layout
    diff_capture_routes.rebuild_glb_from_layout = rebuild_glb_from_layout

    for router in (
        catalog_routes.router,
        design_router,
        scene_jobs_router,
        scenario_designs_router,
        branch_benchmarks_router,
        diff_capture_routes.router,
        evaluation_router,
        assets_router,
        knowledge_router,
    ):
        app.include_router(router)

    return app


app = create_app()
