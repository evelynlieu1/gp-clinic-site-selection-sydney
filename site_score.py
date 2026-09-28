"""
Site score v2 for the Nuvo Health suburb selection: implements decisions D1 and D6.

Supersedes v1 (``population / (total_gp + 1)`` in-polygon; weights 0.35 supply / 0.25
income / 0.20 seniors / 0.15 population / 0.05 area), which saw Croydon as 1 practice
with 27 within 2 km, let the 0.05 area term and a 30,000-plus population range rank
1,334-person Point Piper first, and read income as monotonic higher-is-better.

CRITERIA (decision D6). Every sub-score is on [0, 1], higher = better site.

===================  ======  ==================================================
criterion            weight  direction
===================  ======  ==================================================
access_gap             0.40  LOW accessibility -> HIGH score. The core question.
demand_scale           0.25  senior-weighted resident base, log ramp, gated
mixed_billing_fit      0.25  inverted U on household income (D1)
operational            0.10  pharmacy co-location plus DPA status
===================  ======  ==================================================

The recommendation is the robust core, not the point ranking: :func:`run_sensitivity`
re-ranks under 31 perturbations and reports the suburbs that stay top 10 under every
one. D3: the primary lens is COMMERCIAL mixed billing; :func:`community_need_income_fit`
is a secondary column, never ranked on by default.

D4: :data:`NUVO_SITES` is the operator's published list (nuvohealth.com.au, retrieved
2026-09-23); brand-string matching found 6 of 18 centres, understating the exclusion
set threefold. CONFIRM WITH THE CLIENT - a listing can lag an opening or closure, and
exclusion is at GP-CATCHMENT level: a clinic 1 km away in the next suburb cannibalises.

D5: supply is practices per capita via accessibility; the scraped ``total_gp`` is never
a RANKING input, and no FTE source exists below SA3 (roughly 14 suburbs per unit). One
unit per practice misreads few-but-large practices as scarcity, so ``total_gp`` may
CONTRADICT a high ``access_gap`` via :func:`headcount_adequacy`: downward only, only
where ``gp_data_status == "ok"``, and a sensitivity family so it can be read off.
MEASURED: it demotes 159 of 443 eligible suburbs; Dee Why rank 2 (35 recorded GPs,
23,354 residents) -> 17, Mona Vale 9 -> 42, Freshwater 10 -> 12. BLIND SPOT: 123
eligible suburbs have no trustworthy headcount and cannot be guarded at all - one,
Caringbah South, ranks 2 - so a ``no_data`` suburb's rank carries strictly less
evidence than an ``ok`` one and must not be presented as though it carries the same.

Two caveats that must travel with the output:
1. The claim is "too few practices for the population that must share them", never "no
   GP here": Cronulla at rank 1 has 6 practices within 2 km, nearest 104 m; St Clair at
   rank 2 has 6 within 2 km, nearest 415 m.
2. MEASURED: coastal-peninsula catchments hold 13 percent of eligible suburbs but 60
   percent of the top 10 and 47 percent of the top 30; ``access_gap`` correlates -0.739
   with log reachable catchment demand against -0.543 with practices within 2 km, so it
   tracks reachable people more than nearby practices. Commercially that is a captive
   market, but only as far as the straight-line travel in :mod:`accessibility`, least
   accurate across water; top-10 peninsula share stays 4-7 across travel-cost variants.

Outputs: ``eda/site_scores_v2.csv`` (all 680 suburbs, sub-scores, rank, stability),
``eda/sensitivity_analysis.csv`` (one row per suburb per perturbation),
``eda/final_shortlist_v2.csv`` (the robust core, ranked, with provenance).
Run: ``python site_score.py``  (add ``--test`` for the self-checks only).
"""
from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

import accessibility
import gp_supply

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "scraped_data"
EDA_DIR = ROOT / "eda"

EDA_DATA = DATA_DIR / "eda_data.csv"
ACCESS_CSV = DATA_DIR / "suburb_accessibility.csv"
CATCHMENT_CSV = DATA_DIR / "suburb_gp_catchment.csv"
DPA_CSV = DATA_DIR / "suburb_dpa_status.csv"
PRACTICE_COUNTS_CSV = DATA_DIR / "suburb_practice_counts.csv"
PHARMACIES_CSV = DATA_DIR / "ga_pharmacies_nsw.csv"

SCORES_OUT = EDA_DIR / "site_scores_v2.csv"
SENSITIVITY_OUT = EDA_DIR / "sensitivity_analysis.csv"
SHORTLIST_OUT = EDA_DIR / "final_shortlist_v2.csv"

#: Optional input, written by ``validation.py``. Absent on a first run, which is why
#: every use of it is guarded: the shortlist must build without it.
LISA_CSV = EDA_DIR / "lisa_site_score.csv"

#: Stamped into every output file so a stray CSV cannot be read as verified.
NUVO_UNVERIFIED_NOTE = (
    "site list from nuvohealth.com.au/medical-centres/ retrieved 2026-09-23; "
    "15 of 18 GP centres map to an ABS suburb, the other 3 are precinct names whose "
    "catchments are already covered; confirm with client before acting"
)

SOURCE_NOTE = (
    "supply: Geoscience Australia National HealthDirect Health Facilities, NHSD-derived, "
    "extract 2025-03-07, CC-BY 4.0; access: E2SFCA per accessibility.py; "
    "catchments: GPC 2023; DPA: DPA_GPs_2025a"
)

TOP_N = 10

# ---------------------------------------------------------------------------
# D1 - mixed billing income fit
# ---------------------------------------------------------------------------

#: Inverted-U knots on median weekly household income, dollars, ascending:
#: ``(taper_start, plateau_start, plateau_end, taper_end)``.
#:
#: Why inverted-U (D1): the rebate is about $43 against a typical private fee near $90,
#: so mixed billing needs households that can absorb a gap of roughly $45 per visit.
#: Below the lower taper that gap suppresses attendance; above the upper taper households
#: buy full private care. v1's monotonic higher-is-better optimised for the wrong segment.
#: MEASURED over the 680 Sydney suburbs in ``eda_data.csv``: $1,550 = P14.1,
#: $2,050 = P37.1, $2,750 = P76.2, $3,500 = P95.0 - the brief said P15 / P40 / P80 / P95,
#: three within 3 points but $2,750 is P76, recorded rather than silently restated.
INCOME_KNOTS: tuple[float, float, float, float] = (1550.0, 2050.0, 2750.0, 3500.0)

#: The dollar knots above are a static fallback (doctests, a bare :class:`ScoreConfig`);
#: :func:`main` derives the knots from the income distribution actually loaded, at these
#: percentiles. The dollars agree with the data only by construction - both are 2021 - so
#: a later vintage would break them silently. MEASURED: inflating income 20 percent
#: against FIXED dollar knots changes the income sub-score for 268 of 341 eligible
#: suburbs, mean absolute delta 0.3011; against percentile-derived knots it changes ZERO.
#: Re-anchoring the current data costs 161 of 341 suburbs, mean absolute delta 0.0375 -
#: hygiene that removes a future failure, not a correction of a present error.
#: Percentiles run over ALL 680 suburbs with a positive recorded income, never the
#: eligible cohort, whose eligibility depends on these knots.
INCOME_KNOT_PERCENTILES: tuple[float, float, float, float] = (15.0, 40.0, 80.0, 95.0)

#: Community-need variant (D3) reuses the two OUTER knots of :data:`INCOME_KNOTS` so the
#: two lenses are directly comparable and only the shape between them differs.

# ---------------------------------------------------------------------------
# D6 - demand scale
# ---------------------------------------------------------------------------

#: Attendance weight of a 65-plus resident relative to an average resident. Medicare
#: attendance data puts 65-plus GP attendances at roughly 11 to 12 per year against 5 to
#: 6 population-wide, so 2.5 is mid-ratio. Swept: a judgement, not a measurement here.
SENIOR_ATTENDANCE_MULTIPLE = 2.5

#: Minimum viable LOCAL resident base. A 7-day multidisciplinary clinic carries weekend
#: and allied-health fixed cost that a small resident base cannot fill. Below this the
#: suburb is INELIGIBLE: sub-scores stay in the output for audit, but it gets no rank.
#:
#: LOWERED FROM 4,000 TO 1,000. A suburb-level headcount rule sits awkwardly inside a
#: catchment-based analysis, and at 4,000 it excluded 294 of 680 suburbs on a criterion
#: the rest of the method does not use. Lowered rather than removed: the v1 failure it
#: guarded against (Point Piper, population 1,334, ranked first) now scores 0.186 anyway,
#: and dropping the floor is safe for the ANSWER - best readmitted suburb 0.679 against a
#: rank-10 score of 0.780 - but it readmits Coasters Retreat at 30 residents and Berrilee
#: at 212. MEASURED: 1,000 excludes 96 of 680. Floor 0.0 is one of the sensitivity runs.
MIN_VIABLE_POPULATION = 1000.0

#: Minimum viable CATCHMENT demand: decay-weighted, senior-weighted population reachable
#: within the general travel cutoff, outside-the-study demand ring included. Same weights
#: as E2SFCA step 1, so it cannot disagree with the access measure about who is reachable.
#:
#: HONEST FINDING: at :data:`MIN_VIABLE_POPULATION` this binds on ZERO extra suburbs, and
#: only starts excluding at about 50,000, where the first three (North Richmond, Berowra
#: Heights, Richmond) are legitimate candidates. Kept as an explicit backstop; that it
#: binds on nothing means Sydney has no isolated-but-populous suburb, not that it is idle.
MIN_CATCHMENT_DEMAND = 25000.0

