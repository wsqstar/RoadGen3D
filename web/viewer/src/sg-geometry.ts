import type {
  AnnotationPoint,
  AnnotatedCenterline,
  AnnotatedCrossSectionStrip,
  AnnotatedBuildingRegion,
  AnnotatedJunction,
  BuildingRegionResizeHandle,
  BranchSnapTarget,
  DerivedJunctionOverlay,
  DerivedJunctionOverlayArm,
  DerivedJunctionOverlayBoundary,
  DerivedJunctionOverlayConnectorLine,
  DerivedJunctionOverlayPatch,
  JunctionOverlayControlPoint,
  JunctionOverlayCornerFocus,
  JunctionOverlayCornerKernel,
  JunctionOverlayFootPoint,
  JunctionOverlayGuideLine,
  JunctionOverlayStripLink,
  ClippedDisplaySegment,
  StripKind,
  StripZone,
  SideStripLayouts,
  ReferenceAnnotation,
} from "./sg-types";
import {
  ANNOTATION_MODEL_TOLERANCE_PX,
  BUILDING_REGION_MIN_SIZE_PX,
  BUILDING_REGION_ROTATE_HANDLE_OFFSET_PX,
  CENTER_STRIP_KINDS,
  CORNER_LINK_STRIP_KINDS,
  CROSS_SECTION_MODE_DETAILED,
  DEFAULT_CENTERLINE_MARK_WIDTH_M,
  DEFAULT_DRIVE_LANE_WIDTH_M,
  NOMINAL_STRIP_WIDTHS,
  SIDE_STRIP_KINDS,
} from "./sg-constants";
import {
  buildOffsetPolylineSegments,
  clamp,
  clonePoint,
  deriveLaneProfileFromStrips,
  formatCrossSectionSummary,
  formatLaneSummary,
  getCenterlineCarriagewayWidth,
  getCenterlineCrossSectionWidth,
  junctionAnchorPoint,
  laneProfile,
  limitedMiterJoinPoint,
  lineIntersectionTs,
  normalizeVector,
  offsetPointAlongNormal,
  offsetPolyline,
  pointDistance,
  polylineLength,
  projectPointOntoPolyline,
  resolvedCrossSectionMode,
  seedDetailedCrossSection,
  sortedCrossSectionStrips,
  stationToPolylinePoint,
  stripCenterOffsetMeters,
} from "./sg-utils";

export function crossAxisNormalAtSnap(centerline: AnnotatedCenterline, snap: BranchSnapTarget): AnnotationPoint {
  const sample = stationToPolylinePoint(centerline.points, snap.stationPx);
  const length = Math.max(Math.hypot(sample.leftNormal.x, sample.leftNormal.y), 1e-6);
  return {
    x: sample.leftNormal.x / length,
    y: sample.leftNormal.y / length,
  };
}

export function pointOnAxis(anchor: AnnotationPoint, axisNormal: AnnotationPoint, signedDistancePx: number): AnnotationPoint {
  return {
    x: anchor.x + axisNormal.x * signedDistancePx,
    y: anchor.y + axisNormal.y * signedDistancePx,
  };
}

export function normalizeAngleDegTs(value: number): number {
  let normalized = value % 360;
  if (normalized < 0) {
    normalized += 360;
  }
  return normalized;
}

export function angleDegTs(fromPoint: AnnotationPoint, toPoint: AnnotationPoint): number {
  return normalizeAngleDegTs((Math.atan2(toPoint.y - fromPoint.y, toPoint.x - fromPoint.x) * 180) / Math.PI);
}

export function angleDistanceDegTs(aDeg: number, bDeg: number): number {
  const diff = Math.abs(normalizeAngleDegTs(aDeg - bDeg));
  return Math.min(diff, Math.abs(diff - 360));
}

export function axisDistanceDegTs(angleDeg: number, axisAngleDeg: number): number {
  const diff = angleDistanceDegTs(angleDeg, axisAngleDeg);
  return Math.min(diff, Math.abs(diff - 180));
}

export function classifyDerivedJunctionKind(anglesDeg: number[]): "t_junction" | "cross_junction" | "complex_junction" {
  const ordered = [...anglesDeg].sort((a, b) => a - b);
  const diffs = ordered.map((value, index) => {
    const nextValue = ordered[(index + 1) % ordered.length];
    const raw = nextValue - value + (index === ordered.length - 1 ? 360 : 0);
    return raw;
  });
  if (ordered.length === 4 && diffs.length > 0 && diffs.every((diff) => Math.abs(diff - 90) <= 35)) {
    return "cross_junction";
  }
  if (ordered.length === 3 && diffs.some((diff) => diff >= 145)) {
    return "t_junction";
  }
  return "complex_junction";
}

export function junctionProfileWidths(centerline: AnnotatedCenterline): {
  carriagewayWidthM: number;
  nearroadBufferWidthM: number;
  nearroadFurnishingWidthM: number;
  clearSidewalkWidthM: number;
  farfromroadBufferWidthM: number;
  frontageReserveWidthM: number;
} {
  const strips =
    resolvedCrossSectionMode(centerline) === CROSS_SECTION_MODE_DETAILED && centerline.cross_section_strips.length > 0
      ? centerline.cross_section_strips
      : seedDetailedCrossSection(centerline);
  const sideStrips = strips.filter((strip) => strip.zone === "left" || strip.zone === "right");
  const maxWidthForKind = (kind: StripKind): number => {
    let best = 0;
    for (const strip of sideStrips) {
      if (strip.kind === kind) {
        best = Math.max(best, Math.max(0, strip.width_m));
      }
    }
    return best;
  };
  return {
    carriagewayWidthM: getCenterlineCarriagewayWidth(centerline),
    nearroadBufferWidthM: maxWidthForKind("nearroad_buffer"),
    nearroadFurnishingWidthM: maxWidthForKind("nearroad_furnishing"),
    clearSidewalkWidthM: maxWidthForKind("clear_sidewalk"),
    farfromroadBufferWidthM: maxWidthForKind("farfromroad_buffer"),
    frontageReserveWidthM: maxWidthForKind("frontage_reserve"),
  };
}

export function rectanglePolygonPoints(
  center: AnnotationPoint,
  tangent: AnnotationPoint,
  normal: AnnotationPoint,
  lengthPx: number,
  widthPx: number,
): AnnotationPoint[] {
  const halfLength = Math.max(lengthPx * 0.5, 1);
  const halfWidth = Math.max(widthPx * 0.5, 1);
  return [
    { x: center.x - tangent.x * halfLength - normal.x * halfWidth, y: center.y - tangent.y * halfLength - normal.y * halfWidth },
    { x: center.x - tangent.x * halfLength + normal.x * halfWidth, y: center.y - tangent.y * halfLength + normal.y * halfWidth },
    { x: center.x + tangent.x * halfLength + normal.x * halfWidth, y: center.y + tangent.y * halfLength + normal.y * halfWidth },
    { x: center.x + tangent.x * halfLength - normal.x * halfWidth, y: center.y + tangent.y * halfLength - normal.y * halfWidth },
  ];
}

export function buildingRegionAxes(yawDeg: number): { axisX: AnnotationPoint; axisY: AnnotationPoint } {
  const yawRad = (normalizeAngleDegTs(yawDeg) * Math.PI) / 180;
  return {
    axisX: { x: Math.cos(yawRad), y: -Math.sin(yawRad) },
    axisY: { x: -Math.sin(yawRad), y: -Math.cos(yawRad) },
  };
}

export function buildingRegionPolygonPoints(region: AnnotatedBuildingRegion): AnnotationPoint[] {
  const { axisX, axisY } = buildingRegionAxes(region.yaw_deg);
  const halfWidth = Math.max(region.width_px * 0.5, 0.5);
  const halfHeight = Math.max(region.height_px * 0.5, 0.5);
  const corners: Array<[number, number]> = [
    [-halfWidth, -halfHeight],
    [halfWidth, -halfHeight],
    [halfWidth, halfHeight],
    [-halfWidth, halfHeight],
  ];
  return corners.map(([localX, localY]) => ({
    x: region.center_px.x + axisX.x * localX + axisY.x * localY,
    y: region.center_px.y + axisX.y * localX + axisY.y * localY,
  }));
}

export function buildingRegionLocalPoint(region: AnnotatedBuildingRegion, point: AnnotationPoint): AnnotationPoint {
  const { axisX, axisY } = buildingRegionAxes(region.yaw_deg);
  const dx = point.x - region.center_px.x;
  const dy = point.y - region.center_px.y;
  return {
    x: dx * axisX.x + dy * axisX.y,
    y: dx * axisY.x + dy * axisY.y,
  };
}

export function buildingRegionResizeHandlePoint(region: AnnotatedBuildingRegion, handle: BuildingRegionResizeHandle): AnnotationPoint {
  const localX = handle === "ne" || handle === "se" ? region.width_px * 0.5 : -region.width_px * 0.5;
  const localY = handle === "se" || handle === "sw" ? -region.height_px * 0.5 : region.height_px * 0.5;
  const { axisX, axisY } = buildingRegionAxes(region.yaw_deg);
  return {
    x: region.center_px.x + axisX.x * localX + axisY.x * localY,
    y: region.center_px.y + axisX.y * localX + axisY.y * localY,
  };
}

export function buildingRegionRotateHandlePoint(region: AnnotatedBuildingRegion): AnnotationPoint {
  const { axisY } = buildingRegionAxes(region.yaw_deg);
  const distance = region.height_px * 0.5 + BUILDING_REGION_ROTATE_HANDLE_OFFSET_PX;
  return {
    x: region.center_px.x + axisY.x * distance,
    y: region.center_px.y + axisY.y * distance,
  };
}

