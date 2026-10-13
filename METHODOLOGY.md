# Methodology, validation and limitations

Single reference for how the Nuvo Health site selection works, what was checked, and what
it cannot tell you. Replaces the previous `METHODOLOGY.md` and `CAVEATS.md`, which
overlapped and had drifted: of their 73 sections, a triage against the live code found 17
still accurate, 44 stale in their numbers or file names, and 12 describing the v1 pipeline
that was deleted on 2026-09-23.

Every figure below was re-derived from the repository rather than carried forward. Where a
number could not be verified it is either absent or marked.

The short version is in [README.md](README.md). This document is the detail behind it.

---

## The question

Nuvo Health runs mixed-billing, 7-day, multidisciplinary GP clinics, and the decision is which Greater Sydney
suburb the next one opens in. That is a commercial question, not a public-health one: the score asks where a
mixed-billing clinic can fill appointments, and the need lens is carried separately as `community_need_score`,
never ranked on by default.

The frame is the 680 suburbs in `scraped_data/eda_data.csv`. Demand is the 2021 Census (population, 65-plus
share, median weekly household income); supply is the Healthdirect National Health Services Directory as
republished by Geoscience Australia, extract 2025-03-07: 2,089 NSW general practices and 1,832 pharmacies,
dated and re-fetchable. 443 of the 680 suburbs are eligible for a rank.

What a ranked row claims is "too few practices for the population that must share them", never "no GP here".
Cronulla at rank 1 has 6 practices within 2 km, the nearest 104 m away.

## Method

Build an E2SFCA accessibility surface, score four criteria on [0,1], combine as a weighted sum, apply two hard
eligibility gates and the Nuvo catchment exclusion, then re-rank under 31 perturbations - the deliverable is
the robust core of that sweep, not the point ranking.

### Why E2SFCA rather than counting practices inside the suburb

Counting practices inside a suburb polygon measures which side of an administrative line a clinic sits on.
Croydon has 1 practice inside its boundary and 27 within 2 km; v1 ranked it the best opportunity in Sydney, and
it now ranks 141 at the 86th percentile of access. The artefact is not rare: 298 of 680 suburbs contain no
practice, and of the 46 suburbs with 5,000-plus residents and none inside, 43 have one within 2 km.

E2SFCA (Luo and Qi 2009) replaces the count with a continuous surface in two steps (`accessibility.py`). Step 1
gives each practice `R_j = S_j / sum_k P_k W(d_kj)`, its supply over the decay-weighted population competing for
it; step 2 gives each suburb `A_i = sum_j R_j W(d_ij)`, in practices per 1,000 people. `S_j = 1` for every
practice - the register has no practitioner-count field, which is what the D5 headcount guard offsets.

`W` is a stepped 5-minute Gaussian decay, 0.980 / 0.827 / 0.549 / 0.192 out to the 20-minute general cutoff; the
15-minute 65-plus surface is context only, and seniors enter the score through the 2.5x attendance multiple in
`demand_scale`. Step 1 also counts ABS SA2 population outside the 680-suburb footprint - without that ring, edge
practices divide by a truncated population and the fringe reads as superbly served; adding it moved the access
distribution's correlation with practices within 2 km from -0.01 to +0.56. Travel cost is straight-line at an
effective 35 km/h, not road-network routing: the largest weakness, treated below.

### The four criteria

| criterion | weight | value function | direction |
|---|---|---|---|
| `access_gap` | 0.40 | 1 - rank percentile of E2SFCA access, then `x (1 - 0.5 x headcount_adequacy)` | low access -> high score |
| `demand_scale` | 0.25 | log ramp on senior-weighted population: 0 at 5,000, 1 at 20,000 weighted persons | more demand -> higher |
| `mixed_billing_fit` | 0.25 | inverted U on median weekly household income, knots at P15/P40/P80/P95 | mid-income -> highest |
| `operational` | 0.10 | `0.7 x` nearest-pharmacy ramp (1.0 at <=0.4 km, 0 at >=2.0 km) `+ 0.3 x` DPA flag | closer pharmacy -> higher |

Each shape answers a v1 failure. `access_gap` is a rank percentile so that zero-access and ferry-only suburbs
cannot set the scale, and its guard term lets a trustworthy headcount contradict a high gap score - downward
only, `gp_data_status == "ok"` only, so headcount never ranks. `demand_scale` is logarithmic because
senior-weighted population spans 3 to 60,700, and a linear min-max over that range let v1 rank a 1,334-person
enclave first. `mixed_billing_fit` is an inverted U because a ~$43 rebate against a ~$90 private fee needs
households that can absorb the gap sometimes; its knots are re-derived at run time from the loaded income column
($1,583 / $2,101 / $2,872 / $3,492 on the shipped 2021 data). `operational` caps DPA at 0.3 of 0.10 because DPA=Y
fires on 79 of 680 suburbs at a median population of 815 - a hidden smallness bonus at full weight.

### Gates and exclusions

Both gates are hard: a suburb below either keeps its sub-scores for audit but gets no rank.

| gate | threshold | suburbs removed |
|---|---|---|
| local population | >= 1,000 | 96 of 680 |
| catchment demand | >= 25,000 decay- and senior-weighted persons, ring included | 28, of which 7 are not already below the population floor |

The catchment gate's own comment (`site_score.py:161`) claims it binds on zero extra suburbs; the shipped output
says 7, all rural-fringe, so the comment is stale rather than the gate.

Nuvo's own sites are excluded at GP-catchment level, not by suburb name, because a clinic 1 km away in the next
suburb cannibalises just as effectively. The list is the operator's published listing of 18 centres (15 map to ABS
suburbs; 3 are precinct names in catchments the 15 already cover), retrieved 2026-09-23 from nuvohealth.com.au,
and it removes 145 suburbs across 10 of the 46 GP catchments the study touches - 134 of them otherwise eligible.
**The list is unverified**: it was read off the operator's public website, every output row says so, and a
public page can lag an opening or a closure. This project has no relationship with Nuvo Health; the list would
need confirming with the operator before anyone acted on it.