#: Log-ramp anchors for :func:`demand_scale_score`, in senior-weighted persons. The floor
#: is :data:`MIN_VIABLE_POPULATION` senior-weighted at the study-area 65-plus share. Past
#: saturation (about P91 of the 680) a single site is capacity- rather than demand-
#: constrained: extra local population accrues to competitors, so it should stop scoring.
#: Log rather than linear because the distribution spans 3 to 60,700, and a linear min-max
#: is what let v1 barely penalise a 1,300-person enclave.
DEMAND_FLOOR_WEIGHTED = 5000.0
DEMAND_SATURATION_WEIGHTED = 20000.0

# ---------------------------------------------------------------------------
# D5 - headcount demotion guard
# ---------------------------------------------------------------------------

#: The E2SFCA supply term in :mod:`accessibility` is ONE UNIT PER PRACTICE - a 12-doctor
#: clinic and a solo GP count the same, as the register carries no practitioner count -
#: so a suburb served by FEW BUT LARGE practices reads as under-supplied. MEASURED: Dee
#: Why ranked 2, 35 recorded GPs against 23,354 residents, is 667 people per GP, better
#: supplied than 73 percent of the eligible cohort; Mona Vale ranked 9 is worse at 494
#: per GP, better supplied than 87 percent. The guard does NOT make headcount a ranking
#: input - that would reverse D5 and lean on the very scrape the project discredited -
#: only that a trustworthy headcount can CONTRADICT a high gap score, so it only demotes.
#: Knots are percentiles of people-per-GP across the eligible ok-cohort (302 suburbs):
#: MEASURED P25 = 640, P50 = 1,042, rounded to two significant figures below.
GUARD_WELL_SUPPLIED_PPG = 640.0
GUARD_NO_GUARD_PPG = 1040.0

#: Share of ``access_gap`` removed at full adequacy. At 0.5 a suburb in the best supplied
#: quartile keeps half its gap score. Varied in the sweep because it is a judgement.
HEADCOUNT_GUARD_STRENGTH = 0.5

# ---------------------------------------------------------------------------
# D6 - operational
# ---------------------------------------------------------------------------

#: At or below this the pharmacy is on the same retail strip: a walk with script in hand.
PHARMACY_CO_LOCATED_KM = 0.4

#: At or beyond this the patient drives, so the pharmacy could be anywhere: no benefit.
PHARMACY_NO_BENEFIT_KM = 2.0

#: Split of the 0.10 operational criterion between pharmacy co-location and DPA status.
#: DPA gets the smaller share deliberately: it is a near-constant zero (79 of 680
#: suburbs) and fires almost entirely on fringe suburbs too small to host this clinic -
#: median population 815 for DPA=Y against 5,643 for DPA=N - so at full weight it acts as
#: a hidden smallness bonus, the defect that got ``area`` dropped from v1. At 0.3 of 0.10
#: it moves a total score by at most 0.03: a tie-breaker, as the DPA analysis
#: recommended. Swept at 0.0 and 0.6.
DPA_OPERATIONAL_SHARE = 0.3

#: Radius for the reported (not scored) pharmacy count.
PHARMACY_COUNT_RADIUS_KM = 1.0

# ---------------------------------------------------------------------------
# D6 - weights
# ---------------------------------------------------------------------------

BASE_WEIGHTS: dict[str, float] = {
    "access_gap": 0.40,
    "demand_scale": 0.25,
    "mixed_billing_fit": 0.25,
    "operational": 0.10,
}

CRITERIA = tuple(BASE_WEIGHTS)

# ---------------------------------------------------------------------------
# D4 - Nuvo exclusion
# ---------------------------------------------------------------------------

#: EDITABLE. Suburbs hosting an existing Nuvo Health GP centre; every suburb in the same
#: GP catchment is excluded, because a clinic 1 km away still cannibalises. PROVENANCE:
#: the operator's own listing, nuvohealth.com.au/medical-centres/, retrieved 2026-09-23,
#: 18 GP medical centres. SUPERSEDES a brand-string match on "nuvo" over the clinic
#: scrape, which found only 6 because neither the scrape nor the Geoscience Australia
#: register records a brand owner: that understated the set threefold, 6 catchments and
#: 75 suburbs against 10 and 145 here.
#:
#: THREE of the 18 are precinct names rather than ABS suburbs, omitted because each sits
#: in a catchment the listed 15 already cover:
#:   Green Square  -> Zetland,       Sydney Inner City  (covered by Surry Hills, Glebe)
#:   Harold Park   -> Forest Lodge,  Sydney Inner City  (covered by Surry Hills, Glebe)
#:   Birkenhead    -> Drummoyne,     Canada Bay         (covered by Five Dock)
#:
#: MEASURED: Manly (was rank 11) and Bondi Beach (was rank 13) leave contention; the top
#: 10 is unchanged, so the recommendation does not depend on this fix.
NUVO_SITES: tuple[str, ...] = (
    "Surry Hills",
    "Randwick",
    "Double Bay",
    "Coogee",
    "Maroubra",
    "Leichhardt",
    "Rozelle",
    "Glebe",
    "Five Dock",
    "Manly",
    "Chatswood",
    "Longueville",
    "Neutral Bay",
    "Parramatta",
    "Epping",
)


# ---------------------------------------------------------------------------
# Sub-score functions
# ---------------------------------------------------------------------------


def income_knots_from_percentiles(
    incomes: np.ndarray,
    percentiles: tuple[float, float, float, float] = INCOME_KNOT_PERCENTILES,
) -> tuple[float, float, float, float]:
    """
    Derive the four :func:`mixed_billing_fit` knots from an income distribution.

    Zeros and NaNs are dropped: two suburbs of 14 and 4 record $0, which no household has.

    >>> vals = np.arange(1000.0, 5001.0, 10.0)
    >>> income_knots_from_percentiles(vals, (15.0, 40.0, 80.0, 95.0))
    (1600.0, 2600.0, 4200.0, 4800.0)

    Scale-invariant: scale all incomes and all knots scale with them, so no score moves.

    >>> a = income_knots_from_percentiles(vals)
    >>> b = income_knots_from_percentiles(vals * 1.2)
    >>> [round(x * 1.2, 6) for x in a] == [round(y, 6) for y in b]
    True

    >>> dirty = np.array([0.0, np.nan, 1000.0, 2000.0, 3000.0, 4000.0])
    >>> income_knots_from_percentiles(dirty, (0.0, 25.0, 75.0, 100.0))
    (1000.0, 1750.0, 3250.0, 4000.0)
    """
    if len(percentiles) != 4:
        raise ValueError(f"need exactly 4 percentiles, got {len(percentiles)}")
    if not all(a <= b for a, b in zip(percentiles, percentiles[1:])):
        raise ValueError(f"percentiles must ascend, got {percentiles}")
    clean = np.asarray(incomes, dtype=float)
    clean = clean[np.isfinite(clean) & (clean > 0.0)]
    if clean.size == 0:
        raise ValueError("no positive incomes to derive knots from")
    knots = tuple(float(np.percentile(clean, q)) for q in percentiles)
    return knots  # type: ignore[return-value]


def mixed_billing_fit(
    income: float, knots: tuple[float, float, float, float] = INCOME_KNOTS
) -> float:
    """
    Decision D1: inverted-U fit of household income to a mixed-billing offer.

    Full score on the plateau, linear taper to zero at each outer knot, zero outside.
    ``knots`` is ``(taper_start, plateau_start, plateau_end, taper_end)``.

    At the four knots:

    >>> mixed_billing_fit(1550.0)
    0.0
    >>> mixed_billing_fit(2050.0)
    1.0
    >>> mixed_billing_fit(2750.0)
    1.0
    >>> mixed_billing_fit(3500.0)
    0.0

    On the plateau, and at the midpoint of each taper:

    >>> mixed_billing_fit(2400.0)
    1.0
    >>> mixed_billing_fit(1800.0)
    0.5
    >>> mixed_billing_fit(3125.0)
    0.5

    Outside the knots, including the $0 suburbs (14 and 4 residents) and Sydney's richest:

    >>> mixed_billing_fit(0.0)
    0.0
    >>> mixed_billing_fit(1200.0)
    0.0
    >>> mixed_billing_fit(4000.0)
    0.0
    >>> mixed_billing_fit(6999.0)
    0.0

    Non-monotonic by design, the point of D1: $4,000 scores BELOW $2,400, where the v1
    monotonic score put it far above.

    >>> mixed_billing_fit(4000.0) < mixed_billing_fit(2400.0)
    True

    Shifting the whole band moves the taper but not the shape:

    >>> mixed_billing_fit(1800.0, (1300.0, 1800.0, 2500.0, 3250.0))
    1.0
    """
    lo_start, lo_end, hi_start, hi_end = knots
    if not lo_start <= lo_end <= hi_start <= hi_end:
        raise ValueError(f"income knots must be ascending, got {knots}")
    if math.isnan(income):
        return 0.0
    if income <= lo_start or income >= hi_end:
        return 0.0
    if income >= lo_end and income <= hi_start:
        return 1.0
    if income < lo_end:
        return (income - lo_start) / (lo_end - lo_start)
    return (hi_end - income) / (hi_end - hi_start)


def community_need_income_fit(
    income: float, knots: tuple[float, float, float, float] = INCOME_KNOTS
) -> float:
    """
    Decision D3 secondary lens: income rewarded lower-is-better.

    Reuses the outer two :data:`INCOME_KNOTS`: same span as the commercial lens, inverted.

    >>> community_need_income_fit(1550.0)
    1.0
    >>> community_need_income_fit(1200.0)
    1.0
    >>> community_need_income_fit(3500.0)
    0.0
    >>> community_need_income_fit(4000.0)
    0.0
    >>> community_need_income_fit(2525.0)
    0.5

    Mirror of the commercial lens at the extremes; its plateau scores middling here:

    >>> community_need_income_fit(1550.0), mixed_billing_fit(1550.0)
    (1.0, 0.0)
    >>> round(community_need_income_fit(2400.0), 4)
    0.5641
    """
    lo_start, _, _, hi_end = knots
    if math.isnan(income):
        return 0.0
    if income <= lo_start:
        return 1.0
    if income >= hi_end:
        return 0.0
    return (hi_end - income) / (hi_end - lo_start)


