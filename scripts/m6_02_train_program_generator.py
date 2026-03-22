#!/usr/bin/env python3
"""Train learned_v1 StreetProgram generator from distilled M6 data."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.program_generator import (  # noqa: E402
    ProgramTrainConfig,
    split_program_samples_by_scene,
    train_program_generator,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the M6 learned_v1 program generator.")
    parser.add_argument("--data", type=Path, default=Path("artifacts/m6/program_train.jsonl"))
    parser.add_argument("--out-dir", type=Path, default=Path("artifacts/m6"))
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--patience", type=int, default=3)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--resume-ckpt", type=Path, default=None)
    return parser.parse_args()


def _load_jsonl(path: Path) -> List[Dict[str, object]]:
    if not path.exists():
        raise FileNotFoundError(f"program training data not found: {path}")
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
        raise ValueError(f"program training data is empty: {path}")
    return rows


def _to_samples(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    samples: List[Dict[str, object]] = []
    for row in rows:
        features = row.get("features")
        targets = row.get("targets")
        if not isinstance(features, list) or not isinstance(targets, dict):
            continue
        samples.append(
            {
                "scene_id": str(row.get("scene_id", "")),
                "features": features,
                "targets": targets,
            }
        )
    if not samples:
        raise RuntimeError("No usable M6 training samples after preprocessing.")
    return samples


def train_from_jsonl(
    *,
    data_path: Path,
    out_dir: Path,
    config: ProgramTrainConfig,
    resume_ckpt: Optional[Path] = None,
    progress_callback=None,
) -> Dict[str, object]:
    samples = _to_samples(_load_jsonl(Path(data_path).resolve()))
    train_samples, val_samples = split_program_samples_by_scene(samples, train_ratio=0.9)
    result = train_program_generator(
        train_samples=train_samples,
        val_samples=val_samples,
        out_dir=Path(out_dir).resolve(),
        config=config,
        resume_checkpoint=resume_ckpt,
        progress_callback=progress_callback,
    )
    summary = {
        "split": {"train": len(train_samples), "val": len(val_samples)},
        "outputs": {
            "checkpoint": result["checkpoint"],
            "meta_path": result["meta_path"],
            "curve_path": result["curve_path"],
        },
        "best_val_loss": float(result["meta"]["best_val_loss"]),
    }
    summary_path = Path(out_dir).resolve() / "program_generator_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=True), encoding="utf-8")
    summary["outputs"]["summary_path"] = str(summary_path)
    return summary


def main() -> int:
    args = parse_args()
    try:
        summary = train_from_jsonl(
            data_path=Path(args.data).resolve(),
            out_dir=Path(args.out_dir).resolve(),
            config=ProgramTrainConfig(
                epochs=int(args.epochs),
                batch_size=int(args.batch_size),
                lr=float(args.lr),
                weight_decay=float(args.weight_decay),
                patience=int(args.patience),
                device=args.device,
            ),
            resume_ckpt=Path(args.resume_ckpt).resolve() if args.resume_ckpt else None,
        )
        print(json.dumps(summary, indent=2, ensure_ascii=True))
        return 0
    except Exception as exc:
        print(f"Train program generator failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
