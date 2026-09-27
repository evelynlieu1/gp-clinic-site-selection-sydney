# Things to bear in mind before using this analysis

Last verified: 2026-08-19, against 680 suburbs in `scraped_data/eda_data.csv`.

This file records what the numbers in this repo can and cannot support. Read the first two
sections before quoting any figure to anyone.

---

## 1. The one concept everything depends on: `gp_data_status`

GP supply was gathered by pasting 70 prompts into an LLM chat and asking it to browse the web
(`prompts/chunk_*.txt`). It came back for some suburbs and not others. The original pipeline
recorded "no data came back" as `total_gp = 0`, and because pressure is
`population / (total_gp + 1)`, those suburbs scored as maximum pressure and dominated every
shortlist and map. **Seven of the ten original final recommendations were suburbs that had never
been scraped at all.**

Every row now carries a status. Treat it as the gate on every supply claim:

| `gp_data_status` | Count | What `total_gp` means | Safe to rank? |
|---|---|---|---|
| `ok` | 433 | Observed supply | **Yes** |
| `gp_count_unknown` | 8 | Clinics exist, every doctor count was blank | No |
| `no_data` | 239 | Suburb was asked for, but no clinic record came back | No |

**Rules:**

- Never filter, rank, threshold or colour on `total_gp == 0` alone. It means "zero doctors" only
  when `gp_data_status == "ok"`.
- Use `gp_supply.rankable_mask(df)` to select the analysable cohort.
- `gp_supply.require_status_column(df, path)` raises if you load a stale CSV that predates this
  work - leave those calls in place.
- `total_gp == 0` **with** status `ok` means the scrape explicitly reported no clinic in the
  suburb. That is a *confirmed* zero and the strongest supply-gap signal in the dataset. Exactly
  two suburbs qualify: **Bayview** and **Beacon Hill**.

The 247 non-`ok` suburbs are listed in `eda/excluded_no_gp_data.csv`, population-descending.
**That file is a re-scrape work list, not a list of rejects.**

---

## 1.5 Two findings that change how to read everything below

Added after checking this project against the national GP practice register (Geoscience Australia,
"National HealthDirect Health Facilities", NHSD-derived, CC-BY 4.0). Verified figures: layer 0
returns **2,482 NSW records, of which 2,089 are strictly `General practice service`** - the other
393 (15.8%) are nurse-led clinics, maternal/child health, skin-cancer clinics and similar, so
counting the whole layer as "GP clinics" overstates supply by about a sixth.

Current data record: eCat `696d12c2-38c6-4afa-96b6-309a1ac9a50b` (eCatId 149331), NHSD extract
**March 2025**, released November 2025. Queryable at
`https://services.ga.gov.au/gis/rest/services/National_HealthDirect_Health_Facilities/MapServer/0/query`
(capabilities `Map,Query,Data`, geoJSON, pagination, `maxRecordCount` 2000). Note the older
record `1d95ca95-…` is the **WMS service** record and still carries March-2024 lineage - do not
mistake it for the current data.

### 1.5.1 Suburb boundaries are the wrong unit for a supply gap

This is the most important finding in this file, and it undercuts the core metric.

Of the held-out suburbs with zero practices inside their own boundary, **83.3% have at least one
general practice within 1.5 km**, and about 90% of the population in them does. Only 18 suburbs -
**5,353 people in total**, all rural-fringe localities like Cattai, Maroota and Great Mackerel
Beach - have no practice within 3 km.

Patients do not stop at the suburb line. `population / (total_gp + 1)` computed per suburb
measures *which side of an administrative boundary a clinic sits on*, not access. The zeros are
mostly sub-locality splitting: **Caringbah South 0 but Caringbah 8; Lane Cove North 0 but Lane
Cove 6; Killara 0 but East Killara 1; Narrabeen 0 but North Narrabeen 1.**

**Recommendation: count practices within a radius (1.5-2 km) of the suburb centroid or population
centre, not inside the polygon.** Any suburb-polygon supply gap should be treated as unproven
until it survives a catchment test. Caveat: a radius count double-counts practices across
neighbouring suburbs (roughly 10× at 2 km), so it works as a *screen* for "is this actually
under-served", not as a denominator for a people-per-practice rate.

