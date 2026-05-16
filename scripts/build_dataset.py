"""
Kazakhstan Fuel & Energy Balance — Real Data Pipeline
======================================================
Downloads official stat.gov.kz Excel files, ACTUALLY parses them
(the previous data_parser.py never opened the Excels — it just had
hardcoded numbers in the script), and produces a clean unified dataset.

Outputs:
  data/clean/master_long.csv          — long format (year, unit, section, line_item, fuel_group, fuel, value)
  data/clean/master_TJ_wide.csv       — wide format in TJ (year × line_item × fuel)
  data/clean/master_toe_wide.csv      — wide format in 1000 toe
  data/clean/electricity_balance.csv  — extracted electricity sub-balance
  data/clean/by_fuel_group_TJ.csv     — annual totals by major fuel group
  data/clean/kz_energy_balance.db     — SQLite database
  data/clean/quality_report.md        — coverage and validation report

Author: Gabit Sekenov / Aisulu Kassekeyeva (Astana IT University)
Project: Statistical Analysis for the Fuel and Energy Balance of Kazakhstan
"""
from __future__ import annotations

import logging
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests

# ── Paths ──────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data" / "raw"
CLEAN_DIR = BASE_DIR / "data" / "clean"
LOG_FILE = BASE_DIR / "data" / "build_dataset.log"

RAW_DIR.mkdir(parents=True, exist_ok=True)
CLEAN_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger(__name__)

# ── stat.gov.kz: Direct XLSX URLs (verified March 2026) ────────────
# These are the official Bureau of National Statistics download endpoints.
# If a URL goes dead, the script falls back to the local file in data/raw/.
BALANCE_XLSX_URLS = {
    2021: "https://stat.gov.kz/api/iblock/element/37685/file/en/",
    2022: "https://stat.gov.kz/api/iblock/element/75937/file/en/",
    2023: "https://stat.gov.kz/api/iblock/element/205511/file/en/",
    2024: "https://stat.gov.kz/api/iblock/element/411633/file/en/",
}

# Sheet → unit family
SHEETS = {
    "1": "physical",   # mixed units (thousand tons, TJ, m³, GWh) — skipped from numeric melt
    "2": "TJ",         # Terajoules (energy-uniform)
    "3": "toe",        # Thousand tons of oil equivalent
}

# Sentinel values used by stat.gov.kz to encode "missing" or "no data"
MISSING_TOKENS = {"-", "—", "...", "…", "x", "X", "n/a", "N/A"}

# Normalize inconsistent fuel-group names across years (2021 wording differs from 2022+).
FUEL_GROUP_ALIASES = {
    "coal and its processed products": "Coal and products of its processing",
    "coal and products of its processing": "Coal and products of its processing",
    "oil and petrolium products": "Oil and oil products",
    "oil and oil products": "Oil and oil products",
    "natural gas million cubik meters": "Natural gas",
    "natural gas2 million cubik meters": "Natural gas",
    "natural gas 2 million cubik meters": "Natural gas",
    "natural gas2": "Natural gas",
    "natural gas": "Natural gas",
    "gas2": "Natural gas",
    "gas 2": "Natural gas",
    "gas": "Natural gas",
    "renewable energy sources": "Renewable energy sources",
    "electricity": "Electricity",
    "electricity gwh": "Electricity",
    "heat": "Heat",
    "heat tj": "Heat",
    "thermal energy": "Heat",
    "total": "Total",
}

