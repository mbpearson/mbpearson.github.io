"""Transform EIA-930 records into frontend-ready regional snapshots.

The functions in this module perform no I/O. They accept the sanitized records
produced by :mod:`pipeline.fetch_example_data` (or the future API client) and
return JSON-serializable dictionaries for the Grid Pulse frontend.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping


Number = int | float

OPERATING_FIELDS = {
    "D": "actual_mwh",
    "DF": "forecast_mwh",
    "NG": "net_generation_mwh",
    "TI": "net_interchange_mwh",
}

REGION_DISPLAY_IDS = {
    "CISO": "CAISO",
}

FUEL_TYPES = {
    "BAT": ("battery", "Battery storage", False),
    "COL": ("coal", "Coal", False),
    "GEO": ("geothermal", "Geothermal", True),
    "NG": ("natural_gas", "Natural gas", False),
    "NUC": ("nuclear", "Nuclear", False),
    "OIL": ("petroleum", "Petroleum", False),
    "OTH": ("other", "Other", False),
    "SUN": ("solar", "Solar", True),
    "WAT": ("hydro", "Hydro", True),
    "WND": ("wind", "Wind", True),
}


class TransformError(ValueError):
    """Raised when source records cannot be transformed safely."""


def _number(value: Any, context: str) -> Number:
    """Return a finite JSON number while preserving integral values as ints."""
    if isinstance(value, bool) or value is None or value == "":
        raise TransformError(f"Missing numeric value for {context}.")

    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise TransformError(f"Invalid numeric value for {context}: {value!r}.") from exc

    if not number.is_finite():
        raise TransformError(f"Non-finite numeric value for {context}: {value!r}.")
    if number == number.to_integral_value():
        return int(number)
    return float(number)


def _timestamp(period: Any) -> str:
    """Normalize an EIA hourly period to an ISO-8601 UTC timestamp."""
    if not isinstance(period, str) or not period.strip():
        raise TransformError(f"Invalid EIA period: {period!r}.")

    source = period.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}", source):
            parsed = datetime.strptime(source, "%Y-%m-%dT%H").replace(
                tzinfo=timezone.utc
            )
        else:
            parsed = datetime.fromisoformat(source.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            parsed = parsed.astimezone(timezone.utc)
    except ValueError as exc:
        raise TransformError(f"Invalid EIA period: {period!r}.") from exc

    return parsed.isoformat(timespec="seconds").replace("+00:00", "Z")


def _generated_timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
    return _timestamp(value)


def _require_text(record: Mapping[str, Any], field: str, context: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise TransformError(f"Missing {field!r} for {context}.")
    return value.strip()


def _fuel_metadata(code: str, source_name: str | None = None) -> tuple[str, str, bool]:
    metadata = FUEL_TYPES.get(code)
    if metadata is not None:
        return metadata

    label = source_name.strip() if source_name and source_name.strip() else code
    fuel_id = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return fuel_id or code.lower(), label, False


def _assign_unique(
    target: dict[str, Number], field: str, value: Number, context: str
) -> None:
    if field in target:
        existing = target[field]
        if existing != value:
            raise TransformError(
                f"Conflicting duplicate values for {context}: "
                f"{existing} and {value}."
            )
    target[field] = value


def _metric(value: Number | None, timestamp: str | None) -> dict[str, Any]:
    return {"value": value, "timestamp": timestamp}


def _latest_value(
    rows: list[dict[str, Any]], field: str
) -> tuple[Number | None, str | None]:
    for row in reversed(rows):
        value = row[field]
        if value is not None:
            return value, row["timestamp"]
    return None, None


def _forecast_error(
    rows: list[dict[str, Any]],
) -> tuple[float | None, str | None]:
    for row in reversed(rows):
        actual = row["actual_mwh"]
        forecast = row["forecast_mwh"]
        if actual not in (None, 0) and forecast is not None:
            error = abs(float(actual) - float(forecast)) / abs(float(actual)) * 100
            return round(error, 1), row["timestamp"]
    return None, None


def _renewable_share(
    rows: list[dict[str, Any]], fuel_catalog: list[dict[str, Any]]
) -> tuple[float | None, str | None]:
    renewable_ids = {
        fuel["id"] for fuel in fuel_catalog if fuel["renewable"] is True
    }
    for row in reversed(rows):
        positive_values = {
            fuel_id: max(float(value), 0.0)
            for fuel_id, value in row["fuels"].items()
            if value is not None
        }
        total = sum(positive_values.values())
        if total > 0:
            renewable = sum(
                value
                for fuel_id, value in positive_values.items()
                if fuel_id in renewable_ids
            )
            return round(renewable / total * 100, 1), row["timestamp"]
    return None, None


def transform_region(
    source_region: str,
    operating_records: Iterable[Mapping[str, Any]],
    fuel_records: Iterable[Mapping[str, Any]],
    *,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build one regional dashboard snapshot from EIA operating and fuel rows."""
    operating_by_time: dict[str, dict[str, Number]] = defaultdict(dict)
    fuels_by_time: dict[str, dict[str, Number]] = defaultdict(dict)
    observed_fuels: dict[str, tuple[str, str, bool]] = {}
    region_name: str | None = None
    operating_count = 0
    fuel_count = 0

    for record in operating_records:
        if record.get("respondent") != source_region:
            continue
        period = _timestamp(record.get("period"))
        type_code = _require_text(record, "type", f"{source_region} at {period}")
        field = OPERATING_FIELDS.get(type_code)
        if field is None:
            continue
        value = _number(record.get("value"), f"{source_region} {type_code} at {period}")
        _assign_unique(operating_by_time[period], field, value, f"{source_region} {type_code} at {period}")
        source_name = record.get("respondent-name")
        if isinstance(source_name, str) and source_name.strip():
            region_name = source_name.strip()
        operating_count += 1

    for record in fuel_records:
        if record.get("respondent") != source_region:
            continue
        period = _timestamp(record.get("period"))
        code = _require_text(record, "fueltype", f"{source_region} at {period}")
        source_type_name = record.get("type-name")
        metadata = _fuel_metadata(
            code, source_type_name if isinstance(source_type_name, str) else None
        )
        fuel_id = metadata[0]
        value = _number(record.get("value"), f"{source_region} {code} at {period}")
        _assign_unique(fuels_by_time[period], fuel_id, value, f"{source_region} {code} at {period}")
        observed_fuels[code] = metadata
        source_name = record.get("respondent-name")
        if region_name is None and isinstance(source_name, str) and source_name.strip():
            region_name = source_name.strip()
        fuel_count += 1

    if operating_count == 0 and fuel_count == 0:
        raise TransformError(f"No records found for respondent {source_region!r}.")

    demand = [
        {
            "timestamp": period,
            **{
                field: values.get(field)
                for field in OPERATING_FIELDS.values()
            },
        }
        for period, values in sorted(operating_by_time.items())
    ]

    fuel_catalog = [
        {
            "code": code,
            "id": metadata[0],
            "label": metadata[1],
            "renewable": metadata[2],
        }
        for code, metadata in sorted(observed_fuels.items())
    ]
    fuel_ids: list[str] = [str(fuel["id"]) for fuel in fuel_catalog]
    generation_mix = []
    for period, values in sorted(fuels_by_time.items()):
        fuels = {fuel_id: values.get(fuel_id) for fuel_id in fuel_ids}
        available_values = [value for value in fuels.values() if value is not None]
        generation_mix.append(
            {
                "timestamp": period,
                "total_mwh": sum(available_values) if available_values else None,
                "fuels": fuels,
            }
        )

    demand_value, demand_time = _latest_value(demand, "actual_mwh")
    interchange_value, interchange_time = _latest_value(
        demand, "net_interchange_mwh"
    )
    error_value, error_time = _forecast_error(demand)
    renewable_value, renewable_time = _renewable_share(
        generation_mix, fuel_catalog
    )

    all_timestamps = [row["timestamp"] for row in demand]
    all_timestamps.extend(row["timestamp"] for row in generation_mix)
    display_id = REGION_DISPLAY_IDS.get(source_region, source_region)

    return {
        "schema_version": 1,
        "generated_at": _generated_timestamp(generated_at),
        "region": {
            "id": display_id,
            "source_id": source_region,
            "slug": display_id.lower(),
            "name": region_name or display_id,
        },
        "units": {"power": "megawatthours", "percentage": "percent"},
        "coverage": {
            "start": min(all_timestamps),
            "end": max(all_timestamps),
        },
        "kpis": {
            "demand_mwh": _metric(demand_value, demand_time),
            "forecast_error_pct": _metric(error_value, error_time),
            "renewable_share_pct": _metric(renewable_value, renewable_time),
            "net_interchange_mwh": _metric(
                interchange_value, interchange_time
            ),
        },
        "demand": demand,
        "fuel_catalog": fuel_catalog,
        "generation_mix": generation_mix,
        "processing": {
            "operating_records": operating_count,
            "fuel_records": fuel_count,
        },
    }


def transform_documents(
    operating_document: Mapping[str, Any],
    fuel_document: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Transform sanitized EIA documents into snapshots keyed by region slug."""
    operating_records = operating_document.get("records")
    fuel_records = fuel_document.get("records")
    if not isinstance(operating_records, list):
        raise TransformError("Operating document must contain a records list.")
    if not isinstance(fuel_records, list):
        raise TransformError("Fuel document must contain a records list.")

    source_regions: set[str] = set()
    for record in [*operating_records, *fuel_records]:
        if not isinstance(record, Mapping):
            continue
        respondent = record.get("respondent")
        if isinstance(respondent, str) and respondent:
            source_regions.add(respondent)
    generated_at = operating_document.get("pulled_at") or fuel_document.get(
        "pulled_at"
    )
    if generated_at is not None and not isinstance(generated_at, str):
        raise TransformError("pulled_at must be an ISO-8601 string.")

    snapshots: dict[str, dict[str, Any]] = {}
    for source_region in sorted(source_regions):
        snapshot = transform_region(
            source_region,
            operating_records,
            fuel_records,
            generated_at=generated_at,
        )
        snapshots[snapshot["region"]["slug"]] = snapshot
    return snapshots
