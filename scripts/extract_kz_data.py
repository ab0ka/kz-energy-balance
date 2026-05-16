"""
Extract Kazakhstan-specific data from downloaded global datasets.
Sources: Our World in Data (OWID), Ember Global Electricity
Outputs structured CSVs for analysis and article figures.
"""
import pandas as pd
import numpy as np
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
EXT_DIR = DATA_DIR / "external"
OUT_DIR = DATA_DIR / "processed"

print("=" * 60)
print("  Extracting Kazakhstan Energy Data")
print("=" * 60)

# ══════════════════════════════════════════════════════════════
# 1. Our World in Data — comprehensive energy dataset
# ══════════════════════════════════════════════════════════════
print("\n1. Processing OWID Energy Data...")
owid = pd.read_csv(EXT_DIR / "owid_energy_data.csv")
print(f"   Full dataset: {owid.shape[0]:,} rows, {owid.shape[1]} columns")

kz = owid[owid["country"] == "Kazakhstan"].copy()
print(f"   Kazakhstan rows: {kz.shape[0]}")
print(f"   Year range: {kz['year'].min()} - {kz['year'].max()}")

# Select key columns
key_cols = [
    "year", "country",
    # Primary energy
    "primary_energy_consumption", "energy_per_capita", "energy_per_gdp",
    # Electricity
    "electricity_generation", "electricity_demand",
    # By source
    "coal_electricity", "gas_electricity", "oil_electricity",
    "hydro_electricity", "wind_electricity", "solar_electricity",
    "nuclear_electricity", "biofuel_electricity",
    # Shares
    "coal_share_elec", "gas_share_elec", "oil_share_elec",
    "hydro_share_elec", "wind_share_elec", "solar_share_elec",
    "renewables_share_elec", "fossil_share_elec",
    # Capacity
    "coal_cons_per_capita", "gas_production", "oil_production",
    # Carbon
    "carbon_intensity_elec", "greenhouse_gas_emissions",
    "co2_including_luc",
    # Population & GDP
    "population", "gdp",
]
available_cols = [c for c in key_cols if c in kz.columns]
kz_clean = kz[available_cols].copy()

# Filter to 2000+ for modern analysis
kz_modern = kz_clean[kz_clean["year"] >= 2000].copy()
kz_modern["gdp_missing_flag"] = kz_modern["gdp"].isna().astype(int) if "gdp" in kz_modern.columns else 1
kz_modern["energy_per_gdp_missing_flag"] = (
    kz_modern["energy_per_gdp"].isna().astype(int) if "energy_per_gdp" in kz_modern.columns else 1
)

# Keep both files: synced 2024 and legacy <=2023 for backward compatibility.
kz_modern.to_csv(OUT_DIR / "kz_owid_energy_2000_2024.csv", index=False)
kz_modern[kz_modern["year"] <= 2023].to_csv(OUT_DIR / "kz_owid_energy_2000_2023.csv", index=False)
print(
    f"   Saved: kz_owid_energy_2000_2024.csv ({kz_modern.shape[0]} rows, {len(kz_modern.columns)} cols)"
)

# Print latest available data
latest = kz_modern[kz_modern["year"] == kz_modern["year"].max()].iloc[0]
print(f"\n   Latest OWID data ({int(latest['year'])}):")
for col in ["electricity_generation", "coal_share_elec", "renewables_share_elec",
            "carbon_intensity_elec", "energy_per_gdp"]:
    if col in latest.index and pd.notna(latest[col]):
        print(f"     {col}: {latest[col]:.2f}")


# ══════════════════════════════════════════════════════════════
# 2. Ember — detailed electricity data
# ══════════════════════════════════════════════════════════════
print("\n2. Processing Ember Electricity Data...")
ember = pd.read_csv(EXT_DIR / "ember_yearly_electricity.csv")
print(f"   Full dataset: {ember.shape[0]:,} rows")

# Check column names
print(f"   Columns: {list(ember.columns)}")

# Filter Kazakhstan
kz_ember_mask = ember.apply(
    lambda row: any("kazakh" in str(v).lower() for v in row.values), axis=1
)
kz_ember = ember[kz_ember_mask].copy()
print(f"   Kazakhstan rows: {kz_ember.shape[0]}")

if kz_ember.shape[0] > 0:
    kz_ember.to_csv(OUT_DIR / "kz_ember_electricity.csv", index=False)
    print(f"   Saved: kz_ember_electricity.csv")
    print(f"   Sample data:")
    print(kz_ember.head(10).to_string())
else:
    print("   No Kazakhstan data found in Ember dataset")


# ══════════════════════════════════════════════════════════════
# 3. Summary statistics for article
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("  KEY STATISTICS FOR ARTICLE (from OWID)")
print("=" * 60)

recent = kz_modern[kz_modern["year"] >= 2015].copy()
for _, row in recent.iterrows():
    yr = int(row["year"])
    gen = row.get("electricity_generation", np.nan)
    coal_sh = row.get("coal_share_elec", np.nan)
    ren_sh = row.get("renewables_share_elec", np.nan)
    co2_int = row.get("carbon_intensity_elec", np.nan)
    line = f"  {yr}: "
    if pd.notna(gen):
        line += f"Gen={gen:.1f} TWh, "
    if pd.notna(coal_sh):
        line += f"Coal={coal_sh:.1f}%, "
    if pd.notna(ren_sh):
        line += f"RES={ren_sh:.1f}%, "
    if pd.notna(co2_int):
        line += f"CO2int={co2_int:.0f} gCO2/kWh"
    print(line)


# ══════════════════════════════════════════════════════════════
# 4. Peer comparison (Central Asia + Russia + EU average)
# ══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("  PEER COMPARISON (2022)")
print("=" * 60)

peers = ["Kazakhstan", "Uzbekistan", "Kyrgyzstan", "Tajikistan",
         "Turkmenistan", "Russia", "World"]
peer_data = owid[(owid["country"].isin(peers)) & (owid["year"] == 2022)]

for _, row in peer_data.iterrows():
    cn = row["country"]
    gen = row.get("electricity_generation", np.nan)
    ren = row.get("renewables_share_elec", np.nan)
    co2 = row.get("carbon_intensity_elec", np.nan)
    line = f"  {cn:20s}: "
    if pd.notna(gen):
        line += f"Gen={gen:>8.1f} TWh, "
    if pd.notna(ren):
        line += f"RES={ren:>5.1f}%, "
    if pd.notna(co2):
        line += f"CO2={co2:>5.0f} gCO2/kWh"
    print(line)

peer_data_out = peer_data[available_cols].copy() if len(peer_data) > 0 else pd.DataFrame()
if not peer_data_out.empty:
    peer_data_out.to_csv(OUT_DIR / "central_asia_comparison_2022.csv", index=False)
    print(f"\n  Saved: central_asia_comparison_2022.csv")


print("\n" + "=" * 60)
print("  ALL FILES IN processed/")
print("=" * 60)
for f in sorted(OUT_DIR.glob("*")):
    size_kb = f.stat().st_size / 1024
    print(f"  {f.name:45s} {size_kb:>8.1f} KB")
