"""GraphRAG artifact access for the RoadGen3D workbench.

This retriever prefers the official ``graphrag`` runtime when a quickstart
project is available, but it keeps the existing merged ``txt`` corpus as a
stable fallback. The workbench therefore gets three properties at once:

1. already-merged ``txt`` data is searchable immediately
2. official GraphRAG ``local_search`` / ``basic_search`` can be enabled
3. quickstart ``input`` / ``cache`` / ``output`` are reused until inputs or
   settings actually change
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .pdf_rag import KnowledgeChunk, KnowledgeSearchHit

_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u3400-\u9FFF]+", flags=re.IGNORECASE)
_BOOK_RE = re.compile(r"^\s*书名:\s*(.+?)\s*$", flags=re.MULTILINE)
_SECTION_RE = re.compile(r"^\s*章节:\s*(.+?)\s*$", flags=re.MULTILINE)
_RUNTIME_STATE_FILENAME = "roadgen3d_runtime_state.json"
_INPUT_MANIFEST_FILENAME = "roadgen3d_input_manifest.json"
_OFFICIAL_RESPONSE_TYPE = "Multiple paragraphs"


def _clean_text(value: object) -> str:
    return str(value or "").replace("\x00", " ").strip()


def _clean_doc_id(value: object) -> str:
    normalized = re.sub(r"[^a-z0-9_]+", "_", _clean_text(value).lower()).strip("_")
    return normalized or "graphrag"


def _tokenize(text: str) -> List[str]:
    return [match.group(0).lower() for match in _TOKEN_RE.finditer(str(text or ""))]


def _shorten(text: str, *, max_chars: int = 900) -> str:
    cleaned = _clean_text(text)
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 3].rstrip() + "..."


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", _clean_text(text))


def _infer_page_from_name(path: Path) -> int:
    match = re.match(r"^(\d+)", path.stem)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except ValueError:
        return 0


def _read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def _infer_txt_title(path: Path, text: str) -> str:
    section_match = _SECTION_RE.search(text)
    if section_match:
        return _clean_text(section_match.group(1))
    book_match = _BOOK_RE.search(text)
    if book_match:
        return _clean_text(book_match.group(1))
    return path.stem.replace("_", " ")


def _make_excerpt(text: str, *, query: str, tokens: Sequence[str], max_chars: int = 900) -> str:
    normalized = _clean_text(text)
    if len(normalized) <= max_chars:
        return normalized

    lowered = normalized.lower()
    positions = [lowered.find(token.lower()) for token in [query, *tokens] if str(token).strip()]
    positions = [position for position in positions if position >= 0]
    if not positions:
        return _shorten(normalized, max_chars=max_chars)

    start = max(0, min(positions) - max_chars // 4)
    end = min(len(normalized), start + max_chars)
    excerpt = normalized[start:end].strip()
    if start > 0:
        excerpt = "..." + excerpt
    if end < len(normalized):
        excerpt = excerpt.rstrip() + "..."
    return excerpt


def _lexical_score(query: str, *, title: str, text: str) -> float:
    normalized_query = _normalize_whitespace(query).lower()
    if not normalized_query:
        return 0.0

    normalized_title = _normalize_whitespace(title).lower()
    normalized_text = _normalize_whitespace(text).lower()
    haystack = f"{normalized_title}\n{normalized_text}"
    tokens = tuple(dict.fromkeys(token for token in _tokenize(normalized_query) if len(token) >= 2))
    if not tokens:
        return 0.0

    score = 0.0
    if normalized_query in normalized_title:
        score += 7.0
    if normalized_query in normalized_text:
        score += 4.5

    for token in tokens:
        title_count = normalized_title.count(token)
        text_count = normalized_text.count(token)
        if title_count:
            score += 2.2 + min(title_count, 3) * 0.35
        if text_count:
            score += 1.1 + min(text_count, 6) * 0.18

    if len(tokens) > 1:
        matched = sum(1 for token in tokens if token in haystack)
        score += matched / float(len(tokens)) * 3.0

    return score


def _coerce_findings_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = _clean_text(value)
        if text.startswith("[") or text.startswith("{"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return text
            return _coerce_findings_text(parsed)
        return text
    if isinstance(value, dict):
        summary = _clean_text(value.get("summary"))
        explanation = _clean_text(value.get("explanation"))
        return "\n".join(part for part in [summary, explanation] if part)
    if isinstance(value, Iterable):
        parts = [_coerce_findings_text(item) for item in value]
        return "\n\n".join(part for part in parts if part)
    return _clean_text(value)


def _load_pandas():
    try:
        import pandas as pd  # type: ignore
    except ImportError as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "GraphRAG parquet access requires `pandas` and a parquet backend such as `pyarrow`."
        ) from exc
    return pd


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stable_json_hash(payload: object) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return _sha256_bytes(encoded)


@contextlib.contextmanager
def _pushd(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


@dataclass(frozen=True)
class GraphRagSourceStatus:
    key: str
    label: str
    available: bool
    description: str
    artifact_count: int = 0
    item_count: int = 0
    project_dir: str = ""
    output_dir: str = ""
    txt_dir: str = ""
    input_dir: str = ""
    cache_dir: str = ""
    runtime_mode: str = "static_fallback"
    needs_rebuild: bool = False
    synced_input_count: int = 0
    last_build_status: str = ""
    runtime_error: str = ""
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _GraphRecord:
    chunk_id: str
    doc_id: str
    section_title: str
    text: str
    source_path: str
    page_start: int
    page_end: int
    search_title: str


class GraphRagKnowledgeRetriever:
    """Search GraphRAG artifacts with official runtime preference and txt fallback."""

    def __init__(
        self,
        *,
        project_dir: str | Path,
        output_dir: str | Path | None = None,
        txt_dir: str | Path | None = None,
        quickstart_dir: str | Path | None = None,
    ) -> None:
        self.project_dir = Path(project_dir).expanduser().resolve()
        self.quickstart_dir = (
            Path(quickstart_dir).expanduser().resolve()
            if quickstart_dir is not None
            else (self.project_dir / "graphrag_quickstart").resolve()
        )
        self.output_dir = (
            Path(output_dir).expanduser().resolve()
            if output_dir is not None
            else (self.quickstart_dir / "output").resolve()
        )
        self.txt_dir = (
            Path(txt_dir).expanduser().resolve()
            if txt_dir is not None
            else (self.project_dir / "graphrag_txt").resolve()
        )
        self.input_dir = (self.quickstart_dir / "input").resolve()
        self.cache_dir = (self.quickstart_dir / "cache").resolve()
        self.settings_path = (self.quickstart_dir / "settings.yaml").resolve()
        self.runtime_state_path = (self.cache_dir / _RUNTIME_STATE_FILENAME).resolve()
        self.input_manifest_path = (self.cache_dir / _INPUT_MANIFEST_FILENAME).resolve()
        self.root_env_path = (self.project_dir.parent.parent / ".env").resolve()

        self._records: List[_GraphRecord] | None = None
        self._runtime_tables: Dict[str, Any] | None = None
        self._status: GraphRagSourceStatus | None = None
        self._runtime_lock = threading.RLock()

    def describe(self) -> GraphRagSourceStatus:
        self._records = None
        try:
            records = self._load_records()
            source_entries = self._collect_source_entries()
            runtime_state = self._read_json_file(self.runtime_state_path)
            runtime_mode = "static_fallback"
            needs_rebuild = False
            if self._official_runtime_supported():
                needs_rebuild = self._needs_runtime_rebuild(source_entries)
                if self._runtime_query_mode() and not needs_rebuild:
                    runtime_mode = "official"

            artifact_count = len(source_entries) + sum(
                1
                for path in [
                    self.output_dir / "entities.parquet",
                    self.output_dir / "relationships.parquet",
                    self.output_dir / "communities.parquet",
                    self.output_dir / "community_reports.parquet",
                    self.output_dir / "text_units.parquet",
                    self.output_dir / "documents.parquet",
                ]
                if path.exists()
            )
            if (self.output_dir / "lancedb").exists():
                artifact_count += 1

            synced_input_count = 0
            if self.input_dir.exists():
                synced_input_count = len(list(self.input_dir.glob("*.txt")))

            self._status = GraphRagSourceStatus(
                key="graph_rag",
                label="GraphRAG",
                available=bool(records),
                description=(
                    "Prefer official GraphRAG runtime when quickstart artifacts are current; "
                    "otherwise fall back to merged txt/community artifacts."
                ),
                artifact_count=artifact_count,
                item_count=len(records),
                project_dir=str(self.project_dir),
                output_dir=str(self.output_dir),
                txt_dir=str(self.txt_dir),
                input_dir=str(self.input_dir),
                cache_dir=str(self.cache_dir),
                runtime_mode=runtime_mode,
                needs_rebuild=needs_rebuild,
                synced_input_count=synced_input_count,
                last_build_status=str(runtime_state.get("build_status") or ""),
                runtime_error=str(runtime_state.get("last_error") or ""),
            )
        except Exception as exc:
            self._status = GraphRagSourceStatus(
                key="graph_rag",
                label="GraphRAG",
                available=False,
                description=(
                    "Prefer official GraphRAG runtime when quickstart artifacts are current; "
                    "otherwise fall back to merged txt/community artifacts."
                ),
                project_dir=str(self.project_dir),
                output_dir=str(self.output_dir),
                txt_dir=str(self.txt_dir),
                input_dir=str(self.input_dir),
                cache_dir=str(self.cache_dir),
                error=str(exc),
            )
        return self._status

    def sync_input_corpus(self) -> Dict[str, Any]:
        with self._runtime_lock:
            source_entries = self._collect_source_entries()
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self.input_dir.mkdir(parents=True, exist_ok=True)
            self._assert_unique_target_names(source_entries)

            desired_names = {entry["target_name"] for entry in source_entries}
            removed = 0
            for target_path in sorted(self.input_dir.glob("*.txt")):
                if target_path.name in desired_names:
                    continue
                target_path.unlink()
                removed += 1

            updated = 0
            unchanged = 0
            for entry in source_entries:
                source_path = Path(entry["source_path"])
                target_path = self.input_dir / entry["target_name"]
                should_copy = True
                if target_path.exists():
                    existing_hash = _sha256_text(_read_text_file(target_path))
                    should_copy = existing_hash != entry["sha256"]
                if should_copy:
                    shutil.copyfile(source_path, target_path)
                    updated += 1
                else:
                    unchanged += 1

            manifest = {
                "source_dir": str(self.txt_dir),
                "input_dir": str(self.input_dir),
                "source_file_count": len(source_entries),
                "source_fingerprint": self._fingerprint_source_entries(source_entries),
                "files": [
                    {
                        "source_relpath": entry["source_relpath"],
                        "target_name": entry["target_name"],
                        "sha256": entry["sha256"],
                    }
                    for entry in source_entries
                ],
            }
            self.input_manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self._records = None
            self._status = None
            return {
                "source_file_count": len(source_entries),
                "copied_count": updated,
                "unchanged_count": unchanged,
                "removed_count": removed,
                "input_dir": str(self.input_dir),
                "manifest_path": str(self.input_manifest_path),
            }

    def ensure_runtime_artifacts(self, *, force: bool = False) -> Dict[str, Any]:
        with self._runtime_lock:
            return self._ensure_runtime_artifacts_locked(force=force)

    def search(self, query: str, *, topk: int = 5) -> List[KnowledgeSearchHit]:
        query_text = _clean_text(query)
        if not query_text:
            return []

        topk = max(1, int(topk))
        runtime_error: Exception | None = None
        try:
            runtime_hits = self._search_with_official_runtime(query_text, topk=topk)
            if runtime_hits:
                return runtime_hits[:topk]
        except Exception as exc:  # pragma: no cover - exercised only with full runtime env
            runtime_error = exc

        fallback_hits = self._search_records(query_text, topk=topk)
        if fallback_hits:
            return fallback_hits
        if runtime_error is not None:
            raise RuntimeError(f"GraphRAG runtime search failed: {runtime_error}") from runtime_error
        return []

    def _search_records(self, query_text: str, *, topk: int) -> List[KnowledgeSearchHit]:
        records = self._load_records()
        tokens = _tokenize(query_text)
        scored_rows: List[tuple[float, _GraphRecord]] = []
        for record in records:
            raw_score = _lexical_score(query_text, title=record.search_title, text=record.text)
            if raw_score <= 0.0:
                continue
            scored_rows.append((raw_score, record))

        if not scored_rows:
            return []

        scored_rows.sort(key=lambda item: item[0], reverse=True)
        max_score = max(scored_rows[0][0], 1.0)
        results: List[KnowledgeSearchHit] = []
        for raw_score, record in scored_rows[:topk]:
            normalized_score = 0.30 + 0.60 * min(raw_score / max_score, 1.0)
            excerpt = _make_excerpt(record.text, query=query_text, tokens=tokens)
            results.append(
                KnowledgeSearchHit(
                    chunk=KnowledgeChunk(
                        chunk_id=record.chunk_id,
                        doc_id=record.doc_id,
                        page_start=record.page_start,
                        page_end=record.page_end,
                        section_title=record.section_title,
                        text=excerpt,
                        source_path=record.source_path,
                    ),
                    score=float(normalized_score),
                )
            )
        return results

    def _search_with_official_runtime(self, query_text: str, *, topk: int) -> List[KnowledgeSearchHit]:
        runtime_modules = self._get_runtime_modules()
        if runtime_modules is None or not self.settings_path.exists():
            return []

        with self._runtime_lock:
            rebuild_info = self._ensure_runtime_artifacts_locked()
            runtime_state = self._read_json_file(self.runtime_state_path)
            if runtime_state.get("build_status") != "succeeded":
                return []
            query_mode = rebuild_info.get("query_mode") or self._runtime_query_mode()
            if query_mode not in {"local", "basic"}:
                return []

            api, load_config = runtime_modules
            self._load_runtime_env()
            tables = self._load_runtime_tables(refresh=bool(rebuild_info.get("rebuilt")))
            with _pushd(self.quickstart_dir):
                config = load_config(self.quickstart_dir)
                response, context_data = self._run_runtime_query_locked(
                    api=api,
                    config=config,
                    tables=tables,
                    query_text=query_text,
                    preferred_mode=str(query_mode),
                )
        return self._runtime_hits_from_response(
            query_text=query_text,
            response=response,
            context_data=context_data,
            topk=topk,
        )

    def _run_runtime_query_locked(
        self,
        *,
        api: Any,
        config: Any,
        tables: Mapping[str, Any],
        query_text: str,
        preferred_mode: str,
    ) -> tuple[Any, Any]:
        local_level = self._infer_community_level(tables.get("communities"))
        query_order = [preferred_mode]
        if preferred_mode == "local":
            query_order.append("basic")

        last_error: Exception | None = None
        for mode in query_order:
            try:
                if mode == "local" and self._has_local_runtime_tables(tables):
                    return self._run_async(
                        api.local_search(
                            config=config,
                            entities=tables["entities"],
                            communities=tables["communities"],
                            community_reports=tables["community_reports"],
                            text_units=tables["text_units"],
                            relationships=tables["relationships"],
                            covariates=tables.get("covariates"),
                            community_level=local_level,
                            response_type=_OFFICIAL_RESPONSE_TYPE,
                            query=query_text,
                        )
                    )
                if mode == "basic" and self._has_basic_runtime_tables(tables):
                    return self._run_async(
                        api.basic_search(
                            config=config,
                            text_units=tables["text_units"],
                            response_type=_OFFICIAL_RESPONSE_TYPE,
                            query=query_text,
                        )
                    )
            except Exception as exc:  # pragma: no cover - depends on runtime env/network
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("Official GraphRAG runtime artifacts are incomplete.")

    def _runtime_hits_from_response(
        self,
        *,
        query_text: str,
        response: Any,
        context_data: Any,
        topk: int,
    ) -> List[KnowledgeSearchHit]:
        hits: List[KnowledgeSearchHit] = []
        seen = set()

        response_text = self._coerce_runtime_response_text(response)
        if response_text:
            response_hit = KnowledgeSearchHit(
                chunk=KnowledgeChunk(
                    chunk_id=f"graphrag_runtime::response::{_sha256_text(query_text)[:16]}",
                    doc_id="graphrag_runtime",
                    page_start=0,
                    page_end=0,
                    section_title="GraphRAG Runtime Response",
                    text=_shorten(response_text, max_chars=1600),
                    source_path=str(self.settings_path),
                ),
                score=0.99,
            )
            hits.append(response_hit)
            seen.add(response_hit.chunk.chunk_id)

        context_records = self._coerce_runtime_context_records(context_data)
        for index, record in enumerate(context_records):
            if record.chunk_id in seen:
                continue
            seen.add(record.chunk_id)
            hits.append(
                KnowledgeSearchHit(
                    chunk=KnowledgeChunk(
                        chunk_id=record.chunk_id,
                        doc_id=record.doc_id,
                        page_start=record.page_start,
                        page_end=record.page_end,
                        section_title=record.section_title,
                        text=_make_excerpt(record.text, query=query_text, tokens=_tokenize(query_text), max_chars=1200),
                        source_path=record.source_path,
                    ),
                    score=max(0.55, 0.92 - index * 0.04),
                )
            )
            if len(hits) >= topk:
                break

        return hits[:topk]

    def _coerce_runtime_context_records(self, context_data: Any) -> List[_GraphRecord]:
        pd = _load_pandas()
        frames: List[tuple[str, Any]] = []
        if isinstance(context_data, Mapping):
            frames.extend((str(name or "context"), value) for name, value in context_data.items())
        elif isinstance(context_data, list):
            frames.extend((f"context_{index}", value) for index, value in enumerate(context_data))

        records: List[_GraphRecord] = []
        for table_name, value in frames:
            dataframe = None
            if hasattr(value, "to_dict") and hasattr(value, "columns"):
                dataframe = value
            elif isinstance(value, list) and value and isinstance(value[0], Mapping):
                dataframe = pd.DataFrame(value)
            if dataframe is None:
                continue
            for row in dataframe.to_dict(orient="records"):
                record = self._graph_record_from_runtime_row(table_name, row)
                if record is not None:
                    records.append(record)
        return records

    def _graph_record_from_runtime_row(self, table_name: str, row: Mapping[str, Any]) -> _GraphRecord | None:
        title = _clean_text(
            row.get("title")
            or row.get("name")
            or row.get("summary")
            or row.get("id")
            or table_name
        )
        text_parts = [
            _clean_text(row.get("text")),
            _clean_text(row.get("description")),
            _clean_text(row.get("summary")),
            _clean_text(row.get("explanation")),
            _coerce_findings_text(row.get("findings")),
            _shorten(_clean_text(row.get("full_content")), max_chars=2000),
        ]
        text = "\n\n".join(part for part in text_parts if part)
        if not text:
            return None
        identifier = _clean_text(row.get("id") or row.get("human_readable_id") or title)
        source_candidate = self.output_dir / f"{table_name}.parquet"
        source_path = source_candidate if source_candidate.exists() else self.settings_path
        document_id = _clean_text(row.get("document_id") or row.get("source") or table_name)
        return _GraphRecord(
            chunk_id=f"graphrag_runtime::{table_name}::{identifier}",
            doc_id=_clean_doc_id(document_id),
            section_title=title,
            text=text,
            source_path=str(source_path),
            page_start=0,
            page_end=0,
            search_title=f"{table_name} {title}",
        )

    def _coerce_runtime_response_text(self, response: Any) -> str:
        if response is None:
            return ""
        if isinstance(response, str):
            return _clean_text(response)
        if isinstance(response, Mapping):
            return _shorten(
                json.dumps(response, ensure_ascii=False, indent=2),
                max_chars=1800,
            )
        if isinstance(response, list):
            if response and isinstance(response[0], Mapping):
                return _shorten(
                    json.dumps(response, ensure_ascii=False, indent=2),
                    max_chars=1800,
                )
            return "\n".join(_clean_text(item) for item in response if _clean_text(item))
        return _clean_text(response)

    def _load_records(self) -> List[_GraphRecord]:
        if self._records is not None:
            return self._records

        records: List[_GraphRecord] = []
        records.extend(self._load_txt_records())
        if not records:
            records.extend(self._load_text_unit_records())
            records.extend(self._load_document_records())
        records.extend(self._load_community_report_records())
        if not records:
            raise RuntimeError(
                f"No GraphRAG artifacts were found under {self.project_dir}. "
                "Expected merged txt files and/or GraphRAG output parquet files."
            )
        self._records = records
        return self._records

    def _load_txt_records(self) -> List[_GraphRecord]:
        if not self.txt_dir.exists():
            return []
        rows: List[_GraphRecord] = []
        for path in sorted(self.txt_dir.rglob("*.txt")):
            text = _read_text_file(path)
            if not _clean_text(text):
                continue
            title = _infer_txt_title(path, text)
            parent_name = path.parent.name or path.parent.parent.name or "graphrag_txt"
            page_number = _infer_page_from_name(path)
            rows.append(
                _GraphRecord(
                    chunk_id=f"graphrag_txt::{path.relative_to(self.project_dir).as_posix()}",
                    doc_id=_clean_doc_id(parent_name),
                    section_title=title,
                    text=text,
                    source_path=str(path),
                    page_start=page_number,
                    page_end=page_number,
                    search_title=f"{parent_name} {title}",
                )
            )
        return rows

    def _load_community_report_records(self) -> List[_GraphRecord]:
        path = self.output_dir / "community_reports.parquet"
        if not path.exists():
            return []
        dataframe = self._read_parquet(path)
        rows: List[_GraphRecord] = []
        for row in dataframe.to_dict(orient="records"):
            title = _clean_text(row.get("title")) or "GraphRAG Community Report"
            summary = _clean_text(row.get("summary"))
            findings = _coerce_findings_text(row.get("findings"))
            full_content = _shorten(_clean_text(row.get("full_content")), max_chars=2200)
            text = "\n\n".join(part for part in [summary, findings, full_content] if part)
            if not text:
                continue
            rows.append(
                _GraphRecord(
                    chunk_id=f"graphrag_report::{_clean_text(row.get('id') or row.get('human_readable_id') or title)}",
                    doc_id="graphrag_community_report",
                    section_title=title,
                    text=text,
                    source_path=str(path),
                    page_start=0,
                    page_end=0,
                    search_title=title,
                )
            )
        return rows

    def _load_text_unit_records(self) -> List[_GraphRecord]:
        path = self.output_dir / "text_units.parquet"
        if not path.exists():
            return []
        dataframe = self._read_parquet(path)
        rows: List[_GraphRecord] = []
        for row in dataframe.to_dict(orient="records"):
            text = _clean_text(row.get("text"))
            if not text:
                continue
            document_id = _clean_text(row.get("document_id") or row.get("id"))
            rows.append(
                _GraphRecord(
                    chunk_id=f"graphrag_text_unit::{_clean_text(row.get('id') or document_id)}",
                    doc_id=_clean_doc_id(document_id),
                    section_title=document_id or "GraphRAG Text Unit",
                    text=text,
                    source_path=str(path),
                    page_start=0,
                    page_end=0,
                    search_title=document_id,
                )
            )
        return rows

    def _load_document_records(self) -> List[_GraphRecord]:
        path = self.output_dir / "documents.parquet"
        if not path.exists():
            return []
        dataframe = self._read_parquet(path)
        rows: List[_GraphRecord] = []
        for row in dataframe.to_dict(orient="records"):
            title = _clean_text(row.get("title")) or "GraphRAG Document"
            text = _clean_text(row.get("text"))
            if not text:
                continue
            rows.append(
                _GraphRecord(
                    chunk_id=f"graphrag_document::{_clean_text(row.get('id') or title)}",
                    doc_id="graphrag_document",
                    section_title=title,
                    text=text,
                    source_path=str(path),
                    page_start=0,
                    page_end=0,
                    search_title=title,
                )
            )
        return rows

    def _read_parquet(self, path: Path):
        pd = _load_pandas()
        try:
            return pd.read_parquet(path)
        except Exception as exc:  # pragma: no cover - passthrough for env-specific parquet issues
            raise RuntimeError(f"Failed to read GraphRAG parquet artifact: {path}") from exc

    def _collect_source_entries(self) -> List[Dict[str, Any]]:
        if not self.txt_dir.exists():
            return []
        entries: List[Dict[str, Any]] = []
        for path in sorted(self.txt_dir.rglob("*.txt")):
            text = _read_text_file(path)
            if not _clean_text(text):
                continue
            entries.append(
                {
                    "source_path": str(path),
                    "source_relpath": path.relative_to(self.txt_dir).as_posix(),
                    "target_name": path.name,
                    "sha256": _sha256_text(text),
                    "size_bytes": len(text.encode("utf-8")),
                }
            )
        return entries

    def _assert_unique_target_names(self, entries: Sequence[Mapping[str, Any]]) -> None:
        counts: Dict[str, int] = {}
        for entry in entries:
            name = str(entry.get("target_name") or "")
            counts[name] = counts.get(name, 0) + 1
        duplicates = [name for name, count in counts.items() if count > 1]
        if duplicates:
            raise RuntimeError(
                "GraphRAG input sync found duplicate txt basenames: "
                + ", ".join(sorted(duplicates))
            )

    def _fingerprint_source_entries(self, entries: Sequence[Mapping[str, Any]]) -> str:
        fingerprint_payload = [
            {
                "source_relpath": entry["source_relpath"],
                "target_name": entry["target_name"],
                "sha256": entry["sha256"],
            }
            for entry in entries
        ]
        return _stable_json_hash(fingerprint_payload)

    def _settings_fingerprint(self) -> str:
        if not self.settings_path.exists():
            return ""
        return _sha256_bytes(self.settings_path.read_bytes())

    def _read_json_file(self, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if isinstance(payload, dict):
            return payload
        return {}

    def _needs_runtime_rebuild(self, source_entries: Sequence[Mapping[str, Any]]) -> bool:
        if not self._official_runtime_supported():
            return False
        if not self.settings_path.exists():
            return False
        if not source_entries:
            return False
        if not self._runtime_query_mode():
            return True

        state = self._read_json_file(self.runtime_state_path)
        if state.get("build_status") != "succeeded":
            return True
        input_fingerprint = self._fingerprint_source_entries(source_entries)
        settings_fingerprint = self._settings_fingerprint()
        if state.get("input_fingerprint") != input_fingerprint:
            return True
        if state.get("settings_fingerprint") != settings_fingerprint:
            return True
        return False

    def _should_attempt_runtime_build(
        self,
        source_entries: Sequence[Mapping[str, Any]],
        *,
        force: bool = False,
    ) -> bool:
        if force:
            return True
        state = self._read_json_file(self.runtime_state_path)
        if not state:
            return True
        input_fingerprint = self._fingerprint_source_entries(source_entries)
        settings_fingerprint = self._settings_fingerprint()
        if state.get("input_fingerprint") != input_fingerprint:
            return True
        if state.get("settings_fingerprint") != settings_fingerprint:
            return True
        return state.get("build_status") != "failed"

    def _ensure_runtime_artifacts_locked(self, *, force: bool = False) -> Dict[str, Any]:
        sync_info = self.sync_input_corpus()
        source_entries = self._collect_source_entries()
        runtime_state = self._read_json_file(self.runtime_state_path)
        result = {
            "synced": True,
            "sync": sync_info,
            "rebuilt": False,
            "query_mode": self._runtime_query_mode(),
            "build_status": runtime_state.get("build_status", ""),
            "last_error": runtime_state.get("last_error", ""),
        }
        if not self._official_runtime_supported() or not self.settings_path.exists():
            return result
        if not source_entries:
            return result
        if not self._needs_runtime_rebuild(source_entries):
            return result
        if not self._should_attempt_runtime_build(source_entries, force=force):
            result["skipped_due_to_previous_failure"] = True
            return result

        api, load_config = self._get_runtime_modules() or (None, None)
        if api is None or load_config is None:
            return result

        self._load_runtime_env()
        self._write_runtime_state(
            source_entries,
            build_status="running",
            query_mode=self._runtime_query_mode(),
            last_error="",
        )
        result["build_status"] = "running"
        try:
            with _pushd(self.quickstart_dir):
                config = load_config(self.quickstart_dir)
                outputs = self._run_async(api.build_index(config=config))
        except Exception as exc:
            error_message = str(exc)
            self._write_runtime_state(
                source_entries,
                build_status="failed",
                query_mode=self._runtime_query_mode(),
                last_error=error_message,
            )
            raise

        failed_workflows = [
            str(getattr(item, "workflow", "unknown"))
            for item in outputs or []
            if getattr(item, "error", None) is not None
        ]
        if failed_workflows:
            error_message = (
                "Official GraphRAG build did not finish cleanly. Failed workflows: "
                + ", ".join(failed_workflows)
            )
            self._write_runtime_state(
                source_entries,
                build_status="failed",
                query_mode=self._runtime_query_mode(),
                last_error=error_message,
            )
            raise RuntimeError(error_message)

        self._write_runtime_state(
            source_entries,
            build_status="succeeded",
            query_mode=self._runtime_query_mode(),
            last_error="",
        )
        self._runtime_tables = None
        self._records = None
        self._status = None
        result["rebuilt"] = True
        result["build_status"] = "succeeded"
        result["last_error"] = ""
        result["query_mode"] = self._runtime_query_mode()
        return result

    def _write_runtime_state(
        self,
        source_entries: Sequence[Mapping[str, Any]],
        *,
        build_status: str,
        query_mode: str | None,
        last_error: str,
    ) -> None:
        state_payload = {
            "build_status": build_status,
            "input_fingerprint": self._fingerprint_source_entries(source_entries),
            "settings_fingerprint": self._settings_fingerprint(),
            "source_file_count": len(source_entries),
            "query_mode": query_mode or "",
            "last_error": last_error,
        }
        self.runtime_state_path.write_text(
            json.dumps(state_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._status = None

    def _runtime_query_mode(self) -> str | None:
        if self._has_local_runtime_artifacts():
            return "local"
        if self._has_basic_runtime_artifacts():
            return "basic"
        return None

    def _has_basic_runtime_artifacts(self) -> bool:
        return (self.output_dir / "text_units.parquet").exists() and (self.output_dir / "lancedb").exists()

    def _has_local_runtime_artifacts(self) -> bool:
        return all(
            path.exists()
            for path in [
                self.output_dir / "entities.parquet",
                self.output_dir / "relationships.parquet",
                self.output_dir / "communities.parquet",
                self.output_dir / "community_reports.parquet",
                self.output_dir / "text_units.parquet",
                self.output_dir / "lancedb",
            ]
        )

    def _has_basic_runtime_tables(self, tables: Mapping[str, Any]) -> bool:
        return tables.get("text_units") is not None and (self.output_dir / "lancedb").exists()

    def _has_local_runtime_tables(self, tables: Mapping[str, Any]) -> bool:
        required_keys = ("entities", "relationships", "communities", "community_reports", "text_units")
        return all(tables.get(key) is not None for key in required_keys) and (self.output_dir / "lancedb").exists()

    def _load_runtime_tables(self, *, refresh: bool = False) -> Dict[str, Any]:
        if self._runtime_tables is not None and not refresh:
            return self._runtime_tables
        tables: Dict[str, Any] = {}
        for name in [
            "entities",
            "relationships",
            "communities",
            "community_reports",
            "text_units",
            "documents",
            "covariates",
        ]:
            path = self.output_dir / f"{name}.parquet"
            if path.exists():
                tables[name] = self._read_parquet(path)
            else:
                tables[name] = None
        self._runtime_tables = tables
        return tables

    def _infer_community_level(self, communities: Any) -> int:
        if communities is None:
            return 0
        try:
            if "level" not in communities.columns:
                return 0
            level = int(communities["level"].max())
            return max(level, 0)
        except Exception:
            return 0

    def _load_runtime_env(self) -> None:
        for path in [self.root_env_path, self.quickstart_dir / ".env"]:
            if not path.exists():
                continue
            for key, value in self._parse_env_file(path).items():
                if key.startswith("GRAPHRAG_") and value:
                    os.environ[key] = value

    def _parse_env_file(self, path: Path) -> Dict[str, str]:
        values: Dict[str, str] = {}
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("export "):
                stripped = stripped[len("export ") :].strip()
            if "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                values[key] = value
        return values

    def _get_runtime_modules(self):
        try:
            import graphrag.api as api  # type: ignore
            from graphrag.config.load_config import load_config  # type: ignore
        except ImportError:
            return None
        return api, load_config

    def _official_runtime_supported(self) -> bool:
        return self.settings_path.exists() and self._get_runtime_modules() is not None

    def _run_async(self, coroutine):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)

        if not loop.is_running():
            return loop.run_until_complete(coroutine)

        result: Dict[str, Any] = {}
        error: Dict[str, BaseException] = {}

        def _runner() -> None:
            try:
                result["value"] = asyncio.run(coroutine)
            except BaseException as exc:  # pragma: no cover - thread fallback
                error["value"] = exc

        thread = threading.Thread(target=_runner, daemon=True)
        thread.start()
        thread.join()
        if "value" in error:
            raise error["value"]
        return result.get("value")
