"""
Mixed billing feasibility: inverted-U income FIT (choropleth) + real GP locations (dots).

Reads scraped_data/eda_data.csv, scraped_data/ga_general_practices_nsw.csv and cached OSM
suburb polygons (same cache as the priority map). Writes:
  - eda/plots/mixed_billing_income_vs_supply.png
  - eda/plots/mixed_billing_income_vs_supply.html

TWO THINGS CHANGED FROM THE EARLIER VERSION OF THIS MAP.

1. The choropleth is no longer raw household income. It is the D1 mixed-billing fit score
   imported from ``site_score.mixed_billing_fit``: an inverted U, full score on a middle
   plateau, tapering linearly to zero at each outer knot. The knots are DERIVED from
   percentiles of the income column at run time, not hardcoded, so quoting figures here
   would go stale the moment the data changed - the rendered caption prints the live band
   (currently a $2,101-$2,872 plateau, zero at or beyond $1,583 and $3,492). Dark now
   means "best fit for a mixed-billing offer", NOT "richest". A monotonic income ramp made
   the wealthiest suburbs look like the best prospects, which is backwards: the standard
   consult rebate is about $43 against a typical private fee near $90, so mixed billing
   needs households able to absorb a gap of roughly $45 per visit and not already buying
   full private care. The score is imported rather than reimplemented so this map and the
   ranking cannot drift apart.

2. Supply is no longer the clinic scrape's per-suburb GP head count. Every dot is one real
   general practice from the Geoscience Australia HealthDirect register (NSW extract
   2025-03-07, filtered to ``nhsd_service_type == 'General practice service'``, 2,089
   statewide), drawn at its own coordinates. A head count inside a suburb polygon is the
   metric that ranked Croydon first while 27 practices sat within 2 km of it, so the map
   now shows where practices actually are and lets the reader see clusters crossing suburb
   boundaries. Practices that fall outside the 680 study suburbs, or inside one of the 30
   with no polygon, are drawn as hollow rings: they compete for the same patients but are
   attributed to no study suburb.

THREE DIFFERENT ABSENCES, still drawn as three different things rather than one silent
one. Under an inverted U a pale suburb can mean two opposite commercial problems, and a
sequential ramp cannot tell them apart, so the two zero causes are hatched apart:

- fit 0 because income is at or below the lower knot -> brown forward hatch. Households
  cannot absorb the gap; this is a bulk-bill-only market, not a mixed-billing one.
- fit 0 because income is at or above the upper knot -> navy back hatch. Households buy full
  private care. These are the suburbs the old monotonic income map coloured DARKEST.
- no income recorded at all -> flat grey. Fit is not computable, which is not a score of
  zero. (Currently no suburb is in this class; the count is printed so a reader can see
  that rather than assume it.)

The scraped GP columns survive only as clearly labelled tooltip context, so the register
and the scrape can be compared, and they follow the ``gp_supply.py`` rule: ``total_gp``
is printed as a number only where ``gp_data_status == 'ok'``.

Run from project root:
  python eda/plot_mixed_billing_choropleth.py
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import folium
import geopandas as gpd
import matplotlib as mpl
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import osmnx as ox
import pandas as pd
from branca.colormap import LinearColormap
from folium import Element
from folium.plugins import StripePattern
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize, to_hex, to_rgba
from matplotlib.lines import Line2D
from shapely.geometry import box

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gp_supply import (
    GP_STATUS_OK,
    require_status_column,
    status_summary,
)
from site_score import (
    income_knots_from_percentiles,
    mixed_billing_fit,
)

DATA_PATH = ROOT / "scraped_data" / "eda_data.csv"
REGISTER_PATH = ROOT / "scraped_data" / "ga_general_practices_nsw.csv"
EDA_DIR = Path(__file__).resolve().parent
PLOTS_DIR = EDA_DIR / "plots"
GEO_CACHE_DIR = EDA_DIR / "geo_cache"
CACHE_GPKG = GEO_CACHE_DIR / "osm_suburb_polygons_greater_sydney.gpkg"
OUT_PNG = PLOTS_DIR / "mixed_billing_income_vs_supply.png"
OUT_HTML = PLOTS_DIR / "mixed_billing_income_vs_supply.html"

OSM_BBOX = (150.5, -34.35, 151.65, -33.45)
VIEW_BBOX = (150.74, -34.08, 151.52, -33.58)
PLOT_CRS = "EPSG:7856"

# Provenance of the dots, stamped into the rendered footnote so the map carries its source.
REGISTER_NAME = "Geoscience Australia National HealthDirect Health Facilities"
REGISTER_EXTRACT = "2025-03-07"
REGISTER_NSW_TOTAL = 2089
REGISTER_LAYER_NSW_ROWS = 2482
REGISTER_LICENCE = "CC-BY 4.0"

#: The income band drawn on this map MUST be the one the score actually used, or the
#: figure contradicts the model it illustrates. site_score.main() does not use the static
#: INCOME_KNOTS fallback - it derives the knots from the income distribution it loads, at
#: percentiles 15/40/80/95 - so importing the constant here drew the wrong band. MEASURED:
#: the static values are (1550, 2050, 2750, 3500) against derived (1583, 2101, 2872, 3492),
#: which moved the hatch thresholds on the published map by up to $122/wk.
INCOME_KNOTS = income_knots_from_percentiles(
    pd.read_csv(DATA_PATH)["median_household_income_weekly"].to_numpy(float)
)

# Fit is a 0..1 score by construction, so the colour scale is not clipped and carries no
# judgement of its own: the judgement is entirely in INCOME_KNOTS, derived above.
FIT_VMIN = 0.0
FIT_VMAX = 1.0

# --- Practice dots --------------------------------------------------------------
# One dot per practice, uniform size. Size must not encode anything here: there is no
# practitioner-count field anywhere in the register, so a graduated marker would be
# inventing magnitude. Dot density is the only supply magnitude this source supports.
MARKER_COLOR = "#37285a"
MARKER_EDGE = "#f7f7fb"
MARKER_ALPHA = 0.78
MARKER_EDGE_ALPHA = 0.95
MARKER_EDGE_WIDTH = 0.4
R_DOT_PT = 1.6

# Practices outside the 680, or inside one of the 30 suburbs with no polygon: hollow, so
# they read as real competing supply that is attributed to no suburb on this map.
R_RING_PT = 2.0
RING_EDGE_WIDTH = 0.8

# Leaflet CircleMarker radii are pixels; scale the same radii so the HTML mirrors the PNG.
HTML_RADIUS_SCALE = 1.8

# The two fit-zero causes. Different hatch direction AND different hue, because direction
# alone is hard to read at this polygon size.
BELOW_HATCH = "////"
BELOW_HATCH_COLOR = "#8a5324"
ABOVE_HATCH = "\\\\\\\\"
ABOVE_HATCH_COLOR = "#243f8a"
HATCH_LINEWIDTH = 0.7

WATER_COLOR = "#7eb8d9"
NO_INCOME_COLOR = "#d9d9d9"

# Register rows whose organisation name says they are not conventional general practice.
# The nhsd_service_type filter removes the obvious non-GPs (nurse-led clinics, skin cancer
# clinics and so on) but a residue survives it. Flagged and counted, NEVER dropped: some
# of these do bulk bill general consultations, so the call belongs to a human.
NON_GP_LIKE_PATTERNS: tuple[str, ...] = (
    "headspace",
    "aboriginal",
    "refugee health",
    "travel doctor",
    "university health service",
)

FOOTNOTE_WRAP_CHARS = 150
# YlGn slice used for the choropleth: stops short of the near-black top so the practice
# dots stay visible over the 257 suburbs that score exactly 1.0.
CMAP_NAME = "YlGn"
CMAP_LOW, CMAP_HIGH = 0.05, 0.80

TITLE = "Mixed-billing fit vs. where the GPs actually are - Greater Sydney"
SUBTITLE_1 = (
    "Choropleth: D1 inverted-U mixed-billing fit (dark = best fit, NOT richest). Full score "
    f"${INCOME_KNOTS[1]:,.0f}-${INCOME_KNOTS[2]:,.0f}/wk household income, tapering to 0 at "
    f"${INCOME_KNOTS[0]:,.0f} and at ${INCOME_KNOTS[3]:,.0f}."
)
SUBTITLE_2 = (
    "Dots: one real general practice each, from the GA HealthDirect register "
    f"({REGISTER_NSW_TOTAL:,} in NSW, extract {REGISTER_EXTRACT}) - not a per-suburb count."
)


def _rgba_css(color: str, alpha: float) -> str:
    """``rgba(...)`` string, so an HTML legend swatch can be translucent-filled but hard-edged."""
    r, g, b, _ = to_rgba(color)
    return f"rgba({round(r * 255)},{round(g * 255)},{round(b * 255)},{alpha:.2f})"


def mpl_text(text: str) -> str:
    r"""
    Escape dollar signs for matplotlib, which otherwise parses ``$...$`` as mathtext.

    This map's captions are full of dollar amounts, and a pair of them turns everything
    between into italic maths: ``$2,050-$2,750`` rendered as an italic "2,050 - 2,750" with
    a MINUS SIGN glyph for the hyphen, and ``at $1,550 and $3,500`` lost the word "and"
    entirely. Both are silent, and one of them puts a long dash into rendered output. The
    same strings go to HTML unescaped, so the escape belongs here and not in the constants.

    >>> mpl_text("plateau $2,050-$2,750/wk")
    'plateau \\$2,050-\\$2,750/wk'
    >>> mpl_text("no dollars here")
    'no dollars here'
    """
    return text.replace("$", r"\$")


def truncated_cmap(name: str, low: float, high: float, n: int = 256):
    """
    A colormap that stops short of ``name``'s darkest end.

    Needed because 257 of the 650 mapped suburbs score exactly 1.0 on D1, so the top of the
    ramp covers nearly 40% of the map. On full YlGn that is near-black green, which both
    flattens the choropleth and hides the practice dots drawn on top of it.

    >>> cm = truncated_cmap("YlGn", 0.05, 0.82)
    >>> to_hex(cm(1.0)) != to_hex(plt.cm.YlGn(1.0))
    True
    """
    base = plt.get_cmap(name)
    return mpl.colors.LinearSegmentedColormap.from_list(
        f"{name}_{low:g}_{high:g}", base(np.linspace(low, high, n))
    )


#: The one colormap instance both renderers use, and the palest colour on it. The legend's
#: two hatch swatches are drawn on that colour, so retuning the ramp cannot leave the
#: legend describing a background the map does not actually have.
FIT_CMAP = truncated_cmap(CMAP_NAME, CMAP_LOW, CMAP_HIGH)
PALE_FIT_COLOR = to_hex(FIT_CMAP(0.0))


def format_total_gp(total_gp: float, is_floor: bool, status: str, clinics_listed: float) -> str:
    """
    Scraped GP count as a reader may safely read it (see ``gp_supply.py``).

    ``total_gp`` is stored as 0 for every suburb the scrape never returned, so it carries
    information only where ``gp_data_status == 'ok'``. This map does not rank on it at all,
    but it is shown so the register and the scrape can be compared, and a stored 0 must
    never be printed as though it meant zero doctors.

    >>> format_total_gp(7.0, False, "ok", 3)
    '7'
    >>> format_total_gp(7.0, True, "ok", 3)
    '7+'
    >>> format_total_gp(0.0, False, "ok", 0)
    '0 - no clinic found in suburb'
    >>> format_total_gp(0.0, False, "no_data", 0)
    'not known'
    >>> format_total_gp(0.0, False, "gp_count_unknown", 4)
    'not known'
    """
    if status != GP_STATUS_OK:
        return "not known"
    if float(clinics_listed) == 0:
        return "0 - no clinic found in suburb"
    return f"{total_gp:,.0f}{'+' if is_floor else ''}"


def fit_scores(
    income: pd.Series, knots: tuple[float, float, float, float] | None = None
) -> np.ndarray:
    """
    D1 fit for each income, NaN where income is missing.

    ``mixed_billing_fit`` returns 0.0 for a NaN income, which is right for a ranking (a
    suburb with no income data cannot be scored as a mixed-billing prospect) but wrong for
    a choropleth: it would paint "unknown" and "measured, outside the band" the same pale
    colour. So missing income is kept as NaN here and drawn flat grey.

    Knots are passed explicitly here so the doctest states the SHAPE and does not move
    when the income data changes. Production callers pass nothing and get the derived
    band.

    >>> k = (1550.0, 2050.0, 2750.0, 3500.0)
    >>> vals = fit_scores(pd.Series([1200.0, 1800.0, 2400.0, 3125.0, 4000.0, float("nan")]), k)
    >>> [None if np.isnan(v) else round(float(v), 3) for v in vals]
    [0.0, 0.5, 1.0, 0.5, 0.0, None]
    """
    knots = INCOME_KNOTS if knots is None else knots
    inc = pd.to_numeric(income, errors="coerce").to_numpy(dtype=float)
    return np.array(
        # INCOME_KNOTS must be passed explicitly. mixed_billing_fit binds its default
        # at definition time from site_score, so rebinding the name in this module changes
        # the caption and not the colours - which is worse than not deriving them at all.
        [mixed_billing_fit(float(v), knots) if np.isfinite(v) else np.nan
         for v in inc],
        dtype=float,
    )


def fit_verdict(
    income: float, fit: float,
    knots: tuple[float, float, float, float] | None = None,
) -> str:
    """
    One short reader-facing phrase per suburb, naming WHICH side of the U it sits on.

    Knots passed explicitly so the phrasing under test does not move with the data.

    >>> k = (1550.0, 2050.0, 2750.0, 3500.0)
    >>> fit_verdict(float("nan"), float("nan"), k)
    'no income recorded, fit not computable'
    >>> fit_verdict(1200.0, 0.0, k)
    'income at or below $1,550: cannot absorb the gap'
    >>> fit_verdict(4000.0, 0.0, k)
    'income at or above $3,500: buys full private care'
    >>> fit_verdict(2400.0, 1.0, k)
    'best fit: on the mixed-billing plateau'
    >>> fit_verdict(1800.0, 0.5, k)
    'partial fit: on the lower taper'
    >>> fit_verdict(3125.0, 0.5, k)
    'partial fit: on the upper taper'
    """
    lo, plateau_lo, _plateau_hi, hi = INCOME_KNOTS if knots is None else knots
    if not np.isfinite(income) or not np.isfinite(fit):
        return "no income recorded, fit not computable"
    if fit <= 0.0:
        if income <= lo:
            return f"income at or below ${lo:,.0f}: cannot absorb the gap"
        return f"income at or above ${hi:,.0f}: buys full private care"
    if fit >= 1.0:
        return "best fit: on the mixed-billing plateau"
    return f"partial fit: on the {'lower' if income < plateau_lo else 'upper'} taper"


def fit_counts(frame: pd.DataFrame) -> dict[str, int]:
    """
    How many suburbs sit in each fit class, including the two DIFFERENT zero causes.

    The two zero causes are counted from income against the outer knots, then asserted
    against the number of suburbs the score itself puts at zero, so the legend cannot drift
    out of step with the choropleth it is labelling.
    """
    inc = pd.to_numeric(frame["median_household_income_weekly"], errors="coerce")
    fit = pd.to_numeric(frame["mixed_billing_fit"], errors="coerce")
    lo, _, _, hi = INCOME_KNOTS
    counts = {
        "best": int((fit >= 1.0).sum()),
        "partial": int(((fit > 0.0) & (fit < 1.0)).sum()),
        "below": int((inc.notna() & (inc <= lo)).sum()),
        "above": int((inc.notna() & (inc >= hi)).sum()),
        "no_income": int(inc.isna().sum()),
    }
    scored_zero = int((fit.notna() & (fit <= 0.0)).sum())
    if counts["below"] + counts["above"] != scored_zero:
        raise AssertionError(
            f"fit-zero split {counts['below']} + {counts['above']} does not match the "
            f"{scored_zero} suburbs scoring 0; the legend would misdescribe the map"
        )
    if counts["best"] + counts["partial"] + scored_zero + counts["no_income"] != len(frame):
        raise AssertionError("fit classes do not partition the frame")
    return counts


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


def load_register(polys: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Every NSW general practice in the register as a point, tagged with the suburb it lands in.

    ``attributed`` is True where the point falls inside one of the mapped suburb polygons.
    The rest are drawn hollow: they are real supply competing for the same patients but
    belong to no suburb on this map, which is exactly why a per-suburb head count went
    wrong in the first place.
    """
    if not REGISTER_PATH.exists():
        print(
            f"Missing {REGISTER_PATH}. Run `python health_facilities.py` to fetch the GA "
            "HealthDirect register first.",
            file=sys.stderr,
        )
        sys.exit(1)
    reg = pd.read_csv(REGISTER_PATH)
    needed = {
        "organisation_name",
        "address",
        "suburb",
        "longitude",
        "latitude",
        "nhsd_service_type",
    }
    missing = sorted(needed - set(reg.columns))
    if missing:
        print(f"{REGISTER_PATH.name} is missing columns {missing}", file=sys.stderr)
        sys.exit(1)
    if len(reg) != REGISTER_NSW_TOTAL:
        print(
            f"Note: {REGISTER_PATH.name} has {len(reg)} rows, not the {REGISTER_NSW_TOTAL} "
            "this map's caption quotes. Counts below follow the file, not the caption.",
            flush=True,
        )
    pts = gpd.GeoDataFrame(
        reg,
        geometry=gpd.points_from_xy(reg["longitude"], reg["latitude"]),
        crs="EPSG:4326",
    )
    joined = gpd.sjoin(pts, polys[["suburb_key", "geometry"]], how="left", predicate="within")
    # A point exactly on a shared border can match two dissolved polygons; keep one row per
    # practice so a dot count stays a practice count and not a join artefact.
    joined = joined[~joined.index.duplicated(keep="first")].copy()
    joined["attributed"] = joined["suburb_key"].notna()
    lower = joined["organisation_name"].astype(str).str.lower()
    joined["non_gp_like"] = lower.str.contains("|".join(NON_GP_LIKE_PATTERNS), regex=True)
    return joined