### 1.5.2 Both this project and the national register are incomplete, in different directions

Neither source is ground truth. Use them against each other:

- Correlation between our `clinics_listed` and the register's practice count per suburb is
  **Pearson 0.82-0.84** - same signal, meaningfully different values.
- **We undercount:** Beverly Hills (a current recommendation) shows **1** clinic in our data
  against **4** real general practices in the register.
- **The register undercounts too:** it is missing `Croydon Medical Centre`, ~20 years in
  operation, in the very suburb ranked #1. Nationally the register holds **6,254** practices
  against roughly **7,000-7,135** accredited Australian general practices, implying it misses
  **10-12%**. Listing on NHSD is voluntary/self-registered.
- **54 of our `ok` suburbs** hold 69 clinics that the register has none of.

So the register is much better on *provenance* than the LLM scrape, but it is not a drop-in
replacement, and it still cannot give `total_gp` - it has no practitioner-count field at all.

---

## 2. Hard blockers - do not pick a site before these are settled

### 2.1 The shortlist is four names, and the held-out pile is not the answer either

`eda/final_top10_clinical_intensity.csv` holds **4 rows, not 10**. The filename is now wrong.

| # | Suburb | People/GP | Income/wk | Basis |
|---|---|---|---|---|
| 1 | Croydon | 5,378 | $2,157 | one clinic reporting exactly 1 GP |
| 2 | Greystanes | 5,878 | $1,930 | **floor** - 3 of its 4 clinics reported no count |
| 3 | Beverly Hills | 5,242 | $1,862 | one clinic reporting exactly 1 GP |
| 4 | Beacon Hill | 7,814 | $2,937 | confirmed zero clinics |

Eight held-out suburbs match the mixed-billing profile on demographics (population ≥ 7,000 and
income ≥ $1,800) - Caringbah South (13,168 / $2,721), Lane Cove North (11,773), Killara (10,620 /
$2,802), Vaucluse (9,510 / $3,418), Wollstonecraft, Cromer, Lilyfield, Bonnyrigg Heights - plus
Narrabeen (8,255 at 31.1% aged 65+). Roughly 26 held-out suburbs would have cleared the pressure
threshold on raw arithmetic.

**But do not read those as under-served - see §1.5. Checked against the national GP practice
register, most of them genuinely have no practice inside the suburb boundary and are still
comfortably served from the adjoining suburb.** Caringbah South has 15 general practices within
1.5 km. Lane Cove North has 31 within 2 km. They are boundary artefacts, not opportunities.

### 2.2 The top MCDA "opportunity" is Nuvo's own existing clinic

There is **no existing-footprint or cannibalisation exclusion anywhere in the pipeline.** All six
Nuvo sites present in the clinic data are scored as candidate greenfield locations:

| Suburb | Nuvo GPs listed | Rank in `weighted_opportunity_scores.csv` |
|---|---|---|
| **Longueville** | 8 | **1 of 423** |
| Epping | 10 | 331 |
| Five Dock | 7 | 368 |
| Maroubra | 10 | 382 |
| Neutral Bay | 4 | 413 |
| Leichhardt | 13 | 421 |

Five of the six correctly rank low. Longueville ranking **first** is the tell for §3.2 below: at
3.78 GPs per 1,000 residents it is one of the *best*-supplied suburbs in Sydney, on a population
of 2,116.

**The six above are only the clinics whose name contains "Nuvo" - the real footprint is larger.**
Cross-checking against the national register suggests roughly **23 Sydney clinics** in the group,
most trading under non-Nuvo brands, so a name match misses the majority of them. **Get the
authoritative current-site list from the client** and exclude on address or coordinates, not on a
brand string, before circulating any ranking.

### 2.3 Rank 2 probably does not survive scrutiny

75 of the 433 rankable suburbs (17%) have `total_gp_is_floor = True`, meaning at least one clinic
reported no usable doctor count, so real supply is **higher** and the apparent shortage is
**overstated**. This lands directly on the shortlist:

- **Greystanes** - 4 clinics, 3 with no count. Impute the observed median of 3 GPs/clinic and
  pressure falls **5,878 → ~1,809**, well below the 5,000 threshold.
