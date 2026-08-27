"""Fetch annual state electricity generation and emissions from EIA API v2."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from pipeline.eia_client import EIAClient, EIAClientError, load_api_key


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "grid-pulse" / "data" / "source"
GENERATION_ROUTE = "electric-power-operational-data"
EMISSIONS_ROUTE = "emissions-by-state-by-fuel"
FUEL_IDS = (
    "ALL", "DPV", "REN", "TSN", "WND", "COW", "NGO", "NUC", "PET",
    "HYC", "GEO", "BIO",
)
STATE_CODES = (
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
)


def _fetch_pages(
    client: EIAClient,
    route: str,
    params: Iterable[tuple[str, str | int]],
) -> list[dict[str, Any]]:
    """Fetch a deterministically sorted EIA dataset without retaining its key."""
    records: list[dict[str, Any]] = []
    offset = 0
    page_size = 5_000
    for _ in range(10_000):
        response = client._request_page(  # The shared client owns retry/redaction logic.
            route, [*params, ("offset", offset), ("length", page_size)]
        )
        page = response["data"]
        if not all(isinstance(row, dict) for row in page):
            raise EIAClientError(f"{route}: response contains a non-object row.")
        records.extend(dict(row) for row in page)
        try:
            total = int(response["total"])
        except (KeyError, TypeError, ValueError):
            total = None
        if not page or (total is not None and len(records) >= total) or len(page) < page_size:
            return records
        offset += len(page)
    raise EIAClientError(f"{route}: pagination exceeded the safety limit.")


def fetch_state_documents(
    api_key: str,
    *,
    start_year: int = 2019,
    end_year: int = 2024,
    now: datetime | None = None,
    generation_client: EIAClient | None = None,
    emissions_client: EIAClient | None = None,
) -> dict[str, dict[str, Any]]:
    """Return sanitized annual state generation and carbon-rate documents."""
    if start_year > end_year:
        raise ValueError("start_year must not be later than end_year.")
    generation_client = generation_client or EIAClient(
        api_key, base_url="https://api.eia.gov/v2/electricity"
    )
    emissions_client = emissions_client or EIAClient(
        api_key, base_url="https://api.eia.gov/v2/electricity/state-electricity-profiles"
    )
    generation = _fetch_pages(
        generation_client,
        GENERATION_ROUTE,
        [
            ("frequency", "annual"), ("data[0]", "generation"),
            ("start", start_year), ("end", end_year),
            ("facets[sectorid][]", "99"),
            *(("facets[fueltypeid][]", fuel) for fuel in FUEL_IDS),
            ("sort[0][column]", "period"), ("sort[0][direction]", "asc"),
            ("sort[1][column]", "location"), ("sort[1][direction]", "asc"),
            ("sort[2][column]", "fueltypeid"), ("sort[2][direction]", "asc"),
        ],
    )
    emissions = _fetch_pages(
        emissions_client,
        EMISSIONS_ROUTE,
        [
            ("frequency", "annual"), ("data[0]", "co2-rate-lbs-mwh"),
            ("start", end_year), ("end", end_year),
            ("facets[fuelid][]", "ALL"),
            ("sort[0][column]", "stateid"), ("sort[0][direction]", "asc"),
        ],
    )
    if not generation or not emissions:
        raise EIAClientError("EIA returned no annual state data for the requested window.")
    pulled_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")
    return {
        "state-generation": {
            "source": "U.S. Energy Information Administration",
            "api_route": "/v2/electricity/electric-power-operational-data/data/",
            "pulled_at": pulled_at,
            "requested_period": {"start": start_year, "end": end_year},
            "record_count": len(generation),
            "records": generation,
        },
        "state-emissions": {
            "source": "U.S. Energy Information Administration",
            "api_route": "/v2/electricity/state-electricity-profiles/emissions-by-state-by-fuel/data/",
            "pulled_at": pulled_at,
            "requested_period": {"start": end_year, "end": end_year},
            "record_count": len(emissions),
            "records": emissions,
        },
    }


def write_state_documents(output_dir: Path, documents: dict[str, dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in ("state-generation", "state-emissions"):
        destination = output_dir / f"{name}.json"
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(documents[name], indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, destination)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-year", type=int, default=2019)
    parser.add_argument("--end-year", type=int, default=2024)
    parser.add_argument("--key-file", type=Path, default=PROJECT_ROOT / "eia_api_key")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args(argv)
    try:
        documents = fetch_state_documents(
            load_api_key(args.key_file), start_year=args.start_year, end_year=args.end_year
        )
        write_state_documents(args.output_dir, documents)
    except (EIAClientError, OSError, ValueError) as exc:
        print(f"State EIA fetch failed: {exc}", file=sys.stderr)
        return 1
    print(f"Fetched {documents['state-generation']['record_count']:,} generation rows and "
          f"{documents['state-emissions']['record_count']:,} emissions rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
