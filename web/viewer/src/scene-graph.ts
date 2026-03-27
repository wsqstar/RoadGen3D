type AnnotationPoint = {
  x: number;
  y: number;
};

type AnnotatedCenterline = {
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
};

type LaneProfile = {
  forward_drive_lane_count: number;
  reverse_drive_lane_count: number;
  bike_lane_count: number;
  bus_lane_count: number;
  parking_lane_count: number;
  total_drive_lane_count: number;
  total_lane_count: number;
};

type AnnotatedMarker = {
  id: string;
  label: string;
  x: number;
  y: number;
  kind: string;
};

type AnnotatedRoundabout = {
  id: string;
  label: string;
  x: number;
  y: number;
  radius_px: number;
};

type ReferenceAnnotation = {
  version: string;
  plan_id: string;
  image_path: string;
  image_width_px: number;
  image_height_px: number;
  pixels_per_meter: number;
  centerlines: AnnotatedCenterline[];
  junctions: AnnotatedMarker[];
  roundabouts: AnnotatedRoundabout[];
  control_points: AnnotatedMarker[];
};

type ReferencePlan = {
  plan_id: string;
  label: string;
  description?: string;
  image_url?: string;
};

type ReferencePlansPayload = {
  items?: ReferencePlan[];
};

type ConvertedGraphPayload = {
  annotation: ReferenceAnnotation;
  graph: {
    mode: string;
    nodes: Array<Record<string, unknown>>;
    edges: Array<Record<string, unknown>>;
  };
  road_profiles?: Array<Record<string, unknown>>;
  summary: Record<string, unknown>;
};

type Tool = "select" | "centerline" | "junction" | "roundabout" | "control_point";

type Selection =
  | {
      kind: "centerline";
      id: string;
      vertexIndex?: number;
    }
  | {
      kind: "junction" | "roundabout" | "control_point";
      id: string;
    }
  | null;

type DragState =
  | {
      kind: "centerline_vertex";
      id: string;
      vertexIndex: number;
      pointerId: number;
    }
  | {
      kind: "marker";
      markerKind: "junction" | "roundabout" | "control_point";
      id: string;
      pointerId: number;
    }
  | null;

type StatusTone = "neutral" | "success" | "error";

const API_BASE = (import.meta.env.VITE_ROADGEN_API_BASE as string | undefined) || "http://127.0.0.1:8010";
const ANNOTATION_SCHEMA_VERSION = "roadgen3d_reference_annotation_v1";
const DEFAULT_PIXELS_PER_METER = 8;
const DEFAULT_SIDEWALK_WIDTH_M = 3;
const DEFAULT_SEGMENT_LENGTH_M = 12;
const DEFAULT_ROAD_WIDTH_M = 12;
const DEFAULT_ROUNDABOUT_RADIUS_PX = 36;
const DEFAULT_FORWARD_DRIVE_LANE_COUNT = 1;
const DEFAULT_REVERSE_DRIVE_LANE_COUNT = 1;

function asNullableNumber(value: unknown): number | null {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value === "string" && !value.trim()) {
    return null;
  }
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function asNonNegativeInt(value: unknown, fallback: number): number {
  return Math.max(0, Math.round(asNumber(value, fallback)));
}

