import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const toolDir = path.dirname(fileURLToPath(import.meta.url));
const project = path.resolve(toolDir, "..");
const payloadPath = path.join(project, "catalog", "workbook_payload.json");
const migrationOutput = path.join(project, "catalog", "Migration_Review.xlsx");
const projectMapOutput = path.join(project, "docs", "Research_Project_Map.xlsx");
const renderRoot = "D:\\FYP_DataVault\\csi300_breakout_events\\archive\\control_workbook_renders_20260924";
const verificationOutput = path.join(renderRoot, "workbook_verification.json");

await fs.mkdir(renderRoot, { recursive: true });
const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));

const COLORS = {
  navy: "#17365D",
  teal: "#0F6B78",
  paleBlue: "#DDEBF7",
  paleGold: "#FFF2CC",
  paleGreen: "#E2F0D9",
  paleRed: "#FCE4D6",
  border: "#B4C7E7",
  white: "#FFFFFF",
  dark: "#1F1F1F",
};

function columnLetter(number) {
  let value = number;
  let result = "";
  while (value > 0) {
    const remainder = (value - 1) % 26;
    result = String.fromCharCode(65 + remainder) + result;
    value = Math.floor((value - 1) / 26);
  }
  return result;
}

function normalize(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return value;
}

function addDataSheet(workbook, spec) {
  const { name, title, purpose, source, headers, rows, widths = [] } = spec;
  const sheet = workbook.worksheets.add(name);
  sheet.showGridLines = false;
  const lastColumn = columnLetter(Math.max(headers.length, 2));

  sheet.mergeCells(`A1:${lastColumn}1`);
  sheet.getRange("A1").values = [[title]];
  sheet.getRange(`A1:${lastColumn}1`).format = {
    fill: COLORS.navy,
    font: { name: "Calibri", size: 16, bold: true, color: COLORS.white },
    verticalAlignment: "center",
  };
  sheet.getRange("A1").format.rowHeight = 28;

  sheet.mergeCells(`A2:${lastColumn}2`);
  sheet.getRange("A2").values = [[purpose]];
  sheet.getRange(`A2:${lastColumn}2`).format = {
    fill: COLORS.paleBlue,
    font: { name: "Calibri", size: 10, color: COLORS.dark },
    wrapText: true,
    verticalAlignment: "center",
  };
  sheet.getRange("A2").format.rowHeight = 34;

  sheet.mergeCells(`A3:${lastColumn}3`);
  sheet.getRange("A3").values = [[source]];
  sheet.getRange(`A3:${lastColumn}3`).format = {
    fill: "#F3F6FA",
    font: { name: "Calibri", size: 9, italic: true, color: "#555555" },
    wrapText: true,
  };
  sheet.getRange("A3").format.rowHeight = 30;

  sheet.getRangeByIndexes(4, 0, 1, headers.length).values = [headers];
  sheet.getRangeByIndexes(4, 0, 1, headers.length).format = {
    fill: COLORS.teal,
    font: { name: "Calibri", size: 10, bold: true, color: COLORS.white },
    wrapText: true,
    verticalAlignment: "center",
    borders: { preset: "all", style: "thin", color: COLORS.border },
  };
  sheet.getRangeByIndexes(4, 0, 1, headers.length).format.rowHeight = 28;

  const matrix = rows.map((row) => headers.map((header) => normalize(row[header])));
  const chunkSize = 750;
  for (let start = 0; start < matrix.length; start += chunkSize) {
    const block = matrix.slice(start, start + chunkSize);
    sheet.getRangeByIndexes(5 + start, 0, block.length, headers.length).values = block;
  }

  if (matrix.length > 0) {
    const dataRange = sheet.getRangeByIndexes(5, 0, matrix.length, headers.length);
    dataRange.format = {
      font: { name: "Calibri", size: 9, color: COLORS.dark },
      wrapText: true,
      verticalAlignment: "top",
      borders: { preset: "all", style: "thin", color: "#D9E2F3" },
    };
    const table = sheet.tables.add(`A5:${lastColumn}${5 + matrix.length}`, true, `${name.replace(/[^A-Za-z0-9]/g, "")}Table`);
    table.style = "TableStyleMedium2";
  }

  headers.forEach((_, index) => {
    const width = widths[index] ?? 18;
    sheet.getRange(`${columnLetter(index + 1)}:${columnLetter(index + 1)}`).format.columnWidth = width;
  });
  sheet.freezePanes.freezeRows(5);
  return sheet;
}

function entriesHeaders(rows, fallback) {
  return rows.length > 0 ? Object.keys(rows[0]) : fallback;
}