export function buildBuildingRegionFromDraft(
  id: string,
  startPoint: AnnotationPoint,
  currentPoint: AnnotationPoint,
): AnnotatedBuildingRegion {
  const minX = Math.min(startPoint.x, currentPoint.x);
  const maxX = Math.max(startPoint.x, currentPoint.x);
  const minY = Math.min(startPoint.y, currentPoint.y);
  const maxY = Math.max(startPoint.y, currentPoint.y);
  return {
    id,
    label: id,
    center_px: {
      x: (minX + maxX) * 0.5,
      y: (minY + maxY) * 0.5,
    },
    width_px: Math.max(maxX - minX, BUILDING_REGION_MIN_SIZE_PX),
    height_px: Math.max(maxY - minY, BUILDING_REGION_MIN_SIZE_PX),
    yaw_deg: 0,
  };
}

export function centerlineSideStripLayouts(centerline: AnnotatedCenterline): SideStripLayouts {
  const strips =
    resolvedCrossSectionMode(centerline) === CROSS_SECTION_MODE_DETAILED && centerline.cross_section_strips.length > 0
      ? sortedCrossSectionStrips(centerline.cross_section_strips)
      : sortedCrossSectionStrips(seedDetailedCrossSection(centerline));
  const offsets = stripCenterOffsetMeters({
    ...centerline,
    cross_section_strips: strips,
  });
  const result: SideStripLayouts = {
    left: [],
    center: [],
    right: [],
  };
  for (const strip of strips) {
    const bounds = offsets[strip.strip_id];
    if (!bounds) {
      continue;
    }
    const halfWidthM = bounds.widthM * 0.5;
    result[strip.zone].push({
      stripId: strip.strip_id,
      kind: strip.kind,
      centerOffsetM: bounds.centerOffsetM,
      innerOffsetM: bounds.centerOffsetM - halfWidthM,
      outerOffsetM: bounds.centerOffsetM + halfWidthM,
    });
  }
  return result;
}


export function facingZoneForCornerTs(
  boundaryCenter: AnnotationPoint,
  normal: AnnotationPoint,
  cornerCenter: AnnotationPoint,
): StripZone {
  const dotValue = (cornerCenter.x - boundaryCenter.x) * normal.x + (cornerCenter.y - boundaryCenter.y) * normal.y;
  return dotValue >= 0 ? "left" : "right";
}

export function genericStripOffsetRangeTs(
  arm: {
    carriagewayWidthPx: number;
    nearroadBufferWidthPx: number;
    nearroadFurnishingWidthPx: number;
    clearSidewalkWidthPx: number;
    farfromroadBufferWidthPx: number;
    frontageReserveWidthPx: number;
  },
  kind: StripKind,
  zone: StripZone,
): { centerOffsetPx: number; innerOffsetPx: number; outerOffsetPx: number } | null {
  const sign = zone === "left" ? 1 : -1;
  const halfCarriagewayPx = Math.max(arm.carriagewayWidthPx * 0.5, 0);
  let innerAbsPx: number | null = null;
  let outerAbsPx: number | null = null;
  if (kind === "nearroad_furnishing" && arm.nearroadFurnishingWidthPx > 0) {
    innerAbsPx = halfCarriagewayPx + arm.nearroadBufferWidthPx;
    outerAbsPx = innerAbsPx + arm.nearroadFurnishingWidthPx;
  } else if (kind === "clear_sidewalk" && arm.clearSidewalkWidthPx > 0) {
    innerAbsPx = halfCarriagewayPx + arm.nearroadBufferWidthPx + arm.nearroadFurnishingWidthPx;
    outerAbsPx = innerAbsPx + arm.clearSidewalkWidthPx;
  } else if (kind === "frontage_reserve" && arm.frontageReserveWidthPx > 0) {
    innerAbsPx =
      halfCarriagewayPx +
      arm.nearroadBufferWidthPx +
      arm.nearroadFurnishingWidthPx +
      arm.clearSidewalkWidthPx +
      arm.farfromroadBufferWidthPx;
    outerAbsPx = innerAbsPx + arm.frontageReserveWidthPx;
  }
  if (innerAbsPx === null || outerAbsPx === null) {
    return null;
  }
  return {
    centerOffsetPx: ((innerAbsPx + outerAbsPx) * 0.5) * sign,
    innerOffsetPx: innerAbsPx * sign,
    outerOffsetPx: outerAbsPx * sign,
  };
}

export function orientedOffsetRangeTs(
  range: { centerOffsetPx: number; innerOffsetPx: number; outerOffsetPx: number },
  reverseOffsets: boolean,
): { centerOffsetPx: number; innerOffsetPx: number; outerOffsetPx: number } {
  if (!reverseOffsets) {
    return range;
  }
  return {
    centerOffsetPx: -range.centerOffsetPx,
    innerOffsetPx: -range.outerOffsetPx,
    outerOffsetPx: -range.innerOffsetPx,
  };
}

export function junctionControlPointOffsetsTs(
  strip: {
    centerOffsetM: number;
    innerOffsetM: number;
    outerOffsetM: number;
  },
  reverseOffsets: boolean,
  pixelsPerMeter: number,
): Array<[JunctionOverlayControlPoint["pointKind"], number]> {
  const orientedRange = orientedOffsetRangeTs(
    {
      centerOffsetPx: strip.centerOffsetM * pixelsPerMeter,
      innerOffsetPx: strip.innerOffsetM * pixelsPerMeter,
      outerOffsetPx: strip.outerOffsetM * pixelsPerMeter,
    },
    reverseOffsets,
  );
  return [
    ["center_control_point", orientedRange.centerOffsetPx],
    ["inner_edge_control_point", orientedRange.innerOffsetPx],
    ["outer_edge_control_point", orientedRange.outerOffsetPx],
  ];
}

export function cornerStripOffsetRangeTs(
  arm: {
    sideStripLayouts: SideStripLayouts;
    carriagewayWidthPx: number;
    nearroadBufferWidthPx: number;
    nearroadFurnishingWidthPx: number;
    clearSidewalkWidthPx: number;
    farfromroadBufferWidthPx: number;
    frontageReserveWidthPx: number;
    splitBoundaryCenter: AnnotationPoint;
    normal: AnnotationPoint;
    reverseOffsets: boolean;
  },
  cornerCenter: AnnotationPoint,
  kind: StripKind,
  pixelsPerMeter: number,
): { zone: StripZone; stripId: string | null; centerOffsetPx: number; innerOffsetPx: number; outerOffsetPx: number } | null {
  const zone = facingZoneForCornerTs(arm.splitBoundaryCenter, arm.normal, cornerCenter);
  const matching = arm.sideStripLayouts[zone].find((item) => item.kind === kind) ?? null;
  if (matching) {
    return {
      zone,
      stripId: matching.stripId,
      ...orientedOffsetRangeTs(
        {
          centerOffsetPx: matching.centerOffsetM * pixelsPerMeter,
          innerOffsetPx: matching.innerOffsetM * pixelsPerMeter,
          outerOffsetPx: matching.outerOffsetM * pixelsPerMeter,
        },
        arm.reverseOffsets,
      ),
    };
  }
  const generic = genericStripOffsetRangeTs(arm, kind, zone);
  if (!generic) {
    return null;
  }
  return { zone, stripId: null, ...orientedOffsetRangeTs(generic, arm.reverseOffsets) };
}

export function pointOnBoundaryWithOffsetTs(
  boundaryCenter: AnnotationPoint,
  normal: AnnotationPoint,
  offsetPx: number,
): AnnotationPoint {
  return {
    x: boundaryCenter.x + normal.x * offsetPx,
    y: boundaryCenter.y + normal.y * offsetPx,
  };
}

export function connectorJoinPointTs(
  pointA: AnnotationPoint,
  tangentA: AnnotationPoint,
  pointB: AnnotationPoint,
  tangentB: AnnotationPoint,
): AnnotationPoint {
  return lineIntersectionTs(pointA, tangentA, pointB, tangentB) ?? {
    x: (pointA.x + pointB.x) * 0.5,
    y: (pointA.y + pointB.y) * 0.5,
  };
}

export function pointAlongVectorTs(
  point: AnnotationPoint,
  vector: AnnotationPoint,
  distance: number,
): AnnotationPoint {
  return {
    x: point.x + vector.x * distance,
    y: point.y + vector.y * distance,
  };
}

export function midpointTs(pointA: AnnotationPoint, pointB: AnnotationPoint): AnnotationPoint {
  return {
    x: (pointA.x + pointB.x) * 0.5,
    y: (pointA.y + pointB.y) * 0.5,
  };
}

export function dotProductTs(a: AnnotationPoint, b: AnnotationPoint): number {
  return a.x * b.x + a.y * b.y;
}

export function subtractPointTs(a: AnnotationPoint, b: AnnotationPoint): AnnotationPoint {
  return { x: a.x - b.x, y: a.y - b.y };
}

export function perpendicularDirectionsTs(direction: AnnotationPoint): [AnnotationPoint, AnnotationPoint] {
  return [
    { x: -direction.y, y: direction.x },
    { x: direction.y, y: -direction.x },
  ];
}

export function headingDegForVectorTs(direction: AnnotationPoint): number {
  return angleDegTs({ x: 0, y: 0 }, direction);
}

export function arcSweepRadiansTs(startAngle: number, endAngle: number, clockwise: boolean): number {
  let sweep = clockwise ? startAngle - endAngle : endAngle - startAngle;
  while (sweep <= 0) {
    sweep += Math.PI * 2;
  }
  return sweep;
}

