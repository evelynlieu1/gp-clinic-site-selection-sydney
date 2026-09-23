"""
GP Catchments (GPC) 2023: real boundaries, and an assignment for every project suburb.

Why this module exists
----------------------
Supply was previously counted *inside* suburb polygons. Croydon ranked #1 on one
practice inside its own boundary while sitting between Burwood and Ashfield with 21
general practices within 1.5 km. A suburb polygon is the wrong unit for a market: it
is an address-labelling convention, not a description of where patients go.

GP Catchments are the Department of Health's answer to exactly that problem. They are
custom aggregations of ABS ASGS 2021 SA1 / SA2 / SA3 / UCL, built by aggregating
sub-catchments using **MBS patient and provider flow data** plus population, GP
workforce, GP infrastructure, accessibility, topography and administrative boundaries.
They are empirical: they describe observed service-seeking behaviour.

Source and provenance
---------------------
Dataset:   "General Practitioner Catchments (GPC) 2023"
Publisher: Australian Government Department of Health, Disability and Ageing
Portal:    data.gov.au package ``cc26a27b-3b37-4922-a8a1-700857eb4888``
Release:   December 2023 update (first approved for use 2019)
Vintage:   ASGS 2021 boundaries, Medicare flow block CY2017-21
Licence:   Creative Commons Attribution 2.5 Australia (CC-BY 2.5 AU)
Contact:   HWDDataGovernance@health.gov.au

Attribution required by the licence: "Department of Health, Disability and Ageing,
General Practitioner Catchments (GPC) 2023, CC-BY 2.5 AU."

The published shapefile is boundary-only: 826 features, 9 attribute fields, no
population and no workforce counts. Anything beyond geometry has to be joined on.

Access note
-----------
``data.gov.au`` portal *pages* reject some automated clients, but the CKAN API and the
direct resource download URLs both answer normally. This module goes straight to the
resource URL rather than scraping the dataset page.

Suburb geometry
---------------
Project suburb names are matched to **ABS ASGS 2021 Suburbs and Localities (SAL)**
polygons, GDA2020. Two reasons SAL rather than the OSM ``place=suburb`` cache in
``eda/geo_cache``:

1. Coverage. The OSM cache is missing 30 of the 680 project suburbs, including
   Chatswood, Manly, Liverpool, Hornsby, Campbelltown, Camden, Richmond and Windsor,
   because OSM tags those as ``place=town``/``place=city`` rather than ``suburb``.
2. Lineage. GPC is built from ASGS 2021 units, so SAL shares its vintage and its
   underlying mesh blocks. Overlaying like with like keeps sliver artefacts down.

Do not resolve suburb identity using the ``area`` column in ``eda_data.csv``. That
column has verified copy and swap errors: ``Tennyson`` carries ``Tennyson Point``'s
area (0.4488 for both, ABS says Tennyson is 8.7031), ``Denistone`` carries
``Denistone East``'s (0.6976 for both), and ``Prospect`` / ``Pemulwuy`` are swapped
(13.96 / 3.71 against ABS 2.58 / 14.98). Names are matched; areas are only reported.

Catchment population
--------------------
GPC ships no population. Population is built here as an **areal apportionment** of ABS
SA2 Estimated Resident Population through the official SA2-2021-to-GPC-2023
correspondence, which publishes the intersecting area of each catchment inside each
SA2. Where an SA2 splits across catchments its ERP is divided in proportion to that
area, which assumes uniform density inside the SA2. That assumption is the main
weakness of the population figures and :func:`catchment_population` reports how much
of the total is exposed to it.
"""
from __future__ import annotations

import io
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "facility_cache"
DATA_PATH = ROOT / "scraped_data" / "eda_data.csv"
OUT_CSV = ROOT / "scraped_data" / "suburb_gp_catchment.csv"

# Equal-area projection for every area computation in this module.
# EPSG:3577 -- GDA94 / Australian Albers. Equal-area conic, the standard choice for
# national-scale area work in Australia. Areal overlap fractions are only meaningful
# in an equal-area CRS, hence decision point 3 of the task.
EQUAL_AREA_CRS = "EPSG:3577"

#: GPC 2023 boundary shapefile, zipped. data.gov.au resource 341f53c7-...
GPC_ZIP_URL = (
    "https://data.gov.au/data/dataset/cc26a27b-3b37-4922-a8a1-700857eb4888"
    "/resource/341f53c7-9344-4575-9467-2422df765bd4/download/gpc_2023.zip"
)
GPC_ZIP = CACHE_DIR / "gpc_2023.zip"
GPC_DIR = CACHE_DIR / "gpc_2023"
GPC_SHP = GPC_DIR / "GPC_2023.shp"