- **Belmore** - 3 clinics, 1 with a count. Falls **6,891 → ~1,723** on the same imputation.

Always read `total_gp_is_floor` alongside any pressure figure.

### 2.4 The pressure threshold is now effectively a P99 filter

`HIGH_PRESSURE_THRESHOLD = 5000` was calibrated against the buggy 35-suburb pool. Against real
observed supply it clears only **5 of 433** suburbs, because 5,000 sits at the 99th percentile:

| Percentile of people-per-GP (`ok` cohort) | Value |
|---|---|
| P90 | 2,043 |
| P95 | 2,796 |
| P99 | 5,163 |

`MAX_SHORTLIST = 50` and `FINAL_TOP_N = 10` are both now unreachable. Either lower the threshold
or switch to a percentile of the rankable cohort so it self-calibrates as data improves.

---

## 3. Data limits that remain

### 3.1 The supply side is unverified LLM browsing output

This is the weakest link and everything else rests on it. No source URLs were retained, so no
count is auditable and the scrape is not reproducible. Two specific symptoms:

- **26% of usable counts are exactly 1** (304 of 1,186), against a median of 3 and mean of 4.39
  GPs per clinic. Many are names like "… Medical Centre" / "… Family Medical Practice", where a
  single GP is implausible - and these are flagged `is_floor = False`, i.e. *exact*. Both Croydon
  and Beverly Hills rest on one such row. Consider treating a count of 1 attached to a
  "Centre/Practice/Clinic" name as a lower bound.
- **Suburb attribution errors move supply between neighbours.** Example: `Lane Cove Family Medical
  Practice` is recorded against **Riverview**. Errors of this kind cut both ways.

### 3.2 The MCDA supply criterion is a head count, so it rewards smallness

`weighted_opportunity_scores.csv` puts weight 0.35 on *lower `total_gp`* - an absolute count, not
a rate - plus 0.05 on smaller area and 0.25 on higher income, against only 0.15 on population.
Population is min-maxed across a range dominated by 30,000-plus suburbs, so a small suburb barely
registers on it. The result ranks hamlets and wealthy enclaves: top-20 median population 4,119 vs
8,126 for the cohort. **Rank on `gp_per_1000` or people-per-GP, not head count**, or this will keep
surfacing places too small to fund a 7-day multidisciplinary clinic.

Related: `weighted_score_mixed_billing` rewards *higher* income and
`weighted_score_community_need` rewards *lower*. They are different strategies and produce
near-disjoint lists. Pick one as primary and say so.

### 3.3 Income clears the floor; market behaviour may not

The $1,800/week mixed-billing cutoff is a blunt proxy for gap-fee tolerance, and the survivors
clear it narrowly - Beverly Hills by 3.4%, Greystanes by 7%. Raise the floor to $1,900 and half
the list disappears. Greystanes ($1,930, Cumberland LGA) and Belmore ($1,456,
Canterbury-Bankstown) sit in near-universal bulk-billing markets regardless of what the median
income says. There is no robustness margin in a 4-name shortlist.

### 3.4 Coverage gaps in the maps

- **30 suburbs have no OSM polygon** and are silently absent from every map, including Chatswood,
  Hornsby, Liverpool, Campbelltown, Manly, Camden, Mona Vale and Lane Cove North. The scripts
  print the count; the map still looks complete.
- **68 sparse suburbs** (< 120 people/km²) are excluded from the combined map so the colour scale
  is not skewed by industrial and acreage pockets.
- `eda/geo_cache/` is gitignored, so on a fresh clone the map scripts hit the Overpass API and may
  get different polygons. The map PNGs are only reproducible on a machine that already has the
  cache.
- **33 of the 179 unranked suburbs sit in the top senior-density tercile** - the combined map
  calls this out as a blind spot. It is the highest-value slice of the re-scrape list.

### 3.5 The `area` column is corrupted, and every density figure inherits it

Found 2026-08-19 by checking against ABS SAL 2021 geometry (now cached in `facility_cache/`).

- **12 of 548 matched suburbs are wrong by more than 2x**, 21 by more than 1.5x, 61 by more
  than 1.2x.