That leaves 443 ranked suburbs, with `catchment_rank` and `best_in_catchment` flagging the cases where two of them
are really one opportunity - Caringbah South at rank 2 sits in Cronulla's own catchment.

---

## Three structural errors, and how each was caught

Three errors in the first pipeline each changed the answer, not the margins. None was found by
reading the code: each was found by taking the name at the top of the list and testing it against a
source the pipeline had never seen. A fourth of the same shape turned up in the rebuild and is
recorded here because it is only half-closed.

| # | What was wrong | What exposed it | What replaced it |
|---|---|---|---|
| 1 | Never-scraped suburbs scored as having zero GPs | 7 of the 10 recommended names had no supply data at all | Three-state `gp_data_status`; supply re-measured from a 2,089-practice register |
| 2 | Supply counted inside the suburb polygon | Croydon: 1 practice inside, 27 within 2 km | E2SFCA accessibility surface over practice points |
| 3 | Income scored monotonically higher-is-better | Top-20 median income $3,283 against a cohort median of $2,171 | Inverted U on income, knots derived from percentiles (D1) |
| 4 | One unit per practice, so few-but-large reads as scarcity | Dee Why ranked 2 on 35 recorded GPs for 23,354 residents | Downward-only headcount demotion guard (D5) |

### 1. Missing data was read as zero supply

**Wrong.** GP supply came from 70 prompt files pasted into an LLM chat (`prompts/chunk_*.txt`). The
scrape returned 441 of the 680 study suburbs; 239 never appeared. The merge fills those with
`total_gp = 0.0` (`gp_supply.py:251`), and both v1 consumers read that zero as a measurement: the
MCDA supply criterion was a min-max on `total_gp`, lower-is-better at weight 0.35, so an unmeasured
suburb scored a perfect 1.0; the shortlist chain used `People_Per_GP_Ratio = population /
(total_gp + 1)`, which reads it as maximum pressure.

**Proof.** Cross-tabulate the v1 shortlist against `gp_data_status`. Seven of the ten names in
`eda/final_top10_clinical_intensity.csv` had `total_gp = 0` and status `no_data` - Blakehurst,
Caringbah South, Vaucluse, Killara, Concord West, Cromer, Gymea Bay. The whole-cohort MCDA ranking
was no better: the same 7 of 10, led by Point Piper, population 1,334. Both files are recoverable
at commit `5fe4d38`; the v1 chain was deleted at `ad78bd7`.

**Replaced.** `gp_supply.py` splits the column into three states - `ok` 433, `gp_count_unknown` 8,
`no_data` 239 of 680 - with Bayview and Beacon Hill the only two confirmed zeros. v2 does not rank
on the scrape at all: supply is an E2SFCA surface over the 2,089-practice Healthdirect register, so
all 680 suburbs are measured on the same basis. `site_score.py:968` admits a suburb to the one
surviving headcount rule only where `status == "ok"` and `total_gp > 0`, and `_test_headcount_guard`
(`site_score.py:1428`) asserts it never fires on anything else.

### 2. Supply was counted inside suburb boundaries

**Wrong.** With the no-data suburbs held out, the top name left on the v1 shortlist was Croydon. An
in-polygon count measures which side of an administrative line a clinic sits on.

**Proof.** Count register practices by distance from the suburb centroid instead of inside its
polygon. Croydon, between Burwood and Ashfield, has 1 inside, 21 within 1.5 km, 27 within 2 km, 42
within 3 km, nearest 385 m. The pattern is systematic, not an anecdote: of the 298 suburbs with no
practice inside, 170 (57.0%) have one within 1.5 km and 206 (69.1%) within 2 km, and only 61 suburbs (55,147 people) have none within 3 km. Among suburbs of 5,000-plus residents with none inside there
are 46, and 43 have one within 2 km; the three exceptions are Heathcote, Spring Farm and Ruse. The
error hid the genuine cases too: Heathcote, 6,148 residents, has 0 practices within 2 km, 9 within
3 km, nearest 2.455 km, and never reached a v1 shortlist.

**Replaced.** `accessibility.py` computes an enhanced two-step floating catchment area surface:
supply-to-demand ratios with distance decay (`e2sfca`, line 471), plus a ring of demand outside the
study area (`load_outside_demand`, line 651) so boundary practices are not credited with a
truncated demand pool. `practices_inside` survives as a context column, not a ranking input. Croydon
fell to rank 141 of 443 at the 85.6th access percentile; Heathcote rose to rank 10 at the 5.2nd.
`_test_croydon_structure` (`accessibility.py:1113`) locks the structural claim on synthetic data,
and `validation.py` supplies the external one: against the Department of Health DPA under-supply
flag, which the pipeline never reads, `access_gap_unguarded` scores AUC **0.813 [0.743, 0.879]**.
The composite `site_score` scores 0.640 [0.576, 0.706], as it should - it also rewards income fit
and demand, which DPA does not measure.

### 3. Income was treated as monotonically better

**Wrong.** Income entered at weight 0.25, higher-is-better, so the score could never stop climbing.

**Proof.** Read the list's income distribution against its own cohort. Over the 423 rankable
suburbs in the v1 output, cohort median was $2,171/week and top-20 median $3,283 - the 95th
percentile of that cohort - with 8 of 20 at or above $3,500. The leaders were Longueville $4,894,
Riverview $4,731, Castlecrag $4,675 and North Balgowlah $4,212: households that buy full private
care, the segment least likely to buy mixed billing. The plausible target band, $1,800-3,000, holds
274 of the 433 `ok` suburbs and 2.69 million people.

