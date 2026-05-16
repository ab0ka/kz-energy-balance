"""
Kazakhstan Energy Transition — Comprehensive Infographic
=========================================================
A single, publication-quality figure summarizing:
- Current energy mix & trajectory
- RES growth dynamics
- Energy deficit trend
- CO2 emissions path
- 2030/2050/2060 targets
- Regional power zone balance

Author: Gabit Sekenov
"""
import warnings
warnings.filterwarnings("ignore")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.gridspec as gridspec
import matplotlib.ticker as mticker
import numpy as np
from pathlib import Path

plt.rcParams.update({
    "figure.dpi": 300, "savefig.dpi": 300,
    "font.family": "serif",
    "font.serif": ["Times New Roman", "DejaVu Serif"],
    "axes.titlesize": 11, "axes.labelsize": 9,
    "xtick.labelsize": 8, "ytick.labelsize": 8,
    "legend.fontsize": 7.5,
})

OUT = Path("C:/Users/Timing/Desktop/code/claude/article/Final/figures")

# ── DATA ──────────────────────────────────────────────────────
years = [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025]
production  = [106.8, 106.0, 108.0, 114.3, 112.9, 112.8, 117.9, 122.0]
consumption = [104.3, 105.2, 107.3, 110.7, 112.9, 115.1, 120.0, 125.0]
balance     = [p-c for p,c in zip(production, consumption)]
res_share   = [1.3, 2.3, 3.0, 3.69, 4.53, 5.92, 6.43, 7.0]
wind_mw     = [284, 350, 405, 511, 894, 1410, 1570, 1957]
solar_mw    = [545, 730, 898, 1032, 1150, 1223, 1223, 1313]
hydro_mw    = [285, 240, 241, 255, 280, 270, 288, 314]
co2_mt      = [262, 255, 235, 235, 246, 228, 228, None]
intensity   = [0.34, 0.33, 0.32, 0.32, 0.32, 0.32, 0.30, None]

# Targets
target_2030_pct = 15
target_2050_pct = 50

# Projection: what MW needed for 15% by 2030
# 2025: 7% from ~3586 MW total RES. For 15% need ~7700 MW
# Need ~4100 MW more in 5 years = ~820 MW/year
proj_years = [2025, 2026, 2027, 2028, 2029, 2030]
proj_res_pct = [7.0, 8.6, 10.2, 11.8, 13.4, 15.0]
proj_res_mw = [3586, 4400, 5200, 6000, 6800, 7700]

# ── FIGURE ────────────────────────────────────────────────────
fig = plt.figure(figsize=(18, 22))
gs = gridspec.GridSpec(4, 3, figure=fig, hspace=0.35, wspace=0.3,
                       height_ratios=[0.8, 1, 1, 1])

# ── HEADER ────────────────────────────────────────────────────
ax_header = fig.add_subplot(gs[0, :])
ax_header.axis("off")

ax_header.text(0.5, 0.85, "KAZAKHSTAN ENERGY TRANSITION",
               transform=ax_header.transAxes, ha="center", va="top",
               fontsize=24, fontweight="bold", color="#2c3e50")
ax_header.text(0.5, 0.65, "Statistical Analysis of the Fuel and Energy Balance (2018-2025)",
               transform=ax_header.transAxes, ha="center", va="top",
               fontsize=14, color="#7f8c8d")
ax_header.text(0.5, 0.48, "Sekenov G., Zhakiyev N. | Astana IT University & Harvard University",
               transform=ax_header.transAxes, ha="center", va="top",
               fontsize=10, color="#95a5a6", style="italic")

