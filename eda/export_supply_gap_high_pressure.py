"""
Step A -- Supply gaps from the pressure metric (Option 1 map).

Rule (same as pressure map):  People_Per_GP_Ratio = population / (total_gp + 1)
Shortlist: suburbs where ratio > HIGH_PRESSURE_THRESHOLD (default 5,000).

READ THIS BEFORE READING THE SHORTLIST
--------------------------------------
People_Per_GP_Ratio is a SUBURB-INTERIOR SUPPLY COUNT, NOT A MEASURE OF ACCESS. total_gp
counts doctors inside one suburb polygon; patients do not stop at that boundary, so a
"high pressure" score can mean nothing more than that the practices serving a suburb are
filed to the suburb next door.

WORKED EXAMPLE, CROYDON: 1 general practice inside the suburb, 21 within 1.5 km, 27
within 2 km. Croydon sits between Burwood and Ashfield in one of the most GP-saturated
parts of Sydney -- and it is on this shortlist, at ratio 5,378, for exactly that reason.

Every row of the output now carries the access measurement beside the pressure ratio:
access_general (E2SFCA practices per capita reachable in a 20 min drive, competition
adjusted; HIGHER = BETTER SERVED), access_general_pct, practices_within_2km,
nearest_practice_km and gpc_name, plus the boolean flags saturated_nearby
(practices_within_2km >= 10) and access_above_median (access_general_pct >= 50). 4 of the
5 rows on the current shortlist trip at least one flag. Rank on access, not on the
pressure ratio; eda/site_scores_v2.csv is the ranking that does.

Only suburbs with gp_data_status == 'ok' are eligible. For the 239 suburbs that were
prompted but never returned clinic data, total_gp == 0 means "not scraped", not "no
doctors", and population / (total_gp + 1) would rank them as the most pressured
suburbs in Sydney. The status filter is applied before the threshold; the pool builder
lives in eda/supply_gap_pool.py so Steps A, B and C cannot drift apart.

If more than MAX_SHORTLIST rows qualify, keeps the top MAX_SHORTLIST by ratio.

Outputs (both carry the access columns listed above):
  - eda/high_pressure_supply_gap_suburbs.csv
  - eda/excluded_no_gp_data.csv -- suburbs held out because their GP supply is unknown.
    That is a re-scrape work list of candidates, NOT a list of rejects. The access
    columns are measured for all 680 suburbs regardless of gp_data_status, so a held-out
    suburb can be triaged on access before anyone re-scrapes it.

Run from project root:
  python eda/export_supply_gap_high_pressure.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

EDA_DIR = Path(__file__).resolve().parent
if str(EDA_DIR) not in sys.path:
    sys.path.insert(0, str(EDA_DIR))

from supply_gap_pool import (  # noqa: E402  (eda/ added to sys.path above)
    ACCESS_CONTEXT_COLS,
    EXCLUDED_CSV,
    HIGH_PRESSURE_THRESHOLD,
    PRESSURE_COL,
    build_step_a_pool,
    load_eda_data,
    output_columns,
    print_saturated_report,
)

OUT_CSV = EDA_DIR / "high_pressure_supply_gap_suburbs.csv"

BASE_COLS = [
    "suburb",
    "total_gp",
    "population",
    PRESSURE_COL,
    "over_65_pct",
    "median_household_income_weekly",
    "spatial_density",
    "area",
]


def main() -> None:
    df = load_eda_data()
    hi = build_step_a_pool(df)

    out = hi[output_columns(BASE_COLS, hi)].copy()
    out[PRESSURE_COL] = out[PRESSURE_COL].round(2)
    out.to_csv(OUT_CSV, index=False)

    print(
        f"Step A threshold: {PRESSURE_COL} > {HIGH_PRESSURE_THRESHOLD} "
        f"(population / (total_gp + 1), same as pressure map)\n"
        f"  Ranked on observed GP supply only (gp_data_status == 'ok')\n"
        f"  {PRESSURE_COL} is an INTERIOR count and is NOT access -- see the Croydon\n"
        f"  example above; access lives in the appended columns "
        f"{', '.join(ACCESS_CONTEXT_COLS)}\n"
        f"  Shortlist size: {len(out)}  ->  {OUT_CSV.resolve()}"
    )
    print("\nContradiction accounting for the files this script wrote:")
    print_saturated_report(OUT_CSV.name, out)
    print_saturated_report(EXCLUDED_CSV.name, pd.read_csv(EXCLUDED_CSV), max_named=0)


if __name__ == "__main__":
    main()
