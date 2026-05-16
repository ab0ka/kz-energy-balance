"""
Mirror test: only-KZ vs only-EU vs transfer on REAL KOREM hourly data.
=====================================================================

Answers the user's question empirically:
"If you train on KZ vs EU separately, when does adding EU help?"

Pipeline:
  1. Load real KOREM hourly payments (21k+ rows, 2 zones, 2024-11..2026-02).
  2. Load EU hourly load (ENTSO-E via OPSD, 10 countries, 2020).
  3. z-score normalize per region — kills absolute-scale gap (KZT vs MW).
  4. Build time/lag features on normalized series.
  5. Time-aware split 70/15/15 on KZ ONLY.
  6. Fit 4 model families × 3 horizons (1h, 24h, 168h):
       A. seasonal_naive      — proper baseline: y[t+h] = y[t+h-24*ceil(h/24)]
       B. only_KZ catboost    — trained on KZ train only
       C. only_EU catboost    — trained on EU only (zero-shot on KZ test)
       D. transfer EU→KZ      — pre-train EU, fine-tune KZ via init_model
  7. Metrics on KZ test (denormalized): MAE, RMSE, sMAPE, MASE, PICP_90.
  8. Diebold-Mariano (Newey-West HAC, HLN small-sample correction):
       transfer vs only_KZ — does EU pre-training help?
       only_EU  vs only_KZ — is EU enough on its own?
       <all>    vs naive   — beats the baseline?

Output: data/clean/ml_results/mirror_test/{metrics,dm,predictions}.csv
"""
from __future__ import annotations

import json
import sqlite3
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as scistats

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "clean" / "kz_energy_balance.db"
EU_CSV = BASE / "data" / "forecasting" / "table_load_hourly.csv"
OUT_DIR = BASE / "data" / "clean" / "ml_results" / "mirror_test"
OUT_DIR.mkdir(parents=True, exist_ok=True)

HORIZONS = [1, 24, 168]
LAGS = [1, 2, 3, 6, 12, 24, 48, 168]
FEATURES = (["hour", "dow", "month", "is_weekend"]
            + [f"lag_{l}" for l in LAGS]
            + ["roll24", "roll168"])


# ───────────────────────── DATA LOADING ─────────────────────────
def load_korem() -> pd.DataFrame:
    con = sqlite3.connect(str(DB))
    df = pd.read_sql(
        "SELECT date, hour, zone, payment_to_supplier_kzt AS y "
        "FROM korem_hourly", con)
    con.close()
    df["timestamp"] = (pd.to_datetime(df["date"])
                       + pd.to_timedelta(df["hour"], unit="h"))
    df["region_id"] = "KZ_" + df.zone.str.upper().str.replace("-", "_")
    df = df.dropna(subset=["y"])
    return (df[["timestamp", "region_id", "y"]]
            .sort_values(["region_id", "timestamp"])
            .reset_index(drop=True))


def load_eu() -> pd.DataFrame:
    df = pd.read_csv(EU_CSV, parse_dates=["timestamp_utc"])
    df = df[df.region_id != "KZ_SYSTEM"].copy()
    df = df.rename(columns={"timestamp_utc": "timestamp", "load_mw": "y"})
    df = df.dropna(subset=["y"])
    return (df[["timestamp", "region_id", "y"]]
            .sort_values(["region_id", "timestamp"])
            .reset_index(drop=True))


