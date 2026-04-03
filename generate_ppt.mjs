import { createRequire } from "module";
const require = createRequire(import.meta.url);
const PptxGenJS = require("/opt/homebrew/lib/node_modules/pptxgenjs");

const pptx = new PptxGenJS();
pptx.layout = "LAYOUT_WIDE"; // 13.33 x 7.5
pptx.author = "RoadGen3D Team";
pptx.subject = "RoadGen3D Project Overview";

// Color palette — "Ocean Gradient" adapted for urban tech
const C = {
  deepNavy:   "0D1B2A",
  darkBlue:   "1B2838",
  midBlue:    "1B4965",
  teal:       "065A82",
  seafoam:    "00A896",
  mint:       "02C39A",
  lightGray:  "E8ECF1",
  offWhite:   "F4F6F9",
  white:      "FFFFFF",
  accent:     "62B6CB",
  textDark:   "1A1A2E",
  textMid:    "3D405B",
  textLight:  "FFFFFF",
};

const imgDir = "/Users/shiqi/Coding/github/GIStudio/RoadGen3D/artifacts/real/presentation_views";

// ==============================
// SLIDE 1: Title Slide
// ==============================
const slide1 = pptx.addSlide();
slide1.background = { color: C.deepNavy };

// Large title
slide1.addText("RoadGen3D", {
  x: 0.8, y: 1.5, w: 7, h: 1.2,
  fontSize: 54, fontFace: "Arial Black",
  color: C.white, bold: true,
});

// Subtitle
slide1.addText("Text-to-3D Urban Street Scene Generation", {
  x: 0.8, y: 2.7, w: 7, h: 0.8,
  fontSize: 22, fontFace: "Arial",
  color: C.accent,
});

// Tagline
slide1.addText("A Neuro-Symbolic System for Intelligent Street Design", {
  x: 0.8, y: 3.6, w: 7, h: 0.6,
  fontSize: 16, fontFace: "Arial",
  color: C.lightGray, italic: true,
});

// Right side: hero image
slide1.addImage({
  path: `${imgDir}/hero_left.png`,
  x: 8.0, y: 0.8, w: 4.8, h: 5.8,
  rounding: true,
});

// Bottom info bar
slide1.addShape(pptx.ShapeType.rect, {
  x: 0.8, y: 6.2, w: 6.5, h: 0.04,
  fill: { color: C.seafoam },
});
slide1.addText("GIStudio  |  Neuro-Symbolic Urban Generation", {
  x: 0.8, y: 6.4, w: 6, h: 0.4,
  fontSize: 11, fontFace: "Arial",
  color: "778DA9",
});

// ==============================
// SLIDE 2: Core Pipeline
// ==============================
const slide2 = pptx.addSlide();
slide2.background = { color: C.offWhite };

// Title
slide2.addText("Core Pipeline", {
  x: 0.8, y: 0.4, w: 5, h: 0.7,
  fontSize: 32, fontFace: "Arial Black",
  color: C.deepNavy, bold: true,
});

// Accent bar under title
slide2.addShape(pptx.ShapeType.rect, {
  x: 0.8, y: 1.1, w: 2.2, h: 0.06,
  fill: { color: C.seafoam },
});

// Pipeline steps — horizontal flow with boxes
const steps = [
  { title: "Text\nPrompt", desc: "Natural language\ndescription", icon: "Aa" },
  { title: "CLIP +\nFAISS", desc: "Semantic asset\nretrieval", icon: ">>" },
  { title: "Street\nProgram", desc: "Declarative\nrepresentation", icon: "{}" },
  { title: "Layout\nSolver", desc: "Constraint-aware\noptimization", icon: "<>" },
  { title: "3D\nScene", desc: "GLB / PLY\nexport", icon: "3D" },
];

const boxW = 2.0;
const boxH = 2.2;
const startX = 0.6;
const gap = 0.35;
const boxY = 1.6;

