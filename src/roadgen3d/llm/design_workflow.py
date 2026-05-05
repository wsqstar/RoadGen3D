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
    ScenarioParameterTripleStore,
)
from ..knowledge.pdf_rag import KnowledgeSearchHit
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
_DEFAULT_KNOWLEDGE_SOURCE = "graph_rag"
_ALLOWED_KNOWLEDGE_SOURCES = frozenset({"hybrid", "pdf_rag", "graph_rag", "scenario_parameters"})
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
        self.eval_engine = EvalEngine(EvalConfig(enable_llm_eval=True))
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
        return [hybrid_status, pdf_status, graph_status, scenario_status]

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
    ) -> Dict[str, Any]:
        """Evaluate scene with unified 3-dimension scores using road-metrics EvalEngine.

        Args:
            layout_path: Path to scene_layout.json
            image_path: Optional legacy path to rendered preview image
            rendered_views: Rendered scene views as data URLs for visual LLM evaluation
            knowledge_source: Knowledge source to use (pdf_rag, graph_rag, hybrid, or none)

        Returns:
            Dict with walkability, safety, beauty (0-100), overall (0-100), evaluation, suggestions
        """
        layout = Path(layout_path).expanduser().resolve()
        if not layout.exists():
            raise RuntimeError(f"Layout file not found: {layout}")
        
        payload = json.loads(layout.read_text(encoding="utf-8"))
        
        # Use EvalEngine from road-metrics submodule
        result = self.eval_engine.evaluate(
            payload,
            rendered_views=list(rendered_views or []),
            image_path=image_path,
        )
        safety_available = self._llm_report_available(result.safety)
        beauty_available = self._llm_report_available(result.beauty)
        visual_scores_available = safety_available and beauty_available
        
        # Convert to Web API format (0-100 scale)
        return {
            "walkability": int(result.walkability.walkability_index * 100),
            "safety": int(result.safety.final_score * 100) if safety_available else None,
            "beauty": int(result.beauty.final_score * 100) if beauty_available else None,
            "overall": int(result.evaluation_score * 100) if visual_scores_available else None,
            "score_weights": self._score_weights_payload(),
            "score_formula": self._score_formula_text(),
            "evaluation": self._generate_evaluation_text(result),
            "suggestions": self._generate_suggestions(result),
            "indicators": self._extract_indicators(result),
            "config_patch": self._generate_config_patch(result),
            "llm_status": self._extract_llm_status(result),
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

    def _score_weights_payload(self) -> Dict[str, float]:
        """Return the active unified-evaluation aggregation weights."""
        aggregation = getattr(getattr(self.eval_engine, "config", None), "aggregation", None)
        return {
            "walkability": float(getattr(aggregation, "walkability_weight", 0.45)),
            "safety": float(getattr(aggregation, "safety_weight", 0.35)),
            "beauty": float(getattr(aggregation, "beauty_weight", 0.20)),
        }

    def _score_formula_text(self) -> str:
        weights = self._score_weights_payload()
        return (
            "overall = "
            f"walkability {weights['walkability']:.2f} + "
            f"safety {weights['safety']:.2f} + "
            f"beauty {weights['beauty']:.2f}"
        )

    def _generate_evaluation_text(self, result) -> str:
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
        parts.append(f"评分公式: {self._score_formula_text()}")
        
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
            "vehicle_throughput_compliance": "Pass" if w.transit_prox > 0.3 else "Fail",
            "rule_satisfaction": (
                round(result.evaluation_score, 2)
                if self._llm_report_available(s) and self._llm_report_available(b)
                else None
            ),
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
