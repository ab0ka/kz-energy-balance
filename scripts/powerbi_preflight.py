"""
Power BI dashboard pre-flight check
====================================
Validates that data/clean/kz_energy_balance.db is ready to be loaded into
Energy.pbix. Run this BEFORE opening Power BI Desktop:

    python3 scripts/powerbi_preflight.py

Exit code 0 = good to go. Non-zero = fix the data layer first.

Checks:
  1. All 8 tables that the dashboard consumes are present and non-empty.
  2. Acceptance KPI: TPEC 2024 'Total' = 3,132,269 TJ (Overview page).
  3. KOREM hourly has no NULL year/month/zone (Demand page).
  4. ML tables have ≥3 rows each (Forecasting page).
  5. Prints the ODBC connection string to paste into Power Query.
"""
from __future__ import annotations
import sqlite3
import sys
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
DB = BASE_DIR / "data" / "clean" / "kz_energy_balance.db"

REQUIRED_TABLES = {
    "balance_long":        ("Overview / drill-down by fuel & section",  4000),
    "by_fuel_group_TJ":    ("Overview KPI cards",                         50),
    "electricity_balance": ("Overview KPI",                                4),
    "kegoc_balance_wide":  ("Generation & Trade",                          4),
    "korem_hourly":        ("Demand page hourly profile",               10000),
    "korem_monthly":       ("Demand page monthly aggregates",              10),
    "ml_metrics":          ("Forecasting page model comparison",            3),
    "ml_predictions":      ("Forecasting page actuals vs forecast",         6),
}

EXPECTED_TPEC_2024 = 3_132_269  # TJ, fuel_group='Total'


def main() -> int:
    if not DB.exists():
        print(f"[FAIL] DB not found: {DB}")
        print("       run scripts/build_dataset.py first")
        return 2

    con = sqlite3.connect(str(DB))
    failures: list[str] = []

    # 1. Tables present
    have = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    print("=== Required tables ===")
    for t, (purpose, min_rows) in REQUIRED_TABLES.items():
        if t not in have:
            print(f"  [MISSING] {t:24s} — {purpose}")
            failures.append(f"missing table: {t}")
            continue
        n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        ok = n >= min_rows
        mark = "OK    " if ok else "LOW   "
        print(f"  [{mark}] {t:24s} rows={n:>6}  (min {min_rows}) — {purpose}")
        if not ok:
            failures.append(f"low row count: {t} has {n}, expected ≥{min_rows}")

    # 2. Acceptance KPI
    print("\n=== Acceptance KPI: TPEC 2024 ===")
    row = con.execute(
        "SELECT value FROM by_fuel_group_TJ "
        "WHERE year=2024 AND line_item='Total primary energy consumption' "
        "AND fuel_group='Total'").fetchone()
    if row is None:
        print("  [FAIL] no Total/Total row for 2024 in by_fuel_group_TJ")
        failures.append("missing 2024 Total TPEC row")
    else:
        v = float(row[0])
        delta = v - EXPECTED_TPEC_2024
        mark = "OK" if abs(delta) < 1 else "MISMATCH"
        print(f"  [{mark}] TPEC 2024 = {v:,.0f} TJ  (expected {EXPECTED_TPEC_2024:,})  Δ={delta:+,.1f}")
        if abs(delta) > 1:
            failures.append(f"TPEC 2024 mismatch: got {v:,.0f}, expected {EXPECTED_TPEC_2024:,}")

    # 3. KOREM null check
    print("\n=== KOREM hourly null check ===")
    nulls = pd.read_sql(
        "SELECT SUM(year IS NULL) y, SUM(month IS NULL) m, "
        "       SUM(zone IS NULL) z, SUM(date IS NULL) d "
        "FROM korem_hourly", con).iloc[0]
    print(f"  null counts: year={int(nulls.y)} month={int(nulls.m)} "
          f"zone={int(nulls.z)} date={int(nulls.d)}")
    if int(nulls.y) or int(nulls.m) or int(nulls.z) or int(nulls.d):
        failures.append("KOREM has NULL in key columns — re-run korem_xlsx_parser.py")

    # 4. Hours-per-day completeness
    gaps = pd.read_sql(
        "SELECT zone, date, COUNT(*) n FROM korem_hourly "
        "GROUP BY zone, date HAVING n != 24", con)
    if len(gaps):
        print(f"  [WARN] {len(gaps)} (zone,date) groups with !=24 hours")
        print(gaps.head().to_string(index=False))
    else:
        print("  [OK    ] every day has exactly 24 hours in both zones")

    # 5. Print ODBC string
    print("\n=== ODBC connection string (paste into Power Query → Get Data → ODBC) ===")
    print(f"  Driver=SQLite3 ODBC Driver;Database={DB.as_posix()}")
    print(f"\n  (UNC absolute path on this machine: {DB})")

    # Verdict
    print()
    if failures:
        print(f"[FAIL] {len(failures)} issue(s):")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("[OK] Pre-flight passed. Open Energy.pbix → Home → Refresh.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
