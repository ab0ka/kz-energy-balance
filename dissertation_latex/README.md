# Master's Thesis — Sekenov Gabit (ADA-2404M)

**Topic:** Statistical Analysis for the Fuel and Energy Balance of Kazakhstan
**Program:** 7M06105 — Applied Data Analytics
**Supervisor:** Nurkhat Zhakiyev · Astana IT University · Defense: Astana, 2026
**Language:** English

---

## Status

Complete. 69 pages total (58 pages of main text, excluding the reference list
and appendices) · 8 chapters · 3 appendices · 27 figures · 5 tables · 48 references.
Compiles cleanly (0 errors, 0 undefined references). The compiled document is
`main.pdf`. Structure and volume follow the AITU methodological guidelines
ДП-AITU-19 (cover, title page, abstract ≤1 p., introduction ≤3 p.,
conclusion 2–3 p., main text 50–60 p.).

## File structure

```
Dissertation_Document/
├── main.tex          # entry point
├── refs.bib          # bibliography (48 entries)
├── compile.bat       # Windows compile script
├── main.pdf          # compiled thesis (69 pages)
├── chapters/         # all 15 chapter/section files
└── figures/          # all figures used in the thesis
```

## How to compile (if editing the source)

```bash
pdflatex main.tex
bibtex main
pdflatex main.tex
pdflatex main.tex
```

or run `compile.bat` (Windows). Requires MiKTeX or TeX Live with the usual
packages (`babel`, `times`, `geometry`, `hyperref`, `natbib`, `graphicx`,
`booktabs`, `titlesec`, `extreport`, `setspace`, `microtype`).

## Reproducibility of the results

The forecasting results in Chapters 5 and 6 are reproducible from the scripts
in `../02_Energy_Balance_and_Demand_Forecasting_KZ/Article/scripts/`:
`forecast_renewable_cf.py` (Chapter 5, wind capacity-factor model comparison)
and `transfer_forecasting/` (Chapter 6, EU-to-KZ transfer-learning pipeline).
The consolidated dataset is `kz_energy_balance.db` (18 tables, 34,362 rows).

## Formatting

AITU requirements: Times New Roman 14pt, 1.5 line spacing, A4,
margins left 30 mm / right 15 mm / top-bottom 20 mm, chapter headings
bold and centred.
