from types import SimpleNamespace
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from roadgen3d.placement_zones import apply_road_selection


def test_apply_road_selection_prefers_selected_osm_id():
    roads = [
        SimpleNamespace(osm_id=101, highway_type="primary", coords=[(0.0, 0.0), (20.0, 0.0)], width_m=8.0),
        SimpleNamespace(osm_id=202, highway_type="service", coords=[(1.0, 1.0), (40.0, 1.0)], width_m=6.0),
    ]
    projected = SimpleNamespace(
        roads=roads,
        buildings=[],
        entrances=[],
        bus_stops=[],
        fire_points=[],
        bbox_m=(0.0, 0.0, 50.0, 10.0),
        origin_utm=(0.0, 0.0),
        utm_epsg=32650,
    )
    config = SimpleNamespace(road_selection="primary_road", selected_road_osm_id=202)

    filtered = apply_road_selection(projected, config)

    assert len(filtered.roads) == 1
    assert filtered.roads[0].osm_id == 202


def test_apply_road_selection_prefers_walkable_neighborhood_types():
    roads = [
        SimpleNamespace(osm_id=101, highway_type="primary", coords=[(0.0, 0.0), (20.0, 0.0)], width_m=12.0),
        SimpleNamespace(osm_id=202, highway_type="residential", coords=[(1.0, 1.0), (21.0, 1.0)], width_m=6.0),
        SimpleNamespace(osm_id=303, highway_type="tertiary", coords=[(2.0, 2.0), (22.0, 2.0)], width_m=7.0),
    ]
    projected = SimpleNamespace(
        roads=roads,
        buildings=[],
        entrances=[],
        bus_stops=[],
        fire_points=[],
        bbox_m=(0.0, 0.0, 50.0, 10.0),
        origin_utm=(0.0, 0.0),
        utm_epsg=32650,
    )
    config = SimpleNamespace(road_selection="walkable_neighborhood", selected_road_osm_id=None)

    filtered = apply_road_selection(projected, config)

    assert len(filtered.roads) == 1
    assert filtered.roads[0].osm_id == 303
