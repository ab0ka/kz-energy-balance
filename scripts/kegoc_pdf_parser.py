"""
KEGOC Annual Report Parser
==========================
Extracts the electricity-balance data from KEGOC annual reports
(downloaded from https://www.kegoc.kz/en/for-investors-and-shareholders/
raskrytie-informatsii/annual-reports/).

KEGOC reports embed balance numbers as info-graphic charts rather than
formal tables. An earlier text-window heuristic pulled the WRONG numbers
(it confused "technical dispatching" / "transmission" / TPP-share % with
production and consumption). The current pipeline therefore prefers a
hand-verified CSV as the source of truth, with the PDF parser used only
for cross-validation and provenance.

Pipeline:
1. Load `data/clean/kegoc_balance_verified.csv` (gold source — every row
   carries `source_verified`, `source_year_report`, `source_page` and a free
   `note`).
2. Run the coordinate-aware PDF extractor on each report and emit a separate
   `kegoc_balance_pdf_extracted.csv` for audit. If the two disagree by more
   than 0.5 BkWh, log a warning so the verified CSV can be updated.
3. Write the verified rows to `kegoc_balance` and a wide pivot
   `kegoc_balance_wide` in SQLite.

Output:
    data/clean/kegoc_balance.csv               (= verified copy)
    data/clean/kegoc_balance_pdf_extracted.csv (audit, machine-read)
    sqlite tables: kegoc_balance, kegoc_balance_wide

Usage
-----
    python3 scripts/kegoc_pdf_parser.py --verified-only            # quick, no PDF parsing
    python3 scripts/kegoc_pdf_parser.py --all data/raw/kegoc/      # audit run
    python3 scripts/kegoc_pdf_parser.py data/raw/kegoc/kegoc_annual_report_2024.pdf
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
RAW_DIR = BASE_DIR / "data" / "raw" / "kegoc"
CLEAN_DIR = BASE_DIR / "data" / "clean"
DB_PATH = CLEAN_DIR / "kz_energy_balance.db"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


VERIFIED_CSV = CLEAN_DIR / "kegoc_balance_verified.csv"

# Chart pages where the production / consumption series live, one per report.
# Hand-curated from inspection of the actual PDFs in data/raw/kegoc/.
KNOWN_CHART_PAGES = {
    2020: 59,   # "Electricity production-consumption, billion kWh" + Production/Consumption labels
    2021: 25,   # "Electricity generation and consumption" — covers 2016/2018/2019/2020/2021
    2023: 11,   # "Dynamics of electricity generation, billion kWh"  — 2019..2023 generation only
    2024: 20,   # "Dynamics of electricity generation" — 2020..2024 generation only
    # AR2022 has no year-aligned production/consumption chart; gen for 2022 comes
    # from AR2023, consumption for 2022 from cross-validation against text.
}

YEAR_RE = re.compile(r"^(20[12][0-9])$")
NUMBER_TOKEN_RE = re.compile(r"^-?\d{1,3}(?:[\s,]\d{3})*(?:[.,]\d+)?$")


def _to_number(s: str) -> Optional[float]:
    s = s.strip()
    # Drop thousands separators (space/comma) but keep last "." or "," as decimal
    if re.fullmatch(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?", s):
        s = s.replace(",", "")
    elif re.fullmatch(r"-?\d{1,3}(?:\s\d{3})+(?:[.,]\d+)?", s):
        s = s.replace(" ", "").replace(",", ".")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _extract_chart_pairs(doc, page_idx: int) -> list[dict]:
    """Coordinate-aware extraction.

    Reads `page.get_text("words")` -> list of (x0, y0, x1, y1, text, ...).
    Groups tokens into rows by y-coordinate; in each row, separates years
    (2016..2029) from numeric tokens (50..200). Year-aligned pairs are
    candidate (year, value) records.

    This replaces the previous text-window heuristic, which silently aligned
    'consumption' values to the wrong years because KEGOC infographics
    interleave labels and numbers along the diagonals of a chart.
    """
    page = doc[page_idx]
    words = page.get_text("words") or []
    if not words:
        return []

    # Tag every word as YEAR, NUM, or OTHER
    tagged = []
    for x0, y0, x1, y1, txt, *_ in words:
        s = str(txt).strip()
        m = YEAR_RE.match(s)
        if m:
            tagged.append((y0, x0, "YEAR", int(s)))
            continue
        if NUMBER_TOKEN_RE.match(s):
            v = _to_number(s)
            if v is not None and 50 <= v <= 200:
                tagged.append((y0, x0, "NUM", v))
                continue
        # also catch numbers expressed in mln kWh (>50,000 with thousands sep)
        if NUMBER_TOKEN_RE.match(s):
            v = _to_number(s)
            if v is not None and 50_000 <= v <= 200_000:
                tagged.append((y0, x0, "NUM_MLN", v))

    if not tagged:
        return []

    # Cluster by row (y within 6 pt of each other)
    tagged.sort(key=lambda t: (t[0], t[1]))
    rows: list[list[tuple]] = []
    for t in tagged:
        if rows and abs(t[0] - rows[-1][-1][0]) < 6:
            rows[-1].append(t)
        else:
            rows.append([t])

    out: list[dict] = []
    # A "year row" has 4-6 YEAR tokens and ~no NUMs; a "value row" has 4-6 NUM
    # tokens at similar y as the year row. KEGOC charts put years on the x-axis
    # and values just above them, so we pair every year row with the nearest
    # value row above (smaller y).
    for r in rows:
        years = [(x, v) for _, x, kind, v in r if kind == "YEAR"]
        nums  = [(x, v) for _, x, kind, v in r if kind == "NUM"]
        if 3 <= len(years) <= 6 and len(nums) == 0:
            # find candidate value row above
            yr_y = next(y for y, _, k, _ in r if k == "YEAR")
            best = None; best_dist = 1e9
            for r2 in rows:
                if r2 is r: continue
                r2_nums = [(x, v) for _, x, k, v in r2 if k == "NUM"]
                if len(r2_nums) >= len(years) - 1:
                    dist = abs(r2[0][0] - yr_y)
                    if 5 < dist < 120 and dist < best_dist:
                        best, best_dist = r2_nums, dist
            if best:
                # align by x-coordinate
                years_sorted = sorted(years, key=lambda t: t[0])
                nums_sorted  = sorted(best,  key=lambda t: t[0])
                for (xy, yv), (xn, nv) in zip(years_sorted, nums_sorted):
                    if abs(xy - xn) < 60:
                        out.append({"year": yv, "value_bkwh": nv})
    return out


def parse_one(pdf_path: Path, year: Optional[int] = None) -> pd.DataFrame:
    """Audit extractor — returns whatever the coordinate parser finds on the
    known chart page for this report. The metric (production / consumption)
    cannot be reliably inferred from a single chart row, so values are emitted
    untyped and the verified CSV is the source of truth for the metric label.
    """
    if year is None:
        m = re.search(r"(20\d\d)", pdf_path.name)
        year = int(m.group(1)) if m else 0

    try:
        import fitz
    except ImportError:
        log.error("PyMuPDF (fitz) is required: pip install PyMuPDF")
        return pd.DataFrame()

    log.info(f"Parsing {pdf_path.name} (audit only)")
    doc = fitz.open(pdf_path)
    page_idx_1based = KNOWN_CHART_PAGES.get(year)
    rows: list[dict] = []
    if page_idx_1based is None:
        log.info(f"  no known chart page for AR{year}; skipping coord extract")
    else:
        rows = _extract_chart_pairs(doc, page_idx_1based - 1)
        for r in rows:
            r["source_year_report"] = year
            r["source_page"] = page_idx_1based
    doc.close()

    if not rows:
        log.warning(f"  no chart values extracted from {pdf_path.name}")
        return pd.DataFrame()

    df = pd.DataFrame(rows).drop_duplicates(subset=["year", "value_bkwh"])
    log.info(f"  extracted {len(df)} (year,value) pairs for audit")
    return df


def parse_all(folder: Path) -> pd.DataFrame:
    """Audit-only: extract chart values from every PDF and emit raw pairs."""
    folder = Path(folder)
    pieces = []
    for pdf in sorted(folder.glob("*.pdf")):
        df = parse_one(pdf)
        if not df.empty:
            pieces.append(df)
    if not pieces:
        return pd.DataFrame()
    return pd.concat(pieces, ignore_index=True)


def load_verified() -> pd.DataFrame:
    """The single source of truth. Hand-curated by reading the PDFs."""
    if not VERIFIED_CSV.exists():
        raise FileNotFoundError(
            f"Verified CSV missing: {VERIFIED_CSV}\n"
            "Hand-curate it from the KEGOC PDFs (see KNOWN_CHART_PAGES).")
    df = pd.read_csv(VERIFIED_CSV)
    expected_cols = {"year", "metric", "value_bkwh",
                     "source_year_report", "source_page", "source_verified"}
    missing = expected_cols - set(df.columns)
    if missing:
        raise ValueError(f"verified CSV missing columns: {missing}")
    return df


def audit_against_verified(verified: pd.DataFrame, pdf_audit: pd.DataFrame,
                           tol: float = 0.5) -> list[str]:
    """For each (year, value) pair found in PDFs, check that there is a row
    in the verified table with a value within `tol` BkWh."""
    warnings: list[str] = []
    if pdf_audit.empty:
        return warnings
    for _, row in pdf_audit.iterrows():
        y = int(row["year"])
        v = float(row["value_bkwh"])
        match = verified[(verified.year == y) &
                         (abs(verified.value_bkwh - v) <= tol)]
        if match.empty:
            warnings.append(
                f"  AR{int(row['source_year_report'])} p.{int(row['source_page'])}: "
                f"PDF reports {y}={v:.2f} BkWh — not in verified table")
    return warnings


def write_to_sqlite(df: pd.DataFrame):
    if df.empty:
        return
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    df.to_sql("kegoc_balance", conn, if_exists="replace", index=False)
    # also a wide pivot for convenience
    wide = df.pivot_table(index="year", columns="metric",
                          values="value_bkwh", aggfunc="first")
    wide = wide.reset_index()
    wide.to_sql("kegoc_balance_wide", conn, if_exists="replace", index=False)
    conn.close()
    log.info(f"  wrote kegoc_balance + kegoc_balance_wide → {DB_PATH}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("pdf", nargs="?", help="Path to a single KEGOC PDF")
    p.add_argument("--all", metavar="DIR", help="Run audit on every PDF in DIR")
    p.add_argument("--year", type=int, help="Override year for single-PDF mode")
    p.add_argument("--verified-only", action="store_true",
                   help="Load verified CSV and write to SQLite without parsing PDFs")
    args = p.parse_args(argv)

    verified = load_verified()
    log.info(f"Loaded verified CSV: {len(verified)} rows "
             f"covering years {sorted(verified.year.unique().tolist())}")

    if args.verified_only:
        out_csv = CLEAN_DIR / "kegoc_balance.csv"
        verified.to_csv(out_csv, index=False)
        log.info(f"Wrote {out_csv} (verified, {len(verified)} rows)")
        write_to_sqlite(verified)
        print()
        print(verified.pivot_table(index="year", columns="metric",
                                   values="value_bkwh", aggfunc="first").to_string())
        return 0

    if args.all:
        audit = parse_all(Path(args.all))
    elif args.pdf:
        audit = parse_one(Path(args.pdf), args.year)
    else:
        audit = pd.DataFrame()

    if not audit.empty:
        audit_csv = CLEAN_DIR / "kegoc_balance_pdf_extracted.csv"
        audit.to_csv(audit_csv, index=False)
        log.info(f"PDF audit saved: {audit_csv} ({len(audit)} rows)")
        problems = audit_against_verified(verified, audit)
        if problems:
            log.warning("PDF-vs-verified discrepancies (review whether "
                        "verified CSV needs an update):")
            for w in problems:
                log.warning(w)
        else:
            log.info("PDF audit agrees with verified CSV within tolerance")

    # Always write verified data to the canonical outputs
    out_csv = CLEAN_DIR / "kegoc_balance.csv"
    verified.to_csv(out_csv, index=False)
    log.info(f"Wrote {out_csv} (verified, {len(verified)} rows)")
    write_to_sqlite(verified)
    print()
    print(verified.pivot_table(index="year", columns="metric",
                               values="value_bkwh", aggfunc="first").to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
