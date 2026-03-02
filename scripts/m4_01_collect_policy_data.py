#!/usr/bin/env python3
"""Collect slot-level policy distillation data from rule-based M3 layout."""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.embedder import ClipTextEmbedder, ModelLoadError  # noqa: E402
from roadgen3d.index_store import FaissIndexStore  # noqa: E402
from roadgen3d.layout_features import PolicyFeatureContext  # noqa: E402
from roadgen3d.street_layout import (  # noqa: E402
    DEFAULT_CATEGORIES,
    DEFAULT_SPACING_M,
    SIDE_PREF,
    _bbox_intersects,
    _compute_bbox,
    _load_mesh_cache,
    _load_real_manifest,
    _pick_category_candidate,
    _sample_pose,
)

DEFAULT_QUERIES = [
    "modern clean urban street",
    "tree-lined residential street",
    "dense downtown avenue with street furniture",
    "quiet neighborhood road with benches",
    "functional industrial roadside",
    "pedestrian-friendly boulevard",
    "compact city block street",
    "orderly transit corridor",
    "minimalist urban street",
    "high-utility municipal road",
    "mixed-use street with bus facilities",
    "wide arterial road with sparse furniture",
    "cozy community street",
    "street with frequent bollards and lamps",
    "green urban corridor",
    "commercial street with mailbox and trash",
    "modern tactical city street",
    "nordic-style clean roadway",
    "high-density downtown street",
    "balanced street with diverse assets",
]


def _load_queries(path: Path | None) -> List[str]:
    if path is None or not path.exists():
        return list(DEFAULT_QUERIES)
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return lines or list(DEFAULT_QUERIES)


def _resolve_side(category: str, slot_idx: int) -> float:
    side_pref = SIDE_PREF.get(category, "both")
    if side_pref == "right":
        return -1.0
    if side_pref == "left":
        return 1.0
    return 1.0 if (slot_idx % 2 == 0) else -1.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect M4 policy training data from rule layout.")
    parser.add_argument("--manifest", type=Path, default=Path("data/real/real_assets_manifest.jsonl"))
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts/real"))
    parser.add_argument("--out", type=Path, default=Path("artifacts/m4/policy_train.jsonl"))
    parser.add_argument("--queries", type=Path, default=Path("data/eval/queries_m4.txt"))
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--seed-end", type=int, default=49)
    parser.add_argument("--model-name", default="openai/clip-vit-base-patch32")
    parser.add_argument("--model-dir", type=Path, default=None)
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--length-m", type=float, default=80.0)
    parser.add_argument("--road-width-m", type=float, default=8.0)
    parser.add_argument("--sidewalk-width-m", type=float, default=2.5)
    parser.add_argument("--lane-count", type=int, default=2)
    parser.add_argument("--density", type=float, default=1.0)
    parser.add_argument("--topk-per-category", type=int, default=20)
    parser.add_argument("--max-trials-per-slot", type=int, default=30)
    return parser.parse_args()


