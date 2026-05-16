#!/usr/bin/env python3
"""Data cleaning and QA rules for transfer-learning demand forecasting pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
PROCESSED_DIR = DATA_DIR / "processed"
QA_DIR = PROCESSED_DIR / "qa_reports"

SOLAR_WIND_FILES = [
    DATA_DIR / "2011-2014_solar-wind.csv",
    DATA_DIR / "2011-2014_solar-wind_fixed.csv",
]
OWID_LEGACY_PATH = PROCESSED_DIR / "kz_owid_energy_2000_2023.csv"
OWID_SYNC_PATH = PROCESSED_DIR / "kz_owid_energy_2000_2024.csv"
EMBER_PATH = PROCESSED_DIR / "kz_ember_electricity.csv"
OWID_EXTERNAL_PATH = DATA_DIR / "external" / "owid_energy_data.csv"


@dataclass
class CleaningSummary:
    file: str
    rows_before: int
    rows_after: int
    dropped_blank_rows: int
    dropped_invalid_time_rows: int
    dropped_duplicates: int
    missing_hourly_timestamps: int



def clean_solar_wind_file(path: Path) -> CleaningSummary:
    df = pd.read_csv(path)
    rows_before = len(df)

    if "Time" not in df.columns:
        raise ValueError(f"Expected 'Time' column in {path}")

    # Normalize timestamp format: YYYY-MM-DDHH:MM:SS -> YYYY-MM-DD HH:MM:SS
    df["Time"] = df["Time"].astype(str).str.strip()
    mask_compact = df["Time"].str.match(r"^\d{4}-\d{2}-\d{2}\d{2}:\d{2}:\d{2}$", na=False)
    df.loc[mask_compact, "Time"] = df.loc[mask_compact, "Time"].str.replace(
        r"^(\d{4}-\d{2}-\d{2})(\d{2}:\d{2}:\d{2})$", r"\1 \2", regex=True
    )

    df["Time"] = pd.to_datetime(df["Time"], errors="coerce", format="%Y-%m-%d %H:%M:%S")

    non_time_cols = [c for c in df.columns if c != "Time"]
    blank_rows = df[non_time_cols].isna().all(axis=1) if non_time_cols else pd.Series(False, index=df.index)
    invalid_time_rows = df["Time"].isna()

    dropped_blank_rows = int(blank_rows.sum())
    dropped_invalid_time_rows = int((invalid_time_rows & ~blank_rows).sum())

    df = df.loc[~(blank_rows | invalid_time_rows)].copy()

    before_dedup = len(df)
    df = df.drop_duplicates(subset=["Time"], keep="first")
    dropped_duplicates = before_dedup - len(df)

    df = df.sort_values("Time").reset_index(drop=True)

    if df.empty:
        missing_hourly_timestamps = 0
    else:
        expected = pd.date_range(df["Time"].min(), df["Time"].max(), freq="h")
        missing_hourly_timestamps = int(len(expected.difference(df["Time"])))

    df["Time"] = df["Time"].dt.strftime("%Y-%m-%d %H:%M:%S")
    df.to_csv(path, index=False)

    return CleaningSummary(
        file=str(path.relative_to(BASE_DIR)),
        rows_before=rows_before,
        rows_after=len(df),
        dropped_blank_rows=dropped_blank_rows,
        dropped_invalid_time_rows=dropped_invalid_time_rows,
        dropped_duplicates=dropped_duplicates,
        missing_hourly_timestamps=missing_hourly_timestamps,
    )



def sync_owid_file() -> Dict[str, object]:
    df = pd.read_csv(OWID_LEGACY_PATH)

    if "year" not in df.columns:
        raise ValueError(f"Expected 'year' column in {OWID_LEGACY_PATH}")

    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df = df[df["year"].notna()].copy()
    df["year"] = df["year"].astype(int)

    if "gdp" in df.columns:
        df["gdp_missing_flag"] = df["gdp"].isna().astype(int)
    else:
        df["gdp_missing_flag"] = 1

    if "energy_per_gdp" in df.columns:
        df["energy_per_gdp_missing_flag"] = df["energy_per_gdp"].isna().astype(int)
    else:
        df["energy_per_gdp_missing_flag"] = 1

    # If external OWID exists, append/refresh Kazakhstan rows to keep 2024 coverage.
    if OWID_EXTERNAL_PATH.exists():
        ext = pd.read_csv(OWID_EXTERNAL_PATH)
        if {"country", "year"}.issubset(ext.columns):
            kz_ext = ext[ext["country"].astype(str).str.lower().eq("kazakhstan")].copy()
            kz_ext["year"] = pd.to_numeric(kz_ext["year"], errors="coerce")
            kz_ext = kz_ext[kz_ext["year"].notna()].copy()
            kz_ext["year"] = kz_ext["year"].astype(int)
            kz_ext = kz_ext[kz_ext["year"] >= 2000]

            # Keep only columns known in current processed schema.
            shared_cols = [c for c in df.columns if c in kz_ext.columns]
            if shared_cols:
                kz_ext = kz_ext[shared_cols].copy()
                df = pd.concat([df, kz_ext], ignore_index=True)
                df = df.sort_values("year").drop_duplicates(subset=["year"], keep="last").reset_index(drop=True)

                if "gdp" in df.columns:
                    df["gdp_missing_flag"] = df["gdp"].isna().astype(int)
                if "energy_per_gdp" in df.columns:
                    df["energy_per_gdp_missing_flag"] = df["energy_per_gdp"].isna().astype(int)

    df_2024 = df[df["year"] <= 2024].sort_values("year").reset_index(drop=True)
    df_2023 = df_2024[df_2024["year"] <= 2023].copy()

    # Keep legacy file aligned to its name (<=2023)
    df_2023.to_csv(OWID_LEGACY_PATH, index=False)
    # Write synced file with 2024 included
    df_2024.to_csv(OWID_SYNC_PATH, index=False)

    return {
        "legacy_file": str(OWID_LEGACY_PATH.relative_to(BASE_DIR)),
        "synced_file": str(OWID_SYNC_PATH.relative_to(BASE_DIR)),
        "legacy_max_year": int(df_2023["year"].max()) if not df_2023.empty else None,
        "synced_max_year": int(df_2024["year"].max()) if not df_2024.empty else None,
        "gdp_missing_years": sorted(df_2024.loc[df_2024["gdp_missing_flag"] == 1, "year"].tolist()),
        "energy_per_gdp_missing_years": sorted(
            df_2024.loc[df_2024["energy_per_gdp_missing_flag"] == 1, "year"].tolist()
        ),
    }



def apply_ember_negative_value_rule() -> Dict[str, object]:
    df = pd.read_csv(EMBER_PATH)

    if "Value" not in df.columns:
        raise ValueError(f"Expected 'Value' column in {EMBER_PATH}")

    df["Value"] = pd.to_numeric(df["Value"], errors="coerce")
    neg_mask = df["Value"] < 0

    variable_col = "Variable" if "Variable" in df.columns else None
    if variable_col is None:
        valid_neg_mask = pd.Series(False, index=df.index)
    else:
        valid_neg_mask = neg_mask & df[variable_col].astype(str).str.lower().eq("net imports")

    invalid_neg_mask = neg_mask & ~valid_neg_mask
    invalid_count = int(invalid_neg_mask.sum())

    # Enforce rule: invalid negatives are set to NaN and flagged
    if invalid_count:
        df.loc[invalid_neg_mask, "Value"] = np.nan

    df["negative_value_rule_ok"] = (~neg_mask) | valid_neg_mask
    df.to_csv(EMBER_PATH, index=False)

    return {
        "file": str(EMBER_PATH.relative_to(BASE_DIR)),
        "negative_values_total": int(neg_mask.sum()),
        "valid_negative_values": int(valid_neg_mask.sum()),
        "invalid_negative_values": invalid_count,
    }



def run() -> Path:
    QA_DIR.mkdir(parents=True, exist_ok=True)

    solar_wind_summaries: List[Dict[str, object]] = []
    for path in SOLAR_WIND_FILES:
        summary = clean_solar_wind_file(path)
        solar_wind_summaries.append(summary.__dict__)

    owid_summary = sync_owid_file()
    ember_summary = apply_ember_negative_value_rule()

    report = {
        "pipeline": "transfer_forecasting_cleaning",
        "solar_wind": solar_wind_summaries,
        "owid_sync": owid_summary,
        "ember_negative_rule": ember_summary,
    }

    report_path = QA_DIR / "transfer_forecasting_cleaning_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    return report_path


if __name__ == "__main__":
    report_path = run()
    print(f"Cleaning complete. QA report: {report_path}")