def in_bbox(frame: gpd.GeoDataFrame, bbox: tuple[float, float, float, float]) -> np.ndarray:
    """Boolean mask of points inside ``bbox`` (minx, miny, maxx, maxy), in EPSG:4326."""
    x = frame.geometry.x.to_numpy()
    y = frame.geometry.y.to_numpy()
    return (x >= bbox[0]) & (x <= bbox[2]) & (y >= bbox[1]) & (y <= bbox[3])


def _fit_style(feature: dict, norm: Normalize, cmap) -> dict:
    """Base choropleth style: mixed-billing fit, flat grey where income (so fit) is missing."""
    props = feature["properties"]
    raw = props.get("mixed_billing_fit")
    try:
        v = float(raw)
    except (TypeError, ValueError):
        v = float("nan")
    if not np.isfinite(v):
        return {
            "fillColor": NO_INCOME_COLOR,
            "color": "white",
            "weight": 0.25,
            "fillOpacity": 0.88,
        }
    return {
        "fillColor": to_hex(cmap(norm(v))),
        "color": "white",
        "weight": 0.25,
        "fillOpacity": 0.92,
    }


def _practice_tooltip(row: pd.Series) -> str:
    """Tooltip for one register practice: the identifying fields, and nothing invented."""
    where = "inside a mapped suburb" if bool(row["attributed"]) else "outside the mapped suburbs"
    flag = (
        "<br><i>name suggests this is not conventional general practice</i>"
        if bool(row["non_gp_like"])
        else ""
    )
    return (
        "<div style='font:12px/1.45 system-ui,sans-serif;max-width:260px'>"
        f"<b>{row['organisation_name']}</b><br>{row['address']}<br>"
        f"Register suburb: {row['suburb']}<br>"
        f"{row['nhsd_service_type']}<br>{where}{flag}"
        "</div>"
    )


