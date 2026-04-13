"""Lightweight registry for multiple PDF/GraphRAG knowledge sources."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Sequence

_REGISTRY_PATH = Path(__file__).resolve().parents[3] / "data" / "knowledge_sources.json"
_UPLOAD_DIR = Path(__file__).resolve().parents[3] / "data" / "knowledge_uploads"


@dataclass
class KnowledgeSourceRecord:
    source_id: str
    label: str
    source_type: str  # "pdf_rag" | "graph_rag"
    pdf_path: str | None = None
    artifact_dir: str | None = None
    graphrag_project_dir: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "label": self.label,
            "type": self.source_type,
            "pdf_path": self.pdf_path,
            "artifact_dir": self.artifact_dir,
            "graphrag_project_dir": self.graphrag_project_dir,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "KnowledgeSourceRecord":
        return cls(
            source_id=str(data.get("source_id", "") or ""),
            label=str(data.get("label", "") or ""),
            source_type=str(data.get("type", data.get("source_type", "pdf_rag")) or "pdf_rag"),
            pdf_path=str(data.get("pdf_path")) if data.get("pdf_path") else None,
            artifact_dir=str(data.get("artifact_dir")) if data.get("artifact_dir") else None,
            graphrag_project_dir=str(data.get("graphrag_project_dir")) if data.get("graphrag_project_dir") else None,
        )


def _ensure_registry() -> Path:
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _REGISTRY_PATH.exists():
        _REGISTRY_PATH.write_text("[]", encoding="utf-8")
    return _REGISTRY_PATH


def list_sources() -> List[KnowledgeSourceRecord]:
    path = _ensure_registry()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    return [KnowledgeSourceRecord.from_dict(item) for item in payload if isinstance(item, dict)]


def get_source(source_id: str) -> KnowledgeSourceRecord | None:
    for source in list_sources():
        if source.source_id == source_id:
            return source
    return None


def add_source(record: KnowledgeSourceRecord) -> KnowledgeSourceRecord:
    sources = list_sources()
    sources = [s for s in sources if s.source_id != record.source_id]
    sources.append(record)
    _save_sources(sources)
    return record


def remove_source(source_id: str) -> bool:
    sources = list_sources()
    before = len(sources)
    sources = [s for s in sources if s.source_id != source_id]
    if len(sources) == before:
        return False
    _save_sources(sources)
    return True


def _save_sources(sources: Sequence[KnowledgeSourceRecord]) -> None:
    path = _ensure_registry()
    path.write_text(
        json.dumps([s.to_dict() for s in sources], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def allocate_upload_paths(label: str) -> tuple[str, Path, Path]:
    """Return (source_id, pdf_path, artifact_dir) for a new upload."""
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    source_id = f"custom_{uuid.uuid4().hex[:12]}"
    pdf_path = _UPLOAD_DIR / f"{source_id}.pdf"
    artifact_dir = _UPLOAD_DIR / f"{source_id}_artifacts"
    return source_id, pdf_path, artifact_dir