async function buildMigrationWorkbook() {
  const wb = Workbook.create();
  const channelRows = Object.entries(payload.channel_counts).map(([channel, count]) => ({
    "项目": `文件路由：${channel}`,
    "当前值": count,
    "判断或操作": channel === "Delete Candidate" ? "仅列入审阅，未经用户批准不删除" : "已按通道登记",
  }));
  const summaryRows = [
    { "项目": "扫描文件总数", "当前值": payload.migration.length, "判断或操作": "包含workspace全部受治理文件；运行环境归为可重建" },
    { "项目": "冻结事件数", "当前值": payload.baseline.events, "判断或操作": "必须保持193" },
    { "项目": "冻结股票数", "当前值": payload.baseline.stocks, "判断或操作": "必须保持160" },
    { "项目": "baseline SHA-256", "当前值": payload.baseline.sha256, "判断或操作": "任何变化都应阻断验收" },
    ...channelRows,
    { "项目": "删除执行", "当前值": "未执行", "判断或操作": "本工作簿用于你审阅，不会自动删除或移动原文件" },
  ];
  addDataSheet(wb, {
    name: "Summary", title: "FYP Migration Review", purpose: "迁移总览：每个文件必须进入GitHub、DataVault、可重建、归档或待删除五类之一。",
    source: `Source: catalog/workbook_payload.json; generated ${payload.generated_utc}; no research data were modified.`,
    headers: ["项目", "当前值", "判断或操作"], rows: summaryRows, widths: [28, 58, 66],
  });
  addDataSheet(wb, {
    name: "Inventory", title: "Complete File Routing Inventory", purpose: "逐文件路径、hash、作用、同步位置和删除政策；这是防止孤儿文件的主审阅表。",
    source: "Source: complete workspace scan by tools/project_control.py. SHA-256 is calculated from current bytes.",
    headers: entriesHeaders(payload.migration, ["relative_path"]), rows: payload.migration,
    widths: [62, 14, 22, 66, 20, 20, 18, 12, 46, 50, 42],
  });
  const deletionRows = payload.migration.filter((row) => row.channel === "Delete Candidate");
  addDataSheet(wb, {
    name: "Delete Candidates", title: "Delete Candidates — Approval Required", purpose: "只列出临时/锁文件候选。当前没有执行删除；请在确认后另行授权。",
    source: "Source: migration inventory filtered where channel = Delete Candidate.",
    headers: entriesHeaders(deletionRows, entriesHeaders(payload.migration, ["relative_path"])), rows: deletionRows,
    widths: [62, 14, 22, 66, 20, 20, 18, 12, 46, 50, 42],
  });
  const issues = [
    { "ID": "K01", "问题": "threshold_robustness.xlsx扩展名错误", "影响": "文件实际是JSON，Excel无法正常打开", "当前处理": "原件不覆盖；完整归档；正式汇总读取robustness_summary.xlsx", "状态": "known defect" },
    { "ID": "K02", "问题": "DataVault尚未接入D盘私有云盘", "影响": "当前只有本机D盘第二份，尚不是异地备份", "当前处理": "等待配置支持D盘同步根目录的私有云客户端", "状态": "external action" },
    { "ID": "K03", "问题": "恢复测试依赖远端就绪", "影响": "尚未证明另一目录/设备能完整恢复", "当前处理": "GitHub和云盘完成后，在独立D盘目录执行clone+verify", "状态": "pending" },
    { "ID": "K04", "问题": "HC3不处理同股或同日期相关", "影响": "标准误可能仍偏乐观", "当前处理": "列为后续聚类标准误稳健性，不修改baseline", "状态": "research limitation" },
    { "ID": "K05", "问题": "当前行业快照不是历史行业", "影响": "不能声称已做事件时点行业固定效应", "当前处理": "只作集中度诊断并明确限制", "状态": "research limitation" },
  ];
  addDataSheet(wb, {
    name: "Known Issues", title: "Known Defects and Open Actions", purpose: "区分文件缺陷、外部同步事项和研究限制，避免把未完成事项说成已完成。",
    source: "Source: Research Control Center, audit reports and current environment checks.",
    headers: ["ID", "问题", "影响", "当前处理", "状态"], rows: issues, widths: [12, 44, 48, 68, 22],
  });
  const readmeRows = [
    { "主题": "这本工作簿是什么", "说明": "项目迁移审阅单。它把每个扫描文件分配到一个治理通道，并保存hash。" },
    { "主题": "为什么存在", "说明": "防止旧方案、缓存、论文结果和冻结数据混在一起后无法判断用途。" },
    { "主题": "如何审阅", "说明": "先看Summary，再筛选Inventory的channel/status，最后逐项确认Delete Candidates。" },
    { "主题": "不会做什么", "说明": "不会自动删除、移动或修改baseline；待删除项仍在原位置。" },
    { "主题": "hash原理", "说明": "SHA-256是文件指纹；路径相同但hash不同，说明字节内容发生变化。" },
    { "主题": "更新方法", "说明": "运行project_control.py inventory后重新运行build_control_workbooks.mjs。" },
  ];
  addDataSheet(wb, {
    name: "README", title: "How to Use This Workbook", purpose: "面向项目所有者的使用说明。",
    source: "Status: validated control artifact; research values are not recalculated here.",
    headers: ["主题", "说明"], rows: readmeRows, widths: [32, 110],
  });
  wb.recalculate();
  const blob = await SpreadsheetFile.exportXlsx(wb);
  await blob.save(migrationOutput);
}

