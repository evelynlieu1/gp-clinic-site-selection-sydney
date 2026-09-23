"""
Canonical GP-supply aggregation for the suburb-selection pipeline.

Single source of truth for two things the rest of the project got wrong:

1. **Parsing clinic GP counts.** ``gp_clinics_all.csv`` stores ``number_of_gps`` as
   free text. Blank / ``unknown`` / ``not stated`` mean *we do not know*, and lower
   bounds like ``>=6`` mean *at least six*. Coercing all of these to ``0`` (the old
   ``pd.to_numeric(...).fillna(0)``) understates supply.

2. **Separating "no doctors" from "no data".** A suburb that never appeared in the
   clinic scrape is not a suburb with zero GPs. Because the pressure metric is
   ``population / (total_gp + 1)``, a missing suburb scores as maximum pressure and
   is promoted straight to the top of every shortlist. ``gp_data_status`` makes the
   difference explicit so rankings and maps can exclude it.

Status values (see ``GP_STATUS_*``):

- ``ok``                -- suburb is in the scrape and at least one clinic has a usable count.
- ``gp_count_unknown``  -- suburb is in the scrape with >=1 clinic, but no clinic has a
                           usable count. ``total_gp`` is 0 only because counts are missing.
- ``no_data``           -- suburb never appeared in the scrape. ``total_gp`` carries no
                           information; do not rank or colour it as low supply.

Only ``ok`` suburbs are safe to rank on a supply metric -- use :func:`rankable_mask`.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

GP_STATUS_OK = "ok"
GP_STATUS_COUNT_UNKNOWN = "gp_count_unknown"
GP_STATUS_NO_DATA = "no_data"

#: Status values for which ``total_gp`` reflects observed doctors.
RANKABLE_STATUSES = frozenset({GP_STATUS_OK})

# Text that explicitly means "we don't know", as opposed to a real zero.
_UNKNOWN_TOKENS = frozenset(
    {"", "unknown", "not stated", "not_stated", "n/a", "na", "none", "null", "nan", "?", "-"}
)

# ">=6", ">= 6", "≥6", "6+", "at least 6", "more than 6" -> lower bound of 6.
_LOWER_BOUND_RE = re.compile(
    r"^\s*(?:>=|>|≥|at\s+least|min(?:imum)?|more\s+than)\s*(\d+(?:\.\d+)?)\s*$|^\s*(\d+(?:\.\d+)?)\s*\+\s*$",
    re.IGNORECASE,
)

# "4-6", "4 to 6" -> take the midpoint, flagged as approximate.
# The dash alternatives are \u escapes, not literal characters: scraped text really does
# use en/em dashes for ranges, and a find-and-replace over long dashes in the project's
# report text must not silently stop this parsing them. Covers hyphen, non-breaking
# hyphen, figure dash, en dash, em dash, horizontal bar and minus sign.
_RANGE_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)?)\s*(?:[-\u2010\u2011\u2012\u2013\u2014\u2015\u2212]|to)\s*(\d+(?:\.\d+)?)\s*$",
    re.IGNORECASE,
)

# Some scrape batches record an explicit negative finding as a pseudo-clinic row, e.g.
# "No in-suburb GP clinic found,Beacon Hill,0". That is a *confirmed zero* -- the suburb
# was searched and nothing was there -- not a clinic staffed by zero doctors. Counting it
# as a clinic reports "1 clinic listed, 0 GPs" for what is really "no clinic in suburb".
_SENTINEL_CLINIC_RE = re.compile(
    r"no\s+(?:in-?suburb\s+)?(?:gp|general\s+practice|medical|clinic|practice)"
    r"|no\s+clinics?\s+found"
    r"|none\s+found"
    r"|not\s+found",
    re.IGNORECASE,
)


def is_sentinel_clinic(clinic_name: object) -> bool:
    """
    True for placeholder rows meaning "we searched this suburb and found no clinic".

    Such a row is evidence of a real zero, so the suburb still counts as scraped
    (``gp_data_status == "ok"``), but it must not inflate ``clinics_listed``.

    >>> is_sentinel_clinic("No in-suburb GP clinic found")
    True
    >>> is_sentinel_clinic("Gladesville Medical")
    False
    """
    if clinic_name is None:
        return False
    text = str(clinic_name).strip()
    if not text:
        return False
    return bool(_SENTINEL_CLINIC_RE.search(text))


def parse_gp_count(raw: object) -> tuple[float | None, bool]:
    """
    Parse one ``number_of_gps`` cell.

    Returns ``(count, is_lower_bound)``:

    - ``(n, False)``    -- an exact count of ``n`` doctors (``n`` may legitimately be 0).
    - ``(n, True)``     -- at least ``n`` doctors; the true value is >= ``n``.
    - ``(None, False)`` -- unusable (blank, ``unknown``, ``not stated``, unparseable).

    >>> parse_gp_count(4)
    (4.0, False)
    >>> parse_gp_count(">=6")
    (6.0, True)
    >>> parse_gp_count("unknown")
    (None, False)
    """
    if raw is None:
        return None, False
    if isinstance(raw, (int, float, np.integer, np.floating)):
        if pd.isna(raw):
            return None, False
        val = float(raw)
        return (val, False) if val >= 0 else (None, False)

    text = str(raw).strip()
    if text.casefold() in _UNKNOWN_TOKENS:
        return None, False

    try:
        val = float(text)
    except ValueError:
        pass
    else:
        return (val, False) if val >= 0 else (None, False)

    m = _LOWER_BOUND_RE.match(text)
    if m:
        digits = m.group(1) or m.group(2)
        return float(digits), True

    m = _RANGE_RE.match(text)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        if lo > hi:
            lo, hi = hi, lo
        # Midpoint is the best point estimate; flag it so callers know it is soft.
        return (lo + hi) / 2.0, True

    return None, False


def build_gp_supply_table(clinics: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate a clinic-level table to one row per suburb.

    ``clinics`` needs ``suburb``, ``clinic_name`` and ``number_of_gps`` columns
    (i.e. the schema of ``scraped_data/gp_clinics_all.csv``).

    Returned columns:

    ``suburb``               -- whitespace-stripped suburb name.
    ``total_gp``             -- sum of usable counts. A floor, not an exact total,
                                whenever ``total_gp_is_floor`` is True.
    ``clinics_listed``       -- real clinic records found (sentinel "none found" rows
                                excluded, so 0 here with status ``ok`` means a
                                *confirmed* absence of clinics).
    ``gp_counts_known``      -- clinics whose GP count was usable.
    ``gp_counts_missing``    -- clinics whose GP count was blank / unknown.
    ``total_gp_is_floor``    -- True if any contributing count was a lower bound, or
                                any clinic count was missing, so the real total is higher.
    ``gp_data_status``       -- ``ok`` or ``gp_count_unknown`` (never ``no_data``: every
                                suburb in this table appeared in the scrape).
    """
    required = {"suburb", "number_of_gps"}
    missing_cols = required - set(clinics.columns)
    if missing_cols:
        raise ValueError(f"clinics table missing column(s): {sorted(missing_cols)}")

    df = clinics.copy()
    df["suburb"] = df["suburb"].astype(str).str.strip()

    # "None found" placeholder rows are evidence of a real zero, not clinics.
    names = df["clinic_name"] if "clinic_name" in df.columns else pd.Series("", index=df.index)
    df["_sentinel"] = names.map(is_sentinel_clinic)

    parsed = df["number_of_gps"].map(parse_gp_count)
    df["_count"] = [p[0] for p in parsed]
    df["_is_floor"] = [p[1] for p in parsed]
    # Only real clinics contribute doctors, counts, or floor-ness.
    real = ~df["_sentinel"]
    df["_count"] = df["_count"].where(real)
    df["_is_floor"] = df["_is_floor"] & real
    df["_known"] = df["_count"].notna()
    df["_real"] = real

    grouped = df.groupby("suburb", as_index=False).agg(
        total_gp=("_count", "sum"),  # NaN counts are skipped by sum()
        clinics_listed=("_real", "sum"),
        gp_counts_known=("_known", "sum"),
        _any_bound=("_is_floor", "any"),
        _searched_empty=("_sentinel", "any"),
    )
    grouped["total_gp"] = grouped["total_gp"].fillna(0.0).astype(float)
    grouped["gp_counts_known"] = grouped["gp_counts_known"].astype(int)
    grouped["clinics_listed"] = grouped["clinics_listed"].astype(int)
    grouped["gp_counts_missing"] = grouped["clinics_listed"] - grouped["gp_counts_known"]

    # Total is a floor if a lower bound fed into it, or a real clinic contributed nothing.
    grouped["total_gp_is_floor"] = grouped["_any_bound"] | (grouped["gp_counts_missing"] > 0)

    # "ok" means total_gp is usable evidence: either we counted doctors, or the scrape
    # explicitly reported no clinic in the suburb (a confirmed zero).
    confirmed_zero = grouped["_searched_empty"] & (grouped["clinics_listed"] == 0)
    grouped["gp_data_status"] = np.where(
        (grouped["gp_counts_known"] > 0) | confirmed_zero,
        GP_STATUS_OK,
        GP_STATUS_COUNT_UNKNOWN,
    )

    return grouped.drop(columns=["_any_bound", "_searched_empty"])[
        [
            "suburb",
            "total_gp",
            "clinics_listed",
            "gp_counts_known",
            "gp_counts_missing",
            "total_gp_is_floor",
            "gp_data_status",
        ]
    ]


