"""Audio profile generation for scenes.

Derives ambient audio volumes and point sound sources from scene layout.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

from ..core.config import AudioConfig
from ..core.types import AudioProfile


def analyze_scene_audio(
    placements: Sequence[Mapping[str, Any]],
    length_m: float,
    road_width_m: float,
    lane_count: int,
    density: float,
    vehicle_demand: float = 0.5,
    ped_demand: float = 0.5,
    bike_demand: float = 0.0,
    transit_demand: float = 0.0,
    config: AudioConfig | None = None,
) -> AudioProfile:
    """Derive an audio profile from scene layout data.

    Args:
        placements: List of placed assets
        length_m: Street segment length
        road_width_m: Total road width
        lane_count: Number of vehicle lanes
        density: Overall furniture density scale
        vehicle_demand: Vehicle demand level (0-1)
        ped_demand: Pedestrian demand level (0-1)
        bike_demand: Bike demand level (0-1)
        transit_demand: Transit demand level (0-1)
        config: Audio parameters

    Returns:
        AudioProfile with ambient volumes and point sources
    """
    cfg = config or AudioConfig()

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
                    "radius_m": cfg.bus_stop_radius_m,
                })
        elif cat in ("building", "house", "store"):
            building_count += 1

    # Traffic volume: more lanes, wider road, higher vehicle demand = more traffic noise
    traffic_volume = min(
        1.0,
        (lane_count / cfg.lane_count_max) * cfg.lane_count_weight
        + (road_width_m / cfg.road_width_max_m) * cfg.road_width_weight
        + vehicle_demand * cfg.vehicle_demand_weight,
    )

    # Nature volume: trees, planters, low vehicle demand
    green_density = (tree_count + planter_count) / max(length_m / 10.0, 1.0)
    nature_volume = min(
        1.0,
        green_density * cfg.green_density_weight
        + (1.0 - vehicle_demand) * cfg.vehicle_demand_inverse_weight,
    )

    # Urban volume: density, pedestrian demand, building count
    urban_volume = min(
        1.0,
        density * cfg.density_weight
        + ped_demand * cfg.ped_demand_weight
        + min(building_count / cfg.building_count_max, 1.0) * cfg.building_weight
        + bike_demand * cfg.bike_demand_weight,
    )

    # Transit volume: bus stops, transit demand
    transit_volume = min(
        1.0,
        bus_stop_count * cfg.bus_stop_weight
        + transit_demand * cfg.transit_demand_weight,
    )

    return AudioProfile(
        traffic=round(traffic_volume, 3),
        nature=round(nature_volume, 3),
        urban=round(urban_volume, 3),
        transit=round(transit_volume, 3),
        point_sources=point_sources,
    )