steps.forEach((step, i) => {
  const x = startX + i * (boxW + gap);

  // Box background
  slide2.addShape(pptx.ShapeType.roundRect, {
    x, y: boxY, w: boxW, h: boxH,
    fill: { color: C.white },
    shadow: { type: "outer", blur: 6, offset: 2, color: "B0BEC5", opacity: 0.3 },
    rectRadius: 0.1,
  });

  // Accent top bar on box
  slide2.addShape(pptx.ShapeType.rect, {
    x, y: boxY, w: boxW, h: 0.06,
    fill: { color: i === steps.length - 1 ? C.mint : C.teal },
  });

  // Step number badge
  slide2.addShape(pptx.ShapeType.ellipse, {
    x: x + 0.7, y: boxY + 0.2, w: 0.6, h: 0.6,
    fill: { color: i === steps.length - 1 ? C.mint : C.teal },
  });
  slide2.addText(`${i + 1}`, {
    x: x + 0.7, y: boxY + 0.2, w: 0.6, h: 0.6,
    fontSize: 18, fontFace: "Arial", color: C.white, bold: true,
    align: "center", valign: "middle",
  });

  // Step title
  slide2.addText(step.title, {
    x: x + 0.15, y: boxY + 0.95, w: boxW - 0.3, h: 0.7,
    fontSize: 14, fontFace: "Arial", color: C.textDark,
    bold: true, align: "center", valign: "top",
  });

  // Step description
  slide2.addText(step.desc, {
    x: x + 0.15, y: boxY + 1.55, w: boxW - 0.3, h: 0.55,
    fontSize: 10, fontFace: "Arial", color: C.textMid,
    align: "center", valign: "top",
  });

  // Arrow between boxes
  if (i < steps.length - 1) {
    slide2.addShape(pptx.ShapeType.rightArrow, {
      x: x + boxW + 0.02, y: boxY + boxH / 2 - 0.15, w: 0.3, h: 0.3,
      fill: { color: C.seafoam },
    });
  }
});

// Bottom section: key features in 2-column grid
const features = [
  "Neural retrieval with CLIP text embeddings",
  "Symbolic StreetProgram for editable structure",
  "Constraint-aware layout with collision detection",
  "Multi-source: OSM, template, graph, manual",
];
const featY = 4.2;
features.forEach((feat, i) => {
  const col = i % 2;
  const row = Math.floor(i / 2);
  const fx = 0.8 + col * 5.5;
  const fy = featY + row * 0.55;

  slide2.addShape(pptx.ShapeType.ellipse, {
    x: fx, y: fy + 0.05, w: 0.25, h: 0.25,
    fill: { color: C.seafoam },
  });
  slide2.addText("+", {
    x: fx, y: fy + 0.05, w: 0.25, h: 0.25,
    fontSize: 12, fontFace: "Arial", color: C.white, bold: true,
    align: "center", valign: "middle",
  });
  slide2.addText(feat, {
    x: fx + 0.4, y: fy, w: 4.5, h: 0.35,
    fontSize: 12, fontFace: "Arial", color: C.textMid,
    valign: "middle",
  });
});

// ==============================
// SLIDE 3: Auto Scene Pipeline + LLM Loop
// ==============================
const slide3 = pptx.addSlide();
slide3.background = { color: C.white };

slide3.addText("LLM-Driven Auto Pipeline", {
  x: 0.8, y: 0.4, w: 8, h: 0.7,
  fontSize: 32, fontFace: "Arial Black",
  color: C.deepNavy, bold: true,
});
slide3.addShape(pptx.ShapeType.rect, {
  x: 0.8, y: 1.1, w: 2.2, h: 0.06,
  fill: { color: C.seafoam },
});

// Left: flow diagram as stacked cards
const flowSteps = [
  { label: "Input", detail: "graph.json + base_map.png", color: C.midBlue },
  { label: "Parse & Compose", detail: "Graph Parser + LLM Context", color: C.teal },
  { label: "Scene Generation", detail: "compose_street_scene()", color: C.seafoam },
  { label: "Render Preview", detail: "Top-down view → preview.png", color: C.mint },
  { label: "LLM Evaluate", detail: "Score + Suggestions + config_patch", color: C.seafoam },
  { label: "Iterate", detail: "Apply patch → loop (max 3 rounds)", color: C.teal },
];

const cardW = 5.0;
const cardH = 0.72;
const cardX = 0.8;
const cardStartY = 1.5;
const cardGap = 0.12;

flowSteps.forEach((step, i) => {
  const cy = cardStartY + i * (cardH + cardGap);

  // Card bg
  slide3.addShape(pptx.ShapeType.roundRect, {
    x: cardX, y: cy, w: cardW, h: cardH,
    fill: { color: C.offWhite },
    rectRadius: 0.08,
  });

  // Left color bar
  slide3.addShape(pptx.ShapeType.rect, {
    x: cardX, y: cy, w: 0.08, h: cardH,
    fill: { color: step.color },
  });

  // Step label
  slide3.addText(step.label, {
    x: cardX + 0.25, y: cy + 0.08, w: 2.2, h: 0.28,
    fontSize: 13, fontFace: "Arial", color: C.textDark, bold: true,
  });

  // Step detail
  slide3.addText(step.detail, {
    x: cardX + 0.25, y: cy + 0.38, w: 4.5, h: 0.25,
    fontSize: 10, fontFace: "Arial", color: C.textMid,
  });

  // Arrow connector
  if (i < flowSteps.length - 1) {
    slide3.addShape(pptx.ShapeType.downArrow, {
      x: cardX + cardW / 2 - 0.1, y: cy + cardH + 0.01, w: 0.2, h: 0.1,
      fill: { color: C.lightGray },
    });
  }
});

