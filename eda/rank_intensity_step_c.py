"""
Step C -- Clinical "intensity" ranking (Option 3 proxy) on the Step B primary list.

Pipeline (same thresholds as Steps A + B; pool built by eda/supply_gap_pool.py):
  A) gp_data_status == 'ok'  -- observed GP supply only. Suburbs that were prompted but
     never returned clinic data have total_gp == 0 for want of data, so ranking them on
     population / (total_gp + 1) puts unscraped suburbs at the top; they are held out.
  A) People_Per_GP_Ratio = population / (total_gp + 1)  >  HIGH_PRESSURE_THRESHOLD
  B) median_household_income_weekly >= MIXED_BILLING_MIN_INCOME_WEEKLY

Among those suburbs, rank by how strong the high-frequency patient demographic is,
using ABS % aged 65+ as the main lever (Option 3 senior-demand logic).

Ranking order:
  1) over_65_pct  (desc)  -- higher % seniors -> stronger primary-care utilisation proxy
  2) People_Per_GP_Ratio  (desc)  -- tie-break: more pressure / fewer listed GPs
  3) estimated_seniors = population * over_65_pct / 100  (desc)  -- tie-break: more seniors in absolute terms

READ THIS BEFORE READING THE RANKING
------------------------------------
SUBURB-INTERIOR SUPPLY COUNTS ARE NOT A MEASURE OF ACCESS. Step A's
People_Per_GP_Ratio counts doctors inside one suburb polygon, and it survives into this
file as the second sort key. Senior demand is a real signal, but ranking it on top of an
interior supply count cannot turn that count into access.

WORKED EXAMPLE, CROYDON: 1 general practice inside the suburb, 21 within 1.5 km, 27
within 2 km, with Burwood and Ashfield in the same GP catchment. Croydon reached rank 1
of the earlier final shortlist on that basis. Its measured access is percentile 85.6 of
the 680 suburbs -- better served than five suburbs in six.

Both outputs carry the access measurement on every row: access_general (E2SFCA practices
per capita reachable in a 20 min drive, competition adjusted; HIGHER = BETTER SERVED),
access_general_pct, practices_within_2km, nearest_practice_km, gpc_name, plus
saturated_nearby (practices_within_2km >= 10) and access_above_median
(access_general_pct >= 50). 3 of the 4 ranked rows trip at least one flag. Rank on
access, not on intensity over a boundary count; eda/site_scores_v2.csv is the ranking
that does, and it puts Croydon at 166 of 341.

Outputs (both carry the access columns listed above):
  - eda/final_top10_clinical_intensity.csv   (top FINAL_TOP_N; see the note below)
  - eda/mixed_billing_primary_ranked_intensity.csv  (full Step B pool, ranked)
  - eda/excluded_no_gp_data.csv -- suburbs held out because their GP supply is unknown.
    That is a re-scrape work list of candidates, NOT a list of rejects. Access is
    measured for all 680 suburbs whatever their gp_data_status, so those rows can be
    triaged on access before anyone re-scrapes them.

WHY "final_top10_clinical_intensity.csv" HOLDS FEWER THAN 10 ROWS
-----------------------------------------------------------------
The file name promises FINAL_TOP_N = 10; the funnel only produces 4 candidates, so the
file holds 4. It is short because there is nothing else that passed Steps A and B, NOT
because rows were dropped, and it is never padded to 10. The console output prints the
full funnel (680 -> 433 rankable -> 5 over the pressure threshold -> 4 above the income
floor) and a rows_written / rows_requested accounting line on every run. The name is kept
because downstream notebooks and docs reference it.

Run from project root:
  python eda/rank_intensity_step_c.py
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
    FINAL_TOP_N,
    HIGH_PRESSURE_THRESHOLD,
    MIXED_BILLING_MIN_INCOME_WEEKLY,
    PRESSURE_COL,
    build_step_a_pool,
    load_eda_data,
    output_columns,
    pool_eligible_mask,
    print_saturated_report,
    split_mixed_billing,
)

OUT_TOP10 = EDA_DIR / "final_top10_clinical_intensity.csv"
OUT_FULL_RANKED = EDA_DIR / "mixed_billing_primary_ranked_intensity.csv"

BASE_COLS = [
    "intensity_rank",
    "suburb",
    "over_65_pct",
    "estimated_seniors",
    PRESSURE_COL,
    "median_household_income_weekly",
    "total_gp",
    "population",
    "spatial_density",
    "area",
]


def rank_by_intensity(primary: pd.DataFrame) -> pd.DataFrame:
    """Sort the Step B primary pool by senior demand, then pressure, then senior count."""
    out = primary.copy()
    pct = pd.to_numeric(out["over_65_pct"], errors="coerce").fillna(0.0)
    pop = pd.to_numeric(out["population"], errors="coerce").fillna(0.0)
    out["over_65_pct"] = pct
    out["estimated_seniors"] = (pop * pct / 100.0).round(1)

    out = out.sort_values(
        by=["over_65_pct", PRESSURE_COL, "estimated_seniors"],
        ascending=[False, False, False],
    ).reset_index(drop=True)
    out["intensity_rank"] = range(1, len(out) + 1)
    return out


def print_row_count_disclosure(df: pd.DataFrame, ranked: pd.DataFrame, written: int) -> None:
    """
    Explain the gap between the ``top10`` in the file name and the rows actually in it.

    The file is named for ``FINAL_TOP_N`` but only ever holds ``min(FINAL_TOP_N, len(ranked))``
    rows, and at present that is 4. A short file with a "top10" name invites the reader to
    assume six rows were cut for cause, so the whole funnel is printed and the shortfall is
    named. It is never padded: no synthetic or lower-quality row is added to reach 10.
    """
    n_read = len(df)
    n_rankable = int(pool_eligible_mask(df).sum())
    n_ranked = len(ranked)
    print("\n=== Row-count disclosure for final_top10_clinical_intensity.csv ===")
    print(
        f"  FINAL_TOP_N (requested) = {FINAL_TOP_N};  rows written = {written}"
        + (
            f";  SHORT BY {FINAL_TOP_N - written}"
            if written < FINAL_TOP_N
            else ";  full"
        )
    )
    if written < FINAL_TOP_N:
        print(
            f"  The file name says 'top10' and the file holds {written}. It is short "
            f"because the funnel runs out,\n"
            f"  not because {FINAL_TOP_N - written} rows were cut for cause, and it is "
            f"NOT padded:\n"
            f"    {n_read:5d}  suburbs read from scraped_data/eda_data.csv\n"
            f"    {n_rankable:5d}  rankable on supply (gp_data_status == 'ok' and "
            f"total_gp known)\n"
            f"    {len(ranked) + 0:5d}  ranked here = also over {PRESSURE_COL} > "
            f"{HIGH_PRESSURE_THRESHOLD} and income >= "
            f"${MIXED_BILLING_MIN_INCOME_WEEKLY}/wk\n"
            f"    {written:5d}  written = min(FINAL_TOP_N={FINAL_TOP_N}, "
            f"ranked={n_ranked})\n"
            f"  There is no {written + 1}th suburb that passed Steps A and B. To get more "
            f"rows, loosen a\n"
            f"  threshold in eda/supply_gap_pool.py or re-scrape the 247 held-out suburbs "
            f"in\n"
            f"  eda/excluded_no_gp_data.csv -- do not treat the shortfall as a filtered "
            f"result."
        )


def main() -> None:
    df = load_eda_data()
    shortlist = build_step_a_pool(df)
    primary, _social = split_mixed_billing(shortlist)

    ranked_full = rank_by_intensity(primary)
    ranked = ranked_full[output_columns(BASE_COLS, ranked_full)].copy()
    ranked[PRESSURE_COL] = ranked[PRESSURE_COL].round(2)

    ranked.to_csv(OUT_FULL_RANKED, index=False)
    top10 = ranked.head(FINAL_TOP_N).copy()
    top10.to_csv(OUT_TOP10, index=False)

    print(
        f"Step C: ranked {len(ranked)} suburbs (observed GP supply, high pressure, "
        f"income >= ${MIXED_BILLING_MIN_INCOME_WEEKLY}/wk)\n"
        f"  Sort: over_65_pct desc, then pressure ratio, then estimated_seniors\n"
        f"  Senior demand is real, but it is ranked here on top of {PRESSURE_COL}, an\n"
        f"  INTERIOR supply count that is not access (Croydon: 1 practice inside,\n"
        f"  21 within 1.5 km, 27 within 2 km). Access is in the appended columns\n"
        f"  {', '.join(ACCESS_CONTEXT_COLS)}.\n"
        f"  Full ranked list ({len(ranked)} rows): {OUT_FULL_RANKED.resolve()}\n"
        f"  Top {FINAL_TOP_N} ({len(top10)} rows): {OUT_TOP10.resolve()}"
    )

    print_row_count_disclosure(df, ranked, len(top10))

    print("\nContradiction accounting for the files this script wrote:")
    print_saturated_report(OUT_TOP10.name, top10)
    print_saturated_report(OUT_FULL_RANKED.name, ranked, max_named=0)


if __name__ == "__main__":
    main()
