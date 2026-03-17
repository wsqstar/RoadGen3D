"""Declarative street design rule profiles for the neuralsymbolic pipeline."""

from __future__ import annotations

from typing import Dict, Iterable, Tuple

from .types import ConstraintSet, DesignRuleSpec

_BALANCED_RULES: Tuple[DesignRuleSpec, ...] = (
    DesignRuleSpec(
        name="max_lane_count",
        description="Keep the template street within a compact complete-street lane count.",
        target="lane_count",
        mode="hard",
        operator="<=",
        value=2,
    ),
    DesignRuleSpec(
        name="min_clear_path_width",
        description="Maintain a usable pedestrian clear path on both sides.",
        target="band_min_width",
        mode="hard",
        operator=">=",
        value=2.2,
        parameters={"band_kind": "clear_path"},
    ),
    DesignRuleSpec(
        name="min_furnishing_width",
        description="Reserve a furnishing strip so assets do not spill into the clear path.",
        target="band_min_width",
        mode="hard",
        operator=">=",
        value=0.9,
        parameters={"band_kind": "furnishing"},
    ),
    DesignRuleSpec(
        name="max_furnishing_width",
        description="Keep furnishing strips within a moderate width budget so the row does not collapse into a single dominant edge.",
        target="band_max_width",
        mode="hard",
        operator="<=",
        value=2.4,
        parameters={"band_kind": "furnishing"},
    ),
    DesignRuleSpec(
        name="row_width_budget",
        description="Keep the full street row within the available right-of-way budget.",
        target="total_row_width_budget",
        mode="hard",
        operator="<=",
        value=True,
        parameters={"source": "program_row_width"},
    ),
    DesignRuleSpec(
        name="pedestrian_throughput_floor",
        description="The clear path must support baseline pedestrian throughput.",
        target="mode_throughput_min",
        mode="hard",
        operator=">=",
        value=1.0,
        parameters={"mode": "ped_clear_path"},
    ),
    DesignRuleSpec(
        name="vehicle_throughput_floor",
        description="The carriageway must support baseline vehicle throughput.",
        target="mode_throughput_min",
        mode="hard",
        operator=">=",
        value=1.0,
        parameters={"mode": "vehicle_carriageway"},
    ),
    DesignRuleSpec(
        name="left_clear_path_adjacent_furnishing",
        description="The left clear path must remain directly adjacent to the furnishing edge.",
        target="adjacency_required",
        mode="hard",
        operator="adjacent",
        value=True,
        parameters={"band_name": "left_clear_path", "adjacent_to": "left_furnishing"},
    ),
    DesignRuleSpec(
        name="left_clear_path_separates_furnishing_and_carriageway",
        description="The left clear path must separate furnishing from the carriageway.",
        target="separation_required",
        mode="hard",
        operator="between",
        value=True,
        parameters={"left": "left_furnishing", "right": "carriageway", "separator": "left_clear_path"},
    ),
    DesignRuleSpec(
        name="bench_entrance_keepout",
        description="Avoid placing benches directly inside entrance clear zones.",
        target="keepout_radius",
        mode="hard",
        operator=">=",
        value=1.8,
        parameters={"category": "bench", "poi_type": "entrance"},
    ),
    DesignRuleSpec(
        name="balanced_bench_cap",
        description="Balanced streets should keep optional bench density moderate.",
        target="slot_count_max",
        mode="soft",
        operator="<=",
        value=3,
        parameters={"category": "bench"},
    ),
    DesignRuleSpec(
        name="furniture_buffer_allocation",
        description="Street furniture should occupy furnishing or transit-edge bands, not the pedestrian clear path.",
        target="category_allowed_band",
        mode="hard",
        operator="in",
        value=("furnishing", "transit_edge"),
        parameters={"category": "all"},
    ),
)