export function sampleCircularArcPointsTs(
  center: AnnotationPoint,
  radiusPx: number,
  startAngle: number,
  endAngle: number,
  clockwise: boolean,
  startPoint: AnnotationPoint,
  endPoint: AnnotationPoint,
  targetSegmentLengthPx: number,
): AnnotationPoint[] {
  const sweep = arcSweepRadiansTs(startAngle, endAngle, clockwise);
  const arcLength = Math.max(radiusPx * sweep, 0);
  let pointCount = Math.ceil(arcLength / Math.max(targetSegmentLengthPx, 1e-6)) + 1;
  pointCount = clamp(pointCount, 8, 24);
  pointCount = Math.max(pointCount, 3);
  const direction = clockwise ? -1 : 1;
  const points: AnnotationPoint[] = [];
  for (let index = 0; index < pointCount; index += 1) {
    const ratio = index / Math.max(pointCount - 1, 1);
    const angle = startAngle + direction * sweep * ratio;
    points.push({
      x: center.x + Math.cos(angle) * radiusPx,
      y: center.y + Math.sin(angle) * radiusPx,
    });
  }
  points[0] = clonePoint(startPoint);
  points[points.length - 1] = clonePoint(endPoint);
  return points;
}

export function fallbackCornerKernelGeometryTs(
  startPoint: AnnotationPoint,
  endPoint: AnnotationPoint,
  startTangent: AnnotationPoint,
  endTangent: AnnotationPoint,
): {
  kernelKind: "polyline_fallback";
  center: AnnotationPoint;
  radiusPx: number;
  startHeadingDeg: number;
  endHeadingDeg: number;
  clockwise: null;
  sampledPoints: AnnotationPoint[];
} {
  const joinPoint = connectorJoinPointTs(startPoint, startTangent, endPoint, endTangent);
  return {
    kernelKind: "polyline_fallback",
    center: clonePoint(joinPoint),
    radiusPx: 0,
    startHeadingDeg: headingDegForVectorTs(startTangent),
    endHeadingDeg: headingDegForVectorTs(endTangent),
    clockwise: null,
    sampledPoints: [clonePoint(startPoint), clonePoint(joinPoint), clonePoint(endPoint)],
  };
}

export function cornerLaneKernelGeometryTs(
  startPoint: AnnotationPoint,
  endPoint: AnnotationPoint,
  startTangent: AnnotationPoint,
  endTangent: AnnotationPoint,
  cornerCenter: AnnotationPoint | null,
  targetSegmentLengthPx: number,
  minRadiusPx: number,
): {
  kernelKind: "circular_arc" | "polyline_fallback";
  center: AnnotationPoint;
  radiusPx: number;
  startHeadingDeg: number;
  endHeadingDeg: number;
  clockwise: boolean | null;
  sampledPoints: AnnotationPoint[];
} {
  const normalizedStart = normalizeVector(startTangent);
  const normalizedEnd = normalizeVector(endTangent);
  const fallback = fallbackCornerKernelGeometryTs(startPoint, endPoint, startTangent, endTangent);
  if (!normalizedStart || !normalizedEnd) {
    return fallback;
  }

  let bestCandidate:
    | {
        score: number;
        center: AnnotationPoint;
        radiusPx: number;
        startAngle: number;
        endAngle: number;
        startHeadingDeg: number;
        endHeadingDeg: number;
        clockwise: boolean;
      }
    | null = null;

  for (const normalStart of perpendicularDirectionsTs(normalizedStart)) {
    for (const normalEnd of perpendicularDirectionsTs(normalizedEnd)) {
      const center = lineIntersectionTs(startPoint, normalStart, endPoint, normalEnd);
      if (!center) {
        continue;
      }
      const radiusStart = pointDistance(center, startPoint);
      const radiusEnd = pointDistance(center, endPoint);
      const radiusPx = (radiusStart + radiusEnd) * 0.5;
      if (radiusPx < minRadiusPx) {
        continue;
      }
      if (Math.abs(radiusStart - radiusEnd) > Math.max(0.05, radiusPx * 0.05)) {
        continue;
      }
      const radialStart = normalizeVector(subtractPointTs(startPoint, center));
      const radialEnd = normalizeVector(subtractPointTs(endPoint, center));
      if (!radialStart || !radialEnd) {
        continue;
      }
      const startAngle = Math.atan2(startPoint.y - center.y, startPoint.x - center.x);
      const endAngle = Math.atan2(endPoint.y - center.y, endPoint.x - center.x);
      const candidates = [
        {
          clockwise: true,
          tangentStart: { x: radialStart.y, y: -radialStart.x },
          tangentEnd: { x: radialEnd.y, y: -radialEnd.x },
        },
        {
          clockwise: false,
          tangentStart: { x: -radialStart.y, y: radialStart.x },
          tangentEnd: { x: -radialEnd.y, y: radialEnd.x },
        },
      ] as const;
      for (const candidate of candidates) {
        const alignStart = Math.abs(dotProductTs(candidate.tangentStart, normalizedStart));
        const alignEnd = Math.abs(dotProductTs(candidate.tangentEnd, normalizedEnd));
        const minAlign = Math.min(alignStart, alignEnd);
        if (minAlign < 0.5) {
          continue;
        }
        const sweep = arcSweepRadiansTs(startAngle, endAngle, candidate.clockwise);
        if (sweep <= 1e-6 || sweep > Math.PI + (5 * Math.PI) / 180) {
          continue;
        }
        const midpointAngle = startAngle + (candidate.clockwise ? -0.5 : 0.5) * sweep;
        const midpoint = {
          x: center.x + Math.cos(midpointAngle) * radiusPx,
          y: center.y + Math.sin(midpointAngle) * radiusPx,
        };
        let score = minAlign * 10 + alignStart + alignEnd - radiusPx * 0.05;
        if (cornerCenter) {
          score -= pointDistance(midpoint, cornerCenter) * 0.5;
        }
        if (!bestCandidate || score > bestCandidate.score) {
          bestCandidate = {
            score,
            center: clonePoint(center),
            radiusPx,
            startAngle,
            endAngle,
            startHeadingDeg: headingDegForVectorTs(candidate.tangentStart),
            endHeadingDeg: headingDegForVectorTs(candidate.tangentEnd),
            clockwise: candidate.clockwise,
          };
        }
      }
    }
  }

  if (!bestCandidate) {
    return fallback;
  }

  return {
    kernelKind: "circular_arc",
    center: clonePoint(bestCandidate.center),
    radiusPx: bestCandidate.radiusPx,
    startHeadingDeg: bestCandidate.startHeadingDeg,
    endHeadingDeg: bestCandidate.endHeadingDeg,
    clockwise: bestCandidate.clockwise,
    sampledPoints: sampleCircularArcPointsTs(
      bestCandidate.center,
      bestCandidate.radiusPx,
      bestCandidate.startAngle,
      bestCandidate.endAngle,
      bestCandidate.clockwise,
      startPoint,
      endPoint,
      targetSegmentLengthPx,
    ),
  };
}

