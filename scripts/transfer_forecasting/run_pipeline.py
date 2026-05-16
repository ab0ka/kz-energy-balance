#!/usr/bin/env python3
"""Orchestrator: clean -> build tables -> train -> summarize."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from clean_data import run as run_clean
from build_tables import run as run_build
from train_models import run as run_train

BASE_DIR = Path(__file__).resolve().parents[2]
RESULTS_DIR = BASE_DIR / "results" / "forecasting"



def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run transfer forecasting pipeline")
    p.add_argument("--skip-clean", action="store_true", help="Skip data cleaning phase")
    p.add_argument("--skip-build", action="store_true", help="Skip table-building phase")
    p.add_argument("--skip-train", action="store_true", help="Skip training phase")
    p.add_argument(
        "--fetch-weather",
        action="store_true",
        help="Fetch open-meteo weather for KZ weather table during build phase",
    )
    return p.parse_args()



def main() -> None:
    args = parse_args()

    summary = {}

    if not args.skip_clean:
        report = run_clean()
        summary["clean_report"] = str(report.relative_to(BASE_DIR))

    if not args.skip_build:
        summary["build"] = run_build(fetch_weather=args.fetch_weather)

    if not args.skip_train:
        summary["train"] = run_train()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / "pipeline_summary.json"
    out.write_text(json.dumps(summary, ensure_ascii=True, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=True, indent=2))
    print(f"\nSaved summary: {out}")


if __name__ == "__main__":
    main()
