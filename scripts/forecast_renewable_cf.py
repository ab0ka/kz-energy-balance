"""
Renewable Capacity-Factor Forecasting Pipeline
==============================================
Forecasts the daily wind capacity-factor series (ERA5-Land, 2011-2014) with a
six-model comparison and a hybrid SARIMA-LSTM, reproducing the results reported
in Chapter 5 (Table 5.2, Figure 5.13) of the dissertation.

Split   : train = 2011-2013, test = 2014 (364 days), static 12-month horizon.
Models  : naive persistence, ARIMA(2,0,2), Holt-Winters, SARIMA (ARIMA+Fourier),
          LSTM (recursive), SARIMA-LSTM hybrid (LSTM on SARIMA residuals).
Metrics : MAE, RMSE, sMAPE, R^2 on the 2014 test set; Diebold-Mariano test
          (HLN-corrected) of every model against naive persistence.

Outputs : data/clean/ml_results/metrics_renewable_cf.csv
          data/clean/ml_results/predictions_renewable_cf.csv
          updates ml_metrics_renewable_cf / ml_predictions_renewable_cf /
          ml_dm_renewable_cf in kz_energy_balance.db
          ../../Dissertation_Document/figures/fig7_arima_forecast.pdf

Usage   : python scripts/forecast_renewable_cf.py
Deps    : numpy, pandas, statsmodels, scipy, tensorflow, matplotlib
"""
from __future__ import annotations
import csv, math, os, sqlite3, warnings
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["PYTHONHASHSEED"] = "42"

SEED = 42
WINDOW = 30          # LSTM look-back (days)
FOURIER_K = 4        # annual Fourier harmonics for the SARIMA seasonal term
EPOCHS = 40

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
SW = DATA / "2011-2014_solar-wind_fixed.csv"
DB = DATA / "clean" / "kz_energy_balance.db"
RESULTS = DATA / "clean" / "ml_results"
FIG = BASE.parent.parent / "Dissertation_Document" / "figures" / "fig7_arima_forecast.pdf"


