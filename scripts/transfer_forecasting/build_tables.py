#!/usr/bin/env python3
"""Build canonical forecasting tables for EU pretraining and KZ transfer learning."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
PROCESSED_DIR = DATA_DIR / "processed"
FORECASTING_DIR = DATA_DIR / "forecasting"

OPSD_URL = (
    "https://data.open-power-system-data.org/time_series/latest/"
    "time_series_60min_singleindex.csv"
)

SELECTED_EU_REGIONS = ["DE", "FR", "ES", "IT", "NL", "PL", "SE", "AT", "BE", "CZ"]



def _load_eu_hourly_opsd(start_year: int = 2020, end_year: int = 2024) -> pd.DataFrame:
    raw = pd.read_csv(OPSD_URL)

    ts_col = "utc_timestamp" if "utc_timestamp" in raw.columns else raw.columns[0]
    raw[ts_col] = pd.to_datetime(raw[ts_col], errors="coerce", utc=True)
    raw = raw[raw[ts_col].notna()].copy()

    # ENTSO-E load columns follow pattern: DE_load_actual_entsoe_transparency
    load_cols = [c for c in raw.columns if c.endswith("_load_actual_entsoe_transparency")]
    keep_cols = [ts_col] + load_cols
    df = raw[keep_cols].copy()

    long = df.melt(id_vars=[ts_col], var_name="series", value_name="load_mw")
    long["region_id"] = long["series"].str.split("_", n=1).str[0]
    long = long[long["region_id"].isin(SELECTED_EU_REGIONS)]
    long = long[long["load_mw"].notna()].copy()

    long = long.rename(columns={ts_col: "timestamp_utc"})
    long["timestamp_utc"] = long["timestamp_utc"].dt.tz_convert("UTC").dt.tz_localize(None)
    long = long[
        (long["timestamp_utc"].dt.year >= start_year)
        & (long["timestamp_utc"].dt.year <= end_year)
    ].copy()

    long["source"] = "opsd_entsoe"
    long["is_synthetic_disaggregation"] = 0

    return long[["timestamp_utc", "region_id", "load_mw", "source", "is_synthetic_disaggregation"]]



def _build_intraday_profile(eu_hourly: pd.DataFrame) -> pd.DataFrame:
    temp = eu_hourly.copy()
    temp["month"] = temp["timestamp_utc"].dt.month
    temp["dow"] = temp["timestamp_utc"].dt.dayofweek
    temp["hour"] = temp["timestamp_utc"].dt.hour

    profile = (
        temp.groupby(["month", "dow", "hour"], as_index=False)["load_mw"]
        .mean()
        .rename(columns={"load_mw": "profile_weight_raw"})
    )

    # Backstop profile by hour only (used for rare missing month/dow/hour combos)
    hour_backstop = (
        temp.groupby(["hour"], as_index=False)["load_mw"]
        .mean()
        .rename(columns={"load_mw": "hour_backstop_raw"})
    )

    profile = profile.merge(hour_backstop, on="hour", how="left")
    profile["profile_weight_raw"] = profile["profile_weight_raw"].fillna(profile["hour_backstop_raw"])
    return profile[["month", "dow", "hour", "profile_weight_raw"]]



def _load_kz_monthly_demand() -> pd.DataFrame:
    path = DATA_DIR / "kz_demand_validation.csv"
    df = pd.read_csv(path)

    for c in ["year", "month", "demand_gegis", "demand_korem"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # Use official KOREM where available, otherwise use scaled GEGIS as fallback.
    overlap = df[df["demand_korem"].notna() & df["demand_gegis"].notna()].copy()
    if overlap.empty:
        scale = 1.0
    else:
        scale = float((overlap["demand_korem"] / overlap["demand_gegis"]).median())

    df["demand_gwh_month"] = df["demand_korem"]
    missing_official = df["demand_gwh_month"].isna()
    df.loc[missing_official, "demand_gwh_month"] = df.loc[missing_official, "demand_gegis"] * scale
    df["demand_source"] = np.where(df["demand_korem"].notna(), "korem", "gegis_scaled")

    df = df[df["demand_gwh_month"].notna()].copy()
    df["year"] = df["year"].astype(int)
    df["month"] = df["month"].astype(int)
    return df[["year", "month", "demand_gwh_month", "demand_source"]]



def _disaggregate_kz_monthly_to_hourly(
    kz_monthly: pd.DataFrame,
    profile: pd.DataFrame,
) -> pd.DataFrame:
    rows: List[pd.DataFrame] = []

    for rec in kz_monthly.itertuples(index=False):
        start = pd.Timestamp(year=rec.year, month=rec.month, day=1, hour=0)
        end = (start + pd.offsets.MonthEnd(1)).replace(hour=23)
        hours = pd.date_range(start=start, end=end, freq="h")

        tmp = pd.DataFrame({"timestamp_utc": hours})
        tmp["month"] = tmp["timestamp_utc"].dt.month
        tmp["dow"] = tmp["timestamp_utc"].dt.dayofweek
        tmp["hour"] = tmp["timestamp_utc"].dt.hour

        tmp = tmp.merge(profile, on=["month", "dow", "hour"], how="left")

        # Fill any profile gaps with hour-of-day average within current month slice.
        if tmp["profile_weight_raw"].isna().any():
            h_fallback = tmp.groupby("hour")["profile_weight_raw"].transform("mean")
            tmp["profile_weight_raw"] = tmp["profile_weight_raw"].fillna(h_fallback)
            tmp["profile_weight_raw"] = tmp["profile_weight_raw"].fillna(tmp["profile_weight_raw"].mean())

        weights = tmp["profile_weight_raw"].clip(lower=0)
        if weights.sum() <= 0:
            weights = pd.Series(np.full(len(tmp), 1 / len(tmp)), index=tmp.index)
        else:
            weights = weights / weights.sum()

        monthly_mwh = float(rec.demand_gwh_month) * 1000.0
        tmp["load_mw"] = weights * monthly_mwh  # 1h step => MW equals MWh/h
        tmp["region_id"] = "KZ_SYSTEM"
        tmp["source"] = f"kz_monthly_disaggregated_{rec.demand_source}"
        tmp["is_synthetic_disaggregation"] = 1

        rows.append(tmp[["timestamp_utc", "region_id", "load_mw", "source", "is_synthetic_disaggregation"]])

    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()



def _build_weather_table(fetch_weather: bool = False) -> pd.DataFrame:
    weather_path = DATA_DIR / "2011-2014_solar-wind_fixed.csv"
    df = pd.read_csv(weather_path)

    if "Time" not in df.columns:
        raise ValueError("Expected Time column in 2011-2014_solar-wind_fixed.csv")

    df["timestamp_utc"] = pd.to_datetime(df["Time"], errors="coerce")
    df = df[df["timestamp_utc"].notna()].copy()

    df["wind_cf"] = pd.to_numeric(df.get("onwind"), errors="coerce")
    df["solar_cf"] = pd.to_numeric(df.get("solar"), errors="coerce")

    # Proxy physical variables from CF when weather API is unavailable.
    df["wind_speed_10m"] = df["wind_cf"].clip(lower=0) * 15.0
    df["shortwave_radiation"] = df["solar_cf"].clip(lower=0) * 1000.0
    df["temperature_2m"] = np.nan
    df["relative_humidity_2m"] = np.nan

    out = df[
        [
            "timestamp_utc",
            "wind_cf",
            "solar_cf",
            "wind_speed_10m",
            "shortwave_radiation",
            "temperature_2m",
            "relative_humidity_2m",
        ]
    ].copy()

    out["region_id"] = "KZ_SYSTEM"
    out["source"] = "local_era5_cf"

    if fetch_weather:
        out = _merge_open_meteo_weather(out)

    return out[
        [
            "timestamp_utc",
            "region_id",
            "temperature_2m",
            "wind_speed_10m",
            "shortwave_radiation",
            "relative_humidity_2m",
            "wind_cf",
            "solar_cf",
            "source",
        ]
    ]



def _merge_open_meteo_weather(df: pd.DataFrame) -> pd.DataFrame:
    try:
        import urllib.parse
        import urllib.request

        start = df["timestamp_utc"].min().date().isoformat()
        end = df["timestamp_utc"].max().date().isoformat()

        params = {
            "latitude": 51.1694,
            "longitude": 71.4491,
            "start_date": start,
            "end_date": end,
            "hourly": "temperature_2m,relative_humidity_2m,wind_speed_10m,shortwave_radiation",
            "timezone": "UTC",
        }
        url = f"https://archive-api.open-meteo.com/v1/archive?{urllib.parse.urlencode(params)}"

        with urllib.request.urlopen(url, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))

        hourly = payload.get("hourly", {})
        wx = pd.DataFrame(
            {
                "timestamp_utc": pd.to_datetime(hourly.get("time", []), errors="coerce"),
                "temperature_2m": pd.to_numeric(hourly.get("temperature_2m", []), errors="coerce"),
                "relative_humidity_2m": pd.to_numeric(hourly.get("relative_humidity_2m", []), errors="coerce"),
                "wind_speed_10m": pd.to_numeric(hourly.get("wind_speed_10m", []), errors="coerce"),
                "shortwave_radiation": pd.to_numeric(hourly.get("shortwave_radiation", []), errors="coerce"),
            }
        )
        wx = wx[wx["timestamp_utc"].notna()]

        merged = df.merge(
            wx,
            on="timestamp_utc",
            how="left",
            suffixes=("", "_open_meteo"),
        )

        for col in ["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "shortwave_radiation"]:
            alt = f"{col}_open_meteo"
            if alt in merged.columns:
                merged[col] = merged[alt].combine_first(merged[col])
                merged = merged.drop(columns=[alt])

        merged["source"] = "local_era5_cf+open_meteo"
        return merged
    except Exception:
        # Keep proxy values if external fetch fails.
        return df



def _build_calendar_table(timestamps: Iterable[pd.Timestamp]) -> pd.DataFrame:
    idx = pd.Index(pd.to_datetime(pd.Series(list(timestamps))).dropna().unique())
    cal = pd.DataFrame({"timestamp_utc": idx.sort_values()})

    cal["hour"] = cal["timestamp_utc"].dt.hour
    cal["dow"] = cal["timestamp_utc"].dt.dayofweek
    cal["month"] = cal["timestamp_utc"].dt.month
    cal["is_weekend"] = cal["dow"].isin([5, 6]).astype(int)
    cal["is_month_start"] = cal["timestamp_utc"].dt.is_month_start.astype(int)
    cal["is_month_end"] = cal["timestamp_utc"].dt.is_month_end.astype(int)

    try:
        import holidays

        years = sorted(cal["timestamp_utc"].dt.year.unique().tolist())
        kz_holidays = holidays.country_holidays("KZ", years=years)
        cal["is_kz_holiday"] = cal["timestamp_utc"].dt.date.map(lambda d: int(d in kz_holidays))
    except Exception:
        cal["is_kz_holiday"] = 0

    return cal



def _build_macro_table(region_ids: List[str]) -> pd.DataFrame:
    # KZ macro from synced processed file
    kz_path = PROCESSED_DIR / "kz_owid_energy_2000_2024.csv"
    if not kz_path.exists():
        kz_path = PROCESSED_DIR / "kz_owid_energy_2000_2023.csv"

    kz = pd.read_csv(kz_path)
    kz["year"] = pd.to_numeric(kz["year"], errors="coerce")
    kz = kz[kz["year"].notna()].copy()
    kz["year"] = kz["year"].astype(int)

    kz_macro = pd.DataFrame(
        {
            "year": kz["year"],
            "region_id": "KZ_SYSTEM",
            "country": "Kazakhstan",
            "industry_index": np.nan,
            "gdp_proxy": kz.get("gdp"),
            "energy_intensity": kz.get("energy_per_gdp"),
        }
    )

    # Optional EU macro from raw OWID file if available
    eu_macro_frames: List[pd.DataFrame] = []
    owid_external = DATA_DIR / "external" / "owid_energy_data.csv"
    if owid_external.exists():
        owid = pd.read_csv(owid_external, usecols=["iso_code", "country", "year", "gdp", "energy_per_gdp"])
        owid["year"] = pd.to_numeric(owid["year"], errors="coerce")
        owid = owid[owid["year"].notna()].copy()
        owid["year"] = owid["year"].astype(int)

        for region in sorted(set(r for r in region_ids if r != "KZ_SYSTEM")):
            sub = owid[owid["iso_code"].astype(str).eq(region)]
            if sub.empty:
                continue
            eu_macro_frames.append(
                pd.DataFrame(
                    {
                        "year": sub["year"],
                        "region_id": region,
                        "country": sub["country"],
                        "industry_index": np.nan,
                        "gdp_proxy": sub["gdp"],
                        "energy_intensity": sub["energy_per_gdp"],
                    }
                )
            )

    macro = pd.concat([kz_macro] + eu_macro_frames, ignore_index=True)
    macro = macro.drop_duplicates(subset=["year", "region_id"], keep="last")
    return macro.sort_values(["region_id", "year"]).reset_index(drop=True)



def run(fetch_weather: bool = False) -> Dict[str, object]:
    FORECASTING_DIR.mkdir(parents=True, exist_ok=True)

    eu_hourly = _load_eu_hourly_opsd()
    profile = _build_intraday_profile(eu_hourly)
    kz_monthly = _load_kz_monthly_demand()
    kz_hourly = _disaggregate_kz_monthly_to_hourly(kz_monthly, profile)

    load = pd.concat([eu_hourly, kz_hourly], ignore_index=True)
    load = load.sort_values(["region_id", "timestamp_utc"]).reset_index(drop=True)
    load.to_csv(FORECASTING_DIR / "table_load_hourly.csv", index=False)

    weather = _build_weather_table(fetch_weather=fetch_weather)
    weather.to_csv(FORECASTING_DIR / "table_weather_hourly.csv", index=False)

    calendar = _build_calendar_table(load["timestamp_utc"])
    calendar.to_csv(FORECASTING_DIR / "table_calendar.csv", index=False)

    macro = _build_macro_table(sorted(load["region_id"].unique().tolist()))
    macro.to_csv(FORECASTING_DIR / "table_macro_annual.csv", index=False)

    summary = {
        "table_load_hourly_rows": int(len(load)),
        "table_weather_hourly_rows": int(len(weather)),
        "table_calendar_rows": int(len(calendar)),
        "table_macro_annual_rows": int(len(macro)),
        "regions": sorted(load["region_id"].unique().tolist()),
        "kz_time_range": {
            "min": str(load.loc[load["region_id"] == "KZ_SYSTEM", "timestamp_utc"].min()),
            "max": str(load.loc[load["region_id"] == "KZ_SYSTEM", "timestamp_utc"].max()),
        },
        "eu_time_range": {
            "min": str(load.loc[load["region_id"] != "KZ_SYSTEM", "timestamp_utc"].min()),
            "max": str(load.loc[load["region_id"] != "KZ_SYSTEM", "timestamp_utc"].max()),
        },
    }

    manifest_path = FORECASTING_DIR / "build_manifest.json"
    manifest_path.write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    out = run(fetch_weather=False)
    print(json.dumps(out, ensure_ascii=True, indent=2))
