"""
Insight plots for GP underprovision (population, density, seniors, income vs GP access).

Reads: ../scraped_data/eda_data.csv
Writes PNGs to: ./plots/
Updates ../scraped_data/eda_data.csv with column ``opportunity_score`` (density/income/age min-max composite).

Writes opportunity tables to: ./
  - opportunity_high_pop_low_gp.csv
  - opportunity_high_elderly_pct_low_gp.csv
  - opportunity_high_density_low_gp.csv
  - opportunity_all_signals.csv  (all suburbs in cohort + three scores)
  - underprovided_suburbs.csv
  - weighted_opportunity_scores.csv  (MCDA: min-max 0-1 criteria + weighted composite)
  - OPPORTUNITY_SCORING.md  (methodology for reports; regenerated in full, see build_scoring_doc)

Every one of those CSVs now carries the access columns from ``supply_gap_pool``
(``practices_within_2km``, ``nearest_practice_km``, ``access_general``,
``access_general_pct``, ``gpc_name``, ``saturated_nearby``, ``access_above_median``), so a
low-supply row can never be read without the measurement that contradicts or confirms it.
``scraped_data/eda_data.csv`` is deliberately NOT given those columns: it is the shared
input frame for the whole project and ``scraped_data/suburb_accessibility.csv`` is the
single copy of the access surface. Only ``opportunity_score`` is written back there, as
before.

SUBURB-INTERIOR SUPPLY COUNTS ARE NOT A MEASURE OF ACCESS
---------------------------------------------------------
Every underprovision signal in this file is built on ``gp_per_1000``, i.e. ``total_gp``
divided by population -- doctors counted **inside one suburb polygon**. Patients do not
stop at the boundary, so ``gp_per_1000`` measures tenancy location, not availability of
care. It is retained here unchanged (these are the documented EDA plots) but every table
and every figure now says so.

WORKED EXAMPLE, CROYDON: **1** general practice inside the suburb, **21 within 1.5 km, 27
within 2 km**. Croydon sits between Burwood and Ashfield -- all three are in the same
Department of Health GP catchment -- in one of the most GP-saturated parts of Sydney. On
``gp_per_1000`` it reads as underprovided; its measured access is percentile **85.6** of
the 680 suburbs. The earlier answer ranked it #1 on precisely this error.

Read ``access_general`` (E2SFCA practices per capita reachable in a 20 min drive, divided
by the demand competing for them; HIGHER = BETTER SERVED) instead of ``gp_per_1000``, and
``eda/site_scores_v2.csv`` instead of these shortlists, for a ranking built on access.

GP-data status rule (see ``gp_supply.py``)
-----------------------------------------
239 of 680 suburbs were prompted for clinic data but never returned a single clinic row, so
their ``total_gp`` is 0 because nothing was collected -- not because there are no doctors.
``total_gp`` measures supply **only** where ``gp_data_status == 'ok'``.

Every output here that reads GP supply is therefore restricted to ``rankable_mask(df)``
(status ``ok``), on top of the existing population >= 1000 cohort rule:

  - ``gp_per_1000``            -- NaN outside status ``ok`` instead of a fake 0
  - ``sig_pop_lowgp`` / ``sig_65_lowgp`` / ``sig_density_lowgp``
  - the three ``opportunity_*.csv`` shortlists and ``opportunity_all_signals.csv``
  - ``underprovided_suburbs.csv``
  - ``weighted_opportunity_scores.csv`` (MCDA; ``total_gp`` is 35% of the composite,
    lower-is-better, so an unscraped suburb would score a free 0.35)

``opportunity_score`` in ``eda_data.csv`` is the one exception: it is density + income +
age with **no supply term**, so it is still written for all 680 rows. ``gp_data_status``,
``clinics_listed`` and ``total_gp_is_floor`` are carried into every export so the numbers
stay interpretable.

Run from project root:
  python eda/generate_insight_plots.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.transforms import Bbox

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gp_supply import (  # noqa: E402  -- sys.path must be set before this import
    GP_STATUS_COUNT_UNKNOWN,
    GP_STATUS_NO_DATA,
    GP_STATUS_OK,
    rankable_mask,
    require_status_column,
    status_summary,
)
from supply_gap_pool import (  # noqa: E402  -- eda/ added to sys.path above
    ACCESS_CONTEXT_COLS,
    add_access_context,
    print_interior_count_warning,
    print_saturated_report,
)

DATA_PATH = ROOT / "scraped_data" / "eda_data.csv"
OUT_DIR = Path(__file__).resolve().parent / "plots"
OUT_DIR.mkdir(parents=True, exist_ok=True)
EDA_DIR = Path(__file__).resolve().parent
# Suburbs below this population excluded from thresholds & flags (unstable gp_per_1000)
MIN_POP_FLAGS = 1000

# Minimum population for ranking tables (stable rates)
MIN_POP_RANK = 1000

# How many suburbs to keep in each "opportunity" shortlist CSV
TOP_OPPORTUNITY_N = 40

# Columns coerced to numeric on load
NUMERIC_COLS = (
    "total_gp",
    "population",
    "over_65_pct",
    "spatial_density",
    "median_household_income_weekly",
    "median_family_income_weekly",
    "median_personal_income_weekly",
    "area",
)

# Provenance carried into every export, so a reader can tell "no doctors" from "no data"
# without going back to eda_data.csv.
STATUS_COLS = ("gp_data_status", "clinics_listed", "total_gp_is_floor")

# Why a suburb is not rankable on supply (printed, never dropped silently).
EXCLUSION_REASONS = {
    GP_STATUS_NO_DATA: "never appeared in the clinic scrape",
    GP_STATUS_COUNT_UNKNOWN: "clinics listed but no usable doctor count",
}

SIGNAL_COLS = ("sig_pop_lowgp", "sig_65_lowgp", "sig_density_lowgp")

# Axes built from a count of doctors inside the suburb polygon. A figure with one of these
# on an axis gets the interior-count caption stamped on it, because a PNG travels into
# slide decks without its console log or its CSV.
INTERIOR_SUPPLY_AXES = ("total_gp", "gp_per_1000")
FIGURE_INTERIOR_NOTE = (
    "Interior supply count, not access: counts doctors inside the suburb polygon only. "
    "Croydon has 1 practice inside, 21 within 1.5 km, 27 within 2 km "
    "(access percentile 85.6). Read access_general in the exported CSVs."
)

# --- MCDA weights (sum to 1.0); each criterion scaled to [0, 1] within cohort ---
# Cohort: population >= MIN_POP_RANK AND gp_data_status == 'ok' (supply is a criterion).
# Scaling: cannot add $ income to % age without common units.
# Higher is better:  (x - min) / (max - min)
# Lower is better:   (max - x) / (max - min)
# If max == min, score = 0.5 (neutral). Missing values imputed to cohort median before min/max.
W_GP_SUPPLY = 0.35  # total GP count (lower -> less competition signal in data)
W_INCOME = 0.25  # household income (higher vs lower strategy)
W_SENIORS = 0.20  # % aged 65+ (higher is better)
W_POPULATION = 0.15  # population (higher is better)
W_AREA = 0.05  # area km2 (smaller is better -> lower-is-better scaling)

# Final column `opportunity_score` in eda_data.csv (all suburbs; min-max across full file)
# Score = w1*Norm(spatial_density) + w2*Norm(household_income) + w3*Norm(over_65_pct); all higher-better.
# No supply term, so it stays defined for every row whatever its gp_data_status.
W_OPP_DENSITY = 1.0 / 3.0
W_OPP_INCOME = 1.0 / 3.0
W_OPP_AGE = 1.0 / 3.0

# --- Scatter label placement ---
# Labels used to overprint each other (Bankstown / Castle Hill / Auburn in
# 01_population_vs_total_gp.png). Ring styling is deliberately untouched.
LABEL_FONTSIZE = 7
MAX_SCATTER_LABELS = 12
# Skip a label whose marker sits within this many pixels of an already-labelled marker.
MIN_LABEL_POINT_SEP_PX = 13.0
# Clearance kept between two label boxes.
LABEL_PAD_PX = 1.5
# A label pushed at least this far (points) gets a hairline leader back to its marker.
LABEL_LEADER_DY_PT = 11.0
# Attribution guard, in pixels. The ha="left"/"right" offsets below displace text sideways
# by up to a full text width (~90 px for "Wentworth Point") at a dy of only +4/-5, so a
# label can end up closer to a neighbouring highlighted ring than to its own marker -- and
# a reader attributes a label to whichever ring is nearest. A candidate whose text centre
# comes within this margin of another highlighted marker counts as ambiguous: an
# unambiguous offset is preferred, and an ambiguous placement always gets a leader line.
LABEL_ATTR_MARGIN_PX = 6.0
# Candidate (dx, dy, ha, va) offsets in points, tried in order until one fits.
LABEL_OFFSETS = (
    (6.0, 4.0, "left", "bottom"),
    (6.0, -5.0, "left", "top"),
    (-6.0, 4.0, "right", "bottom"),
    (-6.0, -5.0, "right", "top"),
    (7.0, 13.0, "left", "bottom"),
    (7.0, -14.0, "left", "top"),
    (-7.0, 13.0, "right", "bottom"),
    (-7.0, -14.0, "right", "top"),
    (0.0, 11.0, "center", "bottom"),
    (0.0, -12.0, "center", "top"),
)

# Two trailing spaces = a markdown hard line break. Kept as a named constant because an
# editor that strips trailing whitespace silently rejoins the formula onto its label line.
MD_BREAK = "  "

OPPORTUNITY_SCORE_REPORT_BLURB = """
### Final `opportunity_score` (in `scraped_data/eda_data.csv`)