// Right side: scene images
slide3.addImage({
  path: `${imgDir}/overview_top_design.png`,
  x: 6.5, y: 1.3, w: 6.0, h: 2.8,
  rounding: true,
});
slide3.addText("Top-down Scene Overview", {
  x: 6.5, y: 4.15, w: 6, h: 0.3,
  fontSize: 10, fontFace: "Arial", color: C.textMid,
  align: "center", italic: true,
});

slide3.addImage({
  path: `${imgDir}/final_oblique_45_watercolor.png`,
  x: 6.5, y: 4.6, w: 6.0, h: 2.6,
  rounding: true,
});
slide3.addText("Watercolor Oblique 45 View", {
  x: 6.5, y: 7.2, w: 6, h: 0.3,
  fontSize: 10, fontFace: "Arial", color: C.textMid,
  align: "center", italic: true,
});

// ==============================
// SLIDE 4: Technical Highlights + Conclusion
// ==============================
const slide4 = pptx.addSlide();
slide4.background = { color: C.deepNavy };

slide4.addText("Technical Highlights", {
  x: 0.8, y: 0.4, w: 6, h: 0.7,
  fontSize: 32, fontFace: "Arial Black",
  color: C.white, bold: true,
});
slide4.addShape(pptx.ShapeType.rect, {
  x: 0.8, y: 1.1, w: 2.2, h: 0.06,
  fill: { color: C.seafoam },
});

// 2x3 feature grid
const highlights = [
  { title: "Neuro-Symbolic\nGeneration", desc: "StreetProgram + ConstraintSet\n+ LayoutSolver (M6)" },
  { title: "Multi-Source\nInput", desc: "OSM / Template / Graph /\nMetaUrban / Manual" },
  { title: "LLM Design\nAssistant", desc: "RAG-powered design with\niterative refinement" },
  { title: "Design Rule\nProfiles", desc: "Balanced, pedestrian-priority,\ntransit-priority" },
  { title: "Built-in\nEvaluation", desc: "Rule satisfaction, topology,\nplacement efficiency" },
  { title: "Web\nWorkbench", desc: "FastAPI + Three.js viewer\n+ Vite/React UI" },
];

const gridCols = 3;
const gridCellW = 3.7;
const gridCellH = 1.5;
const gridStartX = 0.8;
const gridStartY = 1.5;
const gridGapX = 0.35;
const gridGapY = 0.3;

highlights.forEach((h, i) => {
  const col = i % gridCols;
  const row = Math.floor(i / gridCols);
  const gx = gridStartX + col * (gridCellW + gridGapX);
  const gy = gridStartY + row * (gridCellH + gridGapY);

  // Card
  slide4.addShape(pptx.ShapeType.roundRect, {
    x: gx, y: gy, w: gridCellW, h: gridCellH,
    fill: { color: C.darkBlue },
    rectRadius: 0.1,
  });

  // Top accent line
  slide4.addShape(pptx.ShapeType.rect, {
    x: gx + 0.15, y: gy + 0.15, w: 0.5, h: 0.05,
    fill: { color: C.seafoam },
  });

  // Title
  slide4.addText(h.title, {
    x: gx + 0.15, y: gy + 0.3, w: gridCellW - 0.3, h: 0.5,
    fontSize: 14, fontFace: "Arial", color: C.white, bold: true,
  });

  // Description
  slide4.addText(h.desc, {
    x: gx + 0.15, y: gy + 0.85, w: gridCellW - 0.3, h: 0.5,
    fontSize: 10, fontFace: "Arial", color: "778DA9",
  });
});

// Bottom tagline
slide4.addShape(pptx.ShapeType.rect, {
  x: 0.8, y: 5.2, w: 11.7, h: 0.04,
  fill: { color: C.teal },
});
slide4.addText("From text to 3D streets — bridging creative intent and structural design", {
  x: 0.8, y: 5.5, w: 11.7, h: 0.6,
  fontSize: 16, fontFace: "Arial", color: C.accent,
  align: "center", italic: true,
});

slide4.addText("GIStudio  |  github.com/GIStudio/RoadGen3D", {
  x: 0.8, y: 6.4, w: 11.7, h: 0.4,
  fontSize: 11, fontFace: "Arial", color: "546A7B",
  align: "center",
});

// ==============================
// Save
// ==============================
const outPath = "/Users/shiqi/Coding/github/GIStudio/RoadGen3D/RoadGen3D_Overview.pptx";
await pptx.writeFile({ fileName: outPath });
console.log(`Presentation saved to ${outPath}`);