def _legend_html(counts: dict[str, int], dots: dict[str, int]) -> str:
    """Fixed-position HTML legend for the Leaflet map (mirrors the PNG legends)."""
    lo, _, _, hi = INCOME_KNOTS
    d_dot = 2 * R_DOT_PT * HTML_RADIUS_SCALE
    d_ring = 2 * R_RING_PT * HTML_RADIUS_SCALE
    swatch = f"{d_ring + 8:.0f}px"

    def row(symbol: str, text: str) -> str:
        return (
            "<div style='display:flex;align-items:center;gap:8px;margin:3px 0'>"
            f"<span style='display:inline-flex;width:{swatch};justify-content:center'>"
            f"{symbol}</span><span>{text}</span></div>"
        )

    dot_sym = (
        f"<span style='width:{d_dot:.1f}px;height:{d_dot:.1f}px;border-radius:50%;"
        f"background:{_rgba_css(MARKER_COLOR, MARKER_ALPHA)};"
        f"border:1px solid {MARKER_EDGE}'></span>"
    )
    ring_sym = (
        f"<span style='width:{d_ring:.1f}px;height:{d_ring:.1f}px;border-radius:50%;"
        f"background:transparent;border:1.4px solid {MARKER_COLOR}'></span>"
    )

    def hatch_sym(color: str, deg: int) -> str:
        return (
            f"<span style='width:18px;height:12px;border:1px solid {color};"
            f"background:repeating-linear-gradient({deg}deg,{color} 0 1.5px,"
            f"{PALE_FIT_COLOR} 1.5px 5px)'></span>"
        )

    supply_block = row(
        dot_sym, f"General practice inside a mapped suburb ({dots['attributed']:,})"
    ) + row(
        ring_sym,
        f"General practice outside them, still competing ({dots['unattributed']:,})",
    )
    absent_block = (
        row(
            hatch_sym(BELOW_HATCH_COLOR, 45),
            f"Fit 0: income at or below ${lo:,.0f}, cannot absorb the gap "
            f"({counts['below']:,})",
        )
        + row(
            hatch_sym(ABOVE_HATCH_COLOR, -45),
            f"Fit 0: income at or above ${hi:,.0f}, buys full private care "
            f"({counts['above']:,})",
        )
        + row(
            "<span style='width:18px;height:12px;border:1px solid #9a9a9a;"
            f"background:{NO_INCOME_COLOR}'></span>",
            f"No income recorded, fit not computable ({counts['no_income']:,})",
        )
    )

    return f"""
    <div style="position:fixed;bottom:28px;left:12px;z-index:9999;
    font:12px/1.4 system-ui,-apple-system,sans-serif;background:rgba(255,255,255,0.95);
    padding:10px 12px;border-radius:5px;box-shadow:0 1px 4px rgba(0,0,0,0.25);
    max-width:min(360px,90vw);">
      <div style="font-weight:700;margin-bottom:4px">GP supply - one dot is one practice
      <br><span style="font-weight:400;color:#555">GA HealthDirect register, extract
      {REGISTER_EXTRACT}; dot size encodes nothing, because the register has no
      practitioner count</span></div>
      {supply_block}
      <div style="font-weight:700;margin:8px 0 4px">Pale is not one thing - three
      different absences</div>
      {absent_block}
      <div style="margin-top:8px;color:#444">
        A suburb with no dot has no register practice at its own address. That is
        <b>not</b> the same as no GP access: Croydon has 1 practice inside it and 27 within
        2 km. The register also misses roughly 10 to 12% of practices nationally, so a
        sparse patch is a floor on supply, not proof of absence.
      </div>
    </div>
    """


