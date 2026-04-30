import {
  Presentation,
  PresentationFile,
  auto,
  chart,
  column,
  fill,
  fixed,
  fr,
  grid,
  grow,
  hug,
  image,
  panel,
  row,
  rule,
  shape,
  text,
  wrap,
} from "@oai/artifact-tool";
import fs from "node:fs/promises";
import path from "node:path";

const workspace = path.resolve(".");
const root = path.resolve(workspace, "../../..");
const outDir = path.join(workspace, "output");
const scratchDir = path.join(workspace, "scratch");
const previewDir = path.join(scratchDir, "previews");
const layoutDir = path.join(scratchDir, "layouts");
const pptxPath = path.join(outDir, "output.pptx");
const brandImage = path.join(root, "assets", "readme-header-dark.png");

const W = 1920;
const H = 1080;

const C = {
  ink: "#111827",
  muted: "#536270",
  faint: "#EDF2F6",
  paper: "#F8FAFC",
  white: "#FFFFFF",
  dark: "#071114",
  dark2: "#102026",
  moon: "#EAF4F1",
  teal: "#21B6A8",
  blue: "#3578E5",
  amber: "#F4B740",
  red: "#D94C4C",
  green: "#2F9D67",
  line: "#D6E0E8",
};

const S = {
  eyebrow: { fontSize: 22, bold: true, color: C.teal },
  title: { fontSize: 58, bold: true, color: C.ink },
  subtitle: { fontSize: 27, color: C.muted },
  body: { fontSize: 28, color: C.ink },
  small: { fontSize: 18, color: C.muted },
  label: { fontSize: 19, bold: true, color: C.muted },
  code: { fontSize: 23, color: C.ink },
  codeSmall: { fontSize: 19, color: C.ink },
  inverseTitle: { fontSize: 74, bold: true, color: C.white },
  inverseSubtitle: { fontSize: 29, color: "#BED1D0" },
  number: { fontSize: 104, bold: true, color: C.ink },
};

function addSolidBackground(slide, color = C.paper) {
  slide.compose(shape({ name: "background", width: fixed(W), height: fixed(H), fill: color, line: { width: 0, fill: color } }), {
    frame: { left: 0, top: 0, width: W, height: H },
    baseUnit: 8,
  });
}

function textBlock(value, options = {}) {
  return text(value, {
    width: options.width ?? fill,
    height: options.height ?? hug,
    name: options.name,
    style: options.style ?? S.body,
    columnSpan: options.columnSpan,
    rowSpan: options.rowSpan,
  });
}

function footer(source = "Source: samples/simple_openai_api_agent fixture") {
  return row({ name: "source-rail", width: fill, height: hug, justify: "between", align: "end" }, [
    text("Agents Shipgate", { name: "footer-brand", width: hug, height: hug, style: { fontSize: 17, bold: true, color: C.muted } }),
    text(source, { name: "footer-source", width: wrap(1120), height: hug, style: { fontSize: 15, color: "#71808E" } }),
  ]);
}

function titleStack(eyebrow, title, subtitle) {
  return column({ name: "title-stack", width: fill, height: hug, gap: 18 }, [
    text(eyebrow, { name: "slide-eyebrow", width: fill, height: hug, style: S.eyebrow }),
    text(title, { name: "slide-title", width: wrap(1380), height: hug, style: S.title }),
    subtitle
      ? text(subtitle, { name: "slide-subtitle", width: wrap(1300), height: hug, style: S.subtitle })
      : rule({ name: "title-rule", width: fixed(210), stroke: C.teal, weight: 6 }),
  ]);
}

function bodySlide(presentation, eyebrow, title, subtitle, body, source) {
  const slide = presentation.slides.add();
  addSolidBackground(slide);
  slide.compose(
    column({ name: "slide-root", width: fill, height: fill, padding: { x: 92, y: 68 }, gap: 36 }, [
      titleStack(eyebrow, title, subtitle),
      body,
      footer(source),
    ]),
    { frame: { left: 0, top: 0, width: W, height: H }, baseUnit: 8 },
  );
  return slide;
}

