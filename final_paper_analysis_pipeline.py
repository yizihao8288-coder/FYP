"""Generate thesis-ready Chapter 4 tables, figures, and narrative results.

This pipeline reads the frozen 193-event baseline and already-saved robustness
outputs. It does not screen events, alter event dates, remove outliers, or
winsorize observations. Baseline regressions use OLS with HC3 standard errors.
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
from typing import Any, Iterable

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_BASELINE = PROJECT_ROOT / "outputs" / "hfq_final" / "final_analysis_dataset_v1.csv"
DEFAULT_ROBUSTNESS = PROJECT_ROOT / "outputs" / "hfq_final" / "robustness_summary.xlsx"
DEFAULT_FAILURE = PROJECT_ROOT / "outputs" / "hfq_final" / "breakout_failure_analysis.xlsx"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "Final_Empirical_Result"
RUNTIME_DIR = PROJECT_ROOT / ".runtime" / "final_paper_analysis"

DEFAULT_NODE = Path(
    r"D:\programming _Learning\.codex\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
)
DEFAULT_NODE_MODULES = Path(
    r"D:\programming _Learning\.codex\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules"
)
DEFAULT_FONT_DIR = Path(
    r"D:\programming _Learning\.codex\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\Lib\site-packages\reportlab\fonts"
)

RETURN_WINDOWS = ["R1", "R3", "R5", "R20", "R60"]
REGRESSION_RETURNS = ["R1", "R5", "R20", "R60"]
MODEL_SPECS = {
    "Model 1": ["ln_RVOL"],
    "Model 2": ["ln_RVOL", "Drawdown", "Duration", "BreakoutStrength"],
}
GROUP_ORDER = ["Low", "Medium", "High"]
GROUP_LABELS = ["Low RVOL", "Medium RVOL", "High RVOL"]
BASELINE_SHA256 = "94d52af2f33639cc336a6967d3fecfdccc2a2fc3f8b6ae265d116ae457ce0f76"


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
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, dict):
        return {str(key): clean_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_json(item) for item in value]
    return value


def normal_two_sided_p(z_value: float) -> float:
    if not math.isfinite(z_value):
        return math.nan
    return math.erfc(abs(z_value) / math.sqrt(2.0))


def significance(p_value: float) -> str:
    if not math.isfinite(p_value):
        return ""
    if p_value < 0.01:
        return "***"
    if p_value < 0.05:
        return "**"
    if p_value < 0.10:
        return "*"
    return ""


def ols_hc3(
    data: pd.DataFrame,
    dependent: str,
    predictors: list[str],
) -> tuple[pd.DataFrame, dict[str, Any], np.ndarray]:
    subset = data[[dependent, *predictors]].dropna().copy()
    if len(subset) <= len(predictors) + 1:
        raise ValueError(f"Insufficient observations for {dependent} ~ {predictors}")
    y = subset[dependent].to_numpy(dtype=float)
    x = np.column_stack(
        [np.ones(len(subset)), subset[predictors].to_numpy(dtype=float)]
    )
    terms = ["Intercept", *predictors]
    xtx_inverse = np.linalg.pinv(x.T @ x)
    beta = xtx_inverse @ x.T @ y
    residual = y - x @ beta
    leverage = np.sum((x @ xtx_inverse) * x, axis=1)
    denominator = np.maximum(1.0 - leverage, np.finfo(float).eps)
    adjusted_residual = residual / denominator
    meat = x.T @ (x * np.square(adjusted_residual)[:, None])
    covariance = xtx_inverse @ meat @ xtx_inverse
    standard_error = np.sqrt(np.maximum(np.diag(covariance), 0.0))
    z_values = np.divide(
        beta,
        standard_error,
        out=np.full_like(beta, np.nan),
        where=standard_error > 0,
    )
    total_ss = float(np.sum(np.square(y - y.mean())))
    residual_ss = float(np.sum(np.square(residual)))
    r_squared = math.nan if total_ss == 0 else 1.0 - residual_ss / total_ss
    rows = []
    for index, term in enumerate(terms):
        p_value = normal_two_sided_p(float(z_values[index]))
        rows.append(
            {
                "Outcome": dependent,
                "Variable": term,
                "Coefficient": float(beta[index]),
                "HC3 Robust SE": float(standard_error[index]),
                "z-statistic": float(z_values[index]),
                "p-value": p_value,
                "Significance": significance(p_value),
                "N": int(len(subset)),
                "R2": float(r_squared),
            }
        )
    summary = {
        "Outcome": dependent,
        "N": int(len(subset)),
        "R2": float(r_squared),
    }
    return pd.DataFrame(rows), summary, covariance


def prepare_baseline(path: Path) -> pd.DataFrame:
    data = pd.read_csv(
        path,
        dtype={"stock_code": "string", "stock_name": "string"},
        low_memory=False,
    )
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
    missing = sorted(required.difference(data.columns))
    if missing:
        raise ValueError(f"Baseline is missing required columns: {missing}")
    numeric = [
        "Drawdown",
        "Duration",
        "bandwidth",
        "BreakoutStrength",
        "RelativeVolume",
        *RETURN_WINDOWS,
    ]
    for column in numeric:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    if (data["RelativeVolume"].dropna() <= 0).any():
        raise ValueError("RelativeVolume must be positive where observed")
    data["ln_RVOL"] = np.log(data["RelativeVolume"])
    data["event_date"] = pd.to_datetime(data["event_date"], errors="raise")
    if len(data) != 193:
        raise ValueError(f"Frozen baseline must contain 193 events; found {len(data)}")
    if data.duplicated(["stock_code", "event_date"]).any():
        raise ValueError("Frozen baseline contains duplicate stock-date event keys")
    return data


def locate_table(matrix: list[list[Any]], required_header: str) -> pd.DataFrame:
    header_row = None
    for index, row in enumerate(matrix):
        values = ["" if value is None else str(value).strip() for value in row]
        if required_header in values:
            header_row = index
            break
    if header_row is None:
        raise ValueError(f"Could not locate header {required_header!r} in saved workbook")
    headers = ["" if value is None else str(value).strip() for value in matrix[header_row]]
    last_header = max(index for index, value in enumerate(headers) if value)
    headers = headers[: last_header + 1]
    rows: list[list[Any]] = []
    for raw in matrix[header_row + 1 :]:
        row = list(raw[: len(headers)]) + [None] * max(0, len(headers) - len(raw))
        row = row[: len(headers)]
        if all(value is None or str(value).strip() == "" for value in row):
            if rows:
                break
            continue
        rows.append(row)
    return pd.DataFrame(rows, columns=headers)


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
        raise RuntimeError(
            f"Could not create node_modules junction: {completed.stdout}\n{completed.stderr}"
        )


def cleanup_runtime() -> None:
    resolved = RUNTIME_DIR.resolve()
    expected_parent = (PROJECT_ROOT / ".runtime").resolve()
    if resolved.parent != expected_parent:
        raise RuntimeError(f"Refusing to clean unexpected runtime path: {resolved}")
    junction = RUNTIME_DIR / "node_modules"
    if junction.exists():
        os.rmdir(junction)
    if RUNTIME_DIR.exists():
        shutil.rmtree(RUNTIME_DIR)


ARTIFACT_SCRIPT = r'''import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const [mode, ...args] = process.argv.slice(2);

if (mode === "extract") {
  const [robustnessPath, failurePath, outputPath] = args;
  const robustness = await SpreadsheetFile.importXlsx(await FileBlob.load(robustnessPath));
  const failure = await SpreadsheetFile.importXlsx(await FileBlob.load(failurePath));
  const result = {
    core_results: robustness.worksheets.getItem("Core Results").getUsedRange(true).values,
    failure_logit: failure.worksheets.getItem("Logit").getUsedRange(true).values,
    failure_events: failure.worksheets.getItem("Events").getUsedRange(true).values,
  };
  await fs.writeFile(outputPath, JSON.stringify(result));
  console.log("EXTRACTION_COMPLETE");
} else if (mode === "build") {
  const [payloadPath, outputDir, previewDir] = args;
  const payload = JSON.parse(await fs.readFile(payloadPath, "utf8"));
  await fs.mkdir(outputDir, { recursive: true });
  await fs.mkdir(previewDir, { recursive: true });

  const FONT = "Arial";
  const NAVY = "#1F4E78";
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

  for (const book of payload.books) {
    const workbook = Workbook.create();
    const sheet = workbook.worksheets.add(book.sheet_name);
    sheet.showGridLines = false;
    const lastCol = colLetter(book.headers.length - 1);

    sheet.getRange("A2").values = [[book.title]];
    sheet.getRange(`A2:${lastCol}2`).format = {
      font: { name: FONT, size: 14, bold: true, color: TEXT },
      borders: { bottom: { style: "thin", color: NAVY } },
      verticalAlignment: "center",
      rowHeight: 25,
    };
    sheet.getRange("A3").values = [[book.subtitle]];
    sheet.getRange(`A3:${lastCol}3`).format = {
      font: { name: FONT, size: 10, italic: true, color: "#4B5563" },
      verticalAlignment: "center",
      rowHeight: 21,
    };

    const matrix = [book.headers, ...book.rows];
    const endRow = 5 + matrix.length;
    sheet.getRange(`A6:${lastCol}${endRow}`).values = matrix;
    sheet.getRange(`A6:${lastCol}6`).format = {
      fill: NAVY,
      font: { name: FONT, size: 10, bold: true, color: "#FFFFFF" },
      horizontalAlignment: "center",
      verticalAlignment: "center",
      wrapText: true,
      borders: { insideVertical: { style: "thin", color: "#FFFFFF" } },
      rowHeight: 30,
    };
    if (book.rows.length) {
      sheet.getRange(`A7:${lastCol}${endRow}`).format = {
        font: { name: FONT, size: 10, color: TEXT },
        verticalAlignment: "center",
        borders: {
          insideHorizontal: { style: "thin", color: LINE },
          bottom: { style: "thin", color: NAVY },
        },
        rowHeight: 21,
      };
    }
    Object.entries(book.formats ?? {}).forEach(([indexText, numberFormat]) => {
      const index = Number(indexText);
      if (book.rows.length) {
        sheet.getRange(`${colLetter(index)}7:${colLetter(index)}${endRow}`).format.numberFormat = numberFormat;
      }
    });
    book.widths.forEach((width, index) => {
      sheet.getRange(`${colLetter(index)}1:${colLetter(index)}${Math.max(endRow, 7)}`).format.columnWidth = width;
    });

    const notesStart = endRow + 3;
    if (book.notes.length) {
      sheet.getRange(`A${notesStart}`).values = [["Notes"]];
      sheet.getRange(`A${notesStart}:${lastCol}${notesStart}`).format = {
        fill: "#F3F6FA",
        font: { name: FONT, size: 10, bold: true, color: TEXT },
        borders: { bottom: { style: "thin", color: NAVY } },
      };
      book.notes.forEach((note, index) => {
        const row = notesStart + 1 + index;
        sheet.getRange(`A${row}`).values = [[note]];
        sheet.mergeCells(`A${row}:${lastCol}${row}`);
        sheet.getRange(`A${row}:${lastCol}${row}`).format = {
          font: { name: FONT, size: 9, color: "#4B5563" },
          wrapText: true,
          rowHeight: 28,
          verticalAlignment: "center",
        };
      });
    }

    workbook.recalculate();
    const errors = await workbook.inspect({
      kind: "match",
      searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
      options: { useRegex: true, maxResults: 100 },
      summary: book.filename + " formula error scan",
    });
    console.log(`ERROR_SCAN=${book.filename}=${errors.ndjson}`);
    const previewEnd = Math.min(notesStart + book.notes.length + 1, 48);
    const preview = await workbook.render({
      sheetName: book.sheet_name,
      range: `A1:${lastCol}${previewEnd}`,
      scale: 1.25,
      format: "png",
    });
    await fs.writeFile(
      path.join(previewDir, `${path.parse(book.filename).name}.png`),
      new Uint8Array(await preview.arrayBuffer())
    );
    const outputPath = path.join(outputDir, book.filename);
    const output = await SpreadsheetFile.exportXlsx(workbook);
    await output.save(outputPath);
    const reopened = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
    const inspection = await reopened.inspect({
      kind: "table",
      range: `${book.sheet_name}!A1:${lastCol}${Math.min(endRow, 18)}`,
      include: "values,formulas",
      tableMaxRows: 18,
      tableMaxCols: 12,
      maxChars: 7000,
    });
    console.log(`SAVED_INSPECT=${book.filename}=${inspection.ndjson}`);
  }
  console.log(`WORKBOOKS_CREATED=${payload.books.length}`);
} else {
  throw new Error(`Unknown mode: ${mode}`);
}
'''


def run_node(mode: str, arguments: list[Path]) -> None:
    node = Path(os.environ.get("CODEX_ARTIFACT_NODE", str(DEFAULT_NODE)))
    node_modules = Path(
        os.environ.get("CODEX_ARTIFACT_NODE_MODULES", str(DEFAULT_NODE_MODULES))
    )
    if not node.exists():
        raise FileNotFoundError(f"D-drive Node runtime not found: {node}")
    ensure_junction(RUNTIME_DIR / "node_modules", node_modules)
    script_path = RUNTIME_DIR / "artifact_pipeline.mjs"
    script_path.write_text(ARTIFACT_SCRIPT, encoding="utf-8")
    temp_dir = RUNTIME_DIR / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["TEMP"] = str(temp_dir)
    environment["TMP"] = str(temp_dir)
    completed = subprocess.run(
        [str(node), str(script_path), mode, *[str(path) for path in arguments]],
        cwd=RUNTIME_DIR,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="")
    if completed.returncode != 0:
        # On Windows the embedded spreadsheet runtime can terminate with
        # STATUS_STACK_BUFFER_OVERRUN after all awaited authoring, export,
        # render, reopen, and inspection steps have completed. The explicit
        # completion marker makes this narrow post-success case detectable.
        completed_marker = (
            mode == "build" and "WORKBOOKS_CREATED=" in completed.stdout
        )
        if completed.returncode == 3221226505 and completed_marker:
            print(
                "WARNING: spreadsheet runtime exited abnormally after its "
                "completion marker; exported and reopened files will be "
                "validated again by the pipeline."
            )
            return
        raise RuntimeError(
            f"Artifact-tool {mode} step failed with return code {completed.returncode}"
        )


def extract_saved_results(
    robustness_path: Path,
    failure_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    extracted_path = RUNTIME_DIR / "saved_results.json"
    run_node("extract", [robustness_path, failure_path, extracted_path])
    raw = json.loads(extracted_path.read_text(encoding="utf-8"))
    core = locate_table(raw["core_results"], "Analysis")
    logit = locate_table(raw["failure_logit"], "term")
    failure_events = locate_table(raw["failure_events"], "stock_code")
    return core, logit, failure_events


def descriptive_table(data: pd.DataFrame) -> pd.DataFrame:
    variables = [
        ("Drawdown", data["Drawdown"]),
        ("Duration", data["Duration"]),
        ("Bandwidth", data["bandwidth"]),
        ("BreakoutStrength", data["BreakoutStrength"]),
        ("RVOL", data["RelativeVolume"]),
        ("ln_RVOL", data["ln_RVOL"]),
        *((window, data[window]) for window in RETURN_WINDOWS),
    ]
    rows: list[dict[str, Any]] = []
    for label, series in variables:
        observed = pd.to_numeric(series, errors="coerce").dropna()
        rows.append(
            {
                "Variable": label,
                "N": int(observed.size),
                "Missing": int(series.size - observed.size),
                "Mean": float(observed.mean()),
                "Median": float(observed.median()),
                "Std. Dev.": float(observed.std(ddof=1)),
                "Minimum": float(observed.min()),
                "Maximum": float(observed.max()),
                "25th Percentile": float(observed.quantile(0.25)),
                "75th Percentile": float(observed.quantile(0.75)),
            }
        )
    return pd.DataFrame(rows)


def baseline_regression_table(data: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    tables: list[pd.DataFrame] = []
    fitted: dict[str, Any] = {}
    for outcome in REGRESSION_RETURNS:
        for model_label, predictors in MODEL_SPECS.items():
            result, summary, covariance = ols_hc3(data, outcome, predictors)
            result.insert(1, "Model", model_label)
            tables.append(result)
            fitted[f"{outcome}|{model_label}"] = {
                "rows": result.copy(),
                "summary": summary,
                "covariance": covariance,
            }
    columns = [
        "Outcome",
        "Model",
        "Variable",
        "N",
        "Coefficient",
        "HC3 Robust SE",
        "p-value",
        "Significance",
        "R2",
    ]
    return pd.concat(tables, ignore_index=True)[columns], fitted


def assign_rvol_groups(values: pd.Series) -> pd.Categorical:
    return pd.qcut(values, q=3, labels=GROUP_ORDER, duplicates="raise")


def rvol_group_table(data: pd.DataFrame) -> tuple[pd.DataFrame, tuple[float, float]]:
    grouped = data.copy()
    grouped["RVOL Group"] = assign_rvol_groups(grouped["ln_RVOL"])
    cutoffs = tuple(float(value) for value in grouped["ln_RVOL"].quantile([1 / 3, 2 / 3]))
    rows: list[dict[str, Any]] = []
    for label in GROUP_ORDER:
        part = grouped.loc[grouped["RVOL Group"] == label]
        r20 = part["R20"].dropna()
        r60 = part["R60"].dropna()
        rows.append(
            {
                "RVOL Group": label,
                "Event Count": int(len(part)),
                "Valid R20 N": int(r20.size),
                "Valid R60 N": int(r60.size),
                "Mean R20": float(r20.mean()),
                "Mean R60": float(r60.mean()),
                "Median R20": float(r20.median()),
                "Positive R20 Ratio": float((r20 > 0).mean()),
            }
        )
    return pd.DataFrame(rows), cutoffs


def coerce_saved_results(
    core: pd.DataFrame,
    logit: pd.DataFrame,
    failure_events: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    core = core.copy()
    logit = logit.copy()
    failure_events = failure_events.copy()
    for column in ["N", "RVOL coefficient", "SE", "p-value", "R2", "Odds ratio"]:
        core[column] = pd.to_numeric(core[column], errors="coerce")
    for column in [
        "coefficient",
        "standard_error",
        "z_value",
        "p_value",
        "odds_ratio",
        "n",
        "mcfadden_pseudo_r2",
    ]:
        logit[column] = pd.to_numeric(logit[column], errors="coerce")
    failure_events["stock_code"] = (
        failure_events["stock_code"].astype("string").str.replace(r"\.0$", "", regex=True).str.zfill(6)
    )
    failure_events["event_date"] = pd.to_datetime(
        failure_events["event_date"], errors="raise"
    )
    for column in ["Failure20", "ln_RVOL60"]:
        failure_events[column] = pd.to_numeric(failure_events[column], errors="coerce")
    return core, logit, failure_events


def select_core_row(
    core: pd.DataFrame,
    analysis: str,
    variant: str,
    outcome: str = "R20",
    model: str = "M2",
) -> pd.Series:
    mask = (
        core["Analysis"].astype(str).eq(analysis)
        & core["Variant"].astype(str).eq(variant)
        & core["Outcome"].astype(str).eq(outcome)
        & core["Model"].astype(str).eq(model)
    )
    rows = core.loc[mask]
    if len(rows) != 1:
        raise ValueError(
            f"Expected one saved result for {analysis}/{variant}/{outcome}/{model}; "
            f"found {len(rows)}"
        )
    return rows.iloc[0]


def robustness_tables(
    core: pd.DataFrame,
    logit: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    # The maximum valid N across the saved return models equals the event sample
    # for each re-screened robustness specification (R5 is complete here).
    threshold_event_counts = {
        variant: int(
            core.loc[
                core["Analysis"].astype(str).eq("Breakout threshold")
                & core["Variant"].astype(str).eq(variant),
                "N",
            ].max()
        )
        for variant in ["0.5%", "1%", "2%"]
    }
    threshold_rows: list[dict[str, Any]] = []
    for variant in ["0.5%", "1%", "2%"]:
        row = select_core_row(core, "Breakout threshold", variant)
        threshold_rows.append(
            {
                "Threshold": variant,
                "Event Sample": threshold_event_counts[variant],
                "Regression N": int(row["N"]),
                "ln_RVOL Coefficient": float(row["RVOL coefficient"]),
                "HC3 Robust SE": float(row["SE"]),
                "p-value": float(row["p-value"]),
                "Significance": significance(float(row["p-value"])),
                "R2": float(row["R2"]),
            }
        )
    table4 = pd.DataFrame(threshold_rows)

    baseline = select_core_row(core, "Breakout threshold", "0.5%")
    atr = select_core_row(core, "ATR20 breakout", "Close > resistance + ATR20")
    atr_event_count = int(
        core.loc[core["Analysis"].astype(str).eq("ATR20 breakout"), "N"].max()
    )
    table5 = pd.DataFrame(
        [
            {
                "Specification": "Baseline 0.5%",
                "Event Sample": 193,
                "Regression N": int(baseline["N"]),
                "ln_RVOL Coefficient": float(baseline["RVOL coefficient"]),
                "HC3 Robust SE": float(baseline["SE"]),
                "p-value": float(baseline["p-value"]),
                "Significance": significance(float(baseline["p-value"])),
                "R2": float(baseline["R2"]),
            },
            {
                "Specification": "ATR20 Breakout",
                "Event Sample": atr_event_count,
                "Regression N": int(atr["N"]),
                "ln_RVOL Coefficient": float(atr["RVOL coefficient"]),
                "HC3 Robust SE": float(atr["SE"]),
                "p-value": float(atr["p-value"]),
                "Significance": significance(float(atr["p-value"])),
                "R2": float(atr["R2"]),
            },
        ]
    )

    volume_rows: list[dict[str, Any]] = []
    volume_event_count = int(
        core.loc[core["Analysis"].astype(str).eq("Volume window"), "N"].max()
    )
    for window in ["RVOL20", "RVOL60", "RVOL120"]:
        row = select_core_row(core, "Volume window", window)
        volume_rows.append(
            {
                "Volume Benchmark": window,
                "Event Sample": volume_event_count,
                "Regression N": int(row["N"]),
                "ln_RVOL Coefficient": float(row["RVOL coefficient"]),
                "HC3 Robust SE": float(row["SE"]),
                "p-value": float(row["p-value"]),
                "Significance": significance(float(row["p-value"])),
                "R2": float(row["R2"]),
            }
        )
    table6 = pd.DataFrame(volume_rows)

    table7 = logit.rename(
        columns={
            "term": "Variable",
            "coefficient": "Coefficient",
            "standard_error": "Standard Error",
            "z_value": "z-statistic",
            "p_value": "p-value",
            "odds_ratio": "Odds Ratio",
            "n": "N",
            "mcfadden_pseudo_r2": "McFadden Pseudo R2",
        }
    )[
        [
            "Variable",
            "N",
            "Coefficient",
            "Standard Error",
            "p-value",
            "Odds Ratio",
            "McFadden Pseudo R2",
        ]
    ].copy()
    table7.insert(
        6,
        "Significance",
        [significance(float(value)) for value in table7["p-value"]],
    )
    table7["N"] = table7["N"].astype(int)
    return table4, table5, table6, table7


def failure_group_table(failure_events: pd.DataFrame) -> pd.DataFrame:
    if failure_events["Failure20"].isna().any():
        raise ValueError("Failure20 contains missing values in the saved failure dataset")
    grouped = failure_events.copy()
    grouped["RVOL Group"] = assign_rvol_groups(grouped["ln_RVOL60"])
    rows = []
    for label in GROUP_ORDER:
        part = grouped.loc[grouped["RVOL Group"] == label, "Failure20"]
        rows.append(
            {
                "RVOL Group": label,
                "Event Count": int(part.size),
                "Failures": int(part.sum()),
                "Observed Failure Rate": float(part.mean()),
            }
        )
    return pd.DataFrame(rows)


def blend_with_white(color: str, alpha: float) -> str:
    color = color.lstrip("#")
    channels = [int(color[index : index + 2], 16) for index in (0, 2, 4)]
    blended = [round(255 * (1 - alpha) + channel * alpha) for channel in channels]
    return "#" + "".join(f"{channel:02X}" for channel in blended)


class RasterSurface:
    width = 700
    height = 500
    scale = 3

    def __init__(self, font_dir: Path) -> None:
        self.image = Image.new(
            "RGB", (self.width * self.scale, self.height * self.scale), "white"
        )
        self.draw = ImageDraw.Draw(self.image)
        self.font_dir = font_dir
        self.font_cache: dict[tuple[int, bool, bool], ImageFont.FreeTypeFont] = {}

    def font(self, points: int, bold: bool = False, italic: bool = False) -> ImageFont.FreeTypeFont:
        key = (points, bold, italic)
        if key not in self.font_cache:
            filename = "VeraBd.ttf" if bold else "VeraIt.ttf" if italic else "Vera.ttf"
            path = self.font_dir / filename
            self.font_cache[key] = ImageFont.truetype(
                str(path), round(points * 300 / 72)
            )
        return self.font_cache[key]

    def line(
        self,
        points: list[tuple[float, float]],
        color: str,
        width: float = 1.0,
        dash: tuple[float, float] | None = None,
    ) -> None:
        scaled = [(round(x * self.scale), round(y * self.scale)) for x, y in points]
        if dash is None:
            self.draw.line(scaled, fill=color, width=max(1, round(width * self.scale)))
            return
        for start, end in zip(scaled, scaled[1:]):
            x1, y1 = start
            x2, y2 = end
            length = math.hypot(x2 - x1, y2 - y1)
            if length == 0:
                continue
            on, off = dash[0] * self.scale, dash[1] * self.scale
            distance = 0.0
            while distance < length:
                segment_end = min(distance + on, length)
                a = distance / length
                b = segment_end / length
                p1 = (round(x1 + (x2 - x1) * a), round(y1 + (y2 - y1) * a))
                p2 = (round(x1 + (x2 - x1) * b), round(y1 + (y2 - y1) * b))
                self.draw.line(
                    [p1, p2], fill=color, width=max(1, round(width * self.scale))
                )
                distance += on + off

    def rect(
        self,
        box: tuple[float, float, float, float],
        fill: str | None = None,
        outline: str | None = None,
        width: float = 1.0,
        alpha: float = 1.0,
    ) -> None:
        scaled = tuple(round(value * self.scale) for value in box)
        fill_color = blend_with_white(fill, alpha) if fill and alpha < 1 else fill
        self.draw.rectangle(
            scaled,
            fill=fill_color,
            outline=outline,
            width=max(1, round(width * self.scale)),
        )

    def polygon(
        self,
        points: list[tuple[float, float]],
        fill: str,
        alpha: float = 1.0,
        outline: str | None = None,
    ) -> None:
        scaled = [(round(x * self.scale), round(y * self.scale)) for x, y in points]
        fill_color = blend_with_white(fill, alpha) if alpha < 1 else fill
        self.draw.polygon(scaled, fill=fill_color, outline=outline)

    def circle(
        self,
        x: float,
        y: float,
        radius: float,
        fill: str,
        outline: str | None = None,
        alpha: float = 1.0,
    ) -> None:
        box = (
            round((x - radius) * self.scale),
            round((y - radius) * self.scale),
            round((x + radius) * self.scale),
            round((y + radius) * self.scale),
        )
        fill_color = blend_with_white(fill, alpha) if alpha < 1 else fill
        self.draw.ellipse(box, fill=fill_color, outline=outline)

    def text(
        self,
        x: float,
        y: float,
        value: str,
        points: int,
        color: str = "#172B4D",
        bold: bool = False,
        italic: bool = False,
        anchor: str = "mm",
        rotate: float = 0.0,
    ) -> None:
        font = self.font(points, bold=bold, italic=italic)
        position = (round(x * self.scale), round(y * self.scale))
        if rotate == 0:
            self.draw.text(position, value, font=font, fill=color, anchor=anchor)
            return
        bbox = self.draw.textbbox((0, 0), value, font=font)
        padding = 12 * self.scale
        layer = Image.new(
            "RGBA",
            (bbox[2] - bbox[0] + 2 * padding, bbox[3] - bbox[1] + 2 * padding),
            (255, 255, 255, 0),
        )
        layer_draw = ImageDraw.Draw(layer)
        layer_draw.text(
            (padding - bbox[0], padding - bbox[1]), value, font=font, fill=color
        )
        rotated = layer.rotate(rotate, expand=True, resample=Image.Resampling.BICUBIC)
        top_left = (
            round(position[0] - rotated.width / 2),
            round(position[1] - rotated.height / 2),
        )
        self.image.paste(rotated, top_left, rotated)
        self.draw = ImageDraw.Draw(self.image)

    def save(self, path: Path) -> None:
        self.image.save(path, format="PNG", dpi=(300, 300), optimize=True)


class VectorSurface:
    width = 700
    height = 500
    scale = 72 / 100

    def __init__(self, path: Path, font_dir: Path) -> None:
        for name, filename in [
            ("Vera", "Vera.ttf"),
            ("Vera-Bold", "VeraBd.ttf"),
            ("Vera-Italic", "VeraIt.ttf"),
        ]:
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, str(font_dir / filename)))
        self.canvas = pdf_canvas.Canvas(
            str(path), pagesize=(self.width * self.scale, self.height * self.scale)
        )

    def _x(self, value: float) -> float:
        return value * self.scale

    def _y(self, value: float) -> float:
        return (self.height - value) * self.scale

    def line(
        self,
        points: list[tuple[float, float]],
        color: str,
        width: float = 1.0,
        dash: tuple[float, float] | None = None,
    ) -> None:
        self.canvas.saveState()
        self.canvas.setStrokeColor(color)
        self.canvas.setLineWidth(width * self.scale)
        self.canvas.setDash(*[value * self.scale for value in dash]) if dash else self.canvas.setDash()
        path = self.canvas.beginPath()
        path.moveTo(self._x(points[0][0]), self._y(points[0][1]))
        for x, y in points[1:]:
            path.lineTo(self._x(x), self._y(y))
        self.canvas.drawPath(path, stroke=1, fill=0)
        self.canvas.restoreState()

    def rect(
        self,
        box: tuple[float, float, float, float],
        fill: str | None = None,
        outline: str | None = None,
        width: float = 1.0,
        alpha: float = 1.0,
    ) -> None:
        x1, y1, x2, y2 = box
        self.canvas.saveState()
        if fill:
            self.canvas.setFillColor(fill)
            if hasattr(self.canvas, "setFillAlpha"):
                self.canvas.setFillAlpha(alpha)
        if outline:
            self.canvas.setStrokeColor(outline)
        self.canvas.setLineWidth(width * self.scale)
        self.canvas.rect(
            self._x(x1),
            self._y(y2),
            self._x(x2 - x1),
            self._x(y2 - y1),
            stroke=1 if outline else 0,
            fill=1 if fill else 0,
        )
        self.canvas.restoreState()

    def polygon(
        self,
        points: list[tuple[float, float]],
        fill: str,
        alpha: float = 1.0,
        outline: str | None = None,
    ) -> None:
        self.canvas.saveState()
        self.canvas.setFillColor(fill)
        if hasattr(self.canvas, "setFillAlpha"):
            self.canvas.setFillAlpha(alpha)
        if outline:
            self.canvas.setStrokeColor(outline)
        path = self.canvas.beginPath()
        path.moveTo(self._x(points[0][0]), self._y(points[0][1]))
        for x, y in points[1:]:
            path.lineTo(self._x(x), self._y(y))
        path.close()
        self.canvas.drawPath(path, stroke=1 if outline else 0, fill=1)
        self.canvas.restoreState()

    def circle(
        self,
        x: float,
        y: float,
        radius: float,
        fill: str,
        outline: str | None = None,
        alpha: float = 1.0,
    ) -> None:
        self.canvas.saveState()
        self.canvas.setFillColor(fill)
        if hasattr(self.canvas, "setFillAlpha"):
            self.canvas.setFillAlpha(alpha)
        if outline:
            self.canvas.setStrokeColor(outline)
        self.canvas.circle(
            self._x(x),
            self._y(y),
            radius * self.scale,
            stroke=1 if outline else 0,
            fill=1,
        )
        self.canvas.restoreState()

    def text(
        self,
        x: float,
        y: float,
        value: str,
        points: int,
        color: str = "#172B4D",
        bold: bool = False,
        italic: bool = False,
        anchor: str = "mm",
        rotate: float = 0.0,
    ) -> None:
        font_name = "Vera-Bold" if bold else "Vera-Italic" if italic else "Vera"
        width = pdfmetrics.stringWidth(value, font_name, points)
        dx = 0.0
        if anchor.startswith("m"):
            dx = -width / 2
        elif anchor.startswith("r"):
            dx = -width
        self.canvas.saveState()
        self.canvas.setFillColor(color)
        self.canvas.setFont(font_name, points)
        self.canvas.translate(self._x(x), self._y(y))
        self.canvas.rotate(rotate)
        self.canvas.drawString(dx, -points * 0.35, value)
        self.canvas.restoreState()

    def save(self, path: Path | None = None) -> None:
        self.canvas.showPage()
        self.canvas.save()


def draw_dual_figure(
    output_base: Path,
    draw_function: Any,
    font_dir: Path,
) -> None:
    png_path = output_base.with_suffix(".png")
    pdf_path = output_base.with_suffix(".pdf")
    raster = RasterSurface(font_dir)
    draw_function(raster)
    raster.save(png_path)
    vector = VectorSurface(pdf_path, font_dir)
    draw_function(vector)
    vector.save()


def draw_common_title(surface: Any, title: str, subtitle: str | None = None) -> None:
    surface.text(72, 30, title, 13, bold=True, anchor="lm")
    if subtitle:
        surface.text(72, 53, subtitle, 9, color="#4B5563", anchor="lm")


def linear_scale(value: float, lower: float, upper: float, pixel1: float, pixel2: float) -> float:
    if upper == lower:
        return (pixel1 + pixel2) / 2
    return pixel1 + (value - lower) / (upper - lower) * (pixel2 - pixel1)


def draw_axes(
    surface: Any,
    bounds: tuple[float, float, float, float],
    x_label: str,
    y_label: str,
    y_ticks: Iterable[float],
    y_min: float,
    y_max: float,
    y_formatter: Any,
) -> None:
    left, top, right, bottom = bounds
    for value in y_ticks:
        y = linear_scale(float(value), y_min, y_max, bottom, top)
        surface.line([(left, y), (right, y)], "#D9D9D9", 0.6)
        surface.text(left - 10, y, y_formatter(float(value)), 9, anchor="rm")
    surface.line([(left, top), (left, bottom), (right, bottom)], "#4D4D4D", 0.9)
    surface.text((left + right) / 2, 470, x_label, 10)
    surface.text(28, (top + bottom) / 2, y_label, 10, rotate=90)


def bar_figure(
    labels: list[str],
    values: list[float],
    counts: list[int],
    title: str,
    y_label: str,
    output_base: Path,
    font_dir: Path,
    fixed_range: tuple[float, float] | None = None,
) -> None:
    colors = ["#BFD7EA", "#6BAED6", "#2171B5"]

    def render(surface: Any) -> None:
        draw_common_title(surface, title)
        left, top, right, bottom = 92, 72, 670, 415
        if fixed_range:
            y_min, y_max = fixed_range
        else:
            low, high = min([0.0, *values]), max([0.0, *values])
            span = high - low or 0.01
            y_min = low - span * 0.18
            y_max = high + span * 0.18
        ticks = np.linspace(y_min, y_max, 6)
        draw_axes(
            surface,
            (left, top, right, bottom),
            "RVOL tercile group",
            y_label,
            ticks,
            y_min,
            y_max,
            lambda value: f"{100 * value:.1f}%",
        )
        if y_min <= 0 <= y_max:
            zero_y = linear_scale(0, y_min, y_max, bottom, top)
            surface.line([(left, zero_y), (right, zero_y)], "#4D4D4D", 1.0)
        centers = np.linspace(left + 95, right - 95, len(values))
        bar_width = 86
        for index, (x, value) in enumerate(zip(centers, values)):
            y_value = linear_scale(value, y_min, y_max, bottom, top)
            y_zero = linear_scale(0, y_min, y_max, bottom, top)
            surface.rect(
                (x - bar_width / 2, min(y_value, y_zero), x + bar_width / 2, max(y_value, y_zero)),
                fill=colors[index],
            )
            surface.text(x, bottom + 20, labels[index], 9)
            surface.text(x, bottom + 38, f"n = {counts[index]}", 8, color="#4B5563")
            label_y = y_value - 14 if value >= 0 else y_value + 14
            surface.text(x, label_y, f"{100 * value:.2f}%", 9, bold=True)

    draw_dual_figure(output_base, render, font_dir)


def scatter_figure(
    data: pd.DataFrame,
    fitted: dict[str, Any],
    output_base: Path,
    font_dir: Path,
) -> None:
    subset = data[["ln_RVOL", "R20"]].dropna()
    x = subset["ln_RVOL"].to_numpy(dtype=float)
    y = subset["R20"].to_numpy(dtype=float)
    fit_rows = fitted["R20|Model 1"]["rows"].set_index("Variable")
    intercept = float(fit_rows.loc["Intercept", "Coefficient"])
    slope = float(fit_rows.loc["ln_RVOL", "Coefficient"])
    covariance = np.asarray(fitted["R20|Model 1"]["covariance"], dtype=float)
    correlation = float(np.corrcoef(x, y)[0, 1])
    x_grid = np.linspace(x.min(), x.max(), 160)
    design = np.column_stack([np.ones(x_grid.size), x_grid])
    y_fit = intercept + slope * x_grid
    fit_se = np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", design, covariance, design), 0))
    ci_low = y_fit - 1.96 * fit_se
    ci_high = y_fit + 1.96 * fit_se
    x_min, x_max = float(x.min() - 0.08), float(x.max() + 0.08)
    y_min = float(min(y.min(), ci_low.min(), 0) - 0.035)
    y_max = float(max(y.max(), ci_high.max(), 0) + 0.035)

    def render(surface: Any) -> None:
        draw_common_title(
            surface,
            "Abnormal Trading Volume and Subsequent Returns",
            "OLS fitted line with a 95% HC3 confidence band for the conditional mean",
        )
        left, top, right, bottom = 92, 82, 670, 415
        ticks = np.linspace(y_min, y_max, 6)
        draw_axes(
            surface,
            (left, top, right, bottom),
            "Log relative volume, ln(RVOL)",
            "20-trading-day return (%)",
            ticks,
            y_min,
            y_max,
            lambda value: f"{100 * value:.0f}%",
        )
        x_ticks = np.linspace(x_min, x_max, 6)
        for value in x_ticks:
            px = linear_scale(value, x_min, x_max, left, right)
            surface.text(px, bottom + 18, f"{value:.1f}", 9)
        zero_y = linear_scale(0, y_min, y_max, bottom, top)
        surface.line([(left, zero_y), (right, zero_y)], "#8A8A8A", 0.8, dash=(5, 4))
        polygon = [
            (
                linear_scale(value, x_min, x_max, left, right),
                linear_scale(high, y_min, y_max, bottom, top),
            )
            for value, high in zip(x_grid, ci_high)
        ] + [
            (
                linear_scale(value, x_min, x_max, left, right),
                linear_scale(low, y_min, y_max, bottom, top),
            )
            for value, low in zip(x_grid[::-1], ci_low[::-1])
        ]
        surface.polygon(polygon, "#D55E00", alpha=0.16)
        for xv, yv in zip(x, y):
            surface.circle(
                linear_scale(xv, x_min, x_max, left, right),
                linear_scale(yv, y_min, y_max, bottom, top),
                2.4,
                "#0072B2",
                alpha=0.32,
            )
        fit_points = [
            (
                linear_scale(value, x_min, x_max, left, right),
                linear_scale(prediction, y_min, y_max, bottom, top),
            )
            for value, prediction in zip(x_grid, y_fit)
        ]
        surface.line(fit_points, "#D55E00", 2.0)
        box_left, box_top = 470, 95
        surface.rect((box_left, box_top, 657, 142), fill="#FFFFFF", outline="#D9D9D9")
        surface.text(
            box_left + 12,
            box_top + 15,
            f"Pearson r = {correlation:.3f}",
            9,
            bold=True,
            anchor="lm",
        )
        surface.text(
            box_left + 12,
            box_top + 34,
            f"N = {len(subset)}",
            9,
            anchor="lm",
        )

    draw_dual_figure(output_base, render, font_dir)


def coefficient_figure(
    labels: list[str],
    coefficients: list[float],
    standard_errors: list[float],
    title: str,
    x_label: str,
    output_base: Path,
    font_dir: Path,
) -> None:
    lower = np.asarray(coefficients) - 1.96 * np.asarray(standard_errors)
    upper = np.asarray(coefficients) + 1.96 * np.asarray(standard_errors)
    low = float(min(0.0, lower.min()))
    high = float(max(0.0, upper.max()))
    span = high - low or 0.1
    y_min, y_max = low - span * 0.18, high + span * 0.18

    def render(surface: Any) -> None:
        draw_common_title(
            surface,
            title,
            "Controlled R20 specification; error bars show 95% HC3 intervals",
        )
        left, top, right, bottom = 92, 82, 670, 415
        ticks = np.linspace(y_min, y_max, 6)
        draw_axes(
            surface,
            (left, top, right, bottom),
            x_label,
            "Coefficient on ln(RVOL)",
            ticks,
            y_min,
            y_max,
            lambda value: f"{value:.3f}",
        )
        zero_y = linear_scale(0, y_min, y_max, bottom, top)
        surface.line([(left, zero_y), (right, zero_y)], "#4D4D4D", 1.0)
        xs = np.linspace(left + 90, right - 90, len(labels))
        points: list[tuple[float, float]] = []
        for x_pos, label, coefficient, lo, hi in zip(
            xs, labels, coefficients, lower, upper
        ):
            y_pos = linear_scale(coefficient, y_min, y_max, bottom, top)
            y_lo = linear_scale(float(lo), y_min, y_max, bottom, top)
            y_hi = linear_scale(float(hi), y_min, y_max, bottom, top)
            surface.line([(x_pos, y_lo), (x_pos, y_hi)], "#2171B5", 1.6)
            surface.line([(x_pos - 7, y_lo), (x_pos + 7, y_lo)], "#2171B5", 1.6)
            surface.line([(x_pos - 7, y_hi), (x_pos + 7, y_hi)], "#2171B5", 1.6)
            surface.circle(x_pos, y_pos, 5.0, "#2171B5")
            surface.text(x_pos, bottom + 20, label, 9)
            surface.text(x_pos, y_pos - 16, f"{coefficient:.3f}", 9, bold=True)
            points.append((x_pos, y_pos))
        surface.line(points, "#2171B5", 1.7)

    draw_dual_figure(output_base, render, font_dir)


def frame_to_book(
    filename: str,
    sheet_name: str,
    title: str,
    subtitle: str,
    frame: pd.DataFrame,
    widths: list[float],
    formats: dict[int, str],
    notes: list[str],
) -> dict[str, Any]:
    if len(widths) != len(frame.columns):
        raise ValueError(f"Column width count does not match {filename}")
    return {
        "filename": filename,
        "sheet_name": sheet_name,
        "title": title,
        "subtitle": subtitle,
        "headers": frame.columns.tolist(),
        "rows": clean_json(frame.to_numpy().tolist()),
        "widths": widths,
        "formats": {str(key): value for key, value in formats.items()},
        "notes": notes,
    }


def workbook_payload(
    table1: pd.DataFrame,
    table2: pd.DataFrame,
    table3: pd.DataFrame,
    table4: pd.DataFrame,
    table5: pd.DataFrame,
    table6: pd.DataFrame,
    table7: pd.DataFrame,
    cutoffs: tuple[float, float],
) -> dict[str, Any]:
    decimal4 = "0.0000;[Red]-0.0000"
    decimal6 = "0.000000;[Red]-0.000000"
    percent2 = "0.00%;[Red]-0.00%"
    books = [
        frame_to_book(
            "Table1.xlsx",
            "Table 1",
            "Table 1. Descriptive Statistics",
            "Frozen baseline event sample; no deletion, winsorization, or imputation",
            table1,
            [22, 10, 10, 14, 14, 14, 14, 14, 16, 16],
            {1: "0", 2: "0", **{index: decimal4 for index in range(3, 10)}},
            [
                "Statistics use the available observations for each variable; standard deviation is the sample standard deviation (N-1).",
                "Return variables are decimal returns. Missing observations remain missing and are reported explicitly.",
            ],
        ),
        frame_to_book(
            "Table2.xlsx",
            "Table 2",
            "Table 2. Baseline OLS Regression Results",
            "OLS estimates with HC3 heteroskedasticity-robust standard errors",
            table2,
            [11, 12, 21, 10, 15, 15, 12, 12, 11],
            {3: "0", 4: decimal6, 5: decimal6, 6: "0.0000", 8: "0.0000"},
            [
                "Model 1 includes ln_RVOL. Model 2 adds Drawdown, Duration, and BreakoutStrength.",
                "Significance markers: *** p<0.01, ** p<0.05, * p<0.10. All observations are retained subject only to variable availability.",
            ],
        ),
        frame_to_book(
            "Table3.xlsx",
            "Table 3",
            "Table 3. Subsequent Returns across RVOL Terciles",
            "Groups are formed from the frozen baseline using ln_RVOL terciles",
            table3,
            [17, 13, 13, 13, 14, 14, 15, 20],
            {1: "0", 2: "0", 3: "0", 4: percent2, 5: percent2, 6: percent2, 7: percent2},
            [
                f"ln_RVOL tercile cutoffs are {cutoffs[0]:.6f} and {cutoffs[1]:.6f}; Positive R20 Ratio is the share of non-missing R20 observations above zero.",
                "Event Count reports all events in each group; Valid R20 N and Valid R60 N report the observations used for the respective outcomes.",
            ],
        ),
        frame_to_book(
            "Table4.xlsx",
            "Table 4",
            "Table 4. Breakout-Threshold Robustness",
            "Controlled R20 specification: ln_RVOL plus baseline controls",
            table4,
            [13, 13, 14, 19, 16, 12, 12, 11],
            {1: "0", 2: "0", 3: decimal6, 4: decimal6, 5: "0.0000", 7: "0.0000"},
            [
                "Each row uses OLS with HC3 robust standard errors and the same stock pool, distress rule, sideways rule, and volume construction.",
                "Event Sample and Regression N are separate because missing future returns can reduce the regression sample.",
            ],
        ),
        frame_to_book(
            "Table5.xlsx",
            "Table 5",
            "Table 5. ATR20 Breakout Robustness",
            "Baseline 0.5% rule compared with the ATR20 breakout rule",
            table5,
            [22, 13, 14, 19, 16, 12, 12, 11],
            {1: "0", 2: "0", 3: decimal6, 4: decimal6, 5: "0.0000", 7: "0.0000"},
            [
                "Rows report the controlled R20 OLS specification with HC3 robust standard errors.",
                "The ATR20 row comes from the saved robustness analysis; the frozen baseline dataset is unchanged.",
            ],
        ),
        frame_to_book(
            "Table6.xlsx",
            "Table 6",
            "Table 6. Volume-Window Robustness",
            "Alternative relative-volume benchmark windows in the controlled R20 model",
            table6,
            [20, 13, 14, 19, 16, 12, 12, 11],
            {1: "0", 2: "0", 3: decimal6, 4: decimal6, 5: "0.0000", 7: "0.0000"},
            [
                "Rows report OLS coefficients with HC3 robust standard errors; RVOL60 is the baseline volume window.",
                "No observations are deleted and no variables are winsorized.",
            ],
        ),
        frame_to_book(
            "Table7.xlsx",
            "Table 7",
            "Table 7. Breakout Failure Logistic Regression",
            "Outcome equals one when the minimum close over the next 20 trading days is below the breakout price",
            table7,
            [21, 10, 15, 15, 12, 14, 12, 18],
            {1: "0", 2: decimal6, 3: decimal6, 4: "0.0000", 5: "0.0000", 7: "0.0000"},
            [
                "This table preserves the separately specified logistic model and its saved model-based standard errors; it is not an OLS model.",
                "Significance markers: *** p<0.01, ** p<0.05, * p<0.10. Odds ratios equal exp(coefficient).",
            ],
        ),
    ]
    return {"books": books}


def build_workbooks(payload: dict[str, Any], table_dir: Path, preview_dir: Path) -> None:
    payload_path = RUNTIME_DIR / "workbook_payload.json"
    payload_path.parent.mkdir(parents=True, exist_ok=True)
    # Keep each authoring process small; the embedded spreadsheet runtime can retain
    # substantial memory after exporting and reopening a workbook.
    for book in payload["books"]:
        payload_path.write_text(
            json.dumps({"books": [book]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        run_node("build", [payload_path, table_dir, preview_dir])


def write_summary(
    output_path: Path,
    data: pd.DataFrame,
    table2: pd.DataFrame,
    table3: pd.DataFrame,
    table4: pd.DataFrame,
    table5: pd.DataFrame,
    table6: pd.DataFrame,
    table7: pd.DataFrame,
    correlation: float,
    failure_groups: pd.DataFrame,
) -> None:
    def coefficient(outcome: str, model: str) -> pd.Series:
        return table2.loc[
            (table2["Outcome"] == outcome)
            & (table2["Model"] == model)
            & (table2["Variable"] == "ln_RVOL")
        ].iloc[0]

    r20_m1 = coefficient("R20", "Model 1")
    r20_m2 = coefficient("R20", "Model 2")
    r60_m2 = coefficient("R60", "Model 2")
    failure_ln = table7.loc[table7["Variable"] == "ln_RVOL"].iloc[0]
    text = f"""# Empirical Results Summary

