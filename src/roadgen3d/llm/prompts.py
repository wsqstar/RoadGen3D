"""Prompt builders for the RoadGen3D design assistant."""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Sequence

from ..services.design_types import ChatMessage, DesignIntent, RagEvidence


def build_design_intent_messages(
    history: Sequence[ChatMessage],
    user_input: str,
    current_patch: Mapping[str, Any] | None = None,
) -> list[Dict[str, str]]:
    conversation = [
        {
            "role": item.role,
            "content": item.content,
        }
        for item in history
        if str(item.content).strip()
    ]
    system_prompt = (
        "你是 RoadGen3D 的街道设计助手。"
        "请把用户关于街道风格与安全目标的自然语言，整理成一个 JSON 对象。"
        "你不能输出 Markdown，只能输出 JSON。"
        "字段必须包含："
        "`user_goals`(string[])、`style_preferences`(string[])、"
        "`safety_priorities`(string[])、`follow_up_questions`(string[])、"
        "`rag_queries`(string[])。"
        "`follow_up_questions` 只包含继续生成设计草案前必须确认的阻塞性问题。"
        "如果当前对话和 current_patch 已经足够，就返回空数组。"
        "不要重复询问历史里已经回答过的问题，最多返回 3 个问题。"
        "RAG 查询必须是适合从 complete streets 设计文档中检索规范建议的英文短句。"
        "即使用户使用中文，`rag_queries` 也必须输出英文。"
        "如果用户强调步行安全、全龄友好、慢行优先等，要明确写进 safety_priorities。"
    )
    messages: list[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    messages.extend(conversation)
    messages.append({
        "role": "user",
        "content": json.dumps(
            {
                "latest_user_input": str(user_input),
                "current_patch": dict(current_patch or {}),
                "instruction": "如果还缺少阻塞性信息，请只在 follow_up_questions 中提出。否则返回空数组并继续给出 RAG queries。",
            },
            ensure_ascii=False,
        ),
    })
    return messages


def build_rag_query_translation_messages(
    queries: Sequence[str],
) -> list[Dict[str, str]]:
    system_prompt = (
        "你是 RoadGen3D 的 RAG 检索查询翻译器。"
        "请把街道设计相关的检索短句翻译并重写成适合英文设计指南检索的英文短句。"
        "你只能输出 JSON。"
        "字段必须包含：`english_queries`(string[])。"
        "保留设计意图，不要扩写成完整段落，不要输出中文。"
    )
    payload = {
        "queries": [str(item).strip() for item in queries if str(item).strip()],
        "instruction": "输出适合英文 complete streets 设计文档检索的英文查询。",
    }
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def build_parameter_followup_query_messages(
    intent: DesignIntent,
    missing_fields: Sequence[str],
    evidence: Sequence[RagEvidence],
    current_patch: Mapping[str, Any] | None,
) -> list[Dict[str, str]]:
    serialized_evidence = [
        {
            "chunk_id": item.chunk_id,
            "section_title": item.section_title,
            "page_start": item.page_start,
            "page_end": item.page_end,
            "parameter_hints": item.parameter_hints,
        }
        for item in evidence
    ]
    system_prompt = (
        "你是 RoadGen3D 的街道参数检索规划器。"
        "请针对仍然缺失的街道设计参数，生成适合英文 complete streets 设计指南检索的英文短查询。"
        "你只能输出 JSON。"
        "字段必须包含：`field_queries`(object<string,string[]>)。"
        "只为值得从设计指南补证据的字段生成查询。"
        "如果某个字段更适合根据用户目标直接推断，而不是查文档，就不要为它生成查询。"
        "不要输出中文，不要输出完整段落，不要编造资产 ID。"
    )
    user_payload = {
        "intent": intent.to_dict(),
        "missing_fields": [str(item).strip() for item in missing_fields if str(item).strip()],
        "current_patch": dict(current_patch or {}),
        "evidence": serialized_evidence,
        "instruction": "为缺失参数生成英文 follow-up RAG queries；没有必要查文档的字段可以省略。",
    }
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def build_design_draft_messages(
    history: Sequence[ChatMessage],
    intent: DesignIntent,
    evidence: Sequence[RagEvidence],
    current_patch: Mapping[str, Any] | None,
    missing_fields: Sequence[str] | None = None,
) -> list[Dict[str, str]]:
    serialized_evidence = [
        {
            "chunk_id": item.chunk_id,
            "section_title": item.section_title,
            "page_start": item.page_start,
            "page_end": item.page_end,
            "text": item.text,
            "parameter_hints": item.parameter_hints,
        }
        for item in evidence
    ]
    system_prompt = (
        "你是 RoadGen3D 的街道设计参数建议器。"
        "请基于用户对话、设计意图与 RAG 证据，输出一个 JSON 对象。"
        "你只能输出 JSON。"
        "字段必须包含："
        "`normalized_scene_query`(string)、"
        "`compose_config_patch`(object)、"
        "`citations_by_field`(object<string,string[]>)、"
        "`design_summary`(string)、"
        "`risk_notes`(string[])。"
        "compose_config_patch 只能使用这些字段："
        "query, design_rule_profile, target_street_type, objective_profile, city_context, "
        "style_preset, beauty_mode, "
        "length_m, road_width_m, sidewalk_width_m, lane_count, density, "
        "ped_demand_level, bike_demand_level, transit_demand_level, vehicle_demand_level。"
        "compose_config_patch 必须尽量为这些允许字段都给出非空值，不要留空。"
        "如果某个字段能从 RAG 证据中得到支持，就在 citations_by_field 中给出 chunk_id。"
        "如果某个字段缺少直接证据，也要根据用户目标与已有证据给出合理推断值，不要输出 None/null。"
        "引用必须使用证据中的 chunk_id。"
        "不要编造具体资产 ID。"
    )
    messages: list[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    for item in history:
        if str(item.content).strip():
            messages.append({"role": item.role, "content": item.content})
    user_payload = {
        "intent": intent.to_dict(),
        "evidence": serialized_evidence,
        "current_patch": dict(current_patch or {}),
        "missing_fields": [str(item).strip() for item in (missing_fields or []) if str(item).strip()],
        "instruction": "基于证据给出适合生成街道的参数草案，并明确把关键字段映射到引用。",
    }
    messages.append({"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)})
    return messages


def build_scene_evaluation_messages(
    *,
    summary: dict,
    placement_summary: list[dict],
    image_data_url: str | None = None,
) -> list[Dict[str, str]]:
    system_prompt = (
        "你是 RoadGen3D 的场景评价专家。"
        "请基于场景截图和评估指标，输出一个 JSON 对象。"
        "你只能输出 JSON。"
        "字段必须包含："
        "`evaluation`(string，中文自然语言评价)、"
        "`score`(number，0-10 综合评分)、"
        "`suggestions`(string[]，具体改进建议列表)、"
        "`config_patch`(object，可选的配置修改建议)。"
        "请按以下维度评价：\n"
        "1. 视觉美观度与协调性\n"
        "2. 空间布局合理性\n"
        "3. 多样性与丰富度\n"
        "4. 规范合规性\n"
        "5. 行人友好性\n"
        "config_patch 只能使用这些字段："
        "query, design_rule_profile, target_street_type, objective_profile, city_context, "
        "style_preset, beauty_mode, "
        "length_m, road_width_m, sidewalk_width_m, lane_count, density, "
        "ped_demand_level, bike_demand_level, transit_demand_level, vehicle_demand_level。"
    )
    user_content: list[dict] = []
    user_content.append({
        "type": "text",
        "text": json.dumps({
            "summary": summary,
            "placements_preview": placement_summary,
            "instruction": "请评价这个街道场景的质量并给出改进建议。",
        }, ensure_ascii=False),
    })
    if image_data_url:
        user_content.append({
            "type": "image_url",
            "image_url": {"url": image_data_url},
        })
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},  # type: ignore[list-item]
    ]


def build_unified_evaluation_messages(
    *,
    summary: dict,
    placement_summary: list[dict],
    image_data_url: str | None = None,
) -> list[Dict[str, str]]:
    """Build evaluation prompt with unified 3-dimension output.

    Returns:
        walkability: 0-100 score for pedestrian-friendliness
        safety: 0-100 score for safety features
        beauty: 0-100 score for aesthetics
        overall: 0-100 weighted total score
        evaluation: text summary in Chinese
        suggestions: improvement suggestions
        indicators: detailed walkability indicators
    """
    system_prompt = (
        "你是 RoadGen3D 的场景评价专家。"
        "请基于街道场景布局信息，输出一个 JSON 对象。"
        "你只能输出 JSON，不能输出其他内容。"
        "\n"
        "评估维度（必须全部输出）：\n"
        "1. walkability (步行性，0-100): 人行道宽度、净空连续性、家具密度、照明均匀、绿化遮荫\n"
        "2. safety (安全性，0-100): 交通隔离、过街设施、缓冲带、安全感知\n"
        "3. beauty (美观性，0-100): 植物配置协调性、街道家具风格统一、空间丰富度\n"
        "4. overall (综合分，0-100): 基于步行性45%、安全性35%、美观性20%的加权总分\n"
        "\n"
        "必须返回的字段：\n"
        "walkability (int, 0-100)\n"
        "safety (int, 0-100)\n"
        "beauty (int, 0-100)\n"
        "overall (int, 0-100): 必须是 walkability*0.45 + safety*0.35 + beauty*0.20\n"
        "evaluation (string): 中文评价，简要说明该方案的优缺点\n"
        "suggestions (string[]): 1-3条具体改进建议\n"
        "\n"
        "可选字段：\n"
        "indicators (object): 详细指标，仅当有足够信息时提供\n"
    )
    user_payload = {
        "summary": summary,
        "placements_preview": placement_summary[:30],  # Limit to 30 placements
        "instruction": "请评价这个街道场景，给出步行性、安全性、美观性三个维度的评分(0-100)，并计算综合分。",
    }
    user_content: list[dict] = [{"type": "text", "text": json.dumps(user_payload, ensure_ascii=False)}]
    if image_data_url:
        user_content.append({
            "type": "image_url",
            "image_url": {"url": image_data_url},
        })
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},  # type: ignore[list-item]
    ]


