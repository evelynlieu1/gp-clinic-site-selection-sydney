# Methodology and design decisions

Companion to [CAVEATS.md](CAVEATS.md). That file records what the numbers *cannot* support; this
one records what we chose to measure, why, and what we rejected.

Last updated: 2026-08-19.

---

## 1. The question

Nuvo Health wants sites for a **mixed-billing, 7-day, multidisciplinary** GP clinic in Greater
Sydney. That product definition does more work than it first appears, and getting it wrong is the
root of most errors below:

- **Mixed billing** means the clinic bulk-bills concession holders and charges a gap to everyone
  else. It needs households who can absorb a gap fee *sometimes*. It is not a bulk-bill practice
  and it is not a premium private practice.
- **7 days** and **multidisciplinary** mean the product is *differentiated*. This matters because
  it breaks the assumption that a suburb with many GPs is a closed market.

The business question is therefore **not** "which suburb has the fewest doctors". It is:
**where can this particular product win?**

---

## 2. Three fundamental errors we found and corrected

### 2.1 Missing data read as zero supply

GP supply came from 70 prompts pasted into an LLM chat (`prompts/chunk_*.txt`). 239 of 680
suburbs were asked for and no clinic data came back. The pipeline recorded that as
`total_gp = 0`, and since pressure is `population / (total_gp + 1)`, those suburbs scored as
*maximum* pressure and dominated every shortlist. **Seven of the original ten recommendations
were suburbs with no data at all.**

Fixed by `gp_supply.py`, which separates three states: `ok` (433), `gp_count_unknown` (8),
`no_data` (239). Only `ok` is rankable on supply. See CAVEATS.md §1.

### 2.2 Supply counted inside suburb boundaries

This one invalidated the answer even after 2.1 was fixed.

**Croydon ranked #1 with one practice inside its polygon - and 21 general practices within
1.5 km, 27 within 2 km, 42 within 3 km. Nearest is 370 m.** It sits between Burwood and
Ashfield, in one of the most GP-saturated parts of Sydney. Nobody would open there.

The pattern is systematic, not a one-off:

| | Practices inside | Within 2 km |
|---|---|---|
| Croydon | 1 | 27 |
| Chippendale | 0 | 43 |

**45 suburbs of 5,000+ people show zero practices inside while being comfortably served.**
Meanwhile the method hid real candidates: **Heathcote** - 6,148 people, 22.8% aged 65+,
$2,240/week - has **zero practices within 3 km**, nearest 5.07 km, and never appeared on any
shortlist.

Patients do not stop at the suburb line. A suburb-interior count measures *which side of an
administrative boundary a clinic sits on*. Fixed by replacing it with an accessibility surface
(§4).

### 2.3 Income treated as monotonically better

`W_INCOME = 0.25` on higher-is-better meant the score could never stop climbing. Consequences,
measured:

| | Value |
|---|---|
| Cohort median household income | $2,171/wk |
| Top-20 median income | **$3,283/wk** (~P90) |
| Top 20 above $3,500 (P95) | 8 of 20 |

The top of the list was Longueville $4,894, Riverview $4,731, Castlecrag $4,675, North Balgowlah
$4,212. **Those households want full private billing, not mixed billing.** The score was
optimising for the segment least likely to buy the product, while the plausible target band
($1,800-3,000/wk) holds 270 of 423 suburbs and 2.69 million people.

Fixed by an inverted-U income curve (D1).

---

## 3. Decisions

The client gave no specification on any of these, so each is our call, stated with rationale so a
reader can disagree with the reasoning rather than guess at it.

### D1 - Income is an inverted-U, anchored to percentiles

Full score on a plateau of **$2,050-2,750/week** (Sydney P40-P80), tapering linearly to zero
below **$1,550** (P15) and above **$3,500** (P95).

*Rationale.* A standard consult rebate is roughly $43 against a typical private fee near $90, so
a gap of about $45. Below the lower knot, bulk-billing is effectively universal and gap fees do
not hold - which is why Belmore ($1,456) was correctly diverted to the community-need list.
Above the upper knot, households prefer premium private care and mixed billing reads downmarket.
Percentile anchoring means the band self-calibrates rather than hard-coding 2026 dollars.

