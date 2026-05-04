"""Structured scenario-to-parameter triples for RAG grounding."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .pdf_rag import KnowledgeChunk, KnowledgeSearchHit

_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u3400-\u9FFF]+", flags=re.IGNORECASE)
_ID_RE = re.compile(r"[^a-z0-9]+")
_FEET_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)")

MATRIX_SOURCE_DOC = "Complete-Streets-Design-Handbook-2024"
MATRIX_SECTION = "1.1.2 DESIGN TREATMENT SUITABILITY MATRIX"
SCENARIO_PARAMETER_SOURCE = "scenario_parameter_triples"

STREET_TYPE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("street_type.high_volume_pedestrian", "High-Volume Pedestrian"),
    ("street_type.civic_ceremonial_street", "Civic/Ceremonial Street"),
    ("street_type.walkable_commercial_corridor", "Walkable Commercial Corridor"),
    ("street_type.urban_arterial", "Urban Arterial"),
    ("street_type.auto_oriented_commercial_industrial", "Auto-Oriented Commercial/Industrial"),
    ("street_type.park_road", "Park Road"),
    ("street_type.scenic_drive", "Scenic Drive"),
    ("street_type.city_neighborhood", "City Neighborhood"),
    ("street_type.low_density_residential", "Low-Density Residential"),
    ("street_type.shared_narrow", "Shared Narrow"),
    ("street_type.local", "Local"),
)

MATRIX_PARAMETER_ROWS: tuple[tuple[str, str, str], ...] = (
    ("4.3.1", "Sidewalk Width", "sidewalk_width_m"),
    ("4.3.2", "Walking Zone Width", "walking_zone_width_m"),
    ("4.4.1", "Building Zone Width", "building_zone_width_m"),
    ("4.4.2", "Furnishing Zone Width", "furnishing_zone_width_m"),
)


@dataclass(frozen=True)
class ScenarioParameterTriple:
    scenario_id: str
    scenario_label: str
    parameter_name: str
    raw_value: Any
    normalized_value: Any
    unit: str
    source_doc: str
    section: str
    chunk_id: str
    confidence: float
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ScenarioParameterTriple":
        return cls(
            scenario_id=str(payload.get("scenario_id", "") or ""),
            scenario_label=str(payload.get("scenario_label", "") or ""),
            parameter_name=str(payload.get("parameter_name", "") or ""),
            raw_value=payload.get("raw_value"),
            normalized_value=payload.get("normalized_value"),
            unit=str(payload.get("unit", "") or ""),
            source_doc=str(payload.get("source_doc", "") or ""),
            section=str(payload.get("section", "") or ""),
            chunk_id=str(payload.get("chunk_id", "") or ""),
            confidence=float(payload.get("confidence", 0.0) or 0.0),
            notes=str(payload.get("notes", "") or ""),
        )


def stable_sort_triples(triples: Iterable[ScenarioParameterTriple]) -> list[ScenarioParameterTriple]:
    return sorted(
        triples,
        key=lambda item: (
            item.source_doc,
            item.scenario_id,
            item.parameter_name,
            str(item.raw_value),
            item.chunk_id,
        ),
    )


def read_triples_jsonl(path: str | Path) -> list[ScenarioParameterTriple]:
    triples_path = Path(path).expanduser().resolve()
    if not triples_path.exists():
        return []
    rows: list[ScenarioParameterTriple] = []
    with triples_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(ScenarioParameterTriple.from_dict(json.loads(line)))
    return rows


def write_triples_jsonl(path: str | Path, triples: Sequence[ScenarioParameterTriple]) -> dict[str, Any]:
    triples_path = Path(path).expanduser().resolve()
    triples_path.parent.mkdir(parents=True, exist_ok=True)
    rows = stable_sort_triples(triples)
    seen: set[str] = set()
    with triples_path.open("w", encoding="utf-8") as handle:
        for item in rows:
            if item.chunk_id in seen:
                raise ValueError(f"Duplicate scenario parameter chunk_id: {item.chunk_id}")
            seen.add(item.chunk_id)
            handle.write(json.dumps(item.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n")
    fingerprint = _sha256_bytes(triples_path.read_bytes())
    return {
        "version": "scenario_parameter_triples_v1",
        "triple_count": len(rows),
        "fingerprint": fingerprint,
        "path": str(triples_path),
    }


def write_triples_metadata(
    path: str | Path,
    *,
    triples_path: str | Path,
    triples: Sequence[ScenarioParameterTriple],
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    metadata_path = Path(path).expanduser().resolve()
    triples_file = Path(triples_path).expanduser().resolve()
    sources: dict[str, int] = {}
    for item in triples:
        sources[item.source_doc] = sources.get(item.source_doc, 0) + 1
    payload: dict[str, Any] = {
        "version": "scenario_parameter_triples_v1",
        "triple_count": len(triples),
        "fingerprint": _sha256_bytes(triples_file.read_bytes()) if triples_file.exists() else "",
        "triples_path": str(triples_file),
        "sources": sources,
    }
    payload.update(dict(extra or {}))
    metadata_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def parse_suitability_matrix_triples(
    text: str,
    *,
    source_path: str | Path,
) -> list[ScenarioParameterTriple]:
    lines = [_clean_line(line) for line in str(text or "").splitlines()]
    lines = [line for line in lines if line]
    triples: list[ScenarioParameterTriple] = []
    for section_id, label, parameter_name in MATRIX_PARAMETER_ROWS:
        values = _extract_matrix_row_values(lines, label=label)
        if len(values) == 1 and _is_no_minimum(values[0]):
            values = values * len(STREET_TYPE_COLUMNS)
        if len(values) != len(STREET_TYPE_COLUMNS):
            raise ValueError(
                f"Expected {len(STREET_TYPE_COLUMNS)} values for {label}, got {len(values)}: {values!r}"
            )
        for (scenario_id, scenario_label), raw_value in zip(STREET_TYPE_COLUMNS, values):
            normalized_value, unit, notes = _normalize_matrix_value(raw_value)
            triples.append(
                ScenarioParameterTriple(
                    scenario_id=scenario_id,
                    scenario_label=scenario_label,
                    parameter_name=parameter_name,
                    raw_value=raw_value,
                    normalized_value=normalized_value,
                    unit=unit,
                    source_doc=MATRIX_SOURCE_DOC,
                    section=f"{MATRIX_SECTION} / {section_id} {label}",
                    chunk_id=_chunk_id("matrix", scenario_id, parameter_name),
                    confidence=0.95,
                    notes=notes,
                )
            )
    return stable_sort_triples(triples)


def build_preset_triples(presets: Sequence[Mapping[str, Any]]) -> list[ScenarioParameterTriple]:
    triples: list[ScenarioParameterTriple] = []
    for preset in presets:
        preset_id = str(preset.get("id", "") or "").strip()
        if not preset_id:
            continue
        scenario_id = f"preset.{_slug(preset_id)}"
        scenario_label = str(preset.get("nameEn") or preset.get("name") or preset_id).strip()
        config_patch = dict(preset.get("configPatch") or {})
        for parameter_name, raw_value in config_patch.items():
            normalized_value, unit = _normalize_preset_value(str(parameter_name), raw_value)
            triples.append(
                ScenarioParameterTriple(
                    scenario_id=scenario_id,
                    scenario_label=scenario_label,
                    parameter_name=str(parameter_name),
                    raw_value=raw_value,
                    normalized_value=normalized_value,
                    unit=unit,
                    source_doc="roadgen3d.presets.SCENE_PRESETS",
                    section=preset_id,
                    chunk_id=_chunk_id("preset", scenario_id, str(parameter_name)),
                    confidence=1.0,
                    notes=str(preset.get("description", "") or ""),
                )
            )
    return stable_sort_triples(triples)


class ScenarioParameterTripleStore:
    """JSONL-backed lexical retriever for scenario parameter triples."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self._triples: list[ScenarioParameterTriple] | None = None

    def load(self) -> list[ScenarioParameterTriple]:
        if self._triples is None:
            self._triples = read_triples_jsonl(self.path)
        return self._triples

    def artifact_fingerprint(self) -> str:
        if not self.path.exists():
            return ""
        return _sha256_bytes(self.path.read_bytes())

    def search(
        self,
        query: str,
        *,
        topk: int = 8,
        parameter_names: Sequence[str] | None = None,
    ) -> list[KnowledgeSearchHit]:
        query_text = str(query or "").strip()
        if not query_text:
            return []
        allowed_parameters = {str(item) for item in (parameter_names or ()) if str(item).strip()}
        query_tokens = _tokenize(query_text)
        scored: list[tuple[float, ScenarioParameterTriple]] = []
        for triple in self.load():
            if allowed_parameters and triple.parameter_name not in allowed_parameters:
                continue
            raw_score = _lexical_score(query_text, query_tokens, triple)
            if raw_score <= 0:
                continue
            scored.append((raw_score * max(0.1, float(triple.confidence)), triple))
        if not scored:
            return []
        scored.sort(key=lambda item: (item[0], item[1].confidence, item[1].chunk_id), reverse=True)
        max_score = max(scored[0][0], 1.0)
        hits: list[KnowledgeSearchHit] = []
        for raw_score, triple in scored[: max(1, int(topk))]:
            score = 0.70 + 0.27 * min(raw_score / max_score, 1.0)
            hits.append(triple_to_search_hit(triple, source_path=self.path, score=score))
        return hits