def build_layout_edit_messages(
    image_data_url: str,
    layout_summary: str,
    user_query: str,
    iteration: int,
    score_history: list[float] | None = None,
) -> list[Dict[str, str]]:
    """Build messages for LLM to propose layout edits.

    The LLM sees a top-down preview image and the current layout summary,
    then proposes a JSON patch to add/remove placements or resize bands.
    """
    system_prompt = (
        "你是 RoadGen3D 的场景编辑专家。"
        "你看到一个街道场景的俯视预览图和当前布局摘要。"
        "你需要根据用户的设计需求和当前场景状态，提出具体的布局修改建议。"
        "你只能输出 JSON。"
        "字段必须包含："
        "`add_placements`(array)、`remove_placements`(array)、"
        "`resize_bands`(array)、`batch_add_along_street`(array)、"
        "`adjust_sub_lanes`(array)、`reasoning`(string)。"
        "\n"
        "add_placements 中每个元素必须包含："
        "`category`(string, 如 tree/bench/lamp/trash/bollard/hydrant/mailbox/bus_stop/"
        "flower_bed/planter/shrub)、"
        "`position_xyz`(array of 3 numbers)、"
        "`yaw_deg`(number, 默认0)、`scale`(number, 默认1.0)。"
        "\n"
        "remove_placements 是要删除的 instance_id 字符串数组。"
        "\n"
        "resize_bands 中每个元素必须包含："
        "`band_name`(string, 如 left_furnishing/right_furnishing/carriageway)、"
        "`width_m`(number, 新宽度)。"
        "\n"
        "batch_add_along_street 批量沿街道等距添加元素，每个元素必须包含："
        "`category`(string, 如 tree/flower_bed/planter/shrub)、"
        "`side`(string, left 或 right)、"
        "`band_name`(string, 参考 layout_summary 中 Bands 部分的 band 名称)、"
        "`spacing_m`(number, 间距，默认8.0)、"
        "`count`(integer, 数量，0表示按间距自动计算)、"
        "`yaw_deg`(number, 默认0)、`scale`(number, 默认1.0)。"
        "\n"
        "adjust_sub_lanes 调整车道数量和宽度，每个元素必须包含："
        "`side`(string, 如 left)、"
        "`width_m`(number, 目标车行道总宽度)、"
        "`lane_count`(integer, 可选，目标车道数)。"
        "\n"
        "注意事项：\n"
        "1. 新增元素的位置必须在合理的空间范围内（参考已有元素的坐标范围）。\n"
        "2. 道路中央（carriageway）不应放置家具。\n"
        "3. left_furnishing 的 z 坐标为正值，right_furnishing 的 z 坐标为负值。\n"
        "4. 每次修改不要太多，保持渐进式改进。\n"
        "5. 如果场景已经很好，可以返回空数组不做修改。\n"
        "6. 使用 batch_add_along_street 时，务必参照 layout_summary 中 Bands 部分的 band_name "
        "来确保元素放置在正确的 band 上。\n"
        "7. flower_bed/planter/shrub 适合放在 furnishing band 上增加绿化。"
    )

    history_text = ""
    if score_history:
        history_text = f"\n历史评分: {', '.join(f'{s:.1f}' for s in score_history)}"

    user_content: list[Dict[str, Any]] = [
        {
            "type": "text",
            "text": json.dumps({
                "user_query": user_query,
                "iteration": iteration,
                "layout_summary": layout_summary,
                "score_history_note": history_text,
                "instruction": (
                    "基于预览图和布局摘要，提出具体的布局修改。"
                    "重点关注：多样性、美观度、行人友好性。"
                ),
            }, ensure_ascii=False),
        },
    ]
    if image_data_url:
        user_content.append({
            "type": "image_url",
            "image_url": {"url": image_data_url},
        })

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},  # type: ignore[list-item]
    ]


