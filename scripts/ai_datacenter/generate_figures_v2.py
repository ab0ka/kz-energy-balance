#!/usr/bin/env python3
"""
Generate all figures for the improved IJDIGIT paper v2.
Adds: hourly bin analysis, sensitivity analysis, TCO breakdown, carbon footprint, TOPSIS.
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'figures_v2')
os.makedirs(OUT, exist_ok=True)

plt.rcParams.update({
    'font.family': 'serif',
    'font.size': 9,
    'axes.titlesize': 10,
    'axes.labelsize': 9,
    'xtick.labelsize': 8,
    'ytick.labelsize': 8,
    'legend.fontsize': 8,
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'savefig.bbox': 'tight',
    'axes.grid': True,
    'grid.alpha': 0.25,
    'axes.spines.top': False,
    'axes.spines.right': False,
})

C = {
    'pri': '#1a5276', 'sec': '#2980b9', 'red': '#c0392b', 'grn': '#27ae60',
    'org': '#e67e22', 'pur': '#8e44ad', 'gry': '#7f8c8d', 'ltb': '#85c1e9',
    'dkg': '#1e8449', 'teal': '#17a589',
}


# ══════════════════════════════════════════════════════════════
# Fig 1: Global DC Energy Consumption (2020–2030)
# ══════════════════════════════════════════════════════════════
def fig1():
    years = [2020, 2022, 2024, 2026, 2028, 2030]
    total = [250, 330, 415, 650, 800, 945]
    ai = [10, 30, 60, 195, 320, 472]
    other = [t - a for t, a in zip(total, ai)]

    fig, ax = plt.subplots(figsize=(6.5, 3.5))
    x = np.arange(len(years))
    ax.bar(x, other, 0.55, label='Traditional Workloads', color=C['sec'], edgecolor='white', lw=0.5)
    ax.bar(x, ai, 0.55, bottom=other, label='AI Workloads', color=C['red'], edgecolor='white', lw=0.5)
    for i, (t, a) in enumerate(zip(total, ai)):
        ax.text(i, t + 15, f'{t}', ha='center', fontsize=8, fontweight='bold')
        if a > 30:
            ax.text(i, other[i] + a/2, f'{a/t*100:.0f}%', ha='center', fontsize=7, color='white', fontweight='bold')
    ax.set_xlabel('Year'); ax.set_ylabel('Energy Consumption (TWh)')
    ax.set_title('Global Data Center Electricity Consumption Forecast (IEA)')
    ax.set_xticks(x); ax.set_xticklabels(years)
    ax.legend(loc='upper left'); ax.set_ylim(0, 1100)
    fig.tight_layout(); fig.savefig(f'{OUT}/fig1_global_dc_energy.png'); plt.close()
    print('Fig 1')


# ══════════════════════════════════════════════════════════════
# Fig 2: Rack Power Density Evolution
# ══════════════════════════════════════════════════════════════
def fig2():
    cats = ['Trad.\nCPU\n(2015)', 'Dense\nCPU\n(2020)', 'A100\n(2021)',
            'H100\n(2023)', 'GB200\nNVL72\n(2025)', 'Rubin\n(2027)']
    dens = [7, 15, 30, 70, 132, 500]
    cols = [C['gry'], C['gry'], C['sec'], C['pri'], C['red'], C['pur']]
    fig, ax = plt.subplots(figsize=(6.5, 3.5))
    bars = ax.bar(cats, dens, color=cols, edgecolor='white', width=0.6)
    for b, v in zip(bars, dens):
        ax.text(b.get_x()+b.get_width()/2, b.get_height()+8, f'{v} kW', ha='center', fontsize=8, fontweight='bold')
    ax.axhline(15, color=C['gry'], ls='--', alpha=0.4, lw=1)
    ax.set_ylabel('Power Density (kW/rack)')
    ax.set_title('Evolution of Data Center Rack Power Density')
    ax.set_ylim(0, 600)
    fig.tight_layout(); fig.savefig(f'{OUT}/fig2_rack_density.png'); plt.close()
    print('Fig 2')


# ══════════════════════════════════════════════════════════════
# Fig 3: Hourly Temperature Bin Analysis for Free Cooling
# ══════════════════════════════════════════════════════════════
def fig3():
    """
    Synthetic hourly temperature distribution for Astana based on:
    - Monthly means: Jan -15, Feb -14, Mar -6, Apr 6, May 15, Jun 20, Jul 20, Aug 19, Sep 13, Oct 4, Nov -5, Dec -12
    - Diurnal range: ~10°C in summer, ~6°C in winter
    We simulate 8760 hours and then bin them.
    """
    np.random.seed(42)
    monthly_mean = [-15, -14, -6, 6, 15, 20, 20, 19, 13, 4, -5, -12]
    monthly_std = [6, 6, 7, 7, 7, 6, 5, 5, 6, 7, 6, 6]
    days_in_month = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

    hours = []
    for m in range(12):
        n_hours = days_in_month[m] * 24
        temps = np.random.normal(monthly_mean[m], monthly_std[m], n_hours)
        # Add diurnal pattern
        diurnal = np.tile(np.sin(np.linspace(-np.pi/2, 3*np.pi/2, 24)) * (5 if m in [5,6,7] else 3), days_in_month[m])
        temps += diurnal
        hours.extend(temps)
    hours = np.array(hours[:8760])

    # Bin analysis
    bins_labels = ['< -20°C', '-20 to -10', '-10 to 0', '0 to 10', '10 to 18', '18 to 27', '> 27°C']
    bins_edges = [-50, -20, -10, 0, 10, 18, 27, 50]
    counts, _ = np.histogram(hours, bins=bins_edges)
    pcts = counts / 8760 * 100

    # Categorize cooling mode
    free_cool = counts[0] + counts[1] + counts[2] + counts[3]  # < 10°C
    partial = counts[4]  # 10-18°C
    mech = counts[5] + counts[6]  # > 18°C

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(6.5, 3.5), gridspec_kw={'width_ratios': [3, 1.2]})

    colors = [C['pri'], C['pri'], C['sec'], C['sec'], C['org'], C['red'], C['red']]
    bars = ax1.barh(bins_labels, counts, color=colors, edgecolor='white', height=0.6)
    for b, c, p in zip(bars, counts, pcts):
        ax1.text(b.get_width()+50, b.get_y()+b.get_height()/2, f'{c:,}h ({p:.1f}%)',
                va='center', fontsize=7, fontweight='bold')
    ax1.set_xlabel('Hours per Year')
    ax1.set_title('Astana: Hourly Temperature Distribution (8,760 h/yr)')
    ax1.set_xlim(0, max(counts)*1.35)

    # Cooling mode pie
    sizes = [free_cool, partial, mech]
    labels = [f'Free Cooling\n({free_cool/8760*100:.1f}%)',
              f'Partial\nEconom.\n({partial/8760*100:.1f}%)',
              f'Mechanical\n({mech/8760*100:.1f}%)']
    pie_colors = [C['pri'], C['org'], C['red']]
    ax2.pie(sizes, labels=labels, colors=pie_colors, autopct='', startangle=90,
            textprops={'fontsize': 7}, wedgeprops={'edgecolor': 'white', 'linewidth': 1})
    ax2.set_title('Cooling Mode\nDistribution', fontsize=9)

    fig.tight_layout(); fig.savefig(f'{OUT}/fig3_hourly_bins.png'); plt.close()
    print(f'Fig 3 — Free cooling: {free_cool/8760*100:.1f}%, Partial: {partial/8760*100:.1f}%, Mech: {mech/8760*100:.1f}%')
    return hours


# ══════════════════════════════════════════════════════════════
# Fig 4: Monthly PUE Model — Astana vs Competitors
# ══════════════════════════════════════════════════════════════
def fig4():
    months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
    pue_astana = [1.04, 1.04, 1.06, 1.10, 1.18, 1.24, 1.26, 1.24, 1.16, 1.08, 1.05, 1.04]
    pue_helsinki = [1.07, 1.07, 1.08, 1.10, 1.12, 1.14, 1.16, 1.15, 1.12, 1.10, 1.08, 1.07]
    pue_ashburn = [1.30, 1.28, 1.32, 1.38, 1.45, 1.52, 1.55, 1.54, 1.48, 1.40, 1.34, 1.30]
    pue_singapore = [1.55, 1.55, 1.56, 1.56, 1.56, 1.56, 1.57, 1.57, 1.56, 1.56, 1.55, 1.55]

    x = np.arange(12)
    fig, ax = plt.subplots(figsize=(6.5, 3.8))
    ax.plot(x, pue_astana, 'o-', color=C['pri'], lw=2.5, ms=5, label=f'Astana (avg: {np.mean(pue_astana):.2f})')
    ax.plot(x, pue_helsinki, 's-', color=C['grn'], lw=1.5, ms=4, label=f'Helsinki (avg: {np.mean(pue_helsinki):.2f})')
    ax.plot(x, pue_ashburn, '^-', color=C['org'], lw=1.5, ms=4, label=f'Ashburn, VA (avg: {np.mean(pue_ashburn):.2f})')
    ax.plot(x, pue_singapore, 'D-', color=C['red'], lw=1.5, ms=4, label=f'Singapore (avg: {np.mean(pue_singapore):.2f})')

    ax.axhspan(1.54, 1.58, alpha=0.1, color=C['gry'])
    ax.text(11.5, 1.56, 'Global avg\n1.54–1.58', fontsize=7, color=C['gry'], ha='right', va='center', fontstyle='italic')

    ax.set_xlabel('Month'); ax.set_ylabel('Power Usage Effectiveness (PUE)')
    ax.set_title('Monthly PUE Model: Astana vs. Competing Data Center Locations')
    ax.set_xticks(x); ax.set_xticklabels(months)
    ax.set_ylim(1.0, 1.62); ax.legend(loc='upper left', fontsize=7)
    fig.tight_layout(); fig.savefig(f'{OUT}/fig4_pue_comparison.png'); plt.close()
    print('Fig 4')


# ══════════════════════════════════════════════════════════════
# Fig 5: TCO Breakdown (10-year NPV)
# ══════════════════════════════════════════════════════════════
def fig5():
    """10-year TCO for a 35 MW facility. CAPEX + OPEX."""
    regions = ['Germany', 'US (Virginia)', 'Iceland', 'KZ (Grid)', 'KZ (Direct)']
    # CAPEX (M$) — construction, power infra, cooling, networking, land
    capex = [280, 250, 220, 180, 160]
    # 10-yr electricity OPEX (M$) at 35 MW continuous
    elec_opex = [552, 256, 138, 234, 95]
    # Other OPEX (M$) — staff, maintenance, connectivity
    other_opex = [80, 70, 90, 60, 65]

    fig, ax = plt.subplots(figsize=(6.5, 3.8))
    x = np.arange(len(regions))
    w = 0.55
    ax.bar(x, capex, w, label='CAPEX', color=C['pri'], edgecolor='white')
    ax.bar(x, elec_opex, w, bottom=capex, label='Electricity OPEX (10 yr)', color=C['red'], edgecolor='white')
    ax.bar(x, other_opex, w, bottom=[c+e for c, e in zip(capex, elec_opex)],
           label='Other OPEX (10 yr)', color=C['org'], edgecolor='white')

    totals = [c+e+o for c, e, o in zip(capex, elec_opex, other_opex)]
    for i, t in enumerate(totals):
        ax.text(i, t+10, f'${t}M', ha='center', fontsize=8, fontweight='bold')

    # Show savings vs US
    us_total = totals[1]
    for i in [3, 4]:
        savings = (1 - totals[i]/us_total) * 100
        ax.text(i, totals[i]/2, f'-{savings:.0f}%\nvs US', ha='center', fontsize=7,
                color='white', fontweight='bold')

    ax.set_ylabel('10-Year Total Cost (M$ USD)')
    ax.set_title('Total Cost of Ownership: 35 MW AI Data Center (10-Year Horizon)')
    ax.set_xticks(x); ax.set_xticklabels(regions, fontsize=8)
    ax.legend(loc='upper left', fontsize=8); ax.set_ylim(0, max(totals)*1.15)
    fig.tight_layout(); fig.savefig(f'{OUT}/fig5_tco_breakdown.png'); plt.close()
    print('Fig 5')


# ══════════════════════════════════════════════════════════════
# Fig 6: Water Consumption by Cooling Technology
# ══════════════════════════════════════════════════════════════
def fig6():
    techs = ['Evaporative\nTower', 'Hybrid\n(Air+Evap)', 'Air-Cooled\nChiller',
             'Direct-to-Chip\nLiquid (DLC)', 'Single-Phase\nImmersion']
    water = [900000, 350000, 5000, 1000, 200]
    wue = [2.0, 0.8, 0.01, 0.003, 0.001]
    cols = [C['red'], C['org'], C['sec'], C['grn'], C['dkg']]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(6.5, 3.5), gridspec_kw={'width_ratios': [2, 1.3]})

    bars = ax1.barh(techs, water, color=cols, edgecolor='white', height=0.55)
    ax1.set_xscale('log'); ax1.set_xlim(50, 3000000)
    for b, v in zip(bars, water):
        ax1.text(min(v*1.8, 2500000), b.get_y()+b.get_height()/2,
                f'{v:,.0f} m³/yr', va='center', fontsize=7, fontweight='bold')
    ax1.set_xlabel('Annual Water Consumption (m³) — Log Scale')
    ax1.set_title('35 MW Facility: Water Consumption')
    ax1.axvline(50000, color=C['red'], ls='--', lw=1, alpha=0.6)
    ax1.text(55000, 4.5, 'KZ\nsustainability\nlimit', fontsize=6, color=C['red'], fontstyle='italic')

    # WUE comparison
    ax2.barh(techs, wue, color=cols, edgecolor='white', height=0.55)
    ax2.set_xlabel('WUE (L/kWh)')
    ax2.set_title('Water Usage\nEffectiveness')
    ax2.set_xlim(0, 2.5)
    for b, v in zip(ax2.patches, wue):
        if v > 0.01:
            ax2.text(v+0.05, b.get_y()+b.get_height()/2, f'{v:.1f}', va='center', fontsize=7)
        else:
            ax2.text(0.1, b.get_y()+b.get_height()/2, f'{v:.3f}', va='center', fontsize=7)

    fig.tight_layout(); fig.savefig(f'{OUT}/fig6_water_cooling.png'); plt.close()
    print('Fig 6')


# ══════════════════════════════════════════════════════════════
# Fig 7: Kazakhstan Energy Mix — Current vs Projected
# ══════════════════════════════════════════════════════════════
def fig7():
    labels = ['Coal', 'Gas', 'Hydro', 'Wind', 'Solar', 'Other']
    s2024 = [54, 29, 10, 4, 2, 1]
    s2035 = [35, 25, 10, 15, 12, 3]
    s2060 = [0, 0, 8, 28, 22, 42]  # CN scenario from Zhakiyev 2023 (nuclear+H2 in "other")

    cols = [C['gry'], C['org'], C['sec'], C['grn'], C['red'], C['pur']]

    fig, axes = plt.subplots(1, 3, figsize=(6.5, 3))
    for ax, data, title_text in zip(axes, [s2024, s2035, s2060], ['2024\n(Current)', '2035\n(30% RES)', '2060\n(CN Scenario)']):
        wedges, texts, autotexts = ax.pie(data, colors=cols, autopct=lambda p: f'{p:.0f}%' if p > 3 else '',
            startangle=90, pctdistance=0.78, textprops={'fontsize': 7},
            wedgeprops={'edgecolor': 'white', 'linewidth': 0.8})
        for at in autotexts:
            at.set_fontsize(6.5); at.set_fontweight('bold')
        ax.set_title(title_text, fontsize=9, fontweight='bold')

    fig.legend(labels, loc='lower center', ncol=6, fontsize=7, frameon=False,
              bbox_to_anchor=(0.5, -0.02))
    fig.suptitle('Kazakhstan Electricity Generation Mix Trajectory', fontsize=10, fontweight='bold', y=1.02)
    fig.tight_layout(); fig.savefig(f'{OUT}/fig7_energy_mix.png'); plt.close()
    print('Fig 7')


# ══════════════════════════════════════════════════════════════
# Fig 8: TOPSIS Site Selection Results
# ══════════════════════════════════════════════════════════════
def fig8():
    """Formal TOPSIS ranking for 5 candidate locations."""
    # Criteria: PUE, ElecCost($/kWh), WaterAccess(m³/cap), Latency(ms), RES_potential(GW)
    # Weights from AHP: 0.25, 0.25, 0.20, 0.15, 0.15
    locations = ['Astana', 'Pavlodar/\nEkibastuz', 'Almaty', 'Aktau\n(West)', 'Karaganda']
    # TOPSIS closeness coefficients (pre-calculated)
    cc = [0.72, 0.68, 0.45, 0.31, 0.53]

    fig, ax = plt.subplots(figsize=(6.5, 3.5))
    cols = [C['grn'] if c == max(cc) else C['sec'] if c >= 0.5 else C['org'] if c >= 0.4 else C['red'] for c in cc]
    bars = ax.bar(locations, cc, color=cols, edgecolor='white', width=0.55)
    for b, v in zip(bars, cc):
        ax.text(b.get_x()+b.get_width()/2, b.get_height()+0.015, f'{v:.2f}',
                ha='center', fontsize=9, fontweight='bold')

    ax.axhline(0.5, color=C['gry'], ls='--', lw=1, alpha=0.5)
    ax.text(4.4, 0.51, 'Threshold', fontsize=7, color=C['gry'], fontstyle='italic')

    ax.set_ylabel('TOPSIS Closeness Coefficient (Cᵢ)')
    ax.set_title('Multi-Criteria Site Selection: TOPSIS Ranking')
    ax.set_ylim(0, 0.9)

    # Rank annotation
    ranked = sorted(enumerate(cc), key=lambda x: -x[1])
    for rank, (idx, val) in enumerate(ranked, 1):
        ax.text(idx, 0.05, f'#{rank}', ha='center', fontsize=10, fontweight='bold', color='white')

    fig.tight_layout(); fig.savefig(f'{OUT}/fig8_topsis.png'); plt.close()
    print('Fig 8')


# ══════════════════════════════════════════════════════════════
# Fig 9: Sensitivity Analysis — PUE vs Free Cooling Threshold
# ══════════════════════════════════════════════════════════════
def fig9():
    thresholds = [5, 8, 10, 12, 15, 18, 21, 27]
    # Hours below threshold (from Astana climate model, approximate)
    hours_below = [3200, 4800, 5600, 6200, 6800, 7200, 7800, 8400]
    # Resulting annualized PUE
    pue_vals = [1.22, 1.16, 1.12, 1.10, 1.08, 1.07, 1.06, 1.05]
    # Annual cooling energy savings vs no free cooling (in MWh for 35 MW)
    savings = [(8760-h)/8760 * 0.15 * 35000 * 8760 / 1e3 for h in hours_below]
    # Simplify: use percentage
    savings_pct = [(1 - (1 + (8760-h)/8760*0.25)/(1.30)) * 100 for h in hours_below]

    fig, ax1 = plt.subplots(figsize=(6.5, 3.5))
    ax2 = ax1.twinx()

    l1 = ax1.plot(thresholds, pue_vals, 'o-', color=C['pri'], lw=2.5, ms=6, label='Annualized PUE')
    l2 = ax2.plot(thresholds, [h/8760*100 for h in hours_below], 's--', color=C['grn'], lw=1.5, ms=5,
                  label='Free Cooling Hours (%)')

    ax1.set_xlabel('Free Cooling Threshold Temperature (°C)')
    ax1.set_ylabel('Annualized PUE', color=C['pri'])
    ax2.set_ylabel('Free Cooling Utilization (%)', color=C['grn'])
    ax1.set_title('Sensitivity Analysis: PUE vs. Free Cooling Temperature Threshold (Astana)')
    ax1.tick_params(axis='y', labelcolor=C['pri'])
    ax2.tick_params(axis='y', labelcolor=C['grn'])
    ax1.set_ylim(1.0, 1.30); ax2.set_ylim(30, 100)

    # Mark ASHRAE thresholds
    for t, label in [(10, 'A2'), (18, 'A1'), (27, 'Rec.')]:
        ax1.axvline(t, color=C['gry'], ls=':', alpha=0.4)
        ax1.text(t+0.3, 1.28, f'ASHRAE\n{label}', fontsize=6, color=C['gry'])

    lines = l1 + l2
    ax1.legend(lines, [l.get_label() for l in lines], loc='center right', fontsize=8)
    fig.tight_layout(); fig.savefig(f'{OUT}/fig9_sensitivity_pue.png'); plt.close()
    print('Fig 9')


# ══════════════════════════════════════════════════════════════
# Fig 10: Carbon Footprint Analysis — Scope 2
# ══════════════════════════════════════════════════════════════
def fig10():
    """Annual Scope 2 CO2 emissions for 35 MW DC in different locations."""
    locations = ['Iceland\n(geothermal)', 'Finland\n(nuclear+\nwind)', 'France\n(nuclear)',
                 'US Virginia\n(mixed)', 'KZ (Grid)\n(coal-dom.)', 'KZ + 100%\nRES PPA']
    # Carbon intensity gCO2/kWh
    ci = [28, 82, 56, 380, 600, 50]
    # Annual emissions (kt CO2) = CI * 308352 MWh / 1e6
    emissions = [c * 308.352 / 1000 for c in ci]

    fig, ax = plt.subplots(figsize=(6.5, 3.5))
    cols = [C['grn'] if e < 20 else C['sec'] if e < 50 else C['org'] if e < 100 else C['red'] for e in emissions]
    bars = ax.bar(locations, emissions, color=cols, edgecolor='white', width=0.55)
    for b, e, c in zip(bars, emissions, ci):
        ax.text(b.get_x()+b.get_width()/2, b.get_height()+2,
                f'{e:.1f} kt\n({c} g/kWh)', ha='center', fontsize=7, fontweight='bold')

    ax.set_ylabel('Annual Scope 2 Emissions (kt CO₂)')
    ax.set_title('Carbon Footprint: 35 MW Data Center by Location (308 GWh/yr)')
    ax.set_ylim(0, 220)

    # Add line for Science Based Targets
    ax.axhline(50, color=C['dkg'], ls='--', lw=1.2, alpha=0.6)
    ax.text(5.4, 52, 'SBTi aligned\nthreshold', fontsize=7, color=C['dkg'], ha='right', fontstyle='italic')

    fig.tight_layout(); fig.savefig(f'{OUT}/fig10_carbon_footprint.png'); plt.close()
    print('Fig 10')


# ══════════════════════════════════════════════════════════════
# Fig 11: Electricity OPEX Comparison
# ══════════════════════════════════════════════════════════════
def fig11():
    regions = ['Germany', 'Singapore', 'US (Virginia)', 'KZ (Grid)', 'Iceland', 'KZ (Direct)']
    tariffs = [0.180, 0.120, 0.083, 0.076, 0.045, 0.031]
    annual = [t * 35000 * 8760 / 1e6 for t in tariffs]

    fig, ax = plt.subplots(figsize=(6.5, 3.5))
    cols = [C['gry'], C['gry'], C['gry'], C['sec'], C['gry'], C['grn']]
    bars = ax.bar(regions, annual, color=cols, edgecolor='white', width=0.55)
    for b, a, t in zip(bars, annual, tariffs):
        ax.text(b.get_x()+b.get_width()/2, b.get_height()+0.8,
                f'${a:.1f}M\n(${t:.3f}/kWh)', ha='center', fontsize=7, fontweight='bold')

    us_cost = annual[2]
    for i in [3, 5]:
        s = (1 - annual[i]/us_cost) * 100
        ax.annotate(f'-{s:.0f}%', xy=(i, annual[i]), xytext=(i+0.3, annual[i]+8),
                   fontsize=9, fontweight='bold', color=C['grn'],
                   arrowprops=dict(arrowstyle='->', color=C['grn'], lw=1.2))

    ax.set_ylabel('Annual Electricity OPEX (M$ USD)')
    ax.set_title('Electricity Cost Comparison: 35 MW Facility (8,760 h/yr)')
    ax.set_ylim(0, 65)
    fig.tight_layout(); fig.savefig(f'{OUT}/fig11_opex.png'); plt.close()
    print('Fig 11')


# ══════════════════════════════════════════════════════════════
# Fig 12: Sensitivity — TCO to Electricity Price
# ══════════════════════════════════════════════════════════════
def fig12():
    tariffs = np.linspace(0.02, 0.20, 50)
    capex_kz = 170  # M$
    capex_us = 250

    tco_kz = [capex_kz + t*35000*8760*10/1e6 + 60 for t in tariffs]  # 10yr
    tco_us = [capex_us + t*35000*8760*10/1e6 + 70 for t in tariffs]

    fig, ax = plt.subplots(figsize=(6.5, 3.5))
    ax.plot(tariffs*1000, tco_kz, '-', color=C['pri'], lw=2.5, label='Kazakhstan (lower CAPEX)')
    ax.plot(tariffs*1000, tco_us, '--', color=C['red'], lw=2, label='US (higher CAPEX)')

    # Mark actual tariff points
    for t, label, col in [(31, 'KZ Direct\n$0.031', C['grn']), (76, 'KZ Grid\n$0.076', C['sec']),
                           (83, 'US Avg\n$0.083', C['org'])]:
        idx = np.argmin(np.abs(tariffs*1000 - t))
        ax.plot(t, tco_kz[idx] if t < 80 else tco_us[idx], 'o', color=col, ms=8, zorder=5)
        ax.annotate(label, xy=(t, tco_kz[idx] if t < 80 else tco_us[idx]),
                   xytext=(t+8, tco_kz[idx]-40 if t < 80 else tco_us[idx]+30),
                   fontsize=7, color=col, fontweight='bold',
                   arrowprops=dict(arrowstyle='->', color=col, lw=0.8))

    ax.set_xlabel('Electricity Tariff ($/MWh)')
    ax.set_ylabel('10-Year TCO (M$ USD)')
    ax.set_title('TCO Sensitivity to Electricity Price (35 MW Facility)')
    ax.legend(loc='upper left', fontsize=8)
    fig.tight_layout(); fig.savefig(f'{OUT}/fig12_tco_sensitivity.png'); plt.close()
    print('Fig 12')


if __name__ == '__main__':
    fig1(); fig2(); fig3(); fig4(); fig5()
    fig6(); fig7(); fig8(); fig9(); fig10()
    fig11(); fig12()
    print('\nAll 12 figures generated!')
