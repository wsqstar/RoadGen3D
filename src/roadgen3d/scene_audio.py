"""Analyze a scene layout and produce an ambient audio profile."""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, MutableMapping, Sequence


def analyze_scene_audio(layout_payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Derive an audio profile from scene layout data.

    Returns a dict with:
      - ambient: {traffic, nature, urban, transit} volumes in [0, 1]
      - point_sources: list of positional sound emitters
    """
    summary = dict(layout_payload.get("summary", {}) or {})
    placements = list(layout_payload.get("placements", []) or [])
    config = dict(layout_payload.get("config", {}) or {})

    length_m = float(config.get("length_m", summary.get("length_m", 80.0)) or 80.0)
    road_width_m = float(config.get("road_width_m", summary.get("road_width_m", 8.0)) or 8.0)
    lane_count = int(config.get("lane_count", summary.get("lane_count", 2)) or 2)
    density = float(config.get("density", summary.get("density", 1.0)) or 1.0)

    # Demand levels
    vehicle_demand = float(config.get("vehicle_demand_level", summary.get("vehicle_demand_level", 0.5)) or 0.5)
    ped_demand = float(config.get("ped_demand_level", summary.get("ped_demand_level", 0.5)) or 0.5)
    bike_demand = float(config.get("bike_demand_level", summary.get("bike_demand_level", 0.0)) or 0.0)
    transit_demand = float(config.get("transit_demand_level", summary.get("transit_demand_level", 0.0)) or 0.0)

    # Count relevant placements
    tree_count = 0
    planter_count = 0
    bus_stop_count = 0
    building_count = 0
    point_sources: List[Dict[str, Any]] = []

    for p in placements:
        cat = str(p.get("category", "")).strip().lower()
        pos = p.get("position_xyz")
        if cat == "tree":
            tree_count += 1
        elif cat in ("planter", "flower_bed", "shrub"):
            planter_count += 1
        elif cat == "bus_stop":
            bus_stop_count += 1
            if isinstance(pos, (list, tuple)) and len(pos) >= 3:
                point_sources.append({
                    "type": "bus_stop",
                    "position": [float(pos[0]), float(pos[1]), float(pos[2])],
                    "radius_m": 15.0,
                })
        elif cat in ("building", "house", "store"):
            building_count += 1

    # Traffic volume: more lanes, wider road, higher vehicle demand = more traffic noise
    traffic_volume = min(1.0, (lane_count / 6.0) * 0.4 + (road_width_m / 20.0) * 0.3 + vehicle_demand * 0.3)

    # Nature volume: trees, planters, low vehicle demand
    green_density = (tree_count + planter_count) / max(length_m / 10.0, 1.0)
    nature_volume = min(1.0, green_density * 0.5 + (1.0 - vehicle_demand) * 0.2)

    # Urban volume: density, pedestrian demand, building count
    urban_volume = min(1.0, density * 0.3 + ped_demand * 0.3 + min(building_count / 10.0, 1.0) * 0.2 + bike_demand * 0.2)

    # Transit volume: bus stops, transit demand
    transit_volume = min(1.0, bus_stop_count * 0.3 + transit_demand * 0.5)

    return {
        "ambient": {
            "traffic": round(traffic_volume, 3),
            "nature": round(nature_volume, 3),
            "urban": round(urban_volume, 3),
            "transit": round(transit_volume, 3),
        },
        "point_sources": point_sources,
    }


def inject_audio_profile(layout_payload: MutableMapping[str, Any]) -> None:
    """Compute and embed the audio profile into the layout payload summary."""
    profile = analyze_scene_audio(layout_payload)
    summary = dict(layout_payload.get("summary", {}) or {})
    summary["audio_profile"] = profile
    layout_payload["summary"] = summary
