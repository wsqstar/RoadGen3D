from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.services import asset_manifest_registry as registry
from roadgen3d.services.asset_manifest_registry import AssetManifestConflictError
from roadgen3d.services.design_types import SceneJobCreateResponse
from web.api.main import create_app


def _write_manifest(path: Path) -> None:
    mesh = path.parent / "bench.glb"
    mesh.write_bytes(b"glTF fixture")
    rows = [
        {"asset_id": "bench-ready", "category": "bench", "text_desc": "bench", "mesh_path": mesh.name, "latent_path": "bench.pt", "scene_eligible": True},
        {"asset_id": "unsupported", "category": "building", "text_desc": "building", "mesh_path": mesh.name, "latent_path": "building.pt", "scene_eligible": True},
        {"asset_id": "disabled", "category": "tree", "text_desc": "tree", "mesh_path": mesh.name, "latent_path": "tree.pt", "scene_eligible": False},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_manifest_summary_and_eligibility_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = tmp_path / "fixture.jsonl"
    _write_manifest(manifest)
    monkeypatch.setattr(registry, "_registered_manifests", lambda: {"fixture.jsonl": manifest})

    summary = registry.summarize_manifest("fixture.jsonl")
    assert summary["count"] == 3
    assert summary["eligibleCount"] == 2
    assert summary["readyCount"] == 1
    assert summary["categoryCounts"] == {"bench": 1, "building": 1}
    assert len(summary["fingerprint"]) == 64

    enabled = registry.read_manifest_page("fixture.jsonl", eligibility="eligible")
    assert enabled["total"] == 2
    assert {row["asset_id"] for row in enabled["assets"]} == {"bench-ready", "unsupported"}
    disabled = registry.read_manifest_page("fixture.jsonl", eligibility="disabled")
    assert [row["asset_id"] for row in disabled["assets"]] == ["disabled"]


def test_candidate_snapshot_validates_names_and_fingerprints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = tmp_path / "fixture.jsonl"
    _write_manifest(manifest)
    monkeypatch.setattr(registry, "_registered_manifests", lambda: {"fixture.jsonl": manifest})
    summary = registry.summarize_manifest("fixture.jsonl")

    frozen = registry.freeze_candidate_manifests(
        [{"name": "fixture.jsonl", "expected_fingerprint": summary["fingerprint"]}],
        snapshot_root=tmp_path / "snapshots",
    )
    snapshot_path = Path(frozen["manifest_paths"][0])
    snapshot_rows = [json.loads(line) for line in snapshot_path.read_text(encoding="utf-8").splitlines()]
    assert Path(snapshot_rows[0]["mesh_path"]).is_absolute()
    assert Path(snapshot_rows[0]["mesh_path"]).is_file()
    assert frozen["manifest_names"] == ["fixture.jsonl"]
    assert frozen["candidate_asset_count"] == 1
    assert frozen["candidate_asset_manifests"][0]["priority"] == 0

    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(AssetManifestConflictError):
        registry.freeze_candidate_manifests(
            [{"name": "fixture.jsonl", "expected_fingerprint": summary["fingerprint"]}],
            snapshot_root=tmp_path / "snapshots",
        )
    for invalid in ("../fixture.jsonl", str(manifest.resolve()), "unknown.jsonl"):
        with pytest.raises(ValueError):
            registry.resolve_registered_manifest(invalid)


def test_scene_asset_reference_search_and_combined_rebuild_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = tmp_path / "fixture.jsonl"
    _write_manifest(manifest)
    monkeypatch.setattr(registry, "_registered_manifests", lambda: {"fixture.jsonl": manifest})
    summary = registry.summarize_manifest("fixture.jsonl")

    search = registry.search_registered_assets(query="bench", category="bench")
    assert search["total"] == 1
    asset = search["assets"][0]
    assert asset["assetId"] == "bench-ready"
    assert asset["fingerprint"] == summary["fingerprint"]
    assert not any("path" in key.lower() for key in asset)

    destination = tmp_path / "combined" / "assets.jsonl"
    result = registry.build_scene_edit_manifest(
        [{
            "asset_id": "bench-ready",
            "asset_ref": {
                "manifestName": "fixture.jsonl",
                "assetId": "bench-ready",
                "fingerprint": summary["fingerprint"],
                "category": "bench",
                "label": "Bench",
            },
        }],
        destination=destination,
    )
    row = json.loads(destination.read_text(encoding="utf-8").strip())
    assert Path(row["mesh_path"]).is_absolute()
    assert result["assets"] == [{
        "manifest_name": "fixture.jsonl",
        "fingerprint": summary["fingerprint"],
        "asset_id": "bench-ready",
    }]

    with pytest.raises(AssetManifestConflictError):
        registry.resolve_registered_asset(
            "fixture.jsonl",
            "bench-ready",
            expected_fingerprint="0" * 64,
        )


class _JobService:
    def __init__(self) -> None:
        self.generation_options = None

    def create_scene_job(self, draft, **kwargs):
        self.generation_options = kwargs.get("generation_options")
        return SceneJobCreateResponse(job_id="candidate-job", status="queued", created_at="2026-07-16T00:00:00Z")

    def list_knowledge_sources(self):
        return []


def test_scene_job_freezes_registered_candidate_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = tmp_path / "fixture.jsonl"
    _write_manifest(manifest)
    monkeypatch.setattr(registry, "_registered_manifests", lambda: {"fixture.jsonl": manifest})
    monkeypatch.setattr(registry, "DEFAULT_SNAPSHOT_ROOT", tmp_path / "snapshots")
    summary = registry.summarize_manifest("fixture.jsonl")
    service = _JobService()
    client = TestClient(create_app(design_service=service))

    catalog = client.get("/api/asset-manifests")
    assert catalog.status_code == 200
    assert catalog.json()["manifests"][0]["readyCount"] == 1
    eligible_page = client.get(
        "/api/asset-manifest",
        params={"name": "fixture.jsonl", "eligibility": "eligible"},
    )
    assert eligible_page.status_code == 200
    assert eligible_page.json()["total"] == 2

    search = client.get(
        "/api/asset-catalog/search",
        params={"q": "bench", "manifest": "fixture.jsonl", "category": "bench"},
    )
    assert search.status_code == 200
    catalog_asset = search.json()["assets"][0]
    assert catalog_asset["assetId"] == "bench-ready"
    assert not any("path" in key.lower() for key in catalog_asset)
    model = client.get(
        "/api/asset-catalog/model",
        params={
            "manifest_name": "fixture.jsonl",
            "asset_id": "bench-ready",
            "fingerprint": summary["fingerprint"],
        },
    )
    assert model.status_code == 200
    assert model.headers["content-type"] == "model/gltf-binary"
    assert model.content == b"glTF fixture"
    stale_model = client.get(
        "/api/asset-catalog/model",
        params={
            "manifest_name": "fixture.jsonl",
            "asset_id": "bench-ready",
            "fingerprint": "0" * 64,
        },
    )
    assert stale_model.status_code == 409

    response = client.post("/api/scene/jobs", json={
        "draft": {
            "normalized_scene_query": "fixture street",
            "compose_config_patch": {},
            "citations_by_field": {},
            "design_summary": "fixture",
            "risk_notes": [],
        },
        "generation_options": {
            "candidate_asset_manifests": [{
                "name": "fixture.jsonl",
                "expected_fingerprint": summary["fingerprint"],
            }],
        },
    })

    assert response.status_code == 200
    assert service.generation_options["manifest_names"] == ["fixture.jsonl"]
    assert service.generation_options["candidate_asset_count"] == 1
    assert Path(service.generation_options["manifest_paths"][0]).is_file()

    stale = client.post("/api/scene/jobs", json={
        "draft": {
            "normalized_scene_query": "fixture street",
            "compose_config_patch": {},
            "citations_by_field": {},
            "design_summary": "fixture",
            "risk_notes": [],
        },
        "generation_options": {
            "candidate_asset_manifests": [{"name": "fixture.jsonl", "expected_fingerprint": "stale"}],
        },
    })
    assert stale.status_code == 409

    for invalid_name in ("../fixture.jsonl", str(manifest.resolve()), "unknown.jsonl"):
        invalid = client.post("/api/scene/jobs", json={
            "draft": {
                "normalized_scene_query": "fixture street",
                "compose_config_patch": {},
                "citations_by_field": {},
                "design_summary": "fixture",
                "risk_notes": [],
            },
            "generation_options": {
                "candidate_asset_manifests": [{"name": invalid_name, "expected_fingerprint": "abc"}],
            },
        })
        assert invalid.status_code == 400

    traversal = client.get("/api/asset-manifest", params={"name": "../fixture.jsonl"})
    assert traversal.status_code == 400