def triple_to_search_hit(
    triple: ScenarioParameterTriple,
    *,
    source_path: str | Path,
    score: float | None = None,
) -> KnowledgeSearchHit:
    text = json.dumps(triple.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return KnowledgeSearchHit(
        chunk=KnowledgeChunk(
            chunk_id=triple.chunk_id,
            doc_id=SCENARIO_PARAMETER_SOURCE,
            page_start=0,
            page_end=0,
            section_title=f"{triple.scenario_label} / {triple.parameter_name}",
            text=text,
            source_path=str(Path(source_path).expanduser().resolve()),
        ),
        score=float(score if score is not None else triple.confidence),
    )


def _extract_matrix_row_values(lines: Sequence[str], *, label: str) -> list[str]:
    try:
        label_index = next(index for index, line in enumerate(lines) if _clean_line(line).lower() == label.lower())
    except StopIteration as exc:
        raise ValueError(f"Could not find matrix row label: {label}") from exc
    values: list[str] = []
    for line in lines[label_index + 1 :]:
        if _looks_like_section_marker(line):
            break
        values.append(_clean_raw_value(line))
    return values


def _normalize_matrix_value(raw_value: str) -> tuple[Any, str, str]:
    value = _clean_raw_value(raw_value)
    if _is_no_minimum(value):
        return None, "", "No minimum requirement in source matrix."
    match = _FEET_RE.search(value)
    if not match:
        return value, "", "Unparsed matrix value retained as raw text."
    feet = float(match.group(1))
    meters = round(feet * 0.3048, 3)
    if not math.isfinite(meters):
        return value, "", "Unparsed matrix value retained as raw text."
    return meters, "m", "Converted from feet in source matrix."


def _normalize_preset_value(parameter_name: str, raw_value: Any) -> tuple[Any, str]:
    unit = ""
    if parameter_name.endswith("_width_m") or parameter_name.endswith("_m"):
        unit = "m"
    elif parameter_name in {"density", "building_density"}:
        unit = "ratio"
    elif parameter_name == "building_max_per_100m":
        unit = "count_per_100m"
    return raw_value, unit


def _lexical_score(query_text: str, query_tokens: Sequence[str], triple: ScenarioParameterTriple) -> float:
    haystack = " ".join(
        [
            triple.scenario_id.replace("_", " "),
            triple.scenario_label,
            triple.parameter_name.replace("_", " "),
            str(triple.raw_value),
            str(triple.normalized_value),
            triple.unit,
            triple.section,
            triple.notes,
        ]
    ).lower()
    normalized_query = query_text.lower()
    score = 0.0
    if normalized_query and normalized_query in haystack:
        score += 5.0
    for token in query_tokens:
        if token in haystack:
            score += 1.0
            if token in triple.parameter_name.lower().replace("_", " "):
                score += 1.3
            if token in triple.scenario_label.lower():
                score += 1.1
    if triple.parameter_name.lower() in normalized_query:
        score += 3.0
    return score


def _tokenize(text: str) -> list[str]:
    return [match.group(0).lower() for match in _TOKEN_RE.finditer(text)]


def _clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", str(line or "").replace("\u00a0", " ")).strip()


def _clean_raw_value(value: str) -> str:
    cleaned = _clean_line(value)
    return cleaned.replace("’", "'").replace("‘", "'")


def _looks_like_section_marker(line: str) -> bool:
    return bool(re.fullmatch(r"\d+\.\d+(?:\.\d+)?", _clean_line(line)))


def _is_no_minimum(value: str) -> bool:
    normalized = _clean_raw_value(value).lower().replace(".", "")
    return normalized in {"no minimum requirement", "no min"}


def _chunk_id(kind: str, scenario_id: str, parameter_name: str) -> str:
    return f"scenario_parameters::{kind}::{_slug(scenario_id)}::{_slug(parameter_name)}"


def _slug(value: str) -> str:
    normalized = _ID_RE.sub("_", str(value or "").lower()).strip("_")
    return normalized or "item"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
