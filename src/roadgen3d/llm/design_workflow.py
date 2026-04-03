"""Workflow service for the LLM + RAG street-design workbench."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from ..knowledge import (
    ClipTextEmbedderAdapter,
    GraphRagKnowledgeRetriever,
    PdfKnowledgeBaseBuilder,
    PdfKnowledgeBaseRetriever,
)
from ..knowledge.pdf_rag import KnowledgeSearchHit
from . import (
    GLMClient,
    build_parameter_followup_query_messages,
    build_design_draft_messages,
    build_design_intent_messages,
    build_rag_query_translation_messages,
)
from ..services.design_runtime import generate_scene_from_draft
from ..services.scene_jobs import SceneJobService
from ..services.design_types import (
    ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS,
    ChatMessage,
    DEFAULT_COMPOSE_CONFIG_PATCH_VALUES,
    DesignDraft,
    DesignDraftBundle,
    DesignIntent,
    RagEvidence,
    SceneContext,
    SceneJobCreateResponse,
    SceneJobStatusResponse,
    SceneRecord,
    sanitize_citations_by_field,
    sanitize_compose_config_patch,
    sanitize_scene_context,
)
from ..services.scene_context_service import list_china_cities_payload


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_COMPLETE_STREETS_PDF = (ROOT / "knowledge" / "book" / "Complete streets design guide.pdf").resolve()
DEFAULT_COMPLETE_STREETS_ARTIFACT_DIR = (ROOT / "knowledge" / "complete_streets").resolve()
DEFAULT_GRAPHRAG_PROJECT_DIR = (ROOT / "knowledge" / "graphRAG").resolve()
DEFAULT_CLIP_MODEL_DIR = (ROOT / "models" / "clip-vit-base-patch32").resolve()
DEFAULT_DESIGN_DRAFT_CACHE_DIR = (ROOT / "artifacts" / "workbench_cache" / "design_draft_cache").resolve()
_CJK_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
_RAG_SOURCE = "rag"
_LLM_INFERRED_SOURCE = "llm_inferred"
_SYSTEM_DEFAULT_SOURCE = "system_default"
_DEFAULT_KNOWLEDGE_SOURCE = "graph_rag"
_ALLOWED_KNOWLEDGE_SOURCES = frozenset({"hybrid", "pdf_rag", "graph_rag"})
_DRAFT_CACHE_VERSION = "roadgen3d_design_draft_cache_v1"
_DRAFT_CACHE_HIT_WARNING = "Loaded cached design analysis for the exact same prompt; skipped new LLM and GraphRAG work."


class DesignAssistantService:
    """Orchestrates LLM intent parsing, RAG search, and scene generation."""

    def __init__(
        self,
        *,
        llm_client: GLMClient | Any | None = None,
        knowledge_builder: PdfKnowledgeBaseBuilder | None = None,
        knowledge_retriever: PdfKnowledgeBaseRetriever | Any | None = None,
        graph_knowledge_retriever: GraphRagKnowledgeRetriever | Any | None = None,
        default_pdf_path: Path | None = None,
        default_artifact_dir: Path | None = None,
        default_graphrag_project_dir: Path | None = None,
        draft_cache_dir: Path | None = None,
        scene_job_service: SceneJobService | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.knowledge_builder = knowledge_builder or PdfKnowledgeBaseBuilder()
        self._knowledge_retriever = knowledge_retriever
        self._graph_knowledge_retriever = graph_knowledge_retriever
        self.default_pdf_path = Path(default_pdf_path or DEFAULT_COMPLETE_STREETS_PDF).expanduser().resolve()
        self.default_artifact_dir = Path(default_artifact_dir or DEFAULT_COMPLETE_STREETS_ARTIFACT_DIR).expanduser().resolve()
        self.default_graphrag_project_dir = Path(
            default_graphrag_project_dir or DEFAULT_GRAPHRAG_PROJECT_DIR
        ).expanduser().resolve()
        self.draft_cache_dir = Path(draft_cache_dir or DEFAULT_DESIGN_DRAFT_CACHE_DIR).expanduser().resolve()
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
        knowledge_source: str = _DEFAULT_KNOWLEDGE_SOURCE,
    ) -> DesignDraftBundle:
        resolved_knowledge_source = normalize_knowledge_source(knowledge_source)
        cache_key = self._build_draft_cache_key(
            user_input=user_input,
            knowledge_source=resolved_knowledge_source,
        )
        cached_bundle = self._load_cached_draft_bundle(
            cache_key=cache_key,
            fallback_query=user_input,
            current_patch=current_patch,
        )
        if cached_bundle is not None:
            return cached_bundle
        chat_messages = normalize_chat_messages(messages)
        llm = self._get_llm_client()
        intent_payload = llm.chat_json(build_design_intent_messages(chat_messages, user_input, current_patch))
        intent = parse_design_intent(intent_payload, fallback_query=user_input)
        if requires_user_clarification(intent):
            warnings: List[str] = []
            if intent.follow_up_questions:
                warnings.append("Additional clarification is required before drafting a street design.")
            result = DesignDraftBundle(
                stage="clarification_required",
                intent=intent,
                evidence=(),
                draft=None,
                warnings=tuple(warnings),
            )
            self._save_draft_bundle_cache(
                cache_key=cache_key,
                user_input=user_input,
                knowledge_source=resolved_knowledge_source,
                bundle=result,
            )
            return result
        retrieval_queries = self._prepare_retrieval_queries(
            llm=llm,
            intent=intent,
            user_input=user_input,
        )
        evidence = tuple(
            self._retrieve_evidence(
                retrieval_queries,
                topk=topk,
                knowledge_source=resolved_knowledge_source,
            )
        )
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
                extra_evidence = tuple(
                    self._retrieve_evidence(
                        followup_queries,
                        topk=max(topk, 8),
                        knowledge_source=resolved_knowledge_source,
                    )
                )
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
            warnings.append(
                "No RAG evidence was found for the current design brief."
                f" Knowledge source: {resolved_knowledge_source}."
            )
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
        result = DesignDraftBundle(
            stage="draft_ready",
            intent=intent,
            evidence=evidence,
            draft=draft,
            warnings=tuple(warnings),
        )
        self._save_draft_bundle_cache(
            cache_key=cache_key,
            user_input=user_input,
            knowledge_source=resolved_knowledge_source,
            bundle=result,
        )
        return result

    def list_knowledge_sources(self) -> List[Dict[str, Any]]:
        pdf_status = self._build_pdf_knowledge_status()
        graph_status = self._build_graph_knowledge_status()
        hybrid_available = bool(pdf_status["available"] or graph_status["available"])
        hybrid_status = {
            "key": "hybrid",
            "label": "Hybrid",
            "available": hybrid_available,
            "description": "Merge the existing PDF RAG chunks with GraphRAG txt/community artifacts.",
            "artifact_count": int(pdf_status.get("artifact_count", 0)) + int(graph_status.get("artifact_count", 0)),
            "item_count": int(pdf_status.get("item_count", 0)) + int(graph_status.get("item_count", 0)),
        }
        return [hybrid_status, pdf_status, graph_status]

    def search_knowledge(
        self,
        *,
        query: str,
        topk: int = 6,
        knowledge_source: str = _DEFAULT_KNOWLEDGE_SOURCE,
    ) -> List[RagEvidence]:
        return self._retrieve_evidence(
            (query,),
            topk=topk,
            knowledge_source=normalize_knowledge_source(knowledge_source),
        )

    def generate_scene(
        self,
        draft: DesignDraft,
        *,
        patch_overrides: Mapping[str, Any] | None = None,
        generation_options: Mapping[str, Any] | None = None,
        scene_context: Mapping[str, Any] | SceneContext | None = None,
    ) -> Dict[str, Any]:
        return self.scene_job_service.run_job_sync(
            draft=draft,
            patch_overrides=patch_overrides,
            generation_options=generation_options,
            scene_context=sanitize_scene_context(scene_context),
        ).to_dict()

    def create_scene_job(
        self,
        draft: DesignDraft,
        *,
        patch_overrides: Mapping[str, Any] | None = None,
        generation_options: Mapping[str, Any] | None = None,
        scene_context: Mapping[str, Any] | SceneContext | None = None,
    ) -> SceneJobCreateResponse:
        return self.scene_job_service.submit_job(
            draft=draft,
            patch_overrides=patch_overrides,
            generation_options=generation_options,
            scene_context=sanitize_scene_context(scene_context),
        )

    def list_scene_jobs(self, *, limit: int = 20) -> List[SceneJobStatusResponse]:
        return self.scene_job_service.list_jobs(limit=limit)

    def get_scene_job(self, job_id: str) -> SceneJobStatusResponse | None:
        return self.scene_job_service.get_job(job_id)

    def list_recent_scenes(self, *, limit: int = 20) -> List[SceneRecord]:
        return self.scene_job_service.list_recent_scenes(limit=limit)

    def list_china_cities(self) -> List[Dict[str, Any]]:
        return list_china_cities_payload()

    def evaluate_scene(
        self,
        *,
        layout_path: str,
        image_path: str | None = None,
    ) -> Dict[str, Any]:
        import base64
        layout = Path(layout_path).expanduser().resolve()
        if not layout.exists():
            raise RuntimeError(f"Layout file not found: {layout}")
        payload = json.loads(layout.read_text(encoding="utf-8"))
        summary = payload.get("summary", {}) or {}
        placements = payload.get("placements", []) or []
        llm = self._get_llm_client()
        placement_summary = []
        for p in placements[:20]:
            placement_summary.append({
                "instance_id": p.get("instance_id", ""),
                "category": p.get("category", ""),
                "asset_id": p.get("asset_id", ""),
                "position_xyz": p.get("position_xyz"),
            })
        image_data_url = None
        if image_path:
            img = Path(image_path).expanduser().resolve()
            if img.exists():
                image_data_url = f"data:image/png;base64,{base64.b64encode(img.read_bytes()).decode('ascii')}"
        from .prompts import build_scene_evaluation_messages
        messages = build_scene_evaluation_messages(
            summary=summary,
            placement_summary=placement_summary,
            image_data_url=image_data_url,
        )
        eval_payload = llm.chat_json(messages)
        return {
            "evaluation": str(eval_payload.get("evaluation", "")),
            "score": float(eval_payload.get("score", 0) or 0),
            "suggestions": list(eval_payload.get("suggestions", []) or []),
            "config_patch": dict(eval_payload.get("config_patch", {}) or {}),
        }

    def _get_llm_client(self) -> GLMClient | Any:
        if self.llm_client is None:
            self.llm_client = GLMClient()
        return self.llm_client

    def _get_pdf_retriever(self) -> PdfKnowledgeBaseRetriever | Any:
        if self._knowledge_retriever is None:
            self._knowledge_retriever = PdfKnowledgeBaseRetriever(artifact_dir=self.default_artifact_dir)
        return self._knowledge_retriever

    def _get_graph_retriever(self) -> GraphRagKnowledgeRetriever | Any:
        if self._graph_knowledge_retriever is None:
            self._graph_knowledge_retriever = GraphRagKnowledgeRetriever(
                project_dir=self.default_graphrag_project_dir,
            )
        return self._graph_knowledge_retriever

    def _build_draft_cache_key(
        self,
        *,
        user_input: str,
        knowledge_source: str,
    ) -> str:
        normalized_prompt = _normalize_cache_prompt(user_input)
        key_payload = {
            "knowledge_source": normalize_knowledge_source(knowledge_source),
            "user_input": normalized_prompt,
        }
        digest = hashlib.sha256(
            json.dumps(key_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return str(digest)

    def _draft_cache_path_for_key(self, cache_key: str) -> Path:
        return self.draft_cache_dir / f"{cache_key}.json"

    def _load_cached_draft_bundle(
        self,
        *,
        cache_key: str,
        fallback_query: str,
        current_patch: Mapping[str, Any] | None,
    ) -> DesignDraftBundle | None:
        cache_path = self._draft_cache_path_for_key(cache_key)
        if not cache_path.exists():
            return None
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if str(payload.get("version", "")) != _DRAFT_CACHE_VERSION:
            return None
        bundle_payload = payload.get("bundle")
        if not isinstance(bundle_payload, Mapping):
            return None
        intent_payload = bundle_payload.get("intent")
        intent = parse_design_intent(
            intent_payload if isinstance(intent_payload, Mapping) else {},
            fallback_query=fallback_query,
        )
        evidence = tuple(
            parse_rag_evidence(item)
            for item in (bundle_payload.get("evidence") or [])
            if isinstance(item, Mapping) and _clean_text(item.get("chunk_id"))
        )
        draft_payload = bundle_payload.get("draft")
        draft = None
        if isinstance(draft_payload, Mapping):
            draft = parse_design_draft(
                draft_payload,
                evidence=evidence,
                fallback_query=fallback_query,
                current_patch={},
            )
            draft, _ = finalize_design_draft(draft)
            draft = _merge_current_patch_into_cached_draft(draft, current_patch=current_patch)
        warnings = tuple(dict.fromkeys(_coerce_text_list(bundle_payload.get("warnings"))))
        if _DRAFT_CACHE_HIT_WARNING not in warnings:
            warnings = warnings + (_DRAFT_CACHE_HIT_WARNING,)
        return DesignDraftBundle(
            stage=str(bundle_payload.get("stage", "draft_ready") or "draft_ready"),
            intent=intent,
            evidence=evidence,
            draft=draft,
            warnings=warnings,
            cache_hit=True,
        )

    def _save_draft_bundle_cache(
        self,
        *,
        cache_key: str,
        user_input: str,
        knowledge_source: str,
        bundle: DesignDraftBundle,
    ) -> None:
        cache_path = self._draft_cache_path_for_key(cache_key)
        payload = {
            "version": _DRAFT_CACHE_VERSION,
            "cache_key": str(cache_key),
            "knowledge_source": normalize_knowledge_source(knowledge_source),
            "user_input": _normalize_cache_prompt(user_input),
            "bundle": bundle.to_dict(),
        }
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            return

    def _build_pdf_knowledge_status(self) -> Dict[str, Any]:
        metadata_path = self.default_artifact_dir / "metadata.json"
        chunks_path = self.default_artifact_dir / "chunks.jsonl"
        index_path = self.default_artifact_dir / "index.faiss"
        item_count = 0
        if metadata_path.exists():
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8"))
                item_count = int(payload.get("chunk_count", 0) or 0)
            except Exception:
                item_count = 0
        return {
            "key": "pdf_rag",
            "label": "PDF RAG",
            "available": chunks_path.exists() and index_path.exists(),
            "description": "FAISS-backed chunk retrieval built from the complete streets PDF guide.",
            "artifact_count": sum(1 for path in [metadata_path, chunks_path, index_path] if path.exists()),
            "item_count": item_count,
            "artifact_dir": str(self.default_artifact_dir),
            "source_path": str(self.default_pdf_path),
        }

    def _build_graph_knowledge_status(self) -> Dict[str, Any]:
        return self._get_graph_retriever().describe().to_dict()

    def _resolve_retrievers_for_source(self, knowledge_source: str) -> Tuple[Tuple[str, Any], ...]:
        resolved = normalize_knowledge_source(knowledge_source)
        if resolved == "pdf_rag":
            return (("pdf_rag", self._get_pdf_retriever()),)
        if resolved == "graph_rag":
            graph_status = self._build_graph_knowledge_status()
            if not graph_status.get("available"):
                raise RuntimeError(graph_status.get("error") or "GraphRAG artifacts are not available.")
            return (("graph_rag", self._get_graph_retriever()),)

        retrievers: List[Tuple[str, Any]] = []
        pdf_status = self._build_pdf_knowledge_status()
        if pdf_status.get("available"):
            retrievers.append(("pdf_rag", self._get_pdf_retriever()))
        graph_status = self._build_graph_knowledge_status()
        if graph_status.get("available"):
            retrievers.append(("graph_rag", self._get_graph_retriever()))
        if not retrievers:
            raise RuntimeError("No knowledge sources are available for the workbench.")
        return tuple(retrievers)

    def _retrieve_evidence(
        self,
        queries: Iterable[str],
        *,
        topk: int,
        knowledge_source: str = _DEFAULT_KNOWLEDGE_SOURCE,
    ) -> List[RagEvidence]:
        retrievers = self._resolve_retrievers_for_source(knowledge_source)
        items: List[RagEvidence] = []
        seen = set()
        for query in queries:
            query_text = str(query or "").strip()
            if not query_text:
                continue
            for source_name, retriever in retrievers:
                try:
                    hits = retriever.search(query_text, topk=max(1, int(topk)))
                except Exception:
                    if knowledge_source != "hybrid":
                        raise
                    continue
                for hit in hits:
                    evidence = convert_search_hit_to_evidence(
                        hit,
                        rag_query=query_text,
                        knowledge_source=source_name,
                    )
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


def requires_user_clarification(intent: DesignIntent) -> bool:
    return bool(intent.follow_up_questions)


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


def convert_search_hit_to_evidence(
    hit: KnowledgeSearchHit,
    *,
    rag_query: str,
    knowledge_source: str = "pdf_rag",
) -> RagEvidence:
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
        knowledge_source=normalize_knowledge_source(knowledge_source),
        parameter_hints=hints,
    )


def parse_rag_evidence(payload: Mapping[str, Any]) -> RagEvidence:
    parameter_hints = payload.get("parameter_hints")
    return RagEvidence(
        chunk_id=_clean_text(payload.get("chunk_id")) or "cached_chunk",
        doc_id=_clean_text(payload.get("doc_id")) or "cached_doc",
        section_title=_clean_text(payload.get("section_title")) or "Cached Evidence",
        page_start=int(payload.get("page_start", 0) or 0),
        page_end=int(payload.get("page_end", 0) or 0),
        text=str(payload.get("text", "") or ""),
        source_path=str(payload.get("source_path", "") or ""),
        score=float(payload.get("score", 0.0) or 0.0),
        relevance_reason=str(payload.get("relevance_reason", "") or ""),
        knowledge_source=normalize_knowledge_source(payload.get("knowledge_source")),
        parameter_hints={
            str(key): str(value)
            for key, value in dict(parameter_hints or {}).items()
            if _clean_text(key) and _clean_text(value)
        },
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


def _merge_current_patch_into_cached_draft(
    draft: DesignDraft,
    *,
    current_patch: Mapping[str, Any] | None,
) -> DesignDraft:
    overrides = sanitize_compose_config_patch(current_patch)
    if not overrides:
        return draft
    patch = dict(draft.compose_config_patch)
    citations = {
        key: tuple(value)
        for key, value in draft.citations_by_field.items()
    }
    for key, value in overrides.items():
        if str(patch.get(key, "")) != str(value):
            citations.pop(key, None)
        patch[key] = value
    merged_draft, _ = finalize_design_draft(
        DesignDraft(
            normalized_scene_query=str(patch.get("query") or draft.normalized_scene_query),
            compose_config_patch=patch,
            citations_by_field=citations,
            design_summary=draft.design_summary,
            risk_notes=draft.risk_notes,
        )
    )
    return merged_draft


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


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _normalize_cache_prompt(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def normalize_knowledge_source(value: object) -> str:
    normalized = str(value or _DEFAULT_KNOWLEDGE_SOURCE).strip().lower()
    if normalized not in _ALLOWED_KNOWLEDGE_SOURCES:
        return _DEFAULT_KNOWLEDGE_SOURCE
    return normalized
