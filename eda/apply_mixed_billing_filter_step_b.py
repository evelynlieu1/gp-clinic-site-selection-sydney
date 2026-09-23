"""
Step B -- Mixed billing filter (Option 2 logic) on the Step A high-pressure shortlist.

Starts from the same pool as Step A (built by eda/supply_gap_pool.py):
  gp_data_status == 'ok'  AND
  People_Per_GP_Ratio = population / (total_gp + 1)  >  HIGH_PRESSURE_THRESHOLD

The status filter comes first: a suburb that was prompted but never returned clinic
data has total_gp == 0 for want of data, not for want of doctors, so it cannot be
ranked on supply.

READ THIS BEFORE READING EITHER LIST
------------------------------------
SUBURB-INTERIOR SUPPLY COUNTS ARE NOT A MEASURE OF ACCESS. total_gp and
People_Per_GP_Ratio count doctors inside one suburb polygon. The income split below is a
demand-side test only -- it cannot repair a supply number that stopped at the boundary,
so a high-income row on the primary list is not evidence of an underserved market.

WORKED EXAMPLE, CROYDON: 1 general practice inside the suburb, 21 within 1.5 km, 27
within 2 km, in one of the most GP-saturated parts of Sydney (Burwood and Ashfield are
in the same GP catchment). Croydon reaches the primary mixed-billing list here on a
pressure ratio of 5,378 that measures its border, not its supply.

Both outputs carry the access measurement on every row: access_general (E2SFCA practices
per capita reachable in a 20 min drive, competition adjusted; HIGHER = BETTER SERVED),
access_general_pct, practices_within_2km, nearest_practice_km, gpc_name, and the flags
saturated_nearby (practices_within_2km >= 10) and access_above_median
(access_general_pct >= 50). 3 of the 4 rows on the current primary list trip at least
one, and so does the single social-need row (Belmore: 22 practices within 2 km). The one
that does not is Beacon Hill, whose zero is confirmed by two independent sources. Rank on
access, not on the pressure ratio; eda/site_scores_v2.csv is the ranking that does.

Then splits by median household income (weekly, same column as mixed_billing map):
  - Primary business list:  income >= MIXED_BILLING_MIN_INCOME_WEEKLY ($1,800)
  - Social need list:        income <  that threshold, OR income missing

Outputs (all three carry the access columns listed above):
  - eda/mixed_billing_primary_supply_gap.csv
  - eda/social_need_supply_gap.csv
  - eda/excluded_no_gp_data.csv -- suburbs held out because their GP supply is unknown.
    That is a re-scrape work list of candidates, NOT a list of rejects. Access is
    measured for all 680 suburbs whatever their gp_data_status, so those rows can be
    triaged on access before anyone re-scrapes them.

Run from project root (Step A export is optional; this script reads eda_data.csv directly):
  python eda/apply_mixed_billing_filter_step_b.py
"""
from __future__ import annotations

import sys
from pathlib import Path

EDA_DIR = Path(__file__).resolve().parent
if str(EDA_DIR) not in sys.path:
    sys.path.insert(0, str(EDA_DIR))

from supply_gap_pool import (  # noqa: E402  (eda/ added to sys.path above)
    ACCESS_CONTEXT_COLS,
    HIGH_PRESSURE_THRESHOLD,
    MAX_SHORTLIST,
    MIXED_BILLING_MIN_INCOME_WEEKLY,
    PRESSURE_COL,
    build_step_a_pool,
    load_eda_data,
    output_columns,
    print_saturated_report,
    split_mixed_billing,
)

OUT_PRIMARY = EDA_DIR / "mixed_billing_primary_supply_gap.csv"
OUT_SOCIAL = EDA_DIR / "social_need_supply_gap.csv"

BASE_COLS = [
    "suburb",
    "total_gp",
    "population",
    PRESSURE_COL,
    "median_household_income_weekly",
    "over_65_pct",
    "spatial_density",
    "area",
]


def main() -> None:
    df = load_eda_data()
    shortlist = build_step_a_pool(df)
    primary, social = split_mixed_billing(shortlist)

    primary[PRESSURE_COL] = primary[PRESSURE_COL].round(2)
    social[PRESSURE_COL] = social[PRESSURE_COL].round(2)

    primary[output_columns(BASE_COLS, primary)].to_csv(OUT_PRIMARY, index=False)
    # filter_reason keeps its old position (after the provenance block) via `tail`, so the
    # access columns are strictly last and nothing pre-existing moves.
    social[output_columns(BASE_COLS, social, tail=["filter_reason"])].to_csv(
        OUT_SOCIAL, index=False
    )

    print(
        f"Step A pool: {PRESSURE_COL} > {HIGH_PRESSURE_THRESHOLD}, "
        f"gp_data_status == 'ok' only (max {MAX_SHORTLIST} rows)\n"
        f"  Suburbs in pool: {len(shortlist)}\n"
        f"Step B: median_household_income_weekly >= {MIXED_BILLING_MIN_INCOME_WEEKLY} -> primary\n"
        f"  Mixed billing (primary): {len(primary)}  ->  {OUT_PRIMARY.resolve()}\n"
        f"  Social need (income < {MIXED_BILLING_MIN_INCOME_WEEKLY} or missing): "
        f"{len(social)}  ->  {OUT_SOCIAL.resolve()}\n"
        f"  The income split is demand-side only. It does NOT make {PRESSURE_COL} a\n"
        f"  measure of access -- that is an interior count (Croydon: 1 practice inside,\n"
        f"  21 within 1.5 km, 27 within 2 km). Access is in the appended columns\n"
        f"  {', '.join(ACCESS_CONTEXT_COLS)}."
    )
    print("\nContradiction accounting for the files this script wrote:")
    print_saturated_report(OUT_PRIMARY.name, primary)
    print_saturated_report(OUT_SOCIAL.name, social)


if __name__ == "__main__":
    main()
