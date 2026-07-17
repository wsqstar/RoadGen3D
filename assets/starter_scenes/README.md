# RoadGen3D starter scenes

`guangzhou_road_skeleton_v1` is the immutable professional-workbench preview.
It contains a frozen OpenStreetMap snapshot, the normalized annotation source,
2D/Graph overlays, and a furniture-free road GLB. Runtime startup never contacts
Overpass and never reads an ignored `artifacts/real` result.

Rebuild the checked-in package deterministically from its frozen inputs:

```bash
MPLCONFIGDIR=/tmp/roadgen-mpl PYTHONPATH=src python3 tools/build_starter_scene.py
```

For a deliberate bootstrap from another known road-base result, pass
`--raw-osm`, `--source-layout`, and `--source-road-glb` explicitly. Review and
commit all changed fingerprints together.
