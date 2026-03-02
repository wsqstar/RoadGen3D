from __future__ import annotations

import argparse
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

from roadgen3d.eval_metrics import compute_dropped_slot_rate, compute_overlap_rate  # noqa: E402
from roadgen3d.types import StreetComposeResult  # noqa: E402
import scripts.m4_10_eval_engineering as m4_eval  # noqa: E402


def test_overlap_rate_zero_when_no_intersection():
    bboxes = [
        [-1.0, 0.0, -1.0, 0.0],
        [0.1, 1.0, 0.1, 1.0],
        [1.2, 2.0, -0.5, 0.2],
    ]
    assert compute_overlap_rate(bboxes) == 0.0


def test_dropped_slot_rate_formula():
    rate = compute_dropped_slot_rate(instance_count=15, dropped_slots=5)
    assert rate == pytest.approx(0.25)


def _fake_compose_factory(tmp_path: Path):
    def _fake_compose(**kwargs):
        mode = kwargs.get("placement_policy", "rule")
        out_dir = Path(kwargs["out_dir"]).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        if mode == "learned":
            instance_count = 10
            dropped_slots = 2
            diversity_ratio = 0.52
            retrieval_hit = 0.83
            latency_total = 140.0
        else:
            instance_count = 12
            dropped_slots = 1
            diversity_ratio = 0.56
            retrieval_hit = 0.86
            latency_total = 100.0

        scene_layout = out_dir / "scene_layout.json"
        scene_glb = out_dir / "scene.glb"
        scene_ply = out_dir / "scene.ply"
        scene_glb.write_bytes(b"glb")
        scene_ply.write_bytes(b"ply")

        summary = {
            "instance_count": instance_count,
            "dropped_slots": dropped_slots,
            "dropped_slot_rate": dropped_slots / (instance_count + dropped_slots),
            "overlap_rate": 0.0,
            "diversity_ratio": diversity_ratio,
            "retrieval_top3_category_hit": retrieval_hit,
            "latency_ms_total": latency_total,
            "latency_ms_per_instance": latency_total / instance_count,
            "policy_used": mode,
        }
        scene_layout.write_text(
            json.dumps(
                {
                    "query": kwargs["config"].query,
                    "summary": summary,
                    "placements": [],
                    "outputs": {"scene_glb": str(scene_glb), "scene_ply": str(scene_ply)},
                },
                ensure_ascii=True,
            ),
            encoding="utf-8",
        )

        return StreetComposeResult(
            query=kwargs["config"].query,
            instance_count=instance_count,
            dropped_slots=dropped_slots,
            placements=[],
            outputs={
                "scene_layout": str(scene_layout),
                "scene_glb": str(scene_glb),
                "scene_ply": str(scene_ply),
                "policy_used": mode,
            },
        )

    return _fake_compose


def _build_args(tmp_path: Path, placement_policy: str, compare_rule: bool) -> argparse.Namespace:
    queries = tmp_path / "queries.txt"
    queries.write_text("modern clean urban street\n", encoding="utf-8")
    return argparse.Namespace(
        queries=queries,
        manifest=tmp_path / "real_assets_manifest.jsonl",
        artifacts=tmp_path / "artifacts",
        out_dir=tmp_path / "m4_eval",
        model_name="openai/clip-vit-base-patch32",
        model_dir=None,
        local_files_only=True,
        device="cpu",
        placement_policy=placement_policy,
        policy_ckpt=None,
        policy_temperature=0.12,
        compare_rule=compare_rule,
        seed_start=0,
        seed_end=0,
        length_m=80.0,
        road_width_m=8.0,
        sidewalk_width_m=2.5,
        lane_count=2,
        density=1.0,
        topk_per_category=20,
        max_trials_per_slot=30,
        export_format="glb",
    )


def test_eval_report_schema_fields(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(m4_eval, "compose_street_scene", _fake_compose_factory(tmp_path))
    args = _build_args(tmp_path, placement_policy="rule", compare_rule=False)

    report = m4_eval.run_eval(args)
    summary = report["summary"]
    for key in (
        "instance_count",
        "dropped_slots",
        "dropped_slot_rate",
        "overlap_rate",
        "diversity_ratio",
        "retrieval_top3_category_hit",
        "latency_ms_total",
        "latency_ms_per_instance",
    ):
        assert key in summary

    outputs = report["outputs"]
    assert Path(outputs["eval_per_scene"]).exists()
    assert Path(outputs["eval_report"]).exists()


def test_rule_vs_learned_comparison_output(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(m4_eval, "compose_street_scene", _fake_compose_factory(tmp_path))
    args = _build_args(tmp_path, placement_policy="learned", compare_rule=True)

    report = m4_eval.run_eval(args)
    assert report["rule_summary"] is not None
    comparison = report["comparison_vs_rule"]
    assert "delta_instance_count" in comparison
    assert "delta_diversity_ratio" in comparison
    assert comparison["delta_instance_count"] == pytest.approx(-2.0)
