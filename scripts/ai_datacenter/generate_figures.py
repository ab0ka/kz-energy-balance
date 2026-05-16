#!/usr/bin/env python3
"""
Generate all figures for the IJDIGIT paper:
"AI Data Center Infrastructure in Kazakhstan: Technical Resource Analysis and Site Optimization"
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import os

OUT = os.path.join(os.path.dirname(__file__), 'figures')
os.makedirs(OUT, exist_ok=True)

# Global style
plt.rcParams.update({
    'font.family': 'serif',
    'font.size': 10,
    'axes.titlesize': 11,
    'axes.labelsize': 10,
    'xtick.labelsize': 9,
    'ytick.labelsize': 9,
    'legend.fontsize': 9,
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'savefig.bbox': 'tight',
    'axes.grid': True,
    'grid.alpha': 0.3,
})

COLORS = {
    'primary': '#1a5276',
    'secondary': '#2980b9',
    'accent': '#e74c3c',
    'green': '#27ae60',
    'orange': '#f39c12',
    'purple': '#8e44ad',
    'gray': '#7f8c8d',
    'light_blue': '#85c1e9',
    'dark_green': '#1e8449',
}

# ============================================================
# Fig 1: Global Data Center Energy Consumption (2020–2030)
# ============================================================
def fig1_global_dc_energy():
    years = [2020, 2022, 2024, 2026, 2028, 2030]
    total_twh = [250, 330, 415, 650, 800, 945]
    ai_share = [10, 30, 60, 195, 320, 472]  # approximate AI portion

    fig, ax = plt.subplots(figsize=(7, 4.2))
    x = np.arange(len(years))
    w = 0.38
    bars1 = ax.bar(x - w/2, total_twh, w, label='Total Data Center', color=COLORS['primary'], edgecolor='white', linewidth=0.5)
    bars2 = ax.bar(x + w/2, ai_share, w, label='AI Workloads', color=COLORS['accent'], edgecolor='white', linewidth=0.5)

    for bar in bars1:
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 12, f'{int(bar.get_height())}',
                ha='center', va='bottom', fontsize=8, fontweight='bold')
    for bar in bars2:
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 12, f'{int(bar.get_height())}',
                ha='center', va='bottom', fontsize=8, fontweight='bold', color=COLORS['accent'])

    ax.set_xlabel('Year')
    ax.set_ylabel('Energy Consumption (TWh)')
    ax.set_title('Global Data Center Energy Consumption Forecast')
    ax.set_xticks(x)
    ax.set_xticklabels(years)
    ax.legend(loc='upper left')
    ax.set_ylim(0, 1100)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    # Add annotation
    ax.annotate('CAGR ~15%', xy=(4, 800), xytext=(3.2, 950),
                arrowprops=dict(arrowstyle='->', color=COLORS['gray']),
                fontsize=9, color=COLORS['gray'], fontstyle='italic')

    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'fig1_global_dc_energy.png'))
    plt.close(fig)
    print('Fig 1 done')


# ============================================================
# Fig 2: Rack Power Density Evolution
# ============================================================
def fig2_rack_density():
    categories = ['Traditional\nCPU\n(2015)', 'Dense CPU\n(2020)', 'GPU\nA100\n(2022)',
                   'GPU\nH100\n(2023)', 'GPU\nGB200\n(2025)', 'Projected\n(2027)']
    densities = [7, 15, 30, 70, 132, 500]
    colors_list = [COLORS['gray'], COLORS['gray'], COLORS['secondary'],
                   COLORS['primary'], COLORS['accent'], COLORS['purple']]

    fig, ax = plt.subplots(figsize=(7, 4.2))
    bars = ax.bar(categories, densities, color=colors_list, edgecolor='white', linewidth=0.5, width=0.65)

    for bar, val in zip(bars, densities):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 8,
                f'{val} kW', ha='center', va='bottom', fontsize=9, fontweight='bold')

    ax.set_ylabel('Power Density (kW/rack)')
    ax.set_title('Evolution of Data Center Rack Power Density')
    ax.set_ylim(0, 600)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    # Add horizontal line for traditional DC design threshold
    ax.axhline(y=15, color=COLORS['gray'], linestyle='--', alpha=0.5, linewidth=1)
    ax.text(5.5, 18, 'Traditional DC\ndesign limit', fontsize=7, color=COLORS['gray'],
            ha='right', va='bottom', fontstyle='italic')

    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'fig2_rack_density.png'))
    plt.close(fig)
    print('Fig 2 done')


# ============================================================
# Fig 3: Astana Climate Profile & Free Cooling Window
# ============================================================
def fig3_climate_profile():
    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
              'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    avg_high = [-11.6, -10.1, -2.2, 11.5, 21.4, 25.9, 25.6, 24.8, 18.6, 8.7, -2.2, -8.0]
    avg_low = [-18.3, -18.0, -9.9, 0.4, 8.1, 13.6, 15.1, 13.1, 6.9, -0.1, -8.3, -15.9]
    avg_mean = [(-15+(-14)+(-6)+6+15+20+20+19+13+4+(-5)+(-12))[i] if False else v
                for i, v in enumerate([-15, -14, -6, 6, 15, 20, 20, 19, 13, 4, -5, -12])]

    x = np.arange(len(months))

    fig, ax = plt.subplots(figsize=(7, 4.5))

    # Free cooling zone (below 10°C)
    ax.fill_between(x, -25, 10, alpha=0.12, color=COLORS['secondary'], label='Free Cooling Zone (<10°C)')

    # Temperature bands
    ax.fill_between(x, avg_low, avg_high, alpha=0.25, color=COLORS['accent'], label='Temperature Range')
    ax.plot(x, avg_mean, '-o', color=COLORS['primary'], linewidth=2, markersize=5, label='Mean Temperature', zorder=5)
    ax.plot(x, avg_high, '--', color=COLORS['accent'], linewidth=1, alpha=0.7)
    ax.plot(x, avg_low, '--', color=COLORS['secondary'], linewidth=1, alpha=0.7)

    # Threshold lines
    ax.axhline(y=10, color=COLORS['green'], linestyle='-.', linewidth=1.5, alpha=0.8)
    ax.text(11.5, 11, '10°C threshold', fontsize=8, color=COLORS['green'], ha='right')

    ax.axhline(y=27, color=COLORS['accent'], linestyle='-.', linewidth=1.2, alpha=0.6)
    ax.text(11.5, 28, 'ASHRAE A1 limit (27°C)', fontsize=8, color=COLORS['accent'], ha='right')

    # Annotate free cooling months
    ax.annotate('Free cooling:\nOct–Apr\n(~7 months)', xy=(0, -15), xytext=(1.5, -22),
                fontsize=9, color=COLORS['primary'], fontweight='bold',
                bbox=dict(boxstyle='round,pad=0.3', facecolor='white', edgecolor=COLORS['primary'], alpha=0.8))

    ax.set_xlabel('Month')
    ax.set_ylabel('Temperature (°C)')
    ax.set_title('Astana Annual Climate Profile and Free Cooling Potential')
    ax.set_xticks(x)
    ax.set_xticklabels(months)
    ax.set_ylim(-25, 35)
    ax.legend(loc='upper right', fontsize=8)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'fig3_climate_profile.png'))
    plt.close(fig)
    print('Fig 3 done')


# ============================================================
# Fig 4: Monthly PUE Estimation for Astana
# ============================================================
def fig4_pue_monthly():
    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
              'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    # PUE model: free cooling when <10°C → PUE ≈ 1.04-1.06
    # partial cooling 10-20°C → PUE ≈ 1.10-1.20
    # mechanical cooling >20°C → PUE ≈ 1.20-1.30
    pue_astana = [1.04, 1.04, 1.05, 1.08, 1.18, 1.25, 1.26, 1.24, 1.15, 1.06, 1.05, 1.04]
    pue_us_avg = [1.45] * 12
    pue_nordic = [1.08, 1.07, 1.08, 1.10, 1.12, 1.15, 1.16, 1.15, 1.12, 1.10, 1.08, 1.07]

    x = np.arange(len(months))

    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(x, pue_astana, '-o', color=COLORS['primary'], linewidth=2.5, markersize=6, label='Astana (modeled)')
    ax.plot(x, pue_nordic, '-s', color=COLORS['green'], linewidth=1.5, markersize=5, label='Nordic benchmark')
    ax.axhline(y=1.45, color=COLORS['accent'], linestyle='--', linewidth=1.5, label='US industry avg. (1.45)')
    ax.axhline(y=1.58, color=COLORS['gray'], linestyle=':', linewidth=1, label='Global avg. (1.58)')

    # Shade the mechanical cooling months
    ax.axvspan(4.5, 7.5, alpha=0.08, color=COLORS['accent'])
    ax.text(6, 1.32, 'Mechanical\ncooling\nrequired', fontsize=7, color=COLORS['accent'],
            ha='center', fontstyle='italic')

    # Annotate annual PUE
    annual_pue = np.mean(pue_astana)
    ax.annotate(f'Annual avg PUE: {annual_pue:.2f}', xy=(0, annual_pue), xytext=(2, 1.33),
                arrowprops=dict(arrowstyle='->', color=COLORS['primary']),
                fontsize=9, color=COLORS['primary'], fontweight='bold',
                bbox=dict(boxstyle='round', facecolor='white', edgecolor=COLORS['primary']))

    ax.set_xlabel('Month')
    ax.set_ylabel('Power Usage Effectiveness (PUE)')
    ax.set_title('Monthly PUE Estimation: Astana vs. Benchmarks')
    ax.set_xticks(x)
    ax.set_xticklabels(months)
    ax.set_ylim(1.0, 1.65)
    ax.legend(loc='upper right', fontsize=8)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'fig4_pue_monthly.png'))
    plt.close(fig)
    print('Fig 4 done')


# ============================================================
# Fig 5: Electricity OPEX Comparison (35 MW Facility)
# ============================================================
def fig5_opex_comparison():
    regions = ['Germany', 'US\n(Avg.)', 'Singapore', 'Kazakhstan\n(Grid)', 'Kazakhstan\n(Direct)',
               'Iceland']
    tariffs = [0.180, 0.083, 0.120, 0.076, 0.031, 0.045]
    annual_cost = [t * 35000 * 8760 / 1e6 for t in tariffs]  # M$/year

    fig, ax1 = plt.subplots(figsize=(7, 4.2))

    colors_list = [COLORS['gray'], COLORS['gray'], COLORS['gray'],
                   COLORS['secondary'], COLORS['accent'], COLORS['gray']]
    bars = ax1.bar(regions, annual_cost, color=colors_list, edgecolor='white', width=0.6)

    for bar, cost in zip(bars, annual_cost):
        ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.5,
                f'${cost:.1f}M', ha='center', va='bottom', fontsize=9, fontweight='bold')

    # Add tariff labels inside bars
    for bar, t in zip(bars, tariffs):
        ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height()/2,
                f'${t:.3f}/kWh', ha='center', va='center', fontsize=7, color='white', fontweight='bold')

    ax1.set_ylabel('Annual Electricity Cost (M$ USD)')
    ax1.set_title('Annual Electricity OPEX for a 35 MW AI Data Center Facility')
    ax1.spines['top'].set_visible(False)
    ax1.spines['right'].set_visible(False)
    ax1.set_ylim(0, 65)

    # Add savings annotation
    us_cost = annual_cost[1]
    kz_direct = annual_cost[4]
    savings_pct = (1 - kz_direct / us_cost) * 100
    ax1.annotate(f'{savings_pct:.0f}% savings\nvs. US avg.',
                xy=(4, kz_direct), xytext=(4.7, 20),
                arrowprops=dict(arrowstyle='->', color=COLORS['accent'], lw=1.5),
                fontsize=10, color=COLORS['accent'], fontweight='bold',
                bbox=dict(boxstyle='round', facecolor='white', edgecolor=COLORS['accent']))

    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'fig5_opex_comparison.png'))
    plt.close(fig)
    print('Fig 5 done')


# ============================================================
# Fig 6: Water Consumption by Cooling Technology
# ============================================================
def fig6_water_consumption():
    technologies = ['Evaporative\nCooling Tower', 'Hybrid\n(Air+Evap)', 'Air-Cooled\nChiller',
                    'Direct-to-Chip\nLiquid', 'Immersion\nCooling']
    water_m3 = [900000, 350000, 5000, 1000, 200]  # m³/year for 35 MW

    fig, ax = plt.subplots(figsize=(7, 4.2))

    colors_list = [COLORS['accent'], COLORS['orange'], COLORS['secondary'],
                   COLORS['green'], COLORS['dark_green']]
    bars = ax.barh(technologies, water_m3, color=colors_list, edgecolor='white', height=0.55)

    for bar, val in zip(bars, water_m3):
        if val > 50000:
            ax.text(bar.get_width() + 15000, bar.get_y() + bar.get_height()/2,
                    f'{val:,.0f} m³', va='center', fontsize=9, fontweight='bold')
        else:
            ax.text(max(bar.get_width() + 15000, 40000), bar.get_y() + bar.get_height()/2,
                    f'{val:,.0f} m³', va='center', fontsize=9, fontweight='bold')

    ax.set_xlabel('Annual Water Consumption (m³/year) — 35 MW Facility')
    ax.set_title('Water Consumption by Cooling Technology')
    ax.set_xscale('log')
    ax.set_xlim(100, 2000000)
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

    # Add Kazakhstan threshold
    ax.axvline(x=100000, color=COLORS['accent'], linestyle='--', linewidth=1.5, alpha=0.7)
    ax.text(110000, 4.3, 'Sustainability\nthreshold for\nKazakhstan', fontsize=7,
            color=COLORS['accent'], fontstyle='italic')

    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'fig6_water_consumption.png'))
    plt.close(fig)
    print('Fig 6 done')


# ============================================================
# Fig 7: Kazakhstan Electricity Generation Mix (2024)
# ============================================================
def fig7_energy_mix():
    labels = ['Coal', 'Natural Gas', 'Hydropower', 'Wind', 'Solar', 'Other RES']
    sizes = [54, 29, 10, 4, 2, 1]
    colors_list = [COLORS['gray'], COLORS['orange'], COLORS['secondary'],
                   COLORS['green'], COLORS['accent'], COLORS['purple']]
    explode = (0.03, 0.03, 0.03, 0.06, 0.06, 0.06)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8, 4))

    # Current mix (2024)
    wedges1, texts1, autotexts1 = ax1.pie(sizes, labels=labels, autopct='%1.0f%%',
        colors=colors_list, explode=explode, startangle=90,
        pctdistance=0.82, textprops={'fontsize': 8})
    for at in autotexts1:
        at.set_fontsize(8)
        at.set_fontweight('bold')
    ax1.set_title('2024 (Current)', fontsize=11, fontweight='bold')

    # Projected 2035 (from Zhakiyev PyPSA-KZ, 30% RES scenario)
    sizes_2035 = [35, 25, 10, 15, 12, 3]
    wedges2, texts2, autotexts2 = ax2.pie(sizes_2035, labels=labels, autopct='%1.0f%%',
        colors=colors_list, explode=explode, startangle=90,
        pctdistance=0.82, textprops={'fontsize': 8})
    for at in autotexts2:
        at.set_fontsize(8)
        at.set_fontweight('bold')
    ax2.set_title('2035 (30% RES Scenario)', fontsize=11, fontweight='bold')

    fig.suptitle('Kazakhstan Electricity Generation Mix', fontsize=12, fontweight='bold', y=1.02)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'fig7_energy_mix.png'))
    plt.close(fig)
    print('Fig 7 done')


# ============================================================
# Fig 8: Regional Site Selection Matrix (Radar Chart)
# ============================================================
def fig8_site_selection():
    categories = ['PUE\nEfficiency', 'Power\nCost', 'Water\nAccess',
                  'Network\nConnectivity', 'Renewable\nPotential']
    N = len(categories)

    # Scores out of 10
    astana = [9, 6, 7, 9, 7]
    pavlodar = [8, 10, 6, 5, 5]
    almaty = [5, 5, 5, 8, 8]

    angles = np.linspace(0, 2 * np.pi, N, endpoint=False).tolist()
    # Close the plot
    astana += astana[:1]
    pavlodar += pavlodar[:1]
    almaty += almaty[:1]
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(6, 5.5), subplot_kw=dict(polar=True))

    ax.plot(angles, astana, 'o-', linewidth=2, color=COLORS['primary'], label='Astana', markersize=6)
    ax.fill(angles, astana, alpha=0.15, color=COLORS['primary'])

    ax.plot(angles, pavlodar, 's-', linewidth=2, color=COLORS['accent'], label='Pavlodar/Ekibastuz', markersize=6)
    ax.fill(angles, pavlodar, alpha=0.1, color=COLORS['accent'])

    ax.plot(angles, almaty, '^-', linewidth=2, color=COLORS['green'], label='Almaty', markersize=6)
    ax.fill(angles, almaty, alpha=0.1, color=COLORS['green'])

    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(categories, fontsize=9)
    ax.set_ylim(0, 10)
    ax.set_yticks([2, 4, 6, 8, 10])
    ax.set_yticklabels(['2', '4', '6', '8', '10'], fontsize=7)
    ax.set_title('Regional Site Selection Matrix\nfor AI Data Center Deployment', fontsize=11, fontweight='bold', y=1.08)
    ax.legend(loc='lower right', bbox_to_anchor=(1.25, -0.05), fontsize=9)

    fig.tight_layout()
    fig.savefig(os.path.join(OUT, 'fig8_site_selection.png'))
    plt.close(fig)
    print('Fig 8 done')


# ============================================================
# Run all
# ============================================================
if __name__ == '__main__':
    fig1_global_dc_energy()
    fig2_rack_density()
    fig3_climate_profile()
    fig4_pue_monthly()
    fig5_opex_comparison()
    fig6_water_consumption()
    fig7_energy_mix()
    fig8_site_selection()
    print('\nAll figures generated successfully!')
