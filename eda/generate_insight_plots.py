"""
Insight plots for GP underprovision (population, density, seniors, income vs GP access).

Reads: ../scraped_data/eda_data.csv
Writes PNGs to: ./plots/
Updates ../scraped_data/eda_data.csv with column ``opportunity_score`` (density/income/age min-max composite).

Writes opportunity tables to: ./
  - opportunity_high_pop_low_gp.csv
  - opportunity_high_elderly_pct_low_gp.csv
  - opportunity_high_density_low_gp.csv
  - opportunity_all_signals.csv  (all suburbs in cohort + three scores)
  - weighted_opportunity_scores.csv  (MCDA: min-max 0-1 criteria + weighted composite)
  - OPPORTUNITY_SCORING.md  (methodology for reports)

Run from project root:
  python eda/generate_insight_plots.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "scraped_data" / "eda_data.csv"
OUT_DIR = Path(__file__).resolve().parent / "plots"
OUT_DIR.mkdir(parents=True, exist_ok=True)
EDA_DIR = Path(__file__).resolve().parent
# Suburbs below this population excluded from thresholds & flags (unstable gp_per_1000)
MIN_POP_FLAGS = 1000

# Minimum population for ranking tables (stable rates)
MIN_POP_RANK = 1000

# How many suburbs to keep in each "opportunity" shortlist CSV
TOP_OPPORTUNITY_N = 40

# --- MCDA weights (sum to 1.0); each criterion scaled to [0, 1] within cohort ---
# Scaling (population >= MIN_POP_RANK): cannot add $ income to % age without common units.
# Higher is better:  (x - min) / (max - min)
# Lower is better:   (max - x) / (max - min)
# If max == min, score = 0.5 (neutral). Missing values imputed to cohort median before min/max.
W_GP_SUPPLY = 0.35  # total GP count (lower -> less competition signal in data)
W_INCOME = 0.25  # household income (higher vs lower strategy)
W_SENIORS = 0.20  # % aged 65+ (higher is better)
W_POPULATION = 0.15  # population (higher is better)
W_AREA = 0.05  # area km2 (smaller is better -> lower-is-better scaling)

# Final column `opportunity_score` in eda_data.csv (all suburbs; min-max across full file)
# Score = w1*Norm(spatial_density) + w2*Norm(household_income) + w3*Norm(over_65_pct); all higher-better.
W_OPP_DENSITY = 1.0 / 3.0
W_OPP_INCOME = 1.0 / 3.0
W_OPP_AGE = 1.0 / 3.0

WEIGHTED_SCORE_REPORT_BLURB = """
### MCDA opportunity score (EDA)

**Why min-max:** You cannot add incomparable units (e.g. income in dollars to % over 65).
Each criterion is scaled to **0–1** within the analysis cohort (population >= 1000).

**Scaling (within cohort):**

- **Higher is better** (income, population, % aged 65+):  
  `(x - min) / (max - min)`

- **Lower is better** (total GP count, area km²):  
  `(max - x) / (max - min)`

**Composite:** Weighted sum of the 0–1 scores (weights sum to 1 → score in ~[0, 1]). Higher = better fit.

**Edge cases:** If `max == min` for a column, everyone gets **0.5** on that criterion. Missing values are imputed to the **cohort median** (per column) before min–max.

| Criterion | Weight | Direction | Raw variable |
|-----------|--------|-----------|--------------|
| Listed GP supply | 0.35 | Lower better | `total_gp` |
| Household income | 0.25 | Higher (mixed billing) or lower (community need) | `median_household_income_weekly` |
| Age demand | 0.20 | Higher better | `over_65_pct` |
| Population | 0.15 | Higher better | `population` |
| Area | 0.05 | Lower better (walk-in catchment) | `area` |

