#!/usr/bin/env python3
"""
批量测试脚本 - 并行生成多个场景

支持两种模式:
1) 现有预设模板批量测试
2) OSM 直接到 3D 的随机批量测试（200m-500m 方形 AOI）
"""

from __future__ import annotations

import argparse
import concurrent.futures
import math
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
for candidate in (ROOT, ROOT.parent):
    src_root = candidate / "src"
    if src_root.is_dir():
        sys.path.insert(0, str(src_root))
        break
else:
    raise RuntimeError("Unable to locate project src directory for roadgen3d imports.")

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
DEFAULT_OSM_RUN_COUNT = 100
DEFAULT_OSM_SIDE_M_MIN = 200.0
DEFAULT_OSM_SIDE_M_MAX = 500.0
DEFAULT_RANDOM_SEED = 42
DEFAULT_OSM_BASE_POINTS = [
    (22.5431, 114.0579),  # Shenzhen
    (23.1291, 113.2644),  # Guangzhou
    (22.3020, 114.1693),  # Hong Kong
    (31.2304, 121.4737),  # Shanghai
    (39.9042, 116.4074),  # Beijing
    (30.5728, 104.0668),  # Chengdu
]
OSM_DESIGN_RULE_PROFILES = (
    "balanced_complete_street_v1",
    "noise_aware_v1",
    "pedestrian_priority_v1",
    "transit_priority_v1",
)
OSM_SKELETON_DESIGN_PROFILES = (
    "child_friendly_school",
    "walkable_commercial",
    "vehicle_access_commercial",
    "transit_priority",
    "green_walkable",
    "quiet_residential",
)
OSM_STREET_FURNITURE_PROFILES = (
    "none",
    "balanced_complete",
    "pedestrian_friendly",
    "commercial_vitality",
    "transit_priority",
    "park_landscape",
    "quiet_residential",
)
ERROR_CATEGORIES = ("zero_furniture", "failed", "timeout", "submit_error", "succeeded")


# ── Data Classes ────────────────────────────────────────────────────────────────

@dataclass
class BatchResult:
    """批量测试结果"""
    preset_id: str
    preset_name: str
    job_id: str = ""
    status: str = "pending"  # pending, running, succeeded, failed, timeout
    error_category: str = "pending"
    scene_context: dict[str, Any] = field(default_factory=dict)
    patch_overrides: dict[str, Any] = field(default_factory=dict)
    viewer_url: str | None = None
    scene_layout_path: str | None = None
    scene_glb_path: str | None = None
    duration_seconds: float = 0.0
    error_message: str | None = None
    evaluation: dict | None = None
    start_time: float = 0.0
    end_time: float = 0.0


def _coerce_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _build_random_osm_center() -> tuple[float, float]:
    """在固定城市群周边生成一个随机中心点。"""
    lat0, lon0 = random.choice(DEFAULT_OSM_BASE_POINTS)
    # ~1.0-1.2km 的抖动，避免生成完全重复点位
    lat = lat0 + random.uniform(-0.01, 0.01)
    lon = lon0 + random.uniform(-0.01, 0.01)
    return lat, lon


def _meters_side_to_bbox(center_lat: float, center_lon: float, side_m: float) -> list[float]:
    """按边长（米）生成 [min_lon, min_lat, max_lon, max_lat]。"""
    half_m = max(float(side_m), 1.0) / 2.0
    delta_lat = half_m / 111_320.0
    cos_lat = math.cos(math.radians(center_lat))
    meters_per_deg_lon = 111_320.0 * max(abs(cos_lat), 1e-6)
    delta_lon = half_m / meters_per_deg_lon
    return [
        center_lon - delta_lon,
        center_lat - delta_lat,
        center_lon + delta_lon,
        center_lat + delta_lat,
    ]


def _build_osm_scene_context(side_m: float) -> dict[str, Any]:
    """构造一份 OSM mode 的 scene_context。"""
    center_lat, center_lon = _build_random_osm_center()
    return {
        "layout_mode": "osm",
        "aoi_bbox": _meters_side_to_bbox(center_lat, center_lon, side_m),
        "city_name_en": None,
        "reference_plan_id": None,
        "graph_template_id": None,
    }