def attach_gp_supply(
    suburbs: pd.DataFrame,
    clinics: pd.DataFrame,
    suburb_col: str = "suburb",
) -> pd.DataFrame:
    """
    Left-join per-suburb GP supply onto a suburb table, marking unscraped suburbs.

    Suburbs absent from ``clinics`` get ``total_gp = 0`` (kept for continuity with the
    existing schema) but ``gp_data_status == 'no_data'``, which is the flag every
    downstream ranking and map must respect. Never filter on ``total_gp == 0`` alone.
    """
    supply = build_gp_supply_table(clinics)

    out = suburbs.copy()
    keys = out[suburb_col].astype(str).str.strip()
    merged = out.assign(_key=keys).merge(
        supply.rename(columns={"suburb": "_key"}), on="_key", how="left"
    )
    merged = merged.drop(columns=["_key"])

    in_scrape = merged["gp_data_status"].notna()
    merged["total_gp"] = merged["total_gp"].fillna(0.0).astype(float)
    merged["clinics_listed"] = merged["clinics_listed"].fillna(0).astype(int)
    merged["gp_counts_known"] = merged["gp_counts_known"].fillna(0).astype(int)
    merged["gp_counts_missing"] = merged["gp_counts_missing"].fillna(0).astype(int)
    # .eq(True) maps the merge's NaNs to False without an object-dtype downcast.
    merged["total_gp_is_floor"] = merged["total_gp_is_floor"].eq(True).astype(bool)
    merged["gp_data_status"] = merged["gp_data_status"].where(in_scrape, GP_STATUS_NO_DATA)

    return merged