*Risk.* The knots are a modelling choice, not a measurement. Mitigated by sensitivity analysis
(§5): if the recommendation only holds for one band setting, it is not a recommendation.

### D2 - Catchment is 20 minutes general, 15 minutes for 65+

*Rationale.* A 20-minute cutoff is the common standard in the primary-care access literature.
The tighter senior band encodes something the project's own thesis requires: seniors are 20-25% of
the scoring weight and the entire clinical-intensity argument, and that cohort does **not** drive
5 minutes to the next centre. Using one radius for everyone would overstate access for exactly
the patients the clinic is designed around.

### D3 - Primary lens is commercial (mixed billing)

*Rationale.* The client is a private operator whose stated model is mixed billing. Two scores
producing disjoint lists with no stated primary is not an analysis, it is two analyses. The
community-need variant (income rewarded lower-is-better) is retained and **reported** as an
equity cross-check, never as a competing recommendation.

### D4 - Existing Nuvo sites excluded at catchment level

*Rationale.* A clinic 1 km away in the adjacent suburb cannibalises just as effectively as one
across the road, so excluding by suburb name would not work - the same boundary error as 2.2.

*Known incomplete.* Brand-string matching finds 6 clinics; the practice register suggests roughly
**23 Sydney clinics** in the group trading under non-Nuvo brands. The exclusion list is an
editable config, and every output carries an explicit "unverified - confirm with client" flag.
**This is the single cheapest thing the client could supply to improve the analysis.**

### D5 - Supply is practices per capita, not GP headcount

*Rationale.* Three converging reasons:
1. The authoritative register has **no practitioner-count field** at all.
2. The scraped headcount is unreliable: 26% of usable counts are exactly 1 against a median of 3,
   and 17% of rankable suburbs are lower bounds rather than exact totals.
3. GP FTE does exist from the Department of Health, but only at **SA3 scale** - median catchment
   121,917 people and 56 km2 against a median suburb of 2.7 km2, roughly 14 suburbs per unit. It
   cannot separate neighbouring suburbs, which is the entire decision being made.

GP FTE therefore appears as a **context column only**, never as a ranking input. The honest
statement is "practices per capita, because headcount at suburb resolution does not exist".

### D6 - Criteria restructured; weights defended by sensitivity, not assertion

The old weights (0.35 supply / 0.25 income / 0.20 seniors / 0.15 population / 0.05 area) were
undocumented and had two specific defects: `area` at 0.05 rewarded *smallness*, and population at
only 0.15 - min-maxed across a range dominated by 30,000-plus suburbs - meant a 1,300-person
enclave was barely penalised. Point Piper ranked first.

| Criterion | Weight | Direction |
|---|---|---|
| Access gap (E2SFCA) | 0.40 | Lower accessibility is better |
| Demand scale (senior-weighted, with a viability floor) | 0.25 | Higher is better |
| Mixed-billing fit (inverted-U income, D1) | 0.25 | Peak in band |
| Operational (pharmacy co-location, DPA status) | 0.10 | Higher is better |

`area` is dropped. Raw population is replaced by senior-weighted demand with a minimum viable
catchment floor.

*The weights are not the defence.* No client input existed to derive them, so asserting them
would be false precision. The defence is §5: the recommendation is the set of suburbs that stay
top-10 across every plausible weighting.

---

## 4. Technique: why E2SFCA

Four options were considered for turning "practices near a suburb" into a supply measure.

| Technique | Verdict |
|---|---|
| Euclidean buffer count (practices within N km) | **Screen only.** Enough to disprove Croydon; too crude to rank on - it double-counts across neighbours and has no demand denominator |
| Road-network isochrones | **Wanted, NOT SHIPPED.** See the disclosure box below |
| **E2SFCA** (Enhanced Two-Step Floating Catchment Area) | **Chosen** for its two-step structure and demand denominator. See below |
| Huff / gravity market-share model | **Deferred.** The right tool for revenue forecasting, but needs calibration data we do not have |