def zscore_per_region(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """z-score each region independently → kills absolute-scale gap."""
    stats_df = (df.groupby("region_id")["y"]
                  .agg(mu="mean", sigma="std").reset_index())
    out = df.merge(stats_df, on="region_id")
    out["y_norm"] = (out.y - out.mu) / out.sigma.replace(0, np.nan)
    out = out.dropna(subset=["y_norm"])
    return out, stats_df


def add_features(df: pd.DataFrame, horizon: int) -> pd.DataFrame:
    out = df.sort_values(["region_id", "timestamp"]).copy()
    out["hour"] = out.timestamp.dt.hour
    out["dow"] = out.timestamp.dt.dayofweek
    out["month"] = out.timestamp.dt.month
    out["is_weekend"] = (out.dow >= 5).astype(int)
    for lag in LAGS:
        out[f"lag_{lag}"] = out.groupby("region_id")["y_norm"].shift(lag)
    out["roll24"] = (out.groupby("region_id")["y_norm"]
                        .rolling(24, min_periods=1).mean()
                        .reset_index(level=0, drop=True))
    out["roll168"] = (out.groupby("region_id")["y_norm"]
                         .rolling(168, min_periods=1).mean()
                         .reset_index(level=0, drop=True))
    out["target_norm"] = out.groupby("region_id")["y_norm"].shift(-horizon)
    # Proper seasonal naive: y[t+h] = y[t+h - 24*ceil(h/24)]
    k = int(np.ceil(horizon / 24))
    shift_naive = 24 * k - horizon  # ≥ 0
    if shift_naive == 0:
        out["seasonal_naive_norm"] = out["y_norm"]
    else:
        out["seasonal_naive_norm"] = (
            out.groupby("region_id")["y_norm"].shift(shift_naive))
    needed = ([f"lag_{l}" for l in LAGS]
              + ["roll24", "roll168", "target_norm", "seasonal_naive_norm"])
    return out.dropna(subset=needed).reset_index(drop=True)


def split_kz(df: pd.DataFrame, tr=0.70, va=0.15) -> tuple:
    pieces = [[], [], []]
    for _, g in df.groupby("region_id"):
        g = g.sort_values("timestamp").reset_index(drop=True)
        n = len(g)
        i_tr = int(n * tr)
        i_va = int(n * (tr + va))
        pieces[0].append(g.iloc[:i_tr])
        pieces[1].append(g.iloc[i_tr:i_va])
        pieces[2].append(g.iloc[i_va:])
    return (pd.concat(pieces[0]).reset_index(drop=True),
            pd.concat(pieces[1]).reset_index(drop=True),
            pd.concat(pieces[2]).reset_index(drop=True))


# ───────────────────────── MODELS ─────────────────────────
def fit_catboost(X_train, y_train, init_model=None,
                 iterations=400, depth=6, lr=0.05):
    from catboost import CatBoostRegressor
    m = CatBoostRegressor(loss_function="RMSE",
                          iterations=iterations, depth=depth,
                          learning_rate=lr, verbose=False, random_seed=42)
    fit_kwargs = {}
    if init_model is not None:
        fit_kwargs["init_model"] = init_model
    m.fit(X_train, y_train, **fit_kwargs)
    return m


# ───────────────────────── METRICS ─────────────────────────
def _metrics(y_true, y_pred, y_train_ref, y_lo=None, y_hi=None):
    e = y_true - y_pred
    mae = float(np.mean(np.abs(e)))
    rmse = float(np.sqrt(np.mean(e ** 2)))
    denom = np.abs(y_true) + np.abs(y_pred)
    mask = denom > 0
    smape = float(np.mean(2 * np.abs(e[mask]) / denom[mask]) * 100) if mask.any() else float("nan")
    if len(y_train_ref) > 24:
        scale = np.mean(np.abs(y_train_ref[24:] - y_train_ref[:-24]))
    else:
        scale = np.mean(np.abs(np.diff(y_train_ref)))
    scale = max(float(scale), 1e-9)
    mase = mae / scale
    res = {"MAE": mae, "RMSE": rmse, "sMAPE_pct": smape, "MASE": float(mase)}
    if y_lo is not None and y_hi is not None:
        picp = float(np.mean((y_true >= y_lo) & (y_true <= y_hi)) * 100)
        res["PICP_90"] = picp
    return res


def diebold_mariano(e1, e2, h: int):
    """Two-sided DM with Newey-West HAC + HLN small-sample correction.
    e1, e2 are forecast errors (y_true - y_pred); compare squared-error loss."""
    e1 = np.asarray(e1, dtype=float)
    e2 = np.asarray(e2, dtype=float)
    d = e1 ** 2 - e2 ** 2
    n = len(d)
    if n < 8:
        return float("nan"), float("nan")
    d_mean = float(np.mean(d))
    # HAC variance up to lag h-1
    gamma0 = float(np.var(d, ddof=1))
    var_d = gamma0
    for lag in range(1, min(h, n - 1)):
        cov = float(np.cov(d[lag:], d[:-lag], ddof=1)[0, 1])
        var_d += 2 * cov
    var_d = max(var_d, 1e-12)
    dm = d_mean / np.sqrt(var_d / n)
    hln = np.sqrt((n + 1 - 2 * h + h * (h - 1) / n) / n)
    dm *= hln
    p = 2 * scistats.t.sf(abs(dm), df=n - 1)
    return float(dm), float(p)


# ───────────────────────── PIPELINE ─────────────────────────
def run() -> dict:
    print("=" * 70)
    print("MIRROR TEST: only-KZ vs only-EU vs transfer on KOREM hourly")
    print("=" * 70)

    print("\nLoading KOREM…")
    kz_raw = load_korem()
    print(f"  KOREM rows: {len(kz_raw):,}  regions: {kz_raw.region_id.unique().tolist()}")
    print(f"  date range: {kz_raw.timestamp.min()} .. {kz_raw.timestamp.max()}")

    print("\nLoading EU (ENTSO-E)…")
    eu_raw = load_eu()
    print(f"  EU rows: {len(eu_raw):,}  regions: {eu_raw.region_id.unique().tolist()}")
    print(f"  date range: {eu_raw.timestamp.min()} .. {eu_raw.timestamp.max()}")

    # z-score normalize
    kz_norm, kz_stats = zscore_per_region(kz_raw)
    eu_norm, eu_stats = zscore_per_region(eu_raw)
    print(f"\nz-score stats (KZ):\n{kz_stats.to_string(index=False)}")
    print(f"\nz-score stats (EU, head):\n{eu_stats.head(4).to_string(index=False)}")

    all_metric_rows = []
    all_dm_rows = []
    all_pred_rows = []

    for h in HORIZONS:
        print(f"\n{'─'*70}\nHorizon = {h} h\n{'─'*70}")

        kz_feat = add_features(kz_norm, h)
        eu_feat = add_features(eu_norm, h)
        print(f"  KZ feat rows: {len(kz_feat):,}   EU feat rows: {len(eu_feat):,}")

        kz_tr, kz_va, kz_te = split_kz(kz_feat)
        print(f"  KZ split:  train={len(kz_tr):,}   val={len(kz_va):,}   test={len(kz_te):,}")

        # Cap EU rows for runtime
        if len(eu_feat) > 60000:
            eu_feat = eu_feat.sample(n=60000, random_state=42).sort_values(
                ["region_id", "timestamp"]).reset_index(drop=True)
            print(f"  EU subsampled to {len(eu_feat):,} rows")

        X_eu, y_eu = eu_feat[FEATURES].fillna(0.0), eu_feat["target_norm"].to_numpy()
        X_tr, y_tr = kz_tr[FEATURES].fillna(0.0), kz_tr["target_norm"].to_numpy()
        X_va, y_va = kz_va[FEATURES].fillna(0.0), kz_va["target_norm"].to_numpy()
        X_te, y_te = kz_te[FEATURES].fillna(0.0), kz_te["target_norm"].to_numpy()

        # ----- A. seasonal_naive -----
        y_pred_naive_norm = kz_te["seasonal_naive_norm"].to_numpy()

        # ----- B. only-KZ -----
        print("  fitting only-KZ catboost…")
        m_kz = fit_catboost(X_tr, y_tr)
        y_pred_kz_norm = m_kz.predict(X_te)

        # ----- C. only-EU (zero-shot) -----
        print("  fitting only-EU catboost (zero-shot)…")
        m_eu = fit_catboost(X_eu, y_eu)
        y_pred_eu_norm = m_eu.predict(X_te)

        # ----- D. transfer EU→KZ -----
        print("  fitting transfer (EU→KZ fine-tune)…")
        base = fit_catboost(X_eu, y_eu, iterations=400)
        m_tr = fit_catboost(X_tr, y_tr, init_model=base, iterations=200)
        y_pred_tr_norm = m_tr.predict(X_te)

        # Denormalize: y = y_norm * sigma + mu, per region
        kz_te = kz_te.copy()
        kz_te["pred_naive"]    = y_pred_naive_norm * kz_te.sigma + kz_te.mu
        kz_te["pred_only_kz"]  = y_pred_kz_norm    * kz_te.sigma + kz_te.mu
        kz_te["pred_only_eu"]  = y_pred_eu_norm    * kz_te.sigma + kz_te.mu
        kz_te["pred_transfer"] = y_pred_tr_norm    * kz_te.sigma + kz_te.mu
        kz_te["y_true"]        = kz_te.target_norm * kz_te.sigma + kz_te.mu

        # Conformal intervals: 90% absolute residual on validation, per-region scaled
        # First denormalize val predictions of m_kz
        y_pred_va_kz = m_kz.predict(X_va)
        va = kz_va.copy()
        va["pred_va_norm"] = y_pred_va_kz
        va["pred_va"] = va.pred_va_norm * va.sigma + va.mu
        va["y_va"] = va.target_norm * va.sigma + va.mu
        q = float(np.quantile(np.abs(va.y_va - va.pred_va), 0.90))
        # apply same band to transfer (use its own val residuals)
        y_pred_va_tr = m_tr.predict(X_va)
        va["pred_va_tr"] = y_pred_va_tr * va.sigma + va.mu
        q_tr = float(np.quantile(np.abs(va.y_va - va.pred_va_tr), 0.90))

        # Metrics
        y_train_ref = (kz_tr.target_norm * kz_tr.sigma + kz_tr.mu).to_numpy()
        models = [
            ("seasonal_naive",  kz_te.pred_naive.to_numpy(),   None, None),
            ("only_KZ_catboost",kz_te.pred_only_kz.to_numpy(), kz_te.pred_only_kz - q,    kz_te.pred_only_kz + q),
            ("only_EU_catboost",kz_te.pred_only_eu.to_numpy(), None, None),
            ("transfer_EU_KZ",  kz_te.pred_transfer.to_numpy(),kz_te.pred_transfer - q_tr,kz_te.pred_transfer + q_tr),
        ]
        for name, ypred, lo, hi in models:
            m = _metrics(kz_te.y_true.to_numpy(), ypred, y_train_ref,
                         y_lo=lo.to_numpy() if lo is not None else None,
                         y_hi=hi.to_numpy() if hi is not None else None)
            m["horizon"] = h
            m["model"] = name
            m["n_test"] = len(kz_te)
            all_metric_rows.append(m)

        # DM tests (squared-error loss). Compare:
        #   each non-naive   vs seasonal_naive
        #   transfer         vs only_KZ
        #   only_EU          vs only_KZ
        err = {name: (kz_te.y_true - ypred).to_numpy() for name, ypred, _, _ in models}
        pairs = [
            ("only_KZ_catboost", "seasonal_naive"),
            ("only_EU_catboost", "seasonal_naive"),
            ("transfer_EU_KZ",   "seasonal_naive"),
            ("transfer_EU_KZ",   "only_KZ_catboost"),
            ("only_EU_catboost", "only_KZ_catboost"),
            ("transfer_EU_KZ",   "only_EU_catboost"),
        ]
        for a, b in pairs:
            dm, p = diebold_mariano(err[a], err[b], h=h)
            all_dm_rows.append({"horizon": h, "model_a": a, "model_b": b,
                                "DM_stat": dm, "p_value": p, "n": len(err[a])})

        # Save per-horizon predictions
        pdf = kz_te[["timestamp", "region_id", "y_true", "pred_naive",
                     "pred_only_kz", "pred_only_eu", "pred_transfer"]].copy()
        pdf["horizon"] = h
        all_pred_rows.append(pdf)

    metrics_df = pd.DataFrame(all_metric_rows).sort_values(["horizon", "MAE"])
    dm_df = pd.DataFrame(all_dm_rows)
    pred_df = pd.concat(all_pred_rows, ignore_index=True)

    metrics_df.to_csv(OUT_DIR / "metrics.csv", index=False)
    dm_df.to_csv(OUT_DIR / "diebold_mariano.csv", index=False)
    pred_df.to_csv(OUT_DIR / "predictions.csv", index=False)

    # Write to SQLite for Power BI
    con = sqlite3.connect(str(DB))
    metrics_df.to_sql("mirror_test_metrics", con, if_exists="replace", index=False)
    dm_df.to_sql("mirror_test_dm", con, if_exists="replace", index=False)
    con.close()

    print("\n" + "=" * 70)
    print("RESULTS")
    print("=" * 70)
    print("\nMetrics (sorted by MAE within horizon):")
    print(metrics_df[["horizon", "model", "MAE", "RMSE", "sMAPE_pct",
                      "MASE", "PICP_90", "n_test"]].to_string(index=False))
    print("\nDiebold-Mariano (model_a vs model_b — negative DM means a is better):")
    print(dm_df.to_string(index=False))

    summary = {
        "horizons": HORIZONS,
        "n_kz_test_rows_per_horizon": {h: int(metrics_df[metrics_df.horizon == h].n_test.iloc[0]) for h in HORIZONS},
        "outputs": {
            "metrics_csv": str((OUT_DIR / "metrics.csv").relative_to(BASE)),
            "dm_csv":      str((OUT_DIR / "diebold_mariano.csv").relative_to(BASE)),
            "predictions_csv": str((OUT_DIR / "predictions.csv").relative_to(BASE)),
        },
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2),
                                          encoding="utf-8")
    print(f"\nSaved → {OUT_DIR}")
    return summary


if __name__ == "__main__":
    run()