**Replaced.** D1 makes income an inverted U whose knots are derived at run time from percentiles
15/40/80/95 of the income column actually loaded (`INCOME_KNOT_PERCENTILES`, `site_score.py:130`) - on the shipped 2021 data, $1,583 / $2,101 / $2,872 / $3,492. The eligible top 20 now has a median
household income of $2,388, a maximum of $3,120 and nothing at or above $3,500; all four former
leaders score `mixed_billing_fit` 0.0. `_test_derived_income_knots` (`site_score.py:1402`) asserts
the property the hard-coded dollar knots lacked: multiply every income by 1.2, and no sub-score moves.

### 4. Found after the rebuild: one unit per practice

**Wrong.** The register carries no practitioner-count field, so the E2SFCA supply term is one unit
per practice: a twelve-doctor clinic and a solo GP count the same, and a suburb served by few but
large practices reads as under-supplied.

**Proof.** Read the rebuilt ranking against the very scrape v1 was scrapped for. Dee Why ranked 2
on `access_gap` 0.844 with 35 recorded GPs for 23,354 residents - 667 people per GP, better
supplied than 70% of the eligible cohort with a usable ratio. Mona Vale at rank 9 was worse still
at 494 per GP, better supplied than 84%.

**Replaced.** The D5 guard (`headcount_adequacy`, `site_score.py:839`) lets a trustworthy headcount
contradict a high `access_gap`, downward only: full demotion at 640 people per GP or fewer, none at
1,040 or more, at half strength. It demotes 159 of the 443 eligible suburbs, 89 at full adequacy,
mean `access_gap` reduction 0.210. Dee Why 2 → 17, Mona Vale 9 → 42, Freshwater 10 → 12, and four
of the ten recommended names change: out go Dee Why, Engadine, Freshwater and Mona Vale; in come
Collaroy, Glenmore Park, Heathcote and Newport. Guard strength is one of the nine sensitivity
families (off / 0.25 / 0.5 / 0.75), so the effect can be read off rather than taken on trust.

**Still open.** The guard can only check a suburb with a trustworthy headcount, and 125 of the 443
eligible have none - 116 `no_data`, 7 `gp_count_unknown`, 2 confirmed zeros. Caringbah South at
rank 2 is one; Cromer at 16 is the other in the top 30. Five more top-30 names carry floor counts
rather than exact ones (Cranebrook 5, Newport 8, Miranda 15, Dee Why 17, Botany 23), so their
people-per-GP is overstated and they are under-demoted. The guard also costs agreement with the
external check - AUC falls from 0.813 to 0.779 [0.707, 0.846] on the DPA flag, on heavily
overlapping intervals - and its knots are frozen constants: re-derived on the current 318-suburb
cohort, the same percentiles give 612 and 1,030, not 640 and 1,040. `site_score.py:35-38` still
quotes the pre-gate cohort, 197 of 507 and 134 unguardable with Dee Why → 19, against the live 159
of 443, 125 and 17. A `no_data` suburb's rank carries strictly less evidence than an `ok` one.

---

## Does it work?

`validation.py` asks the two questions nothing else in the pipeline asks: is the score any good against
something outside itself, and is the ranking just a map of where Sydney's peninsulas are. It is seeded,
and re-running it leaves its three output CSVs byte-identical.

### External check: access_gap against the DPA flag

The Department of Health's Distribution Priority Area designation is an independent under-supply call
derived from GP FTE per capita. AUC, with a 2,000-draw class-stratified percentile bootstrap, over all
680 suburbs (79 positive, 601 negative).

| measure | AUC | 95% CI |
|---|---|---|
| `access_gap_unguarded` | 0.813 | [0.743, 0.879] |
| `access_gap` (shipped, after the D5 guard) | 0.779 | [0.707, 0.846] |
| `site_score` (composite) | 0.641 | [0.576, 0.706] |
| `demand_scale` | 0.240 | [0.208, 0.275] |

0.813 is real discrimination on the criterion carrying 40 percent of the weight; `demand_scale` at 0.240
is the check in the other direction, since a size criterion should anti-predict a flag whose positives
are tiny. Three things travel with the headline number.

1. **The benchmark is rural-calibrated.** The median DPA-positive suburb has 815 people and zero
 practices within 2 km, against a median of 4,940 across all 680, and only 29 of the 79 positives
 clear the eligibility gates. A high AUC says the score agrees with the government about obvious
 scarcity, not that it picks good sites.
2. **It validates one criterion, not the ranking.** The headline figure is the *unguarded* access
 surface; the shipped `access_gap` scores 0.779 and the composite only 0.641 - by design, since 60
 percent of the weight is commercial fit. Even 0.641 is flattered: `operational` is 0.10 of the weight
 and DPA is 0.3 of it (`DPA_OPERATIONAL_SHARE`, site_score.py:214), so 3 percent of the composite is
 the answer key. `access_gap` itself has never seen DPA.
3. **The interval is too narrow.** The bootstrap resamples suburbs as though independent, but DPA
 arrives by areal overlay of catchment-scale polygons: 35 of the 46 catchments carry a single label
 across a median of 15 suburbs, so the effective sample is nearer 46 than 680.

### Spatial autocorrelation: the suburbs are not independent observations

Global Moran's I over the 443 eligible suburbs, k-nearest-neighbour weights, 999 permutations:
`site_score` 0.359 (z 16.1, p 0.001), `access_gap` 0.623 (z 27.2, p 0.001), `mixed_billing_fit` 0.387,
`demand_scale` 0.070 (p 0.003). I on `site_score` falls with the neighbour count - 0.397 (k=4), 0.359
(k=8), 0.296 (k=16), 0.232 (k=32) - and p holds at 0.001, the floor 999 permutations can return rather
than a measured one-in-a-thousand. The clustering is not a defect; access to care really is spatially
structured. It changes inference: 443 eligible suburbs are not 443 independent observations, so every
interval computed suburb-by-suburb, the AUC above included, is a lower bound on the true uncertainty.

### Local clusters: the top of the list is one place