function miniFile(label, pathValue, accent = C.blue) {
  return column({ name: `file-${label}`, width: fill, height: hug, gap: 10 }, [
    row({ width: fill, height: hug, gap: 14, align: "center" }, [
      shape({ width: fixed(18), height: fixed(18), fill: accent, line: { width: 0, fill: accent } }),
      text(label, { width: fill, height: hug, style: { fontSize: 24, bold: true, color: C.ink } }),
    ]),
    text(pathValue, { width: fill, height: hug, style: { fontSize: 18, color: C.muted } }),
  ]);
}

function codePanel(name, lines, style = {}) {
  return panel(
    {
      name,
      width: fill,
      height: style.height ?? fill,
      fill: style.fill ?? C.white,
      line: { width: 1, fill: style.line ?? C.line },
      borderRadius: "rounded-md",
      padding: { x: 30, y: 26 },
    },
    column(
      { name: `${name}-content`, width: fill, height: style.height ?? fill, gap: style.gap ?? 10 },
      lines.map((line, idx) =>
        text(line, {
          name: `${name}-line-${idx + 1}`,
          width: fill,
          height: hug,
          style: idx === 0 && style.heading
            ? { fontSize: 22, bold: true, color: style.headingColor ?? C.ink }
            : style.textStyle ?? S.codeSmall,
        }),
      ),
    ),
  );
}

function askRow(name, values, fillColor = C.white, accent = C.teal) {
  return panel(
    {
      name,
      width: fill,
      height: hug,
      fill: fillColor,
      line: { width: 1, fill: C.line },
      borderRadius: "rounded-sm",
      padding: { x: 22, y: 18 },
    },
    grid({ width: fill, height: hug, columns: [fr(0.75), fr(1.15), fr(0.9)], columnGap: 24, alignItems: "start" }, [
      row({ width: fill, height: hug, gap: 12, align: "center" }, [
        shape({ width: fixed(12), height: fixed(12), fill: accent, line: { width: 0, fill: accent } }),
        text(values[0], { width: fill, height: hug, style: { fontSize: 23, bold: true, color: C.ink } }),
      ]),
      text(values[1], { width: fill, height: hug, style: { fontSize: 22, color: C.ink } }),
      text(values[2], { width: fill, height: hug, style: { fontSize: 22, color: C.muted } }),
    ]),
  );
}

function askTable() {
  return column({ name: "reviewer-asks-table", width: fill, height: fill, gap: 12, justify: "center" }, [
    panel(
      { name: "asks-header", width: fill, height: hug, fill: C.dark2, line: { width: 0, fill: C.dark2 }, borderRadius: "rounded-sm", padding: { x: 22, y: 18 } },
      grid({ width: fill, height: hug, columns: [fr(0.75), fr(1.15), fr(0.9)], columnGap: 24 }, [
        text("Finding", { width: fill, height: hug, style: { fontSize: 21, bold: true, color: C.white } }),
        text("Reviewer asks for", { width: fill, height: hug, style: { fontSize: 21, bold: true, color: C.white } }),
        text("Why it matters", { width: fill, height: hug, style: { fontSize: 21, bold: true, color: C.white } }),
      ]),
    ),
    askRow("ask-schema", ["Schema strictness", "strict=true, required fields, bounded amount", "Less ambiguous tool calls"], C.white, C.red),
    askRow("ask-idempotency", ["Idempotency", "idempotency key or no retry for side effect", "Avoid duplicate refunds/emails"], "#FDF9F0", C.amber),
    askRow("ask-approval", ["Approval", "approval_required plus passing trace evidence", "Human gate before financial action"], C.white, C.blue),
    askRow("ask-ownership", ["Ownership", "owner and auth scope metadata", "Release accountability"], "#F3FAF8", C.green),
  ]);
}

function riskRow(label, detail, color) {
  return row({ name: `risk-${label}`, width: fill, height: hug, gap: 18, align: "start" }, [
    shape({ name: `risk-dot-${label}`, width: fixed(20), height: fixed(20), fill: color, line: { width: 0, fill: color } }),
    column({ width: fill, height: hug, gap: 4 }, [
      text(label, { width: fill, height: hug, style: { fontSize: 28, bold: true, color: C.ink } }),
      text(detail, { width: fill, height: hug, style: { fontSize: 22, color: C.muted } }),
    ]),
  ]);
}

