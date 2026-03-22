from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.placement_field import (
    load_placement_field_config,
    pair_interaction_scores,
    placement_priority_rank,
)


def test_load_placement_field_config_requires_all_keys(tmp_path: Path):
    bad_config_path = tmp_path / "placement_field_bad.json"
    bad_config_path.write_text(
        json.dumps(
            {
                "version": "placement_field_v1",
                "cell_size_m": 4.0,
                "poi_attraction_weights": {},
                "poi_attraction_sigma_m": {},
                "pair_relations": {},
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing keys"):
        load_placement_field_config(str(bad_config_path))


def test_same_category_repulsion_dominates_near_distance():
    near_attraction, near_repulsion = pair_interaction_scores(
        "bench",
        (0.0, 0.0),
        "bench",
        (0.5, 0.0),
    )
    target_attraction, target_repulsion = pair_interaction_scores(
        "bench",
        (0.0, 0.0),
        "bench",
        (7.8, 0.0),
    )

    assert near_repulsion > near_attraction
    assert target_attraction > near_attraction
    assert near_repulsion > target_repulsion


def test_special_pair_curves_follow_config_targets():
    bench_trash_target_attraction, bench_trash_target_repulsion = pair_interaction_scores(
        "bench",
        (0.0, 0.0),
        "trash",
        (4.5, 0.0),
    )
    bench_trash_far_attraction, _ = pair_interaction_scores(
        "bench",
        (0.0, 0.0),
        "trash",
        (9.0, 0.0),
    )
    bus_stop_lamp_target_attraction, bus_stop_lamp_target_repulsion = pair_interaction_scores(
        "bus_stop",
        (0.0, 0.0),
        "lamp",
        (6.0, 0.0),
    )
    _, bus_stop_lamp_near_repulsion = pair_interaction_scores(
        "bus_stop",
        (0.0, 0.0),
        "lamp",
        (0.4, 0.0),
    )

    assert bench_trash_target_attraction > bench_trash_far_attraction
    assert bench_trash_target_attraction > 0.0
    assert bench_trash_target_repulsion < 0.1
    assert bus_stop_lamp_target_attraction > 0.0
    assert bus_stop_lamp_near_repulsion > bus_stop_lamp_target_repulsion


def test_anchor_placement_priority_order_is_stable():
    assert placement_priority_rank("fire_hydrant") < placement_priority_rank("bus_stop")
    assert placement_priority_rank("bus_stop") < placement_priority_rank("bollard")
    assert placement_priority_rank("unknown_anchor") > placement_priority_rank("bollard")
