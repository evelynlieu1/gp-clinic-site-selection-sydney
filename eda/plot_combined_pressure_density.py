"""
Canonical priority map for siting a mixed-billing, 7-day, multidisciplinary GP clinic.

This one map replaces the two standalone choropleths (population pressure per GP and
senior density per km2). It is a 3x3 **bivariate** choropleth: averaging the two
normalised metrics into a single score -- what this script used to do -- destroys the
distinction between "thin GP supply" and "lots of seniors", which is the only reason to
look at the two together in the first place.

  X axis -- GP accessibility = E2SFCA ``access_general`` from
            ``scraped_data/suburb_accessibility.csv``, in practices per 1,000 people
            within a 20 minute travel cutoff, competition adjusted. Plotted DESCENDING,
            so the right-hand column is the least accessible tercile.
  Y axis -- Senior demand    = population * over_65_pct / 100 / area  "seniors per km2"

Each axis is cut into terciles within the mapped cohort, giving 9 classes. The dark
corner is still highest priority: lowest accessibility x highest senior density.

WHY THE X AXIS CHANGED. It used to be ``population / (total_gp + 1)``, counting only the
doctors found INSIDE each suburb polygon. That is the metric that put Croydon first: it
has 1 practice inside it and 27 within 2 km, sitting between Burwood and Ashfield in one
of the most GP-saturated parts of Sydney. Forty five suburbs of 5,000-plus people read as
empty while being comfortably served (Chippendale: 0 inside, 43 within 2 km), and the
genuinely thin fringe never surfaced. Accessibility fixes the direction of the error:
supply is counted wherever it actually is, reachable practices are shared with everyone
else who can reach them, and the score is per capita rather than a head count.

CONSEQUENCE FOR THE HATCHED GREY CLASS. ``access_general`` is defined for all 680
suburbs, including the 239 the clinic scrape never returned, because it depends on the
location of practices in the Geoscience Australia HealthDirect register and on who else
can reach them -- not on whether that particular suburb was scraped. The 179 mapped
suburbs that this map previously had to hatch and leave unranked therefore all receive a
real class now, and nothing needs hatching. The hatch treatment is kept, with a live
count, because it is the guard that fires if an accessibility value ever goes missing;
it must never be repurposed to mean "score of zero".

The scraped GP columns survive only as tooltip context, never as an axis: the tooltip
prints a GP count only where the count was observed, "not known" everywhere else, and
labels a suburb the scrape searched and found no clinic in as the confirmed zero it is.
Those confirmed zeros are no longer pushed to the top of the X axis by construction --
they are ranked on how much GP capacity is actually within reach of them, which for
Beacon Hill and Bayview is not nothing.

Outputs (filenames unchanged):
  - eda/plots/combined_pressure_density.png
  - eda/plots/combined_pressure_density.html

Run from project root:
  python eda/plot_combined_pressure_density.py
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import folium
import geopandas as gpd
import matplotlib.pyplot as plt
import numpy as np
import osmnx as ox
import pandas as pd
from folium import Element
from matplotlib.patches import FancyBboxPatch, Rectangle
from shapely.geometry import box

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gp_supply import (  # noqa: E402  (ROOT must be on sys.path first)
    GP_STATUS_NO_DATA,
    GP_STATUS_OK,
    rankable_mask,
    require_status_column,
    status_summary,
)

DATA_PATH = ROOT / "scraped_data" / "eda_data.csv"
ACCESS_PATH = ROOT / "scraped_data" / "suburb_accessibility.csv"
EDA_DIR = Path(__file__).resolve().parent
PLOTS_DIR = EDA_DIR / "plots"
GEO_CACHE_DIR = EDA_DIR / "geo_cache"
CACHE_GPKG = GEO_CACHE_DIR / "osm_suburb_polygons_greater_sydney.gpkg"
OUT_PNG = PLOTS_DIR / "combined_pressure_density.png"
OUT_HTML = PLOTS_DIR / "combined_pressure_density.html"

OSM_BBOX = (150.5, -34.35, 151.65, -33.45)
VIEW_BBOX = (150.74, -34.08, 151.52, -33.58)
PLOT_CRS = "EPSG:7856"  # GDA2020 / MGA zone 56

FILTER_LOW_OVERALL_DENSITY = True
MIN_OVERALL_PEOPLE_PER_KM2 = 120.0

# The PNG footnote block is hard-wrapped at this many characters. matplotlib will not wrap
# it, and ``bbox_inches="tight"`` grows the canvas to fit the longest line, so an unwrapped
# footnote silently widens the figure and shrinks the map.
FOOTNOTE_WRAP_CHARS = 168

WATER_COLOR = "#7eb8d9"

# Bivariate palette (Stevens 3x3): X = accessibility shortfall on a teal ramp, Y = senior
# density on a magenta ramp, high-high = dark indigo, low-low = pale neutral. Teal against
# magenta stays separable under the common red-green colour deficiencies.
# Indexed [senior_class - 1][access_class - 1]; row 0 = fewest seniors, and column 2 is
# the LEAST accessible tercile, so the dark corner remains highest priority.
BIVARIATE_PALETTE: tuple[tuple[str, str, str], ...] = (
    ("#e8e8e8", "#ace4e4", "#5ac8c8"),
    ("#dfb0d6", "#a5add3", "#5698b9"),
    ("#be64ac", "#8c62aa", "#3b4994"),
)

# Suburbs whose accessibility or senior density could not be computed at all: set aside,
# never given an access class. This is now an empty class in practice (accessibility is
# defined for every suburb), and the legend prints the live count so a reader can see
# that. It exists as the guard that fires if a value ever goes missing, and it must never
# be read as "accessibility of zero" -- eight real suburbs score an exact 0.0 and those
# are ranked, in the least accessible tercile, where they belong.
NO_DATA_FILL = "#c2c2c2"
NO_DATA_LINE = "#5f5f5f"
NO_DATA_HATCH = "///"


def no_data_label(n: int) -> str:
    """Legend text for the hatched class, stating how many suburbs are actually in it."""
    return f"Accessibility not computable, not ranked ({n:,d} suburbs)"


# Reader-facing text for the supply columns in the HTML tooltip/popup. ``total_gp`` is
# stored as 0 for every suburb the scrape never returned, so it must never be printed
# as a bare number unless gp_data_status says it was observed.
GP_UNKNOWN_TEXT = "not known"
CONFIRMED_ZERO_TEXT = "0 - no clinic found in suburb"

X_AXIS_LABEL = "Lower GP accessibility"
Y_AXIS_LABEL = "More seniors per km²"

TITLE = (
    "Where to site a mixed-billing, 7-day multidisciplinary GP clinic - Greater Sydney"
)
SUBTITLE = (
    "Bivariate priority: GP accessibility (practices per 1,000 people within 20 min, "
    "competition adjusted) × senior density (seniors per km²)"
)


def load_or_fetch_suburb_polygons() -> gpd.GeoDataFrame:
    """Dissolved suburb polygons keyed by lowercased ``suburb_key`` (cached on disk)."""
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


def tercile_edges(values: pd.Series) -> tuple[float, float]:
    """The two interior cut points that split ``values`` into three equal-count classes."""
    vals = pd.to_numeric(values, errors="coerce").dropna().to_numpy(dtype=float)
    if vals.size < 3:
        raise SystemExit("Not enough classifiable suburbs to build terciles.")
    lo, hi = (float(x) for x in np.percentile(vals, [100.0 / 3.0, 200.0 / 3.0]))
    if not lo < hi:
        # Heavily tied distribution: fall back to the distinct values available.
        uniq = np.unique(vals)
        lo, hi = float(uniq[0]), float(uniq[-1])
        print(
            f"Warning: tied distribution; tercile edges collapsed to {lo:,.1f} / {hi:,.1f}.",
            file=sys.stderr,
        )
    return lo, hi


def classify_terciles(values: pd.Series, edges: tuple[float, float]) -> pd.Series:
    """Map values onto class 1/2/3 (NaN stays NaN). Class 3 is the top tercile."""
    vals = pd.to_numeric(values, errors="coerce")
    cls = np.digitize(vals.to_numpy(dtype=float), np.asarray(edges, dtype=float)) + 1
    return pd.Series(cls, index=vals.index, dtype="float64").where(vals.notna())


def classify_terciles_descending(
    values: pd.Series, edges: tuple[float, float]
) -> pd.Series:
    """
    Class 3 for the BOTTOM tercile of ``values``, class 1 for the top (NaN stays NaN).

    Accessibility is higher-is-better, but the dark end of the palette has to stay
    highest-priority, so the axis is reversed here rather than by negating the metric --
    that way the value printed in the tooltip and on the legend ticks is the real
    ``access_general``, not a sign-flipped stand-in.
    """
    return 4.0 - classify_terciles(values, edges)


def bivariate_fill(access_class: float, senior_class: float) -> str:
    """Palette lookup for one suburb; the caller guarantees both classes are 1..3."""
    return BIVARIATE_PALETTE[int(senior_class) - 1][int(access_class) - 1]


def draw_bivariate_legend(
    fig: plt.Figure,
    ax: plt.Axes,
    x_labels: tuple[str, str],
    y_labels: tuple[str, str],
    nodata_label: str,
    safe_left_frac: float,
) -> None:
    """
    Inset 3x3 legend square with axis arrows, plus the grey no-data swatch.

    ``x_labels`` and ``y_labels`` are the two interior tercile cut points already
    formatted, in the order they appear along the drawn axis (x left to right, y bottom to
    top). The X axis runs in decreasing accessibility, so its labels are supplied in
    decreasing order; formatting them here would hide that reversal inside the drawing
    code, where it is easy to get wrong and impossible to see.

    ``safe_left_frac`` is the axes-fraction x beyond which no suburb polygon is drawn
    (open water east of the metro crop), so the panel never sits on top of data.
    """
    fig.draw_without_rendering()  # settle the equal-aspect axes box first
    ax.apply_aspect()
    pos = ax.get_position()
    fig_w_in, fig_h_in = fig.get_size_inches()
    map_w_in = pos.width * fig_w_in

    pad_in = 0.10
    room_in = max(0.0, (1.0 - safe_left_frac) * map_w_in - 2 * pad_in)
    panel_w_in = float(np.clip(room_in, 2.35, 2.70))
    if room_in < panel_w_in:
        print(
            f"Note: legend panel ({panel_w_in:.2f} in) is wider than the open-water margin "
            f"({room_in:.2f} in); it may overlap the eastern suburbs.",
            flush=True,
        )

    left_gutter_in, right_pad_in = 0.54, 0.10
    x_gutter_in, title_in, nodata_in = 0.50, 0.42, 0.46
    square_in = panel_w_in - left_gutter_in - right_pad_in
    panel_h_in = nodata_in + x_gutter_in + square_in + title_in

    panel_x0 = pos.x1 - (panel_w_in + pad_in) / fig_w_in
    panel_y0 = pos.y0 + pad_in / fig_h_in

    fig.patches.append(
        FancyBboxPatch(
            (panel_x0, panel_y0),
            panel_w_in / fig_w_in,
            panel_h_in / fig_h_in,
            boxstyle="round,pad=0,rounding_size=0.006",
            transform=fig.transFigure,
            facecolor="white",
            alpha=0.94,
            edgecolor="#8a8a8a",
            linewidth=0.8,
            zorder=5,
        )
    )

    # --- the 3x3 square -------------------------------------------------------
    sq_x0 = panel_x0 + left_gutter_in / fig_w_in
    sq_y0 = panel_y0 + (nodata_in + x_gutter_in) / fig_h_in
    ax_leg = fig.add_axes(
        [sq_x0, sq_y0, square_in / fig_w_in, square_in / fig_h_in], zorder=6
    )
    ax_leg.set_xlim(0, 3)
    ax_leg.set_ylim(0, 3)
    ax_leg.set_axis_off()
    ax_leg.patch.set_visible(False)
    for yi in range(3):
        for xi in range(3):
            ax_leg.add_patch(
                Rectangle(
                    (xi, yi),
                    1,
                    1,
                    facecolor=BIVARIATE_PALETTE[yi][xi],
                    edgecolor="white",
                    linewidth=1.0,
                )
            )

    def off(inches: float) -> float:
        """An offset in inches, expressed in square-side fractions (for transAxes)."""
        return inches / square_in

    arrow_kw = dict(
        arrowstyle="-|>,head_width=0.16,head_length=0.34",
        color="#333333",
        lw=0.9,
        shrinkA=0,
        shrinkB=0,
    )
    ax_leg.annotate(
        "",
        xy=(1.0, -off(0.235)),
        xytext=(0.0, -off(0.235)),
        xycoords="axes fraction",
        textcoords="axes fraction",
        arrowprops=arrow_kw,
        annotation_clip=False,
    )
    ax_leg.annotate(
        "",
        xy=(-off(0.34), 1.0),
        xytext=(-off(0.34), 0.0),
        xycoords="axes fraction",
        textcoords="axes fraction",
        arrowprops=arrow_kw,
        annotation_clip=False,
    )
    ax_leg.text(
        0.5,
        -off(0.30),
        X_AXIS_LABEL,
        transform=ax_leg.transAxes,
        ha="center",
        va="top",
        fontsize=8.5,
        color="#222222",
    )
    ax_leg.text(
        -off(0.44),
        0.5,
        Y_AXIS_LABEL,
        transform=ax_leg.transAxes,
        rotation=90,
        ha="center",
        va="center",
        fontsize=8.5,
        color="#222222",
    )
    for frac, label in zip((1 / 3, 2 / 3), x_labels):
        ax_leg.text(
            frac,
            -off(0.075),
            label,
            transform=ax_leg.transAxes,
            ha="center",
            va="top",
            fontsize=6.5,
            color="#555555",
        )
    for frac, label in zip((1 / 3, 2 / 3), y_labels):
        ax_leg.text(
            -off(0.055),
            frac,
            label,
            transform=ax_leg.transAxes,
            ha="right",
            va="center",
            fontsize=6.5,
            color="#555555",
        )

    fig.text(
        panel_x0 + (panel_w_in / 2) / fig_w_in,
        panel_y0 + (panel_h_in - title_in / 2) / fig_h_in,
        "Priority class (terciles)",
        ha="center",
        va="center",
        fontsize=9.5,
        fontweight="bold",
        color="#111111",
        zorder=7,
    )

    # --- no-data swatch -------------------------------------------------------
    rule_y = panel_y0 + nodata_in / fig_h_in
    fig.add_artist(
        plt.Line2D(
            [panel_x0 + 0.10 / fig_w_in, panel_x0 + (panel_w_in - 0.10) / fig_w_in],
            [rule_y, rule_y],
            transform=fig.transFigure,
            color="#cfcfcf",
            linewidth=0.8,
            zorder=7,
        )
    )

    sw_w_in, sw_h_in = 0.22, 0.15
    sw_x0 = panel_x0 + 0.14 / fig_w_in
    sw_y0 = panel_y0 + (nodata_in / 2 - sw_h_in / 2) / fig_h_in
    fig.patches.append(
        Rectangle(
            (sw_x0, sw_y0),
            sw_w_in / fig_w_in,
            sw_h_in / fig_h_in,
            transform=fig.transFigure,
            facecolor=NO_DATA_FILL,
            edgecolor=NO_DATA_LINE,
            hatch=NO_DATA_HATCH,
            linewidth=0.7,
            zorder=7,
        )
    )
    fig.text(
        sw_x0 + (sw_w_in + 0.09) / fig_w_in,
        panel_y0 + (nodata_in / 2) / fig_h_in,
        textwrap.fill(nodata_label, width=30),
        ha="left",
        va="center",
        fontsize=7.5,
        color="#333333",
        linespacing=1.25,
        zorder=7,
    )


def _svg_hatch_defs() -> str:
    """Zero-size inline SVG holding the no-data fill pattern used by the Leaflet paths."""
    return f"""
    <svg xmlns="http://www.w3.org/2000/svg" aria-hidden="true"
         style="position:absolute;width:0;height:0;overflow:hidden">
      <defs>
        <pattern id="nodata-hatch" width="7" height="7" patternUnits="userSpaceOnUse"
                 patternTransform="rotate(45)">
          <rect width="7" height="7" fill="{NO_DATA_FILL}"></rect>
          <line x1="0" y1="0" x2="0" y2="7" stroke="{NO_DATA_LINE}" stroke-width="2"></line>
        </pattern>
      </defs>
    </svg>
    """


def _style_function(feature: dict) -> dict:
    """
    Style straight from the pre-computed class fill, so the HTML matches the PNG exactly.

    No-data suburbs get the hatch pattern (with a flat grey SVG paint fallback) and a
    dashed outline; they are never given a pressure colour.
    """
    props = feature["properties"]
    if props.get("_nodata"):
        return {
            "fillColor": f"url(#nodata-hatch) {NO_DATA_FILL}",
            "fillOpacity": 1.0,
            "color": NO_DATA_LINE,
            "weight": 0.7,
            "dashArray": "3,3",
        }  # unreachable while every suburb has an accessibility value; kept as the guard
    return {
        "fillColor": props.get("_fill", NO_DATA_FILL),
        "fillOpacity": 0.92,
        "color": "white",
        "weight": 0.25,
    }


def _legend_html(
    x_labels: tuple[str, str], y_labels: tuple[str, str], nodata_label: str
) -> str:
    """3x3 legend, axis arrows and no-data swatch, mirroring the PNG inset.

    ``x_labels`` runs left to right and ``y_labels`` bottom to top, matching
    :func:`draw_bivariate_legend`; the Y column is reversed here because HTML lays it out
    top down.
    """
    cells = []
    for yi in (2, 1, 0):  # top row of the grid = most seniors
        for xi in (0, 1, 2):
            cells.append(
                f'<div class="bl-cell" style="background:{BIVARIATE_PALETTE[yi][xi]}" '
                f'title="accessibility class {xi + 1} of 3 (3 = least accessible), '
                f'senior-density class {yi + 1} of 3"></div>'
            )
    return f"""
    <div class="bivar-legend">
      <div class="bl-title">Priority class (terciles)</div>
      <div class="bl-body">
        <div class="bl-ylab"><span>{Y_AXIS_LABEL} &#8594;</span></div>
        <div class="bl-ynums"><span>{y_labels[1]}</span><span>{y_labels[0]}</span></div>
        <div class="bl-grid">{''.join(cells)}</div>
        <div class="bl-xnums"><span>{x_labels[0]}</span><span>{x_labels[1]}</span></div>
        <div class="bl-xlab">{X_AXIS_LABEL} &#8594;</div>
      </div>
      <div class="bl-nodata"><span class="bl-swatch"></span>{nodata_label}</div>
    </div>
    <style>
      .bivar-legend {{
        position:fixed; bottom:22px; right:12px; z-index:9999; width:212px;
        background:rgba(255,255,255,0.95); padding:8px 10px 9px; border-radius:5px;
        box-shadow:0 1px 5px rgba(0,0,0,0.3); color:#222;
        font:12px/1.35 "Helvetica Neue",Arial,sans-serif;
      }}
      .bl-title {{ font-weight:700; font-size:12px; text-align:center; margin-bottom:6px; }}
      .bl-body {{ display:grid; grid-template-columns:14px 30px 1fr;
                  grid-template-rows:1fr 14px 16px; column-gap:3px; row-gap:2px; }}
      .bl-ylab {{ grid-column:1; grid-row:1; position:relative; }}
      .bl-ylab span {{ position:absolute; left:50%; top:50%; white-space:nowrap;
                       transform:translate(-50%,-50%) rotate(-90deg); font-size:10px; }}
      .bl-ynums {{ grid-column:2; grid-row:1; position:relative; font-size:9px; color:#555; }}
      .bl-ynums span {{ position:absolute; right:0; transform:translateY(-50%); }}
      .bl-ynums span:first-child {{ top:33.3%; }}
      .bl-ynums span:last-child {{ top:66.7%; }}
      .bl-grid {{ grid-column:3; grid-row:1; display:grid; gap:1.5px; aspect-ratio:1/1;
                  grid-template-columns:repeat(3,1fr); grid-template-rows:repeat(3,1fr); }}
      .bl-cell {{ width:100%; height:100%; }}
      .bl-xnums {{ grid-column:3; grid-row:2; position:relative; font-size:9px; color:#555; }}
      .bl-xnums span {{ position:absolute; top:1px; transform:translateX(-50%); }}
      .bl-xnums span:first-child {{ left:33.3%; }}
      .bl-xnums span:last-child {{ left:66.7%; }}
      .bl-xlab {{ grid-column:3; grid-row:3; text-align:center; font-size:10px; }}
      .bl-nodata {{ margin-top:9px; padding-top:7px; border-top:1px solid #ddd;
                    font-size:10.5px; line-height:1.3; }}
      .bl-swatch {{ display:inline-block; width:16px; height:11px; margin-right:5px;
                    vertical-align:-1px; border:1px solid {NO_DATA_LINE};
                    background-color:{NO_DATA_FILL};
                    background-image:repeating-linear-gradient(45deg,{NO_DATA_LINE} 0 1.5px,
                      transparent 1.5px 4px); }}
    </style>
    """


def format_total_gp(
    total_gp: float,
    is_floor: bool,
    status: str,
    clinics_listed: int,
) -> str:
    """
    GP count as a reader may safely read it.

    ``total_gp`` carries information only where ``gp_data_status == 'ok'``; elsewhere the
    stored 0 means "never scraped" or "counts all missing", so it is shown as unknown
    rather than as zero doctors -- the same rule the pressure metric follows. The one
    case where 0 is real is a suburb the scrape searched and found no clinic in at all
    (status ``ok`` with no clinics listed); that confirmed zero is labelled as such so it
    reads as the strongest supply gap, not as missing data.
    """
    if status != GP_STATUS_OK:
        return GP_UNKNOWN_TEXT
    if int(clinics_listed) == 0:
        return CONFIRMED_ZERO_TEXT
    return f"{total_gp:,.0f}{'+' if is_floor else ''}"


def format_clinics_listed(clinics_listed: int, status: str) -> str:
    """Clinic count, blanked for never-scraped suburbs where the stored 0 means nothing."""
    if status == GP_STATUS_NO_DATA:
        return GP_UNKNOWN_TEXT
    return f"{int(clinics_listed):,d}"


def save_interactive_html(
    gdf_4326: gpd.GeoDataFrame,
    fields: list[str],
    aliases: list[str],
    x_labels: tuple[str, str],
    y_labels: tuple[str, str],
    nodata_label: str,
    footnotes: list[str],
    out_path: Path,
) -> None:
    """Leaflet version of the same bivariate classification (hover + click)."""
    geojson = json.loads(gdf_4326.to_json())

    m = folium.Map(
        tiles="OpenStreetMap",
        location=[(VIEW_BBOX[1] + VIEW_BBOX[3]) / 2, (VIEW_BBOX[0] + VIEW_BBOX[2]) / 2],
        zoom_start=10,
    )
    folium.GeoJson(
        geojson,
        style_function=_style_function,
        highlight_function=lambda _f: {"weight": 2.5, "color": "#222222"},
        tooltip=folium.GeoJsonTooltip(fields=fields, aliases=aliases, sticky=True, labels=True),
        popup=folium.GeoJsonPopup(fields=fields, aliases=aliases, max_width=460),
    ).add_to(m)
    m.fit_bounds([[VIEW_BBOX[1], VIEW_BBOX[0]], [VIEW_BBOX[3], VIEW_BBOX[2]]])

    m.get_root().html.add_child(Element(_svg_hatch_defs()))
    m.get_root().html.add_child(
        Element(_legend_html(x_labels, y_labels, nodata_label))
    )

    title_html = f"""
    <div style="position:fixed;top:12px;left:56px;z-index:9999;
    background:rgba(255,255,255,0.93);padding:8px 12px;border-radius:4px;
    box-shadow:0 1px 4px rgba(0,0,0,0.25);max-width:min(560px,72vw);
    font:600 15px/1.3 'Helvetica Neue',Arial,sans-serif;color:#111;">
    {TITLE}
    <div style="font-weight:400;font-size:12px;margin-top:3px;color:#444;">{SUBTITLE}</div>
    </div>
    """
    m.get_root().html.add_child(Element(title_html))

    note_html = f"""
    <div style="position:fixed;bottom:22px;left:12px;z-index:9999;
    background:rgba(255,255,255,0.93);padding:7px 10px;border-radius:4px;
    box-shadow:0 1px 4px rgba(0,0,0,0.2);max-width:min(430px,58vw);
    font:11.5px/1.4 'Helvetica Neue',Arial,sans-serif;color:#333;">
    {"<br>".join(footnotes)}
    </div>
    """
    m.get_root().html.add_child(Element(note_html))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out_path))


def load_accessibility() -> pd.DataFrame:
    """
    The E2SFCA surface built by ``accessibility.py``, as the X axis of this map.

    Only the columns this map reads are kept, and the merge below is a left join on the
    suburb name with an explicit count of what failed to match, so a rename upstream shows
    up as hatched suburbs and a printed warning rather than as a silently shifted tercile.
    """
    if not ACCESS_PATH.exists():
        print(
            f"Missing {ACCESS_PATH}. Run `python accessibility.py` first: this map's X "
            "axis is its output.",
            file=sys.stderr,
        )
        sys.exit(1)
    acc = pd.read_csv(ACCESS_PATH)
    needed = [
        "suburb",
        "access_general",
        "access_general_pct",
        "access_senior",
        "practices_within_2km",
        "nearest_practice_km",
        "travel_cost_method",
    ]
    missing = [c for c in needed if c not in acc.columns]
    if missing:
        print(f"{ACCESS_PATH.name} is missing columns {missing}", file=sys.stderr)
        sys.exit(1)
    print(
        f"Read {len(acc)} accessibility rows from {ACCESS_PATH.name} "
        f"(method: {acc['travel_cost_method'].iloc[0]}).",
        flush=True,
    )
    return acc[needed]


def main() -> None:
    if not DATA_PATH.exists():
        print(f"Missing {DATA_PATH}", file=sys.stderr)
        sys.exit(1)

    df = pd.read_csv(DATA_PATH)
    require_status_column(df, DATA_PATH)
    print(f"Read {len(df)} suburbs from {DATA_PATH.name}. GP data status:")
    print(status_summary(df), flush=True)

    acc = load_accessibility()
    df = df.merge(acc, on="suburb", how="left")
    unmatched = int(df["access_general"].isna().sum())
    if unmatched:
        print(
            f"Warning: {unmatched} suburbs got no accessibility value from "
            f"{ACCESS_PATH.name}; they will be hatched, not ranked.",
            file=sys.stderr,
        )

    pop = pd.to_numeric(df["population"], errors="coerce")
    pct = pd.to_numeric(df["over_65_pct"], errors="coerce")
    area = pd.to_numeric(df["area"], errors="coerce")
    total_gp = pd.to_numeric(df["total_gp"], errors="coerce").fillna(0).clip(lower=0)
    df["total_gp"] = total_gp

    # The X axis. No gp_data_status filter: accessibility is a property of where the
    # register's practices are and of who else can reach them, so it is defined for the
    # 239 suburbs the clinic scrape never returned exactly as it is for the other 441.
    # This is the whole reason the hatched class collapses to nothing.
    df["Access_General"] = pd.to_numeric(df["access_general"], errors="coerce")

    # Kept for the tooltip only, and deliberately NOT an axis: this is the inside-polygon
    # head count that ranked Croydon first.
    ok = rankable_mask(df)
    df["People_Per_GP_Ratio"] = np.where(ok, pop / (total_gp + 1), np.nan)

    has_area = area.notna() & (area > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        df["Senior_Density_per_km2"] = np.where(has_area, pop * (pct / 100.0) / area, np.nan)
        df["_overall_people_per_km2"] = np.where(has_area, pop / area, np.nan)

    polys = load_or_fetch_suburb_polygons()
    df["suburb_key"] = df["suburb"].astype(str).str.strip().str.lower()
    merged = df.merge(polys, on="suburb_key", how="left")

    no_geom = int(merged["geometry"].isna().sum())
    if no_geom:
        print(f"Excluded {no_geom} suburbs with no OSM polygon match (cannot be drawn).", flush=True)
    plot_df = merged.dropna(subset=["geometry"]).copy()

    n_sparse = 0
    if FILTER_LOW_OVERALL_DENSITY:
        keep = plot_df["_overall_people_per_km2"].astype(float) >= MIN_OVERALL_PEOPLE_PER_KM2
        n_sparse = int((~keep).sum())
        if n_sparse:
            print(
                f"Excluded {n_sparse} sparse suburbs (< {MIN_OVERALL_PEOPLE_PER_KM2:.0f} "
                "people/km²) so the terciles are not skewed by near-empty polygons.",
                flush=True,
            )
        plot_df = plot_df.loc[keep].copy()

    # --- classification -------------------------------------------------------
    # No gp_data_status gate on the X axis any more. The only thing that can stop a suburb
    # being classified is a metric that does not exist: a missing accessibility value or a
    # missing/zero area. Hatch exactly that, and nothing else.
    metrics_ok = plot_df["Access_General"].notna() & plot_df["Senior_Density_per_km2"].notna()
    classified = metrics_ok

    x_edges = tercile_edges(plot_df.loc[classified, "Access_General"])
    y_edges = tercile_edges(plot_df.loc[classified, "Senior_Density_per_km2"])

    # Class 3 on the X axis is the LEAST accessible tercile, so the palette's dark corner
    # stays "highest priority". Senior density is census-derived and known everywhere.
    plot_df["access_class"] = classify_terciles_descending(
        plot_df["Access_General"].where(classified), x_edges
    )
    plot_df["senior_class"] = classify_terciles(plot_df["Senior_Density_per_km2"], y_edges)
    plot_df["_nodata"] = ~classified
    plot_df["_fill"] = [
        NO_DATA_FILL if nd else bivariate_fill(ac, sc)
        for nd, ac, sc in zip(
            plot_df["_nodata"], plot_df["access_class"], plot_df["senior_class"]
        )
    ]

    n_map = len(plot_df)
    n_class = int(classified.sum())
    n_set_aside = n_map - n_class

    # What the old X axis would have refused to rank, so the gain is stated rather than
    # asserted. gp_data_status still governs whether a GP COUNT may be shown to a reader;
    # it no longer governs whether a suburb can be ranked.
    was_unrankable = ~rankable_mask(plot_df)
    n_recovered = int((was_unrankable & classified).sum())
    recovered_status = plot_df.loc[was_unrankable & classified, "gp_data_status"].value_counts()

    print(
        f"Mapped {n_map} suburbs: {n_class} classified into the 3x3 grid, "
        f"{n_set_aside} hatched (accessibility or area not computable).",
        flush=True,
    )
    print(
        f"Hatched under the old people-per-GP axis: {int(was_unrankable.sum())}. "
        f"Now classified: {n_recovered}. Still hatched: {n_set_aside}.",
        flush=True,
    )
    for status, n in recovered_status.items():
        print(f"  recovered: {status:18s} {int(n):4d}", flush=True)
    if n_set_aside:
        for status, n in plot_df.loc[~classified, "gp_data_status"].value_counts().items():
            print(f"  still hatched: {status:18s} {int(n):4d}", flush=True)
    n_never = int(
        (plot_df["gp_data_status"] == GP_STATUS_NO_DATA).sum()
    )
    print(
        f"Accessibility terciles (practices per 1,000 people within 20 min): "
        f"least accessible <= {x_edges[0]:.3f} | {x_edges[0]:.3f}-{x_edges[1]:.3f} | "
        f"best served > {x_edges[1]:.3f}",
        flush=True,
    )
    print(
        f"Senior-density terciles (per km²): <= {y_edges[0]:,.0f} | "
        f"{y_edges[0]:,.0f}-{y_edges[1]:,.0f} | > {y_edges[1]:,.0f}",
        flush=True,
    )
    top_cell = int(((plot_df["access_class"] == 3) & (plot_df["senior_class"] == 3)).sum())
    print(
        f"Top-priority cell (least accessible tercile and densest senior tercile): "
        f"{top_cell} suburbs.",
        flush=True,
    )
    top_recovered = int((was_unrankable & (plot_df["access_class"] == 3) & (plot_df["senior_class"] == 3)).sum())
    print(
        f"  of which {top_recovered} were hatched and unranked under the old axis.",
        flush=True,
    )

    # A sanity read on the two suburbs the whole rebuild turns on, printed every run so a
    # regression in either direction is visible without opening the PNG.
    for name in ("Croydon", "Heathcote", "Chippendale", "Beacon Hill", "Bayview"):
        row = plot_df.loc[plot_df["suburb"].astype(str) == name]
        if row.empty:
            print(f"  check: {name:14s} not in the mapped cohort", flush=True)
            continue
        r = row.iloc[0]
        print(
            f"  check: {name:14s} access {float(r['Access_General']):.3f} "
            f"(pct {float(r['access_general_pct']):5.1f}) class {int(r['access_class'])}/3, "
            f"seniors/km2 {float(r['Senior_Density_per_km2']):7,.0f} class "
            f"{int(r['senior_class'])}/3, practices within 2 km "
            f"{int(r['practices_within_2km'])}",
            flush=True,
        )

    # A confirmed zero: the scrape searched the suburb and reported no clinic at all, so
    # total_gp = 0 here is evidence, not the absence of it. They used to be pushed into the
    # top pressure tercile by construction (population / 1). Under accessibility they are
    # ranked on the capacity actually within reach, which is a weaker but truer signal, so
    # the class they land in is printed rather than assumed.
    confirmed_zero = rankable_mask(plot_df) & (
        pd.to_numeric(plot_df["clinics_listed"], errors="coerce") == 0
    )
    n_confirmed_zero = int(confirmed_zero.sum())
    cz = plot_df.loc[confirmed_zero]
    cz_names = ", ".join(sorted(cz["suburb"].astype(str))[:3])
    if n_confirmed_zero > 3:
        cz_names += f", +{n_confirmed_zero - 3} more"
    cz_detail = "; ".join(
        f"{r['suburb']} access class {int(r['access_class'])}/3"
        for _, r in cz.sort_values("suburb").iterrows()
    )
    if n_confirmed_zero:
        print(
            f"Confirmed zero supply: {n_confirmed_zero} mapped suburbs were searched with "
            f"no clinic found at all -- {cz_detail}. No longer forced to the top of the "
            "X axis; ranked on reachable capacity.",
            flush=True,
        )

    gdf_4326 = gpd.GeoDataFrame(plot_df, geometry="geometry", crs="EPSG:4326")
    gdf = gdf_4326.to_crs(PLOT_CRS)

    footnotes = [
        f"Darkest cell = least accessible tercile and densest senior tercile: {top_cell} "
        f"suburbs, {top_recovered} of them unrankable on the old axis.",
        "X axis: E2SFCA accessibility (accessibility.py) = practices per 1,000 people within "
        "a 20 min cutoff, each practice shared with everyone who can reach it. Supply is the "
        "Geoscience Australia HealthDirect register, 2,089 NSW general practices, extract "
        "2025-03-07, placed by coordinates.",
        "It replaces population / (total GP + 1), which counted only doctors INSIDE a suburb "
        "and so ranked Croydon first on the 1 practice inside it while 27 sit within 2 km. "
        "Low accessibility means too few practices for the people sharing them, never that "
        "no GP is nearby.",
        f"Accessibility exists for all {n_map} mapped suburbs, the {n_never} the clinic "
        "scrape never returned included, because it depends on where practices are and who "
        f"else can reach them: {n_recovered} suburbs this map used to hatch as unranked now "
        f"carry a real class, {n_set_aside} remain hatched.",
        f"Terciles computed within the {n_class} classified suburbs. Not drawn: {no_geom} "
        f"suburbs with no OSM polygon, {n_sparse} sparse suburbs "
        f"(< {MIN_OVERALL_PEOPLE_PER_KM2:.0f} people/km²).",
        "Travel cost is straight-line at 35 km/h, an optimistic bound calibrated against a "
        "routed OSM drive network and weakest across water, so peninsulas are flattered. The "
        "register misses roughly 10 to 12% of practices nationally, so a thin reading is a "
        "floor on supply, not proof of absence.",
    ]
    if n_confirmed_zero:
        footnotes.insert(
            4,
            f"Confirmed zero: {n_confirmed_zero} mapped suburbs ({cz_names}) were searched "
            "and no clinic found - a real 0, not missing data. They are no longer forced to "
            f"the top of the X axis; they rank on reachable capacity ({cz_detail}).",
        )

    # Tick labels in drawn-axis order. X runs left to right in DECREASING accessibility,
    # so the upper cut point is the left-hand tick: get this backwards and the legend
    # silently contradicts the map.
    x_labels = (f"{x_edges[1]:.3f}", f"{x_edges[0]:.3f}")
    y_labels = (f"{y_edges[0]:,.0f}", f"{y_edges[1]:,.0f}")
    nodata_text = no_data_label(n_set_aside)

    # --- static map -----------------------------------------------------------
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    plt.rcParams["hatch.linewidth"] = 0.5
    fig, ax = plt.subplots(figsize=(13.5, 10.8))
    fig.subplots_adjust(left=0.02, right=0.98, top=0.905, bottom=0.085)
    ax.set_facecolor(WATER_COLOR)

    classified_gdf = gdf.loc[~gdf["_nodata"]]
    nodata_gdf = gdf.loc[gdf["_nodata"]]
    if not classified_gdf.empty:
        classified_gdf.plot(
            ax=ax,
            color=classified_gdf["_fill"].tolist(),
            edgecolor="white",
            linewidth=0.15,
        )
    if not nodata_gdf.empty:
        nodata_gdf.plot(
            ax=ax,
            color=NO_DATA_FILL,
            edgecolor=NO_DATA_LINE,
            linewidth=0.25,
            hatch=NO_DATA_HATCH,
        )

    view_poly = gpd.GeoDataFrame(geometry=[box(*VIEW_BBOX)], crs="EPSG:4326").to_crs(PLOT_CRS)
    xmin, ymin, xmax, ymax = view_poly.total_bounds
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    for spine in ax.spines.values():
        spine.set_visible(False)

    fig.text(0.5, 0.966, TITLE, ha="center", va="center", fontsize=16, fontweight="bold")
    fig.text(0.5, 0.934, SUBTITLE, ha="center", va="center", fontsize=11.5, color="#333333")

    # Keep the legend over open water: no polygon is drawn east of this axes fraction.
    data_xmax = float(gdf.total_bounds[2])
    safe_left_frac = min(1.0, max(0.0, (min(data_xmax, xmax) - xmin) / (xmax - xmin)))
    draw_bivariate_legend(fig, ax, x_labels, y_labels, nodata_text, safe_left_frac)

    # Hard-wrapped, not left to the renderer: with bbox_inches="tight" a single long line
    # widens the whole figure and squashes the map into a corner of the PNG.
    pos = ax.get_position()
    fig.text(
        pos.x0,
        pos.y0 - 0.014,
        "\n".join(
            textwrap.fill(note, width=FOOTNOTE_WRAP_CHARS, subsequent_indent="    ")
            for note in footnotes
        ),
        ha="left",
        va="top",
        fontsize=8.5,
        color="#3a3a3a",
        linespacing=1.5,
    )

    fig.savefig(OUT_PNG, dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Saved {OUT_PNG.resolve()}", flush=True)

    # --- interactive map ------------------------------------------------------
    def fmt(value: float, suffix: str = "") -> str:
        return "not ranked" if pd.isna(value) else f"{value:,.0f}{suffix}"

    def fmt3(value: float) -> str:
        return "not ranked" if pd.isna(value) else f"{value:,.3f}"

    html_df = pd.DataFrame(
        {
            "suburb": gdf_4326["suburb"].to_numpy(),
            "population": [fmt(v) for v in gdf_4326["population"]],
            "access_general": [fmt3(v) for v in gdf_4326["Access_General"]],
            "access_general_pct": [
                "not ranked" if pd.isna(v) else f"{v:,.1f}"
                for v in gdf_4326["access_general_pct"]
            ],
            "practices_within_2km": [fmt(v) for v in gdf_4326["practices_within_2km"]],
            "nearest_practice_km": [
                "not known" if pd.isna(v) else f"{v:,.2f} km"
                for v in gdf_4326["nearest_practice_km"]
            ],
            "total_gp": [
                format_total_gp(gp, floor, status, clinics)
                for gp, floor, status, clinics in zip(
                    gdf_4326["total_gp"],
                    gdf_4326["total_gp_is_floor"],
                    gdf_4326["gp_data_status"],
                    gdf_4326["clinics_listed"],
                )
            ],
            "clinics_listed": [
                format_clinics_listed(clinics, status)
                for clinics, status in zip(
                    gdf_4326["clinics_listed"], gdf_4326["gp_data_status"]
                )
            ],
            "gp_data_status": gdf_4326["gp_data_status"].to_numpy(),
            "people_per_gp_retired": [fmt(v) for v in gdf_4326["People_Per_GP_Ratio"]],
            "seniors_per_km2": [fmt(v) for v in gdf_4326["Senior_Density_per_km2"]],
            "access_class": [fmt(v, " of 3") for v in gdf_4326["access_class"]],
            "senior_class": [fmt(v, " of 3") for v in gdf_4326["senior_class"]],
            "_fill": gdf_4326["_fill"].to_numpy(),
            "_nodata": gdf_4326["_nodata"].to_numpy(),
        },
        index=gdf_4326.index,
    )
    # Order matters: the ranked metric first, the register evidence behind it next, and the
    # retired inside-polygon head count last and explicitly labelled as retired, so a reader
    # can see the size of the error rather than being asked to take it on trust.
    fields = [
        "suburb",
        "population",
        "access_general",
        "access_general_pct",
        "access_class",
        "seniors_per_km2",
        "senior_class",
        "practices_within_2km",
        "nearest_practice_km",
        "total_gp",
        "clinics_listed",
        "gp_data_status",
        "people_per_gp_retired",
    ]
    aliases = [
        "Suburb",
        "Population",
        "Accessibility (practices per 1,000 within 20 min)",
        "Accessibility percentile (100 = best served)",
        "Accessibility class (3 = least accessible)",
        "Seniors per km²",
        "Senior-density class (3 = densest)",
        "Register practices within 2 km",
        "Nearest register practice",
        "Total GPs (scrape, context only)",
        "Clinics listed (scrape, context only)",
        "GP data status",
        "RETIRED metric: people per GP inside suburb",
    ]
    html_gdf = gpd.GeoDataFrame(html_df, geometry=gdf_4326.geometry, crs="EPSG:4326")
    save_interactive_html(
        html_gdf, fields, aliases, x_labels, y_labels, nodata_text, footnotes, OUT_HTML
    )
    print(f"Saved {OUT_HTML.resolve()} - open in a browser (hover + click suburbs)", flush=True)


if __name__ == "__main__":
    main()