_PEDESTRIAN_RULES: Tuple[DesignRuleSpec, ...] = (
    DesignRuleSpec(
        name="max_lane_count",
        description="Pedestrian-priority streets should not expand the carriageway beyond two lanes.",
        target="lane_count",
        mode="hard",
        operator="<=",
        value=2,
    ),
    DesignRuleSpec(
        name="wide_clear_path",
        description="Pedestrian-priority streets must widen the clear path.",
        target="band_min_width",
        mode="hard",
        operator=">=",
        value=3.0,
        parameters={"band_kind": "clear_path"},
    ),
    DesignRuleSpec(
        name="wide_furnishing_strip",
        description="Street furniture needs a dedicated furnishing strip on pedestrian-priority streets.",
        target="band_min_width",
        mode="hard",
        operator=">=",
        value=1.2,
        parameters={"band_kind": "furnishing"},
    ),
    DesignRuleSpec(
        name="pedestrian_row_width_budget",
        description="Pedestrian-priority streets still stay within the available right-of-way budget.",
        target="total_row_width_budget",
        mode="hard",
        operator="<=",
        value=True,
        parameters={"source": "program_row_width"},
    ),
    DesignRuleSpec(
        name="pedestrian_throughput_floor",
        description="Pedestrian-priority streets must support elevated pedestrian throughput.",
        target="mode_throughput_min",
        mode="hard",
        operator=">=",
        value=1.15,
        parameters={"mode": "ped_clear_path"},
    ),
    DesignRuleSpec(
        name="min_tree_count",
        description="Pedestrian-priority streets require a minimum tree cadence.",
        target="slot_count_min",
        mode="hard",
        operator=">=",
        value=3,
        parameters={"category": "tree"},
    ),
    DesignRuleSpec(
        name="min_bench_count",
        description="Pedestrian-priority streets should offer places to stop and stay.",
        target="slot_count_min",
        mode="hard",
        operator=">=",
        value=2,
        parameters={"category": "bench"},
    ),
    DesignRuleSpec(
        name="tree_entrance_keepout",
        description="Trees should not block entrances on pedestrian-priority streets.",
        target="keepout_radius",
        mode="hard",
        operator=">=",
        value=2.2,
        parameters={"category": "tree", "poi_type": "entrance"},
    ),
    DesignRuleSpec(
        name="pedestrian_clear_band",
        description="All placeable street furniture should remain in furnishing bands on pedestrian-priority streets.",
        target="category_allowed_band",
        mode="hard",
        operator="in",
        value=("furnishing",),
        parameters={"category": "all"},
    ),
)

_TRANSIT_RULES: Tuple[DesignRuleSpec, ...] = (
    DesignRuleSpec(
        name="min_lane_count",
        description="Transit-priority streets keep at least two travel lanes.",
        target="lane_count",
        mode="hard",
        operator=">=",
        value=2,
    ),
    DesignRuleSpec(
        name="min_clear_path_width",
        description="Transit-priority streets still preserve a clear pedestrian path.",
        target="band_min_width",
        mode="hard",
        operator=">=",
        value=2.5,
        parameters={"band_kind": "clear_path"},
    ),
    DesignRuleSpec(
        name="min_transit_edge_width",
        description="Reserve a wider right-side edge for transit operations.",
        target="band_min_width",
        mode="hard",
        operator=">=",
        value=1.6,
        parameters={"band_kind": "transit_edge"},
    ),
    DesignRuleSpec(
        name="transit_row_width_budget",
        description="Transit-priority streets stay within the available right-of-way budget.",
        target="total_row_width_budget",
        mode="hard",
        operator="<=",
        value=True,
        parameters={"source": "program_row_width"},
    ),
    DesignRuleSpec(
        name="transit_throughput_floor",
        description="Transit-priority streets reserve enough width for transit-edge operations.",
        target="mode_throughput_min",
        mode="hard",
        operator=">=",
        value=1.0,
        parameters={"mode": "transit_edge"},
    ),
    DesignRuleSpec(
        name="transit_edge_separated_from_carriageway",
        description="The clear path must separate the transit edge from the carriageway.",
        target="separation_required",
        mode="hard",
        operator="between",
        value=True,
        parameters={"left": "carriageway", "right": "right_transit_edge", "separator": "right_clear_path"},
    ),
    DesignRuleSpec(
        name="min_bus_stop_count",
        description="Transit-priority streets require at least one bus stop when the asset inventory supports it.",
        target="slot_count_min",
        mode="hard",
        operator=">=",
        value=1,
        parameters={"category": "bus_stop"},
    ),
    DesignRuleSpec(
        name="bus_stop_inventory",
        description="If the inventory lacks a bus stop, surface the substitution decision explicitly.",
        target="required_category_available",
        mode="hard",
        operator="present",
        value=True,
        parameters={"category": "bus_stop", "substitute_categories": ("lamp", "bench")},
    ),
    DesignRuleSpec(
        name="bus_stop_transit_edge_only",
        description="Bus stops must remain in the transit edge.",
        target="category_allowed_band",
        mode="hard",
        operator="in",
        value=("transit_edge",),
        parameters={"category": "bus_stop"},
    ),
    DesignRuleSpec(
        name="transit_edge_reserved",
        description="Reserve the transit edge for bus-stop-aligned infrastructure first.",
        target="reserved_band_category",
        mode="hard",
        operator="=",
        value="bus_stop",
        parameters={"band_kind": "transit_edge"},
    ),
    DesignRuleSpec(
        name="bench_out_of_transit_edge",
        description="Benches should not block the transit edge on transit-priority streets.",
        target="category_allowed_band",
        mode="hard",
        operator="in",
        value=("furnishing",),
        parameters={"category": "bench"},
    ),
    DesignRuleSpec(
        name="bench_bus_stop_keepout",
        description="Keep benches out of the immediate bus-stop stopping envelope.",
        target="keepout_radius",
        mode="hard",
        operator=">=",
        value=3.0,
        parameters={"category": "bench", "poi_type": "bus_stop"},
    ),
)

