# RoadGen3D starter scenes

`guangzhou_complete_intersection_v4` is the default immutable professional-workbench
preview. It presents the real Guangzhou OSM cross junction, transparent building
massing, and a compact representative set of trees, lamps, bollards, benches, and
trash bins. It is a product tour, not a completed user workflow: 01A, 01B, and 02
remain untouched until the user explicitly copies the example or starts their own
OSM study.

The previous `guangzhou_road_skeleton_v1`, `guangzhou_road_skeleton_v2`, and
`guangzhou_complete_intersection_v3` packages remain registered for old links and
geometry regression tests. Runtime startup never contacts Overpass and never reads
an ignored `artifacts/real` result. The v4 package is the first complete-intersection
starter rendered from a unified carriageway/curb/sidewalk partition with final-GLB
coverage and needle-triangle QA.

Rebuild the checked-in package deterministically from its frozen inputs:

```bash
MPLCONFIGDIR=/tmp/roadgen-mpl PYTHONPATH=src python3 tools/build_complete_starter_scene.py
```

The command rebuilds the current road surfaces with fixed seed 42, transparent
building massing, and then retains a deterministic representative asset subset.
It does not download OSM or copy a previous GLB. Rebuild the furniture-free v2
geometry fixture separately with `tools/build_starter_scene.py`. Review and commit
all changed fingerprints together.
