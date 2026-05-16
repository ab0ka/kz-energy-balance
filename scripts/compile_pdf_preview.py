"""
Generate a preview PDF of the article with all figures.
For final Scopus submission, use Overleaf with main.tex.
This is a preview/draft version for local viewing.
"""
from fpdf import FPDF
from pathlib import Path
import os

ARTICLE_DIR = Path("C:/Users/Timing/Desktop/code/claude/article")
FIGURES_DIR = ARTICLE_DIR / "Final" / "figures"
OUT_PDF = ARTICLE_DIR / "Final" / "Sekenov_Zhakiyev_Energy_Balance_KZ_PREVIEW.pdf"


class ArticlePDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 5, "PREVIEW DRAFT - For Scopus submission, compile main.tex in Overleaf", align="C")
        self.ln(8)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")

    def title_page(self):
        self.add_page()
        self.ln(30)
        self.set_font("Helvetica", "B", 18)
        self.set_text_color(44, 62, 80)
        self.multi_cell(0, 10,
            "Statistical Analysis of Kazakhstan's Fuel and Energy Balance:\n"
            "An Integrated PyPSA-Power BI Framework with ERA5-Land Climate Data",
            align="C")
        self.ln(15)
        self.set_font("Helvetica", "", 12)
        self.set_text_color(0, 0, 0)
        self.cell(0, 8, "Gabit Sekenov (a,*), Nurkhat Zhakiyev (b)", align="C", new_x="LMARGIN", new_y="NEXT")
        self.ln(5)
        self.set_font("Helvetica", "I", 10)
        self.set_text_color(100, 100, 100)
        self.multi_cell(0, 6,
            "(a) Department of Applied Data Analytics, Astana IT University, Astana, Kazakhstan\n"
            "(b) Davis Center for Russian and Eurasian Studies, Harvard University, Cambridge, MA, USA\n"
            "(*) Corresponding author: 243046@astanait.edu.kz",
            align="C")
        self.ln(15)

        # Abstract
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(44, 62, 80)
        self.cell(0, 8, "Abstract", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(0, 0, 0)
        abstract = (
            "Kazakhstan ranks among the world's most carbon-intensive economies yet targets carbon "
            "neutrality by 2060. This study develops an integrated statistical analysis system combining "
            "PyPSA for energy data processing, ERA5-Land reanalysis datasets for renewable resource "
            "profiling, and Microsoft Power BI for interactive visualization. Using hourly time-series "
            "data spanning 2011-2018 for demand analysis and 2011-2014 for renewable resource profiling "
            "(35,040 hourly capacity factor observations), we perform: (i) demand analysis revealing a "
            "pronounced continental seasonal pattern with a 6,000 MW winter-summer differential; "
            "(ii) generation capacity assessment confirming 59.3% coal dependence; (iii) cross-border "
            "trade evaluation across three partner countries (2021-2023); and (iv) renewable resource "
            "profiling showing mean wind and solar capacity factors of 0.327 and 0.155, respectively. "
            "Time-series forecasting with ARIMA(2,0,2) achieves a 16.1% MAE improvement over naive "
            "benchmarks. Incorporating verified 2020-2025 data, we document a structural inflection in "
            "2023 when Kazakhstan became a net electricity importer for the first time, with the deficit "
            "growing to ~3.0 billion kWh by 2025. Renewable capacity grew from 1,122 MW (2018) to "
            "3,537 MW (2025) across 148 facilities, with the generation share reaching 7%."
        )
        self.multi_cell(0, 5, abstract)
        self.ln(5)

        # Keywords
        self.set_font("Helvetica", "B", 9)
        self.cell(0, 6, "Keywords: ", new_x="END", new_y="TOP")
        self.set_font("Helvetica", "I", 9)
        self.cell(0, 6,
            "fuel and energy balance; Kazakhstan; PyPSA; Power BI; renewable energy; "
            "time-series forecasting; ARIMA; ERA5-Land",
            new_x="LMARGIN", new_y="NEXT")

    def section(self, title, level=1):
        self.ln(5)
        if level == 1:
            self.set_font("Helvetica", "B", 14)
            self.set_text_color(44, 62, 80)
        else:
            self.set_font("Helvetica", "B", 11)
            self.set_text_color(52, 73, 94)
        self.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 9)
        self.set_text_color(0, 0, 0)

    def body_text(self, text):
        self.set_font("Helvetica", "", 9)
        self.multi_cell(0, 5, text)
        self.ln(2)

    def add_figure(self, filename, caption, width=180):
        filepath = FIGURES_DIR / filename
        if not filepath.exists():
            self.set_font("Helvetica", "I", 9)
            self.cell(0, 8, f"[Figure not found: {filename}]",
                      new_x="LMARGIN", new_y="NEXT")
            return
        # Check if enough space, else new page
        if self.get_y() > 200:
            self.add_page()
        try:
            self.image(str(filepath), w=width, x=(210-width)/2)
        except Exception:
            self.set_font("Helvetica", "I", 9)
            self.cell(0, 8, f"[Could not embed: {filename}]",
                      new_x="LMARGIN", new_y="NEXT")
            return
        self.ln(2)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(80, 80, 80)
        self.multi_cell(0, 4, caption)
        self.set_text_color(0, 0, 0)
        self.ln(3)


