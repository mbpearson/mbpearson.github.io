"""Download a small, sanitized EIA-930 dataset for Grid Pulse development."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE_URL = "https://api.eia.gov/v2/electricity/rto"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KEY_FILE = PROJECT_ROOT / "eia_api_key"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "grid-pulse" / "data" / "sample"
REGION_CODES = {
    "MISO": "MISO",
    "PJM": "PJM",
    "CAISO": "CISO",
}
PAGE_SIZE = 5_000


class EIAError(RuntimeError):
    """Raised when EIA returns an unusable response."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch recent EIA-930 operating and generation-by-fuel data for "
            "Grid Pulse. EIA_API_KEY is preferred; a local eia_api_key file is "
            "also supported."
        )
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=168,
        help="Approximate history window to request (default: 168 hours).",
    )
    parser.add_argument(
        "--regions",
        nargs="+",
        choices=tuple(REGION_CODES),
        default=list(REGION_CODES),
        help="Dashboard regions to fetch (default: MISO PJM CAISO).",
    )
    parser.add_argument(
        "--key-file",
        type=Path,
        default=DEFAULT_KEY_FILE,
        help="Fallback file containing the API key (default: ./eia_api_key).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for sanitized JSON files.",
    )
    return parser.parse_args()


def load_api_key(key_file: Path) -> str:
    key = os.environ.get("EIA_API_KEY", "").strip()
    if key:
        return key

    try:
        key = key_file.expanduser().read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise EIAError(
            "Set EIA_API_KEY or create the ignored local file "
            f"{key_file}."
        ) from exc

    if "=" in key:
        variable, value = key.split("=", 1)
        if variable.strip() == "EIA_API_KEY":
            key = value.strip().strip("\"'")

    if not key:
        raise EIAError(f"The API key file is empty: {key_file}")
    return key


def hourly_period(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H")


def request_page(
    route: str,
    api_key: str,
    params: Iterable[tuple[str, str | int]],
) -> dict[str, Any]:
    query = urlencode([("api_key", api_key), *params])
    request = Request(
        f"{API_BASE_URL}/{route}/data/?{query}",
        headers={"Accept": "application/json", "User-Agent": "grid-pulse/0.1"},
    )

    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise EIAError(
            f"EIA request for {route!r} failed with HTTP {exc.code} {exc.reason}."
        ) from exc
    except (URLError, TimeoutError) as exc:
        reason = getattr(exc, "reason", str(exc))
        raise EIAError(f"Could not reach EIA for {route!r}: {reason}") from exc
    except json.JSONDecodeError as exc:
        raise EIAError(f"EIA returned invalid JSON for {route!r}.") from exc

    response = payload.get("response")
    if not isinstance(response, dict) or not isinstance(response.get("data"), list):
        error = payload.get("error", "missing response.data")
        raise EIAError(f"Unexpected EIA response for {route!r}: {error}")
    return response


def fetch_dataset(
    route: str,
    api_key: str,
    regions: list[str],
    start: str,
    end: str,
) -> list[dict[str, Any]]:
    base_params: list[tuple[str, str | int]] = [
        ("frequency", "hourly"),
        ("data[0]", "value"),
        ("start", start),
        ("end", end),
        ("sort[0][column]", "period"),
        ("sort[0][direction]", "asc"),
    ]
    base_params.extend(("facets[respondent][]", code) for code in regions)

    records: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = request_page(
            route,
            api_key,
            [*base_params, ("offset", offset), ("length", PAGE_SIZE)],
        )
        page = response["data"]
        records.extend(page)

        try:
            total = int(response.get("total", len(records)))
        except (TypeError, ValueError):
            total = len(records)
        if not page or len(records) >= total:
            return records
        offset += len(page)


def write_dataset(
    output_path: Path,
    route: str,
    pulled_at: str,
    start: str,
    end: str,
    region_labels: list[str],
    records: list[dict[str, Any]],
) -> None:
    document = {
        "source": "U.S. Energy Information Administration (EIA), Form EIA-930",
        "api_route": f"/v2/electricity/rto/{route}/data/",
        "pulled_at": pulled_at,
        "requested_period": {"start": start, "end": end},
        "regions": region_labels,
        "record_count": len(records),
        "records": records,
    }
    output_path.write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    if args.hours < 1:
        raise EIAError("--hours must be at least 1.")

    api_key = load_api_key(args.key_file)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = hourly_period(now - timedelta(hours=args.hours))
    end = hourly_period(now)
    respondent_codes = [REGION_CODES[label] for label in args.regions]
    pulled_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    datasets = {
        "region-data": fetch_dataset(
            "region-data", api_key, respondent_codes, start, end
        ),
        "fuel-type-data": fetch_dataset(
            "fuel-type-data", api_key, respondent_codes, start, end
        ),
    }

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    for route, records in datasets.items():
        output_path = output_dir / f"{route}.json"
        write_dataset(
            output_path,
            route,
            pulled_at,
            start,
            end,
            args.regions,
            records,
        )
        print(f"Wrote {len(records):,} records to {output_path}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EIAError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