def save_interactive_html(
    gdf_4326: gpd.GeoDataFrame,
    reg_4326: gpd.GeoDataFrame,
    norm: Normalize,
    cmap,
    counts: dict[str, int],
    dots: dict[str, int],
    out_path: Path,
) -> None:
    """Leaflet mirror of the PNG: fit choropleth, two hatched zero causes, practice dots."""
    fields = [
        "suburb",
        "median_household_income_weekly",
        "mixed_billing_fit",
        "fit_verdict",
        "register_practices_inside",
        "population",
        "over_65_pct",
        "total_gp_display",
        "gp_data_status",
    ]
    aliases = [
        "Suburb",
        "Median household income ($/week)",
        "Mixed-billing fit (0 to 1)",
        "Why",
        "Register practices inside this suburb",
        "Population",
        "Aged 65+ (%)",
        "Total GPs (clinic scrape, context only)",
        "GP data status (scrape)",
    ]

    m = folium.Map(
        tiles="OpenStreetMap",
        location=[(VIEW_BBOX[1] + VIEW_BBOX[3]) / 2, (VIEW_BBOX[0] + VIEW_BBOX[2]) / 2],
        zoom_start=10,
    )

    folium.GeoJson(
        json.loads(gdf_4326[fields + ["geometry"]].to_json()),
        style_function=lambda feature: _fit_style(feature, norm, cmap),
        highlight_function=lambda _f: {"weight": 2.5, "color": "#222222"},
        tooltip=folium.GeoJsonTooltip(fields=fields, aliases=aliases, sticky=True, labels=True),
        popup=folium.GeoJsonPopup(fields=fields, aliases=aliases, max_width=480, localize=True),
    ).add_to(m)

    # The two fit-zero causes, hatched apart exactly as in the PNG, same angles and hues.
    lo, _, _, hi = INCOME_KNOTS
    inc = pd.to_numeric(gdf_4326["median_household_income_weekly"], errors="coerce")
    for mask, angle, color, label in (
        (inc.notna() & (inc <= lo), 45, BELOW_HATCH_COLOR, f"income <= ${lo:,.0f}"),
        (inc.notna() & (inc >= hi), -45, ABOVE_HATCH_COLOR, f"income >= ${hi:,.0f}"),
    ):
        subset = gdf_4326.loc[mask]
        if subset.empty:
            continue
        stripes = StripePattern(
            angle=angle,
            weight=1.6,
            space_weight=4.4,
            color=color,
            space_color="#ffffff",
            opacity=0.9,
            space_opacity=0.0,
        )
        stripes.add_to(m)
        folium.GeoJson(
            json.loads(subset[fields + ["geometry"]].to_json()),
            style_function=lambda _f, _s=stripes, _c=color: {
                "fillPattern": _s,
                "fillOpacity": 1.0,
                "color": _c,
                "weight": 0.4,
                "opacity": 0.6,
            },
            highlight_function=lambda _f: {"weight": 2.5, "color": "#222222"},
            tooltip=folium.GeoJsonTooltip(fields=fields, aliases=aliases, sticky=True, labels=True),
            popup=folium.GeoJsonPopup(fields=fields, aliases=aliases, max_width=480, localize=True),
        ).add_to(m)
        print(f"  HTML hatch layer: {len(subset):3d} suburbs, fit 0, {label}", flush=True)

    # Practice dots. Hollow rings first, so a filled dot is never buried under one.
    ring_r = R_RING_PT * HTML_RADIUS_SCALE
    dot_r = R_DOT_PT * HTML_RADIUS_SCALE
    for attributed in (False, True):
        subset = reg_4326.loc[reg_4326["attributed"] == attributed]
        for _, row in subset.iterrows():
            folium.CircleMarker(
                location=[float(row.geometry.y), float(row.geometry.x)],
                radius=dot_r if attributed else ring_r,
                color=MARKER_EDGE if attributed else MARKER_COLOR,
                weight=1.0 if attributed else 1.4,
                opacity=0.9,
                fill=bool(attributed),
                fill_color=MARKER_COLOR,
                fill_opacity=MARKER_ALPHA if attributed else 0.0,
                tooltip=folium.Tooltip(_practice_tooltip(row), sticky=True),
            ).add_to(m)

    m.fit_bounds([[VIEW_BBOX[1], VIEW_BBOX[0]], [VIEW_BBOX[3], VIEW_BBOX[2]]])

    gradient = [to_hex(cmap(float(x))) for x in np.linspace(0, 1, 11)]
    LinearColormap(
        colors=gradient,
        vmin=FIT_VMIN,
        vmax=FIT_VMAX,
        caption=(
            "Mixed-billing fit score: 1 = household income on the "
            f"${INCOME_KNOTS[1]:,.0f}-${INCOME_KNOTS[2]:,.0f}/wk plateau, "
            f"0 = at or beyond ${INCOME_KNOTS[0]:,.0f} / ${INCOME_KNOTS[3]:,.0f}"
        ),
    ).add_to(m)

    title_html = f"""
    <div style="position:fixed;top:12px;left:56px;z-index:9999;
    background:rgba(255,255,255,0.93);padding:8px 12px;border-radius:4px;
    box-shadow:0 1px 4px rgba(0,0,0,0.25);max-width:min(600px,74vw);
    font:600 15px/1.3 'Helvetica Neue',Arial,sans-serif;color:#111;">
    {TITLE}
    <div style="font-weight:400;font-size:12px;margin-top:3px;color:#444;">{SUBTITLE_1}</div>
    <div style="font-weight:400;font-size:12px;margin-top:2px;color:#444;">{SUBTITLE_2}</div>
    </div>
    """
    m.get_root().html.add_child(Element(title_html))
    m.get_root().html.add_child(Element(_legend_html(counts, dots)))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out_path))