LISA with a conditional permutation null and Benjamini-Hochberg control at FDR 0.05 finds 58 of 443
suburbs significant against 124 uncorrected, so the correction is doing real work. By quadrant: 47 LL,
9 HH, 2 LH, **zero HL**. All nine significant high-high suburbs are Northern Beaches: Avalon Beach
(rank 4), Newport (8), Collaroy (9), Narrabeen (61), Bilgola Plateau (73), Wheeler Heights (79), Palm
Beach (84), Collaroy Plateau (191), Bayview (197).

No significant HL suburb means no candidate is a *local* anomaly - every high scorer sits among other
high scorers, so the opportunity is shared with an adjacent suburb rather than specific to the site.
Seven of the top 10 are not individually significant, Cronulla among them (local I 0.479, p 0.632). The
two connected clusters are unstable: Avalon Beach and Collaroy share a cluster at k=16 and k=24 but not
at k=6, 8 or 12, so membership is advisory and the shortlist groups on GP catchment instead.

### Rank uncertainty, and two results that came out negative

1,000 resamples of the 33 GP catchments covering the 443 eligible suburbs.

| rank | suburb | median | 95% interval | P(top 10) |
|---|---|---|---|---|
| 1 | Cronulla | 1 | [1, 1] | 1.00 |
| 2 | Caringbah South | 3 | [2, 5] | 1.00 |
| 6 | Oran Park | 5 | [2, 11] | 0.96 |
| 10 | Heathcote | 9 | [3, 17] | 0.63 |

**Blocking barely matters.** Mean 95 percent interval width across the top 10 is 7.8 ranks blocked
against 7.3 naive, 7 percent. The Moran's I above predicted a much larger gap; it does not appear
because a rank depends on how many suburbs outscore you, and that count hardly notices whether
correlated neighbours arrive together. Blocking matters for a mean and very little for a rank. Recorded
rather than dropped, because the design was argued for in advance and the data then barely supported it.

**The leader's [1, 1] is tautological.** Resampling changes which suburbs compete, never their scores,
so the top scorer is rank 1 in every draw by construction. That is not evidence of robustness and must
not be quoted as such; Cronulla's case rests on the 31 assumption perturbations, not on this. The
informative rows are lower down - Heathcote at rank 10 spans [3, 17] and is top-10 in only 63 percent of
draws. Neither bootstrap carries measurement error: population, income and the practice register are
treated as exact, so none of their uncertainty reaches a rank.

---

### Population vintage: the one test that could have moved rank 1

Supply is the March 2025 register; demand is the 2021 Census. Four years of population
change sits unmodelled between them, and it is not uniform.

This is deliberately **not** "Census measurement error". The ABS perturbs small-area
counts for confidentiality, but these are suburb totals in the thousands - at Cronulla's
17,899 residents a ±3 perturbation is 0.02% and propagating it would produce an
authoritative-looking interval of zero width. The vintage gap is the uncertainty that
actually moves scores.

**Calibration is empirical, and matching on name alone was wrong.** 90 suburbs share a
name with an SA2, but SAL and SA2 geographies differ: Chullora reads as 14 residents
against an SA2 ERP of 1,524 - a 109× "growth" that is entirely an area mismatch. Name
matching gave a median growth of 8.2% and 25 suburbs above 25%. Requiring
intersection-over-union ≥ 0.90 between the two polygons leaves **43 suburbs, median
growth 5.1%, 5 above 25%**, and puts Oran Park at 1.458 - the figure this project had
recorded independently. The filter discards 47 of 90 name matches.

200 replicates per mode. Each draws a growth ratio per suburb from those 43 observed
ratios, scales population, and rebuilds the E2SFCA surface - population is the demand
side, so `access_gap` moves too, not only `demand_scale`.

| Base rank | Suburb | Median | 95% interval | P(top 10) | P(rank 1) |
|---|---|---|---|---|---|
| 1 | Cronulla | 1 | [1, 2] | 1.00 | **0.95** |
| 2 | Caringbah South | 2 | [2, 6] | 1.00 | 0.00 |
| 3 | St Clair | 5 | [2, 9] | 0.99 | 0.00 |
| 4 | Avalon Beach | 4 | [1, 7] | 1.00 | 0.04 |
| 6 | Oran Park | 5 | [2, 12] | 0.95 | 0.00 |
| 10 | Heathcote | 11 | [6, 18] | 0.42 | 0.00 |

*(catchment-blocked draws, the conservative mode)*

Cronulla holds rank 1 in **95%** of replicates under blocked draws and 99% under
independent ones. This is the first test in the project capable of dislodging it - the
rank bootstrap could not, because resampling never changed a score - and it did not.

**Blocking matters here, and that is itself the finding.** Correlating growth within a
GP catchment widens the top-10 intervals by **66%** (mean width 8.3 ranks against 5.0).
The cohort bootstrap saw only 7% from the same treatment. The difference is real: cohort
membership is not spatially structured, but growth is - corridors grow together, so
correlated draws generate scenarios independent draws never reach. Where a quantity is
genuinely spatial, blocking changes the answer; where it is not, it does not.

**Limits, stated rather than buried.** 43 calibration suburbs is a small sample and not a
random one - they are the suburbs whose boundaries happen to match an SA2. ERP adjusts
for Census undercount, so roughly 2% of every ratio is definitional rather than growth,
biasing the pool up and the spread conservative in one direction. Only population is
perturbed; the register, incomes and travel cost are held fixed.

---

### Does access_gap add anything beyond suburb size? Nested logistic models

The AUC comparison prints 0.813 against a 0.849 population baseline and leaves the reader
to eyeball the difference. Fitting nested models answers it directly, and the coefficient
is interpretable where an AUC gap is not.

Fit `DPA ~ log(population)`, then `DPA ~ log(population) + access_gap`, and compare by
likelihood ratio test - `LR = 2(ℓ_full - ℓ_reduced)`, chi-square on 1 df.

