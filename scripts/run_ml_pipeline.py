"""
ML Pipeline Runner — Energy Balance Forecasting on Clean Dataset
=================================================================
Wraps the existing transfer_forecasting/ scripts to run on the new
clean/ dataset built by build_dataset.py. Produces forecasting metrics
and saves them to data/clean/ml_results/ for use in the dissertation
and Power BI dashboard.

Models:
  - Seasonal naive (baseline)
  - ARIMA(2, 0, 2)
  - Holt-Winters
  - GBDT (lightgbm)
  - SARIMA-LSTM hybrid (calls existing transfer_forecasting/train_models.py
    when the heavier dependencies are available)

Outputs:
  data/clean/ml_results/metrics.csv          — MAE / RMSE / sMAPE / MASE per model
  data/clean/ml_results/predictions.csv      — point forecasts + 90% intervals
  data/clean/ml_results/diebold_mariano.csv  — pairwise statistical tests
  data/clean/ml_results/summary.json         — pipeline summary

Usage
-----
    python3 scripts/run_ml_pipeline.py
    python3 scripts/run_ml_pipeline.py --target electricity_consumption --horizon 24
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
CLEAN_DIR = BASE_DIR / "data" / "clean"
RESULTS_DIR = CLEAN_DIR / "ml_results"
DB_PATH = CLEAN_DIR / "kz_energy_balance.db"

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


# ───────────────────────── METRICS ──────────────────────────
def _mae(y, p):  return float(np.mean(np.abs(y - p)))
def _rmse(y, p): return float(np.sqrt(np.mean((y - p) ** 2)))


def _smape(y, p):
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    denom = (np.abs(y) + np.abs(p)) / 2.0
    mask = denom > 0
    if not mask.any():
        return float("nan")
    return float(np.mean(np.abs(y[mask] - p[mask]) / denom[mask]) * 100)


def _mase(y, p, train_y, season=1):
    """MASE relative to in-sample seasonal-naive on the training set."""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    ty = np.asarray(train_y, dtype=float)
    if len(ty) <= season:
        return float("nan")
    naive_err = np.abs(ty[season:] - ty[:-season])
    scale = naive_err.mean()
    if not np.isfinite(scale) or scale <= 0:
        return float("nan")
    return float(np.mean(np.abs(y - p)) / scale)


def _diebold_mariano(e1, e2, h=1) -> tuple[float, float]:
    """Two-sided DM test on squared-error loss with HLN small-sample correction.

    Returns (NaN, NaN) when the test is not defined (too few obs, zero
    variance, identical forecasts) — callers must skip / flag rather than
    treat 0/1 as a valid 'no difference' result.
    """
    from scipy import stats
    e1 = np.asarray(e1, dtype=float)
    e2 = np.asarray(e2, dtype=float)
    d = e1 ** 2 - e2 ** 2
    n = len(d)
    if n < 8:
        return float("nan"), float("nan")
    var = np.var(d, ddof=1)
    if not np.isfinite(var) or var <= 0:
        return float("nan"), float("nan")
    dm = d.mean() / np.sqrt(var / n)
    # Harvey-Leybourne-Newbold small-sample correction
    hln = np.sqrt((n + 1 - 2 * h + h * (h - 1) / n) / n)
    dm *= hln
    p = 2 * stats.t.sf(abs(dm), df=n - 1)
    return float(dm), float(p)


# ───────────────────────── DATA LOAD ────────────────────────
def load_target_series(target: str = "electricity_consumption") -> pd.Series:
    """Pull the target series from clean/master_long.csv.

    For the dissertation defense, the natural target is annual Total Primary
    Energy Consumption (electricity sub-sector) in TJ. Where finer granularity
    is needed, hook the KOREM hourly stream produced by `korem_xlsx_parser.py`.
    """
    path = CLEAN_DIR / "master_long.csv"
    if not path.exists():
        log.warning("clean/master_long.csv not found — run build_dataset.py first")
        return pd.Series(dtype=float)
    df = pd.read_csv(path)
    # Annual electricity supply
    if target == "electricity_consumption":
        s = df.query(
            "unit == 'TJ' and fuel_group == 'Electricity' "
            "and line_item == 'Total primary energy consumption'"
        ).groupby("year")["value"].sum()
    elif target == "total_primary_consumption":
        s = df.query("unit == 'TJ' and line_item == 'Total primary energy consumption'") \
              .groupby("year")["value"].sum()
    elif target == "renewable_production":
        s = df.query(
            "unit == 'TJ' and fuel_group == 'Renewable energy sources' "
            "and line_item == 'Primary energy production'"
        ).groupby("year")["value"].sum()
    elif target == "korem_monthly_supplier_payment":
        # Long enough series for meaningful backtesting (≥12 obs).
        # Reads from the SQLite KOREM monthly aggregate.
        if not DB_PATH.exists():
            log.warning("DB not found — run korem_xlsx_parser.py first")
            return pd.Series(dtype=float)
        with sqlite3.connect(str(DB_PATH)) as c:
            mdf = pd.read_sql(
                "SELECT year, month, zone, total_payment_to_supplier "
                "FROM korem_monthly", c)
        mdf = mdf.dropna(subset=["year", "month"])
        s = (mdf.groupby(["year", "month"])["total_payment_to_supplier"]
                 .sum().sort_index())
        s.index = pd.to_datetime(
            [f"{int(y)}-{int(m):02d}-01" for y, m in s.index])
        return s.sort_index()
    else:
        raise ValueError(f"unknown target: {target}")
    s.index = pd.to_datetime(s.index, format="%Y")
    return s.sort_index()


# ───────────────────────── MODELS ───────────────────────────
def fit_seasonal_naive(train: pd.Series, horizon: int) -> np.ndarray:
    if len(train) < 1:
        return np.zeros(horizon)
    return np.repeat(train.iloc[-1], horizon)


def fit_arima(train: pd.Series, horizon: int) -> Optional[np.ndarray]:
    try:
        from statsmodels.tsa.arima.model import ARIMA
        order = (2, 0, 2) if len(train) >= 6 else (1, 0, 0)
        m = ARIMA(train, order=order).fit()
        return m.forecast(steps=horizon).values
    except Exception as e:
        log.warning(f"  ARIMA failed: {e}")
        return None


def fit_holt_winters(train: pd.Series, horizon: int) -> Optional[np.ndarray]:
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
        m = ExponentialSmoothing(train, trend="add", seasonal=None,
                                 initialization_method="estimated").fit()
        return m.forecast(steps=horizon).values
    except Exception as e:
        log.warning(f"  Holt-Winters failed: {e}")
        return None


def fit_gbdt(train: pd.Series, horizon: int) -> Optional[np.ndarray]:
    try:
        import lightgbm as lgb
    except (ImportError, OSError) as e:
        log.warning(f"  lightgbm unavailable ({type(e).__name__}); skipping GBDT model")
        return None
    if len(train) < 3:
        # need ≥1 (lag-1,lag-2)->target row, i.e. ≥3 observations total
        return None
    # build (lag-1, lag-2) regression
    y = train.values
    X = np.column_stack([y[:-2], y[1:-1]])
    yt = y[2:]
    if len(yt) < 1:
        return None
    if len(yt) < 2:
        # sklearn / LightGBM rejects single-sample fit; skip
        return None
    # Adaptive regularization: keep LightGBM defaults (min_data_in_leaf=20,
    # min_data_in_bin=3) on long series, scale down only when there literally
    # are not enough points to satisfy them. Avoids over-fitting on KOREM.
    n = len(yt)
    mdl_leaf = max(1, min(20, n // 4))
    mdl_bin  = max(1, min(3, n // 10))
    try:
        model = lgb.LGBMRegressor(
            n_estimators=200, max_depth=4, verbose=-1,
            min_data_in_leaf=mdl_leaf, min_data_in_bin=mdl_bin)
        model.fit(X, yt)
    except (ValueError, RuntimeError) as e:
        log.warning(f"  gbdt_lgbm fit failed on len={len(train)}: {e}")
        return None
    last = list(y[-2:])
    out = []
    for _ in range(horizon):
        x = np.array([[last[-2], last[-1]]])
        nxt = float(model.predict(x)[0])
        out.append(nxt)
        last.append(nxt)
    return np.array(out)


def fit_linear_trend(train: pd.Series, horizon: int) -> Optional[np.ndarray]:
    """OLS line on the index of the training window — needs only 2 points.
    Acts as a 4th baseline so that short annual series (4 obs) still produce
    a 4-model comparison instead of 3."""
    if len(train) < 2:
        return None
    y = train.values.astype(float)
    x = np.arange(len(y), dtype=float)
    # closed-form OLS slope/intercept
    xm, ym = x.mean(), y.mean()
    denom = ((x - xm) ** 2).sum()
    if denom == 0:
        return np.repeat(ym, horizon)
    slope = ((x - xm) * (y - ym)).sum() / denom
    intercept = ym - slope * xm
    xs = np.arange(len(y), len(y) + horizon, dtype=float)
    return intercept + slope * xs


# ───────────────────────── EVAL ─────────────────────────────
def evaluate_models(series: pd.Series, horizon: int = 1,
                    min_train: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Walk-forward expanding-window evaluation.

    Folds = len(series) - min_train - horizon + 1. Default min_train keeps at
    least 60% of the series for the first training window so that backtest
    covers the most recent 40% (capped at 12 folds to keep runtime sane).
    """
    if len(series) < 4:
        log.warning("need at least 4 observations for backtest")
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    if min_train is None:
        # default: leave the most recent 40% (≥2) for backtest, cap at 12 folds
        backtest_len = max(2, min(int(len(series) * 0.4), 12))
        min_train = max(3, len(series) - backtest_len - horizon + 1)
    n_train = max(2, min_train)
    log.info(f"  walk-forward: n_train={n_train}, "
             f"folds={max(0, len(series) - n_train - horizon + 1)}")
    metrics: list[dict] = []
    pred_rows: list[dict] = []
    actuals_per_model: dict[str, list[float]] = {}
    preds_per_model: dict[str, list[float]] = {}
    train_tail_per_model: dict[str, np.ndarray] = {}
    errors_per_model: dict[str, list[float]] = {}

    for cutoff in range(n_train, len(series) - horizon + 1):
        train = series.iloc[:cutoff]
        actual = series.iloc[cutoff:cutoff + horizon]

        candidates = {
            "seasonal_naive": fit_seasonal_naive(train, horizon),
            "arima_2_0_2":   fit_arima(train, horizon),
            "holt_winters":  fit_holt_winters(train, horizon),
            "gbdt_lgbm":     fit_gbdt(train, horizon),
            "linear_trend":  fit_linear_trend(train, horizon),
        }
        for name, pred in candidates.items():
            if pred is None:
                continue
            err = actual.values - pred
            errors_per_model.setdefault(name, []).extend(err.tolist())
            actuals_per_model.setdefault(name, []).extend(actual.values.tolist())
            preds_per_model.setdefault(name, []).extend(pred.tolist())
            train_tail_per_model[name] = train.values  # last seen training set
            sigma = float(np.std(err)) if len(err) > 1 else float(np.abs(err[0]))
            for h, (a, p) in enumerate(zip(actual.values, pred), start=1):
                pred_rows.append({"cutoff": str(train.index[-1].date()),
                                  "horizon": h, "model": name,
                                  "actual": float(a), "pred": float(p),
                                  "lo90": float(p - 1.645 * sigma),
                                  "hi90": float(p + 1.645 * sigma)})

    # Aggregate metrics across folds — sMAPE/MASE on actual vs pred, not errors.
    for name in errors_per_model:
        a = np.array(actuals_per_model[name])
        p = np.array(preds_per_model[name])
        e = a - p
        metrics.append({
            "model": name,
            "MAE": float(np.mean(np.abs(e))),
            "RMSE": float(np.sqrt(np.mean(e ** 2))),
            "sMAPE_pct": _smape(a, p),
            "MASE": _mase(a, p, train_tail_per_model[name], season=1),
            "n_folds": len(e),
        })

    metrics_df = pd.DataFrame(metrics).sort_values("MAE")
    pred_df = pd.DataFrame(pred_rows)

    # Diebold-Mariano against seasonal_naive (NaN when not defined)
    dm_rows = []
    if "seasonal_naive" in errors_per_model:
        baseline = np.array(errors_per_model["seasonal_naive"])
        for name, errs in errors_per_model.items():
            if name == "seasonal_naive":
                continue
            e2 = np.array(errs)
            n = min(len(baseline), len(e2))
            dm, pv = _diebold_mariano(baseline[:n], e2[:n], h=horizon)
            dm_rows.append({"compared_to": "seasonal_naive", "model": name,
                            "DM_stat": dm, "p_value": pv, "n_obs": n})
    dm_df = pd.DataFrame(dm_rows)

    return metrics_df, pred_df, dm_df


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--target", default="total_primary_consumption",
                   choices=["electricity_consumption", "total_primary_consumption",
                            "renewable_production", "korem_monthly_supplier_payment"])
    p.add_argument("--horizon", type=int, default=1)
    p.add_argument("--min-train", type=int, default=None,
                   help="initial training window for walk-forward (default: leave 40% for backtest)")
    args = p.parse_args(argv)

    log.info("=" * 60)
    log.info(f"ML pipeline: target={args.target}  horizon={args.horizon}")
    log.info("=" * 60)

    series = load_target_series(args.target)
    if series.empty:
        log.error("no target series available")
        return 1

    log.info(f"target series ({len(series)} obs):")
    for d, v in series.items():
        log.info(f"  {d.year}: {v:,.1f}")

    metrics, preds, dm = evaluate_models(series, args.horizon, args.min_train)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    # Per-target outputs so multi-target sweeps don't clobber each other
    metrics_tagged = metrics.copy()
    metrics_tagged.insert(0, "target", args.target)
    preds_tagged = preds.copy()
    preds_tagged.insert(0, "target", args.target) if not preds.empty else None
    metrics_tagged.to_csv(RESULTS_DIR / f"metrics_{args.target}.csv", index=False)
    preds_tagged.to_csv(RESULTS_DIR / f"predictions_{args.target}.csv", index=False)
    dm.to_csv(RESULTS_DIR / f"diebold_mariano_{args.target}.csv", index=False)
    # Combined files — append-or-replace per target row
    combined_path = RESULTS_DIR / "metrics.csv"
    if combined_path.exists():
        try:
            old = pd.read_csv(combined_path)
            if "target" in old.columns:
                old = old[old["target"] != args.target]
                metrics_tagged = pd.concat([old, metrics_tagged], ignore_index=True)
        except Exception:
            pass
    metrics_tagged.to_csv(combined_path, index=False)
    preds.to_csv(RESULTS_DIR / "predictions.csv", index=False)
    dm.to_csv(RESULTS_DIR / "diebold_mariano.csv", index=False)
    summary = {
        "target": args.target,
        "horizon": args.horizon,
        "n_obs": len(series),
        "best_model": metrics.iloc[0]["model"] if not metrics.empty else None,
        "best_MAE": float(metrics.iloc[0]["MAE"]) if not metrics.empty else None,
        "models_evaluated": metrics["model"].tolist() if not metrics.empty else [],
    }
    (RESULTS_DIR / "summary.json").write_text(json.dumps(summary, indent=2))

    log.info("Metrics:")
    log.info("\n" + metrics.to_string(index=False))
    log.info(f"All outputs → {RESULTS_DIR}")

    # Also persist into the SQLite DB
    if DB_PATH.exists() and not metrics.empty:
        conn = sqlite3.connect(str(DB_PATH))
        metrics.to_sql("ml_metrics", conn, if_exists="replace", index=False)
        preds.to_sql("ml_predictions", conn, if_exists="replace", index=False)
        if not dm.empty:
            dm.to_sql("ml_diebold_mariano", conn, if_exists="replace", index=False)
        conn.close()
        log.info("ml_metrics, ml_predictions, ml_diebold_mariano tables added to SQLite")

    return 0


if __name__ == "__main__":
    sys.exit(main())