# Key stats boxes
stats = [
    ("122.0\nbillion kWh", "Production\n(2025)", "#2c3e50"),
    ("125.0\nbillion kWh", "Consumption\n(2025)", "#e74c3c"),
    ("-3.0\nbillion kWh", "Energy\nDeficit", "#c0392b"),
    ("3,586\nMW", "RES Installed\nCapacity", "#27ae60"),
    ("7.0%", "Renewable\nShare", "#1abc9c"),
    ("148", "RES\nFacilities", "#f39c12"),
]
for i, (val, label, color) in enumerate(stats):
    x = 0.08 + i * 0.15
    rect = mpatches.FancyBboxPatch((x-0.06, 0.02), 0.12, 0.38,
                                     boxstyle="round,pad=0.01",
                                     facecolor=color, alpha=0.1,
                                     edgecolor=color, linewidth=2,
                                     transform=ax_header.transAxes)
    ax_header.add_patch(rect)
    ax_header.text(x, 0.28, val, transform=ax_header.transAxes,
                   ha="center", va="center", fontsize=13, fontweight="bold", color=color)
    ax_header.text(x, 0.08, label, transform=ax_header.transAxes,
                   ha="center", va="center", fontsize=7.5, color="#7f8c8d")


# ── PANEL 1: Electricity Balance ─────────────────────────────
ax1 = fig.add_subplot(gs[1, 0])
x = np.arange(len(years))
ax1.plot(years, production, "b-o", markersize=5, linewidth=2, label="Production")
ax1.plot(years, consumption, "r-s", markersize=5, linewidth=2, label="Consumption")
ax1.fill_between(years, production, consumption,
                 where=[p < c for p,c in zip(production, consumption)],
                 color="red", alpha=0.15, label="Deficit")
ax1.fill_between(years, production, consumption,
                 where=[p >= c for p,c in zip(production, consumption)],
                 color="green", alpha=0.15, label="Surplus")
ax1.axhline(y=production[-1], color="blue", ls=":", alpha=0.3)
ax1.set_title("(a) Electricity Balance", fontweight="bold")
ax1.set_ylabel("Billion kWh")
ax1.set_ylim(100, 130)
ax1.legend(fontsize=7, loc="upper left")
ax1.annotate("Net importer\nsince 2023", xy=(2023, 113),
             xytext=(2019.5, 118), fontsize=7.5, color="#c0392b",
             arrowprops=dict(arrowstyle="->", color="#c0392b"))


# ── PANEL 2: RES Share + Projection to 2030 ──────────────────
ax2 = fig.add_subplot(gs[1, 1])
ax2.plot(years, res_share, "g-o", markersize=6, linewidth=2.5, label="Actual", zorder=5)
ax2.plot(proj_years, proj_res_pct, "g--^", markersize=5, linewidth=1.5,
         alpha=0.6, label="Projected (linear)")
ax2.axhline(15, color="#e74c3c", ls="--", lw=1.5, alpha=0.7, label="2030 target (15%)")
ax2.axhline(50, color="#e67e22", ls=":", lw=1, alpha=0.4, label="2050 target (50%)")
ax2.fill_between(proj_years, proj_res_pct, alpha=0.08, color="green")
ax2.set_title("(b) Renewable Energy Share (%)", fontweight="bold")
ax2.set_ylabel("Share in Generation (%)")
ax2.set_ylim(0, 55)
ax2.legend(fontsize=7, loc="upper left")
ax2.annotate("~820 MW/year\nadditions needed", xy=(2028, 12),
             fontsize=7.5, color="#27ae60", ha="center",
             bbox=dict(boxstyle="round,pad=0.3", facecolor="#27ae60", alpha=0.1))


# ── PANEL 3: RES Capacity Stacked ────────────────────────────
ax3 = fig.add_subplot(gs[1, 2])
ax3.stackplot(years, wind_mw, solar_mw, hydro_mw,
              labels=["Wind", "Solar PV", "Small Hydro"],
              colors=["#1abc9c", "#f1c40f", "#3498db"], alpha=0.8)
ax3.set_title("(c) RES Installed Capacity (MW)", fontweight="bold")
ax3.set_ylabel("MW")
ax3.legend(loc="upper left", fontsize=7)
for i, yr in enumerate(years):
    total = wind_mw[i] + solar_mw[i] + hydro_mw[i]
    if yr >= 2020:
        ax3.text(yr, total + 50, f"{total:,}", ha="center", fontsize=6.5, fontweight="bold")


