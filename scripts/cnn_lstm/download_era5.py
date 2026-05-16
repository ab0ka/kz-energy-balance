#!/usr/bin/env python3
"""
Download ERA5-Land (and ERA5 pressure-level) hourly data from the
Copernicus Climate Data Store (CDS) for Kazakhstan renewable-energy zones.

Prerequisites
-------------
1. Install the CDS API client:        pip install cdsapi xarray netCDF4 pandas numpy
2. Create a CDS account at            https://cds.climate.copernicus.eu
3. Place your API key in              ~/.cdsapirc
   (see https://cds.climate.copernicus.eu/how-to-api for details)

Usage
-----
    # Download all years (2011-2024) for every location
    python download_era5.py --download

    # Post-process downloaded NetCDF files into CSV
    python download_era5.py --process

    # Do both
    python download_era5.py --download --process

    # Download a single year (useful for debugging / resuming)
    python download_era5.py --download --year 2023
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import xarray as xr

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Project paths (adjust if your layout differs)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

# Representative locations in Kazakhstan
# Each entry: (label, latitude, longitude, primary_resource)
LOCATIONS: List[Tuple[str, float, float, str]] = [
    ("astana_wind",     51.1, 71.4, "wind"),   # Astana / Central steppe
    ("zhambyl_solar",   43.3, 70.4, "solar"),  # Zhambyl / South
    ("mangystau_wind",  43.6, 51.1, "wind"),   # Mangystau / West Caspian
    ("almaty_solar",    43.2, 76.9, "solar"),  # Almaty / Southeast
]

# Bounding box that encompasses all locations (with a small margin)
# CDS expects [North, West, South, East]
LAT_MIN = min(loc[1] for loc in LOCATIONS) - 0.5
LAT_MAX = max(loc[1] for loc in LOCATIONS) + 0.5
LON_MIN = min(loc[2] for loc in LOCATIONS) - 0.5
LON_MAX = max(loc[2] for loc in LOCATIONS) + 0.5
AREA = [LAT_MAX, LON_MIN, LAT_MIN, LON_MAX]  # [N, W, S, E]

# Time range
YEAR_START = 2011
YEAR_END = 2024

# All months / days / hours (strings as CDS expects)
MONTHS = [f"{m:02d}" for m in range(1, 13)]
DAYS = [f"{d:02d}" for d in range(1, 32)]
HOURS = [f"{h:02d}:00" for h in range(24)]

# ---- Variables split by dataset ----
# ERA5-Land variables (available in "reanalysis-era5-land")
ERA5_LAND_VARIABLES = [
    "10m_u_component_of_wind",
    "10m_v_component_of_wind",
    "2m_temperature",
    "surface_solar_radiation_downwards",
    "surface_pressure",
    "2m_dewpoint_temperature",
]

# ERA5 single-level variables NOT present in ERA5-Land
# (available in "reanalysis-era5-single-levels")
ERA5_SINGLE_LEVEL_VARIABLES = [
    "100m_u_component_of_wind",
    "100m_v_component_of_wind",
    "total_cloud_cover",
]

# ---------------------------------------------------------------------------
# Simple turbine & solar models for capacity-factor estimation
# ---------------------------------------------------------------------------

# Generic 3 MW onshore wind turbine power curve (wind speed m/s -> CF)
# Cut-in 3 m/s, rated 12 m/s, cut-out 25 m/s, rated power = 1.0 (normalised)
TURBINE_CUT_IN = 3.0      # m/s
TURBINE_RATED = 12.0       # m/s
TURBINE_CUT_OUT = 25.0     # m/s


def wind_capacity_factor(ws: np.ndarray) -> np.ndarray:
    """
    Compute normalised power output from a simplified cubic power curve.

    P(ws) =  0                                     if ws < cut_in or ws > cut_out
             ((ws - cut_in) / (rated - cut_in))^3   if cut_in <= ws < rated
             1.0                                     if rated <= ws <= cut_out
    """
    cf = np.where(
        ws < TURBINE_CUT_IN, 0.0,
        np.where(
            ws < TURBINE_RATED,
            ((ws - TURBINE_CUT_IN) / (TURBINE_RATED - TURBINE_CUT_IN)) ** 3,
            np.where(ws <= TURBINE_CUT_OUT, 1.0, 0.0),
        ),
    )
    return cf


# Simple solar model: CF ~ GHI / STC_irradiance, with temperature derating
STC_IRRADIANCE = 1000.0   # W/m^2 at Standard Test Conditions
TEMP_COEFF = -0.004        # typical crystalline-Si temperature coefficient (%/K)
T_STC = 25.0               # cell temperature at STC (degC)


def solar_capacity_factor(ghi_wm2: np.ndarray, t2m_C: np.ndarray) -> np.ndarray:
    """
    Estimate PV capacity factor from global horizontal irradiance and
    ambient temperature using a simple linear de-rating model.

    CF = (GHI / 1000) * [1 + gamma * (T_cell - 25)]

    T_cell is approximated as T_ambient + 25  (NOCT simplification).
    """
    t_cell = t2m_C + 25.0  # rough NOCT approximation
    cf_raw = (ghi_wm2 / STC_IRRADIANCE) * (1.0 + TEMP_COEFF * (t_cell - T_STC))
    return np.clip(cf_raw, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def setup_logging(verbose: bool = False) -> logging.Logger:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger("era5_downloader")


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------

def _cds_client():
    """Return a cdsapi.Client, failing fast with a helpful message."""
    try:
        import cdsapi
    except ImportError:
        sys.exit(
            "ERROR: cdsapi is not installed.  Run:  pip install cdsapi\n"
            "Then configure your API key – see https://cds.climate.copernicus.eu/how-to-api"
        )
    return cdsapi.Client()


def download_era5_land_year(client, year: int, target_dir: Path, log: logging.Logger):
    """
    Download ERA5-Land hourly data for a single year.

    Saves to  <target_dir>/era5land_<year>.nc
    """
    target = target_dir / f"era5land_{year}.nc"
    if target.exists():
        log.info("  [skip] %s already exists", target.name)
        return

    log.info("  Requesting ERA5-Land data for %d ...", year)
    client.retrieve(
        "reanalysis-era5-land",
        {
            "product_type": "reanalysis",
            "variable": ERA5_LAND_VARIABLES,
            "year": str(year),
            "month": MONTHS,
            "day": DAYS,
            "time": HOURS,
            "area": AREA,
            "format": "netcdf",
        },
        str(target),
    )
    log.info("  Saved %s (%.1f MB)", target.name, target.stat().st_size / 1e6)


def download_era5_single_level_year(client, year: int, target_dir: Path, log: logging.Logger):
    """
    Download ERA5 single-level hourly data (100 m wind, cloud cover) for
    a single year.

    Saves to  <target_dir>/era5sl_<year>.nc
    """
    target = target_dir / f"era5sl_{year}.nc"
    if target.exists():
        log.info("  [skip] %s already exists", target.name)
        return

    log.info("  Requesting ERA5 single-level data for %d ...", year)
    client.retrieve(
        "reanalysis-era5-single-levels",
        {
            "product_type": "reanalysis",
            "variable": ERA5_SINGLE_LEVEL_VARIABLES,
            "year": str(year),
            "month": MONTHS,
            "day": DAYS,
            "time": HOURS,
            "area": AREA,
            "format": "netcdf",
        },
        str(target),
    )
    log.info("  Saved %s (%.1f MB)", target.name, target.stat().st_size / 1e6)


def download_all(years: List[int], log: logging.Logger):
    """Download ERA5-Land and ERA5 single-level data year by year."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    client = _cds_client()

    total = len(years)
    for i, year in enumerate(years, 1):
        log.info("=== Year %d  (%d / %d) ===", year, i, total)
        try:
            download_era5_land_year(client, year, RAW_DIR, log)
        except Exception as exc:
            log.error("  ERA5-Land download failed for %d: %s", year, exc)

        try:
            download_era5_single_level_year(client, year, RAW_DIR, log)
        except Exception as exc:
            log.error("  ERA5 single-level download failed for %d: %s", year, exc)

    log.info("Download phase complete.")


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------