def draw_png(
    gdf: gpd.GeoDataFrame,
    reg: gpd.GeoDataFrame,
    norm: Normalize,
    cmap,
    counts: dict[str, int],
    dots: dict[str, int],
    footnotes: list[str],
) -> None:
    """Static map: mixed-billing fit choropleth + one dot per register practice."""
    lo, _, _, hi = INCOME_KNOTS
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    # rc_context, not a global rcParams assignment: importing this module must not change
    # hatch rendering for whoever imported it.
    with mpl.rc_context({"hatch.linewidth": HATCH_LINEWIDTH}):
        fig, ax = plt.subplots(figsize=(12, 10.5))
        ax.set_facecolor(WATER_COLOR)

        gdf.plot(
            column="mixed_billing_fit",
            cmap=cmap,
            norm=norm,
            ax=ax,
            edgecolor="white",
            linewidth=0.15,
            legend=False,
            missing_kwds={"color": NO_INCOME_COLOR, "edgecolor": "white", "linewidth": 0.15},
        )

        # The two zero causes. Both are palest on the ramp, so without these two hatches a
        # $1,200 suburb and a $4,500 suburb are the same colour and the map says nothing
        # about which of two opposite commercial problems it is looking at.
        inc = pd.to_numeric(gdf["median_household_income_weekly"], errors="coerce")
        for mask, hatch, color in (
            (inc.notna() & (inc <= lo), BELOW_HATCH, BELOW_HATCH_COLOR),
            (inc.notna() & (inc >= hi), ABOVE_HATCH, ABOVE_HATCH_COLOR),
        ):
            subset = gdf.loc[mask]
            if not subset.empty:
                subset.plot(
                    ax=ax,
                    facecolor="none",
                    edgecolor=color,
                    hatch=hatch,
                    linewidth=0.25,
                    zorder=4,
                )

        attributed = reg["attributed"].to_numpy(dtype=bool)
        x = reg.geometry.x.to_numpy()
        y = reg.geometry.y.to_numpy()
        # Hollow rings under filled dots, so the attributed supply stays readable.
        ax.scatter(
            x[~attributed],
            y[~attributed],
            s=(2 * R_RING_PT) ** 2,
            facecolors="none",
            edgecolors=to_rgba(MARKER_COLOR, 0.9),
            linewidths=RING_EDGE_WIDTH,
            zorder=6,
        )
        ax.scatter(
            x[attributed],
            y[attributed],
            s=(2 * R_DOT_PT) ** 2,
            facecolors=to_rgba(MARKER_COLOR, MARKER_ALPHA),
            edgecolors=to_rgba(MARKER_EDGE, MARKER_EDGE_ALPHA),
            linewidths=MARKER_EDGE_WIDTH,
            zorder=7,
        )

        view_poly = gpd.GeoDataFrame(geometry=[box(*VIEW_BBOX)], crs="EPSG:4326").to_crs(PLOT_CRS)
        xmin, ymin, xmax, ymax = view_poly.total_bounds
        ax.set_xlim(xmin, xmax)
        ax.set_ylim(ymin, ymax)

        ax.set_title(mpl_text(TITLE), fontsize=15, pad=58)
        ax.text(
            0.5,
            1.012,
            mpl_text(
                "\n".join(
                    textwrap.fill(line, width=118) for line in (SUBTITLE_1, SUBTITLE_2)
                )
            ),
            transform=ax.transAxes,
            ha="center",
            va="bottom",
            fontsize=9.5,
            color="#444444",
            linespacing=1.4,
        )
        ax.set_xticks([])
        ax.set_yticks([])
        ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
        for spine in ax.spines.values():
            spine.set_visible(False)

        sm = ScalarMappable(norm=norm, cmap=cmap)
        sm.set_array([])
        cbar = fig.colorbar(sm, ax=ax, shrink=0.62, pad=0.015, ticks=[0, 0.25, 0.5, 0.75, 1.0])
        cbar.set_label("Mixed-billing fit score (D1 inverted U)", fontsize=10)
        cbar.ax.set_yticklabels(["0  outside", "0.25", "0.50", "0.75", "1  best fit"])
        cbar.outline.set_visible(False)

        supply_handles = [
            Line2D(
                [0], [0],
                linestyle="none",
                marker="o",
                markersize=2 * R_DOT_PT,
                markerfacecolor=to_rgba(MARKER_COLOR, MARKER_ALPHA),
                markeredgecolor=MARKER_EDGE,
                markeredgewidth=MARKER_EDGE_WIDTH,
                label=mpl_text(f"Inside a mapped suburb  ({dots['attributed']:,})"),
            ),
            Line2D(
                [0], [0],
                linestyle="none",
                marker="o",
                markerfacecolor="none",
                markeredgecolor=MARKER_COLOR,
                markersize=2 * R_RING_PT,
                markeredgewidth=RING_EDGE_WIDTH,
                label=mpl_text(f"Outside them, still competing  ({dots['unattributed']:,})"),
            ),
        ]
        absent_handles = [
            mpatches.Patch(
                facecolor=PALE_FIT_COLOR,
                edgecolor=BELOW_HATCH_COLOR,
                hatch=BELOW_HATCH,
                linewidth=0.4,
                label=mpl_text(
                    f"Fit 0: income at or below ${lo:,.0f}, cannot absorb the gap "
                    f"({counts['below']})"
                ),
            ),
            mpatches.Patch(
                facecolor=PALE_FIT_COLOR,
                edgecolor=ABOVE_HATCH_COLOR,
                hatch=ABOVE_HATCH,
                linewidth=0.4,
                label=mpl_text(
                    f"Fit 0: income at or above ${hi:,.0f}, buys full private care "
                    f"({counts['above']})"
                ),
            ),
            mpatches.Patch(
                facecolor=NO_INCOME_COLOR,
                edgecolor="#9a9a9a",
                linewidth=0.4,
                label=f"No income recorded, fit not computable ({counts['no_income']})",
            ),
        ]

        # Both legends sit BELOW the map in figure coordinates. The map axes is narrower than
        # the figure (equal aspect), so anchoring to the axes made the two boxes overlap;
        # anchoring to the figure after a draw pass gives them the full width to share.
        fig.canvas.draw()
        legend_top = ax.get_position().y0 - 0.012
        supply_legend = fig.legend(
            handles=supply_handles,
            title="One dot = one general practice (in view)",
            loc="upper left",
            bbox_to_anchor=(0.03, legend_top),
            bbox_transform=fig.transFigure,
            ncol=1,
            frameon=True,
            fontsize=9.5,
            title_fontsize=10,
            handletextpad=0.9,
            columnspacing=1.2,
            borderpad=0.8,
            labelspacing=0.7,
        )
        supply_legend._legend_box.align = "left"
        supply_legend.get_frame().set_edgecolor("#c8ccd2")

        absent_legend = fig.legend(
            handles=absent_handles,
            title="Pale is not one thing - three different absences",
            loc="upper right",
            bbox_to_anchor=(0.985, legend_top),
            bbox_transform=fig.transFigure,
            ncol=1,
            frameon=True,
            fontsize=9.5,
            title_fontsize=10,
            handletextpad=0.9,
            borderpad=0.8,
            labelspacing=0.6,
        )
        absent_legend._legend_box.align = "left"
        absent_legend.get_frame().set_edgecolor("#c8ccd2")

        # Centre the two boxes as one block: pinned to opposite figure edges they left a wide
        # empty gap between them, which read as an unfinished figure.
        fig.canvas.draw()
        fig_w = fig.bbox.width
        w_supply = supply_legend.get_window_extent().width / fig_w
        w_absent = absent_legend.get_window_extent().width / fig_w
        gap = 0.035
        left = max(0.02, (1.0 - (w_supply + w_absent + gap)) / 2.0)
        supply_legend.set_bbox_to_anchor((left, legend_top), transform=fig.transFigure)
        absent_legend.set_bbox_to_anchor(
            (left + w_supply + gap + w_absent, legend_top), transform=fig.transFigure
        )

        # Footnotes below both legend boxes. Hard-wrapped, because matplotlib will not wrap
        # them and bbox_inches="tight" grows the canvas to fit the longest line, which
        # silently widens the figure and shrinks the map.
        fig.canvas.draw()
        fig_h = fig.bbox.height
        legend_h = (
            max(
                supply_legend.get_window_extent().height,
                absent_legend.get_window_extent().height,
            )
            / fig_h
        )
        fig.text(
            left,
            legend_top - legend_h - 0.016,
            mpl_text(
                "\n".join(
                    textwrap.fill(note, width=FOOTNOTE_WRAP_CHARS, subsequent_indent="    ")
                    for note in footnotes
                )
            ),
            ha="left",
            va="top",
            fontsize=8.5,
            color="#3a3a3a",
            linespacing=1.5,
        )

        fig.savefig(OUT_PNG, dpi=160, bbox_inches="tight", facecolor="white")
        plt.close(fig)