# ───────────────────────── data ─────────────────────────
def load_daily_wind():
    daily = defaultdict(list)
    with open(SW, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d = datetime.strptime(r["Time"], "%Y-%m-%d %H:%M:%S").date()
            daily[d].append(float(r["onwind"]))
    dates = sorted(daily)
    series = np.array([sum(daily[d]) / len(daily[d]) for d in dates])
    cut = next(i for i, d in enumerate(dates) if d >= datetime(2014, 1, 1).date())
    return dates, series, cut


# ───────────────────────── metrics ─────────────────────────
def metrics(actual, pred):
    a, p = np.asarray(actual, float), np.asarray(pred, float)
    e = a - p
    mae = float(np.mean(np.abs(e)))
    rmse = float(np.sqrt(np.mean(e ** 2)))
    denom = (np.abs(a) + np.abs(p)) / 2.0
    smape = float(np.mean(np.abs(e)[denom > 0] / denom[denom > 0]) * 100)
    ss_res = float(np.sum(e ** 2))
    ss_tot = float(np.sum((a - a.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    return mae, rmse, smape, r2


def diebold_mariano(e_base, e_model, h=1):
    """HLN-corrected DM test on squared-error loss. e_base = naive errors."""
    from scipy import stats
    e1, e2 = np.asarray(e_base, float), np.asarray(e_model, float)
    d = e1 ** 2 - e2 ** 2
    n = len(d)
    var = np.var(d, ddof=1)
    if not np.isfinite(var) or var <= 0:
        return float("nan"), float("nan")
    dm = d.mean() / np.sqrt(var / n)
    hln = math.sqrt((n + 1 - 2 * h + h * (h - 1) / n) / n)
    dm *= hln
    p = 2 * stats.t.sf(abs(dm), df=n - 1)
    return float(dm), float(p)


# ───────────────────────── models ─────────────────────────
def fourier_terms(idx, period, K):
    t = np.asarray(idx, float)
    return np.column_stack(
        [f(2 * np.pi * k * t / period) for k in range(1, K + 1) for f in (np.sin, np.cos)]
    )


def build_lstm(window):
    from tensorflow.keras.models import Sequential
    from tensorflow.keras.layers import LSTM, Dense, Input
    m = Sequential([Input((window, 1)), LSTM(64),
                    Dense(32, activation="relu"), Dense(1)])
    m.compile(optimizer="adam", loss="mse")
    return m


def recursive_forecast(model, history_scaled, window, n):
    buf = list(history_scaled[-window:])
    out = []
    for _ in range(n):
        x = np.array(buf[-window:])[None, ..., None]
        nxt = float(model.predict(x, verbose=0)[0, 0])
        out.append(nxt)
        buf.append(nxt)
    return np.array(out)


def run():
    import tensorflow as tf
    tf.random.set_seed(SEED)
    np.random.seed(SEED)
    from statsmodels.tsa.arima.model import ARIMA
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    dates, series, cut = load_daily_wind()
    train, test = series[:cut], series[cut:]
    test_dates = dates[cut:]
    n = len(test)
    print(f"daily obs: {len(series)}  train: {len(train)}  test: {n}  "
          f"({test_dates[0]} .. {test_dates[-1]})")

    preds = {}

    # baselines
    preds["naive_last"] = np.repeat(train[-1], n)
    preds["arima_2_0_2"] = np.asarray(ARIMA(train, order=(2, 0, 2)).fit().forecast(n))
    preds["holt_winters"] = np.asarray(
        ExponentialSmoothing(train, trend="add", seasonal=None).fit().forecast(n))

    # SARIMA = ARIMA(2,0,2) + annual Fourier seasonal regressors
    tr_idx = np.arange(len(train))
    te_idx = np.arange(len(train), len(train) + n)
    Xtr = fourier_terms(tr_idx, 365.25, FOURIER_K)
    Xte = fourier_terms(te_idx, 365.25, FOURIER_K)
    sarima_fit = ARIMA(train, order=(2, 0, 2), exog=Xtr).fit()
    sarima_fc = np.asarray(sarima_fit.forecast(n, exog=Xte))
    preds["sarima"] = sarima_fc

    # LSTM (recursive multi-step on lag window)
    lo, hi = float(train.min()), float(train.max())
    sc = lambda x: (np.asarray(x, float) - lo) / (hi - lo)
    un = lambda x: np.asarray(x, float) * (hi - lo) + lo
    tr_s = sc(train)
    Xs = np.array([tr_s[i:i + WINDOW] for i in range(len(tr_s) - WINDOW)])[..., None]
    ys = np.array([tr_s[i + WINDOW] for i in range(len(tr_s) - WINDOW)])
    lstm = build_lstm(WINDOW)
    lstm.fit(Xs, ys, epochs=EPOCHS, batch_size=32, verbose=0, validation_split=0.15)
    preds["lstm"] = un(recursive_forecast(lstm, tr_s, WINDOW, n))

    # SARIMA-LSTM hybrid: LSTM models the SARIMA in-sample residuals
    in_sample = np.asarray(sarima_fit.predict(start=0, end=len(train) - 1, exog=Xtr))
    resid = train - in_sample
    rlo, rhi = float(resid.min()), float(resid.max())
    r_s = (resid - rlo) / (rhi - rlo)
    Xr = np.array([r_s[i:i + WINDOW] for i in range(len(r_s) - WINDOW)])[..., None]
    yr = np.array([r_s[i + WINDOW] for i in range(len(r_s) - WINDOW)])
    lstm_r = build_lstm(WINDOW)
    lstm_r.fit(Xr, yr, epochs=EPOCHS, batch_size=32, verbose=0, validation_split=0.15)
    resid_fc = recursive_forecast(lstm_r, r_s, WINDOW, n) * (rhi - rlo) + rlo
    preds["sarima_lstm"] = sarima_fc + resid_fc

    # ── metrics + DM ──
    order = ["naive_last", "arima_2_0_2", "holt_winters", "sarima", "lstm", "sarima_lstm"]
    naive_err = test - preds["naive_last"]
    rows, dm_rows = [], []
    for name in order:
        mae, rmse, smape, r2 = metrics(test, preds[name])
        rows.append(dict(target="wind_cf_daily", model=name, MAE=mae, RMSE=rmse,
                         sMAPE_pct=smape, R2=r2, n_test_obs=n))
        if name != "naive_last":
            dm, p = diebold_mariano(naive_err, test - preds[name])
            dm_rows.append(dict(target="wind_cf_daily", compared_to="naive_last",
                                model=name, DM_stat=dm, p_value=p, n_obs=n))

    naive_mae = rows[0]["MAE"]
    print(f"\n{'model':<16}{'MAE':>9}{'RMSE':>9}{'sMAPE%':>9}{'R2':>8}{'vs naive':>10}")
    print("-" * 61)
    for r in rows:
        imp = (1 - r["MAE"] / naive_mae) * 100
        print(f"{r['model']:<16}{r['MAE']:>9.4f}{r['RMSE']:>9.4f}"
              f"{r['sMAPE_pct']:>9.2f}{r['R2']:>8.3f}{imp:>9.1f}%")
    print("\nDiebold-Mariano vs naive_last (HLN-corrected):")
    for r in dm_rows:
        print(f"  {r['model']:<14} DM={r['DM_stat']:+.3f}  p={r['p_value']:.5f}")

    # ── save CSVs ──
    RESULTS.mkdir(parents=True, exist_ok=True)
    with open(RESULTS / "metrics_renewable_cf.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["target", "model", "MAE", "RMSE",
                                          "sMAPE_pct", "R2", "n_test_obs"])
        w.writeheader(); w.writerows(rows)
    with open(RESULTS / "predictions_renewable_cf.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["target", "model", "date", "actual", "prediction"])
        for name in order:
            for d, a, p in zip(test_dates, test, preds[name]):
                w.writerow(["wind_cf_daily", name, str(d), float(a), float(p)])

    # ── update SQLite ──
    if DB.exists():
        conn = sqlite3.connect(str(DB))
        c = conn.cursor()
        c.execute("DELETE FROM ml_metrics_renewable_cf WHERE target='wind_cf_daily'")
        for r in rows:
            c.execute("INSERT INTO ml_metrics_renewable_cf "
                      "(target,model,MAE,RMSE,sMAPE_pct,MASE,n_test_obs) VALUES (?,?,?,?,?,?,?)",
                      (r["target"], r["model"], r["MAE"], r["RMSE"], r["sMAPE_pct"],
                       r["R2"], r["n_test_obs"]))
        c.execute("DELETE FROM ml_predictions_renewable_cf WHERE target='wind_cf_daily'")
        for name in order:
            for d, a, p in zip(test_dates, test, preds[name]):
                c.execute("INSERT INTO ml_predictions_renewable_cf VALUES (?,?,?,?,?)",
                          ("wind_cf_daily", name, str(d), float(a), float(p)))
        c.execute("DELETE FROM ml_dm_renewable_cf WHERE target='wind_cf_daily'")
        for r in dm_rows:
            c.execute("INSERT INTO ml_dm_renewable_cf VALUES (?,?,?,?,?,?)",
                      (r["target"], r["compared_to"], r["model"],
                       r["DM_stat"], r["p_value"], r["n_obs"]))
        conn.commit()
        tot = c.execute(
            "SELECT (SELECT COUNT(*) FROM balance_long)+(SELECT COUNT(*) FROM balance_wide_by_fuel)"
            "+(SELECT COUNT(*) FROM by_fuel_group_TJ)+(SELECT COUNT(*) FROM electricity_balance)"
            "+(SELECT COUNT(*) FROM kegoc_balance)+(SELECT COUNT(*) FROM kegoc_balance_wide)"
            "+(SELECT COUNT(*) FROM korem_hourly)+(SELECT COUNT(*) FROM korem_monthly)"
            "+(SELECT COUNT(*) FROM meta)+(SELECT COUNT(*) FROM mirror_test_dm)"
            "+(SELECT COUNT(*) FROM mirror_test_metrics)+(SELECT COUNT(*) FROM ml_diebold_mariano)"
            "+(SELECT COUNT(*) FROM ml_dm_renewable_cf)+(SELECT COUNT(*) FROM ml_metrics)"
            "+(SELECT COUNT(*) FROM ml_metrics_all_targets)+(SELECT COUNT(*) FROM ml_metrics_renewable_cf)"
            "+(SELECT COUNT(*) FROM ml_predictions)+(SELECT COUNT(*) FROM ml_predictions_renewable_cf)"
        ).fetchone()[0]
        conn.close()
        print(f"\nSQLite updated. New total rows across 18 tables: {tot:,}")

    # ── figure 5.13 ──
    make_figure(test_dates, test, preds, rows, naive_mae)
    return rows, dm_rows


def make_figure(test_dates, test, preds, rows, naive_mae):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    td = [datetime.combine(d, datetime.min.time()) for d in test_dates]
    mae = {r["model"]: r["MAE"] for r in rows}
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7))
    fig.suptitle("Wind Capacity Factor — SARIMA-LSTM Forecast vs Actual (2014 test set)",
                 fontsize=12, fontweight="bold")

    # (a) full test year: actual + the 3 strongest models
    ax1.plot(td, test, color="#444444", lw=1.0, label="Actual")
    ax1.plot(td, preds["arima_2_0_2"], color="#1f77b4", lw=1.3, ls="--",
             label=f"ARIMA(2,0,2)  MAE={mae['arima_2_0_2']:.4f}")
    ax1.plot(td, preds["sarima"], color="#2ca02c", lw=1.3, ls="-.",
             label=f"SARIMA  MAE={mae['sarima']:.4f}")
    ax1.plot(td, preds["sarima_lstm"], color="#d62728", lw=1.6,
             label=f"SARIMA-LSTM hybrid  MAE={mae['sarima_lstm']:.4f}")
    ax1.set_title("(a) Full 12-month test horizon", fontsize=10)
    ax1.set_ylabel("Wind capacity factor")
    ax1.legend(fontsize=8, loc="upper right")
    ax1.grid(alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b"))

    # (b) zoom: first 90 days
    k = 90
    ax2.plot(td[:k], test[:k], color="#444444", lw=1.2, label="Actual")
    ax2.plot(td[:k], preds["sarima_lstm"][:k], color="#d62728", lw=1.6,
             label="SARIMA-LSTM hybrid")
    ax2.plot(td[:k], preds["arima_2_0_2"][:k], color="#1f77b4", lw=1.2, ls="--",
             label="ARIMA(2,0,2)")
    ax2.set_title("(b) Detail: first 90 days of the test period", fontsize=10)
    ax2.set_ylabel("Wind capacity factor")
    ax2.set_xlabel("2014")
    ax2.legend(fontsize=8, loc="upper right")
    ax2.grid(alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    FIG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG, bbox_inches="tight")
    plt.close(fig)
    print(f"figure saved -> {FIG}")


if __name__ == "__main__":
    run()
