"""
Authoritative health-facility inventory: general practices and pharmacies in NSW.

Why this module exists
----------------------
The project's own ``scraped_data/gp_clinics_all.csv`` is an LLM-prompted scrape. It has
two structural problems no amount of parsing can fix: 239 of the 680 suburbs were never
returned at all (``gp_data_status == 'no_data'`` in :mod:`gp_supply`), and supply was
only ever counted *inside* a suburb polygon. This module supplies a second, independently
sourced point layer so supply can be measured as accessibility from a suburb rather than
membership of it.

Source
------
Geoscience Australia, "National HealthDirect Health Facilities" -- derived from the
National Health Services Directory (NHSD).

- Data record (eCat):  696d12c2-38c6-4afa-96b6-309a1ac9a50b  (eCatId 149331)
- NHSD extract date:   2025-03-07
- Released:            November 2025
- Licence:             Creative Commons Attribution 4.0 International (CC-BY 4.0)

Attribution required by the licence: "Geoscience Australia, National HealthDirect Health
Facilities, CC-BY 4.0, NHSD extract 2025-03-07."

Note on record ids: the older eCat record ``1d95ca95-...`` is the *WMS service* record
and carries March-2024 lineage. It is not the current data record. Use the one above.

Service layers
--------------
``MapServer`` capabilities are Map, Query, Data; formats JSON / geoJSON / PBF;
``maxRecordCount`` 2000 with ``supportsPagination`` true, hence the
``resultOffset`` / ``resultRecordCount`` loop in :func:`fetch_layer`.

- layer 0 -- GENERAL_PRACTICE
- layer 1 -- HOSPITAL
- layer 2 -- PHARMACY

Attribute fields on layers 0 and 2 are identical: ``objectid``, ``operationalstatus``,
``ga_class``, ``organisation_name``, ``address``, ``suburb``, ``state``, ``postcode``,
``longitude``, ``latitude``, ``nhsd_service_id``, ``nhsd_service_type``,
``gnaf_address_detail_pid``, ``ga_source_date``.

**There is no practitioner-count field.** This layer can support a practices-per-capita
measure (decision D5) and nothing finer. Any GP head count or FTE must come from
elsewhere and stays a context column.

The one filter that matters
---------------------------
Layer 0 with ``state='NSW'`` returns 2482 rows, but only 2089 are
``nhsd_service_type = 'General practice service'``. The other 393 (15.8%) are nurse-led
clinics, maternal and child health services, skin-cancer clinics and similar. Counting
the whole layer overstates GP supply by roughly one sixth, which is why
:func:`general_practices_nsw` applies the filter server-side and
:data:`GP_SERVICE_TYPE` is not optional.

Known incompleteness -- do not present this as a census
-------------------------------------------------------
Nationally the layer holds 6254 general practices against roughly 7000-7135 accredited
practices, so it misses an estimated 10-12%. A concrete miss inside the study area:
Croydon Medical Centre, operating about 20 years, is absent. Pearson correlation with
the project's own ``clinics_listed`` is 0.82-0.84. Neither source is ground truth; this
one has far better provenance and should be treated as a good lower bound on supply.

Cache
-----
Raw layer pulls are cached as CSV under ``facility_cache/`` (gitignored, regenerable).
Delete the directory, or pass ``refresh=True``, to re-fetch.

Run from project root:
  python health_facilities.py            # fetch/refresh cache and write the two CSVs
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "facility_cache"
OUT_DIR = ROOT / "scraped_data"

BASE_URL = (
    "https://services.ga.gov.au/gis/rest/services/"
    "National_HealthDirect_Health_Facilities/MapServer"
)

#: Provenance of the extract, for stamping into docs and output headers.
SOURCE_RECORD_ID = "696d12c2-38c6-4afa-96b6-309a1ac9a50b"
SOURCE_ECAT_ID = "149331"
SOURCE_EXTRACT_DATE = "2025-03-07"
SOURCE_LICENCE = "CC-BY 4.0"
SOURCE_ATTRIBUTION = (
    "Geoscience Australia, National HealthDirect Health Facilities, "
    f"{SOURCE_LICENCE}, NHSD extract {SOURCE_EXTRACT_DATE}"
)

LAYER_GENERAL_PRACTICE = 0
LAYER_HOSPITAL = 1
LAYER_PHARMACY = 2

#: The only ``nhsd_service_type`` that is actually a GP clinic. See module docstring.
GP_SERVICE_TYPE = "General practice service"

#: Server-side page size. The service caps a single response at 2000 records.
PAGE_SIZE = 2000

#: Fields shared by layers 0 and 2. Requesting them explicitly (rather than ``*``)
#: keeps the cached CSV schema stable if GA adds columns.
FACILITY_FIELDS = [
    "objectid",
    "operationalstatus",
    "ga_class",
    "organisation_name",
    "address",
    "suburb",
    "state",
    "postcode",
    "longitude",
    "latitude",
    "nhsd_service_id",
    "nhsd_service_type",
    "gnaf_address_detail_pid",
    "ga_source_date",
]

_REQUEST_TIMEOUT = 120
_RETRIES = 3
_RETRY_WAIT = 5.0


def _query_url(layer_id: int, params: dict[str, str]) -> str:
    return f"{BASE_URL}/{layer_id}/query?" + urllib.parse.urlencode(params)


def _get_json(url: str) -> dict:
    """GET one ArcGIS query URL, retrying transient failures."""
    last: Exception | None = None
    for attempt in range(1, _RETRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=_REQUEST_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
            if attempt < _RETRIES:
                time.sleep(_RETRY_WAIT * attempt)
            continue
        # ArcGIS reports application errors with HTTP 200 and an "error" object.
        if "error" in payload:
            raise RuntimeError(f"ArcGIS error for {url}: {payload['error']}")
        return payload
    raise RuntimeError(f"failed after {_RETRIES} attempts: {url}") from last


def layer_count(layer_id: int, where: str) -> int:
    """Server-side row count for a where clause, without downloading the rows."""
    url = _query_url(
        layer_id, {"where": where, "returnCountOnly": "true", "f": "json"}
    )
    return int(_get_json(url)["count"])


def fetch_layer(
    layer_id: int,
    where: str = "1=1",
    out_fields: list[str] | None = None,
    *,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    Download every row a where clause matches, paging with resultOffset.

    The service returns at most :data:`PAGE_SIZE` rows per response and sets
    ``exceededTransferLimit`` when more remain, so a single request silently truncates
    any layer larger than that. This pages until the server stops returning features
    and cross-checks the total against :func:`layer_count`, raising if they disagree --
    a short fetch that looks like a real answer is the failure mode worth being loud
    about.

    Geometry is not requested: ``longitude`` / ``latitude`` attributes carry the
    position, in EPSG:4326.
    """
    fields = out_fields or FACILITY_FIELDS
    expected = layer_count(layer_id, where)
    if verbose:
        print(f"layer {layer_id}: server reports {expected} rows for {where!r}", flush=True)

    rows: list[dict] = []
    offset = 0
    while True:
        url = _query_url(
            layer_id,
            {
                "where": where,
                "outFields": ",".join(fields),
                "returnGeometry": "false",
                "orderByFields": "objectid ASC",
                "resultOffset": str(offset),
                "resultRecordCount": str(PAGE_SIZE),
                "f": "json",
            },
        )
        payload = _get_json(url)
        features = payload.get("features", [])
        if not features:
            break
        rows.extend(f["attributes"] for f in features)
        if verbose:
            print(f"  fetched {len(rows)}/{expected}", flush=True)
        offset += len(features)
        if len(features) < PAGE_SIZE and not payload.get("exceededTransferLimit"):
            break

    df = pd.DataFrame(rows, columns=fields)
    if len(df) != expected:
        raise RuntimeError(
            f"layer {layer_id} pagination incomplete: got {len(df)} rows, "
            f"server count says {expected}"
        )
    return df