#: Official SA2 2021 to GPC 2023 correspondence, with intersecting areas.
GPC_SA2_XLSX_URL = (
    "https://data.gov.au/data/dataset/6218b1ad-be96-4e47-b28c-3fd83a483666"
    "/resource/5bb5a338-4d3e-4489-9c2a-af591c20523f/download/asgs-sa2-2021-gpc-2023.xlsx"
)
GPC_SA2_XLSX = CACHE_DIR / "asgs_sa2_2021_gpc_2023.xlsx"

#: ABS ASGS 2021 Suburbs and Localities, GDA2020, shapefile.
SAL_ZIP_URL = (
    "https://www.abs.gov.au/statistics/standards/"
    "australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/"
    "access-and-downloads/digital-boundary-files/SAL_2021_AUST_GDA2020_SHP.zip"
)
# The register task caches this under a lowercased name. Accept either so the two
# tasks share one 104 MB download instead of racing to overwrite each other.
SAL_ZIP_CANDIDATES = (
    CACHE_DIR / "abs_sal_2021_aust_gda2020_shp.zip",
    CACHE_DIR / "SAL_2021_AUST_GDA2020_SHP.zip",
)
SAL_DIR = CACHE_DIR / "abs_sal_2021"
SAL_SHP = SAL_DIR / "SAL_2021_AUST_GDA2020.shp"

#: SA2 Estimated Resident Population, ABS Data API, dataflow ABS,ERP_ASGS2021,1.0.0.
#: Key is MEASURE.SEX.AGE.REGION_TYPE.ASGS_2021.FREQ -- ERP, Persons, all ages, SA2.
ERP_CSV_URL = (
    "https://data.api.abs.gov.au/rest/data/ABS,ERP_ASGS2021,1.0.0"
    "/ERP.3.TOT.SA2..A?format=csv"
)
ERP_CSV = CACHE_DIR / "abs_sa2_erp.csv"

#: Geoscience Australia NHSD general practices, NSW, already filtered to
#: ``nhsd_service_type == 'General practice service'`` by the register task.
GA_PRACTICES_CSV = CACHE_DIR / "ga_general_practices_nsw_raw.csv"

#: A suburb is called straddling when this much of its area or more sits outside its
#: dominant catchment. 1% is above the sliver noise floor produced by overlaying a
#: GDA94 catchment layer on GDA2020 suburb polygons.
STRADDLE_THRESHOLD = 0.01

_USER_AGENT = "Mozilla/5.0 (compatible; nuvo-health-suburb-selection/1.0)"


def _download(url: str, dest: Path, label: str) -> Path:
    """Fetch ``url`` to ``dest`` once, then reuse the cached copy."""
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {label} ...", flush=True)
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(request, timeout=600) as response, tmp.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    tmp.replace(dest)
    print(f"  cached {dest.name} ({dest.stat().st_size:,} bytes)", flush=True)
    return dest


def _unzip_once(zip_path: Path, out_dir: Path, sentinel: Path) -> None:
    """Extract ``zip_path`` into ``out_dir`` unless ``sentinel`` is already there."""
    if sentinel.exists():
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(out_dir)


def load_gp_catchments() -> gpd.GeoDataFrame:
    """The 826 GPC 2023 catchments, reprojected to :data:`EQUAL_AREA_CRS`.

    Columns kept: ``gpc_code``, ``gpc_name``, ``state``, ``gpc_sqkm_published``,
    ``geometry``. ``gpc_sqkm_published`` is the department's own area field; the
    module recomputes area from geometry so the two can be compared.
    """
    _download(GPC_ZIP_URL, GPC_ZIP, "GPC 2023 boundaries (45 MB)")
    _unzip_once(GPC_ZIP, GPC_DIR, GPC_SHP)
    gdf = gpd.read_file(GPC_SHP)
    gdf = gdf.rename(
        columns={
            "GPC_CODE23": "gpc_code",
            "GPC_NAME23": "gpc_name",
            "STE_ABBR21": "state",
            "GPC_SQKM23": "gpc_sqkm_published",
        }
    )
    keep = ["gpc_code", "gpc_name", "state", "gpc_sqkm_published", "geometry"]
    return gdf[keep].to_crs(EQUAL_AREA_CRS)


