"""Fetch sanitized EIA-930 source data for the Grid Pulse pipeline.

The client is dependency-free so it can run in GitHub Actions without an
installation step. API keys are used only to construct outbound requests and
are never included in returned documents or written output.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_BASE_URL = "https://api.eia.gov/v2/electricity/rto"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_KEY_FILE = PROJECT_ROOT / "eia_api_key"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "grid-pulse" / "data" / "source"
DEFAULT_HOURS = 24 * 31
DEFAULT_PAGE_SIZE = 5_000
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3
SOURCE_NAME = "U.S. Energy Information Administration (EIA), Form EIA-930"

REGION_CODES = {
    "MISO": "MISO",
    "PJM": "PJM",
    "CAISO": "CISO",
}
DEFAULT_REGION_LABELS = tuple(REGION_CODES)

ROUTE_SORT_COLUMNS = {
    "region-data": ("period", "respondent", "type"),
    "fuel-type-data": ("period", "respondent", "fueltype"),
}

RETRYABLE_HTTP_CODES = frozenset({429, 500, 502, 503, 504})


class EIAClientError(RuntimeError):
    """Raised when EIA data cannot be retrieved or safely persisted."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Datetime values must be timezone-aware.")
    return value.astimezone(timezone.utc)


def _hourly_period(value: datetime) -> str:
    return _utc(value).strftime("%Y-%m-%dT%H")


def _iso(value: datetime) -> str:
    return _utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _safe_api_error(payload: Mapping[str, Any], api_key: str) -> str:
    error = payload.get("error", "missing response.data")
    if isinstance(error, Mapping):
        error = error.get("message") or error.get("error") or "API error"
    message = str(error).replace(api_key, "[REDACTED]")
    return message[:300]


class EIAClient:
    """Small EIA API v2 client with pagination and bounded retries."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = API_BASE_URL,
        page_size: int = DEFAULT_PAGE_SIZE,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        opener: Callable[..., Any] = urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key.strip()
        if not self._api_key:
            raise ValueError("EIA API key must not be empty.")
        if page_size < 1 or page_size > 5_000:
            raise ValueError("page_size must be between 1 and 5000.")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero.")
        if max_retries < 0:
            raise ValueError("max_retries must not be negative.")

        self._base_url = base_url.rstrip("/")
        self._page_size = page_size
        self._timeout = timeout
        self._max_retries = max_retries
        self._opener = opener
        self._sleep = sleeper

    @staticmethod
    def _retry_delay(attempt: int, error: HTTPError | None = None) -> float:
        if error is not None and error.headers is not None:
            retry_after = error.headers.get("Retry-After")
            if retry_after:
                try:
                    return min(max(float(retry_after), 0.0), 60.0)
                except ValueError:
                    pass
        return min(2**attempt, 30)

    def _request_page(
        self, route: str, params: Iterable[tuple[str, str | int]]
    ) -> dict[str, Any]:
        query = urlencode([("api_key", self._api_key), *params])
        request = Request(
            f"{self._base_url}/{route}/data/?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": "grid-pulse/1.0 (+https://github.com/mbpearson/mbpearson.github.io)",
            },
        )

        for attempt in range(self._max_retries + 1):
            try:
                with self._opener(request, timeout=self._timeout) as response:
                    payload = json.load(response)
                break
            except HTTPError as exc:
                if exc.code in RETRYABLE_HTTP_CODES and attempt < self._max_retries:
                    self._sleep(self._retry_delay(attempt, exc))
                    continue
                if exc.code in (401, 403):
                    message = "EIA rejected the API key or request permissions."
                else:
                    message = f"EIA request failed with HTTP {exc.code} {exc.reason}."
                raise EIAClientError(f"{route}: {message}") from exc
            except (URLError, TimeoutError, OSError) as exc:
                if attempt < self._max_retries:
                    self._sleep(self._retry_delay(attempt))
                    continue
                raise EIAClientError(
                    f"{route}: EIA could not be reached after {attempt + 1} attempts."
                ) from exc
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise EIAClientError(f"{route}: EIA returned invalid JSON.") from exc
        else:  # pragma: no cover - the loop always returns or raises
            raise EIAClientError(f"{route}: request failed.")

        if not isinstance(payload, dict):
            raise EIAClientError(f"{route}: EIA returned a non-object response.")
        response_data = payload.get("response")
        if not isinstance(response_data, dict) or not isinstance(
            response_data.get("data"), list
        ):
            detail = _safe_api_error(payload, self._api_key)
            raise EIAClientError(f"{route}: unexpected EIA response ({detail}).")
        return response_data

    def fetch_dataset(
        self,
        route: str,
        respondents: Iterable[str],
        *,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        """Fetch every page for an EIA-930 route and time window."""
        if route not in ROUTE_SORT_COLUMNS:
            raise ValueError(f"Unsupported EIA route: {route!r}.")
        start_utc = _utc(start)
        end_utc = _utc(end)
        if start_utc >= end_utc:
            raise ValueError("start must be earlier than end.")

        respondent_codes = tuple(dict.fromkeys(code.strip() for code in respondents))
        if not respondent_codes or any(not code for code in respondent_codes):
            raise ValueError("At least one non-empty respondent code is required.")

        base_params: list[tuple[str, str | int]] = [
            ("frequency", "hourly"),
            ("data[0]", "value"),
            ("start", _hourly_period(start_utc)),
            ("end", _hourly_period(end_utc)),
        ]
        base_params.extend(
            (f"sort[{index}][column]", column)
            for index, column in enumerate(ROUTE_SORT_COLUMNS[route])
        )
        base_params.extend(
            (f"sort[{index}][direction]", "asc")
            for index in range(len(ROUTE_SORT_COLUMNS[route]))
        )
        base_params.extend(
            ("facets[respondent][]", code) for code in respondent_codes
        )

        records: list[dict[str, Any]] = []
        offset = 0
        for _page_number in range(10_000):
            response = self._request_page(
                route,
                [
                    *base_params,
                    ("offset", offset),
                    ("length", self._page_size),
                ],
            )
            raw_page = response["data"]
            if not all(isinstance(record, dict) for record in raw_page):
                raise EIAClientError(f"{route}: response contains a non-object row.")
            page = [dict(cast(dict[str, Any], record)) for record in raw_page]
            records.extend(page)

            try:
                total = int(response["total"])
            except (KeyError, TypeError, ValueError):
                total = None

            if not page or (total is not None and len(records) >= total):
                return records
            if len(page) < self._page_size:
                return records
            offset += len(page)

        raise EIAClientError(f"{route}: pagination exceeded the safety limit.")

    def fetch_grid_pulse(
        self,
        *,
        region_labels: Iterable[str] = DEFAULT_REGION_LABELS,
        hours: int = DEFAULT_HOURS,
        now: datetime | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Fetch both source datasets and return sanitized pipeline documents."""
        if hours < 1:
            raise ValueError("hours must be at least 1.")

        labels = tuple(dict.fromkeys(label.upper().strip() for label in region_labels))
        unknown = sorted(set(labels) - set(REGION_CODES))
        if unknown:
            raise ValueError(f"Unsupported dashboard regions: {', '.join(unknown)}.")
        if not labels:
            raise ValueError("At least one dashboard region is required.")

        end = _utc(now or datetime.now(timezone.utc)).replace(
            minute=0, second=0, microsecond=0
        )
        start = end - timedelta(hours=hours)
        respondents = [REGION_CODES[label] for label in labels]
        pulled_at = _iso(now or datetime.now(timezone.utc))

        documents: dict[str, dict[str, Any]] = {}
        for route in ROUTE_SORT_COLUMNS:
            records = self.fetch_dataset(
                route, respondents, start=start, end=end
            )
            if not records:
                raise EIAClientError(
                    f"{route}: EIA returned no records for the requested window."
                )
            documents[route] = {
                "source": SOURCE_NAME,
                "api_route": f"/v2/electricity/rto/{route}/data/",
                "pulled_at": pulled_at,
                "requested_period": {
                    "start": _hourly_period(start),
                    "end": _hourly_period(end),
                },
                "regions": list(labels),
                "record_count": len(records),
                "records": records,
            }
        return documents