function resolveDriveLaneDefaults(record: Record<string, unknown>): {
  forward_drive_lane_count: number;
  reverse_drive_lane_count: number;
} {
  const legacyLaneCount = Math.max(1, Math.round(asNumber(record.lane_count, 2)));
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

function laneProfile(centerline: AnnotatedCenterline): LaneProfile {
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
    total_drive_lane_count: forward + reverse,
    total_lane_count: forward + reverse + bike + bus + parking,
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
  return Math.max(centerline.road_width_m * Math.max(pixelsPerMeter, 0.0001), 2);
}

function formatLaneSummary(centerline: AnnotatedCenterline): string {
  const profile = laneProfile(centerline);
  const parts = [`drive ${profile.forward_drive_lane_count}/${profile.reverse_drive_lane_count}`];
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

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function asNumber(value: unknown, fallback: number): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
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
  const referenceWidthPx = asNullableNumber(record.reference_width_px);
  return {
    id: asString(record.id, `centerline_${String(index + 1).padStart(2, "0")}`),
    label: asString(record.label, asString(record.id, `Centerline ${index + 1}`)),
    points: rawPoints.map((item) => normalizePoint(item)),
    road_width_m: Math.max(1, asNumber(record.road_width_m, DEFAULT_ROAD_WIDTH_M)),
    reference_width_px: referenceWidthPx === null ? null : Math.max(1, referenceWidthPx),
    forward_drive_lane_count: driveLaneDefaults.forward_drive_lane_count,
    reverse_drive_lane_count: driveLaneDefaults.reverse_drive_lane_count,
    bike_lane_count: asNonNegativeInt(record.bike_lane_count, 0),
    bus_lane_count: asNonNegativeInt(record.bus_lane_count, 0),
    parking_lane_count: asNonNegativeInt(record.parking_lane_count, 0),
    highway_type: asString(record.highway_type, "annotated_centerline"),
  };
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

function normalizeAnnotation(value: unknown): ReferenceAnnotation {
  const record = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const centerlines = Array.isArray(record.centerlines)
    ? record.centerlines.map((item, index) => normalizeCenterline(item, index))
    : [];
  const junctions = Array.isArray(record.junctions)
    ? record.junctions.map((item, index) => normalizeMarker(item, index, "intersection"))
    : [];
  const roundabouts = Array.isArray(record.roundabouts)
    ? record.roundabouts.map((item, index) => normalizeRoundabout(item, index))
    : [];
  const controlPoints = Array.isArray(record.control_points)
    ? record.control_points.map((item, index) => normalizeMarker(item, index, "control_point"))
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
  };
}

function stringifyAnnotation(annotation: ReferenceAnnotation): string {
  return JSON.stringify(annotation, null, 2);
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
    annotation.control_points.length
  );
}