def senior_weighted_population(
    population: float, over_65_pct: float, multiple: float = SENIOR_ATTENDANCE_MULTIPLE
) -> float:
    """
    Population re-weighted so a 65-plus resident counts ``multiple`` times an average one.

    ``P * (1 + (m - 1) * s)`` where ``s`` is the 65-plus share.

    >>> senior_weighted_population(10000.0, 0.0)
    10000.0
    >>> senior_weighted_population(10000.0, 100.0)
    25000.0

    At the study-area average share of about 16 percent:

    >>> round(senior_weighted_population(10000.0, 15.95), 1)
    12392.5

    Heathcote (22.82 percent) outscores an equal-sized suburb at the 15.95 average, which
    is the demand signal this criterion carries:

    >>> senior_weighted_population(6148.0, 22.82) > senior_weighted_population(6148.0, 15.95)
    True
    >>> senior_weighted_population(1.0, 20.0, multiple=1.0)
    1.0
    """
    if multiple < 1.0:
        raise ValueError(f"senior multiple below 1.0 makes seniors count less: {multiple}")
    return float(population) * (1.0 + (multiple - 1.0) * float(over_65_pct) / 100.0)


def log_ramp(value: float, floor: float, saturation: float) -> float:
    """
    Log-scaled ramp from 0 at ``floor`` to 1 at ``saturation``, clipped outside.

    Log because the quantity being scaled spans four orders of magnitude, and a linear
    min-max over such a range is what let v1 barely penalise a tiny suburb.

    >>> log_ramp(5000.0, 5000.0, 20000.0)
    0.0
    >>> log_ramp(20000.0, 5000.0, 20000.0)
    1.0
    >>> log_ramp(1000.0, 5000.0, 20000.0)
    0.0
    >>> log_ramp(60000.0, 5000.0, 20000.0)
    1.0

    Half score falls at the GEOMETRIC midpoint, 10,000, not the arithmetic one:

    >>> round(log_ramp(10000.0, 5000.0, 20000.0), 10)
    0.5
    >>> round(log_ramp(12500.0, 5000.0, 20000.0), 4)
    0.661
    """
    if floor <= 0 or saturation <= floor:
        raise ValueError(f"need 0 < floor < saturation, got {floor} and {saturation}")
    if value <= floor:
        return 0.0
    if value >= saturation:
        return 1.0
    return (math.log10(value) - math.log10(floor)) / (
        math.log10(saturation) - math.log10(floor)
    )


def pharmacy_score(
    nearest_km: float,
    co_located_km: float = PHARMACY_CO_LOCATED_KM,
    no_benefit_km: float = PHARMACY_NO_BENEFIT_KM,
) -> float:
    """
    Pharmacy co-location sub-score from the distance to the nearest GA-register pharmacy.

    Scored on the NEAREST pharmacy, not the count: one adjacent dispensary is the win and
    a fifth adds nothing, so the :data:`PHARMACY_COUNT_RADIUS_KM` count is context only.

    >>> pharmacy_score(0.0)
    1.0
    >>> pharmacy_score(0.4)
    1.0
    >>> pharmacy_score(2.0)
    0.0
    >>> pharmacy_score(13.2)
    0.0
    >>> pharmacy_score(1.2)
    0.5

    Measured over the 680: P5 0.113 km, median 0.688, P75 1.342, max 13.151, so the ramp
    spans the middle of the real distribution rather than being flat.

    >>> round(pharmacy_score(0.688), 4)
    0.82
    """
    if nearest_km <= co_located_km:
        return 1.0
    if nearest_km >= no_benefit_km:
        return 0.0
    return (no_benefit_km - nearest_km) / (no_benefit_km - co_located_km)


def operational_score(
    pharmacy: float, dpa_is_y: bool, dpa_share: float = DPA_OPERATIONAL_SHARE
) -> float:
    """
    Combine pharmacy co-location and DPA status into the 0.10 operational criterion.

    ``(1 - dpa_share) * pharmacy + dpa_share * dpa``; split rationale on
    :data:`DPA_OPERATIONAL_SHARE`: DPA flags 79 of 680 suburbs, median population 815, so
    a large share would smuggle a smallness bonus back in.

    Pharmacy without DPA beats DPA without pharmacy: adjacency beats a recruitment lever.

    >>> operational_score(1.0, False)
    0.7
    >>> round(operational_score(0.0, True), 10)
    0.3
    >>> operational_score(1.0, True)
    1.0
    >>> operational_score(0.0, False)
    0.0
    >>> round(operational_score(0.5, True), 10)
    0.65

    At ``dpa_share=0`` the criterion is pharmacy only, one of the sensitivity runs:

    >>> operational_score(0.4, True, dpa_share=0.0)
    0.4
    """
    if not 0.0 <= dpa_share <= 1.0:
        raise ValueError(f"dpa_share must be in [0, 1], got {dpa_share}")
    return (1.0 - dpa_share) * float(pharmacy) + dpa_share * (1.0 if dpa_is_y else 0.0)


