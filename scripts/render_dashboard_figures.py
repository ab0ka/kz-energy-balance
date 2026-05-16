"""
Plan-B dashboard renderer
=========================
If Power BI Desktop / SQLite ODBC driver are unavailable on this machine,
this script generates the four dashboard pages (Overview, Demand,
Generation & Trade, Forecasting) directly from the SQLite database using
matplotlib. The output PNGs go into Final/figures/dashboard_v2/ and can
serve as a drop-in replacement for the Power BI screenshots referenced by
figure 13 of main.tex.

The look-and-feel is intentionally clean (white background, Tableau-style
palette) so the figures can be used as-is in the manuscript.

Usage:
    python scripts/render_dashboard_figures.py

Output:
    Final/figures/dashboard_v2/overview.png
    Final/figures/dashboard_v2/demand.png
    Final/figures/dashboard_v2/generation_trade.png
    Final/figures/dashboard_v2/forecasting.png
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
DB = BASE / "data" / "clean" / "kz_energy_balance.db"
OUT_DIR = BASE / "Final" / "figures" / "dashboard_v2"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Palette
C = {
    "primary":  "#1f77b4",
    "accent":   "#ff7f0e",
    "good":     "#2ca02c",
    "muted":    "#7f7f7f",
    "grid":     "#dddddd",
    "kpi_bg":   "#f5f7fa",
    "title":    "#1a1a2e",
}

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 10,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.color": C["grid"],
    "grid.linewidth": 0.5,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
})


# ───────────────────────── PAGE 1: OVERVIEW ─────────────────────────
def page_overview(con):
    fig = plt.figure(figsize=(14, 8))
    fig.suptitle("Kazakhstan Energy Balance — Overview", fontsize=16,
                 fontweight="bold", color=C["title"], y=0.98)

    # KPI strip
    bf = pd.read_sql("SELECT * FROM by_fuel_group_TJ "
                     "WHERE year=2024 AND line_item='Total primary energy consumption'", con)
    total_2024 = float(bf[bf.fuel_group == "Total"].value.iloc[0])
    res_2024 = float(bf[bf.fuel_group == "Renewable energy sources"].value.iloc[0])
    res_share = 100 * res_2024 / total_2024

    kegoc = pd.read_sql("SELECT * FROM kegoc_balance_wide WHERE year=2024", con).iloc[0]

    kpis = [
        ("TPEC 2024",          f"{total_2024/1e6:.2f} EJ",     f"≈ {total_2024:,.0f} TJ"),
        ("Electricity prod.",  f"{kegoc.production_bkwh:.1f}", "BkWh (2024, KEGOC)"),
        ("Electricity cons.",  f"{kegoc.consumption_bkwh:.1f}", "BkWh (2024, KEGOC)"),
        ("RES share in TPEC",  f"{res_share:.1f}%",           "renewable / total 2024"),
    ]
    for i, (label, value, sub) in enumerate(kpis):
        ax = fig.add_axes([0.05 + i * 0.235, 0.78, 0.22, 0.13])
        ax.set_xticks([]); ax.set_yticks([])
        ax.set_facecolor(C["kpi_bg"])
        ax.text(0.5, 0.78, label, ha="center", va="center",
                fontsize=10, color=C["muted"], transform=ax.transAxes)
        ax.text(0.5, 0.42, value, ha="center", va="center",
                fontsize=22, fontweight="bold", color=C["title"], transform=ax.transAxes)
        ax.text(0.5, 0.12, sub, ha="center", va="center",
                fontsize=9, color=C["muted"], transform=ax.transAxes)
        for spine in ax.spines.values():
            spine.set_visible(True); spine.set_color(C["grid"])

    # Fuel mix donut
    ax1 = fig.add_axes([0.05, 0.10, 0.40, 0.55])
    mix = bf[bf.fuel_group != "Total"][["fuel_group", "value"]].copy()
    mix = mix[mix.value > 0].sort_values("value", ascending=False)
    palette = ["#2c3e50", "#3498db", "#e67e22", "#27ae60", "#9b59b6", "#95a5a6"]
    wedges, _, autotexts = ax1.pie(
        mix.value, labels=mix.fuel_group, autopct="%1.1f%%",
        startangle=90, colors=palette[:len(mix)],
        wedgeprops=dict(width=0.42, edgecolor="white"),
        textprops=dict(fontsize=9))
    for at in autotexts:
        at.set_color("white"); at.set_fontweight("bold")
    ax1.set_title("TPEC fuel mix, 2024", fontsize=12, fontweight="bold")

    # TPEC by year
    ax2 = fig.add_axes([0.55, 0.10, 0.40, 0.55])
    series = pd.read_sql(
        "SELECT year, SUM(value) total FROM by_fuel_group_TJ "
        "WHERE line_item='Total primary energy consumption' AND fuel_group<>'Total' "
        "GROUP BY year ORDER BY year", con)
    ax2.bar(series.year.astype(str), series.total / 1e6,
            color=C["primary"], width=0.6, alpha=0.85)
    for x, v in zip(series.year.astype(str), series.total / 1e6):
        ax2.text(x, v + 0.05, f"{v:.2f}", ha="center", va="bottom",
                 fontsize=10, fontweight="bold")
    ax2.set_ylabel("Total Primary Energy Consumption, EJ")
    ax2.set_title("TPEC by year (stat.gov.kz)", fontsize=12, fontweight="bold")
    ax2.set_ylim(0, series.total.max() / 1e6 * 1.15)

    fig.savefig(OUT_DIR / "overview.png", dpi=150, bbox_inches="tight",
                facecolor="white")
    plt.close(fig)
    print(f"  saved {OUT_DIR/'overview.png'}")


# ───────────────────────── PAGE 2: DEMAND ─────────────────────────
def page_demand(con):
    fig = plt.figure(figsize=(14, 8))
    fig.suptitle("Kazakhstan Demand — KOREM Hourly Balancing Market",
                 fontsize=16, fontweight="bold", color=C["title"], y=0.98)

    df = pd.read_sql(
        "SELECT date, hour, zone, payment_to_supplier_kzt FROM korem_hourly", con)
    df["timestamp"] = pd.to_datetime(df.date) + pd.to_timedelta(df.hour, unit="h")
    df["dow"] = df.timestamp.dt.dayofweek
    df["month_ts"] = df.timestamp.dt.to_period("M").dt.to_timestamp()

    # Hourly profile by zone
    ax1 = fig.add_axes([0.06, 0.55, 0.42, 0.36])
    for zone, color in [("North-South", C["primary"]), ("West", C["accent"])]:
        sub = df[df.zone == zone].groupby("hour")["payment_to_supplier_kzt"].mean() / 1e6
        ax1.plot(sub.index, sub.values, marker="o", linewidth=2,
                 color=color, label=zone, markersize=4)
    ax1.set_xlabel("Hour of day"); ax1.set_ylabel("Mean payment, M KZT")
    ax1.set_title("Mean hourly profile by zone", fontsize=12, fontweight="bold")
    ax1.set_xticks(range(0, 24, 2))
    ax1.legend(loc="upper left", frameon=False)

    # Weekly profile
    ax2 = fig.add_axes([0.55, 0.55, 0.42, 0.36])
    dnames = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for zone, color in [("North-South", C["primary"]), ("West", C["accent"])]:
        sub = df[df.zone == zone].groupby("dow")["payment_to_supplier_kzt"].mean() / 1e6
        ax2.plot(range(7), sub.values, marker="s", linewidth=2,
                 color=color, label=zone, markersize=6)
    ax2.set_xticks(range(7)); ax2.set_xticklabels(dnames)
    ax2.set_ylabel("Mean payment, M KZT")
    ax2.set_title("Mean weekday profile by zone", fontsize=12, fontweight="bold")
    ax2.legend(loc="upper left", frameon=False)

    # Monthly totals timeline
    ax3 = fig.add_axes([0.06, 0.07, 0.91, 0.36])
    monthly = (df.groupby(["month_ts", "zone"])["payment_to_supplier_kzt"]
                 .sum().reset_index())
    pivot = monthly.pivot(index="month_ts", columns="zone",
                          values="payment_to_supplier_kzt") / 1e9
    pivot.plot(kind="bar", ax=ax3, color=[C["primary"], C["accent"]],
               width=0.75, alpha=0.85)
    ax3.set_xticklabels([d.strftime("%Y-%m") for d in pivot.index],
                        rotation=45, ha="right")
    ax3.set_ylabel("Monthly total payment, B KZT")
    ax3.set_xlabel("")
    ax3.set_title("Monthly aggregate payments — supplier compensation, by zone",
                  fontsize=12, fontweight="bold")
    ax3.legend(title="", frameon=False, loc="upper right")

    fig.savefig(OUT_DIR / "demand.png", dpi=150, bbox_inches="tight",
                facecolor="white")
    plt.close(fig)
    print(f"  saved {OUT_DIR/'demand.png'}")


# ───────────────────────── PAGE 3: GENERATION & TRADE ─────────────────────────
def page_generation_trade(con):
    fig = plt.figure(figsize=(14, 8))
    fig.suptitle("Generation & Cross-Border Trade", fontsize=16,
                 fontweight="bold", color=C["title"], y=0.98)

    # KEGOC prod vs cons
    ax1 = fig.add_axes([0.06, 0.10, 0.55, 0.78])
    k = pd.read_sql("SELECT year, production_bkwh, consumption_bkwh "
                    "FROM kegoc_balance_wide ORDER BY year", con)
    x = np.arange(len(k))
    w = 0.38
    ax1.bar(x - w/2, k.production_bkwh, width=w, label="Production",
            color=C["primary"], alpha=0.85)
    ax1.bar(x + w/2, k.consumption_bkwh, width=w, label="Consumption",
            color=C["accent"], alpha=0.85)
    # annotate the balance
    for i, row in k.iterrows():
        bal = row.production_bkwh - row.consumption_bkwh
        color = C["good"] if bal >= 0 else "#d62728"
        ax1.text(i, max(row.production_bkwh, row.consumption_bkwh) + 1.5,
                 f"{bal:+.1f}", ha="center", va="bottom",
                 fontsize=9, color=color, fontweight="bold")
    ax1.set_xticks(x); ax1.set_xticklabels(k.year.astype(int).astype(str))
    ax1.set_ylabel("BkWh"); ax1.set_xlabel("")
    ax1.set_title("Electricity Production vs Consumption (KEGOC, verified)",
                  fontsize=12, fontweight="bold")
    ax1.legend(loc="upper left", frameon=False)
    ax1.set_ylim(85, max(k.consumption_bkwh.max(), k.production_bkwh.max()) + 6)

    # Side panel: fuel mix in primary production 2024 (from balance_long)
    ax2 = fig.add_axes([0.66, 0.10, 0.31, 0.78])
    bw = pd.read_sql(
        "SELECT fuel_group, SUM(value) value FROM balance_long "
        "WHERE year=2024 AND unit='TJ' AND line_item='Primary energy production' "
        "AND fuel_group<>'Total' GROUP BY fuel_group "
        "HAVING SUM(value) > 0 ORDER BY 2 DESC", con)
    palette = ["#2c3e50", "#3498db", "#e67e22", "#27ae60", "#9b59b6", "#95a5a6"]
    if not bw.empty:
        ax2.barh(bw.fuel_group, bw.value / 1e6, color=palette[:len(bw)], alpha=0.85)
        for i, v in enumerate(bw.value / 1e6):
            ax2.text(v, i, f" {v:.2f} EJ", va="center", fontsize=9)
        ax2.invert_yaxis()
        ax2.set_xlabel("EJ")
        ax2.set_xlim(0, (bw.value / 1e6).max() * 1.20)
    else:
        ax2.text(0.5, 0.5, "no production data for 2024",
                 ha="center", va="center", transform=ax2.transAxes)
        ax2.set_xticks([]); ax2.set_yticks([])
    ax2.set_title("Primary energy production, 2024", fontsize=12, fontweight="bold")

    fig.savefig(OUT_DIR / "generation_trade.png", dpi=150, bbox_inches="tight",
                facecolor="white")
    plt.close(fig)
    print(f"  saved {OUT_DIR/'generation_trade.png'}")


# ───────────────────────── PAGE 4: FORECASTING ─────────────────────────
def page_forecasting(con):
    fig = plt.figure(figsize=(14, 8))
    fig.suptitle("Forecasting — Model Comparison (last run)",
                 fontsize=16, fontweight="bold", color=C["title"], y=0.98)

    # Model metrics from SQLite (= last ml_pipeline run)
    metrics = pd.read_sql("SELECT * FROM ml_metrics ORDER BY MAE", con)

    ax1 = fig.add_axes([0.06, 0.55, 0.42, 0.36])
    palette = [C["good"], C["primary"], C["accent"], "#9b59b6", C["muted"]]
    bars = ax1.barh(metrics.model, metrics.MAE / 1e6,
                    color=palette[:len(metrics)], alpha=0.85)
    for b, v in zip(bars, metrics.MAE / 1e6):
        ax1.text(v, b.get_y() + b.get_height() / 2,
                 f" {v:.2f}M", va="center", fontsize=9)
    ax1.set_xlabel("MAE (M KZT)")
    ax1.set_title("Model comparison — MAE on KOREM monthly",
                  fontsize=12, fontweight="bold")
    ax1.invert_yaxis()

    ax2 = fig.add_axes([0.55, 0.55, 0.42, 0.36])
    ax2.barh(metrics.model, metrics.MASE,
             color=palette[:len(metrics)], alpha=0.85)
    ax2.axvline(1.0, ls="--", color=C["muted"], lw=1, alpha=0.7)
    ax2.text(1.02, len(metrics) - 0.5, "naive\nbaseline",
             fontsize=8, color=C["muted"], va="top")
    for i, v in enumerate(metrics.MASE):
        ax2.text(v, i, f" {v:.2f}", va="center", fontsize=9)
    ax2.set_xlabel("MASE (vs hourly seasonal naive in train)")
    ax2.set_title("MASE — <1 means better than naive",
                  fontsize=12, fontweight="bold")
    ax2.invert_yaxis()

    # Predictions panel — last cutoff
    ax3 = fig.add_axes([0.06, 0.07, 0.91, 0.36])
    pred = pd.read_sql("SELECT * FROM ml_predictions ORDER BY cutoff", con)
    last_cut = pred.cutoff.max()
    pred = pred[pred.cutoff == last_cut].sort_values("model")
    if len(pred) > 0:
        x = np.arange(len(pred))
        ax3.bar(x - 0.25, pred.actual / 1e9, width=0.4,
                label="Actual", color=C["muted"], alpha=0.7)
        ax3.bar(x + 0.25, pred.pred / 1e9, width=0.4,
                label="Predicted", color=C["primary"], alpha=0.85)
        ax3.errorbar(x + 0.25,
                     pred.pred / 1e9,
                     yerr=[(pred.pred - pred.lo90) / 1e9,
                           (pred.hi90 - pred.pred) / 1e9],
                     fmt="none", ecolor="black", capsize=4, lw=1)
        ax3.set_xticks(x); ax3.set_xticklabels(pred.model, rotation=15, ha="right")
        ax3.set_ylabel("Payment, B KZT")
        ax3.set_title(f"Latest fold (cutoff = {last_cut}) — actual vs predicted "
                      "with 90% CI", fontsize=12, fontweight="bold")
        ax3.legend(loc="upper right", frameon=False)
    else:
        ax3.text(0.5, 0.5, "no predictions available", ha="center", va="center",
                 transform=ax3.transAxes, fontsize=12)
        ax3.set_xticks([]); ax3.set_yticks([])

    fig.savefig(OUT_DIR / "forecasting.png", dpi=150, bbox_inches="tight",
                facecolor="white")
    plt.close(fig)
    print(f"  saved {OUT_DIR/'forecasting.png'}")


def main():
    print(f"Rendering dashboard pages from {DB}")
    con = sqlite3.connect(str(DB))
    page_overview(con)
    page_demand(con)
    page_generation_trade(con)
    page_forecasting(con)
    con.close()
    print(f"\nAll four pages → {OUT_DIR}")


if __name__ == "__main__":
    main()