def load_sal_boundaries(state: str = "New South Wales") -> gpd.GeoDataFrame:
    """ABS ASGS 2021 Suburbs and Localities polygons for ``state``, equal-area CRS."""
    zip_path = next((p for p in SAL_ZIP_CANDIDATES if p.exists()), None)
    if zip_path is None:
        zip_path = _download(SAL_ZIP_URL, SAL_ZIP_CANDIDATES[0], "ABS SAL 2021 (104 MB)")
    _unzip_once(zip_path, SAL_DIR, SAL_SHP)
    gdf = gpd.read_file(
        SAL_SHP, columns=["SAL_CODE21", "SAL_NAME21", "STE_NAME21", "AREASQKM21"]
    )
    gdf = gdf[gdf["STE_NAME21"] == state].copy()
    gdf = gdf.rename(
        columns={
            "SAL_CODE21": "sal_code",
            "SAL_NAME21": "sal_name",
            "AREASQKM21": "sal_sqkm_published",
        }
    )
    keep = ["sal_code", "sal_name", "sal_sqkm_published", "geometry"]
    return gdf[keep].to_crs(EQUAL_AREA_CRS)


def _strip_state_suffix(name: str) -> str:
    """``'Bangor (NSW)'`` -> ``'Bangor'``. Leaves LGA-disambiguated names alone."""
    text = str(name).strip()
    if text.endswith(")"):
        head, _, tail = text.rpartition("(")
        if head and tail[:-1].strip().upper() in {
            "NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT", "OT",
        }:
            return head.strip()
    return text


def _strip_lga_suffix(name: str) -> str:
    """``'Dural (Hornsby - NSW)'`` -> ``'Dural'``. Returns the input if no suffix."""
    text = str(name).strip()
    if text.endswith(")"):
        head, _, _tail = text.rpartition("(")
        if head:
            return head.strip()
    return text


def _norm(name: str) -> str:
    """Lowercase alphanumeric key. ``'Brighton-Le-Sands'`` -> ``'brighton le sands'``."""
    text = _strip_state_suffix(name).lower()
    return " ".join("".join(c if c.isalnum() else " " for c in text).split())


def match_suburbs_to_sal(
    suburbs: pd.Series, sal: gpd.GeoDataFrame
) -> tuple[gpd.GeoDataFrame, pd.DataFrame, list[str]]:
    """Attach an ABS SAL polygon to each project suburb name.

    Two passes. First an exact normalised-name match, which also absorbs the plain
    ``(NSW)`` suffix ABS adds to names duplicated interstate. Whatever is left over is
    an ABS name disambiguated by LGA -- ``Dural (Hornsby - NSW)`` against
    ``Dural (Singleton - NSW)`` -- so the second pass matches on the base name and
    breaks the tie **geographically**: the winner is the candidate closest to the
    footprint of the suburbs already matched in pass one. Area is deliberately not used
    as a tiebreak because ``eda_data.csv``'s ``area`` column is known to be wrong for
    several of these suburbs.

    Returns ``(matched, tiebreaks, unmatched)``.
    """
    sal = sal.copy()
    sal["key"] = sal["sal_name"].map(_norm)
    if sal["key"].duplicated().any():
        clashes = sorted(sal.loc[sal["key"].duplicated(keep=False), "sal_name"])
        raise ValueError(f"SAL normalised names are not unique: {clashes}")

    frame = pd.DataFrame({"suburb": suburbs.astype(str).str.strip()})
    frame["key"] = frame["suburb"].map(_norm)

    exact = frame.merge(sal, on="key", how="inner")
    exact_gdf = gpd.GeoDataFrame(exact, geometry="geometry", crs=sal.crs)

    remaining = frame[~frame["suburb"].isin(exact["suburb"])].copy()
    tiebreaks: list[dict[str, object]] = []
    resolved: list[dict[str, object]] = []
    unmatched: list[str] = []

    if not remaining.empty:
        # Footprint of the confidently matched suburbs, used as the geographic anchor.
        anchor = exact_gdf.geometry.union_all()
        sal_base = sal.copy()
        sal_base["base_key"] = sal_base["sal_name"].map(
            lambda n: _norm(_strip_lga_suffix(n))
        )
        for _, row in remaining.iterrows():
            candidates = sal_base[sal_base["base_key"] == row["key"]].copy()
            if candidates.empty:
                unmatched.append(row["suburb"])
                continue
            candidates["dist_km"] = candidates.geometry.distance(anchor) / 1000.0
            candidates = candidates.sort_values("dist_km")
            best = candidates.iloc[0]
            for _, cand in candidates.iterrows():
                tiebreaks.append(
                    {
                        "suburb": row["suburb"],
                        "sal_name": cand["sal_name"],
                        "sal_sqkm_published": cand["sal_sqkm_published"],
                        "dist_to_sydney_footprint_km": round(cand["dist_km"], 3),
                        "chosen": cand["sal_code"] == best["sal_code"],
                    }
                )
            resolved.append(
                {
                    "suburb": row["suburb"],
                    "key": row["key"],
                    "sal_code": best["sal_code"],
                    "sal_name": best["sal_name"],
                    "sal_sqkm_published": best["sal_sqkm_published"],
                    "geometry": best["geometry"],
                }
            )

    if resolved:
        extra = gpd.GeoDataFrame(resolved, geometry="geometry", crs=sal.crs)
        matched = gpd.GeoDataFrame(
            pd.concat([exact_gdf, extra], ignore_index=True), geometry="geometry", crs=sal.crs
        )
    else:
        matched = exact_gdf

    return matched, pd.DataFrame(tiebreaks), unmatched


