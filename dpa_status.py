"""
Distribution Priority Area (DPA) status for the 680 Greater Sydney project suburbs.

DPA is a recruitment-cost lever, not a demand signal. Inside a DPA an operator can
employ doctors restricted under s19AB of the Health Insurance Act 1973 (international
medical graduates and foreign graduates of accredited medical schools); outside one it
must compete for unrestricted local GPs, which is slower and dearer.

Method and the two traps it avoids
----------------------------------
* **Geometry, not postcode.** The published postcode-to-catchment concordance chain
  mis-assigns suburbs (Campbelltown lands in "Bowral - Mittagong"). This module
  overlays real polygons in an equal-area projection and takes the dominant areal
  share, so the concordance never enters the chain. ``verify_campbelltown`` asserts
  the geometric answer.
* **No "MM1 is automatically non-DPA" shortcut.** That claim is false: fully-MM1
  catchments can be entirely DPA=Y (Richmond - Windsor is, in this very file). The
  status is read from the ``DPA_MM2`` attribute only. Note the shipped shapefile
  dropped the ``MMM_2023`` field during its final export, so Modified Monash class
  cannot be recovered from it -- another reason not to reason about MM at all here.

Sources
-------
DPA (GPs) 2025a, Department of Health, Disability and Ageing, current as at March 2025,
CC-BY 2.5 AU. Built from ABS ASGS 2021 SA1s, dissolved to GP catchment scale.
Suburb geometry: ABS ASGS Edition 3 SAL 2021 (Suburbs and Localities), the same
official suburb frame the project's population and income columns are reported on.

Suburb geometry deliberately does NOT come from the project's cached OSM
``place=suburb`` polygons: those are missing 30 of the 680 suburbs, and the missing
set includes Richmond and Windsor, which is precisely the DPA=Y cluster this task
exists to find. Using OSM here would have hidden the answer.
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA_PATH = ROOT / "scraped_data" / "eda_data.csv"
OUT_CSV = ROOT / "scraped_data" / "suburb_dpa_status.csv"
CACHE_DIR = ROOT / "facility_cache"

DPA_ZIP = CACHE_DIR / "dpa_gps_2025a.zip"
DPA_DIR = CACHE_DIR / "dpa_gps_2025a"
DPA_SHP = DPA_DIR / "DPA_GPs_2025a.shp"
DPA_URL = (
    "https://data.gov.au/data/dataset/7d889af7-9506-4eb5-930e-cefe6b5f39c1"
    "/resource/b08c23ce-5db4-4cc7-a473-d9db7d491464/download/dpa_gps_2025a.zip"
)

SAL_ZIP = CACHE_DIR / "abs_sal_2021_aust_gda2020_shp.zip"
SAL_DIR = CACHE_DIR / "abs_sal_2021"
SAL_SHP = SAL_DIR / "SAL_2021_AUST_GDA2020.shp"
SAL_URL = (
    "https://www.abs.gov.au/statistics/standards/"
    "australian-statistical-geography-standard-asgs-edition-3/jul2021-jun2026/"
    "access-and-downloads/digital-boundary-files/SAL_2021_AUST_GDA2020_SHP.zip"
)

#: GDA94 / Australian Albers. Equal-area, so intersection areas are comparable.
EQUAL_AREA_CRS = "EPSG:3577"

#: Generous box around Greater Sydney, used only to shrink the national DPA layer
#: before the overlay. Geometry is filtered, never clipped, so overlap fractions are
#: exact for any suburb wholly inside the box (all 680 are).
SYDNEY_BBOX = (150.2, -34.6, 151.8, -33.2)

#: Minority DPA share (of the suburb's DPA-covered area) at which the suburb is
#: reported as ambiguous. 10 per cent of a suburb is a big enough slice that a site
#: on the wrong side of the line would get the wrong recruitment answer.
AMBIGUOUS_MINORITY_SHARE = 0.10

#: Intersection parts below this share of the suburb are boundary artefacts, not real
#: overlap: SAL 2021 is GDA2020 and the DPA layer is GDA94 SA1 2021, so coincident
#: borders miss each other by centimetres and the overlay emits slivers. Measured on
#: the 2025a file, 532 of 1,308 suburb-catchment pairs are below 0.01 per cent of the
#: suburb (many under one square metre) and the next pair up is at 0.72 per cent
#: (the genuinely DPA=Y corner of Hornsby), so any cut inside that gap is the same
#: cut. Without it, Campbelltown reads as straddling Camden on 0.03 square metres.
SLIVER_SHARE = 0.001

#: Project suburb names that are not unique within NSW, so ABS appends the LGA.
#: Resolved to an explicit SAL_CODE21 rather than guessed, because the wrong pick is
#: a different part of the state (Singleton, Bega Valley, Lake Macquarie).
#: Every code below was checked to sit inside SYDNEY_BBOX by ``_resolve_geometry``.
SAL_CODE_OVERRIDES: dict[str, str] = {
    "darlington": "11215",  # Darlington (Sydney - NSW)
    "dural": "11331",  # Dural (Hornsby - NSW)
    "elderslie": "11394",  # Elderslie (Camden - NSW)
    "enmore": "11428",  # Enmore (Inner West - NSW)
    "greendale": "11765",  # Greendale (Liverpool - NSW)
    "kingswood": "12171",  # Kingswood (Penrith - NSW)
    "lilli pilli": "12331",  # Lilli Pilli (Sutherland Shire - NSW)
    "long point": "12390",  # Long Point (Campbelltown - NSW)
    "punchbowl": "13286",  # Punchbowl (Canterbury-Bankstown - NSW)
    "silverwater": "13562",  # Silverwater (Parramatta - NSW)
    "st clair": "13647",  # St Clair (Penrith - NSW)
    "summer hill": "13702",  # Summer Hill (Inner West - NSW)
    "the rocks": "13856",  # The Rocks (Sydney - NSW)
}

_STATE_SUFFIX_RE = re.compile(
    r"\s*\((?:[^()]*\s-\s)?(?:NSW|ACT|VIC|QLD|SA|WA|TAS|NT)\)\s*$", re.IGNORECASE
)


def norm_name(name: object) -> str:
    """Lowercase suburb key with any ABS ``(LGA - STATE)`` / ``(STATE)`` suffix removed."""
    text = _STATE_SUFFIX_RE.sub("", str(name).strip())
    return re.sub(r"\s+", " ", text).strip().lower()


def _require(path: Path, zip_path: Path, url: str, label: str) -> None:
    """Extract a cached zip on demand; fail loudly rather than approximate."""
    if path.exists():
        return
    if not zip_path.exists():
        print(
            f"Missing {label}. Download it to {zip_path} first:\n  curl -L -o "
            f'"{zip_path}" "{url}"',
            file=sys.stderr,
        )
        sys.exit(1)
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(path.parent)


def load_dpa() -> gpd.GeoDataFrame:
    """DPA polygons near Greater Sydney, in the equal-area CRS.

    The national file is dissolved by (catchment, Modified Monash, inner-metro flag,
    DPA), so one catchment can appear as several rows sharing a status. Rows are kept
    as-is and aggregated after the overlay.
    """
    _require(DPA_SHP, DPA_ZIP, DPA_URL, "DPA GPs 2025a shapefile")
    dpa = gpd.read_file(DPA_SHP, bbox=SYDNEY_BBOX)
    dpa = dpa[["DPA_Ctch", "DPA_MM2", "geometry"]].rename(
        columns={"DPA_Ctch": "dpa_area_name", "DPA_MM2": "dpa_status"}
    )
    dpa["dpa_status"] = dpa["dpa_status"].astype(str).str.strip().str.upper()
    unexpected = set(dpa["dpa_status"]) - {"Y", "N"}
    if unexpected:
        raise ValueError(f"Unexpected DPA_MM2 values: {sorted(unexpected)}")
    return dpa.to_crs(EQUAL_AREA_CRS)


def load_suburbs(frame: pd.DataFrame) -> gpd.GeoDataFrame:
    """Project suburbs joined to ABS SAL 2021 geometry, in the equal-area CRS."""
    _require(SAL_SHP, SAL_ZIP, SAL_URL, "ABS SAL 2021 shapefile")
    sal = gpd.read_file(SAL_SHP, bbox=SYDNEY_BBOX)
    sal = sal[sal["STE_NAME21"] == "New South Wales"].copy()
    sal["suburb_key"] = sal["SAL_NAME21"].map(norm_name)
    sal["SAL_CODE21"] = sal["SAL_CODE21"].astype(str)

    by_key = sal[~sal["suburb_key"].duplicated(keep=False)].set_index("suburb_key")
    by_code = sal.set_index("SAL_CODE21")

    keys = frame["suburb"].map(norm_name)
    rows: list[dict[str, object]] = []
    unmatched: list[str] = []
    for suburb, key in zip(frame["suburb"], keys):
        code = SAL_CODE_OVERRIDES.get(key)
        if code is not None:
            match = by_code.loc[code] if code in by_code.index else None
        else:
            match = by_key.loc[key] if key in by_key.index else None
        if match is None:
            unmatched.append(suburb)
            continue
        rows.append(
            {
                "suburb": suburb,
                "sal_name": match["SAL_NAME21"],
                "sal_code": code if code is not None else match.name,
                "geometry": match["geometry"],
            }
        )
    if unmatched:
        raise ValueError(f"No SAL 2021 geometry for: {unmatched}")

    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=sal.crs)
    if len(gdf) != len(frame):
        raise ValueError(f"Expected {len(frame)} suburb polygons, built {len(gdf)}")
    return gdf.to_crs(EQUAL_AREA_CRS)


def overlay_dpa(
    suburbs: gpd.GeoDataFrame, dpa: gpd.GeoDataFrame
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Areal intersection of suburbs with DPA polygons, with slivers dropped.

    Returns ``(by_status, by_catchment)``: area in square metres per suburb per DPA
    status, and per suburb per catchment per status. See :data:`SLIVER_SHARE` for why
    the tiny parts have to go.
    """
    parts = gpd.overlay(
        suburbs[["suburb", "geometry"]], dpa, how="intersection", keep_geom_type=True
    )
    parts["overlap_m2"] = parts.geometry.area
    parts = parts[parts["overlap_m2"] > 0.0]

    suburb_m2 = pd.Series(
        suburbs.geometry.area.to_numpy(), index=suburbs["suburb"].to_numpy()
    )
    share = parts["overlap_m2"] / parts["suburb"].map(suburb_m2)
    dropped = parts.loc[share < SLIVER_SHARE, "overlap_m2"]
    if len(dropped):
        print(
            f"  dropped {len(dropped)} sliver parts "
            f"(largest {dropped.max():.2f} m2, all under {100 * SLIVER_SHARE:.1f}% of "
            "their suburb)"
        )
    parts = parts[share >= SLIVER_SHARE]

    by_catchment = (
        parts.groupby(["suburb", "dpa_area_name", "dpa_status"], as_index=False)["overlap_m2"]
        .sum()
        .sort_values(["suburb", "overlap_m2"], ascending=[True, False])
    )
    by_status = (
        parts.groupby(["suburb", "dpa_status"], as_index=False)["overlap_m2"]
        .sum()
        .sort_values(["suburb", "overlap_m2"], ascending=[True, False])
    )
    return by_status, by_catchment


