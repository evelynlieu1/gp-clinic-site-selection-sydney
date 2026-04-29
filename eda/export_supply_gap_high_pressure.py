"""
Step A — Supply gaps from the pressure metric (Option 1 map).

Rule (same as pressure map):  People_Per_GP_Ratio = population / (total_gp + 1)
Shortlist: suburbs where ratio > HIGH_PRESSURE_THRESHOLD (default 5,000).

If more than MAX_SHORTLIST rows qualify, keeps the top MAX_SHORTLIST by ratio.

Output: eda/high_pressure_supply_gap_suburbs.csv

Run from project root:
  python eda/export_supply_gap_high_pressure.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "scraped_data" / "eda_data.csv"
OUT_CSV = Path(__file__).resolve().parent / "high_pressure_supply_gap_suburbs.csv"

# People per (GP + 1); aligns with pressure_people_per_gp choropleth
HIGH_PRESSURE_THRESHOLD = 5000
MAX_SHORTLIST = 50


def main() -> None:
    df = pd.read_csv(DATA_PATH)
    df["total_gp"] = pd.to_numeric(df["total_gp"], errors="coerce").fillna(0).clip(lower=0)
    df["population"] = pd.to_numeric(df["population"], errors="coerce")

    df["People_Per_GP_Ratio"] = df["population"] / (df["total_gp"] + 1)

    hi = df[df["People_Per_GP_Ratio"] > HIGH_PRESSURE_THRESHOLD].sort_values(
        "People_Per_GP_Ratio", ascending=False
    )
    if len(hi) > MAX_SHORTLIST:
        hi = hi.head(MAX_SHORTLIST)

    cols = [
        "suburb",
        "total_gp",
        "population",
        "People_Per_GP_Ratio",
        "over_65_pct",
        "median_household_income_weekly",
        "spatial_density",
        "area",
    ]
    present = [c for c in cols if c in hi.columns]
    out = hi[present].copy()
    out["People_Per_GP_Ratio"] = out["People_Per_GP_Ratio"].round(2)

    out.to_csv(OUT_CSV, index=False)
    print(
        f"Threshold: People_Per_GP_Ratio > {HIGH_PRESSURE_THRESHOLD} "
        f"(population / (total_gp + 1), same as pressure map)\n"
        f"Shortlist size: {len(out)}  ->  {OUT_CSV.resolve()}"
    )


if __name__ == "__main__":
    main()