def assign_catchments(
    suburb_polygons: gpd.GeoDataFrame, catchments: gpd.GeoDataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Assign each suburb polygon to its dominant-overlap catchment.

    Both layers must already be in :data:`EQUAL_AREA_CRS`. For every intersecting
    suburb/catchment pair the intersection area is divided by the suburb's own area to
    give ``overlap_fraction``; the largest fraction wins.

    Returns ``(assignment, all_overlaps)``. ``all_overlaps`` keeps every pair above
    the sliver floor so straddling can be inspected, not just asserted.
    """
    if suburb_polygons.crs != catchments.crs:
        raise ValueError("suburb and catchment layers must share a CRS")

    suburbs = suburb_polygons.copy()
    suburbs["suburb_area_m2"] = suburbs.geometry.area

    pairs = gpd.sjoin(
        suburbs[["suburb", "suburb_area_m2", "geometry"]],
        catchments[["gpc_code", "gpc_name", "geometry"]],
        how="left",
        predicate="intersects",
    )
    pairs = pairs.reset_index(names="suburb_idx")

    overlaps: list[dict[str, object]] = []
    catchment_geom = catchments.set_index("gpc_code").geometry
    for row in pairs.itertuples(index=False):
        if not isinstance(row.gpc_code, str):
            continue
        inter = row.geometry.intersection(catchment_geom.loc[row.gpc_code])
        if inter.is_empty:
            continue
        overlaps.append(
            {
                "suburb": row.suburb,
                "gpc_code": row.gpc_code,
                "gpc_name": row.gpc_name,
                "overlap_fraction": inter.area / row.suburb_area_m2,
                "overlap_km2": inter.area / 1e6,
                "suburb_km2": row.suburb_area_m2 / 1e6,
            }
        )

    all_overlaps = pd.DataFrame(overlaps).sort_values(
        ["suburb", "overlap_fraction"], ascending=[True, False]
    )
    dominant = all_overlaps.drop_duplicates("suburb", keep="first").copy()

    # Share of each suburb that is NOT in its dominant catchment, using every
    # intersecting catchment rather than just the runner-up.
    covered = all_overlaps.groupby("suburb")["overlap_fraction"].sum()
    n_catch = all_overlaps.groupby("suburb")["gpc_code"].nunique()
    dominant["catchments_touched"] = dominant["suburb"].map(n_catch)
    dominant["total_covered_fraction"] = dominant["suburb"].map(covered)
    dominant["straddles_catchment"] = (
        1.0 - dominant["overlap_fraction"]
    ) >= STRADDLE_THRESHOLD

    return dominant.reset_index(drop=True), all_overlaps.reset_index(drop=True)


def sa2_to_gpc_correspondence() -> pd.DataFrame:
    """Official SA2 2021 to GPC 2023 correspondence with intersecting areas."""
    _download(GPC_SA2_XLSX_URL, GPC_SA2_XLSX, "SA2 2021 to GPC 2023 correspondence")
    frame = pd.read_excel(GPC_SA2_XLSX, header=7).iloc[:, 1:]
    frame.columns = [
        "sa2_code",
        "sa2_name",
        "state",
        "sa2_sqkm",
        "gpc_code",
        "gpc_name",
        "gpc_sqkm",
        "gpc_area_in_sa2_sqkm",
    ]
    frame = frame.dropna(subset=["sa2_code"]).copy()
    frame["sa2_code"] = frame["sa2_code"].astype("int64")
    return frame


def sa2_erp() -> pd.DataFrame:
    """Latest ABS SA2 Estimated Resident Population, one row per SA2."""
    if not ERP_CSV.exists():
        _download(ERP_CSV_URL, CACHE_DIR / "abs_sa2_erp_raw.csv", "ABS SA2 ERP time series")
        raw = pd.read_csv(CACHE_DIR / "abs_sa2_erp_raw.csv")
        year = int(raw["TIME_PERIOD"].max())
        latest = raw.loc[raw["TIME_PERIOD"] == year, ["ASGS_2021", "OBS_VALUE"]]
        latest = latest.rename(columns={"ASGS_2021": "sa2_code", "OBS_VALUE": "erp"})
        latest["year"] = year
        latest.to_csv(ERP_CSV, index=False)
    frame = pd.read_csv(ERP_CSV)
    frame["sa2_code"] = frame["sa2_code"].astype("int64")
    return frame


def catchment_population() -> tuple[pd.DataFrame, dict[str, float]]:
    """Catchment ERP, apportioned from SA2 ERP by intersecting area.

    Returns ``(populations, diagnostics)``. ``diagnostics`` reports how much of the
    national ERP sits in SA2s that split across catchments, which is the population
    exposed to the uniform-density assumption.
    """
    corr = sa2_to_gpc_correspondence()
    erp = sa2_erp()
    merged = corr.merge(erp, on="sa2_code", how="left")

    missing = merged["erp"].isna().sum()
    denom = merged.groupby("sa2_code")["gpc_area_in_sa2_sqkm"].transform("sum")
    merged["share"] = np.where(denom > 0, merged["gpc_area_in_sa2_sqkm"] / denom, 0.0)
    merged["erp_apportioned"] = merged["erp"] * merged["share"]

    split_sa2 = merged.groupby("sa2_code")["gpc_code"].transform("nunique") > 1
    total_erp = float(erp["erp"].sum())
    diagnostics = {
        "erp_year": int(erp["year"].iloc[0]),
        "total_erp": total_erp,
        "sa2_rows_missing_erp": float(missing),
        "sa2s_split_across_catchments": float(merged.loc[split_sa2, "sa2_code"].nunique()),
        "erp_in_split_sa2s": float(merged.loc[split_sa2, "erp_apportioned"].sum()),
        "pct_erp_apportioned": 100.0 * merged.loc[split_sa2, "erp_apportioned"].sum() / total_erp,
    }

    populations = (
        merged.groupby(["gpc_code", "gpc_name"], as_index=False)["erp_apportioned"]
        .sum()
        .rename(columns={"erp_apportioned": "catchment_erp"})
    )
    populations["catchment_erp"] = populations["catchment_erp"].round(0)
    return populations, diagnostics


def practices_per_catchment(catchments: gpd.GeoDataFrame) -> pd.DataFrame:
    """Count Geoscience Australia NHSD general practices inside each catchment.

    Uses the register task's cached NSW extract. Practices are point-in-polygon
    assigned, so a practice is counted once, in the catchment it physically sits in.
    """
    if not GA_PRACTICES_CSV.exists():
        raise FileNotFoundError(
            f"{GA_PRACTICES_CSV} not found. Run the health-facility register task first."
        )
    frame = pd.read_csv(GA_PRACTICES_CSV)
    points = gpd.GeoDataFrame(
        frame,
        geometry=gpd.points_from_xy(frame["longitude"], frame["latitude"]),
        crs="EPSG:4326",
    ).to_crs(catchments.crs)
    joined = gpd.sjoin(
        points[["organisation_name", "suburb", "geometry"]],
        catchments[["gpc_code", "gpc_name", "geometry"]],
        how="left",
        predicate="within",
    )
    counts = (
        joined.dropna(subset=["gpc_code"])
        .groupby(["gpc_code", "gpc_name"], as_index=False)
        .size()
        .rename(columns={"size": "ga_practices"})
    )
    return counts


def build() -> pd.DataFrame:
    """Run the full assignment and write :data:`OUT_CSV`."""
    project = pd.read_csv(DATA_PATH)
    catchments = load_gp_catchments()
    sal = load_sal_boundaries()

    matched, tiebreaks, unmatched = match_suburbs_to_sal(project["suburb"], sal)
    print(f"Suburb polygons matched: {len(matched)} of {len(project)}", flush=True)
    if not tiebreaks.empty:
        print("\nLGA-disambiguated names resolved by distance to the Sydney footprint:")
        print(tiebreaks.to_string(index=False))
    if unmatched:
        print(f"\nNo ABS SAL polygon for: {unmatched}", file=sys.stderr)

    assignment, all_overlaps = assign_catchments(matched, catchments)

    out = assignment[
        ["suburb", "gpc_code", "gpc_name", "overlap_fraction", "straddles_catchment"]
    ].copy()
    out["overlap_fraction"] = out["overlap_fraction"].round(6)
    out = out.sort_values("suburb").reset_index(drop=True)
    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_CSV, index=False)
    print(f"\nWrote {OUT_CSV} ({len(out)} rows)", flush=True)
    return out


if __name__ == "__main__":
    build()