# ── PANEL 4: CO2 Emissions ───────────────────────────────────
ax4 = fig.add_subplot(gs[2, 0])
co2_years = years[:-1]
co2_vals = [v for v in co2_mt if v is not None]
colors_co2 = ["#e74c3c" if v > 240 else "#f39c12" if v > 230 else "#27ae60" for v in co2_vals]
bars = ax4.bar(co2_years, co2_vals, color=colors_co2, edgecolor="white", width=0.65, alpha=0.8)
for yr, v in zip(co2_years, co2_vals):
    ax4.text(yr, v + 2, str(v), ha="center", fontsize=7.5, fontweight="bold")
ax4.set_title("(d) CO2 Emissions from Energy (Mt CO2)", fontweight="bold")
ax4.set_ylabel("Mt CO2")
ax4.set_ylim(200, 270)
ax4.axhline(y=130, color="#27ae60", ls="--", alpha=0.5, lw=1)
ax4.text(2024.5, 133, "2060 target\n(~50% reduction)", fontsize=6.5, color="#27ae60", ha="right")


# ── PANEL 5: Energy Intensity ────────────────────────────────
ax5 = fig.add_subplot(gs[2, 1])
int_years = years[:-1]
int_vals = [v for v in intensity if v is not None]
ax5.plot(int_years, int_vals, "o-", color="#8e44ad", markersize=7, linewidth=2.5)
ax5.fill_between(int_years, int_vals, alpha=0.12, color="#8e44ad")
ax5.axhline(0.107, color="#3498db", ls=":", alpha=0.6, lw=1.5)
ax5.text(2024.3, 0.112, "EU-27 avg\n(0.107)", fontsize=7, color="#3498db")
ax5.set_title("(e) Energy Intensity (toe/1000 USD GDP)", fontweight="bold")
ax5.set_ylabel("toe per 1,000 USD (2015 prices)")
ax5.set_ylim(0.05, 0.40)
ax5.annotate("3x EU average", xy=(2022, 0.32), xytext=(2019, 0.37),
             fontsize=8, color="#8e44ad",
             arrowprops=dict(arrowstyle="->", color="#8e44ad"))


# ── PANEL 6: Capacity Mix (Pie) 2024 ─────────────────────────
ax6 = fig.add_subplot(gs[2, 2])
labels_pie = ["Coal\n(14.0 GW)", "Natural Gas\n(6.7 GW)", "Large Hydro\n(2.25 GW)",
              "Wind+Solar+\nSmall Hydro\n(3.08 GW)", "Oil & Other\n(0.6 GW)"]
sizes = [14.0, 6.7, 2.25, 3.08, 0.6]
colors_pie = ["#2c3e50", "#95a5a6", "#3498db", "#27ae60", "#e67e22"]
explode = (0, 0, 0, 0.08, 0)
wedges, texts, autotexts = ax6.pie(sizes, labels=labels_pie, autopct="%1.1f%%",
                                    colors=colors_pie, explode=explode,
                                    textprops={"fontsize": 7},
                                    pctdistance=0.8, startangle=140)
for t in autotexts:
    t.set_fontsize(7)
    t.set_fontweight("bold")
ax6.set_title("(f) Total Installed Capacity Mix (2024)\nTotal: 24.6 GW", fontweight="bold")


# ── PANEL 7: Cross-border Trade ──────────────────────────────
ax7 = fig.add_subplot(gs[3, 0])
trade_years = [2021, 2022, 2023]
ru_export = [2854, 1502, 1377]
ru_import = [1456, 2100, 4994]
kg_export = [467, 300, 200]
kg_import = [63, 50, 80]

