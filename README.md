# Kazakhstan Energy Balance Analysis (2019–2025)

Reproducible statistical and machine learning analysis of Kazakhstan's fuel and energy balance, with focus on the emerging electricity deficit, the "gas substitution trap" in CO₂ emissions, and demand/renewable forecasting.

This repository accompanies the master's thesis **"Statistical Analysis for the Fuel and Energy Balance of Kazakhstan"** (Gabit Sekenov, ADA-2404M, Astana IT University, 2026; supervisor: Nurkhat Zhakiyev, PhD).

---

## ⭐ Key results

| Result | Method | Source script |
|---|---|---|
| Quantified **"gas substitution trap"**: coal→gas cuts emissions by −13.8 Mt, demand growth adds +10.2 Mt | LMDI decomposition | `scripts/run_ml_pipeline.py`, `scripts/build_dataset.py` |
| **2023 structural break** in the electricity balance formally confirmed (sup F = 14.7, p < 0.01) | Bai–Perron test | `scripts/forecast_renewable_cf.py` |
| **SARIMA-LSTM** wind capacity-factor forecasting: 25.2% MAE reduction vs naive baseline | Hybrid time-series + LSTM | `scripts/forecast_renewable_cf.py` |
| **EU→KZ transfer learning** for hourly demand forecasting: 40.1% sMAPE reduction | CatBoost + transfer learning | `scripts/transfer_forecasting/` |
| **Multi-criteria TOPSIS** for AI data-center site selection in Kazakhstan | 11-criterion multi-criteria analysis | `scripts/ai_datacenter/` |

---

## 📊 Dataset

The consolidated dataset (SQLite database with 18 tables, 34 362 rows) and the full CSV exports are published as a separate Kaggle dataset for size reasons:

🔗 **[Kazakhstan Energy Balance 2019–2025 — Kaggle dataset](https://www.kaggle.com/datasets/ab0ka/kazakhstan-energy-balance-2019-2025)**

A few small sample CSVs are included in this repo under `data_sample/` for quick exploration without leaving GitHub.

---

## 🗂 Repository structure

```
.
├── README.md
├── LICENSE                                # MIT
├── requirements.txt                       # Python dependencies
├── .gitignore
│
├── scripts/                               # Main pipeline (Article 2/3 — Energy Balance)
│   ├── build_dataset.py                   # stat.gov.kz Excel → master_long, SQLite
│   ├── kegoc_pdf_parser.py                # KEGOC annual reports → kegoc_balance
│   ├── korem_xlsx_parser.py               # KOREM monthly XLSX → korem_hourly
│   ├── run_ml_pipeline.py                 # ML wrapper (naive, ARIMA, Holt–Winters, GBDT)
│   ├── forecast_renewable_cf.py           # Wind CF forecasting + SARIMA-LSTM
│   ├── full_verification.py               # End-to-end QA
│   ├── db_info.py                         # SQLite inspector
│   └── transfer_forecasting/              # Article 3 — EU→KZ transfer learning
│       ├── run_pipeline.py
│       ├── train_models.py
│       ├── clean_data.py
│       ├── build_tables.py
│       ├── build_submission_package.py
│       └── make_figure.py
│
├── scripts/ai_datacenter/                 # Article 1 — AI Data Center TCO/PUE/TOPSIS
│   ├── build_paper.py
│   ├── generate_figures.py
│   └── build_methodology_doc.py
│
├── scripts/cnn_lstm/                      # Article 4 — CNN-LSTM renewable forecasting
│   ├── train_models.py
│   └── download_era5.py
│
├── data_sample/                           # Small CSV samples (full set on Kaggle)
│   ├── electricity_balance.csv            (7 KB)
│   ├── kegoc_balance.csv                  (2 KB)
│   ├── master_TJ_wide.csv                 (58 KB)
│   └── master_toe_wide.csv                (68 KB)
│
└── dissertation_latex/                    # LaTeX source of the thesis
    ├── main.tex
    ├── refs.bib
    ├── compile.bat
    ├── README.md
    └── chapters/  (15 .tex files)
```

---

## 🚀 Quickstart

```bash
# 1. Clone the repository
git clone https://github.com/ab0ka/kz-energy-balance.git
cd kz-energy-balance

# 2. Set up Python environment
python -m venv .venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\activate           # Windows

pip install -r requirements.txt

# 3. Download the full dataset from Kaggle
#    https://www.kaggle.com/datasets/ab0ka/kazakhstan-energy-balance-2019-2025
#    → place kz_energy_balance.db in data/clean/

# 4. Inspect the database
python scripts/db_info.py

# 5. Reproduce the wind capacity-factor forecasting pipeline
python scripts/forecast_renewable_cf.py

# 6. Reproduce the EU→KZ transfer learning pipeline
python scripts/transfer_forecasting/run_pipeline.py
```

---

## 📈 Power BI dashboard

The interactive dashboard (`Energy.pbix`, 4 pages: Overview, Demand, Generation & Trade, Forecasting) is **not** included in the repository (it's a binary Power BI file). Download it from the Kaggle dataset page above. To refresh from your local SQLite copy:

1. Open `Energy.pbix` in Power BI Desktop.
2. Install the SQLite ODBC driver (free).
3. **Home → Get Data → ODBC** with connection string:
   ```
   Driver=SQLite3 ODBC Driver;Database=<absolute-path>/kz_energy_balance.db
   ```
4. **Home → Refresh.**

---

## 🎓 Citation

```bibtex
@mastersthesis{sekenov2026,
  author  = {Sekenov, Gabit},
  title   = {Statistical Analysis for the Fuel and Energy Balance of Kazakhstan},
  school  = {Astana IT University},
  year    = {2026},
  type    = {Master's thesis},
  address = {Astana, Kazakhstan},
  note    = {Supervisor: Nurkhat Zhakiyev. Program: 7M06105 Applied Data Analytics.}
}
```

If you use this code or dataset in academic work, please also cite the underlying article:

```bibtex
@article{sekenov_zhakiyev2026,
  author  = {Sekenov, Gabit and Zhakiyev, Nurkhat},
  title   = {Diagnosis and Forecasting of {K}azakhstan's Emerging Electricity Deficit:
             A Multi-Method Statistical and Machine Learning Analysis (2019--2025)},
  journal = {Manuscript in preparation},
  year    = {2026}
}
```

---

## 📄 License

Code: **MIT** (see [LICENSE](LICENSE)).
Dataset (on Kaggle): **CC BY 4.0** — please attribute when using.

---

## 👥 Authors and contact

- **Gabit Sekenov** — Astana IT University · [243046@astanait.edu.kz](mailto:243046@astanait.edu.kz)
- **Nurkhat Zhakiyev**, PhD — Astana IT University; Harvard Davis Center · [nzhakiyev@fas.harvard.edu](mailto:nzhakiyev@fas.harvard.edu)

For dataset issues or reproduction problems, please open a GitHub issue.
