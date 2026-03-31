import type {
  AnnotationPoint,
  AnnotatedCenterline,
  AnnotatedCrossSectionStrip,
  AnnotatedJunction,
  AnnotatedStreetFurnitureInstance,
  AnnotationModelIssue,
  BranchSnapTarget,
  CrossSectionMode,
  DerivedJunctionOverlay,
  FurnitureKind,
  JunctionOverlayStripLink,
  JunctionOverlayStripLinkEndpoint,
  LaneProfile,
  ReferenceAnnotation,
  StripDirection,
  StripKind,
  StripZone,
  SelectedStripCornerConnection,
  SelectedStripCornerFamilyTarget,
  OffsetPolylineSegment,
  Selection,
} from "./sg-types";
import {
  ANNOTATION_MODEL_TOLERANCE_PX,
  BRANCH_SNAP_TOLERANCE_PX,
  BRANCH_VERTEX_REUSE_TOLERANCE_PX,
  CROSS_SECTION_MODE_COARSE,
  CROSS_SECTION_MODE_DETAILED,
  DEFAULT_FORWARD_DRIVE_LANE_COUNT,
  DEFAULT_REVERSE_DRIVE_LANE_COUNT,
  FURNITURE_COMPATIBLE_STRIP_KINDS,
  FURNITURE_KINDS,
  NOMINAL_STRIP_WIDTHS,
  STRIP_DIRECTION_OPTIONS,
  STRIP_KINDS,
} from "./sg-constants";