def _build_osm_patch(run_index: int) -> dict[str, Any]:
    """为一次 OSM 直接生成构造随机参数。"""
    patch = {
        "seed": random.randint(1, 2_000_000),
        "design_rule_profile": random.choice(OSM_DESIGN_RULE_PROFILES),
        "skeleton_design_profile": random.choice(OSM_SKELETON_DESIGN_PROFILES),
        "skeleton_design_profile_source": "manual",
        "skeleton_design_profile_confidence": round(random.uniform(0.55, 1.0), 3),
        "street_furniture_profile": random.choice(OSM_STREET_FURNITURE_PROFILES),
        "street_furniture_profile_source": "manual",
        "street_furniture_profile_confidence": round(random.uniform(0.35, 0.95), 3),
        "length_m": round(random.uniform(40.0, 180.0), 1),
        "road_width_m": round(random.uniform(6.0, 12.0), 2),
        "sidewalk_width_m": round(random.uniform(1.6, 3.6), 2),
        "segment_length_m": round(random.uniform(8.0, 28.0), 1),
        "density": round(random.uniform(0.25, 1.25), 3),
        "building_density": round(random.uniform(0.25, 1.0), 3),
        # OSM batch runs are provenance tests: never fill missing source
        # geometry with generated lots or inferred frontage buildings.
        "surrounding_building_mode": "footprint_based",
        "infill_policy": "off",
        "bus_stop_enabled": random.choice([True, False]),
    }
    if run_index % 17 == 0:
        patch["seed"] = 1_000_000 + run_index
    return patch


