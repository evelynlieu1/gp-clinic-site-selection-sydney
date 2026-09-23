"""
Shared candidate pool for the supply-gap shortlist (Steps A, B and C).

Steps A, B and C all start from the same pressure metric::

    People_Per_GP_Ratio = population / (total_gp + 1)

and each script used to rebuild that pool itself, so a fix in one never reached the
other two. This module is the single implementation all three import, together with
the thresholds (``HIGH_PRESSURE_THRESHOLD``, ``MAX_SHORTLIST``,
``MIXED_BILLING_MIN_INCOME_WEEKLY``, ``FINAL_TOP_N``).

SUBURB-INTERIOR SUPPLY COUNTS ARE NOT A MEASURE OF ACCESS
---------------------------------------------------------
``total_gp``, ``clinics_listed`` and ``People_Per_GP_Ratio`` count doctors inside a
suburb's own boundary. Residents do not stop at that boundary, so an interior count is
a count of *tenancies in one polygon*, never a measure of how much GP care a resident
can reach. Worked example, **Croydon**: 1 general practice inside the suburb, **21
within 1.5 km, 27 within 2 km**. It sits between Burwood and Ashfield in one of the
most GP-saturated parts of Sydney, yet ``population / (total_gp + 1)`` reads it as the
most pressured suburb on this pipeline's shortlist -- and that is how Croydon came to
top the earlier answer. The pressure metric is retained here unchanged, because these
three steps are the documented Option 1/2/3 pipeline, but every row it emits now
carries the access columns next to it so the Croydon reading cannot be repeated:

``practices_within_2km``  general practices reachable within 2 km of the suburb centroid.
``nearest_practice_km``   straight-line distance to the closest one (any suburb).
``access_general``        E2SFCA competition-adjusted practices per capita reachable
                          within a 20 min drive. **Higher = better served.**
``access_general_pct``    that value as a percentile of the 680 suburbs.
``gpc_name``              the Department of Health GP catchment the suburb sits in, so
                          same-catchment substitutes are visible as substitutes.
``saturated_nearby``      True when ``practices_within_2km >=
                          SATURATED_NEARBY_MIN_PRACTICES_2KM``: the interior gap is
                          contradicted by the surroundings.
``access_above_median``   True when ``access_general_pct >=
                          ACCESS_ABOVE_MEDIAN_PCT``: better served than the median
                          Sydney suburb on the competition-adjusted measure.

Sources: ``scraped_data/suburb_accessibility.csv`` (built by ``accessibility.py``) and
``scraped_data/suburb_gp_catchment.csv`` (built by ``gp_catchments.py``). Rank on the
access columns, not on the interior counts; see also ``eda/site_scores_v2.csv``.

**The rule enforced here.** ``total_gp == 0`` means "zero doctors" only where
``gp_data_status == 'ok'``. 239 of the 680 suburbs were prompted during the clinic
scrape but no clinic data ever came back (``no_data``), and a few more were listed
with clinics whose doctor counts were all blank (``gp_count_unknown``). For those
suburbs ``total_gp`` carries no information, yet ``population / (total_gp + 1)``
converts it into maximum pressure -- which is how never-scraped suburbs took 7 of the
10 places in the old final shortlist. So the pool is restricted to
:func:`pool_eligible_mask` (``gp_supply.rankable_mask`` plus a known ``total_gp``)
*before* the ``HIGH_PRESSURE_THRESHOLD`` test runs.

**The held-out suburbs are NOT rejects.** A suburb dropped by the status filter is a
candidate whose GP supply is unknown, not a candidate that failed a test. Those rows
are written to ``eda/excluded_no_gp_data.csv``, sorted by population descending: that
file is the re-scrape work list. Scrape a suburb on it and it becomes eligible for the
shortlist on exactly the same terms as every other suburb. A row whose status is ``ok``
but whose ``total_gp`` cell is blank is held out the same way: a missing count is never
filled with 0, so it cannot masquerade as zero supply.

Public helpers:

``load_eda_data``            -- read ``eda_data.csv`` + access context, failing loudly
                                on a pre-fix copy.
``add_pressure_metric``      -- attach ``People_Per_GP_Ratio`` (see that function's caveat).
``pool_eligible_mask``       -- rankable status *and* a known ``total_gp``.
``build_step_a_pool``        -- Step A pool: rankable, over threshold, top ``MAX_SHORTLIST``.
``split_mixed_billing``      -- Step B split of a pool into (primary, social need).
``write_excluded_work_list`` -- write the re-scrape work list CSV.
``output_columns``           -- append provenance + access columns to an output column list.
``load_access_context``      -- read the access / catchment columns for all 680 suburbs.
``add_access_context``       -- merge them onto a frame and derive the contradiction flags.
``print_interior_count_warning`` -- the Croydon disclosure, printed by all three steps.
``print_saturated_report``   -- per-file count of contradicted rows.

Run from project root:
  python eda/export_supply_gap_high_pressure.py   (Step A)
  python eda/apply_mixed_billing_filter_step_b.py (Step B)
  python eda/rank_intensity_step_c.py             (Step C)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

EDA_DIR = Path(__file__).resolve().parent
ROOT = EDA_DIR.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gp_supply import (  # noqa: E402  (project root added to sys.path above)
    GP_STATUS_COUNT_UNKNOWN,
    GP_STATUS_NO_DATA,
    rankable_mask,
    require_status_column,
    status_summary,
)

DATA_PATH = ROOT / "scraped_data" / "eda_data.csv"
EXCLUDED_CSV = EDA_DIR / "excluded_no_gp_data.csv"

#: Competition-adjusted access surface, 680 rows, built by ``accessibility.py``.
ACCESS_PATH = ROOT / "scraped_data" / "suburb_accessibility.csv"
#: Department of Health GP catchment per suburb, 680 rows, built by ``gp_catchments.py``.
CATCHMENT_PATH = ROOT / "scraped_data" / "suburb_gp_catchment.csv"

# Shared thresholds -- Steps A, B and C must never diverge on these.
HIGH_PRESSURE_THRESHOLD = 5000
MAX_SHORTLIST = 50
# Option 2 map uses light green below ~$1,500; Step B cutoff for gap-fee viability:
MIXED_BILLING_MIN_INCOME_WEEKLY = 1800
FINAL_TOP_N = 10

PRESSURE_COL = "People_Per_GP_Ratio"

# --- Contradiction thresholds (stated, not implicit) -------------------------------
#: ``saturated_nearby`` fires at or above this many general practices within 2 km of the
#: suburb centroid. 10 is the count at which an "empty" suburb is plainly inside a
#: cluster rather than near one: 228 of the 680 suburbs clear it, and Croydon (27) clears
#: it by a wide margin while holding 1 practice inside its own boundary.
SATURATED_NEARBY_MIN_PRACTICES_2KM = 10
#: ``access_above_median`` fires at or above this percentile of ``access_general``.
#: 50 = better served than the median Sydney suburb once competition for each practice is
#: accounted for. It catches the case ``saturated_nearby`` cannot: Greystanes has only 5
#: practices within 2 km yet sits at percentile 85.7.
ACCESS_ABOVE_MEDIAN_PCT = 50.0

#: Appended to every shortlist CSV so a reader can see where each row's supply came from.
PROVENANCE_COLS = ["gp_data_status", "clinics_listed", "total_gp_is_floor"]

#: Read from ``ACCESS_PATH`` (the first four) and ``CATCHMENT_PATH`` (``gpc_name``).
#: Deliberately excludes that file's ``population`` / ``over_65_pct`` / ``gp_data_status``,
#: which would collide with ``eda_data.csv``.
ACCESS_SOURCE_COLS = [
    "practices_within_2km",
    "nearest_practice_km",
    "access_general",
    "access_general_pct",
]
CATCHMENT_SOURCE_COLS = ["gpc_name"]

#: Derived here from the two thresholds above.
CONTRADICTION_COLS = ["saturated_nearby", "access_above_median"]

#: Appended to every output CSV, in this order, after the provenance columns. An
#: interior-count row is never emitted without the access columns beside it.
ACCESS_CONTEXT_COLS = ACCESS_SOURCE_COLS + CATCHMENT_SOURCE_COLS + CONTRADICTION_COLS

#: Columns of the re-scrape work list, in order.
EXCLUDED_COLS = [
    "suburb",
    "population",
    "over_65_pct",
    "median_household_income_weekly",
    "clinics_listed",
    "gp_data_status",
] + ACCESS_CONTEXT_COLS

_STATUS_NOTE = {
    GP_STATUS_NO_DATA: "never appeared in the clinic scrape",
    GP_STATUS_COUNT_UNKNOWN: "clinics listed, every GP count blank",
}

#: Printed by Steps A, B and C, and by eda/generate_insight_plots.py. The point of the
#: rebuild in one block: no reader of a console log or a CSV should be able to repeat the
#: Croydon reading.
INTERIOR_SUPPLY_WARNING = f"""
!! SUBURB-INTERIOR SUPPLY COUNTS ARE NOT A MEASURE OF ACCESS !!
  total_gp, clinics_listed, gp_per_1000 and People_Per_GP_Ratio all count doctors inside
  one suburb polygon. Patients do not stop at the boundary, so none of them measures how
  much GP care a resident can reach.
  WORKED EXAMPLE, CROYDON: 1 general practice inside the suburb, 21 within 1.5 km,
  27 within 2 km. Croydon sits between Burwood and Ashfield in one of the most
  GP-saturated parts of Sydney, and it topped the earlier shortlist purely because the
  supply count stopped at its own border.
  READ ACCESS FROM THESE APPENDED COLUMNS INSTEAD:
    access_general        E2SFCA practices per capita reachable in a 20 min drive,
                          divided by the demand competing for them. HIGHER = BETTER
                          SERVED. Croydon 0.311 (percentile 85.6); Heathcote 0.114 (5.2).
    practices_within_2km  practices reachable within 2 km, whatever suburb they are in.
    nearest_practice_km   distance to the closest practice anywhere.
    gpc_name              GP catchment, so same-catchment substitutes read as substitutes
                          (Croydon, Burwood and Ashfield are all one catchment).
  CONTRADICTION FLAGS (thresholds stated, not implicit):
    saturated_nearby    = practices_within_2km >= {SATURATED_NEARBY_MIN_PRACTICES_2KM}
    access_above_median = access_general_pct    >= {ACCESS_ABOVE_MEDIAN_PCT:.0f}
  A row with either flag True has an apparent interior supply gap that its surroundings
  contradict. Source: scraped_data/suburb_accessibility.csv (accessibility.py) and
  scraped_data/suburb_gp_catchment.csv (gp_catchments.py). For a ranking that uses access
  rather than interior counts, see eda/site_scores_v2.csv.