def zones_for_cutoff(
    cutoff_min: float, width_min: float = accessibility.ZONE_WIDTH_MIN
) -> tuple[tuple[float, float], ...]:
    """
    Stepped Gaussian decay zones for an arbitrary travel cutoff (sensitivity test 3).

    Zone weight is :func:`accessibility.gaussian_weight` at the zone MIDPOINT, rounded to
    3 dp, derived rather than hard-coded so the 15 and 25 minute variants cannot drift.

    It reproduces both shipped tables exactly, so it is the rule the baseline was built with:

    >>> zones_for_cutoff(20.0) == accessibility.GENERAL_ZONES
    True
    >>> zones_for_cutoff(15.0) == accessibility.SENIOR_ZONES
    True
    >>> zones_for_cutoff(25.0)
    ((5.0, 0.987), (10.0, 0.888), (15.0, 0.701), (20.0, 0.448), (25.0, 0.154))

    Weights decrease with distance and a wider cutoff is more generous everywhere:

    >>> ws = [w for _, w in zones_for_cutoff(25.0)]
    >>> all(a > b for a, b in zip(ws, ws[1:]))
    True
    >>> zones_for_cutoff(25.0)[0][1] > zones_for_cutoff(20.0)[0][1]
    True
    """
    if cutoff_min <= 0 or width_min <= 0:
        raise ValueError(f"cutoff and width must be positive: {cutoff_min}, {width_min}")
    n_zones = int(round(cutoff_min / width_min))
    if not math.isclose(n_zones * width_min, cutoff_min):
        raise ValueError(f"cutoff {cutoff_min} is not a whole number of {width_min} min zones")
    return tuple(
        (
            width_min * (i + 1),
            round(accessibility.gaussian_weight(width_min * (i + 0.5), cutoff_min), 3),
        )
        for i in range(n_zones)
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoreConfig:
    """Every knob the score depends on. Perturbing one of these is one sensitivity run."""

    weights: tuple[tuple[str, float], ...] = tuple(BASE_WEIGHTS.items())
    income_knots: tuple[float, float, float, float] = INCOME_KNOTS
    general_cutoff_min: float = accessibility.GENERAL_CUTOFF_MIN
    speed_kmh: float = accessibility.EFFECTIVE_STRAIGHT_LINE_SPEED_KMH
    senior_multiple: float = SENIOR_ATTENDANCE_MULTIPLE
    min_population: float = MIN_VIABLE_POPULATION
    min_catchment_demand: float = MIN_CATCHMENT_DEMAND
    demand_floor: float = DEMAND_FLOOR_WEIGHTED
    demand_saturation: float = DEMAND_SATURATION_WEIGHTED
    dpa_share: float = DPA_OPERATIONAL_SHARE
    pharmacy_co_located_km: float = PHARMACY_CO_LOCATED_KM
    pharmacy_no_benefit_km: float = PHARMACY_NO_BENEFIT_KM
    #: Access criterion scaling: rank percentile (default) or min-max on the P2 to P98
    #: winsorised range. Both run, so the choice is measured rather than argued.
    access_scaling: str = "rank_percentile"
    #: D5 headcount demotion guard; 0.0 disables it, which is one of the sensitivity runs
    #: so a robust core that depends on the guard is visible rather than assumed.
    headcount_guard_strength: float = HEADCOUNT_GUARD_STRENGTH
    guard_well_supplied_ppg: float = GUARD_WELL_SUPPLIED_PPG
    guard_no_guard_ppg: float = GUARD_NO_GUARD_PPG

    @property
    def weight_map(self) -> dict[str, float]:
        return dict(self.weights)

    def normalised_weights(self) -> dict[str, float]:
        """Weights rescaled to sum to 1, so a perturbation cannot change the total's range."""
        w = self.weight_map
        total = sum(w.values())
        if total <= 0:
            raise ValueError("weights sum to zero")
        return {k: v / total for k, v in w.items()}


def with_weight(config: ScoreConfig, criterion: str, value: float) -> ScoreConfig:
    """Config copy with one weight replaced.

    >>> c = with_weight(ScoreConfig(), "access_gap", 0.2)
    >>> c.weight_map["access_gap"], c.weight_map["demand_scale"]
    (0.2, 0.25)
    >>> round(c.normalised_weights()["access_gap"], 4)
    0.25
    """
    if criterion not in BASE_WEIGHTS:
        raise ValueError(f"unknown criterion {criterion!r}")
    w = config.weight_map
    w[criterion] = value
    return replace(config, weights=tuple(w.items()))


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass
class Inputs:
    """
    Everything loaded from disk once, plus the expensive geometry.

    Matrices and the demand ring never vary with :class:`ScoreConfig`, so the 31-run
    sweep builds them once; surfaces vary with speed and cutoff, so are memoised.
    """

    frame: pd.DataFrame
    practice_cost_min: np.ndarray
    ring_cost_min: np.ndarray
    site_to_suburb_min: np.ndarray
    site_to_ring_min: np.ndarray
    ring_population: np.ndarray
    #: Knots from THIS frame's income column (:data:`INCOME_KNOT_PERCENTILES`). :func:`main`
    #: seeds the base config with them, so the sweep moves with the data vintage.
    income_knots: tuple[float, float, float, float] = INCOME_KNOTS
    _surfaces: dict[tuple[float, float], tuple[np.ndarray, np.ndarray]] = field(
        default_factory=dict
    )

    def surface(self, speed_kmh: float, cutoff_min: float) -> tuple[np.ndarray, np.ndarray]:
        """``(access_general, catchment_weighted_demand)`` for one speed and cutoff."""
        key = (float(speed_kmh), float(cutoff_min))
        if key not in self._surfaces:
            self._surfaces[key] = self._build_surface(*key)
        return self._surfaces[key]

    def _build_surface(
        self, speed_kmh: float, cutoff_min: float
    ) -> tuple[np.ndarray, np.ndarray]:
        base = accessibility.EFFECTIVE_STRAIGHT_LINE_SPEED_KMH
        rescale = base / speed_kmh  # cost is linear in 1/speed
        zones = zones_for_cutoff(cutoff_min)

        pop = self.frame["population"].to_numpy(float)
        o65 = self.frame["over_65_pct"].to_numpy(float)
        senior_share = float((pop * o65 / 100.0).sum() / pop.sum())

        # Access: E2SFCA step 1 over study suburbs PLUS the demand ring, step 2 reported
        # for the 680 only. Reusing accessibility.e2sfca means this cannot diverge from
        # the shipped surface; _test_reproduces_shipped_access asserts that it does not.
        cost_sites = self.practice_cost_min * rescale
        cost_demand = np.vstack([cost_sites, self.ring_cost_min * rescale])
        demand_vec = np.concatenate([pop, self.ring_population])
        access, _, _ = accessibility.e2sfca(
            cost_demand, demand_vec, zones, cost_eval=cost_sites
        )

        # Catchment demand: the senior-weighted pool a clinic AT this suburb would see,
        # using the same decay weights, so access and viability agree about reachability.
        mult = SENIOR_ATTENDANCE_MULTIPLE
        w_local = pop * (1.0 + (mult - 1.0) * o65 / 100.0)
        w_ring = self.ring_population * (1.0 + (mult - 1.0) * senior_share)
        catchment = accessibility.decay_weights(
            self.site_to_suburb_min * rescale, zones
        ) @ w_local + accessibility.decay_weights(
            self.site_to_ring_min * rescale, zones
        ) @ w_ring
        return access * 1000.0, catchment


def load_inputs(verbose: bool = True) -> Inputs:
    """Load the 680-suburb frame with every joined signal, and build the cost matrices."""
    eda = pd.read_csv(EDA_DATA)
    gp_supply.require_status_column(eda, EDA_DATA)

    frame = eda[
        [
            "suburb",
            "population",
            "over_65_pct",
            "median_household_income_weekly",
            "total_gp",
            "total_gp_is_floor",
            "gp_data_status",
        ]
    ].copy()

    joins = (
        (ACCESS_CSV, ["access_general", "access_general_pct", "practices_within_2km",
                      "nearest_practice_km", "access_senior"]),
        (CATCHMENT_CSV, ["gpc_code", "gpc_name", "overlap_fraction", "straddles_catchment"]),
        (DPA_CSV, ["dpa_status", "dpa_area_name", "ambiguous"]),
        (PRACTICE_COUNTS_CSV, ["practices_inside", "pharmacies_inside", "join_method"]),
    )
    for path, cols in joins:
        other = pd.read_csv(path)
        missing = set(cols) - set(other.columns)
        if missing:
            raise ValueError(f"{path} missing column(s): {sorted(missing)}")
        frame = frame.merge(
            other[["suburb", *cols]], on="suburb", how="left", validate="one_to_one"
        )
    frame = frame.rename(
        columns={
            "overlap_fraction": "catchment_overlap_fraction",
            "ambiguous": "dpa_ambiguous",
            "pharmacies_inside": "pharmacies",
        }
    )
    if len(frame) != 680 or frame["suburb"].duplicated().any():
        raise AssertionError(f"expected 680 unique suburbs, got {len(frame)}")
    unjoined = frame[["access_general", "gpc_name", "dpa_status", "pharmacies"]].isna()
    if unjoined.any().any():
        bad = frame.loc[unjoined.any(axis=1), "suburb"].tolist()
        raise AssertionError(f"suburbs missing a joined signal: {bad}")

    # Geometry, from the same SAL frame accessibility and dpa_status use.
    geo = accessibility.load_suburbs()
    frame = frame.merge(geo[["suburb", "lon", "lat"]], on="suburb", how="left",
                        validate="one_to_one")
    practices = accessibility.load_practices()
    ring = accessibility.load_outside_demand(geo)
    if verbose:
        print(f"loaded {len(frame)} suburbs, {len(practices)} NSW practices, "
              f"{len(ring)} demand-ring parts ({ring['population'].sum():,.0f} people)")

    speed = accessibility.EFFECTIVE_STRAIGHT_LINE_SPEED_KMH
    lat = frame["lat"].to_numpy(float)
    lon = frame["lon"].to_numpy(float)
    r_lat = ring["lat"].to_numpy(float)
    r_lon = ring["lon"].to_numpy(float)

    practice_cost = accessibility.straight_line_minutes(
        accessibility.distance_matrix_km(geo, practices), speed
    )
    ring_cost = accessibility.straight_line_minutes(
        accessibility.distance_matrix_km(ring, practices), speed
    )
    site_to_suburb = accessibility.straight_line_minutes(
        accessibility.haversine_km(lat[:, None], lon[:, None], lat[None, :], lon[None, :]),
        speed,
    )
    site_to_ring = accessibility.straight_line_minutes(
        accessibility.haversine_km(lat[:, None], lon[:, None], r_lat[None, :], r_lon[None, :]),
        speed,
    )

    # Pharmacy co-location, measured from the register rather than taken from the
    # inside-polygon count. Counting facilities inside polygons is the specific error
    # this whole rebuild exists to remove, and it applies to pharmacies too: 288 of 680
    # suburbs contain no pharmacy, but only 96 have none within 2 km.
    pharm = pd.read_csv(PHARMACIES_CSV)
    needed = {"latitude", "longitude", "organisation_name", "suburb"}
    if needed - set(pharm.columns):
        raise ValueError(f"{PHARMACIES_CSV} missing {sorted(needed - set(pharm.columns))}")
    d_ph = accessibility.haversine_km(
        lat[:, None], lon[:, None],
        pharm["latitude"].to_numpy(float)[None, :],
        pharm["longitude"].to_numpy(float)[None, :],
    )
    nearest = d_ph.argmin(axis=1)
    frame["nearest_pharmacy_km"] = d_ph[np.arange(len(d_ph)), nearest].round(3)
    frame["nearest_pharmacy_name"] = pharm["organisation_name"].to_numpy()[nearest]
    frame["pharmacies_within_1km"] = (d_ph <= PHARMACY_COUNT_RADIUS_KM).sum(axis=1)

    # D4 exclusion at catchment level.
    known = set(frame["suburb"])
    unknown_sites = [s for s in NUVO_SITES if s not in known]
    if unknown_sites:
        raise ValueError(f"NUVO_SITES not in the 680: {unknown_sites}")
    nuvo_catchments = set(frame.loc[frame["suburb"].isin(NUVO_SITES), "gpc_code"])
    frame["excluded_nuvo_catchment"] = frame["gpc_code"].isin(nuvo_catchments)
    frame["nuvo_exclusion_note"] = NUVO_UNVERIFIED_NOTE
    if verbose:
        print(f"D4: {len(NUVO_SITES)} Nuvo sites -> {len(nuvo_catchments)} catchments -> "
              f"{int(frame['excluded_nuvo_catchment'].sum())} suburbs excluded "
              f"({NUVO_UNVERIFIED_NOTE})")

    return Inputs(
        frame=frame,
        practice_cost_min=practice_cost,
        ring_cost_min=ring_cost,
        site_to_suburb_min=site_to_suburb,
        site_to_ring_min=site_to_ring,
        ring_population=ring["population"].to_numpy(float),
        income_knots=income_knots_from_percentiles(
            frame["median_household_income_weekly"].to_numpy(float)
        ),
    )


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def headcount_adequacy(
    people_per_gp: float,
    well_supplied: float = GUARD_WELL_SUPPLIED_PPG,
    no_guard: float = GUARD_NO_GUARD_PPG,
) -> float:
    """
    How strongly a trustworthy GP headcount contradicts a high ``access_gap``.

    1.0 means doctors per resident at least matching the eligible cohort's best supplied
    quartile, so a high gap score is probably an artefact of the one-unit-per-practice
    supply term. 0.0 means no demotion applies.

    >>> headcount_adequacy(400.0)
    1.0
    >>> headcount_adequacy(640.0)
    1.0
    >>> headcount_adequacy(1040.0)
    0.0
    >>> headcount_adequacy(3000.0)
    0.0
    >>> round(headcount_adequacy(840.0), 3)
    0.5

    The measured top-10 cases the guard exists for, and the two it must NOT touch:

    >>> round(headcount_adequacy(667.0), 2)
    0.93
    >>> headcount_adequacy(494.0)
    1.0
    >>> headcount_adequacy(2237.0)
    0.0
    >>> headcount_adequacy(3156.0)
    0.0

    Missing, zero or negative headcount NEVER demotes: penalising a failed scrape was the
    v1 defect that put seven never-scraped suburbs in the top 10.

    >>> headcount_adequacy(float("nan"))
    0.0
    >>> headcount_adequacy(0.0)
    0.0
    >>> headcount_adequacy(-5.0)
    0.0
    """
    if not well_supplied < no_guard:
        raise ValueError(
            f"guard knots must ascend, got {well_supplied} then {no_guard}"
        )
    if math.isnan(people_per_gp) or people_per_gp <= 0.0:
        return 0.0
    if people_per_gp <= well_supplied:
        return 1.0
    if people_per_gp >= no_guard:
        return 0.0
    return (no_guard - people_per_gp) / (no_guard - well_supplied)


def access_gap_scores(access_general: np.ndarray, scaling: str) -> np.ndarray:
    """
    Invert an accessibility surface into an opportunity sub-score on [0, 1].

    ``rank_percentile`` (default) survives the eight suburbs with access exactly 0 and the
    ferry-only suburbs whose straight-line values :mod:`accessibility` calls meaningless.
    ``winsorised_minmax`` (P2 to P98) keeps magnitude instead, run as a sensitivity case.

    >>> a = np.array([0.0, 0.1, 0.2, 0.3, 0.4])
    >>> access_gap_scores(a, "rank_percentile").round(2)
    array([0.8, 0.6, 0.4, 0.2, 0. ])
    >>> access_gap_scores(a, "winsorised_minmax").round(3)
    array([1.  , 0.76, 0.5 , 0.24, 0.  ])
    """
    arr = np.asarray(access_general, dtype=float)
    if scaling == "rank_percentile":
        return 1.0 - pd.Series(arr).rank(pct=True).to_numpy()
    if scaling == "winsorised_minmax":
        lo, hi = np.percentile(arr, 2.0), np.percentile(arr, 98.0)
        if hi <= lo:
            raise ValueError("degenerate accessibility distribution")
        return np.clip((hi - arr) / (hi - lo), 0.0, 1.0)
    raise ValueError(f"unknown access_scaling {scaling!r}")


def score(inputs: Inputs, config: ScoreConfig | None = None) -> pd.DataFrame:
    """
    Score all 680 suburbs under one configuration. Ineligible suburbs keep every
    sub-score but get ``rank`` as NA, so an exclusion is visible rather than absent.
    """
    cfg = config or ScoreConfig()
    out = inputs.frame.copy()
    access, catchment = inputs.surface(cfg.speed_kmh, cfg.general_cutoff_min)

    out["access_general_scored"] = np.round(access, 6)
    out["catchment_weighted_demand"] = np.round(catchment, 0)
    out["access_gap"] = access_gap_scores(access, cfg.access_scaling)

    pop = out["population"].to_numpy(float)
    o65 = out["over_65_pct"].to_numpy(float)
    weighted = np.array(
        [senior_weighted_population(p, s, cfg.senior_multiple) for p, s in zip(pop, o65)]
    )
    out["senior_weighted_population"] = np.round(weighted, 1)
    out["demand_scale"] = [
        log_ramp(w, cfg.demand_floor, cfg.demand_saturation) for w in weighted
    ]

    out["mixed_billing_fit"] = [
        mixed_billing_fit(v, cfg.income_knots)
        for v in out["median_household_income_weekly"].to_numpy(float)
    ]
    out["community_need_income_fit"] = [
        community_need_income_fit(v, cfg.income_knots)
        for v in out["median_household_income_weekly"].to_numpy(float)
    ]

    out["pharmacy_fit"] = [
        pharmacy_score(k, cfg.pharmacy_co_located_km, cfg.pharmacy_no_benefit_km)
        for k in out["nearest_pharmacy_km"].to_numpy(float)
    ]
    out["operational"] = [
        operational_score(p, d == "Y", cfg.dpa_share)
        for p, d in zip(out["pharmacy_fit"], out["dpa_status"])
    ]

    # Applied to access_gap only: that is the criterion the one-unit-per-practice supply
    # term distorts, and it reaches the total through the existing 0.40 weight rather than
    # a second knob. access_gap_unguarded is kept so each demotion can be checked.
    total_gp = out["total_gp"].to_numpy(float)
    status = out["gp_data_status"].astype(str).to_numpy()
    trustworthy = (status == "ok") & (total_gp > 0) & (pop > 0)
    ppg = np.where(trustworthy, np.divide(pop, total_gp, out=np.zeros_like(pop),
                                          where=total_gp > 0), np.nan)
    out["people_per_gp"] = np.round(ppg, 1)
    adequacy = np.array([
        headcount_adequacy(v, cfg.guard_well_supplied_ppg, cfg.guard_no_guard_ppg)
        for v in ppg
    ])
    out["headcount_adequacy"] = np.round(adequacy, 4)
    out["access_gap_unguarded"] = out["access_gap"]
    out["access_gap"] = out["access_gap"].to_numpy(float) * (
        1.0 - cfg.headcount_guard_strength * adequacy
    )
    out["headcount_demoted"] = adequacy > 0.0

    # Viability gates. Both are hard: a suburb below either gets no rank at all, because
    # a soft penalty still let v1 rank a 1,334-person enclave first.
    below_pop = pop < cfg.min_population
    below_catchment = catchment < cfg.min_catchment_demand
    out["below_population_floor"] = below_pop
    out["below_catchment_floor"] = below_catchment
    out["viable"] = ~(below_pop | below_catchment)

    reasons = []
    for pop_bad, cat_bad, nuvo in zip(
        below_pop, below_catchment, out["excluded_nuvo_catchment"]
    ):
        parts = []
        if pop_bad:
            parts.append(f"population below {cfg.min_population:,.0f}")
        if cat_bad:
            parts.append(f"catchment demand below {cfg.min_catchment_demand:,.0f}")
        if nuvo:
            parts.append("existing Nuvo catchment (unverified)")
        reasons.append("; ".join(parts))
    out["exclusion_reason"] = reasons

    weights = cfg.normalised_weights()
    out["site_score"] = sum(out[c].to_numpy(float) * weights[c] for c in CRITERIA)
    community = dict(weights)
    out["community_need_score"] = (
        out["access_gap"].to_numpy(float) * community["access_gap"]
        + out["demand_scale"].to_numpy(float) * community["demand_scale"]
        + out["community_need_income_fit"].to_numpy(float) * community["mixed_billing_fit"]
        + out["operational"].to_numpy(float) * community["operational"]
    )

    eligible = out["viable"] & ~out["excluded_nuvo_catchment"]
    out["eligible"] = eligible
    out["rank"] = pd.Series(pd.NA, index=out.index, dtype="Int64")
    out.loc[eligible, "rank"] = (
        out.loc[eligible, "site_score"].rank(ascending=False, method="min").astype(int)
    )
    out["community_need_rank"] = pd.Series(pd.NA, index=out.index, dtype="Int64")
    out.loc[eligible, "community_need_rank"] = (
        out.loc[eligible, "community_need_score"]
        .rank(ascending=False, method="min")
        .astype(int)
    )

    # Two eligible suburbs in the same GP catchment are largely the SAME opportunity, not
    # two: a clinic in either draws on the same practice pool and the same population.
    # Without this flag a shortlist can present one catchment three times and look more
    # diversified than it is.
    out["catchment_rank"] = pd.Series(pd.NA, index=out.index, dtype="Int64")
    out.loc[eligible, "catchment_rank"] = (
        out.loc[eligible]
        .groupby("gpc_code")["site_score"]
        .rank(ascending=False, method="min")
        .astype(int)
    )
    out["best_in_catchment"] = out["catchment_rank"].eq(1)
    return out


def top_suburbs(frame: pd.DataFrame, n: int = TOP_N) -> list[str]:
    """The ranked top ``n`` eligible suburbs, best first."""
    ranked = frame.loc[frame["eligible"]].nlargest(n, "site_score")
    return ranked["suburb"].tolist()


# ---------------------------------------------------------------------------
# Sensitivity analysis
# ---------------------------------------------------------------------------


def perturbations(base: ScoreConfig | None = None) -> list[tuple[str, str, ScoreConfig]]:
    """
    The full perturbation set as ``(family, label, config)``, baseline excluded.

    Four families the brief requires (weights, income knots, cutoff, robust core), plus
    four judgement calls left untested otherwise: viability floor, senior multiple, DPA
    share, and the speed constant :mod:`accessibility` calls its least defensible number.

    >>> labels = [lab for _, lab, _ in perturbations()]
    >>> len(labels), len(set(labels))
    (31, 31)
    >>> "weight access_gap x0.5" in labels, "cutoff 25 min" in labels
    (True, True)
    """
    cfg = base or ScoreConfig()
    out: list[tuple[str, str, ScoreConfig]] = []

    # 1. Weights: +/- 50 percent and to zero, renormalised to sum to 1.
    for criterion, w in BASE_WEIGHTS.items():
        for factor, tag in ((0.5, "x0.5"), (1.5, "x1.5"), (0.0, "to 0")):
            out.append(
                ("weight", f"weight {criterion} {tag}", with_weight(cfg, criterion, w * factor))
            )

    # 2. Income knots +/- $250: shift the whole band, then widen and narrow the plateau.
    k = cfg.income_knots
    out.append(("income", "income knots -$250", replace(
        cfg, income_knots=tuple(v - 250.0 for v in k))))
    out.append(("income", "income knots +$250", replace(
        cfg, income_knots=tuple(v + 250.0 for v in k))))
    out.append(("income", "income plateau widened $250", replace(
        cfg, income_knots=(k[0], k[1] - 250.0, k[2] + 250.0, k[3]))))
    out.append(("income", "income plateau narrowed $250", replace(
        cfg, income_knots=(k[0], k[1] + 250.0, k[2] - 250.0, k[3]))))

    # 3. Travel cutoff (D2 is 20 min general).
    for cutoff in (15.0, 25.0):
        out.append(("cutoff", f"cutoff {cutoff:g} min", replace(cfg, general_cutoff_min=cutoff)))

    # 4. Viability floor, including 0.0 (no local floor at all) and 4,000 (the original
    # value), so both ends of the choice are tested rather than argued.
    for floor in (0.0, 2000.0, 4000.0):
        out.append(("floor", f"population floor {floor:,.0f}", replace(cfg, min_population=floor)))

    # 5. Senior attendance multiple.
    for mult in (1.5, 4.0):
        out.append(("senior", f"senior multiple {mult:g}x", replace(cfg, senior_multiple=mult)))

    # 6. DPA share of the operational criterion.
    for share in (0.0, 0.6):
        out.append(("dpa", f"dpa share {share:g}", replace(cfg, dpa_share=share)))

    # 7. Speed constant and the access scaling choice.
    for speed in (30.0, 40.0):
        out.append(("speed", f"speed {speed:g} km/h", replace(cfg, speed_kmh=speed)))
    out.append(("scaling", "access winsorised minmax",
                replace(cfg, access_scaling="winsorised_minmax")))

    # 8. D5 headcount guard. Includes OFF, so the sweep answers whether the robust core
    # survives without the guard as well as with it.
    for strength, tag in ((0.0, "off"), (0.25, "0.25"), (0.75, "0.75")):
        out.append(("guard", f"headcount guard {tag}",
                    replace(cfg, headcount_guard_strength=strength)))

    return out


def run_sensitivity(
    inputs: Inputs, base: ScoreConfig | None = None, n: int = TOP_N
) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
    """
    Re-rank under every perturbation, returning ``(long, stability, core)``; ``core`` is
    the suburbs that are top ``n`` in the baseline AND in every perturbation.
    """
    cfg = base or ScoreConfig()
    runs = [("baseline", "baseline", cfg), *perturbations(cfg)]
    rows: list[pd.DataFrame] = []
    top_sets: dict[str, list[str]] = {}

    for family, label, run_cfg in runs:
        frame = score(inputs, run_cfg)
        top_sets[label] = top_suburbs(frame, n)
        rows.append(
            pd.DataFrame(
                {
                    "run": label,
                    "family": family,
                    "suburb": frame["suburb"],
                    "site_score": frame["site_score"].round(6),
                    "rank": frame["rank"],
                    "eligible": frame["eligible"],
                    "in_top_n": frame["suburb"].isin(top_sets[label]),
                }
            )
        )

    long = pd.concat(rows, ignore_index=True)
    n_perturb = len(runs) - 1

    perturbed = long.loc[long["run"] != "baseline"]
    hits = perturbed.groupby("suburb")["in_top_n"].sum()
    base_frame = long.loc[long["run"] == "baseline"].set_index("suburb")
    stability = pd.DataFrame(
        {
            "suburb": hits.index,
            "top_n_hits": hits.to_numpy(int),
            "n_perturbations": n_perturb,
            "rank_stability": (hits.to_numpy(float) / n_perturb).round(4),
        }
    )
    stability["baseline_rank"] = base_frame.loc[stability["suburb"], "rank"].to_numpy()
    worst = perturbed.loc[perturbed["eligible"]].groupby("suburb")["rank"].max()
    best = perturbed.loc[perturbed["eligible"]].groupby("suburb")["rank"].min()
    stability["worst_rank"] = stability["suburb"].map(worst).astype("Int64")
    stability["best_rank"] = stability["suburb"].map(best).astype("Int64")
    stability["runs_eligible"] = stability["suburb"].map(
        perturbed.groupby("suburb")["eligible"].sum()
    ).astype(int)

    baseline_top = set(top_sets["baseline"])
    robust = baseline_top.intersection(*(set(v) for k, v in top_sets.items() if k != "baseline"))
    core = [s for s in top_sets["baseline"] if s in robust]

    # Second, weaker tier. Setting a weight to 0 does not perturb this model, it replaces
    # it with a different one: "weight access_gap to 0" asks a suburb to be top 10 in a
    # score that has deleted the core question. Surviving that is remarkable but failing
    # it is not evidence of instability, so the two tiers are reported separately.
    degenerate = {lab for _, lab, _ in perturbations() if lab.endswith("to 0")}
    non_degenerate = [
        set(v) for k, v in top_sets.items() if k != "baseline" and k not in degenerate
    ]
    near = baseline_top.intersection(*non_degenerate)
    near_core = [s for s in top_sets["baseline"] if s in near]

    long.attrs["top_sets"] = top_sets
    long.attrs["degenerate_runs"] = sorted(degenerate)
    long.attrs["robust_core"] = core
    long.attrs["near_core"] = near_core
    return long, stability, core


def report_sensitivity(
    long: pd.DataFrame, stability: pd.DataFrame, core: list[str], n: int = TOP_N
) -> None:
    """Print the readable summary that makes the weights defensible."""
    top_sets: dict[str, list[str]] = long.attrs["top_sets"]
    baseline = top_sets["baseline"]

    print()
    print("=" * 78)
    print(f"SENSITIVITY: {len(top_sets) - 1} perturbations, top-{n} churn against baseline")
    print("=" * 78)
    print(f"{'run':32s} {'churn':>6s}  {'in':>3s} {'out':>3s}  entered / left")
    for label, members in top_sets.items():
        if label == "baseline":
            continue
        entered = [s for s in members if s not in baseline]
        left = [s for s in baseline if s not in members]
        churn = len(entered)
        print(f"{label:32s} {churn:5d}/{n}  {len(entered):3d} {len(left):3d}  "
              f"+{','.join(entered) if entered else '-'} / "
              f"-{','.join(left) if left else '-'}")

    print()
    print(f"Rank stability (share of {int(stability['n_perturbations'].iloc[0])} perturbations "
          f"keeping the suburb top {n}):")
    show = stability.sort_values(
        ["rank_stability", "baseline_rank"], ascending=[False, True]
    ).head(18)
    print(f"  {'suburb':22s} {'base':>5s} {'stab':>6s} {'best':>5s} {'worst':>6s}")
    for _, r in show.iterrows():
        base_rank = "-" if pd.isna(r["baseline_rank"]) else f"{int(r['baseline_rank'])}"
        print(f"  {r['suburb']:22s} {base_rank:>5s} {r['rank_stability']:6.2f} "
              f"{r['best_rank']:5} {r['worst_rank']:6}")

    n_degen = len(long.attrs["degenerate_runs"])
    print()
    print(f"ROBUST CORE - top {n} under the baseline AND all {len(top_sets)-1} perturbations:")
    if core:
        for i, s in enumerate(core, 1):
            print(f"  {i}. {s}")
    else:
        print("  EMPTY - no suburb survives every perturbation. Report that, do not "
              "relax the test until something appears.")
    near = long.attrs["near_core"]
    print()
    print(f"  Second tier: top {n} under the {len(top_sets)-1-n_degen} non-degenerate "
          f"perturbations, i.e. excluding the {n_degen} runs that DELETE a criterion "
          f"entirely ({', '.join(long.attrs['degenerate_runs'])}).")
    print(f"  {', '.join(near) if near else 'EMPTY'}")


def report_criterion_influence(frame: pd.DataFrame, config: ScoreConfig) -> None:
    """
    Measure what each criterion actually does: ``w*sd``, not the nominal weight, is the
    real discriminating power, because a criterion where most suburbs share a value
    cannot move a ranking however heavily weighted. ``r(total)`` shows what it tracks.
    """
    weights = config.normalised_weights()
    elig = frame.loc[frame["eligible"]]
    print()
    print("=" * 78)
    print(f"CRITERION INFLUENCE over the {len(elig)} eligible suburbs")
    print("=" * 78)
    print(f"  {'criterion':20s} {'weight':>7s} {'mean':>7s} {'sd':>7s} {'w*sd':>7s} "
          f"{'at 1.0':>7s} {'r(total)':>9s}")
    for c in CRITERIA:
        v = elig[c].to_numpy(float)
        r = float(np.corrcoef(v, elig["site_score"].to_numpy(float))[0, 1])
        print(f"  {c:20s} {weights[c]:7.3f} {v.mean():7.3f} {v.std():7.3f} "
              f"{weights[c]*v.std():7.4f} {100*(v >= 0.999).mean():6.1f}% {r:9.3f}")
    print()
    print("  Pairwise correlation between sub-scores (double counting shows up here):")
    print(f"    {'':20s}" + "".join(f"{c[:9]:>10s}" for c in CRITERIA))
    for a in CRITERIA:
        line = f"    {a:20s}"
        for b in CRITERIA:
            line += f"{np.corrcoef(elig[a].to_numpy(float), elig[b].to_numpy(float))[0,1]:10.3f}"
        print(line)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _test_income_shape() -> None:
    """The D1 curve really is an inverted U, and the v1 shape really is different."""
    lo_start, lo_end, hi_start, hi_end = INCOME_KNOTS
    grid = np.linspace(0.0, 7000.0, 701)
    vals = np.array([mixed_billing_fit(float(x)) for x in grid])
    assert vals.min() == 0.0 and vals.max() == 1.0, "sub-score leaves [0, 1]"
    plateau = (grid >= lo_end) & (grid <= hi_start)
    assert np.allclose(vals[plateau], 1.0), "plateau is not flat at 1.0"
    rising = vals[(grid > lo_start) & (grid < lo_end)]
    assert np.all(np.diff(rising) > 0), "lower taper is not increasing"
    falling = vals[(grid > hi_start) & (grid < hi_end)]
    assert np.all(np.diff(falling) < 0), "upper taper is not decreasing"
    # The defect D1 exists to fix: a rich suburb must not outscore a mid one.
    assert mixed_billing_fit(3800.0) < mixed_billing_fit(2400.0)
    # Continuity at the knots, so a $1 income difference cannot jump the score.
    for knot in INCOME_KNOTS:
        left = mixed_billing_fit(knot - 1e-6)
        right = mixed_billing_fit(knot + 1e-6)
        assert abs(left - right) < 1e-4, f"discontinuity at {knot}"


def _test_smallness_trap() -> None:
    """
    The specific v1 failures must be structurally impossible, not merely unlikely.

    Point Piper (population 1,334, income far above the upper knot) and Wisemans Ferry
    (population 233, accessibility exactly 0, so a perfect access_gap score) are the two
    ways a score can be gamed by smallness.
    """
    assert log_ramp(1334.0 * 1.3, DEMAND_FLOOR_WEIGHTED, DEMAND_SATURATION_WEIGHTED) == 0.0
    assert 233.0 < MIN_VIABLE_POPULATION, "Wisemans Ferry must fail the population gate"

    # Point Piper NO LONGER fails the population gate: the floor moved from 4,000 to
    # 1,000 and it has 1,334 residents. That is deliberate, and the protection against it
    # is now structural rather than a gate it happens to sit under. TWO sub-scores are
    # pinned at zero independently:
    #   demand_scale       - asserted above, its senior-weighted base is below the ramp
    #   mixed_billing_fit  - income $4,530 is above the upper knot, the D1 inverted U
    # so the most it can score is access_gap plus operational, both at a perfect 1.0.
    assert mixed_billing_fit(4530.0) == 0.0, "Point Piper must score zero on income"
    cfg = ScoreConfig()
    w = cfg.normalised_weights()
    ceiling = w["access_gap"] * 1.0 + w["operational"] * 1.0
    assert ceiling <= 0.50, f"smallness ceiling drifted to {ceiling:.3f}"
    # MEASURED: the rank-10 site_score is about 0.78, so a 0.50 ceiling cannot reach the
    # shortlist even with a perfect access gap. If the weights ever move enough to close
    # that margin, this assertion is the thing that should fail first.
    assert ceiling < 0.70, (
        "a suburb zeroed on demand and income could now reach the shortlist on access "
        "alone, which is the v1 smallness trap returning by a different route"
    )


def _test_weight_normalisation() -> None:
    """Zeroing a weight must not shrink the score's range and change every comparison."""
    for criterion in BASE_WEIGHTS:
        cfg = with_weight(ScoreConfig(), criterion, 0.0)
        w = cfg.normalised_weights()
        assert math.isclose(sum(w.values()), 1.0), f"weights do not sum to 1 for {criterion}"
        assert math.isclose(w[criterion], 0.0)
    base = ScoreConfig().normalised_weights()
    assert math.isclose(sum(base.values()), 1.0)
    assert math.isclose(base["access_gap"], 0.40)


def _test_synthetic_score() -> None:
    """A hand-computed total, so the weighted sum cannot silently change."""
    cfg = ScoreConfig()
    w = cfg.normalised_weights()
    # A suburb on the income plateau, 12,500 senior-weighted people, a pharmacy 1.2 km
    # away, DPA=N, and accessibility at the median of a symmetric 5-point surface.
    access = access_gap_scores(np.array([0.0, 0.1, 0.2, 0.3, 0.4]), "rank_percentile")[2]
    demand = log_ramp(12500.0, cfg.demand_floor, cfg.demand_saturation)
    income = mixed_billing_fit(2400.0)
    oper = operational_score(pharmacy_score(1.2), False)
    total = w["access_gap"] * access + w["demand_scale"] * demand + \
        w["mixed_billing_fit"] * income + w["operational"] * oper
    expected = 0.40 * 0.4 + 0.25 * 0.6610 + 0.25 * 1.0 + 0.10 * 0.35
    assert abs(total - expected) < 1e-3, f"{total} != {expected}"


def _test_reproduces_shipped_access(inputs: Inputs) -> None:
    """
    The recomputed 20-minute surface must match ``suburb_accessibility.csv``.

    This is the check that the sensitivity sweep is perturbing the SAME model that
    produced the shipped accessibility table, rather than a lookalike of it.
    """
    access, _ = inputs.surface(
        accessibility.EFFECTIVE_STRAIGHT_LINE_SPEED_KMH, accessibility.GENERAL_CUTOFF_MIN
    )
    shipped = inputs.frame["access_general"].to_numpy(float)
    worst = float(np.abs(access - shipped).max())
    assert worst < 1e-6, f"recomputed access differs from shipped by {worst:.2e}"
    print(f"  recomputed access matches suburb_accessibility.csv to {worst:.1e}")


def _test_monotone_in_access(inputs: Inputs) -> None:
    """
    Worse accessibility must always mean a higher access_gap sub-score, never lower.

    Spearman rather than Pearson: the default scaling is a rank percentile, so a
    monotone inversion is exactly -1.0 on ranks but only about -0.98 on levels. Testing
    Pearson here would be testing linearity, which is not what is being claimed.
    """
    frame = score(inputs)
    elig = frame.loc[frame["eligible"]]
    # access_gap_unguarded, not access_gap: the headcount guard breaks this monotonicity
    # by design, so testing the guarded column would assert the guard does nothing.
    rho = float(
        elig["access_general_scored"].corr(elig["access_gap_unguarded"], method="spearman")
    )
    assert abs(rho + 1.0) < 1e-9, f"access_gap is not a monotone inversion (rho={rho:.6f})"
    alt = access_gap_scores(
        frame["access_general_scored"].to_numpy(float), "winsorised_minmax"
    )
    rho_alt = float(pd.Series(frame["access_general_scored"]).corr(
        pd.Series(alt), method="spearman"))
    assert rho_alt < -0.99, f"alternative scaling is not monotone (rho={rho_alt:.6f})"


def _test_derived_income_knots(inputs: Inputs) -> None:
    """
    The derived knots must sit at their target percentiles, and the income sub-score
    must be invariant to a uniform rescale of the income column.

    The second assertion is the one that matters. It is the property the hardcoded
    dollar knots did NOT have, and it is what stops the 2021 vintage of the income
    column being a silent dependency of the ranking.
    """
    income = inputs.frame["median_household_income_weekly"].to_numpy(float)
    knots = inputs.income_knots
    clean = income[np.isfinite(income) & (income > 0.0)]
    for knot, target in zip(knots, INCOME_KNOT_PERCENTILES):
        got = float((clean < knot).mean() * 100.0)
        assert abs(got - target) < 1.0, (
            f"knot {knot:,.0f} sits at P{got:.1f}, expected P{target:.0f}"
        )

    factor = 1.2
    rescaled = income_knots_from_percentiles(clean * factor)
    before = [mixed_billing_fit(v, knots) for v in clean]
    after = [mixed_billing_fit(v * factor, rescaled) for v in clean]
    worst = max(abs(a - b) for a, b in zip(before, after))
    assert worst < 1e-9, f"income fit not scale invariant, worst delta {worst:.2e}"


def _test_headcount_guard(inputs: Inputs) -> None:
    """
    The D5 guard may only ever move a suburb DOWN, and only on trustworthy data.

    Three invariants, each of which would be a real defect if broken:

    1. The guard never promotes. If it could raise ``access_gap`` it would be a
       ranking input, which reverses D5 and leans on the discredited scrape.
    2. It never fires where ``gp_data_status != "ok"``. Firing on missing data would
       recreate the v1 defect in mirror image: absence of evidence read as evidence.
    3. It actually bites on the case it was built for. A guard that demotes nothing is
       a comment, not a control.
    """
    frame = score(inputs)
    guarded = frame["access_gap"].to_numpy(float)
    raw = frame["access_gap_unguarded"].to_numpy(float)
    assert (guarded <= raw + 1e-12).all(), "guard raised at least one access_gap"

    untrusted = frame["gp_data_status"].astype(str).to_numpy() != "ok"
    assert not frame.loc[untrusted, "headcount_demoted"].any(), (
        "guard fired on a suburb without trustworthy headcount"
    )

    demoted = int(frame["headcount_demoted"].sum())
    assert demoted > 0, "guard demoted nothing"

    off = score(inputs, replace(ScoreConfig(), headcount_guard_strength=0.0))
    assert (
        off["access_gap"].to_numpy(float) == off["access_gap_unguarded"].to_numpy(float)
    ).all(), "strength 0.0 still altered access_gap"


def _test_catchment_dedupe(inputs: Inputs) -> None:
    """
    No two suburbs marked "recommended" may share a GP catchment.

    This is the Croydon objection applied to the OUTPUT rather than the input: having
    fixed supply so it is measured across boundaries, a shortlist that then lists two
    suburbs from one catchment reintroduces the same error at the point of decision.
    """
    frame = score(inputs)
    elig = frame.loc[frame["eligible"]].sort_values("site_score", ascending=False)
    top = elig.head(TOP_N)
    leader = top.groupby("gpc_code")["suburb"].transform("first")
    recommended = top.loc[top["suburb"] == leader]
    assert recommended["gpc_code"].is_unique, (
        "two recommended suburbs share a catchment: "
        f"{recommended.loc[recommended['gpc_code'].duplicated(keep=False), 'suburb'].tolist()}"
    )
    assert len(recommended) < len(top), (
        "dedupe removed nothing from the top 10, so either the data changed or the "
        "grouping is not working - check before assuming this is good news"
    )


def _test_nuvo_catchment_exclusion(inputs: Inputs) -> None:
    """
    D4 must exclude the CATCHMENT, not just the suburb.

    A suburb-level exclusion would leave Randwick open next to Nuvo Maroubra, which is
    the cannibalisation the decision exists to prevent.
    """
    frame = inputs.frame
    excluded = set(frame.loc[frame["excluded_nuvo_catchment"], "suburb"])
    for site in NUVO_SITES:
        assert site in excluded, f"{site} itself not excluded"
    neighbours = {"Randwick", "Coogee", "Lilyfield", "Rozelle", "Mosman", "Pennant Hills"}
    missing = neighbours - excluded
    assert not missing, f"catchment neighbours not excluded: {sorted(missing)}"
    assert len(excluded) > len(NUVO_SITES) * 3, "catchment exclusion looks suburb sized"


def run_tests(inputs: Inputs | None = None) -> None:
    """Doctests plus the assertion cases. ``inputs`` enables the data-backed checks."""
    import doctest

    result = doctest.testmod(verbose=False, report=True)
    if result.failed:
        raise SystemExit(f"{result.failed} doctest(s) failed")

    checks = [_test_income_shape, _test_smallness_trap, _test_weight_normalisation,
              _test_synthetic_score]
    for fn in checks:
        fn()
    data_checks = 0
    if inputs is not None:
        for fn in (_test_reproduces_shipped_access, _test_monotone_in_access,
                   _test_headcount_guard, _test_derived_income_knots,
                   _test_catchment_dedupe,
                   _test_nuvo_catchment_exclusion):
            fn(inputs)
            data_checks += 1
    print(f"tests OK: {len(checks)} assert cases, {data_checks} data-backed cases, "
          f"{result.attempted} doctests")


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

#: Column order for ``site_scores_v2.csv``. Required names first, provenance after.
SCORE_COLUMNS = [
    "suburb",
    "access_gap",
    "access_gap_unguarded",
    "people_per_gp",
    "headcount_adequacy",
    "headcount_demoted",
    "demand_scale",
    "mixed_billing_fit",
    "operational",
    "site_score",
    "community_need_score",
    "rank",
    "rank_stability",
    "gp_data_status",
    "practices_within_2km",
    "nearest_practice_km",
    "gpc_name",
    "dpa_status",
    "pharmacies",
    "excluded_nuvo_catchment",
    # appended provenance and context
    "community_need_rank",
    "catchment_rank",
    "best_in_catchment",
    "robust_core_tier",
    "eligible",
    "viable",
    "exclusion_reason",
    "below_population_floor",
    "below_catchment_floor",
    "population",
    "over_65_pct",
    "senior_weighted_population",
    "catchment_weighted_demand",
    "median_household_income_weekly",
    "community_need_income_fit",
    "pharmacy_fit",
    "nearest_pharmacy_km",
    "nearest_pharmacy_name",
    "pharmacies_within_1km",
    "access_general_scored",
    "access_general_pct",
    "practices_inside",
    "gpc_code",
    "catchment_overlap_fraction",
    "straddles_catchment",
    "dpa_area_name",
    "dpa_ambiguous",
    "join_method",
    "top_n_hits",
    "n_perturbations",
    "best_rank",
    "worst_rank",
    "total_gp_context_only",
    "total_gp_is_floor",
    "nuvo_exclusion_note",
    "source_note",
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--test", action="store_true", help="run self-checks and exit")
    parser.add_argument("--no-write", action="store_true", help="report only")
    args = parser.parse_args()

    inputs = load_inputs()
    if args.test:
        run_tests(inputs)
        return
    run_tests(inputs)

    base = ScoreConfig(income_knots=inputs.income_knots)
    frame = score(inputs, base)
    long, stability, core = run_sensitivity(inputs, base)

    frame = frame.merge(
        stability[["suburb", "rank_stability", "top_n_hits", "n_perturbations",
                   "best_rank", "worst_rank"]],
        on="suburb", how="left", validate="one_to_one",
    )
    frame = frame.rename(columns={"total_gp": "total_gp_context_only"})
    frame["source_note"] = SOURCE_NOTE
    near_core = long.attrs["near_core"]
    frame["robust_core_tier"] = np.where(
        frame["suburb"].isin(core),
        "core_all_perturbations",
        np.where(frame["suburb"].isin(near_core), "core_excluding_zeroed_weights", ""),
    )

    missing = [c for c in SCORE_COLUMNS if c not in frame.columns]
    if missing:
        raise AssertionError(f"output columns missing: {missing}")
    out = frame[SCORE_COLUMNS].sort_values(
        ["rank", "site_score"], ascending=[True, False], na_position="last"
    )
    for col in ("access_gap", "demand_scale", "mixed_billing_fit", "operational",
                "site_score", "community_need_score", "community_need_income_fit",
                "pharmacy_fit"):
        out[col] = out[col].round(6)

    print()
    print("=" * 78)
    print(f"TOP {TOP_N} - commercial mixed-billing lens (D3 primary)")
    print("=" * 78)
    cols = ["rank", "suburb", "site_score", "access_gap", "demand_scale",
            "mixed_billing_fit", "operational", "population", "over_65_pct",
            "median_household_income_weekly", "practices_within_2km", "dpa_status"]
    print(out.loc[out["eligible"]].head(TOP_N)[cols].to_string(index=False))

    top = out.loc[out["eligible"]].head(TOP_N)
    by_catch = top.groupby("gpc_name")["suburb"].apply(list)
    print()
    print(f"  The top {TOP_N} spans only {len(by_catch)} GP catchments. Same-catchment "
          "entries are the SAME opportunity, not two:")
    for name, members in by_catch.items():
        marker = "  <- substitutes" if len(members) > 1 else ""
        print(f"    {name:34s} {', '.join(members)}{marker}")

    print()
    print("Community-need variant (D3 cross-check, NEVER the default ranking):")
    cn = out.loc[out["eligible"]].nsmallest(TOP_N, "community_need_rank")
    print(cn[["community_need_rank", "suburb", "community_need_score",
              "median_household_income_weekly", "rank"]].to_string(index=False))

    report_criterion_influence(frame, base)
    report_sensitivity(long, stability, core)

    print()
    print("=" * 78)
    print("WHAT HAPPENED TO THE v1 SHORTLIST")
    print("=" * 78)
    v1 = ["Croydon", "Greystanes", "Beverly Hills", "Beacon Hill", "Belmore"]
    v1_cols = ["suburb", "rank", "site_score", "access_gap", "demand_scale",
               "mixed_billing_fit", "operational", "eligible", "exclusion_reason",
               "practices_within_2km", "access_general_pct"]
    print(out.loc[out["suburb"].isin(v1), v1_cols].to_string(index=False))

    print()
    n_elig = int(out["eligible"].sum())
    print(f"eligible for ranking: {n_elig} of 680 "
          f"({int(out['below_population_floor'].sum())} below the population floor, "
          f"{int(out['below_catchment_floor'].sum())} below the catchment floor, "
          f"{int(out['excluded_nuvo_catchment'].sum())} in a Nuvo catchment)")

    if args.no_write:
        return

    EDA_DIR.mkdir(exist_ok=True)
    out.to_csv(SCORES_OUT, index=False)
    long.drop(columns=[]).to_csv(SENSITIVITY_OUT, index=False)

    # Two suburbs in one GP catchment are one opportunity: a clinic in either draws on the
    # same practices and population. Cronulla and Caringbah South are both Cronulla -
    # Miranda - Caringbah. Duplicates are marked rather than dropped so they stay visible.
    shortlist = out.loc[out["robust_core_tier"] != ""].copy().sort_values("rank")
    leader = shortlist.groupby("gpc_code")["suburb"].transform("first")
    shortlist["shortlist_status"] = np.where(
        shortlist["suburb"] == leader, "recommended", "same catchment as " + leader
    )
    distinct_sites = int((shortlist["shortlist_status"] == "recommended").sum())

    # Advisory only. validation.py finds connected high-high LISA clusters, a second
    # answer to "are these one opportunity". Not used to suppress a row: Avalon Beach and
    # Collaroy fall in separate clusters at k <= 12 and the same one at k >= 16, and k has
    # no principled value. Catchments at least come from Medicare patient flows.
    shortlist["spatial_cluster"] = ""
    shortlist["spatial_note"] = ""
    if LISA_CSV.exists():
        lisa = pd.read_csv(LISA_CSV)
        if "spatial_cluster" in lisa.columns:
            lookup = dict(zip(lisa["suburb"], lisa["spatial_cluster"].fillna("")))
            shortlist["spatial_cluster"] = [
                lookup.get(s, "") for s in shortlist["suburb"]
            ]
            first_in_cluster = (
                shortlist.loc[shortlist["spatial_cluster"] != ""]
                .groupby("spatial_cluster")["suburb"].first().to_dict()
            )
            shortlist["spatial_note"] = [
                ""
                if not c or first_in_cluster.get(c) == s
                else f"LISA k=8 puts this in a cluster with {first_in_cluster[c]}; "
                     f"unstable in k, treat as advisory"
                for s, c in zip(shortlist["suburb"], shortlist["spatial_cluster"])
            ]
    shortlist.to_csv(SHORTLIST_OUT, index=False)

    print()
    print(f"wrote {SCORES_OUT} ({len(out)} rows, {len(out.columns)} cols)")
    print(f"wrote {SENSITIVITY_OUT} ({len(long)} rows)")
    print(f"wrote {SHORTLIST_OUT} ({len(shortlist)} rows, {distinct_sites} distinct "
          f"catchments) - robust core, "
          f"{int((shortlist['robust_core_tier'] == 'core_all_perturbations').sum())} "
          f"surviving every perturbation")
    suppressed = shortlist.loc[shortlist["shortlist_status"] != "recommended"]
    for row in suppressed.itertuples():
        print(f"  NOT a separate opportunity: {row.suburb} is {row.shortlist_status}")
    print(f"D4 NOTE stamped in every row: {NUVO_UNVERIFIED_NOTE}")


if __name__ == "__main__":
    main()
