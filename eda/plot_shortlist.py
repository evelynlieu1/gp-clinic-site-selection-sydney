"""
Two summary figures for the top of the README.

1. eda/plots/shortlist_rerun_results.png
   For the strongest suburbs, how often each one came first, and how often it made the
   top 10, across the 400 joint re-runs in eda/joint_sensitivity.csv (every weight and
   setting varied at the same time).

2. eda/plots/shortlist_map.png
   Every scored suburb in Greater Sydney shaded by its site score, with the four
   shortlisted suburbs outlined and labelled.

Both figures are rebuilt from the CSVs, so re-running site_score.py and then this
script keeps them in line with the analysis. The map needs the cached suburb polygons
in eda/geo_cache (created by plot_combined_pressure_density.py).

Run: python eda/plot_shortlist.py
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.colors import LinearSegmentedColormap

ROOT = Path(__file__).resolve().parent.parent
JOINT = ROOT / "eda" / "joint_sensitivity.csv"
SCORES = ROOT / "eda" / "site_scores_v2.csv"
POLYS = ROOT / "eda" / "geo_cache" / "osm_suburb_polygons_greater_sydney.gpkg"
PLOTS = ROOT / "eda" / "plots"

# The shortlist stated in the README. Caringbah South also scores well, but it sits in
# the same GP catchment as Cronulla, so it is the same market rather than a separate option.
SHORTLIST = ["Cronulla", "Avalon Beach", "Oran Park", "Glenmore Park"]
SAME_MARKET_NOTE = {"Caringbah South": "same GP catchment as Cronulla"}

TEAL_DARK = "#0F6E56"
TEAL_LIGHT = "#7FCBB0"
GREY_DARK = "#8C8A82"
GREY_LIGHT = "#D3D1C7"
INK = "#2C2C2A"

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "axes.edgecolor": "#B4B2A9",
    "axes.labelcolor": INK,
    "xtick.color": INK,
    "ytick.color": INK,
})


def rerun_chart() -> None:
    joint = pd.read_csv(JOINT)
    joint = joint.loc[joint["draws"] == joint["draws"].max()]
    n_draws = int(joint["draws"].max())

    top = joint.nlargest(8, "p_top_n").copy()
    top["is_short"] = top["suburb"].isin(SHORTLIST)
    top = top.sort_values(["is_short", "p_rank_1", "p_top_n"], ascending=[True, True, True])

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 5.4), sharey=True,
                                   gridspec_kw={"wspace": 0.08})
    y = range(len(top))
    labels = [
        f"{s}\n({SAME_MARKET_NOTE[s]})" if s in SAME_MARKET_NOTE else s
        for s in top["suburb"]
    ]

    for ax, col, title, dark, light in [
        (ax1, "p_rank_1", "Ranked #1", TEAL_DARK, GREY_DARK),
        (ax2, "p_top_n", "Made the top 10", TEAL_LIGHT, GREY_LIGHT),
    ]:
        colours = [dark if s else light for s in top["is_short"]]
        vals = top[col] * 100
        ax.barh(y, vals, color=colours, height=0.62)
        for yi, v in zip(y, vals):
            label = "<1%" if 0 < v < 1 else f"{v:.0f}%"
            ax.text(v + 1.5, yi, label, va="center", fontsize=10, color=INK)
        ax.set_xlim(0, 112)
        ax.set_title(title, fontsize=12, color=INK, loc="left", pad=8)
        ax.set_xticks([0, 25, 50, 75, 100])
        ax.set_xticklabels(["0%", "25%", "50%", "75%", "100%"], fontsize=9)
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="x", color="#ECEAE3", linewidth=0.8)
        ax.set_axisbelow(True)

    ax1.set_yticks(list(y))
    ax1.set_yticklabels(labels, fontsize=10.5)
    for tick, short in zip(ax1.get_yticklabels(), top["is_short"]):
        tick.set_fontweight("bold" if short else "normal")

    fig.suptitle("Which suburbs keep coming out on top?", x=0.02, ha="left",
                 fontsize=15, fontweight="bold", color=INK, y=1.02)
    fig.text(0.02, 0.955,
             f"Share of {n_draws} re-runs of the model, with every weight and setting varied "
             "at the same time. Green = the four-suburb shortlist.",
             fontsize=10, color="#5F5E5A")
    out = PLOTS / "shortlist_rerun_results.png"
    fig.savefig(out, dpi=170, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Wrote {out}")


def shortlist_map() -> None:
    scores = pd.read_csv(SCORES)
    joint = pd.read_csv(JOINT)[["suburb", "p_rank_1"]]
    scores = scores.merge(joint, on="suburb", how="left")
    scores["suburb_key"] = scores["suburb"].astype(str).str.strip().str.lower()
    polys = gpd.read_file(POLYS)
    gdf = polys.merge(scores, on="suburb_key", how="inner").to_crs("EPSG:7856")

    eligible = gdf.loc[gdf["eligible"] == True]
    other = gdf.loc[gdf["eligible"] != True]
    short = gdf.loc[gdf["suburb"].isin(SHORTLIST)]

    cmap = LinearSegmentedColormap.from_list(
        "score", ["#F1EFE8", "#C9E8DC", "#5DCAA5", "#1D9E75", "#085041"])

    fig, ax = plt.subplots(figsize=(10, 10))
    other.plot(ax=ax, color="#E6E4DD", edgecolor="white", linewidth=0.3)
    eligible.plot(ax=ax, column="site_score", cmap=cmap, edgecolor="white", linewidth=0.3)
    short.plot(ax=ax, facecolor="none", edgecolor="#D85A30", linewidth=2.2)

    # Label offsets in metres (x, y), chosen so labels sit in open space.
    offsets = {
        "Cronulla": (9000, -6000),
        "Avalon Beach": (-16000, 5000),
        "Oran Park": (-17000, -7000),
        "Glenmore Park": (-15000, 8000),
    }
    for _, row in short.iterrows():
        pt = row.geometry.representative_point()
        dx, dy = offsets.get(row["suburb"], (8000, 8000))
        ax.annotate(
            f"{row['suburb']}\n#1 in {row['p_rank_1'] * 100:.0f}% of re-runs",
            xy=(pt.x, pt.y), xytext=(pt.x + dx, pt.y + dy),
            fontsize=11, fontweight="bold", color=INK, ha="center",
            arrowprops=dict(arrowstyle="-", color="#D85A30", linewidth=1.2),
            bbox=dict(boxstyle="round,pad=0.3", facecolor="white", edgecolor="#D85A30",
                      linewidth=1),
        )

    ax.set_axis_off()
    sm = plt.cm.ScalarMappable(cmap=cmap, norm=plt.Normalize(
        eligible["site_score"].min(), eligible["site_score"].max()))
    cax = fig.add_axes([0.52, 0.12, 0.28, 0.018])
    cb = fig.colorbar(sm, cax=cax, orientation="horizontal")
    cb.set_label("Site score (higher = stronger case for a clinic)", fontsize=9, color=INK)
    cb.ax.tick_params(labelsize=8)
    cb.outline.set_visible(False)

    ax.set_title("Where the shortlisted suburbs are", loc="left", fontsize=15,
                 fontweight="bold", color=INK, pad=34)
    ax.text(0, 1.01,
            "Eligible suburbs shaded by site score. Grey = excluded (under 1,000 people, too little\n"
            "demand in the GP catchment, or already inside an existing Nuvo clinic's catchment).",
            transform=ax.transAxes, fontsize=9.5, color="#5F5E5A")
    out = PLOTS / "shortlist_map.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Wrote {out}")


if __name__ == "__main__":
    rerun_chart()
    shortlist_map()