> ### DISCLOSURE: the shipped travel cost is straight-line, not road network
>
> The accessibility surface in `accessibility.py` uses **straight-line distance converted to
> indicative drive time at an assumed 35 km/h**. The road-network isochrone work was time-boxed
> and abandoned. This is the single largest methodological weakness in the rebuild, and it is
> stated here because two of the three objections raised against Euclidean buffers above
> **also apply to what actually shipped**: it ignores barriers, and it treats a 10-GP centre
> the same as a solo GP (see D5 and §8).
>
> What E2SFCA does add over a buffer count is real - a two-step supply-to-demand ratio and a
> distance-decayed demand denominator - but it does not fix the geometry.
>
> **Why this matters in Sydney specifically:** the harbour, the Parramatta and Georges Rivers,
> national parks and motorways with few crossings mean two suburbs 2 km apart can be 20 minutes
> apart. Straight-line distance is the **optimistic** bound, so coastal and peninsula suburbs
> are flattered.
>
> **The result is sensitive to the assumed speed.** Croydon's "well served" percentile moves
> 57.8 / 71.9 / 85.6 / 98.1 across 24 / 30 / 35 / 40 km/h. The qualitative conclusion (above
> median, therefore not an opportunity) survives the whole range, but at the low end it is
> "barely above median", not "comfortably served".
>
> Fixing this is the highest-value next step.

**E2SFCA** (Luo & Wang 2003; Luo & Qi 2009) is the standard method in health geography for
spatial access to primary care:

- *Step 1:* for each practice, a supply-to-demand ratio = supply divided by the population within
  its travel catchment, distance-decayed.
- *Step 2:* for each suburb, sum the ratios of every practice reachable from it, distance-decayed.

It fixes 2.2 **structurally** rather than by patching: access becomes a continuous surface, so
Croydon correctly inherits Burwood's supply. Two further properties matter here:

1. The decay weights let D2's cohort difference be expressed directly - a tighter curve for 65+.
2. **Accessibility is legitimately defined for the 239 `no_data` suburbs**, because it depends on
   *nearby* practices rather than on whether that suburb was scraped. This is how the held-out
   suburbs re-enter the analysis instead of being permanently excluded.

A plain `practices_within_2km` column is carried on every output alongside it as a
human-readable sanity check, so no reader can repeat the Croydon mistake.

---

## 5. Sensitivity analysis is the deliverable

With no client input on weights, thresholds or travel time, a single ranking is not defensible.
What *is* defensible is a ranking plus a demonstration that it survives the choices we had to
make. Three perturbations:

1. Each weight varied +/- 50% and to zero.
2. Income band knots moved +/- $250.
3. Travel cutoff at 15 / 20 / 25 minutes.

**The recommendation is the robust core - the suburbs that stay top-10 under every perturbation.**
Anything that appears only under one parameterisation is reported as fragile, not recommended.

---

## 6. Data sources

| Purpose | Source | Status |
|---|---|---|
| Practice inventory + coordinates | GA "National HealthDirect Health Facilities" layer 0, eCat `696d12c2-38c6-4afa-96b6-309a1ac9a50b`, extract 2025-03-07, CC-BY 4.0 | **Authoritative.** Filter `nhsd_service_type='General practice service'`: 2,089 of 2,482 NSW rows |
| Pharmacy co-location | Same service, layer 2 - 1,832 NSW | Adopted |
| Empirical catchments | GP Catchments (GPC) 2023, ABS ASGS 2021 aggregations built from **MBS patient-flow data** | Boundaries only, CC-BY 2.5 AU |
| DPA (hiring cost) | `dpa_gps_2025a.zip`, 1,328 polygons, March 2025 | Narrow discriminator in Sydney |
| GP headcount | Dept of Health HeaDS UPP / NHWDS | SA3 scale - context only (D5) |
| Pathology centres | none exists | Dropped |
| PHIDU atlases | PHIDU | **CC BY-NC-SA - non-commercial. Unusable for a client engagement** |

**NSW government publishes no GP location data.** Its health facilities service carries only
Ambulance (316) and Hospital (306) layers; Data.NSW has no primary-care equivalent. Everything
usable is Commonwealth.

### Two important caveats on the register

- **It is not complete.** Nationally 6,254 practices against roughly 7,000-7,135 accredited, so
  it misses an estimated **10-12%**. Listing on the NHSD is voluntary and self-registered. It is
  demonstrably missing Croydon Medical Centre, about 20 years in operation.
