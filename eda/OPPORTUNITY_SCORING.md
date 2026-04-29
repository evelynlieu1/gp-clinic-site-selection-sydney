### Final `opportunity_score` (in `scraped_data/eda_data.csv`)

For **every suburb** in `eda_data.csv`:

`opportunity_score = (1/3)×Norm(spatial_density) + (1/3)×Norm(median_household_income_weekly) + (1/3)×Norm(over_65_pct)`

- **Norm** = min–max to **[0, 1]** using **all suburbs** in the file (not only population ≥ 1000).
- **Higher raw value → higher Norm** for each of the three inputs.
- **Income:** weekly household income; `0` is treated as missing before scaling.
- Recomputed when you run `python eda/generate_insight_plots.py` or when you re-export from `pre-processing.ipynb`.

---

### MCDA opportunity score (EDA)

**Why min-max:** You cannot add incomparable units (e.g. income in dollars to % over 65).
Each criterion is scaled to **0–1** within the analysis cohort (population >= 1000).

**Scaling (within cohort):**

- **Higher is better** (income, population, % aged 65+):  
  `(x - min) / (max - min)`

- **Lower is better** (total GP count, area km²):  
  `(max - x) / (max - min)`

**Composite:** Weighted sum of the 0–1 scores. Higher = better site fit on that strategy.

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

**For written reports:** We include median household income to capture **financial viability** (mixed billing / ability to pay gap fees). For a **community-need** lens (access over profitability), use `weighted_score_community_need`, which rewards **lower** income instead.