## Sample description

The frozen baseline contains **{len(data)} events** from **{data['stock_code'].nunique()} stocks**, spanning **{data['event_date'].min():%Y-%m-%d} to {data['event_date'].max():%Y-%m-%d}**. There are no duplicate stock-date event keys. The baseline file is read without deleting outliers, winsorizing variables, imputing missing values, or re-screening events. R1, R3, R20, and R60 each have one missing observation; R5 is complete.

## Main findings

The Pearson correlation between ln_RVOL and R20 is **{correlation:.3f}** (N={int(data[['ln_RVOL', 'R20']].dropna().shape[0])}). Mean R20 declines from **{table3.loc[0, 'Mean R20']:.2%}** in the Low group to **{table3.loc[2, 'Mean R20']:.2%}** in the High group. Mean R60 changes from **{table3.loc[0, 'Mean R60']:.2%}** to **{table3.loc[2, 'Mean R60']:.2%}** across the same groups. These patterns describe associations in the observed event sample.

## Regression results

In the simple R20 model, the ln_RVOL coefficient is **{r20_m1['Coefficient']:.4f}** (HC3 SE={r20_m1['HC3 Robust SE']:.4f}, p={r20_m1['p-value']:.4f}). With controls, the R20 coefficient is **{r20_m2['Coefficient']:.4f}** (HC3 SE={r20_m2['HC3 Robust SE']:.4f}, p={r20_m2['p-value']:.4f}); this is marginal at the 10% level. In the controlled R60 model, the coefficient is **{r60_m2['Coefficient']:.4f}** (HC3 SE={r60_m2['HC3 Robust SE']:.4f}, p={r60_m2['p-value']:.4f}). The estimates indicate that higher relative volume is associated with lower subsequent returns in these specifications.

