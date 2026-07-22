from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from web.api.main import create_app  # noqa: E402


class _NoopDesignService:
    pass


class _FeatureServiceStub:
    def __init__(self, image_path: Path) -> None:
        self.image_path = image_path
        self.accepted = ""

    def submit_run(self, **kwargs):
        return {"run_id": "run-1", "status": "queued", "submitted": kwargs}

    def get_run(self, run_id: str):
        return {"run_id": run_id, "status": "succeeded", "variants": []} if run_id == "run-1" else None

    def accept_variant(self, run_id: str, variant_id: str):
        if run_id != "run-1":
            raise KeyError(run_id)
        self.accepted = variant_id
        return {"run_id": run_id, "accepted_variant_id": variant_id, "patch": {"curb_ramp_enabled": True}}

    def artifact_path(self, run_id: str, variant_id: str, view_id: str):
        if (run_id, variant_id, view_id) == ("run-1", "v1", "feature_top"):
            return self.image_path
        return None


def test_feature_quality_routes_create_poll_accept_and_serve_artifact(tmp_path: Path) -> None:
    image = tmp_path / "feature_top.png"
    image.write_bytes(b"png")
    app = create_app(design_service=_NoopDesignService())
    stub = _FeatureServiceStub(image)
    app.state.feature_quality_run_service = stub
    client = TestClient(app)

    created = client.post(
        "/api/design/feature-quality-runs",
        json={
            "target_id": "curb_ramp",
            "brief": "Independent ramp",
            "variant_count": 4,
            "base_patch": {"bus_stop_enabled": False},
        },
    )
    assert created.status_code == 200
    assert created.json()["submitted"]["variant_count"] == 4
    assert client.get("/api/design/feature-quality-runs/run-1").json()["status"] == "succeeded"

    accepted = client.post("/api/design/feature-quality-runs/run-1/accept/v1")
    assert accepted.status_code == 200
    assert accepted.json()["patch"] == {"curb_ramp_enabled": True}
    assert stub.accepted == "v1"

    artifact = client.get("/api/design/feature-quality-runs/run-1/artifacts/v1/feature_top")
    assert artifact.status_code == 200
    assert artifact.content == b"png"


def test_feature_quality_request_rejects_too_few_variants() -> None:
    app = create_app(design_service=_NoopDesignService())
    client = TestClient(app)
    response = client.post(
        "/api/design/feature-quality-runs",
        json={"target_id": "curb_ramp", "brief": "ramp", "variant_count": 2},
    )
    assert response.status_code == 422