def _cache_path(name: str) -> Path:
    return CACHE_DIR / f"{name}.csv"


def load_or_fetch(
    name: str,
    layer_id: int,
    where: str = "1=1",
    out_fields: list[str] | None = None,
    *,
    refresh: bool = False,
    verbose: bool = True,
) -> pd.DataFrame:
    """
    Read ``facility_cache/<name>.csv`` if it exists, otherwise fetch and write it.

    ``refresh=True`` forces a re-fetch and overwrites the cache. Identifier-like
    columns are read back as strings so leading zeros in ``postcode`` and the
    ``gnaf_address_detail_pid`` / ``nhsd_service_id`` keys survive the round trip.
    """
    path = _cache_path(name)
    if path.exists() and not refresh:
        if verbose:
            print(f"cache hit: {path}", flush=True)
        return pd.read_csv(path, dtype=_READ_DTYPES, keep_default_na=True)

    df = fetch_layer(layer_id, where, out_fields, verbose=verbose)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8")
    if verbose:
        print(f"cached {len(df)} rows to {path}", flush=True)
    return df


#: Columns that must never be parsed as numbers (postcodes and opaque ids).
_READ_DTYPES = {
    "postcode": "string",
    "nhsd_service_id": "string",
    "gnaf_address_detail_pid": "string",
    "ga_source_date": "string",
}