# Normalize inconsistent line-item labels across years (2021 differs from 2022+).
LINE_ITEM_ALIASES = {
    "production extraction of primary energy": "Primary energy production",
    "production extraction of primary energy +": "Primary energy production",
    "total primary energy production +": "Primary energy production",
    "total primary energy production": "Primary energy production",
    "imports": "Imports",
    "imports +": "Imports",
    "import": "Imports",
    "exports": "Exports",
    "exports -": "Exports",
    "export": "Exports",
    "international marine and aviation bunkers -": "International marine/aviation bunkers",
    "international marine and aviation bunkering": "International marine/aviation bunkers",
    "bunkering": "International marine/aviation bunkers",
    "stocks at the beginning of the year": "Stocks at year start",
    "stocks at the end of the year": "Stocks at year end",
    "stock changes": "Stock changes",
    "stock changes +": "Stock changes",
    "stock change": "Stock changes",
    "total primary energy consumption =": "Total primary energy consumption",
    "total primary energy consumption": "Total primary energy consumption",
    "total primary energy consumption and its equivalents =": "Total primary energy consumption",
    "total primary energy consumption and its equivalents": "Total primary energy consumption",
    "statistical discrepancies": "Statistical discrepancies",
    "statistsical diferences": "Statistical discrepancies",
    "inter-product transfers overflows": "Inter-product transfers",
    "available for final consumption": "Available for final consumption",
    "final energy consumption": "Final energy consumption",
    "final consumption for non-energy purposes": "Final non-energy consumption",
    "final non-energy consumption": "Final non-energy consumption",
    "losses during the technological process transportation and distribution": "Losses (transmission/distribution)",
    "losses during the technological process transportation and distribution -": "Losses (transmission/distribution)",
    "losses": "Losses (transmission/distribution)",
}


def _normalize_line_item(raw: Optional[str]) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    key = re.sub(r"[^a-z0-9 \-/]+", " ", raw.lower()).strip()
    key = re.sub(r"\s+", " ", key)
    return LINE_ITEM_ALIASES.get(key, raw)


def _normalize_fuel_group(raw: Optional[str]) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    key = re.sub(r"[^a-z0-9 ]+", " ", raw.lower()).strip()
    key = re.sub(r"\s+", " ", key)
    return FUEL_GROUP_ALIASES.get(key, raw.strip().rstrip(",").strip())


def _clean_fuel_label(raw: Optional[str], fallback_group: Optional[str]) -> Optional[str]:
    """Clean a fuel cell value. If empty, fall back to the group name."""
    if isinstance(raw, str) and raw.strip():
        s = re.sub(r"\s+", " ", raw.replace("\n", " ")).strip().strip(",").strip()
        # drop trailing unit suffix like ", thousand tons" / ", GWh" / ", TJ"
        s = re.sub(
            r",?\s*(thousand tons|thous\.?\s*tons|gwh|tj|million cubik meters|"
            r"million cubic meters|m3|toe|kt|mt)\s*$",
            "",
            s,
            flags=re.IGNORECASE,
        ).strip()
        return s
    if isinstance(fallback_group, str) and fallback_group.strip():
        return fallback_group.strip()
    return None


