"""
Step B -- Mixed billing filter (Option 2 logic) on the Step A high-pressure shortlist.

Starts from the same rule as Step A / pressure map:
  People_Per_GP_Ratio = population / (total_gp + 1)  >  HIGH_PRESSURE_THRESHOLD

Then splits by median household income (weekly, same column as mixed_billing map):
  - Primary business list:  income >= MIXED_BILLING_MIN_INCOME_WEEKLY ($1,800)
  - Social need list:        income <  that threshold, OR income missing

Outputs:
  - eda/mixed_billing_primary_supply_gap.csv
  - eda/social_need_supply_gap.csv

Run from project root (after Step A export is optional; this script reads eda_data.csv directly):
  python eda/apply_mixed_billing_filter_step_b.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "scraped_data" / "eda_data.csv"
EDA_DIR = Path(__file__).resolve().parent
OUT_PRIMARY = EDA_DIR / "mixed_billing_primary_supply_gap.csv"
OUT_SOCIAL = EDA_DIR / "social_need_supply_gap.csv"

HIGH_PRESSURE_THRESHOLD = 5000
MAX_SHORTLIST = 50
# Option 2 map uses light green below ~$1,500; Step B cutoff for gap-fee viability:
MIXED_BILLING_MIN_INCOME_WEEKLY = 1800

OUT_COLS = [
    "suburb",
    "total_gp",
    "population",
    "People_Per_GP_Ratio",
    "median_household_income_weekly",
    "over_65_pct",
    "spatial_density",
    "area",
]


def load_high_pressure_shortlist(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["total_gp"] = pd.to_numeric(df["total_gp"], errors="coerce").fillna(0).clip(lower=0)
    df["population"] = pd.to_numeric(df["population"], errors="coerce")
    df["People_Per_GP_Ratio"] = df["population"] / (df["total_gp"] + 1)
    hi = df[df["People_Per_GP_Ratio"] > HIGH_PRESSURE_THRESHOLD].sort_values(
        "People_Per_GP_Ratio", ascending=False
    )
    if len(hi) > MAX_SHORTLIST:
        hi = hi.head(MAX_SHORTLIST)
    return hi


def main() -> None:
    df = pd.read_csv(DATA_PATH)
    shortlist = load_high_pressure_shortlist(df)

    inc = pd.to_numeric(shortlist["median_household_income_weekly"], errors="coerce")

    # Primary: known income and at/above mixed-billing floor
    primary_mask = inc.notna() & (inc >= MIXED_BILLING_MIN_INCOME_WEEKLY)
    # Social need: below floor or unknown income
    social_mask = ~primary_mask

    primary = shortlist.loc[primary_mask].copy()
    social = shortlist.loc[social_mask].copy()

    primary["People_Per_GP_Ratio"] = primary["People_Per_GP_Ratio"].round(2)
    social["People_Per_GP_Ratio"] = social["People_Per_GP_Ratio"].round(2)

    social["filter_reason"] = social.apply(
        lambda r: (
            "income_below_1800"
            if pd.notna(r.get("median_household_income_weekly"))
            and float(r["median_household_income_weekly"]) < MIXED_BILLING_MIN_INCOME_WEEKLY
            else "income_missing"
        ),
        axis=1,
    )

    pcols = [c for c in OUT_COLS if c in primary.columns]
    primary[pcols].to_csv(OUT_PRIMARY, index=False)

    scols = [c for c in OUT_COLS if c in social.columns] + ["filter_reason"]
    social[scols].to_csv(OUT_SOCIAL, index=False)

    print(
        f"Step A pool: People_Per_GP_Ratio > {HIGH_PRESSURE_THRESHOLD} (max {MAX_SHORTLIST} rows)\n"
        f"  Suburbs in pool: {len(shortlist)}\n"
        f"Step B: median_household_income_weekly >= {MIXED_BILLING_MIN_INCOME_WEEKLY} -> primary\n"
        f"  Mixed billing (primary): {len(primary)}  ->  {OUT_PRIMARY.resolve()}\n"
        f"  Social need (income < {MIXED_BILLING_MIN_INCOME_WEEKLY} or missing): {len(social)}  ->  {OUT_SOCIAL.resolve()}"
    )


if __name__ == "__main__":
    main()