export function asNullableNumber(value: unknown): number | null {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value === "string" && !value.trim()) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function asNonNegativeInt(value: unknown, fallback: number): number {
  return Math.max(0, Math.round(asNumber(value, fallback)));
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function asNumber(value: unknown, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}

export function isStripZone(value: string): value is StripZone {
  return value === "left" || value === "center" || value === "right";
}

export function isStripKind(value: string): value is StripKind {
  return STRIP_KINDS.includes(value as StripKind);
}

export function isStripDirection(value: string): value is StripDirection {
  return STRIP_DIRECTION_OPTIONS.includes(value as StripDirection);
}

export function isFurnitureKind(value: string): value is FurnitureKind {
  return FURNITURE_KINDS.includes(value as FurnitureKind);
}

export function resolveDriveLaneDefaults(record: Record<string, unknown>): {
  forward_drive_lane_count: number;
  reverse_drive_lane_count: number;
} {
  const legacyLaneCount = Math.max(
    1,
    Math.round(asNumber(record.lane_count, DEFAULT_FORWARD_DRIVE_LANE_COUNT + DEFAULT_REVERSE_DRIVE_LANE_COUNT)),
  );
  const defaultForward = Math.max(1, Math.ceil(legacyLaneCount / 2));
  const defaultReverse = Math.max(0, legacyLaneCount - defaultForward);
  const forwardDriveLaneCount = asNonNegativeInt(record.forward_drive_lane_count, defaultForward);
  const reverseDriveLaneCount = asNonNegativeInt(record.reverse_drive_lane_count, defaultReverse);
  if (forwardDriveLaneCount <= 0 && reverseDriveLaneCount <= 0) {
    return {
      forward_drive_lane_count: DEFAULT_FORWARD_DRIVE_LANE_COUNT,
      reverse_drive_lane_count: DEFAULT_REVERSE_DRIVE_LANE_COUNT,
    };
  }
  return {
    forward_drive_lane_count: forwardDriveLaneCount,
    reverse_drive_lane_count: reverseDriveLaneCount,
  };
}

export function laneProfile(centerline: AnnotatedCenterline): LaneProfile {
  const forward = Math.max(0, centerline.forward_drive_lane_count);
  const reverse = Math.max(0, centerline.reverse_drive_lane_count);
  const bike = Math.max(0, centerline.bike_lane_count);
  const bus = Math.max(0, centerline.bus_lane_count);
  const parking = Math.max(0, centerline.parking_lane_count);
  return {
    forward_drive_lane_count: forward,
    reverse_drive_lane_count: reverse,
    bike_lane_count: bike,
    bus_lane_count: bus,
    parking_lane_count: parking,
    bidirectional_drive_lane_count: 0,
    bidirectional_lane_count: 0,
    total_drive_lane_count: forward + reverse,
    total_lane_count: forward + reverse + bike + bus + parking,
  };
}

export function resolvedCrossSectionMode(centerline: AnnotatedCenterline): CrossSectionMode {
  if (centerline.cross_section_strips.length > 0) {
    return CROSS_SECTION_MODE_DETAILED;
  }
  return centerline.cross_section_mode === CROSS_SECTION_MODE_DETAILED
    ? CROSS_SECTION_MODE_DETAILED
    : CROSS_SECTION_MODE_COARSE;
}

export function sortedCrossSectionStrips(strips: AnnotatedCrossSectionStrip[]): AnnotatedCrossSectionStrip[] {
  const zoneRank: Record<StripZone, number> = { left: 0, center: 1, right: 2 };
  return [...strips].sort((a, b) => {
    const zoneDelta = zoneRank[a.zone] - zoneRank[b.zone];
    if (zoneDelta !== 0) {
      return zoneDelta;
    }
    if (a.order_index !== b.order_index) {
      return a.order_index - b.order_index;
    }
    return a.strip_id.localeCompare(b.strip_id);
  });
}

export function getCenterlineCrossSectionWidth(centerline: AnnotatedCenterline): number {
  if (resolvedCrossSectionMode(centerline) === CROSS_SECTION_MODE_DETAILED && centerline.cross_section_strips.length > 0) {
    return centerline.cross_section_strips.reduce((sum, strip) => sum + Math.max(0, strip.width_m), 0);
  }
  return Math.max(1, centerline.road_width_m);
}

export function getCenterlineCarriagewayWidth(centerline: AnnotatedCenterline): number {
  if (resolvedCrossSectionMode(centerline) === CROSS_SECTION_MODE_DETAILED && centerline.cross_section_strips.length > 0) {
    const width = centerline.cross_section_strips.reduce((sum, strip) => {
      if (strip.zone !== "center") {
        return sum;
      }
      return sum + Math.max(0, strip.width_m);
    }, 0);
    if (width > 0) {
      return width;
    }
  }
  return Math.max(1, centerline.road_width_m);
}

export function deriveLaneProfileFromStrips(strips: AnnotatedCrossSectionStrip[]): LaneProfile {
  let forwardDriveLaneCount = 0;
  let reverseDriveLaneCount = 0;
  let bikeLaneCount = 0;
  let busLaneCount = 0;
  let parkingLaneCount = 0;
  let bidirectionalDriveLaneCount = 0;
  let bidirectionalLaneCount = 0;

  for (const strip of strips) {
    if (strip.zone !== "center") {
      continue;
    }
    if (strip.kind === "drive_lane") {
      if (strip.direction === "forward") {
        forwardDriveLaneCount += 1;
      } else if (strip.direction === "reverse") {
        reverseDriveLaneCount += 1;
      } else if (strip.direction === "bidirectional") {
        bidirectionalDriveLaneCount += 1;
        bidirectionalLaneCount += 1;
      }
    } else if (strip.kind === "bike_lane") {
      bikeLaneCount += 1;
      if (strip.direction === "bidirectional") {
        bidirectionalLaneCount += 1;
      }
    } else if (strip.kind === "bus_lane") {
      busLaneCount += 1;
      if (strip.direction === "bidirectional") {
        bidirectionalLaneCount += 1;
      }
    } else if (strip.kind === "parking_lane") {
      parkingLaneCount += 1;
    }
  }

  return {
    forward_drive_lane_count: forwardDriveLaneCount,
    reverse_drive_lane_count: reverseDriveLaneCount,
    bike_lane_count: bikeLaneCount,
    bus_lane_count: busLaneCount,
    parking_lane_count: parkingLaneCount,
    bidirectional_drive_lane_count: bidirectionalDriveLaneCount,
    bidirectional_lane_count: bidirectionalLaneCount,
    total_drive_lane_count: forwardDriveLaneCount + reverseDriveLaneCount + bidirectionalDriveLaneCount,
    total_lane_count:
      forwardDriveLaneCount +
      reverseDriveLaneCount +
      bikeLaneCount +
      busLaneCount +
      parkingLaneCount +
      bidirectionalDriveLaneCount,
  };
}

export function deriveLaneProfile(centerline: AnnotatedCenterline): LaneProfile {
  if (resolvedCrossSectionMode(centerline) === CROSS_SECTION_MODE_DETAILED && centerline.cross_section_strips.length > 0) {
    return deriveLaneProfileFromStrips(centerline.cross_section_strips);
  }
  return laneProfile(centerline);
}

export function reindexCenterlineStrips(centerline: AnnotatedCenterline): void {
  const nextStrips: AnnotatedCrossSectionStrip[] = [];
  for (const zone of ["left", "center", "right"] as StripZone[]) {
    const zoneStrips = sortedCrossSectionStrips(centerline.cross_section_strips).filter((strip) => strip.zone === zone);
    zoneStrips.forEach((strip, index) => {
      nextStrips.push({ ...strip, order_index: index });
    });
  }
  centerline.cross_section_strips = nextStrips;
}

export function nextStripId(centerline: AnnotatedCenterline, zone: StripZone): string {
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

export function splitAuxiliaryCountAcrossDirections(
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

export function nominalSeedCrossSectionWidthForCounts(
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

export function seedDetailedCrossSection(centerline: AnnotatedCenterline): AnnotatedCrossSectionStrip[] {
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

export function ensureDetailedCrossSection(centerline: AnnotatedCenterline): boolean {
  if (resolvedCrossSectionMode(centerline) === CROSS_SECTION_MODE_DETAILED && centerline.cross_section_strips.length > 0) {
    syncCenterlineDerivedFields(centerline);
    return false;
  }
  centerline.cross_section_strips = seedDetailedCrossSection(centerline);
  centerline.street_furniture_instances = [];
  syncCenterlineDerivedFields(centerline);
  return true;
}

export function syncCenterlineDerivedFields(centerline: AnnotatedCenterline): void {
  reindexCenterlineStrips(centerline);
  const mode = centerline.cross_section_strips.length > 0 ? CROSS_SECTION_MODE_DETAILED : CROSS_SECTION_MODE_COARSE;
  centerline.cross_section_mode = mode;
  const profile = deriveLaneProfile(centerline);
  centerline.forward_drive_lane_count = profile.forward_drive_lane_count;
  centerline.reverse_drive_lane_count = profile.reverse_drive_lane_count;
  centerline.bike_lane_count = profile.bike_lane_count;
  centerline.bus_lane_count = profile.bus_lane_count;
  centerline.parking_lane_count = profile.parking_lane_count;
  centerline.road_width_m = getCenterlineCrossSectionWidth(centerline);
  const validStripIds = new Set(centerline.cross_section_strips.map((strip) => strip.strip_id));
  const validFurnitureStripIds = new Set(
    centerline.cross_section_strips
      .filter((strip) => FURNITURE_COMPATIBLE_STRIP_KINDS.has(strip.kind))
      .map((strip) => strip.strip_id),
  );
  centerline.street_furniture_instances = centerline.street_furniture_instances
    .filter((instance) => validStripIds.has(instance.strip_id) && validFurnitureStripIds.has(instance.strip_id))
    .map((instance) => ({ ...instance, centerline_id: centerline.id }));
}

export function formatLaneSummary(centerline: AnnotatedCenterline): string {
  const profile = deriveLaneProfile(centerline);
  const parts = [`drive ${profile.forward_drive_lane_count}/${profile.reverse_drive_lane_count}`];
  if (profile.bidirectional_drive_lane_count > 0) {
    parts.push(`bi-drive ${profile.bidirectional_drive_lane_count}`);
  }
  if (profile.bike_lane_count > 0) {
    parts.push(`bike ${profile.bike_lane_count}`);
  }
  if (profile.bus_lane_count > 0) {
    parts.push(`bus ${profile.bus_lane_count}`);
  }
  if (profile.parking_lane_count > 0) {
    parts.push(`park ${profile.parking_lane_count}`);
  }
  return parts.join(" · ");
}

export function formatCrossSectionSummary(centerline: AnnotatedCenterline): string {
  if (resolvedCrossSectionMode(centerline) !== CROSS_SECTION_MODE_DETAILED || centerline.cross_section_strips.length === 0) {
    return "coarse";
  }
  const left = centerline.cross_section_strips.filter((strip) => strip.zone === "left").length;
  const center = centerline.cross_section_strips.filter((strip) => strip.zone === "center").length;
  const right = centerline.cross_section_strips.filter((strip) => strip.zone === "right").length;
  return `L${left} · C${center} · R${right}`;
}

export function stripKey(centerlineId: string, stripId: string): string {
  return `${centerlineId}:${stripId}`;
}

export function stripLinkEndpointMatches(
  endpoint: JunctionOverlayStripLinkEndpoint,
  centerlineId: string,
  stripId: string,
): boolean {
  return endpoint.centerlineId === centerlineId && endpoint.stripId === stripId;
}

export function selectedStripCornerConnections(
  junctionOverlays: DerivedJunctionOverlay[],
  centerlineId: string,
  stripId: string,
): SelectedStripCornerConnection[] {
  const connections: SelectedStripCornerConnection[] = [];
  for (const overlay of junctionOverlays) {
    for (const link of overlay.cornerStripLinks) {
      if (stripLinkEndpointMatches(link.start, centerlineId, stripId)) {
        connections.push({
          linkId: link.linkId,
          junctionId: link.junctionId,
          quadrantId: link.quadrantId,
          kernelId: link.kernelId,
          stripKind: link.stripKind,
          current: link.start,
          peer: link.end,
          points: link.points.map((point) => clonePoint(point)),
        });
        continue;
      }
      if (stripLinkEndpointMatches(link.end, centerlineId, stripId)) {
        connections.push({
          linkId: link.linkId,
          junctionId: link.junctionId,
          quadrantId: link.quadrantId,
          kernelId: link.kernelId,
          stripKind: link.stripKind,
          current: link.end,
          peer: link.start,
          points: [...link.points].reverse().map((point) => clonePoint(point)),
        });
      }
    }
  }
  return connections;
}

export function cornerFamilyIdentity(link: JunctionOverlayStripLink): string | null {
  if (!link.kernelId) {
    return null;
  }
  return `${link.junctionId}::${link.quadrantId}::${link.kernelId}`;
}

export function selectedStripCornerFamilyTargets(
  junctionOverlays: DerivedJunctionOverlay[],
  centerlineId: string,
  stripId: string,
): SelectedStripCornerFamilyTarget[] {
  const familyIds = new Set<string>();
  for (const overlay of junctionOverlays) {
    if (overlay.kind !== "cross_junction") {
      continue;
    }
    for (const link of overlay.cornerStripLinks) {
      if (
        stripLinkEndpointMatches(link.start, centerlineId, stripId) ||
        stripLinkEndpointMatches(link.end, centerlineId, stripId)
      ) {
        const familyId = cornerFamilyIdentity(link);
        if (familyId) {
          familyIds.add(familyId);
        }
      }
    }
  }
  if (familyIds.size === 0) {
    return selectedStripCornerConnections(junctionOverlays, centerlineId, stripId).map((connection) => ({
      targetId: `${connection.linkId}:${connection.peer.centerlineId}:${connection.peer.stripId}`,
      junctionId: connection.junctionId,
      quadrantId: connection.quadrantId,
      kernelId: connection.kernelId,
      stripKind: connection.stripKind,
      target: connection.peer,
      points: connection.points.map((point) => clonePoint(point)),
    }));
  }
  const targets: SelectedStripCornerFamilyTarget[] = [];
  const seen = new Set<string>();
  for (const overlay of junctionOverlays) {
    if (overlay.kind !== "cross_junction") {
      continue;
    }
    for (const link of overlay.cornerStripLinks) {
      const familyId = cornerFamilyIdentity(link);
      if (!familyId || !familyIds.has(familyId)) {
        continue;
      }
      for (const [endpoint, points] of [
        [link.start, link.points],
        [link.end, [...link.points].reverse()],
      ] as const) {
        if (stripLinkEndpointMatches(endpoint, centerlineId, stripId)) {
          continue;
        }
        const targetId = `${familyId}:${endpoint.centerlineId}:${endpoint.stripId}`;
        if (seen.has(targetId)) {
          continue;
        }
        seen.add(targetId);
        targets.push({
          targetId,
          junctionId: link.junctionId,
          quadrantId: link.quadrantId,
          kernelId: link.kernelId,
          stripKind: link.stripKind,
          target: endpoint,
          points: points.map((point) => clonePoint(point)),
        });
      }
    }
  }
  return targets;
}

export function stripCenterOffsetMeters(centerline: AnnotatedCenterline): Record<string, { centerOffsetM: number; widthM: number }> {
  const strips = sortedCrossSectionStrips(centerline.cross_section_strips);
  const left = strips.filter((strip) => strip.zone === "left");
  const center = strips.filter((strip) => strip.zone === "center");
  const right = strips.filter((strip) => strip.zone === "right");
  const carriagewayWidthM = center.reduce((sum, strip) => sum + strip.width_m, 0);
  const result: Record<string, { centerOffsetM: number; widthM: number }> = {};

  let leftAccum = 0;
  for (const strip of left) {
    const centerOffsetM = -(carriagewayWidthM * 0.5 + leftAccum + strip.width_m * 0.5);
    result[strip.strip_id] = { centerOffsetM, widthM: strip.width_m };
    leftAccum += strip.width_m;
  }

  let centerAccum = -carriagewayWidthM * 0.5;
  for (const strip of center) {
    const centerOffsetM = centerAccum + strip.width_m * 0.5;
    result[strip.strip_id] = { centerOffsetM, widthM: strip.width_m };
    centerAccum += strip.width_m;
  }

  let rightAccum = 0;
  for (const strip of right) {
    const centerOffsetM = carriagewayWidthM * 0.5 + rightAccum + strip.width_m * 0.5;
    result[strip.strip_id] = { centerOffsetM, widthM: strip.width_m };
    rightAccum += strip.width_m;
  }

  return result;
}

export function polylineLength(points: AnnotationPoint[]): number {
  let total = 0;
  for (let index = 0; index < points.length - 1; index += 1) {
    total += Math.hypot(points[index + 1].x - points[index].x, points[index + 1].y - points[index].y);
  }
  return total;
}

export function offsetPointAlongNormal(
  point: AnnotationPoint,
  normal: AnnotationPoint,
  offsetPx: number,
): AnnotationPoint {
  return {
    x: point.x + normal.x * offsetPx,
    y: point.y + normal.y * offsetPx,
  };
}

export function normalizeVector(point: AnnotationPoint): AnnotationPoint | null {
  const length = Math.hypot(point.x, point.y);
  if (length <= 1e-6) {
    return null;
  }
  return {
    x: point.x / length,
    y: point.y / length,
  };
}

export function limitedMiterJoinPoint(
  point: AnnotationPoint,
  prevNormal: AnnotationPoint,
  nextNormal: AnnotationPoint,
  offsetPx: number,
  maxJoinDistance: number,
): AnnotationPoint | null {
  const bisector = normalizeVector({
    x: prevNormal.x + nextNormal.x,
    y: prevNormal.y + nextNormal.y,
  });
  if (!bisector) {
    return null;
  }
  const projection = bisector.x * prevNormal.x + bisector.y * prevNormal.y;
  if (Math.abs(projection) <= 1e-6) {
    return null;
  }
  const rawMiterLength = offsetPx / projection;
  if (!Number.isFinite(rawMiterLength)) {
    return null;
  }
  const clampedMiterLength = clamp(rawMiterLength, -maxJoinDistance, maxJoinDistance);
  return {
    x: point.x + bisector.x * clampedMiterLength,
    y: point.y + bisector.y * clampedMiterLength,
  };
}

export function buildOffsetPolylineSegments(points: AnnotationPoint[], offsetPx: number): OffsetPolylineSegment[] {
  const segments: OffsetPolylineSegment[] = [];
  for (let index = 0; index < points.length - 1; index += 1) {
    const start = points[index];
    const end = points[index + 1];
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const length = Math.hypot(dx, dy);
    if (length <= 1e-6) {
      continue;
    }
    const tangent = {
      x: dx / length,
      y: dy / length,
    };
    const normal = {
      x: dy / length,
      y: -dx / length,
    };
    segments.push({
      startIndex: index,
      endIndex: index + 1,
      tangent,
      normal,
      offsetStart: offsetPointAlongNormal(start, normal, offsetPx),
      offsetEnd: offsetPointAlongNormal(end, normal, offsetPx),
    });
  }
  return segments;
}

export function offsetPolyline(points: AnnotationPoint[], offsetPx: number): AnnotationPoint[] {
  if (Math.abs(offsetPx) <= 1e-6 || points.length < 2) {
    return points.map((point) => ({ ...point }));
  }
  const segments = buildOffsetPolylineSegments(points, offsetPx);
  if (!segments.length) {
    return points.map((point) => ({ ...point }));
  }

  const previousSegmentIndices = new Array<number>(points.length).fill(-1);
  const nextSegmentIndices = new Array<number>(points.length).fill(-1);

  let previousCursor = 0;
  let previousSegmentIndex = -1;
  for (let pointIndex = 0; pointIndex < points.length; pointIndex += 1) {
    while (previousCursor < segments.length && segments[previousCursor].endIndex <= pointIndex) {
      previousSegmentIndex = previousCursor;
      previousCursor += 1;
    }
    previousSegmentIndices[pointIndex] = previousSegmentIndex;
  }

  let nextCursor = 0;
  for (let pointIndex = 0; pointIndex < points.length; pointIndex += 1) {
    while (nextCursor < segments.length && segments[nextCursor].startIndex < pointIndex) {
      nextCursor += 1;
    }
    nextSegmentIndices[pointIndex] = nextCursor < segments.length ? nextCursor : -1;
  }

  const firstSegment = segments[0];
  const lastSegment = segments[segments.length - 1];
  const maxJoinDistance = Math.abs(offsetPx) * 4;

  return points.map((point, index) => {
    if (index === 0) {
      return clonePoint(firstSegment.offsetStart);
    }
    if (index === points.length - 1) {
      return clonePoint(lastSegment.offsetEnd);
    }

    const previousIndex = previousSegmentIndices[index];
    const nextIndex = nextSegmentIndices[index];
    if (previousIndex < 0 && nextIndex < 0) {
      return clonePoint(point);
    }
    if (previousIndex < 0) {
      return offsetPointAlongNormal(point, segments[nextIndex].normal, offsetPx);
    }
    if (nextIndex < 0) {
      return offsetPointAlongNormal(point, segments[previousIndex].normal, offsetPx);
    }

    const previousSegment = segments[previousIndex];
    const nextSegment = segments[nextIndex];
    if (previousIndex === nextIndex) {
      return offsetPointAlongNormal(point, previousSegment.normal, offsetPx);
    }

    const previousOffsetPoint = offsetPointAlongNormal(point, previousSegment.normal, offsetPx);
    const nextOffsetPoint = offsetPointAlongNormal(point, nextSegment.normal, offsetPx);
    const joinPoint = lineIntersectionTs(
      previousOffsetPoint,
      previousSegment.tangent,
      nextOffsetPoint,
      nextSegment.tangent,
    );
    if (joinPoint && pointDistance(joinPoint, point) <= maxJoinDistance + 1e-6) {
      return joinPoint;
    }

    return (
      limitedMiterJoinPoint(
        point,
        previousSegment.normal,
        nextSegment.normal,
        offsetPx,
        maxJoinDistance,
      ) ?? previousOffsetPoint
    );
  });
}

export function stationToPolylinePoint(points: AnnotationPoint[], stationPx: number): {
  point: AnnotationPoint;
  tangent: AnnotationPoint;
  leftNormal: AnnotationPoint;
} {
  if (points.length < 2) {
    return {
      point: points[0] ?? { x: 0, y: 0 },
      tangent: { x: 1, y: 0 },
      leftNormal: { x: 0, y: -1 },
    };
  }
  let remaining = clamp(stationPx, 0, polylineLength(points));
  for (let index = 0; index < points.length - 1; index += 1) {
    const a = points[index];
    const b = points[index + 1];
    const segmentLength = Math.hypot(b.x - a.x, b.y - a.y);
    if (segmentLength <= 1e-6) {
      continue;
    }
    if (remaining <= segmentLength || index === points.length - 2) {
      const ratio = clamp(remaining / segmentLength, 0, 1);
      const point = {
        x: a.x + (b.x - a.x) * ratio,
        y: a.y + (b.y - a.y) * ratio,
      };
      const tangent = {
        x: (b.x - a.x) / segmentLength,
        y: (b.y - a.y) / segmentLength,
      };
      return {
        point,
        tangent,
        leftNormal: { x: tangent.y, y: -tangent.x },
      };
    }
    remaining -= segmentLength;
  }
  const last = points[points.length - 1];
  const prev = points[points.length - 2];
  const segmentLength = Math.max(Math.hypot(last.x - prev.x, last.y - prev.y), 1e-6);
  const tangent = {
    x: (last.x - prev.x) / segmentLength,
    y: (last.y - prev.y) / segmentLength,
  };
  return {
    point: { ...last },
    tangent,
    leftNormal: { x: tangent.y, y: -tangent.x },
  };
}

export function projectPointOntoPolyline(points: AnnotationPoint[], point: AnnotationPoint): {
  stationPx: number;
  lateralPx: number;
  projectedPoint: AnnotationPoint;
  segmentIndex: number;
  distancePx: number;
} {
  if (points.length < 2) {
    return {
      stationPx: 0,
      lateralPx: 0,
      projectedPoint: points[0] ?? point,
      segmentIndex: 0,
      distancePx: 0,
    };
  }
  let bestDistance = Number.POSITIVE_INFINITY;
  let bestStationPx = 0;
  let bestLateralPx = 0;
  let bestPoint = points[0];
  let bestSegmentIndex = 0;
  let accumulated = 0;
  for (let index = 0; index < points.length - 1; index += 1) {
    const a = points[index];
    const b = points[index + 1];
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const lengthSq = dx * dx + dy * dy;
    if (lengthSq <= 1e-6) {
      continue;
    }
    const ratio = clamp(((point.x - a.x) * dx + (point.y - a.y) * dy) / lengthSq, 0, 1);
    const projectedPoint = { x: a.x + dx * ratio, y: a.y + dy * ratio };
    const distance = Math.hypot(point.x - projectedPoint.x, point.y - projectedPoint.y);
    if (distance < bestDistance) {
      const length = Math.sqrt(lengthSq);
      const tangent = { x: dx / length, y: dy / length };
      const leftNormal = { x: tangent.y, y: -tangent.x };
      bestDistance = distance;
      bestStationPx = accumulated + ratio * length;
      bestLateralPx =
        (point.x - projectedPoint.x) * leftNormal.x +
        (point.y - projectedPoint.y) * leftNormal.y;
      bestPoint = projectedPoint;
      bestSegmentIndex = index;
    }
    accumulated += Math.sqrt(lengthSq);
  }
  return {
    stationPx: bestStationPx,
    lateralPx: bestLateralPx,
    projectedPoint: bestPoint,
    segmentIndex: bestSegmentIndex,
    distancePx: bestDistance,
  };
}

export function pointDistance(a: AnnotationPoint, b: AnnotationPoint): number {
  return Math.hypot(a.x - b.x, a.y - b.y);
}

export function pointOnSegmentDistance(point: AnnotationPoint, start: AnnotationPoint, end: AnnotationPoint): number {
  const dx = end.x - start.x;
  const dy = end.y - start.y;
  const lengthSq = dx * dx + dy * dy;
  if (lengthSq <= 1e-6) {
    return pointDistance(point, start);
  }
  const ratio = clamp(((point.x - start.x) * dx + (point.y - start.y) * dy) / lengthSq, 0, 1);
  const projectedPoint = {
    x: start.x + dx * ratio,
    y: start.y + dy * ratio,
  };
  return pointDistance(point, projectedPoint);
}

export function segmentIntersectionDetails(
  startA: AnnotationPoint,
  endA: AnnotationPoint,
  startB: AnnotationPoint,
  endB: AnnotationPoint,
  tolerancePx = ANNOTATION_MODEL_TOLERANCE_PX,
): { point: AnnotationPoint } | null {
  const directionA = { x: endA.x - startA.x, y: endA.y - startA.y };
  const directionB = { x: endB.x - startB.x, y: endB.y - startB.y };
  const determinant = directionA.x * directionB.y - directionA.y * directionB.x;
  if (Math.abs(determinant) > 1e-6) {
    const deltaX = startB.x - startA.x;
    const deltaY = startB.y - startA.y;
    const tValue = (deltaX * directionB.y - deltaY * directionB.x) / determinant;
    const uValue = (deltaX * directionA.y - deltaY * directionA.x) / determinant;
    if (tValue >= -1e-6 && tValue <= 1 + 1e-6 && uValue >= -1e-6 && uValue <= 1 + 1e-6) {
      return {
        point: {
          x: startA.x + directionA.x * tValue,
          y: startA.y + directionA.y * tValue,
        },
      };
    }
    return null;
  }
  const candidates = [startA, endA, startB, endB];
  for (const candidate of candidates) {
    if (
      pointOnSegmentDistance(candidate, startA, endA) <= tolerancePx
      && pointOnSegmentDistance(candidate, startB, endB) <= tolerancePx
    ) {
      return { point: { ...candidate } };
    }
  }
  return null;
}

export function explicitJunctionEndpointTolerancePx(annotation: ReferenceAnnotation): number {
  return Math.max(BRANCH_VERTEX_REUSE_TOLERANCE_PX, annotation.pixels_per_meter * 0.35);
}

export function explicitJunctionEndpointSnapTolerancePx(annotation: ReferenceAnnotation): number {
  return Math.max(BRANCH_SNAP_TOLERANCE_PX, annotation.pixels_per_meter * 0.5);
}

export function explicitJunctionNearPoint(
  annotation: ReferenceAnnotation,
  point: AnnotationPoint,
  tolerancePx = explicitJunctionEndpointTolerancePx(annotation),
): AnnotatedJunction | null {
  return annotation.junctions.find(
    (junction) =>
      junction.source_mode === "explicit"
      && pointDistance(junctionAnchorPoint(junction), point) <= tolerancePx,
  ) ?? null;
}

export function endpointJunctionIdNearPoint(
  annotation: ReferenceAnnotation,
  centerline: AnnotatedCenterline,
  point: AnnotationPoint,
  tolerancePx = explicitJunctionEndpointTolerancePx(annotation),
): string | null {
  if (centerline.points.length === 0) {
    return null;
  }
  if (pointDistance(centerline.points[0], point) <= tolerancePx) {
    return centerline.start_junction_id || null;
  }
  if (pointDistance(centerline.points[centerline.points.length - 1], point) <= tolerancePx) {
    return centerline.end_junction_id || null;
  }
  return null;
}

export function registerCenterlineWithExplicitJunction(
  annotation: ReferenceAnnotation,
  junctionId: string,
  centerlineId: string,
): void {
  if (!junctionId || !centerlineId) {
    return;
  }
  const junction = annotation.junctions.find((item) => item.id === junctionId && item.source_mode === "explicit") ?? null;
  if (!junction) {
    return;
  }
  if (!junction.connected_centerline_ids.includes(centerlineId)) {
    junction.connected_centerline_ids = [...junction.connected_centerline_ids, centerlineId];
  }
}

export function snapDraftCenterlineEndpointsToExplicitJunctions(
  annotation: ReferenceAnnotation,
  points: AnnotationPoint[],
): {
  points: AnnotationPoint[];
  startJunctionId: string;
  endJunctionId: string;
} {
  const snappedPoints = points.map((point) => ({ ...point }));
  const snapTolerancePx = explicitJunctionEndpointSnapTolerancePx(annotation);
  let startJunctionId = "";
  let endJunctionId = "";
  if (snappedPoints.length === 0) {
    return { points: snappedPoints, startJunctionId, endJunctionId };
  }
  const startJunction = explicitJunctionNearPoint(annotation, snappedPoints[0], snapTolerancePx);
  if (startJunction) {
    snappedPoints[0] = junctionAnchorPoint(startJunction);
    startJunctionId = startJunction.id;
  }
  if (snappedPoints.length > 1) {
    const endJunction = explicitJunctionNearPoint(annotation, snappedPoints[snappedPoints.length - 1], snapTolerancePx);
    if (endJunction) {
      snappedPoints[snappedPoints.length - 1] = junctionAnchorPoint(endJunction);
      endJunctionId = endJunction.id;
    }
  }
  return { points: snappedPoints, startJunctionId, endJunctionId };
}

export function validateDraftCenterlinePlacement(
  annotation: ReferenceAnnotation,
  draftPoints: AnnotationPoint[],
): AnnotationModelIssue[] {
  const issues: AnnotationModelIssue[] = [];
  if (draftPoints.length < 2) {
    return issues;
  }
  const endpointTolerancePx = explicitJunctionEndpointTolerancePx(annotation);
  const startPoint = draftPoints[0];
  const endPoint = draftPoints[draftPoints.length - 1];
  for (const centerline of annotation.centerlines) {
    let centerlineIssue: AnnotationModelIssue | null = null;
    for (let draftIndex = 0; draftIndex < draftPoints.length - 1 && !centerlineIssue; draftIndex += 1) {
      const draftStart = draftPoints[draftIndex];
      const draftEnd = draftPoints[draftIndex + 1];
      for (let existingIndex = 0; existingIndex < centerline.points.length - 1; existingIndex += 1) {
        const existingStart = centerline.points[existingIndex];
        const existingEnd = centerline.points[existingIndex + 1];
        const intersection = segmentIntersectionDetails(draftStart, draftEnd, existingStart, existingEnd);
        if (!intersection) {
          continue;
        }
        const draftTouchesEndpoint =
          pointDistance(intersection.point, startPoint) <= endpointTolerancePx
          || pointDistance(intersection.point, endPoint) <= endpointTolerancePx;
        const existingTouchesEndpoint =
          pointDistance(intersection.point, centerline.points[0]) <= endpointTolerancePx
          || pointDistance(intersection.point, centerline.points[centerline.points.length - 1]) <= endpointTolerancePx;
        const sharedExplicitJunction =
          draftTouchesEndpoint
          && existingTouchesEndpoint
          && explicitJunctionNearPoint(annotation, intersection.point, endpointTolerancePx);
        if (sharedExplicitJunction) {
          continue;
        }
        centerlineIssue = {
          code: "centerline_intersection",
          message: `This centerline intersects ${centerline.id}. Draw approach roads only, then use Cross Tool or Branch Tool to create the junction explicitly.`,
        };
        break;
      }
    }
    if (centerlineIssue) {
      issues.push(centerlineIssue);
    }
  }
  return issues;
}

export function validateAnnotationForExplicitJunctionModel(annotation: ReferenceAnnotation): AnnotationModelIssue[] {
  const issues: AnnotationModelIssue[] = [];
  const endpointTolerancePx = explicitJunctionEndpointTolerancePx(annotation);
  const centerlinesById = new Map(annotation.centerlines.map((centerline) => [centerline.id, centerline]));
  const explicitJunctions = annotation.junctions.filter((junction) => junction.source_mode === "explicit");

  for (const junction of explicitJunctions) {
    const anchor = junctionAnchorPoint(junction);
    const connectedIds = new Set(junction.connected_centerline_ids);
    for (const centerlineId of junction.connected_centerline_ids) {
      const centerline = centerlinesById.get(centerlineId) ?? null;
      if (!centerline) {
        issues.push({
          code: "junction_connection",
          message: `Explicit junction ${junction.id} references missing centerline ${centerlineId}.`,
        });
        continue;
      }
      const anchoredAtStart = pointDistance(centerline.points[0], anchor) <= endpointTolerancePx;
      const anchoredAtEnd = pointDistance(centerline.points[centerline.points.length - 1], anchor) <= endpointTolerancePx;
      if (!anchoredAtStart && !anchoredAtEnd) {
        issues.push({
          code: "junction_connection",
          message: `Centerline ${centerline.id} is listed on explicit junction ${junction.id} but does not terminate at that junction anchor. Split the road at the junction or redraw it from the junction endpoint.`,
        });
        continue;
      }
      if (anchoredAtStart && centerline.start_junction_id !== junction.id) {
        issues.push({
          code: "junction_connection",
          message: `Centerline ${centerline.id} starts at explicit junction ${junction.id} but is missing matching start_junction_id metadata.`,
        });
      }
      if (anchoredAtEnd && centerline.end_junction_id !== junction.id) {
        issues.push({
          code: "junction_connection",
          message: `Centerline ${centerline.id} ends at explicit junction ${junction.id} but is missing matching end_junction_id metadata.`,
        });
      }
    }
    for (const centerline of annotation.centerlines) {
      if (centerline.points.length < 2) {
        continue;
      }
      const anchoredAtStart = pointDistance(centerline.points[0], anchor) <= endpointTolerancePx;
      const anchoredAtEnd = pointDistance(centerline.points[centerline.points.length - 1], anchor) <= endpointTolerancePx;
      if (anchoredAtStart || anchoredAtEnd) {
        const endpointJunctionIds = new Set<string>(
          [centerline.start_junction_id, centerline.end_junction_id].filter((value) => Boolean(value)),
        );
        if (endpointJunctionIds.has(junction.id) && !connectedIds.has(centerline.id)) {
          issues.push({
            code: "junction_connection",
            message: `Centerline ${centerline.id} points to explicit junction ${junction.id}, but the junction does not include it in connected_centerline_ids.`,
          });
        }
        continue;
      }
      const projection = projectPointOntoPolyline(centerline.points, anchor);
      if (
        projection.distancePx <= endpointTolerancePx
        && pointDistance(projection.projectedPoint, anchor) <= endpointTolerancePx
      ) {
        issues.push({
          code: "junction_pass_through",
          message: `Centerline ${centerline.id} passes through explicit junction ${junction.id}. In Reference Plan Annotator, roads must terminate at junctions instead of continuing through them.`,
        });
      }
    }
  }

  for (let leftIndex = 0; leftIndex < annotation.centerlines.length; leftIndex += 1) {
    const left = annotation.centerlines[leftIndex];
    let foundPairIssue = false;
    for (let rightIndex = leftIndex + 1; rightIndex < annotation.centerlines.length && !foundPairIssue; rightIndex += 1) {
      const right = annotation.centerlines[rightIndex];
      for (let leftSegmentIndex = 0; leftSegmentIndex < left.points.length - 1 && !foundPairIssue; leftSegmentIndex += 1) {
        for (let rightSegmentIndex = 0; rightSegmentIndex < right.points.length - 1; rightSegmentIndex += 1) {
          const intersection = segmentIntersectionDetails(
            left.points[leftSegmentIndex],
            left.points[leftSegmentIndex + 1],
            right.points[rightSegmentIndex],
            right.points[rightSegmentIndex + 1],
          );
          if (!intersection) {
            continue;
          }
          const leftJunctionId = endpointJunctionIdNearPoint(annotation, left, intersection.point, endpointTolerancePx);
          const rightJunctionId = endpointJunctionIdNearPoint(annotation, right, intersection.point, endpointTolerancePx);
          if (
            leftJunctionId
            && rightJunctionId
            && leftJunctionId === rightJunctionId
            && explicitJunctions.some((junction) => junction.id === leftJunctionId)
          ) {
            continue;
          }
          issues.push({
            code: "centerline_intersection",
            message: `Centerlines ${left.id} and ${right.id} intersect without an explicit junction. Split them at the junction and recreate the connection with Cross Tool or Branch Tool.`,
          });
          foundPairIssue = true;
          break;
        }
      }
    }
  }
  return issues;
}

export function cloneCenterlineForBranch(source: AnnotatedCenterline, id: string, points: AnnotationPoint[]): AnnotatedCenterline {
  const branch: AnnotatedCenterline = {
    id,
    label: id,
    points: points.map((point) => ({ ...point })),
    road_width_m: source.road_width_m,
    reference_width_px: source.reference_width_px,
    forward_drive_lane_count: source.forward_drive_lane_count,
    reverse_drive_lane_count: source.reverse_drive_lane_count,
    bike_lane_count: source.bike_lane_count,
    bus_lane_count: source.bus_lane_count,
    parking_lane_count: source.parking_lane_count,
    highway_type: source.highway_type,
    cross_section_mode: resolvedCrossSectionMode(source),
    cross_section_strips: source.cross_section_strips.map((strip) => ({ ...strip })),
    street_furniture_instances: [],
    start_junction_id: "",
    end_junction_id: "",
  };
  syncCenterlineDerivedFields(branch);
  return branch;
}

export function createDefaultAnnotatedCenterline(
  id: string,
  points: AnnotationPoint[],
  options: {
    label?: string;
    startJunctionId?: string;
    endJunctionId?: string;
    highwayType?: string;
  } = {},
): AnnotatedCenterline {
  const centerline: AnnotatedCenterline = {
    id,
    label: options.label ?? id,
    points: points.map((point) => ({ ...point })),
    road_width_m: nominalSeedCrossSectionWidthForCounts(
      DEFAULT_FORWARD_DRIVE_LANE_COUNT,
      DEFAULT_REVERSE_DRIVE_LANE_COUNT,
      0,
      0,
      0,
    ),
    reference_width_px: null,
    forward_drive_lane_count: DEFAULT_FORWARD_DRIVE_LANE_COUNT,
    reverse_drive_lane_count: DEFAULT_REVERSE_DRIVE_LANE_COUNT,
    bike_lane_count: 0,
    bus_lane_count: 0,
    parking_lane_count: 0,
    highway_type: options.highwayType ?? "annotated_centerline",
    cross_section_mode: CROSS_SECTION_MODE_COARSE,
    cross_section_strips: [],
    street_furniture_instances: [],
    start_junction_id: options.startJunctionId ?? "",
    end_junction_id: options.endJunctionId ?? "",
  };
  ensureDetailedCrossSection(centerline);
  return centerline;
}

export function reserveNextFeatureIds(annotation: ReferenceAnnotation, prefix: string, count: number): string[] {
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
  const result: string[] = [];
  let counter = 1;
  while (result.length < count) {
    const candidate = `${prefix}_${String(counter).padStart(2, "0")}`;
    if (!ids.has(candidate)) {
      ids.add(candidate);
      result.push(candidate);
    }
    counter += 1;
  }
  return result;
}

export function reserveNextFeatureIdsWithBlocked(
  annotation: ReferenceAnnotation,
  prefix: string,
  count: number,
  blockedIds: string[],
): string[] {
  const ids = new Set<string>(blockedIds);
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
  const result: string[] = [];
  let counter = 1;
  while (result.length < count) {
    const candidate = `${prefix}_${String(counter).padStart(2, "0")}`;
    if (!ids.has(candidate)) {
      ids.add(candidate);
      result.push(candidate);
    }
    counter += 1;
  }
  return result;
}

export function clonePoint(point: AnnotationPoint): AnnotationPoint {
  return { x: point.x, y: point.y };
}

export function replaceCenterlineReference(
  junction: AnnotatedJunction,
  previousCenterlineId: string,
  replacementIds: string[],
): void {
  const nextIds: string[] = [];
  let replaced = false;
  for (const item of junction.connected_centerline_ids) {
    if (item === previousCenterlineId) {
      replaced = true;
      for (const replacementId of replacementIds) {
        if (!nextIds.includes(replacementId)) {
          nextIds.push(replacementId);
        }
      }
      continue;
    }
    if (!nextIds.includes(item)) {
      nextIds.push(item);
    }
  }
  if (replaced) {
    junction.connected_centerline_ids = nextIds;
  }
}

export function junctionAnchorPoint(junction: AnnotatedJunction): AnnotationPoint {
  return { x: junction.x, y: junction.y };
}

export function endpointJunctionIdAtPoint(centerline: AnnotatedCenterline, point: AnnotationPoint): string | null {
  if (centerline.points.length === 0) {
    return null;
  }
  if (pointDistance(centerline.points[0], point) <= BRANCH_VERTEX_REUSE_TOLERANCE_PX) {
    return centerline.start_junction_id || null;
  }
  if (pointDistance(centerline.points[centerline.points.length - 1], point) <= BRANCH_VERTEX_REUSE_TOLERANCE_PX) {
    return centerline.end_junction_id || null;
  }
  return null;
}

export function classifyJunctionKindForCount(count: number): string {
  if (count >= 4) {
    return "cross_junction";
  }
  if (count === 3) {
    return "t_junction";
  }
  return "junction";
}

export function updateJunctionConnectedCenterlines(
  annotation: ReferenceAnnotation,
  junctionId: string,
  connectedCenterlineIds: string[],
): AnnotatedJunction | null {
  const junction = annotation.junctions.find((item) => item.id === junctionId) ?? null;
  if (!junction) {
    return null;
  }
  const deduped = [...new Set(connectedCenterlineIds.filter((item) => Boolean(item)))];
  junction.connected_centerline_ids = deduped;
  if (junction.source_mode === "explicit") {
    junction.kind = classifyJunctionKindForCount(deduped.length);
  }
  return junction;
}

export function createExplicitJunction(
  annotation: ReferenceAnnotation,
  options: {
    junctionId: string;
    kind: string;
    anchor: AnnotationPoint;
    connectedCenterlineIds: string[];
    crosswalkDepthM?: number;
  },
): AnnotatedJunction {
  const { junctionId, kind, anchor, connectedCenterlineIds, crosswalkDepthM = 3 } = options;
  const junction: AnnotatedJunction = {
    id: junctionId,
    label: junctionId,
    x: anchor.x,
    y: anchor.y,
    kind,
    connected_centerline_ids: [...new Set(connectedCenterlineIds)],
    crosswalk_depth_m: Math.max(0.5, crosswalkDepthM),
    source_mode: "explicit",
  };
  annotation.junctions.push(junction);
  return junction;
}

export function splitFurnitureInstancesForCenterline(
  centerline: AnnotatedCenterline,
  leftCenterlineId: string,
  rightCenterlineId: string,
  splitStationM: number,
): {
  left: AnnotatedStreetFurnitureInstance[];
  right: AnnotatedStreetFurnitureInstance[];
} {
  const left: AnnotatedStreetFurnitureInstance[] = [];
  const right: AnnotatedStreetFurnitureInstance[] = [];
  for (const instance of centerline.street_furniture_instances) {
    if (instance.station_m <= splitStationM + 1e-6) {
      left.push({
        ...instance,
        centerline_id: leftCenterlineId,
        station_m: Math.max(0, instance.station_m),
      });
    } else {
      right.push({
        ...instance,
        centerline_id: rightCenterlineId,
        station_m: Math.max(0, instance.station_m - splitStationM),
      });
    }
  }
  return { left, right };
}

export function splitCenterlineAtSnap(
  annotation: ReferenceAnnotation,
  centerlineId: string,
  snap: BranchSnapTarget,
  junctionId: string,
  blockedCenterlineIds: string[] = [],
): {
  anchorPoint: AnnotationPoint;
  connectedCenterlineIds: string[];
} | null {
  const centerlineIndex = annotation.centerlines.findIndex((item) => item.id === centerlineId);
  if (centerlineIndex < 0) {
    return null;
  }
  const centerline = annotation.centerlines[centerlineIndex];
  const originalPoints = centerline.points.map((point) => clonePoint(point));
  let splitIndex = originalPoints.findIndex((point) => pointDistance(point, snap.point) <= BRANCH_VERTEX_REUSE_TOLERANCE_PX);
  const anchorPoint = clonePoint(snap.point);
  if (splitIndex < 0) {
    splitIndex = clamp(snap.segmentIndex + 1, 1, originalPoints.length - 1);
    originalPoints.splice(splitIndex, 0, anchorPoint);
  } else {
    anchorPoint.x = originalPoints[splitIndex].x;
    anchorPoint.y = originalPoints[splitIndex].y;
  }

  if (splitIndex <= 0) {
    centerline.points = originalPoints;
    centerline.start_junction_id = junctionId;
    syncCenterlineDerivedFields(centerline);
    return { anchorPoint, connectedCenterlineIds: [centerline.id] };
  }
  if (splitIndex >= originalPoints.length - 1) {
    centerline.points = originalPoints;
    centerline.end_junction_id = junctionId;
    syncCenterlineDerivedFields(centerline);
    return { anchorPoint, connectedCenterlineIds: [centerline.id] };
  }

  const [leftId, rightId] = reserveNextFeatureIdsWithBlocked(annotation, "centerline", 2, blockedCenterlineIds);
  const left = cloneCenterlineForBranch(centerline, leftId, originalPoints.slice(0, splitIndex + 1));
  const right = cloneCenterlineForBranch(centerline, rightId, originalPoints.slice(splitIndex));
  left.start_junction_id = centerline.start_junction_id;
  left.end_junction_id = junctionId;
  right.start_junction_id = junctionId;
  right.end_junction_id = centerline.end_junction_id;
  const splitStationM = snap.stationPx / Math.max(annotation.pixels_per_meter, 0.0001);
  const splitFurniture = splitFurnitureInstancesForCenterline(centerline, left.id, right.id, splitStationM);
  left.street_furniture_instances = splitFurniture.left;
  right.street_furniture_instances = splitFurniture.right;
  syncCenterlineDerivedFields(left);
  syncCenterlineDerivedFields(right);
  annotation.centerlines.splice(centerlineIndex, 1, left, right);
  for (const junction of annotation.junctions) {
    if (junction.id === centerline.start_junction_id) {
      replaceCenterlineReference(junction, centerline.id, [left.id]);
    } else if (junction.id === centerline.end_junction_id) {
      replaceCenterlineReference(junction, centerline.id, [right.id]);
    }
  }
  return { anchorPoint, connectedCenterlineIds: [left.id, right.id] };
}

export function findNearestBranchSnapTarget(
  annotation: ReferenceAnnotation,
  point: AnnotationPoint,
  options: { excludeCenterlineId?: string } = {},
): BranchSnapTarget | null {
  const { excludeCenterlineId } = options;
  let best: BranchSnapTarget | null = null;
  for (const centerline of annotation.centerlines) {
    if (excludeCenterlineId && centerline.id === excludeCenterlineId) {
      continue;
    }
    if (centerline.points.length < 2) {
      continue;
    }
    const projection = projectPointOntoPolyline(centerline.points, point);
    if (projection.distancePx > BRANCH_SNAP_TOLERANCE_PX) {
      continue;
    }
    if (!best || projection.distancePx < best.distancePx) {
      best = {
        centerlineId: centerline.id,
        segmentIndex: projection.segmentIndex,
        stationPx: projection.stationPx,
        point: { ...projection.projectedPoint },
        distancePx: projection.distancePx,
      };
    }
  }
  return best;
}

export function insertSharedVertexAtSnap(centerline: AnnotatedCenterline, snap: BranchSnapTarget): AnnotationPoint {
  for (const point of centerline.points) {
    if (pointDistance(point, snap.point) <= BRANCH_VERTEX_REUSE_TOLERANCE_PX) {
      return point;
    }
  }
  const insertIndex = clamp(snap.segmentIndex + 1, 1, centerline.points.length - 1);
  const inserted = { ...snap.point };
  centerline.points.splice(insertIndex, 0, inserted);
  return inserted;
}

export function lineIntersectionTs(
  pointA: AnnotationPoint,
  directionA: AnnotationPoint,
  pointB: AnnotationPoint,
  directionB: AnnotationPoint,
): AnnotationPoint | null {
  const determinant = directionA.x * directionB.y - directionA.y * directionB.x;
  if (Math.abs(determinant) <= 1e-6) {
    return null;
  }
  const deltaX = pointB.x - pointA.x;
  const deltaY = pointB.y - pointA.y;
  const tValue = (deltaX * directionB.y - deltaY * directionB.x) / determinant;
  return {
    x: pointA.x + directionA.x * tValue,
    y: pointA.y + directionA.y * tValue,
  };
}

export function ensureDetailedCrossSections(annotation: ReferenceAnnotation, centerlineIds: Iterable<string>): string[] {
  const seen = new Set<string>();
  const changed: string[] = [];
  for (const centerlineId of centerlineIds) {
    if (!centerlineId || seen.has(centerlineId)) {
      continue;
    }
    seen.add(centerlineId);
    const centerline = annotation.centerlines.find((item) => item.id === centerlineId);
    if (!centerline) {
      continue;
    }
    if (ensureDetailedCrossSection(centerline)) {
      changed.push(centerlineId);
    }
  }
  return changed;
}

export function linkedCrossStripKeys(
  junctionOverlays: DerivedJunctionOverlay[],
  selection: Selection,
  selectedStripId: string | null,
): Set<string> {
  const keys = new Set<string>();
  if (selection?.kind !== "centerline" || !selectedStripId) {
    return keys;
  }
  keys.add(stripKey(selection.id, selectedStripId));
  for (const target of selectedStripCornerFamilyTargets(junctionOverlays, selection.id, selectedStripId)) {
    keys.add(stripKey(target.target.centerlineId, target.target.stripId));
  }
  return keys;
}
