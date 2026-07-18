# RoadGen3D starter scenes

`guangzhou_road_skeleton_v2` is the default immutable professional-workbench preview.
The previous `guangzhou_road_skeleton_v1` package remains registered for old links.
It contains a frozen OpenStreetMap snapshot, the normalized annotation source,
2D/Graph overlays, and a furniture-free road GLB. Runtime startup never contacts
Overpass and never reads an ignored `artifacts/real` result.

Rebuild the checked-in package deterministically from its frozen inputs:

```bash
MPLCONFIGDIR=/tmp/roadgen-mpl PYTHONPATH=src python3 tools/build_starter_scene.py
```

The command rebuilds the road GLB from the frozen OSM snapshot and normalized
ReferenceAnnotation; it does not copy a previous road mesh. For a deliberate
bootstrap from another fixed snapshot, pass `--raw-osm` and `--source-layout`
explicitly. Review and commit all changed fingerprints together.
