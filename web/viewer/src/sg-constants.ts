import type { CrossSectionMode, StripKind, StripDirection, FurnitureKind, MetaurbanAssetBadge, ReferencePlan } from "./sg-types";

export const API_BASE = (import.meta.env.VITE_ROADGEN_API_BASE as string | undefined) || "http://127.0.0.1:8010";
export const ANNOTATION_SCHEMA_VERSION = "roadgen3d_reference_annotation_v2";
export const FALLBACK_REFERENCE_PLAN: ReferencePlan = {
  plan_id: "hkust_gz_gate",
  label: "HKUST-GZ Gate",
  description: "Built-in fallback reference plan.",
  image_url: "/api/reference-plans/hkust_gz_gate/image",
};
export const ALL_ROADS_SELECTION_ID = "__all_roads__";
export const DEFAULT_PIXELS_PER_METER = 1.5;
export const DEFAULT_SIDEWALK_WIDTH_M = 3;
export const DEFAULT_SEGMENT_LENGTH_M = 12;
export const DEFAULT_ROUNDABOUT_RADIUS_PX = 36;
export const DEFAULT_FORWARD_DRIVE_LANE_COUNT = 2;
export const DEFAULT_REVERSE_DRIVE_LANE_COUNT = 2;
export const CROSS_SECTION_MODE_COARSE: CrossSectionMode = "coarse";
export const CROSS_SECTION_MODE_DETAILED: CrossSectionMode = "detailed";
export const DEFAULT_DRIVE_LANE_WIDTH_M = 3.3;
export const DEFAULT_CENTERLINE_MARK_WIDTH_M = 0.3;
export const BRANCH_SNAP_TOLERANCE_PX = 16;
export const BRANCH_VERTEX_REUSE_TOLERANCE_PX = 4;
export const BRANCH_MIN_LENGTH_M = 4;
export const CROSS_MIN_HALF_LENGTH_M = 4;
export const STANDALONE_CROSS_ARM_LENGTH_M = 20;
export const ANNOTATION_MODEL_TOLERANCE_PX = 4;
export const BUILDING_REGION_MIN_SIZE_PX = 18;
export const BUILDING_REGION_ROTATE_HANDLE_OFFSET_PX = 28;
export const BUILDING_REGION_HANDLE_RADIUS_PX = 7;

export const STRIP_KINDS: StripKind[] = [
  "drive_lane",
  "bus_lane",
  "bike_lane",
  "parking_lane",
  "median",
  "nearroad_buffer",
  "nearroad_furnishing",
  "clear_sidewalk",
  "farfromroad_buffer",
  "frontage_reserve",
];
export const SIDE_STRIP_KINDS = new Set<StripKind>([
  "nearroad_buffer",
  "nearroad_furnishing",
  "clear_sidewalk",
  "farfromroad_buffer",
  "frontage_reserve",
]);
export const CENTER_STRIP_KINDS = new Set<StripKind>([
  "drive_lane",
  "bus_lane",
  "bike_lane",
  "parking_lane",
  "median",
]);
export const CORNER_LINK_STRIP_KINDS = new Set<StripKind>([
  "nearroad_furnishing",
  "clear_sidewalk",
  "frontage_reserve",
]);
export const FURNITURE_COMPATIBLE_STRIP_KINDS = new Set<StripKind>(["nearroad_furnishing", "frontage_reserve"]);
export const FURNITURE_KINDS: FurnitureKind[] = [
  "bench",
  "lamp",
  "trash",
  "mailbox",
  "bollard",
  "sign",
  "hydrant",
  "bus_stop",
  "tree",
];
export const STRIP_DIRECTION_OPTIONS: StripDirection[] = ["forward", "reverse", "bidirectional", "none"];
export const NOMINAL_STRIP_WIDTHS: Record<StripKind, number> = {
  drive_lane: DEFAULT_DRIVE_LANE_WIDTH_M,
  bus_lane: 3.5,
  bike_lane: 1.8,
  parking_lane: 2.5,
  median: 0.3,
  nearroad_buffer: 0.5,
  nearroad_furnishing: 1.5,
  clear_sidewalk: 2.5,
  farfromroad_buffer: 0.5,
  frontage_reserve: 2.0,
};
export const DEFAULT_ROAD_WIDTH_M =
  (DEFAULT_FORWARD_DRIVE_LANE_COUNT + DEFAULT_REVERSE_DRIVE_LANE_COUNT) * DEFAULT_DRIVE_LANE_WIDTH_M +
  2 *
    (NOMINAL_STRIP_WIDTHS.nearroad_furnishing +
      NOMINAL_STRIP_WIDTHS.clear_sidewalk +
      NOMINAL_STRIP_WIDTHS.frontage_reserve) +
  NOMINAL_STRIP_WIDTHS.median;
