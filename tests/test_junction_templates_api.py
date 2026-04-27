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

import roadgen3d.api.junction_templates as junction_templates_api  # noqa: E402
from web.api.main import create_app  # noqa: E402


class _StubService:
    default_pdf_path = Path("/tmp/guide.pdf")
    default_artifact_dir = Path("/tmp/knowledge")


def test_canonical_backend_exposes_junction_template_routes(tmp_path: Path, monkeypatch) -> None:
    template_dir = tmp_path / "junction_templates"
    template_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(junction_templates_api, "TEMPLATES_DIR", template_dir)

    client = TestClient(create_app(design_service=_StubService()))
    payload = {
        "junction": {
            "id": "junction_test",
            "label": "Test Junction",
            "x": 12.5,
            "y": 8.0,
            "kind": "cross_junction",
            "connected_centerline_ids": [],
            "crosswalk_depth_m": 3.0,
            "source_mode": "explicit",
        },
        "compositions": [
            {
                "junctionId": "junction_test",
                "kind": "cross_junction",
                "quadrants": [
                    {
                        "quadrantId": "Q0",
                        "armAId": "",
                        "armBId": "",
                        "patches": [],
                        "skeletonLines": [
                            {
                                "lineId": "skel_junction_test_1",
                                "stripKind": "clear_sidewalk",
                                "curve": {
                                    "start": {"x": 12.5, "y": 8.0},
                                    "control1": {"x": 13.0, "y": 8.5},
                                    "control2": {"x": 14.0, "y": 9.5},
                                    "end": {"x": 15.0, "y": 10.0},
                                },
                                "widthM": 3.0,
                            }
                        ],
                    }
                ],
            }
        ],
        "metadata": {"version": "1.0"},
    }

    create_response = client.post("/api/junction-templates", json=payload)
    assert create_response.status_code == 200
    created = create_response.json()
    assert created["template_id"] == "junction_test"
    assert created["filename"] == "junction_test.json"
    assert (template_dir / "junction_test.json").exists()

    list_response = client.get("/api/junction-templates")
    assert list_response.status_code == 200
    listed = list_response.json()
    assert len(listed) == 1
    assert listed[0]["template_id"] == "junction_test"

    get_response = client.get("/api/junction-templates/junction_test")
    assert get_response.status_code == 200
    fetched = get_response.json()
    assert fetched["junction"]["label"] == "Test Junction"
    assert fetched["compositions"][0]["quadrants"][0]["quadrantId"] == "Q0"