async function buildProjectMapWorkbook() {
  const wb = Workbook.create();
  const current = [
    { "分类": "Existing Result", "项目": "Research question", "当前状态或数值": "困境反转突破中，ln_RVOL是否与未来收益及失败风险相关", "证据或下一步": "docs/RESEARCH_CONTROL_CENTER.md" },
    { "分类": "Existing Result", "项目": "Frozen baseline", "当前状态或数值": `${payload.baseline.events} events; ${payload.baseline.stocks} stocks`, "证据或下一步": payload.baseline.sha256 },
    { "分类": "Existing Result", "项目": "Completed", "当前状态或数值": "baseline、稳健性、Failure20/60、市场调整、研究审计、论文Table1-7/Figure1-6", "证据或下一步": "results/ and catalog/artifacts.csv" },
    { "分类": "Existing Result", "项目": "Interpretation", "当前状态或数值": "识别条件相关关系，不识别因果关系", "证据或下一步": "docs/THESIS_EVIDENCE_MATRIX.md" },
    { "分类": "Potential Improvement", "项目": "Inference", "当前状态或数值": "补充stock/date clustered standard errors", "证据或下一步": "不得替代HC3 baseline" },
    { "分类": "Potential Improvement", "项目": "Controls", "当前状态或数值": "年份/市场状态、事件前波动率、历史行业", "证据或下一步": "新结果必须单独登记" },
    { "分类": "Recommended Future Work", "项目": "Execution design", "当前状态或数值": "可构造次日开盘可执行版本", "证据或下一步": "解决close-based信号的时点问题" },
    { "分类": "Open Action", "项目": "Off-device backup", "当前状态或数值": "D盘DataVault已建立，私有云尚未接入", "证据或下一步": "选择同步根目录可位于D盘的客户端" },
  ];
  addDataSheet(wb, {
    name: "Current Status", title: "Research Control Center — One-Minute View", purpose: "一分钟回答做到哪里、哪个数据有效、结果能否写入论文和下一步是什么。",
    source: `Source: Control Center and registries; generated ${payload.generated_utc}.`,
    headers: ["分类", "项目", "当前状态或数值", "证据或下一步"], rows: current, widths: [28, 32, 88, 66],
  });
  addDataSheet(wb, {
    name: "Datasets", title: "Dataset Registry", purpose: "逐个数据集说明来源、层级、公式、输入、hash、同步位置和删除规则。",
    source: "Source: catalog/datasets.csv generated by tools/project_control.py.",
    headers: entriesHeaders(payload.datasets, ["数据ID"]), rows: payload.datasets,
    widths: [12, 30, 52, 16, 48, 34, 58, 45, 28, 18, 18, 14, 30, 68, 30, 18, 16, 16, 54],
  });
  addDataSheet(wb, {
    name: "Code", title: "Code Registry", purpose: "说明每个正式脚本读取什么、写出什么、核心逻辑以及是否影响事件定义。",
    source: "Source: catalog/code_registry.csv and code scan.",
    headers: entriesHeaders(payload.code, ["代码ID"]), rows: payload.code,
    widths: [12, 48, 48, 64, 44, 44, 22, 32, 18, 52],
  });
  const tables = payload.artifacts.filter((row) => row["类型"] === "table");
  const figures = payload.artifacts.filter((row) => row["类型"] === "figure");
  addDataSheet(wb, {
    name: "Tables", title: "Table Evidence Registry", purpose: "把每张论文表与研究问题、数据hash、生成代码、模型和解释边界连接起来。",
    source: "Source: catalog/artifacts.csv filtered to tables.",
    headers: entriesHeaders(tables, ["结果ID"]), rows: tables,
    widths: [12, 28, 14, 52, 38, 48, 38, 42, 62, 36, 24],
  });
  addDataSheet(wb, {
    name: "Figures", title: "Figure Evidence Registry", purpose: "说明图形含义、横纵轴所表达的问题、数据来源、生成代码和论文位置。",
    source: "Source: catalog/artifacts.csv filtered to figures.",
    headers: entriesHeaders(figures, ["结果ID"]), rows: figures,
    widths: [12, 30, 14, 52, 38, 48, 38, 42, 62, 36, 24],
  });
  addDataSheet(wb, {
    name: "Runs", title: "Run Registry", purpose: "连接运行日期、Git commit、输入hash、参数、命令、输出和测试状态。",
    source: "Source: catalog/runs.csv. Historical pre-Git runs are explicitly labeled.",
    headers: entriesHeaders(payload.runs, ["运行ID"]), rows: payload.runs,
    widths: [14, 18, 45, 68, 24, 62, 42, 36, 16, 54],
  });
  addDataSheet(wb, {
    name: "Decisions", title: "Research Decision Log", purpose: "记录为什么采用当前参数、替代方案、代价和重新评估条件。",
    source: "Source: catalog/decisions.csv and docs/DECISION_LOG.md.",
    headers: entriesHeaders(payload.decisions, ["决策ID"]), rows: payload.decisions,
    widths: [14, 18, 44, 48, 42, 62, 48, 56],
  });
  const missing = [
    { "类别": "External sync", "项目": "D盘DataVault私有云副本", "状态": "Pending", "处理": "配置可把同步根目录放在D盘的私有云客户端" },
    { "类别": "Recovery test", "项目": "独立D盘目录clone + DataVault + verify", "状态": "Pending", "处理": "GitHub和云盘远端就绪后执行" },
    { "类别": "Research limitation", "项目": "stock/date clustered standard errors", "状态": "Potential Improvement", "处理": "追加稳健性，不替换现有HC3" },
    { "类别": "Research limitation", "项目": "事件时点历史行业分类", "状态": "Recommended Future Work", "处理": "取得历史数据后再做行业控制" },
  ];
  addDataSheet(wb, {
    name: "Missing", title: "Missing, Unregistered and Open Items", purpose: "列出尚未完成的外部连接和未来改进；未登记文件由自动报告单独核验。",
    source: "Source: current environment checks and research audit. No item here changes the frozen baseline.",
    headers: ["类别", "项目", "状态", "处理"], rows: missing, widths: [28, 58, 28, 78],
  });
  wb.recalculate();
  const blob = await SpreadsheetFile.exportXlsx(wb);
  await blob.save(projectMapOutput);
}

