#!/usr/bin/env python3
"""Train learned slot-level layout policy from distilled M4 data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.layout_features import (  # noqa: E402
    CandidateDescriptor,
    PolicyFeatureContext,
    vectorize_slot_candidates,
)
from roadgen3d.layout_policy import (  # noqa: E402
    PolicyTrainConfig,
    split_samples_by_scene,
    train_layout_policy,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train M4 learned layout policy.")
    parser.add_argument("--data", type=Path, default=Path("artifacts/m4/policy_train.jsonl"))
    parser.add_argument("--out-dir", type=Path, default=Path("artifacts/m4"))
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--entropy-weight", type=float, default=0.01)
    parser.add_argument("--patience", type=int, default=3)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--resume-ckpt", type=Path, default=None)
    return parser.parse_args()


def _load_jsonl(path: Path) -> List[Dict[str, object]]:
    if not path.exists():
        raise FileNotFoundError(f"policy data not found: {path}")
    rows: List[Dict[str, object]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSONL at line {line_no} ({path}): {exc}") from exc
        rows.append(payload)
    if not rows:
        raise ValueError(f"policy data is empty: {path}")
    return rows


def _to_training_samples(rows: List[Dict[str, object]]) -> tuple[List[Dict[str, object]], Dict[str, int]]:
    samples: List[Dict[str, object]] = []
    skipped_no_candidates = 0
    skipped_no_choice = 0

    for row in rows:
        candidate_asset_ids = [str(x) for x in (row.get("candidate_asset_ids") or [])]
        candidate_scores = [float(x) for x in (row.get("candidate_scores") or [])]
        candidate_categories = [str(x) for x in (row.get("candidate_categories") or [])]

        if not candidate_asset_ids:
            skipped_no_candidates += 1
            continue

        n = min(len(candidate_asset_ids), len(candidate_scores), len(candidate_categories))
        if n <= 0:
            skipped_no_candidates += 1
            continue

        candidate_asset_ids = candidate_asset_ids[:n]
        candidate_scores = candidate_scores[:n]
        candidate_categories = candidate_categories[:n]

        chosen_index = int(row.get("chosen_index", -1))
        if chosen_index < 0 or chosen_index >= n:
            chosen_asset = str(row.get("chosen_asset_id", ""))
            if chosen_asset and chosen_asset in candidate_asset_ids:
                chosen_index = candidate_asset_ids.index(chosen_asset)

        if chosen_index < 0 or chosen_index >= n:
            skipped_no_choice += 1
            continue

        road_params = row.get("road_params") or {}
        context = PolicyFeatureContext(
            query=str(row.get("query", "")),
            category=str(row.get("category", "")).strip().lower(),
            slot_idx=int(row.get("slot_idx", 0)),
            slot_x=float(row.get("slot_x", 0.0)),
            slot_z=float(row.get("slot_z", 0.0)),
            length_m=float(road_params.get("length_m", 80.0)),
            road_width_m=float(road_params.get("road_width_m", 8.0)),
            sidewalk_width_m=float(road_params.get("sidewalk_width_m", 2.5)),
            lane_count=int(road_params.get("lane_count", 2)),
            density=float(road_params.get("density", 1.0)),
            topk=max(1, n),
            used_asset_ids=set(str(x) for x in (row.get("used_asset_ids_before_slot") or [])),
        )
        candidates = [
            CandidateDescriptor(
                asset_id=candidate_asset_ids[i],
                category=candidate_categories[i].strip().lower(),
                score=float(candidate_scores[i]),
            )
            for i in range(n)
        ]
        features = vectorize_slot_candidates(context, candidates)
        samples.append(
            {
                "scene_id": str(row.get("scene_id", "")),
                "candidate_features": features,
                "chosen_index": int(chosen_index),
            }
        )

    stats = {
        "raw_rows": len(rows),
        "usable_rows": len(samples),
        "skipped_no_candidates": skipped_no_candidates,
        "skipped_no_choice": skipped_no_choice,
    }
    return samples, stats


def train_from_jsonl(
    *,
    data_path: Path,
    out_dir: Path,
    config: PolicyTrainConfig,
    resume_ckpt: Path | None = None,
    progress_callback: Optional[Callable[[Dict[str, float]], None]] = None,
) -> Dict[str, object]:
    raw_rows = _load_jsonl(Path(data_path).resolve())
    samples, ingest_stats = _to_training_samples(raw_rows)
    if not samples:
        raise RuntimeError(
            "No usable policy samples after preprocessing. "
            "Check candidate lists and chosen_index in policy_train.jsonl"
        )

    train_samples, val_samples = split_samples_by_scene(samples, train_ratio=0.9)
    result = train_layout_policy(
        train_samples=train_samples,
        val_samples=val_samples,
        out_dir=out_dir,
        config=config,
        resume_checkpoint=resume_ckpt,
        progress_callback=progress_callback,
    )

    summary = {
        "ingest": ingest_stats,
        "split": {"train": len(train_samples), "val": len(val_samples)},
        "outputs": {
            "checkpoint": result["checkpoint"],
            "meta_path": result["meta_path"],
            "curve_path": result["curve_path"],
        },
        "best_val_loss": float(result["meta"]["best_val_loss"]),
        "resumed_from": str(resume_ckpt.resolve()) if resume_ckpt else "",
    }
    summary_path = Path(out_dir).resolve() / "train_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=True), encoding="utf-8")
    summary["outputs"]["summary_path"] = str(summary_path)
    return summary


def main() -> int:
    args = parse_args()
    try:
        config = PolicyTrainConfig(
            epochs=int(args.epochs),
            batch_size=int(args.batch_size),
            lr=float(args.lr),
            weight_decay=float(args.weight_decay),
            entropy_weight=float(args.entropy_weight),
            patience=int(args.patience),
            device=args.device,
        )
        summary = train_from_jsonl(
            data_path=args.data.resolve(),
            out_dir=args.out_dir,
            config=config,
            resume_ckpt=args.resume_ckpt,
        )

        print(json.dumps(summary, indent=2, ensure_ascii=True))
        return 0
    except Exception as exc:
        print(f"Train layout policy failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
