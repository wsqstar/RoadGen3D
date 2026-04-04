"""JSON patch operations on scene_layout.json for LLM-driven layout editing."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, List, Sequence, Tuple


def apply_scene_patch(layout: Dict[str, Any], patch: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """Apply a structured patch to a scene layout and return (new_layout, changelog).

    *layout* is **not** mutated; a deep copy is returned.
    """
    layout = copy.deepcopy(layout)
    changelog: List[str] = []

    # Remove placements
    remove_ids = patch.get("remove_placements") or []
    if remove_ids:
        layout, removed = apply_placements_remove(layout, remove_ids)
        changelog.append(f"Removed {len(removed)} placement(s): {', '.join(removed)}")

    # Add placements
    additions = patch.get("add_placements") or []
    if additions:
        layout, added_ids = apply_placements_add(layout, additions)
        changelog.append(f"Added {len(added_ids)} placement(s): {', '.join(added_ids)}")

    # Resize bands
    resize_ops = patch.get("resize_bands") or []
    if resize_ops:
        for op in resize_ops:
            band_name = str(op.get("band_name", ""))
            new_width = float(op.get("width_m", 0))
            if band_name and new_width > 0:
                layout = apply_band_resize(layout, band_name, new_width)
                changelog.append(f"Resized band '{band_name}' to {new_width:.2f}m")

    # Batch add along street
    batch_adds = patch.get("batch_add_along_street") or []
    if batch_adds:
        for op in batch_adds:
            layout, added = batch_add_along_street(layout, **_coerce_batch_add_kwargs(op))
            changelog.append(f"Batch-added {len(added)} '{op.get('category', '?')}' along {op.get('side', '?')} side")

    # Adjust sub-lanes
    lane_ops = patch.get("adjust_sub_lanes") or []
    if lane_ops:
        for op in lane_ops:
            layout = adjust_sub_lanes(layout, op)
            changelog.append(
                f"Adjusted sub-lanes: side={op.get('side', '?')}, "
                f"width_m={op.get('width_m', '?')}"
            )

    return layout, changelog


def apply_placements_add(
    layout: Dict[str, Any],
    additions: Sequence[Dict[str, Any]],
) -> Tuple[Dict[str, Any], List[str]]:
    """Add new placement entries to the layout.

    Each addition dict should have:
      - category: str (e.g. "tree", "bench")
      - position_xyz: [x, y, z]
      - yaw_deg: float (default 0)
      - scale: float (default 1.0)
      - asset_id: optional, auto-generated if not provided
    """
    placements: List[Dict[str, Any]] = list(layout.get("placements") or [])
    added_ids: List[str] = []

    for addition in additions:
        category = str(addition.get("category", "unknown"))
        pos = addition.get("position_xyz", [0.0, 0.0, 0.0])
        if not isinstance(pos, (list, tuple)) or len(pos) < 3:
            pos = [0.0, 0.0, 0.0]
        pos = [float(pos[0]), float(pos[1]), float(pos[2])]

        instance_id = _generate_instance_id(layout)
        asset_id = str(addition.get("asset_id", "") or f"{category}_llm_edit")

        placement: Dict[str, Any] = {
            "instance_id": instance_id,
            "asset_id": asset_id,
            "category": category,
            "score": 1.0,
            "position_xyz": pos,
            "yaw_deg": float(addition.get("yaw_deg", 0.0) or 0.0),
            "scale": float(addition.get("scale", 1.0) or 1.0),
            "bbox_xz": [],
            "selection_source": "llm_layout_edit",
            "constraint_penalty": 0.0,
            "feasibility_score": 1.0,
            "violated_rules": [],
        }
        placements.append(placement)
        added_ids.append(instance_id)

    layout["placements"] = placements
    return layout, added_ids


def apply_placements_remove(
    layout: Dict[str, Any],
    instance_ids: Sequence[str],
) -> Tuple[Dict[str, Any], List[str]]:
    """Remove placements by instance_id. Returns (layout, removed_ids)."""
    ids_to_remove = set(str(i) for i in instance_ids)
    placements: List[Dict[str, Any]] = list(layout.get("placements") or [])
    removed: List[str] = []
    kept: List[Dict[str, Any]] = []
    for p in placements:
        pid = str(p.get("instance_id", ""))
        if pid in ids_to_remove:
            removed.append(pid)
        else:
            kept.append(p)
    layout["placements"] = kept
    return layout, removed


def apply_band_resize(
    layout: Dict[str, Any],
    band_name: str,
    new_width: float,
) -> Dict[str, Any]:
    """Resize a band in both config.bands and street_program.bands.

    Adjusts z_center_m of the target band and shifts adjacent bands
    to maintain ordering.
    """
    new_width = max(0.1, float(new_width))

    for band_container_key in ("program_generation", "street_program"):
        container = layout.get(band_container_key, {})
        program = container.get("program") if band_container_key == "program_generation" else container
        if not isinstance(program, dict):
            continue
        bands = list(program.get("bands") or [])
        _resize_bands_list(bands, band_name, new_width)
        program["bands"] = bands

    # Also update top-level config if applicable
    config = layout.get("config", {})
    if isinstance(config, dict):
        if band_name == "carriageway":
            config["road_width_m"] = new_width
        elif "clear_path" in band_name:
            config["sidewalk_width_m"] = new_width
        elif "furnishing" in band_name:
            config["furnishing_width_m"] = new_width

    return layout


def _resize_bands_list(bands: List[Dict[str, Any]], band_name: str, new_width: float) -> None:
    """Resize a specific band in a bands list, adjusting z_center_m of the band
    and shifting adjacent bands on the same side."""
    target_idx = None
    old_width = None
    for i, band in enumerate(bands):
        if str(band.get("name", "")) == band_name:
            target_idx = i
            old_width = float(band.get("width_m", 0))
            band["width_m"] = new_width
            break

    if target_idx is None or old_width is None or old_width == 0:
        return

    delta = new_width - old_width
    target_band = bands[target_idx]
    target_side = str(target_band.get("side", "center"))

    if target_side == "center":
        # Shifting center band affects everything
        for band in bands:
            if str(band.get("side", "")) == "left":
                band["z_center_m"] = float(band.get("z_center_m", 0)) + delta / 2.0
            elif str(band.get("side", "")) == "right":
                band["z_center_m"] = float(band.get("z_center_m", 0)) - delta / 2.0
    elif target_side in ("left", "right"):
        sign = 1.0 if target_side == "left" else -1.0
        # Bands further from center (larger abs z_center) on the same side
        # need to shift outward by delta
        target_z = abs(float(target_band.get("z_center_m", 0)))
        for band in bands:
            if str(band.get("side", "")) == target_side:
                if abs(float(band.get("z_center_m", 0))) > target_z:
                    band["z_center_m"] = float(band.get("z_center_m", 0)) + sign * delta


def _generate_instance_id(layout: Dict[str, Any]) -> str:
    """Generate a unique instance_id like 'inst_0042'."""
    placements = layout.get("placements") or []
    existing_ids = {str(p.get("instance_id", "")) for p in placements}
    counter = len(placements) + 1
    while True:
        candidate = f"inst_{counter:04d}"
        if candidate not in existing_ids:
            return candidate
        counter += 1


def build_layout_summary(layout: Dict[str, Any]) -> str:
    """Build a human-readable summary of the layout for LLM context."""
    lines: List[str] = []

    config = layout.get("config") or {}
    lines.append("=== Scene Layout Summary ===")
    lines.append(f"Road length: {config.get('length_m', '?')}m")
    lines.append(f"Road width: {config.get('road_width_m', '?')}m")
    lines.append(f"Sidewalk width: {config.get('sidewalk_width_m', '?')}m")
    lines.append(f"Lane count: {config.get('lane_count', '?')}")

    # Bands
    bands = []
    sp = layout.get("street_program") or {}
    program = sp if sp.get("bands") else {}
    band_list = program.get("bands") or []
    if not band_list:
        pg = layout.get("program_generation") or {}
        prog = pg.get("program") or {}
        band_list = prog.get("bands") or []
    if band_list:
        lines.append("")
        lines.append("Bands:")
        for band in band_list:
            name = band.get("name", "?")
            width = band.get("width_m", "?")
            z_center = band.get("z_center_m", "?")
            allowed = band.get("allowed_categories") or []
            lines.append(f"  {name}: width={width}m, z_center={z_center}m, allowed={allowed}")

    # Placement counts by category
    placements = layout.get("placements") or []
    cat_counts: Dict[str, int] = {}
    for p in placements:
        cat = str(p.get("category", "unknown"))
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    lines.append("")
    lines.append(f"Total placements: {len(placements)}")
    if cat_counts:
        lines.append("By category:")
        for cat, count in sorted(cat_counts.items()):
            lines.append(f"  {cat}: {count}")

    # Placement details (first 30 to keep context manageable)
    lines.append("")
    lines.append("Placement details (first 30):")
    for p in placements[:30]:
        pid = p.get("instance_id", "?")
        cat = p.get("category", "?")
        pos = p.get("position_xyz", [0, 0, 0])
        yaw = p.get("yaw_deg", 0)
        lines.append(f"  {pid}: {cat} at ({pos[0]:.1f}, {pos[1]:.1f}, {pos[2]:.1f}), yaw={yaw:.0f}")

    if len(placements) > 30:
        lines.append(f"  ... and {len(placements) - 30} more")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Batch helpers
# ---------------------------------------------------------------------------

def _get_band_list(layout: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return the bands list from whichever container holds it."""
    sp = layout.get("street_program") or {}
    if sp.get("bands"):
        return list(sp["bands"])
    pg = layout.get("program_generation") or {}
    prog = pg.get("program") or {}
    if prog.get("bands"):
        return list(prog["bands"])
    return []