def load_api_key(key_file: Path = DEFAULT_KEY_FILE) -> str:
    """Read the key from EIA_API_KEY, falling back to an ignored local file."""
    key = os.environ.get("EIA_API_KEY", "").strip()
    if key:
        return key

    try:
        value = key_file.expanduser().read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise EIAClientError(
            "Set EIA_API_KEY or create the ignored local eia_api_key file."
        ) from exc

    if "=" in value:
        variable, candidate = value.split("=", 1)
        if variable.strip() == "EIA_API_KEY":
            value = candidate.strip().strip("\"'")
    if not value:
        raise EIAClientError("The EIA API key file is empty.")
    return value


def write_documents(output_dir: Path, documents: Mapping[str, Mapping[str, Any]]) -> None:
    """Atomically replace the source JSON documents after all are serialized."""
    output_dir = output_dir.resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        with TemporaryDirectory(prefix=".eia-source-", dir=output_dir.parent) as name:
            staging_dir = Path(name)
            for route in ROUTE_SORT_COLUMNS:
                document = documents.get(route)
                if document is None:
                    raise EIAClientError(f"Missing fetched document for {route}.")
                with (staging_dir / f"{route}.json").open(
                    "w", encoding="utf-8", newline="\n"
                ) as output_file:
                    json.dump(
                        document,
                        output_file,
                        indent=2,
                        ensure_ascii=False,
                        allow_nan=False,
                    )
                    output_file.write("\n")

            output_dir.mkdir(parents=True, exist_ok=True)
            for route in ROUTE_SORT_COLUMNS:
                os.replace(
                    staging_dir / f"{route}.json",
                    output_dir / f"{route}.json",
                )
    except EIAClientError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise EIAClientError(f"Could not write EIA source documents: {exc}") from exc


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch current EIA-930 source data for Grid Pulse."
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=DEFAULT_HOURS,
        help=f"History window to fetch (default: {DEFAULT_HOURS} hours).",
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
        help="Fallback API-key file for local runs.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for sanitized source documents.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        client = EIAClient(load_api_key(args.key_file))
        documents = client.fetch_grid_pulse(
            region_labels=args.regions,
            hours=args.hours,
        )
        write_documents(args.output_dir, documents)
    except (EIAClientError, ValueError) as exc:
        print(f"EIA fetch failed: {exc}", file=sys.stderr)
        return 1

    for route, document in documents.items():
        print(
            f"Fetched {document['record_count']:,} {route} records "
            f"for {', '.join(document['regions'])}."
        )
    print(f"Source data: {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
