export type AnnotationPoint = {
  x: number;
  y: number;
};

export type CrossSectionMode = "coarse" | "detailed";
export type StripZone = "left" | "center" | "right";
export type StripDirection = "forward" | "reverse" | "bidirectional" | "none";
export type StripKind =
  | "drive_lane"
  | "bus_lane"
  | "bike_lane"
  | "parking_lane"
  | "median"
  | "nearroad_buffer"
  | "nearroad_furnishing"
  | "clear_sidewalk"
  | "farfromroad_buffer"
  | "frontage_reserve";
export type FurnitureKind =
  | "bench"
  | "lamp"
  | "trash"
  | "mailbox"
  | "bollard"
  | "sign"
  | "hydrant"
  | "bus_stop"
  | "tree";

export type AnnotatedCrossSectionStrip = {
  strip_id: string;
  zone: StripZone;
  kind: StripKind;
  width_m: number;
  direction: StripDirection;
  order_index: number;
};

export type AnnotatedStreetFurnitureInstance = {
  instance_id: string;
  centerline_id: string;
  strip_id: string;
  kind: FurnitureKind;
  station_m: number;
  lateral_offset_m: number;
  yaw_deg: number | null;
};

export type AnnotatedCenterline = {
  id: string;
  label: string;
  points: AnnotationPoint[];
  road_width_m: number;
  reference_width_px: number | null;
  forward_drive_lane_count: number;
  reverse_drive_lane_count: number;
  bike_lane_count: number;
  bus_lane_count: number;
  parking_lane_count: number;
  highway_type: string;
  cross_section_mode: CrossSectionMode;
  cross_section_strips: AnnotatedCrossSectionStrip[];
  street_furniture_instances: AnnotatedStreetFurnitureInstance[];
  start_junction_id: string;
  end_junction_id: string;
};

export type LaneProfile = {
  forward_drive_lane_count: number;
  reverse_drive_lane_count: number;
  bike_lane_count: number;
  bus_lane_count: number;
  parking_lane_count: number;
  bidirectional_drive_lane_count: number;
  bidirectional_lane_count: number;
  total_drive_lane_count: number;
  total_lane_count: number;
};

export type AnnotatedMarker = {
  id: string;
  label: string;
  x: number;
  y: number;
  kind: string;
};

export type AnnotatedJunction = {
  id: string;
  label: string;
  x: number;
  y: number;
  kind: string;
  connected_centerline_ids: string[];
  crosswalk_depth_m: number;
  source_mode: "explicit" | "legacy_marker";
};

export type AnnotatedRoundabout = {
  id: string;
  label: string;
  x: number;
  y: number;
  radius_px: number;
};

export type AnnotatedBuildingRegion = {
  id: string;
  label: string;
  center_px: AnnotationPoint;
  width_px: number;
  height_px: number;
  yaw_deg: number;
};

export type ReferenceAnnotation = {
  version: string;
  plan_id: string;
  image_path: string;
  image_width_px: number;
  image_height_px: number;
  pixels_per_meter: number;
  centerlines: AnnotatedCenterline[];
  junctions: AnnotatedJunction[];
  roundabouts: AnnotatedRoundabout[];
  control_points: AnnotatedMarker[];
  building_regions: AnnotatedBuildingRegion[];
};

export type ReferencePlan = {
  plan_id: string;
  label: string;
  description?: string;
  image_url?: string;
};

export type ReferencePlansPayload = {
  items?: ReferencePlan[];
};

export type ConvertedGraphPayload = {
  annotation: ReferenceAnnotation;
  graph: {
    mode: string;
    nodes: Array<Record<string, unknown>>;
    edges: Array<Record<string, unknown>>;
  };
  road_profiles?: Array<Record<string, unknown>>;
  cross_section_profiles?: Array<Record<string, unknown>>;
  street_furniture_instances?: Array<Record<string, unknown>>;
  derived_junctions?: Array<Record<string, unknown>>;
  metaurban_asset_hints?: Array<Record<string, unknown>>;
  metaurban_asset_guide?: Record<string, unknown>;
  summary: Record<string, unknown>;
};