## Robustness checks

Under the 0.5%, 1%, and 2% breakout thresholds, the controlled R20 coefficients are **{table4.loc[0, 'ln_RVOL Coefficient']:.4f}**, **{table4.loc[1, 'ln_RVOL Coefficient']:.4f}**, and **{table4.loc[2, 'ln_RVOL Coefficient']:.4f}**, respectively. The ATR20 specification reports **{table5.loc[1, 'ln_RVOL Coefficient']:.4f}** (p={table5.loc[1, 'p-value']:.4f}). Across RVOL20, RVOL60, and RVOL120, the coefficients are **{table6.loc[0, 'ln_RVOL Coefficient']:.4f}**, **{table6.loc[1, 'ln_RVOL Coefficient']:.4f}**, and **{table6.loc[2, 'ln_RVOL Coefficient']:.4f}**. The sign is stable, while statistical precision varies with the volume benchmark window.

## Breakout failure analysis

In the saved logistic specification, the ln_RVOL coefficient is **{failure_ln['Coefficient']:.4f}** (odds ratio={failure_ln['Odds Ratio']:.4f}, p={failure_ln['p-value']:.4f}). The observed Failure20 rates are **{failure_groups.loc[0, 'Observed Failure Rate']:.2%}**, **{failure_groups.loc[1, 'Observed Failure Rate']:.2%}**, and **{failure_groups.loc[2, 'Observed Failure Rate']:.2%}** for the Low, Medium, and High groups. Higher relative volume is therefore associated with a higher observed breakout-failure rate in this sample.

