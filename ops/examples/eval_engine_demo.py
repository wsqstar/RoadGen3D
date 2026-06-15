"""Example: Using the decoupled EvalEngine from road-metrics submodule."""

from __future__ import annotations

import sys
from pathlib import Path

# Add submodule to path
ROOT = Path(__file__).resolve().parents[1]
SUBMODULE = ROOT / "src" / "roadgen3d" / "eval_engine_ext"
if str(SUBMODULE) not in sys.path:
    sys.path.insert(0, str(SUBMODULE))

import json

from road_metrics import EvalEngine, EvalConfig
from road_metrics.reports.writer import write_evaluation_report


def main():
    # 1. Load scene
    scene_path = Path("artifacts/snapshot_diff_20260404_034235/iter_00/scene_layout.json")
    if not scene_path.exists():
        print(f"Scene not found: {scene_path}")
        print("Run scene generation first.")
        return

    payload = json.loads(scene_path.read_text(encoding="utf-8"))
    print(f"Loaded scene: {scene_path}")
    print(f"  Length: {payload.get('summary', {}).get('length_m', 'N/A')}m")
    print(f"  Placements: {len(payload.get('placements', []))}")

    # 2. Default evaluation
    print("\n=== Default Evaluation ===")
    engine = EvalEngine()
    result = engine.evaluate(payload)

    print(f"Walkability Index: {result.walkability.walkability_index:.4f}")
    print(f"  Protection: {result.walkability.protection:.4f}")
    print(f"  Comfort: {result.walkability.comfort:.4f}")
    print(f"  Delight: {result.walkability.delight:.4f}")

    print(f"\nSafety Score: {result.safety.final_score:.4f}")
    print(f"  Weakest: {result.safety.diagnosis.get('weakest', 'N/A')}")

    print(f"\nBeauty Score: {result.beauty.final_score:.4f}")
    print(f"  Weakest: {result.beauty.diagnosis.get('weakest', 'N/A')}")

    print(f"\nCombined Score: {result.evaluation_score:.4f}")

    if result.audio:
        print(f"\nAudio Profile:")
        for key, val in result.audio.to_dict()["ambient"].items():
            print(f"  {key}: {val:.3f}")

    # 3. Custom configuration
    print("\n=== Custom Configuration ===")
    custom_config = EvalConfig.from_dict({
        "walkability": {
            "protection_weight": 0.50,
            "comfort_weight": 0.30,
            "delight_weight": 0.20,
        },
        "aggregation": {
            "walkability_weight": 0.50,
            "safety_weight": 0.30,
            "beauty_weight": 0.20,
        },
        "enable_audio_profile": False,
    })

    engine2 = EvalEngine(custom_config)
    result2 = engine2.evaluate(payload)

    print(f"Combined Score (custom weights): {result2.evaluation_score:.4f}")
    print(f"  (Walkability weight increased from 0.45 to 0.50)")

    # 4. Save report
    report_path = Path("artifacts/eval_engine_report.json")
    write_evaluation_report(result, report_path)
    print(f"\nReport saved to: {report_path}")


if __name__ == "__main__":
    main()