| Cohort | n | DPA+ | LR | p | AIC |
|---|---|---|---|---|---|
| All classified | 680 | 79 | 56.52 | 5.6e-14 | 400.1 → 345.6 |
| Eligible only | 443 | 29 | 8.77 | 0.003 | 195.7 → 188.9 |

On its face that settles it: access_gap adds strongly, odds ratio 49.1 on the full cohort.

**But the likelihood ratio test assumes independent observations, and they are not.**
Refitting with standard errors clustered on GP catchment, which makes no independence
assumption between suburbs sharing a catchment:

| Cohort | z naive | z clustered | p clustered |
|---|---|---|---|
| All 680 | +6.53 | **+1.65** | 0.098 |
| Eligible 443 | +2.75 | **+0.87** | 0.384 |

The evidence collapses. And the reason is specific rather than mysterious: **40 of the 79
DPA-positive suburbs sit in just three GP catchments**, and positives appear in only 16 of
46 catchments at all. The naive test counts those 40 as 40 independent pieces of evidence.
Clustering counts them closer to three, which is nearer the truth.

**What this does and does not mean.** The coefficient sign and magnitude are unchanged - access_gap still moves DPA odds in the expected direction. What changes is how much
confidence the DPA benchmark can support: once spatial concentration is handled honestly,
it cannot demonstrate that access_gap beats a size proxy. That is a statement about the
benchmark's power, not proof that the access measure is uninformative. The
population-stratified AUC, which attacks the same question differently, still shows
discrimination at 0.81-0.82 within bands.

**A caveat on the caveat.** Cluster-robust standard errors are themselves unreliable below
roughly 40-50 clusters. There are 46 here, 33 among the eligible. So the clustered z is the
better of two imperfect numbers, not a definitive one.

---

### What the DPA check does and does not establish

Reported three ways, because the headline number alone is misleading.

**On all 680 classified suburbs**, `access_gap` scores AUC 0.813 [0.743, 0.879] against a
flag it has never seen. **On the 443 eligible suburbs** - the ones actually competing for
the recommendation - it falls to 0.719 [0.586, 0.837], and the composite `site_score`
falls to 0.570 [0.452, 0.673], an interval containing chance.

**A naive baseline beats the model on the full cohort.** `-log(population)` alone scores
0.849 [0.809, 0.886]. DPA-positive suburbs have a median population of 815 against 5,643
for the rest, so any measure correlated with smallness scores well without knowing
anything about GP supply. Reporting 0.813 as validation, without this, would be claiming
credit a population proxy earns more of.

**Stratifying by population isolates whether the score adds anything.** Within bands, the
size channel is closed off:

| Band | n | DPA+ | AUC |
|---|---|---|---|
| 0-1,000 | 96 | 43 | 0.808 [0.71, 0.90] |
| 1,000-5,000 | 248 | 26 | 0.824 [0.71, 0.92] |
| 5,000-15,000 | 262 | 10 | 0.635 [0.38, 0.86] |
| 15,000+ | 74 | 0 | not testable |

Discrimination survives at 0.81-0.82 in the two bands with enough positives, so
`access_gap` is carrying supply information rather than reading suburb size.

**The limitation that follows.** DPA has no positives above 15,000 residents and only ten
between 5,000 and 15,000, while the top 10 spans 6,148 to 25,021 with a median of 14,473.
The external check is therefore strongest exactly where the shortlist is not. It
establishes that the access measure works where it can be tested; it does not establish
that the ranking of large metropolitan suburbs is externally validated, and no dataset in
this project can.

---

### One factor at a time overstates robustness

The 32-run sweep moves one knob per run. That design cannot see interactions, so it
reports the best case: a suburb only has to survive 32 single deviations from the
baseline, never a combination of them.

`uncertainty.py --joint` draws every knob together, 400 times. The ranges are not new - each is the span the corresponding one-at-a-time family already sweeps, so the joint run
explores the same space and the only thing that changes is whether knobs move together.
Two deliberate exclusions: the `to 0` weight runs, because deleting a criterion is a
different study, and continuous travel cutoffs, because `zones_for_cutoff` requires whole
5-minute decay bands so 15/20/25 is the complete admissible set.

| Base rank | Suburb | Median | 95% interval | P(top 10) | P(rank 1) |
|---|---|---|---|---|---|
| 1 | Cronulla | 1 | [1, 20] | 0.94 | **0.55** |
| 4 | Avalon Beach | 3 | [1, 11] | **0.97** | 0.20 |
| 6 | Oran Park | 4 | [1, 12] | 0.96 | 0.08 |
| 7 | Glenmore Park | 6 | [1, 25] | 0.80 | 0.10 |
| 2 | Caringbah South | 5 | [1, 23] | 0.82 | 0.05 |
| 10 | Heathcote | 21 | [4, 74] | 0.18 | 0.00 |

Monte Carlo error at 400 draws is about ±2.5 points on a probability near 0.5, so
Cronulla's rank-1 share is 0.55 [0.50, 0.60].

**What this changes.** Under the one-at-a-time sweep Cronulla holds the top 10 in all 32
runs and no other suburb does. Under joint draws it holds rank 1 in 55% and the top 10 in
94%, while Avalon Beach holds the top 10 in 97% - a gap of 3 points against a standard
error of 1.5, so Avalon Beach is marginally the more robust top-10 member, not Cronulla.
Five suburbs hold rank 1 in some draw.

**The honest claim.** Cronulla is the modal winner and the single most likely rank 1, by a
clear margin over Avalon Beach at 20%. But rank 1 is not settled: it depends on a
combination of reasonable choices, no one of which is unreasonable on its own. A shortlist
of Cronulla, Avalon Beach, Oran Park and Glenmore Park is what the evidence supports; a
single recommended suburb is not.

This is a general property, not a defect specific to this project. A one-factor-at-a-time
sweep is the standard robustness check and it systematically flatters whatever the
baseline happens to be.

