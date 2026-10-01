"""
Inferential checks on the site score: external validation and spatial structure.

Everything upstream is measurement and decision analysis, which leaves two questions
unanswered: is the score any good against something outside itself, and is the ranking
just a map of where the peninsulas are?

validate_against_dpa
    The Department of Health's Distribution Priority Area flag is an independent
    under-supply designation derived from GP FTE per capita, and the score has never
    seen it. Reported as AUC with a bootstrap interval; AUC rather than accuracy
    because 79 positives of 680 means always-no scores 88 percent.

    DPA is a weak benchmark and weak in one direction: DPA-positive suburbs have a
    median population of 815 and a median of zero practices within 2 km, so it is
    calibrated for rural workforce distribution, not metropolitan siting.

morans_i and lisa_test
    Global and local spatial autocorrelation against a permutation null. The peninsula
    concentration was previously asserted from a map; this measures it. Clustering is
    not a defect - health access is genuinely spatial - but it means the eligible
    suburbs are not independent observations, so intervals that assume they are will
    be too narrow.

Run: python validation.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

import dpa_status

ROOT = Path(__file__).resolve().parent
SCORES = ROOT / "eda" / "site_scores_v2.csv"
CENTROID_CACHE = ROOT / "scraped_data" / "suburb_centroids.csv"
OUT = ROOT / "eda" / "validation.csv"

#: Neighbours per suburb for the Moran's I weight matrix. 8 is the common default and is
#: varied in :func:`sensitivity_over_k` because a single k is a researcher choice.
DEFAULT_K = 8

#: Population band edges for :func:`stratified_auc`. Chosen so the lowest band sits
#: below the eligibility floor and the rest split the eligible cohort into roughly even
#: thirds, not tuned to produce a result.
POPULATION_STRATA = (1_000.0, 5_000.0, 15_000.0)

#: Permutation draws for the Moran's I null, and resamples for the AUC interval.
N_PERMUTATIONS = 999
N_BOOTSTRAP = 2000

#: Resamples for the rank interval. Fewer than the AUC bootstrap because each draw
#: re-ranks the whole cohort, and the quantity of interest is a coarse integer rank
#: rather than a statistic needing three decimal places.
N_RANK_BOOTSTRAP = 1000

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


def stratified_auc(
    score: np.ndarray,
    positive: np.ndarray,
    population: np.ndarray,
    edges: tuple[float, ...] = POPULATION_STRATA,
) -> pd.DataFrame:
    """
    AUC within population bands, which is the test of whether the score adds signal
    beyond detecting small suburbs.

    DPA-positive suburbs have a median population of 815 against 5,643 for the rest, so a
    measure that merely correlates with smallness will score a high AUC without knowing
    anything about GP supply. Holding population roughly fixed removes that channel: if
    discrimination survives inside a band, the score is carrying supply information; if it
    collapses to chance, it was reading size.

    Bands with fewer than 5 of either class are reported with an empty AUC rather than a
    number, because AUC on 2 positives is noise with a decimal point.
    """
    score = np.asarray(score, dtype=float)
    positive = np.asarray(positive, dtype=bool)
    population = np.asarray(population, dtype=float)
    rows = []
    bounds = (0.0,) + tuple(edges) + (float("inf"),)
    for lo, hi in zip(bounds, bounds[1:]):
        m = (population >= lo) & (population < hi)
        n_pos, n_neg = int((positive & m).sum()), int((~positive & m).sum())
        band = f"{lo:,.0f}-{hi:,.0f}" if np.isfinite(hi) else f"{lo:,.0f}+"
        if n_pos < 5 or n_neg < 5:
            rows.append({"band": band, "n": int(m.sum()), "positives": n_pos,
                         "auc": np.nan, "ci_low": np.nan, "ci_high": np.nan})
            continue
        point, lo_ci, hi_ci = auc_interval(score[m], positive[m], n=800)
        rows.append({"band": band, "n": int(m.sum()), "positives": n_pos,
                     "auc": point, "ci_low": lo_ci, "ci_high": hi_ci})
    return pd.DataFrame(rows)


def nested_logistic(frame, base, added, outcome="dpa_positive", cluster="gpc_code"):
    """
    Fit two nested logistic models and compare them by likelihood ratio test.

    The question the AUC comparison could only answer by eye: does ``added`` carry
    information about ``outcome`` BEYOND what ``base`` already explains? Fitting
    ``outcome ~ base`` and ``outcome ~ base + added`` and comparing the log-likelihoods
    answers it directly, and the coefficient is interpretable where an AUC difference is
    not.

    ``LR = 2 * (loglik_full - loglik_reduced)`` is distributed chi-square with degrees of
    freedom equal to the number of added terms, under the null that the added terms have
    zero coefficients.

    THE P-VALUE ASSUMES INDEPENDENT OBSERVATIONS AND THEY ARE NOT. Moran's I on
    ``access_gap`` is 0.623 (p = 0.001), so suburbs near each other carry similar values
    and the model sees less information than its row count suggests. The full model is
    therefore refitted with standard errors clustered on ``cluster``, which does not
    assume independence between suburbs in the same GP catchment. Compare the two z
    statistics: if clustering roughly halves it, the naive inference was overconfident by
    about that much.

    Returns a dict, not a printed table, so the caller decides what to report.
    """
    import statsmodels.api as sm

    df = frame.dropna(subset=[outcome] + list(base) + list(added)).copy()
    y = df[outcome].to_numpy(float)
    X0 = sm.add_constant(df[list(base)].to_numpy(float), has_constant="add")
    X1 = sm.add_constant(df[list(base) + list(added)].to_numpy(float), has_constant="add")

    m0 = sm.Logit(y, X0).fit(disp=0)
    m1 = sm.Logit(y, X1).fit(disp=0)
    lr = 2.0 * (m1.llf - m0.llf)
    dof = len(added)
    p_lr = float(stats.chi2.sf(lr, dof))

    clustered = sm.Logit(y, X1).fit(
        disp=0, cov_type="cluster", cov_kwds={"groups": df[cluster].to_numpy()}
    )
    names = ["const"] + list(base) + list(added)
    terms = []
    for i, nm in enumerate(names):
        terms.append({
            "term": nm,
            "coef": float(m1.params[i]),
            "odds_ratio": float(np.exp(m1.params[i])),
            "se_naive": float(m1.bse[i]),
            "se_clustered": float(clustered.bse[i]),
            "z_naive": float(m1.tvalues[i]),
            "z_clustered": float(clustered.tvalues[i]),
            "p_clustered": float(clustered.pvalues[i]),
        })
    return {
        "n": len(df), "events": int(y.sum()),
        "llf_reduced": float(m0.llf), "llf_full": float(m1.llf),
        "aic_reduced": float(m0.aic), "aic_full": float(m1.aic),
        "lr_stat": float(lr), "lr_df": dof, "lr_p": p_lr,
        "pseudo_r2_reduced": float(m0.prsquared), "pseudo_r2_full": float(m1.prsquared),
        "terms": terms,
    }


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


def local_morans_i(values: np.ndarray, w: np.ndarray) -> np.ndarray:
    """
    Local Moran's I per observation: ``I_i = n * z_i * (Wz)_i / sum(z^2)``.

    Scaled so the MEAN of the local statistics is exactly the global statistic. That
    identity is what makes this a decomposition of :func:`morans_i` rather than a second,
    separately defined quantity that happens to look similar.

    >>> xy = np.array([[float(i), 0.0] for i in range(8)])
    >>> w = knn_weights(xy, k=2)
    >>> v = np.array([1.0, 1.0, 1.0, 1.0, 9.0, 9.0, 9.0, 9.0])
    >>> bool(np.isclose(local_morans_i(v, w).mean(), morans_i(v, w)))
    True

    A value sitting among others like it scores positive, and the two at the boundary
    between the blocks score lowest, each being the one least like its neighbours:

    >>> li = local_morans_i(v, w)
    >>> bool(li[0] > 0), bool(li[3] < li[0]), bool(li[4] < li[7])
    (True, True, True)

    Constant input has no variance, and yields zeros rather than a divide by zero:

    >>> local_morans_i(np.ones(8), w).tolist()
    [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    """
    z = np.asarray(values, dtype=float)
    z = z - z.mean()
    denom = float(z @ z)
    if denom == 0.0:
        return np.zeros_like(z)
    return z.size * z * (w @ z) / denom


def lisa_quadrants(values: np.ndarray, w: np.ndarray) -> np.ndarray:
    """
    Label each observation HH, LL, HL or LH from its own value and its neighbours' mean.

    HL is the label that matters commercially: a suburb scoring high while its neighbours
    score low is a LOCAL anomaly, so the opportunity is not shared with an adjacent
    candidate. HH is a suburb that scores well because its whole region does, which is
    the peninsula effect and is a weaker claim about the suburb itself.

    >>> xy = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [3.0, 0.0]])
    >>> w = knn_weights(xy, k=1)
    >>> lisa_quadrants(np.array([9.0, 0.0, 0.0, 0.0]), w).tolist()
    ['HL', 'LH', 'LL', 'LL']
    """
    z = np.asarray(values, dtype=float)
    z = z - z.mean()
    lag = w @ z
    out = np.empty(z.size, dtype=object)
    for i in range(z.size):
        hi = z[i] > 0
        hi_lag = lag[i] > 0
        out[i] = "HH" if hi and hi_lag else "HL" if hi else "LH" if hi_lag else "LL"
    return out


def benjamini_hochberg(p: np.ndarray, alpha: float = 0.05) -> np.ndarray:
    """
    Benjamini-Hochberg step-up: which p-values survive at false discovery rate ``alpha``.

    Needed because LISA runs one test per suburb. At 443 suburbs and alpha 0.05 about 22
    "significant" clusters appear in pure noise, so an uncorrected LISA map is a
    well-known way to report nothing with confidence.

    Thresholds are ``alpha * i / n``, so for five tests they are 0.01, 0.02, 0.03, 0.04,
    0.05. Only the first two p-values clear their own threshold:

    >>> benjamini_hochberg(np.array([0.001, 0.008, 0.039, 0.041, 0.9])).tolist()
    [True, True, False, False, False]

    Step-up: 0.04 fails its own threshold of 0.0333, but a larger p-value below it clears
    0.05, so everything up to that point is accepted.

    >>> benjamini_hochberg(np.array([0.001, 0.04, 0.045])).tolist()
    [True, True, True]

    Nothing significant returns all False rather than raising:

    >>> benjamini_hochberg(np.array([0.06, 0.2, 0.5])).tolist()
    [False, False, False]
    """
    p = np.asarray(p, dtype=float)
    n = p.size
    order = np.argsort(p)
    ranked = p[order]
    thresholds = alpha * np.arange(1, n + 1) / n
    passed = ranked <= thresholds
    out = np.zeros(n, dtype=bool)
    if passed.any():
        cutoff = int(np.flatnonzero(passed).max())
        out[order[: cutoff + 1]] = True
    return out


def lisa_test(
    values: np.ndarray,
    w: np.ndarray,
    n: int = N_PERMUTATIONS,
    seed: int = RNG_SEED,
    alpha: float = 0.05,
) -> pd.DataFrame:
    """
    LISA with a CONDITIONAL permutation null and a Benjamini-Hochberg FDR filter.

    Conditional means each observation keeps its own value while its neighbours are
    resampled from the rest. That is the correct null for a local statistic: the question
    is "given this suburb's score, are its neighbours more alike than chance would give",
    not "could this whole map have arisen at random", which is what the global test asks.

    The pseudo p-value is two-sided, because an unusually HIGH local I (a cluster) and an
    unusually low one (a spatial outlier) are both findings.
    """
    z = np.asarray(values, dtype=float)
    z = z - z.mean()
    size = z.size
    denom = float(z @ z)
    positive = w[w > 0]
    k = int(round(1.0 / positive.min())) if positive.size else 1
    observed = local_morans_i(values, w)
    rng = np.random.default_rng(seed)
    p_values = np.empty(size, dtype=float)
    for i in range(size):
        others = np.delete(z, i)
        draws = rng.choice(others, size=(n, k), replace=True).mean(axis=1)
        null_i = size * z[i] * draws / denom if denom else np.zeros(n)
        extreme = int((np.abs(null_i) >= abs(observed[i])).sum())
        p_values[i] = (1 + extreme) / (1 + n)
    return pd.DataFrame({
        "local_i": observed,
        "p_value": p_values,
        "quadrant": lisa_quadrants(values, w),
        "significant_fdr": benjamini_hochberg(p_values, alpha),
    })


def spatial_clusters(significant: np.ndarray, quadrant: np.ndarray, w: np.ndarray) -> np.ndarray:
    """
    Connected components of the significant HH suburbs under the spatial weight graph.

    A suburb belongs to a cluster only if it is BOTH significant and high-high, and two
    such suburbs join the same cluster when either is among the other's k nearest
    neighbours. Everything else gets an empty label.

    This is what turns LISA from a per-suburb verdict into a group identity, which is
    what the shortlist needs: not "is Collaroy significant" but "is Collaroy the same
    opportunity as Avalon Beach".

    Two adjacent significant HH suburbs merge; an isolated one stands alone; a
    non-significant suburb between them does NOT bridge them.

    >>> xy = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [90.0, 0.0]])
    >>> w = knn_weights(xy, k=1)
    >>> sig = np.array([True, True, False, True])
    >>> quad = np.array(["HH", "HH", "HH", "HH"])
    >>> spatial_clusters(sig, quad, w).tolist()
    ['SC1', 'SC1', '', 'SC2']

    Significant but not HH is not a cluster member, because a LOW cluster is not a
    shared opportunity:

    >>> spatial_clusters(np.array([True, True, False, True]),
    ...                  np.array(["LL", "LL", "HH", "HH"]), w).tolist()
    ['', '', '', 'SC1']
    """
    member = np.asarray(significant, dtype=bool) & (np.asarray(quadrant) == "HH")
    size = member.size
    parent = list(range(size))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    adjacency = (w > 0) | (w.T > 0)
    for i in range(size):
        if not member[i]:
            continue
        for j in range(i + 1, size):
            if member[j] and adjacency[i, j]:
                union(i, j)

    labels = np.array([""] * size, dtype=object)
    seen: dict[int, str] = {}
    for i in range(size):
        if not member[i]:
            continue
        root = find(i)
        if root not in seen:
            seen[root] = f"SC{len(seen) + 1}"
        labels[i] = seen[root]
    return labels


def bootstrap_ranks(
    suburbs: np.ndarray,
    scores: np.ndarray,
    blocks: np.ndarray | None,
    n: int = N_RANK_BOOTSTRAP,
    top_n: int = 10,
    seed: int = RNG_SEED,
) -> pd.DataFrame:
    """
    Resampling interval on each suburb's rank, optionally blocked on ``blocks``.

    Rank is relative: a suburb's position depends on which others it is measured against,
    so the question is how much the rank depends on the comparison set.

    blocks=None resamples individual suburbs and assumes they are independent, which
    morans_i shows they are not. Passing GP catchment codes keeps correlated suburbs
    together. Measured, that changes the top-10 intervals by about 7 percent - far less
    than the global Moran's I suggested, because a rank depends on how many suburbs
    outscore you and that count barely notices whether neighbours arrive together.

    Two reasons the intervals are a lower bound. Measurement error in population, income
    and the register is not modelled (uncertainty.py does population). And scores never
    move, so the highest scorer is rank 1 in every draw by construction - an interval of
    [1, 1] for the leader is arithmetic, not robustness.

    Two suburbs, one clearly better, ranks are certain:

    >>> out = bootstrap_ranks(np.array(["a", "b"]), np.array([1.0, 0.0]), None, n=50)
    >>> out.loc[out["suburb"] == "a", "rank_median"].item()
    1.0
    >>> out.loc[out["suburb"] == "b", "rank_median"].item()
    2.0
    """
    suburbs = np.asarray(suburbs)
    scores = np.asarray(scores, dtype=float)
    rng = np.random.default_rng(seed)
    size = suburbs.size
    seen = {s: [] for s in suburbs}

    if blocks is None:
        units = [np.array([i]) for i in range(size)]
    else:
        blocks = np.asarray(blocks)
        units = [np.flatnonzero(blocks == b) for b in pd.unique(blocks)]

    for _ in range(n):
        picked = rng.integers(0, len(units), size=len(units))
        idx = np.concatenate([units[j] for j in picked])
        ranks = pd.Series(-scores[idx]).rank(method="min").to_numpy()
        for name, r in zip(suburbs[idx], ranks):
            seen[name].append(r)

    rows = []
    for name in suburbs:
        draws = np.array(seen[name], dtype=float)
        if draws.size == 0:
            rows.append({"suburb": name, "appearances": 0, "rank_median": np.nan,
                         "rank_lo": np.nan, "rank_hi": np.nan, "p_top_n": np.nan})
            continue
        lo, hi = np.percentile(draws, [2.5, 97.5])
        rows.append({
            "suburb": name,
            "appearances": int(draws.size),
            "rank_median": float(np.median(draws)),
            "rank_lo": float(lo),
            "rank_hi": float(hi),
            "p_top_n": float((draws <= top_n).mean()),
        })
    return pd.DataFrame(rows)


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
    elig_mask = flagged["eligible"].to_numpy(bool)
    print(f"  {'measure':26s} {'all 680':>16s} {'eligible 443':>18s}")
    for col in ("access_gap_unguarded", "access_gap", "demand_scale",
                "mixed_billing_fit", "site_score"):
        if col not in flagged:
            continue
        v = flagged[col].to_numpy(float)
        p_all, lo_all, hi_all = auc_interval(v, positive)
        pe, lo_e, hi_e = auc_interval(v[elig_mask], positive[elig_mask])
        chance = " *" if lo_e < 0.5 < hi_e else ""
        print(f"  {col:26s} {p_all:6.3f} [{lo_all:.2f},{hi_all:.2f}] "
              f"{pe:8.3f} [{lo_e:.2f},{hi_e:.2f}]{chance}")
        rows.append({"test": "auc_vs_dpa_all680", "measure": col, "statistic": p_all,
                     "ci_low": lo_all, "ci_high": hi_all, "p_value": ""})
        rows.append({"test": "auc_vs_dpa_eligible", "measure": col, "statistic": pe,
                     "ci_low": lo_e, "ci_high": hi_e, "p_value": ""})
    print("  * interval contains 0.5, i.e. not distinguishable from chance")

    print()
    print("  BASELINE. A measure that knows nothing about GP supply, for comparison:")
    for label, v in (("population < 1,000 flag", (flagged["population"] < 1000).astype(float).to_numpy()),
                     ("-log(population)", -np.log(flagged["population"].clip(lower=1).to_numpy(float)))):
        point, lo, hi = auc_interval(v, positive)
        print(f"  {label:26s} {point:6.3f} [{lo:.2f},{hi:.2f}]")
        rows.append({"test": "auc_vs_dpa_baseline", "measure": label, "statistic": point,
                     "ci_low": lo, "ci_high": hi, "p_value": ""})

    print()
    print("  WITHIN POPULATION BANDS. If discrimination survives here the score carries")
    print("  supply information; if it collapses, it was reading suburb size.")
    strat = stratified_auc(flagged["access_gap_unguarded"].to_numpy(float), positive,
                           flagged["population"].to_numpy(float))
    print(f"  {'band':>16s} {'n':>5s} {'DPA+':>5s} {'AUC':>7s} {'95% CI':>16s}")
    for r in strat.itertuples():
        if np.isnan(r.auc):
            print(f"  {r.band:>16s} {r.n:5d} {r.positives:5d} {'too few':>7s}")
            continue
        chance = " *" if r.ci_low < 0.5 < r.ci_high else ""
        print(f"  {r.band:>16s} {r.n:5d} {r.positives:5d} {r.auc:7.3f} "
              f"[{r.ci_low:.2f},{r.ci_high:.2f}]{chance}")
        rows.append({"test": "auc_vs_dpa_stratified", "measure": r.band,
                     "statistic": r.auc, "ci_low": r.ci_low, "ci_high": r.ci_high,
                     "p_value": ""})

    print()
    print("  DOES access_gap ADD ANYTHING BEYOND SUBURB SIZE? Nested logistic models,")
    print("  compared by likelihood ratio test.")
    flagged["dpa_positive"] = positive.astype(float)
    flagged["log_population"] = np.log(flagged["population"].clip(lower=1))
    for label, sub in (("all 680", flagged),
                       ("eligible 443", flagged.loc[elig_mask])):
        try:
            fit = nested_logistic(sub, ["log_population"], ["access_gap_unguarded"])
        except Exception as exc:  # noqa: BLE001 - a singular fit must not stall the run
            print(f"    {label}: model did not converge ({type(exc).__name__})")
            continue
        print(f"    {label}: n={fit['n']}, {fit['events']} DPA-positive")
        print(f"      LR = {fit['lr_stat']:.2f} on {fit['lr_df']} df, p = {fit['lr_p']:.3g}"
              f"   AIC {fit['aic_reduced']:.1f} -> {fit['aic_full']:.1f}")
        for t in fit["terms"]:
            if t["term"] == "const":
                continue
            print(f"      {t['term']:22s} coef {t['coef']:+7.3f}  OR {t['odds_ratio']:7.3f}"
                  f"  z {t['z_naive']:+6.2f} naive / {t['z_clustered']:+6.2f} clustered"
                  f"  p {t['p_clustered']:.3g}")
            rows.append({"test": f"logit_{label.replace(' ', '')}", "measure": t["term"],
                         "statistic": t["coef"], "ci_low": t["se_clustered"],
                         "ci_high": t["odds_ratio"], "p_value": t["p_clustered"]})
        rows.append({"test": f"logit_lr_{label.replace(' ', '')}", "measure": "access_gap_unguarded",
                     "statistic": fit["lr_stat"], "ci_low": "", "ci_high": "",
                     "p_value": fit["lr_p"]})
    print("  z clustered on GP catchment does not assume suburbs are independent;")
    print("  the gap between the two z values is the cost of the spatial dependence.")

    print()
    print("=" * 78)
    print("2. SPATIAL AUTOCORRELATION, Moran's I under a permutation null")
    print("=" * 78)
    elig = df[df["eligible"] == True]
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

    print()
    print("=" * 78)
    print("3. LOCAL CLUSTERS, LISA with a conditional null and FDR control")
    print("=" * 78)
    lisa = lisa_test(elig["site_score"].to_numpy(float), w)
    lisa.insert(0, "suburb", elig["suburb"].to_numpy())
    lisa["rank"] = elig["rank"].to_numpy()
    sig = lisa[lisa["significant_fdr"]]
    raw = int((lisa["p_value"] <= 0.05).sum())
    print(f"  {len(sig)} of {len(lisa)} suburbs significant at FDR 0.05 "
          f"({raw} before correction)")
    print("  by quadrant: " + ", ".join(
        f"{q} {int((sig['quadrant'] == q).sum())}" for q in ("HH", "HL", "LH", "LL")))
    print()
    print("  HH = good suburb in a good region, i.e. the peninsula effect")
    print("  HL = good suburb among poorly scoring neighbours: a LOCAL anomaly, so the")
    print("       opportunity is not shared with an adjacent candidate")
    print()
    top = lisa[lisa["rank"] <= 10].sort_values("rank")
    print(f"  {'rank':>4s}  {'suburb':22s} {'local I':>8s} {'p':>7s}  {'quad':4s} {'sig':>4s}")
    for r in top.itertuples():
        mark = "yes" if r.significant_fdr else "no"
        print(f"  {int(r.rank):>4d}  {r.suburb:22s} {r.local_i:8.3f} {r.p_value:7.3f}  "
              f"{r.quadrant:4s} {mark:>4s}")
    hl = sig[sig["quadrant"] == "HL"].sort_values("rank")
    print()
    if len(hl):
        print("  Significant HL suburbs (local anomalies), best rank first:")
        for r in hl.head(8).itertuples():
            print(f"    {r.suburb:22s} rank {int(r.rank):>3d}  I = {r.local_i:6.3f}")
    else:
        print("  No significant HL suburbs: every high scorer sits in a high-scoring")
        print("  neighbourhood, so no candidate is a purely local anomaly.")
    lisa["spatial_cluster"] = spatial_clusters(
        lisa["significant_fdr"].to_numpy(bool), lisa["quadrant"].to_numpy(), w
    )
    named = lisa[lisa["spatial_cluster"] != ""]
    print()
    print(f"  {named['spatial_cluster'].nunique()} connected high-high cluster(s), "
          f"used by site_score.py to group suburbs into single opportunities:")
    for cid, grp in named.groupby("spatial_cluster"):
        members = ", ".join(grp.sort_values("rank")["suburb"].head(6))
        print(f"    {cid}: {len(grp)} suburbs - {members}")
    print()
    print("  STABILITY OF THE CLUSTERING, which decides whether these are one")
    print("  opportunity or several. k is a free parameter with no principled value:")
    for kk in (6, 8, 12, 16, 24):
        wk = knn_weights(xy, kk)
        lk = lisa_test(elig["site_score"].to_numpy(float), wk, n=299)
        ck = spatial_clusters(
            lk["significant_fdr"].to_numpy(bool), lk["quadrant"].to_numpy(), wk
        )
        members = pd.DataFrame({"suburb": elig["suburb"].to_numpy(), "c": ck})
        members = members[members["c"] != ""]
        groups = members.groupby("c")["suburb"].apply(list)
        together = any(
            "Avalon Beach" in g and "Collaroy" in g for g in groups
        )
        print(f"    k={kk:<3d} {members['c'].nunique()} cluster(s), "
              f"{len(members):>2d} suburbs | Avalon Beach and Collaroy in ONE cluster: "
              f"{'yes' if together else 'no'}")
    print()
    print("  The answer FLIPS on k, so cluster membership is advisory only. The")
    print("  shortlist groups on GP catchment, which comes from Medicare patient flows")
    print("  and is a measurement rather than a tuning choice.")
    lisa.to_csv(ROOT / "eda" / "lisa_site_score.csv", index=False)
    for r in lisa.itertuples():
        rows.append({"test": "lisa_site_score", "measure": r.suburb,
                     "statistic": r.local_i, "ci_low": "", "ci_high": "",
                     "p_value": r.p_value})

    print()
    print("=" * 78)
    print("4. RANK UNCERTAINTY, bootstrap blocked on GP catchment")
    print("=" * 78)
    names = elig["suburb"].to_numpy()
    vals = elig["site_score"].to_numpy(float)
    blocked = bootstrap_ranks(names, vals, elig["gpc_code"].to_numpy())
    naive = bootstrap_ranks(names, vals, None)
    merged = (
        blocked.merge(naive, on="suburb", suffixes=("", "_naive"))
        .merge(elig[["suburb", "rank"]], on="suburb")
        .sort_values("rank")
    )
    n_blocks = int(pd.unique(elig["gpc_code"]).size)
    print(f"  {N_RANK_BOOTSTRAP} resamples of {n_blocks} catchments "
          f"covering {len(elig)} eligible suburbs")
    print()
    print(f"  {'rank':>4s}  {'suburb':20s} {'median':>7s} {'95% interval':>16s} "
          f"{'P(top10)':>9s} {'naive width':>12s}")
    top = merged[merged["rank"] <= 10]
    for r in top.itertuples():
        width = r.rank_hi - r.rank_lo
        width_naive = r.rank_hi_naive - r.rank_lo_naive
        print(f"  {int(r.rank):>4d}  {r.suburb:20s} {r.rank_median:7.0f} "
              f"{f'[{r.rank_lo:.0f}, {r.rank_hi:.0f}]':>16s} {r.p_top_n:9.2f} "
              f"{width_naive:7.0f} vs {width:.0f}")
    blocked_w = (top["rank_hi"] - top["rank_lo"]).mean()
    naive_w = (top["rank_hi_naive"] - top["rank_lo_naive"]).mean()
    print()
    print(f"  Mean 95% interval width over the top 10: blocked {blocked_w:.1f} ranks, "
          f"naive {naive_w:.1f}")
    print(f"  Blocking widens the interval by only "
          f"{100 * (1 - naive_w / blocked_w):.0f} percent. The global Moran's I in "
          f"section 2 predicted")
    print("  a larger gap; it does not appear because a rank depends on how many "
          "suburbs outscore")
    print("  you, and that count barely notices whether correlated neighbours arrive "
          "together.")
    print()
    print("  Resampling never changes a score, so the top scorer is rank 1 in every draw.")
    print("  [1, 1] for the leader is arithmetic, not robustness - read the rows below it.")
    merged.to_csv(ROOT / "eda" / "rank_bootstrap.csv", index=False)
    for r in merged.itertuples():
        rows.append({"test": "rank_bootstrap_blocked", "measure": r.suburb,
                     "statistic": r.rank_median, "ci_low": r.rank_lo,
                     "ci_high": r.rank_hi, "p_value": ""})

    pd.DataFrame(rows).to_csv(OUT, index=False)
    print()
    print(f"wrote {OUT} ({len(rows)} rows)")


if __name__ == "__main__":
    import doctest

    failures, _ = doctest.testmod(verbose=False)
    if failures:
        raise SystemExit(f"{failures} doctest failures")
    main()