**Economic:** We include income to capture **financial viability** (mixed billing / gap fees).
`weighted_score_community_need` uses the **lower-is-better** min-max on income instead.
""".strip()


def load_prepared() -> pd.DataFrame:
    if not DATA_PATH.exists():
        print(f"Missing {DATA_PATH}", file=sys.stderr)
        sys.exit(1)
    df = pd.read_csv(DATA_PATH)
    for c in [
        "total_gp",
        "population",
        "over_65_pct",
        "spatial_density",
        "median_household_income_weekly",
        "median_family_income_weekly",
        "median_personal_income_weekly",
        "area",
    ]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["total_gp"] = df["total_gp"].fillna(0).clip(lower=0)
    # GPs per 1,000 residents
    df["gp_per_1000"] = np.where(
        df["population"] > 0,
        1000.0 * df["total_gp"] / df["population"],
        np.nan,
    )
    return df


def export_three_opportunity_lists(r: pd.DataFrame, out_dir: Path, n: int) -> None:
    """
    Persist the three EDA targets (high demand axis vs low GP supply).

    Within suburbs with population >= MIN_POP_RANK, each score is:
      z(demand_feature) - z(gp_per_1000)
    Higher = more "high X but low GP" relative to the cohort.
    """
    base_cols = [
        "suburb",
        "population",
        "over_65_pct",
        "spatial_density",
        "total_gp",
        "gp_per_1000",
    ]
    inc = "median_household_income_weekly"
    if inc in r.columns:
        base_cols = base_cols + [inc]

    # 1) High population, low GP (catchment / foot traffic story)
    top_pop = r.nlargest(n, "sig_pop_lowgp")
    top_pop.to_csv(out_dir / "opportunity_high_pop_low_gp.csv", index=False)

    # 2) High elderly %, low GP (utilisation / chronic care story)
    top_65 = r.nlargest(n, "sig_65_lowgp")
    top_65.to_csv(out_dir / "opportunity_high_elderly_pct_low_gp.csv", index=False)

    # 3) High density, low GP (competition / access in compact areas)
    top_den = r.nlargest(n, "sig_density_lowgp")
    top_den.to_csv(out_dir / "opportunity_high_density_low_gp.csv", index=False)

    all_sig_cols = base_cols + [
        "sig_pop_lowgp",
        "sig_65_lowgp",
        "sig_density_lowgp",
    ]
    present = [c for c in all_sig_cols if c in r.columns]
    r[present].sort_values("suburb").to_csv(out_dir / "opportunity_all_signals.csv", index=False)

    print(f"\n=== Opportunity shortlists (top {n} each, population >= {MIN_POP_RANK}) ===")
    print("Target 1: HIGH population, relatively LOW gp_per_1000  ->  sig_pop_lowgp")
    print("Target 2: HIGH share aged 65+, relatively LOW gp_per_1000  ->  sig_65_lowgp")
    print("Target 3: HIGH spatial density, relatively LOW gp_per_1000  ->  sig_density_lowgp")
    print(f"Saved: {out_dir / 'opportunity_high_pop_low_gp.csv'}")
    print(f"Saved: {out_dir / 'opportunity_high_elderly_pct_low_gp.csv'}")
    print(f"Saved: {out_dir / 'opportunity_high_density_low_gp.csv'}")
    print(f"Saved: {out_dir / 'opportunity_all_signals.csv'}")


def zscore(s: pd.Series) -> pd.Series:
    s = s.astype(float)
    m, sd = s.mean(), s.std(ddof=0)
    if sd == 0 or np.isnan(sd):
        return s * 0.0
    return (s - m) / sd


def min_max_higher(s: pd.Series) -> pd.Series:
    """
    Higher raw values -> higher score in [0, 1]: (x - min) / (max - min).
    """
    v = pd.to_numeric(s, errors="coerce").astype(float)
    med = float(np.nanmedian(v.to_numpy()))
    v = v.fillna(med)
    lo, hi = float(v.min()), float(v.max())
    if hi == lo or not np.isfinite(lo) or not np.isfinite(hi):
        return pd.Series(0.5, index=s.index, dtype=float)
    out = (v - lo) / (hi - lo)
    return out.clip(0.0, 1.0)


def min_max_lower(s: pd.Series) -> pd.Series:
    """
    Lower raw values -> higher score in [0, 1]: (max - x) / (max - min).
    """
    v = pd.to_numeric(s, errors="coerce").astype(float)
    med = float(np.nanmedian(v.to_numpy()))
    v = v.fillna(med)
    lo, hi = float(v.min()), float(v.max())
    if hi == lo or not np.isfinite(lo) or not np.isfinite(hi):
        return pd.Series(0.5, index=s.index, dtype=float)
    out = (hi - v) / (hi - lo)
    return out.clip(0.0, 1.0)


def add_opportunity_score_three_criteria(df: pd.DataFrame) -> pd.DataFrame:
    """
    Per-suburb final opportunity score (same formula as pre-processing export).

    Norms are min-max on [0, 1] using **all rows** in ``df`` (not only pop >= MIN_POP_RANK).
    Zero household income is treated as missing before scaling.
    """
    out = df.copy()
    norm_d = min_max_higher(out["spatial_density"])
    inc = pd.to_numeric(out["median_household_income_weekly"], errors="coerce").replace(
        0, np.nan
    )
    norm_i = min_max_higher(inc)
    norm_a = min_max_higher(out["over_65_pct"])
    out["opportunity_score"] = (
        W_OPP_DENSITY * norm_d + W_OPP_INCOME * norm_i + W_OPP_AGE * norm_a
    ).round(6)
    return out


def persist_opportunity_score_to_eda_csv() -> None:
    """Recompute ``opportunity_score`` on disk (``scraped_data/eda_data.csv``)."""
    if not DATA_PATH.exists():
        print(f"Skip opportunity_score persist: missing {DATA_PATH}", file=sys.stderr)
        return
    raw = pd.read_csv(DATA_PATH)
    for c in [
        "total_gp",
        "population",
        "over_65_pct",
        "spatial_density",
        "median_household_income_weekly",
        "median_family_income_weekly",
        "median_personal_income_weekly",
        "area",
    ]:
        if c in raw.columns:
            raw[c] = pd.to_numeric(raw[c], errors="coerce")
    raw["total_gp"] = raw["total_gp"].fillna(0).clip(lower=0)
    raw = add_opportunity_score_three_criteria(raw)
    raw.to_csv(DATA_PATH, index=False)
    print(f"Updated column opportunity_score in {DATA_PATH.resolve()}")


def build_weighted_opportunity_scores(df: pd.DataFrame) -> pd.DataFrame:
    """
    MCDA composite on [0, 1]-scaled criteria (min-max within cohort).

    Cohort: population >= MIN_POP_RANK. See WEIGHTED_SCORE_REPORT_BLURB / OPPORTUNITY_SCORING.md.
    """
    r = df[df["population"] >= MIN_POP_RANK].copy()
    if r.empty:
        return r

    # Supply: fewer listed GPs -> higher score
    mm_total_gp = min_max_lower(r["total_gp"].fillna(0))

    inc_col = "median_household_income_weekly"
    if inc_col in r.columns:
        inc = pd.to_numeric(r[inc_col], errors="coerce").replace(0, np.nan)
        inc_med = float(inc.median()) if inc.notna().any() else 0.0
        inc = inc.fillna(inc_med)
        mm_inc_high = min_max_higher(inc)
        mm_inc_low = min_max_lower(inc)
    else:
        mm_inc_high = pd.Series(0.5, index=r.index, dtype=float)
        mm_inc_low = pd.Series(0.5, index=r.index, dtype=float)

    pct = pd.to_numeric(r["over_65_pct"], errors="coerce")
    pct_med = float(pct.median()) if pct.notna().any() else 0.0
    pct = pct.fillna(pct_med)
    r["seniors_estimated_count"] = r["population"].astype(float) * pct / 100.0
    mm_over_65 = min_max_higher(pct)

    mm_pop = min_max_higher(r["population"].astype(float))

    if "area" in r.columns:
        area = pd.to_numeric(r["area"], errors="coerce").replace(0, np.nan)
        a_med = float(area.median()) if area.notna().any() else 1.0
        area = area.fillna(a_med)
        mm_area = min_max_lower(area)
    else:
        mm_area = pd.Series(0.5, index=r.index, dtype=float)

    r["mcda_mm_total_gp_lower_better"] = mm_total_gp
    r["mcda_mm_income_higher_better"] = mm_inc_high
    r["mcda_mm_income_lower_better"] = mm_inc_low
    r["mcda_mm_over_65_pct_higher_better"] = mm_over_65
    r["mcda_mm_population_higher_better"] = mm_pop
    r["mcda_mm_area_lower_better"] = mm_area

    r["weighted_score_mixed_billing"] = (
        W_GP_SUPPLY * mm_total_gp
        + W_INCOME * mm_inc_high
        + W_SENIORS * mm_over_65
        + W_POPULATION * mm_pop
        + W_AREA * mm_area
    )
    r["weighted_score_community_need"] = (
        W_GP_SUPPLY * mm_total_gp
        + W_INCOME * mm_inc_low
        + W_SENIORS * mm_over_65
        + W_POPULATION * mm_pop
        + W_AREA * mm_area
    )

    r["contrib_total_gp_w"] = W_GP_SUPPLY * mm_total_gp
    r["contrib_income_mixed_billing_w"] = W_INCOME * mm_inc_high
    r["contrib_income_community_need_w"] = W_INCOME * mm_inc_low
    r["contrib_over_65_pct_w"] = W_SENIORS * mm_over_65
    r["contrib_population_w"] = W_POPULATION * mm_pop
    r["contrib_area_w"] = W_AREA * mm_area

    return r


def print_corr(label: str, x: pd.Series, y: pd.Series) -> None:
    mask = x.notna() & y.notna()
    if mask.sum() < 3:
        print(f"{label}: n too small")
        return
    r = np.corrcoef(x[mask], y[mask])[0, 1]
    print(f"{label}: Pearson r = {r:.4f} (n={mask.sum()})")


def scatter_with_marginals(
    df: pd.DataFrame,
    xcol: str,
    ycol: str,
    title: str,
    xlab: str,
    ylab: str,
    fname: str,
    log_x: bool = False,
    log_y: bool = False,
    highlight_mask: pd.Series | None = None,
) -> None:
    fig, ax = plt.subplots(figsize=(9, 6))
    d = df[[xcol, ycol, "suburb"]].dropna(subset=[xcol, ycol])
    if log_x:
        d = d[d[xcol] > 0]
    if log_y:
        d = d[d[ycol] > 0]

    ax.scatter(d[xcol], d[ycol], alpha=0.35, s=22, c="steelblue", edgecolors="none")

    if highlight_mask is not None:
        hi = df.loc[highlight_mask, [xcol, ycol, "suburb"]].dropna(subset=[xcol, ycol])
        if len(hi):
            ax.scatter(hi[xcol], hi[ycol], s=55, facecolors="none", edgecolors="crimson", linewidths=1.2)
            for _, r in hi.head(12).iterrows():
                ax.annotate(
                    r["suburb"][:18],
                    (r[xcol], r[ycol]),
                    fontsize=7,
                    alpha=0.85,
                    xytext=(4, 4),
                    textcoords="offset points",
                )

    if log_x:
        ax.set_xscale("log")
    if log_y:
        ax.set_yscale("log")
    ax.set_xlabel(xlab)
    ax.set_ylabel(ylab)
    ax.set_title(title)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    out = OUT_DIR / fname
    fig.savefig(out, dpi=140)
    plt.close(fig)
    print(f"Saved {out}")


def build_underprovided_table(df: pd.DataFrame) -> pd.DataFrame:
    """
    Simple supply flags from gp_per_1000 vs cohort thresholds (median / bottom 25%).

    High-demand side: at or above **median** (within pop>=MIN_POP_FLAGS) for at least one of:
    population, over_65_pct, spatial_density.

    Output: one row per suburb in the analysis cohort with boolean flags + priority columns.
    """
    sub = df[df["population"] >= MIN_POP_FLAGS].copy()
    if sub.empty:
        return sub

    gp = sub["gp_per_1000"]
    thr_med = float(gp.median())
    thr_p25 = float(gp.quantile(0.25))
    # If many suburbs have 0 listed GPs, overall P25 is often 0.0 — add a stricter
    # threshold using only suburbs with total_gp > 0 (within this cohort).
    pos = sub.loc[sub["total_gp"] > 0, "gp_per_1000"]
    thr_p25_nonzero = float(pos.quantile(0.25)) if len(pos) else float("nan")

    sub["gp_threshold_median"] = thr_med
    sub["gp_threshold_p25"] = thr_p25
    sub["gp_threshold_p25_among_gps_gt0"] = thr_p25_nonzero
    sub["underprovided_below_median"] = gp < thr_med
    sub["underprovided_bottom_25pct"] = gp <= thr_p25
    sub["underprovided_bottom_25pct_among_gps_gt0"] = (sub["total_gp"] > 0) & (
        gp <= thr_p25_nonzero
    )

    med_pop = sub["population"].median()
    med_65 = sub["over_65_pct"].median()
    med_den = sub["spatial_density"].median()

    sub["high_pop_demand"] = sub["population"] >= med_pop
    sub["high_elderly_demand"] = sub["over_65_pct"] >= med_65
    sub["high_density_demand"] = sub["spatial_density"].notna() & (
        sub["spatial_density"] >= med_den
    )
    sub["high_demand_any"] = (
        sub["high_pop_demand"]
        | sub["high_elderly_demand"]
        | sub["high_density_demand"]
    )

    sub["priority_low_gp_high_demand_median"] = (
        sub["underprovided_below_median"] & sub["high_demand_any"]
    )
    sub["priority_low_gp_high_demand_p25"] = (
        sub["underprovided_bottom_25pct"] & sub["high_demand_any"]
    )
    sub["priority_low_gp_high_demand_p25_nonzero"] = (
        sub["underprovided_bottom_25pct_among_gps_gt0"] & sub["high_demand_any"]
    )

    return sub


def main() -> None:
    persist_opportunity_score_to_eda_csv()
    df = load_prepared()
    rank_df = df[df["population"] >= MIN_POP_RANK].copy()

    print("=== Dataset ===")
    print(f"Rows: {len(df)}  (ranking subset pop>={MIN_POP_RANK}: {len(rank_df)})")
    print(f"total_gp: min={df['total_gp'].min():.0f} max={df['total_gp'].max():.0f} median={df['total_gp'].median():.0f}")
    print(f"gp_per_1000: median={df['gp_per_1000'].median():.3f} max={df['gp_per_1000'].max():.3f}")

    print("\n=== Correlations (all suburbs with valid pairs) ===")
    print_corr("population vs total_gp", df["population"], df["total_gp"])
    print_corr("spatial_density vs gp_per_1000", df["spatial_density"], df["gp_per_1000"])
    print_corr("over_65_pct vs gp_per_1000", df["over_65_pct"], df["gp_per_1000"])
    print_corr(
        "median_household_income vs gp_per_1000",
        df["median_household_income_weekly"],
        df["gp_per_1000"],
    )

    # Composite underprovision scores (population subset)
    r = rank_df.copy()
    r["z_pop"] = zscore(r["population"])
    r["z_density"] = zscore(r["spatial_density"])
    r["z_65"] = zscore(r["over_65_pct"])
    r["z_gp1k"] = zscore(r["gp_per_1000"].fillna(0))
    r["sig_pop_lowgp"] = r["z_pop"] - r["z_gp1k"]
    r["sig_65_lowgp"] = r["z_65"] - r["z_gp1k"]
    r["sig_density_lowgp"] = r["z_density"] - r["z_gp1k"]

    export_three_opportunity_lists(r, EDA_DIR, TOP_OPPORTUNITY_N)

    print(f"\n=== Top 12 preview (same logic as CSVs; population >= {MIN_POP_RANK}) ===\n")

    def show_table(name: str, sub: pd.DataFrame, cols: list[str]) -> None:
        print(f"--- {name} ---")
        print(sub[cols].to_string(index=False))
        print()

    show_table(
        "High population, low GP access (z_pop - z_gp_per_1000)",
        r.nlargest(12, "sig_pop_lowgp"),
        [
            "suburb",
            "population",
            "total_gp",
            "gp_per_1000",
            "sig_pop_lowgp",
        ],
    )
    show_table(
        "High % aged 65+, low GP access (z_65 - z_gp_per_1000)",
        r.nlargest(12, "sig_65_lowgp"),
        ["suburb", "over_65_pct", "total_gp", "gp_per_1000", "sig_65_lowgp"],
    )
    show_table(
        "High spatial density, low GP access (z_density - z_gp_per_1000)",
        r.nlargest(12, "sig_density_lowgp"),
        [
            "suburb",
            "spatial_density",
            "population",
            "total_gp",
            "gp_per_1000",
            "sig_density_lowgp",
        ],
    )

    # Highlight union of top 8 each for population plot
    top_union = pd.Index([])
    for col in ["sig_pop_lowgp", "sig_65_lowgp", "sig_density_lowgp"]:
        top_union = top_union.union(r.nlargest(8, col).index)
    highlight = df.index.isin(top_union)

    scatter_with_marginals(
        df,
        "population",
        "total_gp",
        "Population vs total GP count",
        "Population",
        "Total GP count (summed from clinic list)",
        "01_population_vs_total_gp.png",
        log_x=True,
        log_y=True,
        highlight_mask=highlight,
    )

    d2 = df[df["spatial_density"].notna() & df["gp_per_1000"].notna()].copy()
    scatter_with_marginals(
        d2,
        "spatial_density",
        "gp_per_1000",
        "Spatial density vs GPs per 1,000 residents",
        "People per km²",
        "GPs per 1,000 population",
        "02_spatial_density_vs_gp_per_1000.png",
        log_x=True,
        log_y=True,
        highlight_mask=d2.index.isin(top_union),
    )

    scatter_with_marginals(
        df,
        "over_65_pct",
        "gp_per_1000",
        "% aged 65+ vs GPs per 1,000 residents",
        "Over 65 %",
        "GPs per 1,000 population",
        "03_over_65_vs_gp_per_1000.png",
        log_y=True,
        highlight_mask=highlight,
    )

    inc = df["median_household_income_weekly"]
    scatter_with_marginals(
        df,
        "median_household_income_weekly",
        "gp_per_1000",
        "Median household income vs GPs per 1,000",
        "Median household income ($/week)",
        "GPs per 1,000 population",
        "04_household_income_vs_gp_per_1000.png",
        log_y=True,
        highlight_mask=highlight,
    )

    # Extra: income vs total_gp (raw availability)
    scatter_with_marginals(
        df,
        "median_household_income_weekly",
        "total_gp",
        "Median household income vs total GP count",
        "Median household income ($/week)",
        "Total GP count",
        "05_household_income_vs_total_gp.png",
        log_y=True,
        highlight_mask=highlight,
    )

    print(f"\nAll figures saved under: {OUT_DIR.resolve()}")

    # --- Underprovision flags (EDA): low gp_per_1000 + high demand proxies ---
    flags = build_underprovided_table(df)
    out_csv = EDA_DIR / "underprovided_suburbs.csv"
    export_cols = [
        "suburb",
        "population",
        "over_65_pct",
        "spatial_density",
        "total_gp",
        "gp_per_1000",
        "gp_threshold_median",
        "gp_threshold_p25",
        "gp_threshold_p25_among_gps_gt0",
        "underprovided_below_median",
        "underprovided_bottom_25pct",
        "underprovided_bottom_25pct_among_gps_gt0",
        "high_pop_demand",
        "high_elderly_demand",
        "high_density_demand",
        "high_demand_any",
        "priority_low_gp_high_demand_median",
        "priority_low_gp_high_demand_p25",
        "priority_low_gp_high_demand_p25_nonzero",
    ]
    present = [c for c in export_cols if c in flags.columns]
    flags[present].sort_values("gp_per_1000").to_csv(out_csv, index=False)
    print(f"\n=== Underprovision flags (population >= {MIN_POP_FLAGS}) ===")
    print(f"gp_per_1000 threshold (median): {flags['gp_threshold_median'].iloc[0]:.6f}")
    print(f"gp_per_1000 threshold (25th pct, all): {flags['gp_threshold_p25'].iloc[0]:.6f}")
    tnz = flags["gp_threshold_p25_among_gps_gt0"].iloc[0]
    print(
        f"gp_per_1000 threshold (25th pct, only suburbs with total_gp>0): "
        f"{tnz:.6f}"
    )
    print(f"Count underprovided (below median): {int(flags['underprovided_below_median'].sum())}")
    print(f"Count underprovided (bottom 25%, all): {int(flags['underprovided_bottom_25pct'].sum())}")
    print(
        "Count underprovided (bottom 25% among listed-GP>0 suburbs): "
        f"{int(flags['underprovided_bottom_25pct_among_gps_gt0'].sum())}"
    )
    print(
        f"PRIORITY (below median GP supply AND high demand on any axis): "
        f"{int(flags['priority_low_gp_high_demand_median'].sum())}"
    )
    print(
        f"PRIORITY (bottom 25% all AND high demand): "
        f"{int(flags['priority_low_gp_high_demand_p25'].sum())}"
    )
    print(
        f"PRIORITY (bottom 25% among gp>0 AND high demand): "
        f"{int(flags['priority_low_gp_high_demand_p25_nonzero'].sum())}"
    )
    print(f"Saved full flag table: {out_csv.resolve()}")
    pri = flags[flags["priority_low_gp_high_demand_p25_nonzero"]].sort_values(
        "population", ascending=False
    )
    if len(pri):
        print(
            "\n--- Top priority suburbs (bottom 25% gp_per_1000 among listed-GP suburbs + high demand) ---"
        )
        print(
            pri[
                [
                    "suburb",
                    "population",
                    "over_65_pct",
                    "spatial_density",
                    "total_gp",
                    "gp_per_1000",
                ]
            ]
            .head(25)
            .to_string(index=False)
        )

    # --- Weighted opportunity scores (business criteria + explicit income logic) ---
    wdf = build_weighted_opportunity_scores(df)
    if len(wdf):
        method_md = EDA_DIR / "OPPORTUNITY_SCORING.md"
        extra = (
            "\n\n---\n\n**For written reports:** We include median household income to "
            "capture **financial viability** (mixed billing / ability to pay gap fees). "
            "For a **community-need** lens (access over profitability), use "
            "`weighted_score_community_need`, which rewards **lower** income instead.\n"
        )
        method_md.write_text(WEIGHTED_SCORE_REPORT_BLURB + extra, encoding="utf-8")

        wcsv = EDA_DIR / "weighted_opportunity_scores.csv"
        wcols = [
            "suburb",
            "population",
            "over_65_pct",
            "seniors_estimated_count",
            "spatial_density",
            "area",
            "median_household_income_weekly",
            "total_gp",
            "gp_per_1000",
            "weighted_score_mixed_billing",
            "weighted_score_community_need",
            "mcda_mm_total_gp_lower_better",
            "mcda_mm_income_higher_better",
            "mcda_mm_income_lower_better",
            "mcda_mm_over_65_pct_higher_better",
            "mcda_mm_population_higher_better",
            "mcda_mm_area_lower_better",
            "contrib_total_gp_w",
            "contrib_income_mixed_billing_w",
            "contrib_income_community_need_w",
            "contrib_over_65_pct_w",
            "contrib_population_w",
            "contrib_area_w",
        ]
        present_w = [c for c in wcols if c in wdf.columns]
        wdf[present_w].sort_values(
            "weighted_score_mixed_billing", ascending=False
        ).to_csv(wcsv, index=False)

        print("\n=== MCDA (min-max 0-1 criteria, see OPPORTUNITY_SCORING.md) ===")
        print(
            "Criteria scaled within cohort: higher-better = (x-min)/(max-min); "
            "lower-better (total_gp, area) = (max-x)/(max-min)."
        )
        print(
            "Income: mixed_billing uses higher-better; community_need uses lower-better on income."
        )
        print(f"Saved methodology: {method_md.resolve()}")
        print(f"Saved scores: {wcsv.resolve()}")
        print("\n--- Top 12: mixed-billing / low-competition lens ---")
        print(
            wdf.nlargest(12, "weighted_score_mixed_billing")[
                [
                    "suburb",
                    "total_gp",
                    "median_household_income_weekly",
                    "over_65_pct",
                    "population",
                    "weighted_score_mixed_billing",
                ]
            ].to_string(index=False)
        )
        print("\n--- Top 12: community-need lens (income min-max inverted) ---")
        print(
            wdf.nlargest(12, "weighted_score_community_need")[
                [
                    "suburb",
                    "total_gp",
                    "median_household_income_weekly",
                    "over_65_pct",
                    "population",
                    "weighted_score_community_need",
                ]
            ].to_string(index=False)
        )


if __name__ == "__main__":
    main()