---

### Method choice: rank 1 is not formulation-independent

Every other family in the sweep varies a *parameter*. This one varies the *model*.

The shipped step 1 is enhanced two-step floating catchment (2SFCA): a suburb contributes
its whole population to every practice it can reach, so demand is counted once per
overlapping catchment. Three-step floating catchment (3SFCA, Wan, Zou and Sternberg 2012)
inserts a selection weight first - `G_kj = W(d_kj) / Σ_m W(d_km)` - so a suburb splits
itself across the practices it can reach, in proportion to how close each is. 3SFCA is the
stricter formulation: it removes a double-count that 2SFCA genuinely has.

Both conserve supply - `Σ R_j · pool_j` equals the number of reachable practices under
either, which is the check that this reallocates demand rather than changing how much
supply exists. Access correlates at Spearman 0.838.

**They disagree about the winner:**

| Suburb | 2SFCA | 3SFCA |
|---|---|---|
| Avalon Beach | 4 | **1** |
| Glenmore Park | 7 | **2** |
| Cronulla | **1** | 3 |
| Newport | 8 | 4 |
| St Clair | 3 | 5 |
| Oran Park | 6 | 6 |
| Cranebrook | 5 | 7 |
| Caringbah South | 2 | 8 |

Eight of ten top-10 members are common to both; Warriewood and Spring Farm enter under
3SFCA, Collaroy and Heathcote leave.

**What this does and does not cost the recommendation.** Cronulla stays in the top 10
under all 32 perturbations including this one - it is the only suburb that does. But
"Cronulla is rank 1" holds under 2SFCA and not under 3SFCA, and the honest claim is
therefore narrower: *Cronulla, Avalon Beach and Glenmore Park are the three strongest
candidates, and which of them leads depends on whether demand-side competition weighting
is applied.*

Before this family existed the sweep had 31 runs and none of them touched the
formulation, so the ranking had never been tested against the single largest modelling
choice in the project.

---

## Limitations

Five limitations are large enough to change a site decision. Each is stated below with its measured size and the direction it pushes the answer.

| # | Limitation | Measured size | Direction of error |
|---|---|---|---|
| 1 | Travel is straight-line, not road network | Croydon's access percentile swings 57.8 to 98.1 across plausible speeds | Optimistic; flatters coast, peninsula and anywhere across water |
| 2 | The `area` column is corrupted | 12 of 548 matched suburbs wrong by more than 2x | Maps only; no longer touches the score |
| 3 | Supply is March 2025, demand is the 2021 Census | ABS 2025 ERP implies 7.9% more people inside the study area | Understates demand, worst in greenfield suburbs |
| 4 | The headcount guard is blind on 123 eligible suburbs | 123 of 443, including Caringbah South at rank 2 | Ranks can be too high, never too low |
| 5 | The Nuvo exclusion list is unconfirmed | 145 suburbs in 10 catchments removed on it | Unknown; a wrong list is a large error either way |

### 1. Travel cost is straight-line at 35 km/h

This is the largest methodological weakness. `access_gap` carries 40% of the weight and rests entirely on great-circle distance converted to drive minutes by one constant.

The constant is measured, not assumed. A real Sydney drive graph gives a median effective straight-line speed of 47.6 km/h over 151,558 origin-destination pairs; that is divided by a stated congestion and access-delay factor of 1.36 to give 35.0 km/h (`accessibility.py:232-247`). The conventional 24 km/h was tested and rejected. Road-network routing is implemented and runnable in `accessibility.py`; it was abandoned on a timed extrapolation of roughly 46.5 minutes of Overpass download for the 2.24 sq deg study bbox against a 25 minute budget - a measured rejection, not an assumption.

No single constant models barriers. Repeating the calibration on a graph spanning Sydney Harbour costs about 17% of median effective speed and raises median circuity from 1.259 to 1.457. The clearest single defect is Dangar Island, ferry-only, credited with a nearest practice 1.544 km away.

Measured across the five speeds in the sweep (`python accessibility.py --sensitivity`):

| km/h | 20-min reach | Croydon pct | Heathcote pct | top 20 kept |
|---|---|---|---|---|
| 24.0 | 8.00 km | 57.8 | 12.8 | 16/20 |
| 30.0 | 10.00 km | 71.9 | 7.3 | 17/20 |
| **35.0** | **11.67 km** | **85.6** | **5.2** | **20/20** |
| 40.0 | 13.33 km | 98.1 | 7.2 | 17/20 |
| 47.6 | 15.87 km | 95.7 | 5.0 | 17/20 |

The qualitative conclusions survive the whole range - Croydon stays above median, Heathcote stays at the bottom. The point percentiles do not, and neither does a quarter of the top 20 at the extremes.

### 2. The `area` column in `scraped_data/eda_data.csv` is corrupted

Checked against the cached ABS SAL 2021 shapefile: 12 of the 548 matched suburbs are wrong by more than 2x. Sydney is recorded at 26.4694 km² against 2.94 actual. Six pairs of unrelated suburbs share a byte-identical value - Dolls Point/The Rocks, Berowra/Berowra Heights, Denistone/Denistone East, Bass Hill/Westmead, Northwood/Woolloomooloo, Tennyson/Tennyson Point - which is a copy-paste signature, and Prospect/Pemulwuy are swapped.

The column feeds nothing in the score. It still drives the combined map's senior-density axis and its sparse-suburb exclusion (`eda/plot_combined_pressure_density.py:671-707`), so map classes for the affected suburbs are wrong today. The fix is one step: recompute areas from `facility_cache/abs_sal_2021`, which is already cached and is already the geometry frame for accessibility, DPA and catchments.

### 3. Supply is March 2025; demand is the 2021 Census

The practice register is the NHSD extract dated 2025-03-07. Population, over-65 share and median household income are ABS 2021 Census. The two sides of every ratio are four years apart.