_NOISE_AWARE_RULES: Tuple[DesignRuleSpec, ...] = (
    DesignRuleSpec(
        name="max_lane_count",
        description="Keep the template street within a compact complete-street lane count.",
        target="lane_count",
        mode="hard",
        operator="<=",
        value=2,
    ),
    DesignRuleSpec(
        name="min_clear_path_width",
        description="Maintain a usable pedestrian clear path on both sides.",
        target="band_min_width",
        mode="hard",
        operator=">=",
        value=2.2,
        parameters={"band_kind": "clear_path"},
    ),
    DesignRuleSpec(
        name="wide_furnishing_strip",
        description="Reserve a wide furnishing strip for noise-shielding assets (trees, bollards).",
        target="band_min_width",
        mode="hard",
        operator=">=",
        value=1.2,
        parameters={"band_kind": "furnishing"},
    ),
    DesignRuleSpec(
        name="noise_row_width_budget",
        description="Noise-aware streets still stay within the available right-of-way budget.",
        target="total_row_width_budget",
        mode="hard",
        operator="<=",
        value=True,
        parameters={"source": "program_row_width"},
    ),
    DesignRuleSpec(
        name="min_tree_count",
        description="Noise-aware streets require trees for canopy-based noise shielding.",
        target="slot_count_min",
        mode="hard",
        operator=">=",
        value=2,
        parameters={"category": "tree"},
    ),
    DesignRuleSpec(
        name="furniture_buffer_allocation",
        description="Street furniture should occupy furnishing or transit-edge bands, not the pedestrian clear path.",
        target="category_allowed_band",
        mode="hard",
        operator="in",
        value=("furnishing", "transit_edge"),
        parameters={"category": "all"},
    ),
    DesignRuleSpec(
        name="entrance_openness",
        description="At least 60% angular openness must be maintained within 4 m of each entrance.",
        target="entrance_openness_threshold",
        mode="soft",
        operator=">=",
        value=0.6,
        parameters={"radius_m": 4.0},
    ),
    DesignRuleSpec(
        name="noise_shielding",
        description="At least 30% of detection rays from entrances toward the carriageway should be intercepted by shielding assets.",
        target="noise_shielding_threshold",
        mode="soft",
        operator=">=",
        value=0.3,
        parameters={"ray_count": 7, "fan_half_angle_deg": 30.0},
    ),
)

_CONSTRAINT_SETS: Dict[str, ConstraintSet] = {
    "balanced_complete_street_v1": ConstraintSet(
        name="balanced_complete_street_v1",
        description="Balanced complete street with dedicated furnishing strips and moderate amenity density.",
        rules=_BALANCED_RULES,
    ),
    "pedestrian_priority_v1": ConstraintSet(
        name="pedestrian_priority_v1",
        description="Pedestrian-priority profile with wider clear paths and denser amenity placement.",
        rules=_PEDESTRIAN_RULES,
    ),
    "transit_priority_v1": ConstraintSet(
        name="transit_priority_v1",
        description="Transit-priority profile with a reserved transit edge and explicit substitution reporting.",
        rules=_TRANSIT_RULES,
    ),
    "noise_aware_v1": ConstraintSet(
        name="noise_aware_v1",
        description="Noise-aware profile with entrance openness and carriageway noise-shielding rules.",
        rules=_NOISE_AWARE_RULES,
    ),
}


def list_constraint_profiles() -> Tuple[str, ...]:
    """Return the known design-rule profiles."""

    return tuple(sorted(_CONSTRAINT_SETS.keys()))


def load_constraint_set(name: str = "balanced_complete_street_v1") -> ConstraintSet:
    """Load a named declarative design-rule profile."""

    key = str(name).strip().lower() or "balanced_complete_street_v1"
    constraint_set = _CONSTRAINT_SETS.get(key)
    if constraint_set is None:
        raise ValueError(f"Unknown design rule profile: {name!r}. Available: {list_constraint_profiles()}")
    return constraint_set


def extend_constraint_set(base: ConstraintSet, extra_rules: Iterable[DesignRuleSpec]) -> ConstraintSet:
    """Create a new constraint set by appending extra rules to an existing profile."""

    extra_rules_tuple = tuple(extra_rules)
    rules = tuple(base.rules) + extra_rules_tuple
    return ConstraintSet(
        name=f"{base.name}_extended",
        description=f"{base.description} + {len(extra_rules_tuple)} extra rule(s)",
        rules=rules,
    )