export function buildCrossCornerOverlayTs(
  junctionId: string,
  orderedArms: DerivedJunctionOverlayArm[],
  ppm: number,
): {
  quadrantCornerKernels: JunctionOverlayCornerKernel[];
  connectorCenterLines: DerivedJunctionOverlayConnectorLine[];
  cornerStripLinks: JunctionOverlayStripLink[];
  cornerFocusPoints: JunctionOverlayCornerFocus[];
  boundaryExtensionLines: JunctionOverlayGuideLine[];
  focusGuideLines: JunctionOverlayGuideLine[];
} {
  const quadrantCornerKernels: JunctionOverlayCornerKernel[] = [];
  const connectorCenterLines: DerivedJunctionOverlayConnectorLine[] = [];
  const cornerStripLinks: JunctionOverlayStripLink[] = [];
  const cornerFocusPoints: JunctionOverlayCornerFocus[] = [];
  const boundaryExtensionLines: JunctionOverlayGuideLine[] = [];
  const focusGuideLines: JunctionOverlayGuideLine[] = [];
  const targetSegmentLengthPx = Math.max(ppm * 0.75, 1);
  const minRadiusPx = Math.max(ppm * 0.25, 0.25);

  for (let armIndex = 0; armIndex < orderedArms.length; armIndex += 1) {
    const arm = orderedArms[armIndex];
    const nextArm = orderedArms[(armIndex + 1) % orderedArms.length];
    let sweep = nextArm.angleDeg - arm.angleDeg;
    if (sweep <= 0) {
      sweep += 360;
    }
    if (sweep <= 5 || sweep >= 175) {
      continue;
    }
    const cornerCenter = lineIntersectionTs(
      arm.splitBoundaryCenter,
      arm.normal,
      nextArm.splitBoundaryCenter,
      nextArm.normal,
    );
    if (!cornerCenter) {
      continue;
    }
    const quadrantId = `${junctionId}_quadrant_${String(armIndex + 1).padStart(2, "0")}`;
    const kernelId = `${quadrantId}_kernel`;
    const startTangent = { x: -arm.tangent.x, y: -arm.tangent.y };
    const endTangent = clonePoint(nextArm.tangent);

    cornerFocusPoints.push({
      focusId: `${junctionId}_focus_${String(armIndex + 1).padStart(2, "0")}`,
      point: clonePoint(cornerCenter),
    });
    boundaryExtensionLines.push(
      {
        guideId: `${junctionId}_boundary_extension_${String(armIndex + 1).padStart(2, "0")}_a`,
        start: clonePoint(cornerCenter),
        end: clonePoint(arm.splitBoundaryCenter),
      },
      {
        guideId: `${junctionId}_boundary_extension_${String(armIndex + 1).padStart(2, "0")}_b`,
        start: clonePoint(cornerCenter),
        end: clonePoint(nextArm.splitBoundaryCenter),
      },
    );

    let canonicalStartPoint: AnnotationPoint | null = null;
    let canonicalEndPoint: AnnotationPoint | null = null;
    for (const kind of ["clear_sidewalk", "nearroad_furnishing", "frontage_reserve"] as const) {
      const offsetsA = cornerStripOffsetRangeTs(arm, cornerCenter, kind, ppm);
      const offsetsB = cornerStripOffsetRangeTs(nextArm, cornerCenter, kind, ppm);
      if (!offsetsA || !offsetsB) {
        continue;
      }
      canonicalStartPoint = pointOnBoundaryWithOffsetTs(
        arm.splitBoundaryCenter,
        arm.normal,
        offsetsA.centerOffsetPx,
      );
      canonicalEndPoint = pointOnBoundaryWithOffsetTs(
        nextArm.splitBoundaryCenter,
        nextArm.normal,
        offsetsB.centerOffsetPx,
      );
      break;
    }
    if (!canonicalStartPoint || !canonicalEndPoint) {
      continue;
    }

    const canonicalKernel = cornerLaneKernelGeometryTs(
      canonicalStartPoint,
      canonicalEndPoint,
      startTangent,
      endTangent,
      cornerCenter,
      targetSegmentLengthPx,
      minRadiusPx,
    );
    quadrantCornerKernels.push({
      kernelId,
      quadrantId,
      junctionId,
      startCenterlineId: arm.centerlineId,
      endCenterlineId: nextArm.centerlineId,
      kernelKind: canonicalKernel.kernelKind,
      center: clonePoint(canonicalKernel.center),
      radiusPx: canonicalKernel.radiusPx,
      startHeadingDeg: canonicalKernel.startHeadingDeg,
      endHeadingDeg: canonicalKernel.endHeadingDeg,
      clockwise: canonicalKernel.clockwise,
      points: canonicalKernel.sampledPoints.map((point) => clonePoint(point)),
    });

    for (const spec of [
      { kind: "nearroad_furnishing" as const, patchPrefix: "nearroad" },
      { kind: "clear_sidewalk" as const, patchPrefix: "sidewalk" },
      { kind: "frontage_reserve" as const, patchPrefix: "frontage" },
    ]) {
      const offsetsA = cornerStripOffsetRangeTs(arm, cornerCenter, spec.kind, ppm);
      const offsetsB = cornerStripOffsetRangeTs(nextArm, cornerCenter, spec.kind, ppm);
      if (!offsetsA || !offsetsB) {
        continue;
      }
      const centerPointA = pointOnBoundaryWithOffsetTs(arm.splitBoundaryCenter, arm.normal, offsetsA.centerOffsetPx);
      const centerPointB = pointOnBoundaryWithOffsetTs(nextArm.splitBoundaryCenter, nextArm.normal, offsetsB.centerOffsetPx);
      const innerPointA = pointOnBoundaryWithOffsetTs(arm.splitBoundaryCenter, arm.normal, offsetsA.innerOffsetPx);
      const innerPointB = pointOnBoundaryWithOffsetTs(nextArm.splitBoundaryCenter, nextArm.normal, offsetsB.innerOffsetPx);
      const outerPointA = pointOnBoundaryWithOffsetTs(arm.splitBoundaryCenter, arm.normal, offsetsA.outerOffsetPx);
      const outerPointB = pointOnBoundaryWithOffsetTs(nextArm.splitBoundaryCenter, nextArm.normal, offsetsB.outerOffsetPx);
      const strokeWidthPx = Math.max(
        2,
        (Math.abs(offsetsA.outerOffsetPx - offsetsA.innerOffsetPx) + Math.abs(offsetsB.outerOffsetPx - offsetsB.innerOffsetPx)) * 0.5,
      );
      const lineKernel = cornerLaneKernelGeometryTs(
        centerPointA,
        centerPointB,
        startTangent,
        endTangent,
        cornerCenter,
        targetSegmentLengthPx,
        minRadiusPx,
      );
      connectorCenterLines.push({
        connectorId: `${junctionId}_${spec.patchPrefix}_${String(armIndex + 1).padStart(2, "0")}_centerline`,
        stripKind: spec.kind,
        quadrantId,
        kernelId,
        strokeWidthPx,
        points: lineKernel.sampledPoints.map((point) => clonePoint(point)),
      });
      if (offsetsA.stripId && offsetsB.stripId) {
        cornerStripLinks.push({
          linkId: `${junctionId}_${spec.patchPrefix}_${String(armIndex + 1).padStart(2, "0")}_link`,
          junctionId,
          quadrantId,
          kernelId,
          stripKind: spec.kind,
          start: {
            centerlineId: arm.centerlineId,
            stripId: offsetsA.stripId,
            stripKind: spec.kind,
            stripZone: offsetsA.zone,
          },
          end: {
            centerlineId: nextArm.centerlineId,
            stripId: offsetsB.stripId,
            stripKind: spec.kind,
            stripZone: offsetsB.zone,
          },
          points: lineKernel.sampledPoints.map((point) => clonePoint(point)),
          strokeWidthPx,
        });
      }
      focusGuideLines.push(
        {
          guideId: `${junctionId}_${spec.patchPrefix}_${String(armIndex + 1).padStart(2, "0")}_center_a`,
          start: clonePoint(cornerCenter),
          end: clonePoint(centerPointA),
        },
        {
          guideId: `${junctionId}_${spec.patchPrefix}_${String(armIndex + 1).padStart(2, "0")}_center_b`,
          start: clonePoint(cornerCenter),
          end: clonePoint(centerPointB),
        },
        {
          guideId: `${junctionId}_${spec.patchPrefix}_${String(armIndex + 1).padStart(2, "0")}_inner_a`,
          start: clonePoint(cornerCenter),
          end: clonePoint(innerPointA),
        },
        {
          guideId: `${junctionId}_${spec.patchPrefix}_${String(armIndex + 1).padStart(2, "0")}_inner_b`,
          start: clonePoint(cornerCenter),
          end: clonePoint(innerPointB),
        },
        {
          guideId: `${junctionId}_${spec.patchPrefix}_${String(armIndex + 1).padStart(2, "0")}_outer_a`,
          start: clonePoint(cornerCenter),
          end: clonePoint(outerPointA),
        },
        {
          guideId: `${junctionId}_${spec.patchPrefix}_${String(armIndex + 1).padStart(2, "0")}_outer_b`,
          start: clonePoint(cornerCenter),
          end: clonePoint(outerPointB),
        },
      );
    }
  }

  return {
    quadrantCornerKernels,
    connectorCenterLines,
    cornerStripLinks,
    cornerFocusPoints,
    boundaryExtensionLines,
    focusGuideLines,
  };
}

export function shouldTrimOutsideCornerTs(
  kind: "t_junction" | "cross_junction",
  sweepDeg: number,
): boolean {
  void kind;
  return Math.abs(sweepDeg - 90) <= 30;
}

export function cornerConnectorPatchGeometryTs(
  arm: {
    splitBoundaryCenter: AnnotationPoint;
    normal: AnnotationPoint;
    tangent: AnnotationPoint;
  },
  nextArm: {
    splitBoundaryCenter: AnnotationPoint;
    normal: AnnotationPoint;
    tangent: AnnotationPoint;
  },
  offsetsA: { innerOffsetPx: number; outerOffsetPx: number },
  offsetsB: { innerOffsetPx: number; outerOffsetPx: number },
  options: {
    trimOutsideCorner: boolean;
  },
): {
  points: AnnotationPoint[];
  cutoutPoints?: AnnotationPoint[];
} {
  const innerPointA = pointOnBoundaryWithOffsetTs(arm.splitBoundaryCenter, arm.normal, offsetsA.innerOffsetPx);
  const innerPointB = pointOnBoundaryWithOffsetTs(nextArm.splitBoundaryCenter, nextArm.normal, offsetsB.innerOffsetPx);
  const outerPointA = pointOnBoundaryWithOffsetTs(arm.splitBoundaryCenter, arm.normal, offsetsA.outerOffsetPx);
  const outerPointB = pointOnBoundaryWithOffsetTs(nextArm.splitBoundaryCenter, nextArm.normal, offsetsB.outerOffsetPx);
  const innerJoin = connectorJoinPointTs(innerPointA, arm.tangent, innerPointB, nextArm.tangent);
  const outerJoin = connectorJoinPointTs(outerPointA, arm.tangent, outerPointB, nextArm.tangent);
  return options.trimOutsideCorner
    ? {
        points: [outerPointA, outerJoin, outerPointB, innerPointB, innerJoin, innerPointA],
        cutoutPoints: [outerPointA, outerJoin, outerPointB],
      }
    : {
        points: [outerPointA, outerJoin, outerPointB, innerPointB, innerJoin, innerPointA],
      };
}

