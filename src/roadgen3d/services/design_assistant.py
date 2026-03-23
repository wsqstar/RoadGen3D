"""Workflow service for the LLM + RAG street-design workbench."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from ..knowledge import ClipTextEmbedderAdapter, PdfKnowledgeBaseBuilder, PdfKnowledgeBaseRetriever
from ..knowledge.pdf_rag import KnowledgeSearchHit
from ..llm import (
    GLMClient,
    build_parameter_followup_query_messages,
    build_design_draft_messages,
    build_design_intent_messages,
    build_rag_query_translation_messages,
)
from .design_runtime import generate_scene_from_draft
from .scene_jobs import SceneJobService
from .design_types import (
    ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS,
    ChatMessage,
    DEFAULT_COMPOSE_CONFIG_PATCH_VALUES,
    DesignDraft,
    DesignDraftBundle,
    DesignIntent,
    RagEvidence,
    SceneJobCreateResponse,
    SceneJobStatusResponse,
    SceneRecord,
    sanitize_citations_by_field,
    sanitize_compose_config_patch,
)


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_COMPLETE_STREETS_PDF = (ROOT / "knowledge" / "book" / "Complete streets design guide.pdf").resolve()
DEFAULT_COMPLETE_STREETS_ARTIFACT_DIR = (ROOT / "knowledge" / "complete_streets").resolve()
DEFAULT_CLIP_MODEL_DIR = (ROOT / "models" / "clip-vit-base-patch32").resolve()
_CJK_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
_RAG_SOURCE = "rag"
_LLM_INFERRED_SOURCE = "llm_inferred"
_SYSTEM_DEFAULT_SOURCE = "system_default"


class DesignAssistantService:
    """Orchestrates LLM intent parsing, RAG search, and scene generation."""

    def __init__(
        self,
        *,
        llm_client: GLMClient | Any | None = None,
        knowledge_builder: PdfKnowledgeBaseBuilder | None = None,
        knowledge_retriever: PdfKnowledgeBaseRetriever | Any | None = None,
        default_pdf_path: Path | None = None,
        default_artifact_dir: Path | None = None,
        scene_job_service: SceneJobService | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.knowledge_builder = knowledge_builder or PdfKnowledgeBaseBuilder()
        self._knowledge_retriever = knowledge_retriever
        self.default_pdf_path = Path(default_pdf_path or DEFAULT_COMPLETE_STREETS_PDF).expanduser().resolve()
        self.default_artifact_dir = Path(default_artifact_dir or DEFAULT_COMPLETE_STREETS_ARTIFACT_DIR).expanduser().resolve()
        self.scene_job_service = scene_job_service or SceneJobService(generator=generate_scene_from_draft)

    def rebuild_knowledge(
        self,
        *,
        pdf_path: str | Path | None = None,
        artifact_dir: str | Path | None = None,
    ) -> Dict[str, Any]:
        pdf_file = Path(pdf_path or self.default_pdf_path).expanduser().resolve()
        out_dir = Path(artifact_dir or self.default_artifact_dir).expanduser().resolve()
        try:
            artifacts = self.knowledge_builder.build(pdf_file, out_dir)
        except Exception:
            fallback_builder = PdfKnowledgeBaseBuilder(
                embedder=ClipTextEmbedderAdapter(
                    model_dir=DEFAULT_CLIP_MODEL_DIR,
                    local_files_only=True,
                    device="cpu",
                )
            )
            artifacts = fallback_builder.build(pdf_file, out_dir)
        self._knowledge_retriever = None
        return artifacts.to_dict()

    def draft_design(
        self,
        *,
        messages: Sequence[Mapping[str, Any]] | Sequence[ChatMessage],
        user_input: str,
        current_patch: Mapping[str, Any] | None = None,
        topk: int = 6,
    ) -> DesignDraftBundle:
        chat_messages = normalize_chat_messages(messages)
        llm = self._get_llm_client()
        intent_payload = llm.chat_json(build_design_intent_messages(chat_messages, user_input))
        intent = parse_design_intent(intent_payload, fallback_query=user_input)
        retrieval_queries = self._prepare_retrieval_queries(
            llm=llm,
            intent=intent,
            user_input=user_input,
        )
        evidence = tuple(self._retrieve_evidence(retrieval_queries, topk=topk))
        draft = self._generate_design_draft(
            llm=llm,
            chat_messages=chat_messages,
            intent=intent,
            evidence=evidence,
            current_patch=current_patch,
            fallback_query=user_input,
        )
        missing_fields = identify_missing_compose_fields(draft.compose_config_patch)
        if missing_fields:
            followup_queries = self._plan_missing_parameter_queries(
                llm=llm,
                intent=intent,
                evidence=evidence,
                current_patch=current_patch or {},
                missing_fields=missing_fields,
            )
            if followup_queries:
                extra_evidence = tuple(self._retrieve_evidence(followup_queries, topk=max(topk, 8)))
                if extra_evidence:
                    evidence = tuple(merge_evidence_collections(evidence, extra_evidence))
                    draft = self._generate_design_draft(
                        llm=llm,
                        chat_messages=chat_messages,
                        intent=intent,
                        evidence=evidence,
                        current_patch=current_patch,
                        fallback_query=user_input,
                        missing_fields=missing_fields,
                    )
                    missing_fields = identify_missing_compose_fields(draft.compose_config_patch)
        else:
            followup_queries = ()
        draft, defaulted_fields = finalize_design_draft(draft)
        warnings: List[str] = []
        if not evidence:
            warnings.append("No RAG evidence was found for the current design brief.")
        if not draft.compose_config_patch:
            warnings.append("The LLM returned an empty compose-config patch; defaults will be used.")
        if followup_queries:
            warnings.append(
                "Ran a follow-up evidence search for missing parameters: "
                + ", ".join(followup_queries[:6])
                + ("..." if len(followup_queries) > 6 else "")
            )
        if defaulted_fields:
            warnings.append(
                "Some parameters still lacked explicit values after retrieval and were filled with stable defaults: "
                + ", ".join(defaulted_fields)
            )
        return DesignDraftBundle(
            intent=intent,
            evidence=evidence,
            draft=draft,
            warnings=tuple(warnings),
        )

    def generate_scene(
        self,
        draft: DesignDraft,
        *,
        patch_overrides: Mapping[str, Any] | None = None,
        generation_options: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        return self.scene_job_service.run_job_sync(
            draft=draft,
            patch_overrides=patch_overrides,
            generation_options=generation_options,
        ).to_dict()

    def create_scene_job(
        self,
        draft: DesignDraft,
        *,
        patch_overrides: Mapping[str, Any] | None = None,
        generation_options: Mapping[str, Any] | None = None,
    ) -> SceneJobCreateResponse:
        return self.scene_job_service.submit_job(
            draft=draft,
            patch_overrides=patch_overrides,
            generation_options=generation_options,
        )

    def list_scene_jobs(self, *, limit: int = 20) -> List[SceneJobStatusResponse]:
        return self.scene_job_service.list_jobs(limit=limit)

    def get_scene_job(self, job_id: str) -> SceneJobStatusResponse | None:
        return self.scene_job_service.get_job(job_id)

    def list_recent_scenes(self, *, limit: int = 20) -> List[SceneRecord]:
        return self.scene_job_service.list_recent_scenes(limit=limit)

    def _get_llm_client(self) -> GLMClient | Any:
        if self.llm_client is None:
            self.llm_client = GLMClient()
        return self.llm_client

    def _get_retriever(self) -> PdfKnowledgeBaseRetriever | Any:
        if self._knowledge_retriever is None:
            self._knowledge_retriever = PdfKnowledgeBaseRetriever(artifact_dir=self.default_artifact_dir)
        return self._knowledge_retriever

    def _retrieve_evidence(self, queries: Iterable[str], *, topk: int) -> List[RagEvidence]:
        retriever = self._get_retriever()
        items: List[RagEvidence] = []
        seen = set()
        for query in queries:
            query_text = str(query or "").strip()
            if not query_text:
                continue
            for hit in retriever.search(query_text, topk=max(1, int(topk))):
                evidence = convert_search_hit_to_evidence(hit, rag_query=query_text)
                if evidence.chunk_id in seen:
                    continue
                seen.add(evidence.chunk_id)
                items.append(evidence)
        items.sort(key=lambda item: float(item.score), reverse=True)
        return items[: max(1, int(topk))]

    def _generate_design_draft(
        self,
        *,
        llm: GLMClient | Any,
        chat_messages: Sequence[ChatMessage],
        intent: DesignIntent,
        evidence: Sequence[RagEvidence],
        current_patch: Mapping[str, Any] | None,
        fallback_query: str,
        missing_fields: Sequence[str] | None = None,
    ) -> DesignDraft:
        draft_payload = llm.chat_json(
            build_design_draft_messages(
                chat_messages,
                intent,
                evidence,
                current_patch or {},
                missing_fields=missing_fields,
            )
        )
        return parse_design_draft(
            draft_payload,
            evidence=evidence,
            fallback_query=fallback_query,
            current_patch=current_patch,
        )

    def _plan_missing_parameter_queries(
        self,
        *,
        llm: GLMClient | Any,
        intent: DesignIntent,
        evidence: Sequence[RagEvidence],
        current_patch: Mapping[str, Any],
        missing_fields: Sequence[str],
    ) -> Tuple[str, ...]:
        if not missing_fields:
            return ()
        try:
            payload = llm.chat_json(
                build_parameter_followup_query_messages(
                    intent=intent,
                    missing_fields=missing_fields,
                    evidence=evidence,
                    current_patch=current_patch,
                )
            )
        except Exception:
            return ()
        field_queries = payload.get("field_queries")
        queries: List[str] = []
        if isinstance(field_queries, Mapping):
            for field_name in missing_fields:
                queries.extend(_coerce_text_list(field_queries.get(field_name)))
        else:
            queries.extend(_coerce_text_list(payload.get("english_queries")))
        return self._normalize_retrieval_queries(llm=llm, queries=queries)

    def _prepare_retrieval_queries(
        self,
        *,
        llm: GLMClient | Any,
        intent: DesignIntent,
        user_input: str,
    ) -> Tuple[str, ...]:
        base_queries = tuple(
            dict.fromkeys(
                query_text
                for query_text in (intent.rag_queries or (str(user_input).strip(),))
                if str(query_text or "").strip()
            )
        )
        return self._normalize_retrieval_queries(llm=llm, queries=base_queries)

    def _normalize_retrieval_queries(
        self,
        *,
        llm: GLMClient | Any,
        queries: Sequence[str],
    ) -> Tuple[str, ...]:
        base_queries = tuple(dict.fromkeys(query for query in queries if str(query or "").strip()))
        if not base_queries:
            return ()
        if not any(_contains_cjk_text(query) for query in base_queries):
            return base_queries
        try:
            translation_payload = llm.chat_json(build_rag_query_translation_messages(base_queries))
        except Exception:
            return base_queries
        translated_queries = tuple(dict.fromkeys(_coerce_text_list(translation_payload.get("english_queries"))))
        if not translated_queries:
            return base_queries
        return tuple(dict.fromkeys(translated_queries + base_queries))


def normalize_chat_messages(messages: Sequence[Mapping[str, Any]] | Sequence[ChatMessage]) -> Tuple[ChatMessage, ...]:
    items: List[ChatMessage] = []
    for raw in messages:
        if isinstance(raw, ChatMessage):
            role = raw.role
            content = raw.content
        else:
            role = str(raw.get("role", "user"))
            content = str(raw.get("content", ""))
        if str(content).strip():
            items.append(ChatMessage(role=role, content=content))
    return tuple(items)


def parse_design_intent(payload: Mapping[str, Any], *, fallback_query: str) -> DesignIntent:
    queries = tuple(dict.fromkeys(_coerce_text_list(payload.get("rag_queries"))))
    if not queries and str(fallback_query).strip():
        queries = (str(fallback_query).strip(),)
    return DesignIntent(
        user_goals=tuple(dict.fromkeys(_coerce_text_list(payload.get("user_goals")))),
        style_preferences=tuple(dict.fromkeys(_coerce_text_list(payload.get("style_preferences")))),
        safety_priorities=tuple(dict.fromkeys(_coerce_text_list(payload.get("safety_priorities")))),
        follow_up_questions=tuple(dict.fromkeys(_coerce_text_list(payload.get("follow_up_questions")))),
        rag_queries=queries,
    )


def parse_design_draft(
    payload: Mapping[str, Any],
    *,
    evidence: Sequence[RagEvidence],
    fallback_query: str,
    current_patch: Mapping[str, Any] | None = None,
) -> DesignDraft:
    patch = sanitize_compose_config_patch(payload.get("compose_config_patch"))
    patch.update(sanitize_compose_config_patch(current_patch))
    normalized_scene_query = str(
        payload.get("normalized_scene_query")
        or patch.get("query")
        or fallback_query
    ).strip()
    if normalized_scene_query and "query" not in patch:
        patch["query"] = normalized_scene_query
    evidence_ids = {item.chunk_id for item in evidence}
    citations = sanitize_citations_by_field(
        payload.get("citations_by_field"),
        allowed_fields=ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS,
    )
    filtered_citations = {
        key: tuple(citation for citation in value if citation in evidence_ids)
        for key, value in citations.items()
    }
    filtered_citations = {key: value for key, value in filtered_citations.items() if value}
    summary = str(payload.get("design_summary", "") or "").strip()
    risk_notes = tuple(dict.fromkeys(_coerce_text_list(payload.get("risk_notes"))))
    return DesignDraft(
        normalized_scene_query=normalized_scene_query,
        compose_config_patch=patch,
        citations_by_field=filtered_citations,
        design_summary=summary,
        risk_notes=risk_notes,
    )


def convert_search_hit_to_evidence(hit: KnowledgeSearchHit, *, rag_query: str) -> RagEvidence:
    hints = infer_parameter_hints(hit.chunk.text)
    return RagEvidence(
        chunk_id=hit.chunk.chunk_id,
        doc_id=hit.chunk.doc_id,
        section_title=hit.chunk.section_title,
        page_start=int(hit.chunk.page_start),
        page_end=int(hit.chunk.page_end),
        text=hit.chunk.text,
        source_path=hit.chunk.source_path,
        score=float(hit.score),
        relevance_reason=f"Matched RAG query: {rag_query}",
        parameter_hints=hints,
    )


def infer_parameter_hints(text: str) -> Dict[str, str]:
    lowered = str(text or "").lower()
    hints: Dict[str, str] = {}
    if "sidewalk" in lowered or "pedestrian" in lowered:
        hints["sidewalk_width_m"] = "Review pedestrian clear-path guidance in this excerpt."
    if "lane" in lowered or "carriageway" in lowered:
        hints["lane_count"] = "Check motor-vehicle lane allocation guidance here."
    if "transit" in lowered or "bus" in lowered:
        hints["transit_demand_level"] = "Transit-supporting layout guidance appears in this excerpt."
    if "safe" in lowered or "all ages" in lowered:
        hints["design_rule_profile"] = "Safety-oriented profile guidance appears in this excerpt."
    return hints


def identify_missing_compose_fields(patch: Mapping[str, Any]) -> Tuple[str, ...]:
    normalized_patch = sanitize_compose_config_patch(patch)
    return tuple(field for field in ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS if field not in normalized_patch)


def merge_evidence_collections(*groups: Sequence[RagEvidence]) -> List[RagEvidence]:
    merged: Dict[str, RagEvidence] = {}
    for group in groups:
        for item in group:
            existing = merged.get(item.chunk_id)
            if existing is None or float(item.score) > float(existing.score):
                merged[item.chunk_id] = item
    return sorted(merged.values(), key=lambda item: float(item.score), reverse=True)


def finalize_design_draft(draft: DesignDraft) -> Tuple[DesignDraft, Tuple[str, ...]]:
    patch = sanitize_compose_config_patch(draft.compose_config_patch)
    citations = {
        key: tuple(value)
        for key, value in draft.citations_by_field.items()
        if key in ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS and value
    }
    if draft.normalized_scene_query and "query" not in patch:
        patch["query"] = draft.normalized_scene_query

    sources: Dict[str, str] = {}
    for field_name in ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS:
        if field_name in patch:
            sources[field_name] = _RAG_SOURCE if citations.get(field_name) else _LLM_INFERRED_SOURCE

    defaulted_fields: List[str] = []
    for field_name, default_value in DEFAULT_COMPOSE_CONFIG_PATCH_VALUES.items():
        if field_name in patch:
            continue
        patch[field_name] = default_value
        sources[field_name] = _SYSTEM_DEFAULT_SOURCE
        defaulted_fields.append(field_name)

    if "query" in patch:
        sources.setdefault("query", _RAG_SOURCE if citations.get("query") else _LLM_INFERRED_SOURCE)

    return (
        DesignDraft(
            normalized_scene_query=draft.normalized_scene_query,
            compose_config_patch=patch,
            citations_by_field=citations,
            design_summary=draft.design_summary,
            risk_notes=draft.risk_notes,
            parameter_sources_by_field=sources,
        ),
        tuple(defaulted_fields),
    )


def _coerce_text_list(value: object) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    else:
        items = list(value)
    return [str(item).strip() for item in items if str(item).strip()]


def _contains_cjk_text(value: str) -> bool:
    return bool(_CJK_RE.search(str(value or "")))
