"""
Senior population density: seniors per km² (high-frequency patient demand).

Metric: Senior_Density_per_km2 = (population × % over 65 / 100) / area_km²

Optionally excludes suburbs with very low overall population density (sparse /
industrial) so the colour scale is not skewed by odd pockets.

Outputs:
  - eda/plots/senior_density_per_km2.png
  - eda/plots/senior_density_per_km2.html

Run from project root:
  python eda/plot_senior_density_choropleth.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import folium
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import osmnx as ox
import pandas as pd
from branca.colormap import LinearColormap
from folium import Element
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize, to_hex
from shapely.geometry import box

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "scraped_data" / "eda_data.csv"
EDA_DIR = Path(__file__).resolve().parent
PLOTS_DIR = EDA_DIR / "plots"
GEO_CACHE_DIR = EDA_DIR / "geo_cache"
CACHE_GPKG = GEO_CACHE_DIR / "osm_suburb_polygons_greater_sydney.gpkg"
OUT_PNG = PLOTS_DIR / "senior_density_per_km2.png"
OUT_HTML = PLOTS_DIR / "senior_density_per_km2.html"

OSM_BBOX = (150.5, -34.35, 151.65, -33.45)
VIEW_BBOX = (150.74, -34.08, 151.52, -33.58)
PLOT_CRS = "EPSG:7856"

# Optional: drop suburbs with overall population density below this (people / km²)
# to reduce industrial / near-empty polygons skewing the choropleth.
FILTER_LOW_OVERALL_DENSITY = True
MIN_OVERALL_PEOPLE_PER_KM2 = 120.0


def load_or_fetch_suburb_polygons() -> gpd.GeoDataFrame:
    GEO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if CACHE_GPKG.exists():
        return gpd.read_file(CACHE_GPKG)

    ox.settings.use_cache = True
    print("Downloading OSM suburb polygons (first run only; may take a few minutes)...", flush=True)
    gdf = ox.features_from_bbox(OSM_BBOX, tags={"place": "suburb"})
    poly = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    if poly.empty:
        print("No polygon suburbs returned from OSM.", file=sys.stderr)
        sys.exit(1)
    poly["suburb_key"] = poly["name"].astype(str).str.strip().str.lower()
    poly = poly.dissolve(by="suburb_key", as_index=False)[["suburb_key", "geometry"]]
    poly = poly.set_crs("EPSG:4326")
    poly.to_file(CACHE_GPKG, driver="GPKG")
    print(f"Cached {len(poly)} suburb polygons to {CACHE_GPKG}", flush=True)
    return poly


def _senior_density_style(feature: dict, norm: Normalize, cmap) -> dict:
    props = feature["properties"]
    raw = props.get("Senior_Density_per_km2")
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = float("nan")
    if not np.isfinite(v):
        return {
            "fillColor": "#d9d9d9",
            "color": "white",
            "weight": 0.25,
            "fillOpacity": 0.88,
        }
    t = norm(v)
    fill = to_hex(cmap(t))
    return {
        "fillColor": fill,
        "color": "white",
        "weight": 0.25,
        "fillOpacity": 0.92,
    }


def save_interactive_html(
    gdf_4326: gpd.GeoDataFrame,
    norm: Normalize,
    cmap,
    p_lo: float,
    p_hi: float,
    out_path: Path,
) -> None:
    geojson = json.loads(gdf_4326.to_json())
    fields = [c for c in gdf_4326.columns if c != "geometry"]
    aliases = [c.replace("_", " ").title() for c in fields]

    def style_fn(feature: dict) -> dict:
        return _senior_density_style(feature, norm, cmap)

    m = folium.Map(
        tiles="OpenStreetMap",
        location=[(VIEW_BBOX[1] + VIEW_BBOX[3]) / 2, (VIEW_BBOX[0] + VIEW_BBOX[2]) / 2],
        zoom_start=10,
    )

    folium.GeoJson(
        geojson,
        style_function=style_fn,
        highlight_function=lambda _f: {"weight": 2.5, "color": "#222222"},
        tooltip=folium.GeoJsonTooltip(
            fields=fields,
            aliases=aliases,
            sticky=True,
            labels=True,
        ),
        popup=folium.GeoJsonPopup(
            fields=fields,
            aliases=aliases,
            max_width=480,
            localize=True,
        ),
    ).add_to(m)

    m.fit_bounds([[VIEW_BBOX[1], VIEW_BBOX[0]], [VIEW_BBOX[3], VIEW_BBOX[2]]])

    gradient = [to_hex(cmap(float(x))) for x in np.linspace(0, 1, 11)]
    LinearColormap(
        colors=gradient,
        vmin=p_lo,
        vmax=p_hi,
        caption="Seniors per km² (YlOrBr scale, 2–98% percentiles)",
    ).add_to(m)

    title_html = """
    <div style="position:fixed;top:12px;left:56px;z-index:9999;font-size:15px;font-weight:600;
    background:rgba(255,255,255,0.92);padding:8px 12px;border-radius:4px;
    box-shadow:0 1px 4px rgba(0,0,0,0.25);max-width:min(520px,92vw);">
    High-Frequency Patient Demand: Senior Density per km²
    </div>
    """
    m.get_root().html.add_child(Element(title_html))

    note = (
        f"Polygons with overall density &lt; {MIN_OVERALL_PEOPLE_PER_KM2:.0f} people/km² "
        "omitted when filtering is on."
        if FILTER_LOW_OVERALL_DENSITY
        else "All matched suburbs shown."
    )
    note_html = f"""
    <div style="position:fixed;bottom:36px;right:12px;z-index:9999;font-size:12px;
    background:rgba(255,255,255,0.92);padding:6px 10px;border-radius:4px;
    box-shadow:0 1px 4px rgba(0,0,0,0.2);max-width:min(360px,90vw);">
    {note}
    </div>
    """
    m.get_root().html.add_child(Element(note_html))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out_path))


def main() -> None:
    if not DATA_PATH.exists():
        print(f"Missing {DATA_PATH}", file=sys.stderr)
        sys.exit(1)

    df = pd.read_csv(DATA_PATH)
    pop = pd.to_numeric(df["population"], errors="coerce")
    pct = pd.to_numeric(df["over_65_pct"], errors="coerce")
    area = pd.to_numeric(df["area"], errors="coerce")

    seniors = pop * (pct / 100.0)
    # Senior_Density_per_km2 = (Population * (Percentage over 65 / 100)) / Area_sq_km
    with np.errstate(divide="ignore", invalid="ignore"):
        df["Senior_Density_per_km2"] = np.where(
            (area.notna()) & (area > 0),
            seniors / area,
            np.nan,
        )

    # Overall population density (people/km²) for optional filter
    with np.errstate(divide="ignore", invalid="ignore"):
        overall_d = np.where((area.notna()) & (area > 0), pop / area, np.nan)
    df["_overall_people_per_km2"] = overall_d

    polys = load_or_fetch_suburb_polygons()
    df["suburb_key"] = df["suburb"].astype(str).str.strip().str.lower()
    merged = df.merge(polys, on="suburb_key", how="left")
    missing_geom = merged["geometry"].isna().sum()
    if missing_geom:
        print(
            f"Note: {missing_geom} suburbs have no OSM polygon match (excluded from map).",
            flush=True,
        )

    plot_df = merged.dropna(subset=["geometry"]).copy()

    if FILTER_LOW_OVERALL_DENSITY:
        od = plot_df["_overall_people_per_km2"].astype(float)
        keep = od >= MIN_OVERALL_PEOPLE_PER_KM2
        dropped = (~keep).sum()
        if dropped:
            print(
                f"Filtered out {dropped} suburbs with overall density "
                f"< {MIN_OVERALL_PEOPLE_PER_KM2:.0f} people/km² (sparse/industrial guardrail).",
                flush=True,
            )
        plot_df = plot_df.loc[keep].copy()

    gdf_4326 = gpd.GeoDataFrame(plot_df, geometry="geometry", crs="EPSG:4326")
    gdf = gdf_4326.to_crs(PLOT_CRS)

    vals = gdf["Senior_Density_per_km2"].astype(float)
    p_lo, p_hi = np.nanpercentile(vals, [2, 98])
    if not np.isfinite(p_lo) or not np.isfinite(p_hi) or p_lo >= p_hi:
        p_lo, p_hi = float(np.nanmin(vals)), float(np.nanmax(vals))
        if p_lo >= p_hi:
            p_hi = p_lo + 1.0
    norm = Normalize(vmin=p_lo, vmax=p_hi, clip=True)
    cmap = plt.cm.YlOrBr

    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(12, 12))
    ax.set_facecolor("#7eb8d9")

    gdf.plot(
        column="Senior_Density_per_km2",
        cmap=cmap,
        norm=norm,
        ax=ax,
        edgecolor="white",
        linewidth=0.15,
        legend=False,
        missing_kwds={
            "color": "#d9d9d9",
            "edgecolor": "white",
            "linewidth": 0.15,
        },
    )

    view_poly = gpd.GeoDataFrame(geometry=[box(*VIEW_BBOX)], crs="EPSG:4326").to_crs(PLOT_CRS)
    xmin, ymin, xmax, ymax = view_poly.total_bounds
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)

    ax.set_title(
        "High-Frequency Patient Demand: Senior Density per km²",
        fontsize=14,
        pad=12,
    )
    ax.set_xticks([])
    ax.set_yticks([])
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    for spine in ax.spines.values():
        spine.set_visible(False)

    sm = ScalarMappable(norm=norm, cmap=cmap)
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, shrink=0.65, pad=0.02)
    cbar.set_label("Seniors per km²", fontsize=10)

    filter_note = (
        f" (excl. <{MIN_OVERALL_PEOPLE_PER_KM2:.0f} people/km² overall)"
        if FILTER_LOW_OVERALL_DENSITY
        else ""
    )
    fig.text(
        0.02,
        0.02,
        f"Colour scale: 2nd–98th percentile of senior density{filter_note}.",
        fontsize=8,
        color="#333333",
        transform=fig.transFigure,
    )

    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=160, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {OUT_PNG.resolve()}", flush=True)

    # HTML: drop internal column from export if present
    html_df = gdf_4326.drop(columns=["_overall_people_per_km2"], errors="ignore")
    save_interactive_html(html_df, norm, cmap, p_lo, p_hi, OUT_HTML)
    print(f"Saved {OUT_HTML.resolve()}", flush=True)


if __name__ == "__main__":
    main()
