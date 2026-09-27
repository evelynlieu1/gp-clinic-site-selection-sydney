# Handover — what changed, what to check, what's next

A working document for review. Delete it once you've been through it; it is not part of
the deliverable.

Everything below is verifiable. Where a claim rests on a measurement, the command that
produces it is given. **Assume I got something wrong and go looking for it** — I got
several things wrong during this work and they are listed in section 4.

---

## 1. What was yours before I touched anything

These existed and I did not write them. They are the expensive part of the project and
the part I would defend hardest:

```
accessibility.py        1,422   E2SFCA surface, travel cost, demand ring
gp_catchments.py          509   Medicare GP catchment join
build_suburb_facility_counts.py  407
dpa_status.py             353   Distribution Priority Area join
health_facilities.py      338   practice and pharmacy register
gp_supply.py              296   three-way supply status gate
site_score.py           1,782   scoring and sensitivity  (I later modified this)
```

The data acquisition is the moat. Nothing I did improves on it.

---

## 2. What I changed, file by file

### `site_score.py` — modified (1,782 → 1,876 lines)

Seven changes. Each has tests.

**A. Headcount demotion guard (D5).** Dee Why ranked 2 while carrying 35 GPs against
23,354 residents. The E2SFCA supply term counts one unit per practice, so a suburb served
by few but large practices reads as under-supplied.

- New `headcount_adequacy()` — knots at P25 and P50 of people-per-GP (measured 640, 1042)
- Applied to `access_gap` only, multiplicatively, in `score()`
- Fires only where `gp_data_status == "ok"`; never on missing data
- Added as a sensitivity family including OFF
- `_test_headcount_guard` asserts it never promotes, never fires on untrusted data, and
  actually bites
- **Effect: Dee Why 2 → 19, Mona Vale 9 → 44, Freshwater 10 → 14**

**The thing to challenge here:** the guard uses `total_gp`, which came from the LLM scrape
this entire project discredited. I argued that "contradicting a high score" is a weaker
claim than "ranking on it" and so is defensible. That argument is not airtight. A reviewer
could reasonably say discredited data should not touch the ranking at all.

**B. Income knots derived rather than hardcoded.**

- New `income_knots_from_percentiles()`, `INCOME_KNOT_PERCENTILES = (15, 40, 80, 95)`
- `Inputs.income_knots` computed at load; `main()` seeds the whole sweep from it
- `_test_derived_income_knots` asserts scale invariance on real data

**C. Population floor 4,000 → 1,000.** Eligible went 341 → 507 (now 443 after the Nuvo
fix). `_test_smallness_trap` rewritten: Point Piper no longer fails the gate, so the test
now asserts the structural protection instead — it is pinned at zero on two independent
sub-scores, ceiling 0.50 against a 0.78 shortlist threshold.

**D. Nuvo exclusion list corrected.** Was 6 suburbs from a brand-string match. Their
public site listing has 18 GP centres. Now 15 (three are precinct names whose catchments
are already covered). **6 catchments → 10, 75 suburbs excluded → 145.** Manly and Bondi
Beach leave contention.

**E. Shortlist deduplicated by catchment.** Duplicates are marked, not dropped:
`Caringbah South — same catchment as Cronulla`. `_test_catchment_dedupe` enforces it.

**F. Spatial cluster columns — advisory only.** Explained in section 4, item 5.

**G. `_test_monotone_in_access` now tests `access_gap_unguarded`.** The guard
deliberately breaks that monotonicity, so testing the guarded column would have asserted
the guard does nothing.

### `validation.py` — new, 756 lines, all mine

The first inferential code in the project. Twelve functions, 37 doctests.

| Function | What it does |
|---|---|
| `auc`, `auc_interval` | AUC by Mann-Whitney identity, stratified percentile bootstrap |
| `knn_weights` | Row-standardised k-nearest-neighbour spatial weights |
| `morans_i`, `morans_i_test` | Global spatial autocorrelation, permutation null |
| `local_morans_i`, `lisa_quadrants` | Per-suburb decomposition and HH/HL/LH/LL labels |
| `benjamini_hochberg` | FDR control across 443 simultaneous tests |
| `lisa_test` | LISA with a conditional null |
| `spatial_clusters` | Connected components of significant HH suburbs |
| `bootstrap_ranks` | Rank intervals, blocked on catchment |
| `load_centroids` | Suburb centroids via `dpa_status.load_suburbs` |

### `README.md` — new, 261 lines, all mine

Leads with the diagnosis rather than the ranking. Includes the validation results and a
"What this model is not" section stating the commercial-vs-clinical-need distinction.

### `eda/plot_rank_stability.py` — new, 79 lines

One figure for the README, regenerated from `sensitivity_analysis.csv` so it can't go
stale silently. I did not embed your existing plots — both predate the current results.

---

## 3. The results, and how much to believe them

**External validation.** AUC against the Department of Health under-supply flag, which the
model has never seen:

| Measure | AUC | 95% CI |
|---|---|---|
| `access_gap` unguarded | 0.813 | [0.743, 0.879] |
| `access_gap` guarded | 0.779 | [0.707, 0.846] |
| `site_score` composite | 0.641 | [0.576, 0.706] |

**Be skeptical of this.** DPA-positive suburbs have a median population of 815 and a
median of **zero** practices within 2 km. It is calibrated for rural workforce
distribution, not metropolitan commercial siting. A high AUC is meaningful but the
benchmark is weak, and it is weak in a direction that flatters the model.

**Spatial structure.** Moran's I on `site_score` = 0.359, p = 0.001, stable from k=4
(0.397) to k=32 (0.232). The peninsula concentration is real.

**Consequence you should carry:** the 443 eligible suburbs are not 443 independent
observations. Any interval computed as though they were is too narrow.