Measured, not estimated: the ABS 2025 SA2 ERP implies 5,121,215 people inside the study footprint against 4,746,859 in `eda_data.csv`, a 7.9% difference. Growth is not uniform, so the error is not uniform either. Greenfield suburbs in the top 20 - Oran Park (rank 6, 17,624 residents in 2021), Spring Farm (rank 14, 9,868), North Kellyville (rank 18, 17,401) - have grown fastest, so their demand is the most understated and their true rank is probably higher than shown. Practices that opened or closed after March 2025 are missing from the other side of the ratio.

### 4. The headcount guard cannot check 123 of the 443 eligible suburbs

The E2SFCA supply term counts one unit per practice (`S_j = 1`), because the register carries no practitioner-count field. A suburb served by few-but-large practices therefore reads as scarce. The only mitigation is the D5 guard, which lets a trustworthy scraped headcount demote a high `access_gap` - downward only, at half strength, full demotion at 640 people per GP and none at 1,040 (`site_score.py:188-195`). It fires on 159 of the 443 eligible suburbs.

It cannot fire at all on the 123 eligible suburbs whose `gp_data_status` is not `ok` (116 `no_data`, 7 `gp_count_unknown`), nor on Bayview and Beacon Hill, the two confirmed zeros - 125 unguardable suburbs in total. **Caringbah South ranks 2 and is one of them.** Its rank carries strictly less evidence than any `ok` suburb's and must not be presented as equivalent.

A weaker version of the same problem affects suburbs the guard does reach: 57 eligible `ok` suburbs carry `total_gp_is_floor`, meaning the headcount is a lower bound. A floor understates doctors, so `people_per_gp` is too high, adequacy too low, and the suburb is under-demoted. Five sit in the current top 30 - Cranebrook (5), Newport (8), Miranda (15), Dee Why (17), Botany (23). Read `gp_data_status` and `total_gp_is_floor` beside any rank.

The guard is load-bearing: turning it off replaces 4 of the baseline top 10 (Dee Why, Engadine, Freshwater and Mona Vale enter).

### 5. The Nuvo exclusion list is the operator's public web page, not a client-confirmed footprint

`NUVO_SITES` is taken from nuvohealth.com.au/medical-centres/, retrieved 2026-09-23: 18 GP centres, of which 15 map to ABS suburbs and 3 are precinct names (Green Square, Harold Park, Birkenhead) whose catchments the listed 15 already cover. Exclusion is at GP-catchment level, so this removes 145 suburbs across 10 catchments from contention.

Nothing in the repository can confirm it. Neither the clinic scrape nor the Geoscience Australia register records a brand owner - the superseded brand-string match found only 6 of the 18 centres. A public listing can also lag an opening or a closure. `site_score.py` stamps every output row with an unverified note for this reason. This is an independent analysis with no access to the operator, so the list cannot be confirmed from inside the project.

## What this model is not

**It is not a ranking of clinical need.** It ranks commercial suitability for a mixed-billing clinic, and the two point in different directions. A mixed-billing practice charges a gap fee, so the model rewards suburbs where households can absorb one. LISA on `site_score` makes the consequence explicit: the only significant cluster of low-scoring suburbs is in Western Sydney - Merrylands, Guildford, Fairfield, Bankstown, Mount Druitt, Liverpool - and it decomposes against the significant high-scoring Northern Beaches cluster like this:

| Criterion | High cluster (9 suburbs) | Low cluster (47 suburbs) | Share of the score gap |
|---|---|---|---|
| `access_gap` (weight 0.40) | 0.845 | 0.179 | 64% |
| `mixed_billing_fit` (weight 0.25) | 0.805 | 0.102 | 42% |

Median household income is $2,481 a week in the high cluster and $1,355 in the low one. The low cluster sits below the model's lower income knot, so those suburbs score near zero on billing fit however thin their GP supply is. Greystanes is the pattern in one row: $1,930 a week, `mixed_billing_fit` 0.670, and rank 169 on the commercial lens against 123 on the need lens. Stated plainly: the model is **not** claiming Western Sydney has enough GPs. It is claiming Western Sydney is a poor market for a clinic that charges a gap fee. Only the second claim is supported here. `community_need_score` applies the inverted lens and is reported for every suburb; the two lenses share only 7 of their top 20, so one must be named as primary before any decision.

**It is not a claim that anywhere has no GP.** Every ranked row means "too few practices for the population that must share them". Cronulla at rank 1 has 6 practices within 2 km, the nearest 104 m away. Even Heathcote at rank 10, the worst-served eligible suburb, has 9 practices within 3 km, the nearest 2.455 km.

**It does not model practice capacity.** Appointment availability is not in the data, and every practice is treated as an equivalent competitor. The score therefore under-rates dense areas where a seven-day mixed-billing clinic would be genuinely differentiated. The register is also not a census: nationally it lists 6,254 practices against roughly 7,000-7,135 accredited, missing an estimated 10-12%, and listing is voluntary.

**It is not validated end to end.** Validation covers one criterion, not the composite. `access_gap` (unguarded) discriminates the Department of Health DPA under-supply flag at AUC 0.813 [0.743, 0.879]; the guarded version is 0.779 [0.707, 0.846]. `site_score` itself reaches only 0.641 [0.576, 0.706], and the DPA label is thin - `DPA = Y` covers 79 of 680 suburbs and 2.79% of the population. `mixed_billing_fit`, a quarter of the weight, is checked against nothing: no local billing-behaviour data has been ingested. Medicare quarterly bulk-billing rates at SA3 remain the obvious missing check.

**It is not a shortlist with margin.** Under 31 perturbations across 9 families, exactly one suburb - Cronulla - stays in the top 10 under all of them. Caringbah South, Avalon Beach and Oran Park survive every run except those that zero a criterion's weight. And Caringbah South shares Cronulla's own GP catchment, so it is one opportunity with Cronulla, not a second one.

---

