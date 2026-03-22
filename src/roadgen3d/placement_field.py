"""Unified placement-field configuration and scoring utilities."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

from .poi_taxonomy import canonicalize_poi_type, nonempty_poi_points
from .street_priors import DEFAULT_SPACING_M

CONFIG_PATH = (Path(__file__).resolve().parent / "config" / "placement_field_v1.json").resolve()
REQUIRED_TOP_LEVEL_KEYS = (
    "version",
    "cell_size_m",
    "poi_attraction_weights",
    "poi_attraction_sigma_m",
    "pair_relations",
    "placement_priority",
)


@dataclass(frozen=True)
class CandidateEnergy:
    """Resolved placement-field components for one candidate pose."""

    anchor_affinity: float
    poi_attraction: float
    poi_repulsion: float
    pair_attraction: float
    pair_repulsion: float
    band_deviation_penalty: float
    total_energy: float


class UniformSpatialHash:
    """Small spatial hash used to avoid full-scene overlap and neighbor scans."""

    def __init__(self, cell_size_m: float = 4.0) -> None:
        self.cell_size_m = max(float(cell_size_m), 0.5)
        self._cells: Dict[Tuple[int, int], List[int]] = {}

    def _cell_id(self, x: float, z: float) -> Tuple[int, int]:
        return (
            int(math.floor(float(x) / self.cell_size_m)),
            int(math.floor(float(z) / self.cell_size_m)),
        )

    def _bbox_cells(self, bbox_xz: Sequence[float]) -> Iterable[Tuple[int, int]]:
        xmin, xmax, zmin, zmax = [float(value) for value in bbox_xz]
        min_ix, min_iz = self._cell_id(xmin, zmin)
        max_ix, max_iz = self._cell_id(xmax, zmax)
        for ix in range(min_ix, max_ix + 1):
            for iz in range(min_iz, max_iz + 1):
                yield (ix, iz)

    def insert(self, bbox_xz: Sequence[float], index: int) -> None:
        for cell_id in self._bbox_cells(bbox_xz):
            self._cells.setdefault(cell_id, []).append(int(index))

    def query_bbox(self, bbox_xz: Sequence[float]) -> Tuple[int, ...]:
        hits = set()
        for cell_id in self._bbox_cells(bbox_xz):
            hits.update(self._cells.get(cell_id, ()))
        return tuple(sorted(hits))

    def query_radius(self, point_xz: Tuple[float, float], radius_m: float) -> Tuple[int, ...]:
        px, pz = float(point_xz[0]), float(point_xz[1])
        radius = max(float(radius_m), 0.0)
        bbox = (px - radius, px + radius, pz - radius, pz + radius)
        return self.query_bbox(bbox)


def _normalized_pair_key(left: str, right: str) -> str:
    values = sorted((str(left).strip().lower(), str(right).strip().lower()))
    return f"{values[0]}::{values[1]}"


def _validate_config(payload: MutableMapping[str, object]) -> Dict[str, object]:
    missing = [key for key in REQUIRED_TOP_LEVEL_KEYS if key not in payload]
    if missing:
        raise ValueError(f"Invalid placement field config; missing keys: {', '.join(missing)}")
    if not isinstance(payload["poi_attraction_weights"], dict):
        raise ValueError("Invalid placement field config; poi_attraction_weights must be a mapping")
    if not isinstance(payload["poi_attraction_sigma_m"], dict):
        raise ValueError("Invalid placement field config; poi_attraction_sigma_m must be a mapping")
    if not isinstance(payload["pair_relations"], dict):
        raise ValueError("Invalid placement field config; pair_relations must be a mapping")
    pair_relations = payload["pair_relations"]
    for key in ("same_category", "generic_other", "special_pairs"):
        if key not in pair_relations:
            raise ValueError(f"Invalid placement field config; pair_relations.{key} is required")
    if not isinstance(payload["placement_priority"], list) or not payload["placement_priority"]:
        raise ValueError("Invalid placement field config; placement_priority must be a non-empty list")
    return dict(payload)


@lru_cache(maxsize=8)
def load_placement_field_config(config_path: str | None = None) -> Dict[str, object]:
    path = Path(config_path).resolve() if config_path is not None else CONFIG_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _validate_config(payload)


def placement_field_path() -> Path:
    return CONFIG_PATH


def placement_priority_rank(anchor_poi_type: str, spec: Mapping[str, object] | None = None) -> int:
    config = spec or load_placement_field_config()
    canonical = canonicalize_poi_type(anchor_poi_type)
    priority = [canonicalize_poi_type(str(item)) for item in config["placement_priority"]]
    try:
        return int(priority.index(canonical))
    except ValueError:
        return int(len(priority))


def poi_attraction_sigma_m(poi_type: str, spec: Mapping[str, object] | None = None) -> float:
    config = spec or load_placement_field_config()
    sigma_map = config["poi_attraction_sigma_m"]
    canonical = canonicalize_poi_type(poi_type)
    if canonical in sigma_map:
        return float(sigma_map[canonical])
    return float(sigma_map.get("default", 5.0))


def poi_attraction_score(
    category: str,
    position_xz: Tuple[float, float],
    poi_points_by_type: Mapping[str, Sequence[Tuple[float, float]]],
    *,
    spec: Mapping[str, object] | None = None,
    cutoff_m: float | None = None,
) -> float:
    config = spec or load_placement_field_config()
    weights_map = config["poi_attraction_weights"]
    category_weights = weights_map.get(str(category).strip().lower(), {})
    px, pz = float(position_xz[0]), float(position_xz[1])
    cutoff = float(cutoff_m) if cutoff_m is not None else None
    total = 0.0
    normalized_points = nonempty_poi_points(poi_points_by_type)
    for poi_type, weight in category_weights.items():
        sigma = poi_attraction_sigma_m(str(poi_type), config)
        for qx, qz in normalized_points.get(canonicalize_poi_type(str(poi_type)), ()):
            dist = math.hypot(px - float(qx), pz - float(qz))
            if cutoff is not None and dist > cutoff:
                continue
            total += float(weight) * math.exp(-(dist * dist) / (2.0 * sigma * sigma))
    return float(total)


def pair_relation(
    left_category: str,
    right_category: str,
    *,
    spec: Mapping[str, object] | None = None,
) -> Dict[str, float]:
    config = spec or load_placement_field_config()
    pair_relations = config["pair_relations"]
    left = str(left_category).strip().lower()
    right = str(right_category).strip().lower()
    if left == right:
        relation = dict(pair_relations["same_category"])
        relation["target_distance_m"] = float(
            relation.pop("target_distance_multiplier", 0.65)
        ) * float(DEFAULT_SPACING_M.get(left, 12.0))
        return {
            "near_repulsion_weight": float(relation["near_repulsion_weight"]),
            "near_sigma_m": float(relation["near_sigma_m"]),
            "far_attraction_weight": float(relation["far_attraction_weight"]),
            "target_distance_m": float(relation["target_distance_m"]),
            "far_sigma_m": float(relation["far_sigma_m"]),
        }
    key = _normalized_pair_key(left, right)
    special_pairs = pair_relations["special_pairs"]
    if key in special_pairs:
        relation = dict(special_pairs[key])
    else:
        relation = dict(pair_relations["generic_other"])
    return {
        "near_repulsion_weight": float(relation["near_repulsion_weight"]),
        "near_sigma_m": float(relation["near_sigma_m"]),
        "far_attraction_weight": float(relation["far_attraction_weight"]),
        "target_distance_m": float(relation["target_distance_m"]),
        "far_sigma_m": float(relation["far_sigma_m"]),
    }


def pair_cutoff_radius_m(
    left_category: str,
    right_category: str,
    *,
    spec: Mapping[str, object] | None = None,
) -> float:
    relation = pair_relation(left_category, right_category, spec=spec)
    return float(
        max(
            3.0 * relation["near_sigma_m"],
            relation["target_distance_m"] + 3.0 * relation["far_sigma_m"],
        )
    )


def pair_interaction_scores(
    left_category: str,
    position_xz: Tuple[float, float],
    right_category: str,
    other_position_xz: Tuple[float, float],
    *,
    spec: Mapping[str, object] | None = None,
) -> Tuple[float, float]:
    relation = pair_relation(left_category, right_category, spec=spec)
    dist = math.hypot(
        float(position_xz[0]) - float(other_position_xz[0]),
        float(position_xz[1]) - float(other_position_xz[1]),
    )
    repulsion = float(relation["near_repulsion_weight"]) * math.exp(
        -(dist * dist) / (2.0 * max(float(relation["near_sigma_m"]), 1e-6) ** 2)
    )
    if float(relation["far_attraction_weight"]) <= 0.0 or float(relation["far_sigma_m"]) <= 0.0:
        return 0.0, float(repulsion)
    target = float(relation["target_distance_m"])
    sigma = max(float(relation["far_sigma_m"]), 1e-6)
    attraction = float(relation["far_attraction_weight"]) * math.exp(
        -((dist - target) ** 2) / (2.0 * sigma * sigma)
    )
    return float(attraction), float(repulsion)


def compose_candidate_energy(
    *,
    anchor_distance_m: float | None,
    poi_attraction: float,
    poi_repulsion: float,
    pair_attraction: float,
    pair_repulsion: float,
    band_deviation_penalty: float,
) -> CandidateEnergy:
    anchor_affinity = 0.0
    if anchor_distance_m is not None and float(anchor_distance_m) >= 0.0:
        anchor_affinity = math.exp(-((float(anchor_distance_m) ** 2) / (2.0 * 1.5 * 1.5)))
    if anchor_distance_m is not None:
        total = (
            2.5 * anchor_affinity
            + 1.5 * float(poi_attraction)
            + 0.8 * float(pair_attraction)
            - 1.8 * float(poi_repulsion)
            - 1.2 * float(pair_repulsion)
            - 0.6 * float(band_deviation_penalty)
        )
    else:
        total = (
            1.5 * float(poi_attraction)
            + 0.8 * float(pair_attraction)
            - 1.8 * float(poi_repulsion)
            - 1.2 * float(pair_repulsion)
            - 0.6 * float(band_deviation_penalty)
        )
    return CandidateEnergy(
        anchor_affinity=float(anchor_affinity),
        poi_attraction=float(poi_attraction),
        poi_repulsion=float(poi_repulsion),
        pair_attraction=float(pair_attraction),
        pair_repulsion=float(pair_repulsion),
        band_deviation_penalty=float(band_deviation_penalty),
        total_energy=float(total),
    )