def main():
    pdf = ArticlePDF("P", "mm", "A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)

    # Title page
    pdf.title_page()

    # 1. Introduction
    pdf.add_page()
    pdf.section("1. Introduction")
    pdf.body_text(
        "Kazakhstan occupies a unique position in the global energy landscape. As Central Asia's "
        "largest economy and a major fossil fuel exporter, it accounts for approximately 60% of the "
        "region's total energy production. The country's Total Energy Supply (TES) in 2021 amounted "
        "to 2,889,286 TJ, with coal and oil constituting over 98% of the energy mix, while renewable "
        "sources contributed a marginal 2%. Despite this fossil fuel dependence, Kazakhstan has "
        "committed to carbon neutrality by 2060, with intermediate targets including 15% renewable "
        "energy by 2030.\n\n"
        "The most recent data (2025) show that Kazakhstan's electricity production reached 122 billion "
        "kWh while consumption exceeded 125 billion kWh, revealing a national energy deficit of ~3 "
        "billion kWh. Renewable capacity has grown to 3,586 MW across 148 facilities, with the "
        "renewable share reaching 7% of electricity generation."
    )

    # 2. Data and Methodology
    pdf.section("2. Data and Methodology")
    pdf.body_text(
        "This study integrates multiple data sources into a unified analytical framework:\n"
        "- ERA5-Land hourly reanalysis (2011-2014): 35,040 observations for wind/solar CF profiling\n"
        "- PyPSA power plant registry: 93 generators across coal, gas, hydro, wind, solar\n"
        "- KEGOC electricity balance (2020-2025): production, consumption, cross-border trade\n"
        "- stat.gov.kz fuel and energy balance (2018-2024)\n"
        "- QazaqGreen RES statistics (2020-2025)\n"
        "- Ministry of Energy RK official reports\n\n"
        "The data pipeline (scripts/data_parser.py) parses these sources into a unified SQLite "
        "database with 6 structured tables and 7 CSV exports for reproducibility."
    )

    # 3. Results - with figures
    pdf.add_page()
    pdf.section("3. Results")

    pdf.section("3.1 Energy Supply Structure", level=2)
    pdf.add_figure("fig1_tes_structure.png",
                   "Fig. 1: Kazakhstan Total Energy Supply structure (TJ)")
    pdf.add_figure("fig2_capacity_bar.png",
                   "Fig. 2: Installed generation capacity by fuel type")

    pdf.add_page()
    pdf.section("3.2 Regional Demand Analysis", level=2)
    pdf.add_figure("fig3_regional_demand.png",
                   "Fig. 3: Regional electricity demand distribution")
    pdf.add_figure("fig6_monthly_profiles.png",
                   "Fig. 6: Monthly wind and solar capacity factor profiles")

    pdf.add_page()
    pdf.section("3.3 Cross-Border Trade", level=2)
    pdf.add_figure("fig4_trade_flows.png",
                   "Fig. 4: Cross-border electricity trade flows (2021-2023)")

    pdf.section("3.4 Renewable Resource Assessment", level=2)
    pdf.add_figure("fig5_re_capacity_factors.png",
                   "Fig. 5: Wind and solar capacity factors from ERA5-Land")

    pdf.add_page()
    pdf.section("3.5 Time-Series Analysis", level=2)
    pdf.add_figure("fig7_arima_forecast.png",
                   "Fig. 7: ARIMA(2,0,2) wind capacity factor forecast vs actual")
    pdf.add_figure("fig14_acf_pacf.png",
                   "Fig. 14: ACF/PACF plots for model order selection")

    pdf.add_page()
    pdf.section("3.6 Scenario Analysis", level=2)
    pdf.add_figure("fig8_scenario_2030.png",
                   "Fig. 8: Renewable energy scenarios to 2030")
    pdf.add_figure("fig9_load_duration.png",
                   "Fig. 9: Load duration curve analysis")

    pdf.add_page()
    pdf.section("3.7 Energy Transition (2020-2025)", level=2)
    pdf.add_figure("fig12_re_transition.png",
                   "Fig. 12: Electricity production/consumption balance and RES share (2020-2025)")
    pdf.add_figure("fig16_res_capacity_breakdown.png",
                   "Fig. 16: RES capacity by technology and 2025 commissioned projects")

    pdf.add_page()
    pdf.section("3.8 Key Energy Indicators", level=2)
    pdf.add_figure("fig15_energy_indicators.png",
                   "Fig. 15: Energy indicators dashboard (2020-2024)")

    pdf.add_page()
    pdf.section("3.9 Comprehensive Infographic", level=2)
    pdf.add_figure("fig17_infographic.png",
                   "Fig. 17: Kazakhstan Energy Transition comprehensive infographic (2018-2025 with projections to 2060)")

    # 4. Discussion
    pdf.add_page()
    pdf.section("4. Discussion")
    pdf.body_text(
        "Key findings:\n"
        "1. Kazakhstan became a net electricity importer in 2023 for the first time, with the "
        "deficit growing to ~3.0 billion kWh by 2025.\n"
        "2. RES capacity grew from 1,122 MW (2018) to 3,586 MW (2025), but the 2030 target of "
        "15% requires ~820 MW/year additions vs 503 MW added in 2025.\n"
        "3. Energy intensity remains 3x the EU average at 0.30 toe/1000 USD GDP.\n"
        "4. Cross-border imports from Russia tripled in 2023 (4,994 GWh), revealing strategic "
        "energy dependency.\n"
        "5. ARIMA(2,0,2) achieves 16.1% MAE improvement for wind CF forecasting."
    )

    # 5. Conclusion
    pdf.section("5. Conclusion")
    pdf.body_text(
        "This study presents an integrated statistical analysis system for Kazakhstan's fuel and "
        "energy balance, combining PyPSA, ERA5-Land, and Power BI. The framework contributes a "
        "reproducible, open-source methodology for national-level energy balance monitoring. "
        "Future work should incorporate energy storage modeling, real-time data integration, and "
        "extended ERA5-Land climate data."
    )

    # Data Availability
    pdf.section("Data Availability")
    pdf.body_text(
        "The Python source code, processed datasets, and Power BI template are available at "
        "the GitHub repository. Raw ERA5-Land data are accessible via the Copernicus Climate "
        "Data Store (https://cds.climate.copernicus.eu)."
    )

    # Save
    pdf.output(str(OUT_PDF))
    size_mb = os.path.getsize(OUT_PDF) / (1024*1024)
    print(f"Preview PDF created: {OUT_PDF}")
    print(f"Size: {size_mb:.1f} MB, Pages: {pdf.page_no()}")


if __name__ == "__main__":
    main()