def rankable_mask(df: pd.DataFrame, status_col: str = "gp_data_status") -> pd.Series:
    """
    Boolean mask of rows whose ``total_gp`` may be used as a supply measure.

    Excludes ``no_data`` (never scraped) and ``gp_count_unknown`` (clinics found but
    every doctor count missing). Both would otherwise register as zero supply.
    """
    if status_col not in df.columns:
        raise ValueError(
            f"{status_col!r} column not found -- regenerate eda_data.csv via "
            "pre-processing.ipynb (merge cell) or rebuild_suburbs_final.py."
        )
    return df[status_col].isin(RANKABLE_STATUSES)


def require_status_column(df: pd.DataFrame, source: Path | str) -> None:
    """Fail loudly if a consumer is reading a pre-fix CSV with no ``gp_data_status``."""
    if "gp_data_status" not in df.columns:
        raise SystemExit(
            f"{source} has no 'gp_data_status' column.\n"
            "It predates the no-data fix. Regenerate it with:\n"
            "  python rebuild_suburbs_final.py"
        )


def status_summary(df: pd.DataFrame, status_col: str = "gp_data_status") -> str:
    """One-line-per-status breakdown, for script logging."""
    counts = df[status_col].value_counts()
    order = [GP_STATUS_OK, GP_STATUS_COUNT_UNKNOWN, GP_STATUS_NO_DATA]
    lines = []
    for status in order:
        n = int(counts.get(status, 0))
        if n:
            lines.append(f"  {status:18s} {n:4d}")
    return "\n".join(lines)
