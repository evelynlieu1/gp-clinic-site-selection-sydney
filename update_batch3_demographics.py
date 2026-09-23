"""Apply ABS 2021 QuickStats batch 3 to scraped_data JSON (pop, over_65, income)."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "scraped_data"

# From user transcript _batch3_raw.txt - % 65+ = sum(65-69..85+) / headline People
BATCH3 = {
    "Long Point": {
        "population": 173,
        "over_65_pct": round((4 + 19 + 12 + 0 + 0) / 173 * 100, 2),
        "income": {
            "median_household_income_weekly": 1775,
            "median_family_income_weekly": 1968,
            "median_personal_income_weekly": 900,
        },
    },
    "Lilli Pilli": {
        "population": 1405,
        "over_65_pct": round((88 + 92 + 39 + 23 + 27) / 1405 * 100, 2),
        "income": {
            "median_household_income_weekly": 3625,
            "median_family_income_weekly": 3690,
            "median_personal_income_weekly": 1186,
        },
    },
    "Punchbowl": {
        "population": 21384,
        "over_65_pct": round((862 + 672 + 483 + 391 + 334) / 21384 * 100, 2),
        "income": {
            "median_household_income_weekly": 1389,
            "median_family_income_weekly": 1483,
            "median_personal_income_weekly": 504,
        },
    },
    "Silverwater": {
        "population": 3600,
        "over_65_pct": round((90 + 55 + 38 + 16 + 13) / 3600 * 100, 2),
        "income": {
            "median_household_income_weekly": 1875,
            "median_family_income_weekly": 2055,
            "median_personal_income_weekly": 857,
        },
    },
    "St Clair": {
        "population": 19942,
        "over_65_pct": round((1099 + 748 + 364 + 188 + 136) / 19942 * 100, 2),
        "income": {
            "median_household_income_weekly": 2067,
            "median_family_income_weekly": 2183,
            "median_personal_income_weekly": 849,
        },
    },
    "Summer Hill": {
        "population": 7288,
        "over_65_pct": round((271 + 249 + 149 + 92 + 145) / 7288 * 100, 2),
        "income": {
            "median_household_income_weekly": 2260,
            "median_family_income_weekly": 3076,
            "median_personal_income_weekly": 1317,
        },
    },
    "The Rocks": {
        "population": 629,
        "over_65_pct": round((34 + 48 + 16 + 16 + 4) / 629 * 100, 2),
        "income": {
            "median_household_income_weekly": 4374,
            "median_family_income_weekly": 4764,
            "median_personal_income_weekly": 1911,
        },
    },
}


def main() -> None:
    pop_path = DATA / "suburb_populations.json"
    o65_path = DATA / "suburb_over_65_pct.json"
    inc_path = DATA / "suburb_income.json"

    populations = json.loads(pop_path.read_text(encoding="utf-8"))
    over_65 = json.loads(o65_path.read_text(encoding="utf-8"))
    income = json.loads(inc_path.read_text(encoding="utf-8"))

    for suburb, row in BATCH3.items():
        populations[suburb] = row["population"]
        over_65[suburb] = row["over_65_pct"]
        income[suburb] = row["income"]

    pop_path.write_text(json.dumps(populations, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    o65_path.write_text(json.dumps(over_65, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    inc_path.write_text(json.dumps(income, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print("Updated:", ", ".join(BATCH3))
    for s, row in BATCH3.items():
        print(f"  {s}: pop={row['population']}, 65%+={row['over_65_pct']}")


if __name__ == "__main__":
    main()