export type PreviewCrossSection = {
  sourceMode: "seed" | "detailed";
  strips: AnnotatedCrossSectionStrip[];
};

export type BranchSnapTarget = {
  centerlineId: string;
  segmentIndex: number;
  stationPx: number;
  point: AnnotationPoint;
  distancePx: number;
};

export type BranchDraft = {
  anchor: BranchSnapTarget;
  endpoint: AnnotationPoint;
  endpointSnap: BranchSnapTarget | null;
};

export type CrossDraft = {
  anchor: BranchSnapTarget;
  axisNormal: AnnotationPoint;
  halfLengthPx: number;
  negativeEndpoint: AnnotationPoint;
  positiveEndpoint: AnnotationPoint;
  negativeEndpointSnap: BranchSnapTarget | null;
  positiveEndpointSnap: BranchSnapTarget | null;
};

export type AnnotationModelIssue = {
  code: "centerline_intersection" | "junction_pass_through" | "junction_connection";
  message: string;
};

export type DerivedJunctionOverlayPatch = {
  patchId: string;
  points: AnnotationPoint[];
  cutoutPoints?: AnnotationPoint[];
};

export type DerivedJunctionOverlayBoundary = {
  boundaryId: string;
  centerlineId: string;
  start: AnnotationPoint;
  end: AnnotationPoint;
  center: AnnotationPoint;
  exitDistancePx: number;
};

export type JunctionOverlayFootPoint = {
  footId: string;
  centerlineId: string;
  point: AnnotationPoint;
};

export type JunctionOverlayControlPoint = {
  controlId: string;
  centerlineId: string;
  stripId: string;
  stripKind: StripKind;
  stripZone: StripZone;
  pointKind: "station_foot_point" | "center_control_point" | "inner_edge_control_point" | "outer_edge_control_point";
  point: AnnotationPoint;
};

export type JunctionOverlayCornerFocus = {
  focusId: string;
  point: AnnotationPoint;
};

export type JunctionOverlayGuideLine = {
  guideId: string;
  start: AnnotationPoint;
  end: AnnotationPoint;
};

export type JunctionOverlayCornerKernel = {
  kernelId: string;
  quadrantId: string;
  junctionId: string;
  startCenterlineId: string;
  endCenterlineId: string;
  kernelKind: "circular_arc" | "polyline_fallback";
  center: AnnotationPoint;
  radiusPx: number;
  startHeadingDeg: number;
  endHeadingDeg: number;
  clockwise: boolean | null;
  points: AnnotationPoint[];
};

export type DerivedJunctionOverlayConnectorLine = {
  connectorId: string;
  stripKind: StripKind;
  quadrantId: string;
  kernelId: string | null;
  strokeWidthPx: number;
  points: AnnotationPoint[];
};

export type JunctionOverlayStripLinkEndpoint = {
  centerlineId: string;
  stripId: string;
  stripKind: StripKind;
  stripZone: StripZone;
};

export type JunctionOverlayStripLink = {
  linkId: string;
  junctionId: string;
  quadrantId: string;
  kernelId: string | null;
  stripKind: StripKind;
  start: JunctionOverlayStripLinkEndpoint;
  end: JunctionOverlayStripLinkEndpoint;
  points: AnnotationPoint[];
  strokeWidthPx: number;
};

export type DerivedJunctionOverlay = {
  junctionId: string;
  kind: "t_junction" | "cross_junction";
  sourceMode: "explicit" | "derived";
  core: AnnotationPoint[];
  crosswalks: DerivedJunctionOverlayPatch[];
  sidewalkCorners: DerivedJunctionOverlayPatch[];
  nearroadCorners: DerivedJunctionOverlayPatch[];
  frontageCorners: DerivedJunctionOverlayPatch[];
  approachBoundaries: DerivedJunctionOverlayBoundary[];
  anchor: AnnotationPoint;
  armCount: number;
  connectedCenterlineIds: string[];
  skeletonFootPoints: JunctionOverlayFootPoint[];
  subLaneControlPoints: JunctionOverlayControlPoint[];
  cornerFocusPoints: JunctionOverlayCornerFocus[];
  boundaryExtensionLines: JunctionOverlayGuideLine[];
  focusGuideLines: JunctionOverlayGuideLine[];
  quadrantCornerKernels: JunctionOverlayCornerKernel[];
  connectorCenterLines: DerivedJunctionOverlayConnectorLine[];
  cornerStripLinks: JunctionOverlayStripLink[];
};