def main() -> None:
    if not DATA_PATH.exists():
        print(f"Missing {DATA_PATH}", file=sys.stderr)
        sys.exit(1)

    df = pd.read_csv(DATA_PATH)
    require_status_column(df, DATA_PATH)

    df["total_gp"] = pd.to_numeric(df["total_gp"], errors="coerce").fillna(0).clip(lower=0)
    df["clinics_listed"] = pd.to_numeric(df["clinics_listed"], errors="coerce").fillna(0)
    df["total_gp_is_floor"] = (
        df["total_gp_is_floor"].astype(str).str.strip().str.lower().isin({"true", "1", "yes"})
    )
    inc_col = "median_household_income_weekly"
    df[inc_col] = pd.to_numeric(df[inc_col], errors="coerce")

    # The choropleth. Imported from site_score, so this map and the ranking share one D1.
    df["mixed_billing_fit"] = fit_scores(df[inc_col])
    df["fit_verdict"] = [
        fit_verdict(float(i), float(f))
        for i, f in zip(df[inc_col].to_numpy(dtype=float), df["mixed_billing_fit"].to_numpy())
    ]
    df["total_gp_display"] = [
        format_total_gp(gp, floor, status, clinics)
        for gp, floor, status, clinics in zip(
            df["total_gp"], df["total_gp_is_floor"], df["gp_data_status"], df["clinics_listed"]
        )
    ]

    print(f"Loaded {len(df)} suburbs from {DATA_PATH.name}; gp_data_status:")
    print(status_summary(df), flush=True)
    lo, plo, phi, hi = INCOME_KNOTS
    print(
        f"D1 mixed-billing fit imported from site_score: plateau ${plo:,.0f}-${phi:,.0f}/wk, "
        f"zero at or beyond ${lo:,.0f} and ${hi:,.0f}. Choropleth is this score, not income.",
        flush=True,
    )

    polys = load_or_fetch_suburb_polygons()
    df["suburb_key"] = df["suburb"].astype(str).str.strip().str.lower()
    merged = df.merge(polys, on="suburb_key", how="left")
    no_poly = merged["geometry"].isna()
    if no_poly.any():
        print(
            f"Excluded {int(no_poly.sum())} suburbs with no OSM polygon match "
            "(cannot be drawn at all; their practices are still drawn, as hollow rings).",
            flush=True,
        )

    reg = load_register(polys)
    n_non_gp = int(reg["non_gp_like"].sum())

    # The 30 suburbs with no polygon are drawn as nothing, so a tight cluster of hollow
    # rings sitting on the water colour is a missing BOUNDARY, not open water. Four of them
    # are major centres, so name the worst offenders rather than leave a reader guessing.
    no_poly_names = set(merged.loc[no_poly, "suburb"].astype(str))
    ring_gaps = (
        reg.loc[~reg["attributed"] & reg["suburb"].astype(str).isin(no_poly_names), "suburb"]
        .value_counts()
        .head(4)
    )
    gap_phrase = ", ".join(f"{name} ({int(n)})" for name, n in ring_gaps.items())
    print(f"Largest hollow-ring clusters in no-polygon suburbs: {gap_phrase}", flush=True)
    print(
        f"Register: {len(reg):,} NSW general practice rows, {int(reg['attributed'].sum()):,} "
        f"inside a mapped suburb polygon, {int((~reg['attributed']).sum()):,} outside.",
        flush=True,
    )
    print(
        f"  flagged by name as not conventional general practice: {n_non_gp} "
        "(drawn and labelled in the tooltip, never dropped).",
        flush=True,
    )

    gdf_4326 = gpd.GeoDataFrame(merged.loc[~no_poly].copy(), geometry="geometry", crs="EPSG:4326")
    # Register practices inside each suburb, as tooltip context only. This replaces the
    # scraped head count, and is deliberately NOT turned into a colour or a marker size: it
    # is the inside-polygon count whose misuse this rebuild exists to undo.
    inside = (
        reg.loc[reg["attributed"], "suburb_key"]
        .value_counts()
        .rename("register_practices_inside")
    )
    gdf_4326 = gdf_4326.merge(inside, left_on="suburb_key", right_index=True, how="left")
    gdf_4326["register_practices_inside"] = (
        gdf_4326["register_practices_inside"].fillna(0).astype(int)
    )
    n_zero_inside = int((gdf_4326["register_practices_inside"] == 0).sum())
    gdf = gdf_4326.to_crs(PLOT_CRS)

    # The PNG is cropped to the metro view, so its legend counts what the reader can see.
    # The Leaflet map pans and zooms, so its legend counts everything drawn on it.
    view_poly = gpd.GeoDataFrame(geometry=[box(*VIEW_BBOX)], crs="EPSG:4326").to_crs(PLOT_CRS)
    in_view = gdf.geometry.intersects(view_poly.geometry.iloc[0]).to_numpy()
    counts_view = fit_counts(gdf.loc[in_view])
    counts_all = fit_counts(gdf)

    reg_view = reg.loc[in_bbox(reg, VIEW_BBOX)]
    reg_html = reg.loc[in_bbox(reg, OSM_BBOX)]
    dots_view = {
        "attributed": int(reg_view["attributed"].sum()),
        "unattributed": int((~reg_view["attributed"]).sum()),
    }
    dots_html = {
        "attributed": int(reg_html["attributed"].sum()),
        "unattributed": int((~reg_html["attributed"]).sum()),
    }

    print(
        f"Mapped {len(gdf)} suburbs; {int(in_view.sum())} intersect the metro view "
        f"({int((~in_view).sum())} mapped but outside the PNG crop, still on the HTML map).",
        flush=True,
    )
    rows: list[tuple[str, int, int]] = [
        ("choropleth, fit 1.00 (on the plateau)", counts_view["best"], counts_all["best"]),
        ("choropleth, fit between 0 and 1", counts_view["partial"], counts_all["partial"]),
        (f"hatch, fit 0: income <= ${lo:,.0f}", counts_view["below"], counts_all["below"]),
        (f"hatch, fit 0: income >= ${hi:,.0f}", counts_view["above"], counts_all["above"]),
        ("flat grey, no income recorded", counts_view["no_income"], counts_all["no_income"]),
    ]
    print(f"  {'suburb symbol':<40s}{'in view':>8s}{'mapped':>9s}", flush=True)
    for desc, n_v, n_a in rows:
        print(f"  {desc:<40s}{n_v:8d}{n_a:9d}", flush=True)
    print(f"  {'practice dot':<40s}{'in view':>8s}{'on HTML':>9s}", flush=True)
    print(
        f"  {'filled, inside a mapped suburb':<40s}"
        f"{dots_view['attributed']:8d}{dots_html['attributed']:9d}",
        flush=True,
    )
    print(
        f"  {'hollow ring, outside them':<40s}"
        f"{dots_view['unattributed']:8d}{dots_html['unattributed']:9d}",
        flush=True,
    )
    print(f"  {'total practice dots':<40s}{len(reg_view):8d}{len(reg_html):9d}", flush=True)
    print(
        f"{n_zero_inside} of {len(gdf)} mapped suburbs contain no register practice at all. "
        "That is an absence of an address, not an absence of access.",
        flush=True,
    )

    # The reversal the choropleth is here to make visible: the suburbs a monotonic income
    # ramp painted darkest are the ones the inverted U scores zero.
    richest = gdf_4326.nlargest(5, inc_col)[["suburb", inc_col, "mixed_billing_fit"]]
    print("Richest 5 mapped suburbs, and what D1 makes of them:", flush=True)
    for _, r in richest.iterrows():
        print(
            f"  {r['suburb']!s:22s} ${float(r[inc_col]):7,.0f}/wk  fit "
            f"{float(r['mixed_billing_fit']):.3f}",
            flush=True,
        )

    footnotes = [
        (f"Choropleth is the D1 mixed-billing fit score imported from site_score.py, not raw "
        f"income: full score on the ${plo:,.0f}-${phi:,.0f}/wk plateau, tapering to 0 at "
        f"${lo:,.0f} and ${hi:,.0f}. Mixed billing needs households able to absorb roughly a "
        "$45 gap per visit (about $43 rebate against about $90 private) and not already "
        "buying full private care, so fit is not monotonic in income."),
        (f"That is why there are two hatches: {counts_view['below']} suburbs in view score 0 "
        f"because income is at or below ${lo:,.0f}, and {counts_view['above']} because it is "
        f"at or above ${hi:,.0f}. Both sit at the palest end of one ramp and they are "
        "opposite commercial problems. The previous version of this map coloured the second "
        "group darkest of all."),
        (f"Dots are the {REGISTER_NAME} register ({REGISTER_LICENCE}), NSW extract "
        f"{REGISTER_EXTRACT}, filtered to nhsd_service_type = 'General practice service': "
        f"{len(reg):,} of the {REGISTER_LAYER_NSW_ROWS:,} NSW rows in that layer, the other "
        "393 being nurse-led clinics, maternal and child health, skin cancer clinics and "
        "similar. Dot size encodes nothing, because the register has no practitioner-count "
        "field, so there is no GP head count or FTE anywhere on this map."),
        (f"{n_zero_inside} of {len(gdf)} mapped suburbs contain no register practice. That is "
        "an absence of an address inside a boundary, not an absence of access: Croydon has 1 "
        "practice inside it and 27 within 2 km. Hollow rings are practices outside the 680 "
        "study suburbs or inside one of the 30 with no polygon; they compete for the same "
        "patients but belong to no suburb here."),
        ("A tight knot of hollow rings on the water colour is a MISSING BOUNDARY, not open "
        f"water: the 30 suburbs with no polygon are drawn as nothing, and the biggest are "
        f"{gap_phrase}. "
        f"Also worth knowing before reading the dark green as a ranking: {counts_all['best']} "
        f"of the {len(gdf)} mapped suburbs score exactly 1.0, so inside a shortlist this "
        "criterion filters rather than discriminates."),
        ("Register limits, stated: it misses roughly 10 to 12% of practices nationally, and "
        f"{n_non_gp} of the {len(reg):,} NSW rows carry names indicating they are not "
        "conventional general practice (Headspace, Aboriginal Medical Services, refugee, "
        "travel and university health). They are drawn and flagged in the interactive "
        "tooltip rather than removed, because some do bulk bill general consultations."),
    ]

    norm = Normalize(vmin=FIT_VMIN, vmax=FIT_VMAX, clip=False)
    cmap = FIT_CMAP

    draw_png(gdf, reg_view.to_crs(PLOT_CRS), norm, cmap, counts_view, dots_view, footnotes)
    print(f"Saved {OUT_PNG.resolve()}", flush=True)

    save_interactive_html(gdf_4326, reg_html, norm, cmap, counts_all, dots_html, OUT_HTML)
    print(f"Saved {OUT_HTML.resolve()}", flush=True)


if __name__ == "__main__":
    main()
