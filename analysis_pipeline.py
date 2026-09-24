"""Reproducible empirical analysis for the frozen A-share event dataset.

The script reads final_analysis_dataset_v1.csv without modifying it, performs
quality checks, RVOL tercile analysis, Pearson correlations, OLS regressions
with HC3 robust standard errors, and writes publication-oriented outputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT = PROJECT_ROOT / "outputs" / "hfq_final" / "final_analysis_dataset_v1.csv"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs" / "hfq_final"
RUNTIME_DIR = PROJECT_ROOT / ".runtime" / "analysis_pipeline"

# D-drive-only Codex runtime paths. They can be overridden for another machine.
DEFAULT_NODE = Path(
    r"D:\programming _Learning\.codex\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
)
DEFAULT_NODE_MODULES = Path(
    r"D:\programming _Learning\.codex\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules"
)

RETURN_WINDOWS = ["R1", "R3", "R5", "R20", "R60"]
REGRESSION_RETURNS = ["R1", "R5", "R20", "R60"]
MODEL_SPECS = {
    "M1": ["ln_RVOL"],
    "M2": ["ln_RVOL", "Drawdown", "Duration", "BreakoutStrength"],
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def clean_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (pd.Timestamp,)):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, dict):
        return {str(key): clean_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(item) for item in value]
    return value


def frame_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return clean_json(frame.to_dict(orient="records"))


def normal_two_sided_p(z_value: float) -> float:
    if not math.isfinite(z_value):
        return math.nan
    return math.erfc(abs(z_value) / math.sqrt(2.0))


def ols_hc3(data: pd.DataFrame, dependent: str, predictors: list[str]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    subset = data[[dependent, *predictors]].dropna().copy()
    y = subset[dependent].to_numpy(dtype=float)
    x_raw = subset[predictors].to_numpy(dtype=float)
    x = np.column_stack([np.ones(len(subset)), x_raw])
    names = ["Intercept", *predictors]

    xtx_inv = np.linalg.pinv(x.T @ x)
    beta = xtx_inv @ x.T @ y
    residual = y - x @ beta
    leverage = np.sum((x @ xtx_inv) * x, axis=1)
    denominator = np.maximum(1.0 - leverage, np.finfo(float).eps)
    adjusted = residual / denominator
    meat = x.T @ (x * np.square(adjusted)[:, None])
    covariance = xtx_inv @ meat @ xtx_inv
    standard_error = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    z_values = np.divide(
        beta,
        standard_error,
        out=np.full_like(beta, np.nan),
        where=standard_error > 0,
    )
    p_values = [normal_two_sided_p(float(value)) for value in z_values]

    total_ss = float(np.sum(np.square(y - y.mean())))
    residual_ss = float(np.sum(np.square(residual)))
    r_squared = math.nan if total_ss == 0 else 1.0 - residual_ss / total_ss
    rows = []
    for idx, term in enumerate(names):
        rows.append(
            {
                "dependent": dependent,
                "term": term,
                "coefficient": float(beta[idx]),
                "hc3_standard_error": float(standard_error[idx]),
                "z_value": float(z_values[idx]),
                "p_value": float(p_values[idx]),
                "n": int(len(subset)),
                "r_squared": float(r_squared),
            }
        )
    summary = {
        "dependent": dependent,
        "n": int(len(subset)),
        "parameters": int(x.shape[1]),
        "r_squared": float(r_squared),
    }
    return rows, summary


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = []
    env_font = os.environ.get("ANALYSIS_FONT")
    if env_font:
        candidates.append(Path(env_font))
    candidates.extend(
        [
            Path(r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf"),
            Path(r"C:\Windows\Fonts\calibrib.ttf" if bold else r"C:\Windows\Fonts\calibri.ttf"),
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def bar_chart(labels: list[str], values: list[float], title: str, output_path: Path) -> None:
    width, height = 1600, 1000
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    title_font = font(44, bold=True)
    axis_font = font(28)
    label_font = font(31, bold=True)
    value_font = font(28)
    left, right, top, bottom = 180, 1500, 125, 820

    draw.text((left, 35), title, fill="#172B4D", font=title_font)
    low = min(0.0, min(values))
    high = max(0.0, max(values))
    span = high - low
    padding = max(span * 0.20, 0.005)
    low -= padding
    high += padding

    def y_pixel(value: float) -> float:
        return bottom - (value - low) / (high - low) * (bottom - top)

    for tick in np.linspace(low, high, 7):
        y = y_pixel(float(tick))
        draw.line((left, y, right, y), fill="#D9E2F3", width=2)
        label = f"{tick:.1%}"
        box = draw.textbbox((0, 0), label, font=axis_font)
        draw.text((left - 20 - (box[2] - box[0]), y - 16), label, fill="#44546A", font=axis_font)
    zero_y = y_pixel(0.0)
    draw.line((left, zero_y, right, zero_y), fill="#7F8C8D", width=3)

    palette = ["#7F8C8D", "#5B9BD5", "#1F4E78"]
    slot = (right - left) / len(labels)
    bar_width = slot * 0.50
    for index, (label, value) in enumerate(zip(labels, values)):
        center = left + slot * (index + 0.5)
        y_value = y_pixel(value)
        y1, y2 = sorted([zero_y, y_value])
        draw.rectangle((center - bar_width / 2, y1, center + bar_width / 2, y2), fill=palette[index])
        label_box = draw.textbbox((0, 0), label, font=label_font)
        draw.text((center - (label_box[2] - label_box[0]) / 2, bottom + 35), label, fill="#172B4D", font=label_font)
        value_label = f"{value:.2%}"
        value_box = draw.textbbox((0, 0), value_label, font=value_font)
        text_y = y_value - 45 if value >= 0 else y_value + 10
        draw.text((center - (value_box[2] - value_box[0]) / 2, text_y), value_label, fill="#172B4D", font=value_font)

    draw.text((left + 390, 920), "RVOL tercile group", fill="#44546A", font=axis_font)
    image.save(output_path, format="PNG", optimize=True)


def scatter_chart(data: pd.DataFrame, intercept: float, slope: float, r_squared: float, output_path: Path) -> None:
    width, height = 1600, 1050
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image, "RGBA")
    title_font = font(44, bold=True)
    axis_font = font(28)
    note_font = font(25)
    left, right, top, bottom = 185, 1500, 130, 860
    x = data["ln_RVOL"].to_numpy(float)
    y = data["R20"].to_numpy(float)
    x_min, x_max = float(x.min()), float(x.max())
    y_min, y_max = float(y.min()), float(y.max())
    x_pad = max((x_max - x_min) * 0.06, 0.05)
    y_pad = max((y_max - y_min) * 0.08, 0.02)
    x_min, x_max = x_min - x_pad, x_max + x_pad
    y_min, y_max = y_min - y_pad, y_max + y_pad

    def x_pixel(value: float) -> float:
        return left + (value - x_min) / (x_max - x_min) * (right - left)

    def y_pixel(value: float) -> float:
        return bottom - (value - y_min) / (y_max - y_min) * (bottom - top)

    draw.text((left, 35), "ln(RVOL) and R20 return", fill="#172B4D", font=title_font)
    for tick in np.linspace(x_min, x_max, 7):
        px = x_pixel(float(tick))
        draw.line((px, top, px, bottom), fill="#E8EEF7", width=2)
        label = f"{tick:.1f}"
        box = draw.textbbox((0, 0), label, font=axis_font)
        draw.text((px - (box[2] - box[0]) / 2, bottom + 20), label, fill="#44546A", font=axis_font)
    for tick in np.linspace(y_min, y_max, 7):
        py = y_pixel(float(tick))
        draw.line((left, py, right, py), fill="#E8EEF7", width=2)
        label = f"{tick:.0%}"
        box = draw.textbbox((0, 0), label, font=axis_font)
        draw.text((left - 20 - (box[2] - box[0]), py - 15), label, fill="#44546A", font=axis_font)
    if y_min <= 0 <= y_max:
        draw.line((left, y_pixel(0), right, y_pixel(0)), fill="#A6A6A6", width=3)
    for x_value, y_value in zip(x, y):
        px, py = x_pixel(float(x_value)), y_pixel(float(y_value))
        draw.ellipse((px - 7, py - 7, px + 7, py + 7), fill=(68, 114, 196, 125), outline=(31, 78, 121, 180), width=1)
    x_line = [float(x.min()), float(x.max())]
    y_line = [intercept + slope * value for value in x_line]
    draw.line((x_pixel(x_line[0]), y_pixel(y_line[0]), x_pixel(x_line[1]), y_pixel(y_line[1])), fill="#C00000", width=6)
    draw.text((left + 455, 950), "ln(RVOL)", fill="#44546A", font=axis_font)
    draw.text((20, top + 310), "R20 return", fill="#44546A", font=axis_font)
    note = f"OLS fit: R20 = {intercept:.4f} + {slope:.4f} × ln(RVOL)    R² = {r_squared:.4f}    N = {len(data)}"
    draw.text((left, 900), note, fill="#44546A", font=note_font)
    image.convert("RGB").save(output_path, format="PNG", optimize=True)


def build_payload(data: pd.DataFrame, source_path: Path, source_hash: str) -> dict[str, Any]:
    required = {
        "stock_code",
        "stock_name",
        "event_date",
        "Drawdown",
        "Duration",
        "bandwidth",
        "BreakoutStrength",
        "RelativeVolume",
        *RETURN_WINDOWS,
    }
    missing_columns = sorted(required.difference(data.columns))
    if missing_columns:
        raise ValueError(f"Required columns missing: {missing_columns}")

    analysis = data.copy()
    numeric_required = ["Drawdown", "Duration", "bandwidth", "BreakoutStrength", "RelativeVolume", *RETURN_WINDOWS]
    for column in numeric_required:
        analysis[column] = pd.to_numeric(analysis[column], errors="coerce")
    if (analysis["RelativeVolume"].dropna() <= 0).any():
        raise ValueError("RelativeVolume contains non-positive values, so ln_RVOL is undefined.")
    analysis["ln_RVOL"] = np.log(analysis["RelativeVolume"])

    parsed_dates = pd.to_datetime(analysis["event_date"], errors="coerce")
    invalid_dates = int(parsed_dates.isna().sum())
    if invalid_dates:
        raise ValueError(f"event_date contains {invalid_dates} unparsable values.")

    stock_counts = (
        analysis.assign(_event_date=parsed_dates)
        .groupby(["stock_code", "stock_name"], dropna=False)
        .agg(event_count=("event_date", "size"), first_event=("_event_date", "min"), last_event=("_event_date", "max"))
        .reset_index()
        .sort_values(["event_count", "stock_code"], ascending=[False, True])
    )
    stock_counts["first_event"] = stock_counts["first_event"].dt.strftime("%Y-%m-%d")
    stock_counts["last_event"] = stock_counts["last_event"].dt.strftime("%Y-%m-%d")

    duplicate_mask = analysis.duplicated(["stock_code", "event_date"], keep=False)
    duplicates = analysis.loc[duplicate_mask, ["stock_code", "stock_name", "event_date"]].copy()
    if len(duplicates):
        duplicates["duplicate_rows"] = duplicates.groupby(["stock_code", "event_date"])["event_date"].transform("size")
        duplicates = duplicates.sort_values(["stock_code", "event_date"])
    duplicate_keys = int(duplicates[["stock_code", "event_date"]].drop_duplicates().shape[0]) if len(duplicates) else 0

    missing_rows = []
    for column in analysis.columns:
        missing_count = int(analysis[column].isna().sum())
        missing_rows.append(
            {
                "variable": column,
                "dtype": str(analysis[column].dtype),
                "missing_count": missing_count,
                "missing_rate": missing_count / len(analysis),
            }
        )
    missing_values = pd.DataFrame(missing_rows).sort_values(["missing_count", "variable"], ascending=[False, True])

    extreme_columns = [
        "stock_code",
        "stock_name",
        "event_date",
        "RelativeVolume",
        "R1",
        "R3",
        "R5",
        "R20",
        "R60",
        "Drawdown",
        "Duration",
        "BreakoutStrength",
    ]
    extremes: dict[str, list[dict[str, Any]]] = {}
    for metric in ["RelativeVolume", "R20", "R60"]:
        top = analysis.loc[analysis[metric].notna(), extreme_columns].nlargest(10, metric).copy()
        top.insert(0, "rank", np.arange(1, len(top) + 1))
        extremes[metric] = frame_records(top)

    q1, q2 = analysis["ln_RVOL"].quantile([1 / 3, 2 / 3], interpolation="linear").tolist()
    analysis["RVOL_group"] = pd.cut(
        analysis["ln_RVOL"],
        bins=[-np.inf, q1, q2, np.inf],
        labels=["Low", "Medium", "High"],
        right=True,
        include_lowest=True,
    )
    group_rows = []
    for group in ["Low", "Medium", "High"]:
        group_data = analysis.loc[analysis["RVOL_group"] == group]
        for window in RETURN_WINDOWS:
            returns = group_data[window].dropna()
            group_rows.append(
                {
                    "group": group,
                    "event_count": int(len(group_data)),
                    "return_window": window,
                    "return_n": int(len(returns)),
                    "missing_count": int(len(group_data) - len(returns)),
                    "mean": float(returns.mean()),
                    "median": float(returns.median()),
                    "positive_rate": float((returns > 0).mean()),
                }
            )
    group_stats = pd.DataFrame(group_rows)
    group_detail = analysis[["stock_code", "stock_name", "event_date", "RelativeVolume", "ln_RVOL", "RVOL_group", *RETURN_WINDOWS]].copy()
    group_detail["RVOL_group"] = group_detail["RVOL_group"].astype("string")

    correlations = []
    for window in RETURN_WINDOWS:
        pair = analysis[["ln_RVOL", window]].dropna()
        correlations.append(
            {
                "x_variable": "ln_RVOL",
                "return_window": window,
                "n": int(len(pair)),
                "pearson_correlation": float(pair["ln_RVOL"].corr(pair[window])),
                "missing_pairs": int(len(analysis) - len(pair)),
            }
        )

    regression_rows: list[dict[str, Any]] = []
    model_summaries: list[dict[str, Any]] = []
    for dependent in REGRESSION_RETURNS:
        for model, predictors in MODEL_SPECS.items():
            rows, summary = ols_hc3(analysis, dependent, predictors)
            for row in rows:
                row["model"] = model
                row["specification"] = dependent + " ~ " + " + ".join(predictors)
            summary["model"] = model
            summary["specification"] = dependent + " ~ " + " + ".join(predictors)
            regression_rows.extend(rows)
            model_summaries.append(summary)

    descriptive_map = [
        ("drawdown", "Drawdown"),
        ("sideways_days", "Duration"),
        ("bandwidth", "bandwidth"),
        ("breakout_strength", "BreakoutStrength"),
        ("RVOL", "RelativeVolume"),
        ("ln_RVOL", "ln_RVOL"),
        *[(window, window) for window in RETURN_WINDOWS],
    ]
    descriptive_rows = []
    for label, column in descriptive_map:
        values = analysis[column].dropna()
        descriptive_rows.append(
            {
                "variable": label,
                "n": int(len(values)),
                "missing": int(len(analysis) - len(values)),
                "mean": float(values.mean()),
                "std_dev": float(values.std(ddof=1)),
                "minimum": float(values.min()),
                "p25": float(values.quantile(0.25, interpolation="linear")),
                "median": float(values.median()),
                "p75": float(values.quantile(0.75, interpolation="linear")),
                "maximum": float(values.max()),
            }
        )

    scatter_data = analysis[["ln_RVOL", "R20"]].dropna().sort_values("ln_RVOL").copy()
    fit_rows = [row for row in regression_rows if row["dependent"] == "R20" and row["model"] == "M1"]
    fit_map = {row["term"]: row for row in fit_rows}
    intercept = fit_map["Intercept"]["coefficient"]
    slope = fit_map["ln_RVOL"]["coefficient"]
    fit_r2 = fit_map["ln_RVOL"]["r_squared"]
    scatter_data["fitted_R20"] = intercept + slope * scatter_data["ln_RVOL"]

    regression_order = [(dependent, model) for dependent in REGRESSION_RETURNS for model in ["M1", "M2"]]
    paper_regression_headers = [f"{dependent} {model}" for dependent, model in regression_order]
    paper_regression_rows = []
    terms = ["Intercept", "ln_RVOL", "Drawdown", "Duration", "BreakoutStrength"]
    lookup = {(row["dependent"], row["model"], row["term"]): row for row in regression_rows}
    summary_lookup = {(row["dependent"], row["model"]): row for row in model_summaries}
    for term in terms:
        coefficients = []
        errors = []
        for dependent, model in regression_order:
            row = lookup.get((dependent, model, term))
            coefficients.append(None if row is None else row["coefficient"])
            errors.append(None if row is None else row["hc3_standard_error"])
        paper_regression_rows.append({"label": term, "row_type": "coefficient", "values": coefficients})
        paper_regression_rows.append({"label": "HC3 SE", "row_type": "standard_error", "values": errors})
    paper_regression_rows.append(
        {"label": "Observations", "row_type": "n", "values": [summary_lookup[key]["n"] for key in regression_order]}
    )
    paper_regression_rows.append(
        {"label": "R-squared", "row_type": "r_squared", "values": [summary_lookup[key]["r_squared"] for key in regression_order]}
    )

    return clean_json(
        {
            "source": {
                "filename": source_path.name,
                "sha256": source_hash,
                "rows": int(len(analysis)),
                "columns": int(len(data.columns)),
                "stocks": int(analysis["stock_code"].nunique(dropna=True)),
            },
            "quality": {
                "duplicate_keys": duplicate_keys,
                "duplicate_rows": int(duplicate_mask.sum()),
                "variables_with_missing": int((missing_values["missing_count"] > 0).sum()),
                "stock_counts": frame_records(stock_counts),
                "duplicates": frame_records(duplicates),
                "missing_values": frame_records(missing_values),
                "extremes": extremes,
            },
            "rvol": {
                "q1": float(q1),
                "q2": float(q2),
                "group_stats": frame_records(group_stats),
                "group_detail": frame_records(group_detail),
            },
            "correlations": correlations,
            "scatter_data": frame_records(scatter_data),
            "scatter_fit": {"intercept": intercept, "slope": slope, "r_squared": fit_r2, "n": int(len(scatter_data))},
            "regressions": regression_rows,
            "model_summaries": model_summaries,
            "descriptive": descriptive_rows,
            "paper_regression_headers": paper_regression_headers,
            "paper_regression_rows": paper_regression_rows,
        }
    )


ARTIFACT_BUILDER = r'''import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [payloadPath, outputDir, previewDir] = process.argv.slice(2);
const data = JSON.parse(await fs.readFile(payloadPath, "utf8"));
await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(previewDir, { recursive: true });

const FONT = "Arial";
const NAVY = "#1F4E78";
const BLUE = "#D9EAF7";
const LIGHT = "#F3F6FA";
const TEXT = "#172B4D";
const LINE = "#D9E2F3";

function colLetter(index) {
  let n = index + 1;
  let result = "";
  while (n > 0) {
    n -= 1;
    result = String.fromCharCode(65 + (n % 26)) + result;
    n = Math.floor(n / 26);
  }
  return result;
}

function setupSheet(sheet, title, subtitle, lastCol) {
  sheet.showGridLines = false;
  sheet.getRange("A2").values = [[title]];
  sheet.getRange(`A2:${lastCol}2`).format = {
    font: { name: FONT, size: 15, bold: true, color: TEXT },
    borders: { bottom: { style: "thin", color: NAVY } },
    verticalAlignment: "center",
  };
  sheet.getRange("A3").values = [[subtitle]];
  sheet.getRange(`A3:${lastCol}3`).format.font = { name: FONT, size: 10, italic: true, color: "#4B5563" };
  sheet.getRange(`A2:${lastCol}3`).format.rowHeight = 24;
}

function addTable(sheet, startRow, headers, rows, options = {}) {
  const startCol = options.startCol ?? 0;
  const endCol = startCol + headers.length - 1;
  const endRow = startRow + rows.length;
  const left = colLetter(startCol);
  const right = colLetter(endCol);
  sheet.getRange(`${left}${startRow}:${right}${endRow}`).values = [headers, ...rows];
  sheet.getRange(`${left}${startRow}:${right}${startRow}`).format = {
    fill: NAVY,
    font: { name: FONT, size: 10, bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    borders: { insideVertical: { style: "thin", color: "#FFFFFF" } },
  };
  if (rows.length) {
    sheet.getRange(`${left}${startRow + 1}:${right}${endRow}`).format = {
      font: { name: FONT, size: 10, color: TEXT },
      verticalAlignment: "center",
      borders: { insideHorizontal: { style: "thin", color: LINE }, bottom: { style: "thin", color: NAVY } },
    };
  }
  for (const [indexText, format] of Object.entries(options.formats ?? {})) {
    const index = Number(indexText) + startCol;
    sheet.getRange(`${colLetter(index)}${startRow + 1}:${colLetter(index)}${endRow}`).format.numberFormat = format;
  }
  (options.widths ?? []).forEach((width, idx) => {
    sheet.getRange(`${colLetter(startCol + idx)}1:${colLetter(startCol + idx)}${Math.max(endRow, 3)}`).format.columnWidth = width;
  });
  sheet.getRange(`${left}${startRow}:${right}${endRow}`).format.verticalAlignment = "center";
  return { endRow, left, right };
}

async function saveWorkbook(workbook, filename, previewSpecs) {
  workbook.recalculate();
  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
    options: { useRegex: true, maxResults: 100 },
    summary: filename + " formula error scan",
  });
  console.log("ERROR_SCAN=" + filename + "=" + errors.ndjson);
  for (const spec of previewSpecs) {
    const preview = await workbook.render({ sheetName: spec.sheet, range: spec.range, scale: 1.2, format: "png" });
    await fs.writeFile(path.join(previewDir, spec.file), new Uint8Array(await preview.arrayBuffer()));
  }
  const output = await SpreadsheetFile.exportXlsx(workbook);
  const outputPath = path.join(outputDir, filename);
  await output.save(outputPath);
  const saved = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
  const primary = previewSpecs[0];
  const savedInspect = await saved.inspect({
    kind: "table",
    range: `${primary.sheet}!${primary.range}`,
    include: "values,formulas",
    tableMaxRows: 10,
    tableMaxCols: 12,
    maxChars: 6000,
  });
  console.log("SAVED_INSPECT=" + filename + "=" + savedInspect.ndjson);
}

// 1. Quality check workbook.
{
  const workbook = Workbook.create();
  const overview = workbook.worksheets.add("概览");
  setupSheet(overview, "数据质量检查", "冻结样本的只读检查；未删除异常值，未修改事件定义", "F");
  const overviewRows = [
    ["源文件", data.source.filename],
    ["源文件 SHA-256", data.source.sha256],
    ["事件数", data.source.rows],
    ["股票数", data.source.stocks],
    ["同股票同日期重复键", data.quality.duplicate_keys],
    ["重复事件行", data.quality.duplicate_rows],
    ["存在缺失的变量数", data.quality.variables_with_missing],
    ["数据处理", "只读检查；异常值保留"],
  ];
  addTable(overview, 6, ["检查项", "结果"], overviewRows, { widths: [28, 72], formats: { 1: "0" } });

  const counts = workbook.worksheets.add("股票事件数");
  setupSheet(counts, "股票代码事件数量统计", "按事件数降序，同数量按股票代码排序", "E");
  addTable(counts, 6, ["股票代码", "股票名称", "事件数", "首个事件日期", "最后事件日期"], data.quality.stock_counts.map(r => [r.stock_code, r.stock_name, r.event_count, r.first_event, r.last_event]), { widths: [18, 18, 12, 16, 16], formats: { 2: "0" } });
  counts.freezePanes.freezeRows(6);

  const dup = workbook.worksheets.add("重复事件");
  setupSheet(dup, "同股票同日期重复事件", "重复键定义：stock_code + event_date", "D");
  const duplicateRows = data.quality.duplicates.length ? data.quality.duplicates.map(r => [r.stock_code, r.stock_name, r.event_date, r.duplicate_rows]) : [["无重复", null, null, 0]];
  addTable(dup, 6, ["股票代码", "股票名称", "事件日期", "重复行数"], duplicateRows, { widths: [18, 18, 18, 14], formats: { 3: "0" } });

  const missing = workbook.worksheets.add("缺失值");
  setupSheet(missing, "所有变量缺失情况", "缺失率分母为193个事件；不进行填补", "D");
  addTable(missing, 6, ["变量", "数据类型", "缺失数", "缺失率"], data.quality.missing_values.map(r => [r.variable, r.dtype, r.missing_count, r.missing_rate]), { widths: [30, 18, 12, 14], formats: { 2: "0", 3: "0.00%" } });
  missing.freezePanes.freezeRows(6);

  const topHeaders = ["排名", "股票代码", "股票名称", "事件日期", "RVOL", "R1", "R3", "R5", "R20", "R60", "Drawdown", "Duration", "BreakoutStrength"];
  const topKeys = ["rank", "stock_code", "stock_name", "event_date", "RelativeVolume", "R1", "R3", "R5", "R20", "R60", "Drawdown", "Duration", "BreakoutStrength"];
  for (const [metric, sheetName] of [["RelativeVolume", "RVOL_Top10"], ["R20", "R20_Top10"], ["R60", "R60_Top10"]]) {
    const sheet = workbook.worksheets.add(sheetName);
    setupSheet(sheet, `${metric === "RelativeVolume" ? "RVOL" : metric} 最大的10个事件`, "按指定变量降序；缺失值不参与排序，事件未删除", "M");
    const rows = data.quality.extremes[metric].map(r => topKeys.map(key => r[key]));
    addTable(sheet, 6, topHeaders, rows, { widths: [9, 16, 16, 15, 12, 12, 12, 12, 12, 12, 13, 12, 18], formats: { 0: "0", 4: "0.0000", 5: "0.00%", 6: "0.00%", 7: "0.00%", 8: "0.00%", 9: "0.00%", 10: "0.00%", 11: "0", 12: "0.00%" } });
  }
  await saveWorkbook(workbook, "quality_check_report.xlsx", [
    { sheet: "概览", range: "A1:F16", file: "quality_overview.png" },
    { sheet: "股票事件数", range: `A1:E${Math.min(data.quality.stock_counts.length + 6, 45)}`, file: "quality_stock_counts.png" },
    { sheet: "重复事件", range: "A1:D12", file: "quality_duplicates.png" },
    { sheet: "缺失值", range: "A1:D45", file: "quality_missing.png" },
    { sheet: "RVOL_Top10", range: "A1:M17", file: "quality_rvol_top10.png" },
    { sheet: "R20_Top10", range: "A1:M17", file: "quality_r20_top10.png" },
    { sheet: "R60_Top10", range: "A1:M17", file: "quality_r60_top10.png" },
  ]);
}

// 2. RVOL tercile workbook and native charts.
{
  const workbook = Workbook.create();
  const summary = workbook.worksheets.add("分组统计");
  setupSheet(summary, "RVOL三分位数组分析", "分组变量：ln_RVOL；各收益窗口按非缺失观测计算", "Q");
  addTable(summary, 6, ["分组边界", "ln_RVOL"], [["Low上限（含）", data.rvol.q1], ["Medium上限（含）", data.rvol.q2]], { widths: [24, 15], formats: { 1: "0.0000" } });
  const statsRows = data.rvol.group_stats.map(r => [r.group, r.event_count, r.return_window, r.return_n, r.missing_count, r.mean, r.median, r.positive_rate]);
  addTable(summary, 11, ["RVOL组", "事件数", "收益窗口", "收益有效数", "缺失数", "平均收益", "中位数收益", "正收益比例"], statsRows, { widths: [14, 12, 14, 14, 11, 15, 15, 15], formats: { 1: "0", 3: "0", 4: "0", 5: "0.00%", 6: "0.00%", 7: "0.00%" } });
  const groups = ["Low", "Medium", "High"];
  const metricRows = (window) => groups.map(group => data.rvol.group_stats.find(r => r.group === group && r.return_window === window));
  summary.getRange("J5:K8").values = [["RVOL组", "R20平均收益"], ...metricRows("R20").map(r => [r.group, r.mean])];
  summary.getRange("M5:N8").values = [["RVOL组", "R60平均收益"], ...metricRows("R60").map(r => [r.group, r.mean])];
  summary.getRange("J5:K5").format = { fill: BLUE, font: { name: FONT, size: 10, bold: true, color: TEXT } };
  summary.getRange("M5:N5").format = { fill: BLUE, font: { name: FONT, size: 10, bold: true, color: TEXT } };
  summary.getRange("K6:K8").format.numberFormat = "0.00%";
  summary.getRange("N6:N8").format.numberFormat = "0.00%";
  const chart20 = summary.charts.add("bar", summary.getRange("J5:K8"));
  chart20.title = "RVOL组别与R20平均收益";
  chart20.titleTextStyle.typeface = FONT;
  chart20.hasLegend = false;
  chart20.xAxis = { axisType: "textAxis", textStyle: { typeface: FONT } };
  chart20.yAxis = { numberFormatCode: "0.0%", numberFormatSourceLinked: false, textStyle: { typeface: FONT } };
  chart20.setPosition("J10", "Q22");
  const chart60 = summary.charts.add("bar", summary.getRange("M5:N8"));
  chart60.title = "RVOL组别与R60平均收益";
  chart60.titleTextStyle.typeface = FONT;
  chart60.hasLegend = false;
  chart60.xAxis = { axisType: "textAxis", textStyle: { typeface: FONT } };
  chart60.yAxis = { numberFormatCode: "0.0%", numberFormatSourceLinked: false, textStyle: { typeface: FONT } };
  chart60.setPosition("J24", "Q36");

  const detail = workbook.worksheets.add("事件分组");
  setupSheet(detail, "事件级RVOL分组", "每个原始事件保留一行；分组仅用于分析输出", "K");
  const detailRows = data.rvol.group_detail.map(r => [r.stock_code, r.stock_name, r.event_date, r.RelativeVolume, r.ln_RVOL, r.RVOL_group, r.R1, r.R3, r.R5, r.R20, r.R60]);
  addTable(detail, 6, ["股票代码", "股票名称", "事件日期", "RVOL", "ln_RVOL", "RVOL组", "R1", "R3", "R5", "R20", "R60"], detailRows, { widths: [16, 16, 15, 12, 12, 12, 12, 12, 12, 12, 12], formats: { 3: "0.0000", 4: "0.0000", 6: "0.00%", 7: "0.00%", 8: "0.00%", 9: "0.00%", 10: "0.00%" } });
  detail.freezePanes.freezeRows(6);
  await saveWorkbook(workbook, "rvol_group_analysis.xlsx", [
    { sheet: "分组统计", range: "A1:Q37", file: "rvol_group_summary.png" },
    { sheet: "事件分组", range: "A1:K35", file: "rvol_group_detail.png" },
  ]);
}

// 3. Correlation workbook.
{
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add("相关性");
  setupSheet(sheet, "Pearson相关性", "ln_RVOL与未来收益；按变量对排除缺失", "E");
  addTable(sheet, 6, ["X变量", "收益变量", "有效数", "Pearson相关系数", "缺失配对数"], data.correlations.map(r => [r.x_variable, r.return_window, r.n, r.pearson_correlation, r.missing_pairs]), { widths: [18, 16, 12, 22, 16], formats: { 2: "0", 3: "0.0000", 4: "0" } });
  sheet.getRange("A14:B17").values = [
    ["R20线性拟合", "结果"],
    ["截距", data.scatter_fit.intercept],
    ["ln_RVOL系数", data.scatter_fit.slope],
    ["R-squared", data.scatter_fit.r_squared],
  ];
  sheet.getRange("A14:B14").format = { fill: BLUE, font: { name: FONT, size: 10, bold: true, color: TEXT }, borders: { bottom: { style: "thin", color: NAVY } } };
  sheet.getRange("A15:B17").format.font = { name: FONT, size: 10, color: TEXT };
  sheet.getRange("B15:B17").format.numberFormat = "0.0000";

  const scatter = workbook.worksheets.add("散点数据");
  setupSheet(scatter, "ln_RVOL与R20散点数据", "线性拟合来自R20模型1；原始收益缺失保持空白", "C");
  addTable(scatter, 6, ["ln_RVOL", "R20", "拟合R20"], data.scatter_data.map(r => [r.ln_RVOL, r.R20, r.fitted_R20]), { widths: [16, 16, 16], formats: { 0: "0.0000", 1: "0.00%", 2: "0.00%" } });
  scatter.freezePanes.freezeRows(6);
  await saveWorkbook(workbook, "correlation_table.xlsx", [
    { sheet: "相关性", range: "A1:E18", file: "correlation_table.png" },
    { sheet: "散点数据", range: "A1:C40", file: "correlation_scatter_data.png" },
  ]);
}

// 4. Regression workbook.
{
  const workbook = Workbook.create();
  const sheet = workbook.worksheets.add("回归结果");
  setupSheet(sheet, "OLS回归结果", "HC3异方差稳健标准误；p-value使用渐近正态检验", "J");
  const rows = data.regressions.map(r => [r.dependent, r.model, r.specification, r.term, r.n, r.coefficient, r.hc3_standard_error, r.z_value, r.p_value, r.r_squared]);
  const table = addTable(sheet, 6, ["因变量", "模型", "模型设定", "变量", "N", "系数", "HC3标准误", "z值", "p-value", "R-squared"], rows, { widths: [12, 10, 48, 22, 10, 15, 17, 13, 15, 16], formats: { 4: "0", 5: "0.000000", 6: "0.000000", 7: "0.000", 8: "0.0000", 9: "0.0000" } });
  sheet.freezePanes.freezeRows(6);
  sheet.getRange(`A${table.endRow + 3}`).values = [["模型说明"]];
  sheet.getRange(`A${table.endRow + 3}:J${table.endRow + 3}`).format = { fill: LIGHT, font: { name: FONT, size: 10, bold: true, color: TEXT }, borders: { bottom: { style: "thin", color: NAVY } } };
  sheet.getRange(`A${table.endRow + 4}:B${table.endRow + 7}`).values = [
    ["M1", "收益 ~ ln_RVOL"],
    ["M2", "收益 ~ ln_RVOL + Drawdown + Duration + BreakoutStrength"],
    ["样本处理", "每个模型仅排除该模型所需变量中的缺失值"],
    ["异常值处理", "未删除，未winsorize"],
  ];
  for (let row = table.endRow + 4; row <= table.endRow + 7; row += 1) sheet.mergeCells(`B${row}:J${row}`);
  sheet.getRange(`A${table.endRow + 4}:J${table.endRow + 7}`).format.font = { name: FONT, size: 9, color: TEXT };
  await saveWorkbook(workbook, "regression_results.xlsx", [{ sheet: "回归结果", range: `A1:J${table.endRow + 8}`, file: "regression_results.png" }]);
}

// 5. Publication tables workbook.
{
  const workbook = Workbook.create();
  const t1 = workbook.worksheets.add("Table1");
  setupSheet(t1, "Table 1. Descriptive statistics", "Returns and ratio variables are stored in decimal units", "J");
  addTable(t1, 6, ["Variable", "N", "Missing", "Mean", "Std. Dev.", "Minimum", "25th pct.", "Median", "75th pct.", "Maximum"], data.descriptive.map(r => [r.variable, r.n, r.missing, r.mean, r.std_dev, r.minimum, r.p25, r.median, r.p75, r.maximum]), { widths: [22, 10, 11, 14, 14, 14, 14, 14, 14, 14], formats: { 1: "0", 2: "0", 3: "0.0000", 4: "0.0000", 5: "0.0000", 6: "0.0000", 7: "0.0000", 8: "0.0000", 9: "0.0000" } });

  const t2 = workbook.worksheets.add("Table2");
  setupSheet(t2, "Table 2. Returns by RVOL tercile", "Low ≤ Q1; Q1 < Medium ≤ Q2; High > Q2", "H");
  addTable(t2, 6, ["RVOL group", "Events", "Return", "N", "Missing", "Mean", "Median", "Positive share"], data.rvol.group_stats.map(r => [r.group, r.event_count, r.return_window, r.return_n, r.missing_count, r.mean, r.median, r.positive_rate]), { widths: [16, 11, 12, 10, 11, 14, 14, 17], formats: { 1: "0", 3: "0", 4: "0", 5: "0.0000", 6: "0.0000", 7: "0.00%" } });

  const t3 = workbook.worksheets.add("Table3");
  setupSheet(t3, "Table 3. Pearson correlations", "Pairwise complete observations", "D");
  addTable(t3, 6, ["X variable", "Return", "N", "Pearson correlation"], data.correlations.map(r => [r.x_variable, r.return_window, r.n, r.pearson_correlation]), { widths: [20, 14, 10, 22], formats: { 2: "0", 3: "0.0000" } });

  const t4 = workbook.worksheets.add("Table4");
  setupSheet(t4, "Table 4. OLS regressions with HC3 standard errors", "M1: ln_RVOL only. M2: adds Drawdown, Duration, and BreakoutStrength", "I");
  const table4Rows = data.paper_regression_rows.map(r => [r.label, ...r.values]);
  const table4 = addTable(t4, 6, ["Variable", ...data.paper_regression_headers], table4Rows, { widths: [22, 14, 14, 14, 14, 14, 14, 14, 14] });
  data.paper_regression_rows.forEach((row, index) => {
    const excelRow = 7 + index;
    let format = "0.000000";
    if (row.row_type === "standard_error") format = "(0.000000)";
    if (row.row_type === "n") format = "0";
    t4.getRange(`B${excelRow}:I${excelRow}`).format.numberFormat = format;
    if (row.row_type === "standard_error") t4.getRange(`A${excelRow}:I${excelRow}`).format.font = { name: FONT, size: 9, italic: true, color: "#4B5563" };
    if (row.row_type === "n" || row.row_type === "r_squared") t4.getRange(`A${excelRow}:I${excelRow}`).format.borders = { top: { style: "thin", color: NAVY } };
  });
  const noteRow = table4.endRow + 3;
  t4.getRange(`A${noteRow}`).values = [["Notes"]];
  t4.getRange(`A${noteRow}:I${noteRow}`).format = { fill: LIGHT, font: { name: FONT, size: 10, bold: true, color: TEXT }, borders: { bottom: { style: "thin", color: NAVY } } };
  t4.getRange(`A${noteRow + 1}`).values = [["Each model retains the original sample and excludes only observations missing required model variables. No outliers are removed or winsorized. HC3 p-values use the asymptotic normal reference distribution."]];
  t4.mergeCells(`A${noteRow + 1}:I${noteRow + 1}`);
  t4.getRange(`A${noteRow + 1}:I${noteRow + 1}`).format = { font: { name: FONT, size: 9, color: TEXT }, wrapText: true, rowHeight: 34 };
  await saveWorkbook(workbook, "paper_tables.xlsx", [
    { sheet: "Table1", range: "A1:J19", file: "paper_table1.png" },
    { sheet: "Table2", range: "A1:H23", file: "paper_table2.png" },
    { sheet: "Table3", range: "A1:D13", file: "paper_table3.png" },
    { sheet: "Table4", range: `A1:I${noteRow + 2}`, file: "paper_table4.png" },
  ]);
}

console.log("WORKBOOKS_CREATED=5");
'''


def ensure_junction(link: Path, target: Path) -> None:
    if link.exists():
        return
    if not target.exists():
        raise FileNotFoundError(f"Artifact-tool node_modules not found: {target}")
    link.parent.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError(f"Could not create node_modules junction: {completed.stdout}\n{completed.stderr}")


def cleanup_runtime() -> None:
    """Remove only this pipeline's temporary directory without traversing its junction."""
    junction = RUNTIME_DIR / "node_modules"
    if junction.exists():
        os.rmdir(junction)
    if RUNTIME_DIR.exists():
        shutil.rmtree(RUNTIME_DIR)