For **every suburb** in `eda_data.csv`:

`opportunity_score = (1/3)×Norm(spatial_density) + (1/3)×Norm(median_household_income_weekly) + (1/3)×Norm(over_65_pct)`

- **Norm** = min-max to **[0, 1]** using **all suburbs** in the file (not only population ≥ 1000).
- **Higher raw value → higher Norm** for each of the three inputs.
- **Income:** weekly household income; `0` is treated as missing before scaling.
- **No GP-supply term.** This score never reads `total_gp`, so it stays defined for all
  suburbs whatever their `gp_data_status`. Read it next to `gp_data_status` though: a high
  score on a `no_data` suburb says nothing about the competition there.
- Recomputed when you run `python eda/generate_insight_plots.py` or when you re-export from `pre-processing.ipynb`.
""".strip()

WEIGHTED_SCORE_REPORT_BLURB = f"""
### MCDA opportunity score (EDA)

**Why min-max:** You cannot add incomparable units (e.g. income in dollars to % over 65).
Each criterion is scaled to **0-1** within the analysis cohort (population >= 1000 **and**
`gp_data_status == "ok"` - see the status filter below).

**Scaling (within cohort):**

- **Higher is better** (income, population, % aged 65+):{MD_BREAK}
  `(x - min) / (max - min)`

- **Lower is better** (total GP count, area km²):{MD_BREAK}
  `(max - x) / (max - min)`

**Composite:** Weighted sum of the 0-1 scores (weights sum to 1 → score in ~[0, 1]). Higher = better fit.

**Edge cases:** If `max == min` for a column, everyone gets **0.5** on that criterion. Missing values are imputed to the **cohort median** (per column) before min-max.

| Criterion | Weight | Direction | Raw variable |
|-----------|--------|-----------|--------------|
| Listed GP supply | 0.35 | Lower better | `total_gp` |
| Household income | 0.25 | Higher (mixed billing) or lower (community need) | `median_household_income_weekly` |
| Age demand | 0.20 | Higher better | `over_65_pct` |
| Population | 0.15 | Higher better | `population` |
| Area | 0.05 | Lower better (walk-in catchment) | `area` |

**Economic:** We include income to capture **financial viability** (mixed billing / gap fees).
`weighted_score_community_need` uses the **lower-is-better** min-max on income instead.
""".strip()

INTERIOR_COUNT_DOC = """
### Interior supply counts are NOT a measure of access

`total_gp`, `clinics_listed` and `gp_per_1000` count doctors **inside one suburb polygon**.
Patients do not stop at the boundary, so none of them measures how much GP care a resident
can reach. Every underprovision signal and MCDA supply criterion on this page is built on
that interior count.

**Worked example, Croydon.** **1** general practice inside the suburb; **21 within 1.5 km,
27 within 2 km**. Croydon, Burwood and Ashfield are all in the same Department of Health GP
catchment (`Strathfield - Burwood - Ashfield`, 40 practices for ~174,000 people), one of the
most GP-saturated parts of Sydney. On `gp_per_1000` Croydon reads as underprovided; its
measured access is percentile **85.6** of the 680 suburbs, better served than five suburbs
in six. The earlier shortlist ranked it **#1** on exactly this error.

**Read these columns instead.** Every CSV this script writes now carries them:

| Column | Meaning |
|---|---|
| `access_general` | E2SFCA practices per capita reachable within a 20 min drive, divided by the demand competing for them. **Higher = better served.** Croydon 0.311; Heathcote 0.114. |
| `access_general_pct` | `access_general` as a percentile of the 680 suburbs. |
| `practices_within_2km` | General practices within 2 km of the suburb centroid, whatever suburb they are filed in. |
| `nearest_practice_km` | Straight-line distance to the closest practice anywhere. |
| `gpc_name` | GP catchment, so same-catchment substitutes read as substitutes. |
| `saturated_nearby` | `practices_within_2km >= 10`. The interior gap is contradicted by the surroundings. |
| `access_above_median` | `access_general_pct >= 50`. Better served than the median Sydney suburb. |

Source: `scraped_data/suburb_accessibility.csv` (`accessibility.py`) and
`scraped_data/suburb_gp_catchment.csv` (`gp_catchments.py`); both cover all 680 suburbs with
no `gp_data_status` filter. Neither the signals nor the MCDA weights read any of these
columns - they are disclosure, so the thresholds on this page are unchanged. For a ranking
built on access rather than interior counts, see `eda/site_scores_v2.csv`.
""".strip()

REPORT_FOOTER = """
**For written reports:** We include median household income to capture **financial viability**
(mixed billing / ability to pay gap fees). For a **community-need** lens (access over
profitability), use `weighted_score_community_need`, which rewards **lower** income instead.

**Never present a low `total_gp` or a low `gp_per_1000` as an access finding.** It is a count
inside one polygon. Quote `access_general` / `practices_within_2km` beside it, and check
`saturated_nearby` before calling a suburb underserved.
""".strip()


def build_status_filter_blurb(df: pd.DataFrame, cohort_n: int) -> str:
    """The ``gp_data_status`` section of OPPORTUNITY_SCORING.md, with live counts."""
    counts = df["gp_data_status"].value_counts()
    n_ok = int(counts.get(GP_STATUS_OK, 0))
    n_unknown = int(counts.get(GP_STATUS_COUNT_UNKNOWN, 0))
    n_no_data = int(counts.get(GP_STATUS_NO_DATA, 0))
    return f"""
### GP-data status filter (`gp_data_status`)

Clinic data was collected suburb by suburb, and **{n_no_data} of {len(df)}** suburbs were
prompted but never returned a single clinic row. Their `total_gp` is `0` because nothing was
collected, not because there are no doctors - so any ranking that read `total_gp` as supply
promoted them straight to the top.

| `gp_data_status` | Suburbs | Meaning | Rankable on supply? |
|---|---|---|---|
| `ok` | {n_ok} | in the scrape; at least one clinic reported a usable GP count | **Yes** |
| `gp_count_unknown` | {n_unknown} | clinics found, but every GP count was blank / `unknown` | No |
| `no_data` | {n_no_data} | never appeared in the scrape; `total_gp` is not a measurement | No |