def _classify_error(result_status: str, status_response: dict[str, Any]) -> str:
    if result_status == "timeout":
        return "timeout"
    error_text = str(status_response.get("error", "") or "").lower()
    if "osm source coverage insufficient" in error_text:
        return "insufficient_osm_coverage"
    if "zero furniture" in error_text or "composition produced zero furniture" in error_text:
        return "zero_furniture"
    summary = status_response.get("result", {}).get("summary", {})
    if isinstance(summary, dict):
        for key in ("total_furniture_count", "street_furniture_count", "furniture_count"):
            raw_value = summary.get(key)
            try:
                if int(raw_value or 0) <= 0:
                    return "zero_furniture"
            except (TypeError, ValueError):
                continue
    if status_response.get("status") != "succeeded":
        return "failed"
    return "failed"


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
            transport=transport,
            trust_env=False,  # Avoid inheriting system proxy settings (e.g., localhost via HTTP proxy)
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

    def create_scene_job(
        self,
        preset: dict,
        patch_overrides: dict | None = None,
        graph_template_id: str | None = None,
        draft: dict | None = None,
        scene_context: dict[str, Any] | None = None,
    ) -> dict:
        """Create a scene generation job."""
        patch_overrides = patch_overrides or {}

        # Use LLM-generated draft if available
        if draft:
            scene_draft = draft.get("draft", draft)
            compose_config_patch = scene_draft.get("compose_config_patch", preset["config_patch"])
        else:
            compose_config_patch = preset["config_patch"]

        scene_context_payload = scene_context
        if scene_context_payload is None:
            scene_context_payload = {
                "layout_mode": "graph_template",
                "aoi_bbox": None,
                "city_name_en": None,
                "reference_plan_id": None,
                "graph_template_id": graph_template_id or self.graph_template_id,
            }

        payload = {
            "draft": {
                "normalized_scene_query": preset["prompt"],
                "compose_config_patch": compose_config_patch,
                "citations_by_field": draft.get("draft", {}).get("citations_by_field", {}) if draft else {},
                "design_summary": draft.get("draft", {}).get("design_summary", preset["prompt"]) if draft else preset["prompt"],
                "risk_notes": [],
                "parameter_sources_by_field": draft.get("draft", {}).get("parameter_sources_by_field", {}) if draft else {},
            },
            "scene_context": scene_context_payload,
            "patch_overrides": patch_overrides,
            "generation_options": {"preset_id": preset["id"]},
        }
        response = self.client.post(f"{self.base_url}/api/scene/jobs", json=payload)
        response.raise_for_status()
        return response.json()

    def get_job_status(self, job_id: str) -> dict:
        """Get job status using curl."""
        try:
            response = self.client.get(f"{self.base_url}/api/scene/jobs/{job_id}")
            response.raise_for_status()
            return response.json()
        except json.JSONDecodeError as e:
            raw = response.text[:512] if "response" in locals() else ""
            content_type = response.headers.get("content-type", "unknown") if "response" in locals() else "unknown"
            status_code = response.status_code if "response" in locals() else "n/a"
            raise Exception(f"Invalid JSON response (status={status_code}, content-type={content_type}): {e}; body={raw!r}")
        except httpx.TimeoutException:
            raise Exception("Job status request timeout")
        except Exception as e:
            raise Exception(f"Request failed: {e}")

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
    scene_context: dict[str, Any] | None = None,
    patch_overrides: dict[str, Any] | None = None,
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
    result.scene_context = dict(scene_context or {})
    result.patch_overrides = dict(patch_overrides or {})

    try:
        # Generate random scene seed for variation
        if result.patch_overrides.get("seed") is None:
            result.patch_overrides["seed"] = int(time.time() * 1000000 + id(preset)) % 1000000 + random.randint(1, 9999)
        is_osm_mode = (scene_context or {}).get("layout_mode") == "osm"
        if is_osm_mode:
            template_id = None
            template_display = "n/a (OSM)"
        else:
            template_id = graph_template_id or client.graph_template_id
            template_display = template_id

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
        print_status(
            f"  [Job] 创建任务: {preset['name']} ({preset['id']}) "
            f"template={template_display} seed={result.patch_overrides['seed']}"
        )
        job_response = client.create_scene_job(
            preset,
            patch_overrides=result.patch_overrides,
            graph_template_id=template_id,
            draft=draft,
            scene_context=scene_context,
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
                result.error_category = "succeeded"
                result.error_message = None
                break

            elif status == "failed":
                result.status = "failed"
                result.error_message = status_response.get("error", "Job failed")
                result.error_category = _classify_error("failed", status_response)
                break

            # Still running
            if progress_callback:
                progress_callback(result.preset_id, status, elapsed / timeout)

            time.sleep(poll_interval)
            elapsed = time.time() - result.start_time
        else:
            # Timeout
            result.status = "timeout"
            result.error_category = "timeout"
            result.error_message = f"Job timed out after {timeout}s"

    except Exception as e:
        result.status = "failed"
        result.error_message = str(e)
        # If submit succeeded but later polling failed, keep as generic failed.
        if not result.job_id:
            result.error_category = "submit_error"
        else:
            result.error_category = _classify_error("failed", {"error": result.error_message})

    result.end_time = time.time()
    result.duration_seconds = result.end_time - result.start_time
    if result.status not in {"succeeded", "failed", "timeout"}:
        result.error_category = "failed"
        result.error_message = result.error_message or "Unknown state"

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
    scene_contexts: list[dict[str, Any]] | None = None,
    patch_overrides: list[dict[str, Any]] | None = None,
) -> list[BatchResult]:
    """Run multiple preset tests in parallel."""

    # Initialize results
    results = [BatchResult(preset_id=p["id"], preset_name=p["name"]) for p in presets]
    if scene_contexts is None:
        scene_contexts = [None] * len(presets)
    if patch_overrides is None:
        patch_overrides = [None] * len(presets)
    if len(scene_contexts) != len(presets) or len(patch_overrides) != len(presets):
        raise ValueError("scene_contexts/patch_overrides must align with selected presets")

    # Determine template assignments
    is_osm_mode = bool(scene_contexts and scene_contexts[0].get("layout_mode") == "osm")
    if random_template:
        # Each preset gets a random template
        template_assignments = [random.choice(GRAPH_TEMPLATES)["id"] for _ in presets]
        print(f"  模板分配: {dict(zip([p['id'] for p in presets], template_assignments))}")
    elif is_osm_mode:
        template_assignments = [None] * len(presets)
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
        for preset, result, template_id, scene_context, patch_override in zip(
            presets,
            results,
            template_assignments,
            scene_contexts,
            patch_overrides,
        ):
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
                scene_context=scene_context,
                patch_overrides=patch_override,
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
    error_count = failed + timeout
    error_rate = (error_count / total) if total else 0.0
    error_counter: dict[str, int] = {}
    for result in results:
        category = result.error_category or "unknown"
        error_counter[category] = error_counter.get(category, 0) + 1

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
        f"| 失败率 | {error_rate:.2%} |",
        "",
        "## 错误分类统计",
        "",
        "| 错误分类 | 数量 | 占比 |",
        "|------|-----|-----|",
    ]

    for category, count in sorted(error_counter.items()):
        lines.append(f"| {category} | {count} | {(count / total if total else 0.0):.2%} |")

    lines.extend([
        "",
        "## Viewer 链接",
        "",
    ])

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
        lines.append(f"- **错误分类**: {result.error_category}")
        lines.append(f"- **耗时**: {result.duration_seconds:.1f}s")
        if result.scene_layout_path:
            lines.append(f"- **布局**: `{result.scene_layout_path}`")
        if result.error_message:
            lines.append(f"- **错误信息**: {result.error_message}")
        if result.evaluation:
            overall = result.evaluation.get("overall", "N/A")
            lines.append(f"- **综合评分**: {overall}")
        lines.append("")

    lines.extend([
        "## 详细结果",
        "",
        "| 模板 | 状态 | 耗时 | 错误分类 | 综合评分 |",
        "|------|------|------|----------|----------|",
    ])

    for result in results:
        status_emoji = {"succeeded": "✅", "failed": "❌", "timeout": "⏱️"}.get(result.status, "?")
        score = result.evaluation.get("overall", "N/A") if result.evaluation else "N/A"
        lines.append(
            f"| {result.preset_name} | {status_emoji} {result.status} | "
            f"{result.duration_seconds:.1f}s | {result.error_category} | {score} |"
        )

    lines.extend([
        "",
        "## 失败任务清单",
        "",
    ])

    for result in results:
        if result.status == "succeeded":
            continue
        lines.append(f"### ❌ {result.preset_name} (`{result.preset_id}`)")
        lines.append(f"- **错误类别**: {result.error_category}")
        lines.append(f"- **错误信息**: {result.error_message or 'N/A'}")
        if result.job_id:
            lines.append(f"- **Job ID**: {result.job_id}")
        if result.scene_context:
            lines.append(f"- **Scene Context**: `{json.dumps(result.scene_context, ensure_ascii=False)}`")
        if result.patch_overrides:
            lines.append(f"- **参数覆盖**: `{json.dumps(result.patch_overrides, ensure_ascii=False)}`")
        lines.append("")

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
            "error_category": r.error_category,
            "viewer_url": r.viewer_url,
            "scene_context": r.scene_context,
            "patch_overrides": r.patch_overrides,
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

  # OSM 直达 3D: 随机 100 次，边长 200~500m
  uv run python scripts/test_batch.py --mode osm --osm-runs 100 --osm-side-m-min 200 --osm-side-m-max 500
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
    parser.add_argument(
        "--mode",
        choices=["preset", "osm"],
        default="preset",
        help="测试模式: preset=预设模板, osm=OSM 直达 3D",
    )
    parser.add_argument(
        "--osm-runs",
        type=int,
        default=DEFAULT_OSM_RUN_COUNT,
        help=f"OSM 模式下运行次数（默认: {DEFAULT_OSM_RUN_COUNT}）",
    )
    parser.add_argument(
        "--osm-side-m-min",
        type=float,
        default=DEFAULT_OSM_SIDE_M_MIN,
        help=f"OSM AOI 边长下限（米，默认: {DEFAULT_OSM_SIDE_M_MIN}）",
    )
    parser.add_argument(
        "--osm-side-m-max",
        type=float,
        default=DEFAULT_OSM_SIDE_M_MAX,
        help=f"OSM AOI 边长上限（米，默认: {DEFAULT_OSM_SIDE_M_MAX}）",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_RANDOM_SEED,
        help=f"随机种子（默认: {DEFAULT_RANDOM_SEED}）",
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

    random.seed(args.seed)
    selected_presets = []
    scene_contexts = None
    patch_overrides = None
    random_template = args.random_template

    if args.mode == "osm":
        if args.use_llm:
            print("⚠️ OSM 模式下忽略 --use-llm")
        if args.presets or args.all or args.random_template:
            print("⚠️ OSM 模式忽略 preset 相关参数（presets / all / random-template）")
        if args.graph_template:
            print("⚠️ OSM 模式不使用 graph template")

        side_min = _coerce_float(args.osm_side_m_min, DEFAULT_OSM_SIDE_M_MIN)
        side_max = _coerce_float(args.osm_side_m_max, DEFAULT_OSM_SIDE_M_MAX)
        run_count = int(args.osm_runs or DEFAULT_OSM_RUN_COUNT)
        if run_count <= 0:
            print("错误: --osm-runs 必须为正整数")
            sys.exit(1)
        if side_min <= 0 or side_max <= 0 or side_max < side_min:
            print("错误: --osm-side-m-min / --osm-side-m-max 配置无效")
            sys.exit(1)

        for i in range(run_count):
            side_m = random.uniform(side_min, side_max)
            selected_presets.append({
                "id": f"osm-{i+1:04d}",
                "name": f"OSM-{i+1:04d} ({side_m:.1f}m)",
                "prompt": "OSM 直达 3D 直连测试",
                "config_patch": {},
            })
            if scene_contexts is None:
                scene_contexts = []
                patch_overrides = []
            scene_contexts.append(_build_osm_scene_context(side_m))
            patch_overrides.append(_build_osm_patch(i + 1))
        random_template = False
    else:
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
    print(f"模式: {'OSM' if args.mode == 'osm' else 'Preset'}")
    print(f"任务数量: {len(selected_presets)}")
    for p in selected_presets:
        print(f"  - {p['name']} ({p['id']})")
    print(f"API: {args.api_base}")
    if args.mode == "preset":
        print(f"Graph Template: {graph_template_id if not args.random_template else '(随机分配)'}")
    print(f"并行数: {args.workers}")
    print(f"超时: {args.timeout}s")
    print(f"LLM 生成: {'启用' if (args.use_llm and args.mode == 'preset') else '禁用'}")
    if args.mode == "osm":
        print(f"OSM AOI 边长范围: {side_min:.1f}-{side_max:.1f}m")
    print(f"随机种子: {args.seed}")
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
            random_template=random_template,
            use_llm=bool(args.use_llm and args.mode == "preset"),
            scene_contexts=scene_contexts,
            patch_overrides=patch_overrides,
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
        print(f"错误率: {((len(results) - succeeded) / len(results) if results else 0):.2%}")
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
