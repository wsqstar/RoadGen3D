"""Feature builders for learned street-layout policy (M4)."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Set

import numpy as np

DEFAULT_POLICY_INPUT_DIM = 32
_POLICY_CATEGORIES: Sequence[str] = (
    "bench",
    "lamp",
    "trash",
    "tree",
    "bus_stop",
    "mailbox",
    "hydrant",
    "bollard",
)
_POLICY_CATEGORY_TO_INDEX = {name: idx for idx, name in enumerate(_POLICY_CATEGORIES)}


@dataclass(frozen=True)
class PolicyFeatureContext:
    """Context for one slot selection decision."""

    query: str
    category: str
    slot_idx: int
    slot_x: float
    slot_z: float
    length_m: float
    road_width_m: float
    sidewalk_width_m: float
    lane_count: int
    density: float
    topk: int
    used_asset_ids: Set[str]


@dataclass(frozen=True)
class CandidateDescriptor:
    """One candidate item for policy scoring."""

    asset_id: str
    category: str
    score: float


def _hash_to_unit_values(text: str, size: int) -> List[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values: List[float] = []
    cursor = 0
    while len(values) < size:
        if cursor + 4 > len(digest):
            digest = hashlib.sha256(digest).digest()
            cursor = 0
        chunk = digest[cursor : cursor + 4]
        cursor += 4
        as_int = int.from_bytes(chunk, byteorder="big", signed=False)
        values.append((as_int / 4294967295.0) * 2.0 - 1.0)
    return values


def build_candidate_feature(
    context: PolicyFeatureContext,
    candidate: CandidateDescriptor,
    candidate_rank: int,
    candidate_count: int,
) -> np.ndarray:
    """Build fixed-size 32-d feature vector for one candidate option."""
    lane_norm = min(max(float(context.lane_count), 1.0), 8.0) / 8.0
    density_norm = min(max(float(context.density), 0.1), 3.0) / 3.0
    rank_norm = float(candidate_rank) / max(float(candidate_count - 1), 1.0)
    topk_norm = min(float(context.topk), 64.0) / 64.0
    used_flag = 1.0 if candidate.asset_id in context.used_asset_ids else 0.0
    score = float(candidate.score)

    slot_x_norm = float(context.slot_x) / max(float(context.length_m), 1e-6)
    lateral_extent = max(float(context.road_width_m) + 2.0 * float(context.sidewalk_width_m), 1e-6)
    slot_z_norm = float(context.slot_z) / lateral_extent

    periodic = [
        math.sin(float(context.slot_idx) / 3.0),
        math.cos(float(context.slot_idx) / 3.0),
        math.sin(float(context.slot_idx) / 7.0),
        math.cos(float(context.slot_idx) / 7.0),
    ]

    numeric_block = [
        slot_x_norm,
        slot_z_norm,
        min(float(context.length_m), 400.0) / 400.0,
        min(float(context.road_width_m), 20.0) / 20.0,
        min(float(context.sidewalk_width_m), 10.0) / 10.0,
        lane_norm,
        density_norm,
        topk_norm,
    ]
    candidate_block = [
        score,
        max(min(score, 1.0), -1.0),
        rank_norm,
        1.0 - rank_norm,
        used_flag,
        1.0 if candidate.category == context.category else 0.0,
        float(candidate_count) / 64.0,
        1.0,
    ]

    hash_block = _hash_to_unit_values(
        f"{context.query}|{context.category}|{candidate.asset_id}|{candidate.category}",
        size=4,
    )

    category_one_hot = [0.0] * len(_POLICY_CATEGORIES)
    cat_idx = _POLICY_CATEGORY_TO_INDEX.get(context.category, None)
    if cat_idx is not None:
        category_one_hot[cat_idx] = 1.0

    feature = np.asarray(
        numeric_block + candidate_block + periodic + hash_block + category_one_hot,
        dtype=np.float32,
    )
    if feature.shape[0] != DEFAULT_POLICY_INPUT_DIM:
        raise RuntimeError(f"Unexpected feature dimension: {feature.shape[0]}")
    return feature


def vectorize_slot_candidates(
    context: PolicyFeatureContext,
    candidates: Iterable[CandidateDescriptor],
) -> np.ndarray:
    """Create [N, 32] feature matrix for one slot candidate list."""
    candidate_list = list(candidates)
    features = [
        build_candidate_feature(
            context=context,
            candidate=candidate,
            candidate_rank=idx,
            candidate_count=len(candidate_list),
        )
        for idx, candidate in enumerate(candidate_list)
    ]
    if not features:
        return np.zeros((0, DEFAULT_POLICY_INPUT_DIM), dtype=np.float32)
    return np.vstack(features).astype(np.float32)
