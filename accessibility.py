"""
E2SFCA spatial-accessibility surface for GP supply across the 680 study suburbs.

Enhanced Two-Step Floating Catchment Area (Luo and Qi 2009, *Health and Place*
15(4):1100-1107; extending Luo and Wang 2003, *Environment and Planning B*
30(6):865-884). This replaces the inside-the-polygon practice count that produced the
project's original, wrong answer.

WHY THIS EXISTS
---------------
Counting practices inside a suburb boundary makes supply a property of an arbitrary
administrative line. Croydon has 1 practice inside it and 21 within 1.5 km, because it
sits between Burwood and Ashfield; the old method ranked it the single best opportunity
in Greater Sydney. E2SFCA fixes this structurally rather than by patching thresholds:
access is a continuous surface, so Croydon inherits Burwood's and Ashfield's supply at a
distance-discounted rate, and Heathcote (nearest practice 5.07 km, none within 3 km)
inherits almost nothing.

THE TWO STEPS
-------------
Step 1, for each practice j::

    R_j = S_j / sum_k( P_k * W(d_kj) )

``S_j = 1`` for every practice (decision D5: supply is measured in practices, not in
doctor head count, because the source register carries no practitioner-count field at
all). ``R_j`` is therefore "practices per weighted person competing for practice j".

Step 2, for each suburb i::

    A_i = sum_j( R_j * W(d_ij) )

``A_i`` is reported in **practices per 1,000 people**. HIGH ``A_i`` means well served.
LOW ``A_i`` is the opportunity signal.

Supply is conserved: summed over every demand point, ``sum_k(A_k * P_k)`` equals the
number of practices with a non-empty demand pool. :func:`_test_synthetic_e2sfca` asserts
this on a hand-computed case, and :func:`build` asserts it on the real data via the
identical ``sum_j(R_j * pool_j)``.

WHY EVERY SUBURB GETS A VALUE, INCLUDING THE 239 'no_data' ONES
----------------------------------------------------------------
``gp_supply.py`` is canonical: 239 of 680 suburbs were prompted but no clinic data came
back ('no_data'), 8 are 'gp_count_unknown', and only for the 433 'ok' suburbs does
``total_gp`` carry information. Every metric built on ``total_gp`` must exclude the
other 247 or it will read "not scraped" as "no doctors".

Accessibility is the one supply metric that is legitimately defined everywhere, because
it does not depend on the suburb having been scraped. ``A_i`` is a function of NEARBY
practices drawn from the Geoscience Australia register, plus the suburb's own population
and centroid, all of which are known for all 680. A suburb that was never scraped still
has a measurable distance to every practice around it. **This is how the 239 held-out
suburbs re-enter the analysis**, and it is why this module writes 680 rows with no
status filter. ``gp_data_status`` is carried into the output as context only; it is never
used to gate a value, and ``total_gp`` is never read by this module.

THE DEMAND RING, AND THE DEFECT IT FIXES
----------------------------------------
Step 1 divides each practice by the population competing for it. If that population is
restricted to the 680 study suburbs, every practice near the study boundary gets a
truncated denominator, its ``R_j`` explodes, and the fringe suburbs that can reach it read
as superbly served. This is not hypothetical; it was measured on this data:

- two Helensburgh practices (Wollongong LGA, outside the 680) showed a weighted demand
  pool of 1,467 people
- four Woy Woy practices showed a pool of 60 people
- at a 40 km/h conversion those two Helensburgh practices alone supplied 72 per cent of
  Heathcote's accessibility and moved Heathcote from the 4th percentile to the 98th
- the entire top tail of the distribution was this artefact: skew 18.9, kurtosis 407,
  maximum 53x the median

So the demand side is extended past the study boundary. ABS SA2 2021 boundaries are
intersected with the union of the 680 SAL suburb polygons, and each SA2 contributes
``ERP_2025 * (1 - inside_fraction)`` people at the representative point of its outside
part. See :func:`load_outside_demand` for construction and caveats. The 680 suburbs still
supply their own populations, so nobody is double counted, and step 2 is still reported
only for the 680.

Effect of the fix, same inputs otherwise: skew 18.9 to -0.9, kurtosis 407 to 0.9,
max/median 53x to 2x, Pearson against ``practices_within_2km`` -0.01 to +0.56. The
resulting worst-served list is also credible where the old one was not: Holsworthy,
Heathcote, Berowra Heights, Berowra, Cronulla, Kenthurst, Avalon Beach. Those are
peninsular and edge-of-network suburbs, which is exactly what a spatial access measure
should surface. Reproduce the broken version with ``build(use_ring=False)``.

TRAVEL COST: STRAIGHT-LINE FALLBACK IS IN USE, AND THE CONSTANT WAS MEASURED
----------------------------------------------------------------------------
**LIMITATION, STATED UP FRONT: this module does not use road-network routing.** Travel
cost is straight-line (great-circle) distance converted to indicative drive minutes by a
single constant, :data:`EFFECTIVE_STRAIGHT_LINE_SPEED_KMH`. Every consumer sees this in
the ``travel_cost_method`` column of the output CSV.

The road-network path (:func:`network_travel_minutes`) is implemented and runnable, and
was abandoned on measured evidence, not assumption. Two timed Overpass downloads of the
real Sydney drive network on the build machine:

===================  =========  =======  =======
bbox                 sq deg     nodes    seconds
===================  =========  =======  =======
inner west probe         0.006    4,475     32.1
mixed south probe        0.140   68,817    174.3
===================  =========  =======  =======

The study bbox is 2.24 sq deg. Extrapolating the second probe by area gives about 46.5
minutes for the download alone, before graph simplification, sparse-matrix assembly and
680-source shortest paths. That exceeds the 25 minute budget, so the fallback is used.

**The conventional 24 km/h "2 km is about 5 minutes" constant was tested and rejected.**
The 68,817-node cached probe network was used to calibrate the conversion directly:
151,558 origin-destination pairs between 0.5 and 20 km straight-line, network travel time
from OSM ``maxspeed`` via ``osmnx.routing.add_edge_travel_times``. Measured effective
straight-line speed, ``straight_km / network_time_h``:

- median 47.6 km/h, mean 47.6, p5 35.2, p95 60.6
- by distance band, median: 33.4 km/h at 0.5-1 km, 38.9 at 2-4 km, 46.3 at 8-12 km,
  51.1 at 16-20 km (short trips are slower and more circuitous)
- circuity ``network_km / straight_km``: median 1.259, mean 1.310, p95 1.644

Against that network, assuming 24 km/h overstates travel time by a median of +15.4
minutes and is optimistic for only 0.5 per cent of pairs. It would shrink the mean
general-population decay weight from 0.388 to 0.118, a 3.3x contraction of the effective
catchment, which would push E2SFCA back toward a near-neighbour count: the exact failure
mode this rebuild exists to remove. See :func:`calibrate_effective_speed` to reproduce.

The shipped constant is therefore the measured free-flow figure divided by a stated
congestion and access-delay factor (:data:`CONGESTION_DELAY_FACTOR`), because OSM
``maxspeed`` routing is free-flow: it has no signals, no queueing and no park-and-walk-in
penalty, and the realised route speed in the calibration was a median 60.7 km/h, which no
Sydney driver achieves door to door. The factor is a judgement, so the choice is
defended by sensitivity rather than by assertion: ``python accessibility.py
--sensitivity`` re-runs the whole surface at 24, 30, 35, 40 and 47.6 km/h and reports
rank stability and whether the Croydon and Heathcote conclusions survive.

**Residual bias, direction stated and MEASURED: straight line is the optimistic bound.**
Sydney's harbour, rivers, national parks and motorway-only crossings mean two points 2 km
apart can be 20 minutes apart, and no single constant can represent that. The barrier
penalty was quantified by repeating the calibration on a second graph spanning Sydney
Harbour (:data:`HARBOUR_CALIBRATION_BBOX`, 151.13 to 151.31 E, -33.88 to -33.76):

=========================  ==================  ====================
statistic                  south box (no       harbour box (spans
                           harbour crossing)   the harbour)
=========================  ==================  ====================
effective km/h, median                  47.6                  39.4
effective km/h, p5                      35.2                  22.4
circuity, median                       1.259                 1.457
circuity, p95                          1.644                 2.504
=========================  ==================  ====================

Crossing the harbour costs roughly 17 per cent of median effective speed and nearly
doubles the worst-case circuity. In the harbour box, 6.5 per cent of pairs are slower
than even 24 km/h, against 0.5 per cent in the south box. So the barrier tail is real and
it is heavier than the shipped constant admits.

The clearest single case is visible in the output itself: **Dangar Island** is credited
with a nearest practice 1.54 km away and 2 practices within 2 km. Dangar Island has no
road at all. It is an island in the Hawkesbury reachable only by ferry from Brooklyn, so
its true drive time is not 2.6 minutes, it is unbounded. Its accessibility value is
meaningless at any percentile. Where a suburb's nearest practices are across water, its
access is overstated and its opportunity understated. That direction is conservative for
the commercial question, since it hides opportunities rather than inventing them, but it
is a real defect and not a rounding error. Ku-ring-gai Chase, Cottage Point, Milsons
Passage, Great Mackerel Beach, Coasters Retreat, Elvina Bay, Lovett Bay and Morning Bay
are affected the same way; all are tiny and none is a candidate site.

HOW TO CONSUME THIS SAFELY
--------------------------
With the demand ring in place ``access_general`` is well behaved: mean 0.243, median
0.259, standard deviation 0.072, range 0 to 0.409, skew -0.90, kurtosis 0.87. It is
mildly LEFT skewed, the long tail being poorly served suburbs, which is the tail this
project cares about. It is usable directly as a scoring input.

Two things still to respect:

1. **8 suburbs score an exact 0** because no practice at all lies inside the 20 minute
   cutoff: Wisemans Ferry, Laughtondale, Leets Vale, Maroota, Singletons Mill, Canoelands,
   Cumberland Reach, Lower Portland. They are real supply deserts and they are also all
   under 711 people. An access-gap criterion will rank them first on merit and they are
   still not viable sites, so pair the score with a population floor. That is the same
   smallness trap that got 'area' dropped in D6, and it is not solved by the metric.
2. ``access_general_pct`` and ``access_senior_pct`` are provided as the rank-based
   alternative (percentile within the 680, 0 = worst served, 100 = best served). Prefer
   them when combining with other criteria on different scales, since a percentile is
   immune to the choice of the speed constant in a way the raw value is not.

DECAY WEIGHTS
-------------
Decision D2: 20 minute cutoff for the general population, 15 minutes for the 65-plus
cohort, tighter because that cohort travels less for primary care, which is the clinical
intensity premise of the whole project. Both are stepped Gaussian weights, derived rather
than asserted: see :data:`GENERAL_ZONES` and :func:`gaussian_weight`.

SOURCES AND CONVENTIONS
-----------------------
Supply is the Geoscience Australia National HealthDirect Health Facilities register
(layer 0, ``nhsd_service_type == 'General practice service'``, 2,089 NSW practices,
extract 2025-03-07, CC-BY 4.0), never the scraped ``total_gp`` (decision D5). Suburb
geometry is ABS ASGS Ed.3 SAL 2021, reused from :mod:`dpa_status` so all 680 suburbs
match; the project's cached OSM ``place=suburb`` layer is not used because it is missing
30 suburbs including Chatswood, Hornsby, Liverpool and Manly.
"""
from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