def build_layout_evaluation_messages(
    image_data_url: str,
    layout_summary: str,
    user_query: str,
    previous_reasoning: str | None = None,
) -> list[Dict[str, str]]:
    """Build messages for LLM to evaluate an edited layout.

    Returns a JSON object with evaluation, score (0-10), and feedback.
    """
    system_prompt = (
        "你是 RoadGen3D 的场景评价专家。"
        "你看到一个经过编辑的街道场景俯视预览图。"
        "请评价编辑后的场景质量。"
        "你只能输出 JSON。"
        "字段必须包含："
        "`evaluation`(string，中文自然语言评价)、"
        "`score`(number，0-10 综合评分)、"
        "`feedback`(string，具体反馈和改进建议)。"
        "请按以下维度评价：\n"
        "1. 视觉美观度与协调性\n"
        "2. 空间布局合理性\n"
        "3. 多样性与丰富度\n"
        "4. 行人友好性\n"
        "5. 编辑是否改善了场景"
    )

    reasoning_text = ""
    if previous_reasoning:
        reasoning_text = f"\n编辑原因: {previous_reasoning}"

    user_content: list[Dict[str, Any]] = [
        {
            "type": "text",
            "text": json.dumps({
                "user_query": user_query,
                "layout_summary": layout_summary,
                "edit_reasoning": reasoning_text,
                "instruction": "请评价这个编辑后的街道场景质量。",
            }, ensure_ascii=False),
        },
    ]
    if image_data_url:
        user_content.append({
            "type": "image_url",
            "image_url": {"url": image_data_url},
        })

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},  # type: ignore[list-item]
    ]


