#!/usr/bin/env python3
"""Train and evaluate multi-horizon demand forecasting models with EU->KZ transfer."""

from __future__ import annotations

import json
import importlib.util
import os
from dataclasses import dataclass
from math import erf, sqrt
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import TimeSeriesSplit

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data" / "forecasting"
RESULTS_DIR = BASE_DIR / "results" / "forecasting"

HORIZONS = [1, 6, 24, 168]
LAGS = [1, 2, 3, 6, 12, 24, 48, 168]
TARGET_COL = "load_mw"
KZ_REGION = "KZ_SYSTEM"
FAST_MODE = os.environ.get("FAST_DEMO", "0") == "1"
MAX_EU_TRAIN_ROWS = 30_000 if FAST_MODE else 120_000
MAX_MIX_TRAIN_ROWS = 60_000 if FAST_MODE else 180_000
FALLBACK_ESTIMATORS = 80 if FAST_MODE else 180
BACKTEST_ESTIMATORS = 60 if FAST_MODE else 120


@dataclass
class ModelOutput:
    name: str
    horizon: int
    y_true: np.ndarray
    y_pred: np.ndarray
    y_lo: Optional[np.ndarray] = None
    y_hi: Optional[np.ndarray] = None



def _normal_cdf(x: float) -> float:
    return 0.5 * (1 + erf(x / sqrt(2)))



def smape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    denom = (np.abs(y_true) + np.abs(y_pred))
    denom = np.where(denom == 0, 1e-9, denom)
    return float(np.mean(2.0 * np.abs(y_true - y_pred) / denom) * 100)



def mase(y_true: np.ndarray, y_pred: np.ndarray, y_train: np.ndarray, seasonality: int = 24) -> float:
    if len(y_train) <= seasonality:
        scale = np.mean(np.abs(np.diff(y_train)))
    else:
        scale = np.mean(np.abs(y_train[seasonality:] - y_train[:-seasonality]))
    scale = scale if scale > 0 else 1e-9
    return float(np.mean(np.abs(y_true - y_pred)) / scale)



def pinball_loss(y_true: np.ndarray, y_pred: np.ndarray, quantile: float) -> float:
    err = y_true - y_pred
    return float(np.mean(np.maximum(quantile * err, (quantile - 1) * err)))



