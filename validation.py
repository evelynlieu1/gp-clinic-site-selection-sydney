"""
Inferential checks on the v2 site score: external validation and spatial autocorrelation.

WHY THIS EXISTS
---------------
Everything upstream is measurement and decision analysis. Nothing in it asks two
questions a reviewer will ask immediately:

1. Is the score any good against something outside itself?
2. Is the ranking just a map of where Sydney's peninsulas are?

Both are answerable with data already in the repository, and both produce a statistic
with an interval or a p-value rather than an assertion.

WHAT IT TESTS
-------------
``validate_against_dpa``
    The Department of Health's Distribution Priority Area flag is an independent
    under-supply designation, derived from GP FTE per capita weighted by demographics.
    The score has never seen it. Discrimination is reported as AUC with a percentile
    bootstrap interval. AUC rather than accuracy because the classes are heavily
    imbalanced (79 positive of 680) and accuracy would be 88 percent for a model that
    always says "no".

``morans_i``
    Global Moran's I on the score, with k-nearest-neighbour weights and a permutation
    null. The module docstring of :mod:`site_score` already claims the top 10 is
    concentrated in coastal-peninsula catchments. That is a spatial autocorrelation
    claim made by eye; this measures it.

    A high positive I is NOT by itself a defect. Access to health care is genuinely
    spatially structured, so some autocorrelation is the phenomenon, not an artefact.
    What it changes is inference: with I well above the null, the 507 eligible suburbs
    are not 507 independent observations, so any confidence interval computed as though
    they were is too narrow. That caveat is the point of measuring it.

INTERPRETING THE DPA RESULT
---------------------------
DPA is a weak benchmark and the weakness runs one way. DPA-positive suburbs have a
median population of 815 and a median of ZERO practices within 2 km: the designation is
calibrated for rural workforce distribution, not metropolitan commercial siting. A high
AUC therefore says "this score recognises genuine under-supply where the government
recognises it too", which is meaningful. A low AUC on the composite would NOT mean the
composite is broken, because the composite is deliberately optimising for commercial
site suitability, of which under-supply is only one input.

Run: ``python validation.py``
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

import dpa_status

ROOT = Path(__file__).resolve().parent
SCORES = ROOT / "eda" / "site_scores_v2.csv"
CENTROID_CACHE = ROOT / "scraped_data" / "suburb_centroids.csv"
OUT = ROOT / "eda" / "validation.csv"

#: Neighbours per suburb for the Moran's I weight matrix. 8 is the common default and is
#: varied in :func:`sensitivity_over_k` because a single k is a researcher choice.
DEFAULT_K = 8

#: Permutation draws for the Moran's I null, and resamples for the AUC interval.
N_PERMUTATIONS = 999
N_BOOTSTRAP = 2000

RNG_SEED = 20260923


# ---------------------------------------------------------------------------
# Discrimination against the DPA designation
# ---------------------------------------------------------------------------


def auc(score: np.ndarray, positive: np.ndarray) -> float:
    """
    Area under the ROC curve, by the Mann-Whitney U identity.

    Equivalent to the probability that a randomly chosen positive outranks a randomly
    chosen negative. Ties contribute 0.5, which the rank average handles.

    A perfect separator and a perfect inversion:

    >>> auc(np.array([1.0, 2.0, 3.0, 4.0]), np.array([False, False, True, True]))
    1.0
    >>> auc(np.array([4.0, 3.0, 2.0, 1.0]), np.array([False, False, True, True]))
    0.0

    Positives at the two extremes: two of the four positive-negative pairs are won, so
    the score carries no information about the class.

    >>> auc(np.array([1.0, 2.0, 3.0, 4.0]), np.array([True, False, False, True]))
    0.5

    Interleaved positives sit above chance, which is the case worth stating explicitly
    because it looks like alternation and is not 0.5:

    >>> auc(np.array([1.0, 2.0, 3.0, 4.0]), np.array([False, True, False, True]))
    0.75

    All values tied gives exactly 0.5, not a divide by zero:

    >>> auc(np.array([1.0, 1.0, 1.0, 1.0]), np.array([False, False, True, True]))
    0.5
    """
    score = np.asarray(score, dtype=float)
    positive = np.asarray(positive, dtype=bool)
    n_pos = int(positive.sum())
    n_neg = int((~positive).sum())
    if n_pos == 0 or n_neg == 0:
        raise ValueError(f"need both classes, got {n_pos} positive and {n_neg} negative")
    ranks = pd.Series(score).rank().to_numpy()
    return float((ranks[positive].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def auc_interval(
    score: np.ndarray, positive: np.ndarray, n: int = N_BOOTSTRAP, seed: int = RNG_SEED
) -> tuple[float, float, float]:
    """
    Point AUC with a 95 percent percentile bootstrap interval, stratified by class.

    Stratified so every resample keeps both classes, which matters here because the
    positive class is only 79 of 680 and an unstratified resample can empty it.
    """
    score = np.asarray(score, dtype=float)
    positive = np.asarray(positive, dtype=bool)
    point = auc(score, positive)
    rng = np.random.default_rng(seed)
    pos_idx = np.flatnonzero(positive)
    neg_idx = np.flatnonzero(~positive)
    draws = np.empty(n, dtype=float)
    for i in range(n):
        take = np.concatenate([
            rng.choice(pos_idx, pos_idx.size, replace=True),
            rng.choice(neg_idx, neg_idx.size, replace=True),
        ])
        draws[i] = auc(score[take], positive[take])
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return point, float(lo), float(hi)


# ---------------------------------------------------------------------------
# Spatial autocorrelation
# ---------------------------------------------------------------------------


def knn_weights(xy: np.ndarray, k: int = DEFAULT_K) -> np.ndarray:
    """
    Row-standardised k-nearest-neighbour spatial weights from projected coordinates.

    Row standardisation means each suburb's neighbours carry equal total influence, so a
    suburb in a dense cluster does not dominate purely by having close neighbours. No
    suburb is its own neighbour.

    Four points on a line, k=1: each is its nearest neighbour's neighbour.

    >>> xy = np.array([[0.0, 0.0], [1.0, 0.0], [5.0, 0.0], [6.0, 0.0]])
    >>> w = knn_weights(xy, k=1)
    >>> w.shape, float(w.diagonal().sum())
    ((4, 4), 0.0)
    >>> [int(r.argmax()) for r in w]
    [1, 0, 3, 2]
    >>> bool(np.allclose(w.sum(axis=1), 1.0))
    True
    """
    xy = np.asarray(xy, dtype=float)
    n = xy.shape[0]
    if k < 1 or k >= n:
        raise ValueError(f"need 1 <= k < {n}, got {k}")
    d2 = ((xy[:, None, :] - xy[None, :, :]) ** 2).sum(axis=2)
    np.fill_diagonal(d2, np.inf)
    nbr = np.argsort(d2, axis=1)[:, :k]
    w = np.zeros((n, n), dtype=float)
    rows = np.repeat(np.arange(n), k)
    w[rows, nbr.ravel()] = 1.0 / k
    return w


def morans_i(values: np.ndarray, w: np.ndarray) -> float:
    """
    Global Moran's I for ``values`` under row-standardised weights ``w``.

    ``I = (n / S0) * (z' W z) / (z' z)`` with ``z`` the deviations from the mean. With
    row-standardised weights ``S0 == n``, so the leading factor is 1.

    Perfectly alternating values on a line are negatively autocorrelated:

    >>> xy = np.array([[float(i), 0.0] for i in range(6)])
    >>> w = knn_weights(xy, k=2)
    >>> bool(morans_i(np.array([0.0, 1.0, 0.0, 1.0, 0.0, 1.0]), w) < -0.4)
    True

    A clean spatial gradient is positively autocorrelated:

    >>> bool(morans_i(np.arange(6.0), w) > 0.4)
    True

    Constant values have no variance and cannot be autocorrelated:

    >>> morans_i(np.ones(6), w)
    0.0
    """
    z = np.asarray(values, dtype=float)
    z = z - z.mean()
    denom = float(z @ z)
    if denom == 0.0:
        return 0.0
    n = z.size
    s0 = float(w.sum())
    return float((n / s0) * (z @ (w @ z)) / denom)


def morans_i_test(
    values: np.ndarray, w: np.ndarray, n: int = N_PERMUTATIONS, seed: int = RNG_SEED
) -> dict[str, float]:
    """
    Moran's I against a permutation null, returning I, the null mean, a z score and p.

    Permutation rather than the analytic normal approximation because the score is
    bounded on [0, 1] and visibly non-normal, which is exactly where the closed-form
    variance misleads. ``p`` is the one-sided pseudo p-value for positive clustering,
    ``(1 + #{I_perm >= I_obs}) / (1 + n)``, which cannot return 0 and so never claims
    more precision than the number of draws supports.
    """
    observed = morans_i(values, w)
    rng = np.random.default_rng(seed)
    z = np.asarray(values, dtype=float)
    draws = np.empty(n, dtype=float)
    for i in range(n):
        draws[i] = morans_i(rng.permutation(z), w)
    expected = -1.0 / (z.size - 1)
    sd = float(draws.std(ddof=1))
    return {
        "morans_i": observed,
        "expected_under_null": expected,
        "null_mean": float(draws.mean()),
        "null_sd": sd,
        "z_score": (observed - draws.mean()) / sd if sd else float("nan"),
        "p_value": float((1 + int((draws >= observed).sum())) / (1 + n)),
    }


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def load_centroids(frame: pd.DataFrame) -> pd.DataFrame:
    """
    Suburb centroids in the equal-area CRS, cached to CSV after the first build.

    Reuses :func:`dpa_status.load_suburbs`, so the geometry is the same ABS SAL 2021
    join the rest of the pipeline uses rather than a second, silently different one.
    """
    if CENTROID_CACHE.exists():
        cached = pd.read_csv(CENTROID_CACHE)
        if set(cached["suburb"]) >= set(frame["suburb"]):
            return cached
    geo = dpa_status.load_suburbs(frame)
    pts = geo.geometry.representative_point()
    out = pd.DataFrame({
        "suburb": geo["suburb"].to_numpy(),
        "x_m": pts.x.to_numpy(),
        "y_m": pts.y.to_numpy(),
    })
    CENTROID_CACHE.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(CENTROID_CACHE, index=False)
    return out


def main() -> None:
    scores = pd.read_csv(SCORES)
    centroids = load_centroids(scores[["suburb"]])
    df = scores.merge(centroids, on="suburb", how="left", validate="one_to_one")
    if df[["x_m", "y_m"]].isna().any().any():
        raise AssertionError("suburbs without a centroid after the SAL join")

    rows: list[dict[str, object]] = []

    print("=" * 78)
    print("1. DISCRIMINATION against the Department of Health DPA designation")
    print("=" * 78)
    flagged = df[df["dpa_status"].isin(["Y", "N"])].copy()
    positive = (flagged["dpa_status"] == "Y").to_numpy()
    print(f"  {int(positive.sum())} DPA-positive, {int((~positive).sum())} negative, "
          f"{len(flagged)} classified")
    print(f"  {'measure':26s} {'AUC':>6s} {'95% CI':>18s}")
    for col in ("access_gap_unguarded", "access_gap", "demand_scale",
                "mixed_billing_fit", "site_score"):
        if col not in flagged:
            continue
        point, lo, hi = auc_interval(flagged[col].to_numpy(float), positive)
        print(f"  {col:26s} {point:6.3f}   [{lo:.3f}, {hi:.3f}]")
        rows.append({"test": "auc_vs_dpa", "measure": col, "statistic": point,
                     "ci_low": lo, "ci_high": hi, "p_value": ""})

    print()
    print("=" * 78)
    print("2. SPATIAL AUTOCORRELATION, Moran's I under a permutation null")
    print("=" * 78)
    elig = df[df["eligible"] == True]  # noqa: E712
    xy = elig[["x_m", "y_m"]].to_numpy(float)
    w = knn_weights(xy, DEFAULT_K)
    print(f"  {len(elig)} eligible suburbs, k={DEFAULT_K} nearest neighbours, "
          f"{N_PERMUTATIONS} permutations")
    print(f"  {'measure':26s} {'I':>7s} {'E[I]':>7s} {'z':>8s} {'p':>8s}")
    for col in ("site_score", "access_gap", "demand_scale", "mixed_billing_fit"):
        res = morans_i_test(elig[col].to_numpy(float), w)
        print(f"  {col:26s} {res['morans_i']:7.3f} {res['expected_under_null']:7.3f} "
              f"{res['z_score']:8.1f} {res['p_value']:8.3f}")
        rows.append({"test": f"morans_i_k{DEFAULT_K}", "measure": col,
                     "statistic": res["morans_i"], "ci_low": "", "ci_high": "",
                     "p_value": res["p_value"]})

    print()
    print("  Sensitivity of I on site_score to the neighbour count:")
    for k in (4, 8, 16, 32):
        res = morans_i_test(elig["site_score"].to_numpy(float), knn_weights(xy, k))
        print(f"    k={k:<3d} I = {res['morans_i']:6.3f}  p = {res['p_value']:.3f}")
        rows.append({"test": f"morans_i_k{k}", "measure": "site_score",
                     "statistic": res["morans_i"], "ci_low": "", "ci_high": "",
                     "p_value": res["p_value"]})

    pd.DataFrame(rows).to_csv(OUT, index=False)
    print()
    print(f"wrote {OUT} ({len(rows)} rows)")


if __name__ == "__main__":
    import doctest

    failures, _ = doctest.testmod(verbose=False)
    if failures:
        raise SystemExit(f"{failures} doctest failures")
    main()
