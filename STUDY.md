# Study path

Four sessions to go from "I don't know what this is" to able to defend it. Work through in
order; each one assumes the last. Roughly 10 hours total.

Delete this file before publishing - it is scaffolding, not part of the deliverable.

---

## Before you start: make it run

```bash
python site_score.py --test
```

Expect `tests OK: 4 assert cases, 6 data-backed cases, 77 doctests`. If that fails, stop
and fix it - nothing below is worth doing against a broken tree.

Then read the five value functions in `site_score.py`: `mixed_billing_fit`, `log_ramp`,
`operational_score`, `headcount_adequacy`, `access_gap_scores`.

**That is 57 lines of arithmetic, and it is the entire model.** Everything else in that
1,730-line file is plumbing, tests and prose. Knowing this early stops the size of the
repository intimidating you.

---

## The test for whether you own something

Ask these five of every method. If you can answer all five without looking, you own it.

1. What question does it answer?
2. What does it assume?
3. **Which assumption does this project violate, and how do I know?**
4. What would I use instead if I couldn't use this?
5. What does the number actually mean *in this data*?

Question 3 is the one that separates watching lectures from owning the material. For most
methods here you can answer it with a measured number - that is not an accident.

---

## Session 1 - What is this asking? (1 hour, no code)

Read `README.md` start to finish. Nothing else.

**Finish able to answer:**

- What decision is being made, and for whom?
- What would make the answer wrong?
- Why is the top 10 only seven opportunities?

**The idea:** the project's value is not the ranking. It is that the first ranking was
wrong, the errors were structural, and each is documented with the evidence that exposed
it. If you lead with "Cronulla" in an interview you have led with the weakest part.

---

## Session 2 - The one idea everything rests on (2-3 hours)

`accessibility.py`. **Read the module docstring first, all 200 lines.** It is the argument
of the file, not decoration.

Then, in this order:

1. `gaussian_weight` (:360) - how distance becomes a weight between 1 and 0
2. `decay_weights` - the same thing applied to a matrix
3. `e2sfca` - the two steps, and read the doctests before the body

**The mechanism in one paragraph.** Step 1 works practice by practice: add up all the
population that can reach it, each person discounted by distance, then divide the
practice's supply by that crowd. That gives `R_j`, "practices per weighted person
competing here". Step 2 works suburb by suburb: add up the `R_j` of every practice the
suburb can reach, discounted the same way. High means well served; low is the opportunity
signal.

**Finish able to answer:**

- Why did counting practices inside a suburb boundary make Croydon rank first?
- What is the demand ring for, and what breaks without it?
- Is 35 km/h an assumption or a measurement?

**Be ready for:** *"Cronulla has a GP practice 104 metres from your measurement point and
six within 2 km. And you call it the most under-supplied site in Greater Sydney."*

The answer is that `access_gap` is per capita and competition-adjusted. The claim is "too
few practices for the population that must share them", never "no GP here". Say this
before you are asked.

**Concede rather than defend:** `A_i` is a relative surface for ranking, not a clinical
capacity estimate.

---

## Session 3 - How the score is built (2-3 hours)

`site_score.py`, docstring lines 1-56, then the five value functions you already skimmed.

**The structural idea:** there are two different mechanisms doing two different jobs.

- **Hard gates** (population ≥ 1,000, catchment demand ≥ 25,000, Nuvo exclusion) are
 non-compensatory. Fail one and you get no rank at all - a perfect access score cannot
 rescue you. This is what stops a 30-person suburb winning.
- **The weighted sum** is compensatory. Inside the eligible set, a weak criterion can be
 offset by a strong one.

Then read `perturbations()` and `run_sensitivity()`. The sweep varies one knob per run.

**Finish able to answer:**

- Why is income an inverted U rather than "higher is better"?
- What does the headcount guard do, and why can it only move suburbs *down*?
- What is a robust core, and why is yours one suburb?