x_t = np.arange(len(trade_years))
w = 0.2
ax7.bar(x_t - w*1.5, ru_export, w, label="Export to Russia", color="#2c3e50", alpha=0.8)
ax7.bar(x_t - w*0.5, ru_import, w, label="Import from Russia", color="#e74c3c", alpha=0.8)
ax7.bar(x_t + w*0.5, kg_export, w, label="Export to Kyrgyzstan", color="#3498db", alpha=0.8)
ax7.bar(x_t + w*1.5, kg_import, w, label="Import from Kyrgyzstan", color="#f39c12", alpha=0.8)
ax7.set_xticks(x_t)
ax7.set_xticklabels(trade_years)
ax7.set_title("(g) Cross-Border Electricity Trade (GWh)", fontweight="bold")
ax7.set_ylabel("GWh")
ax7.legend(fontsize=6.5, loc="upper left")
ax7.annotate("Russian imports\ntriple in 2023", xy=(2, 4994), xytext=(0.5, 4500),
             fontsize=7, color="#e74c3c",
             arrowprops=dict(arrowstyle="->", color="#e74c3c"))


# ── PANEL 8: RES Capacity Needed for 2030 ────────────────────
ax8 = fig.add_subplot(gs[3, 1])
current = 3586
needed_2030 = 7700
gap = needed_2030 - current
added_2025 = 503
years_left = 5
annual_needed = gap / years_left

categories = ["Current\n(2025)", "Added\n(2025)", "Annual\nNeeded", "Gap to\n2030", "Target\n(2030)"]
values = [current, added_2025, annual_needed, gap, needed_2030]
colors_bar = ["#27ae60", "#1abc9c", "#f39c12", "#e74c3c", "#2c3e50"]

bars = ax8.barh(categories, values, color=colors_bar, edgecolor="white", height=0.5, alpha=0.85)
for bar, v in zip(bars, values):
    ax8.text(bar.get_width() + 50, bar.get_y() + bar.get_height()/2,
             f"{v:,.0f} MW", va="center", fontsize=8, fontweight="bold")

ax8.set_title("(h) RES Capacity: Current vs 2030 Target", fontweight="bold")
ax8.set_xlabel("MW")
ax8.set_xlim(0, 9000)


# ── PANEL 9: Timeline / Roadmap ──────────────────────────────
ax9 = fig.add_subplot(gs[3, 2])
ax9.axis("off")

milestones = [
    (0.95, "2018", "1,122 MW RES\n1.3% share", "#95a5a6"),
    (0.80, "2020", "1,635 MW RES\n3.0% share", "#3498db"),
    (0.65, "2023", "NET IMPORTER\n2,904 MW RES, 5.9%", "#e74c3c"),
    (0.50, "2025", "3,586 MW RES\n7.0%, 148 facilities", "#27ae60"),
    (0.35, "2030", "TARGET: 15%\n~7,700 MW needed", "#f39c12"),
    (0.20, "2050", "TARGET: 50%\nMajor grid overhaul", "#e67e22"),
    (0.05, "2060", "CARBON\nNEUTRALITY", "#2c3e50"),
]

# Draw timeline line
ax9.plot([0.15, 0.15], [0.0, 1.0], color="#bdc3c7", lw=3,
         transform=ax9.transAxes, zorder=1)

for y, year, text, color in milestones:
    ax9.plot(0.15, y, "o", color=color, markersize=12, zorder=5,
             transform=ax9.transAxes)
    ax9.text(0.22, y, f"{year}", transform=ax9.transAxes,
             fontsize=10, fontweight="bold", va="center", color=color)
    ax9.text(0.38, y, text, transform=ax9.transAxes,
             fontsize=7.5, va="center", color="#2c3e50",
             bbox=dict(boxstyle="round,pad=0.2", facecolor=color, alpha=0.08))

ax9.set_title("(i) Energy Transition Roadmap", fontweight="bold")


# ── FOOTER ────────────────────────────────────────────────────
fig.text(0.5, 0.01,
         "Sources: KEGOC AR2023, Ministry of Energy RK, QazaqGreen, stat.gov.kz, "
         "Astana Times, IEA, IRENA, UNFCCC | Data: 2018-2025",
         ha="center", fontsize=8, color="#95a5a6", style="italic")

plt.savefig(OUT / "fig17_infographic.png", bbox_inches="tight", facecolor="white")
plt.savefig(OUT / "fig17_infographic.pdf", bbox_inches="tight", facecolor="white")
plt.close()
print("Infographic created: fig17_infographic.png/pdf")