def assign_status(
    suburbs: gpd.GeoDataFrame, by_status: pd.DataFrame, by_catchment: pd.DataFrame
) -> pd.DataFrame:
    """Dominant-overlap DPA status per suburb, with an ambiguity flag.

    ``overlap_fraction`` is the winning status's share of the suburb's *DPA-covered*
    area, not of its total area. DPA polygons are land-based, so a coastal or
    harbour-side suburb has uncovered water; dividing by total area would report a
    fake ambiguity for Cronulla and a real one for nobody.
    """
    suburb_area = pd.Series(
        suburbs.geometry.area.to_numpy(), index=suburbs["suburb"].to_numpy(), name="suburb_m2"
    )
    covered = by_status.groupby("suburb")["overlap_m2"].sum()

    winners = by_status.drop_duplicates("suburb").set_index("suburb")
    shares = by_status.assign(
        share=by_status["overlap_m2"] / by_status["suburb"].map(covered)
    )
    minority = (
        shares[shares["dpa_status"] != shares["suburb"].map(winners["dpa_status"])]
        .groupby("suburb")["share"]
        .sum()
    )

    top_catchment = by_catchment.drop_duplicates("suburb").set_index("suburb")
    catchment_names = by_catchment.groupby("suburb")["dpa_area_name"].apply(
        lambda names: "; ".join(sorted(set(names)))
    )
    catchment_counts = by_catchment.groupby("suburb")["dpa_area_name"].nunique()

    out = pd.DataFrame({"suburb": suburbs["suburb"].to_numpy()})
    out["dpa_status"] = out["suburb"].map(winners["dpa_status"])
    out["dpa_area_name"] = out["suburb"].map(top_catchment["dpa_area_name"])
    out["overlap_fraction"] = (
        out["suburb"].map(winners["overlap_m2"]) / out["suburb"].map(covered)
    ).round(6)
    minority_share = out["suburb"].map(minority).fillna(0.0)
    out["ambiguous"] = minority_share >= AMBIGUOUS_MINORITY_SHARE
    out["minority_status_fraction"] = minority_share.round(6).to_numpy()
    out["dpa_catchments"] = out["suburb"].map(catchment_names)
    out["dpa_catchment_count"] = out["suburb"].map(catchment_counts).astype("Int64")
    out["dpa_covered_fraction"] = (
        out["suburb"].map(covered) / out["suburb"].map(suburb_area)
    ).round(6)
    out["sal_name"] = suburbs["sal_name"].to_numpy()
    if out["dpa_status"].isna().any():
        missing = out.loc[out["dpa_status"].isna(), "suburb"].tolist()
        raise ValueError(f"No DPA overlap survived for: {missing}")
    if (out["dpa_covered_fraction"] < 0.99).any():
        thin = out.loc[out["dpa_covered_fraction"] < 0.99, ["suburb", "dpa_covered_fraction"]]
        raise ValueError(f"DPA layer does not cover these suburbs:\n{thin}")
    return out


