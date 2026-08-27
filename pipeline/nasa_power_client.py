"""Fetch renewable-resource climatology grids from the NASA POWER API."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_ROOT = "https://power.larc.nasa.gov/api/temporal/climatology/regional"
PARAMETERS = {
    "solar": "ALLSKY_SFC_SW_DWN",
    "wind": "WS50M",
}
REGIONS = {
    "conus": (24.0, 50.0, -125.0, -66.0),
    "alaska": (51.0, 72.0, -170.0, -129.0),
    "hawaii": (18.0, 23.0, -161.0, -154.0),
}
MAX_TILE_DEGREES = 10.0
MIN_TILE_DEGREES = 2.0


class NasaPowerError(RuntimeError):
    """Raised when NASA POWER data cannot be downloaded or parsed."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _get_json(url: str, *, attempts: int = 4, timeout: int = 90) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "GridPulse/1.0"})
    for attempt in range(attempts):
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            if not isinstance(payload, dict):
                raise NasaPowerError("NASA POWER returned a non-object response.")
            return payload
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            if attempt == attempts - 1:
                raise NasaPowerError(f"NASA POWER request failed: {exc}") from exc
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def fetch_climatology() -> dict[str, Any]:
    """Fetch solar and 50-m wind climatologies for all 50 U.S. states."""
    datasets: list[dict[str, Any]] = []
    for metric, parameter in PARAMETERS.items():
        for region, (lat_min, lat_max, lon_min, lon_max) in REGIONS.items():
            for tile_index, tile in enumerate(_tiles(lat_min, lat_max, lon_min, lon_max), start=1):
                tile_lat_min, tile_lat_max, tile_lon_min, tile_lon_max = tile
                query = urlencode(
                    {
                        "latitude-min": tile_lat_min,
                        "latitude-max": tile_lat_max,
                        "longitude-min": tile_lon_min,
                        "longitude-max": tile_lon_max,
                        "parameters": parameter,
                        "community": "RE",
                        "format": "JSON",
                    }
                )
                payload = _get_json(f"{API_ROOT}?{query}")
                features = payload.get("features")
                if not isinstance(features, list) or not features:
                    raise NasaPowerError(f"NASA POWER returned no {metric} cells for {region} tile {tile_index}.")
                datasets.append(
                    {
                        "metric": metric,
                        "parameter": parameter,
                        "region": region,
                        "tile": {"latitude_min": tile_lat_min, "latitude_max": tile_lat_max, "longitude_min": tile_lon_min, "longitude_max": tile_lon_max},
                        "header": payload.get("header", {}),
                        "parameter_metadata": payload.get("parameters", {}).get(parameter, {}),
                        "features": features,
                    }
                )
    return {
        "schema_version": 1,
        "source": "NASA POWER Climatology API",
        "api_route": "/api/temporal/climatology/regional",
        "pulled_at": _utc_now(),
        "datasets": datasets,
    }


def _axis_tiles(minimum: float, maximum: float) -> list[tuple[float, float]]:
    ranges = []
    lower = minimum
    while lower < maximum:
        upper = min(lower + MAX_TILE_DEGREES, maximum)
        if upper - lower < MIN_TILE_DEGREES:
            lower = maximum - MIN_TILE_DEGREES
        ranges.append((lower, upper))
        lower = upper
    return ranges


def _tiles(lat_min: float, lat_max: float, lon_min: float, lon_max: float):
    for latitude in _axis_tiles(lat_min, lat_max):
        for longitude in _axis_tiles(lon_min, lon_max):
            yield (*latitude, *longitude)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("grid-pulse/data/source/nasa-power-climatology.json"),
    )
    args = parser.parse_args()
    document = fetch_climatology()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".tmp")
    temporary.write_text(json.dumps(document, separators=(",", ":")), encoding="utf-8")
    temporary.replace(args.output)
    cells = sum(len(dataset["features"]) for dataset in document["datasets"])
    print(f"Wrote {cells:,} NASA POWER grid cells to {args.output}")


if __name__ == "__main__":
    main()
