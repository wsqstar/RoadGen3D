from __future__ import annotations

import json
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
from scripts import asset_clean_manifest as manifest_cleaner  # noqa: E402


BAD_TREE_ASSET_ID = "objaverse_tree_7c97aea203b34df6bb615d0d3567d984"
BAD_LAMP_ASSET_ID = "003e74743d454448abf11fd78164a75d"


def test_known_bad_tree_asset_is_runtime_ineligible() -> None:
    row = {
        "asset_id": BAD_TREE_ASSET_ID,
        "category": "tree",
        "scene_eligible": True,
        "quality_tier": 3,
    }
    assert street_layout._row_scene_eligible(row) is False
    assert beauty._scene_eligible(row) is False


def test_known_bad_flask_lamp_asset_is_runtime_ineligible() -> None:
    row = {
        "asset_id": BAD_LAMP_ASSET_ID,
        "category": "lamp",
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


def test_street_scene_block_categories_are_manually_disabled_in_street_furniture_manifest() -> None:
    manifest_path = Path(__file__).resolve().parents[1] / "data" / "street_furniture" / "street_furniture_manifest.jsonl"
    block_categories = {
        "house",
        "chair",
        "table",
        "couch",
        "bungalow",
        "arcade game cabinet",
        "candle",
        "grave yard",
        "bed",
    }

    by_category: dict[str, int] = {category: 0 for category in block_categories}
    any_enabled = {category: False for category in block_categories}

    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        category = str(row.get("category", "")).strip()
        if category not in block_categories:
            continue
        by_category[category] += 1
        if row.get("scene_eligible") is True:
            any_enabled[category] = True

    for category in block_categories:
        if by_category[category] == 0:
            continue
        assert not any_enabled[category], f"{category} has scene_eligible=true in manifest"
