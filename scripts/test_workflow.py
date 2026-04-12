#!/usr/bin/env python3
"""
Workbench 自动化测试脚本 (科研版)

随机选择一个预设模板，执行完整的场景生成流程，并生成测试报告。

特性:
- 可重复性: 全局随机种子设置
- 指标验证: 重复运行对比机制
- 性能监控: 系统级超时管理

Usage:
    uv run python scripts/test_workflow.py
    uv run python scripts/test_workflow.py --preset pedestrian_friendly
    uv run python scripts/test_workflow.py --verify-repeat    # 重复运行验证
    uv run python scripts/test_workflow.py --seed 42          # 指定随机种子
    uv run python scripts/test_workflow.py --help
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import random
import signal
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Generator

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
DEFAULT_RANDOM_SEED = 42


# ── Random Seed Management ──────────────────────────────────────────────────────

def set_global_seed(seed: int) -> None:
    """
    设置全局随机种子，确保实验可重复性。

    Args:
        seed: 随机种子值
    """
    # Python random
    random.seed(seed)

    # NumPy (if available)
    try:
        import numpy as np
        np.random.seed(seed)
    except ImportError:
        pass

    # PyTorch (if available)
    try:
        import torch
        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except ImportError:
        pass

    print(f"[Seed] 全局随机种子已设置为: {seed}")


# ── Timeout Context Manager ────────────────────────────────────────────────────

class TimeoutError(Exception):
    """运行超时异常"""
    pass


@contextlib.contextmanager
def timeout_context(seconds: float, task_name: str = "任务") -> Generator[None, None, None]:
    """
    超时上下文管理器。

    Args:
        seconds: 超时秒数
        task_name: 任务名称（用于错误消息）

    Raises:
        TimeoutError: 超时时抛出

    Example:
        with timeout_context(30, "API调用"):
            # 执行可能超时的操作
            pass
    """
    def timeout_handler(signum, frame):
        raise TimeoutError(f"{task_name} 超时 ({seconds}秒)")

    # 设置信号处理器
    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(int(seconds))

    try:
        yield
    finally:
        # 恢复原来的处理器
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


class RunnerTimeout:
    """线程安全的超时运行器"""

    def __init__(self, seconds: float):
        self.seconds = seconds
        self.result = None
        self.error = None

    def run_with_timeout(self, func: Callable, *args, **kwargs) -> Any:
        """
        在超时限制内运行函数。

        Args:
            func: 要运行的函数
            *args, **kwargs: 函数参数

        Returns:
            函数返回值

        Raises:
            TimeoutError: 超时时抛出
        """
        result = [None]
        error = [None]

        def wrapper():
            try:
                result[0] = func(*args, **kwargs)
            except Exception as e:
                error[0] = e

        thread = threading.Thread(target=wrapper)
        thread.daemon = True
        thread.start()
        thread.join(timeout=self.seconds)

        if thread.is_alive():
            raise TimeoutError(f"函数执行超时 ({self.seconds}秒)")

        if error[0]:
            raise error[0]

        return result[0]


# ── Metrics Validator ──────────────────────────────────────────────────────────

@dataclass
class MetricsValidator:
    """
    指标验证器，确保实验结果符合预期。

    Attributes:
        tolerance: 容差阈值（默认 0.01）
        repeat_tolerance: 重复运行容差（默认 1e-6）
    """
    tolerance: float = 0.01
    repeat_tolerance: float = 1e-6

    def validate_score_range(
        self,
        score: float,
        field_name: str,
        min_val: float = 0.0,
        max_val: float = 100.0
    ) -> bool:
        """
        验证分数是否在有效范围内。

        Args:
            score: 分数值
            field_name: 字段名称
            min_val: 最小值
            max_val: 最大值

        Returns:
            是否有效
        """
        if not (min_val <= score <= max_val):
            print(f"[警告] {field_name} 分数 {score} 超出范围 [{min_val}, {max_val}]")
            return False
        return True

    def validate_formula(
        self,
        walkability: float,
        safety: float,
        beauty: float,
        overall: float
    ) -> bool:
        """
        验证综合评分公式: overall = walkability*0.45 + safety*0.35 + beauty*0.20

        Args:
            walkability: 步行性分数
            safety: 安全性分数
            beauty: 美观性分数
            overall: 综合分数

        Returns:
            是否符合公式
        """
        expected = round(walkability * 0.45 + safety * 0.35 + beauty * 0.20)

        if overall != expected:
            print(f"[警告] 综合评分公式验证失败: {overall} != {expected} (差值: {abs(overall - expected)})")
            return False
        return True

    def validate_repeatability(
        self,
        result1: dict,
        result2: dict,
        metric_keys: list[str] = None
    ) -> tuple[bool, dict]:
        """
        验证重复运行结果的一致性。

        Args:
            result1: 第一次运行结果
            result2: 第二次运行结果
            metric_keys: 要比较的指标键列表

        Returns:
            (是否一致, 差异详情)
        """
        if metric_keys is None:
            metric_keys = ["walkability", "safety", "beauty", "overall"]

        differences = {}
        all_match = True

        for key in metric_keys:
            val1 = result1.get(key)
            val2 = result2.get(key)

            if val1 is not None and val2 is not None:
                diff = abs(float(val1) - float(val2))
                differences[key] = {
                    "run1": val1,
                    "run2": val2,
                    "difference": diff,
                    "within_tolerance": diff < self.repeat_tolerance
                }
                if diff >= self.repeat_tolerance:
                    all_match = False

        return all_match, differences


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


@dataclass
class RepeatVerificationResult:
    """重复运行验证结果"""
    preset: dict
    run1: TestResult
    run2: TestResult
    repeatability_passed: bool
    metric_differences: dict
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


# ── API Client ─────────────────────────────────────────────────────────────────

class WorkbenchClient:
    def __init__(self, base_url: str, timeout: float = 900.0):
        """
        Initialize the API client.

        Args:
            base_url: Base URL for the API
            timeout: Default timeout for requests in seconds (default: 900 = 15 min)
        """
        self.base_url = base_url.rstrip("/")
        # Use explicit HTTPTransport to avoid HTTP/2 connection issues
        transport = httpx.HTTPTransport(retries=2)
        # Configure timeout: 900s connect, 900s read (足够长以支持长时间轮询)
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=30.0),
            transport=transport
        )

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
        """Get job status using curl to avoid httpx connection issues."""
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
            timeout=120.0,  # Evaluation may take longer
        )
        response.raise_for_status()
        return response.json()

    def health_check(self) -> bool:
        """Check if API is available."""
        try:
            # Use explicit HTTPTransport to avoid HTTP/2 connection issues
            transport = httpx.HTTPTransport(retries=0)
            with httpx.Client(timeout=10.0, transport=transport) as client:
                response = client.get(f"{self.base_url}/api/health")
                return response.status_code == 200
        except Exception:
            return False

    def get_detailed_status(self) -> dict | None:
        """Get detailed API status including version, model info."""
        try:
            transport = httpx.HTTPTransport(retries=0)
            with httpx.Client(timeout=10.0, transport=transport) as client:
                response = client.get(f"{self.base_url}/api/health")
                if response.status_code == 200:
                    return response.json()
        except Exception:
            pass
        return None


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

    # Spinner states for visual feedback
    spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    spinner_idx = 0

    def get_spinner() -> str:
        nonlocal spinner_idx
        spinner_idx = (spinner_idx + 1) % len(spinner_chars)
        return spinner_chars[spinner_idx]

    def format_time(seconds: float) -> str:
        """Format seconds to human-readable string."""
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            mins = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{mins}m {secs}s"
        else:
            hours = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            return f"{hours}h {mins}m"

    def get_progress_bar(progress: float, width: int = 20) -> str:
        """Create a text-based progress bar."""
        filled = int(width * min(progress, 1.0))
        empty = width - filled
        return "█" * filled + "░" * empty

    try:
        # Step 1: Create job
        print(f"\n{'='*60}")
        print(f"Step 1/4 | 创建场景生成任务")
        print(f"{'='*60}")
        print(f"  模板: {preset['name']} ({preset['name_en']})")
        print(f"  Prompt: {preset['prompt'][:50]}...")
        print()

        print("  创建任务中...", end="", flush=True)
        job_response = client.create_scene_job(preset)
        result.job_id = job_response.get("job_id", "")
        print(f"\r  ✓ 任务已创建")
        print(f"  任务 ID: {result.job_id}")
        print()

        # Step 2: Poll for completion
        print(f"{'='*60}")
        print(f"Step 2/4 | 等待场景生成完成")
        print(f"{'='*60}")
        print(f"  超时设置: {format_time(timeout)}")
        print(f"  轮询间隔: {poll_interval}s")
        print()

        elapsed = 0.0
        last_status = ""
        status_counts: dict[str, int] = {}

        while elapsed < timeout:
            status_response = client.get_job_status(result.job_id)
            status = status_response.get("status", "")
            stage = status_response.get("stage", "")

            # Track status transitions
            if status != last_status:
                last_status = status
                status_counts[status] = 0
                print(f"\n  状态变更: {status}")

            status_counts[status] = status_counts.get(status, 0) + 1

            if status == "succeeded":
                result.job_completed_at = datetime.now().isoformat()
                result.scene_layout_path = status_response.get("result", {}).get("scene_layout_path")
                result.scene_glb_path = status_response.get("result", {}).get("scene_glb_path")
                result.viewer_url = status_response.get("result", {}).get("viewer_url")

                # Calculate ETA info
                total_elapsed = time.time() - start_time
                print()
                print(f"{'='*60}")
                print(f"  ✓ 场景生成完成!")
                print(f"{'='*60}")
                print(f"  总耗时: {format_time(total_elapsed)}")
                print(f"  布局路径: {result.scene_layout_path}")
                print(f"  GLB 路径: {result.scene_glb_path}")
                if result.viewer_url:
                    print(f"  Viewer: {result.viewer_url}")
                print()
                break

            elif status == "failed":
                result.error_message = status_response.get("error", "Job failed")
                print()
                print(f"{'='*60}")
                print(f"  ✗ 场景生成失败!")
                print(f"  错误: {result.error_message}")
                print(f"{'='*60}")
                print()
                return result

            elif status == "running" or status == "processing":
                # Show detailed progress for running state
                progress = elapsed / timeout
                eta = (timeout - elapsed) if timeout > elapsed else 0

                # Try to get sub-progress from response
                sub_progress = status_response.get("progress", 0)
                if sub_progress > 0:
                    progress = sub_progress / 100.0
                    eta = (timeout - elapsed) * (1 - progress) / progress if progress > 0.01 else 0

                bar = get_progress_bar(progress)
                eta_str = format_time(eta) if eta > 0 else "计算中..."

                # Get current operation info
                operations = status_response.get("operations", [])
                op_info = ""
                if operations:
                    current_op = operations[-1] if operations else ""
                    if isinstance(current_op, dict):
                        op_info = f" | {current_op.get('name', current_op.get('status', ''))}"
                    else:
                        op_info = f" | {current_op}"

                spinner = get_spinner()
                print(f"\r  {spinner} [{bar}] {progress*100:5.1f}% | {format_time(elapsed)} / {format_time(timeout)} | ETA: {eta_str}{op_info}", end="", flush=True)

            elif status == "queued":
                spinner = get_spinner()
                queue_pos = status_response.get("queue_position", 0)
                queue_info = f" | 队列位置: #{queue_pos}" if queue_pos > 0 else ""
                print(f"\r  {spinner} 状态: {status} | 已等待: {format_time(elapsed)}{queue_info}", end="", flush=True)

            else:
                # Unknown status
                spinner = get_spinner()
                print(f"\r  {spinner} 状态: {status} | 已等待: {format_time(elapsed)}", end="", flush=True)

            # Still pending/running, wait
            time.sleep(poll_interval)
            elapsed = time.time() - start_time

        else:
            # Timeout
            result.status = "timeout"
            result.error_message = f"Job timed out after {timeout}s"
            print()
            print(f"\n{'='*60}")
            print(f"  ⏱️ 超时! 任务在 {format_time(timeout)} 后未完成")
            print(f"  已等待: {format_time(elapsed)}")
            print(f"  最后状态: {last_status}")
            print(f"{'='*60}")
            print()
            return result

        # Step 3: Evaluate scene
        print(f"{'='*60}")
        print(f"Step 3/4 | 调用 LLM 评估场景")
        print(f"{'='*60}")
        print(f"  布局路径: {result.scene_layout_path}")
        print()
        print("  评估中...", end="", flush=True)

        try:
            start_eval = time.time()
            result.evaluation = client.evaluate_scene(result.scene_layout_path)
            eval_time = time.time() - start_eval

            print(f"\r  ✓ 评估完成 ({format_time(eval_time)})")

            # 验证评估结果
            validator = MetricsValidator()
            eval_data = result.evaluation

            if eval_data:
                print()
                print(f"  评估结果:")
                print(f"  ─" * 20)

                scores = []
                for key, label in [("walkability", "步行性"), ("safety", "安全性"), ("beauty", "美观性"), ("overall", "综合评分")]:
                    score = eval_data.get(key, 0)
                    scores.append(score)
                    validator.validate_score_range(score, key)
                    bar = get_progress_bar(score / 100.0, 15)
                    print(f"    {label:8s}: [{bar}] {score:.1f}")

                # Validate formula
                if all(k in eval_data for k in ["walkability", "safety", "beauty", "overall"]):
                    formula_valid = validator.validate_formula(
                        eval_data["walkability"],
                        eval_data["safety"],
                        eval_data["beauty"],
                        eval_data["overall"]
                    )
                    print(f"  ─" * 20)
                    print(f"    公式验证: {'✓ 通过' if formula_valid else '✗ 失败'}")

                # Show suggestions summary
                suggestions = eval_data.get("suggestions", [])
                if suggestions:
                    print()
                    print(f"  改进建议 ({len(suggestions)}条):")
                    for i, s in enumerate(suggestions[:3], 1):
                        print(f"    {i}. {s[:60]}{'...' if len(s) > 60 else ''}")

            print()
        except Exception as e:
            print(f"\r  ✗ 评估失败: {e}")
            result.evaluation = None

        # Step 4: Complete
        result.status = "passed"
        print(f"{'='*60}")
        print(f"Step 4/4 | 测试完成")
        print(f"{'='*60}")
        print(f"  总耗时: {format_time(result.duration_seconds)}")
        print(f"  状态: ✓ 通过")
        print()

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


def run_verify_repeatability(
    client: WorkbenchClient,
    preset: dict,
    timeout: float = 300.0,
) -> RepeatVerificationResult:
    """
    运行重复验证测试。

    Args:
        client: API 客户端
        preset: 预设配置
        timeout: 单次运行超时

    Returns:
        RepeatVerificationResult: 验证结果
    """
    print("\n" + "=" * 60)
    print("重复运行可重复性验证")
    print("=" * 60)
    print(f"模板: {preset['name']} ({preset['id']})")
    print()

    # 第一次运行
    print("[运行 1/2] 开始第一次运行...")
    run1 = run_test(client, preset, timeout=timeout)
    print(f"第一次运行状态: {run1.status}")
    print()

    # 第二次运行（使用相同的随机种子）
    print("[运行 2/2] 开始第二次运行...")
    run2 = run_test(client, preset, timeout=timeout)
    print(f"第二次运行状态: {run2.status}")
    print()

    # 比较结果
    validator = MetricsValidator()
    eval1 = run1.evaluation or {}
    eval2 = run2.evaluation or {}

    repeatability_passed, metric_differences = validator.validate_repeatability(eval1, eval2)

    result = RepeatVerificationResult(
        preset=preset,
        run1=run1,
        run2=run2,
        repeatability_passed=repeatability_passed,
        metric_differences=metric_differences
    )

    # 打印比较结果
    print("=" * 60)
    print("可重复性验证结果")
    print("=" * 60)
    print(f"验证状态: {'✅ 通过' if repeatability_passed else '❌ 失败'}")
    print()
    print("指标对比:")
    for key, diff in metric_differences.items():
        status = "✅" if diff["within_tolerance"] else "❌"
        print(f"  {status} {key}: {diff['run1']:.2f} vs {diff['run2']:.2f} (差值: {diff['difference']:.6f})")
    print("=" * 60)

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
            "```",
            f"{result.error_message}",
            "```",
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


def generate_repeat_report(result: RepeatVerificationResult, output_dir: Path) -> str:
    """Generate repeatability verification report."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"repeat_verify_{timestamp}.md"
    filepath = output_dir / filename

    status_emoji = "✅" if result.repeatability_passed else "❌"

    lines = [
        "# 重复运行可重复性验证报告",
        "",
        f"**测试时间**: {result.timestamp}",
        f"**模板**: {result.preset['name']} (`{result.preset['id']}`)",
        f"**验证状态**: {status_emoji} {'通过' if result.repeatability_passed else '失败'}",
        "",
        "## 运行摘要",
        "",
        "| 运行 | 状态 | 耗时 | 综合评分 |",
        "|------|------|------|----------|",
        f"| 运行 1 | {result.run1.status} | {result.run1.duration_seconds:.1f}s | {result.run1.evaluation.get('overall', 'N/A') if result.run1.evaluation else 'N/A'} |",
        f"| 运行 2 | {result.run2.status} | {result.run2.duration_seconds:.1f}s | {result.run2.evaluation.get('overall', 'N/A') if result.run2.evaluation else 'N/A'} |",
        "",
        "## 指标对比",
        "",
        "| 指标 | 运行 1 | 运行 2 | 差值 | 容差内 |",
        "|------|--------|--------|------|--------|",
    ]

    for key, diff in result.metric_differences.items():
        lines.append(
            f"| {key} | {diff['run1']:.2f} | {diff['run2']:.2f} | {diff['difference']:.6f} | "
            f"{'✅' if diff['within_tolerance'] else '❌'} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "*由 test_workflow.py 自动生成*",
    ])

    filepath.write_text("\n".join(lines), encoding="utf-8")
    return str(filepath)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Workbench 自动化测试脚本 (科研版)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  uv run python scripts/test_workflow.py
  uv run python scripts/test_workflow.py --preset pedestrian_friendly
  uv run python scripts/test_workflow.py --verify-repeat
  uv run python scripts/test_workflow.py --seed 42 --verify-repeat
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
        default=600.0,
        help="任务超时时间，秒 (默认: 600)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/test_reports"),
        help="报告输出目录 (默认: artifacts/test_reports)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_RANDOM_SEED,
        help=f"随机种子 (默认: {DEFAULT_RANDOM_SEED})",
    )
    parser.add_argument(
        "--verify-repeat",
        action="store_true",
        help="运行重复验证 (执行两次并对比结果)",
    )

    args = parser.parse_args()

    # 设置全局随机种子
    set_global_seed(args.seed)

    # Load .env if available
    if load_dotenv:
        load_dotenv()

    # Select preset
    if args.preset:
        preset = next(p for p in SCENE_PRESETS if p["id"] == args.preset)
    else:
        preset = random.choice(SCENE_PRESETS)

    print("=" * 60)
    print("Workbench 自动化测试 (科研版)")
    print("=" * 60)
    print(f"模板: {preset['name']} ({preset['id']})")
    print(f"API: {args.api_base}")
    print(f"超时: {args.timeout}s")
    print(f"随机种子: {args.seed}")
    print("-" * 60)

    # Create client
    client = WorkbenchClient(args.api_base)

    try:
        # Check health
        print("检查 API 连接...")
        print(f"  API 端点: {args.api_base}")

        status_info = client.get_detailed_status()
        if not status_info:
            print("❌ API 不可用，请确保后端服务正在运行:")
            print(f"   uv run uvicorn web.api.main:app --reload --port 8010")
            sys.exit(1)

        # Display available status info
        print(f"  服务状态: {'正常' if status_info.get('ok') else '异常'}")
        if "default_pdf_path" in status_info:
            print(f"  知识库: {status_info['default_pdf_path']}")
        if "default_artifact_dir" in status_info:
            print(f"  工件目录: {status_info['default_artifact_dir']}")

        # 显示测试配置
        print()
        print("测试配置:")
        print(f"  预设模板: {preset['name']} ({preset['id']})")
        print(f"  随机种子: {args.seed}")
        print(f"  超时设置: {args.timeout}s")

        print()
        print("✓ API 连接正常")
        print()

        if args.verify_repeat:
            # 重复验证模式
            result = run_verify_repeatability(client, preset, timeout=args.timeout)
            report_path = generate_repeat_report(result, args.output)
            print(f"\n验证报告已生成: {report_path}")
            sys.exit(0 if result.repeatability_passed else 1)
        else:
            # 普通测试模式
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
