"""
Join the GA HealthDirect facility points to the project's 680 suburbs, two ways.

Outputs ``scraped_data/suburb_practice_counts.csv``: one row per project suburb with
``practices_inside``, ``pharmacies_inside``, the ``join_method`` used, and a flag where
the two independent joins disagree.

Two joins, deliberately kept separate
-------------------------------------
1. **Name join.** The GA ``suburb`` attribute normalised (case, whitespace, punctuation,
   ``St`` -> ``Saint``, ``Mt`` -> ``Mount``) and matched to the normalised project suburb
   name. Cheap, covers every project suburb, but inherits whatever locality the NHSD
   record was filed under -- which is sometimes the postal suburb rather than the
   gazetted one.

2. **Point-in-polygon join.** The facility's ``longitude`` / ``latitude`` tested against
   the dissolved OSM suburb polygons in
   ``eda/geo_cache/osm_suburb_polygons_greater_sydney.gpkg``. Geometrically honest, but
   only 650 of the 680 project suburbs have a polygon.

Where they disagree, the disagreement is reported, not resolved. ``practices_inside``
prefers the polygon count when a polygon exists (``join_method == 'polygon'``) and falls
back to the name count for the 30 suburbs with no polygon (``join_method == 'name'``).

**These are still inside-the-polygon counts.** That is the metric the rebuild exists to
stop ranking on -- Croydon has 1 practice inside it and 21 within 1.5 km. This file is
an input to the accessibility work and a like-for-like comparator against the project's
own ``clinics_listed``, not a supply measure in its own right.

Run from project root:
  python build_suburb_facility_counts.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import health_facilities as hf  # noqa: E402  (project root added to sys.path above)

EDA_DATA = ROOT / "scraped_data" / "eda_data.csv"
POLYGON_GPKG = ROOT / "eda" / "geo_cache" / "osm_suburb_polygons_greater_sydney.gpkg"
OUT_CSV = ROOT / "scraped_data" / "suburb_practice_counts.csv"

#: Bounding box the OSM polygon cache was built for (lon_min, lat_min, lon_max, lat_max).
#: Used only to separate "regional NSW, outside the study area" from "inside Sydney but
#: filed under a suburb name that is not one of the 680".
SYDNEY_BBOX = (150.5, -34.35, 151.65, -33.45)

_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")
_WS_RE = re.compile(r"\s+")
_ABBREV = {"st": "saint", "mt": "mount", "pt": "point", "nth": "north", "sth": "south"}


def normalise_suburb(name: object) -> str:
    """
    Canonical suburb key for name matching.

    Lowercases, turns punctuation into spaces (so ``Brighton-Le-Sands`` and
    ``Brighton Le Sands`` agree), collapses whitespace, and expands the abbreviations
    that actually differ between the two sources -- ``St Ives`` / ``Saint Ives``,
    ``Mt Druitt`` / ``Mount Druitt``.

    Expansion is token-wise, so ``Mount`` inside ``Mount Kuring-Gai`` is untouched and a
    suburb legitimately containing the letters ``st`` (``Castle Hill``) is not mangled.

    >>> normalise_suburb("  ST  IVES CHASE ")
    'saint ives chase'
    >>> normalise_suburb("Brighton-Le-Sands")
    'brighton le sands'
    >>> normalise_suburb("Castle Hill")
    'castle hill'
    """
    text = str(name or "").strip().lower()
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    if not text:
        return ""
    return " ".join(_ABBREV.get(tok, tok) for tok in text.split(" "))


def load_polygons() -> gpd.GeoDataFrame:
    """Dissolved OSM suburb polygons, with a normalised match key added."""
    if not POLYGON_GPKG.exists():
        raise SystemExit(
            f"{POLYGON_GPKG} not found. Rebuild it with:\n"
            "  python eda/plot_combined_pressure_density.py"
        )
    poly = gpd.read_file(POLYGON_GPKG)
    poly["match_key"] = poly["suburb_key"].map(normalise_suburb)
    # Two raw OSM names can normalise to the same key; dissolve so the join stays 1:1.
    poly = poly.dissolve(by="match_key", as_index=False)[["match_key", "geometry"]]
    return poly.set_crs("EPSG:4326", allow_override=True)


def to_points(df: pd.DataFrame) -> gpd.GeoDataFrame:
    """Facility rows as EPSG:4326 points, dropping any row without coordinates."""
    have = df["longitude"].notna() & df["latitude"].notna()
    pts = gpd.GeoDataFrame(
        df.loc[have].copy(),
        geometry=gpd.points_from_xy(df.loc[have, "longitude"], df.loc[have, "latitude"]),
        crs="EPSG:4326",
    )
    return pts


def quality_report(gps: pd.DataFrame, pharms: pd.DataFrame, project_keys: set[str]) -> None:
    """Print the data-quality findings. Nothing is deduped or dropped here."""
    print("=" * 78)
    print("DATA QUALITY -- reported, not corrected")
    print("=" * 78)

    for label, df in (("general practices", gps), ("pharmacies", pharms)):
        print(f"\n-- {label}: {len(df)} rows --")

        missing_xy = int((df["longitude"].isna() | df["latitude"].isna()).sum())
        print(f"  rows with no coordinates              : {missing_xy}")

        coord = df.groupby(["longitude", "latitude"]).size()
        dup_coord_groups = coord[coord > 1]
        print(
            f"  duplicate coordinate pairs            : "
            f"{len(dup_coord_groups)} locations covering "
            f"{int(dup_coord_groups.sum())} rows"
        )

        pid = df["gnaf_address_detail_pid"]
        blank_pid = int(pid.isna().sum() + (pid.fillna("").str.strip() == "").sum())
        dup_pid = pid.dropna()
        dup_pid = dup_pid[dup_pid.str.strip() != ""]
        pid_groups = dup_pid.value_counts()
        pid_groups = pid_groups[pid_groups > 1]
        print(f"  blank G-NAF address pid               : {blank_pid} "
              f"({100 * blank_pid / len(df):.1f}%)")
        print(
            f"  duplicate G-NAF address pid           : "
            f"{len(pid_groups)} pids covering {int(pid_groups.sum())} rows"
        )

        svc = df["nhsd_service_id"]
        svc_dup = svc.value_counts()
        svc_dup = svc_dup[svc_dup > 1]
        print(f"  duplicate nhsd_service_id             : {len(svc_dup)}")

        name_key = df["organisation_name"].astype(str).str.strip().str.casefold()
        exact_dup = name_key.value_counts()
        exact_dup = exact_dup[exact_dup > 1]
        print(f"  repeated organisation_name            : "
              f"{len(exact_dup)} names covering {int(exact_dup.sum())} rows")

        same_name_same_spot = df.groupby([name_key, "longitude", "latitude"]).size()
        same_name_same_spot = same_name_same_spot[same_name_same_spot > 1]
        print(f"  same name AND same coordinates        : "
              f"{len(same_name_same_spot)} groups covering "
              f"{int(same_name_same_spot.sum())} rows  <-- likeliest true duplicates")

        ah = name_key.str.contains(
            r"after\s*hours|afterhours|out\s*of\s*hours|home\s*doctor|"
            r"locum|national\s*home\s*doctor|13\s*sick|hotdoc",
            regex=True,
            na=False,
        )
        print(f"  after-hours / home-visit style names  : {int(ah.sum())}")

        key = df["suburb"].map(normalise_suburb)
        outside = ~key.isin(project_keys)
        in_bbox = (
            df["longitude"].between(SYDNEY_BBOX[0], SYDNEY_BBOX[2])
            & df["latitude"].between(SYDNEY_BBOX[1], SYDNEY_BBOX[3])
        )
        print(f"  suburb not one of the project 680     : {int(outside.sum())}")
        print(f"    ... of which inside the Sydney bbox : "
              f"{int((outside & in_bbox).sum())}  <-- real gaps in the 680, or naming")
        print(f"    ... regional NSW, outside the bbox  : {int((outside & ~in_bbox).sum())}")

        if int((outside & in_bbox).sum()):
            top = (
                df.loc[outside & in_bbox, "suburb"]
                .value_counts()
                .head(15)
            )
            print("    top in-Sydney suburbs not in the 680:")
            for name, n in top.items():
                print(f"      {n:3d}  {name}")

    print("\n-- pharmacy service types --")
    for name, n in pharms["nhsd_service_type"].value_counts().items():
        print(f"  {n:5d}  {name}")

    # The nhsd_service_type filter removes 393 obvious non-GPs from NSW layer 0, but a
    # residue survives it: youth mental health, refugee health, travel clinics, campus
    # health services. Flagged, never dropped -- some genuinely bulk-bill general
    # consultations, so the call belongs to a human.
    gp_key = gps["organisation_name"].astype(str).str.casefold()
    residual = gp_key.str.contains(
        r"headspace|refugee\s*health|travel\s*doctor|travel\s*clinic|"
        r"sexual\s*health|university\s*health\s*service|student\s*health|"
        r"aboriginal\s*medical|drug\s*(?:and|&)\s*alcohol|methadone",
        regex=True,
        na=False,
    )
    print(f"\n-- residual non-GP services surviving the service-type filter --")
    print(f"  flagged for review (NOT removed)      : {int(residual.sum())}")
    for _, r in gps.loc[residual, ["organisation_name", "suburb"]].iterrows():
        print(f"      {str(r['organisation_name'])[:50]:52s} {r['suburb']}")


def show_examples(gps: pd.DataFrame) -> None:
    """Print concrete duplicate examples so the reader can judge them."""
    name_key = gps["organisation_name"].astype(str).str.strip().str.casefold()
    grp = gps.assign(_k=name_key).groupby(["_k", "longitude", "latitude"])
    sizes = grp.size()
    worst = sizes[sizes > 1].sort_values(ascending=False).head(5)
    if worst.empty:
        print("\nno same-name-same-coordinate groups")
        return
    print("\n-- example same-name, same-coordinate groups --")
    for (k, lon, lat), n in worst.items():
        rows = gps[(name_key == k) & (gps["longitude"] == lon) & (gps["latitude"] == lat)]
        print(f"  {n} rows  {rows['organisation_name'].iloc[0]}  ({lat:.5f}, {lon:.5f})")
        for _, r in rows.iterrows():
            print(f"      objectid {r['objectid']:6d}  pid {str(r['gnaf_address_detail_pid']):>16s}"
                  f"  {r['suburb']}")


#: Projected CRS for metric distances (GDA2020 / MGA zone 56), matching the plot scripts.
DISTANCE_CRS = "EPSG:7856"

#: A point this far outside the polygon of the suburb it is filed under is very likely a
#: source geocoding error rather than a boundary-edge disagreement.
GEOCODE_SUSPECT_KM = 2.0


def geocode_suspects(pts: gpd.GeoDataFrame, poly: gpd.GeoDataFrame, label: str) -> None:
    """
    Report facilities whose coordinates sit far outside their own filed suburb.

    A boundary-edge mismatch puts a point tens of metres into the neighbouring suburb.
    A point kilometres away means the source geocoded the wrong address, and it will
    contaminate any accessibility measure built on these coordinates.
    """
    pts = pts.copy()
    pts["name_key"] = pts["suburb"].map(normalise_suburb)
    joined = pts.merge(
        poly.rename(columns={"match_key": "name_key", "geometry": "suburb_geom"}),
        on="name_key",
        how="inner",
    )
    if joined.empty:
        print(f"\n-- {label}: no filed suburb matched a polygon, distance check skipped")
        return

    a = gpd.GeoSeries(joined["geometry"], crs="EPSG:4326").to_crs(DISTANCE_CRS)
    b = gpd.GeoSeries(joined["suburb_geom"], crs="EPSG:4326").to_crs(DISTANCE_CRS)
    joined["km_outside"] = a.distance(b, align=False).to_numpy() / 1000.0

    bad = joined[joined["km_outside"] > GEOCODE_SUSPECT_KM].sort_values(
        "km_outside", ascending=False
    )
    print(f"\n-- {label}: suspected source geocoding errors --")
    print(f"  points checked against their filed suburb polygon : {len(joined)}")
    print(f"  more than {GEOCODE_SUSPECT_KM:.0f} km outside it                       : {len(bad)}")
    for _, r in bad.head(15).iterrows():
        print(f"      {r['km_outside']:6.1f} km  {str(r['organisation_name'])[:40]:42s} "
              f"filed {r['suburb']}")


def counts_by_key(keys: pd.Series, index: pd.Index) -> pd.Series:
    """Count occurrences of each key, reindexed onto the project suburb keys."""
    return keys.value_counts().reindex(index, fill_value=0).astype(int)


def main() -> None:
    eda = pd.read_csv(EDA_DATA)
    eda["match_key"] = eda["suburb"].map(normalise_suburb)
    if eda["match_key"].duplicated().any():
        dupes = eda.loc[eda["match_key"].duplicated(keep=False), "suburb"].tolist()
        raise SystemExit(f"normalisation collapsed distinct project suburbs: {dupes}")
    keys = pd.Index(eda["match_key"])

    gps = hf.general_practices_nsw(verbose=False)
    pharms = hf.pharmacies_nsw(verbose=False)
    print(f"loaded {len(gps)} general practices, {len(pharms)} pharmacies")
    print(f"source: {hf.SOURCE_ATTRIBUTION}\n")

    quality_report(gps, pharms, set(keys))
    show_examples(gps)

    # ---- name join ----------------------------------------------------------
    gp_name_key = gps["suburb"].map(normalise_suburb)
    ph_name_key = pharms["suburb"].map(normalise_suburb)
    gp_name = counts_by_key(gp_name_key, keys)
    ph_name = counts_by_key(ph_name_key, keys)

    # ---- point in polygon join ----------------------------------------------
    poly = load_polygons()
    poly_keys = set(poly["match_key"])
    # pd.Index.isin returns a plain ndarray, so keep the boolean mask as one throughout.
    has_poly = keys.isin(poly_keys)

    gp_pts = to_points(gps)
    ph_pts = to_points(pharms)
    gp_pip = gpd.sjoin(gp_pts, poly, how="left", predicate="within")
    ph_pip = gpd.sjoin(ph_pts, poly, how="left", predicate="within")
    geocode_suspects(gp_pts, poly, "general practices")
    geocode_suspects(ph_pts, poly, "pharmacies")
    gp_poly = counts_by_key(gp_pip["match_key"].dropna(), keys)
    ph_poly = counts_by_key(ph_pip["match_key"].dropna(), keys)

    print("\n" + "=" * 78)
    print("JOIN AGREEMENT")
    print("=" * 78)
    n_poly = int(has_poly.sum())
    print(f"project suburbs                          : {len(keys)}")
    print(f"  with an OSM polygon (polygon join)     : {n_poly}")
    print(f"  no polygon, name join only             : {len(keys) - n_poly}")
    no_poly_names = sorted(eda.loc[~has_poly, "suburb"].tolist())
    print(f"  the {len(no_poly_names)} name-only suburbs:")
    for i in range(0, len(no_poly_names), 4):
        print("    " + ", ".join(no_poly_names[i:i + 4]))

    # Facility-level agreement: for each GA practice, does the suburb it is filed
    # under match the polygon it actually falls inside?
    project_poly_keys = set(keys[has_poly])
    for label, pip in (("practices", gp_pip), ("pharmacies", ph_pip)):
        fac = pip[["objectid", "organisation_name", "suburb", "match_key"]].copy()
        fac["name_key"] = fac["suburb"].map(normalise_suburb)
        # Comparable = the point landed in a project suburb polygon AND the suburb it is
        # filed under is itself a project suburb with a polygon. Anything else cannot
        # disagree, it is simply unresolvable by one of the two methods.
        both = fac["match_key"].isin(project_poly_keys) & fac["name_key"].isin(
            project_poly_keys
        )
        sub = fac.loc[both]
        agree = sub["match_key"] == sub["name_key"]
        print(f"\nfacility-level agreement, {label} resolvable by BOTH methods:")
        print(f"  comparable records                     : {len(sub)}")
        print(f"  same suburb from both methods          : {int(agree.sum())} "
              f"({100 * agree.mean():.1f}%)")
        print(f"  filed under a different suburb         : {int((~agree).sum())}")
        for _, r in sub.loc[~agree].iterrows():
            print(f"      {str(r['organisation_name'])[:44]:46s} "
                  f"filed {r['suburb']:20s} -> polygon {r['match_key']}")

    # Suburb-level agreement on the counts themselves.
    cmp_gp = gp_name[has_poly] == gp_poly[has_poly]
    cmp_ph = ph_name[has_poly] == ph_poly[has_poly]
    print(f"\nsuburb-level count agreement (the {n_poly} suburbs with a polygon):")
    print(f"  practices count identical              : {int(cmp_gp.sum())} "
          f"({100 * cmp_gp.mean():.1f}%)")
    print(f"  pharmacies count identical             : {int(cmp_ph.sum())} "
          f"({100 * cmp_ph.mean():.1f}%)")

    out = pd.DataFrame(
        {
            "suburb": eda["suburb"].to_numpy(),
            "practices_inside": 0,
            "pharmacies_inside": 0,
            "join_method": ["polygon" if p else "name" for p in has_poly],
            "practices_name_join": gp_name.to_numpy(),
            "practices_polygon_join": gp_poly.to_numpy(),
            "pharmacies_name_join": ph_name.to_numpy(),
            "pharmacies_polygon_join": ph_poly.to_numpy(),
        }
    )
    use_poly = has_poly
    out["practices_inside"] = pd.Series(
        gp_poly.to_numpy()
    ).where(use_poly, pd.Series(gp_name.to_numpy())).astype(int)
    out["pharmacies_inside"] = pd.Series(
        ph_poly.to_numpy()
    ).where(use_poly, pd.Series(ph_name.to_numpy())).astype(int)
    # Only meaningful where both joins ran; a name-only suburb cannot disagree.
    out["join_disagree"] = use_poly & (
        (out["practices_name_join"] != out["practices_polygon_join"])
        | (out["pharmacies_name_join"] != out["pharmacies_polygon_join"])
    )
    out["practices_join_delta"] = out["practices_polygon_join"] - out["practices_name_join"]

    print(f"\nsuburbs flagged join_disagree             : {int(out['join_disagree'].sum())}")
    flagged = out[out["join_disagree"]].copy()
    worst = flagged.sort_values(
        "practices_join_delta", key=lambda s: s.abs(), ascending=False
    ).head(20)
    print("\nlargest practice-count disagreements (polygon minus name):")
    print("  suburb                       name  poly  delta")
    for _, r in worst.iterrows():
        print(f"  {r['suburb']:28s} {r['practices_name_join']:4d}  "
              f"{r['practices_polygon_join']:4d}  {r['practices_join_delta']:+5d}")

    out.to_csv(OUT_CSV, index=False, encoding="utf-8")
    print(f"\nwrote {len(out)} rows to {OUT_CSV}")
    print(f"total practices attributed to the 680     : {int(out['practices_inside'].sum())}"
          f" of {len(gps)} NSW")
    print(f"total pharmacies attributed to the 680    : {int(out['pharmacies_inside'].sum())}"
          f" of {len(pharms)} NSW")


if __name__ == "__main__":
    main()
