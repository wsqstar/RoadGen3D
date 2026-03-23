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