def general_practices_nsw(*, refresh: bool = False, verbose: bool = True) -> pd.DataFrame:
    """
    NSW general practices, filtered to ``nhsd_service_type = 'General practice service'``.

    Expected 2089 rows. The filter is applied server-side, so the cache holds the
    filtered set and cannot be accidentally used unfiltered.
    """
    where = f"state='NSW' AND nhsd_service_type='{GP_SERVICE_TYPE}'"
    return load_or_fetch(
        "ga_general_practices_nsw_raw",
        LAYER_GENERAL_PRACTICE,
        where,
        refresh=refresh,
        verbose=verbose,
    )


def pharmacies_nsw(*, refresh: bool = False, verbose: bool = True) -> pd.DataFrame:
    """
    NSW pharmacies (layer 2, all service types). Expected 1832 rows.

    Pharmacies are a co-location and foot-traffic signal for site selection, not a
    supply measure -- they are never a ranking input.
    """
    return load_or_fetch(
        "ga_pharmacies_nsw_raw",
        LAYER_PHARMACY,
        "state='NSW'",
        refresh=refresh,
        verbose=verbose,
    )


def all_nsw_layer0(*, refresh: bool = False, verbose: bool = True) -> pd.DataFrame:
    """
    Unfiltered NSW layer 0 (2482 rows), for auditing what the GP filter removes.

    Not for counting supply. See the module docstring.
    """
    return load_or_fetch(
        "ga_layer0_nsw_all_raw",
        LAYER_GENERAL_PRACTICE,
        "state='NSW'",
        refresh=refresh,
        verbose=verbose,
    )


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    gps = general_practices_nsw()
    pharms = pharmacies_nsw()

    gp_out = OUT_DIR / "ga_general_practices_nsw.csv"
    ph_out = OUT_DIR / "ga_pharmacies_nsw.csv"
    gps.to_csv(gp_out, index=False, encoding="utf-8")
    pharms.to_csv(ph_out, index=False, encoding="utf-8")

    print()
    print(f"source: {SOURCE_ATTRIBUTION}")
    print(f"record: {SOURCE_RECORD_ID} (eCatId {SOURCE_ECAT_ID})")
    print(f"{len(gps):5d} general practices -> {gp_out}")
    print(f"{len(pharms):5d} pharmacies       -> {ph_out}")


if __name__ == "__main__":
    main()