function getSelectedFeature(annotation: ReferenceAnnotation, selection: Selection):
  | AnnotatedCenterline
  | AnnotatedMarker
  | AnnotatedRoundabout
  | null {
  if (!selection) {
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
  return annotation.control_points.find((item) => item.id === selection.id) ?? null;
}

function buildAnnotationSummaryMarkup(annotation: ReferenceAnnotation): string {
  const roadCount = annotation.centerlines.length;
  const roadWidths = annotation.centerlines.map((item) => item.road_width_m);
  const referenceWidths = annotation.centerlines.map((item) => getDisplayReferenceWidthPx(item, annotation.pixels_per_meter));
  const driveLaneTotal = annotation.centerlines.reduce(
    (sum, item) => sum + laneProfile(item).total_drive_lane_count,
    0,
  );
  const bikeLaneTotal = annotation.centerlines.reduce((sum, item) => sum + item.bike_lane_count, 0);
  const busLaneTotal = annotation.centerlines.reduce((sum, item) => sum + item.bus_lane_count, 0);
  const parkingLaneTotal = annotation.centerlines.reduce((sum, item) => sum + item.parking_lane_count, 0);
  return `
    <div>
      <span class="scene-metric-label">Roads</span>
      <strong>${roadCount}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Junctions</span>
      <strong>${annotation.junctions.length}</strong>
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
      <span class="scene-metric-label">Junction Segments</span>
      <strong>${escapeHtml(String(summary.junction_segment_count ?? 0))}</strong>
    </div>
    <div>
      <span class="scene-metric-label">Min Width</span>
      <strong>${escapeHtml(Number(summary.min_road_width_m ?? 0).toFixed(1))}m</strong>
    </div>
    <div>
      <span class="scene-metric-label">Max Width</span>
      <strong>${escapeHtml(Number(summary.max_road_width_m ?? 0).toFixed(1))}m</strong>
    </div>
    <div>
      <span class="scene-metric-label">Avg Width</span>
      <strong>${escapeHtml(Number(summary.avg_road_width_m ?? 0).toFixed(1))}m</strong>
    </div>
    <div>
      <span class="scene-metric-label">Edges</span>
      <strong>${escapeHtml(String(summary.edge_count ?? 0))}</strong>
    </div>
  `;
}

function buildFeatureTableMarkup(annotation: ReferenceAnnotation): string {
  const rows: string[] = [];
  for (const centerline of annotation.centerlines) {
    rows.push(`
      <tr>
        <td>centerline</td>
        <td>${escapeHtml(centerline.id)}</td>
        <td>${escapeHtml(centerline.label)}</td>
        <td>${centerline.points.length} pts · ${centerline.road_width_m.toFixed(1)}m · ${getDisplayReferenceWidthPx(centerline, annotation.pixels_per_meter).toFixed(0)}px · ${escapeHtml(formatLaneSummary(centerline))}</td>
      </tr>
    `);
  }
  for (const item of annotation.junctions) {
    rows.push(`
      <tr>
        <td>junction</td>
        <td>${escapeHtml(item.id)}</td>
        <td>${escapeHtml(item.label)}</td>
        <td>${escapeHtml(item.kind)} · (${item.x.toFixed(0)}, ${item.y.toFixed(0)})</td>
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
  return rows.join("");
}

function buildInspectorMarkup(annotation: ReferenceAnnotation, selection: Selection): string {
  if (!selection) {
    return `<div class="scene-empty-note">选择一条中心线、路口、环岛或控制点后，可以在这里编辑属性。</div>`;
  }
  const feature = getSelectedFeature(annotation, selection);
  if (!feature) {
    return `<div class="scene-empty-note">当前选择的要素已经不存在。</div>`;
  }
  if (selection.kind === "centerline") {
    const centerline = feature as AnnotatedCenterline;
    const referenceWidthMeters = getReferenceWidthMeters(centerline, annotation.pixels_per_meter);
    const profile = laneProfile(centerline);
    return `
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
          <span>Road Width (m)</span>
          <input id="annotation-inspector-road-width" type="number" min="1" step="0.5" value="${centerline.road_width_m}" />
        </label>
        <label class="scene-form-field">
          <span>Reference Width (px)</span>
          <input id="annotation-inspector-reference-width" type="number" min="1" step="1" placeholder="auto" value="${centerline.reference_width_px === null ? "" : centerline.reference_width_px.toFixed(0)}" />
        </label>
        <label class="scene-form-field">
          <span>Forward Drive</span>
          <input id="annotation-inspector-forward-drive-lanes" type="number" min="0" step="1" value="${centerline.forward_drive_lane_count}" />
        </label>
        <label class="scene-form-field">
          <span>Reverse Drive</span>
          <input id="annotation-inspector-reverse-drive-lanes" type="number" min="0" step="1" value="${centerline.reverse_drive_lane_count}" />
        </label>
        <label class="scene-form-field">
          <span>Bike Lanes</span>
          <input id="annotation-inspector-bike-lanes" type="number" min="0" step="1" value="${centerline.bike_lane_count}" />
        </label>
        <label class="scene-form-field">
          <span>Bus Lanes</span>
          <input id="annotation-inspector-bus-lanes" type="number" min="0" step="1" value="${centerline.bus_lane_count}" />
        </label>
        <label class="scene-form-field">
          <span>Parking Lanes</span>
          <input id="annotation-inspector-parking-lanes" type="number" min="0" step="1" value="${centerline.parking_lane_count}" />
        </label>
        <label class="scene-form-field scene-form-field-wide">
          <span>Highway Type</span>
          <input id="annotation-inspector-highway-type" type="text" value="${escapeHtml(centerline.highway_type)}" />
        </label>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Reference Width (m)</span>
          <strong>${referenceWidthMeters === null ? "auto" : referenceWidthMeters.toFixed(2)}</strong>
        </div>
        <div class="scene-fact-card">
          <span class="scene-fact-label">Lane Summary</span>
          <strong>${profile.total_drive_lane_count} drive · ${profile.total_lane_count} total</strong>
        </div>
        <div class="scene-fact-card scene-form-field-wide">
          <span class="scene-fact-label">Geometry</span>
          <strong>${centerline.points.length} vertices${selection.vertexIndex !== undefined ? ` · selected vertex ${selection.vertexIndex + 1}` : ""}</strong>
        </div>
      </div>
    `;
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

function buildOverlayMarkup(
  annotation: ReferenceAnnotation,
  draftCenterline: AnnotationPoint[],
  selection: Selection,
): string {
  const width = Math.max(annotation.image_width_px, 1);
  const height = Math.max(annotation.image_height_px, 1);
  const selectedKey = selection ? `${selection.kind}:${selection.id}` : "";

  const centerlineMarkup = annotation.centerlines
    .map((centerline) => {
      const isSelected = selectedKey === `centerline:${centerline.id}`;
      const selectedVertexIndex =
        selection && selection.kind === "centerline" && selection.id === centerline.id
          ? selection.vertexIndex
          : undefined;
      const roadBandWidthPx = getDisplayReferenceWidthPx(centerline, annotation.pixels_per_meter);
      const points = centerline.points.map((point) => `${point.x},${point.y}`).join(" ");
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
      return `
        <g class="annotation-feature-group">
          <polyline
            class="annotation-road-band${isSelected ? " annotation-feature-selected" : ""}"
            points="${points}"
            style="stroke-width: ${roadBandWidthPx}px"
            data-feature-kind="centerline"
            data-feature-id="${escapeHtml(centerline.id)}"
          />
          <polyline
            class="annotation-centerline${isSelected ? " annotation-feature-selected" : ""}"
            points="${points}"
            style="stroke-width: 3px"
            data-feature-kind="centerline"
            data-feature-id="${escapeHtml(centerline.id)}"
          />
          ${vertexMarkup}
          <text class="annotation-label" x="${centerline.points[0]?.x ?? 0}" y="${(centerline.points[0]?.y ?? 0) - 12}">
            ${escapeHtml(centerline.label || centerline.id)}
          </text>
        </g>
      `;
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

  const draftMarkup =
    draftCenterline.length > 0
      ? `
        <g class="annotation-feature-group">
          <polyline
            class="annotation-centerline annotation-centerline-draft"
            points="${draftCenterline.map((point) => `${point.x},${point.y}`).join(" ")}"
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

  return `
    <svg
      id="annotation-overlay-svg"
      class="annotation-overlay-svg"
      viewBox="0 0 ${width} ${height}"
      role="img"
      aria-label="Reference annotation overlay"
    >
      <rect x="0" y="0" width="${width}" height="${height}" fill="transparent" />
      ${centerlineMarkup}
      ${markerMarkup}
      ${roundaboutMarkup}
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
            载入参考图后，手工标注中心线、路口、环岛和关键控制点，导出 JSON，并直接调用后端转换成可复用的道路 graph。
          </p>
        </div>
        <div class="scene-page-actions">
          <button id="scene-page-back" class="viewer-nav-button" type="button">Back to Viewer</button>
        </div>
      </div>

      <div class="scene-page-layout">
        <section class="scene-panel scene-panel-canvas">
          <div class="scene-panel-header">
            <h2>Reference Board</h2>
            <p>先选参考图，再用工具在图上画中心线和关键节点。拖拽顶点或点位即可微调。</p>
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
            <button id="annotation-tool-centerline" class="scene-tool-button" data-tool="centerline" type="button">Centerline</button>
            <button id="annotation-tool-junction" class="scene-tool-button" data-tool="junction" type="button">Junction</button>
            <button id="annotation-tool-roundabout" class="scene-tool-button" data-tool="roundabout" type="button">Roundabout</button>
            <button id="annotation-tool-control-point" class="scene-tool-button" data-tool="control_point" type="button">Control Point</button>
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
            <button id="annotation-undo-point" class="scene-toolbar-button scene-toolbar-button-secondary" type="button">Undo Point</button>
            <button id="annotation-delete-selected" class="scene-toolbar-button scene-toolbar-button-secondary" type="button">Delete Selected</button>
            <button id="annotation-reset" class="scene-toolbar-button scene-toolbar-button-secondary" type="button">Reset Annotation</button>
          </div>

          <div id="annotation-image-meta" class="scene-image-meta">
            选择参考 plan 或导入 PNG 后，就可以在图上开始标注。
          </div>

          <div id="annotation-stage" class="scene-layer-stage" data-has-image="false">
            <div id="annotation-stage-empty" class="scene-image-empty">
              Load a reference plan image to start annotating.
            </div>
            <div id="annotation-board" class="scene-board" hidden>
              <img id="annotation-original-image" class="scene-original-image annotation-original-image" alt="Reference plan" />
              <div id="annotation-overlay-host" class="scene-graph-overlay"></div>
            </div>
          </div>
        </section>

        <aside class="scene-sidebar">
          <section class="scene-panel">
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

          <section class="scene-panel scene-metrics">
            <div class="scene-panel-header">
              <h2>Annotation Summary</h2>
              <p>当前手工标注的统计概览。</p>
            </div>
            <div id="annotation-summary-grid" class="scene-metric-grid"></div>
          </section>

          <section class="scene-panel">
            <div class="scene-panel-header">
              <h2>Selected Feature</h2>
              <p>修改当前要素的属性，中心线顶点可直接拖拽。</p>
            </div>
            <div id="annotation-inspector" class="scene-inspector-wrap"></div>
          </section>

          <section class="scene-panel">
            <div class="scene-panel-header">
              <h2>Graph Conversion</h2>
              <p>把当前 annotation JSON 直接送进后端 converter，生成可复用的 segment graph。</p>
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

          <section class="scene-panel">
            <div class="scene-panel-header">
              <h2>Feature Table</h2>
              <p>快速检查当前所有要素及其核心属性。</p>
            </div>
            <div class="scene-table-wrap">
              <table class="scene-table">
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
        </aside>
      </div>
    </div>
  `;

  const backButton = requireElement<HTMLButtonElement>(root, "#scene-page-back");
  const planSelect = requireElement<HTMLSelectElement>(root, "#annotation-plan-select");
  const imageInput = requireElement<HTMLInputElement>(root, "#annotation-image-input");
  const imageResetButton = requireElement<HTMLButtonElement>(root, "#annotation-image-reset");
  const showOriginalInput = requireElement<HTMLInputElement>(root, "#annotation-show-original");
  const showOverlayInput = requireElement<HTMLInputElement>(root, "#annotation-show-overlay");
  const originalOpacityInput = requireElement<HTMLInputElement>(root, "#annotation-original-opacity");
  const overlayOpacityInput = requireElement<HTMLInputElement>(root, "#annotation-overlay-opacity");
  const pixelsPerMeterInput = requireElement<HTMLInputElement>(root, "#annotation-pixels-per-meter");
  const roundaboutRadiusInput = requireElement<HTMLInputElement>(root, "#annotation-roundabout-radius");
  const finishCenterlineButton = requireElement<HTMLButtonElement>(root, "#annotation-finish-centerline");
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

  const toolButtons = Array.from(root.querySelectorAll<HTMLButtonElement>(".scene-tool-button"));

  const state = {
    referencePlans: [] as ReferencePlan[],
    annotation: createEmptyAnnotation(),
    draftCenterline: [] as AnnotationPoint[],
    selectedTool: "select" as Tool,
    selection: null as Selection,
    drag: null as DragState,
    currentImageUrl: "",
    currentObjectUrl: "",
    graphResult: null as ConvertedGraphPayload | null,
    showOriginal: true,
    showOverlay: true,
    originalOpacity: 1,
    overlayOpacity: 0.88,
    defaultRoundaboutRadiusPx: DEFAULT_ROUNDABOUT_RADIUS_PX,
  };

  function clearGraphResult(reason: string): void {
    state.graphResult = null;
    graphTextarea.value = "";
    graphSummaryEl.innerHTML = buildGraphSummaryMarkup(null);
    setStatus(graphStatusEl, reason, "neutral");
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
    boardEl.hidden = !hasImage;
    stageEmptyEl.hidden = hasImage;
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

  function renderInspector(): void {
    inspectorEl.innerHTML = buildInspectorMarkup(state.annotation, state.selection);
    const selectedFeature = getSelectedFeature(state.annotation, state.selection);
    if (!selectedFeature || !state.selection) {
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

    const updateSelection = (): void => {
      const feature = getSelectedFeature(state.annotation, state.selection);
      if (!feature || !state.selection) {
        return;
      }
      if (idInput) {
        const nextId = idInput.value.trim();
        if (nextId) {
          if ("id" in feature) {
            feature.id = nextId;
            state.selection.id = nextId;
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
      clearGraphResult("Annotation changed. Re-run convert to refresh graph output.");
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
  }

  function renderOverlay(): void {
    if (!state.currentImageUrl || state.annotation.image_width_px <= 0 || state.annotation.image_height_px <= 0) {
      overlayHostEl.innerHTML = "";
      updateStageVisibility();
      return;
    }
    overlayHostEl.innerHTML = buildOverlayMarkup(state.annotation, state.draftCenterline, state.selection);
    updateStageVisibility();
  }

  function renderAll(): void {
    renderToolButtons();
    summaryGridEl.innerHTML = buildAnnotationSummaryMarkup(state.annotation);
    featureTableEl.innerHTML = buildFeatureTableMarkup(state.annotation);
    graphSummaryEl.innerHTML = buildGraphSummaryMarkup(state.graphResult);
    graphTextarea.value = state.graphResult ? JSON.stringify(state.graphResult, null, 2) : "";
    pixelsPerMeterInput.value = String(state.annotation.pixels_per_meter);
    roundaboutRadiusInput.value = String(state.defaultRoundaboutRadiusPx);
    syncJsonTextarea();
    renderInspector();
    renderOverlay();
    imageMetaEl.textContent = state.currentImageUrl
      ? `${state.annotation.plan_id || "custom"} · ${state.annotation.image_width_px} × ${state.annotation.image_height_px}px · ${state.annotation.pixels_per_meter.toFixed(1)} px/m · ${state.annotation.centerlines.length} roads · ${getFeatureCount(state.annotation)} features`
      : "选择参考 plan 或导入 PNG 后，就可以在图上开始标注。";
    finishCenterlineButton.disabled = state.draftCenterline.length < 2;
    undoPointButton.disabled = state.draftCenterline.length === 0;
    deleteSelectedButton.disabled = !state.selection;
    imageResetButton.disabled = !state.currentImageUrl;
    downloadGraphButton.disabled = !state.graphResult;
  }

  function setTool(tool: Tool): void {
    state.selectedTool = tool;
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
    if (featureKind === "junction" || featureKind === "roundabout" || featureKind === "control_point") {
      return { kind: featureKind, id: featureId };
    }
    return null;
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
    state.currentImageUrl = resolvedImageUrl;
    await new Promise<void>((resolve, reject) => {
      originalImageEl.onload = () => resolve();
      originalImageEl.onerror = () => reject(new Error("Failed to load the selected image."));
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
    state.draftCenterline = [];
    clearGraphResult("Reference image updated. Convert again after annotating.");
    setStatus(statusEl, `Loaded reference image: ${planId || "custom"}.`, "success");
    renderAll();
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

  async function loadReferencePlans(): Promise<void> {
    const response = await fetch(`${API_BASE}/api/reference-plans`);
    if (!response.ok) {
      throw new Error(`Failed to load reference plans (${response.status}).`);
    }
    const payload = (await response.json()) as ReferencePlansPayload;
    state.referencePlans = Array.isArray(payload.items) ? payload.items : [];
    const options = [
      `<option value="">Choose a reference plan</option>`,
      ...state.referencePlans.map(
        (plan) => `<option value="${escapeHtml(plan.plan_id)}">${escapeHtml(plan.label || plan.plan_id)}</option>`,
      ),
    ];
    planSelect.innerHTML = options.join("");
    const defaultPlan = state.referencePlans.find((item) => item.plan_id === "hkust_gz_gate") ?? state.referencePlans[0];
    if (defaultPlan) {
      planSelect.value = defaultPlan.plan_id;
      await applyReferencePlan(defaultPlan.plan_id);
    }
  }

  function finalizeDraftCenterline(): void {
    if (state.draftCenterline.length < 2) {
      setStatus(statusEl, "Centerline needs at least two points.", "error");
      return;
    }
    const id = nextFeatureId(state.annotation, "centerline");
    state.annotation.centerlines.push({
      id,
      label: id,
      points: state.draftCenterline.map((point) => ({ x: point.x, y: point.y })),
      road_width_m: DEFAULT_ROAD_WIDTH_M,
      reference_width_px: null,
      forward_drive_lane_count: DEFAULT_FORWARD_DRIVE_LANE_COUNT,
      reverse_drive_lane_count: DEFAULT_REVERSE_DRIVE_LANE_COUNT,
      bike_lane_count: 0,
      bus_lane_count: 0,
      parking_lane_count: 0,
      highway_type: "annotated_centerline",
    });
    state.selection = { kind: "centerline", id };
    state.draftCenterline = [];
    clearGraphResult("Annotation changed. Re-run convert to refresh graph output.");
    setStatus(statusEl, `Saved centerline ${id}.`, "success");
    renderAll();
  }

  function resetAnnotation(): void {
    state.annotation.centerlines = [];
    state.annotation.junctions = [];
    state.annotation.roundabouts = [];
    state.annotation.control_points = [];
    state.selection = null;
    state.draftCenterline = [];
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
          setStatus(statusEl, `Deleted centerline ${line.id}.`, "success");
        }
      }
    } else if (state.selection.kind === "junction") {
      state.annotation.junctions = state.annotation.junctions.filter((item) => item.id !== state.selection?.id);
      state.selection = null;
      setStatus(statusEl, "Deleted junction.", "success");
    } else if (state.selection.kind === "roundabout") {
      state.annotation.roundabouts = state.annotation.roundabouts.filter((item) => item.id !== state.selection?.id);
      state.selection = null;
      setStatus(statusEl, "Deleted roundabout.", "success");
    } else if (state.selection.kind === "control_point") {
      state.annotation.control_points = state.annotation.control_points.filter((item) => item.id !== state.selection?.id);
      state.selection = null;
      setStatus(statusEl, "Deleted control point.", "success");
    }
    clearGraphResult("Annotation changed. Re-run convert to refresh graph output.");
    renderAll();
  }

  async function convertAnnotationToGraph(): Promise<void> {
    if (state.annotation.centerlines.length === 0) {
      setStatus(graphStatusEl, "Add at least one centerline before converting.", "error");
      return;
    }
    setStatus(graphStatusEl, "Converting annotation to graph...", "neutral");
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
    state.graphResult = payload as ConvertedGraphPayload;
    setStatus(graphStatusEl, "Graph conversion complete.", "success");
    renderAll();
  }

  function syncSelectionAfterMutation(): void {
    if (!state.selection) {
      return;
    }
    if (!getSelectedFeature(state.annotation, state.selection)) {
      state.selection = null;
    }
  }

  backButton.addEventListener(
    "click",
    () => {
      window.location.hash = "#viewer";
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
      state.draftCenterline = [];
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
        state.selection = hit;
        if (hit?.kind === "centerline" && hit.vertexIndex !== undefined) {
          state.drag = {
            kind: "centerline_vertex",
            id: hit.id,
            vertexIndex: hit.vertexIndex,
            pointerId: event.pointerId,
          };
        } else if (hit?.kind === "junction" || hit?.kind === "roundabout" || hit?.kind === "control_point") {
          state.drag = {
            kind: "marker",
            markerKind: hit.kind,
            id: hit.id,
            pointerId: event.pointerId,
          };
        } else {
          state.drag = null;
        }
        renderAll();
        return;
      }

      if (!point) {
        return;
      }

      if (state.selectedTool === "centerline") {
        state.draftCenterline.push(point);
        state.selection = null;
        renderAll();
        return;
      }

      if (state.selectedTool === "junction") {
        const id = nextFeatureId(state.annotation, "junction");
        state.annotation.junctions.push({ id, label: id, x: point.x, y: point.y, kind: "intersection" });
        state.selection = { kind: "junction", id };
        clearGraphResult("Annotation changed. Re-run convert to refresh graph output.");
        setStatus(statusEl, `Added junction ${id}.`, "success");
        renderAll();
        return;
      }

      if (state.selectedTool === "control_point") {
        const id = nextFeatureId(state.annotation, "control_point");
        state.annotation.control_points.push({ id, label: id, x: point.x, y: point.y, kind: "control_point" });
        state.selection = { kind: "control_point", id };
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
      if (!state.drag || state.drag.pointerId !== event.pointerId) {
        return;
      }
      const point = imagePointFromPointer(event);
      if (!point) {
        return;
      }
      if (state.drag.kind === "centerline_vertex") {
        const centerline = state.annotation.centerlines.find((item) => item.id === state.drag?.id);
        if (!centerline) {
          return;
        }
        if (!centerline.points[state.drag.vertexIndex]) {
          return;
        }
        centerline.points[state.drag.vertexIndex] = point;
      } else {
        if (state.drag.markerKind === "junction") {
          const marker = state.annotation.junctions.find((item) => item.id === state.drag?.id);
          if (marker) {
            marker.x = point.x;
            marker.y = point.y;
          }
        } else if (state.drag.markerKind === "roundabout") {
          const marker = state.annotation.roundabouts.find((item) => item.id === state.drag?.id);
          if (marker) {
            marker.x = point.x;
            marker.y = point.y;
          }
        } else {
          const marker = state.annotation.control_points.find((item) => item.id === state.drag?.id);
          if (marker) {
            marker.x = point.x;
            marker.y = point.y;
          }
        }
      }
      clearGraphResult("Annotation changed. Re-run convert to refresh graph output.");
      renderAll();
    },
    { signal },
  );

  window.addEventListener(
    "pointerup",
    (event) => {
      if (state.drag && state.drag.pointerId === event.pointerId) {
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
        state.draftCenterline = [];
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
        state.draftCenterline = [];
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
      downloadText(`${state.annotation.plan_id || "reference_annotation"}_graph.json`, JSON.stringify(state.graphResult, null, 2));
    },
    { signal },
  );

  renderAll();
  void loadReferencePlans().catch((error) => {
    setStatus(statusEl, error instanceof Error ? error.message : "Failed to load reference plans.", "error");
  });

  return () => {
    revokeCurrentObjectUrl();
    eventController.abort();
  };
}
