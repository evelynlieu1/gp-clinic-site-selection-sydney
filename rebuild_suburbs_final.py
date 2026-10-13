"""
Regenerate scraped_data/suburbs_final.csv using the same code as pre-processing.ipynb (merge cell).

Run from the repository root:
  python rebuild_suburbs_final.py

The script changes to its own folder before it runs, so it also works when called from
another directory with the full path to the file. This is a terminal command, not Python
code for a Jupyter cell.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NB = ROOT / "pre-processing.ipynb"


def main() -> None:
    os.chdir(ROOT)
    nb = json.loads(NB.read_text(encoding="utf-8"))
    cell = nb["cells"][8]
    if cell.get("cell_type") != "code":
        raise SystemExit("Expected pre-processing.ipynb cell 8 to be a code cell.")
    source = "".join(cell.get("source", []))
    if "suburbs_final.csv" not in source or "gp_clinics_all" not in source:
        raise SystemExit(
            "pre-processing.ipynb cell 8 no longer matches the suburbs merge script; update the index in rebuild_suburbs_final.py."
        )
    exec(source, {"__name__": "__main__"})


if __name__ == "__main__":
    main()