# ───────────────────────────────────────────────────────────────────
# 1) DOWNLOAD
# ───────────────────────────────────────────────────────────────────
def download_balance_xlsx(force: bool = False) -> dict[int, Path]:
    """Download official fuel & energy balance XLSX from stat.gov.kz with snapshot fallback."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (KZ Energy Balance Research, Astana IT University)"
    })
    paths: dict[int, Path] = {}
    for year, url in BALANCE_XLSX_URLS.items():
        target = RAW_DIR / f"stat_gov_kz_energy_balance_{year}.xlsx"
        if target.exists() and not force:
            log.info(f"  [{year}] using local snapshot ({target.stat().st_size:,} bytes)")
            paths[year] = target
            continue
        try:
            log.info(f"  [{year}] downloading {url}")
            resp = session.get(url, timeout=60)
            resp.raise_for_status()
            target.write_bytes(resp.content)
            log.info(f"  [{year}] saved {target} ({len(resp.content):,} bytes)")
            paths[year] = target
        except Exception as e:
            log.warning(f"  [{year}] download failed: {e}; checking local snapshot")
            if target.exists():
                log.info(f"  [{year}] using existing snapshot")
                paths[year] = target
            else:
                log.error(f"  [{year}] no local snapshot — skipping")
    return paths


# ───────────────────────────────────────────────────────────────────
# 2) PARSE ONE SHEET
# ───────────────────────────────────────────────────────────────────
def _to_number(v) -> Optional[float]:
    """Coerce stat.gov.kz cell value to float; return NaN on missing tokens."""
    if v is None:
        return np.nan
    if isinstance(v, (int, float)):
        return float(v) if pd.notna(v) else np.nan
    s = str(v).strip()
    if s in MISSING_TOKENS or s == "":
        return np.nan
    # strip thousands separators (rare in this data) and commas-as-decimals
    s = s.replace(" ", "").replace(" ", "")
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return np.nan


def _section_for_label(label: str) -> Optional[str]:
    """Classify a row label into one of the FEB sections."""
    if not isinstance(label, str):
        return None
    L = label.strip().lower()
    if L.startswith("1.") or "total energy" in L and "supply" in L:
        return "1_total_supply"
    if L.startswith("2.") or "conversion" in L or "transformation" in L:
        return "2_transformation"
    if L.startswith("3.") or "final consumption" in L or "final energy consumption" == L:
        return "3_final_consumption"
    return None


def parse_one_sheet(xlsx_path: Path, sheet_name: str, year: int, unit: str) -> pd.DataFrame:
    """Parse one stat.gov.kz energy balance sheet → long format DataFrame.

    Sheet structure (verified for 2021-2024):
      Row 0: title
      Row 2: unit caption ("in physical terms" / "Terajoule, TJ" / "thousand tons of oil equivalent")
      Row 3: fuel-GROUP (Coal, Oil, Gas, Renewables, Electricity, Heat, Total) — sparse, only at group start
      Row 4: specific FUEL within group (Coal concentrate, Crude oil, Natural gas, ...)
      Row 5: calorific value (skip)
      Row 6+: data — column 0 = line_item label; columns 1..N = values per fuel
    """
    raw = pd.read_excel(xlsx_path, sheet_name=sheet_name, header=None)

    # Build column metadata: group + fuel (forward-fill the group across columns)
    group_row = raw.iloc[3, :].tolist()
    fuel_row = raw.iloc[4, :].tolist()

    # Forward-fill groups: a group label spans all columns until the next non-NaN group.
    # Keep the RAW group at the column where it appears (for fuel-fallback), and
    # ALSO carry it forward for downstream label tagging.
    raw_groups: list[Optional[str]] = []
    groups: list[Optional[str]] = []
    current = None
    for g in group_row:
        if isinstance(g, str) and g.strip():
            current = _normalize_fuel_group(g)
            raw_groups.append(current)
        else:
            raw_groups.append(None)
        groups.append(current)

    # Identify data start row: first row after row 5 with a string in col 0
    DATA_START = 6
    # in 2021 there's a "Calorific value" row at idx 5 in sheet 1; sheets 2/3 start at 5
    # we'll just classify all rows ≥ 5 with a string label
    rows = []
    current_section: Optional[str] = None
    for ridx in range(DATA_START - 1, raw.shape[0]):
        label = raw.iloc[ridx, 0]
        if not isinstance(label, str):
            continue
        label_clean = re.sub(r"\s+", " ", label).strip()
        if not label_clean or label_clean.startswith(("1)", "2)", "3)", "4)", "5)", "6)")):
            # footnote rows — skip
            continue
        # detect section header
        sec = _section_for_label(label_clean)
        if sec:
            current_section = sec
            # section headers themselves rarely carry data; still try
        # walk every column with a meaningful header (fuel-cell OR a top-level group)
        for c in range(1, raw.shape[1]):
            fuel = fuel_row[c] if c < len(fuel_row) else None
            group_here = groups[c] if c < len(groups) else None
            raw_group_here = raw_groups[c] if c < len(raw_groups) else None
            # If both fuel and raw_group are empty here, skip
            if not (isinstance(fuel, str) and fuel.strip()) and not raw_group_here:
                continue
            # If fuel cell is empty but this column is the START of a group, treat group as fuel
            fuel_clean = _clean_fuel_label(fuel, raw_group_here)
            if not fuel_clean:
                continue
            value = _to_number(raw.iloc[ridx, c])
            if pd.isna(value):
                continue  # don't pollute long table with empties
            rows.append({
                "year": year,
                "unit": unit,
                "section": current_section,
                "line_item": _normalize_line_item(label_clean),
                "fuel_group": group_here,
                "fuel": fuel_clean,
                "value": value,
            })
    df = pd.DataFrame(rows)
    log.info(f"  parsed {xlsx_path.name} sheet '{sheet_name}' ({unit}, year={year}): "
             f"{len(df):,} non-null observations")
    return df


# ───────────────────────────────────────────────────────────────────
# 3) BUILD MASTER DATASET
# ───────────────────────────────────────────────────────────────────
def build_master(xlsx_paths: dict[int, Path]) -> pd.DataFrame:
    """Concatenate all year × sheet parses into one long DataFrame."""
    pieces = []
    for year, p in sorted(xlsx_paths.items()):
        for sheet, unit in SHEETS.items():
            if unit == "physical":
                continue  # skip — units are not consistent across columns
            try:
                pieces.append(parse_one_sheet(p, sheet, year, unit))
            except Exception as e:
                log.error(f"failed to parse {p} sheet {sheet}: {e}")
    if not pieces:
        return pd.DataFrame()
    master = pd.concat(pieces, ignore_index=True)
    # de-duplicate identical rows that may arise from header-row scanning
    master = master.drop_duplicates(subset=["year", "unit", "line_item", "fuel"]).reset_index(drop=True)
    return master


# ───────────────────────────────────────────────────────────────────
# 4) DERIVED VIEWS
# ───────────────────────────────────────────────────────────────────
def derive_electricity_balance(master: pd.DataFrame) -> pd.DataFrame:
    """Extract electricity-specific balance lines for analysis & ML.

    Filters where fuel_group == 'Electricity' OR fuel mentions 'electric'.
    Pivots to (year × unit) × line_item.
    """
    mask = (
        master["fuel_group"].astype(str).str.contains("Electricity", case=False, na=False)
        | master["fuel"].astype(str).str.contains("electric", case=False, na=False)
    )
    elec = master[mask].copy()
    if elec.empty:
        return elec
    pivot = elec.pivot_table(
        index=["year", "unit"],
        columns="line_item",
        values="value",
        aggfunc="sum",
    ).reset_index()
    pivot.columns.name = None
    return pivot


def derive_by_fuel_group(master: pd.DataFrame, unit: str = "TJ") -> pd.DataFrame:
    """Annual totals by fuel group for a given unit."""
    sub = master[master["unit"] == unit].copy()
    if sub.empty:
        return sub
    # use the most aggregated rows: Total Primary Energy Consumption + Imports + Exports + Production
    key_lines = [
        "Production (extraction) of primary energy (+)",
        "Production (extraction) of primary energy",
        "Imports (+)",
        "import",
        "Exports (-)",
        "export",
        "Total Primary Energy Consumption (=)",
        "Total Primary Energy Consumption",
        "Final energy consumption",
        "Available for final consumption",
    ]
    # match flexibly
    pat = "|".join(re.escape(k) for k in key_lines)
    sub = sub[sub["line_item"].str.contains(pat, case=False, na=False, regex=True)]
    if sub.empty:
        return sub
    out = (
        sub.groupby(["year", "fuel_group", "line_item"], dropna=False)["value"]
        .sum()
        .reset_index()
    )
    return out


def derive_master_wide(master: pd.DataFrame, unit: str) -> pd.DataFrame:
    sub = master[master["unit"] == unit]
    if sub.empty:
        return sub
    return sub.pivot_table(
        index=["line_item"], columns=["year", "fuel"], values="value", aggfunc="sum"
    ).reset_index()


# ───────────────────────────────────────────────────────────────────
# 5) SQLITE
# ───────────────────────────────────────────────────────────────────
def _safe_col(name: str) -> str:
    """Make column names SQLite-safe: lowercase alphanum + underscore.

    SQLite treats column names as case-insensitive in CREATE TABLE, so we
    lowercase to avoid `Agriculture_Forestry` vs `Agriculture_forestry` collisions.
    """
    s = re.sub(r"[^0-9A-Za-z_]+", "_", str(name)).strip("_").lower()
    return s[:60] if s else "col"


def _flatten_dedupe(df: pd.DataFrame) -> pd.DataFrame:
    """Flatten MultiIndex columns and de-duplicate names for SQLite (case-insensitive)."""
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = ["__".join(str(x) for x in tup if x is not None and str(x) != "")
                      for tup in df.columns]
    new_cols: list[str] = []
    seen: dict[str, int] = {}
    for c in df.columns:
        base = _safe_col(c)
        if base in seen:
            seen[base] += 1
            new_cols.append(f"{base}_{seen[base]}")
        else:
            seen[base] = 0
            new_cols.append(base)
    df.columns = new_cols
    return df


def write_sqlite(master: pd.DataFrame, by_group: pd.DataFrame, elec: pd.DataFrame, db_path: Path):
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    _flatten_dedupe(master).to_sql("balance_long", conn, if_exists="replace", index=False)
    if not by_group.empty:
        _flatten_dedupe(by_group).to_sql("by_fuel_group_TJ", conn, if_exists="replace", index=False)
    if not elec.empty:
        _flatten_dedupe(elec).to_sql("electricity_balance", conn, if_exists="replace", index=False)
    # convenience: annual totals table
    pivot = master.pivot_table(
        index=["year", "fuel_group", "fuel", "unit"],
        columns="line_item",
        values="value",
        aggfunc="sum",
    ).reset_index()
    _flatten_dedupe(pivot).to_sql("balance_wide_by_fuel", conn, if_exists="replace", index=False)
    # metadata
    pd.DataFrame([
        {"key": "built_at", "value": datetime.now().isoformat(timespec="seconds")},
        {"key": "source", "value": "stat.gov.kz Bureau of National Statistics — Fuel and Energy Balance"},
        {"key": "years", "value": ",".join(str(y) for y in sorted(master["year"].unique()))},
        {"key": "units", "value": ",".join(sorted(master["unit"].unique()))},
        {"key": "n_rows", "value": str(len(master))},
    ]).to_sql("meta", conn, if_exists="replace", index=False)
    conn.commit()
    conn.close()


# ───────────────────────────────────────────────────────────────────
# 6) QUALITY REPORT
# ───────────────────────────────────────────────────────────────────
def write_quality_report(master: pd.DataFrame, out_path: Path):
    if master.empty:
        out_path.write_text("# Quality report\n\nMaster dataset is EMPTY.\n", encoding="utf-8")
        return
    lines = []
    lines.append("# Kazakhstan Fuel & Energy Balance — Data Quality Report")
    lines.append("")
    lines.append(f"_Built: {datetime.now().isoformat(timespec='seconds')}_")
    lines.append("")
    lines.append("## Coverage")
    lines.append("")
    lines.append(f"- Total non-null observations: **{len(master):,}**")
    lines.append(f"- Years covered: **{sorted(master['year'].unique())}**")
    lines.append(f"- Units present: **{sorted(master['unit'].unique())}**")
    lines.append(f"- Distinct line items: **{master['line_item'].nunique()}**")
    lines.append(f"- Distinct fuels: **{master['fuel'].nunique()}**")
    lines.append(f"- Distinct fuel groups: **{master['fuel_group'].dropna().nunique()}**")
    lines.append("")
    lines.append("## Observations per year × unit")
    lines.append("")
    counts = master.groupby(["year", "unit"]).size().unstack(fill_value=0)
    lines.append(counts.to_markdown())
    lines.append("")
    lines.append("## Top fuel groups by row count")
    lines.append("")
    top_groups = master["fuel_group"].value_counts().head(10)
    lines.append(top_groups.to_markdown())
    lines.append("")
    lines.append("## Section breakdown")
    lines.append("")
    sec_counts = master.groupby(["year", "section"]).size().unstack(fill_value=0)
    lines.append(sec_counts.to_markdown())
    lines.append("")
    lines.append("## Sample rows (first 15)")
    lines.append("")
    lines.append(master.head(15).to_markdown(index=False))
    lines.append("")
    lines.append("## Value distribution by unit")
    lines.append("")
    for u in sorted(master["unit"].unique()):
        s = master.loc[master["unit"] == u, "value"]
        lines.append(f"- **{u}** — n={len(s):,}, min={s.min():.3g}, "
                     f"median={s.median():.3g}, max={s.max():.3g}")
    out_path.write_text("\n".join(lines), encoding="utf-8")


# ───────────────────────────────────────────────────────────────────
# 7) MAIN
# ───────────────────────────────────────────────────────────────────
def main(force_download: bool = False):
    log.info("=" * 70)
    log.info("Kazakhstan Fuel & Energy Balance — building clean dataset")
    log.info("=" * 70)

    # Download (or reuse) Excel files
    log.info("Step 1: download / locate Excel files")
    paths = download_balance_xlsx(force=force_download)
    if not paths:
        log.error("No Excel files available — aborting.")
        return 1

    # Parse all sheets into master long DataFrame
    log.info("Step 2: parse XLSX → long format")
    master = build_master(paths)
    if master.empty:
        log.error("Master DataFrame is empty — parsing failed for all files.")
        return 1
    log.info(f"  master long: {len(master):,} rows, {master['year'].nunique()} years, "
             f"{master['fuel'].nunique()} distinct fuels")

    # Save outputs
    log.info("Step 3: derive views and save")
    master.to_csv(CLEAN_DIR / "master_long.csv", index=False)
    log.info(f"  wrote {CLEAN_DIR/'master_long.csv'}")

    wide_TJ = derive_master_wide(master, "TJ")
    if not wide_TJ.empty:
        wide_TJ.to_csv(CLEAN_DIR / "master_TJ_wide.csv", index=False)
        log.info(f"  wrote {CLEAN_DIR/'master_TJ_wide.csv'} ({wide_TJ.shape})")
    wide_toe = derive_master_wide(master, "toe")
    if not wide_toe.empty:
        wide_toe.to_csv(CLEAN_DIR / "master_toe_wide.csv", index=False)
        log.info(f"  wrote {CLEAN_DIR/'master_toe_wide.csv'} ({wide_toe.shape})")

    elec = derive_electricity_balance(master)
    if not elec.empty:
        elec.to_csv(CLEAN_DIR / "electricity_balance.csv", index=False)
        log.info(f"  wrote {CLEAN_DIR/'electricity_balance.csv'} ({elec.shape})")

    by_group = derive_by_fuel_group(master, "TJ")
    if not by_group.empty:
        by_group.to_csv(CLEAN_DIR / "by_fuel_group_TJ.csv", index=False)
        log.info(f"  wrote {CLEAN_DIR/'by_fuel_group_TJ.csv'} ({by_group.shape})")

    write_sqlite(master, by_group, elec, CLEAN_DIR / "kz_energy_balance.db")
    log.info(f"  wrote {CLEAN_DIR/'kz_energy_balance.db'}")

    write_quality_report(master, CLEAN_DIR / "quality_report.md")
    log.info(f"  wrote {CLEAN_DIR/'quality_report.md'}")

    log.info("=" * 70)
    log.info("Done.")
    log.info(f"All outputs → {CLEAN_DIR}")
    log.info("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main(force_download="--force" in sys.argv))