def verify_campbelltown(result: pd.DataFrame) -> None:
    """Assert the geometric answer differs from the broken postcode concordance."""
    row = result.loc[result["suburb"] == "Campbelltown"]
    if row.empty:
        return
    catchments = str(row["dpa_catchments"].iloc[0])
    if "Bowral" in catchments or catchments != "Campbelltown (NSW)":
        raise ValueError(
            "Campbelltown did not resolve cleanly into its own catchment; the "
            "postcode-concordance error (which gives 'Bowral - Mittagong') or a "
            f"sliver overlap has leaked into the geometry path (got {catchments!r})"
        )
    print(
        f"  postcode-trap check: Campbelltown -> {catchments} at "
        f"{row['overlap_fraction'].iloc[0]:.3f} of its area, DPA="
        f"{row['dpa_status'].iloc[0]}. The published concordance chain wrongly puts "
        "it in 'Bowral - Mittagong'."
    )


def _pct(part: float, whole: float) -> str:
    return f"{part:,.0f} ({0.0 if whole == 0 else 100.0 * part / whole:.1f}%)"


def main() -> None:
    frame = pd.read_csv(DATA_PATH)
    dpa = load_dpa()
    suburbs = load_suburbs(frame)
    by_status, by_catchment = overlay_dpa(suburbs, dpa)
    result = assign_status(suburbs, by_status, by_catchment)

    merged = result.merge(frame[["suburb", "population"]], on="suburb", how="left")
    is_y = merged["dpa_status"] == "Y"
    pop = merged["population"].fillna(0.0)

    print(f"DPA source: {DPA_SHP.name} ({len(dpa)} polygons near Sydney)")
    print(f"  catchments intersecting the study area: {dpa['dpa_area_name'].nunique()}")
    print(f"Suburb geometry: ABS SAL 2021, {len(suburbs)} of {len(frame)} suburbs matched")
    print()
    print(f"DPA=Y suburbs: {_pct(int(is_y.sum()), len(merged))}")
    print(f"DPA=Y population: {_pct(pop[is_y].sum(), pop.sum())}")
    print(f"DPA=Y suburbs over 2,000 residents: {int((is_y & (pop > 2000)).sum())}")
    print(f"Ambiguous (split) suburbs: {int(merged['ambiguous'].sum())}")
    print()
    verify_campbelltown(result)
    print()
    split = by_catchment.groupby("dpa_area_name")["dpa_status"].nunique()
    split = sorted(split[split > 1].index)
    print(f"Internally split catchments touching the study area: {len(split)}")
    for name in split:
        touched = by_catchment.loc[by_catchment["dpa_area_name"] == name, "suburb"].nunique()
        print(f"  {name} ({touched} project suburbs touch it)")
    print()
    print("DPA=Y suburbs by catchment:")
    for name, group in merged[is_y].groupby("dpa_area_name"):
        print(f"  {name}: {len(group)} suburbs, {group['population'].sum():,.0f} people")

    OUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUT_CSV, index=False)
    print(f"\nWrote {OUT_CSV} ({len(result)} rows)")


if __name__ == "__main__":
    main()
