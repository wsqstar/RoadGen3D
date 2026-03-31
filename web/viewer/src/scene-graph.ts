import type {
  AnnotationPoint,
  CrossSectionMode,
  StripZone,
  StripDirection,
  StripKind,
  FurnitureKind,
  AnnotatedCrossSectionStrip,
  AnnotatedStreetFurnitureInstance,
  AnnotatedCenterline,
  LaneProfile,
  AnnotatedMarker,
  AnnotatedJunction,
  AnnotatedRoundabout,
  AnnotatedBuildingRegion,
  ReferenceAnnotation,
  ReferencePlan,
  ReferencePlansPayload,
  ConvertedGraphPayload,
  PreviewCrossSection,
  BranchSnapTarget,
  BranchDraft,
  CrossDraft,
  AnnotationModelIssue,
  DerivedJunctionOverlayPatch,
  DerivedJunctionOverlayBoundary,
  JunctionOverlayFootPoint,
  JunctionOverlayControlPoint,
  JunctionOverlayCornerFocus,
  JunctionOverlayGuideLine,
  JunctionOverlayCornerKernel,
  DerivedJunctionOverlayConnectorLine,
  JunctionOverlayStripLinkEndpoint,
  JunctionOverlayStripLink,
  DerivedJunctionOverlay,
  DerivedJunctionOverlayArm,
  ClippedDisplaySegment,
  MetaurbanAssetBadge,
  Tool,
  Selection,
  BuildingRegionResizeHandle,
  DragState,
  StatusTone,
  SelectedStripCornerConnection,
  SelectedStripCornerFamilyTarget,
  OffsetPolylineSegment,
  SideStripLayoutEntry,
  SideStripLayouts,
} from "./sg-types";

import {
  ALL_ROADS_SELECTION_ID,
  API_BASE,
  ANNOTATION_SCHEMA_VERSION,
  BRANCH_MIN_LENGTH_M,
  BRANCH_SNAP_TOLERANCE_PX,
  BRANCH_VERTEX_REUSE_TOLERANCE_PX,
  BUILDING_REGION_HANDLE_RADIUS_PX,
  BUILDING_REGION_MIN_SIZE_PX,
  BUILDING_REGION_ROTATE_HANDLE_OFFSET_PX,
  CENTER_STRIP_KINDS,
  CORNER_LINK_STRIP_KINDS,
  CROSS_MIN_HALF_LENGTH_M,
  CROSS_SECTION_MODE_COARSE,
  CROSS_SECTION_MODE_DETAILED,
  DEFAULT_CENTERLINE_MARK_WIDTH_M,
  DEFAULT_DRIVE_LANE_WIDTH_M,
  DEFAULT_FORWARD_DRIVE_LANE_COUNT,
  DEFAULT_PIXELS_PER_METER,
  DEFAULT_REVERSE_DRIVE_LANE_COUNT,
  DEFAULT_ROUNDABOUT_RADIUS_PX,
  DEFAULT_SEGMENT_LENGTH_M,
  DEFAULT_SIDEWALK_WIDTH_M,
  FALLBACK_REFERENCE_PLAN,
  FURNITURE_COMPATIBLE_STRIP_KINDS,
  FURNITURE_KINDS,
  FURNITURE_KIND_LABELS,
  METAAURBAN_ASSET_GUIDE_LINES,
  METAAURBAN_STRIP_ASSET_BADGES,
  METAAURBAN_STRIP_DISPLAY_LABELS,
  METAAURBAN_STRIP_GUIDANCE,
  METAAURBAN_STRIP_ZONE_LABELS,
  NOMINAL_STRIP_WIDTHS,
  SIDE_STRIP_KINDS,
  STANDALONE_CROSS_ARM_LENGTH_M,
  STRIP_DIRECTION_OPTIONS,
  STRIP_KIND_LABELS,
} from "./sg-constants";
import {
  asNonNegativeInt,
  asNullableNumber,
  asNumber,
  asString,
  clamp,
  cloneCenterlineForBranch,
  clonePoint,
  createDefaultAnnotatedCenterline,
  createExplicitJunction,
  deriveLaneProfile,
  ensureDetailedCrossSection,
  ensureDetailedCrossSections,
  endpointJunctionIdAtPoint,
  findNearestBranchSnapTarget,
  formatCrossSectionSummary,
  formatLaneSummary,
  getCenterlineCarriagewayWidth,
  getCenterlineCrossSectionWidth,
  insertSharedVertexAtSnap,
  isFurnitureKind,
  isStripDirection,
  isStripKind,
  isStripZone,
  junctionAnchorPoint,
  laneProfile,
  linkedCrossStripKeys,
  lineIntersectionTs,
  offsetPolyline,
  pointDistance,
  polylineLength,
  projectPointOntoPolyline,
  registerCenterlineWithExplicitJunction,
  replaceCenterlineReference,
  reserveNextFeatureIds,
  resolveDriveLaneDefaults,
  resolvedCrossSectionMode,
  selectedStripCornerConnections,
  selectedStripCornerFamilyTargets,
  snapDraftCenterlineEndpointsToExplicitJunctions,
  sortedCrossSectionStrips,
  splitCenterlineAtSnap,
  stationToPolylinePoint,
  stripCenterOffsetMeters,
  stripKey,
  syncCenterlineDerivedFields,
  updateJunctionConnectedCenterlines,
  validateAnnotationForExplicitJunctionModel,
  validateDraftCenterlinePlacement,
} from "./sg-utils";
import {
  buildingRegionLocalPoint,
  buildingRegionPolygonPoints,
  buildingRegionResizeHandlePoint,
  buildingRegionRotateHandlePoint,
  buildBuildingRegionFromDraft,
  centerlineSideStripLayouts,
  crossAxisNormalAtSnap,
  deriveExplicitJunctionOverlayGeometries,
  deriveJunctionOverlayGeometries,
  derivedJunctionKindLabel,
  getJunctionOverlay,
  junctionProfileWidths,
  pointOnAxis,
  rectanglePolygonPoints,
  stripDisplayPoint,
} from "./sg-geometry";

function nextStripId(centerline: AnnotatedCenterline, zone: StripZone): string {
  const used = new Set(centerline.cross_section_strips.map((strip) => strip.strip_id));
  let counter = centerline.cross_section_strips.filter((strip) => strip.zone === zone).length + 1;
  while (true) {
    const candidate = `${zone}_${String(counter).padStart(2, "0")}`;
    if (!used.has(candidate)) {
      return candidate;
    }
    counter += 1;
  }
}

function splitAuxiliaryCountAcrossDirections(
  total: number,
  forwardDriveLaneCount: number,
  reverseDriveLaneCount: number,
): { reverse: number; forward: number } {
  if (forwardDriveLaneCount > 0 && reverseDriveLaneCount > 0) {
    return {
      reverse: Math.ceil(total / 2),
      forward: Math.floor(total / 2),
    };
  }
  if (reverseDriveLaneCount > 0) {
    return { reverse: total, forward: 0 };
  }
  return { reverse: 0, forward: total };
}

function nominalSeedCrossSectionWidthForCounts(
  forwardDriveLaneCount: number,
  reverseDriveLaneCount: number,
  bikeLaneCount: number,
  busLaneCount: number,
  parkingLaneCount: number,
): number {
  const parkingSplit = splitAuxiliaryCountAcrossDirections(
    Math.max(0, parkingLaneCount),
    Math.max(0, forwardDriveLaneCount),
    Math.max(0, reverseDriveLaneCount),
  );
  const bikeSplit = splitAuxiliaryCountAcrossDirections(
    Math.max(0, bikeLaneCount),
    Math.max(0, forwardDriveLaneCount),
    Math.max(0, reverseDriveLaneCount),
  );
  const busSplit = splitAuxiliaryCountAcrossDirections(
    Math.max(0, busLaneCount),
    Math.max(0, forwardDriveLaneCount),
    Math.max(0, reverseDriveLaneCount),
  );
  const sideWidth =
    2 *
    (NOMINAL_STRIP_WIDTHS.nearroad_furnishing +
      NOMINAL_STRIP_WIDTHS.clear_sidewalk +
      NOMINAL_STRIP_WIDTHS.frontage_reserve);
  const centerWidth =
    (Math.max(0, reverseDriveLaneCount) + Math.max(0, forwardDriveLaneCount)) * NOMINAL_STRIP_WIDTHS.drive_lane +
    (parkingSplit.reverse + parkingSplit.forward) * NOMINAL_STRIP_WIDTHS.parking_lane +
    (bikeSplit.reverse + bikeSplit.forward) * NOMINAL_STRIP_WIDTHS.bike_lane +
    (busSplit.reverse + busSplit.forward) * NOMINAL_STRIP_WIDTHS.bus_lane +
    (forwardDriveLaneCount > 0 && reverseDriveLaneCount > 0 ? NOMINAL_STRIP_WIDTHS.median : 0);
  return Number((sideWidth + centerWidth).toFixed(3));
}

function nominalSeedCrossSectionWidth(centerline: AnnotatedCenterline): number {
  return nominalSeedCrossSectionWidthForCounts(
    centerline.forward_drive_lane_count,
    centerline.reverse_drive_lane_count,
    centerline.bike_lane_count,
    centerline.bus_lane_count,
    centerline.parking_lane_count,
  );
}

function seedDetailedCrossSection(centerline: AnnotatedCenterline): AnnotatedCrossSectionStrip[] {
  const leftAux = {
    parking: splitAuxiliaryCountAcrossDirections(
      Math.max(0, centerline.parking_lane_count),
      centerline.forward_drive_lane_count,
      centerline.reverse_drive_lane_count,
    ).reverse,
    bike: splitAuxiliaryCountAcrossDirections(
      Math.max(0, centerline.bike_lane_count),
      centerline.forward_drive_lane_count,
      centerline.reverse_drive_lane_count,
    ).reverse,
    bus: splitAuxiliaryCountAcrossDirections(
      Math.max(0, centerline.bus_lane_count),
      centerline.forward_drive_lane_count,
      centerline.reverse_drive_lane_count,
    ).reverse,
  };
  const rightAux = {
    parking: splitAuxiliaryCountAcrossDirections(
      Math.max(0, centerline.parking_lane_count),
      centerline.forward_drive_lane_count,
      centerline.reverse_drive_lane_count,
    ).forward,
    bike: splitAuxiliaryCountAcrossDirections(
      Math.max(0, centerline.bike_lane_count),
      centerline.forward_drive_lane_count,
      centerline.reverse_drive_lane_count,
    ).forward,
    bus: splitAuxiliaryCountAcrossDirections(
      Math.max(0, centerline.bus_lane_count),
      centerline.forward_drive_lane_count,
      centerline.reverse_drive_lane_count,
    ).forward,
  };
  const strips: AnnotatedCrossSectionStrip[] = [];

  const pushStrip = (zone: StripZone, kind: StripKind, direction: StripDirection): void => {
    strips.push({
      strip_id: nextStripId({ ...centerline, cross_section_strips: strips }, zone),
      zone,
      kind,
      width_m: NOMINAL_STRIP_WIDTHS[kind],
      direction,
      order_index: strips.filter((strip) => strip.zone === zone).length,
    });
  };

  pushStrip("left", "nearroad_furnishing", "none");
  pushStrip("left", "clear_sidewalk", "none");
  pushStrip("left", "frontage_reserve", "none");
  pushStrip("right", "nearroad_furnishing", "none");
  pushStrip("right", "clear_sidewalk", "none");
  pushStrip("right", "frontage_reserve", "none");

  for (let index = 0; index < leftAux.parking; index += 1) {
    pushStrip("center", "parking_lane", "reverse");
  }
  for (let index = 0; index < leftAux.bike; index += 1) {
    pushStrip("center", "bike_lane", "reverse");
  }
  for (let index = 0; index < leftAux.bus; index += 1) {
    pushStrip("center", "bus_lane", "reverse");
  }
  for (let index = 0; index < Math.max(0, centerline.reverse_drive_lane_count); index += 1) {
    pushStrip("center", "drive_lane", "reverse");
  }
  if (centerline.forward_drive_lane_count > 0 && centerline.reverse_drive_lane_count > 0) {
    pushStrip("center", "median", "none");
  }
  for (let index = 0; index < Math.max(0, centerline.forward_drive_lane_count); index += 1) {
    pushStrip("center", "drive_lane", "forward");
  }
  for (let index = 0; index < rightAux.bus; index += 1) {
    pushStrip("center", "bus_lane", "forward");
  }
  for (let index = 0; index < rightAux.bike; index += 1) {
    pushStrip("center", "bike_lane", "forward");
  }
  for (let index = 0; index < rightAux.parking; index += 1) {
    pushStrip("center", "parking_lane", "forward");
  }

  const nominalTotalWidth = strips.reduce((sum, strip) => sum + strip.width_m, 0);
  const targetWidth = Math.max(1, centerline.road_width_m || nominalTotalWidth);
  const scale = nominalTotalWidth > 0 ? targetWidth / nominalTotalWidth : 1;
  return strips.map((strip) => ({
    ...strip,
    width_m: Number((strip.width_m * scale).toFixed(3)),
  }));
}

function normalizeCrossSectionStrip(value: unknown, index: number, prefix: string): AnnotatedCrossSectionStrip {
  const record = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const zone = asString(record.zone, "center");
  const kind = asString(record.kind, "drive_lane");
  const direction = asString(record.direction, "none");
  const normalized: AnnotatedCrossSectionStrip = {
    strip_id: asString(record.strip_id, `${prefix}_strip_${String(index + 1).padStart(2, "0")}`),
    zone: isStripZone(zone) ? zone : "center",
    kind: isStripKind(kind) ? kind : "drive_lane",
    width_m: Math.max(0.1, asNumber(record.width_m, 1)),
    direction: isStripDirection(direction) ? direction : "none",
    order_index: Math.max(0, Math.round(asNumber(record.order_index, index))),
  };
  if (normalized.zone === "center" && !CENTER_STRIP_KINDS.has(normalized.kind)) {
    normalized.kind = "drive_lane";
  }
  if ((normalized.zone === "left" || normalized.zone === "right") && !SIDE_STRIP_KINDS.has(normalized.kind)) {
    normalized.kind = "nearroad_furnishing";
  }
  if (SIDE_STRIP_KINDS.has(normalized.kind) || normalized.kind === "median") {
    normalized.direction = "none";
  }
  return normalized;
}

function normalizeStreetFurnitureInstance(
  value: unknown,
  index: number,
  centerlineId: string,
): AnnotatedStreetFurnitureInstance {
  const record = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const kind = asString(record.kind, "bench");
  return {
    instance_id: asString(record.instance_id ?? record.id, `${centerlineId}_furniture_${String(index + 1).padStart(2, "0")}`),
    centerline_id: asString(record.centerline_id, centerlineId),
    strip_id: asString(record.strip_id, ""),
    kind: isFurnitureKind(kind) ? kind : "bench",
    station_m: Math.max(0, asNumber(record.station_m, 0)),
    lateral_offset_m: asNumber(record.lateral_offset_m, 0),
    yaw_deg: asNullableNumber(record.yaw_deg),
  };
}

function getReferenceWidthMeters(centerline: AnnotatedCenterline, pixelsPerMeter: number): number | null {
  if (centerline.reference_width_px === null) {
    return null;
  }
  return centerline.reference_width_px / Math.max(pixelsPerMeter, 0.0001);
}

function getDisplayReferenceWidthPx(centerline: AnnotatedCenterline, pixelsPerMeter: number): number {
  const explicitWidth = centerline.reference_width_px;
  if (explicitWidth !== null && explicitWidth > 0) {
    return explicitWidth;
  }
  return Math.max(getCenterlineCrossSectionWidth(centerline) * Math.max(pixelsPerMeter, 0.0001), 2);
}

function getDisplayCenterlineWidthPx(pixelsPerMeter: number): number {
  return Math.max(DEFAULT_CENTERLINE_MARK_WIDTH_M * Math.max(pixelsPerMeter, 0.0001), 1);
}

function previewCrossSection(centerline: AnnotatedCenterline): PreviewCrossSection {
  if (resolvedCrossSectionMode(centerline) === CROSS_SECTION_MODE_DETAILED && centerline.cross_section_strips.length > 0) {
    return {
      sourceMode: "detailed",
      strips: sortedCrossSectionStrips(centerline.cross_section_strips),
    };
  }
  return {
    sourceMode: "seed",
    strips: seedDetailedCrossSection(centerline),
  };
}

function crossSectionPreviewDisplayOrder(strips: AnnotatedCrossSectionStrip[]): AnnotatedCrossSectionStrip[] {
  const sorted = sortedCrossSectionStrips(strips);
  const left = sorted.filter((strip) => strip.zone === "left").reverse();
  const center = sorted.filter((strip) => strip.zone === "center");
  const right = sorted.filter((strip) => strip.zone === "right");
  return [...left, ...center, ...right];
}

function metaurbanStripLabel(kind: StripKind): string {
  return METAAURBAN_STRIP_DISPLAY_LABELS[kind] || STRIP_KIND_LABELS[kind];
}

function metaurbanStripZoneLabel(kind: StripKind): string {
  return METAAURBAN_STRIP_ZONE_LABELS[kind] || kind;
}

function metaurbanAssetBadges(kind: StripKind): MetaurbanAssetBadge[] {
  return METAAURBAN_STRIP_ASSET_BADGES[kind] || [];
}

function stripDirectionChip(strip: AnnotatedCrossSectionStrip): string {
  if (strip.direction === "forward") {
    return "FWD";
  }
  if (strip.direction === "reverse") {
    return "REV";
  }
  if (strip.direction === "bidirectional") {
    return "BI";
  }
  return "STATIC";
}

function stripPreviewFillColor(kind: StripKind): string {
  switch (kind) {
    case "drive_lane":
      return "rgba(66, 74, 87, 0.16)";
    case "bus_lane":
      return "rgba(183, 72, 58, 0.18)";
    case "bike_lane":
      return "rgba(57, 135, 90, 0.18)";
    case "parking_lane":
      return "rgba(166, 130, 86, 0.18)";
    case "median":
      return "rgba(110, 122, 95, 0.16)";
    case "nearroad_buffer":
      return "rgba(152, 152, 152, 0.16)";
    case "nearroad_furnishing":
      return "rgba(126, 101, 71, 0.18)";
    case "clear_sidewalk":
      return "rgba(235, 224, 206, 0.94)";
    case "farfromroad_buffer":
      return "rgba(169, 188, 202, 0.18)";
    case "frontage_reserve":
      return "rgba(183, 212, 230, 0.24)";
    default:
      return "rgba(102, 102, 102, 0.12)";
  }
}

function buildMetaurbanAssetBadgeMarkup(
  kind: StripKind,
  options: {
    emptyMode?: "note" | "omit";
  } = {},
): string {
  const { emptyMode = "omit" } = options;
  const badges = metaurbanAssetBadges(kind);
  if (!badges.length) {
    return emptyMode === "note"
      ? `<span class="scene-micro-note">No MetaUrban asset hints for this strip.</span>`
      : "";
  }
  return `
    <div class="annotation-metaurban-badge-row">
      ${badges
        .map(
          (badge) => `
            <span class="annotation-metaurban-badge" data-asset-key="${escapeHtml(badge.key)}" title="${escapeHtml(badge.label)}">
              ${escapeHtml(badge.shortLabel)}
            </span>
          `,
        )
        .join("")}
    </div>
  `;
}

