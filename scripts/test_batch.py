#!/usr/bin/env python3
"""
批量测试脚本 - 同时生成多个模板的场景

并行运行多个预设模板的场景生成，便于快速对比不同配置的效果。

Usage:
    uv run python scripts/test_batch.py
    uv run python scripts/test_batch.py --presets pedestrian_friendly commercial_vitality
    uv run python scripts/test_batch.py --all --timeout 600
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import random
import sys
import threading
import time
from dataclasses import dataclass, field
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
    load_dotenv = None


# ── Import shared presets ────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from roadgen3d.presets import SCENE_PRESETS

# Map camelCase fields from shared presets to snake_case for backward compatibility
def _adapt_preset(preset: dict) -> dict:
    """Adapt shared preset format to batch test format."""
    return {
        "id": preset["id"],
        "name": preset["name"],
        "name_en": preset["nameEn"],
        "prompt": preset["prompt"],
        "config_patch": preset["configPatch"],
    }

SCENE_PRESETS_BATCH = [_adapt_preset(p) for p in SCENE_PRESETS]

# 可用的 Graph Templates
GRAPH_TEMPLATES = [
    {"id": "hkust_gz_gate", "label": "HKUST-GZ Gate Graph"},
    {"id": "hkust_gz_detailed", "label": "HKUST-GZ Detailed (5建筑区+10道路)"},
    {"id": "hkust_gz_gate_all", "label": "HKUST-GZ Gate (All)"},
]

DEFAULT_GRAPH_TEMPLATE_ID = "hkust_gz_gate"
DEFAULT_RANDOM_SEED = 42


# ── Data Classes ────────────────────────────────────────────────────────────────

@dataclass
class BatchResult:
    """批量测试结果"""
    preset_id: str
    preset_name: str
    job_id: str = ""
    status: str = "pending"  # pending, running, succeeded, failed, timeout
    viewer_url: str | None = None
    scene_layout_path: str | None = None
    scene_glb_path: str | None = None
    duration_seconds: float = 0.0
    error_message: str | None = None
    evaluation: dict | None = None
    start_time: float = 0.0
    end_time: float = 0.0


# ── API Client ─────────────────────────────────────────────────────────────────

class WorkbenchClient:
    def close(self):
        self.client.close()

    def __init__(self, base_url: str, timeout: float = 900.0, graph_template_id: str = DEFAULT_GRAPH_TEMPLATE_ID, use_llm: bool = False):
        self.base_url = base_url.rstrip("/")
        self.graph_template_id = graph_template_id
        self.use_llm = use_llm
        transport = httpx.HTTPTransport(retries=2)
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=30.0),
            transport=transport
        )

    def generate_draft(self, user_input: str, preset_id: str, knowledge_source: str = "graph_rag") -> dict:
        """Generate design draft using LLM with RAG."""
        payload = {
            "messages": [],
            "user_input": user_input,
            "current_patch": {},
            "topk": 6,
            "knowledge_source": knowledge_source,
            "force": True,  # Skip clarification, force generation
        }
        response = self.client.post(f"{self.base_url}/api/design/draft", json=payload)
        response.raise_for_status()
        return response.json()

    def create_scene_job(self, preset: dict, patch_overrides: dict = None, graph_template_id: str = None, draft: dict = None) -> dict:
        """Create a scene generation job."""
        patch_overrides = patch_overrides or {}

        # Use LLM-generated draft if available
        if draft:
            scene_draft = draft.get("draft", draft)
            compose_config_patch = scene_draft.get("compose_config_patch", preset["config_patch"])
        else:
            compose_config_patch = preset["config_patch"]

        payload = {
            "draft": {
                "normalized_scene_query": preset["prompt"],
                "compose_config_patch": compose_config_patch,
                "citations_by_field": draft.get("draft", {}).get("citations_by_field", {}) if draft else {},
                "design_summary": draft.get("draft", {}).get("design_summary", preset["prompt"]) if draft else preset["prompt"],
                "risk_notes": [],
                "parameter_sources_by_field": draft.get("draft", {}).get("parameter_sources_by_field", {}) if draft else {},
            },
            "scene_context": {
                "layout_mode": "graph_template",
                "aoi_bbox": None,
                "city_name_en": None,
                "reference_plan_id": None,
                "graph_template_id": graph_template_id or self.graph_template_id,
            },
            "patch_overrides": patch_overrides,
            "generation_options": {"preset_id": preset["id"]},
        }
        response = self.client.post(f"{self.base_url}/api/scene/jobs", json=payload)
        response.raise_for_status()
        return response.json()

    def get_job_status(self, job_id: str) -> dict:
        """Get job status using curl."""
        import subprocess
        try:
            result = subprocess.run(
                ["curl", "-s", "-m", "30", f"{self.base_url}/api/scene/jobs/{job_id}"],
                capture_output=True,
                text=True,
                timeout=35
            )
            if result.returncode != 0:
                raise Exception(f"curl failed: {result.stderr}")
            return json.loads(result.stdout)
        except subprocess.TimeoutExpired:
            raise Exception("Job status request timeout")
        except json.JSONDecodeError as e:
            raise Exception(f"Invalid JSON response: {e}")

    def evaluate_scene(self, layout_path: str) -> dict:
        """Evaluate scene with LLM."""
        payload = {
            "layout_path": layout_path,
            "image_path": None,
        }
        response = self.client.post(
            f"{self.base_url}/api/design/evaluate/unified",
            json=payload,
            timeout=120.0,
        )
        response.raise_for_status()
        return response.json()

    def health_check(self) -> bool:
        """Check if API is available."""
        try:
            transport = httpx.HTTPTransport(retries=0)
            with httpx.Client(timeout=10.0, transport=transport) as client:
                response = client.get(f"{self.base_url}/api/health")
                return response.status_code == 200
        except Exception:
            return False


# ── Batch Runner ────────────────────────────────────────────────────────────────

def run_single_test(
    client: WorkbenchClient,
    preset: dict,
    result: BatchResult,
    poll_interval: float = 2.0,
    timeout: float = 600.0,
    lock: threading.Lock = None,
    progress_callback: callable = None,
    graph_template_id: str = None,
    use_llm: bool = False,
) -> BatchResult:
    """Run a single preset test and update result object."""

    def print_status(msg: str):
        if lock:
            with lock:
                print(msg)
        else:
            print(msg)

    result.status = "running"
    result.start_time = time.time()

    try:
        # Generate random scene seed for variation
        scene_seed = int(time.time() * 1000000 + id(preset)) % 1000000 + random.randint(1, 9999)
        template_id = graph_template_id or client.graph_template_id

        # Generate LLM draft if enabled
        draft = None
        if use_llm:
            print_status(f"  [LLM] 生成设计中: {preset['name']} ({preset['id']})")
            try:
                draft = client.generate_draft(
                    user_input=preset["prompt"],
                    preset_id=preset["id"],
                    knowledge_source="graph_rag"
                )
                print_status(f"  [LLM] 设计已生成")
            except Exception as e:
                print_status(f"  [LLM] 生成失败，回退到预设配置: {e}")
                draft = None

        # Create job
        print_status(f"  [Job] 创建任务: {preset['name']} ({preset['id']}) template={template_id} seed={scene_seed}")
        job_response = client.create_scene_job(
            preset,
            patch_overrides={"seed": scene_seed},
            graph_template_id=template_id,
            draft=draft
        )
        result.job_id = job_response.get("job_id", "")

        # Poll for completion
        elapsed = 0.0
        while elapsed < timeout:
            status_response = client.get_job_status(result.job_id)
            status = status_response.get("status", "")

            if status == "succeeded":
                result.status = "succeeded"
                result.scene_layout_path = status_response.get("result", {}).get("scene_layout_path")
                result.scene_glb_path = status_response.get("result", {}).get("scene_glb_path")
                result.viewer_url = status_response.get("result", {}).get("viewer_url")
                break

            elif status == "failed":
                result.status = "failed"
                result.error_message = status_response.get("error", "Job failed")
                break

            # Still running
            if progress_callback:
                progress_callback(result.preset_id, status, elapsed / timeout)

            time.sleep(poll_interval)
            elapsed = time.time() - result.start_time
        else:
            # Timeout
            result.status = "timeout"
            result.error_message = f"Job timed out after {timeout}s"

    except Exception as e:
        result.status = "failed"
        result.error_message = str(e)

    result.end_time = time.time()
    result.duration_seconds = result.end_time - result.start_time

    # Evaluate if succeeded
    if result.status == "succeeded" and result.scene_layout_path:
        try:
            print_status(f"  [Eval] 评估场景: {preset['name']}")
            result.evaluation = client.evaluate_scene(result.scene_layout_path)
        except Exception as e:
            print_status(f"  [Eval] 评估失败: {e}")

    return result


def run_batch(
    client: WorkbenchClient,
    presets: list[dict],
    max_workers: int = 6,
    timeout: float = 600.0,
    random_template: bool = False,
    use_llm: bool = False,
) -> list[BatchResult]:
    """Run multiple preset tests in parallel."""

    # Initialize results
    results = [BatchResult(preset_id=p["id"], preset_name=p["name"]) for p in presets]

    # Determine template assignments
    if random_template:
        # Each preset gets a random template
        template_assignments = [random.choice(GRAPH_TEMPLATES)["id"] for _ in presets]
        print(f"  模板分配: {dict(zip([p['id'] for p in presets], template_assignments))}")
    else:
        template_assignments = [client.graph_template_id] * len(presets)

    # Lock for synchronized printing
    lock = threading.Lock()
    active_count = [len(presets)]  # Use list to allow modification in nested function
    completed_count = [0]

    def progress_callback(preset_id: str, status: str, progress: float):
        """Callback for progress updates."""
        with lock:
            completed = completed_count[0]
            total = active_count[0]
            bar = "█" * int(progress * 20) + "░" * (20 - int(progress * 20))
            print(f"\r  [{completed}/{total}] {bar} {progress*100:5.1f}% | {preset_id}: {status}  ", end="", flush=True)

    print(f"启动 {len(presets)} 个并行任务 (max_workers={max_workers})")
    if random_template:
        print("  模式: 随机模板 + LLM生成" if use_llm else "  模式: 随机模板")
    elif use_llm:
        print("  模式: LLM动态生成")
    print()

    # Use ThreadPoolExecutor for parallel execution
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for preset, result, template_id in zip(presets, results, template_assignments):
            future = executor.submit(
                run_single_test,
                client,
                preset,
                result,
                poll_interval=2.0,
                timeout=timeout,
                lock=lock,
                progress_callback=progress_callback,
                graph_template_id=template_id,
                use_llm=use_llm,
            )
            futures[future] = preset

        # Wait for all to complete
        for future in concurrent.futures.as_completed(futures):
            preset = futures[future]
            try:
                result = future.result()
                completed_count[0] += 1
                with lock:
                    status_emoji = {"succeeded": "✅", "failed": "❌", "timeout": "⏱️"}.get(result.status, "❓")
                    duration_str = f"{result.duration_seconds:.1f}s"
                    print(f"\n  {status_emoji} 完成: {preset['name']} ({duration_str})")
                    if result.viewer_url:
                        print(f"      URL: {result.viewer_url}")
            except Exception as e:
                completed_count[0] += 1
                with lock:
                    print(f"\n  ❌ 异常: {preset['name']} - {e}")

    print()
    return results


# ── Report Generation ───────────────────────────────────────────────────────────

def generate_batch_report(results: list[BatchResult], output_dir: Path) -> tuple[str, list[str]]:
    """Generate batch test report with all viewer URLs."""
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"batch_test_{timestamp}.md"
    filepath = output_dir / filename

    # Count stats
    total = len(results)
    succeeded = sum(1 for r in results if r.status == "succeeded")
    failed = sum(1 for r in results if r.status == "failed")
    timeout = sum(1 for r in results if r.status == "timeout")

    viewer_urls: list[str] = []

    lines = [
        "# 批量测试报告",
        "",
        f"**测试时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 统计摘要",
        "",
        "| 指标 | 值 |",
        "|------|-----|",
        f"| 总任务数 | {total} |",
        f"| ✅ 成功 | {succeeded} |",
        f"| ❌ 失败 | {failed} |",
        f"| ⏱️ 超时 | {timeout} |",
        "",
        "## Viewer 链接",
        "",
    ]

    # Add viewer URLs section
    for result in results:
        status_emoji = {"succeeded": "✅", "failed": "❌", "timeout": "⏱️", "pending": "⏳", "running": "🔄"}.get(result.status, "?")
        lines.append(f"### {status_emoji} {result.preset_name} (`{result.preset_id}`)")
        if result.viewer_url:
            lines.append(f"- **URL**: {result.viewer_url}")
            viewer_urls.append(result.viewer_url)
        else:
            lines.append(f"- **URL**: N/A")
        lines.append(f"- **状态**: {result.status}")
        lines.append(f"- **耗时**: {result.duration_seconds:.1f}s")
        if result.scene_layout_path:
            lines.append(f"- **布局**: `{result.scene_layout_path}`")
        if result.evaluation:
            overall = result.evaluation.get("overall", "N/A")
            lines.append(f"- **综合评分**: {overall}")
        lines.append("")

    lines.extend([
        "## 详细结果",
        "",
        "| 模板 | 状态 | 耗时 | 综合评分 |",
        "|------|------|------|----------|",
    ])

    for result in results:
        status_emoji = {"succeeded": "✅", "failed": "❌", "timeout": "⏱️"}.get(result.status, "?")
        score = result.evaluation.get("overall", "N/A") if result.evaluation else "N/A"
        lines.append(f"| {result.preset_name} | {status_emoji} {result.status} | {result.duration_seconds:.1f}s | {score} |")

    lines.extend([
        "",
        "## 原始数据",
        "",
        "```json",
    ])

    # Serialize results
    serialized = []
    for r in results:
        serialized.append({
            "preset_id": r.preset_id,
            "preset_name": r.preset_name,
            "job_id": r.job_id,
            "status": r.status,
            "viewer_url": r.viewer_url,
            "scene_layout_path": r.scene_layout_path,
            "scene_glb_path": r.scene_glb_path,
            "duration_seconds": r.duration_seconds,
            "evaluation": r.evaluation,
            "error_message": r.error_message,
        })
    lines.append(json.dumps(serialized, indent=2, ensure_ascii=False))
    lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*由 test_batch.py 自动生成*")

    filepath.write_text("\n".join(lines), encoding="utf-8")
    return str(filepath), viewer_urls


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="批量测试脚本 - 并行生成多个模板场景",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # 默认: 随机选择 3 个 preset
  uv run python scripts/test_batch.py

  # 指定 preset
  uv run python scripts/test_batch.py --presets pedestrian_friendly commercial_vitality

  # 运行所有 6 个 preset
  uv run python scripts/test_batch.py --all

  # 随机为每个 preset 分配不同的 graph template
  uv run python scripts/test_batch.py --all --random-template

  # 启用 LLM 动态生成配置
  uv run python scripts/test_batch.py --all --use-llm

  # 组合: 随机 template + LLM 生成
  uv run python scripts/test_batch.py --all --random-template --use-llm

  # 指定 graph template
  uv run python scripts/test_batch.py --all --graph-template hkust_gz_detailed

  # 列出所有可用的 templates
  uv run python scripts/test_batch.py --list-templates
        """,
    )
    parser.add_argument(
        "--presets",
        nargs="+",
        choices=[p["id"] for p in SCENE_PRESETS_BATCH],
        default=None,
        help="指定预设模板 ID (默认: 随机选择 3 个)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="运行所有 6 个模板",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=6,
        help="并行工作线程数 (默认: 6)",
    )
    parser.add_argument(
        "--api-base",
        default="http://127.0.0.1:8010",
        help="API 基础地址 (默认: http://127.0.0.1:8010)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=600.0,
        help="单任务超时时间，秒 (默认: 600)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/test_reports"),
        help="报告输出目录 (默认: artifacts/test_reports)",
    )
    parser.add_argument(
        "--graph-template",
        default=None,  # None means random
        choices=[t["id"] for t in GRAPH_TEMPLATES],
        help=f"指定使用的 graph template ID (默认: 随机选择)",
    )
    parser.add_argument(
        "--random-template",
        action="store_true",
        help="为每个 preset 随机分配不同的 graph template",
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="启用 LLM 动态生成配置 (GraphRAG + LLM)",
    )
    parser.add_argument(
        "--list-templates",
        action="store_true",
        help="列出所有可用的 graph templates",
    )

    args = parser.parse_args()

    # List templates and exit if requested
    if args.list_templates:
        print("可用的 Graph Templates:")
        for t in GRAPH_TEMPLATES:
            print(f"  - {t['id']}: {t['label']}")
        sys.exit(0)

    # Load .env if available
    if load_dotenv:
        load_dotenv()

    # Select presets
    if args.all:
        selected_presets = SCENE_PRESETS_BATCH
    elif args.presets:
        selected_presets = [p for p in SCENE_PRESETS_BATCH if p["id"] in args.presets]
        if not selected_presets:
            print("错误: 未找到指定的模板")
            sys.exit(1)
    else:
        selected_presets = random.sample(SCENE_PRESETS_BATCH, min(3, len(SCENE_PRESETS_BATCH)))

    # Determine graph template
    graph_template_id = args.graph_template or DEFAULT_GRAPH_TEMPLATE_ID

    print("=" * 60)
    print("批量测试 - 并行场景生成")
    print("=" * 60)
    print(f"模板数量: {len(selected_presets)}")
    for p in selected_presets:
        print(f"  - {p['name']} ({p['id']})")
    print(f"API: {args.api_base}")
    print(f"Graph Template: {graph_template_id if not args.random_template else '(随机分配)'}")
    print(f"并行数: {args.workers}")
    print(f"超时: {args.timeout}s")
    print(f"LLM 生成: {'启用' if args.use_llm else '禁用 (使用预设配置)'}")
    print("-" * 60)

    # Create client
    client = WorkbenchClient(args.api_base, graph_template_id=graph_template_id, use_llm=args.use_llm)

    try:
        # Check health
        print("检查 API 连接...")
        if not client.health_check():
            print("❌ API 不可用，请确保后端服务正在运行:")
            print(f"   uv run uvicorn web.api.main:app --reload --port 8010")
            sys.exit(1)
        print("✓ API 连接正常")
        print()

        # Run batch
        start_time = time.time()
        results = run_batch(
            client,
            selected_presets,
            max_workers=args.workers,
            timeout=args.timeout,
            random_template=args.random_template,
            use_llm=args.use_llm,
        )
        total_time = time.time() - start_time

        print()
        print("=" * 60)
        print("批量测试完成")
        print("=" * 60)

        # Generate report
        report_path, viewer_urls = generate_batch_report(results, args.output)
        print(f"报告已生成: {report_path}")

        # Print summary
        print()
        print("## 汇总")
        print()
        succeeded = sum(1 for r in results if r.status == "succeeded")
        print(f"成功: {succeeded}/{len(results)}")
        print(f"总耗时: {total_time:.1f}s")

        if viewer_urls:
            print()
            print("## Viewer 链接")
            print()
            for i, url in enumerate(viewer_urls, 1):
                print(f"{i}. {url}")
        else:
            print()
            print("⚠️ 没有成功的场景生成")

        # Exit code
        sys.exit(0 if succeeded == len(results) else 1)

    finally:
        client.close()


if __name__ == "__main__":
    main()