def _get_street_length(layout: Dict[str, Any]) -> float:
    """Return the street length in metres from config."""
    config = layout.get("config") or {}
    return float(config.get("length_m", 100))


def batch_add_along_street(
    layout: Dict[str, Any],
    *,
    category: str,
    side: str = "left",
    band_name: str = "",
    spacing_m: float = 8.0,
    count: int = 0,
    yaw_deg: float = 0.0,
    scale: float = 1.0,
) -> Tuple[Dict[str, Any], List[str]]:
    """Add *count* placements of *category* evenly spaced along the street.

    Reads the band's ``z_center_m`` to determine the lateral (Z) position.
    If *count* is 0, it is derived from street length / spacing.
    """
    layout = copy.deepcopy(layout)
    length_m = _get_street_length(layout)
    bands = _get_band_list(layout)

    # Find z_center from band matching name or side
    z_center = 0.0
    if band_name:
        for b in bands:
            if str(b.get("name", "")) == band_name:
                z_center = float(b.get("z_center_m", 0))
                break
    else:
        # Pick the first furnishing band on the requested side
        for b in bands:
            b_side = str(b.get("side", ""))
            b_kind = str(b.get("kind", ""))
            if b_side == side and "furnishing" in b_kind:
                z_center = float(b.get("z_center_m", 0))
                break

    if count <= 0:
        count = max(1, int(length_m / max(spacing_m, 0.1)))

    # Margin from edges
    margin = spacing_m * 0.5
    if count == 1:
        positions = [length_m / 2.0]
    else:
        step = (length_m - 2 * margin) / max(count - 1, 1)
        positions = [margin + step * i for i in range(count)]

    additions = []
    for x in positions:
        additions.append({
            "category": category,
            "position_xyz": [round(x, 3), 0.0, round(z_center, 3)],
            "yaw_deg": yaw_deg,
            "scale": scale,
        })

    return apply_placements_add(layout, additions)