## Data sources

Everything the ranking uses is dated, licensed and re-fetchable. One LLM-sourced file survives, and it cannot reach a rank.

| Dataset | Provenance | Licence | Lands in |
|---|---|---|---|
| 2,089 NSW general practices | GA "National HealthDirect Health Facilities" layer 0, eCat `696d12c2-38c6-4afa-96b6-309a1ac9a50b`, NHSD extract 2025-03-07. 2,482 NSW rows filtered to `nhsd_service_type = 'General practice service'` | CC-BY 4.0 | `scraped_data/ga_general_practices_nsw.csv` |
| 1,832 NSW pharmacies | Same service, layer 2 | CC-BY 4.0 | `scraped_data/ga_pharmacies_nsw.csv` |
| 826 GP catchments | GPC 2023, Dept of Health, data.gov.au `cc26a27b-…`; ASGS 2021 aggregations built from MBS patient-flow data. Boundary-only, no population | CC-BY 2.5 AU | `facility_cache/gpc_2023/` → `suburb_gp_catchment.csv` |
| 1,328 DPA polygons | DPA (GPs) 2025a, Dept of Health, data.gov.au `7d889af7-…`, current as at March 2025 | CC-BY 2.5 AU | `facility_cache/dpa_gps_2025a/` → `suburb_dpa_status.csv` |
| ABS geography | SAL 2021 GDA2020 (104 MB) is the single suburb frame for accessibility, DPA and catchments; SA2 2021 plus SA2 ERP from the ABS Data API (2,454 SA2s) and the SA2→GPC correspondence build the demand ring and catchment populations | not recorded in repo | `facility_cache/` |
| Population, over-65 %, median incomes | ABS 2021 General Community Profile FeatureServer layer 5, queried per SAL by `pre-processing.ipynb` | not recorded in repo | `scraped_data/eda_data.csv` |
| `area` column | OSM Nominatim boundary polygons, same notebook. **Corrupted** - Sydney 26.47 km² against 2.94 actual. Feeds no live score, still feeds the combined map | ODbL, not stated in repo | same file |
| 1,306 scraped clinic rows | 70 prompts in `prompts/` pasted into an LLM, returned as the 70 CSVs in `gp_data/`. No source URLs retained, so not auditable and not reproducible | none | `scraped_data/gp_clinics_all.csv` |
| 716 OSM `place=suburb` polygons | Overpass via osmnx; misses 30 of the 680 project suburbs | ODbL, not stated in repo | `eda/geo_cache/` (gitignored) |

The scrape is not a ranking input. It becomes `total_gp`, which reaches the score only through the D5 headcount demotion guard, and only where `gp_data_status == 'ok'`.

## Running it

`facility_cache/` (423 MB) and `eda/geo_cache/` are gitignored; every CSV in `scraped_data/` is committed. Full regeneration, from the project root, in this order:

```bash
python rebuild_suburbs_final.py   # suburbs_final.csv + eda_data.csv (re-runs cell 8 of the notebook)
python fix_suburb_area.py         # repairs the area column AND suburb_areas.json, which cell 8 reads
python health_facilities.py       # ga_general_practices_nsw.csv, ga_pharmacies_nsw.csv
python gp_catchments.py           # suburb_gp_catchment.csv; downloads GPC (45 MB), SAL (104 MB), SA2 ERP
python dpa_status.py              # suburb_dpa_status.csv
python accessibility.py           # suburb_accessibility.csv - 680 rows, the E2SFCA surface
python eda/plot_combined_pressure_density.py  # combined_pressure_density.{png,html}; builds the gpkg
python build_suburb_facility_counts.py        # suburb_practice_counts.csv
python site_score.py              # site_scores_v2.csv (443 of 680 eligible), sensitivity_analysis.csv, final_shortlist_v2.csv
python validation.py              # validation.csv, lisa_site_score.csv, rank_bootstrap.csv
python eda/plot_rank_stability.py             # readme_rank_stability.png
python eda/plot_mixed_billing_choropleth.py   # mixed_billing_income_vs_supply.{png,html}
```

Four ordering constraints, all enforced by the code rather than by convention:

- `gp_catchments.py` is the only script that downloads the ABS SAL zip and writes `facility_cache/abs_sa2_erp.csv`. `dpa_status.py` and `accessibility.py` read them and exit with a `curl` line if they are missing, so it goes first.
- `accessibility.py` also needs the ABS SA2 2021 boundary zip, which nothing fetches for you. `--no-network` skips the road-network attempt; the shipped surface is straight-line either way.
- The polygon cache is circular: `build_suburb_facility_counts.py` reads `eda/geo_cache/osm_suburb_polygons_greater_sydney.gpkg`, and only the two map scripts create it. On a fresh clone a map script runs *before* it, not after.
- `site_score.py` calls `accessibility.load_suburbs()` and `load_outside_demand()`, so the SAL and SA2 caches are required even with every CSV committed. There is no cache-free short path.

With `scraped_data/` committed, re-scoring is `python site_score.py && python validation.py` plus the plot scripts. `requirements.txt` is wrong in both directions: add `pandas`, `numpy` and `branca`, used throughout but currently only transitive; drop `exa-py`, `python-dotenv` and `google-genai`, which no live `.py` imports now that the scrape chain is gone.

`eda/excluded_no_gp_data.csv` lost its generator when the v1 chain was deleted. The row set rebuilds in one line:

```python
pd.read_csv("scraped_data/suburbs_final.csv").query("gp_data_status != 'ok'")   # 247 rows
```

Its context columns are a left join of `suburb_accessibility.csv` (`practices_within_2km`, `nearest_practice_km`, `access_general`, `access_general_pct`) and `suburb_gp_catchment.csv` (`gpc_name`), plus `saturated_nearby = practices_within_2km >= 10` and `access_above_median = access_general_pct > 50`, sorted by population descending. That reproduces the shipped file value-for-value.
