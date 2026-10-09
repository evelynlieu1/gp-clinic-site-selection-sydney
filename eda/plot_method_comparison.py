"""
Slope chart: how each top suburb's rank moves between the two floating-catchment
formulations.

The table in the README says Cronulla goes 1 to 3 and Avalon Beach 4 to 1. A table makes
that look like noise. A slope chart makes it obvious that the ordering is unstable while
the membership is not: eight of ten suburbs are in both top tens, but the lines cross.

Reads eda/sensitivity_analysis.csv, which must already contain the "3SFCA demand
competition" run. Writes eda/plots/method_comparison.png.

Run: python eda/plot_method_comparison.py
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SENS = ROOT / "eda" / "sensitivity_analysis.csv"
OUT = ROOT / "eda" / "plots" / "method_comparison.png"

RUN_3SFCA = "3SFCA demand competition"
TOP_N = 10


def main() -> None:
    s = pd.read_csv(SENS)
    if RUN_3SFCA not in set(s["run"]):
        raise SystemExit(f"{SENS.name} has no {RUN_3SFCA!r} run; re-run site_score.py")

    base = s.loc[s["run"] == "baseline"].dropna(subset=["rank"]).set_index("suburb")["rank"]
    alt = s.loc[s["run"] == RUN_3SFCA].dropna(subset=["rank"]).set_index("suburb")["rank"]

    names = sorted(set(base.nsmallest(TOP_N).index) | set(alt.nsmallest(TOP_N).index))
    rows = [{"suburb": n, "a": base.get(n), "b": alt.get(n)} for n in names]
    df = pd.DataFrame(rows).dropna().sort_values("a")

    fig, ax = plt.subplots(figsize=(8.4, 6.4))
    for r in df.itertuples():
        # A suburb that leaves the top ten under the other method is the interesting case,
        # so colour by whether it stays in both rather than by how far it moved.
        both = r.a <= TOP_N and r.b <= TOP_N
        colour = "#1D9E75" if both else "#D85A30"
        ax.plot([0, 1], [r.a, r.b], "-o", color=colour, lw=1.8, ms=6, zorder=2)
        ax.annotate(f"{r.suburb}  {int(r.a)}", (0, r.a), xytext=(-10, 0),
                    textcoords="offset points", ha="right", va="center", fontsize=9)
        ax.annotate(f"{int(r.b)}  {r.suburb}", (1, r.b), xytext=(10, 0),
                    textcoords="offset points", ha="left", va="center", fontsize=9)

    ax.axhline(TOP_N + 0.5, color="#888780", lw=0.8, ls="--", zorder=1)
    ax.annotate("top 10 cut", (0.5, TOP_N + 0.5), xytext=(0, 6),
                textcoords="offset points", ha="center", fontsize=8, color="#5F5E5A")

    ax.set_xlim(-0.42, 1.42)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["2SFCA\n(shipped)", "3SFCA\n(demand competition)"], fontsize=10)
    ax.invert_yaxis()
    ax.set_ylabel("rank")
    ax.set_title("Rank 1 is not formulation-independent\n"
                 "green stays in both top tens, orange does not",
                 fontsize=11, loc="left")
    for side in ("top", "right", "bottom"):
        ax.spines[side].set_visible(False)
    ax.tick_params(axis="x", length=0)
    fig.tight_layout()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=150)
    print(f"wrote {OUT}")
    print(df.rename(columns={"a": "2sfca", "b": "3sfca"}).to_string(index=False))


if __name__ == "__main__":
    main()