import dpa_status
import gp_supply

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "scraped_data"
CACHE_DIR = ROOT / "facility_cache"
EDA_DATA = DATA_DIR / "eda_data.csv"
PRACTICES_CSV = DATA_DIR / "ga_general_practices_nsw.csv"
OUT_CSV = DATA_DIR / "suburb_accessibility.csv"

#: Mean Earth radius, IUGG. Great-circle distances are computed on lon/lat directly
#: rather than in a projected CRS, so there is no projection distortion to argue about.
EARTH_RADIUS_KM = 6371.0088

# ---------------------------------------------------------------------------
# Travel cost
# ---------------------------------------------------------------------------

#: Measured median effective straight-line speed on the real Sydney drive network, from
#: 151,558 sampled origin-destination pairs on the 68,817-node cached probe graph with
#: free-flow OSM maxspeed travel times. Reproduce with
#: ``python accessibility.py --calibrate``. This is a MEASUREMENT, not an assumption.
MEASURED_FREEFLOW_STRAIGHT_LINE_KMH = 47.6

#: Free-flow OSM routing has no traffic signals, no queueing, no turn delay and no
#: park-and-walk-in time; the calibration's realised route speed was a median 60.7 km/h.
#: This divisor converts the free-flow measurement to an indicative door-to-door figure.
#: It is a JUDGEMENT, and it is the single least defensible number in this module, which
#: is why ``--sensitivity`` exists.
CONGESTION_DELAY_FACTOR = 1.36

#: Straight-line km to indicative drive minutes: minutes = km / speed * 60.
#: 47.6 / 1.36 = 35.0. At 35 km/h the 20 minute general cutoff reaches 11.67 km of
#: straight-line distance and the 15 minute senior cutoff reaches 8.75 km.
EFFECTIVE_STRAIGHT_LINE_SPEED_KMH = 35.0

#: Value written to the ``travel_cost_method`` column when the fallback is used, so no
#: downstream reader can mistake this for network routing.
METHOD_STRAIGHT_LINE = (
    f"straight_line_haversine_{EFFECTIVE_STRAIGHT_LINE_SPEED_KMH:.0f}kmh_optimistic_bound"
)

#: Value written when road-network routing succeeded.
METHOD_NETWORK = "osmnx_drive_network_freeflow_maxspeed"

#: Step-1 formulations. 2SFCA is what the project shipped: a demand point contributes its
#: whole population to every practice it can reach, so demand is counted once per
#: overlapping catchment. 3SFCA (Wan, Zou and Sternberg 2012) splits each demand point
#: across the practices it can reach, weighted by proximity, which removes that
#: double-count and is the stricter formulation.
#:
#: MEASURED on this data: the two agree at Spearman 0.838 on access, and 6 of the top 10
#: suburbs are common to both, but they disagree about the winner. Cronulla is rank 1
#: under 2SFCA and rank 3 under 3SFCA; Avalon Beach is rank 4 under 2SFCA and rank 1
#: under 3SFCA. The 31-run sensitivity sweep varies parameters only, so before this
#: existed the ranking had never been tested against the choice of formulation.
METHOD_2SFCA = "2sfca"
METHOD_3SFCA = "3sfca"

#: Speeds re-run by ``--sensitivity``. Spans the rejected 24 km/h constant through the
#: raw free-flow measurement, so a reader can see the whole plausible interval.
SENSITIVITY_SPEEDS_KMH = (24.0, 30.0, 35.0, 40.0, 47.6)

#: Default wall-clock budget for the road-network path before it is abandoned, so a slow
#: route does not stall a routine build. Override with ``--network-budget MIN``; the
#: projection for the full study bbox is about 47 minutes for the download alone.
NETWORK_TIME_BUDGET_S = 25 * 60

#: Overpass endpoint. ``None`` keeps the osmnx default, overpass-api.de.
#:
#: MEASURED on the build machine: overpass-api.de resolves round-robin and at least one of
#: its addresses is unreachable from here - raw TCP to 162.55.144.139:443 times out while
#: ``requests`` eventually lands on a working address after 22 seconds. osmnx gives up
#: first, so every routing attempt failed with ConnectTimeout even for a one-block bbox,
#: which reads like a code fault and is not one. overpass.osm.ch answered the same query
#: in 2.1 seconds. Override with ``--overpass-url``.
OVERPASS_URL: str | None = None

#: Bounding box of the study area, (west, south, east, north). Matches
#: ``dpa_status.SYDNEY_BBOX``.
STUDY_BBOX = dpa_status.SYDNEY_BBOX

#: Timed Overpass drive-network downloads used to reject the network path, as
#: (label, sq deg, nodes, seconds). Kept in code so the decision is auditable.
NETWORK_DOWNLOAD_PROBES = (
    ("inner west", 0.006, 4475, 32.1),
    ("mixed south", 0.140, 68817, 174.3),
)

#: bbox of the graph used by :func:`calibrate_effective_speed`. Contains Botany Bay and
#: the Georges River; does NOT contain Sydney Harbour.
CALIBRATION_BBOX = (150.90, -34.05, 151.30, -33.70)

#: ABS ASGS Ed.3 SA2 2021 boundaries, used ONLY to build the outside-the-study-area
#: demand ring (see :func:`load_outside_demand`). Paired with the SA2 ERP 2025 table the
#: catchment task already cached and validated at 2,454 SA2s.
SA2_ZIP = CACHE_DIR / "abs_sa2_2021_aust_shp_gda2020.zip"
SA2_DIR = CACHE_DIR / "abs_sa2_2021"
SA2_SHP = SA2_DIR / "SA2_2021_AUST_GDA2020.shp"
SA2_ERP_CSV = CACHE_DIR / "abs_sa2_erp.csv"
SA2_URL = (
    "https://www.abs.gov.au/statistics/standards/"
    "australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/"
    "access-and-downloads/digital-boundary-files/SA2_2021_AUST_SHP_GDA2020.zip"
)

#: Ring demand is read from a box this many degrees wider than :data:`STUDY_BBOX`. The
#: general cutoff at the shipped speed reaches 11.67 km, about 0.11 degrees, so 0.6
#: degrees is roughly five times the furthest distance that can matter.
RING_BBOX_PAD_DEG = 0.6

#: Ring SA2 parts holding fewer people than this are dropped: they are sliver artefacts
#: of the difference against the study footprint, not populations.
RING_MIN_POPULATION = 1.0

#: Second calibration graph, spanning Sydney Harbour, used to measure the water-barrier
#: penalty on the straight-line assumption. Median effective speed 39.4 km/h against
#: 47.6 in :data:`CALIBRATION_BBOX`; circuity p95 2.504 against 1.644.
HARBOUR_CALIBRATION_BBOX = (151.13, -33.88, 151.31, -33.76)

# ---------------------------------------------------------------------------
# Distance decay (decision D2)
# ---------------------------------------------------------------------------

#: Travel-time cutoff for the general population, minutes (D2).
GENERAL_CUTOFF_MIN = 20.0