""".strip()


def _require_file(path: Path, built_by: str) -> None:
    """Abort with the command that regenerates ``path`` rather than a bare traceback."""
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {path}. It carries the access columns that stop an interior GP "
            f"count being read as a supply gap (see INTERIOR_SUPPLY_WARNING). "
            f"Rebuild it from the project root with: python {built_by}"
        )


def load_access_context(
    access_path: Path = ACCESS_PATH,
    catchment_path: Path = CATCHMENT_PATH,
) -> pd.DataFrame:
    """
    One row per suburb: the access columns plus ``gpc_name``, ready to merge.

    No thresholds and no ranking here -- this is the measured access surface as
    ``accessibility.py`` and ``gp_catchments.py`` wrote it. Both sources cover all 680
    suburbs with no status filter, so unlike ``total_gp`` these columns exist for the 239
    ``no_data`` suburbs too.
    """
    _require_file(access_path, "accessibility.py")
    _require_file(catchment_path, "gp_catchments.py")

    acc = pd.read_csv(access_path)[["suburb"] + ACCESS_SOURCE_COLS]
    cat = pd.read_csv(catchment_path)[["suburb"] + CATCHMENT_SOURCE_COLS]
    for name, frame in (("suburb_accessibility.csv", acc), ("suburb_gp_catchment.csv", cat)):
        dupes = int(frame["suburb"].duplicated().sum())
        if dupes:
            raise ValueError(f"{name} has {dupes} duplicate suburb rows; cannot merge 1:1")
    return acc.merge(cat, on="suburb", how="outer", validate="1:1")


def add_access_context(
    df: pd.DataFrame, context: pd.DataFrame | None = None
) -> pd.DataFrame:
    """
    Return a copy of ``df`` with the access columns and the two contradiction flags.

    A left merge on ``suburb``, so row order and row count are untouched: this adds
    disclosure, it never adds, drops or re-ranks a candidate. Both flags are False where
    the underlying value is missing, so an absent measurement is never reported as
    "contradicted" -- but the merge is expected to be total and prints a warning if it is
    not, because a silently unmatched suburb would emit an interior count with no access
    column beside it, which is the exact failure being closed.
    """
    ctx = load_access_context() if context is None else context
    out = df.merge(ctx, on="suburb", how="left", validate="m:1")

    missing = out["access_general"].isna()
    if int(missing.sum()):
        names = ", ".join(out.loc[missing, "suburb"].astype(str).head(10))
        print(
            f"  WARNING: {int(missing.sum())} row(s) have no access measurement and so "
            f"carry an interior supply count with no access column beside it: {names}"
        )

    within_2km = pd.to_numeric(out["practices_within_2km"], errors="coerce")
    pct = pd.to_numeric(out["access_general_pct"], errors="coerce")
    out["saturated_nearby"] = (within_2km >= SATURATED_NEARBY_MIN_PRACTICES_2KM).fillna(
        False
    )
    out["access_above_median"] = (pct >= ACCESS_ABOVE_MEDIAN_PCT).fillna(False)
    return out


def load_eda_data(path: Path = DATA_PATH, *, with_access: bool = True) -> pd.DataFrame:
    """
    Read ``eda_data.csv``; abort if it predates the ``gp_data_status`` fix.

    ``with_access`` (default True) merges :func:`load_access_context` straight away, so
    every frame these three steps derive carries the access columns and no export can
    omit them. Pass False only to inspect the raw file.
    """
    df = pd.read_csv(path)
    require_status_column(df, path)
    return add_access_context(df) if with_access else df


def print_interior_count_warning() -> None:
    """Print :data:`INTERIOR_SUPPLY_WARNING`. Called by every script in this pipeline."""
    print(INTERIOR_SUPPLY_WARNING)


def print_saturated_report(label: str, frame: pd.DataFrame, max_named: int = 12) -> None:
    """
    Print how many rows of ``frame`` have an interior gap their surroundings contradict.

    Names them when there are few enough to name: on a 4-row shortlist "3 of 4 rows are
    contradicted" is the finding, and hiding the names behind a count would be the same
    omission in smaller print.
    """
    if "saturated_nearby" not in frame.columns:
        print(f"  {label}: no access columns merged, cannot report contradictions")
        return
    n = len(frame)
    sat = frame["saturated_nearby"].fillna(False).astype(bool)
    above = frame["access_above_median"].fillna(False).astype(bool)
    either = sat | above
    print(
        f"  {label} ({n} rows): "
        f"saturated_nearby (>= {SATURATED_NEARBY_MIN_PRACTICES_2KM} practices within "
        f"2 km) = {int(sat.sum())}; "
        f"access_above_median (>= percentile {ACCESS_ABOVE_MEDIAN_PCT:.0f}) = "
        f"{int(above.sum())}; either = {int(either.sum())} of {n}"
    )
    if int(either.sum()) and int(either.sum()) <= max_named:
        cols = [
            c
            for c in ["suburb", "total_gp", PRESSURE_COL] + ACCESS_CONTEXT_COLS
            if c in frame.columns
        ]
        rows = frame.loc[either, cols].copy()
        if PRESSURE_COL in rows.columns:
            rows[PRESSURE_COL] = pd.to_numeric(
                rows[PRESSURE_COL], errors="coerce"
            ).round(0)
        print(rows.to_string(index=False))


def add_pressure_metric(df: pd.DataFrame) -> pd.DataFrame:
    """
    Return a copy with numeric ``total_gp`` / ``population`` and ``People_Per_GP_Ratio``.

    The ratio is computed for every row, including rows whose supply is unknown, but it
    is meaningful **only** where ``gp_data_status == 'ok'``. On the other rows it exists
    purely so the reports can show how big the pool would have been without the status
    filter. Never rank, threshold or colour on those values.

    A blank / unparseable ``total_gp`` stays ``NaN`` -- it is deliberately **not**
    filled with 0. ``fillna(0)`` here would turn "we do not know how many doctors" into
    "no doctors" and hence ``People_Per_GP_Ratio == population``, i.e. maximum pressure,
    even on a row whose ``gp_data_status`` says ``ok``; that is the exact coercion this
    pipeline exists to remove. Left as ``NaN`` the ratio is ``NaN``, every ``>``
    comparison against the threshold is False, and :func:`pool_eligible_mask` sends the
    row to the re-scrape work list instead of the ranked pool.
    """
    out = df.copy()
    out["total_gp"] = pd.to_numeric(out["total_gp"], errors="coerce").clip(lower=0)
    out["population"] = pd.to_numeric(out["population"], errors="coerce")
    out[PRESSURE_COL] = out["population"] / (out["total_gp"] + 1)
    return out


def pool_eligible_mask(df: pd.DataFrame) -> pd.Series:
    """
    Rows whose GP supply may be ranked: status ``ok`` **and** a known numeric ``total_gp``.

    :func:`gp_supply.rankable_mask` is the canonical status rule; this adds the value
    check that belongs with it. A row flagged ``ok`` whose ``total_gp`` is blank or
    unparseable has no supply number to rank on, so it is held out alongside the
    never-scraped suburbs and appears on the re-scrape work list -- rather than being
    read as zero supply and promoted to the top of the shortlist.
    """
    known_gp = pd.to_numeric(df["total_gp"], errors="coerce").notna()
    return rankable_mask(df) & known_gp


def income_weekly(df: pd.DataFrame) -> pd.Series:
    """Numeric ``median_household_income_weekly``; unparseable values become NaN."""
    return pd.to_numeric(df["median_household_income_weekly"], errors="coerce")


def output_columns(
    base: list[str], df: pd.DataFrame, tail: list[str] | None = None
) -> list[str]:
    """
    ``base``, then the provenance columns, then ``tail``, then the access columns.

    Every CSV these three steps write goes through here, which is what guarantees that an
    interior supply count is never exported without ``practices_within_2km``,
    ``nearest_practice_km``, ``access_general``, ``gpc_name`` and the contradiction flags
    on the same row.

    ``tail`` is for a column a caller used to place after the provenance block (Step B's
    ``filter_reason``). It sits before the access columns so that the access block is
    strictly last and no pre-existing column changes position -- these columns are
    appended, and appended means at the end.
    """
    ordered = (
        list(base)
        + [c for c in PROVENANCE_COLS if c not in base]
        + list(tail or [])
        + list(ACCESS_CONTEXT_COLS)
    )
    seen: set[str] = set()
    return [
        c for c in ordered if c in df.columns and not (c in seen or seen.add(c))
    ]


def write_excluded_work_list(df: pd.DataFrame, out_csv: Path = EXCLUDED_CSV) -> pd.DataFrame:
    """
    Write the suburbs held out of ranking, biggest population first.

    They are candidates whose GP supply is unknown, NOT rejects: this file is the
    re-scrape work list. Returns the frame that was written.
    """
    held = df.loc[~pool_eligible_mask(df)].copy()
    held["population"] = pd.to_numeric(held["population"], errors="coerce")
    held = held.sort_values(
        by=["population", "suburb"], ascending=[False, True], na_position="last"
    )
    out = held[[c for c in EXCLUDED_COLS if c in held.columns]]
    out.to_csv(out_csv, index=False)
    return out


def build_step_a_pool(
    df: pd.DataFrame,
    *,
    excluded_csv: Path | None = EXCLUDED_CSV,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    Step A pool: ranking-eligible suburbs above ``HIGH_PRESSURE_THRESHOLD``.

    Order of operations matters. The status filter is applied *first*, so a suburb whose
    ``total_gp`` is unknown -- by status or by a blank cell -- can never reach the
    threshold test; the survivors are then
    sorted by ``People_Per_GP_Ratio`` and capped at ``MAX_SHORTLIST``.

    Writes the re-scrape work list to ``excluded_csv`` (pass ``None`` to skip) and, when
    ``verbose``, prints pool size before/after the status filter, the count held out per
    status, the interior-count warning and how many pooled rows are contradicted by their
    2 km practice count.

    ``People_Per_GP_Ratio`` is an interior-supply metric and this function does not fix
    that -- it is the documented Option 1 rule and the task here is disclosure, not
    re-ranking. Every row returned carries the access columns from
    :func:`add_access_context`, so a reader can see immediately that (for instance)
    Croydon's pressure rank comes from a boundary, not from a shortage.
    """
    scored = add_pressure_metric(df)
    eligible = pool_eligible_mask(scored)
    over_threshold = scored[PRESSURE_COL] > HIGH_PRESSURE_THRESHOLD

    pool = scored.loc[eligible & over_threshold].sort_values(PRESSURE_COL, ascending=False)
    capped = pool.head(MAX_SHORTLIST) if len(pool) > MAX_SHORTLIST else pool

    written = (
        write_excluded_work_list(scored, excluded_csv) if excluded_csv is not None else None
    )
    if verbose:
        _print_pool_report(scored, eligible, over_threshold, pool, capped, excluded_csv, written)

    return capped.copy()