export function deriveExplicitJunctionOverlayGeometries(annotation: ReferenceAnnotation): DerivedJunctionOverlay[] {
  const ppm = Math.max(annotation.pixels_per_meter, 0.0001);
  const overlays: DerivedJunctionOverlay[] = [];
  const tolerancePx = Math.max(ppm * 0.35, 4);
  for (const junction of annotation.junctions) {
    if (junction.source_mode !== "explicit" || junction.connected_centerline_ids.length < 3) {
      continue;
    }
    const anchor = junctionAnchorPoint(junction);
    const arms: Array<{
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
    }> = [];
    for (const centerlineId of junction.connected_centerline_ids) {
      const centerline = annotation.centerlines.find((item) => item.id === centerlineId);
      if (!centerline || centerline.points.length < 2) {
        continue;
      }
      let neighbor: AnnotationPoint | null = null;
      let reverseOffsets = false;
      if (centerline.start_junction_id === junction.id && pointDistance(centerline.points[0], anchor) <= tolerancePx) {
        neighbor = centerline.points[1];
        reverseOffsets = false;
      } else if (
        centerline.end_junction_id === junction.id &&
        pointDistance(centerline.points[centerline.points.length - 1], anchor) <= tolerancePx
      ) {
        neighbor = centerline.points[centerline.points.length - 2];
        reverseOffsets = true;
      } else {
        const startDistance = pointDistance(centerline.points[0], anchor);
        const endDistance = pointDistance(centerline.points[centerline.points.length - 1], anchor);
        if (startDistance <= endDistance && startDistance <= tolerancePx) {
          neighbor = centerline.points[1];
          reverseOffsets = false;
        } else if (endDistance < startDistance && endDistance <= tolerancePx) {
          neighbor = centerline.points[centerline.points.length - 2];
          reverseOffsets = true;
        }
      }
      if (!neighbor) {
        continue;
      }
      const armLengthPx = pointDistance(anchor, neighbor);
      if (armLengthPx <= 1e-6) {
        continue;
      }
      const tangent = {
        x: (neighbor.x - anchor.x) / armLengthPx,
        y: (neighbor.y - anchor.y) / armLengthPx,
      };
      const widths = junctionProfileWidths(centerline);
      arms.push({
        centerlineId: centerline.id,
        angleDeg: angleDegTs(anchor, neighbor),
        tangent,
        normal: { x: tangent.y, y: -tangent.x },
        reverseOffsets,
        carriagewayWidthPx: widths.carriagewayWidthM * ppm,
        nearroadBufferWidthPx: widths.nearroadBufferWidthM * ppm,
        nearroadFurnishingWidthPx: widths.nearroadFurnishingWidthM * ppm,
        clearSidewalkWidthPx: widths.clearSidewalkWidthM * ppm,
        farfromroadBufferWidthPx: widths.farfromroadBufferWidthM * ppm,
        frontageReserveWidthPx: widths.frontageReserveWidthM * ppm,
        sideStripLayouts: centerlineSideStripLayouts(centerline),
        splitBoundaryCenter: { ...anchor },
      });
    }
    if (arms.length < 3) {
      continue;
    }
    const kind = junction.kind === "cross_junction" || junction.kind === "t_junction"
      ? junction.kind
      : classifyDerivedJunctionKind(arms.map((arm) => arm.angleDeg));
    if (kind !== "t_junction" && kind !== "cross_junction") {
      continue;
    }

    let axisSource = arms[0]?.tangent ?? { x: 1, y: 0 };
    let bestScore = Number.POSITIVE_INFINITY;
    for (let index = 0; index < arms.length; index += 1) {
      for (let otherIndex = index + 1; otherIndex < arms.length; otherIndex += 1) {
        const score = Math.abs(angleDistanceDegTs(arms[index].angleDeg, arms[otherIndex].angleDeg) - 180);
        if (score < bestScore) {
          bestScore = score;
          axisSource = arms[index].tangent;
        }
      }
    }
    const axisLength = Math.max(Math.hypot(axisSource.x, axisSource.y), 1e-6);
    const axisU = { x: axisSource.x / axisLength, y: axisSource.y / axisLength };
    const axisV = { x: -axisU.y, y: axisU.x };
    const axisUAngle = angleDegTs({ x: 0, y: 0 }, axisU);
    const armsOnU: typeof arms = [];
    const armsOnV: typeof arms = [];
    for (const arm of arms) {
      const alongU = axisDistanceDegTs(arm.angleDeg, axisUAngle);
      const alongV = axisDistanceDegTs(arm.angleDeg, axisUAngle + 90);
      if (alongV + 1e-6 < alongU) {
        armsOnV.push(arm);
      } else {
        armsOnU.push(arm);
      }
    }
    const maxHalfWidth = (items: typeof arms, fallback: typeof arms): number => {
      const source = items.length > 0 ? items : fallback;
      return Math.max(...source.map((arm) => Math.max(arm.carriagewayWidthPx * 0.5, 1)));
    };
    const halfUPx = maxHalfWidth(armsOnV, arms);
    const halfVPx = maxHalfWidth(armsOnU, arms);
    const core = [
      { x: anchor.x - axisU.x * halfUPx - axisV.x * halfVPx, y: anchor.y - axisU.y * halfUPx - axisV.y * halfVPx },
      { x: anchor.x - axisU.x * halfUPx + axisV.x * halfVPx, y: anchor.y - axisU.y * halfUPx + axisV.y * halfVPx },
      { x: anchor.x + axisU.x * halfUPx + axisV.x * halfVPx, y: anchor.y + axisU.y * halfUPx + axisV.y * halfVPx },
      { x: anchor.x + axisU.x * halfUPx - axisV.x * halfVPx, y: anchor.y + axisU.y * halfUPx - axisV.y * halfVPx },
    ];

    const approachBoundaries: DerivedJunctionOverlayBoundary[] = [];
    const crosswalks: DerivedJunctionOverlayPatch[] = [];
    const skeletonFootPoints: JunctionOverlayFootPoint[] = [];
    const subLaneControlPoints: JunctionOverlayControlPoint[] = [];
    for (let armIndex = 0; armIndex < arms.length; armIndex += 1) {
      const arm = arms[armIndex];
      const dotU = Math.abs(arm.tangent.x * axisU.x + arm.tangent.y * axisU.y);
      const dotV = Math.abs(arm.tangent.x * axisV.x + arm.tangent.y * axisV.y);
      const coreExitDistancePx = Math.max(
        Math.min(
          dotU > 1e-6 ? halfUPx / dotU : Number.POSITIVE_INFINITY,
          dotV > 1e-6 ? halfVPx / dotV : Number.POSITIVE_INFINITY,
        ),
        1,
      );
      const splitDistancePx = coreExitDistancePx + junction.crosswalk_depth_m * ppm;
      const boundaryCenter = {
        x: anchor.x + arm.tangent.x * splitDistancePx,
        y: anchor.y + arm.tangent.y * splitDistancePx,
      };
      arm.splitBoundaryCenter = boundaryCenter;
      const halfWidth = Math.max(arm.carriagewayWidthPx * 0.5, 1);
      approachBoundaries.push({
        boundaryId: `${junction.id}_approach_${String(armIndex + 1).padStart(2, "0")}`,
        centerlineId: arm.centerlineId,
        start: {
          x: boundaryCenter.x - arm.normal.x * halfWidth,
          y: boundaryCenter.y - arm.normal.y * halfWidth,
        },
        end: {
          x: boundaryCenter.x + arm.normal.x * halfWidth,
          y: boundaryCenter.y + arm.normal.y * halfWidth,
        },
        center: boundaryCenter,
        exitDistancePx: splitDistancePx,
      });
      skeletonFootPoints.push({
        footId: `${junction.id}_foot_${String(armIndex + 1).padStart(2, "0")}`,
        centerlineId: arm.centerlineId,
        point: boundaryCenter,
      });
      for (const zone of ["left", "right"] as StripZone[]) {
        for (const strip of arm.sideStripLayouts[zone]) {
          const pointKinds: Array<[JunctionOverlayControlPoint["pointKind"], number]> = [
            ["station_foot_point", 0],
            ...junctionControlPointOffsetsTs(strip, arm.reverseOffsets, ppm),
          ];
          for (const [pointKind, offsetPx] of pointKinds) {
            subLaneControlPoints.push({
              controlId: `${junction.id}_${arm.centerlineId}_${strip.stripId}_${zone}_${pointKind}`,
              centerlineId: arm.centerlineId,
              stripId: strip.stripId,
              stripKind: strip.kind,
              stripZone: zone,
              pointKind,
              point: {
                x: boundaryCenter.x + arm.normal.x * offsetPx,
                y: boundaryCenter.y + arm.normal.y * offsetPx,
              },
            });
          }
        }
      }
      for (const strip of arm.sideStripLayouts.center) {
        for (const [pointKind, offsetPx] of junctionControlPointOffsetsTs(strip, arm.reverseOffsets, ppm)) {
          subLaneControlPoints.push({
            controlId: `${junction.id}_${arm.centerlineId}_${strip.stripId}_center_${pointKind}`,
            centerlineId: arm.centerlineId,
            stripId: strip.stripId,
            stripKind: strip.kind,
            stripZone: "center",
            pointKind,
            point: {
              x: boundaryCenter.x + arm.normal.x * offsetPx,
              y: boundaryCenter.y + arm.normal.y * offsetPx,
            },
          });
        }
      }
      const crosswalkCenter = {
        x: anchor.x + arm.tangent.x * (coreExitDistancePx + junction.crosswalk_depth_m * ppm * 0.5),
        y: anchor.y + arm.tangent.y * (coreExitDistancePx + junction.crosswalk_depth_m * ppm * 0.5),
      };
      crosswalks.push({
        patchId: `${junction.id}_crosswalk_${String(armIndex + 1).padStart(2, "0")}`,
        points: rectanglePolygonPoints(
          crosswalkCenter,
          arm.tangent,
          arm.normal,
          junction.crosswalk_depth_m * ppm,
          arm.carriagewayWidthPx,
        ),
      });
    }

    const orderedArms = [...arms].sort((a, b) => a.angleDeg - b.angleDeg);
    const sidewalkCorners: DerivedJunctionOverlayPatch[] = [];
    const nearroadCorners: DerivedJunctionOverlayPatch[] = [];
    const frontageCorners: DerivedJunctionOverlayPatch[] = [];
    const quadrantCornerKernels: JunctionOverlayCornerKernel[] = [];
    const connectorCenterLines: DerivedJunctionOverlayConnectorLine[] = [];
    const cornerStripLinks: JunctionOverlayStripLink[] = [];
    const cornerFocusPoints: JunctionOverlayCornerFocus[] = [];
    const boundaryExtensionLines: JunctionOverlayGuideLine[] = [];
    const focusGuideLines: JunctionOverlayGuideLine[] = [];
    if (kind === "cross_junction") {
      const crossCornerData = buildCrossCornerOverlayTs(junction.id, orderedArms, ppm);
      quadrantCornerKernels.push(...crossCornerData.quadrantCornerKernels);
      connectorCenterLines.push(...crossCornerData.connectorCenterLines);
      cornerStripLinks.push(...crossCornerData.cornerStripLinks);
      cornerFocusPoints.push(...crossCornerData.cornerFocusPoints);
      boundaryExtensionLines.push(...crossCornerData.boundaryExtensionLines);
      focusGuideLines.push(...crossCornerData.focusGuideLines);
    } else {
      for (let armIndex = 0; armIndex < orderedArms.length; armIndex += 1) {
        const arm = orderedArms[armIndex];
        const nextArm = orderedArms[(armIndex + 1) % orderedArms.length];
        let sweep = nextArm.angleDeg - arm.angleDeg;
        if (sweep <= 0) {
          sweep += 360;
        }
        if (sweep <= 5 || sweep >= 175) {
          continue;
        }
        const cornerCenter = lineIntersectionTs(arm.splitBoundaryCenter, arm.normal, nextArm.splitBoundaryCenter, nextArm.normal);
        if (!cornerCenter) {
          continue;
        }
        const trimOutsideCorner = shouldTrimOutsideCornerTs(kind, sweep);
        const quadrantId = `${junction.id}_corner_${String(armIndex + 1).padStart(2, "0")}`;
        cornerFocusPoints.push({
          focusId: `${junction.id}_focus_${String(armIndex + 1).padStart(2, "0")}`,
          point: cornerCenter,
        });
        boundaryExtensionLines.push(
          {
            guideId: `${junction.id}_boundary_extension_${String(armIndex + 1).padStart(2, "0")}_a`,
            start: cornerCenter,
            end: arm.splitBoundaryCenter,
          },
          {
            guideId: `${junction.id}_boundary_extension_${String(armIndex + 1).padStart(2, "0")}_b`,
            start: cornerCenter,
            end: nextArm.splitBoundaryCenter,
          },
        );
        for (const spec of [
          { kind: "nearroad_furnishing" as const, bucket: nearroadCorners, patchPrefix: "nearroad" },
          { kind: "clear_sidewalk" as const, bucket: sidewalkCorners, patchPrefix: "sidewalk" },
          { kind: "frontage_reserve" as const, bucket: frontageCorners, patchPrefix: "frontage" },
        ]) {
          const offsetsA = cornerStripOffsetRangeTs(arm, cornerCenter, spec.kind, ppm);
          const offsetsB = cornerStripOffsetRangeTs(nextArm, cornerCenter, spec.kind, ppm);
          if (!offsetsA || !offsetsB) {
            continue;
          }
          const centerPointA = pointOnBoundaryWithOffsetTs(arm.splitBoundaryCenter, arm.normal, offsetsA.centerOffsetPx);
          const centerPointB = pointOnBoundaryWithOffsetTs(nextArm.splitBoundaryCenter, nextArm.normal, offsetsB.centerOffsetPx);
          const innerPointA = pointOnBoundaryWithOffsetTs(arm.splitBoundaryCenter, arm.normal, offsetsA.innerOffsetPx);
          const innerPointB = pointOnBoundaryWithOffsetTs(nextArm.splitBoundaryCenter, nextArm.normal, offsetsB.innerOffsetPx);
          const outerPointA = pointOnBoundaryWithOffsetTs(arm.splitBoundaryCenter, arm.normal, offsetsA.outerOffsetPx);
          const outerPointB = pointOnBoundaryWithOffsetTs(nextArm.splitBoundaryCenter, nextArm.normal, offsetsB.outerOffsetPx);
          const strokeWidthPx = Math.max(
            2,
            (Math.abs(offsetsA.outerOffsetPx - offsetsA.innerOffsetPx) + Math.abs(offsetsB.outerOffsetPx - offsetsB.innerOffsetPx)) * 0.5,
          );
          const linePoints = [centerPointA, connectorJoinPointTs(centerPointA, arm.tangent, centerPointB, nextArm.tangent), centerPointB];
          connectorCenterLines.push({
            connectorId: `${junction.id}_${spec.patchPrefix}_${String(armIndex + 1).padStart(2, "0")}_centerline`,
            stripKind: spec.kind,
            quadrantId,
            kernelId: null,
            strokeWidthPx,
            points: linePoints.map((point) => clonePoint(point)),
          });
          if (offsetsA.stripId && offsetsB.stripId) {
            cornerStripLinks.push({
              linkId: `${junction.id}_${spec.patchPrefix}_${String(armIndex + 1).padStart(2, "0")}_link`,
              junctionId: junction.id,
              quadrantId,
              kernelId: null,
              stripKind: spec.kind,
              start: {
                centerlineId: arm.centerlineId,
                stripId: offsetsA.stripId,
                stripKind: spec.kind,
                stripZone: offsetsA.zone,
              },
              end: {
                centerlineId: nextArm.centerlineId,
                stripId: offsetsB.stripId,
                stripKind: spec.kind,
                stripZone: offsetsB.zone,
              },
              points: linePoints.map((point) => clonePoint(point)),
              strokeWidthPx,
            });
          }
          focusGuideLines.push(
            {
              guideId: `${junction.id}_${spec.patchPrefix}_${String(armIndex + 1).padStart(2, "0")}_center_a`,
              start: cornerCenter,
              end: centerPointA,
            },
            {
              guideId: `${junction.id}_${spec.patchPrefix}_${String(armIndex + 1).padStart(2, "0")}_center_b`,
              start: cornerCenter,
              end: centerPointB,
            },
            {
              guideId: `${junction.id}_${spec.patchPrefix}_${String(armIndex + 1).padStart(2, "0")}_inner_a`,
              start: cornerCenter,
              end: innerPointA,
            },
            {
              guideId: `${junction.id}_${spec.patchPrefix}_${String(armIndex + 1).padStart(2, "0")}_inner_b`,
              start: cornerCenter,
              end: innerPointB,
            },
            {
              guideId: `${junction.id}_${spec.patchPrefix}_${String(armIndex + 1).padStart(2, "0")}_outer_a`,
              start: cornerCenter,
              end: outerPointA,
            },
            {
              guideId: `${junction.id}_${spec.patchPrefix}_${String(armIndex + 1).padStart(2, "0")}_outer_b`,
              start: cornerCenter,
              end: outerPointB,
            },
          );
          const patchGeometry = cornerConnectorPatchGeometryTs(arm, nextArm, offsetsA, offsetsB, {
            trimOutsideCorner,
          });
          if (patchGeometry && patchGeometry.points.length > 0) {
            spec.bucket.push({
              patchId: `${junction.id}_${spec.patchPrefix}_${String(armIndex + 1).padStart(2, "0")}`,
              points: patchGeometry.points,
              cutoutPoints: patchGeometry.cutoutPoints,
            });
          }
        }
      }
    }

    overlays.push({
      junctionId: junction.id,
      kind,
      sourceMode: "explicit",
      core,
      crosswalks,
      sidewalkCorners,
      nearroadCorners,
      frontageCorners,
      approachBoundaries,
      anchor,
      armCount: arms.length,
      connectedCenterlineIds: [...junction.connected_centerline_ids],
      skeletonFootPoints,
      subLaneControlPoints,
      cornerFocusPoints,
      boundaryExtensionLines,
      focusGuideLines,
      quadrantCornerKernels,
      connectorCenterLines,
      cornerStripLinks,
    });
  }
  return overlays;
}