def build_graph_aware_design_messages(
    *,
    graph_summary: dict,
    base_map_data_url: str | None = None,
    user_prompt: str = "",
    current_patch: Mapping[str, Any] | None = None,
) -> list[Dict[str, str]]:
    """Build messages that ask the LLM to propose design parameters based on a
    parsed road-network graph and optional reference base-map image.

    This is used by the auto-pipeline to bootstrap the initial *config_patch*
    without going through the full RAG-enhanced draft flow.
    """
    from ..services.design_types import ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS

    allowed_fields = ", ".join(ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS)

    system_prompt = (
        "你是 RoadGen3D 的街道设计专家。"
        "你需要根据道路网络结构和参考底图设计街道家具布局参数。"
        "你只能输出 JSON。"
        "字段必须包含："
        "`compose_config_patch`(object) 和 `design_summary`(string)。"
        f"compose_config_patch 只能使用这些字段：{allowed_fields}。"
        "请尽量为所有允许字段都给出非空值，不要输出 None/null。"
        "不要编造具体资产 ID。"
    )

    user_payload: Dict[str, Any] = {
        "graph_summary": graph_summary,
        "user_prompt": str(user_prompt).strip() or "Generate a suitable street design",
        "current_patch": dict(current_patch or {}),
        "instruction": (
            "基于道路网络结构和参考底图（如有），"
            "输出适合该道路场景的街道家具布局参数。"
        ),
    }

    user_content: list[Dict[str, Any]] = [
        {"type": "text", "text": json.dumps(user_payload, ensure_ascii=False)},
    ]
    if base_map_data_url:
        user_content.append({
            "type": "image_url",
            "image_url": {"url": base_map_data_url},
        })

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},  # type: ignore[list-item]
    ]