def adjust_sub_lanes(
    layout: Dict[str, Any],
    adjustment: Dict[str, Any],
) -> Dict[str, Any]:
    """Adjust lane count / carriageway width from a sub-lane adjustment dict.

    *adjustment* should contain:
      - ``side`` (str): currently informational
      - ``width_m`` (float): desired total carriageway width
      - ``lane_count`` (int, optional): update lane count

    This widens/narrows the carriageway band and shifts adjacent bands.
    """
    layout = copy.deepcopy(layout)
    new_width = float(adjustment.get("width_m", 0))
    if new_width <= 0:
        return layout

    layout = apply_band_resize(layout, "carriageway", new_width)

    # Optionally update lane count in config
    lane_count = adjustment.get("lane_count")
    if lane_count is not None:
        config = layout.get("config", {})
        if isinstance(config, dict):
            config["lane_count"] = int(lane_count)

    return layout


def _coerce_batch_add_kwargs(op: Dict[str, Any]) -> Dict[str, Any]:
    """Extract and coerce keyword arguments for batch_add_along_street."""
    return {
        "category": str(op.get("category", "tree")),
        "side": str(op.get("side", "left")),
        "band_name": str(op.get("band_name", "")),
        "spacing_m": float(op.get("spacing_m", 8.0) or 8.0),
        "count": int(op.get("count", 0) or 0),
        "yaw_deg": float(op.get("yaw_deg", 0.0) or 0.0),
        "scale": float(op.get("scale", 1.0) or 1.0),
    }