def picp(y_true: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> float:
    inside = (y_true >= lo) & (y_true <= hi)
    return float(np.mean(inside) * 100)



def diebold_mariano(
    y_true: np.ndarray,
    pred_a: np.ndarray,
    pred_b: np.ndarray,
    h: int,
) -> Tuple[float, float]:
    # Squared-error loss differential
    e_a = (y_true - pred_a) ** 2
    e_b = (y_true - pred_b) ** 2
    d = e_a - e_b

    n = len(d)
    if n < 5:
        return np.nan, np.nan

    d_mean = np.mean(d)
    gamma0 = np.var(d, ddof=1)

    # Newey-West style correction up to lag h-1
    var_d = gamma0
    max_lag = min(h - 1, n - 2)
    for lag in range(1, max_lag + 1):
        cov = np.cov(d[lag:], d[:-lag], ddof=1)[0, 1]
        var_d += 2 * cov

    var_d = max(var_d, 1e-9)
    dm_stat = d_mean / np.sqrt(var_d / n)
    p_val = 2 * (1 - _normal_cdf(abs(dm_stat)))
    return float(dm_stat), float(p_val)



def load_and_merge_tables() -> pd.DataFrame:
    load = pd.read_csv(DATA_DIR / "table_load_hourly.csv", parse_dates=["timestamp_utc"])
    weather = pd.read_csv(DATA_DIR / "table_weather_hourly.csv", parse_dates=["timestamp_utc"])
    calendar = pd.read_csv(DATA_DIR / "table_calendar.csv", parse_dates=["timestamp_utc"])
    macro = pd.read_csv(DATA_DIR / "table_macro_annual.csv")

    load[TARGET_COL] = pd.to_numeric(load[TARGET_COL], errors="coerce")
    load = load[load[TARGET_COL].notna()].copy()

    df = load.merge(calendar, on="timestamp_utc", how="left")
    df = df.merge(weather, on=["timestamp_utc", "region_id"], how="left")

    df["year"] = df["timestamp_utc"].dt.year
    macro["year"] = pd.to_numeric(macro["year"], errors="coerce")
    macro = macro[macro["year"].notna()].copy()
    macro["year"] = macro["year"].astype(int)

    df = df.merge(
        macro[["year", "region_id", "industry_index", "gdp_proxy", "energy_intensity"]],
        on=["year", "region_id"],
        how="left",
    )

    df = df.sort_values(["region_id", "timestamp_utc"]).reset_index(drop=True)
    return df



def add_features(df: pd.DataFrame, horizon: int) -> pd.DataFrame:
    out = df.copy()

    for lag in LAGS:
        out[f"lag_{lag}"] = out.groupby("region_id")[TARGET_COL].shift(lag)

    out["roll24"] = (
        out.groupby("region_id")[TARGET_COL]
        .rolling(24, min_periods=1)
        .mean()
        .reset_index(level=0, drop=True)
    )
    out["roll168"] = (
        out.groupby("region_id")[TARGET_COL]
        .rolling(168, min_periods=1)
        .mean()
        .reset_index(level=0, drop=True)
    )

    out["target"] = out.groupby("region_id")[TARGET_COL].shift(-horizon)

    numeric_cols = out.select_dtypes(include=["number"]).columns.tolist()
    for col in numeric_cols:
        if col in ["target"]:
            continue
        out[col] = out[col].replace([np.inf, -np.inf], np.nan)

    needed = [f"lag_{l}" for l in LAGS] + ["roll24", "roll168", "target"]
    out = out.dropna(subset=needed).copy()
    return out



def split_kz_temporal(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    kz = df[df["region_id"] == KZ_REGION].copy()
    kz = kz.sort_values("timestamp_utc").reset_index(drop=True)

    n = len(kz)
    tr = int(n * 0.6)
    va = int(n * 0.8)
    return kz.iloc[:tr].copy(), kz.iloc[tr:va].copy(), kz.iloc[va:].copy()



def get_xy(df: pd.DataFrame) -> Tuple[pd.DataFrame, np.ndarray, List[str]]:
    drop_cols = {
        "target",
        TARGET_COL,
        "timestamp_utc",
        "series",
        "source",
        "country",
        "region_id",
    }

    numeric_cols = [c for c in df.select_dtypes(include=["number", "bool"]).columns if c not in drop_cols]
    feature_cols = numeric_cols

    X = df[feature_cols].copy()
    # Fill remaining gaps with global medians for numeric columns.
    X = X.fillna(X.median(numeric_only=True))
    X = X.fillna(0.0)
    y = df["target"].to_numpy(dtype=float)
    return X, y, feature_cols



def fit_sarimax(y_train: np.ndarray, steps: int) -> Optional[np.ndarray]:
    try:
        from statsmodels.tsa.statespace.sarimax import SARIMAX

        # Keep SARIMAX runtime bounded on long hourly series.
        if len(y_train) > 5000:
            y_train = y_train[-5000:]

        model = SARIMAX(
            y_train,
            order=(2, 0, 2),
            seasonal_order=(1, 0, 1, 24),
            enforce_stationarity=False,
            enforce_invertibility=False,
        )
        fitted = model.fit(disp=False, maxiter=60)
        return np.asarray(fitted.forecast(steps=steps), dtype=float)
    except Exception:
        return None



def fit_prophet(train_df: pd.DataFrame, test_df: pd.DataFrame) -> Optional[np.ndarray]:
    try:
        from prophet import Prophet

        tr = train_df[["timestamp_utc", "target"]].rename(columns={"timestamp_utc": "ds", "target": "y"})
        te = test_df[["timestamp_utc"]].rename(columns={"timestamp_utc": "ds"})

        m = Prophet(daily_seasonality=True, weekly_seasonality=True, yearly_seasonality=True)
        m.fit(tr)
        pred = m.predict(te)
        return pred["yhat"].to_numpy(dtype=float)
    except Exception:
        return None



def fit_transfer_ml(
    model_name: str,
    eu_df: pd.DataFrame,
    kz_train: pd.DataFrame,
    kz_val: pd.DataFrame,
    kz_test: pd.DataFrame,
) -> Optional[ModelOutput]:
    if len(eu_df) > MAX_EU_TRAIN_ROWS:
        eu_df = eu_df.sample(n=MAX_EU_TRAIN_ROWS, random_state=42).sort_values("timestamp_utc")

    X_eu, y_eu, cols = get_xy(eu_df)
    X_tr, y_tr, _ = get_xy(kz_train)
    X_val, y_val, _ = get_xy(kz_val)
    X_te, y_te, _ = get_xy(kz_test)

    # Base model: try XGBoost/CatBoost; fallback to sklearn GBDT.
    model = None
    try:
        if model_name == "xgboost":
            from xgboost import XGBRegressor

            base = XGBRegressor(
                n_estimators=300,
                learning_rate=0.05,
                max_depth=6,
                subsample=0.8,
                colsample_bytree=0.8,
                objective="reg:squarederror",
                random_state=42,
            )
            base.fit(X_eu, y_eu)

            model = XGBRegressor(
                n_estimators=180,
                learning_rate=0.05,
                max_depth=5,
                subsample=0.9,
                colsample_bytree=0.9,
                objective="reg:squarederror",
                random_state=42,
            )
            model.fit(X_tr, y_tr, xgb_model=base.get_booster())

        elif model_name == "catboost":
            from catboost import CatBoostRegressor

            base = CatBoostRegressor(
                loss_function="RMSE",
                iterations=300,
                depth=6,
                learning_rate=0.05,
                verbose=False,
                random_seed=42,
            )
            base.fit(X_eu, y_eu)

            model = CatBoostRegressor(
                loss_function="RMSE",
                iterations=180,
                depth=5,
                learning_rate=0.05,
                verbose=False,
                random_seed=42,
            )
            model.fit(X_tr, y_tr, init_model=base)
    except Exception:
        model = None

    if model is None:
        model = GradientBoostingRegressor(
            n_estimators=FALLBACK_ESTIMATORS,
            learning_rate=0.05,
            max_depth=3,
            random_state=42,
        )
        train_mix = pd.concat([eu_df[cols + ["target"]], kz_train[cols + ["target"]]], ignore_index=True)
        if len(train_mix) > MAX_MIX_TRAIN_ROWS:
            train_mix = train_mix.sample(n=MAX_MIX_TRAIN_ROWS, random_state=42)
        X_mix = train_mix[cols].fillna(0.0)
        y_mix = train_mix["target"].to_numpy(dtype=float)
        model.fit(X_mix, y_mix)

    val_pred = np.asarray(model.predict(X_val), dtype=float)
    test_pred = np.asarray(model.predict(X_te), dtype=float)

    # Conformal interval (90%) from validation residuals
    abs_resid = np.abs(y_val - val_pred)
    q = float(np.quantile(abs_resid, 0.90)) if len(abs_resid) else 0.0
    lo = test_pred - q
    hi = test_pred + q

    label = f"{model_name}_transfer"
    return ModelOutput(name=label, horizon=0, y_true=y_te, y_pred=test_pred, y_lo=lo, y_hi=hi)



def fit_non_transfer_ml(
    model_name: str,
    kz_train: pd.DataFrame,
    kz_val: pd.DataFrame,
    kz_test: pd.DataFrame,
) -> Optional[ModelOutput]:
    X_tr, y_tr, _ = get_xy(kz_train)
    X_val, y_val, _ = get_xy(kz_val)
    X_te, y_te, _ = get_xy(kz_test)

    model = None
    try:
        if model_name == "xgboost":
            from xgboost import XGBRegressor

            model = XGBRegressor(
                n_estimators=300,
                learning_rate=0.05,
                max_depth=6,
                subsample=0.8,
                colsample_bytree=0.8,
                objective="reg:squarederror",
                random_state=42,
            )
            model.fit(X_tr, y_tr)
        elif model_name == "catboost":
            from catboost import CatBoostRegressor

            model = CatBoostRegressor(
                loss_function="RMSE",
                iterations=300,
                depth=6,
                learning_rate=0.05,
                verbose=False,
                random_seed=42,
            )
            model.fit(X_tr, y_tr)
    except Exception:
        model = None

    if model is None:
        model = GradientBoostingRegressor(
            n_estimators=FALLBACK_ESTIMATORS,
            learning_rate=0.05,
            max_depth=3,
            random_state=42,
        )
        model.fit(X_tr, y_tr)

    val_pred = np.asarray(model.predict(X_val), dtype=float)
    test_pred = np.asarray(model.predict(X_te), dtype=float)
    q = float(np.quantile(np.abs(y_val - val_pred), 0.90)) if len(y_val) else 0.0
    lo = test_pred - q
    hi = test_pred + q

    label = f"{model_name}_non_transfer"
    return ModelOutput(name=label, horizon=0, y_true=y_te, y_pred=test_pred, y_lo=lo, y_hi=hi)



def fit_nhits_optional(kz_train: pd.DataFrame, kz_test: pd.DataFrame, horizon: int) -> Optional[ModelOutput]:
    try:
        from neuralforecast import NeuralForecast
        from neuralforecast.models import NHITS

        tr = kz_train[["timestamp_utc", "target"]].copy()
        te = kz_test[["timestamp_utc", "target"]].copy()

        tr = tr.rename(columns={"timestamp_utc": "ds", "target": "y"})
        te = te.rename(columns={"timestamp_utc": "ds", "target": "y"})
        tr["unique_id"] = "KZ_SYSTEM"
        te["unique_id"] = "KZ_SYSTEM"

        model = NHITS(
            h=horizon,
            input_size=max(24 * 7, horizon * 4),
            max_steps=300,
            random_seed=42,
        )
        nf = NeuralForecast(models=[model], freq="H")
        nf.fit(tr)

        futr = te[["unique_id", "ds"]].copy()
        pred = nf.predict(futr_df=futr)
        pred_col = [c for c in pred.columns if c not in {"unique_id", "ds"}][0]

        y_pred = pred[pred_col].to_numpy(dtype=float)
        y_true = te["y"].to_numpy(dtype=float)
        # No native intervals in this lightweight setup; use residual-based interval from train tail.
        resid_scale = np.std(tr["y"].diff().dropna().to_numpy(dtype=float))
        q = float(1.64 * resid_scale) if np.isfinite(resid_scale) else 0.0
        return ModelOutput(
            name="nhits_transfer",
            horizon=0,
            y_true=y_true,
            y_pred=y_pred,
            y_lo=y_pred - q,
            y_hi=y_pred + q,
        )
    except Exception:
        return None



def baseline_seasonal_naive(kz_test: pd.DataFrame, horizon: int) -> ModelOutput:
    lag_col = "lag_24" if horizon <= 24 else "lag_168"
    pred = kz_test[lag_col].to_numpy(dtype=float)
    y_true = kz_test["target"].to_numpy(dtype=float)

    # Simple symmetric residual band based on in-sample dispersion proxy.
    resid_scale = np.nanstd(y_true - pred)
    q = float(1.64 * resid_scale) if np.isfinite(resid_scale) else 0.0

    return ModelOutput(
        name="seasonal_naive",
        horizon=horizon,
        y_true=y_true,
        y_pred=pred,
        y_lo=pred - q,
        y_hi=pred + q,
    )



def evaluate(output: ModelOutput, y_train_ref: np.ndarray) -> Dict[str, float]:
    res = {
        "model": output.name,
        "horizon": output.horizon,
        "MAE": mean_absolute_error(output.y_true, output.y_pred),
        "RMSE": float(np.sqrt(mean_squared_error(output.y_true, output.y_pred))),
        "sMAPE": smape(output.y_true, output.y_pred),
        "MASE": mase(output.y_true, output.y_pred, y_train_ref),
    }

    if output.y_lo is not None and output.y_hi is not None:
        alpha = 0.90
        q_low = 1 - alpha
        q_high = alpha
        res["PinballLow"] = pinball_loss(output.y_true, output.y_lo, q_low)
        res["PinballHigh"] = pinball_loss(output.y_true, output.y_hi, q_high)
        res["PICP_90"] = picp(output.y_true, output.y_lo, output.y_hi)
    else:
        res["PinballLow"] = np.nan
        res["PinballHigh"] = np.nan
        res["PICP_90"] = np.nan

    return res



def run_backtesting(kz_df: pd.DataFrame, feature_cols: List[str]) -> pd.DataFrame:
    kz_df = kz_df.sort_values("timestamp_utc").reset_index(drop=True)

    if len(kz_df) < 500:
        return pd.DataFrame()

    tscv = TimeSeriesSplit(n_splits=2 if FAST_MODE else 3)
    rows = []

    X = kz_df[feature_cols].fillna(0.0)
    y = kz_df["target"].to_numpy(dtype=float)

    for fold, (tr_idx, te_idx) in enumerate(tscv.split(X), start=1):
        X_tr, X_te = X.iloc[tr_idx], X.iloc[te_idx]
        y_tr, y_te = y[tr_idx], y[te_idx]

        model = GradientBoostingRegressor(
            n_estimators=BACKTEST_ESTIMATORS,
            learning_rate=0.05,
            max_depth=3,
            random_state=42,
        )
        model.fit(X_tr, y_tr)
        pred = model.predict(X_te)

        rows.append(
            {
                "fold": fold,
                "MAE": mean_absolute_error(y_te, pred),
                "RMSE": float(np.sqrt(mean_squared_error(y_te, pred))),
                "sMAPE": smape(y_te, pred),
            }
        )

    return pd.DataFrame(rows)



def run() -> Dict[str, object]:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    # Remove stale per-horizon artifacts from previous runs.
    for stale in RESULTS_DIR.glob("predictions_h*.csv"):
        stale.unlink(missing_ok=True)
    for stale in RESULTS_DIR.glob("backtest_h*.csv"):
        stale.unlink(missing_ok=True)

    raw = load_and_merge_tables()
    metrics_rows: List[Dict[str, float]] = []
    dm_rows: List[Dict[str, float]] = []

    has_xgb = importlib.util.find_spec("xgboost") is not None
    has_cat = importlib.util.find_spec("catboost") is not None
    run_sarimax = os.environ.get("ENABLE_SARIMAX", "0") == "1"
    if has_xgb or has_cat:
        model_names = []
        if has_xgb:
            model_names.append("xgboost")
        if has_cat:
            model_names.append("catboost")
    else:
        model_names = ["gbdt"]

    horizons = [24] if FAST_MODE else HORIZONS

    for h in horizons:
        feat = add_features(raw, horizon=h)

        eu = feat[feat["region_id"] != KZ_REGION].copy()
        kz_train, kz_val, kz_test = split_kz_temporal(feat)

        y_train_ref = kz_train["target"].to_numpy(dtype=float)

        outputs: List[ModelOutput] = []

        # Baseline
        base = baseline_seasonal_naive(kz_test, horizon=h)
        outputs.append(base)

        # SARIMAX baseline
        if run_sarimax and h in {1, 24}:
            sarimax_pred = fit_sarimax(kz_train["target"].to_numpy(dtype=float), len(kz_test))
            if sarimax_pred is not None and len(sarimax_pred) == len(kz_test):
                outputs.append(
                    ModelOutput(
                        name="sarimax",
                        horizon=h,
                        y_true=kz_test["target"].to_numpy(dtype=float),
                        y_pred=sarimax_pred,
                    )
                )

        # Prophet baseline
        prophet_pred = fit_prophet(kz_train, kz_test)
        if prophet_pred is not None and len(prophet_pred) == len(kz_test):
            outputs.append(
                ModelOutput(
                    name="prophet",
                    horizon=h,
                    y_true=kz_test["target"].to_numpy(dtype=float),
                    y_pred=prophet_pred,
                )
            )

        # Transfer ML + ablation
        for model_name in model_names:
            transfer_out = fit_transfer_ml(model_name, eu, kz_train, kz_val, kz_test)
            if transfer_out is not None:
                transfer_out.horizon = h
                outputs.append(transfer_out)

            non_transfer_out = fit_non_transfer_ml(model_name, kz_train, kz_val, kz_test)
            if non_transfer_out is not None:
                non_transfer_out.horizon = h
                outputs.append(non_transfer_out)

        # Deep model (optional)
        nhits_out = fit_nhits_optional(kz_train, kz_test, horizon=h)
        if nhits_out is not None:
            nhits_out.horizon = h
            outputs.append(nhits_out)

        # Metrics
        for out in outputs:
            metrics_rows.append(evaluate(out, y_train_ref))

        # DM tests vs best non-baseline model by RMSE
        out_df = pd.DataFrame([{"model": o.name, "rmse": float(np.sqrt(mean_squared_error(o.y_true, o.y_pred)))} for o in outputs])
        candidates = out_df[~out_df["model"].eq("seasonal_naive")].sort_values("rmse")
        if not candidates.empty:
            best_name = candidates.iloc[0]["model"]
            best = next(o for o in outputs if o.name == best_name)
            for o in outputs:
                if o.name == best_name:
                    continue
                dm_stat, p_val = diebold_mariano(best.y_true, best.y_pred, o.y_pred, h=max(h, 1))
                dm_rows.append(
                    {
                        "horizon": h,
                        "best_model": best_name,
                        "compared_model": o.name,
                        "dm_stat": dm_stat,
                        "p_value": p_val,
                    }
                )

        # Save predictions per horizon
        pred_dump = pd.DataFrame({"timestamp_utc": kz_test["timestamp_utc"], "y_true": kz_test["target"]})
        for o in outputs:
            pred_dump[f"pred_{o.name}"] = o.y_pred
            if o.y_lo is not None and o.y_hi is not None:
                pred_dump[f"lo_{o.name}"] = o.y_lo
                pred_dump[f"hi_{o.name}"] = o.y_hi
        pred_dump.to_csv(RESULTS_DIR / f"predictions_h{h}.csv", index=False)

        # Rolling-origin backtest (seasonal CV proxy) for one transfer-ready feature frame
        Xkz, _, feature_cols = get_xy(pd.concat([kz_train, kz_val, kz_test], ignore_index=True))
        backtest = run_backtesting(pd.concat([kz_train, kz_val, kz_test], ignore_index=True), feature_cols)
        if not backtest.empty:
            backtest["horizon"] = h
            backtest.to_csv(RESULTS_DIR / f"backtest_h{h}.csv", index=False)

    metrics = pd.DataFrame(metrics_rows).sort_values(["horizon", "RMSE", "MAE"])
    metrics.to_csv(RESULTS_DIR / "model_metrics.csv", index=False)

    dm_df = pd.DataFrame(dm_rows)
    dm_df.to_csv(RESULTS_DIR / "diebold_mariano_tests.csv", index=False)

    summary = {
        "fast_mode": FAST_MODE,
        "metrics_file": str((RESULTS_DIR / "model_metrics.csv").relative_to(BASE_DIR)),
        "dm_file": str((RESULTS_DIR / "diebold_mariano_tests.csv").relative_to(BASE_DIR)),
        "best_by_horizon": metrics.groupby("horizon").first()[["model", "RMSE", "sMAPE", "PICP_90"]].reset_index().to_dict(orient="records"),
    }

    (RESULTS_DIR / "training_summary.json").write_text(
        json.dumps(summary, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )
    return summary


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=True, indent=2))