function requireElement<T extends Element>(root: ParentNode, selector: string): T {
  const element = root.querySelector<T>(selector);
  if (!element) {
    throw new Error(`Missing required annotation element: ${selector}`);
  }
  return element;
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function resolveApiUrl(path: string): string {
  if (/^(https?|blob|data):/i.test(path)) {
    return path;
  }
  if (!path) {
    return "";
  }
  return `${API_BASE}${path.startsWith("/") ? path : `/${path}`}`;
}

function createEmptyAnnotation(planId = "", imagePath = "", imageWidthPx = 0, imageHeightPx = 0): ReferenceAnnotation {
  return {
    version: ANNOTATION_SCHEMA_VERSION,
    plan_id: planId,
    image_path: imagePath,
    image_width_px: imageWidthPx,
    image_height_px: imageHeightPx,
    pixels_per_meter: DEFAULT_PIXELS_PER_METER,
    centerlines: [],
    junctions: [],
    roundabouts: [],
    control_points: [],
    building_regions: [],
  };
}

function normalizePoint(value: unknown): AnnotationPoint {
  const record = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  return {
    x: asNumber(record.x, 0),
    y: asNumber(record.y, 0),
  };
}

function normalizeCenterline(value: unknown, index: number): AnnotatedCenterline {
  const record = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const rawPoints = Array.isArray(record.points) ? record.points : [];
  const driveLaneDefaults = resolveDriveLaneDefaults(record);
  const bikeLaneCount = asNonNegativeInt(record.bike_lane_count, 0);
  const busLaneCount = asNonNegativeInt(record.bus_lane_count, 0);
  const parkingLaneCount = asNonNegativeInt(record.parking_lane_count, 0);
  const referenceWidthPx = asNullableNumber(record.reference_width_px);
  const id = asString(record.id, `centerline_${String(index + 1).padStart(2, "0")}`);
  const crossSectionStrips = Array.isArray(record.cross_section_strips)
    ? record.cross_section_strips.map((item, stripIndex) => normalizeCrossSectionStrip(item, stripIndex, id))
    : [];
  const streetFurnitureInstances = Array.isArray(record.street_furniture_instances)
    ? record.street_furniture_instances.map((item, furnitureIndex) => normalizeStreetFurnitureInstance(item, furnitureIndex, id))
    : [];
  const centerline: AnnotatedCenterline = {
    id,
    label: asString(record.label, asString(record.id, `Centerline ${index + 1}`)),
    points: rawPoints.map((item) => normalizePoint(item)),
    road_width_m: Math.max(
      1,
      asNumber(
        record.road_width_m,
        nominalSeedCrossSectionWidthForCounts(
          driveLaneDefaults.forward_drive_lane_count,
          driveLaneDefaults.reverse_drive_lane_count,
          bikeLaneCount,
          busLaneCount,
          parkingLaneCount,
        ),
      ),
    ),
    reference_width_px: referenceWidthPx === null ? null : Math.max(1, referenceWidthPx),
    forward_drive_lane_count: driveLaneDefaults.forward_drive_lane_count,
    reverse_drive_lane_count: driveLaneDefaults.reverse_drive_lane_count,
    bike_lane_count: bikeLaneCount,
    bus_lane_count: busLaneCount,
    parking_lane_count: parkingLaneCount,
    highway_type: asString(record.highway_type, "annotated_centerline"),
    cross_section_mode:
      asString(record.cross_section_mode, crossSectionStrips.length > 0 ? CROSS_SECTION_MODE_DETAILED : CROSS_SECTION_MODE_COARSE) ===
      CROSS_SECTION_MODE_DETAILED
        ? CROSS_SECTION_MODE_DETAILED
        : CROSS_SECTION_MODE_COARSE,
    cross_section_strips: sortedCrossSectionStrips(crossSectionStrips),
    street_furniture_instances: streetFurnitureInstances,
    start_junction_id: asString(record.start_junction_id, ""),
    end_junction_id: asString(record.end_junction_id, ""),
  };
  syncCenterlineDerivedFields(centerline);
  return centerline;
}

function normalizeMarker(
  value: unknown,
  index: number,
  kindFallback: string,
): AnnotatedMarker {
  const record = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const id = asString(record.id, `${kindFallback}_${String(index + 1).padStart(2, "0")}`);
  return {
    id,
    label: asString(record.label, id),
    x: asNumber(record.x, 0),
    y: asNumber(record.y, 0),
    kind: asString(record.kind, kindFallback),
  };
}

function normalizeJunction(value: unknown, index: number): AnnotatedJunction {
  const record = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const anchorRecord = record.anchor && typeof record.anchor === "object" ? (record.anchor as Record<string, unknown>) : null;
  const id = asString(record.id, `junction_${String(index + 1).padStart(2, "0")}`);
  const rawConnectedIds = Array.isArray(record.connected_centerline_ids) ? record.connected_centerline_ids : [];
  const connectedCenterlineIds = rawConnectedIds.map((item) => asString(item, "")).filter((item) => Boolean(item));
  const sourceMode =
    connectedCenterlineIds.length > 0 || anchorRecord
      ? "explicit"
      : asString(record.source_mode, "legacy_marker") === "explicit"
        ? "explicit"
        : "legacy_marker";
  return {
    id,
    label: asString(record.label, id),
    x: asNumber(record.x, anchorRecord ? asNumber(anchorRecord.x, 0) : 0),
    y: asNumber(record.y, anchorRecord ? asNumber(anchorRecord.y, 0) : 0),
    kind: asString(record.kind, sourceMode === "explicit" ? "t_junction" : "intersection"),
    connected_centerline_ids: [...new Set(connectedCenterlineIds)],
    crosswalk_depth_m: Math.max(0.5, asNumber(record.crosswalk_depth_m, 3)),
    source_mode: sourceMode,
  };
}

function normalizeRoundabout(value: unknown, index: number): AnnotatedRoundabout {
  const record = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const id = asString(record.id, `roundabout_${String(index + 1).padStart(2, "0")}`);
  return {
    id,
    label: asString(record.label, id),
    x: asNumber(record.x, 0),
    y: asNumber(record.y, 0),
    radius_px: Math.max(8, asNumber(record.radius_px, DEFAULT_ROUNDABOUT_RADIUS_PX)),
  };
}

function normalizeBuildingRegion(value: unknown, index: number): AnnotatedBuildingRegion {
  const record = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const centerRecord =
    record.center_px && typeof record.center_px === "object"
      ? (record.center_px as Record<string, unknown>)
      : null;
  const id = asString(record.id, `building_region_${String(index + 1).padStart(2, "0")}`);
  return {
    id,
    label: asString(record.label, id),
    center_px: {
      x: asNumber(centerRecord?.x ?? record.x, 0),
      y: asNumber(centerRecord?.y ?? record.y, 0),
    },
    width_px: Math.max(1, asNumber(record.width_px, 64)),
    height_px: Math.max(1, asNumber(record.height_px, 48)),
    yaw_deg: normalizeAngleDeg(asNumber(record.yaw_deg, 0)),
  };
}

function normalizeAnnotation(value: unknown): ReferenceAnnotation {
  const record = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const centerlines = Array.isArray(record.centerlines)
    ? record.centerlines.map((item, index) => normalizeCenterline(item, index))
    : [];
  const junctions = Array.isArray(record.junctions)
    ? record.junctions.map((item, index) => normalizeJunction(item, index))
    : [];
  const roundabouts = Array.isArray(record.roundabouts)
    ? record.roundabouts.map((item, index) => normalizeRoundabout(item, index))
    : [];
  const controlPoints = Array.isArray(record.control_points)
    ? record.control_points.map((item, index) => normalizeMarker(item, index, "control_point"))
    : [];
  const buildingRegions = Array.isArray(record.building_regions)
    ? record.building_regions.map((item, index) => normalizeBuildingRegion(item, index))
    : [];
  return {
    version: asString(record.version, ANNOTATION_SCHEMA_VERSION),
    plan_id: asString(record.plan_id, "custom_annotation"),
    image_path: asString(record.image_path, ""),
    image_width_px: Math.max(0, Math.round(asNumber(record.image_width_px, 0))),
    image_height_px: Math.max(0, Math.round(asNumber(record.image_height_px, 0))),
    pixels_per_meter: Math.max(0.1, asNumber(record.pixels_per_meter, DEFAULT_PIXELS_PER_METER)),
    centerlines,
    junctions,
    roundabouts,
    control_points: controlPoints,
    building_regions: buildingRegions,
  };
}

function stringifyAnnotation(annotation: ReferenceAnnotation): string {
  return JSON.stringify(annotation, null, 2);
}

function cloneAnnotation(annotation: ReferenceAnnotation): ReferenceAnnotation {
  return normalizeAnnotation(JSON.parse(stringifyAnnotation(annotation)));
}

function setStatus(element: HTMLElement, message: string, tone: StatusTone): void {
  element.textContent = message;
  element.dataset.tone = tone;
}

function nextFeatureId(annotation: ReferenceAnnotation, prefix: string): string {
  const ids = new Set<string>();
  for (const item of annotation.centerlines) {
    ids.add(item.id);
  }
  for (const item of annotation.junctions) {
    ids.add(item.id);
  }
  for (const item of annotation.roundabouts) {
    ids.add(item.id);
  }
  for (const item of annotation.control_points) {
    ids.add(item.id);
  }
  for (const item of annotation.building_regions) {
    ids.add(item.id);
  }
  let counter = 1;
  while (true) {
    const candidate = `${prefix}_${String(counter).padStart(2, "0")}`;
    if (!ids.has(candidate)) {
      return candidate;
    }
    counter += 1;
  }
}

function getFeatureCount(annotation: ReferenceAnnotation): number {
  return (
    annotation.centerlines.length +
    annotation.junctions.length +
    annotation.roundabouts.length +
    annotation.control_points.length +
    annotation.building_regions.length
  );
}

function getSelectedFeature(annotation: ReferenceAnnotation, selection: Selection):
  | AnnotatedCenterline
  | AnnotatedBuildingRegion
  | AnnotatedJunction
  | AnnotatedMarker
  | AnnotatedRoundabout
  | DerivedJunctionOverlay
  | null {
  if (!selection) {
    return null;
  }
  if (selection.kind === "road_collection") {
    return null;
  }
  if (selection.kind === "centerline") {
    return annotation.centerlines.find((item) => item.id === selection.id) ?? null;
  }
  if (selection.kind === "junction") {
    return annotation.junctions.find((item) => item.id === selection.id) ?? null;
  }
  if (selection.kind === "roundabout") {
    return annotation.roundabouts.find((item) => item.id === selection.id) ?? null;
  }
  if (selection.kind === "building_region") {
    return annotation.building_regions.find((item) => item.id === selection.id) ?? null;
  }
  if (selection.kind === "derived_junction") {
    return getJunctionOverlay(annotation, selection.id);
  }
  return annotation.control_points.find((item) => item.id === selection.id) ?? null;
}

function pixelPointToLocal(annotation: ReferenceAnnotation, point: AnnotationPoint): AnnotationPoint {
  const centerX = annotation.image_width_px * 0.5;
  const centerY = annotation.image_height_px * 0.5;
  const ppm = Math.max(annotation.pixels_per_meter, 1e-6);
  return {
    x: (point.x - centerX) / ppm,
    y: (centerY - point.y) / ppm,
  };
}

function collectAnchorClusters(points: AnnotationPoint[], toleranceM: number): Array<{ point: AnnotationPoint; count: number }> {
  const clusters: Array<{ point: AnnotationPoint; count: number }> = [];
  for (const point of points) {
    let matched: { point: AnnotationPoint; count: number } | null = null;
    for (const cluster of clusters) {
      if (pointDistance(cluster.point, point) <= toleranceM) {
        matched = cluster;
        break;
      }
    }
    if (!matched) {
      clusters.push({ point: { ...point }, count: 1 });
      continue;
    }
    const nextCount = matched.count + 1;
    matched.point = {
      x: (matched.point.x * matched.count + point.x) / nextCount,
      y: (matched.point.y * matched.count + point.y) / nextCount,
    };
    matched.count = nextCount;
  }
  return clusters;
}

function normalizeAngleDeg(value: number): number {
  let normalized = value % 360;
  if (normalized < 0) {
    normalized += 360;
  }
  return normalized;
}

function angleDeg(fromPoint: AnnotationPoint, toPoint: AnnotationPoint): number {
  return normalizeAngleDeg((Math.atan2(toPoint.y - fromPoint.y, toPoint.x - fromPoint.x) * 180) / Math.PI);
}

function circularAngleDiffs(anglesDeg: number[]): number[] {
  if (anglesDeg.length === 0) {
    return [];
  }
  const ordered = [...anglesDeg].map(normalizeAngleDeg).sort((a, b) => a - b);
  return ordered.map((value, index) => {
    const nextValue = ordered[(index + 1) % ordered.length];
    return index === ordered.length - 1 ? (nextValue - value) + 360 : nextValue - value;
  });
}

function classifyTopologyJunctionKind(anglesDeg: number[]): "t_junction" | "cross_junction" | "complex_junction" {
  const diffs = circularAngleDiffs(anglesDeg);
  if (anglesDeg.length === 4 && diffs.length > 0 && Math.max(...diffs.map((value) => Math.abs(value - 90))) <= 35) {
    return "cross_junction";
  }
  if (anglesDeg.length === 3 && diffs.some((value) => value >= 145)) {
    return "t_junction";
  }
  return "complex_junction";
}

function deriveTopologyJunctions(annotation: ReferenceAnnotation): Array<{
  anchor: AnnotationPoint;
  armCount: number;
  kind: "t_junction" | "cross_junction" | "complex_junction";
}> {
  const toleranceM = Math.max(DEFAULT_SEGMENT_LENGTH_M * 0.5, 4.0);
  const localCenterlines = annotation.centerlines
    .map((centerline, roadIndex) => ({
      roadId: roadIndex + 1,
      points: centerline.points.map((point) => pixelPointToLocal(annotation, point)),
    }))
    .filter((item) => item.points.length >= 2);
  const clusters: Array<{
    point: AnnotationPoint;
    count: number;
    members: Array<{ roadId: number; vertexIndex: number; points: AnnotationPoint[] }>;
  }> = [];
  for (const road of localCenterlines) {
    road.points.forEach((point, vertexIndex) => {
      let matched = clusters.find((cluster) => pointDistance(cluster.point, point) <= toleranceM) ?? null;
      if (!matched) {
        matched = { point: { ...point }, count: 0, members: [] };
        clusters.push(matched);
      }
      const nextCount = matched.count + 1;
      matched.point = {
        x: (matched.point.x * matched.count + point.x) / nextCount,
        y: (matched.point.y * matched.count + point.y) / nextCount,
      };
      matched.count = nextCount;
      matched.members.push({ roadId: road.roadId, vertexIndex, points: road.points });
    });
  }
  return clusters.flatMap((cluster) => {
    const connectedRoadIds = new Set(cluster.members.map((member) => member.roadId));
    if (connectedRoadIds.size < 2) {
      return [];
    }
    const seenArmKeys = new Set<string>();
    const angles: number[] = [];
    for (const member of cluster.members) {
      for (const neighborIndex of [member.vertexIndex - 1, member.vertexIndex + 1]) {
        if (neighborIndex < 0 || neighborIndex >= member.points.length) {
          continue;
        }
        const neighbor = member.points[neighborIndex];
        if (pointDistance(cluster.point, neighbor) <= Math.max(toleranceM * 0.25, 0.05)) {
          continue;
        }
        const key = `${member.roadId}:${neighbor.x.toFixed(3)}:${neighbor.y.toFixed(3)}`;
        if (seenArmKeys.has(key)) {
          continue;
        }
        seenArmKeys.add(key);
        angles.push(angleDeg(cluster.point, neighbor));
      }
    }
    if (angles.length < 3) {
      return [];
    }
    return [{
      anchor: { ...cluster.point },
      armCount: angles.length,
      kind: classifyTopologyJunctionKind(angles),
    }];
  });
}

function deriveJunctionStats(annotation: ReferenceAnnotation): {
  explicitCount: number;
  legacyCount: number;
  derivedCount: number;
  topologyCount: number;
  tCount: number;
  crossCount: number;
} {
  const toleranceM = Math.max(DEFAULT_SEGMENT_LENGTH_M * 0.5, 4.0);
  const derivedTopologyJunctions = deriveTopologyJunctions(annotation);
  const derivedAnchors = derivedTopologyJunctions.map((item) => item.anchor);
  const explicitAnchors = annotation.junctions
    .filter((item) => item.source_mode === "explicit")
    .map((item) => pixelPointToLocal(annotation, item));
  const topologyAnchors = collectAnchorClusters([...explicitAnchors, ...derivedAnchors], toleranceM);
  return {
    explicitCount: annotation.junctions.filter((item) => item.source_mode === "explicit").length,
    legacyCount: annotation.junctions.filter((item) => item.source_mode !== "explicit").length,
    derivedCount: derivedTopologyJunctions.length,
    topologyCount: topologyAnchors.length,
    tCount: derivedTopologyJunctions.filter((item) => item.kind === "t_junction").length,
    crossCount: derivedTopologyJunctions.filter((item) => item.kind === "cross_junction").length,
  };
}

function buildAnnotationSummaryMarkup(annotation: ReferenceAnnotation): string {
  const roadCount = annotation.centerlines.length;
  const roadWidths = annotation.centerlines.map((item) => getCenterlineCrossSectionWidth(item));
  const referenceWidths = annotation.centerlines.map((item) => getDisplayReferenceWidthPx(item, annotation.pixels_per_meter));
  const driveLaneTotal = annotation.centerlines.reduce(
    (sum, item) => sum + deriveLaneProfile(item).total_drive_lane_count,
    0,
  );
  const bikeLaneTotal = annotation.centerlines.reduce((sum, item) => sum + deriveLaneProfile(item).bike_lane_count, 0);
  const busLaneTotal = annotation.centerlines.reduce((sum, item) => sum + deriveLaneProfile(item).bus_lane_count, 0);
  const parkingLaneTotal = annotation.centerlines.reduce((sum, item) => sum + deriveLaneProfile(item).parking_lane_count, 0);
  const detailedRoadCount = annotation.centerlines.filter((item) => resolvedCrossSectionMode(item) === CROSS_SECTION_MODE_DETAILED).length;
  const stripCount = annotation.centerlines.reduce((sum, item) => sum + item.cross_section_strips.length, 0);
  const furnitureCount = annotation.centerlines.reduce((sum, item) => sum + item.street_furniture_instances.length, 0);
  const buildingRegionCount = annotation.building_regions.length;
  const junctionStats = deriveJunctionStats(annotation);
  return `
    <div>
      <span class="scene-metric-label">Roads</span>
      <strong>${roadCount}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Detailed</span>
      <strong>${detailedRoadCount}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Explicit Jn</span>
      <strong>${junctionStats.explicitCount}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Legacy Jn</span>
      <strong>${junctionStats.legacyCount}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Derived Jn</span>
      <strong>${junctionStats.derivedCount}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Topology Jn</span>
      <strong>${junctionStats.topologyCount}</strong>
    </div>
    <div>
      <span class="scene-metric-label">T / Cross</span>
      <strong>${junctionStats.tCount} / ${junctionStats.crossCount}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Avg Width</span>
      <strong>${roadWidths.length ? (roadWidths.reduce((sum, value) => sum + value, 0) / roadWidths.length).toFixed(1) : "0.0"}m</strong>
    </div>
    <div>
      <span class="scene-metric-label">Max Ref Band</span>
      <strong>${referenceWidths.length ? Math.max(...referenceWidths).toFixed(0) : "0"}px</strong>
    </div>
    <div>
      <span class="scene-metric-label">Drive Lanes</span>
      <strong>${driveLaneTotal}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Bike / Bus</span>
      <strong>${bikeLaneTotal} / ${busLaneTotal}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Parking</span>
      <strong>${parkingLaneTotal}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Strips</span>
      <strong>${stripCount}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Furniture</span>
      <strong>${furnitureCount}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Bldg Regions</span>
      <strong>${buildingRegionCount}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Scale</span>
      <strong>${annotation.pixels_per_meter.toFixed(1)} px/m</strong>
    </div>
  `;
}

function buildGraphSummaryMarkup(graphResult: ConvertedGraphPayload | null): string {
  if (!graphResult) {
    return `
      <div>
        <span class="scene-metric-label">Graph</span>
        <strong>Pending</strong>
      </div>
      <div>
        <span class="scene-metric-label">Segments</span>
        <strong>0</strong>
      </div>
      <div>
        <span class="scene-metric-label">Edges</span>
        <strong>0</strong>
      </div>
      <div>
        <span class="scene-metric-label">Roads</span>
        <strong>0</strong>
      </div>
    `;
  }
  const summary = graphResult.summary;
  return `
    <div>
      <span class="scene-metric-label">Graph</span>
      <strong>${escapeHtml(String(graphResult.graph.mode || "annotation"))}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Segments</span>
      <strong>${escapeHtml(String(summary.segment_count ?? 0))}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Roads</span>
      <strong>${escapeHtml(String(summary.road_profile_count ?? summary.road_count ?? 0))}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Cross Sections</span>
      <strong>${escapeHtml(String(summary.cross_section_profile_count ?? 0))}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Legacy Jn</span>
      <strong>${escapeHtml(String(summary.junction_count ?? 0))}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Derived Jn</span>
      <strong>${escapeHtml(String(summary.derived_junction_count ?? 0))}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Topology Jn</span>
      <strong>${escapeHtml(String(summary.topology_junction_count ?? 0))}</strong>
    </div>
    <div>
      <span class="scene-metric-label">T / Cross</span>
      <strong>${escapeHtml(String(summary.t_junction_count ?? 0))} / ${escapeHtml(String(summary.cross_junction_count ?? 0))}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Junction Segments</span>
      <strong>${escapeHtml(String(summary.junction_segment_count ?? 0))}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Cross Section</span>
      <strong>${escapeHtml(Number(summary.avg_cross_section_width_m ?? 0).toFixed(1))}m avg</strong>
    </div>
    <div>
      <span class="scene-metric-label">Carriageway</span>
      <strong>${escapeHtml(Number(summary.avg_road_width_m ?? 0).toFixed(1))}m avg</strong>
    </div>
    <div>
      <span class="scene-metric-label">Furniture</span>
      <strong>${escapeHtml(String(summary.street_furniture_instance_count ?? 0))}</strong>
    </div>
    <div>
      <span class="scene-metric-label">MetaUrban Hints</span>
      <strong>${escapeHtml(String(summary.metaurban_asset_hint_count ?? 0))}</strong>
    </div>
  `;
}

function buildFeatureTableMarkup(annotation: ReferenceAnnotation): string {
  const rows: string[] = [];
  const derivedJunctions = deriveJunctionOverlayGeometries(annotation);
  for (const centerline of annotation.centerlines) {
    rows.push(`
      <tr>
        <td>centerline</td>
        <td>${escapeHtml(centerline.id)}</td>
        <td>${escapeHtml(centerline.label)}</td>
        <td>${centerline.points.length} pts · ${getCenterlineCrossSectionWidth(centerline).toFixed(1)}m · ${formatCrossSectionSummary(centerline)} · ${centerline.street_furniture_instances.length} furn. · ${escapeHtml(formatLaneSummary(centerline))}</td>
      </tr>
    `);
  }
  for (const item of derivedJunctions.filter((overlay) => overlay.sourceMode === "derived")) {
    rows.push(`
      <tr>
        <td>derived junction</td>
        <td>${escapeHtml(item.junctionId)}</td>
        <td>${escapeHtml(derivedJunctionKindLabel(item.kind))}</td>
        <td>${item.armCount} arms · (${item.anchor.x.toFixed(0)}, ${item.anchor.y.toFixed(0)})</td>
      </tr>
    `);
  }
  for (const item of annotation.junctions) {
    rows.push(`
      <tr>
        <td>junction</td>
        <td>${escapeHtml(item.id)}</td>
        <td>${escapeHtml(item.label)}</td>
        <td>${escapeHtml(item.kind)} · ${escapeHtml(item.source_mode)} · ${item.connected_centerline_ids.length} roads · (${item.x.toFixed(0)}, ${item.y.toFixed(0)})</td>
      </tr>
    `);
  }
  for (const item of annotation.roundabouts) {
    rows.push(`
      <tr>
        <td>roundabout</td>
        <td>${escapeHtml(item.id)}</td>
        <td>${escapeHtml(item.label)}</td>
        <td>r=${item.radius_px.toFixed(0)}px · (${item.x.toFixed(0)}, ${item.y.toFixed(0)})</td>
      </tr>
    `);
  }
  for (const item of annotation.control_points) {
    rows.push(`
      <tr>
        <td>control</td>
        <td>${escapeHtml(item.id)}</td>
        <td>${escapeHtml(item.label)}</td>
        <td>${escapeHtml(item.kind)} · (${item.x.toFixed(0)}, ${item.y.toFixed(0)})</td>
      </tr>
    `);
  }
  for (const item of annotation.building_regions) {
    rows.push(`
      <tr>
        <td>building region</td>
        <td>${escapeHtml(item.id)}</td>
        <td>${escapeHtml(item.label)}</td>
        <td>${item.width_px.toFixed(0)} × ${item.height_px.toFixed(0)}px · yaw ${item.yaw_deg.toFixed(0)}° · (${item.center_px.x.toFixed(0)}, ${item.center_px.y.toFixed(0)})</td>
      </tr>
    `);
  }
  return rows.join("");
}

function buildSelectOptions<T extends string>(
  values: readonly T[],
  selectedValue: T,
  labels: Record<T, string>,
): string {
  return values
    .map(
      (value) =>
        `<option value="${escapeHtml(value)}"${value === selectedValue ? " selected" : ""}>${escapeHtml(labels[value])}</option>`,
    )
    .join("");
}

function stripDirectionMarkup(strip: AnnotatedCrossSectionStrip): string {
  const options =
    strip.zone === "center" && strip.kind !== "median"
      ? STRIP_DIRECTION_OPTIONS
      : (["none"] as const);
  return buildSelectOptions(options, strip.direction, {
    forward: "Forward",
    reverse: "Reverse",
    bidirectional: "Bidirectional",
    none: "None",
  });
}

function stripZoneSideLabel(zone: StripZone): string {
  if (zone === "left") {
    return "Left side";
  }
  if (zone === "right") {
    return "Right side";
  }
  return "Center";
}

function cornerConnectionLabel(quadrantId: string): string {
  const parts = quadrantId.split("_");
  if (parts.length >= 2) {
    return `${parts[parts.length - 2]} ${parts[parts.length - 1]}`;
  }
  return quadrantId.replace(/_/g, " ");
}

function normalizedConnectionPreviewPoints(
  points: AnnotationPoint[],
  width = 96,
  height = 72,
  padding = 10,
): AnnotationPoint[] {
  if (points.length === 0) {
    return [];
  }
  let minX = points[0].x;
  let maxX = points[0].x;
  let minY = points[0].y;
  let maxY = points[0].y;
  for (const point of points) {
    minX = Math.min(minX, point.x);
    maxX = Math.max(maxX, point.x);
    minY = Math.min(minY, point.y);
    maxY = Math.max(maxY, point.y);
  }
  const spanX = Math.max(maxX - minX, 1);
  const spanY = Math.max(maxY - minY, 1);
  const scale = Math.min((width - padding * 2) / spanX, (height - padding * 2) / spanY);
  const drawnWidth = spanX * scale;
  const drawnHeight = spanY * scale;
  const originX = (width - drawnWidth) * 0.5;
  const originY = (height - drawnHeight) * 0.5;
  return points.map((point) => ({
    x: originX + (point.x - minX) * scale,
    y: originY + (point.y - minY) * scale,
  }));
}

function buildCornerConnectionCardMarkup(target: SelectedStripCornerFamilyTarget): string {
  const previewPoints = normalizedConnectionPreviewPoints(target.points);
  const polylinePoints = previewPoints.map((point) => `${point.x},${point.y}`).join(" ");
  const startPoint = previewPoints[0] ?? { x: 12, y: 36 };
  const endPoint = previewPoints[previewPoints.length - 1] ?? { x: 84, y: 36 };
  const quadrantLabel = cornerConnectionLabel(target.quadrantId);
  return `
    <button
      type="button"
      class="annotation-corner-link-card"
      data-action="focus-linked-strip"
      data-centerline-id="${escapeHtml(target.target.centerlineId)}"
      data-strip-id="${escapeHtml(target.target.stripId)}"
    >
      <div class="annotation-corner-link-preview" aria-hidden="true">
        <svg class="annotation-corner-link-svg" viewBox="0 0 96 72" role="presentation">
          <polyline
            points="${polylinePoints}"
            fill="none"
            stroke="${stripStrokeColor(target.stripKind)}"
            stroke-width="10"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
          <circle cx="${startPoint.x}" cy="${startPoint.y}" r="6" fill="#f6f2e8" stroke="${stripStrokeColor(target.stripKind)}" stroke-width="3" />
          <circle cx="${endPoint.x}" cy="${endPoint.y}" r="6" fill="#f6f2e8" stroke="${stripStrokeColor(target.stripKind)}" stroke-width="3" />
        </svg>
      </div>
      <div class="annotation-corner-link-copy">
        <strong>${escapeHtml(target.target.centerlineId)} · ${escapeHtml(target.target.stripId)}</strong>
        <span>${escapeHtml(metaurbanStripLabel(target.target.stripKind))} · ${escapeHtml(stripZoneSideLabel(target.target.stripZone))}</span>
        <span class="annotation-corner-link-junction">via ${escapeHtml(target.junctionId)} · ${escapeHtml(quadrantLabel)}</span>
      </div>
    </button>
  `;
}

function buildStripCornerConnectionsMarkup(
  centerline: AnnotatedCenterline,
  selectedStripId: string | null,
  junctionOverlays: DerivedJunctionOverlay[],
): string {
  const selectedStrip = selectedStripId
    ? centerline.cross_section_strips.find((strip) => strip.strip_id === selectedStripId) ?? null
    : null;
  if (!selectedStrip || !CORNER_LINK_STRIP_KINDS.has(selectedStrip.kind)) {
    return "";
  }
  const targets = selectedStripCornerFamilyTargets(junctionOverlays, centerline.id, selectedStrip.strip_id);
  return `
    <section class="annotation-corner-link-section">
      <div class="annotation-corner-link-header">
        <div>
          <strong>Corner Family</strong>
          <div class="scene-micro-note">${escapeHtml(selectedStrip.strip_id)} · ${escapeHtml(stripZoneSideLabel(selectedStrip.zone))}</div>
        </div>
        <span class="annotation-cross-preview-stat">${targets.length} strip${targets.length === 1 ? "" : "s"}</span>
      </div>
      ${
        targets.length > 0
          ? `
            <div class="annotation-corner-link-list">
              ${targets.map((target) => buildCornerConnectionCardMarkup(target)).join("")}
            </div>
          `
          : `<div class="scene-empty-note">No corner-kernel family is derived for this strip yet.</div>`
      }
    </section>
  `;
}

function buildCrossSectionPreviewMarkup(
  centerline: AnnotatedCenterline,
  selectedStripId: string | null,
  junctionOverlays: DerivedJunctionOverlay[],
): string {
  const preview = previewCrossSection(centerline);
  const isDetailedPreview = preview.sourceMode === "detailed";
  const displayStrips = crossSectionPreviewDisplayOrder(preview.strips);
  const totalWidth = displayStrips.reduce((sum, strip) => sum + Math.max(strip.width_m, 0), 0);
  const bands: string[] = [];
  displayStrips.forEach((strip, index) => {
    const nextStrip = displayStrips[index + 1];
      const selected = selectedStripId === strip.strip_id;
      bands.push(`
        <div
          class="annotation-cross-preview-strip${selected ? " annotation-cross-preview-strip-selected" : ""}"
          data-preview-strip-shell="${escapeHtml(strip.strip_id)}"
          style="flex: ${Math.max(strip.width_m, 0.8)} 0 0; background: ${stripPreviewFillColor(strip.kind)}; border-color: ${stripStrokeColor(strip.kind)};"
        >
          <button
            type="button"
            class="annotation-cross-preview-strip-hitbox"
            data-action="select-preview-strip"
            data-strip-id="${escapeHtml(strip.strip_id)}"
            data-preview-source="${escapeHtml(preview.sourceMode)}"
          >
            <span class="annotation-cross-preview-strip-label">${escapeHtml(metaurbanStripLabel(strip.kind))}</span>
            <span class="annotation-cross-preview-strip-meta">${escapeHtml(strip.width_m.toFixed(2))}m · ${escapeHtml(stripDirectionChip(strip))}</span>
            <span class="annotation-cross-preview-strip-zone">${escapeHtml(metaurbanStripZoneLabel(strip.kind))}</span>
            ${buildMetaurbanAssetBadgeMarkup(strip.kind)}
          </button>
          ${
            isDetailedPreview
              ? `
                <label class="annotation-cross-preview-control">
                  <span>Width</span>
                  <input
                    type="range"
                    min="0.1"
                    max="12"
                    step="0.1"
                    value="${strip.width_m.toFixed(2)}"
                    data-strip-field="width_m"
                    data-strip-id="${escapeHtml(strip.strip_id)}"
                  />
                </label>
              `
              : ""
          }
        </div>
      `);
      if (isDetailedPreview && nextStrip) {
        bands.push(`
          <button
            type="button"
            class="annotation-cross-preview-divider"
            data-action="start-preview-resize"
            data-left-strip-id="${escapeHtml(strip.strip_id)}"
            data-right-strip-id="${escapeHtml(nextStrip.strip_id)}"
            aria-label="Resize boundary between ${escapeHtml(metaurbanStripLabel(strip.kind))} and ${escapeHtml(metaurbanStripLabel(nextStrip.kind))}"
          >
            <span class="annotation-cross-preview-divider-line" aria-hidden="true"></span>
          </button>
        `);
      }
    });
  return `
    <section class="annotation-cross-preview-section">
      <div class="annotation-cross-preview-header">
        <div>
          <h3>Cross Section Preview</h3>
          <div class="scene-micro-note">
            ${escapeHtml(preview.sourceMode === "seed" ? "Seed preview from coarse parameters" : "Detailed cross section")}
          </div>
        </div>
        <div class="annotation-cross-preview-stats">
          <span class="annotation-cross-preview-stat">${escapeHtml(totalWidth.toFixed(2))}m total</span>
          <span class="annotation-cross-preview-stat">${escapeHtml(getCenterlineCarriagewayWidth(centerline).toFixed(2))}m carriageway</span>
        </div>
      </div>
      <div class="annotation-cross-preview-row">
        ${bands.join("")}
      </div>
      <div class="scene-micro-note">
        ${escapeHtml(
          preview.sourceMode === "seed"
            ? "Click a seed band to split this road into editable detailed strips."
            : "Click a band to select it, then adjust width and direction below.",
        )}
      </div>
      ${buildStripCornerConnectionsMarkup(centerline, selectedStripId, junctionOverlays)}
    </section>
  `;
}

function buildSelectedStripEditorMarkup(
  centerline: AnnotatedCenterline,
  selectedStripId: string | null,
  cornerLinkedRoadCount = 0,
): string {
  const strip = selectedStripId
    ? centerline.cross_section_strips.find((item) => item.strip_id === selectedStripId) ?? null
    : null;
  if (!strip) {
    return `
      <section class="annotation-selected-strip-section">
        <div class="annotation-strip-section-header">
          <h3>Selected Strip</h3>
          <span class="scene-micro-note">Click a band in the preview to focus one strip.</span>
        </div>
        <div class="scene-empty-note">No strip is selected yet.</div>
      </section>
    `;
  }
  return `
    <section class="annotation-selected-strip-section">
      <div class="annotation-strip-section-header">
        <h3>Selected Strip</h3>
        <span class="scene-micro-note">${escapeHtml(strip.strip_id)} · ${escapeHtml(metaurbanStripZoneLabel(strip.kind))}</span>
      </div>
      ${buildMetaurbanAssetBadgeMarkup(strip.kind, { emptyMode: "note" })}
      <div class="scene-inspector-grid">
        <label class="scene-form-field">
          <span>Strip ID</span>
          <input type="text" value="${escapeHtml(strip.strip_id)}" readonly />
        </label>
        <label class="scene-form-field">
          <span>Zone</span>
          <input type="text" value="${escapeHtml(strip.zone)}" readonly />
        </label>
        <label class="scene-form-field">
          <span>Kind</span>
          <select data-strip-field="kind" data-strip-id="${escapeHtml(strip.strip_id)}">
            ${buildSelectOptions(
              strip.zone === "center"
                ? (["drive_lane", "bus_lane", "bike_lane", "parking_lane", "median"] as StripKind[])
                : (["nearroad_buffer", "nearroad_furnishing", "clear_sidewalk", "farfromroad_buffer", "frontage_reserve"] as StripKind[]),
              strip.kind,
              STRIP_KIND_LABELS,
            )}
          </select>
        </label>
        <label class="scene-form-field">
          <span>Width (m)</span>
          <input type="number" min="0.1" step="0.1" data-strip-field="width_m" data-strip-id="${escapeHtml(strip.strip_id)}" value="${strip.width_m.toFixed(2)}" />
        </label>
        <label class="scene-form-field">
          <span>Direction</span>
          <select data-strip-field="direction" data-strip-id="${escapeHtml(strip.strip_id)}">
            ${stripDirectionMarkup(strip)}
          </select>
        </label>
        <div class="scene-fact-card">
          <span class="scene-fact-label">MetaUrban Zone</span>
          <strong>${escapeHtml(metaurbanStripZoneLabel(strip.kind))}</strong>
        </div>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Corner-linked Roads</span>
          <strong>${cornerLinkedRoadCount}</strong>
        </div>
        <div class="scene-fact-card scene-form-field-wide">
          <span class="scene-fact-label">Guidance</span>
          <strong>${escapeHtml(METAAURBAN_STRIP_GUIDANCE[strip.kind])}</strong>
        </div>
      </div>
    </section>
  `;
}

function buildMetaurbanAssetGuideMarkup(): string {
  return `
    <section class="annotation-metaurban-guide">
      <div class="annotation-strip-section-header">
        <h3>MetaUrban Asset Hook</h3>
        <span class="scene-micro-note">Placeholder badges now, real assets later.</span>
      </div>
      <div class="annotation-metaurban-guide-lines">
        ${METAAURBAN_ASSET_GUIDE_LINES.map((line) => `<div class="scene-micro-note">${escapeHtml(line)}</div>`).join("")}
      </div>
    </section>
  `;
}

function buildStripSectionMarkup(
  centerline: AnnotatedCenterline,
  zone: StripZone,
  selectedStripId: string | null,
): string {
  const strips = sortedCrossSectionStrips(centerline.cross_section_strips).filter((strip) => strip.zone === zone);
  const rows = strips.length > 0
    ? strips
        .map(
          (strip) => `
            <div class="annotation-strip-row${selectedStripId === strip.strip_id ? " annotation-strip-row-selected" : ""}">
              <div class="annotation-strip-row-header">
                <button type="button" class="scene-toolbar-button scene-toolbar-button-secondary" data-action="select-strip" data-strip-id="${escapeHtml(strip.strip_id)}">
                  ${escapeHtml(strip.strip_id)}
                </button>
                <div class="annotation-strip-row-actions">
                  <button type="button" class="scene-icon-button" data-action="move-strip-up" data-strip-id="${escapeHtml(strip.strip_id)}">↑</button>
                  <button type="button" class="scene-icon-button" data-action="move-strip-down" data-strip-id="${escapeHtml(strip.strip_id)}">↓</button>
                  <button type="button" class="scene-icon-button" data-action="delete-strip" data-strip-id="${escapeHtml(strip.strip_id)}">×</button>
                </div>
              </div>
              <div class="annotation-strip-row-summary">
                <strong>${escapeHtml(metaurbanStripLabel(strip.kind))}</strong>
                <span>${escapeHtml(strip.width_m.toFixed(2))}m</span>
                <span>${escapeHtml(stripDirectionChip(strip))}</span>
                <span>${escapeHtml(metaurbanStripZoneLabel(strip.kind))}</span>
              </div>
              ${buildMetaurbanAssetBadgeMarkup(strip.kind)}
            </div>
          `,
        )
        .join("")
    : `<div class="scene-empty-note">No ${zone} strips yet.</div>`;
  return `
    <section class="annotation-strip-section">
      <div class="annotation-strip-section-header">
        <h3>${escapeHtml(zone.charAt(0).toUpperCase() + zone.slice(1))}</h3>
        <button type="button" class="scene-toolbar-button scene-toolbar-button-secondary" data-action="add-strip" data-zone="${escapeHtml(zone)}">
          Add Strip
        </button>
      </div>
      ${rows}
    </section>
  `;
}

function buildFurnitureMarkup(
  centerline: AnnotatedCenterline,
  selectedStripId: string | null,
  pendingFurnitureKind: FurnitureKind,
  isPlacementArmed: boolean,
): string {
  const selectedStrip = selectedStripId
    ? centerline.cross_section_strips.find((strip) => strip.strip_id === selectedStripId) ?? null
    : null;
  const canPlaceFurniture = Boolean(selectedStrip && FURNITURE_COMPATIBLE_STRIP_KINDS.has(selectedStrip.kind));
  const furnitureRows = centerline.street_furniture_instances.length > 0
    ? centerline.street_furniture_instances
        .map(
          (instance) => `
            <div class="annotation-furniture-row">
              <div class="annotation-furniture-row-header">
                <strong>${escapeHtml(instance.instance_id)}</strong>
                <button type="button" class="scene-icon-button" data-action="delete-furniture" data-instance-id="${escapeHtml(instance.instance_id)}">×</button>
              </div>
              <label class="scene-form-field">
                <span>Kind</span>
                <select data-furniture-field="kind" data-instance-id="${escapeHtml(instance.instance_id)}">
                  ${buildSelectOptions(FURNITURE_KINDS, instance.kind, FURNITURE_KIND_LABELS)}
                </select>
              </label>
              <label class="scene-form-field">
                <span>Strip</span>
                <input type="text" value="${escapeHtml(instance.strip_id)}" readonly />
              </label>
              <label class="scene-form-field">
                <span>Station (m)</span>
                <input type="number" min="0" step="0.1" data-furniture-field="station_m" data-instance-id="${escapeHtml(instance.instance_id)}" value="${instance.station_m.toFixed(2)}" />
              </label>
              <label class="scene-form-field">
                <span>Lateral Offset (m)</span>
                <input type="number" step="0.1" data-furniture-field="lateral_offset_m" data-instance-id="${escapeHtml(instance.instance_id)}" value="${instance.lateral_offset_m.toFixed(2)}" />
              </label>
              <label class="scene-form-field">
                <span>Yaw</span>
                <input type="number" step="1" data-furniture-field="yaw_deg" data-instance-id="${escapeHtml(instance.instance_id)}" value="${instance.yaw_deg === null ? "" : instance.yaw_deg.toFixed(0)}" />
              </label>
            </div>
          `,
        )
        .join("")
    : `<div class="scene-empty-note">No furniture instances yet.</div>`;
  return `
    <section class="annotation-furniture-section">
      <div class="annotation-strip-section-header">
        <h3>Street Furniture</h3>
        <span class="scene-micro-note">${canPlaceFurniture ? `Target: ${escapeHtml(selectedStrip?.strip_id ?? "")}` : "Select a furnishing or frontage strip"}</span>
      </div>
      <div class="annotation-furniture-toolbar">
        <label class="scene-form-field">
          <span>Furniture Kind</span>
          <select id="annotation-inspector-furniture-kind">
            ${buildSelectOptions(FURNITURE_KINDS, pendingFurnitureKind, FURNITURE_KIND_LABELS)}
          </select>
        </label>
        <button type="button" class="scene-toolbar-button" data-action="${isPlacementArmed ? "cancel-furniture-placement" : "arm-furniture-placement"}" ${canPlaceFurniture ? "" : "disabled"}>
          ${isPlacementArmed ? "Cancel Placement" : "Place on Canvas"}
        </button>
      </div>
      ${furnitureRows}
    </section>
  `;
}

function buildBuildingRegionInspectorMarkup(region: AnnotatedBuildingRegion): string {
  const widthM = region.width_px;
  const heightM = region.height_px;
  return `
    <section class="annotation-cross-preview-section">
      <div class="annotation-cross-preview-header">
        <div>
          <h3>Building Region</h3>
          <div class="scene-micro-note">Rotated rectangle for building generation and orientation override.</div>
        </div>
        <div class="annotation-cross-preview-stats">
          <span class="annotation-cross-preview-stat">${widthM.toFixed(0)}px × ${heightM.toFixed(0)}px</span>
          <span class="annotation-cross-preview-stat">${region.yaw_deg.toFixed(0)}°</span>
        </div>
      </div>
      <div class="scene-inspector-grid">
        <label class="scene-form-field">
          <span>ID</span>
          <input id="annotation-region-id" type="text" value="${escapeHtml(region.id)}" />
        </label>
        <label class="scene-form-field scene-form-field-wide">
          <span>Label</span>
          <input id="annotation-region-label" type="text" value="${escapeHtml(region.label)}" />
        </label>
        <label class="scene-form-field">
          <span>Center X</span>
          <input id="annotation-region-center-x" type="number" step="1" value="${region.center_px.x.toFixed(0)}" />
        </label>
        <label class="scene-form-field">
          <span>Center Y</span>
          <input id="annotation-region-center-y" type="number" step="1" value="${region.center_px.y.toFixed(0)}" />
        </label>
        <label class="scene-form-field">
          <span>Width (px)</span>
          <input id="annotation-region-width" type="number" min="${BUILDING_REGION_MIN_SIZE_PX}" step="1" value="${region.width_px.toFixed(0)}" />
        </label>
        <label class="scene-form-field">
          <span>Height (px)</span>
          <input id="annotation-region-height" type="number" min="${BUILDING_REGION_MIN_SIZE_PX}" step="1" value="${region.height_px.toFixed(0)}" />
        </label>
        <label class="scene-form-field">
          <span>Yaw (deg)</span>
          <input id="annotation-region-yaw" type="number" step="1" value="${region.yaw_deg.toFixed(0)}" />
        </label>
        <div class="scene-fact-card scene-form-field-wide">
          <span class="scene-fact-label">Generation Rule</span>
          <strong>Buildings intersecting this region use its orientation. Later regions override earlier ones.</strong>
        </div>
      </div>
    </section>
  `;
}

function buildJunctionInspectorMarkup(
  junction: AnnotatedJunction,
  overlay: DerivedJunctionOverlay | null,
): string {
  if (!overlay) {
    return `
      <div class="scene-inspector-grid">
        <label class="scene-form-field">
          <span>ID</span>
          <input id="annotation-inspector-id" type="text" value="${escapeHtml(junction.id)}" />
        </label>
        <label class="scene-form-field scene-form-field-wide">
          <span>Label</span>
          <input id="annotation-inspector-label" type="text" value="${escapeHtml(junction.label)}" />
        </label>
        <label class="scene-form-field">
          <span>X</span>
          <input id="annotation-inspector-x" type="number" step="1" value="${junction.x.toFixed(0)}" />
        </label>
        <label class="scene-form-field">
          <span>Y</span>
          <input id="annotation-inspector-y" type="number" step="1" value="${junction.y.toFixed(0)}" />
        </label>
        <label class="scene-form-field scene-form-field-wide">
          <span>Kind</span>
          <input id="annotation-inspector-kind" type="text" value="${escapeHtml(junction.kind)}" />
        </label>
      </div>
    `;
  }
  const groupedControlPoints = overlay.subLaneControlPoints.reduce<Record<string, number>>((acc, item) => {
    const key = `${item.stripKind}:${item.pointKind}`;
    acc[key] = (acc[key] ?? 0) + 1;
    return acc;
  }, {});
  const ownershipLabel = overlay.sourceMode === "explicit" ? "explicit junction" : "derived topology overlay";
  return `
    <section class="annotation-cross-preview-section">
      <div class="annotation-cross-preview-header">
        <div>
          <h3>${escapeHtml(derivedJunctionKindLabel(overlay.kind))}</h3>
          <div class="scene-micro-note">${escapeHtml(junction.id)} · ${escapeHtml(ownershipLabel)}</div>
        </div>
        <div class="annotation-cross-preview-stats">
          <span class="annotation-cross-preview-stat">${overlay.armCount} arms</span>
          <span class="annotation-cross-preview-stat">${overlay.crosswalks.length} crossings</span>
        </div>
      </div>
      <div class="scene-inspector-grid">
        <div class="scene-fact-card">
          <span class="scene-fact-label">Anchor</span>
          <strong>${junction.x.toFixed(0)}, ${junction.y.toFixed(0)}</strong>
        </div>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Crosswalk Depth</span>
          <strong>${junction.crosswalk_depth_m.toFixed(1)}m</strong>
        </div>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Connected Arms</span>
          <strong>${overlay.connectedCenterlineIds.length}</strong>
        </div>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Approach Splits</span>
          <strong>${overlay.approachBoundaries.length}</strong>
        </div>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Zebra Boundary Feet</span>
          <strong>${overlay.skeletonFootPoints.length}</strong>
        </div>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Corner Focuses</span>
          <strong>${overlay.cornerFocusPoints.length}</strong>
        </div>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Sub-lane Control Points</span>
          <strong>${overlay.subLaneControlPoints.length}</strong>
        </div>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Boundary Extensions</span>
          <strong>${overlay.boundaryExtensionLines.length}</strong>
        </div>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Focus Guides</span>
          <strong>${overlay.focusGuideLines.length}</strong>
        </div>
        <div class="scene-fact-card scene-form-field-wide">
          <span class="scene-fact-label">Connected Centerlines</span>
          <strong>${escapeHtml(overlay.connectedCenterlineIds.join(" · "))}</strong>
        </div>
        <div class="scene-fact-card scene-form-field-wide">
          <span class="scene-fact-label">Owned Geometry</span>
          <strong>Rectangular carriageway core, zebra boundaries, sidewalk corners, near-road corners, frontage corners.</strong>
        </div>
      </div>
      <div class="annotation-junction-control-list">
        ${Object.keys(groupedControlPoints).length > 0
          ? Object.entries(groupedControlPoints)
              .map(
                ([key, count]) => `
                  <div class="scene-fact-card">
                    <span class="scene-fact-label">${escapeHtml(key)}</span>
                    <strong>${count}</strong>
                  </div>
                `,
              )
              .join("")
          : `<div class="scene-empty-note">No derived control points for this junction.</div>`}
      </div>
    </section>
  `;
}

function buildRoadCollectionInspectorMarkup(annotation: ReferenceAnnotation): string {
  const roads = annotation.centerlines;
  const ppm = Math.max(annotation.pixels_per_meter, 1e-6);
  const junctionOverlays = deriveJunctionOverlayGeometries(annotation);
  const totalLengthM = roads.reduce((sum, centerline) => {
    return (
      sum +
      (clippedCenterlineDisplaySegments(centerline, junctionOverlays, ppm).reduce(
        (segmentSum, segment) => segmentSum + polylineLength(segment.points),
        0,
      ) /
        ppm)
    );
  }, 0);
  const detailedRoadCount = roads.filter((item) => resolvedCrossSectionMode(item) === CROSS_SECTION_MODE_DETAILED).length;
  const coarseRoadCount = roads.length - detailedRoadCount;
  const averageWidthM =
    roads.length > 0
      ? roads.reduce((sum, centerline) => sum + getCenterlineCrossSectionWidth(centerline), 0) / roads.length
      : 0;
  const averageDriveLanes =
    roads.length > 0
      ? roads.reduce((sum, centerline) => sum + deriveLaneProfile(centerline).total_drive_lane_count, 0) / roads.length
      : 0;
  const roadList = roads
    .slice()
    .sort((a, b) => a.id.localeCompare(b.id))
    .map((centerline) => escapeHtml(centerline.id))
    .join(" · ");
  return `
    <section class="annotation-cross-preview-section">
      <div class="annotation-cross-preview-header">
        <div>
          <h3>All Roads</h3>
          <div class="scene-micro-note">aggregated road selection</div>
        </div>
        <div class="annotation-cross-preview-stats">
          <span class="annotation-cross-preview-stat">${roads.length} roads</span>
          <span class="annotation-cross-preview-stat">${detailedRoadCount} detailed</span>
        </div>
      </div>
      <div class="scene-inspector-grid">
        <div class="scene-fact-card">
          <span class="scene-fact-label">Road Count</span>
          <strong>${roads.length}</strong>
        </div>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Detailed</span>
          <strong>${detailedRoadCount}</strong>
        </div>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Coarse</span>
          <strong>${coarseRoadCount}</strong>
        </div>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Total Length</span>
          <strong>${totalLengthM.toFixed(1)}m</strong>
        </div>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Average Width</span>
          <strong>${averageWidthM.toFixed(2)}m</strong>
        </div>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Average Drive Lanes</span>
          <strong>${averageDriveLanes.toFixed(1)}</strong>
        </div>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Total Strips</span>
          <strong>${roads.reduce((sum, centerline) => sum + centerline.cross_section_strips.length, 0)}</strong>
        </div>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Total Furniture</span>
          <strong>${roads.reduce((sum, centerline) => sum + centerline.street_furniture_instances.length, 0)}</strong>
        </div>
        <div class="scene-fact-card scene-form-field-wide">
          <span class="scene-fact-label">Road IDs</span>
          <strong>${roadList || "No roads yet."}</strong>
        </div>
      </div>
    </section>
  `;
}

function buildInspectorMarkup(
  annotation: ReferenceAnnotation,
  selection: Selection,
  selectedStripId: string | null,
  pendingFurnitureKind: FurnitureKind,
  isFurniturePlacementArmed: boolean,
): string {
  if (!selection) {
    return `<div class="scene-empty-note">选择一条中心线、路口、环岛、控制点或建筑区域后，可以在这里编辑属性。</div>`;
  }
  if (selection.kind === "road_collection") {
    return buildRoadCollectionInspectorMarkup(annotation);
  }
  const feature = getSelectedFeature(annotation, selection);
  if (!feature) {
    return `<div class="scene-empty-note">当前选择的要素已经不存在。</div>`;
  }
  if (selection.kind === "building_region") {
    return buildBuildingRegionInspectorMarkup(feature as AnnotatedBuildingRegion);
  }
  if (selection.kind === "centerline") {
    const centerline = feature as AnnotatedCenterline;
    const junctionOverlays = deriveJunctionOverlayGeometries(annotation);
    const cornerFamilyTargets = selectedStripId
      ? selectedStripCornerFamilyTargets(junctionOverlays, centerline.id, selectedStripId)
      : [];
    const linkedRoadIds = new Set(cornerFamilyTargets.map((target) => target.target.centerlineId));
    const referenceWidthMeters = getReferenceWidthMeters(centerline, annotation.pixels_per_meter);
    const profile = deriveLaneProfile(centerline);
    const detailed = resolvedCrossSectionMode(centerline) === CROSS_SECTION_MODE_DETAILED;
    const nominalWidth = nominalSeedCrossSectionWidth(centerline);
    const canCalibratePixelsPerMeter = centerline.reference_width_px !== null && centerline.reference_width_px > 0;
    return `
      ${buildCrossSectionPreviewMarkup(centerline, selectedStripId, junctionOverlays)}
      <div class="scene-inspector-grid">
        <label class="scene-form-field">
          <span>ID</span>
          <input id="annotation-inspector-id" type="text" value="${escapeHtml(centerline.id)}" />
        </label>
        <label class="scene-form-field scene-form-field-wide">
          <span>Label</span>
          <input id="annotation-inspector-label" type="text" value="${escapeHtml(centerline.label)}" />
        </label>
        <label class="scene-form-field">
          <span>Total Width (m)</span>
          <input id="annotation-inspector-road-width" type="number" min="1" step="0.5" value="${centerline.road_width_m.toFixed(2)}" ${detailed ? "readonly" : ""} />
        </label>
        <label class="scene-form-field">
          <span>Reference Width (px)</span>
          <input id="annotation-inspector-reference-width" type="number" min="1" step="1" placeholder="auto" value="${centerline.reference_width_px === null ? "" : centerline.reference_width_px.toFixed(0)}" />
        </label>
        <label class="scene-form-field">
          <span>Forward Drive</span>
          <input id="annotation-inspector-forward-drive-lanes" type="number" min="0" step="1" value="${centerline.forward_drive_lane_count}" ${detailed ? "disabled" : ""} />
        </label>
        <label class="scene-form-field">
          <span>Reverse Drive</span>
          <input id="annotation-inspector-reverse-drive-lanes" type="number" min="0" step="1" value="${centerline.reverse_drive_lane_count}" ${detailed ? "disabled" : ""} />
        </label>
        <label class="scene-form-field">
          <span>Bike Lanes</span>
          <input id="annotation-inspector-bike-lanes" type="number" min="0" step="1" value="${centerline.bike_lane_count}" ${detailed ? "disabled" : ""} />
        </label>
        <label class="scene-form-field">
          <span>Bus Lanes</span>
          <input id="annotation-inspector-bus-lanes" type="number" min="0" step="1" value="${centerline.bus_lane_count}" ${detailed ? "disabled" : ""} />
        </label>
        <label class="scene-form-field">
          <span>Parking Lanes</span>
          <input id="annotation-inspector-parking-lanes" type="number" min="0" step="1" value="${centerline.parking_lane_count}" ${detailed ? "disabled" : ""} />
        </label>
        <label class="scene-form-field scene-form-field-wide">
          <span>Highway Type</span>
          <input id="annotation-inspector-highway-type" type="text" value="${escapeHtml(centerline.highway_type)}" />
        </label>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Mode</span>
          <strong>${detailed ? "Detailed" : "Coarse"}</strong>
        </div>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Reference Width (m)</span>
          <strong>${referenceWidthMeters === null ? "auto" : referenceWidthMeters.toFixed(2)}</strong>
        </div>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Carriageway</span>
          <strong>${getCenterlineCarriagewayWidth(centerline).toFixed(2)}m</strong>
        </div>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Lane Summary</span>
          <strong>${profile.total_drive_lane_count} drive · ${profile.total_lane_count} total</strong>
        </div>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Drive Lane Width</span>
          <strong>${NOMINAL_STRIP_WIDTHS.drive_lane.toFixed(2)}m target</strong>
        </div>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Pixels / Meter</span>
          <strong>${annotation.pixels_per_meter.toFixed(2)} px/m</strong>
        </div>
        <div class="scene-fact-card scene-form-field-wide">
          <span class="scene-fact-label">Geometry</span>
          <strong>${centerline.points.length} vertices${selection.vertexIndex !== undefined ? ` · selected vertex ${selection.vertexIndex + 1}` : ""}</strong>
        </div>
        <div class="annotation-detail-actions scene-form-field-wide">
          ${
            !detailed
              ? `<button type="button" class="scene-toolbar-button scene-toolbar-button-secondary" data-action="reset-road-width-to-nominal">
                  Reset Width to Nominal ${escapeHtml(nominalWidth.toFixed(2))}m
                </button>`
              : ""
          }
          <button
            type="button"
            class="scene-toolbar-button scene-toolbar-button-secondary"
            data-action="calibrate-pixels-per-meter"
            ${canCalibratePixelsPerMeter ? "" : "disabled"}
          >
            Calibrate Pixels / Meter from Reference Width
          </button>
          <button type="button" class="scene-toolbar-button" data-action="split-centerline">
            ${detailed ? "Reseed Cross Section" : "Split to Cross Section"}
          </button>
          ${detailed ? `<button type="button" class="scene-toolbar-button scene-toolbar-button-secondary" data-action="collapse-centerline">Back to Coarse</button>` : ""}
        </div>
      </div>
      ${
        detailed
          ? `
            ${buildSelectedStripEditorMarkup(centerline, selectedStripId, linkedRoadIds.size)}
            <div class="annotation-detailed-layout">
              ${buildStripSectionMarkup(centerline, "left", selectedStripId)}
              ${buildStripSectionMarkup(centerline, "center", selectedStripId)}
              ${buildStripSectionMarkup(centerline, "right", selectedStripId)}
              ${buildFurnitureMarkup(centerline, selectedStripId, pendingFurnitureKind, isFurniturePlacementArmed)}
            </div>
            ${buildMetaurbanAssetGuideMarkup()}
          `
          : `
            <div class="scene-empty-note">先把总宽度和参考图调准；你现在也可以直接点击上方 seed 横截面中的任一部分，自动进入 detailed 编辑。</div>
            ${buildMetaurbanAssetGuideMarkup()}
          `
      }
    `;
  }
  if (selection.kind === "junction") {
    return buildJunctionInspectorMarkup(
      feature as AnnotatedJunction,
      getJunctionOverlay(annotation, selection.id),
    );
  }
  if (selection.kind === "derived_junction") {
    const junction = feature as DerivedJunctionOverlay;
    return buildJunctionInspectorMarkup(
      {
        id: junction.junctionId,
        label: junction.junctionId,
        x: junction.anchor.x,
        y: junction.anchor.y,
        kind: junction.kind,
        connected_centerline_ids: junction.connectedCenterlineIds,
        crosswalk_depth_m: 3,
        source_mode: "legacy_marker",
      },
      junction,
    );
  }
  if (selection.kind === "roundabout") {
    const roundabout = feature as AnnotatedRoundabout;
    return `
      <div class="scene-inspector-grid">
        <label class="scene-form-field">
          <span>ID</span>
          <input id="annotation-inspector-id" type="text" value="${escapeHtml(roundabout.id)}" />
        </label>
        <label class="scene-form-field scene-form-field-wide">
          <span>Label</span>
          <input id="annotation-inspector-label" type="text" value="${escapeHtml(roundabout.label)}" />
        </label>
        <label class="scene-form-field">
          <span>Center X</span>
          <input id="annotation-inspector-x" type="number" step="1" value="${roundabout.x.toFixed(0)}" />
        </label>
        <label class="scene-form-field">
          <span>Center Y</span>
          <input id="annotation-inspector-y" type="number" step="1" value="${roundabout.y.toFixed(0)}" />
        </label>
        <label class="scene-form-field scene-form-field-wide">
          <span>Radius (px)</span>
          <input id="annotation-inspector-radius" type="number" min="8" step="1" value="${roundabout.radius_px.toFixed(0)}" />
        </label>
      </div>
    `;
  }
  const marker = feature as AnnotatedMarker;
  return `
    <div class="scene-inspector-grid">
      <label class="scene-form-field">
        <span>ID</span>
        <input id="annotation-inspector-id" type="text" value="${escapeHtml(marker.id)}" />
      </label>
      <label class="scene-form-field scene-form-field-wide">
        <span>Label</span>
        <input id="annotation-inspector-label" type="text" value="${escapeHtml(marker.label)}" />
      </label>
      <label class="scene-form-field">
        <span>X</span>
        <input id="annotation-inspector-x" type="number" step="1" value="${marker.x.toFixed(0)}" />
      </label>
      <label class="scene-form-field">
        <span>Y</span>
        <input id="annotation-inspector-y" type="number" step="1" value="${marker.y.toFixed(0)}" />
      </label>
      <label class="scene-form-field scene-form-field-wide">
        <span>Kind</span>
        <input id="annotation-inspector-kind" type="text" value="${escapeHtml(marker.kind)}" />
      </label>
    </div>
  `;
}

function stripStrokeColor(kind: StripKind): string {
  switch (kind) {
    case "drive_lane":
      return "rgba(66, 74, 87, 0.82)";
    case "bus_lane":
      return "rgba(183, 72, 58, 0.78)";
    case "bike_lane":
      return "rgba(57, 135, 90, 0.78)";
    case "parking_lane":
      return "rgba(166, 130, 86, 0.75)";
    case "median":
      return "rgba(110, 122, 95, 0.72)";
    case "nearroad_buffer":
      return "rgba(152, 152, 152, 0.4)";
    case "nearroad_furnishing":
      return "rgba(126, 101, 71, 0.56)";
    case "clear_sidewalk":
      return "rgba(235, 224, 206, 0.86)";
    case "farfromroad_buffer":
      return "rgba(169, 188, 202, 0.42)";
    case "frontage_reserve":
      return "rgba(183, 212, 230, 0.58)";
    default:
      return "rgba(102, 102, 102, 0.6)";
  }
}

function dedupeAdjacentDisplayPoints(points: AnnotationPoint[]): AnnotationPoint[] {
  const deduped: AnnotationPoint[] = [];
  for (const point of points) {
    if (!deduped.length || pointDistance(deduped[deduped.length - 1], point) > 1e-3) {
      deduped.push(clonePoint(point));
    }
  }
  return deduped;
}

function junctionOverlayTolerancePx(pixelsPerMeter: number): number {
  return Math.max(pixelsPerMeter * 0.35, 4);
}

function selectClipPointForNeighbor(
  vertex: AnnotationPoint,
  neighbor: AnnotationPoint,
  candidates: AnnotationPoint[],
): AnnotationPoint | null {
  const directionX = neighbor.x - vertex.x;
  const directionY = neighbor.y - vertex.y;
  const directionLength = Math.hypot(directionX, directionY);
  if (directionLength <= 1e-6) {
    return null;
  }
  let bestScore = -Infinity;
  let bestPoint: AnnotationPoint | null = null;
  for (const candidate of candidates) {
    const clipX = candidate.x - vertex.x;
    const clipY = candidate.y - vertex.y;
    const clipLength = Math.hypot(clipX, clipY);
    if (clipLength <= 1e-6) {
      continue;
    }
    const score =
      (directionX / directionLength) * (clipX / clipLength) +
      (directionY / directionLength) * (clipY / clipLength);
    if (score > 0.5 && score > bestScore) {
      bestScore = score;
      bestPoint = clonePoint(candidate);
    }
  }
  return bestPoint;
}

function skeletonClipPointForNeighbor(
  centerline: AnnotatedCenterline,
  vertex: AnnotationPoint,
  neighbor: AnnotationPoint,
  junctionOverlays: DerivedJunctionOverlay[],
  pixelsPerMeter: number,
): AnnotationPoint | null {
  const tolerancePx = junctionOverlayTolerancePx(pixelsPerMeter);
  const candidates: AnnotationPoint[] = [];
  for (const overlay of junctionOverlays) {
    if (pointDistance(overlay.anchor, vertex) > tolerancePx) {
      continue;
    }
    for (const footPoint of overlay.skeletonFootPoints) {
      if (footPoint.centerlineId === centerline.id) {
        candidates.push(footPoint.point);
      }
    }
  }
  return selectClipPointForNeighbor(vertex, neighbor, candidates);
}

function stripClipPointForNeighbor(
  centerline: AnnotatedCenterline,
  stripId: string,
  vertex: AnnotationPoint,
  neighbor: AnnotationPoint,
  junctionOverlays: DerivedJunctionOverlay[],
  pixelsPerMeter: number,
): AnnotationPoint | null {
  const tolerancePx = junctionOverlayTolerancePx(pixelsPerMeter);
  const candidates: AnnotationPoint[] = [];
  for (const overlay of junctionOverlays) {
    if (pointDistance(overlay.anchor, vertex) > tolerancePx) {
      continue;
    }
    for (const controlPoint of overlay.subLaneControlPoints) {
      if (
        controlPoint.centerlineId === centerline.id &&
        controlPoint.stripId === stripId &&
        controlPoint.pointKind === "center_control_point"
      ) {
        candidates.push(controlPoint.point);
      }
    }
  }
  return selectClipPointForNeighbor(vertex, neighbor, candidates);
}

function baseCenterlineDisplaySegments(
  centerline: AnnotatedCenterline,
  junctionOverlays: DerivedJunctionOverlay[],
  pixelsPerMeter: number,
): AnnotationPoint[][] {
  const points = centerline.points.map((point) => clonePoint(point));
  if (points.length < 2) {
    return [];
  }
  const tolerancePx = junctionOverlayTolerancePx(pixelsPerMeter);
  const segments: AnnotationPoint[][] = [];
  let currentSegment: AnnotationPoint[] = [clonePoint(points[0])];
  for (let index = 1; index < points.length; index += 1) {
    currentSegment.push(clonePoint(points[index]));
    const isInternalVertex = index > 0 && index < points.length - 1;
    const shouldSplit =
      isInternalVertex &&
      junctionOverlays.some(
        (overlay) =>
          pointDistance(overlay.anchor, points[index]) <= tolerancePx &&
          overlay.skeletonFootPoints.filter((item) => item.centerlineId === centerline.id).length >= 2,
      );
    if (!shouldSplit) {
      continue;
    }
    const dedupedSegment = dedupeAdjacentDisplayPoints(currentSegment);
    if (dedupedSegment.length >= 2) {
      segments.push(dedupedSegment);
    }
    currentSegment = [clonePoint(points[index])];
  }
  const dedupedSegment = dedupeAdjacentDisplayPoints(currentSegment);
  if (dedupedSegment.length >= 2) {
    segments.push(dedupedSegment);
  }
  return segments;
}

function clippedCenterlineDisplaySegments(
  centerline: AnnotatedCenterline,
  junctionOverlays: DerivedJunctionOverlay[],
  pixelsPerMeter: number,
): ClippedDisplaySegment[] {
  return baseCenterlineDisplaySegments(centerline, junctionOverlays, pixelsPerMeter)
    .map((segment) => {
      const clipped = segment.map((point) => clonePoint(point));
      const startClip = skeletonClipPointForNeighbor(centerline, segment[0], segment[1], junctionOverlays, pixelsPerMeter);
      const endClip = skeletonClipPointForNeighbor(
        centerline,
        segment[segment.length - 1],
        segment[segment.length - 2],
        junctionOverlays,
        pixelsPerMeter,
      );
      if (startClip) {
        clipped[0] = startClip;
      }
      if (endClip) {
        clipped[clipped.length - 1] = endClip;
      }
      const points = dedupeAdjacentDisplayPoints(clipped);
      return {
        points,
        clippedStart: startClip !== null,
        clippedEnd: endClip !== null,
      };
    })
    .filter((segment) => segment.points.length >= 2);
}

function clippedStripDisplaySegments(
  centerline: AnnotatedCenterline,
  stripId: string,
  centerOffsetM: number,
  pixelsPerMeter: number,
  junctionOverlays: DerivedJunctionOverlay[],
): ClippedDisplaySegment[] {
  return baseCenterlineDisplaySegments(centerline, junctionOverlays, pixelsPerMeter)
    .map((segment) => {
      const offsetPoints = offsetPolyline(segment, centerOffsetM * pixelsPerMeter);
      if (offsetPoints.length < 2) {
        return null;
      }
      const startClip = stripClipPointForNeighbor(
        centerline,
        stripId,
        segment[0],
        segment[1],
        junctionOverlays,
        pixelsPerMeter,
      );
      const endClip = stripClipPointForNeighbor(
        centerline,
        stripId,
        segment[segment.length - 1],
        segment[segment.length - 2],
        junctionOverlays,
        pixelsPerMeter,
      );
      if (startClip) {
        offsetPoints[0] = startClip;
      }
      if (endClip) {
        offsetPoints[offsetPoints.length - 1] = endClip;
      }
      const points = dedupeAdjacentDisplayPoints(offsetPoints);
      if (points.length < 2) {
        return null;
      }
      return {
        points,
        clippedStart: startClip !== null,
        clippedEnd: endClip !== null,
      };
    })
    .filter((segment): segment is ClippedDisplaySegment => segment !== null);
}

function buildCenterlineOverlayMarkup(
  centerline: AnnotatedCenterline,
  pixelsPerMeter: number,
  isSelected: boolean,
  selectedVertexIndex: number | undefined,
  selectedStripId: string | null,
  junctionOverlays: DerivedJunctionOverlay[],
  linkedStripKeys: Set<string>,
): string {
  const displaySegments = clippedCenterlineDisplaySegments(centerline, junctionOverlays, pixelsPerMeter);
  if (displaySegments.length === 0) {
    return "";
  }
  const anySegmentClipped = displaySegments.some((segment) => segment.clippedStart || segment.clippedEnd);
  const labelPoint = displaySegments[0]?.points[0] ?? centerline.points[0] ?? { x: 0, y: 0 };
  const centerlineWidthPx = getDisplayCenterlineWidthPx(pixelsPerMeter);
  const vertexMarkup = centerline.points
    .map((point, index) => {
      const vertexSelected = isSelected && selectedVertexIndex === index;
      return `
        <circle
          class="annotation-vertex${vertexSelected ? " annotation-vertex-selected" : ""}"
          cx="${point.x}"
          cy="${point.y}"
          r="6"
          data-feature-kind="centerline"
          data-feature-id="${escapeHtml(centerline.id)}"
          data-vertex-index="${index}"
        />
      `;
    })
    .join("");

  let bandMarkup = "";
  if (resolvedCrossSectionMode(centerline) === CROSS_SECTION_MODE_DETAILED && centerline.cross_section_strips.length > 0) {
    const offsets = stripCenterOffsetMeters(centerline);
    bandMarkup = sortedCrossSectionStrips(centerline.cross_section_strips)
      .map((strip) => {
        const stripOffset = offsets[strip.strip_id];
        const isStripSelected = selectedStripId === strip.strip_id;
        const isLinkedStrip = linkedStripKeys.has(`${centerline.id}:${strip.strip_id}`);
        return clippedStripDisplaySegments(
          centerline,
          strip.strip_id,
          stripOffset.centerOffsetM,
          pixelsPerMeter,
          junctionOverlays,
        )
          .map((segment) => {
            const offsetPolylinePoints = segment.points.map((point) => `${point.x},${point.y}`).join(" ");
            return `
              <polyline
                class="annotation-cross-strip${isStripSelected ? " annotation-cross-strip-selected" : isLinkedStrip ? " annotation-cross-strip-linked" : ""}"
                points="${offsetPolylinePoints}"
                style="stroke: ${stripStrokeColor(strip.kind)}; stroke-width: ${Math.max(2, strip.width_m * pixelsPerMeter)}px; stroke-linecap: ${segment.clippedStart || segment.clippedEnd ? "butt" : "round"}"
                data-feature-kind="centerline"
                data-feature-id="${escapeHtml(centerline.id)}"
              />
            `;
          })
          .join("");
      })
      .join("");
  } else {
    const roadBandWidthPx = getDisplayReferenceWidthPx(centerline, pixelsPerMeter);
    bandMarkup = displaySegments
      .map(
        (segment) => `
      <polyline
        class="annotation-road-band${isSelected ? " annotation-feature-selected" : ""}"
        points="${segment.points.map((point) => `${point.x},${point.y}`).join(" ")}"
        style="stroke-width: ${roadBandWidthPx}px; stroke-linecap: ${segment.clippedStart || segment.clippedEnd ? "butt" : "round"}"
        data-feature-kind="centerline"
        data-feature-id="${escapeHtml(centerline.id)}"
      />
    `,
      )
      .join("");
  }

  const furnitureMarkup = centerline.street_furniture_instances
    .map((instance) => {
      const point = stripDisplayPoint(
        centerline,
        instance.strip_id,
        instance.station_m * pixelsPerMeter,
        instance.lateral_offset_m * pixelsPerMeter,
        pixelsPerMeter,
      );
      if (!point) {
        return "";
      }
      return `
        <g class="annotation-feature-group">
          <circle class="annotation-furniture-point" cx="${point.x}" cy="${point.y}" r="6" />
          <text class="annotation-furniture-label" x="${point.x + 10}" y="${point.y - 8}">
            ${escapeHtml(FURNITURE_KIND_LABELS[instance.kind])}
          </text>
        </g>
      `;
    })
    .join("");

  return `
    <g class="annotation-feature-group">
      ${bandMarkup}
      ${displaySegments
        .map(
          (segment) => `
      <polyline
        class="annotation-centerline${isSelected ? " annotation-feature-selected" : ""}"
        points="${segment.points.map((point) => `${point.x},${point.y}`).join(" ")}"
        style="stroke-width: ${centerlineWidthPx}px; stroke-linecap: ${segment.clippedStart || segment.clippedEnd || anySegmentClipped ? "butt" : "round"}"
        data-feature-kind="centerline"
        data-feature-id="${escapeHtml(centerline.id)}"
      />`,
        )
        .join("")}
      ${vertexMarkup}
      ${furnitureMarkup}
      <text class="annotation-label" x="${labelPoint.x}" y="${labelPoint.y - 12}">
        ${escapeHtml(centerline.label || centerline.id)}
      </text>
    </g>
  `;
}

function buildBuildingRegionOverlayMarkup(
  region: AnnotatedBuildingRegion,
  isSelected: boolean,
): string {
  const polygon = buildingRegionPolygonPoints(region);
  const polygonPoints = polygon.map((point) => `${point.x},${point.y}`).join(" ");
  const labelPoint = polygon[3] ?? region.center_px;
  const resizeHandles: BuildingRegionResizeHandle[] = ["nw", "ne", "se", "sw"];
  const resizeHandleMarkup = isSelected
    ? resizeHandles
        .map((handle) => {
          const point = buildingRegionResizeHandlePoint(region, handle);
          return `
            <circle
              class="annotation-building-region-handle"
              cx="${point.x}"
              cy="${point.y}"
              r="${BUILDING_REGION_HANDLE_RADIUS_PX}"
              data-feature-kind="building_region"
              data-feature-id="${escapeHtml(region.id)}"
              data-region-handle-kind="resize"
              data-region-resize-handle="${handle}"
            />
          `;
        })
        .join("")
    : "";
  const rotateHandlePoint = buildingRegionRotateHandlePoint(region);
  const rotateGuideMarkup = isSelected
    ? `
        <line
          class="annotation-building-region-rotate-guide"
          x1="${region.center_px.x}"
          y1="${region.center_px.y}"
          x2="${rotateHandlePoint.x}"
          y2="${rotateHandlePoint.y}"
        />
        <circle
          class="annotation-building-region-rotate-handle"
          cx="${rotateHandlePoint.x}"
          cy="${rotateHandlePoint.y}"
          r="${BUILDING_REGION_HANDLE_RADIUS_PX}"
          data-feature-kind="building_region"
          data-feature-id="${escapeHtml(region.id)}"
          data-region-handle-kind="rotate"
        />
      `
    : "";
  return `
    <g class="annotation-feature-group">
      <polygon
        class="annotation-building-region${isSelected ? " annotation-building-region-selected" : ""}"
        points="${polygonPoints}"
        data-feature-kind="building_region"
        data-feature-id="${escapeHtml(region.id)}"
      />
      <text class="annotation-label" x="${labelPoint.x}" y="${labelPoint.y - 10}">
        ${escapeHtml(region.label || region.id)}
      </text>
      ${resizeHandleMarkup}
      ${rotateGuideMarkup}
    </g>
  `;
}

function buildBuildingRegionDraftMarkup(drag: Extract<DragState, { kind: "building_region_draw" }> | null): string {
  if (!drag) {
    return "";
  }
  const preview = buildBuildingRegionFromDraft("__draft__", drag.startPoint, drag.currentPoint);
  const polygon = buildingRegionPolygonPoints(preview);
  return `
    <g class="annotation-feature-group">
      <polygon
        class="annotation-building-region annotation-building-region-draft"
        points="${polygon.map((point) => `${point.x},${point.y}`).join(" ")}"
      />
    </g>
  `;
}

function buildBranchPreviewMarkup(
  branchHoverSnap: BranchSnapTarget | null,
  branchDraft: BranchDraft | null,
): string {
  const fragments: string[] = [];
  if (branchHoverSnap && !branchDraft) {
    fragments.push(`
      <g class="annotation-feature-group">
        <circle class="annotation-branch-anchor" cx="${branchHoverSnap.point.x}" cy="${branchHoverSnap.point.y}" r="8" />
      </g>
    `);
  }
  if (branchDraft) {
    fragments.push(`
      <g class="annotation-feature-group">
        <circle class="annotation-branch-anchor" cx="${branchDraft.anchor.point.x}" cy="${branchDraft.anchor.point.y}" r="8" />
        <polyline
          class="annotation-branch-preview"
          points="${branchDraft.anchor.point.x},${branchDraft.anchor.point.y} ${branchDraft.endpoint.x},${branchDraft.endpoint.y}"
        />
        <circle class="annotation-branch-end${branchDraft.endpointSnap ? " annotation-branch-end-snapped" : ""}" cx="${branchDraft.endpoint.x}" cy="${branchDraft.endpoint.y}" r="7" />
      </g>
    `);
  }
  return fragments.join("");
}

function buildCrossPreviewMarkup(
  crossHoverSnap: BranchSnapTarget | null,
  crossDraft: CrossDraft | null,
): string {
  const fragments: string[] = [];
  if (crossHoverSnap && !crossDraft) {
    fragments.push(`
      <g class="annotation-feature-group">
        <circle class="annotation-branch-anchor annotation-cross-anchor" cx="${crossHoverSnap.point.x}" cy="${crossHoverSnap.point.y}" r="8" />
      </g>
    `);
  }
  if (crossDraft) {
    fragments.push(`
      <g class="annotation-feature-group">
        <circle class="annotation-branch-anchor annotation-cross-anchor" cx="${crossDraft.anchor.point.x}" cy="${crossDraft.anchor.point.y}" r="8" />
        <polyline
          class="annotation-branch-preview annotation-cross-preview"
          points="${crossDraft.negativeEndpoint.x},${crossDraft.negativeEndpoint.y} ${crossDraft.anchor.point.x},${crossDraft.anchor.point.y} ${crossDraft.positiveEndpoint.x},${crossDraft.positiveEndpoint.y}"
        />
        <circle class="annotation-branch-end annotation-cross-end${crossDraft.negativeEndpointSnap ? " annotation-branch-end-snapped" : ""}" cx="${crossDraft.negativeEndpoint.x}" cy="${crossDraft.negativeEndpoint.y}" r="7" />
        <circle class="annotation-branch-end annotation-cross-end${crossDraft.positiveEndpointSnap ? " annotation-branch-end-snapped" : ""}" cx="${crossDraft.positiveEndpoint.x}" cy="${crossDraft.positiveEndpoint.y}" r="7" />
      </g>
    `);
  }
  return fragments.join("");
}

function previewCenterlinesFromDrafts(
  annotation: ReferenceAnnotation,
  branchDraft: BranchDraft | null,
  crossDraft: CrossDraft | null,
): AnnotatedCenterline[] {
  const previews: AnnotatedCenterline[] = [];
  if (branchDraft) {
    const host = annotation.centerlines.find((item) => item.id === branchDraft.anchor.centerlineId);
    if (host && pointDistance(branchDraft.anchor.point, branchDraft.endpoint) > 1) {
      previews.push(
        cloneCenterlineForBranch(host, "__preview_branch__", [branchDraft.anchor.point, branchDraft.endpoint]),
      );
    }
  }
  if (crossDraft) {
    const host = annotation.centerlines.find((item) => item.id === crossDraft.anchor.centerlineId);
    if (
      host &&
      (pointDistance(crossDraft.anchor.point, crossDraft.negativeEndpoint) > 1 ||
        pointDistance(crossDraft.anchor.point, crossDraft.positiveEndpoint) > 1)
    ) {
      previews.push(
        cloneCenterlineForBranch(host, "__preview_cross__", [
          crossDraft.negativeEndpoint,
          crossDraft.anchor.point,
          crossDraft.positiveEndpoint,
        ]),
      );
    }
  }
  return previews;
}

function buildDerivedJunctionOverlayMarkup(
  overlays: DerivedJunctionOverlay[],
  selection: Selection,
  options: {
    showJunctionCore: boolean;
    showJunctionConnectors: boolean;
    showJunctionCrosswalks: boolean;
    showJunctionBoundaries: boolean;
    showJunctionLabels: boolean;
    showJunctionDebug: boolean;
  },
): string {
  if (overlays.length === 0) {
    return "";
  }
  const cornerConnectorClassName = (stripKind: StripKind): string => {
    if (stripKind === "clear_sidewalk") {
      return "annotation-junction-sidewalk-connector";
    }
    if (stripKind === "nearroad_furnishing") {
      return "annotation-junction-nearroad-connector";
    }
    return "annotation-junction-frontage-connector";
  };
  return overlays
    .map((overlay) => {
      const featureKind = overlay.sourceMode === "explicit" ? "junction" : "derived_junction";
      const isSelected =
        (featureKind === "junction" && selection?.kind === "junction" && selection.id === overlay.junctionId) ||
        (featureKind === "derived_junction" && selection?.kind === "derived_junction" && selection.id === overlay.junctionId);
      const polygonMarkup = (patches: DerivedJunctionOverlayPatch[], className: string): string =>
        patches
          .map((patch) => {
            if (patch.points.length < 3) {
              return "";
            }
            if (patch.cutoutPoints && patch.cutoutPoints.length >= 3) {
              const pathData = [
                `M ${patch.points.map((point) => `${point.x},${point.y}`).join(" L ")} Z`,
                `M ${patch.cutoutPoints.map((point) => `${point.x},${point.y}`).join(" L ")} Z`,
              ].join(" ");
              return `
                <path
                  class="${className}${isSelected ? ` ${className}-selected` : ""}"
                  d="${pathData}"
                  fill-rule="evenodd"
                  style="stroke: none"
                  data-feature-kind="${featureKind}"
                  data-feature-id="${escapeHtml(overlay.junctionId)}"
                />
              `;
            }
            return `
              <polygon
                class="${className}${isSelected ? ` ${className}-selected` : ""}"
                points="${patch.points.map((point) => `${point.x},${point.y}`).join(" ")}"
                data-feature-kind="${featureKind}"
                data-feature-id="${escapeHtml(overlay.junctionId)}"
              />
            `;
          })
          .join("");
      const cornerConnectorMarkup =
        options.showJunctionConnectors && overlay.kind === "cross_junction"
          ? overlay.connectorCenterLines
              .map((line) => {
                const className = cornerConnectorClassName(line.stripKind);
                return `
                  <polyline
                    class="${className}${isSelected ? ` ${className}-selected` : ""}"
                    points="${line.points.map((point) => `${point.x},${point.y}`).join(" ")}"
                    style="stroke-width: ${line.strokeWidthPx}px"
                    data-feature-kind="${featureKind}"
                    data-feature-id="${escapeHtml(overlay.junctionId)}"
                  />
                `;
              })
              .join("")
          : "";
      const connectorLineMarkup = isSelected && options.showJunctionDebug && overlay.kind === "t_junction"
        ? overlay.connectorCenterLines
            .map(
              (line) => `
                <polyline
                  class="annotation-junction-connector-line"
                  points="${line.points.map((point) => `${point.x},${point.y}`).join(" ")}"
                  style="stroke: ${stripStrokeColor(line.stripKind)}; stroke-width: ${line.strokeWidthPx}px"
                />
              `,
            )
            .join("")
        : "";
      const quadrantCornerKernelMarkup = isSelected && options.showJunctionDebug && overlay.kind === "cross_junction"
        ? overlay.quadrantCornerKernels
            .map(
              (kernel) => `
                <polyline
                  class="annotation-junction-corner-kernel"
                  points="${kernel.points.map((point) => `${point.x},${point.y}`).join(" ")}"
                />
              `,
            )
            .join("")
        : "";
      const connectorDebugLabelMarkup = isSelected && options.showJunctionDebug && overlay.kind === "cross_junction"
        ? overlay.connectorCenterLines
            .map((line) => {
              const anchorPoint = line.points[Math.floor(line.points.length * 0.5)] ?? line.points[0];
              const stripLabel = metaurbanStripLabel(line.stripKind);
              return `
                <text
                  class="annotation-junction-debug-label"
                  x="${anchorPoint?.x ?? 0}"
                  y="${(anchorPoint?.y ?? 0) - 8}"
                  text-anchor="middle"
                >
                  ${escapeHtml(`${cornerConnectionLabel(line.quadrantId)} / ${line.kernelId ?? "no-kernel"} / ${stripLabel}`)}
                </text>
              `;
            })
            .join("")
        : "";
      const boundaryMarkup = options.showJunctionBoundaries
        ? overlay.approachBoundaries
            .map(
              (boundary) => `
            <line
              class="annotation-junction-boundary${isSelected ? " annotation-junction-boundary-selected" : ""}"
              x1="${boundary.start.x}"
              y1="${boundary.start.y}"
              x2="${boundary.end.x}"
              y2="${boundary.end.y}"
              data-feature-kind="${featureKind}"
              data-feature-id="${escapeHtml(overlay.junctionId)}"
            />
          `,
            )
            .join("")
        : "";
      const boundaryExtensionMarkup = isSelected && options.showJunctionDebug
        ? overlay.boundaryExtensionLines
            .map(
              (line) => `
                <line
                  class="annotation-junction-boundary-extension"
                  x1="${line.start.x}"
                  y1="${line.start.y}"
                  x2="${line.end.x}"
                  y2="${line.end.y}"
                />
              `,
            )
            .join("")
        : "";
      const focusGuideMarkup = isSelected && options.showJunctionDebug
        ? overlay.focusGuideLines
            .map(
              (line) => `
                <line
                  class="annotation-junction-focus-guide"
                  x1="${line.start.x}"
                  y1="${line.start.y}"
                  x2="${line.end.x}"
                  y2="${line.end.y}"
                />
              `,
            )
            .join("")
        : "";
      const controlPointMarkup = isSelected && options.showJunctionDebug
        ? `
          ${boundaryExtensionMarkup}
          ${focusGuideMarkup}
          ${overlay.cornerFocusPoints
            .map(
              (item) => `
                <circle
                  class="annotation-junction-corner-focus"
                  cx="${item.point.x}"
                  cy="${item.point.y}"
                  r="5"
                />
              `,
            )
            .join("")}
          ${overlay.skeletonFootPoints
            .map(
              (item) => `
                <circle
                  class="annotation-junction-control-point annotation-junction-foot-point"
                  cx="${item.point.x}"
                  cy="${item.point.y}"
                  r="4.5"
                />
              `,
            )
            .join("")}
          ${overlay.subLaneControlPoints
            .map(
              (item) => `
                <circle
                  class="annotation-junction-control-point"
                  cx="${item.point.x}"
                  cy="${item.point.y}"
                  r="3.5"
                />
              `,
            )
            .join("")}
        `
        : "";
      const coreBounds = overlay.core.reduce(
        (acc, point) => ({
          minX: Math.min(acc.minX, point.x),
          minY: Math.min(acc.minY, point.y),
          maxX: Math.max(acc.maxX, point.x),
          maxY: Math.max(acc.maxY, point.y),
        }),
        { minX: Number.POSITIVE_INFINITY, minY: Number.POSITIVE_INFINITY, maxX: Number.NEGATIVE_INFINITY, maxY: Number.NEGATIVE_INFINITY },
      );
      const labelX = (coreBounds.minX + coreBounds.maxX) * 0.5;
      const labelY = coreBounds.minY - 10;
      return `
        <g class="annotation-feature-group">
          ${connectorLineMarkup}
      ${quadrantCornerKernelMarkup}
          ${connectorDebugLabelMarkup}
          ${
            options.showJunctionConnectors && overlay.kind === "t_junction"
              ? polygonMarkup(overlay.frontageCorners, "annotation-junction-frontage-corner")
              : ""
          }
          ${
            options.showJunctionConnectors && overlay.kind === "t_junction"
              ? polygonMarkup(overlay.nearroadCorners, "annotation-junction-nearroad-corner")
              : ""
          }
          ${
            options.showJunctionConnectors && overlay.kind === "t_junction"
              ? polygonMarkup(overlay.sidewalkCorners, "annotation-junction-sidewalk-corner")
              : ""
          }
          ${
            options.showJunctionCore
              ? `
          <polygon
            class="annotation-junction-core${isSelected ? " annotation-junction-core-selected" : ""}"
            points="${overlay.core.map((point) => `${point.x},${point.y}`).join(" ")}"
            data-feature-kind="${featureKind}"
            data-feature-id="${escapeHtml(overlay.junctionId)}"
          />`
              : ""
          }
          ${options.showJunctionCrosswalks ? polygonMarkup(overlay.crosswalks, "annotation-junction-crosswalk") : ""}
          ${cornerConnectorMarkup}
          ${boundaryMarkup}
          ${controlPointMarkup}
          ${
            options.showJunctionLabels
              ? `<text
            class="annotation-junction-label${isSelected ? " annotation-junction-label-selected" : ""}"
            x="${labelX}"
            y="${labelY}"
            text-anchor="middle"
            data-feature-kind="${featureKind}"
            data-feature-id="${escapeHtml(overlay.junctionId)}"
          >
            ${escapeHtml(derivedJunctionKindLabel(overlay.kind))}
          </text>`
              : ""
          }
        </g>
      `;
    })
    .join("");
}

function buildOverlayMarkup(
  annotation: ReferenceAnnotation,
  draftCenterline: AnnotationPoint[],
  selection: Selection,
  selectedStripId: string | null,
  junctionOverlayOptions: {
    showJunctionCore: boolean;
    showJunctionConnectors: boolean;
    showJunctionCrosswalks: boolean;
    showJunctionBoundaries: boolean;
    showJunctionLabels: boolean;
    showJunctionDebug: boolean;
    showJunctionOutlines: boolean;
  },
  branchHoverSnap: BranchSnapTarget | null,
  branchDraft: BranchDraft | null,
  crossHoverSnap: BranchSnapTarget | null,
  crossDraft: CrossDraft | null,
  buildingRegionDraft: Extract<DragState, { kind: "building_region_draw" }> | null,
): string {
  const width = Math.max(annotation.image_width_px, 1);
  const height = Math.max(annotation.image_height_px, 1);
  const selectedKey = selection ? `${selection.kind}:${selection.id}` : "";
  const junctionOverlays = deriveJunctionOverlayGeometries(
    annotation,
    previewCenterlinesFromDrafts(annotation, branchDraft, crossDraft),
  );
  const linkedStripKeys = linkedCrossStripKeys(junctionOverlays, selection, selectedStripId);

  const centerlineMarkup = annotation.centerlines
    .map((centerline) => {
      const isSelected = selectedKey === `centerline:${centerline.id}` || selection?.kind === "road_collection";
      const selectedVertexIndex =
        selection && selection.kind === "centerline" && selection.id === centerline.id
          ? selection.vertexIndex
          : undefined;
      return buildCenterlineOverlayMarkup(
        centerline,
        annotation.pixels_per_meter,
        isSelected,
        selectedVertexIndex,
        isSelected ? selectedStripId : null,
        junctionOverlays,
        linkedStripKeys,
      );
    })
    .join("");

  const markerMarkup = (
    [
      ...annotation.junctions.map((item) => ({ featureKind: "junction" as const, colorClass: "annotation-junction", item })),
      ...annotation.control_points.map((item) => ({
        featureKind: "control_point" as const,
        colorClass: "annotation-control-point",
        item,
      })),
    ] as const
  )
    .map(({ featureKind, colorClass, item }) => {
      if (featureKind === "junction" && item.source_mode === "explicit") {
        return "";
      }
      const isSelected = selectedKey === `${featureKind}:${item.id}`;
      return `
        <g class="annotation-feature-group">
          <circle
            class="annotation-marker ${colorClass}${isSelected ? " annotation-feature-selected" : ""}"
            cx="${item.x}"
            cy="${item.y}"
            r="9"
            data-feature-kind="${featureKind}"
            data-feature-id="${escapeHtml(item.id)}"
          />
          <text class="annotation-label" x="${item.x + 12}" y="${item.y - 12}">
            ${escapeHtml(item.label || item.id)}
          </text>
        </g>
      `;
    })
    .join("");

  const roundaboutMarkup = annotation.roundabouts
    .map((item) => {
      const isSelected = selectedKey === `roundabout:${item.id}`;
      return `
        <g class="annotation-feature-group">
          <circle
            class="annotation-roundabout${isSelected ? " annotation-feature-selected" : ""}"
            cx="${item.x}"
            cy="${item.y}"
            r="${item.radius_px}"
            data-feature-kind="roundabout"
            data-feature-id="${escapeHtml(item.id)}"
          />
          <circle
            class="annotation-roundabout-center${isSelected ? " annotation-feature-selected" : ""}"
            cx="${item.x}"
            cy="${item.y}"
            r="7"
            data-feature-kind="roundabout"
            data-feature-id="${escapeHtml(item.id)}"
          />
          <text class="annotation-label" x="${item.x + item.radius_px + 12}" y="${item.y - 12}">
            ${escapeHtml(item.label || item.id)}
          </text>
        </g>
      `;
    })
    .join("");

  const buildingRegionMarkup = annotation.building_regions
    .map((region) => buildBuildingRegionOverlayMarkup(region, selectedKey === `building_region:${region.id}`))
    .join("");

  const draftMarkup =
    draftCenterline.length > 0
      ? `
        <g class="annotation-feature-group">
          <polyline
            class="annotation-centerline annotation-centerline-draft"
            points="${draftCenterline.map((point) => `${point.x},${point.y}`).join(" ")}"
            style="stroke-width: ${getDisplayCenterlineWidthPx(annotation.pixels_per_meter)}px"
          />
          ${draftCenterline
            .map(
              (point, index) => `
                <circle class="annotation-vertex annotation-vertex-draft" cx="${point.x}" cy="${point.y}" r="5" />
                <text class="annotation-label" x="${point.x + 10}" y="${point.y - 10}">
                  p${index + 1}
                </text>
              `,
            )
            .join("")}
        </g>
      `
      : "";

  const derivedJunctionMarkup = buildDerivedJunctionOverlayMarkup(junctionOverlays, selection, junctionOverlayOptions);

  return `
    <svg
      id="annotation-overlay-svg"
      class="annotation-overlay-svg"
      viewBox="0 0 ${width} ${height}"
      data-hide-junction-outlines="${junctionOverlayOptions.showJunctionOutlines ? "false" : "true"}"
      role="img"
      aria-label="Reference annotation overlay"
    >
      <rect x="0" y="0" width="${width}" height="${height}" fill="transparent" />
      ${centerlineMarkup}
      ${derivedJunctionMarkup}
      ${buildingRegionMarkup}
      ${markerMarkup}
      ${roundaboutMarkup}
      ${buildBranchPreviewMarkup(branchHoverSnap, branchDraft)}
      ${buildCrossPreviewMarkup(crossHoverSnap, crossDraft)}
      ${buildBuildingRegionDraftMarkup(buildingRegionDraft)}
      ${draftMarkup}
    </svg>
  `;
}

function downloadText(filename: string, text: string): void {
  const blob = new Blob([text], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function mountSceneGraphPage(root: HTMLElement): () => void {
  const eventController = new AbortController();
  const { signal } = eventController;

  root.innerHTML = `
    <div class="scene-page">
      <div class="scene-page-topbar">
        <div>
          <div class="scene-page-kicker">Viewer / Reference Annotation</div>
          <h1 class="scene-page-title">Reference Plan Annotator</h1>
          <p class="scene-page-subtitle">
            先校准道路总宽与参考图，再把中心线拆成车道、步行带、门前预留和街道家具点位，最后导出 JSON 并转换成带详细横断面的道路 graph。
          </p>
        </div>
        <div class="scene-page-actions">
          <button id="scene-page-asset-editor" class="viewer-nav-button" type="button">Asset Editor</button>
          <button id="scene-page-back" class="viewer-nav-button" type="button">Back to Viewer</button>
        </div>
      </div>

      <div id="annotation-page-layout" class="scene-page-layout" data-sidebar-collapsed="false">
        <div class="scene-main-column">
          <section class="scene-panel scene-panel-canvas">
          <div class="scene-panel-header">
            <h2>Reference Board</h2>
            <p>先选参考图并标中心线，调好 Pixels / Meter 和总宽度后，再进入详细 strip 模式拆分车道、人行道、frontage reserve 和街道家具。</p>
          </div>

          <div class="scene-layer-toolbar">
            <label class="scene-select-wrap">
              <span class="scene-select-label">Reference Plan</span>
              <select id="annotation-plan-select" class="scene-select"></select>
            </label>
            <label class="scene-file-button" for="annotation-image-input">Import PNG</label>
            <input id="annotation-image-input" class="scene-file-input" type="file" accept="image/png,image/*" />
            <button id="annotation-image-reset" class="scene-toolbar-button scene-toolbar-button-secondary" type="button">
              Clear Image
            </button>
          </div>

          <div class="scene-tool-row">
            <button id="annotation-tool-select" class="scene-tool-button" data-tool="select" type="button">Select</button>
            <button id="annotation-tool-adjust" class="scene-tool-button" data-tool="adjust" type="button">Adjust</button>
            <button id="annotation-tool-centerline" class="scene-tool-button" data-tool="centerline" type="button">Centerline</button>
            <button id="annotation-tool-branch" class="scene-tool-button" data-tool="branch" type="button">Branch</button>
            <button id="annotation-tool-cross" class="scene-tool-button" data-tool="cross" type="button">Cross</button>
            <button id="annotation-tool-roundabout" class="scene-tool-button" data-tool="roundabout" type="button">Roundabout</button>
            <button id="annotation-tool-control-point" class="scene-tool-button" data-tool="control_point" type="button">Control Point</button>
            <button id="annotation-tool-building-region" class="scene-tool-button" data-tool="building_region" type="button">Building Region</button>
            <button id="annotation-tool-tree" class="scene-tool-button" data-tool="tree" type="button">Tree</button>
            <button id="annotation-tool-lamp" class="scene-tool-button" data-tool="lamp" type="button">Lamp</button>
          </div>

          <div class="scene-layer-controls scene-layer-controls-annotation">
            <label class="scene-layer-toggle" for="annotation-show-original">
              <input id="annotation-show-original" type="checkbox" checked />
              <span>Original Image</span>
            </label>
            <label class="scene-layer-toggle" for="annotation-show-overlay">
              <input id="annotation-show-overlay" type="checkbox" checked />
              <span>Annotation Overlay</span>
            </label>
            <label class="scene-layer-toggle" for="annotation-show-junction-core">
              <input id="annotation-show-junction-core" type="checkbox" />
              <span>Junction Core</span>
            </label>
            <label class="scene-layer-toggle" for="annotation-show-junction-connectors">
              <input id="annotation-show-junction-connectors" type="checkbox" />
              <span>Junction Connectors</span>
            </label>
            <label class="scene-layer-toggle" for="annotation-show-junction-outlines">
              <input id="annotation-show-junction-outlines" type="checkbox" />
              <span>Junction Outlines</span>
            </label>
            <label class="scene-layer-toggle" for="annotation-show-junction-crosswalks">
              <input id="annotation-show-junction-crosswalks" type="checkbox" />
              <span>Crosswalks</span>
            </label>
            <label class="scene-layer-toggle" for="annotation-show-junction-boundaries">
              <input id="annotation-show-junction-boundaries" type="checkbox" />
              <span>Approach Boundaries</span>
            </label>
            <label class="scene-layer-toggle" for="annotation-show-junction-labels">
              <input id="annotation-show-junction-labels" type="checkbox" />
              <span>Junction Labels</span>
            </label>
            <label class="scene-layer-toggle" for="annotation-show-junction-debug">
              <input id="annotation-show-junction-debug" type="checkbox" />
              <span>Junction Debug</span>
            </label>
            <label class="scene-range-control" for="annotation-original-opacity">
              <span>Original Opacity</span>
              <input id="annotation-original-opacity" type="range" min="0" max="100" value="100" />
            </label>
            <label class="scene-range-control" for="annotation-overlay-opacity">
              <span>Overlay Opacity</span>
              <input id="annotation-overlay-opacity" type="range" min="0" max="100" value="88" />
            </label>
            <label class="scene-form-field scene-form-field-inline">
              <span>Pixels / Meter</span>
              <input id="annotation-pixels-per-meter" type="number" min="0.1" step="0.1" value="${DEFAULT_PIXELS_PER_METER}" />
            </label>
            <label class="scene-form-field scene-form-field-inline">
              <span>Default Roundabout Radius</span>
              <input id="annotation-roundabout-radius" type="number" min="8" step="1" value="${DEFAULT_ROUNDABOUT_RADIUS_PX}" />
            </label>
          </div>

          <div class="scene-layer-toolbar scene-layer-toolbar-secondary">
            <button id="annotation-finish-centerline" class="scene-toolbar-button" type="button">Finish Centerline</button>
            <button id="annotation-select-all-roads" class="scene-toolbar-button scene-toolbar-button-secondary" type="button">All Roads</button>
            <button id="annotation-undo-point" class="scene-toolbar-button scene-toolbar-button-secondary" type="button">Undo Point</button>
            <button id="annotation-delete-selected" class="scene-toolbar-button scene-toolbar-button-secondary" type="button">Delete Selected</button>
            <button id="annotation-reset" class="scene-toolbar-button scene-toolbar-button-secondary" type="button">Reset Annotation</button>
          </div>

          <div id="annotation-image-meta" class="scene-image-meta">
            选择参考 plan 或导入 PNG 后，就可以在图上开始标注。
          </div>

          <div id="annotation-stage" class="scene-layer-stage" data-has-image="false" data-loading="true" data-empty-state="loading">
            <div id="annotation-stage-empty" class="scene-image-empty">
              Loading default reference plan...
            </div>
            <div id="annotation-board" class="scene-board" hidden>
              <img id="annotation-original-image" class="scene-original-image annotation-original-image" alt="Reference plan" />
              <div id="annotation-overlay-host" class="scene-graph-overlay"></div>
            </div>
          </div>
          </section>

          <section class="scene-panel scene-panel-selected-feature">
            <div class="scene-panel-header">
              <h2>Selected Feature</h2>
              <p>中心线支持 Coarse / Detailed 两阶段编辑。Detailed 模式下可以手工拆 strip、调方向、放置街道家具，也可以绘制建筑区域并手工指定建筑朝向。</p>
            </div>
            <div id="annotation-inspector" class="scene-inspector-wrap"></div>
          </section>
        </div>

        <aside id="annotation-sidebar" class="scene-sidebar" data-collapsed="false">
          <div class="scene-sidebar-rail">
            <button id="annotation-sidebar-toggle" class="scene-sidebar-toggle" type="button" aria-expanded="true" aria-label="Collapse sidebar panels">
              <span class="scene-sidebar-toggle-icon" aria-hidden="true">></span>
              <span class="scene-sidebar-toggle-label">Hide Panels</span>
            </button>
          </div>
          <div class="scene-sidebar-content">
            <section class="scene-panel scene-panel-compact scene-metrics">
              <div class="scene-panel-header">
                <h2>Annotation Summary</h2>
                <p>当前手工标注的统计概览。</p>
              </div>
              <div id="annotation-summary-grid" class="scene-metric-grid"></div>
            </section>

            <section class="scene-panel scene-panel-compact">
              <div class="scene-panel-header">
                <h2>Graph Conversion</h2>
                <p>把当前 annotation JSON 直接送进后端 converter，生成保留详细横断面与家具实例的 segment graph。</p>
              </div>
              <div class="scene-import-toolbar">
                <label class="scene-form-field scene-form-field-inline">
                  <span>Segment Length (m)</span>
                  <input id="annotation-segment-length" type="number" min="4" step="1" value="${DEFAULT_SEGMENT_LENGTH_M}" />
                </label>
                <label class="scene-form-field scene-form-field-inline">
                  <span>Sidewalk Width (m)</span>
                  <input id="annotation-sidewalk-width" type="number" min="1" step="0.5" value="${DEFAULT_SIDEWALK_WIDTH_M}" />
                </label>
                <button id="annotation-convert-graph" class="scene-toolbar-button" type="button">Convert to Graph</button>
                <button id="annotation-download-graph" class="scene-toolbar-button scene-toolbar-button-secondary" type="button">
                  Download Graph
                </button>
              </div>
              <div id="annotation-graph-status" class="scene-status" data-tone="neutral">
                Convert 后会在这里显示 graph 结果。
              </div>
              <div id="annotation-graph-summary" class="scene-metric-grid"></div>
              <div class="scene-json-wrap">
                <textarea id="annotation-graph-json" class="scene-json-input" spellcheck="false" readonly></textarea>
              </div>
            </section>

            <section class="scene-panel scene-panel-compact">
              <div class="scene-panel-header">
                <h2>Feature Table</h2>
                <p>快速检查当前所有要素及其核心属性。</p>
              </div>
              <div class="scene-table-wrap scene-table-wrap-compact">
                <table class="scene-table scene-table-compact">
                  <thead>
                    <tr>
                      <th>Type</th>
                      <th>ID</th>
                      <th>Label</th>
                      <th>Detail</th>
                    </tr>
                  </thead>
                  <tbody id="annotation-feature-table"></tbody>
                </table>
              </div>
            </section>

            <section class="scene-panel scene-panel-compact">
            <div class="scene-panel-header">
              <h2>Annotation JSON</h2>
              <p>可以从这里导入、导出或直接修改标注 JSON。</p>
            </div>
            <div class="scene-import-toolbar">
              <label class="scene-file-button" for="annotation-json-input">Import JSON</label>
              <input id="annotation-json-input" class="scene-file-input" type="file" accept=".json,application/json" />
              <button id="annotation-apply-json" class="scene-toolbar-button" type="button">Apply JSON</button>
              <button id="annotation-download-json" class="scene-toolbar-button scene-toolbar-button-secondary" type="button">
                Download JSON
              </button>
              <button id="annotation-copy-json" class="scene-toolbar-button scene-toolbar-button-secondary" type="button">
                Copy JSON
              </button>
            </div>
            <div class="scene-json-wrap">
              <textarea id="annotation-json" class="scene-json-input" spellcheck="false"></textarea>
            </div>
            <div id="annotation-status" class="scene-status" data-tone="neutral">
              Waiting for a reference image.
            </div>
            </section>
          </div>
        </aside>
      </div>
    </div>
  `;

  const backButton = requireElement<HTMLButtonElement>(root, "#scene-page-back");
  const pageLayoutEl = requireElement<HTMLElement>(root, "#annotation-page-layout");
  const planSelect = requireElement<HTMLSelectElement>(root, "#annotation-plan-select");
  const imageInput = requireElement<HTMLInputElement>(root, "#annotation-image-input");
  const imageResetButton = requireElement<HTMLButtonElement>(root, "#annotation-image-reset");
  const showOriginalInput = requireElement<HTMLInputElement>(root, "#annotation-show-original");
  const showOverlayInput = requireElement<HTMLInputElement>(root, "#annotation-show-overlay");
  const showJunctionCoreInput = requireElement<HTMLInputElement>(root, "#annotation-show-junction-core");
  const showJunctionConnectorsInput = requireElement<HTMLInputElement>(root, "#annotation-show-junction-connectors");
  const showJunctionOutlinesInput = requireElement<HTMLInputElement>(root, "#annotation-show-junction-outlines");
  const showJunctionCrosswalksInput = requireElement<HTMLInputElement>(root, "#annotation-show-junction-crosswalks");
  const showJunctionBoundariesInput = requireElement<HTMLInputElement>(root, "#annotation-show-junction-boundaries");
  const showJunctionLabelsInput = requireElement<HTMLInputElement>(root, "#annotation-show-junction-labels");
  const showJunctionDebugInput = requireElement<HTMLInputElement>(root, "#annotation-show-junction-debug");
  const originalOpacityInput = requireElement<HTMLInputElement>(root, "#annotation-original-opacity");
  const overlayOpacityInput = requireElement<HTMLInputElement>(root, "#annotation-overlay-opacity");
  const pixelsPerMeterInput = requireElement<HTMLInputElement>(root, "#annotation-pixels-per-meter");
  const roundaboutRadiusInput = requireElement<HTMLInputElement>(root, "#annotation-roundabout-radius");
  const finishCenterlineButton = requireElement<HTMLButtonElement>(root, "#annotation-finish-centerline");
  const selectAllRoadsButton = requireElement<HTMLButtonElement>(root, "#annotation-select-all-roads");
  const undoPointButton = requireElement<HTMLButtonElement>(root, "#annotation-undo-point");
  const deleteSelectedButton = requireElement<HTMLButtonElement>(root, "#annotation-delete-selected");
  const resetAnnotationButton = requireElement<HTMLButtonElement>(root, "#annotation-reset");
  const imageMetaEl = requireElement<HTMLElement>(root, "#annotation-image-meta");
  const stageEl = requireElement<HTMLElement>(root, "#annotation-stage");
  const stageEmptyEl = requireElement<HTMLElement>(root, "#annotation-stage-empty");
  const boardEl = requireElement<HTMLElement>(root, "#annotation-board");
  const originalImageEl = requireElement<HTMLImageElement>(root, "#annotation-original-image");
  const overlayHostEl = requireElement<HTMLElement>(root, "#annotation-overlay-host");
  const jsonFileInput = requireElement<HTMLInputElement>(root, "#annotation-json-input");
  const applyJsonButton = requireElement<HTMLButtonElement>(root, "#annotation-apply-json");
  const downloadJsonButton = requireElement<HTMLButtonElement>(root, "#annotation-download-json");
  const copyJsonButton = requireElement<HTMLButtonElement>(root, "#annotation-copy-json");
  const jsonTextarea = requireElement<HTMLTextAreaElement>(root, "#annotation-json");
  const statusEl = requireElement<HTMLElement>(root, "#annotation-status");
  const summaryGridEl = requireElement<HTMLElement>(root, "#annotation-summary-grid");
  const inspectorEl = requireElement<HTMLElement>(root, "#annotation-inspector");
  const segmentLengthInput = requireElement<HTMLInputElement>(root, "#annotation-segment-length");
  const sidewalkWidthInput = requireElement<HTMLInputElement>(root, "#annotation-sidewalk-width");
  const convertGraphButton = requireElement<HTMLButtonElement>(root, "#annotation-convert-graph");
  const downloadGraphButton = requireElement<HTMLButtonElement>(root, "#annotation-download-graph");
  const graphStatusEl = requireElement<HTMLElement>(root, "#annotation-graph-status");
  const graphSummaryEl = requireElement<HTMLElement>(root, "#annotation-graph-summary");
  const graphTextarea = requireElement<HTMLTextAreaElement>(root, "#annotation-graph-json");
  const featureTableEl = requireElement<HTMLElement>(root, "#annotation-feature-table");
  const sidebarEl = requireElement<HTMLElement>(root, "#annotation-sidebar");
  const sidebarToggleButton = requireElement<HTMLButtonElement>(root, "#annotation-sidebar-toggle");

  const toolButtons = Array.from(root.querySelectorAll<HTMLButtonElement>(".scene-tool-button"));

  const state = {
    referencePlans: [FALLBACK_REFERENCE_PLAN] as ReferencePlan[],
    annotation: createEmptyAnnotation(),
    draftCenterline: [] as AnnotationPoint[],
    selectedTool: "select" as Tool,
    selection: null as Selection,
    selectedStripId: null as string | null,
    drag: null as DragState,
    currentImageUrl: "",
    currentObjectUrl: "",
    graphResult: null as ConvertedGraphPayload | null,
    showOriginal: true,
    showOverlay: true,
    showJunctionCore: false,
    showJunctionConnectors: false,
    showJunctionOutlines: false,
    showJunctionCrosswalks: false,
    showJunctionBoundaries: false,
    showJunctionLabels: false,
    showJunctionDebug: false,
    originalOpacity: 1,
    overlayOpacity: 0.88,
    defaultRoundaboutRadiusPx: DEFAULT_ROUNDABOUT_RADIUS_PX,
    isReferenceImageLoading: true,
    referenceImageLoadingMessage: "Loading default reference plan...",
    isSidebarCollapsed: false,
    previewResize: null as null | {
      pointerId: number;
      centerlineId: string;
      leftStripId: string;
      rightStripId: string;
      startClientX: number;
      startLeftWidthM: number;
      startRightWidthM: number;
      pairWidthPx: number;
      didResize: boolean;
    },
    pendingFurnitureKind: "bench" as FurnitureKind,
    furniturePlacement: null as null | {
      centerlineId: string;
      stripId: string;
      kind: FurnitureKind;
    },
    branchHoverSnap: null as BranchSnapTarget | null,
    branchDraft: null as BranchDraft | null,
    crossHoverSnap: null as BranchSnapTarget | null,
    crossDraft: null as CrossDraft | null,
  };

  function clearGraphResult(reason: string): void {
    state.graphResult = null;
    graphTextarea.value = "";
    graphSummaryEl.innerHTML = buildGraphSummaryMarkup(null);
    setStatus(graphStatusEl, reason, "neutral");
  }

  function selectedCenterline(): AnnotatedCenterline | null {
    const feature = getSelectedFeature(state.annotation, state.selection);
    return state.selection?.kind === "centerline" && feature ? (feature as AnnotatedCenterline) : null;
  }

  function selectedStrip(centerline: AnnotatedCenterline | null = selectedCenterline()): AnnotatedCrossSectionStrip | null {
    if (!centerline || !state.selectedStripId) {
      return null;
    }
    return centerline.cross_section_strips.find((strip) => strip.strip_id === state.selectedStripId) ?? null;
  }

  function clearFurniturePlacement(): void {
    state.furniturePlacement = null;
  }

  function clearBranchDraft(): void {
    state.branchHoverSnap = null;
    state.branchDraft = null;
  }

  function clearCrossDraft(): void {
    state.crossHoverSnap = null;
    state.crossDraft = null;
  }

  function markAnnotationChanged(statusMessage?: string): void {
    clearGraphResult("Annotation changed. Re-run convert to refresh graph output.");
    if (statusMessage) {
      setStatus(statusEl, statusMessage, "success");
    }
  }

  function revokeCurrentObjectUrl(): void {
    if (state.currentObjectUrl) {
      URL.revokeObjectURL(state.currentObjectUrl);
      state.currentObjectUrl = "";
    }
  }

  function updateStageVisibility(): void {
    const hasImage = Boolean(state.currentImageUrl);
    stageEl.dataset.hasImage = hasImage ? "true" : "false";
    stageEl.dataset.loading = state.isReferenceImageLoading ? "true" : "false";
    stageEl.dataset.emptyState = hasImage ? "ready" : state.isReferenceImageLoading ? "loading" : "empty";
    boardEl.hidden = !hasImage;
    stageEmptyEl.hidden = hasImage;
    if (!hasImage) {
      stageEmptyEl.textContent = state.isReferenceImageLoading
        ? state.referenceImageLoadingMessage
        : "Load a reference plan image to start annotating.";
    }
    originalImageEl.hidden = !hasImage || !state.showOriginal;
    originalImageEl.style.opacity = String(state.originalOpacity);
    overlayHostEl.hidden = !state.showOverlay;
    overlayHostEl.style.opacity = String(state.overlayOpacity);
  }

  function syncJsonTextarea(force = false): void {
    if (!force && document.activeElement === jsonTextarea) {
      return;
    }
    jsonTextarea.value = stringifyAnnotation(state.annotation);
  }

  function renderToolButtons(): void {
    for (const button of toolButtons) {
      button.dataset.active = button.dataset.tool === state.selectedTool ? "true" : "false";
    }
  }

  function renderSidebar(): void {
    const collapsed = state.isSidebarCollapsed;
    pageLayoutEl.dataset.sidebarCollapsed = collapsed ? "true" : "false";
    sidebarEl.dataset.collapsed = collapsed ? "true" : "false";
    sidebarToggleButton.setAttribute("aria-expanded", collapsed ? "false" : "true");
    sidebarToggleButton.setAttribute("aria-label", collapsed ? "Expand sidebar panels" : "Collapse sidebar panels");
    const iconEl = sidebarToggleButton.querySelector<HTMLElement>(".scene-sidebar-toggle-icon");
    const labelEl = sidebarToggleButton.querySelector<HTMLElement>(".scene-sidebar-toggle-label");
    if (iconEl) {
      iconEl.textContent = collapsed ? "<" : ">";
    }
    if (labelEl) {
      labelEl.textContent = collapsed ? "Show Panels" : "Hide Panels";
    }
  }

  function mergeReferencePlans(items: ReferencePlan[]): void {
    const byId = new Map<string, ReferencePlan>();
    for (const plan of [...state.referencePlans, ...items]) {
      byId.set(plan.plan_id, plan);
    }
    state.referencePlans = Array.from(byId.values());
  }

  function renderReferencePlanOptions(preferredPlanId?: string): void {
    const options = [
      `<option value="">Choose a reference plan</option>`,
      ...state.referencePlans.map(
        (plan) => `<option value="${escapeHtml(plan.plan_id)}">${escapeHtml(plan.label || plan.plan_id)}</option>`,
      ),
    ];
    planSelect.innerHTML = options.join("");
    const resolvedPlanId =
      (preferredPlanId && state.referencePlans.some((plan) => plan.plan_id === preferredPlanId) ? preferredPlanId : "") ||
      (state.annotation.plan_id && state.referencePlans.some((plan) => plan.plan_id === state.annotation.plan_id)
        ? state.annotation.plan_id
        : "") ||
      state.referencePlans[0]?.plan_id ||
      "";
    planSelect.value = resolvedPlanId;
  }

  function renderInspector(): void {
    inspectorEl.innerHTML = buildInspectorMarkup(
      state.annotation,
      state.selection,
      state.selectedStripId,
      state.pendingFurnitureKind,
      Boolean(state.furniturePlacement),
    );
    const selectedFeature = getSelectedFeature(state.annotation, state.selection);
    if (!selectedFeature || !state.selection) {
      return;
    }
    if (state.selection.kind === "building_region") {
      const region = selectedFeature as AnnotatedBuildingRegion;
      const regionIdInput = inspectorEl.querySelector<HTMLInputElement>("#annotation-region-id");
      const regionLabelInput = inspectorEl.querySelector<HTMLInputElement>("#annotation-region-label");
      const regionCenterXInput = inspectorEl.querySelector<HTMLInputElement>("#annotation-region-center-x");
      const regionCenterYInput = inspectorEl.querySelector<HTMLInputElement>("#annotation-region-center-y");
      const regionWidthInput = inspectorEl.querySelector<HTMLInputElement>("#annotation-region-width");
      const regionHeightInput = inspectorEl.querySelector<HTMLInputElement>("#annotation-region-height");
      const regionYawInput = inspectorEl.querySelector<HTMLInputElement>("#annotation-region-yaw");
      const updateRegion = (): void => {
        if (regionIdInput) {
          const nextId = regionIdInput.value.trim();
          if (nextId) {
            region.id = nextId;
            state.selection = { kind: "building_region", id: nextId };
          }
        }
        if (regionLabelInput) {
          region.label = regionLabelInput.value.trim() || region.id;
        }
        if (regionCenterXInput) {
          region.center_px.x = asNumber(regionCenterXInput.value, region.center_px.x);
        }
        if (regionCenterYInput) {
          region.center_px.y = asNumber(regionCenterYInput.value, region.center_px.y);
        }
        if (regionWidthInput) {
          region.width_px = Math.max(BUILDING_REGION_MIN_SIZE_PX, asNumber(regionWidthInput.value, region.width_px));
        }
        if (regionHeightInput) {
          region.height_px = Math.max(BUILDING_REGION_MIN_SIZE_PX, asNumber(regionHeightInput.value, region.height_px));
        }
        if (regionYawInput) {
          region.yaw_deg = normalizeAngleDeg(asNumber(regionYawInput.value, region.yaw_deg));
        }
        markAnnotationChanged();
        renderAll();
      };
      for (const input of [
        regionIdInput,
        regionLabelInput,
        regionCenterXInput,
        regionCenterYInput,
        regionWidthInput,
        regionHeightInput,
        regionYawInput,
      ]) {
        input?.addEventListener("input", updateRegion, { signal });
      }
      return;
    }
    const idInput = inspectorEl.querySelector<HTMLInputElement>("#annotation-inspector-id");
    const labelInput = inspectorEl.querySelector<HTMLInputElement>("#annotation-inspector-label");
    const xInput = inspectorEl.querySelector<HTMLInputElement>("#annotation-inspector-x");
    const yInput = inspectorEl.querySelector<HTMLInputElement>("#annotation-inspector-y");
    const kindInput = inspectorEl.querySelector<HTMLInputElement>("#annotation-inspector-kind");
    const radiusInput = inspectorEl.querySelector<HTMLInputElement>("#annotation-inspector-radius");
    const roadWidthInput = inspectorEl.querySelector<HTMLInputElement>("#annotation-inspector-road-width");
    const referenceWidthInput = inspectorEl.querySelector<HTMLInputElement>("#annotation-inspector-reference-width");
    const forwardDriveLaneInput = inspectorEl.querySelector<HTMLInputElement>("#annotation-inspector-forward-drive-lanes");
    const reverseDriveLaneInput = inspectorEl.querySelector<HTMLInputElement>("#annotation-inspector-reverse-drive-lanes");
    const bikeLaneInput = inspectorEl.querySelector<HTMLInputElement>("#annotation-inspector-bike-lanes");
    const busLaneInput = inspectorEl.querySelector<HTMLInputElement>("#annotation-inspector-bus-lanes");
    const parkingLaneInput = inspectorEl.querySelector<HTMLInputElement>("#annotation-inspector-parking-lanes");
    const highwayTypeInput = inspectorEl.querySelector<HTMLInputElement>("#annotation-inspector-highway-type");
    const furnitureKindSelect = inspectorEl.querySelector<HTMLSelectElement>("#annotation-inspector-furniture-kind");

    const updateSelection = (): void => {
      const feature = getSelectedFeature(state.annotation, state.selection);
      if (!feature || !state.selection) {
        return;
      }
      if (idInput) {
        const nextId = idInput.value.trim();
        if (nextId) {
          if ("id" in feature) {
            const previousId = feature.id;
            feature.id = nextId;
            state.selection.id = nextId;
            if (state.selection.kind === "centerline") {
              const centerline = feature as AnnotatedCenterline;
              centerline.street_furniture_instances = centerline.street_furniture_instances.map((item) => ({
                ...item,
                centerline_id: nextId,
              }));
              if (state.furniturePlacement?.centerlineId === previousId) {
                state.furniturePlacement = { ...state.furniturePlacement, centerlineId: nextId };
              }
            }
          }
        }
      }
      if (labelInput && "label" in feature) {
        feature.label = labelInput.value.trim();
      }
      if (xInput && "x" in feature) {
        feature.x = asNumber(xInput.value, feature.x);
      }
      if (yInput && "y" in feature) {
        feature.y = asNumber(yInput.value, feature.y);
      }
      if (kindInput && "kind" in feature) {
        feature.kind = kindInput.value.trim() || feature.kind;
      }
      if (radiusInput && "radius_px" in feature) {
        feature.radius_px = Math.max(8, asNumber(radiusInput.value, feature.radius_px));
      }
      if (roadWidthInput && "road_width_m" in feature) {
        feature.road_width_m = Math.max(1, asNumber(roadWidthInput.value, feature.road_width_m));
      }
      if (referenceWidthInput && "reference_width_px" in feature) {
        const parsedWidth = asNullableNumber(referenceWidthInput.value);
        feature.reference_width_px = parsedWidth === null ? null : Math.max(1, parsedWidth);
      }
      if (forwardDriveLaneInput && "forward_drive_lane_count" in feature) {
        feature.forward_drive_lane_count = asNonNegativeInt(forwardDriveLaneInput.value, feature.forward_drive_lane_count);
      }
      if (reverseDriveLaneInput && "reverse_drive_lane_count" in feature) {
        feature.reverse_drive_lane_count = asNonNegativeInt(reverseDriveLaneInput.value, feature.reverse_drive_lane_count);
      }
      if (bikeLaneInput && "bike_lane_count" in feature) {
        feature.bike_lane_count = asNonNegativeInt(bikeLaneInput.value, feature.bike_lane_count);
      }
      if (busLaneInput && "bus_lane_count" in feature) {
        feature.bus_lane_count = asNonNegativeInt(busLaneInput.value, feature.bus_lane_count);
      }
      if (parkingLaneInput && "parking_lane_count" in feature) {
        feature.parking_lane_count = asNonNegativeInt(parkingLaneInput.value, feature.parking_lane_count);
      }
      if (highwayTypeInput && "highway_type" in feature) {
        feature.highway_type = highwayTypeInput.value.trim() || feature.highway_type;
      }
      if (state.selection.kind === "centerline") {
        syncCenterlineDerivedFields(feature as AnnotatedCenterline);
      }
      markAnnotationChanged();
      renderAll();
    };

    for (const input of [
      idInput,
      labelInput,
      xInput,
      yInput,
      kindInput,
      radiusInput,
      roadWidthInput,
      referenceWidthInput,
      forwardDriveLaneInput,
      reverseDriveLaneInput,
      bikeLaneInput,
      busLaneInput,
      parkingLaneInput,
      highwayTypeInput,
    ]) {
      input?.addEventListener("input", updateSelection, { signal });
    }

    furnitureKindSelect?.addEventListener(
      "change",
      () => {
        const value = asString(furnitureKindSelect.value, state.pendingFurnitureKind);
        state.pendingFurnitureKind = isFurnitureKind(value) ? value : state.pendingFurnitureKind;
        if (state.furniturePlacement) {
          state.furniturePlacement = { ...state.furniturePlacement, kind: state.pendingFurnitureKind };
        }
      },
      { signal },
    );

    const centerline = selectedCenterline();
    if (!centerline) {
      return;
    }

    const stripInputs = Array.from(inspectorEl.querySelectorAll<HTMLElement>("[data-strip-field][data-strip-id]"));
    const stripActionButtons = Array.from(inspectorEl.querySelectorAll<HTMLButtonElement>("[data-action]"));
    const furnitureInputs = Array.from(inspectorEl.querySelectorAll<HTMLElement>("[data-furniture-field][data-instance-id]"));
    const previewResizeHandles = Array.from(
      inspectorEl.querySelectorAll<HTMLButtonElement>("[data-action='start-preview-resize']"),
    );

    const findStripById = (stripId: string): AnnotatedCrossSectionStrip | null =>
      centerline.cross_section_strips.find((strip) => strip.strip_id === stripId) ?? null;

    for (const handle of previewResizeHandles) {
      handle.addEventListener(
        "pointerdown",
        (event) => {
          const leftStripId = handle.dataset.leftStripId;
          const rightStripId = handle.dataset.rightStripId;
          if (!leftStripId || !rightStripId) {
            return;
          }
          const leftStrip = findStripById(leftStripId);
          const rightStrip = findStripById(rightStripId);
          if (!leftStrip || !rightStrip) {
            return;
          }
          const leftShell = inspectorEl.querySelector<HTMLElement>(`[data-preview-strip-shell="${leftStripId}"]`);
          const rightShell = inspectorEl.querySelector<HTMLElement>(`[data-preview-strip-shell="${rightStripId}"]`);
          const pairWidthPx = Math.max(
            1,
            (leftShell?.getBoundingClientRect().width ?? 0) + (rightShell?.getBoundingClientRect().width ?? 0),
          );
          state.previewResize = {
            pointerId: event.pointerId,
            centerlineId: centerline.id,
            leftStripId,
            rightStripId,
            startClientX: event.clientX,
            startLeftWidthM: leftStrip.width_m,
            startRightWidthM: rightStrip.width_m,
            pairWidthPx,
            didResize: false,
          };
          event.preventDefault();
          event.stopPropagation();
        },
        { signal },
      );
    }

    for (const input of stripInputs) {
      const eventName = input instanceof HTMLSelectElement ? "change" : "input";
      input.addEventListener(
        eventName,
        () => {
          const stripId = input.dataset.stripId;
          const field = input.dataset.stripField;
          if (!stripId || !field) {
            return;
          }
          const strip = findStripById(stripId);
          if (!strip) {
            return;
          }
          if (field === "kind" && input instanceof HTMLSelectElement) {
            const nextKind = asString(input.value, strip.kind);
            if (isStripKind(nextKind)) {
              strip.kind = nextKind;
              if (strip.zone === "center" && !CENTER_STRIP_KINDS.has(strip.kind)) {
                strip.kind = "drive_lane";
              }
              if ((strip.zone === "left" || strip.zone === "right") && !SIDE_STRIP_KINDS.has(strip.kind)) {
                strip.kind = "nearroad_furnishing";
              }
              if (SIDE_STRIP_KINDS.has(strip.kind) || strip.kind === "median") {
                strip.direction = "none";
              }
            }
          } else if (field === "width_m" && input instanceof HTMLInputElement) {
            strip.width_m = Math.max(0.1, asNumber(input.value, strip.width_m));
          } else if (field === "direction" && input instanceof HTMLSelectElement) {
            const nextDirection = asString(input.value, strip.direction);
            strip.direction = isStripDirection(nextDirection) ? nextDirection : strip.direction;
            if (SIDE_STRIP_KINDS.has(strip.kind) || strip.kind === "median") {
              strip.direction = "none";
            }
          }
          syncCenterlineDerivedFields(centerline);
          markAnnotationChanged();
          renderAll();
        },
        { signal },
      );
    }

    for (const button of stripActionButtons) {
      button.addEventListener(
        "click",
        () => {
          const action = button.dataset.action;
          if (!action) {
            return;
          }
          if (action === "select-preview-strip") {
            const previewStripId = button.dataset.stripId ?? null;
            if (!previewStripId) {
              return;
            }
            if (resolvedCrossSectionMode(centerline) !== CROSS_SECTION_MODE_DETAILED || centerline.cross_section_strips.length === 0) {
              ensureDetailedCrossSection(centerline);
              state.selectedStripId = centerline.cross_section_strips.find((strip) => strip.strip_id === previewStripId)?.strip_id
                ?? centerline.cross_section_strips[0]?.strip_id
                ?? null;
              clearFurniturePlacement();
              markAnnotationChanged(`Split ${centerline.id} into detailed cross-section strips.`);
              renderAll();
              return;
            }
            state.selectedStripId = previewStripId;
            renderAll();
            return;
          }
          if (action === "select-strip") {
            state.selectedStripId = button.dataset.stripId ?? null;
            renderAll();
            return;
          }
          if (action === "focus-linked-strip") {
            const targetCenterlineId = button.dataset.centerlineId ?? "";
            const targetStripId = button.dataset.stripId ?? "";
            const targetCenterline = state.annotation.centerlines.find((item) => item.id === targetCenterlineId) ?? null;
            if (!targetCenterline) {
              return;
            }
            state.selection = { kind: "centerline", id: targetCenterline.id };
            state.selectedStripId = targetCenterline.cross_section_strips.some((strip) => strip.strip_id === targetStripId)
              ? targetStripId
              : null;
            clearFurniturePlacement();
            renderAll();
            return;
          }
          if (action === "reset-road-width-to-nominal") {
            centerline.road_width_m = nominalSeedCrossSectionWidth(centerline);
            markAnnotationChanged(`Reset ${centerline.id} width to nominal cross-section.`);
            renderAll();
            return;
          }
          if (action === "calibrate-pixels-per-meter") {
            if (centerline.reference_width_px && centerline.road_width_m > 0) {
              state.annotation.pixels_per_meter = Math.max(0.1, centerline.reference_width_px / centerline.road_width_m);
              pixelsPerMeterInput.value = state.annotation.pixels_per_meter.toFixed(2);
              markAnnotationChanged(`Calibrated pixels per meter from ${centerline.id} reference width.`);
              renderAll();
            }
            return;
          }
          if (action === "split-centerline") {
            ensureDetailedCrossSection(centerline);
            state.selectedStripId = centerline.cross_section_strips[0]?.strip_id ?? null;
            clearFurniturePlacement();
            markAnnotationChanged(`Split ${centerline.id} into detailed cross-section strips.`);
            renderAll();
            return;
          }
          if (action === "collapse-centerline") {
            centerline.cross_section_strips = [];
            centerline.street_furniture_instances = [];
            centerline.cross_section_mode = CROSS_SECTION_MODE_COARSE;
            state.selectedStripId = null;
            clearFurniturePlacement();
            syncCenterlineDerivedFields(centerline);
            markAnnotationChanged(`Collapsed ${centerline.id} back to coarse mode.`);
            renderAll();
            return;
          }
          if (action === "add-strip") {
            const zoneValue = asString(button.dataset.zone, "center");
            const zone: StripZone = isStripZone(zoneValue) ? zoneValue : "center";
            centerline.cross_section_strips.push({
              strip_id: nextStripId(centerline, zone),
              zone,
              kind: zone === "center" ? "drive_lane" : "nearroad_furnishing",
              width_m: zone === "center" ? NOMINAL_STRIP_WIDTHS.drive_lane : NOMINAL_STRIP_WIDTHS.nearroad_furnishing,
              direction: zone === "center" ? "forward" : "none",
              order_index: centerline.cross_section_strips.filter((strip) => strip.zone === zone).length,
            });
            syncCenterlineDerivedFields(centerline);
            state.selectedStripId = centerline.cross_section_strips[centerline.cross_section_strips.length - 1]?.strip_id ?? null;
            markAnnotationChanged("Added strip.");
            renderAll();
            return;
          }
          if (action === "move-strip-up" || action === "move-strip-down") {
            const stripId = button.dataset.stripId;
            if (!stripId) {
              return;
            }
            const strip = findStripById(stripId);
            if (!strip) {
              return;
            }
            const zoneStrips = sortedCrossSectionStrips(centerline.cross_section_strips).filter((item) => item.zone === strip.zone);
            const currentIndex = zoneStrips.findIndex((item) => item.strip_id === stripId);
            if (currentIndex < 0) {
              return;
            }
            const swapIndex = action === "move-strip-up" ? currentIndex - 1 : currentIndex + 1;
            if (swapIndex < 0 || swapIndex >= zoneStrips.length) {
              return;
            }
            const swapStrip = zoneStrips[swapIndex];
            const originalOrder = strip.order_index;
            strip.order_index = swapStrip.order_index;
            swapStrip.order_index = originalOrder;
            syncCenterlineDerivedFields(centerline);
            markAnnotationChanged("Reordered strip.");
            renderAll();
            return;
          }
          if (action === "delete-strip") {
            const stripId = button.dataset.stripId;
            if (!stripId) {
              return;
            }
            centerline.cross_section_strips = centerline.cross_section_strips.filter((strip) => strip.strip_id !== stripId);
            centerline.street_furniture_instances = centerline.street_furniture_instances.filter((item) => item.strip_id !== stripId);
            if (state.selectedStripId === stripId) {
              state.selectedStripId = null;
            }
            if (state.furniturePlacement?.stripId === stripId) {
              clearFurniturePlacement();
            }
            syncCenterlineDerivedFields(centerline);
            markAnnotationChanged("Deleted strip.");
            renderAll();
            return;
          }
          if (action === "arm-furniture-placement") {
            const strip = selectedStrip(centerline);
            if (!strip || !FURNITURE_COMPATIBLE_STRIP_KINDS.has(strip.kind)) {
              return;
            }
            state.furniturePlacement = {
              centerlineId: centerline.id,
              stripId: strip.strip_id,
              kind: state.pendingFurnitureKind,
            };
            setStatus(statusEl, `Placement armed for ${strip.strip_id}. Click on the canvas to place ${state.pendingFurnitureKind}.`, "neutral");
            renderAll();
            return;
          }
          if (action === "cancel-furniture-placement") {
            clearFurniturePlacement();
            setStatus(statusEl, "Furniture placement cancelled.", "neutral");
            renderAll();
            return;
          }
          if (action === "delete-furniture") {
            const instanceId = button.dataset.instanceId;
            if (!instanceId) {
              return;
            }
            centerline.street_furniture_instances = centerline.street_furniture_instances.filter((item) => item.instance_id !== instanceId);
            markAnnotationChanged("Deleted furniture instance.");
            renderAll();
          }
        },
        { signal },
      );
    }

    for (const input of furnitureInputs) {
      const eventName = input instanceof HTMLSelectElement ? "change" : "input";
      input.addEventListener(
        eventName,
        () => {
          const instanceId = input.dataset.instanceId;
          const field = input.dataset.furnitureField;
          if (!instanceId || !field) {
            return;
          }
          const instance = centerline.street_furniture_instances.find((item) => item.instance_id === instanceId);
          if (!instance) {
            return;
          }
          if (field === "kind" && input instanceof HTMLSelectElement) {
            const value = asString(input.value, instance.kind);
            if (isFurnitureKind(value)) {
              instance.kind = value;
            }
          } else if (field === "station_m" && input instanceof HTMLInputElement) {
            instance.station_m = Math.max(0, asNumber(input.value, instance.station_m));
          } else if (field === "lateral_offset_m" && input instanceof HTMLInputElement) {
            instance.lateral_offset_m = asNumber(input.value, instance.lateral_offset_m);
          } else if (field === "yaw_deg" && input instanceof HTMLInputElement) {
            instance.yaw_deg = asNullableNumber(input.value);
          }
          syncCenterlineDerivedFields(centerline);
          markAnnotationChanged();
          renderAll();
        },
        { signal },
      );
    }
  }

  function renderOverlay(): void {
    if (!state.currentImageUrl || state.annotation.image_width_px <= 0 || state.annotation.image_height_px <= 0) {
      overlayHostEl.innerHTML = "";
      updateStageVisibility();
      return;
    }
    overlayHostEl.innerHTML = buildOverlayMarkup(
      state.annotation,
      state.draftCenterline,
      state.selection,
      state.selectedStripId,
      {
        showJunctionCore: state.showJunctionCore,
        showJunctionConnectors: state.showJunctionConnectors,
        showJunctionCrosswalks: state.showJunctionCrosswalks,
        showJunctionBoundaries: state.showJunctionBoundaries,
        showJunctionLabels: state.showJunctionLabels,
        showJunctionDebug: state.showJunctionDebug,
        showJunctionOutlines: state.showJunctionOutlines,
      },
      state.branchHoverSnap,
      state.branchDraft,
      state.crossHoverSnap,
      state.crossDraft,
      state.drag?.kind === "building_region_draw" ? state.drag : null,
    );
    updateStageVisibility();
  }

  function renderAll(): void {
    renderToolButtons();
    renderSidebar();
    summaryGridEl.innerHTML = buildAnnotationSummaryMarkup(state.annotation);
    featureTableEl.innerHTML = buildFeatureTableMarkup(state.annotation);
    graphSummaryEl.innerHTML = buildGraphSummaryMarkup(state.graphResult);
    graphTextarea.value = state.graphResult ? JSON.stringify(state.graphResult, null, 2) : "";
    showOriginalInput.checked = state.showOriginal;
    showOverlayInput.checked = state.showOverlay;
    showJunctionCoreInput.checked = state.showJunctionCore;
    showJunctionConnectorsInput.checked = state.showJunctionConnectors;
    showJunctionOutlinesInput.checked = state.showJunctionOutlines;
    showJunctionCrosswalksInput.checked = state.showJunctionCrosswalks;
    showJunctionBoundariesInput.checked = state.showJunctionBoundaries;
    showJunctionLabelsInput.checked = state.showJunctionLabels;
    showJunctionDebugInput.checked = state.showJunctionDebug;
    pixelsPerMeterInput.value = String(state.annotation.pixels_per_meter);
    roundaboutRadiusInput.value = String(state.defaultRoundaboutRadiusPx);
    syncJsonTextarea();
    renderInspector();
    renderOverlay();
    const showInlineLoading = state.isReferenceImageLoading && !state.currentImageUrl;
    imageMetaEl.dataset.loading = showInlineLoading ? "true" : "false";
    imageMetaEl.textContent = showInlineLoading
      ? state.referenceImageLoadingMessage
      : state.currentImageUrl
        ? `${state.annotation.plan_id || "custom"} · ${state.annotation.image_width_px} × ${state.annotation.image_height_px}px · ${state.annotation.pixels_per_meter.toFixed(1)} px/m · ${state.annotation.centerlines.length} roads · ${state.annotation.centerlines.reduce((sum, item) => sum + item.cross_section_strips.length, 0)} strips · ${state.annotation.centerlines.reduce((sum, item) => sum + item.street_furniture_instances.length, 0)} furniture · ${state.annotation.building_regions.length} building regions`
        : "选择参考 plan 或导入 PNG 后，就可以在图上开始标注。";
    finishCenterlineButton.disabled = state.draftCenterline.length < 2;
    selectAllRoadsButton.disabled = state.annotation.centerlines.length === 0;
    selectAllRoadsButton.dataset.active = state.selection?.kind === "road_collection" ? "true" : "false";
    undoPointButton.disabled = state.draftCenterline.length === 0;
    const selectedJunction =
      state.selection?.kind === "junction"
        ? state.annotation.junctions.find((item) => item.id === state.selection?.id) ?? null
        : null;
    deleteSelectedButton.disabled =
      !state.selection ||
      state.selection.kind === "road_collection" ||
      state.selection.kind === "derived_junction" ||
      Boolean(selectedJunction && selectedJunction.source_mode === "explicit");
    imageResetButton.disabled = !state.currentImageUrl;
    downloadGraphButton.disabled = !state.graphResult;
  }

  function setTool(tool: Tool): void {
    state.selectedTool = tool;
    state.drag = null;
    if (tool !== "branch") {
      clearBranchDraft();
    }
    if (tool !== "cross") {
      clearCrossDraft();
    }
    if (tool !== "select") {
      clearFurniturePlacement();
    }
    if (tool === "branch") {
      setStatus(statusEl, "Branch Tool: hover an existing road to snap, click once to lock the anchor, then click again to place the branch.", "neutral");
    } else if (tool === "cross") {
      setStatus(statusEl, `Cross Tool: hover an existing road to snap and extend a cross from it, or click empty space to place a standalone ${STANDALONE_CROSS_ARM_LENGTH_M.toFixed(0)}m cross intersection.`, "neutral");
    } else if (tool === "centerline") {
      setStatus(statusEl, "Centerline Tool: draw approach roads only. Use Branch Tool or Cross Tool to create intersections explicitly.", "neutral");
    } else if (tool === "building_region") {
      setStatus(statusEl, "Building Region Tool: drag on the canvas to draw a rotatable building-generation region.", "neutral");
    } else if (tool === "tree") {
      setStatus(statusEl, "Tree Tool: click near a road to place a tree on the nearest furnishing strip.", "neutral");
    } else if (tool === "lamp") {
      setStatus(statusEl, "Lamp Tool: click near a road to place a lamp on the nearest furnishing strip.", "neutral");
    }
    renderAll();
  }

  function imagePointFromPointer(event: PointerEvent): AnnotationPoint | null {
    if (!state.currentImageUrl || state.annotation.image_width_px <= 0 || state.annotation.image_height_px <= 0) {
      return null;
    }
    const svgEl = overlayHostEl.querySelector<SVGSVGElement>("#annotation-overlay-svg");
    if (!svgEl) {
      return null;
    }
    const rect = svgEl.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) {
      return null;
    }
    const x = clamp(((event.clientX - rect.left) / rect.width) * state.annotation.image_width_px, 0, state.annotation.image_width_px);
    const y = clamp(((event.clientY - rect.top) / rect.height) * state.annotation.image_height_px, 0, state.annotation.image_height_px);
    return { x, y };
  }

function hitFromTarget(target: EventTarget | null): Selection {
    const element = target instanceof Element ? target.closest<HTMLElement>("[data-feature-kind][data-feature-id]") : null;
    if (!element) {
      return null;
    }
    const featureKind = element.dataset.featureKind;
    const featureId = element.dataset.featureId;
    if (!featureKind || !featureId) {
      return null;
    }
    if (featureKind === "centerline") {
      const rawVertexIndex = element.dataset.vertexIndex;
      const selection: Extract<Selection, { kind: "centerline" }> = { kind: "centerline", id: featureId };
      if (rawVertexIndex !== undefined) {
        selection.vertexIndex = Math.max(0, Math.round(asNumber(rawVertexIndex, 0)));
      }
      return selection;
    }
    if (
      featureKind === "junction" ||
      featureKind === "roundabout" ||
      featureKind === "control_point" ||
      featureKind === "derived_junction" ||
      featureKind === "building_region"
    ) {
      return { kind: featureKind, id: featureId };
    }
    return null;
}

function buildingRegionHandleFromTarget(
  target: EventTarget | null,
): { regionId: string; handleKind: "resize" | "rotate"; resizeHandle?: BuildingRegionResizeHandle } | null {
  const element = target instanceof Element ? target.closest<HTMLElement>("[data-region-handle-kind][data-feature-id]") : null;
  if (!element) {
    return null;
  }
  const regionId = element.dataset.featureId;
  const handleKind = element.dataset.regionHandleKind;
  if (!regionId || (handleKind !== "resize" && handleKind !== "rotate")) {
    return null;
  }
  if (handleKind === "resize") {
    const resizeHandle = element.dataset.regionResizeHandle;
    if (resizeHandle === "nw" || resizeHandle === "ne" || resizeHandle === "se" || resizeHandle === "sw") {
      return { regionId, handleKind, resizeHandle };
    }
    return null;
  }
  return { regionId, handleKind };
}

  async function loadImageFromUrl(
    imageUrl: string,
    options: {
      planId: string;
      preserveFeatures: boolean;
    },
  ): Promise<void> {
    const { planId, preserveFeatures } = options;
    const resolvedImageUrl = resolveApiUrl(imageUrl);
    state.isReferenceImageLoading = true;
    state.referenceImageLoadingMessage = `Loading reference image: ${planId || "custom"}...`;
    state.currentImageUrl = resolvedImageUrl;
    renderAll();
    try {
      await new Promise<void>((resolve, reject) => {
        const timeoutId = window.setTimeout(() => reject(new Error("Timed out while loading the selected image.")), 4000);
        originalImageEl.onload = () => {
          window.clearTimeout(timeoutId);
          resolve();
        };
        originalImageEl.onerror = () => {
          window.clearTimeout(timeoutId);
          reject(new Error("Failed to load the selected image."));
        };
        originalImageEl.src = resolvedImageUrl;
      });
      const width = originalImageEl.naturalWidth;
      const height = originalImageEl.naturalHeight;
      if (preserveFeatures) {
        state.annotation.image_width_px = width;
        state.annotation.image_height_px = height;
        state.annotation.image_path = imageUrl;
        state.annotation.plan_id = planId || state.annotation.plan_id;
      } else {
        state.annotation = createEmptyAnnotation(planId, imageUrl, width, height);
      }
      state.selection = null;
      state.selectedStripId = null;
      state.draftCenterline = [];
      clearBranchDraft();
      clearCrossDraft();
      clearFurniturePlacement();
      clearGraphResult("Reference image updated. Convert again after annotating.");
      setStatus(statusEl, `Loaded reference image: ${planId || "custom"}.`, "success");
    } catch (error) {
      state.currentImageUrl = "";
      originalImageEl.removeAttribute("src");
      throw error;
    } finally {
      state.isReferenceImageLoading = false;
      renderAll();
    }
  }

  async function applyReferencePlan(planId: string): Promise<void> {
    const plan = state.referencePlans.find((item) => item.plan_id === planId);
    if (!plan?.image_url) {
      state.annotation.plan_id = planId;
      setStatus(statusEl, `Selected reference plan ${planId}, but no image URL was provided.`, "neutral");
      renderAll();
      return;
    }
    await loadImageFromUrl(plan.image_url, { planId: plan.plan_id, preserveFeatures: false });
  }

  async function loadReferencePlans(options: { silent?: boolean } = {}): Promise<void> {
    const { silent = false } = options;
    if (!silent) {
      state.isReferenceImageLoading = true;
      state.referenceImageLoadingMessage = "Loading reference plans...";
      renderAll();
    }
    const controller = new AbortController();
    const timeoutId = window.setTimeout(() => controller.abort(), 4000);
    try {
      const response = await fetch(`${API_BASE}/api/reference-plans`, { signal: controller.signal });
      if (!response.ok) {
        throw new Error(`Failed to load reference plans (${response.status}).`);
      }
      const payload = (await response.json()) as ReferencePlansPayload;
      mergeReferencePlans(Array.isArray(payload.items) ? payload.items : []);
      const defaultPlan = state.referencePlans.find((item) => item.plan_id === "hkust_gz_gate") ?? state.referencePlans[0];
      renderReferencePlanOptions(defaultPlan?.plan_id);
      if (!state.currentImageUrl && defaultPlan) {
        await applyReferencePlan(defaultPlan.plan_id);
        return;
      }
      renderAll();
    } finally {
      window.clearTimeout(timeoutId);
      if (!silent) {
        state.isReferenceImageLoading = false;
      }
    }
  }

  function finalizeDraftCenterline(): void {
    if (state.draftCenterline.length < 2) {
      setStatus(statusEl, "Centerline needs at least two points.", "error");
      return;
    }
    const snappedDraft = snapDraftCenterlineEndpointsToExplicitJunctions(state.annotation, state.draftCenterline);
    const draftIssues = validateDraftCenterlinePlacement(state.annotation, snappedDraft.points);
    if (draftIssues.length > 0) {
      setStatus(statusEl, draftIssues[0].message, "error");
      return;
    }
    const id = nextFeatureId(state.annotation, "centerline");
    const centerline = createDefaultAnnotatedCenterline(id, snappedDraft.points, {
      startJunctionId: snappedDraft.startJunctionId,
      endJunctionId: snappedDraft.endJunctionId,
    });
    state.annotation.centerlines.push(centerline);
    registerCenterlineWithExplicitJunction(state.annotation, centerline.start_junction_id, centerline.id);
    registerCenterlineWithExplicitJunction(state.annotation, centerline.end_junction_id, centerline.id);
    state.selection = { kind: "centerline", id };
    state.selectedStripId = centerline.cross_section_strips[0]?.strip_id ?? null;
    state.draftCenterline = [];
    clearBranchDraft();
    clearCrossDraft();
    if (centerline.start_junction_id || centerline.end_junction_id) {
      markAnnotationChanged(`Saved centerline ${id}, attached it to explicit junction endpoints, and split it into detailed cross-section strips.`);
    } else {
      markAnnotationChanged(`Saved centerline ${id} and split it into detailed cross-section strips.`);
    }
    renderAll();
  }

  function createStandaloneCrossAtPoint(anchorPoint: AnnotationPoint): void {
    const armLengthPx = STANDALONE_CROSS_ARM_LENGTH_M * Math.max(state.annotation.pixels_per_meter, 0.0001);
    const center = { ...anchorPoint };
    const candidateArms = [
      [{ x: center.x - armLengthPx, y: center.y }, center],
      [{ x: center.x + armLengthPx, y: center.y }, center],
      [{ x: center.x, y: center.y - armLengthPx }, center],
      [{ x: center.x, y: center.y + armLengthPx }, center],
    ];
    for (const points of candidateArms) {
      const issues = validateDraftCenterlinePlacement(state.annotation, points);
      if (issues.length > 0) {
        setStatus(statusEl, issues[0].message, "error");
        renderAll();
        return;
      }
    }

    const junctionId = nextFeatureId(state.annotation, "junction");
    const [westArmId, eastArmId, northArmId, southArmId] = reserveNextFeatureIds(state.annotation, "centerline", 4);
    const arms = [
      createDefaultAnnotatedCenterline(westArmId, candidateArms[0], { endJunctionId: junctionId }),
      createDefaultAnnotatedCenterline(eastArmId, candidateArms[1], { endJunctionId: junctionId }),
      createDefaultAnnotatedCenterline(northArmId, candidateArms[2], { endJunctionId: junctionId }),
      createDefaultAnnotatedCenterline(southArmId, candidateArms[3], { endJunctionId: junctionId }),
    ];
    state.annotation.centerlines.push(...arms);
    createExplicitJunction(state.annotation, {
      junctionId,
      kind: "cross_junction",
      anchor: center,
      connectedCenterlineIds: arms.map((arm) => arm.id),
    });
    state.selection = { kind: "junction", id: junctionId };
    state.selectedStripId = null;
    clearFurniturePlacement();
    clearCrossDraft();
    markAnnotationChanged(
      `Created standalone cross junction ${junctionId} with four ${STANDALONE_CROSS_ARM_LENGTH_M.toFixed(0)}m approach roads.`,
    );
    renderAll();
  }

  function resetAnnotation(): void {
    state.annotation.centerlines = [];
    state.annotation.junctions = [];
    state.annotation.roundabouts = [];
    state.annotation.control_points = [];
    state.annotation.building_regions = [];
    state.selection = null;
    state.selectedStripId = null;
    state.draftCenterline = [];
    clearBranchDraft();
    clearCrossDraft();
    clearFurniturePlacement();
    clearGraphResult("Annotation reset. Draw new features and convert again.");
    setStatus(statusEl, "Annotation cleared.", "neutral");
    renderAll();
  }

  function deleteSelection(): void {
    if (!state.selection) {
      return;
    }
    if (state.selection.kind === "centerline") {
      const lineIndex = state.annotation.centerlines.findIndex((item) => item.id === state.selection?.id);
      if (lineIndex >= 0) {
        const line = state.annotation.centerlines[lineIndex];
        if (state.selection.vertexIndex !== undefined && line.points.length > 2) {
          const removedVertexIndex = state.selection.vertexIndex;
          line.points.splice(state.selection.vertexIndex, 1);
          state.selection = { kind: "centerline", id: line.id };
          setStatus(statusEl, `Removed vertex ${removedVertexIndex + 1} from ${line.id}.`, "success");
        } else {
          state.annotation.centerlines.splice(lineIndex, 1);
          state.selection = null;
          state.selectedStripId = null;
          clearFurniturePlacement();
          setStatus(statusEl, `Deleted centerline ${line.id}.`, "success");
        }
      }
    } else if (state.selection.kind === "junction") {
      const junction = state.annotation.junctions.find((item) => item.id === state.selection?.id) ?? null;
      if (junction?.source_mode === "explicit") {
        setStatus(statusEl, "Explicit junctions are owned by connected road arms. Edit or delete the connected roads instead.", "neutral");
      } else {
        state.annotation.junctions = state.annotation.junctions.filter((item) => item.id !== state.selection?.id);
        state.selection = null;
        setStatus(statusEl, "Deleted junction.", "success");
      }
    } else if (state.selection.kind === "roundabout") {
      state.annotation.roundabouts = state.annotation.roundabouts.filter((item) => item.id !== state.selection?.id);
      state.selection = null;
      setStatus(statusEl, "Deleted roundabout.", "success");
    } else if (state.selection.kind === "control_point") {
      state.annotation.control_points = state.annotation.control_points.filter((item) => item.id !== state.selection?.id);
      state.selection = null;
      setStatus(statusEl, "Deleted control point.", "success");
    } else if (state.selection.kind === "building_region") {
      state.annotation.building_regions = state.annotation.building_regions.filter((item) => item.id !== state.selection?.id);
      state.selection = null;
      setStatus(statusEl, "Deleted building region.", "success");
    } else if (state.selection.kind === "derived_junction") {
      setStatus(statusEl, "Derived junctions come from shared road vertices. Edit the connected centerlines instead.", "neutral");
    }
    clearGraphResult("Annotation changed. Re-run convert to refresh graph output.");
    renderAll();
  }

  async function convertAnnotationToGraph(): Promise<void> {
    if (state.annotation.centerlines.length === 0) {
      setStatus(graphStatusEl, "Add at least one centerline before converting.", "error");
      return;
    }
    const modelIssues = validateAnnotationForExplicitJunctionModel(state.annotation);
    if (modelIssues.length > 0) {
      setStatus(graphStatusEl, modelIssues[0].message, "error");
      return;
    }
    setStatus(graphStatusEl, "Converting annotation to graph...", "neutral");
    for (const centerline of state.annotation.centerlines) {
      syncCenterlineDerivedFields(centerline);
    }
    const response = await fetch(`${API_BASE}/api/reference-annotations/convert`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        annotation: state.annotation,
        compose_config: {
          sidewalk_width_m: Math.max(1, asNumber(sidewalkWidthInput.value, DEFAULT_SIDEWALK_WIDTH_M)),
          segment_length_m: Math.max(4, asNumber(segmentLengthInput.value, DEFAULT_SEGMENT_LENGTH_M)),
        },
      }),
    });
    const payload = (await response.json()) as ConvertedGraphPayload | { detail?: string };
    if (!response.ok) {
      throw new Error(typeof payload === "object" && payload && "detail" in payload ? String(payload.detail) : "Graph conversion failed.");
    }
    const convertedPayload = payload as ConvertedGraphPayload;
    const annotationSnapshot = cloneAnnotation(state.annotation);
    state.graphResult = {
      ...convertedPayload,
      annotation: annotationSnapshot,
      summary: {
        ...convertedPayload.summary,
        building_region_count: annotationSnapshot.building_regions.length,
      },
    };
    setStatus(graphStatusEl, "Graph conversion complete.", "success");
    renderAll();
  }

  function syncSelectionAfterMutation(): void {
    if (!state.selection) {
      state.selectedStripId = null;
      clearFurniturePlacement();
      return;
    }
    if (state.selection.kind === "road_collection") {
      if (state.annotation.centerlines.length === 0) {
        state.selection = null;
      }
      state.selectedStripId = null;
      clearFurniturePlacement();
      return;
    }
    const feature = getSelectedFeature(state.annotation, state.selection);
    if (!feature) {
      state.selection = null;
      state.selectedStripId = null;
      clearFurniturePlacement();
      return;
    }
    if (state.selection.kind === "centerline") {
      const centerline = feature as AnnotatedCenterline;
      if (state.selectedStripId && !centerline.cross_section_strips.some((strip) => strip.strip_id === state.selectedStripId)) {
        state.selectedStripId = null;
      }
      if (!state.selectedStripId && centerline.cross_section_strips.length > 0) {
        state.selectedStripId = centerline.cross_section_strips[0]?.strip_id ?? null;
      }
      if (
        state.furniturePlacement &&
        (state.furniturePlacement.centerlineId !== centerline.id ||
          !centerline.cross_section_strips.some((strip) => strip.strip_id === state.furniturePlacement?.stripId))
      ) {
        clearFurniturePlacement();
      }
    } else {
      state.selectedStripId = null;
      clearFurniturePlacement();
    }
  }

  function nextFurnitureInstanceId(centerline: AnnotatedCenterline): string {
    const used = new Set(centerline.street_furniture_instances.map((item) => item.instance_id));
    let counter = centerline.street_furniture_instances.length + 1;
    while (true) {
      const candidate = `${centerline.id}_furniture_${String(counter).padStart(2, "0")}`;
      if (!used.has(candidate)) {
        return candidate;
      }
      counter += 1;
    }
  }

  function createArmFromProfile(
    source: AnnotatedCenterline,
    id: string,
    points: AnnotationPoint[],
    options: {
      startJunctionId?: string;
      endJunctionId?: string;
    } = {},
  ): AnnotatedCenterline {
    const arm = cloneCenterlineForBranch(source, id, points);
    arm.start_junction_id = options.startJunctionId ?? "";
    arm.end_junction_id = options.endJunctionId ?? "";
    syncCenterlineDerivedFields(arm);
    return arm;
  }

  function ensureExplicitJunctionAtSnap(
    annotation: ReferenceAnnotation,
    centerlineId: string,
    snap: BranchSnapTarget,
    options: {
      junctionId?: string;
      kind?: string;
      additionalConnectedCenterlineIds?: string[];
      blockedCenterlineIds?: string[];
    } = {},
  ): {
    junction: AnnotatedJunction;
    anchorPoint: AnnotationPoint;
  } | null {
    const centerline = annotation.centerlines.find((item) => item.id === centerlineId);
    if (!centerline) {
      return null;
    }
    const existingJunctionId = endpointJunctionIdAtPoint(centerline, snap.point);
    if (existingJunctionId) {
      const existingJunction = annotation.junctions.find((item) => item.id === existingJunctionId) ?? null;
      if (!existingJunction) {
        return null;
      }
      updateJunctionConnectedCenterlines(annotation, existingJunction.id, [
        ...existingJunction.connected_centerline_ids,
        centerline.id,
        ...(options.additionalConnectedCenterlineIds ?? []),
      ]);
      return {
        junction: existingJunction,
        anchorPoint: junctionAnchorPoint(existingJunction),
      };
    }

    const junctionId = options.junctionId ?? nextFeatureId(annotation, "junction");
    const splitResult = splitCenterlineAtSnap(
      annotation,
      centerlineId,
      snap,
      junctionId,
      options.blockedCenterlineIds ?? [],
    );
    if (!splitResult) {
      return null;
    }
    let junction = annotation.junctions.find((item) => item.id === junctionId) ?? null;
    if (!junction) {
      junction = createExplicitJunction(annotation, {
        junctionId,
        kind: options.kind ?? "t_junction",
        anchor: splitResult.anchorPoint,
        connectedCenterlineIds: [
          ...splitResult.connectedCenterlineIds,
          ...(options.additionalConnectedCenterlineIds ?? []),
        ],
      });
    } else {
      updateJunctionConnectedCenterlines(annotation, junction.id, [
        ...junction.connected_centerline_ids,
        ...splitResult.connectedCenterlineIds,
        ...(options.additionalConnectedCenterlineIds ?? []),
      ]);
      junction.x = splitResult.anchorPoint.x;
      junction.y = splitResult.anchorPoint.y;
    }
    return {
      junction,
      anchorPoint: splitResult.anchorPoint,
    };
  }

  function maybeConnectArmEndpointToSnap(
    sourceCenterline: AnnotatedCenterline,
    arm: AnnotatedCenterline,
    endpoint: AnnotationPoint,
    endpointSnap: BranchSnapTarget | null,
    armEndpoint: "start" | "end",
  ): AnnotationPoint | null {
    if (!endpointSnap) {
      return endpoint;
    }
    const target = state.annotation.centerlines.find((item) => item.id === endpointSnap.centerlineId);
    if (!target) {
      setStatus(statusEl, "Could not resolve the snapped target road.", "error");
      return null;
    }
    if (target.id === sourceCenterline.id) {
      const targetJunctionId = endpointJunctionIdAtPoint(target, endpointSnap.point);
      if (!targetJunctionId) {
        setStatus(statusEl, "Endpoint cannot snap back onto the same source road unless it reaches an existing junction endpoint.", "error");
        return null;
      }
    }
    const targetJunctionId = nextFeatureId(state.annotation, "junction");
    const targetJunctionResult = ensureExplicitJunctionAtSnap(state.annotation, target.id, endpointSnap, {
      junctionId: targetJunctionId,
      kind: "t_junction",
      additionalConnectedCenterlineIds: [arm.id],
      blockedCenterlineIds: [arm.id],
    });
    if (!targetJunctionResult) {
      setStatus(statusEl, "Failed to create the target junction for the snapped endpoint.", "error");
      return null;
    }
    if (armEndpoint === "start") {
      arm.start_junction_id = targetJunctionResult.junction.id;
    } else {
      arm.end_junction_id = targetJunctionResult.junction.id;
    }
    updateJunctionConnectedCenterlines(state.annotation, targetJunctionResult.junction.id, [
      ...targetJunctionResult.junction.connected_centerline_ids,
      arm.id,
    ]);
    return targetJunctionResult.anchorPoint;
  }

  function updateBranchPreview(point: AnnotationPoint | null): void {
    if (state.selectedTool !== "branch") {
      clearBranchDraft();
      return;
    }
    if (!point) {
      state.branchHoverSnap = null;
      return;
    }
    if (!state.branchDraft) {
      state.branchHoverSnap = findNearestBranchSnapTarget(state.annotation, point);
      return;
    }
    const endpointSnap = findNearestBranchSnapTarget(state.annotation, point, {
      excludeCenterlineId: state.branchDraft.anchor.centerlineId,
    });
    state.branchHoverSnap = null;
    state.branchDraft = {
      ...state.branchDraft,
      endpoint: endpointSnap ? { ...endpointSnap.point } : { ...point },
      endpointSnap,
    };
  }

  function updateCrossPreview(point: AnnotationPoint | null): void {
    if (state.selectedTool !== "cross") {
      clearCrossDraft();
      return;
    }
    if (!point) {
      state.crossHoverSnap = null;
      return;
    }
    if (!state.crossDraft) {
      state.crossHoverSnap = findNearestBranchSnapTarget(state.annotation, point);
      return;
    }
    const anchorPoint = state.crossDraft.anchor.point;
    const axisNormal = state.crossDraft.axisNormal;
    const signedDistancePx =
      (point.x - anchorPoint.x) * axisNormal.x +
      (point.y - anchorPoint.y) * axisNormal.y;
    const halfLengthPx = Math.abs(signedDistancePx);
    const desiredPositive = pointOnAxis(anchorPoint, axisNormal, halfLengthPx);
    const desiredNegative = pointOnAxis(anchorPoint, axisNormal, -halfLengthPx);
    const positiveEndpointSnap = findNearestBranchSnapTarget(state.annotation, desiredPositive, {
      excludeCenterlineId: state.crossDraft.anchor.centerlineId,
    });
    const negativeEndpointSnap = findNearestBranchSnapTarget(state.annotation, desiredNegative, {
      excludeCenterlineId: state.crossDraft.anchor.centerlineId,
    });
    state.crossHoverSnap = null;
    state.crossDraft = {
      ...state.crossDraft,
      halfLengthPx,
      positiveEndpoint: positiveEndpointSnap ? { ...positiveEndpointSnap.point } : desiredPositive,
      negativeEndpoint: negativeEndpointSnap ? { ...negativeEndpointSnap.point } : desiredNegative,
      positiveEndpointSnap,
      negativeEndpointSnap,
    };
  }

  function beginBranchFromSnap(snap: BranchSnapTarget): void {
    const host = state.annotation.centerlines.find((item) => item.id === snap.centerlineId);
    if (!host) {
      setStatus(statusEl, "Could not resolve the host road for this branch anchor.", "error");
      return;
    }
    const anchorPoint = insertSharedVertexAtSnap(host, snap);
    state.branchDraft = {
      anchor: { ...snap, point: { ...anchorPoint } },
      endpoint: { ...anchorPoint },
      endpointSnap: null,
    };
    state.branchHoverSnap = null;
    state.selection = { kind: "centerline", id: host.id };
    state.selectedStripId = host.cross_section_strips[0]?.strip_id ?? null;
    clearFurniturePlacement();
    markAnnotationChanged(`Locked branch anchor on ${host.id}. Move the mouse and click again to place the branch end.`);
    renderAll();
  }

  function beginCrossFromSnap(snap: BranchSnapTarget): void {
    const host = state.annotation.centerlines.find((item) => item.id === snap.centerlineId);
    if (!host) {
      setStatus(statusEl, "Could not resolve the host road for this cross anchor.", "error");
      return;
    }
    const anchorPoint = insertSharedVertexAtSnap(host, snap);
    const axisNormal = crossAxisNormalAtSnap(host, snap);
    state.crossDraft = {
      anchor: { ...snap, point: { ...anchorPoint } },
      axisNormal,
      halfLengthPx: 0,
      negativeEndpoint: { ...anchorPoint },
      positiveEndpoint: { ...anchorPoint },
      negativeEndpointSnap: null,
      positiveEndpointSnap: null,
    };
    state.crossHoverSnap = null;
    state.selection = { kind: "centerline", id: host.id };
    state.selectedStripId = host.cross_section_strips[0]?.strip_id ?? null;
    clearFurniturePlacement();
    markAnnotationChanged(`Locked cross center on ${host.id}. Move the mouse and click again to set the cross half-length.`);
    renderAll();
  }

  function commitBranchAtPoint(point: AnnotationPoint): void {
    const draft = state.branchDraft;
    if (!draft) {
      return;
    }
    const host = state.annotation.centerlines.find((item) => item.id === draft.anchor.centerlineId);
    if (!host) {
      clearBranchDraft();
      setStatus(statusEl, "The host road is no longer available. Start the branch again.", "error");
      renderAll();
      return;
    }
    const endpointSnap = findNearestBranchSnapTarget(state.annotation, point, {
      excludeCenterlineId: draft.anchor.centerlineId,
    });
    let endpoint = endpointSnap ? { ...endpointSnap.point } : { ...point };
    if (!endpointSnap) {
      const hostProjection = projectPointOntoPolyline(host.points, point);
      if (hostProjection.distancePx <= BRANCH_SNAP_TOLERANCE_PX) {
        setStatus(statusEl, "Branch end cannot snap back onto the host road. Click away from the host or snap to another road.", "error");
        state.branchDraft = {
          ...draft,
          endpoint: { ...hostProjection.projectedPoint },
          endpointSnap: null,
        };
        renderAll();
        return;
      }
    }
    const minLengthPx = BRANCH_MIN_LENGTH_M * Math.max(state.annotation.pixels_per_meter, 0.0001);
    if (pointDistance(draft.anchor.point, endpoint) < minLengthPx) {
      setStatus(statusEl, `Branch is too short. Minimum branch length is ${BRANCH_MIN_LENGTH_M.toFixed(1)}m.`, "error");
      state.branchDraft = {
        ...draft,
        endpoint,
        endpointSnap,
      };
      renderAll();
      return;
    }
    const branchId = nextFeatureId(state.annotation, "centerline");
    const centralJunctionId = nextFeatureId(state.annotation, "junction");
    const hostJunctionResult = ensureExplicitJunctionAtSnap(state.annotation, host.id, draft.anchor, {
      junctionId: centralJunctionId,
      kind: "t_junction",
      additionalConnectedCenterlineIds: [branchId],
      blockedCenterlineIds: [branchId],
    });
    if (!hostJunctionResult) {
      clearBranchDraft();
      setStatus(statusEl, "Could not create a branch junction on the host road.", "error");
      renderAll();
      return;
    }
    const branch = createArmFromProfile(host, branchId, [hostJunctionResult.anchorPoint, endpoint], {
      startJunctionId: hostJunctionResult.junction.id,
    });
    const resolvedEndpoint = maybeConnectArmEndpointToSnap(host, branch, endpoint, endpointSnap, "end");
    if (!resolvedEndpoint) {
      renderAll();
      return;
    }
    branch.points = [clonePoint(hostJunctionResult.anchorPoint), clonePoint(resolvedEndpoint)];
    syncCenterlineDerivedFields(branch);
    state.annotation.centerlines.push(branch);
    updateJunctionConnectedCenterlines(state.annotation, hostJunctionResult.junction.id, [
      ...hostJunctionResult.junction.connected_centerline_ids,
      branch.id,
    ]);
    state.selection = { kind: "centerline", id: branchId };
    state.selectedStripId = branch.cross_section_strips[0]?.strip_id ?? null;
    clearFurniturePlacement();
    clearBranchDraft();
    markAnnotationChanged(`Created branch ${branchId}.`);
    renderAll();
  }

  function commitCrossAtPoint(point: AnnotationPoint): void {
    const draft = state.crossDraft;
    if (!draft) {
      return;
    }
    const host = state.annotation.centerlines.find((item) => item.id === draft.anchor.centerlineId);
    if (!host) {
      clearCrossDraft();
      setStatus(statusEl, "The host road is no longer available. Start the cross again.", "error");
      renderAll();
      return;
    }
    updateCrossPreview(point);
    const refreshedDraft = state.crossDraft;
    if (!refreshedDraft) {
      return;
    }
    const minLengthPx = CROSS_MIN_HALF_LENGTH_M * Math.max(state.annotation.pixels_per_meter, 0.0001);
    if (refreshedDraft.halfLengthPx < minLengthPx) {
      setStatus(
        statusEl,
        `Cross is too short. Minimum half-length is ${CROSS_MIN_HALF_LENGTH_M.toFixed(1)}m.`,
        "error",
      );
      renderAll();
      return;
    }
    let negativeEndpoint = { ...refreshedDraft.negativeEndpoint };
    let positiveEndpoint = { ...refreshedDraft.positiveEndpoint };
    const [negativeArmId, positiveArmId] = reserveNextFeatureIds(state.annotation, "centerline", 2);
    const centralJunctionId = nextFeatureId(state.annotation, "junction");
    const hostJunctionResult = ensureExplicitJunctionAtSnap(state.annotation, host.id, refreshedDraft.anchor, {
      junctionId: centralJunctionId,
      kind: "cross_junction",
      additionalConnectedCenterlineIds: [negativeArmId, positiveArmId],
      blockedCenterlineIds: [negativeArmId, positiveArmId],
    });
    if (!hostJunctionResult) {
      clearCrossDraft();
      setStatus(statusEl, "Could not create a cross junction on the host road.", "error");
      renderAll();
      return;
    }
    const negativeArm = createArmFromProfile(host, negativeArmId, [hostJunctionResult.anchorPoint, negativeEndpoint], {
      startJunctionId: hostJunctionResult.junction.id,
    });
    const positiveArm = createArmFromProfile(host, positiveArmId, [hostJunctionResult.anchorPoint, positiveEndpoint], {
      startJunctionId: hostJunctionResult.junction.id,
    });
    const resolvedNegative = maybeConnectArmEndpointToSnap(
      host,
      negativeArm,
      negativeEndpoint,
      refreshedDraft.negativeEndpointSnap,
      "end",
    );
    if (!resolvedNegative) {
      renderAll();
      return;
    }
    const resolvedPositive = maybeConnectArmEndpointToSnap(
      host,
      positiveArm,
      positiveEndpoint,
      refreshedDraft.positiveEndpointSnap,
      "end",
    );
    if (!resolvedPositive) {
      renderAll();
      return;
    }
    negativeArm.points = [clonePoint(hostJunctionResult.anchorPoint), clonePoint(resolvedNegative)];
    positiveArm.points = [clonePoint(hostJunctionResult.anchorPoint), clonePoint(resolvedPositive)];
    syncCenterlineDerivedFields(negativeArm);
    syncCenterlineDerivedFields(positiveArm);
    state.annotation.centerlines.push(negativeArm, positiveArm);
    updateJunctionConnectedCenterlines(state.annotation, hostJunctionResult.junction.id, [
      ...hostJunctionResult.junction.connected_centerline_ids,
      negativeArm.id,
      positiveArm.id,
    ]);
    const autoDetailedRoadIds = new Set<string>([
      ...hostJunctionResult.junction.connected_centerline_ids,
      negativeArm.id,
      positiveArm.id,
    ]);
    for (const junction of state.annotation.junctions) {
      if (
        junction.source_mode === "explicit" &&
        (junction.id === hostJunctionResult.junction.id ||
          junction.connected_centerline_ids.includes(negativeArm.id) ||
          junction.connected_centerline_ids.includes(positiveArm.id))
      ) {
        for (const centerlineId of junction.connected_centerline_ids) {
          autoDetailedRoadIds.add(centerlineId);
        }
      }
    }
    ensureDetailedCrossSections(state.annotation, autoDetailedRoadIds);
    state.selection = { kind: "junction", id: hostJunctionResult.junction.id };
    state.selectedStripId = null;
    clearFurniturePlacement();
    clearCrossDraft();
    markAnnotationChanged(`Created cross junction ${hostJunctionResult.junction.id} and auto-split the connected roads into detailed cross-sections.`);
    renderAll();
  }

  function placeFurnitureAtPoint(point: AnnotationPoint): boolean {
    if (!state.furniturePlacement) {
      return false;
    }
    const centerline = state.annotation.centerlines.find((item) => item.id === state.furniturePlacement?.centerlineId);
    if (!centerline) {
      clearFurniturePlacement();
      return false;
    }
    const strip = centerline.cross_section_strips.find((item) => item.strip_id === state.furniturePlacement?.stripId);
    if (!strip || !FURNITURE_COMPATIBLE_STRIP_KINDS.has(strip.kind)) {
      clearFurniturePlacement();
      return false;
    }
    const projection = projectPointOntoPolyline(centerline.points, point);
    const ppm = Math.max(state.annotation.pixels_per_meter, 0.0001);
    const stripBounds = stripCenterOffsetMeters(centerline)[strip.strip_id];
    const halfWidthM = stripBounds ? stripBounds.widthM * 0.5 : strip.width_m * 0.5;
    const centerOffsetM = stripBounds ? stripBounds.centerOffsetM : 0;
    const absoluteLateralOffsetM = clamp(
      projection.lateralPx / ppm,
      centerOffsetM - halfWidthM,
      centerOffsetM + halfWidthM,
    );
    const lateralOffsetM = absoluteLateralOffsetM - centerOffsetM;
    centerline.street_furniture_instances.push({
      instance_id: nextFurnitureInstanceId(centerline),
      centerline_id: centerline.id,
      strip_id: strip.strip_id,
      kind: state.furniturePlacement.kind,
      station_m: projection.stationPx / ppm,
      lateral_offset_m: lateralOffsetM,
      yaw_deg: null,
    });
    syncCenterlineDerivedFields(centerline);
    clearGraphResult("Annotation changed. Re-run convert to refresh graph output.");
    setStatus(
      statusEl,
      `Placed ${state.furniturePlacement.kind} on ${strip.strip_id}. Click again to place another, or cancel placement.`,
      "success",
    );
    renderAll();
    return true;
  }

  function placeFurnitureQuick(point: AnnotationPoint, kind: FurnitureKind): boolean {
    const ppm = Math.max(state.annotation.pixels_per_meter, 0.0001);
    // 1. Find nearest centerline by projecting point onto each
    let bestCenterline: AnnotatedCenterline | null = null;
    let bestProjection: ReturnType<typeof projectPointOntoPolyline> | null = null;
    for (const cl of state.annotation.centerlines) {
      if (cl.points.length < 2) {
        continue;
      }
      const proj = projectPointOntoPolyline(cl.points, point);
      if (!bestProjection || proj.distancePx < bestProjection.distancePx) {
        bestCenterline = cl;
        bestProjection = proj;
      }
    }
    if (!bestCenterline || !bestProjection || bestProjection.distancePx > 200) {
      setStatus(statusEl, "No road nearby. Click closer to a centerline.", "error");
      return false;
    }
    // 2. Find nearest furniture-compatible strip
    const offsets = stripCenterOffsetMeters(bestCenterline);
    const clickLateralM = bestProjection.lateralPx / ppm;
    let bestStrip: AnnotatedCrossSectionStrip | null = null;
    let bestDist = Infinity;
    for (const strip of bestCenterline.cross_section_strips) {
      if (!FURNITURE_COMPATIBLE_STRIP_KINDS.has(strip.kind)) {
        continue;
      }
      const info = offsets[strip.strip_id];
      if (!info) {
        continue;
      }
      const dist = Math.abs(info.centerOffsetM - clickLateralM);
      if (dist < bestDist) {
        bestDist = dist;
        bestStrip = strip;
      }
    }
    if (!bestStrip) {
      setStatus(statusEl, "No furnishing strip on this road. Add a nearroad_furnishing or frontage_reserve strip first.", "error");
      return false;
    }
    // 3. Compute position and create instance
    const stripBounds = offsets[bestStrip.strip_id];
    const halfW = stripBounds ? stripBounds.widthM * 0.5 : bestStrip.width_m * 0.5;
    const centerOff = stripBounds ? stripBounds.centerOffsetM : 0;
    const absLateral = clamp(clickLateralM, centerOff - halfW, centerOff + halfW);
    const lateralOffsetM = absLateral - centerOff;
    bestCenterline.street_furniture_instances.push({
      instance_id: nextFurnitureInstanceId(bestCenterline),
      centerline_id: bestCenterline.id,
      strip_id: bestStrip.strip_id,
      kind,
      station_m: bestProjection.stationPx / ppm,
      lateral_offset_m: lateralOffsetM,
      yaw_deg: null,
    });
    syncCenterlineDerivedFields(bestCenterline);
    clearGraphResult("Annotation changed. Re-run convert to refresh graph output.");
    setStatus(statusEl, `Placed ${FURNITURE_KIND_LABELS[kind]} on ${bestStrip.strip_id}.`, "success");
    renderAll();
    return true;
  }

  const assetEditorButton = requireElement<HTMLButtonElement>(root, "#scene-page-asset-editor");
  assetEditorButton.addEventListener(
    "click",
    () => {
      window.location.hash = "#asset-editor";
    },
    { signal },
  );

  backButton.addEventListener(
    "click",
    () => {
      window.location.hash = "#viewer";
    },
    { signal },
  );

  sidebarToggleButton.addEventListener(
    "click",
    () => {
      state.isSidebarCollapsed = !state.isSidebarCollapsed;
      renderAll();
    },
    { signal },
  );

  planSelect.addEventListener(
    "change",
    async () => {
      if (!planSelect.value) {
        return;
      }
      try {
        await applyReferencePlan(planSelect.value);
      } catch (error) {
        setStatus(statusEl, error instanceof Error ? error.message : "Failed to load reference plan.", "error");
      }
    },
    { signal },
  );

  imageInput.addEventListener(
    "change",
    async () => {
      const file = imageInput.files?.[0];
      if (!file) {
        return;
      }
      try {
        revokeCurrentObjectUrl();
        state.currentObjectUrl = URL.createObjectURL(file);
        await loadImageFromUrl(state.currentObjectUrl, { planId: "custom_upload", preserveFeatures: false });
        state.annotation.image_path = file.name;
        planSelect.value = "";
      } catch (error) {
        setStatus(statusEl, error instanceof Error ? error.message : "Failed to load uploaded image.", "error");
      } finally {
        imageInput.value = "";
      }
    },
    { signal },
  );

  imageResetButton.addEventListener(
    "click",
    () => {
      revokeCurrentObjectUrl();
      state.currentImageUrl = "";
      state.annotation = createEmptyAnnotation(state.annotation.plan_id);
      state.selection = null;
      state.selectedStripId = null;
      state.draftCenterline = [];
      clearBranchDraft();
      clearCrossDraft();
      clearFurniturePlacement();
      originalImageEl.removeAttribute("src");
      clearGraphResult("Image cleared. Load another reference plan to continue.");
      setStatus(statusEl, "Reference image cleared.", "neutral");
      renderAll();
    },
    { signal },
  );

  showOriginalInput.addEventListener(
    "change",
    () => {
      state.showOriginal = showOriginalInput.checked;
      updateStageVisibility();
    },
    { signal },
  );
  showOverlayInput.addEventListener(
    "change",
    () => {
      state.showOverlay = showOverlayInput.checked;
      updateStageVisibility();
    },
    { signal },
  );
  showJunctionCoreInput.addEventListener(
    "change",
    () => {
      state.showJunctionCore = showJunctionCoreInput.checked;
      renderOverlay();
    },
    { signal },
  );
  showJunctionConnectorsInput.addEventListener(
    "change",
    () => {
      state.showJunctionConnectors = showJunctionConnectorsInput.checked;
      renderOverlay();
    },
    { signal },
  );
  showJunctionOutlinesInput.addEventListener(
    "change",
    () => {
      state.showJunctionOutlines = showJunctionOutlinesInput.checked;
      renderOverlay();
    },
    { signal },
  );
  showJunctionCrosswalksInput.addEventListener(
    "change",
    () => {
      state.showJunctionCrosswalks = showJunctionCrosswalksInput.checked;
      renderOverlay();
    },
    { signal },
  );
  showJunctionBoundariesInput.addEventListener(
    "change",
    () => {
      state.showJunctionBoundaries = showJunctionBoundariesInput.checked;
      renderOverlay();
    },
    { signal },
  );
  showJunctionLabelsInput.addEventListener(
    "change",
    () => {
      state.showJunctionLabels = showJunctionLabelsInput.checked;
      renderOverlay();
    },
    { signal },
  );
  showJunctionDebugInput.addEventListener(
    "change",
    () => {
      state.showJunctionDebug = showJunctionDebugInput.checked;
      renderOverlay();
    },
    { signal },
  );
  originalOpacityInput.addEventListener(
    "input",
    () => {
      state.originalOpacity = asNumber(originalOpacityInput.value, 100) / 100;
      updateStageVisibility();
    },
    { signal },
  );
  overlayOpacityInput.addEventListener(
    "input",
    () => {
      state.overlayOpacity = asNumber(overlayOpacityInput.value, 88) / 100;
      updateStageVisibility();
    },
    { signal },
  );
  pixelsPerMeterInput.addEventListener(
    "input",
    () => {
      state.annotation.pixels_per_meter = Math.max(0.1, asNumber(pixelsPerMeterInput.value, state.annotation.pixels_per_meter));
      clearGraphResult("Scale changed. Re-run convert to refresh graph output.");
      renderAll();
    },
    { signal },
  );
  roundaboutRadiusInput.addEventListener(
    "input",
    () => {
      state.defaultRoundaboutRadiusPx = Math.max(8, asNumber(roundaboutRadiusInput.value, state.defaultRoundaboutRadiusPx));
    },
    { signal },
  );

  for (const button of toolButtons) {
    button.addEventListener(
      "click",
      () => {
        const tool = button.dataset.tool as Tool | undefined;
        if (tool) {
          setTool(tool);
        }
      },
      { signal },
    );
  }

  finishCenterlineButton.addEventListener("click", finalizeDraftCenterline, { signal });
  selectAllRoadsButton.addEventListener(
    "click",
    () => {
      if (state.annotation.centerlines.length === 0) {
        return;
      }
      if (state.selection?.kind === "road_collection") {
        state.selection = null;
        state.selectedStripId = null;
        clearFurniturePlacement();
        setStatus(statusEl, "Cleared road collection selection.", "neutral");
        renderAll();
        return;
      }
      state.selection = { kind: "road_collection", id: ALL_ROADS_SELECTION_ID };
      state.selectedStripId = null;
      clearFurniturePlacement();
      setStatus(statusEl, `Selected all ${state.annotation.centerlines.length} roads.`, "neutral");
      renderAll();
    },
    { signal },
  );
  undoPointButton.addEventListener(
    "click",
    () => {
      state.draftCenterline.pop();
      renderAll();
    },
    { signal },
  );
  deleteSelectedButton.addEventListener("click", deleteSelection, { signal });
  resetAnnotationButton.addEventListener("click", resetAnnotation, { signal });

  overlayHostEl.addEventListener(
    "pointerdown",
    (event) => {
      if (!state.currentImageUrl) {
        return;
      }
      const hit = hitFromTarget(event.target);
      const point = imagePointFromPointer(event);

      if (state.selectedTool === "select") {
        if (state.furniturePlacement && point) {
          if (placeFurnitureAtPoint(point)) {
            return;
          }
        }
        state.selection = hit;
        if (hit?.kind !== "centerline") {
          state.selectedStripId = null;
        }
        state.drag = null;
        syncSelectionAfterMutation();
        renderAll();
        return;
      }

      if (state.selectedTool === "adjust") {
        const regionHandle = buildingRegionHandleFromTarget(event.target);
        state.selection = hit;
        if (hit?.kind !== "centerline") {
          state.selectedStripId = null;
        }
        if (regionHandle) {
          state.selection = { kind: "building_region", id: regionHandle.regionId };
          state.selectedStripId = null;
          state.drag =
            regionHandle.handleKind === "resize"
              ? {
                  kind: "building_region_resize",
                  id: regionHandle.regionId,
                  pointerId: event.pointerId,
                  handle: regionHandle.resizeHandle ?? "se",
                }
              : {
                  kind: "building_region_rotate",
                  id: regionHandle.regionId,
                  pointerId: event.pointerId,
                };
          event.preventDefault();
          event.stopPropagation();
        } else if (hit?.kind === "building_region" && point) {
          state.drag = {
            kind: "building_region_translate",
            id: hit.id,
            pointerId: event.pointerId,
            lastPoint: point,
          };
        } else if (hit?.kind === "centerline" && hit.vertexIndex !== undefined) {
          state.drag = {
            kind: "centerline_vertex",
            id: hit.id,
            vertexIndex: hit.vertexIndex,
            pointerId: event.pointerId,
          };
        } else if (hit?.kind === "centerline" && point) {
          state.drag = {
            kind: "centerline_translate",
            id: hit.id,
            pointerId: event.pointerId,
            lastPoint: point,
          };
        } else if (
          hit?.kind === "junction" &&
          (state.annotation.junctions.find((item) => item.id === hit.id)?.source_mode ?? "legacy_marker") !== "explicit"
        ) {
          state.drag = {
            kind: "marker",
            markerKind: hit.kind,
            id: hit.id,
            pointerId: event.pointerId,
          };
        } else if (hit?.kind === "roundabout" || hit?.kind === "control_point") {
          state.drag = {
            kind: "marker",
            markerKind: hit.kind,
            id: hit.id,
            pointerId: event.pointerId,
          };
        } else {
          state.drag = null;
        }
        syncSelectionAfterMutation();
        renderAll();
        return;
      }

      if (!point) {
        return;
      }

      if (state.selectedTool === "centerline") {
        state.draftCenterline.push(point);
        state.selection = null;
        state.selectedStripId = null;
        clearBranchDraft();
        clearCrossDraft();
        renderAll();
        return;
      }

      if (state.selectedTool === "branch") {
        if (state.branchDraft) {
          commitBranchAtPoint(point);
          return;
        }
        if (state.branchHoverSnap) {
          beginBranchFromSnap(state.branchHoverSnap);
          return;
        }
        if (state.annotation.centerlines.length === 0) {
          setStatus(statusEl, "Draw at least one centerline before creating a branch.", "error");
        } else {
          setStatus(statusEl, "Branch Tool starts from an existing road. Hover a road until the snap anchor appears.", "error");
        }
        renderAll();
        return;
      }

      if (state.selectedTool === "cross") {
        if (state.crossDraft) {
          commitCrossAtPoint(point);
          return;
        }
        if (state.crossHoverSnap) {
          beginCrossFromSnap(state.crossHoverSnap);
          return;
        }
        createStandaloneCrossAtPoint(point);
        return;
      }

      if (state.selectedTool === "building_region") {
        state.selection = null;
        state.selectedStripId = null;
        clearFurniturePlacement();
        state.drag = {
          kind: "building_region_draw",
          pointerId: event.pointerId,
          startPoint: point,
          currentPoint: point,
        };
        renderAll();
        return;
      }

      if (state.selectedTool === "tree") {
        placeFurnitureQuick(point, "tree");
        return;
      }
      if (state.selectedTool === "lamp") {
        placeFurnitureQuick(point, "lamp");
        return;
      }

      if (state.selectedTool === "control_point") {
        const id = nextFeatureId(state.annotation, "control_point");
        state.annotation.control_points.push({ id, label: id, x: point.x, y: point.y, kind: "control_point" });
        state.selection = { kind: "control_point", id };
        state.selectedStripId = null;
        clearGraphResult("Annotation changed. Re-run convert to refresh graph output.");
        setStatus(statusEl, `Added control point ${id}.`, "success");
        renderAll();
        return;
      }

      if (state.selectedTool === "roundabout") {
        const id = nextFeatureId(state.annotation, "roundabout");
        state.annotation.roundabouts.push({
          id,
          label: id,
          x: point.x,
          y: point.y,
          radius_px: state.defaultRoundaboutRadiusPx,
        });
        state.selection = { kind: "roundabout", id };
        state.selectedStripId = null;
        clearGraphResult("Annotation changed. Re-run convert to refresh graph output.");
        setStatus(statusEl, `Added roundabout ${id}.`, "success");
        renderAll();
      }
    },
    { signal },
  );

  window.addEventListener(
    "pointermove",
    (event) => {
      if (state.previewResize && state.previewResize.pointerId === event.pointerId) {
        const centerline = state.annotation.centerlines.find((item) => item.id === state.previewResize?.centerlineId);
        const leftStrip = centerline?.cross_section_strips.find((strip) => strip.strip_id === state.previewResize?.leftStripId);
        const rightStrip = centerline?.cross_section_strips.find((strip) => strip.strip_id === state.previewResize?.rightStripId);
        if (!centerline || !leftStrip || !rightStrip) {
          state.previewResize = null;
          return;
        }
        const pairWidthM = state.previewResize.startLeftWidthM + state.previewResize.startRightWidthM;
        const deltaPx = event.clientX - state.previewResize.startClientX;
        const deltaM = deltaPx * (pairWidthM / Math.max(1, state.previewResize.pairWidthPx));
        const clampedDeltaM = clamp(
          deltaM,
          -(state.previewResize.startLeftWidthM - 0.1),
          state.previewResize.startRightWidthM - 0.1,
        );
        leftStrip.width_m = Math.max(0.1, state.previewResize.startLeftWidthM + clampedDeltaM);
        rightStrip.width_m = Math.max(0.1, state.previewResize.startRightWidthM - clampedDeltaM);
        state.previewResize.didResize = true;
        syncCenterlineDerivedFields(centerline);
        clearGraphResult("Annotation changed. Re-run convert to refresh graph output.");
        renderAll();
        return;
      }
      if (state.selectedTool === "branch" && !state.drag) {
        updateBranchPreview(imagePointFromPointer(event));
        renderAll();
        return;
      }
      if (state.selectedTool === "cross" && !state.drag) {
        updateCrossPreview(imagePointFromPointer(event));
        renderAll();
        return;
      }
      if (!state.drag || state.drag.pointerId !== event.pointerId) {
        return;
      }
      const drag = state.drag;
      const point = imagePointFromPointer(event);
      if (!point && drag.kind !== "building_region_draw") {
        return;
      }
      if (drag.kind === "building_region_draw") {
        if (point) {
          drag.currentPoint = point;
          renderAll();
        }
        return;
      }
      if (!point) {
        return;
      }
      if (drag.kind === "centerline_vertex") {
        const centerline = state.annotation.centerlines.find((item) => item.id === drag.id);
        if (!centerline) {
          return;
        }
        if (!centerline.points[drag.vertexIndex]) {
          return;
        }
        centerline.points[drag.vertexIndex] = point;
      } else if (drag.kind === "building_region_translate") {
        const region = state.annotation.building_regions.find((item) => item.id === drag.id);
        if (!region) {
          return;
        }
        const deltaX = point.x - drag.lastPoint.x;
        const deltaY = point.y - drag.lastPoint.y;
        region.center_px = {
          x: region.center_px.x + deltaX,
          y: region.center_px.y + deltaY,
        };
        drag.lastPoint = point;
      } else if (drag.kind === "building_region_resize") {
        const region = state.annotation.building_regions.find((item) => item.id === drag.id);
        if (!region) {
          return;
        }
        const localPoint = buildingRegionLocalPoint(region, point);
        region.width_px = Math.max(BUILDING_REGION_MIN_SIZE_PX, Math.abs(localPoint.x) * 2.0);
        region.height_px = Math.max(BUILDING_REGION_MIN_SIZE_PX, Math.abs(localPoint.y) * 2.0);
      } else if (drag.kind === "building_region_rotate") {
        const region = state.annotation.building_regions.find((item) => item.id === drag.id);
        if (!region) {
          return;
        }
        const yawRad = Math.atan2(region.center_px.x - point.x, region.center_px.y - point.y);
        region.yaw_deg = normalizeAngleDeg((yawRad * 180) / Math.PI);
      } else if (drag.kind === "centerline_translate") {
        const centerline = state.annotation.centerlines.find((item) => item.id === drag.id);
        if (!centerline) {
          return;
        }
        const deltaX = point.x - drag.lastPoint.x;
        const deltaY = point.y - drag.lastPoint.y;
        centerline.points = centerline.points.map((vertex) => ({
          x: vertex.x + deltaX,
          y: vertex.y + deltaY,
        }));
        drag.lastPoint = point;
      } else {
        if (drag.markerKind === "junction") {
          const marker = state.annotation.junctions.find((item) => item.id === drag.id);
          if (marker) {
            marker.x = point.x;
            marker.y = point.y;
          }
        } else if (drag.markerKind === "roundabout") {
          const marker = state.annotation.roundabouts.find((item) => item.id === drag.id);
          if (marker) {
            marker.x = point.x;
            marker.y = point.y;
          }
        } else {
          const marker = state.annotation.control_points.find((item) => item.id === drag.id);
          if (marker) {
            marker.x = point.x;
            marker.y = point.y;
          }
        }
      }
      syncSelectionAfterMutation();
      clearGraphResult("Annotation changed. Re-run convert to refresh graph output.");
      renderAll();
    },
    { signal },
  );

  window.addEventListener(
    "pointerup",
    (event) => {
      if (state.previewResize && state.previewResize.pointerId === event.pointerId) {
        const resized = state.previewResize.didResize;
        state.previewResize = null;
        if (resized) {
          setStatus(statusEl, "Updated cross-section boundary widths.", "success");
        }
        renderAll();
        return;
      }
      if (state.drag?.kind === "building_region_draw" && state.drag.pointerId === event.pointerId) {
        const { startPoint, currentPoint } = state.drag;
        const deltaX = Math.abs(currentPoint.x - startPoint.x);
        const deltaY = Math.abs(currentPoint.y - startPoint.y);
        if (Math.max(deltaX, deltaY) >= 6) {
          const id = nextFeatureId(state.annotation, "building_region");
          const region = buildBuildingRegionFromDraft(id, startPoint, currentPoint);
          state.annotation.building_regions.push(region);
          state.selection = { kind: "building_region", id };
          state.selectedStripId = null;
          clearFurniturePlacement();
          state.drag = null;
          markAnnotationChanged(`Added building region ${id}.`);
          renderAll();
          return;
        }
        state.drag = null;
        setStatus(statusEl, "Building region drag was too small. Drag to define an area.", "neutral");
        renderAll();
        return;
      }
      if (state.drag && state.drag.pointerId === event.pointerId) {
        if (state.drag.kind === "building_region_translate") {
          setStatus(statusEl, "Moved building region.", "success");
        } else if (state.drag.kind === "building_region_resize") {
          setStatus(statusEl, "Resized building region.", "success");
        } else if (state.drag.kind === "building_region_rotate") {
          setStatus(statusEl, "Updated building region orientation.", "success");
        }
        state.drag = null;
        syncSelectionAfterMutation();
        renderAll();
      }
    },
    { signal },
  );

  jsonFileInput.addEventListener(
    "change",
    async () => {
      const file = jsonFileInput.files?.[0];
      if (!file) {
        return;
      }
      try {
        const text = await file.text();
        const annotation = normalizeAnnotation(JSON.parse(text));
        state.annotation = annotation;
        state.selection = null;
        state.selectedStripId = null;
        state.draftCenterline = [];
        clearBranchDraft();
        clearCrossDraft();
        clearFurniturePlacement();
        if (annotation.image_path) {
          try {
            await loadImageFromUrl(annotation.image_path, { planId: annotation.plan_id, preserveFeatures: true });
          } catch {
            state.currentImageUrl = "";
            clearGraphResult("Annotation JSON imported, but image path could not be loaded.");
            setStatus(statusEl, "Imported annotation JSON. Image path could not be reopened in browser.", "neutral");
            renderAll();
          }
        } else {
          clearGraphResult("Annotation JSON imported. Load an image to keep editing against the reference.");
          setStatus(statusEl, `Imported annotation JSON from ${file.name}.`, "success");
          renderAll();
        }
      } catch (error) {
        setStatus(statusEl, error instanceof Error ? error.message : "Failed to import annotation JSON.", "error");
      } finally {
        jsonFileInput.value = "";
      }
    },
    { signal },
  );

  applyJsonButton.addEventListener(
    "click",
    async () => {
      try {
        const annotation = normalizeAnnotation(JSON.parse(jsonTextarea.value));
        state.annotation = annotation;
        state.selection = null;
        state.selectedStripId = null;
        state.draftCenterline = [];
        clearBranchDraft();
        clearCrossDraft();
        clearFurniturePlacement();
        clearGraphResult("Annotation JSON applied. Re-run convert to refresh graph output.");
        if (annotation.image_path) {
          try {
            await loadImageFromUrl(annotation.image_path, { planId: annotation.plan_id, preserveFeatures: true });
          } catch {
            setStatus(statusEl, "Applied annotation JSON, but the image path could not be loaded.", "neutral");
            renderAll();
          }
        } else {
          setStatus(statusEl, "Applied annotation JSON.", "success");
          renderAll();
        }
      } catch (error) {
        setStatus(statusEl, error instanceof Error ? error.message : "Failed to apply annotation JSON.", "error");
      }
    },
    { signal },
  );

  downloadJsonButton.addEventListener(
    "click",
    () => {
      downloadText(`${state.annotation.plan_id || "reference_annotation"}.json`, stringifyAnnotation(state.annotation));
    },
    { signal },
  );

  copyJsonButton.addEventListener(
    "click",
    async () => {
      try {
        await navigator.clipboard.writeText(stringifyAnnotation(state.annotation));
        setStatus(statusEl, "Annotation JSON copied to clipboard.", "success");
      } catch {
        jsonTextarea.select();
        document.execCommand("copy");
        setStatus(statusEl, "Annotation JSON selected. Press Ctrl/Cmd+C to copy.", "neutral");
      }
    },
    { signal },
  );

  convertGraphButton.addEventListener(
    "click",
    async () => {
      try {
        await convertAnnotationToGraph();
      } catch (error) {
        setStatus(graphStatusEl, error instanceof Error ? error.message : "Failed to convert annotation.", "error");
      }
    },
    { signal },
  );

  downloadGraphButton.addEventListener(
    "click",
    () => {
      if (!state.graphResult) {
        return;
      }
      const annotationSnapshot = cloneAnnotation(state.annotation);
      const exportPayload: ConvertedGraphPayload = {
        ...state.graphResult,
        annotation: annotationSnapshot,
        summary: {
          ...state.graphResult.summary,
          building_region_count: annotationSnapshot.building_regions.length,
        },
      };
      downloadText(`${state.annotation.plan_id || "reference_annotation"}_graph.json`, JSON.stringify(exportPayload, null, 2));
    },
    { signal },
  );

  renderReferencePlanOptions(FALLBACK_REFERENCE_PLAN.plan_id);
  renderAll();
  void applyReferencePlan(FALLBACK_REFERENCE_PLAN.plan_id).catch((error) => {
    state.isReferenceImageLoading = false;
    renderAll();
    setStatus(
      statusEl,
      error instanceof Error ? error.message : `Failed to load default reference plan ${FALLBACK_REFERENCE_PLAN.plan_id}.`,
      "error",
    );
  });
  void loadReferencePlans({ silent: true }).catch((error) => {
    setStatus(statusEl, error instanceof Error ? error.message : "Failed to refresh reference plans.", "error");
  });

  return () => {
    revokeCurrentObjectUrl();
    eventController.abort();
  };
}
