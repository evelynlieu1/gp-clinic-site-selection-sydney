"""
Step C -- Clinical "intensity" ranking (Option 3 proxy) on the Step B primary list.

Pipeline (same thresholds as Steps A + B):
  A) People_Per_GP_Ratio = population / (total_gp + 1)  >  HIGH_PRESSURE_THRESHOLD
  B) median_household_income_weekly >= MIXED_BILLING_MIN_INCOME_WEEKLY

Among those suburbs, rank by how strong the high-frequency patient demographic is,
using ABS % aged 65+ as the main lever (Option 3 senior-demand logic).

Ranking order:
  1) over_65_pct  (desc)  -- higher % seniors -> stronger primary-care utilisation proxy
  2) People_Per_GP_Ratio  (desc)  -- tie-break: more pressure / fewer listed GPs
  3) estimated_seniors = population * over_65_pct / 100  (desc)  -- tie-break: more seniors in absolute terms

Outputs:
  - eda/final_top10_clinical_intensity.csv   (top 10)
  - eda/mixed_billing_primary_ranked_intensity.csv  (full Step B pool, ranked)

Run from project root:
  python eda/rank_intensity_step_c.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "scraped_data" / "eda_data.csv"
EDA_DIR = Path(__file__).resolve().parent
OUT_TOP10 = EDA_DIR / "final_top10_clinical_intensity.csv"
OUT_FULL_RANKED = EDA_DIR / "mixed_billing_primary_ranked_intensity.csv"

HIGH_PRESSURE_THRESHOLD = 5000
MAX_SHORTLIST = 50
MIXED_BILLING_MIN_INCOME_WEEKLY = 1800
FINAL_TOP_N = 10


def step_a_b_pool(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["total_gp"] = pd.to_numeric(d["total_gp"], errors="coerce").fillna(0).clip(lower=0)
    d["population"] = pd.to_numeric(d["population"], errors="coerce")
    d["People_Per_GP_Ratio"] = d["population"] / (d["total_gp"] + 1)
    hi = d[d["People_Per_GP_Ratio"] > HIGH_PRESSURE_THRESHOLD].sort_values(
        "People_Per_GP_Ratio", ascending=False
    )
    if len(hi) > MAX_SHORTLIST:
        hi = hi.head(MAX_SHORTLIST)

    inc = pd.to_numeric(hi["median_household_income_weekly"], errors="coerce")
    primary = hi.loc[inc.notna() & (inc >= MIXED_BILLING_MIN_INCOME_WEEKLY)].copy()
    return primary


def main() -> None:
    df = pd.read_csv(DATA_PATH)
    primary = step_a_b_pool(df)

    pct = pd.to_numeric(primary["over_65_pct"], errors="coerce").fillna(0.0)
    pop = pd.to_numeric(primary["population"], errors="coerce").fillna(0.0)
    primary["over_65_pct"] = pct
    primary["estimated_seniors"] = (pop * pct / 100.0).round(1)

    primary = primary.sort_values(
        by=["over_65_pct", "People_Per_GP_Ratio", "estimated_seniors"],
        ascending=[False, False, False],
    ).reset_index(drop=True)

    primary["intensity_rank"] = range(1, len(primary) + 1)

    cols = [
        "intensity_rank",
        "suburb",
        "over_65_pct",
        "estimated_seniors",
        "People_Per_GP_Ratio",
        "median_household_income_weekly",
        "total_gp",
        "population",
        "spatial_density",
        "area",
    ]
    present = [c for c in cols if c in primary.columns]
    ranked = primary[present].copy()
    ranked["People_Per_GP_Ratio"] = ranked["People_Per_GP_Ratio"].round(2)

    ranked.to_csv(OUT_FULL_RANKED, index=False)
    top10 = ranked.head(FINAL_TOP_N).copy()
    top10.to_csv(OUT_TOP10, index=False)

    print(
        f"Step C: ranked {len(ranked)} suburbs (high pressure + income >= ${MIXED_BILLING_MIN_INCOME_WEEKLY}/wk)\n"
        f"  Sort: over_65_pct desc, then pressure ratio, then estimated_seniors\n"
        f"  Full ranked list: {OUT_FULL_RANKED.resolve()}\n"
        f"  Top {FINAL_TOP_N}: {OUT_TOP10.resolve()}"
    )


if __name__ == "__main__":
    main()