def split_mixed_billing(pool: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Step B split of a Step A pool on ``MIXED_BILLING_MIN_INCOME_WEEKLY``.

    Returns ``(primary, social_need)``. ``primary`` is known income at or above the
    floor; ``social_need`` is everything else (below the floor, or income missing) and
    carries a ``filter_reason`` column. Neither split touches GP data status -- the pool
    handed in is already restricted to rankable suburbs.
    """
    inc = income_weekly(pool)
    primary_mask = inc.notna() & (inc >= MIXED_BILLING_MIN_INCOME_WEEKLY)

    primary = pool.loc[primary_mask].copy()
    social = pool.loc[~primary_mask].copy()
    social["filter_reason"] = [
        "income_below_1800" if pd.notna(v) else "income_missing"
        for v in inc.loc[~primary_mask]
    ]
    return primary, social


def _print_pool_report(
    scored: pd.DataFrame,
    eligible: pd.Series,
    over_threshold: pd.Series,
    pool: pd.DataFrame,
    capped: pd.DataFrame,
    excluded_csv: Path | None,
    written: pd.DataFrame | None,
) -> None:
    """Print the pool / exclusion accounting shared by Steps A, B and C."""
    held_out = int((~eligible).sum())
    breakdown = "\n".join(f"  {line}" for line in status_summary(scored).splitlines())
    print(
        f"Step A pool: {PRESSURE_COL} = population / (total_gp + 1) > "
        f"{HIGH_PRESSURE_THRESHOLD}\n"
        f"  Suburbs read: {len(scored)}  ({DATA_PATH})\n"
        f"  gp_data_status breakdown:\n{breakdown}\n"
        f"  Pool before status filter: {int(over_threshold.sum()):4d} over threshold "
        f"(of {len(scored)} suburbs)\n"
        f"  Pool after  status filter: {len(pool):4d} over threshold "
        f"(of {int(eligible.sum())} rankable suburbs)\n"
        f"  Step A pool, capped at MAX_SHORTLIST={MAX_SHORTLIST}: {len(capped)} rows\n"
        f"  Held out of ranking: {held_out} suburbs"
    )
    for status, note in _STATUS_NOTE.items():
        is_status = scored["gp_data_status"] == status
        n = int(is_status.sum())
        if not n:
            continue
        would_have = int((is_status & over_threshold).sum())
        print(
            f"    {status:18s} {n:4d}  ({note}; "
            f"{would_have} would have cleared the threshold)"
        )
    blank_gp = rankable_mask(scored) & scored["total_gp"].isna()
    n_blank = int(blank_gp.sum())
    if n_blank:
        print(
            f"    {'ok / total_gp blank':18s} {n_blank:4d}  (status 'ok' but no GP count "
            f"to rank on; NOT read as zero supply)"
        )
    print(
        "  Those suburbs are the re-scrape work list -- candidates whose GP supply is\n"
        "  unknown, NOT rejects. Scrape them and they become eligible for the shortlist."
    )
    if excluded_csv is not None and written is not None:
        print(f"  Work list ({len(written)} rows)  ->  {excluded_csv.resolve()}")

    print()
    print_interior_count_warning()
    print("\nContradiction accounting for this pool:")
    print_saturated_report("Step A pool", capped)


def _self_report() -> None:
    """
    Print the disclosure and the contradiction accounting for all 680 suburbs.

    This module is a library, so ``python eda/supply_gap_pool.py`` used to print nothing.
    It now prints the interior-count warning it hands to Steps A, B and C plus a
    study-area-wide count of contradicted suburbs, so the thresholds can be checked
    without running a step. Writes no files and changes no state.
    """
    df = load_eda_data()
    scored = add_pressure_metric(df)
    print_interior_count_warning()
    print("\nContradiction accounting over the whole study area:")
    print_saturated_report("all suburbs in eda_data.csv", scored, max_named=0)
    print_saturated_report(
        f"  of which over {PRESSURE_COL} > {HIGH_PRESSURE_THRESHOLD} (any status)",
        scored.loc[scored[PRESSURE_COL] > HIGH_PRESSURE_THRESHOLD],
        max_named=0,
    )
    print_saturated_report(
        "  of which rankable on supply (gp_data_status 'ok', total_gp known)",
        scored.loc[pool_eligible_mask(scored)],
        max_named=0,
    )
    worst = scored.loc[pool_eligible_mask(scored)].nlargest(10, PRESSURE_COL)
    print(
        f"\nTop 10 on {PRESSURE_COL} (an INTERIOR count), with the access measurement "
        f"beside it:"
    )
    cols = ["suburb", "population", "total_gp", PRESSURE_COL] + ACCESS_CONTEXT_COLS
    shown = worst[[c for c in cols if c in worst.columns]].copy()
    shown[PRESSURE_COL] = shown[PRESSURE_COL].round(0)
    print(shown.to_string(index=False))


if __name__ == "__main__":
    _self_report()