def collect_policy_data(
    *,
    manifest: Path,
    artifacts: Path,
    out: Path,
    queries_path: Path | None,
    seed_start: int,
    seed_end: int,
    model_name: str,
    model_dir: Path | None,
    local_files_only: bool,
    device: str,
    length_m: float,
    road_width_m: float,
    sidewalk_width_m: float,
    lane_count: int,
    density: float,
    topk_per_category: int,
    max_trials_per_slot: int,
) -> List[Dict[str, object]]:
    queries = _load_queries(queries_path)
    rows = _load_real_manifest(manifest.resolve())
    if not rows:
        raise ValueError("real manifest is empty")
    asset_by_id: Dict[str, Dict[str, str]] = {row["asset_id"]: row for row in rows}
    category_to_rows: Dict[str, List[Dict[str, str]]] = {category: [] for category in DEFAULT_CATEGORIES}
    for row in rows:
        if row["category"] in category_to_rows:
            category_to_rows[row["category"]].append(row)

    mesh_cache = _load_mesh_cache([row for row in rows if row["category"] in category_to_rows])

    embedder = ClipTextEmbedder(
        model_name=model_name,
        model_dir=model_dir,
        local_files_only=bool(local_files_only),
        device=device,
    )
    index_store = FaissIndexStore.load(
        index_path=artifacts / "index_ip.faiss",
        id_map_path=artifacts / "id_map.json",
    )

    out_path = out.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    effective_density = max(float(density), 0.1)
    samples: List[Dict[str, object]] = []

    for query_idx, query in enumerate(queries):
        for seed in range(int(seed_start), int(seed_end) + 1):
            scene_id = f"q{query_idx:03d}_s{seed:04d}"
            rng = random.Random(int(seed))
            existing_bboxes = []
            used_asset_ids_by_category: Dict[str, set[str]] = {category: set() for category in DEFAULT_CATEGORIES}

            for category in DEFAULT_CATEGORIES:
                pool = category_to_rows.get(category, [])
                if not pool:
                    continue
                base_spacing = float(DEFAULT_SPACING_M[category])
                spacing = base_spacing / effective_density
                slot_count = max(1, int(float(length_m) // spacing))
                segment = float(length_m) / float(slot_count)

                for slot_idx in range(slot_count):
                    x_center = -float(length_m) / 2.0 + (slot_idx + 0.5) * segment
                    side = _resolve_side(category, slot_idx)
                    slot_z_center = side * (float(road_width_m) / 2.0 + float(sidewalk_width_m) * 0.5)

                    used_before = sorted(used_asset_ids_by_category.setdefault(category, set()))
                    feature_ctx = PolicyFeatureContext(
                        query=query,
                        category=category,
                        slot_idx=int(slot_idx),
                        slot_x=float(x_center),
                        slot_z=float(slot_z_center),
                        length_m=float(length_m),
                        road_width_m=float(road_width_m),
                        sidewalk_width_m=float(sidewalk_width_m),
                        lane_count=int(lane_count),
                        density=float(density),
                        topk=int(topk_per_category),
                        used_asset_ids=set(used_before),
                    )

                    row, score, source, details = _pick_category_candidate(
                        query=query,
                        category=category,
                        topk=int(topk_per_category),
                        embedder=embedder,
                        index_store=index_store,
                        asset_by_id=asset_by_id,
                        category_pool=pool,
                        used_asset_ids=used_asset_ids_by_category.setdefault(category, set()),
                        rng=rng,
                        placement_policy="rule",
                        feature_context=feature_ctx,
                        return_details=True,
                    )

                    chosen_asset_id = str(row["asset_id"])
                    chosen_index = int(details.get("chosen_index", -1))
                    candidates = details.get("candidates", []) or []
                    candidate_asset_ids = [str(item.get("asset_id", "")) for item in candidates]
                    candidate_scores = [float(item.get("score", 0.0)) for item in candidates]
                    candidate_categories = [str(item.get("category", "")) for item in candidates]

                    entry = mesh_cache[chosen_asset_id]
                    dropped = True
                    for trial_idx in range(int(max_trials_per_slot)):
                        x, z, yaw_deg = _sample_pose(
                            category=category,
                            slot_idx=slot_idx,
                            trial_idx=trial_idx,
                            x_center=x_center,
                            length_m=float(length_m),
                            road_width_m=float(road_width_m),
                            sidewalk_width_m=float(sidewalk_width_m),
                            spacing_m=spacing,
                            rng=rng,
                        )
                        bbox = _compute_bbox(
                            x=float(x),
                            z=float(z),
                            yaw_deg=float(yaw_deg),
                            half_x=entry.half_x,
                            half_z=entry.half_z,
                            scale=1.0,
                            clearance=0.2,
                        )
                        if any(_bbox_intersects(bbox, existing) for existing in existing_bboxes):
                            continue
                        existing_bboxes.append(bbox)
                        used_asset_ids_by_category.setdefault(category, set()).add(chosen_asset_id)
                        dropped = False
                        break

                    samples.append(
                        {
                            "scene_id": scene_id,
                            "query": query,
                            "seed": int(seed),
                            "category": category,
                            "slot_idx": int(slot_idx),
                            "slot_x": float(x_center),
                            "slot_z": float(slot_z_center),
                            "road_params": {
                                "length_m": float(length_m),
                                "road_width_m": float(road_width_m),
                                "sidewalk_width_m": float(sidewalk_width_m),
                                "lane_count": int(lane_count),
                                "density": float(density),
                            },
                            "candidate_asset_ids": candidate_asset_ids,
                            "candidate_scores": candidate_scores,
                            "candidate_categories": candidate_categories,
                            "chosen_asset_id": chosen_asset_id,
                            "chosen_index": int(chosen_index),
                            "chosen_source": str(source),
                            "used_asset_ids_before_slot": used_before,
                            "dropped": bool(dropped),
                        }
                    )

    with out_path.open("w", encoding="utf-8") as handle:
        for sample in samples:
            handle.write(json.dumps(sample, ensure_ascii=True) + "\n")
    return samples


def main() -> int:
    args = parse_args()
    try:
        samples = collect_policy_data(
            manifest=args.manifest,
            artifacts=args.artifacts,
            out=args.out,
            queries_path=args.queries,
            seed_start=int(args.seed_start),
            seed_end=int(args.seed_end),
            model_name=args.model_name,
            model_dir=args.model_dir,
            local_files_only=bool(args.local_files_only),
            device=args.device,
            length_m=float(args.length_m),
            road_width_m=float(args.road_width_m),
            sidewalk_width_m=float(args.sidewalk_width_m),
            lane_count=int(args.lane_count),
            density=float(args.density),
            topk_per_category=int(args.topk_per_category),
            max_trials_per_slot=int(args.max_trials_per_slot),
        )

        print(f"Collected {len(samples)} slot samples -> {args.out.resolve()}")
        return 0
    except ModelLoadError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Policy data collection failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