- **Neither source is ground truth.** Correlation with the project's own scraped counts is only
  Pearson 0.82-0.84. The register is far better on *provenance* - auditable, licensed, dated - and
  that is the reason to adopt it, not completeness.

Note the link commonly circulated (`1d95ca95-...`) is the **WMS service** record carrying
March-2024 lineage, not the current data record. It also implies WMS-only access; in fact there is
a queryable ArcGIS REST endpoint that pages GeoJSON directly.

---

## 7. What still needs the client

Ordered by how much it would change the answer:

1. **The authoritative list of current Nuvo sites** (D4). Cheapest, highest value. Without it we
   may recommend a suburb they already operate in.
2. **Actual gap fee charged** (D1). Sets the lower knot with evidence instead of inference.
3. **Whether they want to compete with premium private practices** (D1 upper knot).
4. **How far they believe patients will travel for this product** (D2).
5. **Any weighting preference among access / demand / billing fit / operations** (D6).

Until 1 is answered, every output carries the unverified-exclusion flag.

---

## 8. Known limitations carried forward

See CAVEATS.md for the full list. The ones that bear on this methodology specifically:

- The register misses an estimated 10-12% of practices.
- Practice **capacity** is not modelled. A count of practices says nothing about appointment
  availability, which is arguably Sydney's real constraint. Two suburbs with equal practice counts
  can have very different wait times.
- **Competition is not modelled as differentiable.** A suburb with many GPs may still support a
  7-day multidisciplinary mixed-billing clinic if the incumbents are short-consult bulk-billing
  practices closing at 6pm. The current score treats all practices as equivalent competitors,
  which is conservative - it will under-rate dense areas where the product is genuinely
  differentiated.
- **Absence of supply can indicate absence of demand.** Heathcote may have no GP within 5 km
  because 6,000 people in bushland cannot support one. Every genuine gap needs a "why is nobody
  already here?" answer before it ships.
- Practice **size** is not weighted; a 10-GP centre counts as one practice, because the register
  has no headcount (D5).

---

## 9. Results

Two independent adversarial verifiers re-derived everything below. Where they disagreed with the
implementation, **their** numbers are reported.

### 9.1 The Croydon fix worked

Both verifiers confirm, with high confidence, that Croydon now scores as **well served** and
drops out of the recommendation. The structural error in §2.2 is corrected: access is a surface,
so Croydon inherits Burwood's and Ashfield's supply. Heathcote correctly scores as poorly served.

That was the point of the rebuild, and it holds.

### 9.2 The robust core is ONE suburb, not a shortlist

| | Result |
|---|---|
| Implementation claimed | Cronulla + Dee Why, both stable across all 27 perturbations |
| Independent 28-run sweep | **Cronulla only** |

Dee Why failed 2 perturbations (income knots +$250; demand_scale weight to zero). **Treat
Cronulla as the only survivor** and everything below it as a candidate requiring its own
verification.

Cronulla: population 17,899, 6 practices within 2 km, nearest 104 m, 8 GPs recorded =
1,989 people per GP - thin on both the accessibility measure and the project's own supply data.

One fragility worth knowing: Cronulla's `mixed_billing_fit` of exactly 1.000 depends on its
$2,058 income clearing the $2,050 plateau knot by **$8**. It holds rank 1 when the knot is moved,
but that is a coincidence, not a margin.

### 9.3 A blocking defect: rank 2 contradicts the project's own data

Dee Why was ranked #2 as an opportunity while carrying **35 GPs (a floor, so at least 35)**
against 23,354 residents = **649 people per GP**, better supplied than roughly 73% of eligible
suburbs. Both figures sit in `final_shortlist_v2.csv` side by side, unremarked.

**Cause, and it is a consequence of D5.** E2SFCA was run with `S_j = 1` per practice, because the
register has no headcount. So a suburb served by **few but large** practices scores as
under-served. Practice size varies enormously - GPs per clinic mean 4.41, median 3, and suburb
level p10 1.0 against p90 8.5, an 8.5x spread - and `S_j = 1` was never varied in the sensitivity
sweep, making it the assumption the shortlist is most exposed to.