async function verifyAndRender(workbookPath, renderPrefix, sheetSpecs) {
  const input = await FileBlob.load(workbookPath);
  const wb = await SpreadsheetFile.importXlsx(input);
  const overview = await wb.inspect({ kind: "workbook,sheet,table", maxChars: 12000, tableMaxRows: 3, tableMaxCols: 6, tableMaxCellChars: 70 });
  const errors = await wb.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
    options: { useRegex: true, maxResults: 300 },
    summary: "final formula error scan",
  });
  const sheets = await wb.inspect({ kind: "sheet", include: "id,name", maxChars: 6000 });
  const rendered = [];
  for (const { name: sheetName, range } of sheetSpecs) {
    const safeName = sheetName.replace(/[^A-Za-z0-9_-]/g, "_");
    const preview = await wb.render({ sheetName, range, scale: 1, format: "png" });
    const outputPath = path.join(renderRoot, `${renderPrefix}_${safeName}.png`);
    await fs.writeFile(outputPath, new Uint8Array(await preview.arrayBuffer()));
    rendered.push(outputPath);
  }
  return { workbookPath, overview: overview.ndjson, errors: errors.ndjson, sheets: sheets.ndjson, rendered };
}

await buildMigrationWorkbook();
await buildProjectMapWorkbook();
const verification = [
  await verifyAndRender(migrationOutput, "Migration_Review", [
    { name: "Summary", range: "A1:C28" },
    { name: "Inventory", range: "A1:K28" },
    { name: "Delete Candidates", range: "A1:K28" },
    { name: "Known Issues", range: "A1:E28" },
    { name: "README", range: "A1:B28" },
  ]),
  await verifyAndRender(projectMapOutput, "Research_Project_Map", [
    { name: "Current Status", range: "A1:D28" },
    { name: "Datasets", range: "A1:S28" },
    { name: "Code", range: "A1:J28" },
    { name: "Tables", range: "A1:L28" },
    { name: "Figures", range: "A1:L28" },
    { name: "Runs", range: "A1:J28" },
    { name: "Decisions", range: "A1:H28" },
    { name: "Missing", range: "A1:D28" },
  ]),
];
await fs.writeFile(verificationOutput, JSON.stringify({ generated: new Date().toISOString(), verification }, null, 2), "utf8");
console.log(JSON.stringify({ migrationOutput, projectMapOutput, verificationOutput }, null, 2));
