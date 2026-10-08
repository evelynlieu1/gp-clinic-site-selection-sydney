# Methods used

Every statistical and spatial method in this project: what it does, where it lives, why it
was chosen over the simpler alternative, and what it assumes.

Everything here is **non-parametric and resampling-based**. That was not a stylistic
choice - the observations are spatially autocorrelated (Moran's I = 0.359, p = 0.001), so
any method whose standard error assumes independent suburbs is wrong by an unknown amount.

---

## 1. Spatial accessibility - E2SFCA

**Enhanced two-step floating catchment area.** `accessibility.py`, `e2sfca()`

| | |
|---|---|
| Answers | How many practices can each suburb reach, after everyone else competing for them? |
| Step 1 | `R_j = S_j / Σ_k P_k · W(d_kj)` - supply per weighted person competing at practice *j* |
| Step 2 | `A_i = Σ_j R_j · W(d_ij)` - sum of those ratios across reachable practices |
| Chosen over | Practices inside the suburb boundary; practices within *X* km; nearest-practice distance; a plain gravity model |
| Assumes | Travel cost is a reasonable proxy for access; one unit of supply per practice |

**Why not the simpler version.** Counting practices inside a polygon makes supply a
property of the administrative boundary rather than of access. Croydon ranked first with
one practice inside and 27 within 2 km.

**3SFCA variant** (`method="3sfca"`) adds a demand-side selection weight
`G_kj = W(d_kj) / Σ_m W(d_km)`, so a suburb splits across reachable practices instead of
contributing its whole population to each. It is the stricter formulation; 2SFCA
double-counts demand across overlapping catchments. Carried as a sensitivity family
because the two disagree about rank 1.

**Distance decay.** Zero-anchored Gaussian,
`W(d) = (exp(-½(d/d₀)²) - exp(-½)) / (1 - exp(-½))`, evaluated at 5-minute zone midpoints.
Forces `W(0) = 1` and `W(d₀) = 0` so the weight falls continuously to nothing at the cutoff
rather than stepping off a cliff. Senior zones come out at 0.965 / 0.701 / 0.254, which is
the Luo and Qi (2009) published set to three decimal places.

**Supply conservation** is asserted on every run: `Σ R_j · pool_j` must equal the number of
practices with a non-empty demand pool, under either formulation.

---

## 2. Value functions, not standardisation

`site_score.py`

Each criterion maps to [0,1] through its own function rather than a shared z-score.

| Criterion | Function | Shape |
|---|---|---|
| `access_gap` | `1 - rank_pct(access)` | Ordinal |
| `demand_scale` | Log ramp, 0 at 5,000 → 1 at 20,000 | Log, saturating |
| `mixed_billing_fit` | Piecewise-linear inverted U | Non-monotone |
| `operational` | `(1-s)·pharmacy + s·DPA` | Weighted mean |

**Why not z-scores.** Three reasons, in order of severity.

1. **Two of the four criteria are not monotone.** A z-score can only encode "more is
 better". It structurally cannot express an optimum in the middle, and income has one - mixed billing needs households who can absorb a gap fee and are not already buying full
 private care.
2. **Z-scores are mutually coupled.** Every suburb's score depends on the mean and standard
 deviation of all of them, so one outlier moves everyone. When the eligible cohort
 changed from 341 to 507 suburbs mid-analysis, no sub-score moved; under standardisation
 every one would have.
3. **Z-scores erase thresholds.** The demand saturation point and the income plateau encode
 domain claims. Standardisation dissolves them into "relative to average".

**What z-scores do better, stated:** with sd forced to 1, nominal weight equals effective
weight exactly. Here it does not - `access_gap` carries 40% nominal but 32.6% of the real
discriminating power. That drift is measured by `report_criterion_influence()` on every
run rather than left implicit.

---

## 3. Compensatory and non-compensatory stages

Two different mechanisms, deliberately:

- **Hard gates** (population ≥ 1,000; catchment demand ≥ 25,000; existing-clinic
 exclusion). Non-compensatory - fail one and you get no rank at all, however strong the
 rest. This is what stops a 1,334-person enclave winning.
- **Weighted sum** over the eligible set. Compensatory - a weak criterion can be offset.

Conjunctive screening followed by compensatory aggregation is a standard MCDA pattern, and
the split is the answer to "why doesn't a tiny suburb with perfect access win?"

---

## 4. Discrimination - AUC via Mann-Whitney

`validation.py`, `auc()`, `auc_interval()`, `stratified_auc()`

`AUC = (Σ ranks of positives - n₁(n₁+1)/2) / (n₁ · n₀)`. This **is** the Wilcoxon rank-sum
statistic, normalised.

**Why not accuracy.** 79 positives in 680 means "always no" scores 88%.

**Interval:** percentile bootstrap, **stratified by class** - with 79 positives an
unstratified resample can empty the class entirely.

**Stratified by population band**, because DPA-positive suburbs have a median population of
815 against 5,643 for the rest. Without holding size fixed, the AUC substantially measures
"is this a small suburb" - a bare `-log(population)` baseline scores 0.849 against
`access_gap`'s 0.813.

---

## 5. Nested logistic models

`validation.py`, `nested_logistic()`

`DPA ~ log(population)` against `DPA ~ log(population) + access_gap`, compared by
**likelihood ratio test**: `LR = 2(ℓ_full - ℓ_reduced)`, chi-square on 1 df.

**Why:** the AUC comparison prints two numbers side by side and leaves the reader to
eyeball the difference. This answers it directly, with an interpretable coefficient.

**Cluster-robust standard errors** on GP catchment, because the LR test assumes independent
observations and 40 of the 79 DPA positives sit in three catchments. The access_gap z falls
from +6.53 to +1.65 once that is accounted for.

**Caveat recorded:** cluster-robust SEs are unreliable below roughly 40-50 clusters. There
are 46.

---

## 6. Spatial autocorrelation - Moran's I and LISA

`validation.py`, `morans_i_test()`, `lisa_test()`, `spatial_clusters()`

**Global Moran's I** with row-standardised k-nearest-neighbour weights, against a
**permutation null** (999 draws) rather than the analytic normal approximation - the score
is bounded on [0,1] and visibly non-normal, which is exactly where the closed-form variance
misleads.

**Local Moran's I (LISA)** with a **conditional** null: each suburb keeps its own value
while its neighbours are resampled. That is the correct null for a local statistic.

**Benjamini - Hochberg FDR** across all 443 simultaneous tests. At α = 0.05 roughly 22
"significant" clusters would appear in pure noise; the correction took 124 down to 58.

**Decomposition identity verified:** the mean of the local statistics equals the global
statistic to six decimal places.

---

## 7. Resampling

| Method | Where | Resamples | Answers |
|---|---|---|---|
| Stratified bootstrap | `auc_interval` | Observations within class | Interval on AUC |
| Block bootstrap | `bootstrap_ranks` | GP catchments | Interval on rank |
| Empirical bootstrap | `uncertainty.run` | 43 observed growth ratios | Effect of the 2021/2025 vintage gap |
| Joint Monte Carlo | `uncertainty.run_joint` | Every model knob together | Does rank 1 survive interactions? |

**Block bootstrap** resamples catchments rather than suburbs because Moran's I shows
suburbs are not independent. **Measured, against expectation:** blocking widens the top-10
intervals by only 7% - a rank depends on *how many* suburbs outscore you, and that count
barely notices whether correlated neighbours arrive together.

**The joint sweep** uses exactly the ranges the one-at-a-time families already sweep, so
the result cannot be attributed to widened ranges. One-factor-at-a-time sensitivity cannot
see interactions and systematically flatters the baseline: Cronulla holds rank 1 in 32 of
32 single perturbations, and in 55% of joint draws.

---

## 8. Sensitivity analysis as the deliverable

`site_score.py`, `perturbations()`, `run_sensitivity()`

32 perturbations across 10 families: weights (±50% and to zero), income knots, travel
cutoff, viability floor, senior multiple, DPA share, speed constant, access scaling,
headcount guard, and the 2SFCA/3SFCA formulation.

**Why this instead of deriving the weights.** AHP produces weights that look derived while
still encoding the analyst's judgement. Regression needs an outcome variable, and there is
none - no revenue, no patient volumes, no success labels. So the weights stay asserted and
the *claim* changes: report what survives, not what wins.

---

## 9. Geometric and data-handling methods

| Method | Where | Purpose |
|---|---|---|
| Equal-area projection (EPSG:3577) | throughout | Area and distance in metres, not degrees |
| Polygon overlay with a sliver floor | `gp_catchments.py` | Catchment assignment; 484 of 1,241 pairs were boundary artefacts |
| IoU ≥ 0.90 polygon matching | growth calibration | Name matching alone gave a 109× false "growth" for Chullora |
| Haversine distance matrix | `accessibility.py` | 680 × 2,089 travel costs |
| Three-way status gate | `gp_supply.py` | `ok` / `gp_count_unknown` / `no_data`, so absence of evidence is not evidence of scarcity |
| Demand ring | `accessibility.py` | Edge correction; without it two outside practices supplied 72% of one suburb's accessibility |

---

## 10. What was considered and rejected

| Method | Why not |
|---|---|
| t-test / Welch | Independence measured false. Normality and equal variance both hold; independence does not, and it is the one nobody checks |
| ANOVA | Same independence problem, plus the null is known false - catchments are defined geographically |
| Levene's test | A precondition test for tests that should not be run. It passes (p = 0.63), which is reassurance about the wrong thing |
| Chi-square independence | Expected cell counts below 5 in the upper population bands, and the cells are filled by spatially clustered units |
| Pearson with p-values | At n = 443 everything is significant; magnitudes are the finding. Spearman is used, reported without p-values |
| OLS / any regression on the score | No outcome variable exists. Regressing `site_score` on its own inputs recovers the weights by construction |
| AHP for weights | Launders judgement through a matrix |
| DeLong's test | Assumes independent subjects, so its p-value is optimistic here |

---

## Assumptions carried, in one place

1. Travel cost is straight-line distance at 35 km/h - a measured conversion from 151,558
 origin-destination pairs, median circuity 1.259, but not road routing.
2. One unit of supply per practice. No practitioner-count field exists in the register.
3. Demand is the 2021 Census; supply is the March 2025 register.
4. The income band is calibrated to percentiles of the observed distribution, so it is
 invariant to a uniform rescale but not to a change in the shape of the distribution.
5. The existing-clinic list comes from the operator's public page and is unverified.
