"""
Mixed billing feasibility: household income (choropleth) + GP clinic supply (bubbles).

Reads scraped_data/eda_data.csv and cached OSM suburb polygons (same cache as pressure map).
Writes:
  - eda/plots/mixed_billing_income_vs_supply.png
  - eda/plots/mixed_billing_income_vs_supply.html

Run from project root:
  python eda/plot_mixed_billing_choropleth.py
"""
from __future__ import annotations

import json
import math
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
from matplotlib.lines import Line2D
from shapely.geometry import box

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "scraped_data" / "eda_data.csv"
EDA_DIR = Path(__file__).resolve().parent
PLOTS_DIR = EDA_DIR / "plots"
GEO_CACHE_DIR = EDA_DIR / "geo_cache"
CACHE_GPKG = GEO_CACHE_DIR / "osm_suburb_polygons_greater_sydney.gpkg"
OUT_PNG = PLOTS_DIR / "mixed_billing_income_vs_supply.png"
OUT_HTML = PLOTS_DIR / "mixed_billing_income_vs_supply.html"

OSM_BBOX = (150.5, -34.35, 151.65, -33.45)
VIEW_BBOX = (150.74, -34.08, 151.52, -33.58)
PLOT_CRS = "EPSG:7856"

# Income scale ($/week): light green below this, dark green above this (matplotlib Greens + clip)
INCOME_VMIN = 1500
INCOME_VMAX = 3000


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


def _income_style(feature: dict, norm: Normalize, cmap) -> dict:
    props = feature["properties"]
    raw = props.get("median_household_income_weekly")
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
    out_path: Path,
) -> None:
    geojson = json.loads(gdf_4326.to_json())
    fields = [c for c in gdf_4326.columns if c != "geometry"]
    aliases = [c.replace("_", " ").title() for c in fields]

    def style_fn(feature: dict) -> dict:
        return _income_style(feature, norm, cmap)

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

    # Bubbles: red stroke, no fill; radius scales with total_gp (clinics)
    g_wgs = gdf_4326.copy()
    g_wgs["total_gp"] = pd.to_numeric(g_wgs["total_gp"], errors="coerce").fillna(0).clip(lower=0)
    cents = g_wgs.geometry.representative_point()
    for lat, lon, n_gp in zip(cents.y, cents.x, g_wgs["total_gp"].to_numpy()):
        n = int(n_gp)
        if n <= 0:
            continue
        # Pixel-ish radius on map: sqrt scaling so large counts stay readable
        r = max(3.0, 2.0 + 4.5 * math.sqrt(float(n)))
        folium.CircleMarker(
            location=[float(lat), float(lon)],
            radius=r,
            color="#d32f2f",
            weight=2,
            opacity=0.65,
            fill=False,
        ).add_to(m)

    m.fit_bounds([[VIEW_BBOX[1], VIEW_BBOX[0]], [VIEW_BBOX[3], VIEW_BBOX[2]]])

    gradient = [to_hex(cmap(float(x))) for x in np.linspace(0, 1, 11)]
    LinearColormap(
        colors=gradient,
        vmin=INCOME_VMIN,
        vmax=INCOME_VMAX,
        caption="Median household income ($/week) — light below $1,500, dark above $3,000 (scale clipped)",
    ).add_to(m)

    title_html = """
    <div style="position:fixed;top:12px;left:56px;z-index:9999;font-size:15px;font-weight:600;
    background:rgba(255,255,255,0.92);padding:8px 12px;border-radius:4px;
    box-shadow:0 1px 4px rgba(0,0,0,0.25);max-width:min(520px,92vw);">
    Mixed Billing Opportunity: Household Income vs. GP Supply
    </div>
    """
    m.get_root().html.add_child(Element(title_html))

    note_html = """
    <div style="position:fixed;bottom:36px;right:12px;z-index:9999;font-size:12px;
    background:rgba(255,255,255,0.92);padding:6px 10px;border-radius:4px;
    box-shadow:0 1px 4px rgba(0,0,0,0.2);max-width:min(340px,90vw);">
    Red ring outlines = listed GP clinics (size ∝ supply). No ring ≈ zero clinics.
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
    inc_col = "median_household_income_weekly"
    df[inc_col] = pd.to_numeric(df[inc_col], errors="coerce")

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

    norm = Normalize(vmin=INCOME_VMIN, vmax=INCOME_VMAX, clip=True)
    cmap = plt.cm.Greens

    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(12, 12))
    ax.set_facecolor("#7eb8d9")

    gdf.plot(
        column=inc_col,
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

    centroids = gdf.geometry.centroid
    gp = gdf["total_gp"].to_numpy(dtype=float)
    mask = gp > 0
    if mask.any():
        # Marker area scales ~ linearly with clinic count (larger = more supply)
        sizes = 25.0 + 95.0 * gp[mask]
        ax.scatter(
            centroids.x.values[mask],
            centroids.y.values[mask],
            s=sizes,
            facecolors="none",
            edgecolors=(0.82, 0.12, 0.12, 0.55),
            linewidths=np.clip(0.7 + 0.35 * gp[mask], 0.8, 4.0),
            zorder=6,
        )

    view_poly = gpd.GeoDataFrame(geometry=[box(*VIEW_BBOX)], crs="EPSG:4326").to_crs(PLOT_CRS)
    xmin, ymin, xmax, ymax = view_poly.total_bounds
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)

    ax.set_title(
        "Mixed Billing Opportunity: Household Income vs. GP Supply",
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
    cbar.set_label("Median household income ($/week)", fontsize=10)

    bubble_proxy = Line2D(
        [0],
        [0],
        linestyle="none",
        marker="o",
        markersize=11,
        markerfacecolor="none",
        markeredgecolor=(0.82, 0.12, 0.12, 0.85),
        markeredgewidth=1.8,
        label="GP clinics (ring size ∝ total listed)",
    )
    ax.legend(handles=[bubble_proxy], loc="lower left", frameon=True, fontsize=9)

    fig.tight_layout()
    fig.savefig(OUT_PNG, dpi=160, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved {OUT_PNG.resolve()}", flush=True)

    save_interactive_html(gdf_4326, norm, cmap, OUT_HTML)
    print(f"Saved {OUT_HTML.resolve()}", flush=True)


if __name__ == "__main__":
    main()