- Worst cases: **Sydney recorded as 26.47 km2 against an actual 2.94 (9.0x too big)**;
  Berowra Waters 14.61 vs 1.68 (8.7x); Waterfall 2.36 vs 17.58 (7.5x too *small*);
  Pemulwuy 3.71 vs 14.98.
- **Six pairs share a byte-identical value**, which is a copy-paste signature rather than a
  measurement error: Dolls Point / The Rocks (0.2066), Woolloomooloo / Northwood (0.3769),
  Tennyson / Tennyson Point (0.4488), Denistone / Denistone East (0.6976), Bass Hill /
  Westmead (2.9709), Berowra / Berowra Heights (7.9626).

Because `spatial_density = population / area`, this propagates into the MCDA density term, the
priority map's senior-density axis, that map's "sparse suburb" exclusion, and
`eda/opportunity_high_density_low_gp.csv`. Sydney's density is understated ninefold.

**Fix:** replace the column from the cached ABS SAL 2021 shapefile. Until then, treat any density
or per-km2 figure as unreliable for the suburbs above.

### 3.6 Other data notes

- `area` comes from OSM boundary polygons projected to EPSG:7856, picking the *smallest* matching
  polygon. Sound in general, but a same-named park or locality could occasionally win.
- `affordability_index` is household ÷ personal income - effectively earners per household, not
  affordability. The name is misleading.
- `gp_density` is people-per-GP, not a density. It is now `NaN` for all 247 non-`ok` suburbs; if
  you see a value there, your CSV is stale.

### 3.7 Where to get better data

All checked and current as of August 2026. **NSW government publishes no GP location data** - its
`NSW_FOI_Health_Facilities` service carries only Ambulance (316) and Hospital (306) layers, and
Data.NSW has no primary-care equivalent. Everything usable is Commonwealth.

| Need | Source | Geography | Verdict |
|---|---|---|---|
| Practice inventory + coordinates | GA "National HealthDirect Health Facilities" layer 0 (see §1.5) | Points | **Adopt.** CC-BY 4.0, bulk CSV available. Filter `nhsd_service_type='General practice service'` |
| Pharmacy co-location (brief wanted this) | Same service, **layer 2** - 1,832 NSW | Points | **Adopt.** Discriminates well: 57.4% of suburbs non-zero, median 2, range 0-33. Count by radius, not suburb name |
| GP headcount / FTE | Dept of Health workforce data (HeaDS UPP; NHWDS) | SA3-scale and coarser | Usable but coarse - see below |
| Catchment boundaries | GP Catchments (GPC) 2023 | 826 catchments | **Boundaries only** - 9 fields, no workforce measures. CC-BY 2.5 AU |
| DPA status (brief wanted this) | `dpa_gps_2025a.zip`, 1,328 polygons over 824 catchments, as at March 2025 | SA1-built, GP-catchment reported | Real bulk download, but see below |
| Bulk-billing / demand proxy | Medicare quarterly statistics | SA3 (44 of 45 Sydney SA3s match) | Usable |
| Pathology centres (brief wanted this) | - | - | **No open dataset exists.** Drop it |
| PHIDU social health atlases | PHIDU | LGA / PHN only | **CC BY-NC-SA - non-commercial.** Do not use for a client engagement |

Two warnings on the wanted-but-missing variables:

- **DPA barely discriminates inside Sydney.** `DPA=Y` covers 79 of 680 suburbs (11.6%) but only
  **2.8% of the population**, concentrated in the Hornsby-north and Richmond-Windsor clusters.
  Useful as a binary recruitment-cost flag on those, not as a Sydney-wide criterion. Also note the
  widely repeated claim that "MM1 is automatically non-DPA" is **false** - 27 fully-MM1 catchments
  are entirely `DPA=Y`, including Richmond-Windsor.
- **The headcount geography mismatch is severe.** GP catchments have a median population of
  121,917 and median area **55.93 km²**, against a median suburb of **2.70 km²** - roughly 14
  suburbs per catchment. Any GP-per-area figure from that geography cannot resolve between
  neighbouring suburbs. Note also that "GP Practice Workforce Statistics" has **ceased**
  publication.

---

## 4. How to run it

From the **project root**, in this order. Later steps read what earlier ones write.

