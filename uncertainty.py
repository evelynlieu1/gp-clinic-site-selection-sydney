"""
Propagate input uncertainty into the ranking, and report what survives it.

WHAT THIS IS NOT
----------------
It is not Census measurement error. The ABS perturbs small-area counts for
confidentiality, but the perturbation is a handful of persons and these are suburb
totals in the thousands, so it moves nothing: at Cronulla's 17,899 residents a +/-3
perturbation is 0.02 percent. Propagating it would produce an authoritative-looking
interval of essentially zero width. Stated here because "we propagated Census error"
sounds rigorous and would have been theatre.

WHAT ACTUALLY MOVES THE ANSWER
------------------------------
The vintage gap. Supply is the March 2025 register; demand is the 2021 Census. Four
years of population change sits unmodelled between them, and it is NOT uniform: growth
corridors moved far more than the median suburb.

CALIBRATION, empirical rather than assumed
------------------------------------------
Growth ratios come from comparing 2025 SA2 ERP against 2021 Census counts for suburbs
whose SAL polygon genuinely coincides with an SA2 polygon.

Matching on NAME ALONE IS WRONG and was tried first: 90 suburbs share a name with an
SA2, but the geographies differ. Chullora reads as 14 residents against an SA2 ERP of
1,524, a 109x "growth" that is entirely an area mismatch. Name matching gave a median
growth of 8.2 percent and 25 suburbs above 25 percent.

Requiring intersection-over-union >= 0.90 between the two polygons leaves 43 suburbs and
a median growth of 5.1 percent, with 5 above 25 percent. Oran Park comes out at 1.458,
which is the growth figure this project recorded independently. The IoU filter is doing
real work: it removes 47 of 90 name matches.

METHOD
------
Non-parametric bootstrap. Each replicate draws a growth ratio per suburb from the 43
observed ratios, scales the 2021 population by it, rebuilds the E2SFCA surface (population
is the demand side, so access_gap moves too, not just demand_scale) and re-ranks.

Two draw modes, because growth is spatially clustered and independent draws would deny it:

``independent``  each suburb draws its own ratio
``blocked``      one ratio per GP catchment, shared by its suburbs, so a whole corridor
                 grows or does not grow together

LIMITS, stated rather than buried
---------------------------------
- 43 calibration suburbs is a small sample, and they are not a random sample of Sydney:
  they are the suburbs whose boundaries happen to match an SA2.
- ERP and Census counts are different measures. ERP adjusts for undercount, so a ratio of
  about 1.02 is definitional rather than growth. This biases every ratio UP by roughly
  that amount and is not corrected, which makes the spread conservative in one direction.
- Only population is perturbed. The register, incomes and travel cost are held fixed.

Run: ``python uncertainty.py`` (``--replicates N``, ``--mode independent|blocked|both``).
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

    The accessibility surface is rebuilt every replicate. Population is the demand side of
    E2SFCA, so perturbing it moves ``access_gap`` as well as ``demand_scale`` - which is
    the whole point, and is why this cannot be done by scaling a column after scoring.
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
