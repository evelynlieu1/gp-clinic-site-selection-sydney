"""
Rank-stability figure for the README.

Reads ``eda/sensitivity_analysis.csv`` and plots, for the strongest candidates, the
share of perturbation runs in which the suburb stays in the top 10. This is the one
figure that carries the project's central claim: the recommendation is the suburb that
survives the sweep, not the suburb that wins the point estimate.

Regenerates from the CSV, so re-running ``site_score.py`` then this script keeps the
README figure honest. Run: ``python eda/plot_rank_stability.py``
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SENS = ROOT / "eda" / "sensitivity_analysis.csv"
SCORES = ROOT / "eda" / "site_scores_v2.csv"
OUT = ROOT / "eda" / "plots" / "readme_rank_stability.png"

#: Runs that DELETE a criterion outright. A suburb failing only these has not been shown
#: to be fragile; the study has been changed into a different study.
DEGENERATE = {
    "weight access_gap to 0",
    "weight demand_scale to 0",
    "weight mixed_billing_fit to 0",
    "weight operational to 0",
}


def main() -> None:
    sens = pd.read_csv(SENS)
    scores = pd.read_csv(SCORES)
    base = scores.loc[scores["eligible"] == True].nsmallest(14, "rank")

    runs = sens.loc[sens["run"] != "baseline"]
    n_all = runs["run"].nunique()
    nd = runs.loc[~runs["run"].isin(DEGENERATE)]
    n_nd = nd["run"].nunique()

    rows = []
    for name in base["suburb"]:
        rows.append({
            "suburb": name,
            "rank": int(base.loc[base["suburb"] == name, "rank"].iloc[0]),
            "all": runs.loc[runs["suburb"] == name, "in_top_n"].eq(True).sum() / n_all,
            "nondegen": nd.loc[nd["suburb"] == name, "in_top_n"].eq(True).sum() / n_nd,
        })
    df = pd.DataFrame(rows).sort_values("nondegen")

    fig, ax = plt.subplots(figsize=(9, 6.2))
    y = range(len(df))
    ax.barh(y, df["nondegen"], color="#1D9E75", height=0.62,
            label=f"{n_nd} non-degenerate runs")
    ax.barh(y, df["all"], color="#0F6E56", height=0.26,
            label=f"all {n_all} runs (incl. 4 that delete a criterion)")
    ax.set_yticks(list(y))
    ax.set_yticklabels([f"{r.suburb}  (rank {r.rank})" for r in df.itertuples()], fontsize=9)
    ax.set_xlim(0, 1.0)
    ax.set_xlabel("share of perturbation runs keeping the suburb in the top 10")
    ax.set_title("Rank stability under 31 perturbations\n"
                 "the recommendation is what survives the sweep, not what wins the point estimate",
                 fontsize=11, loc="left")
    ax.axvline(1.0, color="#5F5E5A", lw=0.8, ls="--")
    ax.legend(loc="lower right", fontsize=8, frameon=False)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=150)
    print(f"wrote {OUT}")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
