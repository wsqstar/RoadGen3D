from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import roadgen3d.beauty as beauty  # noqa: E402
import roadgen3d.street_layout as street_layout  # noqa: E402
from scripts import m3_04_clean_asset_manifest as manifest_cleaner  # noqa: E402


BAD_TREE_ASSET_ID = "objaverse_tree_7c97aea203b34df6bb615d0d3567d984"


def test_known_bad_tree_asset_is_runtime_ineligible() -> None:
    row = {
        "asset_id": BAD_TREE_ASSET_ID,
        "category": "tree",
        "scene_eligible": True,
        "quality_tier": 3,
    }
    assert street_layout._row_scene_eligible(row) is False
    assert beauty._scene_eligible(row) is False


def test_known_bad_tree_asset_is_manifest_ineligible() -> None:
    row = {
        "asset_id": BAD_TREE_ASSET_ID,
        "category": "tree",
        "scene_eligible": True,
        "quality_tier": 3,
        "mesh_path": "tree.glb",
    }
    assert manifest_cleaner._scene_eligible(row, face_count=298, quality_tier=1) is False
    notes = manifest_cleaner._quality_notes(row, face_count=298, quality_tier=1, scene_eligible=False)
    assert "known_bad_asset_blocked" in notes
    assert "scene_blocked" in notes
