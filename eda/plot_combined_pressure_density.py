"""
Combined choropleth map: Population pressure per GP clinic & Senior density per km².

Outputs:
  - eda/plots/combined_pressure_density.png (Single map with combined score)
  - eda/plots/combined_pressure_density.html (Interactive map with combined score)

Run from project root:
  python eda/plot_combined_pressure_density.py
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
OUT_PNG = PLOTS_DIR / "combined_pressure_density.png"
OUT_HTML = PLOTS_DIR / "combined_pressure_density.html"

OSM_BBOX = (150.5, -34.35, 151.65, -33.45)
VIEW_BBOX = (150.74, -34.08, 151.52, -33.58)
PLOT_CRS = "EPSG:7856"

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


def _style_function(feature: dict, norm: Normalize, cmap) -> dict:
    props = feature["properties"]
    raw = props.get("Combined_Demand_Score")
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
    fields = [c for c in gdf_4326.columns if c != "geometry" and not c.startswith("_")]
    aliases = [c.replace("_", " ").title() for c in fields]

    m = folium.Map(
        tiles="OpenStreetMap",
        location=[(VIEW_BBOX[1] + VIEW_BBOX[3]) / 2, (VIEW_BBOX[0] + VIEW_BBOX[2]) / 2],
        zoom_start=10,
    )

    folium.GeoJson(
        geojson,
        style_function=lambda f: _style_function(f, norm, cmap),
        highlight_function=lambda _f: {"weight": 2.5, "color": "#222222"},
        tooltip=folium.GeoJsonTooltip(fields=fields, aliases=aliases, sticky=True, labels=True),
        popup=folium.GeoJsonPopup(fields=fields, aliases=aliases, max_width=480, localize=True),
    ).add_to(m)

    m.fit_bounds([[VIEW_BBOX[1], VIEW_BBOX[0]], [VIEW_BBOX[3], VIEW_BBOX[2]]])

    # Legend
    gradient = [to_hex(cmap(float(x))) for x in np.linspace(0, 1, 11)]
    colormap = LinearColormap(
        colors=gradient,
        vmin=p_lo,
        vmax=p_hi,
        caption="Combined High-Frequency Demand Score (0 to 1)",
    )
    m.add_child(colormap)

    title_html = """
    <div style="position:fixed;top:12px;left:56px;z-index:9999;font-size:15px;font-weight:600;
    background:rgba(255,255,255,0.92);padding:8px 12px;border-radius:4px;
    box-shadow:0 1px 4px rgba(0,0,0,0.25);max-width:90vw;">
    High-Frequency Priority: Combined Pressure & Senior Density
    </div>
    """
    m.get_root().html.add_child(Element(title_html))

    note = (
        f"Sparse areas (&lt; {MIN_OVERALL_PEOPLE_PER_KM2:.0f} people/km²) omitted."
        if FILTER_LOW_OVERALL_DENSITY
        else ""
    )
    note_html = f"""
    <div style="position:fixed;bottom:36px;left:12px;z-index:9999;font-size:12px;
    background:rgba(255,255,255,0.92);padding:6px 10px;border-radius:4px;
    box-shadow:0 1px 4px rgba(0,0,0,0.2);max-width:min(360px,90vw);">
    Score = Average of Normalized Population Pressure and Normalized Senior Density.<br>{note}
    </div>
    """
    m.get_root().html.add_child(Element(note_html))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out_path))


def normalize_min_max(series: pd.Series) -> pd.Series:
    """Normalize to [0, 1] using 2nd and 98th percentiles for robustness."""
    vals = series.astype(float)
    p_lo, p_hi = np.nanpercentile(vals, [2, 98])
    if not np.isfinite(p_lo) or not np.isfinite(p_hi) or p_lo >= p_hi:
        p_lo, p_hi = float(np.nanmin(vals)), float(np.nanmax(vals))
        if p_lo >= p_hi:
            return np.zeros_like(vals)
    norm = (vals - p_lo) / (p_hi - p_lo)
    return norm.clip(lower=0.0, upper=1.0)


def main() -> None:
    if not DATA_PATH.exists():
        print(f"Missing {DATA_PATH}", file=sys.stderr)
        sys.exit(1)

    df = pd.read_csv(DATA_PATH)
    pop = pd.to_numeric(df["population"], errors="coerce")
    pct = pd.to_numeric(df["over_65_pct"], errors="coerce")
    area = pd.to_numeric(df["area"], errors="coerce")
    total_gp = pd.to_numeric(df["total_gp"], errors="coerce").fillna(0).clip(lower=0)

    # 1. Compute Base Metrics
    df["People_Per_GP_Ratio"] = pop / (total_gp + 1)
    df["total_gp"] = total_gp

    seniors = pop * (pct / 100.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        df["Senior_Density_per_km2"] = np.where(
            (area.notna()) & (area > 0), seniors / area, np.nan
        )
        overall_d = np.where((area.notna()) & (area > 0), pop / area, np.nan)
    df["_overall_people_per_km2"] = overall_d

    # 2. Combine Metrics into a single score
    norm_pressure = normalize_min_max(df["People_Per_GP_Ratio"])
    norm_density = normalize_min_max(df["Senior_Density_per_km2"])
    
    # Simple 50/50 Average
    df["Combined_Demand_Score"] = ((norm_pressure + norm_density) / 2).round(3)

    # Fetch geometry
    polys = load_or_fetch_suburb_polygons()
    df["suburb_key"] = df["suburb"].astype(str).str.strip().str.lower()
    merged = df.merge(polys, on="suburb_key", how="left")
    missing = merged["geometry"].isna().sum()
    if missing:
        print(f"Note: {missing} suburbs have no OSM polygon match (excluded from map).", flush=True)

    plot_df = merged.dropna(subset=["geometry"]).copy()

    if FILTER_LOW_OVERALL_DENSITY:
        od = plot_df["_overall_people_per_km2"].astype(float)
        keep = od >= MIN_OVERALL_PEOPLE_PER_KM2
        dropped = (~keep).sum()
        if dropped:
            print(f"Filtered out {dropped} suburbs with overall density < {MIN_OVERALL_PEOPLE_PER_KM2:.0f}.", flush=True)
        plot_df = plot_df.loc[keep].copy()

    gdf_4326 = gpd.GeoDataFrame(plot_df, geometry="geometry", crs="EPSG:4326")
    gdf = gdf_4326.to_crs(PLOT_CRS)

    # Normalization for Plotting the Combined Score
    vals = gdf["Combined_Demand_Score"].astype(float)
    p_lo, p_hi = float(np.nanmin(vals)), float(np.nanmax(vals))
    norm = Normalize(vmin=p_lo, vmax=p_hi, clip=True)
    cmap = plt.cm.magma_r  # Using magma_r for a vibrant look

    # Plot Single Static Map
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(12, 12))
    ax.set_facecolor("#7eb8d9")

    gdf.plot(
        column="Combined_Demand_Score", cmap=cmap, norm=norm,
        ax=ax, edgecolor="white", linewidth=0.15, legend=False,
        missing_kwds={"color": "#d9d9d9", "edgecolor": "white", "linewidth": 0.15}
    )

    # Metro view
    view_poly = gpd.GeoDataFrame(geometry=[box(*VIEW_BBOX)], crs="EPSG:4326").to_crs(PLOT_CRS)
    xmin, ymin, xmax, ymax = view_poly.total_bounds
    
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_title("High-Frequency Priority: Combined Pressure & Senior Density", fontsize=16, pad=12)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    for spine in ax.spines.values():
        spine.set_visible(False)

    # Colorbar
    sm = ScalarMappable(norm=norm, cmap=cmap)
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, shrink=0.6, pad=0.02, location="bottom")
    cbar.set_label("Combined High-Frequency Demand Score (0 to 1)", fontsize=12)

    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=160, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {OUT_PNG.resolve()}", flush=True)

    # HTML
    html_df = gdf_4326.drop(columns=["_overall_people_per_km2"], errors="ignore")
    save_interactive_html(
        html_df, 
        norm, cmap, p_lo, p_hi,
        OUT_HTML
    )
    print(f"Saved {OUT_HTML.resolve()}", flush=True)


if __name__ == "__main__":
    main()