def _nearest_point(ds: xr.Dataset, lat: float, lon: float) -> xr.Dataset:
    """Select the nearest grid cell to (lat, lon) in the dataset."""
    return ds.sel(latitude=lat, longitude=lon, method="nearest")


def process_location(
    land_files: List[Path],
    sl_files: List[Path],
    label: str,
    lat: float,
    lon: float,
    resource: str,
    log: logging.Logger,
) -> pd.DataFrame:
    """
    For one location: open all NetCDF files, extract the nearest grid point,
    merge ERA5-Land and ERA5-SL datasets, compute derived variables, and
    return a tidy DataFrame.
    """
    log.info("  Processing location: %s (%.2f N, %.2f E)", label, lat, lon)

    # ---- Open and select nearest grid point from ERA5-Land files ----
    ds_land = xr.open_mfdataset(sorted(land_files), combine="by_coords")
    ds_land = _nearest_point(ds_land, lat, lon)

    # ---- Open and select nearest grid point from ERA5 single-level files ----
    ds_sl = xr.open_mfdataset(sorted(sl_files), combine="by_coords")
    ds_sl = _nearest_point(ds_sl, lat, lon)

    # ---- Merge into a single dataset ----
    # Use 'time' as the join dimension; inner join avoids NaN gaps
    ds = xr.merge([ds_land, ds_sl], join="inner")

    # ---- Convert to DataFrame ----
    df = ds.to_dataframe().reset_index()

    # Drop spatial columns if they became scalars
    for col in ("latitude", "longitude"):
        if col in df.columns:
            df = df.drop(columns=[col])

    # ---- Rename columns to human-friendly names ----
    rename_map = {
        "u10": "u10",               # 10 m u-wind (m/s)
        "v10": "v10",               # 10 m v-wind (m/s)
        "u100": "u100",             # 100 m u-wind (m/s)
        "v100": "v100",             # 100 m v-wind (m/s)
        "t2m": "t2m_K",             # 2 m temperature (K)
        "ssrd": "ssrd_Jm2",         # surface solar radiation downwards (J/m^2, accumulated)
        "sp": "sp_Pa",              # surface pressure (Pa)
        "tcc": "tcc",               # total cloud cover (0-1)
        "d2m": "d2m_K",             # 2 m dewpoint temperature (K)
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    # ---- Derived variables ----

    # Temperature in Celsius
    if "t2m_K" in df.columns:
        df["t2m_C"] = df["t2m_K"] - 273.15

    if "d2m_K" in df.columns:
        df["d2m_C"] = df["d2m_K"] - 273.15

    # Wind speed at 10 m and 100 m
    if {"u10", "v10"}.issubset(df.columns):
        df["ws10"] = np.sqrt(df["u10"] ** 2 + df["v10"] ** 2)

    if {"u100", "v100"}.issubset(df.columns):
        df["ws100"] = np.sqrt(df["u100"] ** 2 + df["v100"] ** 2)

    # Surface solar irradiance: SSRD is accumulated over the forecast step
    # (typically 1 h for ERA5), so divide by 3600 s to get mean W/m^2
    if "ssrd_Jm2" in df.columns:
        df["ghi_wm2"] = df["ssrd_Jm2"] / 3600.0
        df["ghi_wm2"] = df["ghi_wm2"].clip(lower=0)

    # Relative humidity (approximate via Magnus formula)
    if {"t2m_C", "d2m_C"}.issubset(df.columns):
        a, b = 17.625, 243.04  # Magnus coefficients
        df["rh_pct"] = 100.0 * np.exp(
            (a * df["d2m_C"]) / (b + df["d2m_C"])
            - (a * df["t2m_C"]) / (b + df["t2m_C"])
        )

    # Capacity factors
    if resource == "wind" and "ws100" in df.columns:
        df["cf_wind"] = wind_capacity_factor(df["ws100"].values)
    if resource == "solar" and {"ghi_wm2", "t2m_C"}.issubset(df.columns):
        df["cf_solar"] = solar_capacity_factor(df["ghi_wm2"].values, df["t2m_C"].values)

    # Add metadata columns
    df["location"] = label
    df["resource"] = resource

    # Sort by time
    if "time" in df.columns:
        df = df.sort_values("time").reset_index(drop=True)
    elif "valid_time" in df.columns:
        df = df.rename(columns={"valid_time": "time"})
        df = df.sort_values("time").reset_index(drop=True)

    log.info("    -> %d rows, columns: %s", len(df), list(df.columns))
    return df


def process_all(log: logging.Logger):
    """
    Read all downloaded NetCDF files, extract data for each location,
    compute derived variables and capacity factors, and write CSVs.
    """
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)

    # Collect file lists
    land_files = sorted(RAW_DIR.glob("era5land_*.nc"))
    sl_files = sorted(RAW_DIR.glob("era5sl_*.nc"))

    if not land_files:
        log.error("No ERA5-Land files found in %s – run --download first.", RAW_DIR)
        return
    if not sl_files:
        log.warning("No ERA5 single-level files found; 100 m wind & cloud cover will be missing.")

    log.info("Found %d ERA5-Land files and %d ERA5-SL files.", len(land_files), len(sl_files))

    all_frames: List[pd.DataFrame] = []

    for label, lat, lon, resource in LOCATIONS:
        try:
            df = process_location(land_files, sl_files, label, lat, lon, resource, log)
            # Save per-location CSV
            out_path = PROCESSED_DIR / f"{label}.csv"
            df.to_csv(out_path, index=False)
            log.info("    Saved %s", out_path)
            all_frames.append(df)
        except Exception as exc:
            log.error("  Failed to process %s: %s", label, exc, exc_info=True)

    # Save combined CSV
    if all_frames:
        combined = pd.concat(all_frames, ignore_index=True)
        combined_path = PROCESSED_DIR / "all_locations.csv"
        combined.to_csv(combined_path, index=False)
        log.info("Combined dataset: %d rows -> %s", len(combined), combined_path)

    log.info("Post-processing complete.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download and process ERA5/ERA5-Land data for Kazakhstan renewable zones.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--download", action="store_true",
        help="Download raw NetCDF data from CDS (requires cdsapi + API key).",
    )
    parser.add_argument(
        "--process", action="store_true",
        help="Post-process downloaded NetCDF files into CSV.",
    )
    parser.add_argument(
        "--year", type=int, default=None,
        help="Download a single year instead of the full range (2011-2024).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug-level logging.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    log = setup_logging(verbose=args.verbose)

    if not args.download and not args.process:
        log.error("Nothing to do. Pass --download and/or --process.")
        sys.exit(1)

    if args.download:
        if args.year:
            years = [args.year]
        else:
            years = list(range(YEAR_START, YEAR_END + 1))
        download_all(years, log)

    if args.process:
        process_all(log)


if __name__ == "__main__":
    main()
