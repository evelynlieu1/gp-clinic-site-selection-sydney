"""
Recompute the area column from ABS SAL 2021 polygons.

The shipped column disagrees with the boundary file by more than rounding. Measured
across all 680 suburbs: 519 within 5 percent, 70 out by over 20 percent, 18 by more
than double. Sydney reads 26.4694 km2 against 2.9396 actual. Twelve rows share a value
byte-identically with another suburb, which is a fill error rather than noise.

area feeds nothing in site_score.py - D6 dropped it - but
eda/plot_combined_pressure_density.py divides by it for the senior-density axis, so
the map has to be regenerated after this runs, not before.

Areas are polygon areas in EPSG:3577 via dpa_status.load_suburbs, so the geometry join
is the one the rest of the pipeline uses. Overwrites area in suburbs_final.csv and
eda_data.csv; git holds the previous values.

Run: python fix_suburb_area.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

import dpa_status

ROOT = Path(__file__).resolve().parent
TARGETS = (
    ROOT / "scraped_data" / "suburbs_final.csv",
    ROOT / "scraped_data" / "eda_data.csv",
)

#: The ORIGIN of the bad values, and the reason repairing only the CSVs does not hold.
#: Cell 8 of pre-processing.ipynb reads this file and writes ``r["area"] = areas.get(s)``,
#: so ``rebuild_suburbs_final.py`` - the first command in the documented run order -
#: restores every corrupted value and silently undoes this script. Repairing the JSON too
#: makes the fix durable: after one run, rebuilds produce correct areas on their own.
AREAS_JSON = ROOT / "scraped_data" / "suburb_areas.json"

#: Ratio outside which a suburb's shipped area is reported as materially wrong. Chosen so
#: the report separates rounding from corruption: MEASURED, 519 of 680 sit inside it.
TOLERANCE = 0.05


def true_areas(suburbs: pd.DataFrame) -> pd.DataFrame:
    """
    Square-kilometre polygon area per suburb from the ABS SAL 2021 join.

    Returns a two-column frame, ``suburb`` and ``area_sal_km2``. Raises if the join does
    not cover every suburb, because a partial fill is how the column got corrupted in the
    first place.
    """
    geo = dpa_status.load_suburbs(suburbs[["suburb"]])
    out = pd.DataFrame({
        "suburb": geo["suburb"].to_numpy(),
        "area_sal_km2": (geo.geometry.area / 1e6).to_numpy(),
    })
    missing = set(suburbs["suburb"]) - set(out["suburb"])
    if missing:
        raise AssertionError(f"{len(missing)} suburbs have no SAL geometry: {sorted(missing)[:5]}")
    if not (out["area_sal_km2"] > 0).all():
        raise AssertionError("SAL join produced a non-positive area")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()

    base = pd.read_csv(TARGETS[0])
    truth = true_areas(base)
    merged = base[["suburb", "area"]].merge(truth, on="suburb", how="left", validate="one_to_one")
    merged["ratio"] = merged["area"] / merged["area_sal_km2"]

    inside = merged["ratio"].between(1 - TOLERANCE, 1 + TOLERANCE).sum()
    off20 = (~merged["ratio"].between(0.8, 1.2)).sum()
    off2x = (~merged["ratio"].between(0.5, 2.0)).sum()
    dupes = base["area"].duplicated(keep=False) & base["area"].notna()

    print(f"{len(merged)} suburbs joined to SAL 2021 geometry")
    print(f"  within {TOLERANCE:.0%} of the polygon area : {inside}")
    print(f"  off by more than 20 percent          : {off20}")
    print(f"  off by more than 2x                  : {off2x}")
    print(f"  sharing a value with another suburb  : {int(dupes.sum())}")
    print()
    worst = merged.assign(err=(merged["ratio"] - 1).abs()).nlargest(8, "err")
    print(f"  {'suburb':22s} {'shipped':>10s} {'SAL':>10s} {'ratio':>7s}")
    for r in worst.itertuples():
        print(f"  {r.suburb:22s} {r.area:10.4f} {r.area_sal_km2:10.4f} {r.ratio:7.2f}")

    if args.dry_run:
        print("\ndry run, nothing written")
        return

    lookup = dict(zip(truth["suburb"], truth["area_sal_km2"]))
    for path in TARGETS:
        if not path.exists():
            print(f"\nskipped {path.name}, not present")
            continue
        frame = pd.read_csv(path)
        if "area" not in frame.columns:
            print(f"\nskipped {path.name}, no area column")
            continue
        before = frame["area"].copy()
        frame["area"] = frame["suburb"].map(lookup).round(4)
        if frame["area"].isna().any():
            raise AssertionError(f"{path.name}: {int(frame['area'].isna().sum())} suburbs unmapped")
        frame.to_csv(path, index=False)
        changed = int((before.round(4) != frame["area"]).sum())
        print(f"\nwrote {path.name}: {changed} of {len(frame)} area values changed")

    if AREAS_JSON.exists():
        existing = json.loads(AREAS_JSON.read_text(encoding="utf-8"))
        repaired = {k: round(lookup[k], 4) for k in existing if k in lookup}
        kept = {k: v for k, v in existing.items() if k not in lookup}
        moved = sum(1 for k, v in existing.items()
                    if k in lookup and round(float(v), 4) != round(lookup[k], 4))
        AREAS_JSON.write_text(
            json.dumps({**repaired, **kept}, indent=2, sort_keys=True), encoding="utf-8"
        )
        print(f"\nwrote {AREAS_JSON.name}: {moved} of {len(existing)} values changed "
              f"({len(kept)} not in the SAL join, left alone)")
        print("  this is the file cell 8 reads, so rebuilds no longer reintroduce the "
              "corruption")


if __name__ == "__main__":
    main()
