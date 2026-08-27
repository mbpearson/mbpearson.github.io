"""Aggregate NASA POWER climatology grid cells into state-level map data."""

from __future__ import annotations

import argparse
import io
import json
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.request import Request, urlopen

import shapefile
from pyproj import Transformer
from shapely.geometry import box, shape
from shapely.ops import transform

from pipeline.validate import require_valid, validate_resource_map


CENSUS_BOUNDARIES = "https://www2.census.gov/geo/tiger/GENZ2025/shp/cb_2025_us_state_20m.zip"
STATE_FIPS = {
    "01", "02", "04", "05", "06", "08", "09", "10", "12", "13", "15",
    "16", "17", "18", "19", "20", "21", "22", "23", "24", "25", "26",
    "27", "28", "29", "30", "31", "32", "33", "34", "35", "36", "37",
    "38", "39", "40", "41", "42", "44", "45", "46", "47", "48", "49",
    "50", "51", "53", "54", "55", "56",
}
METRICS = {
    "solar": {
        "parameter": "ALLSKY_SFC_SW_DWN",
        "label": "Solar resource",
        "unit": "kWh/m²/day",
        "cell_width": 1.0,
        "cell_height": 1.0,
    },
    "wind": {
        "parameter": "WS50M",
        "label": "Wind speed at 50 m",
        "unit": "m/s",
        "cell_width": 0.625,
        "cell_height": 0.5,
    },
}


class ResourceMapError(RuntimeError):
    """Raised when resource-map inputs cannot produce a valid artifact."""


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def fetch_state_features(url: str = CENSUS_BOUNDARIES) -> list[dict[str, Any]]:
    """Download Census cartographic boundaries and return the 50 state features."""
    request = Request(url, headers={"User-Agent": "GridPulse/1.0"})
    with urlopen(request, timeout=90) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    stems = {Path(name).suffix: name for name in archive.namelist()}
    try:
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read(stems[".shp"])),
            shx=io.BytesIO(archive.read(stems[".shx"])),
            dbf=io.BytesIO(archive.read(stems[".dbf"])),
        )
    except KeyError as exc:
        raise ResourceMapError("Census boundary archive is incomplete.") from exc

    fields = [field[0] for field in reader.fields[1:]]
    features = []
    for record in reader.iterShapeRecords():
        properties = dict(zip(fields, record.record))
        if properties.get("STATEFP") not in STATE_FIPS:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": properties,
                "geometry": record.shape.__geo_interface__,
            }
        )
    if len(features) != 50:
        raise ResourceMapError(f"Expected 50 state boundaries; received {len(features)}.")
    return features


def _grid_cells(document: Mapping[str, Any], metric: str) -> Iterable[tuple[Any, float]]:
    definition = METRICS[metric]
    seen: set[tuple[float, float]] = set()
    for dataset in document.get("datasets", []):
        if not isinstance(dataset, Mapping) or dataset.get("metric") != metric:
            continue
        for feature in dataset.get("features", []):
            coordinates = feature.get("geometry", {}).get("coordinates", [])
            values = feature.get("properties", {}).get("parameter", {}).get(
                definition["parameter"], {}
            )
            if len(coordinates) < 2 or not isinstance(values, Mapping):
                continue
            lon, lat = float(coordinates[0]), float(coordinates[1])
            key = (lon, lat)
            value = values.get("ANN")
            if key in seen or not isinstance(value, (int, float)) or value <= -999:
                continue
            seen.add(key)
            half_width = definition["cell_width"] / 2
            half_height = definition["cell_height"] / 2
            yield box(lon - half_width, lat - half_height, lon + half_width, lat + half_height), float(value)


def _percentiles(values: list[tuple[str, float]]) -> dict[str, tuple[int, int]]:
    ordered = sorted(values, key=lambda item: item[1], reverse=True)
    count = len(ordered)
    return {
        postal: (rank, round((count - rank) / max(count - 1, 1) * 100))
        for rank, (postal, _) in enumerate(ordered, start=1)
    }


def aggregate_resources(
    document: Mapping[str, Any], state_features: Iterable[Mapping[str, Any]], *, generated_at: str | None = None
) -> dict[str, Any]:
    """Area-weight annual NASA grid values into Census state boundaries."""
    projector = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True).transform
    states = []
    for feature in state_features:
        properties = feature["properties"]
        states.append(
            {
                "postal_code": properties["STUSPS"],
                "name": properties["NAME"],
                "geometry": transform(projector, shape(feature["geometry"])),
            }
        )
    if len(states) != 50:
        raise ResourceMapError(f"Expected 50 states; received {len(states)}.")

    totals: dict[str, dict[str, list[float]]] = defaultdict(dict)
    cell_counts: dict[str, int] = {}
    for metric in METRICS:
        cells = [(transform(projector, geometry), value) for geometry, value in _grid_cells(document, metric)]
        cell_counts[metric] = len(cells)
        if not cells:
            raise ResourceMapError(f"No usable NASA POWER cells found for {metric}.")
        for state in states:
            weighted_sum = 0.0
            covered_area = 0.0
            for geometry, value in cells:
                if not state["geometry"].intersects(geometry):
                    continue
                area = state["geometry"].intersection(geometry).area
                weighted_sum += value * area
                covered_area += area
            if covered_area == 0:
                raise ResourceMapError(f"No {metric} coverage for {state['postal_code']}.")
            totals[state["postal_code"]][metric] = [weighted_sum / covered_area, covered_area]

    standings = {
        metric: _percentiles([(state["postal_code"], totals[state["postal_code"]][metric][0]) for state in states])
        for metric in METRICS
    }
    state_rows = []
    for state in sorted(states, key=lambda item: item["name"]):
        postal = state["postal_code"]
        metrics = {}
        for metric in METRICS:
            value = totals[postal][metric][0]
            rank, percentile = standings[metric][postal]
            metrics[metric] = {"value": round(value, 2), "rank": rank, "percentile": percentile}
        state_rows.append({"postal_code": postal, "hc_key": f"us-{postal.lower()}", "name": state["name"], "metrics": metrics})

    return {
        "schema_version": 1,
        "generated_at": generated_at or _timestamp(),
        "source": {
            "nasa": "NASA POWER Climatology API (2001–2020)",
            "boundaries": "U.S. Census Bureau 2025 Cartographic Boundary Files",
            "method": "Equal-area intersection-weighted mean",
        },
        "metrics": {
            metric: {key: value for key, value in definition.items() if key not in {"cell_width", "cell_height"}}
            for metric, definition in METRICS.items()
        },
        "processing": {"states": len(state_rows), "grid_cells": cell_counts},
        "states": state_rows,
    }


def build_resource_map(source: Path, output: Path) -> dict[str, Any]:
    document = json.loads(source.read_text(encoding="utf-8"))
    artifact = aggregate_resources(document, fetch_state_features())
    require_valid(validate_resource_map(artifact))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("grid-pulse/data/source/nasa-power-climatology.json"))
    parser.add_argument("--output", type=Path, default=Path("grid-pulse/data/us-renewable-resources.json"))
    args = parser.parse_args()
    artifact = build_resource_map(args.source, args.output)
    print(f"Wrote {len(artifact['states'])} state resource summaries to {args.output}")


if __name__ == "__main__":
    main()