**The equity finding.** The only significant low-score cluster is Western Sydney.
Decomposing it against the high cluster: `access_gap` is 64% of the gap, `mixed_billing_fit`
is 42%. Median income $2,481/week high cluster, $1,355 low. The low cluster's median sits
below the income knot, so those suburbs score near zero on billing fit regardless of GP
supply. **The model is not saying Western Sydney has enough GPs. It is saying it cannot
pay a gap fee.** Now stated in the README.

---

## 4. Things I got wrong, and corrected

Read this section hardest. It is the best guide to where else I may be wrong.

1. **I said the income knots came from 2026 fee levels.** The code's own comment at
   `site_score.py:139` said they were percentile-anchored from the start. Measured: the
   fix moved the income score by a mean of 0.0375, not the 0.3011 the framing implied.
   **I overstated a problem by 8x before checking.**

2. **I predicted dropping the population floor would reintroduce the Point Piper
   failure.** Measured: the best readmitted suburb scores 0.679 against Cronulla's 0.943.
   It wouldn't have. I lowered rather than dropped the floor for a different and smaller
   reason — 30-resident suburbs look unserious in output tables.

3. **I argued the rank bootstrap had to be spatially blocked or it would be badly
   over-confident.** Measured: blocking widens intervals by 7%. Negligible. Clustering
   matters for means, not for ranks.

4. **I called GP catchments "paperwork".** They are built from Medicare patient flow
   records — measured behaviour, not administrative convenience. That was careless and it
   misled the discussion about Collaroy.

5. **I claimed administrative boundaries disagreed with the real spatial structure.** At
   k=8 the LISA clusters split exactly along the catchment line (Pittwater vs Warringah).
   They agreed. My claim came from reading the significance list without checking
   connectivity. When I then tested the proposed union rule, cluster membership turned out
   to flip on `k` — separate at k≤12, together at k≥16 — so I did **not** build it, and
   the columns are advisory only.

6. **Two doctest errors of my own**: an AUC example I called a coin flip is 0.75, and my
   hand-computed Benjamini-Hochberg expectations were wrong. The code was right both
   times; my expected values weren't.

7. **I flagged the guard's lower DPA AUC as "worth investigating".** The confidence
   intervals overlap heavily. It is noise. The interval answered what the point estimate
   couldn't.

---

## 5. Verify it yourself

```bash
python site_score.py --test
```

Expect: `4 assert cases, 6 data-backed cases, 77 doctests`.

```bash
python validation.py
```

Expect 37 doctests to pass, then four sections of output.

Specific things worth checking by hand:

- **The model really is 57 lines.** Open `site_score.py` and read `mixed_billing_fit`,
  `log_ramp`, `operational_score`, `headcount_adequacy`, `access_gap_scores`. That is the
  whole model. Everything else is plumbing, tests and prose.
- **The Nuvo list.** Open nuvohealth.com.au/medical-centres/ and count. I read 18 GP
  centres and mapped 15 to ABS suburbs.
- **The guard knots.** 640 and 1042 are P25 and P50 of people-per-GP *in the cohort the
  guard is applied to*. That is mildly circular. Decide whether it bothers you.
- **`git log`.** Five commits, each message states what changed and what it measured.

---

## 6. The plan

**Do the extraction first.** Reasoning:

The correctness work is essentially done — three structural errors fixed, the Nuvo list
corrected, the shortlist deduplicated, external validation in place. What is blocking the
project now is not that it is wrong, it is that it is **unreadable**: 10,703 lines of
Python of which 43% is a superseded chain, and a 1,876-line scoring module wrapping 57
lines of arithmetic. Unreadable is indistinguishable from bad to anyone who doesn't have
a day to spend.

Census error propagation is worth doing, but it refines intervals on an answer nobody can
currently find. Sequence it after.

### Order

| # | Task | Effort | Why |
|---|---|---|---|
| 1 | Delete the `eda/` v1 chain (4,606 lines) | 1h | Recoverable from git forever. Removes the risk of someone quoting a disproven answer |
| 2 | Cut `site_score.py` docstrings ~70% | 3h | Same logic and tests; reasoning moves to the report where a reader will find it |
| 3 | Merge CAVEATS + METHODOLOGY into one report | 2h | Three documents, 1,034 lines, overlapping |
| 4 | Regenerate the two stale maps | 1h | Both predate the current top 10 |
| 5 | Census error propagation | 4h | The uncertainty that actually moves scores, unlike the rank bootstrap |
| 6 | Push to GitHub | 5m | Nothing is backed up off this machine |

Target after 1–3: roughly 2,500–3,000 lines of Python, one report, every line live.

### The stopping rule

Write this down before starting, because the open-items list will never empty on its own:

> The deliverable is a report recommending N catchments with stated confidence and stated
> limitations, plus a repository whose tests pass. When both exist, this project is done.

Road-network travel time, the `area` rebuild and the 2025 ERP swap are all **out of
scope** under that rule. They go in the limitations section as known and stated, which is
where they already are.

---

## 7. Still broken, for the record

- **Straight-line travel at 35 km/h.** Largest open weakness. Croydon's accessibility
  percentile swings 57.8 → 98.1 across plausible speeds, and the assumption is least
  accurate over water — which matters because the ranking is partly a map of peninsulas.
- **`area` column corrupted.** Sydney at 26.47 km² against 2.94 actual. Feeds nothing in
  the current score, only the superseded chain.
- **123 of 443 eligible suburbs have no trustworthy headcount**, so the guard cannot check
  them. Caringbah South ranks 2 and is one of them.
- **Supply is March 2025, demand is the 2021 Census.** Oran Park grew 46% across that gap.
- **Nothing is pushed.** Five commits, one machine.