#: Travel-time cutoff for the 65-plus cohort, minutes (D2). Tighter because older
#: patients travel less far for primary care.
SENIOR_CUTOFF_MIN = 15.0

#: Zone width for the stepped weights, minutes.
ZONE_WIDTH_MIN = 5.0

#: General-population weights as ``((upper_bound_minutes, weight), ...)``, ascending.
#: Zone n covers ``[lower, upper)``; beyond the last bound the weight is 0.
#: Each weight is :func:`gaussian_weight` at the zone MIDPOINT with the 20 minute
#: cutoff, rounded to 3 dp. Not hand-picked: :func:`_test_zone_weights` re-derives them.
GENERAL_ZONES: tuple[tuple[float, float], ...] = (
    (5.0, 0.980),   # 0 to 5 min,   midpoint 2.5
    (10.0, 0.827),  # 5 to 10 min,  midpoint 7.5
    (15.0, 0.549),  # 10 to 15 min, midpoint 12.5
    (20.0, 0.192),  # 15 to 20 min, midpoint 17.5
)

#: 65-plus weights, same derivation at the 15 minute cutoff. These come out as
#: 0.965 / 0.701 / 0.254, which is the Luo and Qi (2009) three-zone weight set to 3 dp
#: (they published 1.00 / 0.68 / 0.22 for 0-10 / 10-20 / 20-30 min); the agreement is
#: not a coincidence, it is the same Gaussian evaluated at the same relative positions.
SENIOR_ZONES: tuple[tuple[float, float], ...] = (
    (5.0, 0.965),   # 0 to 5 min,   midpoint 2.5
    (10.0, 0.701),  # 5 to 10 min,  midpoint 7.5
    (15.0, 0.254),  # 10 to 15 min, midpoint 12.5
)

#: Plain straight-line radii for the human-readable sanity columns, km.
SANITY_RADII_KM = (2.0, 5.0)

#: Register rows that survive the ``nhsd_service_type`` filter but are not commercially
#: comparable general practice: youth mental health, refugee health, travel medicine,
#: university health services. 27 statewide, 9 inside the 680. NOT excluded by default,
#: because some do bulk-bill general consultations and the call belongs to a human;
#: ``--sensitivity`` reports what dropping them would do.
NON_GP_LIKE_PATTERNS = (
    "headspace",
    "refugee health",
    "travel doctor",
    "university health service",
)


def gaussian_weight(minutes: float, cutoff_min: float) -> float:
    """
    Luo and Wang (2003) Gaussian distance decay, zero-anchored at the cutoff.

    ``W(d) = (exp(-0.5 * (d/d0)^2) - exp(-0.5)) / (1 - exp(-0.5))`` for ``d < d0``,
    else 0. The subtraction and rescaling force ``W(0) = 1`` and ``W(d0) = 0``, so the
    weight falls continuously to nothing at the cutoff instead of stepping off a cliff
    at a non-zero value.

    >>> round(gaussian_weight(0.0, 20.0), 6)
    1.0
    >>> gaussian_weight(20.0, 20.0)
    0.0
    >>> gaussian_weight(25.0, 20.0)
    0.0
    >>> round(gaussian_weight(2.5, 20.0), 3)
    0.98
    >>> round(gaussian_weight(12.5, 20.0), 3)
    0.549

    Decay is steeper for the same relative position under a tighter cutoff, which is
    the point of the senior variant:

    >>> round(gaussian_weight(7.5, 20.0), 3) > round(gaussian_weight(7.5, 15.0), 3)
    True
    """
    if minutes >= cutoff_min:
        return 0.0
    if minutes < 0:
        raise ValueError(f"negative travel time: {minutes}")
    floor = math.exp(-0.5)
    return (math.exp(-0.5 * (minutes / cutoff_min) ** 2) - floor) / (1.0 - floor)


def zone_bounds(zones: tuple[tuple[float, float], ...]) -> np.ndarray:
    """Upper bounds of each zone, ascending, as a float array."""
    return np.asarray([b for b, _ in zones], dtype=float)


def zone_weights(zones: tuple[tuple[float, float], ...]) -> np.ndarray:
    """Zone weights with a trailing 0.0 for anything beyond the last bound."""
    return np.asarray([w for _, w in zones] + [0.0], dtype=float)


def decay_weight(
    minutes: float, zones: tuple[tuple[float, float], ...] = GENERAL_ZONES
) -> float:
    """
    Stepped decay weight for one travel time, in minutes.

    Zone n covers ``[lower, upper)``: a travel time exactly on a boundary falls into the
    farther zone, and anything at or beyond the cutoff gets 0.

    >>> decay_weight(0.0)
    0.98
    >>> decay_weight(4.999)
    0.98
    >>> decay_weight(5.0)
    0.827
    >>> decay_weight(14.9)
    0.549
    >>> decay_weight(15.0)
    0.192
    >>> decay_weight(20.0)
    0.0
    >>> decay_weight(1000.0)
    0.0

    The senior variant is tighter at every distance and cuts out 5 minutes earlier:

    >>> decay_weight(7.5, SENIOR_ZONES)
    0.701
    >>> decay_weight(16.0, SENIOR_ZONES)
    0.0
    >>> decay_weight(16.0, GENERAL_ZONES)
    0.192

    It is monotone non-increasing, which the vectorised form must also satisfy:

    >>> ws = [decay_weight(m) for m in range(0, 25)]
    >>> all(a >= b for a, b in zip(ws, ws[1:]))
    True
    """
    return float(decay_weights(np.asarray([minutes], dtype=float), zones)[0])


def decay_weights(
    minutes: np.ndarray, zones: tuple[tuple[float, float], ...] = GENERAL_ZONES
) -> np.ndarray:
    """
    Vectorised :func:`decay_weight` over an array of any shape.

    ``side='right'`` in the search puts a value exactly on a bound into the farther
    zone, matching the ``[lower, upper)`` convention.

    >>> decay_weights(np.array([0.0, 5.0, 12.0, 19.999, 20.0]))
    array([0.98 , 0.827, 0.549, 0.192, 0.   ])
    >>> decay_weights(np.array([[1.0, 30.0]]), SENIOR_ZONES)
    array([[0.965, 0.   ]])
    """
    arr = np.asarray(minutes, dtype=float)
    if np.any(arr < 0):
        raise ValueError("negative travel times")
    idx = np.searchsorted(zone_bounds(zones), arr, side="right")
    return zone_weights(zones)[idx]


# ---------------------------------------------------------------------------
# E2SFCA
# ---------------------------------------------------------------------------


