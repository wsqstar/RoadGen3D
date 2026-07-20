"""Workflow service for the LLM + RAG street-design workbench."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence, Tuple

from ..knowledge import (
    ClipTextEmbedderAdapter,
    GraphRagKnowledgeRetriever,
    PdfKnowledgeBaseBuilder,
    PdfKnowledgeBaseRetriever,
    ScenarioParameterTripleStore,
)
from ..knowledge.pdf_rag import KnowledgeSearchHit
from ..evaluation_views import (
    DEFAULT_EVALUATION_RENDER_VIEW_LIMIT,
    rendered_views_for_evaluation_from_payload,
)
from . import (
    LLMClient,
    build_parameter_followup_query_messages,
    build_design_draft_messages,
    build_design_intent_messages,
    build_graph_aware_design_messages,
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
DEFAULT_SCENARIO_PARAMETER_TRIPLES_PATH = (ROOT / "knowledge" / "scenario_parameter_triples.jsonl").resolve()
_CJK_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
_RAG_SOURCE = "rag"
_LLM_INFERRED_SOURCE = "llm_inferred"
_SYSTEM_DEFAULT_SOURCE = "system_default"
_DEFAULT_KNOWLEDGE_SOURCE = "none"
_ALLOWED_KNOWLEDGE_SOURCES = frozenset({"none", "hybrid", "pdf_rag", "graph_rag", "scenario_parameters"})
_ALLOWED_RAG_MODES = frozenset({"disabled", "experimental"})
_DRAFT_CACHE_VERSION = "roadgen3d_design_draft_cache_v1"
_DRAFT_CACHE_HIT_WARNING = "Loaded cached design analysis for the exact same prompt; skipped new LLM and GraphRAG work."


class DesignAssistantService:
    """Orchestrates LLM intent parsing, RAG search, and scene generation."""

    def __init__(
        self,
        *,
        llm_client: LLMClient | Any | None = None,
        knowledge_builder: PdfKnowledgeBaseBuilder | None = None,
        knowledge_retriever: PdfKnowledgeBaseRetriever | Any | None = None,
        graph_knowledge_retriever: GraphRagKnowledgeRetriever | Any | None = None,
        default_pdf_path: Path | None = None,
        default_artifact_dir: Path | None = None,
        default_graphrag_project_dir: Path | None = None,
        scenario_parameter_triples_path: Path | None = None,
        scenario_parameter_store: ScenarioParameterTripleStore | Any | None = None,
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
        self.scenario_parameter_triples_path = Path(
            scenario_parameter_triples_path or DEFAULT_SCENARIO_PARAMETER_TRIPLES_PATH
        ).expanduser().resolve()
        self._scenario_parameter_store = scenario_parameter_store
        self.draft_cache_dir = Path(draft_cache_dir or DEFAULT_DESIGN_DRAFT_CACHE_DIR).expanduser().resolve()
        self.scene_job_service = scene_job_service
        
        # Initialize evaluation engine from road-metrics submodule
        import sys
        _submodule_path = Path(__file__).resolve().parents[1] / "eval_engine_ext"
        if str(_submodule_path) not in sys.path:
            sys.path.insert(0, str(_submodule_path))
        
        from road_metrics import EvalEngine, EvalConfig
        self._EvalEngine = EvalEngine
        self._EvalConfig = EvalConfig
        self.eval_engine = EvalEngine(EvalConfig.for_profile("local_segment_v1", enable_llm_eval=True))
        if self.scene_job_service is None:
            self.scene_job_service = SceneJobService(
                generator=generate_scene_from_draft,
                evaluator=self.evaluate_scene_unified,
            )
        elif isinstance(self.scene_job_service, SceneJobService) and self.scene_job_service.evaluator is None:
            self.scene_job_service.evaluator = self.evaluate_scene_unified

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
        force: bool = False,
    ) -> DesignDraftBundle:
        resolved_knowledge_source = normalize_knowledge_source(knowledge_source)
        cache_key = self._build_draft_cache_key(
            user_input=user_input,
            knowledge_source=resolved_knowledge_source,
        )
        # Skip cache if force=True to get fresh result with AI-filled defaults
        if not force:
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
        if requires_user_clarification(intent) and not force:
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
        if resolved_knowledge_source == "none":
            # ``none`` is a real zero-retrieval mode.  Do not translate search
            # queries and, importantly, do not append the scenario triple store
            # as an implicit second knowledge source.
            retrieval_queries = tuple(
                item for item in (intent.normalized_scene_query, user_input) if str(item or "").strip()
            )[:1]
            evidence: Tuple[RagEvidence, ...] = ()
        else:
            require_experimental_rag(resolved_knowledge_source)
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
            if resolved_knowledge_source in {"hybrid", "scenario_parameters"}:
                scenario_evidence = tuple(
                    self._retrieve_scenario_parameter_evidence(
                        queries=retrieval_queries,
                        topk=24,
                    )
                )
                if scenario_evidence:
                    evidence = tuple(merge_evidence_collections(evidence, scenario_evidence))
        draft = self._generate_design_draft(
            llm=llm,
            chat_messages=chat_messages,
            intent=intent,
            evidence=evidence,
            current_patch=current_patch,
            fallback_query=user_input,
        )
        missing_fields = identify_missing_compose_fields(draft.compose_config_patch)
        if missing_fields and resolved_knowledge_source != "none":
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
                structured_followup_evidence: Tuple[RagEvidence, ...] = ()
                if resolved_knowledge_source in {"hybrid", "scenario_parameters"}:
                    structured_followup_evidence = tuple(
                        self._retrieve_scenario_parameter_evidence(
                            queries=_scenario_parameter_followup_queries(
                                intent=intent,
                                missing_fields=missing_fields,
                                followup_queries=followup_queries,
                            ),
                            topk=24,
                            parameter_names=missing_fields,
                        )
                    )
                if structured_followup_evidence:
                    extra_evidence = tuple(
                        merge_evidence_collections(extra_evidence, structured_followup_evidence)
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
        none_status = {
            "key": "none",
            "label": "No retrieval",
            "available": True,
            "product_available": True,
            "description": "Generate parameter suggestions without loading or querying a knowledge index.",
            "artifact_count": 0,
            "item_count": 0,
        }
        if rag_mode() == "disabled":
            return [none_status]
        pdf_status = self._build_pdf_knowledge_status()
        graph_status = self._build_graph_knowledge_status()
        scenario_status = self._build_scenario_parameter_status()
        hybrid_available = bool(
            pdf_status["available"]
            or graph_status["available"]
            or scenario_status["available"]
        )
        hybrid_status = {
            "key": "hybrid",
            "label": "Hybrid",
            "available": hybrid_available,
            "description": "Merge PDF RAG chunks, GraphRAG txt/community artifacts, and structured scenario-parameter triples.",
            "artifact_count": (
                int(pdf_status.get("artifact_count", 0))
                + int(graph_status.get("artifact_count", 0))
                + int(scenario_status.get("artifact_count", 0))
            ),
            "item_count": (
                int(pdf_status.get("item_count", 0))
                + int(graph_status.get("item_count", 0))
                + int(scenario_status.get("item_count", 0))
            ),
        }
        for item in (hybrid_status, pdf_status, graph_status, scenario_status):
            item["product_available"] = False
            item["experimental"] = True
        return [none_status, hybrid_status, pdf_status, graph_status, scenario_status]

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
        progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
    ) -> Dict[str, Any]:
        return self.scene_job_service.run_job_sync(
            draft=draft,
            patch_overrides=patch_overrides,
            generation_options=generation_options,
            scene_context=sanitize_scene_context(scene_context),
            progress_callback=progress_callback,
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

    def cancel_scene_job(self, job_id: str) -> SceneJobStatusResponse | None:
        return self.scene_job_service.cancel_job(job_id)

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

    def evaluate_scene_with_history(
        self,
        *,
        layout_path: str,
        image_path: str | None = None,
        previous_layout_path: str | None = None,
        previous_image_path: str | None = None,
        previous_score: float = 0.0,
        previous_evaluation: str = "",
        knowledge_source: str = _DEFAULT_KNOWLEDGE_SOURCE,
    ) -> Dict[str, Any]:
        """Evaluate scene with before/after comparison using road-metrics EvalEngine.

        Returns the same fields as evaluate_scene_unified plus a `comparison` object.
        """
        layout = Path(layout_path).expanduser().resolve()
        if not layout.exists():
            raise RuntimeError(f"Layout file not found: {layout}")
        
        # Current evaluation
        current_payload = json.loads(layout.read_text(encoding="utf-8"))
        current_result = self.eval_engine.evaluate(current_payload, image_path=image_path)
        
        # Previous evaluation (if available)
        comparison = {}
        if previous_layout_path:
            prev = Path(previous_layout_path).expanduser().resolve()
            if prev.exists():
                prev_payload = json.loads(prev.read_text(encoding="utf-8"))
                prev_result = self.eval_engine.evaluate(prev_payload, image_path=previous_image_path)
                
                comparison = {
                    "improved_areas": self._find_improvements(current_result, prev_result),
                    "regressed_areas": self._find_regressions(current_result, prev_result),
                    "unchanged_areas": self._find_unchanged(current_result, prev_result),
                    "reasoning": self._generate_comparison_text(current_result, prev_result),
                }
        
        safety_available = self._llm_report_available(current_result.safety)
        beauty_available = self._llm_report_available(current_result.beauty)
        visual_scores_available = safety_available and beauty_available

        # Convert to Web API format (0-100 scale)
        return {
            "walkability": int(current_result.walkability.walkability_index * 100),
            "safety": int(current_result.safety.final_score * 100) if safety_available else None,
            "beauty": int(current_result.beauty.final_score * 100) if beauty_available else None,
            "overall": int(current_result.evaluation_score * 100) if visual_scores_available else None,
            "score_weights": self._score_weights_payload(),
            "score_formula": self._score_formula_text(),
            "evaluation": self._generate_evaluation_text(current_result),
            "suggestions": self._generate_suggestions(current_result),
            "indicators": self._extract_indicators(current_result),
            "config_patch": self._generate_config_patch(current_result),
            "llm_status": self._extract_llm_status(current_result),
            "comparison": comparison,
        }

    def propose_improvement(
        self,
        *,
        current_evaluation: str,
        comparison: Mapping[str, Any],
        current_patch: Mapping[str, Any],
        weakness_queries: Sequence[str] | None = None,
        knowledge_source: str = _DEFAULT_KNOWLEDGE_SOURCE,
    ) -> Dict[str, Any]:
        """Propose a config_patch grounded in RAG evidence for the weakest dimensions."""
        llm = self._get_llm_client()
        evidence: List[RagEvidence] = []
        resolved_knowledge = normalize_knowledge_source(knowledge_source)
        if resolved_knowledge != "none" and weakness_queries:
            try:
                evidence = self._retrieve_evidence(
                    queries=weakness_queries,
                    topk=6,
                    knowledge_source=resolved_knowledge,
                )
                structured_evidence = self._retrieve_scenario_parameter_evidence(
                    queries=weakness_queries,
                    topk=6,
                )
                if structured_evidence:
                    evidence = merge_evidence_collections(evidence, structured_evidence)
            except RuntimeError:
                evidence = []

        from .prompts import build_improvement_messages
        messages = build_improvement_messages(
            current_evaluation=current_evaluation,
            comparison=dict(comparison),
            current_patch=dict(current_patch),
            evidence=evidence,
        )
        payload = llm.chat_json(messages)
        patch = sanitize_compose_config_patch(payload.get("config_patch"))
        return {
            "config_patch": patch,
            "citations": list(payload.get("citations", []) or []),
            "reasoning": str(payload.get("reasoning", "") or "").strip(),
        }

    def propose_improvement_candidates(
        self,
        *,
        current_evaluation: str,
        comparison: Mapping[str, Any],
        current_patch: Mapping[str, Any],
        optimization_directives: Sequence[Mapping[str, Any]],
        topk: int = 3,
        weakness_queries: Sequence[str] | None = None,
        knowledge_source: str = _DEFAULT_KNOWLEDGE_SOURCE,
        evidence: Sequence[RagEvidence] | Sequence[Mapping[str, Any]] | None = None,
    ) -> List[Dict[str, Any]]:
        """Ask the LLM for bounded improvement candidates.

        The LLM receives rule-based directives and must reference them. The
        caller is still responsible for filtering returned patches against the
        directive bounds before generation.
        """
        llm = self._get_llm_client()
        evidence_items: List[RagEvidence] = []
        for item in evidence or ():
            if isinstance(item, RagEvidence):
                evidence_items.append(item)
            elif isinstance(item, Mapping):
                evidence_items.append(parse_rag_evidence(item))
        resolved_knowledge = normalize_knowledge_source(knowledge_source)
        if not evidence_items and resolved_knowledge != "none" and weakness_queries:
            try:
                evidence_items = self._retrieve_evidence(
                    queries=weakness_queries,
                    topk=max(3, int(topk) * 2),
                    knowledge_source=resolved_knowledge,
                )
                structured_evidence = self._retrieve_scenario_parameter_evidence(
                    queries=weakness_queries,
                    topk=max(3, int(topk) * 2),
                )
                if structured_evidence:
                    evidence_items = merge_evidence_collections(evidence_items, structured_evidence)
            except RuntimeError:
                evidence_items = []
        payload = llm.chat_json(_build_candidate_improvement_messages(
            current_evaluation=current_evaluation,
            comparison=dict(comparison),
            current_patch=dict(current_patch),
            optimization_directives=list(optimization_directives),
            evidence=evidence_items,
            topk=max(1, int(topk)),
        ))
        return _parse_candidate_payload(
            payload,
            fallback_query=str(current_patch.get("query", "") or ""),
            topk=max(1, int(topk)),
            default_reason="LLM improvement candidate constrained by rule-based directives.",
            evidence=evidence_items,
            fill_defaults=False,
        )

    def evaluate_scene_unified(
        self,
        *,
        layout_path: str,
        image_path: str | None = None,
        rendered_views: List[Mapping[str, Any]] | None = None,
        knowledge_source: str = _DEFAULT_KNOWLEDGE_SOURCE,
        evaluation_profile: str = "auto",
        evaluation_config: Mapping[str, Any] | None = None,
        evaluation_mode: str = "full",
    ) -> Dict[str, Any]:
        """Evaluate scene with unified 3-dimension scores using road-metrics EvalEngine.

        Args:
            layout_path: Path to scene_layout.json
            image_path: Optional legacy path to rendered preview image
            rendered_views: Rendered scene views as data URLs for visual LLM evaluation
            knowledge_source: Knowledge source to use (pdf_rag, graph_rag, hybrid, or none)
            evaluation_config: Optional scoring and walkability overrides merged over the profile

        Returns:
            Dict with walkability, safety, beauty (0-100), overall (0-100), evaluation, suggestions
        """
        layout = Path(layout_path).expanduser().resolve()
        if not layout.exists():
            raise RuntimeError(f"Layout file not found: {layout}")
        
        mode = str(evaluation_mode or "full").strip().lower()
        if mode not in {"structured", "full"}:
            raise ValueError("evaluation_mode must be structured or full.")
        payload = json.loads(layout.read_text(encoding="utf-8"))
        profile = self._normalize_evaluation_profile(evaluation_profile, payload=payload)
        effective_rendered_views = [] if mode == "structured" else list(rendered_views or [])
        if mode == "full" and not effective_rendered_views:
            effective_rendered_views = rendered_views_for_evaluation_from_payload(
                payload,
                limit=DEFAULT_EVALUATION_RENDER_VIEW_LIMIT,
                base_dir=layout.parent,
            )
        child_views = [
            dict(view) for view in effective_rendered_views
            if str(view.get("view_id") or "").strip().lower() == "child_forward"
        ]
        visual_rendered_views = [
            dict(view) for view in effective_rendered_views
            if str(view.get("view_id") or "").strip().lower() != "child_forward"
        ]
        
        # Use EvalEngine from road-metrics submodule
        engine = self._eval_engine_for_profile(
            profile,
            evaluation_config=evaluation_config,
            enable_llm_eval=mode == "full",
        )
        result = engine.evaluate(
            payload,
            rendered_views=visual_rendered_views,
            image_path=image_path if mode == "full" else None,
        )
        safety_available = self._llm_report_available(result.safety)
        beauty_available = self._llm_report_available(result.beauty)
        visual_scores_available = safety_available and beauty_available
        
        # Convert to Web API format (0-100 scale)
        structured_safety = int(float(getattr(result.safety, "structural_score", result.safety.final_score)) * 100)
        structured_beauty = int(float(getattr(result.beauty, "structural_score", result.beauty.final_score)) * 100)
        structured_composite = int(float(result.evaluation_score) * 100)
        return {
            "evaluation_mode": mode,
            "walkability": int(result.walkability.walkability_index * 100),
            "safety": int(result.safety.final_score * 100) if mode == "full" and safety_available else None,
            "beauty": int(result.beauty.final_score * 100) if mode == "full" and beauty_available else None,
            "overall": int(result.evaluation_score * 100) if mode == "full" and visual_scores_available else None,
            "structured_safety": structured_safety,
            "structured_beauty_proxy": structured_beauty,
            "structured_composite_score": structured_composite if mode == "structured" else None,
            "structured_composite_label": "structured_proxy_not_visual_overall" if mode == "structured" else None,
            "visual_metrics_status": "pending_full_evaluation" if mode == "structured" else ("available" if visual_scores_available else "n/a"),
            "score_weights": self._score_weights_payload(engine),
            "score_formula": self._score_formula_text(engine),
            "evaluation_profile": profile,
            "effective_evaluation_config": self._effective_evaluation_config_payload(engine),
            "evaluation": self._generate_evaluation_text(result, engine=engine),
            "suggestions": self._generate_suggestions(result),
            "indicators": self._extract_indicators(result),
            "indicator_meta": self._indicator_meta_payload(
                engine,
                profile,
                walkability_metadata=getattr(result.walkability, "metadata", {}),
            ),
            "child_friendly": self._child_friendly_payload(result, child_views),
            "config_patch": self._generate_config_patch(result),
            "llm_status": self._extract_llm_status(result),
            "quality_layers": dict(getattr(result, "quality_layers", {}) or {}),
            "generation_quality_score": (
                int(float(getattr(result, "generation_quality_score")) * 100)
                if getattr(result, "generation_quality_score", None) is not None
                else None
            ),
        }

    def generate_initial_config_from_graph(
        self,
        *,
        graph_summary: Mapping[str, Any],
        base_map_data_url: str | None = None,
        user_prompt: str = "",
        current_patch: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Ask the LLM to propose initial design parameters from a graph context.

        Returns a dict with ``compose_config_patch`` and ``design_summary``.
        """
        llm = self._get_llm_client()
        messages = build_graph_aware_design_messages(
            graph_summary=dict(graph_summary),
            base_map_data_url=base_map_data_url,
            user_prompt=user_prompt,
            current_patch=current_patch,
        )
        payload = llm.chat_json(messages)
        patch = sanitize_compose_config_patch(payload.get("compose_config_patch"))
        design_summary = str(payload.get("design_summary", "") or "").strip()
        # Fill defaults for any missing fields
        for field_name, default_value in DEFAULT_COMPOSE_CONFIG_PATCH_VALUES.items():
            if field_name not in patch:
                patch[field_name] = default_value
        if user_prompt and "query" not in patch:
            patch["query"] = str(user_prompt).strip()
        return {
            "compose_config_patch": patch,
            "design_summary": design_summary,
        }

    def generate_initial_config_candidates_from_graph(
        self,
        *,
        graph_summary: Mapping[str, Any],
        base_map_data_url: str | None = None,
        user_prompt: str = "",
        current_patch: Mapping[str, Any] | None = None,
        evidence: Sequence[RagEvidence] | Sequence[Mapping[str, Any]] | None = None,
        topk: int = 3,
    ) -> List[Dict[str, Any]]:
        """Ask the LLM for multiple initial graph-aware design candidates."""
        llm = self._get_llm_client()
        evidence_items: List[RagEvidence] = []
        for item in evidence or ():
            if isinstance(item, RagEvidence):
                evidence_items.append(item)
            elif isinstance(item, Mapping):
                evidence_items.append(parse_rag_evidence(item))
        payload = llm.chat_json(_build_graph_candidate_messages(
            graph_summary=dict(graph_summary),
            base_map_data_url=base_map_data_url,
            user_prompt=user_prompt,
            current_patch=current_patch or {},
            evidence=evidence_items,
            topk=max(1, int(topk)),
        ))
        return _parse_candidate_payload(
            payload,
            fallback_query=user_prompt,
            topk=max(1, int(topk)),
            default_reason="LLM initial graph-aware candidate.",
            evidence=evidence_items,
            fill_defaults=True,
        )

    # ========================================================================
    # Evaluation helper methods
    # ========================================================================

    def _normalize_evaluation_profile(
        self,
        value: str | None,
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> str:
        profile = str(value or "auto").strip().lower() or "auto"
        if profile in {"local_segment_v1", "network_v1"}:
            return profile
        if profile != "auto":
            return "local_segment_v1"
        summary = payload.get("summary", {}) if isinstance(payload, Mapping) else {}
        summary = summary if isinstance(summary, Mapping) else {}
        graph_summary = summary.get("road_segment_graph_summary")
        graph = graph_summary if isinstance(graph_summary, Mapping) else {}
        segment_count = int(graph.get("segment_count") or summary.get("segment_count") or 0)
        road_count = int(
            graph.get("road_count")
            or summary.get("road_count")
            or summary.get("centerline_count")
            or 0
        )
        return "network_v1" if segment_count > 1 or road_count > 1 else "local_segment_v1"

    def _eval_engine_for_profile(
        self,
        profile: str,
        *,
        evaluation_config: Mapping[str, Any] | None = None,
        enable_llm_eval: bool = True,
    ):
        engine = self.eval_engine
        overrides = dict(evaluation_config or {})
        unsupported_keys = sorted(set(overrides) - {"aggregation", "walkability"})
        if unsupported_keys:
            raise RuntimeError(
                "Invalid evaluation_config: unsupported field(s): "
                + ", ".join(unsupported_keys)
            )
        if type(engine) is not self._EvalEngine:
            if overrides:
                raise RuntimeError(
                    "evaluation_config overrides require the standard evaluation engine"
                )
            return engine
        active_config = getattr(engine, "config", None)
        active_profile = str(getattr(active_config, "evaluation_profile", "") or "")
        active_llm = bool(getattr(active_config, "enable_llm_eval", False))
        if not overrides and (
            active_config is None
            or (profile == active_profile and active_llm == bool(enable_llm_eval))
            or not hasattr(self, "_EvalEngine")
        ):
            return engine
        try:
            profile_config = self._EvalConfig.for_profile(
                profile,
                enable_llm_eval=bool(enable_llm_eval),
            )
            effective_config = self._EvalConfig.from_dict(
                overrides,
                base_config=profile_config,
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Invalid evaluation_config: {exc}") from exc
        return self._EvalEngine(effective_config)

    def _score_weights_payload(self, engine: Any | None = None) -> Dict[str, float]:
        """Return normalized weights for the registered unified dimensions."""
        aggregation = getattr(
            getattr(engine or self.eval_engine, "config", None),
            "aggregation",
            None,
        )
        component_names = ("walkability", "safety", "beauty")
        resolver = getattr(aggregation, "normalized_dimension_weights", None)
        if callable(resolver):
            return {
                name: float(value)
                for name, value in resolver(component_names).items()
            }
        raw = {
            name: float(getattr(aggregation, f"{name}_weight", 1.0))
            for name in component_names
        }
        total = sum(raw.values())
        if total <= 0.0:
            raise RuntimeError("Evaluation score weights must not all be zero")
        return {name: value / total for name, value in raw.items()}

    def _effective_evaluation_config_payload(
        self,
        engine: Any | None = None,
    ) -> Dict[str, Any]:
        """Return the effective, API-round-trippable evaluation settings."""
        config = getattr(engine or self.eval_engine, "config", None)
        serializer = getattr(config, "to_dict", None)
        serialized = dict(serializer()) if callable(serializer) else {}
        walkability = dict(serialized.get("walkability", {}) or {})
        walkability_keys = (
            "clear_width_min",
            "clear_width_ideal",
            "amenity_density_ideal",
            "amenity_count_density_ideal",
            "lamp_spacing_m",
            "transit_stop_spacing_m",
            "crossing_spacing_m",
            "entrance_density_ideal",
            "tree_shade_grid_resolution_m",
            "tree_sun_azimuth_deg",
            "tree_sun_elevation_deg",
            "tree_canopy_center_height_ratio",
            "tree_canopy_vertical_ratio",
        )
        return {
            "aggregation": {
                "dimension_weights": self._score_weights_payload(engine),
            },
            "walkability": {
                key: walkability[key]
                for key in walkability_keys
                if key in walkability
            },
        }

    def _score_formula_text(self, engine: Any | None = None) -> str:
        weights = self._score_weights_payload(engine)
        return (
            "overall = "
            f"walkability {weights['walkability']:.2f} + "
            f"safety {weights['safety']:.2f} + "
            f"beauty {weights['beauty']:.2f}"
        )

    def _generate_evaluation_text(self, result, *, engine: Any | None = None) -> str:
        """Generate human-readable evaluation text."""
        w = result.walkability
        s = result.safety
        b = result.beauty
        safety_available = self._llm_report_available(s)
        beauty_available = self._llm_report_available(b)
        
        parts = []
        
        # Walkability summary
        if w.walkability_index >= 0.7:
            parts.append("步行性优秀")
        elif w.walkability_index >= 0.5:
            parts.append("步行性良好")
        else:
            parts.append("步行性需改进")
        
        # Safety summary
        if not safety_available:
            parts.append("视觉安全性 N/A")
        elif s.final_score >= 0.7:
            parts.append("视觉安全性高")
        elif s.final_score >= 0.5:
            parts.append("视觉安全性中等")
        else:
            parts.append("视觉安全性需改进")
        
        # Beauty summary
        if not beauty_available:
            parts.append("视觉美观度 N/A")
        elif b.final_score >= 0.7:
            parts.append("视觉美观度优秀")
        elif b.final_score >= 0.5:
            parts.append("视觉美观度良好")
        else:
            parts.append("视觉美观度需改进")
        
        # Weakest dimension
        if safety_available and s.diagnosis.get("weakest"):
            parts.append(f"最弱安全维度: {s.diagnosis['weakest']}")
        if beauty_available and b.diagnosis.get("weakest"):
            parts.append(f"最弱美观维度: {b.diagnosis['weakest']}")
        parts.append(f"评分公式: {self._score_formula_text(engine)}")
        
        return "。".join(parts) + "。"

    def _generate_suggestions(self, result) -> List[str]:
        """Generate improvement suggestions based on evaluation result."""
        suggestions = []
        safety_available = self._llm_report_available(result.safety)
        beauty_available = self._llm_report_available(result.beauty)
        
        # Safety suggestions
        if safety_available and result.safety.final_score < 0.7:
            weakest = result.safety.diagnosis.get("weakest")
            if weakest == "CROSS_PROV":
                suggestions.append("增加过街设施密度，建议每80米设置一个过街点")
            elif weakest == "LIGHT_UNI":
                suggestions.append("优化路灯布局，提高照明均匀性")
            elif weakest == "BUFFER_RATIO":
                suggestions.append("增加缓冲带宽度，提高行人保护")
            elif weakest == "BOLLARD_DENSITY":
                suggestions.append("增加护柱密度，提高物理隔离")
        
        # Beauty suggestions
        if beauty_available and result.beauty.final_score < 0.7:
            weakest = result.beauty.diagnosis.get("weakest")
            if weakest == "active_front_ratio":
                suggestions.append("增加活跃界面比例，提高街道活力")
            elif weakest == "anchor_poi_score":
                suggestions.append("增加锚点POI，如餐厅、咖啡馆等")
            elif weakest == "presentation_score":
                suggestions.append("提升整体展示质量，包括风格一致性和视觉秩序")
        
        # Walkability suggestions
        w = result.walkability
        if w.walkability_index < 0.7:
            if w.comfort < 0.5:
                suggestions.append("提高步行舒适性，增加净空宽度和绿化遮荫")
            if w.delight < 0.5:
                suggestions.append("增加街道愉悦度，提高家具密度和POI混合度")
        
        if not safety_available or not beauty_available:
            suggestions.append("视觉评估需要 Viewer 截图输入；缺失时安全性和美观度显示为 N/A")
        return suggestions if suggestions else ["场景质量良好，无需重大改进"]

    def _extract_indicators(self, result) -> Dict[str, Any]:
        """Extract detailed indicators for Web API response."""
        w = result.walkability
        s = result.safety
        b = result.beauty

        # 提取步行性子分数
        protection = w.protection
        comfort = w.comfort
        delight = w.delight

        # 提取安全性子分数（如果有 LLM 评分）
        safety_lighting = None
        safety_visibility = None
        safety_protection = None
        safety_activation = None
        
        if s.llm_scores:
            safety_lighting = s.llm_scores.get("lighting")
            safety_visibility = s.llm_scores.get("visibility")
            safety_protection = s.llm_scores.get("protection")
            safety_activation = s.llm_scores.get("activation")

        # 提取美观性子分数（仅在 LLM 返回真实维度时提供）
        beauty_coherence = None
        beauty_human_scale = None
        beauty_material_contrast = None
        beauty_visual_interest = None
        
        if b.llm_scores:
            beauty_coherence = b.llm_scores.get("coherence")
            beauty_human_scale = b.llm_scores.get("human_scale")
            beauty_material_contrast = b.llm_scores.get("material_contrast")
            beauty_visual_interest = b.llm_scores.get("visual_interest")

        return {
            # 步行性子分数
            "protection": round(protection * 100, 1),
            "comfort": round(comfort * 100, 1),
            "delight": round(delight * 100, 1),
            # 安全性子分数
            "safety_lighting": round(safety_lighting * 100, 1) if safety_lighting is not None else None,
            "safety_visibility": round(safety_visibility * 100, 1) if safety_visibility is not None else None,
            "safety_protection": round(safety_protection * 100, 1) if safety_protection is not None else None,
            "safety_activation": round(safety_activation * 100, 1) if safety_activation is not None else None,
            # 美观性子分数
            "beauty_coherence": round(beauty_coherence * 100, 1) if beauty_coherence is not None else None,
            "beauty_human_scale": round(beauty_human_scale * 100, 1) if beauty_human_scale is not None else None,
            "beauty_material_contrast": round(beauty_material_contrast * 100, 1) if beauty_material_contrast is not None else None,
            "beauty_visual_interest": round(beauty_visual_interest * 100, 1) if beauty_visual_interest is not None else None,
            # 保留原有字段
            "sidewalk_adequacy": self._classify(w.sid_clr),
            "furniture_density": self._classify(w.furn_d),
            "tree_shading_rate": self._classify(w.tree_shade),
            "transit_proximity_score": round(float(w.transit_prox) * 100, 1),
            "amenity_service_density": round(float(getattr(w, "amenity_service_density_score", 0.0)) * 100, 1),
            "furniture_occupation_ratio": round(float(getattr(w, "furniture_occupation_ratio", 0.0)) * 100, 1),
            "furniture_overcrowding_penalty": round(float(getattr(w, "furniture_overcrowding_penalty", 0.0)) * 100, 1),
            "clear_path_conflict_penalty": round(float(getattr(w, "clear_path_conflict_penalty", 0.0)) * 100, 1),
            "walkability_top_contributors": list(getattr(w, "top_contributors", []) or []),
        }

    def _indicator_meta_payload(
        self,
        engine: Any,
        profile: str,
        *,
        walkability_metadata: Mapping[str, Any] | None = None,
    ) -> Dict[str, Any]:
        config = getattr(engine, "config", None)
        walkability = getattr(config, "walkability", None)
        delight_weights = dict(getattr(walkability, "delight_component_weights", {}) or {})
        protection_weight = float(getattr(walkability, "protection_weight", 0.40))
        comfort_weight = float(getattr(walkability, "comfort_weight", 0.35))
        delight_weight = float(getattr(walkability, "delight_weight", 0.25))
        weights = {
            "LIGHT_UNI": protection_weight / 3.0,
            "BUFFER_RATIO": protection_weight / 3.0,
            "CROSS_PROV": protection_weight / 3.0,
            "SID_CLR": comfort_weight / 4.0,
            "CLEAR_CONT": comfort_weight / 4.0,
            "TREE_SHADE": comfort_weight / 4.0,
            "MICRO_ENV": comfort_weight / 4.0,
            "FURN_D": delight_weight * float(delight_weights.get("FURN_D", 0.25)),
            "TRANSIT_PROX": delight_weight * float(delight_weights.get("TRANSIT_PROX", 0.25)),
            "ENTR_DENS": delight_weight * float(delight_weights.get("ENTR_DENS", 0.25)),
            "POI_MIX": delight_weight * float(delight_weights.get("POI_MIX", 0.25)),
        }
        local = profile == "local_segment_v1"
        source_by_key = {
            "LIGHT_UNI": "lamp_placement_positions",
            "BUFFER_RATIO": "cross_section_dimensions",
            "CROSS_PROV": "crossing_points_and_segment_length",
            "SID_CLR": "clear_path_dimensions",
            "CLEAR_CONT": "clear_path_dimensions_and_placement_bboxes",
            "TREE_SHADE": "solar_canopy_projection_union_over_local_sidewalk_grid",
            "MICRO_ENV": "tree_shade_noise_shielding_and_entrance_openness",
            "FURN_D": "amenity_placements_and_clear_path_conflicts",
            "TRANSIT_PROX": "spatial_context_or_bus_stop_placements",
            "ENTR_DENS": "entrance_count_and_segment_length",
            "POI_MIX": "land_use_summary_and_spatial_context_pois",
        }
        if not local:
            source_by_key.update({
                "LIGHT_UNI": "lamp_count_per_network_length_proxy",
                "CROSS_PROV": "crossing_count_per_network_length",
                "CLEAR_CONT": "clear_path_dimensions_and_solver_violations",
                "TREE_SHADE": "summed_solar_projected_canopy_area_over_network_sidewalk_area_proxy",
                "TRANSIT_PROX": "bus_stop_points_or_placements_per_network_length_proxy",
            })
        walkability_meta = {
            key: {
                "weight": round(value, 4),
                "source": source_by_key[key],
                "applicability": "network_proxy" if (not local and key in {"LIGHT_UNI", "TREE_SHADE", "TRANSIT_PROX"}) else "local_and_network",
                "included_in_walkability_index": True,
                "low_discrimination": bool(local and key == "TRANSIT_PROX"),
                "note": (
                    "Reduced in local_segment_v1 because single-segment scenes usually share similar transit proximity."
                    if local and key == "TRANSIT_PROX"
                    else "Network-scale proxy; interpret comparatively, not as observed service performance."
                    if not local and key in {"LIGHT_UNI", "TREE_SHADE", "TRANSIT_PROX"}
                    else ""
                ),
            }
            for key, value in weights.items()
        }
        tree_shade_evidence = dict(
            (walkability_metadata or {}).get("tree_shade_metadata", {}) or {}
        )
        if tree_shade_evidence:
            walkability_meta["TREE_SHADE"]["evidence"] = tree_shade_evidence
        walkability_meta.update({
            "AMENITY_SERVICE_DENSITY": {
                "weight": 0.0,
                "source": "amenity_count_and_segment_length",
                "applicability": "local_and_network",
                "included_in_walkability_index": False,
                "role": "FURN_D diagnostic",
            },
            "FURNITURE_OCCUPATION_RATIO": {
                "weight": 0.0,
                "source": "furniture_footprints_over_sidewalk_area",
                "applicability": "local_and_network",
                "included_in_walkability_index": False,
                "role": "FURN_D diagnostic",
            },
            "FURNITURE_OVERCROWDING_PENALTY": {
                "weight": 0.0,
                "source": "furniture_area_density_above_configured_threshold",
                "applicability": "local_and_network",
                "included_in_walkability_index": False,
                "role": "FURN_D diagnostic",
            },
            "CLEAR_PATH_CONFLICT_PENALTY": {
                "weight": 0.0,
                "source": "solver_clear_path_violations" if not local else "placement_bbox_intersection_with_clear_path",
                "applicability": "local_and_network",
                "included_in_walkability_index": False,
                "role": "CLEAR_CONT and FURN_D diagnostic",
            },
        })
        return {
            "profile": profile,
            "walkability": walkability_meta,
            "safety": {
                "source": "visual_llm_from_declared_rendered_views",
                "requires_visual_input": True,
                "missing_visual_policy": "n/a",
                "structural_fallback_in_overall": False,
            },
            "beauty": {
                "source": "visual_llm_from_declared_rendered_views",
                "requires_visual_input": True,
                "missing_visual_policy": "n/a",
                "structural_fallback_in_overall": False,
            },
            "child_friendly": {
                "source": "child_forward_view_gate_plus_structured_layout",
                "requires_visual_input": True,
                "included_in_overall": False,
                "image_role": "availability_gate_only",
                "visual_pixels_scored": False,
            },
        }

    def _child_friendly_payload(self, result, child_views: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        valid_child_view = next(
            (
                view for view in child_views
                if str(view.get("image_data_url") or "").startswith("data:image/")
            ),
            None,
        )
        if valid_child_view is None:
            return {
                "score": None,
                "status": "missing_child_view",
                "indicators": {
                    "visual_input": "missing",
                    "required_view_id": "child_forward",
                    "included_in_overall": False,
                    "visual_pixels_scored": False,
                    "scoring_basis": "structured_layout_only",
                },
                "suggestions": ["Capture child_forward view to enable child-friendly auxiliary scoring."],
            }

        w = result.walkability
        conflict = float(getattr(w, "clear_path_conflict_penalty", 0.0) or 0.0)
        base = (
            0.25 * float(getattr(w, "sid_clr", 0.0) or 0.0)
            + 0.20 * float(getattr(w, "clear_cont", 0.0) or 0.0)
            + 0.20 * float(getattr(w, "buffer_ratio", 0.0) or 0.0)
            + 0.15 * float(getattr(w, "cross_prov", 0.0) or 0.0)
            + 0.10 * float(getattr(w, "light_uni", 0.0) or 0.0)
            + 0.10 * float(getattr(w, "tree_shade", 0.0) or 0.0)
        )
        score = max(0.0, min(base * (1.0 - 0.30 * max(0.0, min(conflict, 1.0))), 1.0))
        suggestions: List[str] = []
        if getattr(w, "sid_clr", 0.0) < 0.65:
            suggestions.append("Increase clear walking width for child-accompanied movement.")
        if getattr(w, "buffer_ratio", 0.0) < 0.35:
            suggestions.append("Strengthen road-edge buffer or protection between children and traffic.")
        if getattr(w, "cross_prov", 0.0) < 0.5:
            suggestions.append("Add or distribute crossings for safer child-scale navigation.")
        if getattr(w, "light_uni", 0.0) < 0.5:
            suggestions.append("Improve lighting uniformity for lower-height sight lines.")
        return {
            "score": int(round(score * 100)),
            "status": "scored_structural_v1",
            "indicators": {
                "visual_input": "provided",
                "view_id": str(valid_child_view.get("view_id") or "child_forward"),
                "child_eye_height_m": 1.1,
                "clear_width": round(float(getattr(w, "sid_clr", 0.0) or 0.0) * 100, 1),
                "clear_continuity": round(float(getattr(w, "clear_cont", 0.0) or 0.0) * 100, 1),
                "buffer_protection": round(float(getattr(w, "buffer_ratio", 0.0) or 0.0) * 100, 1),
                "crossing_provision": round(float(getattr(w, "cross_prov", 0.0) or 0.0) * 100, 1),
                "lighting_uniformity": round(float(getattr(w, "light_uni", 0.0) or 0.0) * 100, 1),
                "tree_shade": round(float(getattr(w, "tree_shade", 0.0) or 0.0) * 100, 1),
                "clear_path_conflict_penalty": round(conflict * 100, 1),
                "included_in_overall": False,
                "visual_pixels_scored": False,
                "image_role": "availability_gate_only",
                "scoring_basis": "structured_layout_only",
            },
            "suggestions": suggestions,
            "limitations": [
                "The child_forward image gates availability but its pixels are not scored.",
                "Traffic speed, driver yielding, supervision, and observed child behavior are not modeled.",
            ],
        }

    def _llm_report_available(self, report) -> bool:
        status = dict(getattr(report, "llm_status", {}) or {})
        return (
            bool(status.get("available", False))
            and str(status.get("visual_input", "") or "").lower() == "provided"
            and bool(getattr(report, "llm_scores", None))
        )

    def _extract_llm_status(self, result) -> Dict[str, Any]:
        """Expose whether safety/beauty LLM subscores came from cache, live LLM, or are unavailable."""
        return {
            "safety": dict(getattr(result.safety, "llm_status", {}) or {}),
            "beauty": dict(getattr(result.beauty, "llm_status", {}) or {}),
        }

    def _generate_config_patch(self, result) -> Dict[str, Any]:
        """Generate configuration patch based on evaluation result."""
        patch = {}
        
        # Safety improvements
        if self._llm_report_available(result.safety) and result.safety.final_score < 0.6:
            weakest = result.safety.diagnosis.get("weakest")
            if weakest == "CROSS_PROV":
                patch["transit_demand_level"] = "high"
            elif weakest == "BUFFER_RATIO":
                patch["road_width_m"] = 14.0  # Wider road for more buffer
        
        # Beauty improvements
        if self._llm_report_available(result.beauty) and result.beauty.final_score < 0.6:
            weakest = result.beauty.diagnosis.get("weakest")
            if weakest == "active_front_ratio":
                patch["density"] = 1.2  # Higher density for more active fronts
        
        return patch

    def _find_improvements(self, current, previous) -> List[str]:
        """Find dimensions that improved significantly."""
        improvements = []
        
        # Check walkability
        w_delta = current.walkability.walkability_index - previous.walkability.walkability_index
        if w_delta > 0.05:
            improvements.append("步行性")
        
        # Check safety
        s_delta = current.safety.final_score - previous.safety.final_score
        if s_delta > 0.05:
            improvements.append("安全性")
        
        # Check beauty
        b_delta = current.beauty.final_score - previous.beauty.final_score
        if b_delta > 0.05:
            improvements.append("美观性")
        
        return improvements

    def _find_regressions(self, current, previous) -> List[str]:
        """Find dimensions that regressed significantly."""
        regressions = []
        
        w_delta = current.walkability.walkability_index - previous.walkability.walkability_index
        if w_delta < -0.05:
            regressions.append("步行性")
        
        s_delta = current.safety.final_score - previous.safety.final_score
        if s_delta < -0.05:
            regressions.append("安全性")
        
        b_delta = current.beauty.final_score - previous.beauty.final_score
        if b_delta < -0.05:
            regressions.append("美观性")
        
        return regressions

    def _find_unchanged(self, current, previous) -> List[str]:
        """Find dimensions that remained stable."""
        unchanged = []
        
        w_delta = abs(current.walkability.walkability_index - previous.walkability.walkability_index)
        if w_delta <= 0.05:
            unchanged.append("步行性")
        
        s_delta = abs(current.safety.final_score - previous.safety.final_score)
        if s_delta <= 0.05:
            unchanged.append("安全性")
        
        b_delta = abs(current.beauty.final_score - previous.beauty.final_score)
        if b_delta <= 0.05:
            unchanged.append("美观性")
        
        return unchanged

    def _generate_comparison_text(self, current, previous) -> str:
        """Generate comparison text between current and previous results."""
        w_delta = current.walkability.walkability_index - previous.walkability.walkability_index
        s_delta = current.safety.final_score - previous.safety.final_score
        b_delta = current.beauty.final_score - previous.beauty.final_score
        
        parts = []
        
        if abs(w_delta) > 0.05:
            direction = "提升" if w_delta > 0 else "下降"
            parts.append(f"步行性{direction}{abs(w_delta)*100:.1f}%")
        
        if abs(s_delta) > 0.05:
            direction = "提升" if s_delta > 0 else "下降"
            parts.append(f"安全性{direction}{abs(s_delta)*100:.1f}%")
        
        if abs(b_delta) > 0.05:
            direction = "提升" if b_delta > 0 else "下降"
            parts.append(f"美观性{direction}{abs(b_delta)*100:.1f}%")
        
        if not parts:
            return "各项指标基本保持不变"
        
        return "。".join(parts) + "。"

    @staticmethod
    def _classify(score: float) -> str:
        """Classify a score into a human-readable label."""
        if score >= 0.8:
            return "Excellent"
        elif score >= 0.6:
            return "High"
        elif score >= 0.4:
            return "Medium"
        elif score >= 0.2:
            return "Low"
        else:
            return "Very Low"

    def _get_llm_client(self) -> LLMClient | Any:
        if self.llm_client is None:
            self.llm_client = LLMClient()
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

    def _get_scenario_parameter_store(self) -> ScenarioParameterTripleStore | Any:
        if self._scenario_parameter_store is None:
            self._scenario_parameter_store = ScenarioParameterTripleStore(self.scenario_parameter_triples_path)
        return self._scenario_parameter_store

    def _scenario_parameter_fingerprint(self) -> str:
        try:
            return str(self._get_scenario_parameter_store().artifact_fingerprint())
        except Exception:
            return ""

    def _build_draft_cache_key(
        self,
        *,
        user_input: str,
        knowledge_source: str,
    ) -> str:
        normalized_prompt = _normalize_cache_prompt(user_input)
        key_payload = {
            "knowledge_source": normalize_knowledge_source(knowledge_source),
            "scenario_parameter_fingerprint": self._scenario_parameter_fingerprint(),
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

    def _build_scenario_parameter_status(self) -> Dict[str, Any]:
        path = self.scenario_parameter_triples_path
        try:
            triples = self._get_scenario_parameter_store().load()
            available = path.exists()
            item_count = len(triples) if available else 0
            fingerprint = self._scenario_parameter_fingerprint() if available else ""
            error = ""
        except Exception as exc:
            available = False
            item_count = 0
            fingerprint = ""
            error = str(exc)
        status: Dict[str, Any] = {
            "key": "scenario_parameters",
            "label": "Scenario Parameters",
            "available": available,
            "description": "Structured scenario-parameter-value triples from the design matrix and scene presets.",
            "artifact_count": 1 if available else 0,
            "item_count": item_count,
            "artifact_path": str(path),
            "fingerprint": fingerprint,
        }
        if error:
            status["error"] = error
        return status

    def _resolve_retrievers_for_source(self, knowledge_source: str) -> Tuple[Tuple[str, Any], ...]:
        resolved = normalize_knowledge_source(knowledge_source)
        if resolved == "none":
            return ()
        require_experimental_rag(resolved)
        if resolved == "scenario_parameters":
            return ()
        if resolved == "pdf_rag":
            return (("pdf_rag", self._get_pdf_retriever()),)
        if resolved == "graph_rag":
            graph_status = self._build_graph_knowledge_status()
            if not graph_status.get("available"):
                raise RuntimeError(graph_status.get("error") or "GraphRAG artifacts are not available.")
            return (("graph_rag", self._get_graph_retriever()),)

        # Check for custom uploaded sources
        from ..knowledge.source_registry import get_source
        custom_source = get_source(resolved)
        if custom_source is not None:
            if custom_source.source_type == "pdf_rag" and custom_source.artifact_dir:
                from ..knowledge.pdf_rag import PdfKnowledgeBaseRetriever
                retriever = PdfKnowledgeBaseRetriever(artifact_dir=custom_source.artifact_dir)
                return ((custom_source.source_id, retriever),)
            raise RuntimeError(f"Unsupported custom knowledge source type: {custom_source.source_type}")

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
        resolved_source = normalize_knowledge_source(knowledge_source)
        if resolved_source == "none":
            return []
        require_experimental_rag(resolved_source)
        if resolved_source == "scenario_parameters":
            return self._retrieve_scenario_parameter_evidence(queries, topk=topk)
        try:
            retrievers = self._resolve_retrievers_for_source(knowledge_source)
        except RuntimeError:
            if resolved_source != "hybrid":
                raise
            retrievers = ()
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
        if resolved_source == "hybrid":
            for evidence in self._retrieve_scenario_parameter_evidence(queries, topk=topk):
                if evidence.chunk_id in seen:
                    continue
                seen.add(evidence.chunk_id)
                items.append(evidence)
        if resolved_source == "hybrid":
            return _limit_evidence_with_source_diversity(items, topk=max(1, int(topk)))
        items.sort(key=lambda item: float(item.score), reverse=True)
        return items[: max(1, int(topk))]

    def _retrieve_scenario_parameter_evidence(
        self,
        queries: Iterable[str],
        *,
        topk: int = 24,
        parameter_names: Sequence[str] | None = None,
    ) -> List[RagEvidence]:
        store = self._get_scenario_parameter_store()
        items: List[RagEvidence] = []
        seen = set()
        for query in queries:
            query_text = str(query or "").strip()
            if not query_text:
                continue
            try:
                hits = store.search(
                    query_text,
                    topk=max(1, int(topk)),
                    parameter_names=parameter_names,
                )
            except Exception:
                continue
            for hit in hits:
                evidence = convert_search_hit_to_evidence(
                    hit,
                    rag_query=query_text,
                    knowledge_source="scenario_parameters",
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
        llm: LLMClient | Any,
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
        llm: LLMClient | Any,
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
        llm: LLMClient | Any,
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
        llm: LLMClient | Any,
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


def _build_graph_candidate_messages(
    *,
    graph_summary: Mapping[str, Any],
    base_map_data_url: str | None,
    user_prompt: str,
    current_patch: Mapping[str, Any],
    evidence: Sequence[RagEvidence],
    topk: int,
) -> list[Dict[str, Any]]:
    from ..services.design_types import ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS

    serialized_evidence = [
        {
            "chunk_id": item.chunk_id,
            "section_title": item.section_title,
            "page_start": item.page_start,
            "page_end": item.page_end,
            "text": item.text,
            "score": item.score,
            "parameter_hints": item.parameter_hints,
        }
        for item in evidence
    ]
    system_prompt = (
        "你是 RoadGen3D 的多方案街道设计参数生成器。"
        "你只能输出 JSON。"
        "字段必须包含 candidates 数组。"
        "每个 candidate 必须包含 compose_config_patch(object), design_summary(string), "
        "reasoning(string), citations(string[])。"
        f"compose_config_patch 只能使用这些字段：{', '.join(ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS)}。"
        "请生成互相有差异但都可行的候选，不要编造资产 ID，不要输出 None/null。"
    )
    user_payload = {
        "topk": int(topk),
        "graph_summary": dict(graph_summary),
        "user_prompt": str(user_prompt).strip() or "Generate a suitable street design",
        "current_patch": dict(current_patch or {}),
        "rag_evidence": serialized_evidence,
        "instruction": "生成 topk 个初始设计参数候选；候选之间应体现不同但合理的设计取向。",
    }
    user_content: list[Dict[str, Any]] = [
        {"type": "text", "text": json.dumps(user_payload, ensure_ascii=False)}
    ]
    if base_map_data_url:
        user_content.append({"type": "image_url", "image_url": {"url": base_map_data_url}})
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]


def _build_candidate_improvement_messages(
    *,
    current_evaluation: str,
    comparison: Mapping[str, Any],
    current_patch: Mapping[str, Any],
    optimization_directives: Sequence[Mapping[str, Any]],
    evidence: Sequence[RagEvidence],
    topk: int,
) -> list[Dict[str, str]]:
    from ..services.design_types import ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS

    serialized_evidence = [
        {
            "chunk_id": item.chunk_id,
            "section_title": item.section_title,
            "page_start": item.page_start,
            "page_end": item.page_end,
            "text": item.text,
            "score": item.score,
            "parameter_hints": item.parameter_hints,
        }
        for item in evidence
    ]
    system_prompt = (
        "你是 RoadGen3D 的受约束设计改进候选生成器。"
        "你只能输出 JSON。字段必须包含 candidates 数组。"
        "每个 candidate 必须包含 compose_config_patch(object), reasoning(string), "
        "directive_ids(string[]), citations(string[])。"
        "你不能自由决定大幅改动；必须遵守 rule-based optimization_directives 的 allowed_fields、"
        "bounds、enum_values 和 forbidden_fields。"
        f"compose_config_patch 只能使用这些字段：{', '.join(ALLOWED_COMPOSE_CONFIG_PATCH_FIELDS)}。"
        "不要编造资产 ID，不要输出 None/null。"
    )
    user_payload = {
        "topk": int(topk),
        "current_evaluation": str(current_evaluation or ""),
        "comparison": dict(comparison or {}),
        "current_patch": dict(current_patch or {}),
        "optimization_directives": list(optimization_directives),
        "rag_evidence": serialized_evidence,
        "instruction": "基于规则方向生成 topk 个小步、可解释、可过滤的改进候选。",
    }
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def _parse_candidate_payload(
    payload: Mapping[str, Any],
    *,
    fallback_query: str,
    topk: int,
    default_reason: str,
    evidence: Sequence[RagEvidence],
    fill_defaults: bool,
) -> List[Dict[str, Any]]:
    raw_candidates = payload.get("candidates")
    if isinstance(raw_candidates, Mapping):
        raw_items = list(raw_candidates.values())
    elif isinstance(raw_candidates, Sequence) and not isinstance(raw_candidates, (str, bytes)):
        raw_items = list(raw_candidates)
    else:
        raw_items = [payload]

    evidence_ids = {item.chunk_id for item in evidence}
    candidates: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_items[: max(1, int(topk))]):
        if not isinstance(raw, Mapping):
            continue
        patch = sanitize_compose_config_patch(raw.get("compose_config_patch") or raw.get("config_patch"))
        if fill_defaults:
            for field_name, default_value in DEFAULT_COMPOSE_CONFIG_PATCH_VALUES.items():
                patch.setdefault(field_name, default_value)
        if fallback_query and "query" not in patch:
            patch["query"] = str(fallback_query).strip()
        citations = [
            str(item)
            for item in (raw.get("citations", []) or [])
            if not evidence_ids or str(item) in evidence_ids
        ]
        candidates.append({
            "candidate_id": str(raw.get("candidate_id") or f"candidate_{index + 1}"),
            "rank": int(raw.get("rank", index + 1) or index + 1),
            "compose_config_patch": patch,
            "design_summary": str(raw.get("design_summary", "") or "").strip(),
            "reasoning": str(raw.get("reasoning", default_reason) or default_reason).strip(),
            "directive_ids": _coerce_text_list(raw.get("directive_ids")),
            "citations": citations,
        })
    if candidates:
        return candidates
    fallback_patch = dict(DEFAULT_COMPOSE_CONFIG_PATCH_VALUES) if fill_defaults else {}
    if fallback_query:
        fallback_patch["query"] = str(fallback_query).strip()
    return [{
        "candidate_id": "candidate_1",
        "rank": 1,
        "compose_config_patch": fallback_patch,
        "design_summary": "",
        "reasoning": default_reason,
        "directive_ids": [],
        "citations": [],
    }]


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
    raw_template_patch = payload.get("template_patch")
    return DesignDraft(
        normalized_scene_query=normalized_scene_query,
        compose_config_patch=patch,
        citations_by_field=filtered_citations,
        design_summary=summary,
        risk_notes=risk_notes,
        template_patch=dict(raw_template_patch) if isinstance(raw_template_patch, Mapping) else None,
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


def _limit_evidence_with_source_diversity(items: Sequence[RagEvidence], *, topk: int) -> List[RagEvidence]:
    ranked = sorted(items, key=lambda item: float(item.score), reverse=True)
    limit = max(1, int(topk))
    selected: List[RagEvidence] = []
    seen_chunks: set[str] = set()
    for source_name in ("pdf_rag", "graph_rag", "scenario_parameters"):
        match = next((item for item in ranked if item.knowledge_source == source_name), None)
        if match is None or match.chunk_id in seen_chunks:
            continue
        selected.append(match)
        seen_chunks.add(match.chunk_id)
        if len(selected) >= limit:
            return selected
    for item in ranked:
        if item.chunk_id in seen_chunks:
            continue
        selected.append(item)
        seen_chunks.add(item.chunk_id)
        if len(selected) >= limit:
            break
    return selected


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
            template_patch=draft.template_patch,
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
            template_patch=draft.template_patch,
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


def _scenario_parameter_followup_queries(
    *,
    intent: DesignIntent,
    missing_fields: Sequence[str],
    followup_queries: Sequence[str],
) -> Tuple[str, ...]:
    queries: List[str] = []
    queries.extend(str(item).strip() for item in followup_queries if str(item).strip())
    queries.extend(str(item).strip() for item in missing_fields if str(item).strip())
    intent_terms = [
        *intent.user_goals,
        *intent.style_preferences,
        *intent.safety_priorities,
    ]
    for field_name in missing_fields:
        field_text = str(field_name).strip()
        if not field_text:
            continue
        for term in intent_terms[:4]:
            term_text = str(term).strip()
            if term_text:
                queries.append(f"{term_text} {field_text}")
    return tuple(dict.fromkeys(item for item in queries if item))


def normalize_knowledge_source(value: object) -> str:
    normalized = str(value or _DEFAULT_KNOWLEDGE_SOURCE).strip().lower()
    if normalized in _ALLOWED_KNOWLEDGE_SOURCES:
        return normalized
    # Allow custom source IDs (e.g., uploaded PDFs) to pass through
    return str(value or _DEFAULT_KNOWLEDGE_SOURCE).strip()


def rag_mode() -> str:
    """Return the explicit product mode for retrieval-backed features."""

    value = str(os.getenv("ROADGEN_RAG_MODE", "disabled") or "disabled").strip().lower()
    return value if value in _ALLOWED_RAG_MODES else "disabled"


def rag_product_available() -> bool:
    """Whether RAG may be called through the expert experimental API."""

    return rag_mode() == "experimental"


def require_experimental_rag(knowledge_source: str) -> None:
    resolved = normalize_knowledge_source(knowledge_source)
    if resolved != "none" and not rag_product_available():
        raise RuntimeError(
            "Knowledge retrieval is disabled in this RoadGen3D deployment. "
            "Set ROADGEN_RAG_MODE=experimental to use the expert retrieval API."
        )