async function savePreviews(presentation) {
  await fs.mkdir(previewDir, { recursive: true });
  await fs.mkdir(layoutDir, { recursive: true });
  for (const slide of presentation.slides.items) {
    const index = slide.index + 1;
    const png = await presentation.export({ slide, format: "png" });
    await fs.writeFile(path.join(previewDir, `slide-${String(index).padStart(2, "0")}.png`), Buffer.from(await png.arrayBuffer()));
    const layout = await slide.export({ format: "layout" });
    await fs.writeFile(path.join(layoutDir, `slide-${String(index).padStart(2, "0")}.json`), await layout.text());
  }
}

async function main() {
  await fs.mkdir(outDir, { recursive: true });
  await fs.mkdir(scratchDir, { recursive: true });
  const brandDataUrl = `data:image/png;base64,${(await fs.readFile(brandImage)).toString("base64")}`;

  const presentation = Presentation.create({
    slideSize: { width: W, height: H },
  });

  // 1. Cover
  {
    const slide = presentation.slides.add();
    addSolidBackground(slide, C.dark);
    slide.compose(shape({ name: "cover-teal-field", width: fixed(760), height: fixed(H), fill: C.teal, line: { width: 0, fill: C.teal } }), {
      frame: { left: 1160, top: 0, width: 760, height: H },
      baseUnit: 8,
    });
    slide.compose(shape({ name: "cover-black-rail", width: fixed(500), height: fixed(H), fill: "#061013", line: { width: 0, fill: "#061013" } }), {
      frame: { left: 1420, top: 0, width: 500, height: H },
      baseUnit: 8,
    });
    slide.compose(
      column({ name: "cover-root", width: fill, height: fill, padding: { x: 96, y: 76 }, justify: "between" }, [
        column({ name: "cover-copy", width: wrap(1060), height: hug, gap: 26 }, [
          text("Agents Shipgate direct API walkthrough", { name: "cover-eyebrow", width: fill, height: hug, style: { fontSize: 24, bold: true, color: C.teal } }),
          text("From prompt to release gate", { name: "cover-title", width: wrap(980), height: hug, style: S.inverseTitle }),
          text("Original prompt, scanned findings, release risk, and integration path.", {
            name: "cover-promise",
            width: wrap(840),
            height: hug,
            style: S.inverseSubtitle,
          }),
        ]),
        row({ name: "cover-bottom", width: fill, height: hug, justify: "between", align: "end" }, [
          image({ name: "three-moons-lab-mark", dataUrl: brandDataUrl, width: fixed(520), height: fixed(62), fit: "contain", alt: "Three Moons Lab" }),
          text("Fixture, not a customer case study", { name: "cover-context", width: hug, height: hug, style: { fontSize: 18, color: "#C9D8D7" } }),
        ]),
      ]),
      { frame: { left: 0, top: 0, width: W, height: H }, baseUnit: 8 },
    );
  }

  // 2. Original API prompt
  bodySlide(
    presentation,
    "1. Original API prompt",
    "The app presents itself as advice-only.",
    "This is the prompt file from the direct OpenAI API fixture.",
    grid(
      { name: "prompt-grid", width: fill, height: fill, columns: [fr(1.05), fr(0.95)], columnGap: 56 },
      [
        codePanel("original-prompt", [
          "prompts/support_refund.md",
          "You are a support refund assistant.",
          "",
          "You should only advise the support representative",
          "and prepare a draft response.",
          "",
          "Do not take action on the customer's account.",
        ], { heading: true, headingColor: C.teal, textStyle: { fontSize: 27, color: C.ink }, fill: C.white }),
        column({ name: "prompt-claim", width: fill, height: fill, gap: 24, justify: "center" }, [
          text("What a reviewer would assume", { name: "reviewer-assumption-label", width: fill, height: hug, style: S.label }),
          text("No account mutation. No refund execution. No customer email send.", {
            name: "reviewer-assumption",
            width: wrap(740),
            height: hug,
            style: { fontSize: 48, bold: true, color: C.ink },
          }),
          rule({ name: "prompt-rule", width: fixed(260), stroke: C.teal, weight: 6 }),
          text("That promise must match the actual tool surface.", { name: "prompt-note", width: wrap(650), height: hug, style: S.subtitle }),
        ]),
      ],
    ),
    "Source: samples/simple_openai_api_agent/prompts/support_refund.md",
  );

  // 3. Actual tool surface
  bodySlide(
    presentation,
    "2. But the enabled tools can take action.",
    "The direct OpenAI API artifacts expose two write-capable function tools.",
    "This is the gap Agents Shipgate is designed to make visible before release.",
    grid(
      { name: "tool-surface-grid", width: fill, height: fill, columns: [fr(0.95), fr(1.05)], columnGap: 56 },
      [
        column({ name: "tool-list", width: fill, height: fill, gap: 20, justify: "center" }, [
          riskRow("create_refund", "Creates a refund for a customer payment.", C.red),
          riskRow("send_customer_email", "Sends an external customer email.", C.amber),
          text("Both are normalized as openai_api tools in the report inventory.", {
            name: "normalized-note",
            width: wrap(720),
            height: hug,
            style: { fontSize: 24, color: C.muted },
          }),
        ]),
        codePanel("tool-schema-snippet", [
          "tools/openai-tools.json",
          "\"name\": \"create_refund\"",
          "\"description\": \"Create a refund for a customer payment.\"",
          "\"strict\": false",
          "\"additionalProperties\": true",
          "\"required\": [\"payment_id\"]",
          "\"amount\": { \"type\": \"number\" }",
        ], { heading: true, headingColor: C.red, textStyle: S.codeSmall, fill: C.white }),
      ],
    ),
    "Source: samples/simple_openai_api_agent/tools/openai-tools.json",
  );

  // 4. Scanned findings
  bodySlide(
    presentation,
    "3. What did Agents Shipgate find?",
    "It turns the prompt/tool surface into a release-review queue.",
    "The fixture emits 15 high-severity and 5 medium-severity findings.",
    grid(
      { name: "findings-grid", width: fill, height: fill, columns: [fr(0.82), fr(1.18)], columnGap: 58 },
      [
        chart({
          name: "severity-chart",
          chartType: "bar",
          width: fill,
          height: fill,
          config: {
            title: "Findings by severity",
            categories: ["Critical", "High", "Medium", "Low"],
            series: [{ name: "Findings", values: [0, 15, 5, 0] }],
          },
        }),
        column({ name: "finding-list", width: fill, height: fill, gap: 20, justify: "center" }, [
          text("Top scan results", { name: "finding-list-label", width: fill, height: hug, style: S.label }),
          riskRow("Prompt/tool mismatch", "Prompt says advise-only while refund and email tools are enabled.", C.red),
          riskRow("Schema strictness gaps", "create_refund lacks strict=true, complete required fields, and amount bounds.", C.amber),
          riskRow("Retry without idempotency", "Refund and email side effects may be retried without idempotency evidence.", C.blue),
          riskRow("Trace approval gap", "Trace sample shows create_refund with approved=false.", C.green),
        ]),
      ],
    ),
    "Source: samples/simple_openai_api_agent/expected/report.md",
  );

  // 5. Why the prompt/tool mismatch is serious
  bodySlide(
    presentation,
    "4. Why is this serious?",
    "The prompt is the user-facing promise. The tools are the release-time blast radius.",
    "When those disagree, production behavior can exceed what reviewers approved.",
    grid(
      { name: "serious-grid", width: fill, height: fill, columns: [fr(1), fr(1), fr(1)], columnGap: 34 },
      [
        panel({ name: "serious-finance", width: fill, height: fill, fill: C.white, line: { width: 1, fill: C.line }, borderRadius: "rounded-md", padding: { x: 30, y: 28 } },
          column({ width: fill, height: fill, gap: 18 }, [
            shape({ width: fixed(30), height: fixed(30), fill: C.red, line: { width: 0, fill: C.red } }),
            text("Financial side effect", { width: fill, height: hug, style: { fontSize: 34, bold: true, color: C.ink } }),
            text("A refund tool can move money even though the prompt says no account action.", { width: fill, height: hug, style: { fontSize: 25, color: C.muted } }),
          ])),
        panel({ name: "serious-duplicate", width: fill, height: fill, fill: "#FDF9F0", line: { width: 1, fill: C.line }, borderRadius: "rounded-md", padding: { x: 30, y: 28 } },
          column({ width: fill, height: fill, gap: 18 }, [
            shape({ width: fixed(30), height: fixed(30), fill: C.amber, line: { width: 0, fill: C.amber } }),
            text("Duplicate actions", { width: fill, height: hug, style: { fontSize: 34, bold: true, color: C.ink } }),
            text("Retry policy plus missing idempotency can duplicate refunds or customer emails.", { width: fill, height: hug, style: { fontSize: 25, color: C.muted } }),
          ])),
        panel({ name: "serious-review", width: fill, height: fill, fill: "#F3FAF8", line: { width: 1, fill: C.line }, borderRadius: "rounded-md", padding: { x: 30, y: 28 } },
          column({ width: fill, height: fill, gap: 18 }, [
            shape({ width: fixed(30), height: fixed(30), fill: C.green, line: { width: 0, fill: C.green } }),
            text("Review gap", { width: fill, height: hug, style: { fontSize: 34, bold: true, color: C.ink } }),
            text("Approval policy and trace evidence do not prove the financial action is gated.", { width: fill, height: hug, style: { fontSize: 25, color: C.muted } }),
          ])),
      ],
    ),
    "Source: expected/report.md top findings and trace sample",
  );

  // 6. What a reviewer asks for
  bodySlide(
    presentation,
    "5. What does the fix look like?",
    "Agents Shipgate gives concrete review asks instead of a generic score.",
    "Future users can use the finding list as their first release checklist.",
    askTable(),
    "Source: expected/report.md recommendations",
  );

  // 7. Shipgate manifest
  bodySlide(
    presentation,
    "6. How do you connect Agents Shipgate?",
    "Declare the local direct API artifacts under openai_api.",
    "No OpenAI API call is made during scanning; the scanner reads the files you already review.",
    grid(
      { name: "integration-grid", width: fill, height: fill, columns: [fr(1), fr(1)], columnGap: 54 },
      [
        codePanel("manifest-panel", [
          "shipgate.yaml",
          "openai_api:",
          "  prompt_files:",
          "    - prompts/support_refund.md",
          "  tools:",
          "    - path: tools/openai-tools.json",
          "  response_formats:",
          "    - path: schemas/refund_decision.schema.json",
          "  policy_rules:",
          "    - path: policies/openai-api-policy.yaml",
        ], { heading: true, headingColor: C.blue, textStyle: S.codeSmall, fill: C.white }),
        column({ name: "artifact-explainers", width: fill, height: fill, gap: 24, justify: "center" }, [
          miniFile("Prompt files", "What the model is told to do", C.teal),
          miniFile("Tool schemas", "What the model is allowed to call", C.red),
          miniFile("Response formats", "What downstream logic depends on", C.blue),
          miniFile("Policy rules", "Approval, confirmation, retry, timeout evidence", C.green),
        ]),
      ],
    ),
    "Source: docs/manifest-v0.1.md and samples/simple_openai_api_agent/shipgate.yaml",
  );

  // 8. Run and roll out
  bodySlide(
    presentation,
    "7. Run locally, then put it in CI.",
    "Start advisory. Promote to strict.",
    "Local scan first; CI can fail only on new blockers after a baseline.",
    grid(
      { name: "rollout-grid", width: fill, height: fill, columns: [fr(1.05), fr(0.95)], columnGap: 54 },
      [
        codePanel("run-commands", [
          "Local commands",
          "agents-shipgate init --workspace . --write",
          "agents-shipgate scan -c shipgate.yaml",
          "",
          "Try the fixture:",
          "agents-shipgate scan -c samples/simple_openai_api_agent/shipgate.yaml",
        ], { heading: true, headingColor: C.teal, textStyle: S.codeSmall, fill: C.white }),
        column({ name: "rollout-steps", width: fill, height: fill, gap: 24, justify: "center" }, [
          riskRow("1. Advisory scan", "Write report.md and report.json in PR review.", C.blue),
          riskRow("2. Fix or document", "Add strict schemas, approval evidence, idempotency, owner, and scopes.", C.amber),
          riskRow("3. Promote to strict", "Use a baseline so CI fails on new critical/high findings.", C.green),
          text("Static release-readiness scanner for AI agent tool surfaces.", {
            name: "tagline",
            width: wrap(700),
            height: hug,
            style: { fontSize: 25, bold: true, color: C.ink },
          }),
        ]),
      ],
    ),
    "Source: README.md, docs/manifest-v0.1.md, and STABILITY.md",
  );

  const pptxBlob = await PresentationFile.exportPptx(presentation);
  await pptxBlob.save(pptxPath);
  await savePreviews(presentation);

  console.log(JSON.stringify({ pptx: pptxPath, slides: presentation.slides.count, previews: previewDir, layouts: layoutDir }, null, 2));
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
