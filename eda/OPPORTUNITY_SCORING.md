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

---

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

---

### MCDA opportunity score (EDA)

**Why min-max:** You cannot add incomparable units (e.g. income in dollars to % over 65).
Each criterion is scaled to **0-1** within the analysis cohort (population >= 1000 **and**
`gp_data_status == "ok"` - see the status filter below).

**Scaling (within cohort):**

- **Higher is better** (income, population, % aged 65+):  
  `(x - min) / (max - min)`

- **Lower is better** (total GP count, area km²):  
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

---

### GP-data status filter (`gp_data_status`)

Clinic data was collected suburb by suburb, and **239 of 680** suburbs were
prompted but never returned a single clinic row. Their `total_gp` is `0` because nothing was
collected, not because there are no doctors - so any ranking that read `total_gp` as supply
promoted them straight to the top.

| `gp_data_status` | Suburbs | Meaning | Rankable on supply? |
|---|---|---|---|
| `ok` | 433 | in the scrape; at least one clinic reported a usable GP count | **Yes** |
| `gp_count_unknown` | 8 | clinics found, but every GP count was blank / `unknown` | No |
| `no_data` | 239 | never appeared in the scrape; `total_gp` is not a measurement | No |

**Rule:** `total_gp == 0` means "zero doctors" **only** when `gp_data_status == "ok"`.
Never filter, rank, threshold or colour on `total_gp == 0` alone.

Every **supply-based** output of `eda/generate_insight_plots.py` is computed on
`gp_data_status == "ok"` **and** `population >= 1000` - a cohort of **423** suburbs:

- `gp_per_1000` (`NaN` outside status `ok`, never a fake `0`)
- `sig_pop_lowgp`, `sig_65_lowgp`, `sig_density_lowgp`
- `opportunity_high_pop_low_gp.csv`, `opportunity_high_elderly_pct_low_gp.csv`,
  `opportunity_high_density_low_gp.csv`, `opportunity_all_signals.csv`
- `underprovided_suburbs.csv` (thresholds are percentiles of the cohort, so the excluded
  zeros no longer drag the 25th percentile down to `0.0`)
- `weighted_opportunity_scores.csv` (both MCDA lenses)

The only exception is `opportunity_score` above: no supply term, so it is still written for
all 680 rows. `gp_data_status`, `clinics_listed` and `total_gp_is_floor` are carried
into every exported CSV so the exclusions are auditable.

`total_gp_is_floor == True` means the total is a **lower bound** (a clinic reported `>=6`, or
a listed clinic never reported a count), so real supply is at least that high.

---

**For written reports:** We include median household income to capture **financial viability**
(mixed billing / ability to pay gap fees). For a **community-need** lens (access over
profitability), use `weighted_score_community_need`, which rewards **lower** income instead.

**Never present a low `total_gp` or a low `gp_per_1000` as an access finding.** It is a count
inside one polygon. Quote `access_general` / `practices_within_2km` beside it, and check
`saturated_nearby` before calling a suburb underserved.
