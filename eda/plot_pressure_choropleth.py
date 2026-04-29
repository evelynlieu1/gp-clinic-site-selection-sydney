"""
Greater Sydney choropleth: population pressure per GP clinic.

Reads scraped_data/eda_data.csv, joins OpenStreetMap suburb polygons (cached),
and saves:
  - eda/plots/pressure_people_per_gp_choropleth.png (static)
  - eda/plots/pressure_people_per_gp_choropleth.html (interactive: hover tooltip,
    click for full suburb data popup)

First run downloads OSM data (~1–3 min). Later runs use eda/geo_cache/.

Run from project root:
  python eda/plot_pressure_choropleth.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import folium
import geopandas as gpd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
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
OUT_PNG = PLOTS_DIR / "pressure_people_per_gp_choropleth.png"
OUT_HTML = PLOTS_DIR / "pressure_people_per_gp_choropleth.html"

# OSM download bbox (left, bottom, right, top) WGS84 — wide enough for outer metro
OSM_BBOX = (150.5, -34.35, 151.65, -33.45)

# Map view (metro focus; trims far western / northern fringe in the figure only)
VIEW_BBOX = (150.74, -34.08, 151.52, -33.58)

# Plot in metric CRS (Greater Sydney)
PLOT_CRS = "EPSG:7856"  # GDA2020 / MGA zone 56


def load_or_fetch_suburb_polygons() -> gpd.GeoDataFrame:
    """Dissolved suburb polygons keyed by lowercased `suburb_key`."""
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


def _style_for_feature(feature: dict, norm: Normalize, cmap) -> dict:
    """Color by People_Per_GP_Ratio for every suburb (0 clinics → population ÷ 1, same OrRd scale)."""
    props = feature["properties"]
    r = props.get("People_Per_GP_Ratio")
    try:
        rv = float(r)
    except (TypeError, ValueError):
        rv = float("nan")
    if not np.isfinite(rv):
        return {
            "fillColor": "#cccccc",
            "color": "#999999",
            "weight": 0.35,
            "fillOpacity": 0.85,
        }
    t = norm(rv)
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
    """Leaflet map: hover shows fields; click opens popup with all attributes."""
    geojson = json.loads(gdf_4326.to_json())
    fields = [c for c in gdf_4326.columns if c != "geometry"]
    aliases = [c.replace("_", " ").title() for c in fields]

    def style_fn(feature: dict) -> dict:
        return _style_for_feature(feature, norm, cmap)

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
        caption="People ÷ (GP clinics + 1) — includes zero-clinic suburbs",
    ).add_to(m)

    title_html = """
    <div style="position:fixed;top:12px;left:56px;z-index:9999;font-size:15px;font-weight:600;
    background:rgba(255,255,255,0.92);padding:8px 12px;border-radius:4px;
    box-shadow:0 1px 4px rgba(0,0,0,0.25);max-width:90vw;">
    Greater Sydney: Population Pressure per GP Clinic
    </div>
    """
    m.get_root().html.add_child(Element(title_html))

    note_html = """
    <div style="position:fixed;bottom:36px;right:12px;z-index:9999;font-size:12px;
    background:rgba(255,255,255,0.92);padding:6px 10px;border-radius:4px;
    box-shadow:0 1px 4px rgba(0,0,0,0.2);max-width:min(320px,90vw);">
    Zero-clinic suburbs use the same colour scale (ratio = population ÷ 1).
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
    df["total_gp"] = pd.to_numeric(df["total_gp"], errors="coerce").fillna(0).clip(lower=0)
    df["population"] = pd.to_numeric(df["population"], errors="coerce")

    # Same definition as gp_density in pre-processing; explicit name for the map
    df["People_Per_GP_Ratio"] = df["population"] / (df["total_gp"] + 1)

    polys = load_or_fetch_suburb_polygons()
    df["suburb_key"] = df["suburb"].astype(str).str.strip().str.lower()
    merged = df.merge(polys, on="suburb_key", how="left")
    missing = merged["geometry"].isna().sum()
    if missing:
        print(
            f"Note: {missing} suburbs have no OSM polygon match (excluded from map).",
            flush=True,
        )

    gdf_4326 = gpd.GeoDataFrame(merged.dropna(subset=["geometry"]), geometry="geometry", crs="EPSG:4326")
    gdf = gdf_4326.to_crs(PLOT_CRS)

    zero_gp = gdf["total_gp"] == 0
    has_clinic = ~zero_gp

    ratios = gdf.loc[has_clinic, "People_Per_GP_Ratio"].astype(float)
    p_lo, p_hi = np.nanpercentile(ratios, [2, 98])
    if not np.isfinite(p_lo) or not np.isfinite(p_hi) or p_lo >= p_hi:
        p_lo, p_hi = float(np.nanmin(ratios)), float(np.nanmax(ratios))
        if p_lo >= p_hi:
            p_hi = p_lo + 1.0
    norm = Normalize(vmin=p_lo, vmax=p_hi, clip=True)
    cmap = plt.cm.OrRd

    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(12, 12))
    ax.set_facecolor("#7eb8d9")  # sea / water behind suburb polygons

    # Clinics > 0: sequential pressure
    gdf.loc[has_clinic].plot(
        column="People_Per_GP_Ratio",
        cmap=cmap,
        norm=norm,
        ax=ax,
        edgecolor="white",
        linewidth=0.15,
        legend=False,
    )

    # Zero clinics: gray overlay
    if zero_gp.any():
        gdf.loc[zero_gp].plot(
            ax=ax,
            color="#b3b3b3",
            edgecolor="#8c8c8c",
            linewidth=0.15,
            legend=False,
        )

    # Metro view
    view_poly = gpd.GeoDataFrame(geometry=[box(*VIEW_BBOX)], crs="EPSG:4326").to_crs(PLOT_CRS)
    xmin, ymin, xmax, ymax = view_poly.total_bounds
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)

    ax.set_title("Greater Sydney: Population Pressure per GP Clinic", fontsize=14, pad=12)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    for spine in ax.spines.values():
        spine.set_visible(False)

    sm = ScalarMappable(norm=norm, cmap=cmap)
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, shrink=0.65, pad=0.02)
    cbar.set_label("People per GP clinic (areas with ≥1 clinic)", fontsize=10)

    zero_patch = mpatches.Patch(
        facecolor="#b3b3b3",
        edgecolor="#8c8c8c",
        label="Zero Clinic Suburbs",
    )
    ax.legend(handles=[zero_patch], loc="lower left", frameon=True, fontsize=10)

    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=160, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {OUT_PNG.resolve()}", flush=True)

    # Interactive map: colour scale uses all suburbs (0-GP areas use population/1 → high pressure = dark red)
    ratios_all = gdf_4326["People_Per_GP_Ratio"].astype(float)
    p_lo_h, p_hi_h = np.nanpercentile(ratios_all, [2, 98])
    if not np.isfinite(p_lo_h) or not np.isfinite(p_hi_h) or p_lo_h >= p_hi_h:
        p_lo_h, p_hi_h = float(np.nanmin(ratios_all)), float(np.nanmax(ratios_all))
        if p_lo_h >= p_hi_h:
            p_hi_h = p_lo_h + 1.0
    norm_html = Normalize(vmin=p_lo_h, vmax=p_hi_h, clip=True)
    save_interactive_html(gdf_4326, norm_html, cmap, p_lo_h, p_hi_h, OUT_HTML)
    print(f"Saved {OUT_HTML.resolve()} — open in a browser (hover + click suburbs)", flush=True)


if __name__ == "__main__":
    main()