**Be ready for:** *"Your robust core has exactly one member out of 443. Is that a
robustness result, or a test tuned until one name survived?"*

And the harder one: *"Your rank 2 is Caringbah South. It has no GP headcount at all, and
it sits in the same GP catchment as your rank 1. What are you recommending?"*

---

## Session 4 - How we know it isn't nonsense (3-4 hours)

`validation.py`. Start with `auc()` and read its doctests before its body.

### This is where your coursework already covers you

| In the code | Your course |
|---|---|
| `auc()` - discrimination via ranks | **W5 V15** Wilcoxon Rank Sum. The AUC *is* U/(n₁n₀) |
| ROC / AUC as a performance measure | **W2 V05** Measures of performance |
| Bootstrap intervals | **W6 V17** Bootstrap |
| Permutation nulls | **W6 V16** Permutation tests |
| Benjamini - Hochberg | **W7 V18** Multiple testing |
| `nested_logistic` + LR test | **W10 V27** model selection, **W11 V29** logistic regression |

Genuinely new: Moran's I, LISA, block bootstrap. Two of the three are extensions of Week 6.

### The half-hour that converts watching into owning

Take ten suburbs. Compute the AUC by hand - rank them, sum the ranks of the DPA-positive
ones, subtract `n(n+1)/2`, divide by `n₁n₀`. Check against `auc()`. This does more than
re-watching the lecture.

### The thing you must raise before anyone asks

The DPA validation is weaker than the headline. Three independent findings:

1. A naive `-log(population)` baseline scores **0.849** against `access_gap`'s 0.813
2. DPA has **zero positives** above 15,000 residents; your top 10 spans 6,148-25,021
3. Clustering standard errors on catchment drops the access_gap z from **+6.53 to +1.65** - because 40 of the 79 DPA positives sit in three catchments

**And 50 of the 79 DPA-positive suburbs are ineligible.** The validation is largely
computed on a cohort excluded from the recommendation. This is the single fact most likely
to end an interview badly if someone finds it before you raise it.

The defensible version: *access_gap measures something real about GP supply, shown where
DPA has enough positives to test it - and DPA cannot test it where the shortlist lives.*

---

## The three claims, in their defensible form

Say these, not their stronger cousins.

| Don't say | Say |
|---|---|
| "Cronulla is the best site" | "Cronulla is the modal winner, rank 1 in 55% of joint draws. The shortlist is four names." |
| "Validated at AUC 0.813" | "0.813 on all 680, 0.719 on the 443 competing, and a population baseline scores 0.849. It works where DPA can test it." |
| "Survives all 32 perturbations" | "Survives all 32 one-at-a-time. Drawn jointly, rank 1 holds 55% of the time." |

---

## Known open defects

Do not be surprised by these; they came out of a systematic review of the codebase.

- **The guard ignores `total_gp_is_floor`** - loaded at :726, written at :1583, never read
 by `headcount_adequacy`. 57 eligible suburbs are affected.
- **Straight-line travel at 35 km/h.** Road routing is implemented but `site_score.py`
 builds its own cost matrices and cannot consume a routed surface. Overpass was also
 unreachable across four endpoints.
- **123 of 443 eligible suburbs have no trustworthy headcount**, so the guard cannot check
 them. Caringbah South ranks 2 and is one.
- **The model measures under-supply, not winnability.** It cannot tell a suburb that is
 thin because practices are turning patients away from one that is thin because the
 market never supported more.

---

## What to lead with

Not the ranking. Lead with the diagnosis: the first answer was wrong, the errors were
structural rather than cosmetic, and each one is documented with the evidence that exposed
it. Anyone can produce a ranked list. Finding that your own top 10 was an artefact of
recording missing data as zero supply, and proving it, is the harder and rarer thing.

The chain of reasoning in REASONING.md is what is worth defending, and it is more
impressive than any individual method in the repository.