export function deriveLegacyJunctionOverlayGeometries(
  annotation: ReferenceAnnotation,
  previewCenterlines: AnnotatedCenterline[] = [],
): DerivedJunctionOverlay[] {
  const allCenterlines = [...annotation.centerlines, ...previewCenterlines];
  const tolerancePx = Math.max(annotation.pixels_per_meter * 0.35, 4);
  const clusters: Array<{
    point: AnnotationPoint;
    count: number;
    members: Array<{ centerline: AnnotatedCenterline; vertexIndex: number; points: AnnotationPoint[] }>;
  }> = [];

  for (const centerline of allCenterlines) {
    for (let vertexIndex = 0; vertexIndex < centerline.points.length; vertexIndex += 1) {
      const point = centerline.points[vertexIndex];
      let matched = clusters.find((cluster) => pointDistance(cluster.point, point) <= tolerancePx) ?? null;
      if (!matched) {
        matched = { point: { ...point }, count: 0, members: [] };
        clusters.push(matched);
      }
      const count = matched.count + 1;
      matched.point = {
        x: (matched.point.x * matched.count + point.x) / count,
        y: (matched.point.y * matched.count + point.y) / count,
      };
      matched.count = count;
      matched.members.push({
        centerline,
        vertexIndex,
        points: centerline.points.map((item) => ({ ...item })),
      });
    }
  }

  const overlays: DerivedJunctionOverlay[] = [];
  for (let clusterIndex = 0; clusterIndex < clusters.length; clusterIndex += 1) {
    const cluster = clusters[clusterIndex];
    const uniqueCenterlineIds = new Set(cluster.members.map((member) => member.centerline.id));
    if (uniqueCenterlineIds.size < 2) {
      continue;
    }
    const anchor = cluster.point;
    const arms: Array<{
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
    }> = [];
    const seenArmKeys = new Set<string>();
    for (const member of cluster.members) {
      const { centerline, vertexIndex, points } = member;
      const widths = junctionProfileWidths(centerline);
      for (const neighborIndex of [vertexIndex - 1, vertexIndex + 1]) {
        if (neighborIndex < 0 || neighborIndex >= points.length) {
          continue;
        }
        const neighbor = points[neighborIndex];
        const lengthPx = pointDistance(anchor, neighbor);
        if (lengthPx <= Math.max(tolerancePx * 0.25, 1)) {
          continue;
        }
        const armKey = `${centerline.id}:${Math.round(neighbor.x * 100)}:${Math.round(neighbor.y * 100)}`;
        if (seenArmKeys.has(armKey)) {
          continue;
        }
        seenArmKeys.add(armKey);
        const tangent = {
          x: (neighbor.x - anchor.x) / lengthPx,
          y: (neighbor.y - anchor.y) / lengthPx,
        };
        arms.push({
          centerlineId: centerline.id,
          angleDeg: angleDegTs(anchor, neighbor),
          tangent,
          normal: { x: tangent.y, y: -tangent.x },
          reverseOffsets: neighborIndex < vertexIndex,
          carriagewayWidthPx: widths.carriagewayWidthM * annotation.pixels_per_meter,
          nearroadBufferWidthPx: widths.nearroadBufferWidthM * annotation.pixels_per_meter,
          nearroadFurnishingWidthPx: widths.nearroadFurnishingWidthM * annotation.pixels_per_meter,
          clearSidewalkWidthPx: widths.clearSidewalkWidthM * annotation.pixels_per_meter,
          farfromroadBufferWidthPx: widths.farfromroadBufferWidthM * annotation.pixels_per_meter,
          frontageReserveWidthPx: widths.frontageReserveWidthM * annotation.pixels_per_meter,
          sideStripLayouts: centerlineSideStripLayouts(centerline),
          splitBoundaryCenter: { ...anchor },
        });
      }
    }
    const kind = classifyDerivedJunctionKind(arms.map((arm) => arm.angleDeg));
    if (kind !== "t_junction" && kind !== "cross_junction") {
      continue;
    }

    let axisSource = arms[0]?.tangent ?? { x: 1, y: 0 };
    let bestScore = Number.POSITIVE_INFINITY;
    for (let index = 0; index < arms.length; index += 1) {
      for (let otherIndex = index + 1; otherIndex < arms.length; otherIndex += 1) {
        const score = Math.abs(angleDistanceDegTs(arms[index].angleDeg, arms[otherIndex].angleDeg) - 180);
        if (score < bestScore) {
          bestScore = score;
          axisSource = arms[index].tangent;
        }
      }
    }
    const axisLength = Math.max(Math.hypot(axisSource.x, axisSource.y), 1e-6);
    const axisU = { x: axisSource.x / axisLength, y: axisSource.y / axisLength };
    const axisV = { x: -axisU.y, y: axisU.x };
    const axisUAngle = angleDegTs({ x: 0, y: 0 }, axisU);
    const armsOnU: typeof arms = [];
    const armsOnV: typeof arms = [];
    for (const arm of arms) {
      const alongU = axisDistanceDegTs(arm.angleDeg, axisUAngle);
      const alongV = axisDistanceDegTs(arm.angleDeg, axisUAngle + 90);
      if (alongV + 1e-6 < alongU) {
        armsOnV.push(arm);
      } else {
        armsOnU.push(arm);
      }
    }
    const maxHalfWidth = (items: typeof arms, fallback: typeof arms): number => {
      const source = items.length > 0 ? items : fallback;
      return Math.max(...source.map((arm) => Math.max(arm.carriagewayWidthPx * 0.5, 1)));
    };
    const halfUPx = maxHalfWidth(armsOnV, arms);
    const halfVPx = maxHalfWidth(armsOnU, arms);
    const core = [
      { x: anchor.x - axisU.x * halfUPx - axisV.x * halfVPx, y: anchor.y - axisU.y * halfUPx - axisV.y * halfVPx },
      { x: anchor.x - axisU.x * halfUPx + axisV.x * halfVPx, y: anchor.y - axisU.y * halfUPx + axisV.y * halfVPx },
      { x: anchor.x + axisU.x * halfUPx + axisV.x * halfVPx, y: anchor.y + axisU.y * halfUPx + axisV.y * halfVPx },
      { x: anchor.x + axisU.x * halfUPx - axisV.x * halfVPx, y: anchor.y + axisU.y * halfUPx - axisV.y * halfVPx },
    ];

    const approachBoundaries: DerivedJunctionOverlayBoundary[] = [];
    const crosswalks: DerivedJunctionOverlayPatch[] = [];
    const skeletonFootPoints: JunctionOverlayFootPoint[] = [];
    const subLaneControlPoints: JunctionOverlayControlPoint[] = [];
    const crosswalkDepthPx = annotation.pixels_per_meter * 3;
    for (let armIndex = 0; armIndex < arms.length; armIndex += 1) {
      const arm = arms[armIndex];
      const dotU = Math.abs(arm.tangent.x * axisU.x + arm.tangent.y * axisU.y);
      const dotV = Math.abs(arm.tangent.x * axisV.x + arm.tangent.y * axisV.y);
      const coreExitDistancePx = Math.max(
        Math.min(
          dotU > 1e-6 ? halfUPx / dotU : Number.POSITIVE_INFINITY,
          dotV > 1e-6 ? halfVPx / dotV : Number.POSITIVE_INFINITY,
        ),
        1,
      );
      const splitDistancePx = coreExitDistancePx + crosswalkDepthPx;
      const boundaryCenter = {
        x: anchor.x + arm.tangent.x * splitDistancePx,
        y: anchor.y + arm.tangent.y * splitDistancePx,
      };
      arm.splitBoundaryCenter = boundaryCenter;
      const halfWidth = Math.max(arm.carriagewayWidthPx * 0.5, 1);
      approachBoundaries.push({
        boundaryId: `junction_overlay_${clusterIndex + 1}_boundary_${armIndex + 1}`,
        centerlineId: arm.centerlineId,
        start: {
          x: boundaryCenter.x - arm.normal.x * halfWidth,
          y: boundaryCenter.y - arm.normal.y * halfWidth,
        },
        end: {
          x: boundaryCenter.x + arm.normal.x * halfWidth,
          y: boundaryCenter.y + arm.normal.y * halfWidth,
        },
        center: boundaryCenter,
        exitDistancePx: splitDistancePx,
      });
      skeletonFootPoints.push({
        footId: `junction_overlay_${clusterIndex + 1}_foot_${armIndex + 1}`,
        centerlineId: arm.centerlineId,
        point: boundaryCenter,
      });
      for (const zone of ["left", "right"] as StripZone[]) {
        for (const strip of arm.sideStripLayouts[zone]) {
          const pointKinds: Array<[JunctionOverlayControlPoint["pointKind"], number]> = [
            ["station_foot_point", 0],
            ...junctionControlPointOffsetsTs(strip, arm.reverseOffsets, annotation.pixels_per_meter),
          ];
          for (const [pointKind, offsetPx] of pointKinds) {
            subLaneControlPoints.push({
              controlId: `junction_overlay_${clusterIndex + 1}_${arm.centerlineId}_${strip.stripId}_${zone}_${pointKind}`,
              centerlineId: arm.centerlineId,
              stripId: strip.stripId,
              stripKind: strip.kind,
              stripZone: zone,
              pointKind,
              point: {
                x: boundaryCenter.x + arm.normal.x * offsetPx,
                y: boundaryCenter.y + arm.normal.y * offsetPx,
              },
            });
          }
        }
      }
      for (const strip of arm.sideStripLayouts.center) {
        for (const [pointKind, offsetPx] of junctionControlPointOffsetsTs(
          strip,
          arm.reverseOffsets,
          annotation.pixels_per_meter,
        )) {
          subLaneControlPoints.push({
            controlId: `junction_overlay_${clusterIndex + 1}_${arm.centerlineId}_${strip.stripId}_center_${pointKind}`,
            centerlineId: arm.centerlineId,
            stripId: strip.stripId,
            stripKind: strip.kind,
            stripZone: "center",
            pointKind,
            point: {
              x: boundaryCenter.x + arm.normal.x * offsetPx,
              y: boundaryCenter.y + arm.normal.y * offsetPx,
            },
          });
        }
      }
      const crosswalkCenter = {
        x: anchor.x + arm.tangent.x * (coreExitDistancePx + crosswalkDepthPx * 0.5),
        y: anchor.y + arm.tangent.y * (coreExitDistancePx + crosswalkDepthPx * 0.5),
      };
      crosswalks.push({
        patchId: `junction_overlay_${clusterIndex + 1}_crosswalk_${armIndex + 1}`,
        points: rectanglePolygonPoints(
          crosswalkCenter,
          arm.tangent,
          arm.normal,
          crosswalkDepthPx,
          arm.carriagewayWidthPx,
        ),
      });
    }

    const orderedArms = [...arms].sort((a, b) => a.angleDeg - b.angleDeg);
    const sidewalkCorners: DerivedJunctionOverlayPatch[] = [];
    const nearroadCorners: DerivedJunctionOverlayPatch[] = [];
    const frontageCorners: DerivedJunctionOverlayPatch[] = [];
    const quadrantCornerKernels: JunctionOverlayCornerKernel[] = [];
    const connectorCenterLines: DerivedJunctionOverlayConnectorLine[] = [];
    const cornerStripLinks: JunctionOverlayStripLink[] = [];
    const cornerFocusPoints: JunctionOverlayCornerFocus[] = [];
    const boundaryExtensionLines: JunctionOverlayGuideLine[] = [];
    const focusGuideLines: JunctionOverlayGuideLine[] = [];
    const overlayJunctionId = `junction_overlay_${String(clusterIndex + 1).padStart(2, "0")}`;
    if (kind === "cross_junction") {
      const crossCornerData = buildCrossCornerOverlayTs(overlayJunctionId, orderedArms, annotation.pixels_per_meter);
      quadrantCornerKernels.push(...crossCornerData.quadrantCornerKernels);
      connectorCenterLines.push(...crossCornerData.connectorCenterLines);
      cornerStripLinks.push(...crossCornerData.cornerStripLinks);
      cornerFocusPoints.push(...crossCornerData.cornerFocusPoints);
      boundaryExtensionLines.push(...crossCornerData.boundaryExtensionLines);
      focusGuideLines.push(...crossCornerData.focusGuideLines);
    } else {
      for (let armIndex = 0; armIndex < orderedArms.length; armIndex += 1) {
        const arm = orderedArms[armIndex];
        const nextArm = orderedArms[(armIndex + 1) % orderedArms.length];
        let sweep = nextArm.angleDeg - arm.angleDeg;
        if (sweep <= 0) {
          sweep += 360;
        }
        if (sweep <= 5 || sweep >= 175) {
          continue;
        }
        const cornerCenter = lineIntersectionTs(arm.splitBoundaryCenter, arm.normal, nextArm.splitBoundaryCenter, nextArm.normal);
        if (!cornerCenter) {
          continue;
        }
        const trimOutsideCorner = shouldTrimOutsideCornerTs(kind, sweep);
        const quadrantId = `${overlayJunctionId}_corner_${String(armIndex + 1).padStart(2, "0")}`;
        cornerFocusPoints.push({
          focusId: `junction_overlay_${clusterIndex + 1}_focus_${armIndex + 1}`,
          point: cornerCenter,
        });
        boundaryExtensionLines.push(
          {
            guideId: `junction_overlay_${clusterIndex + 1}_boundary_extension_${armIndex + 1}_a`,
            start: cornerCenter,
            end: arm.splitBoundaryCenter,
          },
          {
            guideId: `junction_overlay_${clusterIndex + 1}_boundary_extension_${armIndex + 1}_b`,
            start: cornerCenter,
            end: nextArm.splitBoundaryCenter,
          },
        );
        for (const spec of [
          { kind: "nearroad_furnishing" as const, bucket: nearroadCorners, patchPrefix: "nearroad" },
          { kind: "clear_sidewalk" as const, bucket: sidewalkCorners, patchPrefix: "sidewalk" },
          { kind: "frontage_reserve" as const, bucket: frontageCorners, patchPrefix: "frontage" },
        ]) {
          const offsetsA = cornerStripOffsetRangeTs(arm, cornerCenter, spec.kind, annotation.pixels_per_meter);
          const offsetsB = cornerStripOffsetRangeTs(nextArm, cornerCenter, spec.kind, annotation.pixels_per_meter);
          if (!offsetsA || !offsetsB) {
            continue;
          }
          const centerPointA = pointOnBoundaryWithOffsetTs(arm.splitBoundaryCenter, arm.normal, offsetsA.centerOffsetPx);
          const centerPointB = pointOnBoundaryWithOffsetTs(nextArm.splitBoundaryCenter, nextArm.normal, offsetsB.centerOffsetPx);
          const innerPointA = pointOnBoundaryWithOffsetTs(arm.splitBoundaryCenter, arm.normal, offsetsA.innerOffsetPx);
          const innerPointB = pointOnBoundaryWithOffsetTs(nextArm.splitBoundaryCenter, nextArm.normal, offsetsB.innerOffsetPx);
          const outerPointA = pointOnBoundaryWithOffsetTs(arm.splitBoundaryCenter, arm.normal, offsetsA.outerOffsetPx);
          const outerPointB = pointOnBoundaryWithOffsetTs(nextArm.splitBoundaryCenter, nextArm.normal, offsetsB.outerOffsetPx);
          const strokeWidthPx = Math.max(
            2,
            (Math.abs(offsetsA.outerOffsetPx - offsetsA.innerOffsetPx) + Math.abs(offsetsB.outerOffsetPx - offsetsB.innerOffsetPx)) * 0.5,
          );
          const linePoints = [centerPointA, connectorJoinPointTs(centerPointA, arm.tangent, centerPointB, nextArm.tangent), centerPointB];
          connectorCenterLines.push({
            connectorId: `junction_overlay_${clusterIndex + 1}_${spec.patchPrefix}_${armIndex + 1}_centerline`,
            stripKind: spec.kind,
            quadrantId,
            kernelId: null,
            strokeWidthPx,
            points: linePoints.map((point) => clonePoint(point)),
          });
          if (offsetsA.stripId && offsetsB.stripId) {
            cornerStripLinks.push({
              linkId: `junction_overlay_${clusterIndex + 1}_${spec.patchPrefix}_${armIndex + 1}_link`,
              junctionId: overlayJunctionId,
              quadrantId,
              kernelId: null,
              stripKind: spec.kind,
              start: {
                centerlineId: arm.centerlineId,
                stripId: offsetsA.stripId,
                stripKind: spec.kind,
                stripZone: offsetsA.zone,
              },
              end: {
                centerlineId: nextArm.centerlineId,
                stripId: offsetsB.stripId,
                stripKind: spec.kind,
                stripZone: offsetsB.zone,
              },
              points: linePoints.map((point) => clonePoint(point)),
              strokeWidthPx,
            });
          }
          focusGuideLines.push(
            {
              guideId: `junction_overlay_${clusterIndex + 1}_${spec.patchPrefix}_${armIndex + 1}_center_a`,
              start: cornerCenter,
              end: centerPointA,
            },
            {
              guideId: `junction_overlay_${clusterIndex + 1}_${spec.patchPrefix}_${armIndex + 1}_center_b`,
              start: cornerCenter,
              end: centerPointB,
            },
            {
              guideId: `junction_overlay_${clusterIndex + 1}_${spec.patchPrefix}_${armIndex + 1}_inner_a`,
              start: cornerCenter,
              end: innerPointA,
            },
            {
              guideId: `junction_overlay_${clusterIndex + 1}_${spec.patchPrefix}_${armIndex + 1}_inner_b`,
              start: cornerCenter,
              end: innerPointB,
            },
            {
              guideId: `junction_overlay_${clusterIndex + 1}_${spec.patchPrefix}_${armIndex + 1}_outer_a`,
              start: cornerCenter,
              end: outerPointA,
            },
            {
              guideId: `junction_overlay_${clusterIndex + 1}_${spec.patchPrefix}_${armIndex + 1}_outer_b`,
              start: cornerCenter,
              end: outerPointB,
            },
          );
          const patchGeometry = cornerConnectorPatchGeometryTs(arm, nextArm, offsetsA, offsetsB, {
            trimOutsideCorner,
          });
          if (patchGeometry && patchGeometry.points.length > 0) {
            spec.bucket.push({
              patchId: `junction_overlay_${clusterIndex + 1}_${spec.patchPrefix}_${armIndex + 1}`,
              points: patchGeometry.points,
              cutoutPoints: patchGeometry.cutoutPoints,
            });
          }
        }
      }
    }

    overlays.push({
      junctionId: overlayJunctionId,
      kind,
      sourceMode: "derived",
      core,
      crosswalks,
      sidewalkCorners,
      nearroadCorners,
      frontageCorners,
      approachBoundaries,
      anchor: { ...anchor },
      armCount: arms.length,
      connectedCenterlineIds: [...new Set(arms.map((arm) => arm.centerlineId))],
      skeletonFootPoints,
      subLaneControlPoints,
      cornerFocusPoints,
      boundaryExtensionLines,
      focusGuideLines,
      quadrantCornerKernels,
      connectorCenterLines,
      cornerStripLinks,
    });
  }
  return overlays;
}