export const STRIP_KIND_LABELS: Record<StripKind, string> = {
  drive_lane: "Drive Lane",
  bus_lane: "Bus Lane",
  bike_lane: "Bike Lane",
  parking_lane: "Parking Lane",
  median: "Median",
  nearroad_buffer: "Near-Road Buffer",
  nearroad_furnishing: "Near-Road Furnishing",
  clear_sidewalk: "Clear Sidewalk",
  farfromroad_buffer: "Far-From-Road Buffer",
  frontage_reserve: "Frontage Reserve",
};
export const METAAURBAN_STRIP_DISPLAY_LABELS: Record<StripKind, string> = {
  drive_lane: "Drive Lane",
  bus_lane: "Bus Lane",
  bike_lane: "Bike Lane",
  parking_lane: "Parking Lane",
  median: "Median",
  nearroad_buffer: "Near-road Buffer",
  nearroad_furnishing: "Near-road Furnishing",
  clear_sidewalk: "Main Sidewalk",
  farfromroad_buffer: "Outer Buffer",
  frontage_reserve: "Valid Region",
};
export const METAAURBAN_STRIP_ZONE_LABELS: Record<StripKind, string> = {
  drive_lane: "carriageway",
  bus_lane: "carriageway",
  bike_lane: "carriageway_edge",
  parking_lane: "carriageway_edge",
  median: "median",
  nearroad_buffer: "nearroad_buffer_sidewalk",
  nearroad_furnishing: "nearroad_sidewalk",
  clear_sidewalk: "main_sidewalk",
  farfromroad_buffer: "farfromroad_sidewalk",
  frontage_reserve: "valid_region",
};
export const METAAURBAN_STRIP_ASSET_BADGES: Record<StripKind, MetaurbanAssetBadge[]> = {
  drive_lane: [],
  bus_lane: [],
  bike_lane: [],
  parking_lane: [],
  median: [],
  nearroad_buffer: [
    { key: "tree", label: "Tree", shortLabel: "TREE" },
    { key: "traffic_sign", label: "Traffic Sign", shortLabel: "SIGN" },
    { key: "bollard", label: "Bollard", shortLabel: "BOLLARD" },
  ],
  nearroad_furnishing: [
    { key: "lamp_post", label: "Lamp Post", shortLabel: "LAMP" },
    { key: "trash_can", label: "TrashCan", shortLabel: "TRASH" },
    { key: "fire_hydrant", label: "FireHydrant", shortLabel: "HYDRANT" },
  ],
  clear_sidewalk: [
    { key: "pedestrian", label: "Pedestrian", shortLabel: "PED" },
    { key: "wheelchair", label: "Wheelchair", shortLabel: "WC" },
    { key: "mailbox", label: "Mailbox", shortLabel: "MAIL" },
  ],
  farfromroad_buffer: [
    { key: "bench", label: "Bench", shortLabel: "BENCH" },
  ],
  frontage_reserve: [
    { key: "building", label: "Building", shortLabel: "BLDG" },
  ],
};
export const METAAURBAN_STRIP_GUIDANCE: Record<StripKind, string> = {
  drive_lane: "Vehicular through-movement space.",
  bus_lane: "Transit-priority movement space.",
  bike_lane: "Bike movement space.",
  parking_lane: "Parking or loading edge space.",
  median: "Central separator or refuge zone.",
  nearroad_buffer: "MetaUrban nearroad_buffer_sidewalk objects typically sit here.",
  nearroad_furnishing: "MetaUrban nearroad_sidewalk furniture and utilities typically sit here.",
  clear_sidewalk: "MetaUrban main_sidewalk pedestrian flows and mailbox-scale objects typically sit here.",
  farfromroad_buffer: "MetaUrban farfromroad_sidewalk furniture or planting can extend here.",
  frontage_reserve: "MetaUrban valid_region buildings and frontage reserve typically start here.",
};
export const METAAURBAN_ASSET_GUIDE_LINES = [
  "MetaUrban real assets are optional for this annotator.",
  "To add them later, run `python metaurban/pull_asset.py --update`.",
  "Place assets under `metaurban/assets` and `metaurban/assets_pedestrian`.",
];
export const FURNITURE_KIND_LABELS: Record<FurnitureKind, string> = {
  bench: "Bench",
  lamp: "Lamp",
  trash: "Trash",
  mailbox: "Mailbox",
  bollard: "Bollard",
  sign: "Sign",
  hydrant: "Hydrant",
  bus_stop: "Bus Stop",
  tree: "Tree",
};
