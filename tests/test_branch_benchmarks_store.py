from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.services import branch_benchmarks  # noqa: E402
from roadgen3d.services.branch_benchmarks import BranchBenchmarkStore  # noqa: E402


def _sample(sample_id: str, *, walkability: float = 70.0) -> dict[str, object]:
    return {
        "sample_id": sample_id,
        "preset_id": "balanced_complete",
        "preset_name": "Balanced Complete",
        "preset_color": "#607D8B",
        "label": sample_id,
        "created_at": f"2026-05-01T00:00:0{len(sample_id)}+00:00",
        "status": "succeeded",
        "walkability": walkability,
        "safety": 68.0,
        "beauty": 66.0,
        "overall": round((walkability + 68.0 + 66.0) / 3.0, 3),
    }


def _manifest(run_id: str, node_id: str, *, walkability: float) -> dict[str, object]:
    return {
        "run_id": run_id,
        "preset_id": "balanced_complete",
        "created_at": "2026-05-01T00:00:00+00:00",
        "nodes": [{
            "node_id": node_id,
            "status": "succeeded",
            "created_at": "2026-05-01T00:00:00+00:00",
        }],
        "scatter_points": [{
            "node_id": node_id,
            "status": "succeeded",
            "walkability": walkability,
            "safety": 68.0,
            "beauty": 66.0,
            "overall": round((walkability + 68.0 + 66.0) / 3.0, 3),
        }],
    }


def test_query_samples_reuses_pareto_cache_and_invalidates_after_upsert(tmp_path: Path, monkeypatch):
    store = BranchBenchmarkStore(tmp_path / "bench")
    store.upsert_samples([_sample("sample-a")])
    calls = 0
    real_annotate = branch_benchmarks._annotate_pareto

    def _spy_annotate(samples):
        nonlocal calls
        calls += 1
        return real_annotate(samples)

    monkeypatch.setattr(branch_benchmarks, "_annotate_pareto", _spy_annotate)

    first = store.query_samples(limit=10)
    second = store.query_samples(limit=10)

    assert first["total"] == 1
    assert second["total"] == 1
    assert calls == 1

    store.upsert_samples([_sample("sample-b", walkability=75.0)])
    updated = store.query_samples(limit=10)

    assert updated["total"] == 2
    assert calls == 2


def test_import_branch_manifests_batches_sample_writes(tmp_path: Path):
    class _CountingStore(BranchBenchmarkStore):
        def __init__(self, root: Path):
            super().__init__(root)
            self.write_count = 0

        def _write_samples_locked(self, rows):  # type: ignore[override]
            self.write_count += 1
            super()._write_samples_locked(rows)

    branch_root = tmp_path / "branch_runs"
    for run_id, node_id, walkability in (("run-a", "node-a", 70.0), ("run-b", "node-b", 76.0)):
        run_dir = branch_root / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "manifest.json").write_text(
            json.dumps(_manifest(run_id, node_id, walkability=walkability)),
            encoding="utf-8",
        )

    store = _CountingStore(tmp_path / "bench")
    imported = store.import_branch_manifests(branch_root)

    assert imported["imported_samples"] == 2
    assert store.write_count == 1
    assert store.query_samples(limit=10)["total"] == 2

    imported_again = store.import_branch_manifests(branch_root)

    assert imported_again["imported_samples"] == 0
    assert store.write_count == 1