export function deriveJunctionOverlayGeometries(
  annotation: ReferenceAnnotation,
  previewCenterlines: AnnotatedCenterline[] = [],
): DerivedJunctionOverlay[] {
  const explicitOverlays = previewCenterlines.length === 0 ? deriveExplicitJunctionOverlayGeometries(annotation) : [];
  const legacyOverlays = deriveLegacyJunctionOverlayGeometries(annotation, previewCenterlines).filter((overlay) =>
    !explicitOverlays.some((item) => pointDistance(item.anchor, overlay.anchor) <= Math.max(annotation.pixels_per_meter * 0.5, 6)),
  );
  return [...explicitOverlays, ...legacyOverlays];
}

export function derivedJunctionKindLabel(kind: "t_junction" | "cross_junction"): string {
  return kind === "cross_junction" ? "Cross Junction" : "T Junction";
}

export function getJunctionOverlay(
  annotation: ReferenceAnnotation,
  junctionId: string,
): DerivedJunctionOverlay | null {
  return deriveJunctionOverlayGeometries(annotation).find((item) => item.junctionId === junctionId) ?? null;
}

export function stripDisplayPoint(
  centerline: AnnotatedCenterline,
  stripId: string,
  stationPx: number,
  lateralPx = 0,
  pixelsPerMeter: number,
): AnnotationPoint | null {
  const offsets = stripCenterOffsetMeters(centerline);
  const strip = offsets[stripId];
  if (!strip) {
    return null;
  }
  const sample = stationToPolylinePoint(centerline.points, stationPx);
  const offsetPx = (strip.centerOffsetM * pixelsPerMeter) + lateralPx;
  return {
    x: sample.point.x + sample.leftNormal.x * offsetPx,
    y: sample.point.y + sample.leftNormal.y * offsetPx,
  };
}