## Interpretation boundary

The evidence is associational. The event-study design and these regressions do not establish a directional mechanism. All reported estimates retain the original sample and model definitions.
"""
    forbidden = ["cause", "impact", "effect"]
    lower = text.lower()
    violations = [word for word in forbidden if word in lower]
    if violations:
        raise ValueError(f"Forbidden wording found in summary: {violations}")
    output_path.write_text(text, encoding="utf-8")


def validate_deliverables(
    output_root: Path,
    baseline_path: Path,
    initial_hash: str,
) -> dict[str, Any]:
    table_dir = output_root / "Tables"
    figure_dir = output_root / "Figures"
    tables = [table_dir / f"Table{index}.xlsx" for index in range(1, 8)]
    pngs = [figure_dir / f"Figure{index}.png" for index in range(1, 7)]
    pdfs = [figure_dir / f"Figure{index}.pdf" for index in range(1, 7)]
    expected = [*tables, *pngs, *pdfs, output_root / "empirical_results_summary.md"]
    missing = [str(path) for path in expected if not path.exists() or path.stat().st_size == 0]
    if missing:
        raise FileNotFoundError(f"Missing or empty deliverables: {missing}")
    for workbook in tables:
        if workbook.read_bytes()[:2] != b"PK":
            raise ValueError(f"Invalid XLSX signature: {workbook}")
    png_info = []
    for png in pngs:
        with Image.open(png) as image:
            if image.size != (2100, 1500):
                raise ValueError(f"Unexpected PNG size for {png}: {image.size}")
            dpi = image.info.get("dpi", (0, 0))
            if min(dpi) < 299:
                raise ValueError(f"PNG is not 300 dpi: {png}, dpi={dpi}")
            png_info.append({"file": png.name, "size": image.size, "dpi": dpi})
    for pdf in pdfs:
        if pdf.read_bytes()[:4] != b"%PDF":
            raise ValueError(f"Invalid PDF signature: {pdf}")
    final_hash = sha256_file(baseline_path)
    if final_hash != initial_hash:
        raise ValueError("Frozen baseline changed during analysis")
    return {
        "tables": len(tables),
        "png_figures": len(pngs),
        "pdf_figures": len(pdfs),
        "png_checks": png_info,
        "baseline_sha256": final_hash,
    }


def run_pipeline(
    baseline_path: Path,
    robustness_path: Path,
    failure_path: Path,
    output_root: Path,
    keep_runtime: bool = False,
) -> dict[str, Any]:
    for path in [baseline_path, robustness_path, failure_path]:
        if not path.exists():
            raise FileNotFoundError(path)
    initial_hash = sha256_file(baseline_path)
    if baseline_path.resolve() == DEFAULT_BASELINE.resolve() and initial_hash != BASELINE_SHA256:
        raise ValueError(
            f"Frozen baseline hash mismatch: expected {BASELINE_SHA256}, found {initial_hash}"
        )
    data = prepare_baseline(baseline_path)
    core, logit, failure_events = extract_saved_results(robustness_path, failure_path)
    core, logit, failure_events = coerce_saved_results(core, logit, failure_events)

    table1 = descriptive_table(data)
    table2, fitted = baseline_regression_table(data)
    table3, cutoffs = rvol_group_table(data)
    table4, table5, table6, table7 = robustness_tables(core, logit)
    failure_groups = failure_group_table(failure_events)
    correlation = float(data[["ln_RVOL", "R20"]].dropna().corr().iloc[0, 1])

    # Cross-check the recomputed controlled baseline against the saved robustness result.
    recomputed = table2.loc[
        (table2["Outcome"] == "R20")
        & (table2["Model"] == "Model 2")
        & (table2["Variable"] == "ln_RVOL")
    ].iloc[0]
    saved = table4.iloc[0]
    for left_value, right_value, label in [
        (recomputed["Coefficient"], saved["ln_RVOL Coefficient"], "coefficient"),
        (recomputed["HC3 Robust SE"], saved["HC3 Robust SE"], "HC3 SE"),
        (recomputed["p-value"], saved["p-value"], "p-value"),
        (recomputed["R2"], saved["R2"], "R2"),
    ]:
        if not math.isclose(float(left_value), float(right_value), rel_tol=1e-9, abs_tol=1e-11):
            raise ValueError(f"Saved and recomputed baseline {label} do not match")
    expected_failure = [51 / 65, 56 / 64, 59 / 64]
    if not np.allclose(
        failure_groups["Observed Failure Rate"].to_numpy(), expected_failure, atol=1e-12
    ):
        raise ValueError("Failure-group rates do not match the saved event-level results")

    table_dir = output_root / "Tables"
    figure_dir = output_root / "Figures"
    preview_dir = RUNTIME_DIR / "table_previews"
    table_dir.mkdir(parents=True, exist_ok=True)
    figure_dir.mkdir(parents=True, exist_ok=True)
    preview_dir.mkdir(parents=True, exist_ok=True)
    font_dir = Path(os.environ.get("CODEX_REPORTLAB_FONT_DIR", str(DEFAULT_FONT_DIR)))
    for filename in ["Vera.ttf", "VeraBd.ttf", "VeraIt.ttf"]:
        if not (font_dir / filename).exists():
            raise FileNotFoundError(font_dir / filename)

    payload = workbook_payload(
        table1, table2, table3, table4, table5, table6, table7, cutoffs
    )
    build_workbooks(payload, table_dir, preview_dir)

    bar_figure(
        GROUP_ORDER,
        table3["Mean R20"].tolist(),
        table3["Valid R20 N"].astype(int).tolist(),
        "Average 20-Day Returns across RVOL Groups",
        "Average 20-trading-day return (%)",
        figure_dir / "Figure1",
        font_dir,
    )
    bar_figure(
        GROUP_ORDER,
        table3["Mean R60"].tolist(),
        table3["Valid R60 N"].astype(int).tolist(),
        "Average 60-Day Returns across RVOL Groups",
        "Average 60-trading-day return (%)",
        figure_dir / "Figure2",
        font_dir,
    )
    scatter_figure(data, fitted, figure_dir / "Figure3", font_dir)
    coefficient_figure(
        table4["Threshold"].tolist(),
        table4["ln_RVOL Coefficient"].tolist(),
        table4["HC3 Robust SE"].tolist(),
        "RVOL Coefficients across Breakout Thresholds",
        "Breakout threshold",
        figure_dir / "Figure4",
        font_dir,
    )
    coefficient_figure(
        ["20", "60", "120"],
        table6["ln_RVOL Coefficient"].tolist(),
        table6["HC3 Robust SE"].tolist(),
        "RVOL Coefficients across Volume Windows",
        "Volume benchmark window (trading days)",
        figure_dir / "Figure5",
        font_dir,
    )
    bar_figure(
        GROUP_ORDER,
        failure_groups["Observed Failure Rate"].tolist(),
        failure_groups["Event Count"].astype(int).tolist(),
        "Observed Breakout Failure Rate by RVOL Group",
        "Breakout failure rate (%)",
        figure_dir / "Figure6",
        font_dir,
        fixed_range=(0.0, 1.0),
    )

    write_summary(
        output_root / "empirical_results_summary.md",
        data,
        table2,
        table3,
        table4,
        table5,
        table6,
        table7,
        correlation,
        failure_groups,
    )
    validation = validate_deliverables(output_root, baseline_path, initial_hash)
    result = {
        "output_root": str(output_root.resolve()),
        "events": int(len(data)),
        "stocks": int(data["stock_code"].nunique()),
        "date_range": [
            data["event_date"].min().strftime("%Y-%m-%d"),
            data["event_date"].max().strftime("%Y-%m-%d"),
        ],
        "correlation_ln_RVOL_R20": correlation,
        "validation": validation,
    }
    if not keep_runtime:
        cleanup_runtime()
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--robustness", type=Path, default=DEFAULT_ROBUSTNESS)
    parser.add_argument("--failure", type=Path, default=DEFAULT_FAILURE)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--keep-runtime",
        action="store_true",
        help="Keep D-drive temporary previews for visual quality inspection.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_pipeline(
        args.baseline.resolve(),
        args.robustness.resolve(),
        args.failure.resolve(),
        args.output_root.resolve(),
        keep_runtime=args.keep_runtime,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("All thesis-ready tables and figures generated successfully.")


if __name__ == "__main__":
    main()