export type DerivedJunctionOverlayArm = {
  centerlineId: string;
  angleDeg: number;
  tangent: AnnotationPoint;
  normal: AnnotationPoint;
  reverseOffsets: boolean;
  carriagewayWidthPx: number;
  nearroadBufferWidthPx: number;
  nearroadFurnishingWidthPx: number;
  clearSidewalkWidthPx: number;
  farfromroadBufferWidthPx: number;
  frontageReserveWidthPx: number;
  sideStripLayouts: SideStripLayouts;
  splitBoundaryCenter: AnnotationPoint;
};

export type ClippedDisplaySegment = {
  points: AnnotationPoint[];
  clippedStart: boolean;
  clippedEnd: boolean;
};

export type MetaurbanAssetBadge = {
  key: string;
  label: string;
  shortLabel: string;
};

export type Tool = "select" | "adjust" | "centerline" | "branch" | "cross" | "roundabout" | "control_point" | "building_region" | "tree" | "lamp";

export type Selection =
  | {
      kind: "centerline";
      id: string;
      vertexIndex?: number;
    }
  | {
      kind: "road_collection";
      id: string;
    }
  | {
      kind: "junction" | "roundabout" | "control_point" | "derived_junction" | "building_region";
      id: string;
    }
  | null;

export type BuildingRegionResizeHandle = "nw" | "ne" | "se" | "sw";

export type DragState =
  | {
      kind: "centerline_vertex";
      id: string;
      vertexIndex: number;
      pointerId: number;
    }
  | {
      kind: "centerline_translate";
      id: string;
      pointerId: number;
      lastPoint: AnnotationPoint;
    }
  | {
      kind: "marker";
      markerKind: "junction" | "roundabout" | "control_point";
      id: string;
      pointerId: number;
    }
  | {
      kind: "building_region_translate";
      id: string;
      pointerId: number;
      lastPoint: AnnotationPoint;
    }
  | {
      kind: "building_region_resize";
      id: string;
      pointerId: number;
      handle: BuildingRegionResizeHandle;
    }
  | {
      kind: "building_region_rotate";
      id: string;
      pointerId: number;
    }
  | {
      kind: "building_region_draw";
      pointerId: number;
      startPoint: AnnotationPoint;
      currentPoint: AnnotationPoint;
    }
  | null;

export type SelectedStripCornerConnection = {
  linkId: string;
  junctionId: string;
  quadrantId: string;
  kernelId: string | null;
  stripKind: StripKind;
  current: JunctionOverlayStripLinkEndpoint;
  peer: JunctionOverlayStripLinkEndpoint;
  points: AnnotationPoint[];
};

export type SelectedStripCornerFamilyTarget = {
  targetId: string;
  junctionId: string;
  quadrantId: string;
  kernelId: string | null;
  stripKind: StripKind;
  target: JunctionOverlayStripLinkEndpoint;
  points: AnnotationPoint[];
};

export type OffsetPolylineSegment = {
  startIndex: number;
  endIndex: number;
  tangent: AnnotationPoint;
  normal: AnnotationPoint;
  offsetStart: AnnotationPoint;
  offsetEnd: AnnotationPoint;
};

// Replaces ReturnType<typeof centerlineSideStripLayouts>
export type SideStripLayoutEntry = {
  stripId: string;
  kind: StripKind;
  centerOffsetM: number;
  innerOffsetM: number;
  outerOffsetM: number;
};

// Replaces ReturnType<typeof centerlineSideStripLayouts>
export type SideStripLayouts = Record<StripZone, SideStripLayoutEntry[]>;

export type StatusTone = "neutral" | "success" | "error";
