#!/usr/bin/env python3
"""
Workbench 自动化测试脚本

随机选择一个预设模板，执行完整的场景生成流程，并生成测试报告。

Usage:
    uv run python scripts/test_workflow.py
    uv run python scripts/test_workflow.py --preset pedestrian_friendly
    uv run python scripts/test_workflow.py --help
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:
    print("Error: httpx is required. Install with: uv add httpx")
    sys.exit(1)

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # type: ignore


# ── Configuration ────────────────────────────────────────────────────────────────

SCENE_PRESETS = [
    {
        "id": "pedestrian_friendly",
        "name": "步行友好",
        "name_en": "Pedestrian Friendly",
        "prompt": "步行安全，全龄友好的完整街道，安静、安全、舒适",
        "config_patch": {
            "design_rule_profile": "pedestrian_priority_v1",
            "objective_profile": "balanced",
            "density": 0.5,
            "ped_demand_level": "high",
            "bike_demand_level": "medium",
            "transit_demand_level": "medium",
            "vehicle_demand_level": "low",
        },
    },
    {
        "id": "commercial_vitality",
        "name": "商业活力",
        "name_en": "Commercial Vitality",
        "prompt": "商业活跃的街道，商业设施密集，人流穿梭",
        "config_patch": {
            "design_rule_profile": "balanced_complete_street_v1",
            "objective_profile": "commerce",
            "density": 0.9,
            "ped_demand_level": "high",
            "bike_demand_level": "medium",
            "transit_demand_level": "high",
            "vehicle_demand_level": "medium",
        },
    },
    {
        "id": "transit_priority",
        "name": "公交优先",
        "name_en": "Transit Priority",
        "prompt": "公交优先的街道，公交可达性高，换乘便利",
        "config_patch": {
            "design_rule_profile": "transit_priority_v1",
            "objective_profile": "transit",
            "density": 0.85,
            "ped_demand_level": "high",
            "bike_demand_level": "medium",
            "transit_demand_level": "high",
            "vehicle_demand_level": "high",
        },
    },
    {
        "id": "park_landscape",
        "name": "公园景观",
        "name_en": "Park Landscape",
        "prompt": "公园景观街道，绿化丰富，自然生态，休闲舒适",
        "config_patch": {
            "design_rule_profile": "pedestrian_priority_v1",
            "objective_profile": "greening",
            "density": 0.2,
            "ped_demand_level": "medium",
            "bike_demand_level": "medium",
            "transit_demand_level": "low",
            "vehicle_demand_level": "low",
        },
    },
    {
        "id": "quiet_residential",
        "name": "安静居住",
        "name_en": "Quiet Residential",
        "prompt": "安静居住街道，绿树成荫，步行安全，适合全龄",
        "config_patch": {
            "design_rule_profile": "pedestrian_priority_v1",
            "objective_profile": "greening",
            "density": 0.3,
            "ped_demand_level": "low",
            "bike_demand_level": "medium",
            "transit_demand_level": "low",
            "vehicle_demand_level": "low",
        },
    },
    {
        "id": "balanced_complete",
        "name": "平衡街道",
        "name_en": "Balanced Complete",
        "prompt": "各类使用者平衡的完整街道，行人、自行车、公交、机动车和谐共处",
        "config_patch": {
            "design_rule_profile": "balanced_complete_street_v1",
            "objective_profile": "balanced",
            "density": 0.6,
            "ped_demand_level": "medium",
            "bike_demand_level": "medium",
            "transit_demand_level": "medium",
            "vehicle_demand_level": "medium",
        },
    },
]

DEFAULT_GRAPH_TEMPLATE_ID = "hkust_gz_gate"

# ── Data Classes ────────────────────────────────────────────────────────────────

@dataclass
class TestResult:
    preset_id: str
    preset_name: str
    job_id: str
    status: str  # "passed", "failed", "timeout"

    # Timing
    job_created_at: str
    job_completed_at: str | None
    duration_seconds: float

    # Scene generation
    scene_layout_path: str | None
    scene_glb_path: str | None
    viewer_url: str | None

    # Evaluation
    evaluation: dict | None
    error_message: str | None

    # Report path
    report_path: str


# ── API Client ─────────────────────────────────────────────────────────────────

class WorkbenchClient:
    def __init__(self, base_url: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=timeout)

    def close(self):
        self.client.close()

    def create_scene_job(self, preset: dict) -> dict:
        """Create a scene generation job."""
        payload = {
            "draft": {
                "normalized_scene_query": preset["prompt"],
                "compose_config_patch": preset["config_patch"],
                "citations_by_field": {},
                "design_summary": preset["prompt"],
                "risk_notes": [],
                "parameter_sources_by_field": {},
            },
            "scene_context": {
                "layout_mode": "graph_template",
                "aoi_bbox": None,
                "city_name_en": None,
                "reference_plan_id": None,
                "graph_template_id": DEFAULT_GRAPH_TEMPLATE_ID,
            },
            "patch_overrides": {},
            "generation_options": {"preset_id": preset["id"]},
        }

        response = self.client.post(f"{self.base_url}/api/scene/jobs", json=payload)
        response.raise_for_status()
        return response.json()

    def get_job_status(self, job_id: str) -> dict:
        """Get job status."""
        response = self.client.get(f"{self.base_url}/api/scene/jobs/{job_id}")
        response.raise_for_status()
        return response.json()

    def evaluate_scene(self, layout_path: str) -> dict:
        """Evaluate scene with LLM."""
        payload = {
            "layout_path": layout_path,
            "image_path": None,
        }
        response = self.client.post(
            f"{self.base_url}/api/design/evaluate/unified",
            json=payload,
            timeout=120.0,  # Evaluation may take longer
        )
        response.raise_for_status()
        return response.json()

    def health_check(self) -> bool:
        """Check if API is available."""
        try:
            response = self.client.get(f"{self.base_url}/api/health")
            return response.status_code == 200
        except Exception:
            return False


# ── Test Runner ────────────────────────────────────────────────────────────────

def run_test(
    client: WorkbenchClient,
    preset: dict,
    poll_interval: float = 2.0,
    timeout: float = 300.0,
) -> TestResult:
    """Run the workflow test for a given preset."""

    start_time = time.time()
    job_created_at = datetime.now().isoformat()
    result = TestResult(
        preset_id=preset["id"],
        preset_name=preset["name"],
        job_id="",
        status="failed",
        job_created_at=job_created_at,
        job_completed_at=None,
        duration_seconds=0.0,
        scene_layout_path=None,
        scene_glb_path=None,
        viewer_url=None,
        evaluation=None,
        error_message=None,
        report_path="",
    )

    try:
        # Step 1: Create job
        print(f"[1/4] 创建场景生成任务...")
        job_response = client.create_scene_job(preset)
        result.job_id = job_response.get("job_id", "")
        print(f"      任务 ID: {result.job_id}")

        # Step 2: Poll for completion
        print(f"[2/4] 等待场景生成完成...")
        elapsed = 0.0
        while elapsed < timeout:
            status_response = client.get_job_status(result.job_id)
            status = status_response.get("status", "")

            if status == "succeeded":
                result.job_completed_at = datetime.now().isoformat()
                result.scene_layout_path = status_response.get("result", {}).get("scene_layout_path")
                result.scene_glb_path = status_response.get("result", {}).get("scene_glb_path")
                result.viewer_url = status_response.get("result", {}).get("viewer_url")
                print(f"      场景生成完成!")
                print(f"      布局路径: {result.scene_layout_path}")
                break
            elif status == "failed":
                result.error_message = "Job failed"
                print(f"      场景生成失败!")
                return result

            # Still pending/running, wait
            time.sleep(poll_interval)
            elapsed = time.time() - start_time
            print(f"      状态: {status} ({elapsed:.0f}s)", end="\r")

        else:
            # Timeout
            result.status = "timeout"
            result.error_message = f"Job timed out after {timeout}s"
            print(f"\n      超时!")
            return result

        # Step 3: Evaluate scene
        print(f"[3/4] 调用 LLM 评估...")
        try:
            result.evaluation = client.evaluate_scene(result.scene_layout_path)
            print(f"      评估完成!")
        except Exception as e:
            print(f"      评估失败: {e}")
            # Evaluation is optional, don't fail the test
            result.evaluation = None

        # Step 4: Complete
        result.status = "passed"
        print(f"[4/4] 测试完成!")

    except httpx.ConnectError as e:
        result.error_message = f"Connection error: {e}"
        print(f"\n❌ 连接 API 失败: {e}")
    except httpx.TimeoutException as e:
        result.error_message = f"Request timeout: {e}"
        print(f"\n❌ 请求超时: {e}")
    except Exception as e:
        result.error_message = f"Unexpected error: {e}"
        print(f"\n❌ 错误: {e}")
    finally:
        result.duration_seconds = time.time() - start_time

    return result


# ── Report Generator ────────────────────────────────────────────────────────────

def generate_report(result: TestResult, output_dir: Path) -> str:
    """Generate markdown report."""

    # Ensure output directory exists
    output_dir.mkdir(parents=True, exist_ok=True)

    # Generate filename
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"test_{timestamp}.md"
    filepath = output_dir / filename

    # Status emoji
    status_emoji = {
        "passed": "✅",
        "failed": "❌",
        "timeout": "⏱️",
    }.get(result.status, "❓")

    # Build report
    lines = [
        "# Workbench 自动化测试报告",
        "",
        f"**测试时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**模板**: {result.preset_name} (`{result.preset_id}`)",
        f"**状态**: {status_emoji} {result.status.upper()}",
        "",
        "## 执行摘要",
        "",
        "| 指标 | 值 |",
        "|------|-----|",
        f"| 总耗时 | {result.duration_seconds:.1f} 秒 |",
        f"| 任务 ID | `{result.job_id}` |",
        f"| 评估状态 | {'成功' if result.evaluation else '失败/跳过'} |",
        "",
    ]

    # Scene generation section
    lines.extend([
        "## 场景生成",
        "",
        f"- **状态**: {result.status}",
        f"- **布局路径**: `{result.scene_layout_path or 'N/A'}`",
        f"- **GLB 路径**: `{result.scene_glb_path or 'N/A'}`",
        f"- **Viewer URL**: {result.viewer_url or 'N/A'}",
        "",
    ])

    # Evaluation section
    if result.evaluation:
        eval_data = result.evaluation
        scores = {
            "步行性": eval_data.get("walkability", 0),
            "安全性": eval_data.get("safety", 0),
            "美观性": eval_data.get("beauty", 0),
            "**综合**": eval_data.get("overall", 0),
        }

        lines.extend([
            "## 评估结果",
            "",
            "### 综合评分",
            "",
            "| 维度 | 分数 |",
            "|------|------|",
        ])
        for dim, score in scores.items():
            lines.append(f"| {dim} | {score} |")

        # Indicators
        indicators = eval_data.get("indicators")
        if indicators:
            lines.extend([
                "",
                "### 详细指标",
                "",
                "| 指标 | 值 | 说明 |",
                "|------|------|------|",
            ])
            indicator_names = {
                "SID_CLR": "人行道净宽",
                "CLEAR_CONT": "净空连续性",
                "FURN_D": "街道家具密度",
                "LIGHT_UNI": "照明均匀度",
                "TREE_SHADE": "绿化遮荫率",
                "BUFFER_RATIO": "缓冲带比例",
                "TRANSIT_PROX": "公交站可达性",
                "CROSS_PROV": "过街设施",
                "ENTR_DENS": "入口密度",
                "POI_MIX": "POI 混合度",
                "MICRO_ENV": "微气候环境",
            }
            for key, name in indicator_names.items():
                value = indicators.get(key)
                if value is not None:
                    lines.append(f"| {key} | {value:.2f} | {name} |")

        # Evaluation text
        if eval_data.get("evaluation"):
            lines.extend([
                "",
                "### LLM 评价",
                "",
                f"> {eval_data['evaluation']}",
                "",
            ])

        # Suggestions
        suggestions = eval_data.get("suggestions", [])
        if suggestions:
            lines.extend([
                "",
                "### 改进建议",
                "",
            ])
            for i, suggestion in enumerate(suggestions, 1):
                lines.append(f"{i}. {suggestion}")
            lines.append("")

    elif result.status == "passed":
        lines.extend([
            "## 评估结果",
            "",
            "*评估未执行或失败*",
            "",
        ])

    # Error section
    if result.error_message:
        lines.extend([
            "## 错误信息",
            "",
            f"```",
            f"{result.error_message}",
            f"```",
            "",
        ])

    # Raw data section
    lines.extend([
        "## 原始数据",
        "",
        "```json",
        json.dumps(asdict(result), indent=2, ensure_ascii=False),
        "```",
        "",
        "---",
        "",
        "*由 test_workflow.py 自动生成*",
    ])

    # Write report
    filepath.write_text("\n".join(lines), encoding="utf-8")
    return str(filepath)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Workbench 自动化测试脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run python scripts/test_workflow.py
  uv run python scripts/test_workflow.py --preset pedestrian_friendly
  uv run python scripts/test_workflow.py --api-base http://127.0.0.1:8010 --timeout 600
        """,
    )
    parser.add_argument(
        "--preset",
        choices=[p["id"] for p in SCENE_PRESETS],
        default=None,
        help="指定预设模板 ID (默认: 随机选择)",
    )
    parser.add_argument(
        "--api-base",
        default="http://127.0.0.1:8010",
        help="API 基础地址 (默认: http://127.0.0.1:8010)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="任务超时时间，秒 (默认: 300)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/test_reports"),
        help="报告输出目录 (默认: artifacts/test_reports)",
    )

    args = parser.parse_args()

    # Load .env if available
    if load_dotenv:
        load_dotenv()

    # Select preset
    if args.preset:
        preset = next(p for p in SCENE_PRESETS if p["id"] == args.preset)
    else:
        preset = random.choice(SCENE_PRESETS)

    print("=" * 60)
    print("Workbench 自动化测试")
    print("=" * 60)
    print(f"模板: {preset['name']} ({preset['id']})")
    print(f"API: {args.api_base}")
    print(f"超时: {args.timeout}s")
    print("-" * 60)

    # Create client
    client = WorkbenchClient(args.api_base)

    try:
        # Check health
        print("检查 API 连接...")
        if not client.health_check():
            print("❌ API 不可用，请确保后端服务正在运行:")
            print(f"   uv run uvicorn web.api.main:app --reload --port 8010")
            sys.exit(1)
        print("✓ API 连接正常")
        print()

        # Run test
        result = run_test(client, preset, timeout=args.timeout)

        # Generate report
        print()
        print("-" * 60)
        report_path = generate_report(result, args.output)
        print(f"报告已生成: {report_path}")
        print()

        # Print summary
        print("=" * 60)
        print("测试摘要")
        print("=" * 60)
        status_emoji = {"passed": "✅", "failed": "❌", "timeout": "⏱️"}.get(result.status, "❓")
        print(f"状态: {status_emoji} {result.status.upper()}")
        print(f"耗时: {result.duration_seconds:.1f}s")
        if result.evaluation:
            print(f"综合评分: {result.evaluation.get('overall', 'N/A')}")
        print("=" * 60)

        # Exit code
        sys.exit(0 if result.status == "passed" else 1)

    finally:
        client.close()


if __name__ == "__main__":
    main()