**Rule:** `total_gp == 0` means "zero doctors" **only** when `gp_data_status == "ok"`.
Never filter, rank, threshold or colour on `total_gp == 0` alone.

Every **supply-based** output of `eda/generate_insight_plots.py` is computed on
`gp_data_status == "ok"` **and** `population >= {MIN_POP_RANK}` - a cohort of **{cohort_n}** suburbs:

- `gp_per_1000` (`NaN` outside status `ok`, never a fake `0`)
- `sig_pop_lowgp`, `sig_65_lowgp`, `sig_density_lowgp`
- `opportunity_high_pop_low_gp.csv`, `opportunity_high_elderly_pct_low_gp.csv`,
  `opportunity_high_density_low_gp.csv`, `opportunity_all_signals.csv`
- `underprovided_suburbs.csv` (thresholds are percentiles of the cohort, so the excluded
  zeros no longer drag the 25th percentile down to `0.0`)
- `weighted_opportunity_scores.csv` (both MCDA lenses)

The only exception is `opportunity_score` above: no supply term, so it is still written for
all {len(df)} rows. `gp_data_status`, `clinics_listed` and `total_gp_is_floor` are carried
into every exported CSV so the exclusions are auditable.

`total_gp_is_floor == True` means the total is a **lower bound** (a clinic reported `>=6`, or
a listed clinic never reported a count), so real supply is at least that high.
""".strip()


def build_scoring_doc(df: pd.DataFrame, cohort_n: int) -> str:
    """
    Full text of ``eda/OPPORTUNITY_SCORING.md``.

    The file is rewritten on every run, so everything it should contain is generated here.
    The old code wrote only the MCDA blurb, which silently destroyed the hand-written
    ``opportunity_score`` section at the top of the file.
    """
    sections = [
        INTERIOR_COUNT_DOC,
        OPPORTUNITY_SCORE_REPORT_BLURB,
        WEIGHTED_SCORE_REPORT_BLURB,
        build_status_filter_blurb(df, cohort_n),
        REPORT_FOOTER,
    ]
    return "\n\n---\n\n".join(sections) + "\n"


def load_prepared() -> pd.DataFrame:
    """
    Read ``eda_data.csv``, derive ``gp_per_1000``, and merge the access columns.

    ``add_access_context`` is applied here, once, so that every table and figure derived
    below carries ``practices_within_2km`` / ``nearest_practice_km`` / ``access_general``
    / ``access_general_pct`` / ``gpc_name`` and the two contradiction flags. It is a left
    merge on ``suburb``: no row is added, dropped or re-ordered, and no threshold in this
    module reads any of those columns. Disclosure only.
    """
    if not DATA_PATH.exists():
        print(f"Missing {DATA_PATH}", file=sys.stderr)
        sys.exit(1)
    df = pd.read_csv(DATA_PATH)
    require_status_column(df, DATA_PATH)
    for c in NUMERIC_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["total_gp"] = df["total_gp"].fillna(0).clip(lower=0)
    # GPs per 1,000 residents. A supply measure, so it is undefined wherever total_gp
    # carries no information -- NaN, not 0: a fake 0 reads as "worst supply in Sydney".
    #
    # It is also an INTERIOR count and therefore not a measure of access: it divides the
    # doctors inside one polygon by the people inside the same polygon. Croydon scores as
    # underprovided on it with 1 practice inside, 21 within 1.5 km and 27 within 2 km.
    # access_general, merged in below, is the measure to read instead.
    df["gp_per_1000"] = np.where(
        rankable_mask(df) & (df["population"] > 0),
        1000.0 * df["total_gp"] / df["population"],
        np.nan,
    )
    return add_access_context(df)


def supply_cohort(df: pd.DataFrame, min_pop: int, label: str) -> pd.DataFrame:
    """
    Rows safe to rank on GP supply: ``gp_data_status == 'ok'`` and population >= ``min_pop``.

    Prints what was excluded and why -- nothing is dropped silently.
    """
    ok = rankable_mask(df)
    big = df["population"] >= min_pop
    keep = ok & big

    counts = df.loc[~ok, "gp_data_status"].value_counts()
    parts = [
        f"{int(counts.get(status, 0))} {status} ({reason})"
        for status, reason in EXCLUSION_REASONS.items()
        if int(counts.get(status, 0))
    ]
    parts.append(f"{int((ok & ~big).sum())} population < {min_pop} (unstable rates)")

    print(f"\n=== Supply cohort: {label} ===")
    print(
        f"  kept {int(keep.sum())} of {len(df)} suburbs "
        f"(gp_data_status == '{GP_STATUS_OK}' and population >= {min_pop})"
    )
    print("  excluded " + "; ".join(parts))
    return df.loc[keep].copy()


def export_three_opportunity_lists(r: pd.DataFrame, out_dir: Path, n: int) -> None:
    """
    Persist the three EDA targets (high demand axis vs low GP supply).

    ``r`` must already be the supply cohort (status ``ok``, population >= MIN_POP_RANK):
    every score below subtracts observed GP supply, so an unscraped suburb would win.

    Each score is:
      z(demand_feature) - z(gp_per_1000)
    Higher = more "high X but low GP" relative to the cohort.

    ``gp_per_1000`` is an INTERIOR count, so "low GP" here means "few doctors filed to
    this polygon", not "hard to reach a doctor" -- Croydon has 1 practice inside, 21
    within 1.5 km and 27 within 2 km. The access columns are appended to all four CSVs so
    the distinction travels with the data.
    """
    base_cols = [
        "suburb",
        "population",
        "over_65_pct",
        "spatial_density",
        "total_gp",
        "gp_per_1000",
    ]
    inc = "median_household_income_weekly"
    if inc in r.columns:
        base_cols = base_cols + [inc]
    base_cols = base_cols + list(STATUS_COLS)
    export_cols = [c for c in base_cols if c in r.columns]
    # Strictly last, after each file's own signal column, so no pre-existing column moves.
    access_cols = [c for c in ACCESS_CONTEXT_COLS if c in r.columns]

    # 1) High population, low GP (catchment / foot traffic story)
    top_pop = r.nlargest(n, "sig_pop_lowgp")
    top_pop[export_cols + ["sig_pop_lowgp"] + access_cols].to_csv(
        out_dir / "opportunity_high_pop_low_gp.csv", index=False
    )

    # 2) High elderly %, low GP (utilisation / chronic care story)
    top_65 = r.nlargest(n, "sig_65_lowgp")
    top_65[export_cols + ["sig_65_lowgp"] + access_cols].to_csv(
        out_dir / "opportunity_high_elderly_pct_low_gp.csv", index=False
    )

    # 3) High density, low GP (competition / access in compact areas)
    top_den = r.nlargest(n, "sig_density_lowgp")
    top_den[export_cols + ["sig_density_lowgp"] + access_cols].to_csv(
        out_dir / "opportunity_high_density_low_gp.csv", index=False
    )

    all_sig_cols = export_cols + list(SIGNAL_COLS) + access_cols
    present = [c for c in all_sig_cols if c in r.columns]
    r[present].sort_values("suburb").to_csv(out_dir / "opportunity_all_signals.csv", index=False)

    print(
        f"\n=== Opportunity shortlists (top {n} each; population >= {MIN_POP_RANK} "
        f"and gp_data_status == '{GP_STATUS_OK}', cohort n={len(r)}) ==="
    )
    print("Target 1: HIGH population, relatively LOW gp_per_1000  ->  sig_pop_lowgp")
    print("Target 2: HIGH share aged 65+, relatively LOW gp_per_1000  ->  sig_65_lowgp")
    print("Target 3: HIGH spatial density, relatively LOW gp_per_1000  ->  sig_density_lowgp")
    print(
        "  'LOW gp_per_1000' is an INTERIOR count and is NOT low access -- Croydon: 1\n"
        "  practice inside, 21 within 1.5 km, 27 within 2 km. Access is in the appended\n"
        f"  columns {', '.join(ACCESS_CONTEXT_COLS)}."
    )
    print(f"Saved: {out_dir / 'opportunity_high_pop_low_gp.csv'}")
    print(f"Saved: {out_dir / 'opportunity_high_elderly_pct_low_gp.csv'}")
    print(f"Saved: {out_dir / 'opportunity_high_density_low_gp.csv'}")
    print(f"Saved: {out_dir / 'opportunity_all_signals.csv'}")
    print("\nContradiction accounting for the files just written:")
    for name, frame in (
        ("opportunity_high_pop_low_gp.csv", top_pop),
        ("opportunity_high_elderly_pct_low_gp.csv", top_65),
        ("opportunity_high_density_low_gp.csv", top_den),
        ("opportunity_all_signals.csv", r),
    ):
        print_saturated_report(name, frame, max_named=0)


def zscore(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    m, sd = s.mean(), s.std(ddof=0)
    if sd == 0 or np.isnan(sd):
        return s * 0.0
    return (s - m) / sd


def min_max_higher(s: pd.Series) -> pd.Series:
    """
    Higher raw values -> higher score in [0, 1]: (x - min) / (max - min).
    """
    v = pd.to_numeric(s, errors="coerce").astype(float)
    med = float(np.nanmedian(v.to_numpy()))
    v = v.fillna(med)
    lo, hi = float(v.min()), float(v.max())
    if hi == lo or not np.isfinite(lo) or not np.isfinite(hi):
        return pd.Series(0.5, index=s.index, dtype=float)
    out = (v - lo) / (hi - lo)
    return out.clip(0.0, 1.0)


def min_max_lower(s: pd.Series) -> pd.Series:
    """
    Lower raw values -> higher score in [0, 1]: (max - x) / (max - min).
    """
    v = pd.to_numeric(s, errors="coerce").astype(float)
    med = float(np.nanmedian(v.to_numpy()))
    v = v.fillna(med)
    lo, hi = float(v.min()), float(v.max())
    if hi == lo or not np.isfinite(lo) or not np.isfinite(hi):
        return pd.Series(0.5, index=s.index, dtype=float)
    out = (hi - v) / (hi - lo)
    return out.clip(0.0, 1.0)


def add_opportunity_score_three_criteria(df: pd.DataFrame) -> pd.DataFrame:
    """
    Per-suburb final opportunity score (same formula as pre-processing export).

    Norms are min-max on [0, 1] using **all rows** in ``df`` -- not only pop >= MIN_POP_RANK,
    and not only ``gp_data_status == 'ok'``: this score has no GP-supply term, so it is
    defined everywhere. Zero household income is treated as missing before scaling.
    """
    out = df.copy()
    norm_d = min_max_higher(out["spatial_density"])
    inc = pd.to_numeric(out["median_household_income_weekly"], errors="coerce").replace(
        0, np.nan
    )
    norm_i = min_max_higher(inc)
    norm_a = min_max_higher(out["over_65_pct"])
    out["opportunity_score"] = (
        W_OPP_DENSITY * norm_d + W_OPP_INCOME * norm_i + W_OPP_AGE * norm_a
    ).round(6)
    return out


def persist_opportunity_score_to_eda_csv() -> None:
    """
    Recompute ``opportunity_score`` on disk (``scraped_data/eda_data.csv``).

    Written for every row: density + income + age, no supply term, so ``gp_data_status``
    does not gate it. The status columns are preserved in the rewritten file so the score
    stays interpretable next to them.
    """
    if not DATA_PATH.exists():
        print(f"Skip opportunity_score persist: missing {DATA_PATH}", file=sys.stderr)
        return
    raw = pd.read_csv(DATA_PATH)
    require_status_column(raw, DATA_PATH)
    for c in NUMERIC_COLS:
        if c in raw.columns:
            raw[c] = pd.to_numeric(raw[c], errors="coerce")
    raw["total_gp"] = raw["total_gp"].fillna(0).clip(lower=0)
    raw = add_opportunity_score_three_criteria(raw)
    raw.to_csv(DATA_PATH, index=False)
    print(
        f"Updated column opportunity_score in {DATA_PATH.resolve()} "
        f"(all {len(raw)} rows; density+income+age, no supply term)"
    )


def build_weighted_opportunity_scores(df: pd.DataFrame) -> pd.DataFrame:
    """
    MCDA composite on [0, 1]-scaled criteria (min-max within cohort).

    Cohort: population >= MIN_POP_RANK **and** ``gp_data_status == 'ok'``. Supply is 35% of
    the composite and lower-is-better, so a suburb whose ``total_gp`` is 0 for lack of data
    would collect a free 0.35 -- that is exactly how tiny wealthy enclaves (Point Piper,
    Clontarf, Balgowlah Heights, Middle Cove) used to top the mixed-billing lens.
    See WEIGHTED_SCORE_REPORT_BLURB / OPPORTUNITY_SCORING.md.
    """
    r = supply_cohort(df, MIN_POP_RANK, "MCDA weighted scores")
    if r.empty:
        return r

    # Supply: fewer listed GPs -> higher score (observed counts only)
    mm_total_gp = min_max_lower(r["total_gp"].fillna(0))

    inc_col = "median_household_income_weekly"
    if inc_col in r.columns:
        inc = pd.to_numeric(r[inc_col], errors="coerce").replace(0, np.nan)
        inc_med = float(inc.median()) if inc.notna().any() else 0.0
        inc = inc.fillna(inc_med)
        mm_inc_high = min_max_higher(inc)
        mm_inc_low = min_max_lower(inc)
    else:
        mm_inc_high = pd.Series(0.5, index=r.index, dtype=float)
        mm_inc_low = pd.Series(0.5, index=r.index, dtype=float)

    pct = pd.to_numeric(r["over_65_pct"], errors="coerce")
    pct_med = float(pct.median()) if pct.notna().any() else 0.0
    pct = pct.fillna(pct_med)
    r["seniors_estimated_count"] = r["population"].astype(float) * pct / 100.0
    mm_over_65 = min_max_higher(pct)

    mm_pop = min_max_higher(r["population"].astype(float))

    if "area" in r.columns:
        area = pd.to_numeric(r["area"], errors="coerce").replace(0, np.nan)
        a_med = float(area.median()) if area.notna().any() else 1.0
        area = area.fillna(a_med)
        mm_area = min_max_lower(area)
    else:
        mm_area = pd.Series(0.5, index=r.index, dtype=float)

    r["mcda_mm_total_gp_lower_better"] = mm_total_gp
    r["mcda_mm_income_higher_better"] = mm_inc_high
    r["mcda_mm_income_lower_better"] = mm_inc_low
    r["mcda_mm_over_65_pct_higher_better"] = mm_over_65
    r["mcda_mm_population_higher_better"] = mm_pop
    r["mcda_mm_area_lower_better"] = mm_area

    r["weighted_score_mixed_billing"] = (
        W_GP_SUPPLY * mm_total_gp
        + W_INCOME * mm_inc_high
        + W_SENIORS * mm_over_65
        + W_POPULATION * mm_pop
        + W_AREA * mm_area
    )
    r["weighted_score_community_need"] = (
        W_GP_SUPPLY * mm_total_gp
        + W_INCOME * mm_inc_low
        + W_SENIORS * mm_over_65
        + W_POPULATION * mm_pop
        + W_AREA * mm_area
    )

    r["contrib_total_gp_w"] = W_GP_SUPPLY * mm_total_gp
    r["contrib_income_mixed_billing_w"] = W_INCOME * mm_inc_high
    r["contrib_income_community_need_w"] = W_INCOME * mm_inc_low
    r["contrib_over_65_pct_w"] = W_SENIORS * mm_over_65
    r["contrib_population_w"] = W_POPULATION * mm_pop
    r["contrib_area_w"] = W_AREA * mm_area

    return r


def print_corr(label: str, x: pd.Series, y: pd.Series) -> None:
    mask = x.notna() & y.notna()
    if mask.sum() < 3:
        print(f"{label}: n too small")
        return
    r = np.corrcoef(x[mask], y[mask])[0, 1]
    print(f"{label}: Pearson r = {r:.4f} (n={mask.sum()})")


def _padded(bbox: Bbox, pad: float) -> Bbox:
    """Bbox grown by ``pad`` pixels on every side (collision clearance)."""
    return Bbox.from_extents(bbox.x0 - pad, bbox.y0 - pad, bbox.x1 + pad, bbox.y1 + pad)


def _box_inside(box: Bbox, container: Bbox) -> bool:
    """True if ``box`` sits wholly inside ``container`` (so it will not be clipped)."""
    return container.contains(box.x0, box.y0) and container.contains(box.x1, box.y1)


def label_attribution(box: Bbox, own: int, marker_px: np.ndarray) -> tuple[float, float]:
    """
    How a reader will attribute the label drawn in ``box``.

    ``marker_px`` holds the pixel positions of **every** highlighted marker on the axes and
    ``own`` indexes the one this label belongs to. Returns
    ``(own_distance, nearest_other_distance)`` measured from the label's centre. A label is
    read off whichever ring is closest to it, so ``nearest_other <= own`` means the label is
    being credited to the wrong suburb.
    """
    cx, cy = (box.x0 + box.x1) / 2.0, (box.y0 + box.y1) / 2.0
    d = np.hypot(marker_px[:, 0] - cx, marker_px[:, 1] - cy)
    others = np.delete(d, own)
    return float(d[own]), float(others.min()) if others.size else float("inf")


def label_is_ambiguous(box: Bbox, own: int, marker_px: np.ndarray) -> bool:
    """True if another highlighted marker is (near enough) as close to ``box`` as its own."""
    own_dist, other_dist = label_attribution(box, own, marker_px)
    return other_dist <= own_dist + LABEL_ATTR_MARGIN_PX


def place_labels(
    ax: plt.Axes,
    fig: plt.Figure,
    points: pd.DataFrame,
    xcol: str,
    ycol: str,
    max_labels: int = MAX_SCATTER_LABELS,
) -> tuple[int, int]:
    """
    Annotate ``points`` without letting the labels overprint each other.

    ``points`` must be pre-sorted most-important-first. For each point the offsets in
    ``LABEL_OFFSETS`` are tried in order and the first whose real (measured) text box
    clears every placed box, stays inside the axes **and** reads unambiguously as its own
    marker's label wins. A point is skipped when its marker is within
    ``MIN_LABEL_POINT_SEP_PX`` of an already-labelled marker, when no offset fits, or once
    ``max_labels`` labels are down -- dropping a label beats the old Bankstown / Castle
    Hill / Auburn pile-up.

    Not overprinting is not enough. The offsets shift text sideways by up to a full text
    width, so a label that overlaps nothing could still sit nearest a *different*
    highlighted ring (28 labels across the five scatters did). Every placement is therefore
    measured with :func:`label_attribution`, and anything a reader could misattribute gets
    a hairline leader back to its own marker.

    Returns ``(placed, skipped)``.
    """
    if points.empty:
        return 0, 0

    fig.canvas.draw()  # settle transforms and get a usable renderer
    renderer = fig.canvas.get_renderer()

    pixels = ax.transData.transform(points[[xcol, ycol]].to_numpy(dtype=float))
    # Markers are obstacles too: never park a label on top of another highlighted point.
    obstacles = [Bbox.from_bounds(px - 5.0, py - 5.0, 10.0, 10.0) for px, py in pixels]
    placed_boxes: list[Bbox] = []
    labelled_pixels: list[tuple[float, float]] = []
    placed = 0
    skipped = 0

    for i, ((_, row), (px, py)) in enumerate(zip(points.iterrows(), pixels)):
        if placed >= max_labels:
            skipped += 1
            continue
        if any(
            (px - lx) ** 2 + (py - ly) ** 2 < MIN_LABEL_POINT_SEP_PX**2
            for lx, ly in labelled_pixels
        ):
            skipped += 1
            continue

        text = str(row["suburb"])[:18]
        chosen: tuple[tuple[float, float, str, str], Bbox] | None = None
        # Best of the offsets that fit and overlap nothing but still sit about as close to
        # another ring as to their own -- used only if no unambiguous offset fits, and
        # always with a leader line.
        ambiguous: tuple[tuple[float, float, str, str], Bbox] | None = None
        best_margin = -np.inf
        fallback: tuple[tuple[float, float, str, str], Bbox] | None = None
        for offset in LABEL_OFFSETS:
            dx, dy, ha, va = offset
            probe = ax.annotate(
                text,
                (row[xcol], row[ycol]),
                fontsize=LABEL_FONTSIZE,
                alpha=0.85,
                xytext=(dx, dy),
                textcoords="offset points",
                ha=ha,
                va=va,
            )
            box = _padded(probe.get_window_extent(renderer=renderer), LABEL_PAD_PX)
            probe.remove()
            if any(box.overlaps(b) for b in placed_boxes + obstacles):
                continue
            if _box_inside(box, ax.bbox):
                own_dist, other_dist = label_attribution(box, i, pixels)
                margin = other_dist - own_dist
                if margin > LABEL_ATTR_MARGIN_PX:
                    chosen = (offset, box)
                    break
                if margin > best_margin:
                    # Usable, but only with a leader saying which ring it belongs to.
                    # Keep the *least* ambiguous of these, not merely the first.
                    best_margin = margin
                    ambiguous = (offset, box)
            elif fallback is None and _box_inside(box, fig.bbox):
                # Spills past the axes but is still fully on the canvas: last resort.
                # Anything past the canvas edge would be clipped mid-word, so it is
                # dropped instead.
                fallback = (offset, box)

        if chosen is None:
            chosen = ambiguous or fallback
        if chosen is None:
            skipped += 1
            continue

        (dx, dy, ha, va), box = chosen
        extra: dict[str, object] = {}
        if label_is_ambiguous(box, i, pixels) or abs(dy) >= LABEL_LEADER_DY_PT:
            # Either pushed clear of a crowded neighbourhood, or another highlighted ring
            # is as close to the text as its own marker is. Tie it back, so the label can
            # never be read off the wrong suburb.
            extra["arrowprops"] = dict(
                arrowstyle="-", color="0.45", linewidth=0.5, shrinkA=1.0, shrinkB=4.0
            )
        ax.annotate(
            text,
            (row[xcol], row[ycol]),
            fontsize=LABEL_FONTSIZE,
            alpha=0.85,
            xytext=(dx, dy),
            textcoords="offset points",
            ha=ha,
            va=va,
            **extra,
        )
        placed_boxes.append(box)
        labelled_pixels.append((px, py))
        placed += 1

    return placed, skipped


def omission_report(
    src: pd.DataFrame,
    kept: pd.Index,
    xcol: str,
    ycol: str,
    log_x: bool,
    log_y: bool,
) -> tuple[int, list[str]]:
    """
    Account for every row of ``src`` a figure cannot show, and why.

    ``kept`` is the index actually plotted. Returns ``(n_omitted, reasons)`` with the reasons
    ordered most-important-first; each omitted row is counted exactly once. Callers print this
    and stamp it on the figure, so no suburb ever leaves a plot unreported. Three things
    remove rows: ``gp_data_status != 'ok'`` (``gp_per_1000`` is NaN there, and ``total_gp`` is
    not a measurement), a missing x or y value, and a log axis, which cannot draw a value of
    0 -- including a *confirmed* ``total_gp == 0`` such as Bayview or Beacon Hill, so those
    are named rather than merely counted.
    """
    dropped = ~src.index.isin(kept)
    n_omitted = int(dropped.sum())
    reasons: list[str] = []
    if not n_omitted:
        return 0, reasons

    accounted = np.zeros(len(src), dtype=bool)

    def add(mask: np.ndarray, text: str) -> None:
        """Claim the still-unaccounted dropped rows in ``mask`` under one reason."""
        mask = np.asarray(mask) & dropped & ~accounted
        n = int(mask.sum())
        if not n:
            return
        accounted[mask] = True
        names = ", ".join(src.loc[mask, "suburb"].astype(str)) if "{names}" in text else ""
        reasons.append(text.format(n=n, names=names))

    if "gp_data_status" in src.columns:
        status = src["gp_data_status"]
        for value, why in EXCLUSION_REASONS.items():
            add(status.eq(value).to_numpy(), "{n} " + f"{value} ({why})")

    x = pd.to_numeric(src[xcol], errors="coerce").to_numpy(dtype=float)
    y = pd.to_numeric(src[ycol], errors="coerce").to_numpy(dtype=float)
    add(np.isnan(x) | np.isnan(y), "{n} missing " + f"{xcol} / {ycol}")

    for col, values, is_log in ((xcol, x, log_x), (ycol, y, log_y)):
        if not is_log:
            continue
        zero = values <= 0
        # A handful of confirmed zeros is worth naming; a crowd is not.
        few = int((zero & dropped & ~accounted).sum()) <= 4
        add(
            zero,
            "{n} with " + f"{col} == 0"
            + (" ({names})" if few else "")
            + " -- a real 0 cannot be drawn on a log axis",
        )
    add(np.ones(len(src), dtype=bool), "{n} unplottable for other reasons")
    return n_omitted, reasons


def scatter_with_marginals(
    df: pd.DataFrame,
    xcol: str,
    ycol: str,
    title: str,
    xlab: str,
    ylab: str,
    fname: str,
    log_x: bool = False,
    log_y: bool = False,
    highlight_mask: pd.Series | None = None,
    label_rank: pd.Series | None = None,
) -> None:
    """
    Scatter of ``ycol`` against ``xcol``, ringing and labelling the top-signal suburbs.

    Only ``gp_data_status == 'ok'`` rows are drawn: outside that status ``total_gp`` is not
    a measurement and ``gp_per_1000`` is NaN, and a marker plotted from a non-measurement
    reads as genuinely low supply. Whatever the figure cannot show is counted by
    :func:`omission_report`, printed per figure and stamped on the figure as a footnote.

    A figure with ``total_gp`` or ``gp_per_1000`` on either axis also gets
    :data:`FIGURE_INTERIOR_NOTE` stamped on it. Those axes are counts inside the suburb
    polygon, so a low point on them is not a low-access point -- Croydon plots as low
    supply with 1 practice inside, 21 within 1.5 km and 27 within 2 km. A PNG gets pasted
    into a deck without its console log or its CSV, so the caption has to be on the image.
    The old code silently dropped 249 of 680 suburbs here -- 247 without usable GP data
    plus Bayview and Beacon Hill, whose ``total_gp == 0`` is confirmed but undrawable on a
    log axis -- and its grey-'x' branch for them could never run, because the NaN and
    log-scale filters above had already removed every row it was meant to mark.
    """
    fig, ax = plt.subplots(figsize=(9, 6))
    cols = [xcol, ycol, "suburb"]
    if "gp_data_status" in df.columns:
        cols.append("gp_data_status")
    d = df[cols].dropna(subset=[xcol, ycol])
    if "gp_data_status" in d.columns:
        d = d[d["gp_data_status"] == GP_STATUS_OK]
    if log_x:
        d = d[d[xcol] > 0]
    if log_y:
        d = d[d[ycol] > 0]

    n_omitted, omitted_reasons = omission_report(df, d.index, xcol, ycol, log_x, log_y)

    ax.scatter(
        d[xcol],
        d[ycol],
        alpha=0.35,
        s=22,
        c="steelblue",
        edgecolors="none",
    )

    hi: pd.DataFrame | None = None
    off_axes = 0
    if highlight_mask is not None:
        hi = df.loc[highlight_mask, cols].dropna(subset=[xcol, ycol])
        # Ring only points actually on the axes (a log scale drops values <= 0).
        plotted = hi.index.intersection(d.index)
        off_axes = len(hi) - len(plotted)
        hi = hi.loc[plotted]
        if len(hi):
            ax.scatter(
                hi[xcol], hi[ycol], s=55, facecolors="none", edgecolors="crimson", linewidths=1.2
            )

    if log_x:
        ax.set_xscale("log")
    if log_y:
        ax.set_yscale("log")
    ax.set_xlabel(xlab)
    ax.set_ylabel(ylab)
    ax.set_title(title)
    ax.grid(True, alpha=0.25)

    # On-figure, not just on the console: a reader of the PNG alone must still be told how
    # many suburbs are missing from it, and that a supply axis is an interior count.
    footnotes: list[str] = []
    if xcol in INTERIOR_SUPPLY_AXES or ycol in INTERIOR_SUPPLY_AXES:
        footnotes.append(FIGURE_INTERIOR_NOTE)
    if n_omitted:
        footnotes.append(
            f"Not shown: {n_omitted} of {len(df)} suburbs -- " + "; ".join(omitted_reasons)
        )
    if footnotes:
        fig.text(
            0.008,
            0.006,
            "\n".join(footnotes),
            fontsize=6.0,
            color="0.35",
            ha="left",
            va="bottom",
            wrap=True,
        )
        fig.tight_layout(rect=(0.0, 0.02 + 0.022 * len(footnotes), 1.0, 1.0))
    else:
        fig.tight_layout()

    placed = 0
    skipped = 0
    if hi is not None and len(hi):
        if label_rank is not None:
            order = label_rank.reindex(hi.index).fillna(np.inf).sort_values().index
            hi = hi.loc[order]
        placed, skipped = place_labels(ax, fig, hi, xcol, ycol)

    out = OUT_DIR / fname
    fig.savefig(out, dpi=140)
    plt.close(fig)
    note = (
        f"Saved {out}  (plotted {len(d)} of {len(df)}; "
        f"labelled {placed}/{placed + skipped} highlighted"
    )
    if off_axes:
        note += f"; {off_axes} highlighted off-scale"
    print(note + ")")
    if n_omitted:
        print(f"  not shown: {n_omitted} of {len(df)} -- " + "; ".join(omitted_reasons))


def build_underprovided_table(df: pd.DataFrame) -> pd.DataFrame:
    """
    Simple supply flags from gp_per_1000 vs cohort thresholds (median / bottom 25%).

    Cohort: population >= MIN_POP_FLAGS **and** ``gp_data_status == 'ok'``. Thresholds are
    percentiles of gp_per_1000, so unscraped suburbs would both be flagged as the worst
    supplied and drag the percentiles down for everybody else.

    High-demand side: at or above **median** (within the cohort) for at least one of:
    population, over_65_pct, spatial_density.

    Output: one row per suburb in the analysis cohort with boolean flags + priority columns.
    """
    sub = supply_cohort(df, MIN_POP_FLAGS, "underprovision flags")
    if sub.empty:
        return sub

    gp = sub["gp_per_1000"]
    thr_med = float(gp.median())
    thr_p25 = float(gp.quantile(0.25))
    # Inside this cohort total_gp == 0 is a real "no listed GPs" (status is 'ok'), but a
    # cluster of zeros still makes the rate percentile degenerate -- keep the stricter
    # threshold that looks only at suburbs with at least one listed GP.
    pos = sub.loc[sub["total_gp"] > 0, "gp_per_1000"]
    thr_p25_nonzero = float(pos.quantile(0.25)) if len(pos) else float("nan")

    sub["gp_threshold_median"] = thr_med
    sub["gp_threshold_p25"] = thr_p25
    sub["gp_threshold_p25_among_gps_gt0"] = thr_p25_nonzero
    sub["underprovided_below_median"] = gp < thr_med
    sub["underprovided_bottom_25pct"] = gp <= thr_p25
    sub["underprovided_bottom_25pct_among_gps_gt0"] = (sub["total_gp"] > 0) & (
        gp <= thr_p25_nonzero
    )

    med_pop = sub["population"].median()
    med_65 = sub["over_65_pct"].median()
    med_den = sub["spatial_density"].median()

    sub["high_pop_demand"] = sub["population"] >= med_pop
    sub["high_elderly_demand"] = sub["over_65_pct"] >= med_65
    sub["high_density_demand"] = sub["spatial_density"].notna() & (
        sub["spatial_density"] >= med_den
    )
    sub["high_demand_any"] = (
        sub["high_pop_demand"]
        | sub["high_elderly_demand"]
        | sub["high_density_demand"]
    )

    sub["priority_low_gp_high_demand_median"] = (
        sub["underprovided_below_median"] & sub["high_demand_any"]
    )
    sub["priority_low_gp_high_demand_p25"] = (
        sub["underprovided_bottom_25pct"] & sub["high_demand_any"]
    )
    sub["priority_low_gp_high_demand_p25_nonzero"] = (
        sub["underprovided_bottom_25pct_among_gps_gt0"] & sub["high_demand_any"]
    )

    return sub


def main() -> None:
    # First thing in the log, before any table: every supply number below is an interior
    # count, and Croydon is the worked example of what that costs a reader.
    print_interior_count_warning()
    print()
    persist_opportunity_score_to_eda_csv()
    df = load_prepared()

    print("\n=== Dataset ===")
    print(f"Rows: {len(df)}")
    print("GP data status (gp_data_status):")
    print(status_summary(df))
    ok_df = df[rankable_mask(df)]
    print(
        f"total_gp (status '{GP_STATUS_OK}' only, n={len(ok_df)}): "
        f"min={ok_df['total_gp'].min():.0f} max={ok_df['total_gp'].max():.0f} "
        f"median={ok_df['total_gp'].median():.0f}"
    )
    print(
        f"gp_per_1000 (same rows): median={ok_df['gp_per_1000'].median():.3f} "
        f"max={ok_df['gp_per_1000'].max():.3f}  "
        f"(NaN for the {len(df) - len(ok_df)} suburbs without usable GP data)"
    )

    print(f"\n=== Correlations (gp_data_status == '{GP_STATUS_OK}' only) ===")
    print_corr("population vs total_gp", ok_df["population"], ok_df["total_gp"])
    print_corr("spatial_density vs gp_per_1000", ok_df["spatial_density"], ok_df["gp_per_1000"])
    print_corr("over_65_pct vs gp_per_1000", ok_df["over_65_pct"], ok_df["gp_per_1000"])
    print_corr(
        "median_household_income vs gp_per_1000",
        ok_df["median_household_income_weekly"],
        ok_df["gp_per_1000"],
    )

    # Composite underprovision signals: every one subtracts observed GP supply, so the
    # cohort is status 'ok' plus population >= MIN_POP_RANK.
    r = supply_cohort(df, MIN_POP_RANK, "underprovision signals (sig_*)")
    r["z_pop"] = zscore(r["population"])
    r["z_density"] = zscore(r["spatial_density"])
    r["z_65"] = zscore(r["over_65_pct"])
    r["z_gp1k"] = zscore(r["gp_per_1000"].fillna(0))
    r["sig_pop_lowgp"] = r["z_pop"] - r["z_gp1k"]
    r["sig_65_lowgp"] = r["z_65"] - r["z_gp1k"]
    r["sig_density_lowgp"] = r["z_density"] - r["z_gp1k"]

    export_three_opportunity_lists(r, EDA_DIR, TOP_OPPORTUNITY_N)

    print(
        f"\n=== Top 12 preview (same logic as CSVs; population >= {MIN_POP_RANK}, "
        f"gp_data_status == '{GP_STATUS_OK}') ===\n"
    )

    # Appended to every console preview below. The signal columns say "low GP" and mean
    # "low interior count"; these three say whether that is actually low access.
    preview_access_cols = [
        "practices_within_2km",
        "access_general_pct",
        "saturated_nearby",
    ]

    def show_table(name: str, sub: pd.DataFrame, cols: list[str]) -> None:
        print(f"--- {name}  (interior count; access columns on the right) ---")
        present = [c for c in cols + preview_access_cols if c in sub.columns]
        print(sub[present].to_string(index=False))
        print()

    show_table(
        "High population, low GP access (z_pop - z_gp_per_1000)",
        r.nlargest(12, "sig_pop_lowgp"),
        [
            "suburb",
            "population",
            "total_gp",
            "gp_per_1000",
            "sig_pop_lowgp",
        ],
    )
    show_table(
        "High % aged 65+, low GP access (z_65 - z_gp_per_1000)",
        r.nlargest(12, "sig_65_lowgp"),
        ["suburb", "over_65_pct", "total_gp", "gp_per_1000", "sig_65_lowgp"],
    )
    show_table(
        "High spatial density, low GP access (z_density - z_gp_per_1000)",
        r.nlargest(12, "sig_density_lowgp"),
        [
            "suburb",
            "spatial_density",
            "population",
            "total_gp",
            "gp_per_1000",
            "sig_density_lowgp",
        ],
    )

    # Highlight the union of the top 8 on each signal; label priority = the best rank a
    # suburb reaches on any of them, so the most extreme points get labelled first.
    top_union = pd.Index([])
    for col in SIGNAL_COLS:
        top_union = top_union.union(r.nlargest(8, col).index)
    highlight = df.index.isin(top_union)
    label_rank = pd.concat(
        [r[col].rank(ascending=False, method="min") for col in SIGNAL_COLS], axis=1
    ).min(axis=1)

    scatter_with_marginals(
        df,
        "population",
        "total_gp",
        "Population vs total GP count",
        "Population",
        "Total GP count (summed from clinic list)",
        "01_population_vs_total_gp.png",
        log_x=True,
        log_y=True,
        highlight_mask=highlight,
        label_rank=label_rank,
    )

    # The full frame, not a pre-filtered copy: the NaN / status / log-scale filtering all
    # happens inside (the plotted points are identical), and the omission footnote has to
    # count against all 680 suburbs rather than against an already-thinned subset.
    scatter_with_marginals(
        df,
        "spatial_density",
        "gp_per_1000",
        "Spatial density vs GPs per 1,000 residents",
        "People per km²",
        "GPs per 1,000 population",
        "02_spatial_density_vs_gp_per_1000.png",
        log_x=True,
        log_y=True,
        highlight_mask=highlight,
        label_rank=label_rank,
    )

    scatter_with_marginals(
        df,
        "over_65_pct",
        "gp_per_1000",
        "% aged 65+ vs GPs per 1,000 residents",
        "Over 65 %",
        "GPs per 1,000 population",
        "03_over_65_vs_gp_per_1000.png",
        log_y=True,
        highlight_mask=highlight,
        label_rank=label_rank,
    )

    scatter_with_marginals(
        df,
        "median_household_income_weekly",
        "gp_per_1000",
        "Median household income vs GPs per 1,000",
        "Median household income ($/week)",
        "GPs per 1,000 population",
        "04_household_income_vs_gp_per_1000.png",
        log_y=True,
        highlight_mask=highlight,
        label_rank=label_rank,
    )

    # Extra: income vs total_gp (raw availability)
    scatter_with_marginals(
        df,
        "median_household_income_weekly",
        "total_gp",
        "Median household income vs total GP count",
        "Median household income ($/week)",
        "Total GP count",
        "05_household_income_vs_total_gp.png",
        log_y=True,
        highlight_mask=highlight,
        label_rank=label_rank,
    )

    print(f"\nAll figures saved under: {OUT_DIR.resolve()}")

    # --- Underprovision flags (EDA): low gp_per_1000 + high demand proxies ---
    flags = build_underprovided_table(df)
    out_csv = EDA_DIR / "underprovided_suburbs.csv"
    export_cols = [
        "suburb",
        "population",
        "over_65_pct",
        "spatial_density",
        "total_gp",
        "gp_per_1000",
        *STATUS_COLS,
        "gp_threshold_median",
        "gp_threshold_p25",
        "gp_threshold_p25_among_gps_gt0",
        "underprovided_below_median",
        "underprovided_bottom_25pct",
        "underprovided_bottom_25pct_among_gps_gt0",
        "high_pop_demand",
        "high_elderly_demand",
        "high_density_demand",
        "high_demand_any",
        "priority_low_gp_high_demand_median",
        "priority_low_gp_high_demand_p25",
        "priority_low_gp_high_demand_p25_nonzero",
        # Appended, never used by any flag above: every "underprovided" verdict in this
        # file is an interior gp_per_1000 verdict, so the access measurement that can
        # contradict it travels in the same row.
        *ACCESS_CONTEXT_COLS,
    ]
    if flags.empty:
        print("\n=== Underprovision flags: cohort is empty, nothing written ===")
    else:
        present = [c for c in export_cols if c in flags.columns]
        flags[present].sort_values("gp_per_1000").to_csv(out_csv, index=False)
        print(
            f"\n=== Underprovision flags (population >= {MIN_POP_FLAGS}, "
            f"gp_data_status == '{GP_STATUS_OK}', cohort n={len(flags)}) ==="
        )
        print(f"gp_per_1000 threshold (median): {flags['gp_threshold_median'].iloc[0]:.6f}")
        print(f"gp_per_1000 threshold (25th pct, all): {flags['gp_threshold_p25'].iloc[0]:.6f}")
        tnz = flags["gp_threshold_p25_among_gps_gt0"].iloc[0]
        print(
            f"gp_per_1000 threshold (25th pct, only suburbs with total_gp>0): "
            f"{tnz:.6f}"
        )
        print(f"Count underprovided (below median): {int(flags['underprovided_below_median'].sum())}")
        print(f"Count underprovided (bottom 25%, all): {int(flags['underprovided_bottom_25pct'].sum())}")
        print(
            "Count underprovided (bottom 25% among listed-GP>0 suburbs): "
            f"{int(flags['underprovided_bottom_25pct_among_gps_gt0'].sum())}"
        )
        print(
            f"PRIORITY (below median GP supply AND high demand on any axis): "
            f"{int(flags['priority_low_gp_high_demand_median'].sum())}"
        )
        print(
            f"PRIORITY (bottom 25% all AND high demand): "
            f"{int(flags['priority_low_gp_high_demand_p25'].sum())}"
        )
        print(
            f"PRIORITY (bottom 25% among gp>0 AND high demand): "
            f"{int(flags['priority_low_gp_high_demand_p25_nonzero'].sum())}"
        )
        print(f"Saved full flag table: {out_csv.resolve()}")
        print(
            "  Every 'underprovided' flag above is a gp_per_1000 verdict, i.e. an INTERIOR\n"
            "  count, and is NOT a finding about access (Croydon: 1 practice inside, 21\n"
            "  within 1.5 km, 27 within 2 km). Contradiction accounting:"
        )
        print_saturated_report(out_csv.name, flags, max_named=0)
        pri_all = flags[flags["priority_low_gp_high_demand_p25_nonzero"]]
        print_saturated_report(
            "  of which priority_low_gp_high_demand_p25_nonzero", pri_all, max_named=0
        )
        pri = flags[flags["priority_low_gp_high_demand_p25_nonzero"]].sort_values(
            "population", ascending=False
        )
        if len(pri):
            print(
                "\n--- Top priority suburbs (bottom 25% gp_per_1000 among listed-GP "
                "suburbs + high demand; interior count, access columns on the right) ---"
            )
            pri_cols = [
                "suburb",
                "population",
                "over_65_pct",
                "spatial_density",
                "total_gp",
                "gp_per_1000",
                "total_gp_is_floor",
                # The console table gets the access columns too: a reader who never
                # opens the CSV must still see the contradiction.
                "practices_within_2km",
                "nearest_practice_km",
                "access_general_pct",
                "saturated_nearby",
            ]
            print(
                pri[[c for c in pri_cols if c in pri.columns]]
                .head(25)
                .to_string(index=False)
            )

    # --- Weighted opportunity scores (business criteria + explicit income logic) ---
    wdf = build_weighted_opportunity_scores(df)
    if len(wdf):
        # Regenerated in full (formula + MCDA + status filter): the old code wrote only the
        # MCDA blurb and destroyed the opportunity_score section every run.
        method_md = EDA_DIR / "OPPORTUNITY_SCORING.md"
        method_md.write_text(build_scoring_doc(df, len(wdf)), encoding="utf-8")

        wcsv = EDA_DIR / "weighted_opportunity_scores.csv"
        wcols = [
            "suburb",
            "population",
            "over_65_pct",
            "seniors_estimated_count",
            "spatial_density",
            "area",
            "median_household_income_weekly",
            "total_gp",
            "gp_per_1000",
            *STATUS_COLS,
            "weighted_score_mixed_billing",
            "weighted_score_community_need",
            "mcda_mm_total_gp_lower_better",
            "mcda_mm_income_higher_better",
            "mcda_mm_income_lower_better",
            "mcda_mm_over_65_pct_higher_better",
            "mcda_mm_population_higher_better",
            "mcda_mm_area_lower_better",
            "contrib_total_gp_w",
            "contrib_income_mixed_billing_w",
            "contrib_income_community_need_w",
            "contrib_over_65_pct_w",
            "contrib_population_w",
            "contrib_area_w",
            # Appended, and no weight reads them: the 0.35 supply criterion is
            # total_gp, an interior count. These columns say what that count omits.
            *ACCESS_CONTEXT_COLS,
        ]
        present_w = [c for c in wcols if c in wdf.columns]
        wdf[present_w].sort_values(
            "weighted_score_mixed_billing", ascending=False
        ).to_csv(wcsv, index=False)

        print("\n=== MCDA (min-max 0-1 criteria, see OPPORTUNITY_SCORING.md) ===")
        print(
            "Criteria scaled within cohort: higher-better = (x-min)/(max-min); "
            "lower-better (total_gp, area) = (max-x)/(max-min)."
        )
        print(
            "Income: mixed_billing uses higher-better; community_need uses lower-better on income."
        )
        print(
            f"Cohort: {len(wdf)} suburbs (population >= {MIN_POP_RANK} and "
            f"gp_data_status == '{GP_STATUS_OK}'). Supply is 35% of the composite, so "
            "suburbs without usable GP data cannot be scored at all."
        )
        print(
            "The 0.35 supply criterion is total_gp, an INTERIOR count, so a high composite\n"
            "is not evidence of low access (Croydon: 1 practice inside, 21 within 1.5 km,\n"
            f"27 within 2 km). Appended for every row: {', '.join(ACCESS_CONTEXT_COLS)}."
        )
        print(f"Saved methodology: {method_md.resolve()}")
        print(f"Saved scores: {wcsv.resolve()}")
        print_saturated_report(wcsv.name, wdf, max_named=0)
        mcda_cols = [
            "suburb",
            "total_gp",
            "total_gp_is_floor",
            "median_household_income_weekly",
            "over_65_pct",
            "population",
            # Same reason as the priority table above: the console must show it too.
            "practices_within_2km",
            "access_general_pct",
            "saturated_nearby",
        ]
        mcda_cols = [c for c in mcda_cols if c in wdf.columns]
        print(
            "\n--- Top 12: mixed-billing / low-competition lens  "
            "(total_gp is an interior count; access columns on the right) ---"
        )
        print(
            wdf.nlargest(12, "weighted_score_mixed_billing")[
                mcda_cols + ["weighted_score_mixed_billing"]
            ].to_string(index=False)
        )
        print(
            "\n--- Top 12: community-need lens (income min-max inverted; same caveat) ---"
        )
        print(
            wdf.nlargest(12, "weighted_score_community_need")[
                mcda_cols + ["weighted_score_community_need"]
            ].to_string(index=False)
        )


if __name__ == "__main__":
    main()
