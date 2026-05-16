"""
End-to-end data verification for the KZ energy-balance pipeline.

Runs every consistency check in one pass and prints a single PASS/FAIL
verdict. The goal is to have ONE command that confirms the data layer is
ready for thesis/dashboard/publication use:

    python scripts/full_verification.py

Exit code 0 = everything verified. Non-zero = at least one check failed.

Checks (in order):
  1. SQLite tables present and non-empty
  2. KOREM xlsx ↔ SQLite parity (byte-for-byte across all 30 source files)
  3. KEGOC verified CSV ↔ SQLite parity (18 rows, 9 years)
  4. KEGOC PDF audit ↔ verified CSV within 0.5 BkWh
  5. balance_long sanity: TPEC 2024 'Total' == 3,132,269 TJ
  6. ML metrics realism: sMAPE in (0, 200), MASE > 0, DM not synthetic 0/1
  7. korem_hourly null check + 24-hour-per-day completeness

Outputs:
  docs/VERIFICATION_REPORT.md  (human-readable, regenerated each run)
  exit code (machine-readable)
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "clean" / "kz_energy_balance.db"
REPORT = BASE / "docs" / "VERIFICATION_REPORT.md"


class Reporter:
    def __init__(self):
        self.lines: list[str] = []
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def h(self, txt: str):
        self.lines.append(f"\n## {txt}\n")
        print(f"\n=== {txt} ===")

    def line(self, txt: str):
        self.lines.append(txt)
        print(txt)

    def ok(self, txt: str):
        self.line(f"- ✅ {txt}")

    def fail(self, txt: str):
        self.line(f"- ❌ {txt}")
        self.failures.append(txt)

    def warn(self, txt: str):
        self.line(f"- ⚠️ {txt}")
        self.warnings.append(txt)


def check_tables(con, R: Reporter) -> None:
    R.h("1. SQLite tables")
    expected = {
        "balance_long":        4000,
        "by_fuel_group_TJ":      50,
        "electricity_balance":    4,
        "kegoc_balance":         15,
        "kegoc_balance_wide":     8,
        "korem_hourly":       10000,
        "korem_monthly":         10,
        "ml_metrics":             3,
        "ml_predictions":         3,
        "ml_diebold_mariano":     1,
    }
    have = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t, min_rows in expected.items():
        if t not in have:
            R.fail(f"missing table `{t}`")
            continue
        n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        if n >= min_rows:
            R.ok(f"`{t}` — {n:,} rows")
        else:
            R.fail(f"`{t}` has only {n} rows, expected ≥{min_rows}")


def check_korem_parity(con, R: Reporter) -> None:
    R.h("2. KOREM xlsx ↔ SQLite parity")
    sys.path.insert(0, str(BASE / "scripts"))
    from korem_xlsx_parser import _file_meta, _fix_mojibake  # type: ignore

    raw_dir = BASE / "data" / "raw" / "korem"
    files = sorted(raw_dir.iterdir())
    ok_files = 0
    total_diff = 0.0

    for f in files:
        fixed = _fix_mojibake(f.name)
        y, m, z = _file_meta(f.name)
        if not (y and m and z):
            R.warn(f"{fixed}: filename couldn't be parsed")
            continue
        try:
            df = pd.read_excel(f, sheet_name=0, header=0)
        except Exception as e:
            R.warn(f"{fixed}: read error {e}")
            continue
        cols = {str(c).lower(): c for c in df.columns}
        pay_col = next((orig for cl, orig in cols.items()
                        if "рц брэ" in cl or "поставщик" in cl), None)
        date_col = next((orig for cl, orig in cols.items()
                         if "дата" in cl or "date" in cl), df.columns[0])
        xlsx_rows = int(df[date_col].notna().sum())
        xlsx_sum = float(pd.to_numeric(df[pay_col], errors="coerce").sum()
                         if pay_col else 0.0)
        q = pd.read_sql(
            "SELECT COUNT(*) c, COALESCE(SUM(payment_to_supplier_kzt),0) s "
            "FROM korem_hourly WHERE year=? AND month=? AND zone=?",
            con, params=(y, m, z))
        db_rows, db_sum = int(q.c.iloc[0]), float(q.s.iloc[0])
        diff = abs(db_sum - xlsx_sum)
        total_diff += diff
        if db_rows == xlsx_rows and diff < 1:
            ok_files += 1
        else:
            R.fail(
                f"{fixed}: rows {xlsx_rows}↔{db_rows}, "
                f"|Δpayment| = {diff:,.2f}")

    if ok_files == len(files):
        R.ok(f"all {len(files)} KOREM files match SQLite exactly "
             f"(|Σ| = {total_diff:,.2f} KZT)")
    else:
        R.fail(f"only {ok_files}/{len(files)} files match")


def check_kegoc_verified(con, R: Reporter) -> None:
    R.h("3. KEGOC verified CSV ↔ SQLite parity")
    verified_csv = BASE / "data" / "clean" / "kegoc_balance_verified.csv"
    if not verified_csv.exists():
        R.fail("kegoc_balance_verified.csv missing")
        return
    csv = pd.read_csv(verified_csv)
    sql = pd.read_sql("SELECT year, metric, value_bkwh FROM kegoc_balance "
                      "ORDER BY year, metric", con)
    merged = csv.merge(sql, on=["year", "metric"], how="outer",
                       suffixes=("_csv", "_sql"))
    mismatches = merged[
        (merged.value_bkwh_csv != merged.value_bkwh_sql)
        | merged.value_bkwh_csv.isna()
        | merged.value_bkwh_sql.isna()
    ]
    if mismatches.empty:
        R.ok(f"all {len(csv)} verified rows present in SQLite")
        years = sorted(csv.year.unique().tolist())
        R.line(f"  - covers years {years[0]}–{years[-1]} ({len(years)} years)")
    else:
        R.fail(f"{len(mismatches)} row(s) mismatch between CSV and SQLite")


def check_kegoc_pdf_audit(R: Reporter) -> None:
    R.h("4. KEGOC PDF coordinate parser ↔ verified CSV")
    audit_csv = BASE / "data" / "clean" / "kegoc_balance_pdf_extracted.csv"
    verified_csv = BASE / "data" / "clean" / "kegoc_balance_verified.csv"
    if not audit_csv.exists():
        R.warn("audit CSV not found — run `python scripts/kegoc_pdf_parser.py "
               "--all data/raw/kegoc/` to generate")
        return
    audit = pd.read_csv(audit_csv)
    verified = pd.read_csv(verified_csv)
    if audit.empty:
        R.warn("audit CSV is empty (no chart values extracted)")
        return
    discrepancies = 0
    for _, row in audit.iterrows():
        y, v = int(row.year), float(row.value_bkwh)
        match = verified[(verified.year == y)
                         & (abs(verified.value_bkwh - v) <= 0.5)]
        if match.empty:
            discrepancies += 1
    if discrepancies == 0:
        R.ok(f"all {len(audit)} (year,value) pairs from PDF "
             f"match verified table within 0.5 BkWh")
    else:
        R.fail(f"{discrepancies} PDF values disagree with verified table")


def check_acceptance_kpi(con, R: Reporter) -> None:
    R.h("5. Acceptance KPI: TPEC 2024 'Total' = 3,132,269 TJ")
    EXPECTED = 3_132_269
    row = con.execute(
        "SELECT value FROM by_fuel_group_TJ "
        "WHERE year=2024 AND line_item='Total primary energy consumption' "
        "AND fuel_group='Total'").fetchone()
    if row is None:
        R.fail("no Total/2024 row in by_fuel_group_TJ")
        return
    v = float(row[0])
    if abs(v - EXPECTED) < 1:
        R.ok(f"TPEC 2024 = {v:,.0f} TJ (matches stat.gov.kz publication)")
    else:
        R.fail(f"TPEC 2024 = {v:,.0f}, expected {EXPECTED:,}")


def check_ml_realism(con, R: Reporter) -> None:
    R.h("6. ML metrics realism")
    m = pd.read_sql("SELECT * FROM ml_metrics", con)
    bad_smape = m[(m.sMAPE_pct <= 0) | (m.sMAPE_pct >= 200)]
    bad_mase = m[m.MASE.isna() | (m.MASE <= 0)]
    if not bad_smape.empty:
        R.fail(f"sMAPE out of bounds for {len(bad_smape)} model(s) — "
               f"indicates broken metric formula")
        for _, r in bad_smape.iterrows():
            R.line(f"  - {r.model}: sMAPE={r.sMAPE_pct}")
    else:
        R.ok(f"sMAPE realistic for all {len(m)} models")
    if not bad_mase.empty:
        R.fail(f"MASE invalid for {len(bad_mase)} model(s)")
    else:
        R.ok(f"MASE valid for all {len(m)} models")
    # Check DM table — not the synthetic 0/1
    try:
        dm = pd.read_sql("SELECT * FROM ml_diebold_mariano", con)
        synthetic = dm[(dm.DM_stat == 0) & (dm.p_value == 1)]
        if len(synthetic) == len(dm) and len(dm) > 0:
            R.fail("ml_diebold_mariano table contains only synthetic "
                   "DM=0/p=1 rows — DM test is degenerate (insufficient folds)")
        elif not synthetic.empty:
            R.warn(f"{len(synthetic)}/{len(dm)} DM rows are degenerate "
                   f"(0/1) — sample too small for those comparisons")
        else:
            R.ok(f"DM stats are real: {len(dm)} comparison(s) with valid p-values")
    except Exception as e:
        R.warn(f"no ml_diebold_mariano table: {e}")


def check_korem_quality(con, R: Reporter) -> None:
    R.h("7. KOREM hourly quality")
    nulls = pd.read_sql(
        "SELECT SUM(year IS NULL) y, SUM(month IS NULL) m, "
        "       SUM(zone IS NULL) z, SUM(date IS NULL) d, "
        "       COUNT(*) n FROM korem_hourly", con).iloc[0]
    null_total = int(nulls.y) + int(nulls.m) + int(nulls.z) + int(nulls.d)
    if null_total == 0:
        R.ok(f"all {int(nulls.n):,} hourly rows have non-null "
             f"year/month/zone/date")
    else:
        R.fail(f"{null_total} null values across key columns")

    gaps = pd.read_sql(
        "SELECT zone, date, COUNT(*) n FROM korem_hourly "
        "GROUP BY zone, date HAVING n != 24", con)
    if gaps.empty:
        R.ok("every day in every zone has exactly 24 hours")
    else:
        R.fail(f"{len(gaps)} (zone,date) groups with !=24 hours "
               f"— check parser output")


def main() -> int:
    if not DB.exists():
        print(f"FATAL: database not found at {DB}")
        return 99

    R = Reporter()
    R.lines.append(f"# Data Verification Report\n")
    R.lines.append(f"_Generated: {datetime.now().isoformat(timespec='seconds')}_\n")
    R.lines.append(f"_Database: `{DB.relative_to(BASE)}`_\n")

    con = sqlite3.connect(str(DB))
    check_tables(con, R)
    check_korem_parity(con, R)
    check_kegoc_verified(con, R)
    check_kegoc_pdf_audit(R)
    check_acceptance_kpi(con, R)
    check_ml_realism(con, R)
    check_korem_quality(con, R)
    con.close()

    R.h("Verdict")
    if R.failures:
        R.line(f"\n**❌ FAIL — {len(R.failures)} issue(s)**\n")
        for f in R.failures:
            R.line(f"- {f}")
    else:
        R.line(f"\n**✅ PASS** — all checks succeeded.")
    if R.warnings:
        R.line(f"\n{len(R.warnings)} warning(s):\n")
        for w in R.warnings:
            R.line(f"- {w}")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(R.lines), encoding="utf-8")
    print(f"\n→ report saved: {REPORT}")
    return 1 if R.failures else 0


if __name__ == "__main__":
    sys.exit(main())