Illustration:

| Suburb | Pop | Practices within 2 km | GPs recorded | People per GP | Verdict |
|---|---|---|---|---|---|
| Cronulla | 17,899 | 6 | 8 | 1,989 | Genuinely thin - survives |
| **Dee Why** | 23,354 | 6 | **35** (floor) | **649** | **Well supplied - should not rank** |
| Oran Park | 17,624 | 1 | 19 | 881 | Ambiguous - isolated but large practice |
| Croydon | 10,755 | 27 | 1 | 5,378 | Saturated - correctly demoted |

**Required guard, not yet implemented:** demote or flag any suburb whose recorded people-per-GP is
better than the cohort median, even though headcount is not a ranking input. That keeps D5 intact
while stopping this failure mode.

### 9.4 A data bug nobody had caught: the `area` column is corrupted

Found while verifying the maps. Measured against ABS SAL 2021 geometry:

- **12 of 548 matched suburbs are wrong by more than 2x**, 21 by more than 1.5x, 61 by more
  than 1.2x.
- Worst: **Sydney 26.47 km2 recorded against 2.94 actual (9.0x)**; Berowra Waters 14.61 vs 1.68
  (8.7x); Waterfall 2.36 vs 17.58 (7.5x too small); Pemulwuy 3.71 vs 14.98.
- **Six pairs of suburbs share a byte-identical area value** - a copy-paste signature:
  Dolls Point / The Rocks (0.2066), Woolloomooloo / Northwood (0.3769), Tennyson /
  Tennyson Point (0.4488), Denistone / Denistone East (0.6976), Bass Hill / Westmead (2.9709),
  Berowra / Berowra Heights (7.9626).

`spatial_density = population / area`, so every density figure inherits this - including the
priority map's senior-density axis, its "sparse suburb" exclusion, and
`eda/opportunity_high_density_low_gp.csv`. **ABS SAL 2021 is now cached locally and should replace
this column outright.**

### 9.5 Other findings that must be closed before this ships

- **Two incompatible suburb geometry frames coexist, undisclosed.** `practices_inside` is built on
  the OSM `place=suburb` cache (650 polygons + a 30-suburb name join); accessibility, DPA and
  catchment assignment are all built on ABS SAL 2021 (all 680). They disagree on 16 suburbs.
  Pick one frame - SAL, since it is authoritative and complete.
- **Both maps are stale relative to the score.** Neither references the new shortlist, the Nuvo
  exclusion or the viability gates. Of the 29 suburbs in the priority map's darkest "highest
  priority" cell, only **3** are in the score's top 10 and **11 are ineligible**. Rank 9
  (Mona Vale) is not drawn at all, being one of the 30 suburbs with no OSM polygon.
- **D2's senior half never reached the answer.** `access_senior` at the 15-minute cutoff is
  computed and written, then never read by the scoring function. The ranking uses `access_general`
  only; senior demand enters as a non-spatial population multiplier. §4's claim that the decay
  weights express the cohort difference is therefore **not true of what shipped**.
- **13 suburbs' supply rests on a CRS-dependent reference point.** `representative_point()` is not
  population-weighted and moves up to 7.08 km between projections (Berowra Waters).
- **`final_shortlist_v2.csv` does not state the travel-cost method**, so a reader of the actual
  deliverable cannot see that 40% of the weight rests on straight-line distance.
- Register data quality: the blank G-NAF pid figures were **double-counted** (true values 230
  practices / 245 pharmacies, i.e. 11.0% / 13.4%). 27 non-GP services survive the
  `General practice service` filter statewide, 9 inside the study area, mostly Headspace youth
  mental health - so 2,089 slightly overstates comparable GP supply.

### 9.6 Honest summary

The rebuild fixed the error it set out to fix, and the *method* is now defensible in outline. The
*answer* is not yet: one suburb survives perturbation, rank 2 is contradicted by data already in
the repo, the travel surface is straight-line, the area column is corrupted, and the maps no
longer match the ranking.

**Do not put this shortlist in front of the client.** The credible outputs today are the
diagnosis (§2), the method (§3-5), and Cronulla as a single verified candidate.
