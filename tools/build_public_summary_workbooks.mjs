import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const toolDir = path.dirname(fileURLToPath(import.meta.url));
const project = path.resolve(toolDir, "..");
const renderRoot = "D:\\FYP_DataVault\\csi300_breakout_events\\archive\\thesis_public_workbook_renders_20260924";
const fontFamily = "Arial";

const jobs = [
  {
    source: "outputs/research_audit_20260919/market_adjusted_results.xlsx",
    output: "00_THESIS_WRITING_GUIDE/05_Robustness_and_Additional/05_Market_Adjusted_Return/market_adjusted_results_summary.xlsx",
    title: "Market-adjusted return results",
    purpose: "用于论文稳健性小节，保留汇总、回归和方法说明。",
    omitted: "Event Data sheet omitted from the public writing copy; the full workbook remains in DataVault.",
    sheets: ["Summary", "Regression Results", "Notes"],
  },
  {
    source: "outputs/research_audit_20260919/failure60_results.xlsx",
    output: "00_THESIS_WRITING_GUIDE/05_Robustness_and_Additional/06_Failure60/failure60_results_summary.xlsx",
    title: "Failure60 results",
    purpose: "用于论文补充失败分析，保留回归、分组、状态计数和方法说明。",
    omitted: "Event Data sheet omitted from the public writing copy; the full workbook remains in DataVault.",
    sheets: ["Regression Results", "RVOL Groups", "Status Counts", "Notes"],
  },
];

function nonEmptyWidth(rows, columnIndex) {
  let width = 10;
  for (const row of rows) {
    const value = row[columnIndex];
    const length = value == null ? 0 : String(value).length;
    width = Math.max(width, Math.min(length + 2, 42));
  }
  return width;
}

function styleSheet(sheet, rows) {
  if (!rows.length || !rows[0].length) return;
  const rowCount = rows.length;
  const columnCount = rows[0].length;
  const used = sheet.getRangeByIndexes(0, 0, rowCount, columnCount);
  used.format.font = { name: fontFamily, size: 10, color: "#1F1F1F" };
  used.format.verticalAlignment = "center";
  for (let columnIndex = 0; columnIndex < columnCount; columnIndex += 1) {
    sheet.getRangeByIndexes(0, columnIndex, rowCount, 1).format.columnWidth = nonEmptyWidth(rows, columnIndex);
  }
  const likelyHeaderIndex = rows.findIndex((row) => row.filter((value) => value !== null && value !== "").length >= 2);
  if (likelyHeaderIndex >= 0) {
    const header = sheet.getRangeByIndexes(likelyHeaderIndex, 0, 1, columnCount);
    header.format.fill = "#0F6B78";
    header.format.font = { name: fontFamily, size: 10, bold: true, color: "#FFFFFF" };
    header.format.horizontalAlignment = "center";
    header.format.borders = { preset: "outside", style: "thin", color: "#B4C7E7" };
    if (rowCount > likelyHeaderIndex + 1) {
      const body = sheet.getRangeByIndexes(likelyHeaderIndex + 1, 0, rowCount - likelyHeaderIndex - 1, columnCount);
      body.format.borders = { preset: "inside", style: "thin", color: "#D9E2F3" };
    }
    sheet.freezePanes.freezeRows(likelyHeaderIndex + 1);
  }
  sheet.showGridLines = false;
}

async function loadSelectedSheets(sourcePath, names) {
  const input = await FileBlob.load(sourcePath);
  const sourceWorkbook = await SpreadsheetFile.importXlsx(input);
  return names.map((name) => {
    const sourceSheet = sourceWorkbook.worksheets.getItem(name);
    const sourceRange = sourceSheet.getUsedRange();
    return { name, values: sourceRange.values };
  });
}

async function build(job) {
  const sourcePath = path.join(project, job.source);
  const outputPath = path.join(project, job.output);
  const selected = await loadSelectedSheets(sourcePath, job.sheets);
  const workbook = Workbook.create();

  const readme = workbook.worksheets.add("README");
  const readmeRows = [
    [job.title, ""],
    ["Purpose", job.purpose],
    ["Full source", job.source],
    ["Public-data boundary", job.omitted],
    ["Baseline change", "None. This workbook is a summary-only writing copy."],
    ["Status", "validated public summary"],
  ];
  readme.getRangeByIndexes(0, 0, readmeRows.length, 2).values = readmeRows;
  readme.getRange("A1:B1").format.fill = "#17365D";
  readme.getRange("A1:B1").format.font = { name: fontFamily, size: 14, bold: true, color: "#FFFFFF" };
  readme.getRange("A2:A6").format.font = { name: fontFamily, size: 10, bold: true, color: "#1F1F1F" };
  readme.getRange("A1:B6").format.verticalAlignment = "center";
  readme.getRange("A1:A6").format.columnWidth = 24;
  readme.getRange("B1:B6").format.columnWidth = 92;
  readme.getRange("B2:B6").format.wrapText = true;
  readme.showGridLines = false;

  for (const sourceSheet of selected) {
    const sheet = workbook.worksheets.add(sourceSheet.name);
    const rows = sourceSheet.values;
    if (rows.length && rows[0].length) {
      sheet.getRangeByIndexes(0, 0, rows.length, rows[0].length).values = rows;
      styleSheet(sheet, rows);
    }
  }

  workbook.recalculate();
  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(outputPath);

  const reopened = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
  const errors = await reopened.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
    options: { useRegex: true, maxResults: 100 },
    summary: "formula error scan",
  });
  await fs.mkdir(renderRoot, { recursive: true });
  const rendered = [];
  for (const sheetName of ["README", ...job.sheets]) {
    const image = await reopened.render({ sheetName, autoCrop: "all", scale: 1, format: "png" });
    const safe = `${path.basename(job.output, ".xlsx")}_${sheetName.replace(/[^A-Za-z0-9_-]/g, "_")}.png`;
    const imagePath = path.join(renderRoot, safe);
    await fs.writeFile(imagePath, new Uint8Array(await image.arrayBuffer()));
    rendered.push(imagePath);
  }
  return { sourcePath, outputPath, errors: errors.ndjson, rendered };
}

const results = [];
for (const job of jobs) results.push(await build(job));
console.log(JSON.stringify(results, null, 2));