def run_pipeline(input_path: Path, output_dir: Path, keep_runtime: bool = False) -> dict[str, Any]:
    input_path = input_path.resolve()
    output_dir = output_dir.resolve()
    if not input_path.exists():
        raise FileNotFoundError(input_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    source_hash_before = sha256_file(input_path)

    data = pd.read_csv(input_path, dtype={"stock_code": "string", "stock_name": "string"})
    payload = build_payload(data, input_path, source_hash_before)

    rvol_means = {
        window: [
            next(row["mean"] for row in payload["rvol"]["group_stats"] if row["group"] == group and row["return_window"] == window)
            for group in ["Low", "Medium", "High"]
        ]
        for window in ["R20", "R60"]
    }
    bar_chart(["Low", "Medium", "High"], rvol_means["R20"], "RVOL group vs mean R20 return", output_dir / "rvol_group_r20_mean.png")
    bar_chart(["Low", "Medium", "High"], rvol_means["R60"], "RVOL group vs mean R60 return", output_dir / "rvol_group_r60_mean.png")
    scatter_frame = pd.DataFrame(payload["scatter_data"])
    scatter_chart(
        scatter_frame,
        float(payload["scatter_fit"]["intercept"]),
        float(payload["scatter_fit"]["slope"]),
        float(payload["scatter_fit"]["r_squared"]),
        output_dir / "rvol_r20_scatter.png",
    )

    cleanup_runtime()
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    payload_path = RUNTIME_DIR / "analysis_results.json"
    builder_path = RUNTIME_DIR / "artifact_builder.mjs"
    preview_dir = RUNTIME_DIR / "previews"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    builder_path.write_text(ARTIFACT_BUILDER, encoding="utf-8")

    node_path = Path(os.environ.get("CODEX_ARTIFACT_NODE", str(DEFAULT_NODE)))
    node_modules = Path(os.environ.get("CODEX_ARTIFACT_NODE_MODULES", str(DEFAULT_NODE_MODULES)))
    if not node_path.exists():
        raise FileNotFoundError(f"D-drive Node runtime not found: {node_path}")
    ensure_junction(RUNTIME_DIR / "node_modules", node_modules)
    temp_dir = RUNTIME_DIR / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["TEMP"] = str(temp_dir)
    environment["TMP"] = str(temp_dir)
    completed = subprocess.run(
        [str(node_path), str(builder_path), str(payload_path), str(output_dir), str(preview_dir)],
        cwd=RUNTIME_DIR,
        env=environment,
    )
    if completed.returncode != 0:
        raise RuntimeError("Artifact workbook generation failed.")

    expected = [
        "quality_check_report.xlsx",
        "rvol_group_analysis.xlsx",
        "correlation_table.xlsx",
        "regression_results.xlsx",
        "paper_tables.xlsx",
        "rvol_group_r20_mean.png",
        "rvol_group_r60_mean.png",
        "rvol_r20_scatter.png",
    ]
    missing_outputs = [name for name in expected if not (output_dir / name).exists()]
    if missing_outputs:
        raise RuntimeError(f"Expected outputs missing: {missing_outputs}")

    source_hash_after = sha256_file(input_path)
    if source_hash_before != source_hash_after:
        raise RuntimeError("Source CSV hash changed during analysis.")

    summary = {
        "source_sha256": source_hash_before,
        "source_unchanged": True,
        "events": payload["source"]["rows"],
        "stocks": payload["source"]["stocks"],
        "duplicate_keys": payload["quality"]["duplicate_keys"],
        "rvol_cutoffs": [payload["rvol"]["q1"], payload["rvol"]["q2"]],
        "scatter_fit": payload["scatter_fit"],
        "outputs": {name: sha256_file(output_dir / name) for name in expected},
        "preview_dir": str(preview_dir),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if not keep_runtime:
        cleanup_runtime()
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Frozen analysis CSV")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Output directory")
    parser.add_argument("--keep-runtime", action="store_true", help="Keep temporary workbook previews for QA")
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    run_pipeline(arguments.input, arguments.output_dir, arguments.keep_runtime)
