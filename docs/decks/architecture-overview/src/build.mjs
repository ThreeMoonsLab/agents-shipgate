import {
  Presentation,
  PresentationFile,
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
  muted: "#526273",
  paper: "#F8FAFC",
  white: "#FFFFFF",
  dark: "#071114",
  dark2: "#102026",
  teal: "#21B6A8",
  blue: "#3578E5",
  amber: "#F4B740",
  red: "#D94C4C",
  green: "#2F9D67",
  purple: "#7C5CE5",
  line: "#D6E0E8",
};

const S = {
  eyebrow: { fontSize: 22, bold: true, color: C.teal },
  title: { fontSize: 58, bold: true, color: C.ink },
  subtitle: { fontSize: 27, color: C.muted },
  body: { fontSize: 27, color: C.ink },
  small: { fontSize: 18, color: C.muted },
  label: { fontSize: 19, bold: true, color: C.muted },
  code: { fontSize: 20, color: C.ink },
  inverseTitle: { fontSize: 74, bold: true, color: C.white },
  inverseSubtitle: { fontSize: 29, color: "#BED1D0" },
};

function addSolidBackground(slide, color = C.paper) {
  slide.compose(shape({ name: "background", width: fixed(W), height: fixed(H), fill: color, line: { width: 0, fill: color } }), {
    frame: { left: 0, top: 0, width: W, height: H },
    baseUnit: 8,
  });
}

function footer(source = "Source: docs/architecture.md") {
  return row({ name: "source-rail", width: fill, height: hug, justify: "between", align: "end" }, [
    text("Agents Shipgate", { name: "footer-brand", width: hug, height: hug, style: { fontSize: 17, bold: true, color: C.muted } }),
    text(source, { name: "footer-source", width: wrap(1180), height: hug, style: { fontSize: 15, color: "#71808E" } }),
  ]);
}

function titleStack(eyebrow, title, subtitle) {
  return column({ name: "title-stack", width: fill, height: hug, gap: 16 }, [
    text(eyebrow, { name: "slide-eyebrow", width: fill, height: hug, style: S.eyebrow }),
    text(title, { name: "slide-title", width: fixed(1380), height: fixed(140), style: S.title }),
    subtitle
      ? text(subtitle, { name: "slide-subtitle", width: fixed(1320), height: fixed(72), style: S.subtitle })
      : rule({ name: "title-rule", width: fixed(210), stroke: C.teal, weight: 6 }),
  ]);
}

function bodySlide(presentation, eyebrow, title, subtitle, body, source) {
  const slide = presentation.slides.add();
  addSolidBackground(slide);
  slide.compose(
    column({ name: "slide-root", width: fill, height: fill, padding: { x: 92, y: 68 }, gap: 34 }, [
      titleStack(eyebrow, title, subtitle),
      body,
      footer(source),
    ]),
    { frame: { left: 0, top: 0, width: W, height: H }, baseUnit: 8 },
  );
  return slide;
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
      padding: { x: 28, y: 24 },
    },
    column(
      { name: `${name}-content`, width: fill, height: style.height ?? fill, gap: style.gap ?? 9 },
      lines.map((line, idx) =>
        text(line, {
          name: `${name}-line-${idx + 1}`,
          width: fill,
          height: hug,
          style: idx === 0 && style.heading
            ? { fontSize: 22, bold: true, color: style.headingColor ?? C.ink }
            : style.textStyle ?? S.code,
        }),
      ),
    ),
  );
}

function stepBox(name, label, detail, color) {
  return panel(
    {
      name,
      width: fill,
      height: hug,
      fill: C.white,
      line: { width: 1, fill: C.line },
      borderRadius: "rounded-md",
      padding: { x: 24, y: 20 },
    },
    row({ width: fill, height: hug, gap: 16, align: "start" }, [
      shape({ width: fixed(16), height: fixed(16), fill: color, line: { width: 0, fill: color } }),
      column({ width: fill, height: hug, gap: 4 }, [
        text(label, { width: fill, height: hug, style: { fontSize: 26, bold: true, color: C.ink } }),
        text(detail, { width: fill, height: hug, style: { fontSize: 20, color: C.muted } }),
      ]),
    ]),
  );
}