```bash
python rebuild_suburbs_final.py              # merge -> suburbs_final.csv + eda_data.csv
python site_score.py                         # v2 scores, sensitivity sweep, shortlist
python validation.py                         # AUC vs DPA, Moran's I, LISA, rank bootstrap
python eda/plot_rank_stability.py            # README figure
python eda/plot_combined_pressure_density.py # merged bivariate map; also builds the
                                             # gitignored polygon cache that
                                             # build_suburb_facility_counts.py needs
python eda/plot_mixed_billing_choropleth.py  # income x supply map
```

The v1 chain that used to sit between the first and last commands here
(`generate_insight_plots.py` and Steps A/B/C) was deleted on 2026-09-23. It produced the
rankings this document exists to disown - Croydon at #1, a score that rewarded small
suburbs. Recover it with `git checkout 5fe4d38 -- eda/` if you ever need to reproduce
what was wrong.

`requirements.txt` omits `pandas`, `numpy` and `branca`, which are used throughout and currently
resolve only as transitive dependencies. Add them.

Shared logic lives in `gp_supply.py` (status semantics, count parsing) and
`site_score.py` (module-level constants, each with a `#:` comment). Change thresholds there, not in the
step scripts.

---

## 5. Known minor issues, not yet fixed

- The two folium HTML files change on **every** run (folium emits random element ids), producing
  permanent git diff noise. The paired PNGs are byte-identical.
- A *negative* `total_gp` on an `ok` row would still be clipped to 0 rather than treated as
  missing. Unreachable today - `parse_gp_count` maps negatives to `None` - but the guard is
  one-sided.
- `EXCLUDED_SUBURBS` in `pre-processing.ipynb` is dead code - all 19 entries are no-ops, because
  the source JSONs were pre-filtered to 680 by hand. The real exclusion step is undocumented.
- `eda/opportunity_high_elderly_pct_low_gp.csv` is named "low_gp" but its z-score composite mixes
  demand and supply, so it admits above-median-supply suburbs.
- `cache/` (26 MB of regenerable Overpass responses) and `eda/plots/` (20 MB) are committed -
  about 46 MB of the 50 MB repo is regenerable output. `cache/` should probably be gitignored.
- There is no README.

---

## 6. Repo state

Nothing has been committed. The two retired map scripts are **staged as deletions** while every
fix is **unstaged**, so a bare `git commit` right now would capture the deletions and none of the
work. Stage deliberately.

New: `gp_supply.py`, `eda/excluded_no_gp_data.csv`.

`eda/excluded_no_gp_data.csv` lost its generator when the v1 chain was deleted, but it is
not stranded - it is exactly the 247 suburbs whose `gp_data_status` is not `ok`, and
rebuilds in one line:

```python
pd.read_csv("scraped_data/suburbs_final.csv").query("gp_data_status != 'ok'")
```

Retired and replaced by `eda/plot_combined_pressure_density.py`:
`eda/plot_pressure_choropleth.py`, `eda/plot_senior_density_choropleth.py`, and their four
outputs.

---

## 7. The short version

The pipeline mechanics are sound and every ranked output is verified free of unmeasured suburbs.
But the shortlist rests on **433 of 680 suburbs**, two of its four names rest on a single unverified
count of "1 GP" - and one of those, Beverly Hills, actually has **4** general practices - while a
third is a floor that likely fails the threshold once corrected.

The deeper problem is the **unit**, not the missing data. A supply gap measured inside a suburb
boundary is mostly measuring the boundary: **83% of the apparently empty suburbs have a general
practice within 1.5 km**, and only about 5,000 people in Greater Sydney live more than 3 km from
one.

**In priority order:**

1. Replace the scraped clinic inventory with the GA/NHSD practice register (§1.5), filtered to
   `nhsd_service_type = 'General practice service'`.
2. Re-measure supply as practices within a **1.5-2 km radius**, not inside the polygon, and re-run
   the shortlist. Expect it to change substantially.
3. Get the client's real current-site list and exclude it on address, not brand name (§2.2).
4. Decide the headcount question: accept a coarse SA3-scale GP FTE figure, or drop headcount and
   rank on practices per capita.

The 247-suburb re-scrape is a **lower** priority than it appears. The register already answers "is
there a practice here", and for most of that list the answer is "not inside the boundary, but next
door". Re-scrape only what survives the catchment screen.
