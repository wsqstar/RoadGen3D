# External Building Assets

This folder keeps downloaded third-party building assets that are compatible
with the RoadGen3D building manifest.

## 2026-05-09 Kenney Import

Sources:

- Kenney City Kit (Commercial): https://kenney.nl/assets/city-kit-commercial
- Kenney City Kit (Suburban): https://kenney.nl/assets/city-kit-suburban

License:

- Creative Commons CC0 1.0 Universal
- https://creativecommons.org/publicdomain/zero/1.0/

Downloaded archives:

- `downloads/kenney_city-kit-commercial_2.1.zip`
- `downloads/kenney_city-kit-suburban_2.0.zip`

Organized folders:

- `kenney_city_kit_commercial/`
- `kenney_city_kit_suburban/`

Manifest integration:

- `../buildings_manifest.jsonl` now contains 56 Kenney building records:
  - 35 commercial / city buildings from the commercial kit
  - 21 residential low-rise buildings from the suburban kit
- Non-building kit pieces such as paths, fences, trees, driveways, awnings,
  parasols, and overhang details remain unpacked here but are not registered as
  building assets.
- Kenney authoring units are mapped to RoadGen3D meter-scale scenes with
  `scale: 8.0`.

Traceability metadata:

- `sources_2026-05-09.json`