function compactItem(name, label, detail, color) {
  return row({ name, width: fill, height: hug, gap: 14, align: "start" }, [
    shape({ width: fixed(14), height: fixed(14), fill: color, line: { width: 0, fill: color } }),
    column({ width: fill, height: hug, gap: 4 }, [
      text(label, { width: fill, height: hug, style: { fontSize: 24, bold: true, color: C.ink } }),
      text(detail, { width: fill, height: hug, style: { fontSize: 19, color: C.muted } }),
    ]),
  ]);
}

function moduleBox(name, label, detail, color) {
  return panel(
    {
      name,
      width: fill,
      height: fill,
      fill: C.white,
      line: { width: 1, fill: C.line },
      borderRadius: "rounded-md",
      padding: { x: 22, y: 20 },
    },
    column({ width: fill, height: fill, gap: 12 }, [
      shape({ width: fixed(18), height: fixed(18), fill: color, line: { width: 0, fill: color } }),
      text(label, { width: fill, height: hug, style: { fontSize: 27, bold: true, color: C.ink } }),
      text(detail, { width: fill, height: hug, style: { fontSize: 20, color: C.muted } }),
    ]),
  );
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

  const presentation = Presentation.create({ slideSize: { width: W, height: H } });

  // 1. Cover
  {
    const slide = presentation.slides.add();
    addSolidBackground(slide, C.dark);
    slide.compose(shape({ name: "cover-teal-field", width: fixed(690), height: fixed(H), fill: C.teal, line: { width: 0, fill: C.teal } }), {
      frame: { left: 1230, top: 0, width: 690, height: H },
      baseUnit: 8,
    });
    slide.compose(shape({ name: "cover-black-rail", width: fixed(420), height: fixed(H), fill: "#061013", line: { width: 0, fill: "#061013" } }), {
      frame: { left: 1500, top: 0, width: 420, height: H },
      baseUnit: 8,
    });
    slide.compose(
      column({ name: "cover-root", width: fill, height: fill, padding: { x: 96, y: 76 }, justify: "between" }, [
        column({ name: "cover-copy", width: wrap(1060), height: hug, gap: 26 }, [
          text("Agents Shipgate architecture", { name: "cover-eyebrow", width: fill, height: hug, style: { fontSize: 24, bold: true, color: C.teal } }),
          text("How the static release gate works", { name: "cover-title", width: wrap(1060), height: hug, style: S.inverseTitle }),
          text("From shipgate.yaml to deterministic findings, reports, and CI exit codes.", {
            name: "cover-promise",
            width: wrap(820),
            height: hug,
            style: S.inverseSubtitle,
          }),
        ]),
        row({ name: "cover-bottom", width: fill, height: hug, justify: "between", align: "end" }, [
          image({ name: "three-moons-lab-mark", dataUrl: brandDataUrl, width: fixed(520), height: fixed(62), fit: "contain", alt: "Three Moons Lab" }),
          text("Architecture overview", { name: "cover-context", width: hug, height: hug, style: { fontSize: 18, color: "#C9D8D7" } }),
        ]),
      ]),
      { frame: { left: 0, top: 0, width: W, height: H }, baseUnit: 8 },
    );
  }

  // 2. One-line model
  bodySlide(
    presentation,
    "1. Mental model",
    "Static gate.",
    "Reads declared artifacts, normalizes tools, runs checks, and writes reports.",
    grid({ name: "model-grid", width: fill, height: fill, columns: [fr(1), fr(1), fr(1), fr(1)], columnGap: 24 }, [
      stepBox("model-read", "Read", "shipgate.yaml plus local tool and policy artifacts", C.blue),
      stepBox("model-normalize", "Normalize", "turn heterogeneous sources into Tool objects", C.teal),
      stepBox("model-check", "Check", "run release-readiness rules against ScanContext", C.amber),
      stepBox("model-report", "Gate", "write reports and return stable CI exit codes", C.green),
    ]),
    "Source: docs/architecture.md pipeline",
  );

  // 3. Pipeline
  bodySlide(
    presentation,
    "2. Pipeline",
    "The scan is a deterministic data pipeline.",
    "Each stage transforms local inputs into a richer static model; no agent execution is required.",
    grid(
      { name: "pipeline-grid", width: fill, height: fill, columns: [fr(1), fr(1)], rows: [fr(1), fr(1), fr(1)], columnGap: 30, rowGap: 24 },
      [
        stepBox("pipe-config", "config/loader.py", "load and validate shipgate.yaml", C.blue),
        stepBox("pipe-inputs", "inputs/*", "normalize MCP, OpenAPI, API, ADK, SDK inputs", C.teal),
        stepBox("pipe-risk", "core/risk_hints.py", "enrich tools with read/write/destructive risk tags", C.amber),
        stepBox("pipe-context", "core/context.py", "assemble manifest + agent + tools + artifacts", C.purple),
        stepBox("pipe-checks", "checks/*.py", "pure check functions return Finding objects", C.red),
        stepBox("pipe-report", "report/* + ci/*", "write reports and compute CI result", C.green),
      ],
    ),
    "Source: docs/architecture.md",
  );

  // 4. Modules
  bodySlide(
    presentation,
    "3. Codebase map",
    "Repo map.",
    "Adapters, models, checks, and formatters stay separate.",
    grid(
      { name: "module-grid", width: fill, height: fill, columns: [fr(1), fr(1), fr(1)], rows: [fr(1), fr(1)], columnGap: 28, rowGap: 28 },
      [
        moduleBox("mod-cli", "cli/", "entry points: scan, init, doctor, explain", C.blue),
        moduleBox("mod-config", "config/", "Pydantic manifest schema and loader", C.teal),
        moduleBox("mod-core", "core/", "Tool, Finding, Report, ScanContext", C.purple),
        moduleBox("mod-inputs", "inputs/", "source-specific adapters", C.amber),
        moduleBox("mod-checks", "checks/", "release-readiness checks by category", C.red),
        moduleBox("mod-report", "report/", "Markdown, JSON, SARIF output", C.green),
      ],
    ),
    "Source: docs/architecture.md module map",
  );

  // 5. Scan orchestration
  bodySlide(
    presentation,
    "4. run_scan orchestration",
    "The CLI coordinates the whole scan in one pass.",
    "The important boundary is before checks: all inputs become tools, artifacts, warnings, and one ScanContext.",
    grid({ name: "scan-grid", width: fill, height: fill, columns: [fr(1.05), fr(0.95)], columnGap: 52 }, [
      codePanel("scan-code", [
        "src/agents_shipgate/cli/scan.py",
        "manifest = load_manifest(config_path)",
        "loaded_sources = _load_sources(...)",
        "api_source, api_artifacts = load_openai_api_artifacts(...)",
        "tools = enrich_tools_with_risk_hints(manifest, tools)",
        "context = ScanContext(manifest, agent, tools, artifacts)",
        "findings = run_checks(context)",
        "report = build_report(...)",
        "return report, exit_code_for_report(...)",
      ], { heading: true, headingColor: C.blue, textStyle: { fontSize: 18, color: C.ink }, fill: C.white }),
      column({ name: "scan-notes", width: fill, height: fill, gap: 20, justify: "center" }, [
        compactItem("scan-base", "Manifest is copied and CLI flags override config", "CI mode, output dir, formats, fail_on, baseline.", C.blue),
        compactItem("scan-dedupe", "Sources are flattened and deduplicated", "Higher-fidelity tool sources win on duplicate names.", C.teal),
        compactItem("scan-policy", "Suppressions, overrides, and baselines are late-bound", "Finding identity is stable before CI policy is applied.", C.amber),
        compactItem("scan-output", "Reports are generated before exit policy", "JSON/Markdown/SARIF exist even when strict mode fails.", C.green),
      ]),
    ]),
    "Source: src/agents_shipgate/cli/scan.py",
  );

  // 6. Checks and findings
  bodySlide(
    presentation,
    "5. Checks are pure functions.",
    "A check reads ScanContext and returns Finding objects.",
    "That keeps rules deterministic, testable, and composable with policy packs and plugins.",
    grid({ name: "checks-grid", width: fill, height: fill, columns: [fr(1), fr(1)], columnGap: 50 }, [
      column({ name: "check-categories", width: fill, height: fill, gap: 20, justify: "center" }, [
        compactItem("check-api", "api", "OpenAI API schema, structured output, retry, traces", C.blue),
        compactItem("check-auth", "auth", "missing scopes and broad permissions", C.teal),
        compactItem("check-policy", "policy", "approval and confirmation policies", C.amber),
        compactItem("check-sidefx", "side effects", "idempotency, destructive/write behavior", C.red),
        compactItem("check-docs", "documentation", "missing descriptions and injection-like metadata", C.green),
      ]),
      codePanel("finding-contract", [
        "Finding identity",
        "check_id",
        "tool_name",
        "canonical evidence",
        "",
        "Fingerprint:",
        "fp_ + sha256(check_id | tool_name | evidence)[:16]",
        "",
        "Used by suppressions and baselines.",
      ], { heading: true, headingColor: C.purple, textStyle: { fontSize: 23, color: C.ink }, fill: C.white }),
    ]),
    "Source: docs/architecture.md and STABILITY.md",
  );

  // 7. Reports and CI
  bodySlide(
    presentation,
    "6. Reports are artifacts; CI is policy.",
    "The scan always builds a report, then computes the exit code from CI mode and baseline state.",
    "Stable JSON fields are the integration surface for agents and automation.",
    grid({ name: "report-grid", width: fill, height: fill, columns: [fr(1), fr(1)], columnGap: 52 }, [
      column({ name: "report-list", width: fill, height: fill, gap: 22, justify: "center" }, [
        compactItem("report-md", "report.md", "human-readable release review", C.blue),
        compactItem("report-json", "report.json", "stable machine-readable fields", C.teal),
        compactItem("report-sarif", "report.sarif", "code scanning and security tooling", C.purple),
        compactItem("report-summary", "GitHub step summary", "PR feedback without opening artifacts", C.green),
      ]),
      panel({ name: "exit-table", width: fill, height: fill, fill: C.white, line: { width: 1, fill: C.line }, borderRadius: "rounded-md", padding: { x: 28, y: 24 } },
        column({ width: fill, height: fill, gap: 18 }, [
          text("Stable exit codes", { width: fill, height: hug, style: { fontSize: 28, bold: true, color: C.ink } }),
          compactItem("exit-0", "0", "pass or advisory result", C.green),
          compactItem("exit-2", "2", "manifest config error", C.amber),
          compactItem("exit-3", "3", "input parse error", C.red),
          compactItem("exit-20", "20", "strict-mode gate failure", C.red),
        ])),
    ]),
    "Source: STABILITY.md and src/agents_shipgate/cli/scan.py",
  );

  // 8. Trust and extension
  bodySlide(
    presentation,
    "7. Trust model and extension points",
    "Static by default. Extensible by explicit contribution or plugin opt-in.",
    "The default architecture keeps untrusted agent code outside the execution boundary.",
    grid({ name: "trust-grid", width: fill, height: fill, columns: [fr(1), fr(1)], columnGap: 52 }, [
      panel({ name: "trust-invariants", width: fill, height: fill, fill: C.white, line: { width: 1, fill: C.line }, borderRadius: "rounded-md", padding: { x: 28, y: 24 } },
        column({ width: fill, height: fill, gap: 18 }, [
          text("Default invariants", { width: fill, height: hug, style: { fontSize: 28, bold: true, color: C.ink } }),
          compactItem("trust-code", "No user code import or execution", "SDK loaders use AST parsing only.", C.blue),
          compactItem("trust-model", "No model, tool, MCP, or network calls", "Inputs are local files.", C.teal),
          compactItem("trust-path", "Path traversal containment", "Declared paths must stay under the manifest directory.", C.amber),
          compactItem("trust-plugins", "Plugins off by default", "Opt-in changes the trust boundary.", C.red),
        ])),
      panel({ name: "extension-points", width: fill, height: fill, fill: "#F3FAF8", line: { width: 1, fill: C.line }, borderRadius: "rounded-md", padding: { x: 28, y: 24 } },
        column({ width: fill, height: fill, gap: 18 }, [
          text("How to extend", { width: fill, height: hug, style: { fontSize: 28, bold: true, color: C.ink } }),
          compactItem("ext-input", "New input adapter", "Add loader, wire scan.py, fixture, tests.", C.green),
          compactItem("ext-check", "New check", "Add check function, registry metadata, docs, tests.", C.purple),
          compactItem("ext-stable", "Keep IDs stable", "Never rename published check IDs.", C.amber),
          compactItem("ext-report", "Preserve JSON contract", "Additive report changes only in 0.x.", C.blue),
        ])),
    ]),
    "Source: docs/trust-model.md, docs/architecture.md, and STABILITY.md",
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
