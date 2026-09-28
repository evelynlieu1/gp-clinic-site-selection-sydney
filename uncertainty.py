"""
Population uncertainty: does the ranking survive the gap between 2021 demand and
2025 supply?

Not Census measurement error. ABS confidentiality perturbation is a few persons
against suburb totals in the thousands - 0.02 percent at Cronulla - so it moves
nothing. The four-year gap between the Census and the March 2025 register does.

Growth ratios are 2025 SA2 ERP over 2021 Census counts, for suburbs whose SAL polygon
overlaps an SA2 polygon at IoU >= 0.90. Name matching alone is not enough: 90 suburbs
share an SA2 name without sharing its boundary, and Chullora reads as 14 residents
against an ERP of 1,524. The geometric filter keeps 43 of the 90, median growth 5.1
percent, and puts Oran Park at 1.458.

Each replicate draws a ratio per suburb, scales population and rebuilds the E2SFCA
surface. Population is the demand side, so access_gap moves with it, not just
demand_scale. Two modes: independent draws, or one ratio per GP catchment so a growth
corridor moves together.

Caveats: 43 calibration suburbs is small, and they are the ones whose boundaries
happen to match an SA2 rather than a random sample. ERP adjusts for Census undercount,
so roughly 2 percent of every ratio is definitional. Only population is perturbed.

Run: python uncertainty.py [--replicates N] [--mode independent|blocked|both]
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

import site_score

ROOT = Path(__file__).resolve().parent
GROWTH_CSV = ROOT / "scraped_data" / "suburb_erp_growth.csv"
OUT_CSV = ROOT / "eda" / "population_uncertainty.csv"

DEFAULT_REPLICATES = 200
RNG_SEED = 20260927
TOP_N = 10


def growth_ratios() -> np.ndarray:
    """
    The empirical 2021-to-2025 growth ratios used as the resampling pool.

    Raises rather than falling back to an assumed distribution: an invented spread here
    would silently become the project's uncertainty estimate.
    """
    if not GROWTH_CSV.exists():
        raise SystemExit(
            f"{GROWTH_CSV.name} is missing. It is built from the cached SA2 ERP and the "
            "ABS SA2 2021 boundaries, keeping only suburbs whose SAL polygon overlaps an "
            "SA2 polygon at IoU >= 0.90."
        )
    ratios = pd.read_csv(GROWTH_CSV)["ratio"].to_numpy(float)
    ratios = ratios[np.isfinite(ratios) & (ratios > 0)]
    if ratios.size < 20:
        raise SystemExit(f"only {ratios.size} calibration ratios, too few to resample")
    return ratios


def draw(frame: pd.DataFrame, pool: np.ndarray, rng, mode: str) -> np.ndarray:
    """Growth ratio per suburb, either independent or shared within a GP catchment."""
    n = len(frame)
    if mode == "independent":
        return rng.choice(pool, size=n, replace=True)
    if mode == "blocked":
        codes = frame["gpc_code"].to_numpy()
        per_block = {c: rng.choice(pool) for c in pd.unique(codes)}
        return np.array([per_block[c] for c in codes], dtype=float)
    raise ValueError(f"unknown mode {mode!r}")


def run(inputs, mode: str, n: int, seed: int = RNG_SEED) -> pd.DataFrame:
    """
    Re-rank ``n`` times under resampled population, returning one row per suburb.

    The surface is rebuilt every replicate: population is the demand side of E2SFCA, so
    perturbing it moves ``access_gap`` as well as ``demand_scale``. Scaling a column after
    scoring would miss that.
    """
    pool = growth_ratios()
    rng = np.random.default_rng(seed)
    base_pop = inputs.frame["population"].to_numpy(float).copy()
    cfg = site_score.ScoreConfig(income_knots=inputs.income_knots)
    names = inputs.frame["suburb"].to_numpy()
    seen: dict[str, list[float]] = {s: [] for s in names}

    for i in range(n):
        ratios = draw(inputs.frame, pool, rng, mode)
        inputs.frame["population"] = base_pop * ratios
        inputs._surfaces.clear()
        frame = site_score.score(inputs, cfg)
        elig = frame.loc[frame["eligible"]]
        ranks = elig["site_score"].rank(ascending=False, method="min")
        for s, r in zip(elig["suburb"].to_numpy(), ranks.to_numpy()):
            seen[s].append(float(r))
        if (i + 1) % 25 == 0:
            print(f"    {i + 1}/{n} replicates")

    inputs.frame["population"] = base_pop
    inputs._surfaces.clear()

    rows = []
    for s in names:
        d = np.array(seen[s], dtype=float)
        if d.size == 0:
            rows.append({"suburb": s, "mode": mode, "appearances": 0, "rank_median": np.nan,
                         "rank_lo": np.nan, "rank_hi": np.nan, "p_top_n": 0.0,
                         "p_rank_1": 0.0})
            continue
        lo, hi = np.percentile(d, [2.5, 97.5])
        rows.append({
            "suburb": s, "mode": mode, "appearances": int(d.size),
            "rank_median": float(np.median(d)), "rank_lo": float(lo), "rank_hi": float(hi),
            "p_top_n": float((d <= TOP_N).mean()),
            "p_rank_1": float((d == 1).mean()),
        })
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--replicates", type=int, default=DEFAULT_REPLICATES)
    parser.add_argument("--mode", default="both",
                        choices=["independent", "blocked", "both"])
    args = parser.parse_args()

    pool = growth_ratios()
    print(f"calibration pool: {pool.size} suburbs, median growth "
          f"{100 * (np.median(pool) - 1):.1f}%, P95 {np.percentile(pool, 95):.3f}")

    inputs = site_score.load_inputs(verbose=False)
    base = site_score.score(inputs, site_score.ScoreConfig(income_knots=inputs.income_knots))
    base_rank = base.loc[base["eligible"], ["suburb", "rank"]].set_index("suburb")["rank"]

    modes = ["independent", "blocked"] if args.mode == "both" else [args.mode]
    out = []
    for mode in modes:
        print(f"\n{mode} draws, {args.replicates} replicates")
        res = run(inputs, mode, args.replicates)
        out.append(res)
        merged = res.merge(base_rank.rename("base_rank"), on="suburb", how="left")
        top = merged.dropna(subset=["base_rank"]).nsmallest(TOP_N, "base_rank")
        print(f"  {'base':>4s}  {'suburb':20s} {'median':>7s} {'95% interval':>14s} "
              f"{'P(top10)':>9s} {'P(rank1)':>9s}")
        for r in top.itertuples():
            span = f"[{r.rank_lo:.0f}, {r.rank_hi:.0f}]"
            print(f"  {int(r.base_rank):>4d}  {r.suburb:20s} {r.rank_median:7.0f} "
                  f"{span:>14s} {r.p_top_n:9.2f} {r.p_rank_1:9.2f}")
        holders = merged.loc[merged["p_rank_1"] > 0].nlargest(4, "p_rank_1")
        print("  ever holds rank 1: " + ", ".join(
            f"{h.suburb} {h.p_rank_1:.0%}" for h in holders.itertuples()))
    frame = pd.concat(out, ignore_index=True)
    OUT_CSV.parent.mkdir(exist_ok=True)
    frame.to_csv(OUT_CSV, index=False)
    print(f"\nwrote {OUT_CSV} ({len(frame)} rows)")


if __name__ == "__main__":
    main()