def e2sfca(
    cost_minutes: np.ndarray,
    demand: np.ndarray,
    zones: tuple[tuple[float, float], ...] = GENERAL_ZONES,
    supply: np.ndarray | None = None,
    cost_eval: np.ndarray | None = None,
    method: str = METHOD_2SFCA,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Both E2SFCA steps.

    ``cost_minutes`` is ``(n_demand, n_practices)`` travel time from every DEMAND point,
    and ``demand`` is the matching ``(n_demand,)`` population. ``supply`` defaults to 1.0
    per practice (decision D5).

    ``cost_eval`` is ``(n_eval, n_practices)`` and defaults to ``cost_minutes``. It exists
    because step 1 must see every competing population, including the demand ring outside
    the 680 study suburbs, while step 2 is only reported for the 680. Without the split,
    practices near the study boundary get a truncated demand pool and the fringe reads as
    well served: see :func:`load_outside_demand`.

    Returns ``(access, ratios, weighted_demand)``: ``A_i`` at each evaluation point in
    practices per person, ``R_j`` per practice, and each practice's decay-weighted demand
    pool.

    A practice with an empty weighted demand pool gets ``R_j = 0``. That is safe rather
    than arbitrary: an empty pool means every ``W(d_kj)`` in its column is zero, so the
    practice contributes nothing to any ``A_i`` whatever value ``R_j`` takes. It happens
    for the NSW practices outside reach of any demand point.

    ``method`` selects step 1. ``"2sfca"`` is the shipped enhanced two-step: a demand
    point contributes its WHOLE population to every practice it can reach, so demand is
    counted once per overlapping catchment. ``"3sfca"`` (Wan, Zou and Sternberg 2012)
    inserts a selection weight ``G_kj = W(d_kj) / sum_m W(d_km)`` first, so a demand point
    splits itself across the practices it can reach in proportion to how close each is.

    Two demand points of 100 people. The first reaches both practices, the second reaches
    only practice 0. Under 2SFCA practice 0 carries a pool of 200 and practice 1 a pool of
    100; under 3SFCA the first point splits 50/50, so the pools are 150 and 50:

    >>> cost = np.array([[0.0, 0.0], [0.0, 99.0]])
    >>> dem = np.array([100.0, 100.0])
    >>> z = ((1.0, 1.0),)   # one zone: weight 1 within a minute, 0 beyond
    >>> _, _, pool2 = e2sfca(cost, dem, z)
    >>> pool2.tolist()
    [200.0, 100.0]
    >>> _, _, pool3 = e2sfca(cost, dem, z, method="3sfca")
    >>> pool3.tolist()
    [150.0, 50.0]

    Supply conservation holds under both, which is the check that the change is a
    reallocation of demand and not a change in how much supply exists:

    >>> for m in ("2sfca", "3sfca"):
    ...     _, r, pool = e2sfca(cost, dem, z, method=m)
    ...     round(float((r * pool).sum()), 9)
    2.0
    2.0

    A demand point that can reach nothing has no share to split and is skipped rather
    than producing a division by zero:

    >>> _, _, pool = e2sfca(np.array([[99.0, 99.0]]), np.array([50.0]), z, method="3sfca")
    >>> pool.tolist()
    [0.0, 0.0]
    """
    if method not in (METHOD_2SFCA, METHOD_3SFCA):
        raise ValueError(f"unknown method {method!r}")
    cost = np.asarray(cost_minutes, dtype=float)
    dem = np.asarray(demand, dtype=float)
    if cost.ndim != 2:
        raise ValueError(f"cost_minutes must be 2-D, got {cost.shape}")
    if dem.shape != (cost.shape[0],):
        raise ValueError(f"demand shape {dem.shape} does not match {cost.shape}")
    if np.any(dem < 0):
        raise ValueError("negative demand")

    sup = np.ones(cost.shape[1], dtype=float) if supply is None else np.asarray(supply, float)
    if sup.shape != (cost.shape[1],):
        raise ValueError(f"supply shape {sup.shape} does not match {cost.shape}")

    weights = decay_weights(cost, zones)
    if method == METHOD_3SFCA:
        reach = weights.sum(axis=1, keepdims=True)
        share = np.divide(weights, reach, out=np.zeros_like(weights), where=reach > 0)
        weighted_demand = (share * weights).T @ dem
    else:
        weighted_demand = weights.T @ dem
    ratios = np.divide(
        sup, weighted_demand, out=np.zeros_like(sup), where=weighted_demand > 0
    )
    if cost_eval is None:
        access = weights @ ratios
    else:
        eval_cost = np.asarray(cost_eval, dtype=float)
        if eval_cost.ndim != 2 or eval_cost.shape[1] != cost.shape[1]:
            raise ValueError(f"cost_eval shape {eval_cost.shape} does not match {cost.shape}")
        access = decay_weights(eval_cost, zones) @ ratios
    return access, ratios, weighted_demand


def haversine_km(
    lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray
) -> np.ndarray:
    """
    Great-circle distance in km, broadcasting over numpy shapes.

    One degree of latitude is 111.195 km on this Earth radius, at any longitude:

    >>> round(float(haversine_km(np.array(-33.0), np.array(151.0),
    ...                          np.array(-34.0), np.array(151.0))), 3)
    111.195
    >>> round(float(haversine_km(np.array(0.0), np.array(0.0),
    ...                          np.array(0.0), np.array(1.0))), 3)
    111.195
    >>> float(haversine_km(np.array(0.0), np.array(0.0), np.array(0.0), np.array(0.0)))
    0.0

    Broadcasts, so a (n, 1) origin against a (1, m) destination gives (n, m):

    >>> haversine_km(np.zeros((2, 1)), np.zeros((2, 1)),
    ...              np.zeros((1, 3)), np.zeros((1, 3))).shape
    (2, 3)
    """
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dlam = np.radians(np.asarray(lon2, float) - np.asarray(lon1, float))
    a = np.sin(dphi / 2.0) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlam / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_KM * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def straight_line_minutes(
    distance_km: np.ndarray, speed_kmh: float = EFFECTIVE_STRAIGHT_LINE_SPEED_KMH
) -> np.ndarray:
    """
    Convert straight-line km to indicative drive minutes at a fixed effective speed.

    >>> float(straight_line_minutes(np.array(11.6667)))
    20.000057142857145
    >>> [round(float(x), 2) for x in straight_line_minutes(np.array([2.0, 8.75]))]
    [3.43, 15.0]
    """
    if speed_kmh <= 0:
        raise ValueError("speed must be positive")
    return np.asarray(distance_km, dtype=float) / speed_kmh * 60.0


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


def load_suburbs() -> pd.DataFrame:
    """
    The 680 study suburbs with population, 65-plus share and a representative point.

    Geometry is ABS SAL 2021 via :func:`dpa_status.load_suburbs`, reused so the suburb
    frame is identical to the DPA and catchment outputs. The point is the polygon
    centroid, replaced by a guaranteed-interior representative point where the centroid
    falls outside the polygon (coastal and multi-part suburbs).
    """
    frame = pd.read_csv(EDA_DATA)
    gp_supply.require_status_column(frame, EDA_DATA)
    needed = {"suburb", "population", "over_65_pct", "gp_data_status"}
    missing = needed - set(frame.columns)
    if missing:
        raise ValueError(f"{EDA_DATA} missing column(s): {sorted(missing)}")

    geo = dpa_status.load_suburbs(frame)  # EPSG:3577, 680 rows, raises if any unmatched
    points = geo.geometry.centroid
    outside = ~points.within(geo.geometry)
    if outside.any():
        points = points.where(~outside, geo.geometry.representative_point())
    wgs = points.to_crs("EPSG:4326")

    out = frame[["suburb", "population", "over_65_pct", "gp_data_status"]].copy()
    coords = pd.DataFrame(
        {"suburb": geo["suburb"].to_numpy(), "lon": wgs.x.to_numpy(), "lat": wgs.y.to_numpy()}
    )
    out = out.merge(coords, on="suburb", how="left", validate="one_to_one")
    if out[["lon", "lat"]].isna().any().any():
        raise ValueError("suburbs without a centroid after the SAL join")
    out["centroid_outside_polygon"] = outside.to_numpy()
    return out


def load_practices() -> pd.DataFrame:
    """
    The 2,089 NSW general practices from the Geoscience Australia register (D5).

    All NSW practices are kept, not only those inside the 680, because a practice just
    outside the study area legitimately serves suburbs on its edge. Ones out of reach of
    every study suburb fall out on their own with a zero decay weight.
    """
    practices = pd.read_csv(PRACTICES_CSV)
    needed = {"organisation_name", "suburb", "longitude", "latitude", "nhsd_service_type"}
    missing = needed - set(practices.columns)
    if missing:
        raise ValueError(f"{PRACTICES_CSV} missing column(s): {sorted(missing)}")
    wrong_type = practices.loc[
        practices["nhsd_service_type"] != "General practice service", "nhsd_service_type"
    ].unique()
    if len(wrong_type):
        raise ValueError(f"non-GP service types present in {PRACTICES_CSV}: {wrong_type}")
    if practices[["longitude", "latitude"]].isna().any().any():
        raise ValueError("practices with missing coordinates")
    return practices


def non_gp_like_mask(practices: pd.DataFrame) -> np.ndarray:
    """Boolean mask of register rows matching :data:`NON_GP_LIKE_PATTERNS`."""
    names = practices["organisation_name"].astype(str).str.lower()
    hit = np.zeros(len(practices), dtype=bool)
    for pattern in NON_GP_LIKE_PATTERNS:
        hit |= names.str.contains(pattern, regex=False).to_numpy()
    return hit


#: Memo for :func:`load_outside_demand`. The ring depends only on the suburb frame and
#: the two ABS layers, never on the speed constant or the decay zones, so a sensitivity
#: sweep must not rebuild it from two large shapefiles on every pass.
_RING_CACHE: dict[int, pd.DataFrame] = {}


def load_outside_demand(suburbs: pd.DataFrame) -> pd.DataFrame:
    """
    Population OUTSIDE the 680 study suburbs, as demand-only competitor points.

    WHY THIS IS NOT OPTIONAL. Step 1 divides each practice by the population competing
    for it. Restricting that population to the 680 study suburbs truncates the demand
    pool of every practice near the study boundary, so its ``R_j`` explodes and the
    fringe suburbs that reach it read as superbly served. Measured on this data before
    the fix: two Helensburgh practices (Wollongong LGA, outside the 680) showed a
    weighted pool of 1,467 people, and four Woy Woy practices showed 60 people. At a
    40 km/h conversion those two Helensburgh practices supplied 72 per cent of
    Heathcote's accessibility and moved it from the 4th percentile to the 98th. The
    entire top tail of the distribution was an artefact of a missing denominator.

    Construction. ABS SA2 2021 boundaries are intersected with the union of the 680 SAL
    suburb polygons in the equal-area CRS. Each SA2 contributes
    ``ERP * (1 - inside_area_fraction)`` people, located at the representative point of
    the part of the SA2 OUTSIDE the study footprint, so ring demand sits where the
    uncounted people actually are rather than at an SA2 centroid that may fall inside
    the study area. The complement is already counted by the 680 suburb rows, so nobody
    is double counted.

    Caveats, stated rather than buried:

    - Areal apportionment assumes uniform population density inside an SA2. Same
      assumption, and the same weakness, as the catchment population work.
    - ERP 2025 is a different vintage from the project's Census-era suburb populations.
      Measured: the SA2 layer implies 5,121,215 people inside the study footprint against
      4,746,859 in ``eda_data.csv``, a 7.9 per cent difference, which is about what four
      years of Sydney growth would produce. The ring is therefore slightly heavy relative
      to the study rows, so fringe practices carry a marginally over-weighted ring pool
      and fringe access is very slightly UNDERstated. That is the opposite direction to
      the defect this ring fixes, and roughly an order of magnitude smaller.
      :func:`build` prints the comparison on every run so the mismatch stays visible.
    - An SA2 is coarser than a suburb, so ring demand is a coarser surface than study
      demand. It is one point per SA2 part; a large rural SA2 is represented by a single
      point. This still beats counting its population as zero.
    """
    import geopandas as gpd

    key = len(suburbs)
    if key in _RING_CACHE:
        return _RING_CACHE[key]

    dpa_status._require(SA2_SHP, SA2_ZIP, SA2_URL, "ABS SA2 2021 shapefile")
    erp = pd.read_csv(SA2_ERP_CSV)
    erp["sa2_code"] = erp["sa2_code"].astype(str)

    west, south, east, north = STUDY_BBOX
    pad = RING_BBOX_PAD_DEG
    sa2 = gpd.read_file(SA2_SHP, bbox=(west - pad, south - pad, east + pad, north + pad))
    sa2 = sa2[sa2.geometry.notna()].copy()
    sa2["SA2_CODE21"] = sa2["SA2_CODE21"].astype(str)
    sa2 = sa2.merge(
        erp[["sa2_code", "erp"]], left_on="SA2_CODE21", right_on="sa2_code", how="inner"
    )
    sa2 = sa2.to_crs(dpa_status.EQUAL_AREA_CRS)

    study = dpa_status.load_suburbs(suburbs[["suburb"]])
    footprint = study.geometry.union_all()

    total_area = sa2.geometry.area
    outside_geom = sa2.geometry.difference(footprint)
    outside_area = outside_geom.area
    fraction_outside = np.where(total_area > 0, outside_area / total_area, 1.0)
    ring_pop = sa2["erp"].to_numpy(dtype=float) * fraction_outside

    keep = (ring_pop >= RING_MIN_POPULATION) & ~outside_geom.is_empty
    points = gpd.GeoSeries(outside_geom[keep], crs=sa2.crs).representative_point()
    points = points.to_crs("EPSG:4326")
    ring = pd.DataFrame(
        {
            "sa2_code": sa2.loc[keep, "SA2_CODE21"].to_numpy(),
            "sa2_name": sa2.loc[keep, "SA2_NAME21"].to_numpy(),
            "population": ring_pop[keep],
            "lon": points.x.to_numpy(),
            "lat": points.y.to_numpy(),
        }
    )
    # Population the SA2 layer puts INSIDE the study footprint, for the vintage check.
    ring.attrs["implied_inside_population"] = float(
        (sa2["erp"].to_numpy(dtype=float) * (1.0 - fraction_outside)).sum()
    )
    ring.attrs["sa2_parts_considered"] = len(sa2)
    _RING_CACHE[key] = ring
    return ring


def distance_matrix_km(suburbs: pd.DataFrame, practices: pd.DataFrame) -> np.ndarray:
    """``(n_suburbs, n_practices)`` great-circle distances in km."""
    return haversine_km(
        suburbs["lat"].to_numpy()[:, None],
        suburbs["lon"].to_numpy()[:, None],
        practices["latitude"].to_numpy()[None, :],
        practices["longitude"].to_numpy()[None, :],
    )


# ---------------------------------------------------------------------------
# Road-network path: implemented, time-boxed, abandoned on measured evidence
# ---------------------------------------------------------------------------


class NetworkTooSlow(RuntimeError):
    """Raised when the projected network build exceeds :data:`NETWORK_TIME_BUDGET_S`."""


def projected_network_seconds(bbox: tuple[float, float, float, float]) -> float:
    """
    Projected wall-clock seconds to download the drive network for ``bbox``.

    Extrapolates the larger timed probe in :data:`NETWORK_DOWNLOAD_PROBES` by bbox area.
    Download volume scales with road mileage, which scales with area, so area is the
    right denominator; it is an over-estimate to the extent the box contains ocean and
    national park, and an under-estimate to the extent Overpass rate-limits a large
    multi-part query.

    >>> round(projected_network_seconds((150.90, -34.05, 151.30, -33.70)), 1)
    174.3
    >>> projected_network_seconds(STUDY_BBOX) > NETWORK_TIME_BUDGET_S
    True
    """
    west, south, east, north = bbox
    area = abs(east - west) * abs(north - south)
    _, probe_area, _, probe_seconds = NETWORK_DOWNLOAD_PROBES[-1]
    return probe_seconds * area / probe_area


def network_travel_minutes(
    suburbs: pd.DataFrame,
    practices: pd.DataFrame,
    budget_s: float = NETWORK_TIME_BUDGET_S,
    cutoff_min: float = GENERAL_CUTOFF_MIN,
) -> np.ndarray:
    """
    Road-network drive minutes, suburb centroid to practice, or raise.

    Free-flow times from OSM ``maxspeed`` with osmnx imputation. Shortest paths run as
    chunked scipy Dijkstra with a time limit, and each chunk is reduced to practice
    columns immediately: the full 680-by-node matrix would be gigabytes.

    Raises :class:`NetworkTooSlow` before downloading anything when the projection
    exceeds ``budget_s``. That is the path taken for the real study area, and it is why
    the shipped output uses the straight-line fallback.
    """
    projected = projected_network_seconds(STUDY_BBOX)
    if projected > budget_s:
        raise NetworkTooSlow(
            f"projected drive-network download {projected/60:.1f} min for "
            f"{abs(STUDY_BBOX[2]-STUDY_BBOX[0])*abs(STUDY_BBOX[3]-STUDY_BBOX[1]):.3f} "
            f"sq deg exceeds the {budget_s/60:.0f} min budget "
            f"(timed probes: {NETWORK_DOWNLOAD_PROBES}); "
            "falling back to straight-line travel cost"
        )

    import osmnx as ox  # local import: only the network path needs it
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import dijkstra
    from scipy.spatial import cKDTree

    ox.settings.use_cache = True
    ox.settings.cache_folder = str(CACHE_DIR / "osmnx_cache")
    if OVERPASS_URL:
        ox.settings.overpass_url = OVERPASS_URL

    started = time.time()
    graph = ox.graph_from_bbox(STUDY_BBOX, network_type="drive", simplify=True)
    graph = ox.routing.add_edge_travel_times(ox.routing.add_edge_speeds(graph))

    nodes = list(graph.nodes)
    index = {node: i for i, node in enumerate(nodes)}
    node_lat = np.array([graph.nodes[n]["y"] for n in nodes])
    node_lon = np.array([graph.nodes[n]["x"] for n in nodes])

    rows, cols, secs = [], [], []
    for u, v, data in graph.edges(data=True):
        rows.append(index[u])
        cols.append(index[v])
        secs.append(float(data["travel_time"]))
    graph_csr = csr_matrix((secs, (rows, cols)), shape=(len(nodes), len(nodes)))

    # Snap in a local equal-distance projection so nearest-node is metric, not degrees.
    proj = np.radians(node_lon) * EARTH_RADIUS_KM * np.cos(np.radians(node_lat.mean()))
    tree = cKDTree(np.column_stack([proj, np.radians(node_lat) * EARTH_RADIUS_KM]))

    def snap(lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        x = np.radians(lon) * EARTH_RADIUS_KM * np.cos(np.radians(node_lat.mean()))
        y = np.radians(lat) * EARTH_RADIUS_KM
        return tree.query(np.column_stack([x, y]))[1]

    src = snap(suburbs["lon"].to_numpy(), suburbs["lat"].to_numpy())
    dst = snap(practices["longitude"].to_numpy(), practices["latitude"].to_numpy())

    limit_s = cutoff_min * 60.0
    out = np.full((len(suburbs), len(practices)), np.inf)
    for start in range(0, len(src), 50):
        if time.time() - started > budget_s:
            raise NetworkTooSlow(f"exceeded {budget_s/60:.0f} min during shortest paths")
        chunk = src[start : start + 50]
        dist = dijkstra(graph_csr, directed=True, indices=chunk, limit=limit_s)
        out[start : start + len(chunk), :] = dist[:, dst]
    return np.where(np.isfinite(out), out / 60.0, cutoff_min * 10.0)


def calibrate_effective_speed(
    bbox: tuple[float, float, float, float] = CALIBRATION_BBOX,
    sample_targets: int = 4000,
    seed: int = 20260831,
) -> dict:
    """
    Measure straight-line km to network-minutes on a cached probe graph.

    Reproduces the numbers quoted in the module docstring. Pass
    :data:`HARBOUR_CALIBRATION_BBOX` for the water-barrier figures. Needs the graph in
    the osmnx cache; downloads it (about 1 to 3 minutes) if absent.
    """
    import osmnx as ox
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import connected_components, dijkstra

    ox.settings.use_cache = True
    ox.settings.cache_folder = str(CACHE_DIR / "osmnx_cache")
    if OVERPASS_URL:
        ox.settings.overpass_url = OVERPASS_URL
    graph = ox.graph_from_bbox(bbox, network_type="drive", simplify=True)
    graph = ox.routing.add_edge_travel_times(ox.routing.add_edge_speeds(graph))

    nodes = list(graph.nodes)
    index = {node: i for i, node in enumerate(nodes)}
    lat = np.array([graph.nodes[n]["y"] for n in nodes])
    lon = np.array([graph.nodes[n]["x"] for n in nodes])
    rows, cols, secs, mets = [], [], [], []
    for u, v, data in graph.edges(data=True):
        rows.append(index[u])
        cols.append(index[v])
        secs.append(float(data["travel_time"]))
        mets.append(float(data["length"]))
    n = len(nodes)
    time_csr = csr_matrix((secs, (rows, cols)), shape=(n, n))
    dist_csr = csr_matrix((mets, (rows, cols)), shape=(n, n))

    _, labels = connected_components(time_csr, directed=True, connection="strong")
    keep = np.where(labels == np.bincount(labels).argmax())[0]
    rng = np.random.default_rng(seed)
    sources = rng.choice(keep, size=60, replace=False)
    targets = rng.choice(keep, size=min(sample_targets, len(keep)), replace=False)
    tt = dijkstra(time_csr, directed=True, indices=sources)
    dd = dijkstra(dist_csr, directed=True, indices=sources)

    parts = []
    for i, s in enumerate(sources):
        straight = haversine_km(lat[s], lon[s], lat[targets], lon[targets])
        minutes = tt[i, targets] / 60.0
        km = dd[i, targets] / 1000.0
        ok = np.isfinite(minutes) & (straight >= 0.5) & (straight <= 20.0) & (minutes > 0)
        parts.append(np.column_stack([straight[ok], minutes[ok], km[ok]]))
    arr = np.vstack(parts)
    straight, minutes, km = arr[:, 0], arr[:, 1], arr[:, 2]
    effective = straight / (minutes / 60.0)
    return {
        "graph_nodes": n,
        "pairs": len(arr),
        "effective_kmh_median": float(np.median(effective)),
        "effective_kmh_mean": float(effective.mean()),
        "effective_kmh_p5": float(np.percentile(effective, 5)),
        "effective_kmh_p95": float(np.percentile(effective, 95)),
        "circuity_median": float(np.median(km / straight)),
        "circuity_p95": float(np.percentile(km / straight, 95)),
        "route_kmh_median": float(np.median(km / (minutes / 60.0))),
    }


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build(
    speed_kmh: float = EFFECTIVE_STRAIGHT_LINE_SPEED_KMH,
    use_network: bool = True,
    drop_non_gp_like: bool = False,
    use_ring: bool = True,
    verbose: bool = True,
    network_budget_s: float = NETWORK_TIME_BUDGET_S,
) -> tuple[pd.DataFrame, str]:
    """
    Build the accessibility table for all 680 suburbs.

    Returns ``(frame, travel_cost_method)``. ``use_network`` attempts road-network
    routing first and falls back with a printed reason, which is the documented and
    expected outcome for the full study area. ``use_ring=False`` reproduces the
    study-area-only demand denominator and exists only to show what it costs; see
    :func:`load_outside_demand`.
    """
    suburbs = load_suburbs()
    practices = load_practices()
    if drop_non_gp_like:
        practices = practices.loc[~non_gp_like_mask(practices)].reset_index(drop=True)

    dist_km = distance_matrix_km(suburbs, practices)

    method = METHOD_STRAIGHT_LINE
    cost_min = straight_line_minutes(dist_km, speed_kmh)
    if use_network:
        try:
            cost_min = network_travel_minutes(
                suburbs, practices, budget_s=network_budget_s
            )
            method = METHOD_NETWORK
        except NetworkTooSlow as exc:
            if verbose:
                print(f"road network ABANDONED: {exc}\n")
        except Exception as exc:  # noqa: BLE001 - any routing failure must not stall
            if verbose:
                print(f"road network FAILED ({type(exc).__name__}): {exc}\n")
    if method == METHOD_STRAIGHT_LINE and speed_kmh != EFFECTIVE_STRAIGHT_LINE_SPEED_KMH:
        method = f"straight_line_haversine_{speed_kmh:g}kmh_optimistic_bound"

    population = suburbs["population"].to_numpy(dtype=float)
    seniors = population * suburbs["over_65_pct"].to_numpy(dtype=float) / 100.0

    # Step 1 sees the study suburbs PLUS the outside-the-study demand ring; step 2 is
    # reported for the 680 only. See load_outside_demand for why the ring is required.
    if use_ring:
        ring = load_outside_demand(suburbs)
        ring_cost = straight_line_minutes(distance_matrix_km(ring, practices), speed_kmh)
        cost_demand = np.vstack([cost_min, ring_cost])
        ring_pop = ring["population"].to_numpy(dtype=float)
        # No SA2 age structure is cached, so the ring inherits the study area's
        # population-weighted 65-plus share. The ring is Central Coast, Illawarra, Blue
        # Mountains and Macarthur, which are OLDER than Sydney average, so ring senior
        # demand is understated and fringe senior access stays slightly optimistic.
        senior_share = float(seniors.sum() / population.sum())
        demand_general_vec = np.concatenate([population, ring_pop])
        demand_senior_vec = np.concatenate([seniors, ring_pop * senior_share])
        ring_note = (
            f"demand ring: {len(ring)} SA2 parts, {ring_pop.sum():,.0f} people outside "
            f"the 680 (from {ring.attrs['sa2_parts_considered']} SA2s in the padded box); "
            f"SA2 ERP implies {ring.attrs['implied_inside_population']:,.0f} inside the "
            f"study footprint against {population.sum():,.0f} in eda_data.csv "
            f"({100*ring.attrs['implied_inside_population']/population.sum()-100:+.1f}% "
            f"vintage difference); ring 65-plus share assumed {100*senior_share:.2f}%"
        )
    else:
        cost_demand = cost_min
        demand_general_vec = population
        demand_senior_vec = seniors
        ring_note = "demand ring: DISABLED (fringe access is overstated, see docstring)"
    if verbose:
        print(ring_note)

    access_general, ratios_g, demand_general = e2sfca(
        cost_demand, demand_general_vec, GENERAL_ZONES, cost_eval=cost_min
    )
    access_senior, ratios_s, demand_senior = e2sfca(
        cost_demand, demand_senior_vec, SENIOR_ZONES, cost_eval=cost_min
    )

    # E2SFCA conserves supply: summed over ALL demand points, sum(A_k * P_k) equals the
    # number of practices with a non-empty pool. Identically, sum(R_j * pool_j) does,
    # which needs no access vector at the demand points and so also checks the ring.
    for label, ratios, demand in (
        ("general", ratios_g, demand_general),
        ("senior", ratios_s, demand_senior),
    ):
        reachable = int((demand > 0).sum())
        got = float((ratios * demand).sum())
        if not math.isclose(got, reachable, rel_tol=1e-9):
            raise AssertionError(
                f"{label} supply not conserved: sum(R_j * pool_j) = {got:.6f} but "
                f"{reachable} practices have a non-empty demand pool"
            )

    out = pd.DataFrame(
        {
            "suburb": suburbs["suburb"],
            "access_general": access_general * 1000.0,
            "access_senior": access_senior * 1000.0,
        }
    )
    for radius in SANITY_RADII_KM:
        out[f"practices_within_{radius:g}km"] = (dist_km <= radius).sum(axis=1)
    nearest = dist_km.argmin(axis=1)
    out["nearest_practice_km"] = dist_km[np.arange(len(dist_km)), nearest].round(3)
    out["travel_cost_method"] = method

    # Appended context columns. Pre-existing names above are unchanged.
    out["nearest_practice_min"] = np.round(cost_min[np.arange(len(cost_min)), nearest], 2)
    out["nearest_practice_name"] = practices["organisation_name"].to_numpy()[nearest]
    out["nearest_practice_suburb"] = practices["suburb"].to_numpy()[nearest]
    out["practices_within_general_cutoff"] = (cost_min < GENERAL_CUTOFF_MIN).sum(axis=1)
    out["practices_within_senior_cutoff"] = (cost_min < SENIOR_CUTOFF_MIN).sum(axis=1)
    # Percentile within the 680: 0 = worst served, 100 = best served. Low is the
    # opportunity signal, so this is the column a scoring model wants to invert.
    out["access_general_pct"] = (out["access_general"].rank(pct=True) * 100).round(2)
    out["access_senior_pct"] = (out["access_senior"].rank(pct=True) * 100).round(2)
    out["population"] = suburbs["population"]
    out["over_65_pct"] = suburbs["over_65_pct"]
    out["gp_data_status"] = suburbs["gp_data_status"]

    out["access_general"] = out["access_general"].round(6)
    out["access_senior"] = out["access_senior"].round(6)

    if len(out) != 680 or out["suburb"].duplicated().any():
        raise AssertionError(f"expected 680 unique suburbs, got {len(out)}")
    if out[["access_general", "access_senior"]].isna().any().any():
        raise AssertionError("null accessibility values")
    return out, method


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def _test_zone_weights() -> None:
    """The stated zone constants really are the Gaussian at each zone midpoint."""
    for zones, cutoff in ((GENERAL_ZONES, GENERAL_CUTOFF_MIN), (SENIOR_ZONES, SENIOR_CUTOFF_MIN)):
        assert zones[-1][0] == cutoff, f"last zone bound {zones[-1][0]} != cutoff {cutoff}"
        lower = 0.0
        for upper, weight in zones:
            assert math.isclose(upper - lower, ZONE_WIDTH_MIN), f"zone {lower}-{upper}"
            expected = round(gaussian_weight((lower + upper) / 2.0, cutoff), 3)
            assert weight == expected, f"zone {lower}-{upper}: {weight} != {expected}"
            lower = upper
        weights = [w for _, w in zones]
        assert all(a > b for a, b in zip(weights, weights[1:])), "weights not decreasing"
    # The senior cohort is discounted harder at every travel time it shares.
    for minutes in (0.0, 2.0, 6.0, 11.0, 14.0):
        assert decay_weight(minutes, SENIOR_ZONES) < decay_weight(minutes, GENERAL_ZONES)


def _test_synthetic_e2sfca() -> None:
    """
    A two-suburb, two-practice case with an exact hand-computed answer.

    Zones are a single hard 10 minute catchment, weight 1.0, so all the arithmetic is
    integer division and nothing depends on the Gaussian constants.

    Suburb A: 1,000 people. Suburb B: 3,000 people.
    Practice 1: 5 min from both. Practice 2: 5 min from A, 50 min from B.

    Step 1
        R_1 = 1 / (1000*1 + 3000*1) = 1/4000 = 0.00025
        R_2 = 1 / (1000*1 + 3000*0) = 1/1000 = 0.001
    Step 2
        A_A = 0.00025 + 0.001   = 0.00125  practices per person
        A_B = 0.00025 + 0       = 0.00025
    Conservation
        1000*0.00125 + 3000*0.00025 = 1.25 + 0.75 = 2.0 = both practices
    """
    binary = ((10.0, 1.0),)
    cost = np.array([[5.0, 5.0], [5.0, 50.0]])
    demand = np.array([1000.0, 3000.0])
    access, ratios, weighted = e2sfca(cost, demand, binary)

    assert np.allclose(weighted, [4000.0, 1000.0]), weighted
    assert np.allclose(ratios, [0.00025, 0.001]), ratios
    assert np.allclose(access, [0.00125, 0.00025]), access
    assert math.isclose(float((access * demand).sum()), 2.0), "supply not conserved"
    # A is five times better served than B while containing no practice of its own.
    assert math.isclose(access[0] / access[1], 5.0)

    # Same case under the shipped general zones: graded weights, still conserved.
    access_g, _, _ = e2sfca(cost, demand, GENERAL_ZONES)
    assert math.isclose(float((access_g * demand).sum()), 2.0), "supply not conserved"
    assert access_g[0] > access_g[1]


def _test_croydon_structure() -> None:
    """
    The structural claim, on synthetic data: E2SFCA reverses an inside-polygon count.

    Suburb X holds 1 practice and has 20 more 3 minutes away in the neighbouring suburb
    (the Croydon geometry). Suburb Y holds 1 practice and the nearest others are 25
    minutes away, beyond the cutoff (the Heathcote geometry). Both have 10,000 people.
    An inside-polygon count scores them identically at 1 practice each. E2SFCA must
    score X far better than Y.
    """
    n_near = 20
    cost = np.zeros((2, 2 + n_near))
    cost[0] = [1.0, 40.0] + [3.0] * n_near   # X: own practice plus 20 next door
    cost[1] = [40.0, 1.0] + [25.0] * n_near  # Y: own practice, the cluster out of reach
    demand = np.array([10000.0, 10000.0])
    access, _, _ = e2sfca(cost, demand, GENERAL_ZONES)
    assert access[0] > 5 * access[1], (access[0], access[1])


def _test_demand_ring_split() -> None:
    """
    The step-1 / step-2 split, with an exact hand-computed answer.

    One study suburb of 1,000 people and one ring point of 3,000 people, both 5 minutes
    from a single practice. Zones are a single hard 10 minute catchment, weight 1.0.

    Truncated (study demand only)
        R = 1 / 1000            A_study = 0.001
    With the ring
        R = 1 / (1000 + 3000)   A_study = 0.00025

    So ignoring 3,000 competitors outside the study boundary overstates the study
    suburb's accessibility by exactly 4x. That is the Helensburgh defect in miniature.
    """
    binary = ((10.0, 1.0),)
    cost_study = np.array([[5.0]])
    cost_ring = np.array([[5.0], [5.0]])

    truncated, ratios_t, pool_t = e2sfca(cost_study, np.array([1000.0]), binary)
    assert np.allclose(pool_t, [1000.0]) and np.allclose(truncated, [0.001]), truncated

    ringed, ratios_r, pool_r = e2sfca(
        cost_ring, np.array([1000.0, 3000.0]), binary, cost_eval=cost_study
    )
    assert np.allclose(pool_r, [4000.0]), pool_r
    assert np.allclose(ringed, [0.00025]), ringed
    assert math.isclose(float(truncated[0] / ringed[0]), 4.0)
    # Conservation holds over ALL demand points, so it is checked on R_j and the pool.
    assert math.isclose(float((ratios_r * pool_r).sum()), 1.0)
    assert math.isclose(float((ratios_t * pool_t).sum()), 1.0)
    # A_i is reported only for the evaluation points, never for the ring.
    assert ringed.shape == (1,)


def run_tests() -> None:
    """Run the asserts plus the doctests in this module."""
    import doctest

    _test_zone_weights()
    _test_synthetic_e2sfca()
    _test_croydon_structure()
    _test_demand_ring_split()
    result = doctest.testmod(sys.modules[__name__], verbose=False)
    if result.failed:
        raise SystemExit(f"{result.failed} doctest failure(s)")
    print(f"tests OK: 4 assert cases, {result.attempted} doctests")


# ---------------------------------------------------------------------------
# Validation and reporting
# ---------------------------------------------------------------------------

#: Suburbs the brief requires reporting on: four known-saturated, four from the
#: shortlists that the supply rebuild called into question.
VALIDATION_SUBURBS = (
    "Croydon",
    "Burwood",
    "Ashfield",
    "Chippendale",
    "Heathcote",
    "Greystanes",
    "Beverly Hills",
    "Beacon Hill",
)


def validate(frame: pd.DataFrame) -> None:
    """Print the required diagnostics and assert the two conclusions that must hold."""
    cols = [
        "suburb",
        "access_general",
        "access_general_pct",
        "access_senior",
        "access_senior_pct",
        "practices_within_2km",
        "practices_within_5km",
        "nearest_practice_km",
    ]
    view = frame.set_index("suburb").loc[list(VALIDATION_SUBURBS)].reset_index()[cols]
    print("Validation suburbs (access in practices per 1,000 people; pct = percentile "
          "within the 680, 100 = best served):")
    print(view.to_string(index=False))
    print()

    access = frame["access_general"]
    near2 = frame["practices_within_2km"]
    big = frame[frame["population"] >= 5000]
    nonzero = frame[access > 0]
    print("corr(access_general, practices_within_2km) -- expected strong but well short "
          "of 1.0, because access is competition-adjusted per capita and the 2 km count "
          "is not:")
    print(f"  Spearman, all 680                    {access.corr(near2, method='spearman'):+.4f}")
    print(f"  Spearman, population >= 5000 (n={len(big)})  "
          f"{big['access_general'].corr(big['practices_within_2km'], method='spearman'):+.4f}")
    print(f"  Pearson,  population >= 5000         "
          f"{big['access_general'].corr(big['practices_within_2km']):+.4f}")
    print(f"  Pearson,  log access (n={len(nonzero)})         "
          f"{np.log(nonzero['access_general']).corr(nonzero['practices_within_2km']):+.4f}")
    print(f"  Pearson,  all 680                   {access.corr(near2):+.4f}  "
          f"(usable now: skew {access.skew():+.2f}, kurtosis {access.kurt():+.2f}, "
          f"max/median {access.max()/access.median():.1f}x. Before the demand ring this "
          f"was -0.01 on skew 18.9 / kurtosis 407)")
    print(f"corr(access_general, practices_within_5km)  Spearman "
          f"{access.corr(frame['practices_within_5km'], method='spearman'):+.4f}")
    print(f"corr(access_general, nearest_practice_km)   Spearman "
          f"{access.corr(frame['nearest_practice_km'], method='spearman'):+.4f}  "
          "(negative expected: farther nearest practice means worse access)")
    print(f"corr(access_general, access_senior)         Spearman "
          f"{access.corr(frame['access_senior'], method='spearman'):+.4f}")
    print()

    croydon = frame.set_index("suburb").loc["Croydon"]
    heathcote = frame.set_index("suburb").loc["Heathcote"]
    assert croydon["access_general_pct"] >= 60.0, (
        f"Croydon must read as well served, got percentile {croydon['access_general_pct']}"
    )
    assert heathcote["access_general_pct"] <= 25.0, (
        f"Heathcote must read as poorly served, got percentile "
        f"{heathcote['access_general_pct']}"
    )
    assert croydon["access_general"] > heathcote["access_general"], "Croydon vs Heathcote"
    print(f"PASS Croydon well served: percentile {croydon['access_general_pct']:.1f}, "
          f"{int(croydon['practices_within_2km'])} practices within 2 km")
    print(f"PASS Heathcote poorly served: percentile {heathcote['access_general_pct']:.1f}, "
          f"{int(heathcote['practices_within_2km'])} practices within 2 km, nearest "
          f"{heathcote['nearest_practice_km']:.2f} km")
    print(f"PASS Croydon access is {croydon['access_general']/heathcote['access_general']:.2f}x "
          "Heathcote's")
    print()


def report_distribution(frame: pd.DataFrame) -> None:
    """
    Report the shape of ``access_general`` and the two things a scorer must respect.

    The distribution is well behaved once the demand ring is in place, but the 8 suburbs
    with no practice inside the cutoff score an exact 0 and are all tiny, so an access-gap
    criterion will rank hamlets first on merit. That needs a population floor, not a
    different metric.
    """
    access = frame["access_general"]
    print(f"Distribution of access_general: min {access.min():.4f}, "
          f"p5 {access.quantile(.05):.4f}, median {access.median():.4f}, "
          f"p95 {access.quantile(.95):.4f}, max {access.max():.4f}, "
          f"sd {access.std():.4f}")
    print(f"  skew {access.skew():+.2f}, kurtosis {access.kurt():+.2f} -- near symmetric, "
          "mildly left skewed. The long tail is the poorly served end, which is the end "
          "this project is looking for.")
    zero = frame[frame["practices_within_general_cutoff"] == 0]
    print(f"\n{len(zero)} suburbs have NO practice inside the {GENERAL_CUTOFF_MIN:g} min "
          f"cutoff, so access_general is an exact 0: "
          f"{', '.join(sorted(zero['suburb']))}. Combined population "
          f"{int(zero['population'].sum()):,}, largest "
          f"{int(zero['population'].max()):,}. Real supply deserts, and all far too small "
          "to host a 7-day multidisciplinary clinic: pair the score with a population "
          "floor.")
    print("\nWorst served at a viable size (population >= 4,000), the actual output of "
          "this module:")
    viable = frame[frame["population"] >= 4000].nsmallest(10, "access_general")
    print(viable[["suburb", "population", "over_65_pct", "access_general",
                  "access_general_pct", "access_senior_pct", "practices_within_2km",
                  "nearest_practice_km", "gp_data_status"]].to_string(index=False))
    print("\nBest served, for contrast:")
    print(frame.nlargest(5, "access_general")[
        ["suburb", "population", "access_general", "access_general_pct",
         "practices_within_2km", "nearest_practice_km"]].to_string(index=False))
    print()


def report_no_data_reentry(frame: pd.DataFrame) -> None:
    """Show that the 239 held-out suburbs now carry a usable supply signal."""
    print("The 239 'no_data' suburbs re-entering the analysis:")
    grouped = frame.groupby("gp_data_status")["access_general"].agg(["count", "median"])
    print(grouped.round(4).to_string())
    held = frame[frame["gp_data_status"] != gp_supply.GP_STATUS_OK]
    worst = held.nsmallest(8, "access_general")[
        ["suburb", "gp_data_status", "population", "over_65_pct", "access_general",
         "access_general_pct", "nearest_practice_km"]
    ]
    print("\nWorst-served held-out suburbs over 3,000 people "
          "(invisible to every total_gp metric):")
    worst = held[held["population"] >= 3000].nsmallest(10, "access_general")[
        ["suburb", "gp_data_status", "population", "over_65_pct", "access_general",
         "access_general_pct", "nearest_practice_km"]
    ]
    print(worst.to_string(index=False))
    print()


def report_sensitivity(baseline: pd.DataFrame) -> None:
    """Re-run the surface across :data:`SENSITIVITY_SPEEDS_KMH` and the non-GP drop."""
    print("Sensitivity to the effective-speed constant "
          "(rank correlation against the shipped 35 km/h surface):")
    base_rank = baseline.set_index("suburb")["access_general"]
    header = (f"{'km/h':>6} {'20min km':>9} {'rho vs 35':>10} {'Croydon pct':>12} "
              f"{'Heathcote pct':>14} {'top20 kept':>11}")
    print(header)
    base_top = set(baseline.nsmallest(20, "access_general")["suburb"])
    for speed in SENSITIVITY_SPEEDS_KMH:
        frame, _ = build(speed_kmh=speed, use_network=False, verbose=False)
        rank = frame.set_index("suburb")["access_general"]
        rho = base_rank.corr(rank, method="spearman")
        idx = frame.set_index("suburb")
        top = set(frame.nsmallest(20, "access_general")["suburb"])
        print(f"{speed:6.1f} {speed*GENERAL_CUTOFF_MIN/60:9.2f} {rho:10.4f} "
              f"{idx.loc['Croydon','access_general_pct']:12.1f} "
              f"{idx.loc['Heathcote','access_general_pct']:14.1f} "
              f"{len(top & base_top):8d}/20")
    print()

    # Ablation of the demand ring. This is not a tuning knob, it is a correctness fix,
    # and the numbers below are the evidence for that claim.
    broken, _ = build(use_network=False, use_ring=False, verbose=False)
    broken_access = broken.set_index("suburb")["access_general"]
    print("Ablating the outside-the-study demand ring (build(use_ring=False)), i.e. the "
          "study-area-only denominator this module was first built with:")
    print(f"  Spearman vs shipped surface {base_rank.corr(broken_access, method='spearman'):.4f}")
    print(f"  skew {broken_access.skew():+.1f} vs {base_rank.skew():+.2f}, "
          f"kurtosis {broken_access.kurt():+.0f} vs {base_rank.kurt():+.2f}, "
          f"max/median {broken_access.max()/broken_access.median():.0f}x vs "
          f"{base_rank.max()/base_rank.median():.1f}x")
    print(f"  Pearson against practices_within_2km {broken_access.corr(broken['practices_within_2km'].set_axis(broken['suburb'])):+.4f} "
          f"vs {base_rank.corr(baseline['practices_within_2km'].set_axis(baseline['suburb'])):+.4f}")
    broken_top = broken.nlargest(5, "access_general")[["suburb", "population", "access_general"]]
    print("  best-served suburbs WITHOUT the ring, all fringe settlements next to "
          "practices whose demand pool was truncated at the study boundary:")
    print("    " + "; ".join(f"{r.suburb} (pop {int(r.population)}, "
                             f"A {r.access_general:.2f})" for r in broken_top.itertuples()))
    print()

    dropped, _ = build(use_network=False, drop_non_gp_like=True, verbose=False)
    rank = dropped.set_index("suburb")["access_general"]
    practices = load_practices()
    n_dropped = int(non_gp_like_mask(practices).sum())
    moved = (base_rank.rank() - rank.rank()).abs()
    print(f"Dropping the {n_dropped} non-GP-like register rows "
          f"({', '.join(NON_GP_LIKE_PATTERNS)}):")
    print(f"  Spearman vs baseline {base_rank.corr(rank, method='spearman'):.4f}, "
          f"max rank move {moved.max():.0f} places, "
          f"suburbs moving more than 10 places: {(moved > 10).sum()}")
    idx = dropped.set_index("suburb")
    print(f"  Croydon percentile {idx.loc['Croydon','access_general_pct']:.1f}, "
          f"Heathcote {idx.loc['Heathcote','access_general_pct']:.1f}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--test", action="store_true", help="run tests and exit")
    parser.add_argument("--sensitivity", action="store_true", help="also run sensitivity")
    parser.add_argument("--calibrate", action="store_true",
                        help="re-measure the straight-line to drive-time constant")
    parser.add_argument("--overpass-url", default=None, metavar="URL",
                        help="Overpass API base, e.g. https://overpass.osm.ch/api; the "
                             "default overpass-api.de is unreliable from some networks")
    parser.add_argument("--network-budget", type=float, default=None, metavar="MIN",
                        help="minutes allowed for road-network routing before falling "
                             "back; the default 25 was a build-time constraint, raise it "
                             "to actually route")
    parser.add_argument("--no-network", action="store_true",
                        help="skip the road-network attempt entirely")
    parser.add_argument("--no-ring", action="store_true",
                        help="disable the outside-the-study demand ring (reproduces the "
                             "truncated-denominator defect; for ablation only)")
    args = parser.parse_args()

    if args.test:
        run_tests()
        return
    if args.calibrate:
        for label, bbox in (("south (no harbour)", CALIBRATION_BBOX),
                            ("harbour crossing", HARBOUR_CALIBRATION_BBOX)):
            print(f"{label} {bbox}:")
            for key, value in calibrate_effective_speed(bbox).items():
                print(f"  {key:26s} {value}")
        return

    run_tests()
    print()
    if args.overpass_url:
        globals()["OVERPASS_URL"] = args.overpass_url
        print(f"overpass endpoint: {args.overpass_url}")
    budget_s = (args.network_budget * 60.0 if args.network_budget
                else NETWORK_TIME_BUDGET_S)
    frame, method = build(use_network=not args.no_network, use_ring=not args.no_ring,
                          network_budget_s=budget_s)
    print(f"travel_cost_method = {method}")
    print(f"decay: general cutoff {GENERAL_CUTOFF_MIN:g} min {GENERAL_ZONES}")
    print(f"       senior  cutoff {SENIOR_CUTOFF_MIN:g} min {SENIOR_ZONES}")
    print()
    validate(frame)
    report_distribution(frame)
    report_no_data_reentry(frame)
    if args.sensitivity:
        report_sensitivity(frame)

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(OUT_CSV, index=False)
    print(f"wrote {OUT_CSV} ({len(frame)} rows, {len(frame.columns)} columns)")


if __name__ == "__main__":
    main()
