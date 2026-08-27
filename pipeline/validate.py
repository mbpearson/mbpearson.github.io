"""Validate source records and generated Grid Pulse artifacts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from math import isclose, isfinite
from typing import Any, Literal, TypeGuard


Status = Literal["passed", "warning", "failed"]
DEMAND_FIELDS = (
    "actual_mwh",
    "forecast_mwh",
    "net_generation_mwh",
    "net_interchange_mwh",
)
REQUIRED_KPIS = (
    "demand_mwh",
    "forecast_error_pct",
    "renewable_share_pct",
    "net_interchange_mwh",
)


@dataclass(frozen=True)
class ValidationCheck:
    """One named data-quality check suitable for serialization."""

    name: str
    status: Status
    message: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class ValidationReport:
    """Collection of checks for one source document or regional snapshot."""

    subject: str
    checked_at: str
    checks: tuple[ValidationCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.status != "failed" for check in self.checks)

    @property
    def status(self) -> Status:
        if any(check.status == "failed" for check in self.checks):
            return "failed"
        if any(check.status == "warning" for check in self.checks):
            return "warning"
        return "passed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "checked_at": self.checked_at,
            "status": self.status,
            "passed": self.passed,
            "summary": {
                status: sum(check.status == status for check in self.checks)
                for status in ("passed", "warning", "failed")
            },
            "checks": [check.to_dict() for check in self.checks],
        }


class ValidationError(RuntimeError):
    """Raised when a validation report contains a failed check."""

    def __init__(self, report: ValidationReport) -> None:
        failed_names = [
            check.name for check in report.checks if check.status == "failed"
        ]
        super().__init__(
            f"Validation failed for {report.subject}: {', '.join(failed_names)}"
        )
        self.report = report


def require_valid(report: ValidationReport) -> ValidationReport:
    """Return a usable report or raise an exception suitable for failing a build."""
    if not report.passed:
        raise ValidationError(report)
    return report


def validate_resource_map(
    artifact: Any, *, now: datetime | None = None
) -> ValidationReport:
    """Validate the static state-level NASA POWER resource-map artifact."""
    checked = _utc_now(now)
    checks: list[ValidationCheck] = []
    if not isinstance(artifact, Mapping):
        return ValidationReport(
            "U.S. renewable resource map",
            _iso(checked),
            (ValidationCheck("resource_structure", "failed", "Artifact is not an object."),),
        )

    states = artifact.get("states")
    metrics = artifact.get("metrics")
    structure_errors = []
    if artifact.get("schema_version") != 1:
        structure_errors.append("schema_version must equal 1")
    if not isinstance(states, list):
        structure_errors.append("states must be a list")
    if not isinstance(metrics, Mapping) or set(metrics) != {"solar", "wind"}:
        structure_errors.append("metrics must define solar and wind")
    checks.append(
        ValidationCheck(
            "resource_structure",
            "failed" if structure_errors else "passed",
            "; ".join(structure_errors) if structure_errors else "Resource-map schema is present.",
        )
    )
    if structure_errors or not isinstance(states, list):
        return ValidationReport("U.S. renewable resource map", _iso(checked), tuple(checks))

    errors: list[str] = []
    postals: list[str] = []
    map_keys: list[str] = []
    for index, state in enumerate(states):
        if not isinstance(state, Mapping):
            errors.append(f"state {index} is not an object")
            continue
        postal = state.get("postal_code")
        map_key = state.get("hc_key")
        if not isinstance(postal, str) or len(postal) != 2:
            errors.append(f"state {index}.postal_code is invalid")
        else:
            postals.append(postal)
        if not isinstance(map_key, str) or not map_key.startswith("us-"):
            errors.append(f"state {index}.hc_key is invalid")
        else:
            map_keys.append(map_key)
        state_metrics = state.get("metrics")
        if not isinstance(state_metrics, Mapping):
            errors.append(f"state {index}.metrics is invalid")
            continue
        for metric in ("solar", "wind"):
            value = state_metrics.get(metric)
            if not isinstance(value, Mapping):
                errors.append(f"state {index}.{metric} is missing")
                continue
            if not _is_number(value.get("value")) or value["value"] < 0:
                errors.append(f"state {index}.{metric}.value is invalid")
            if not isinstance(value.get("rank"), int) or not 1 <= value["rank"] <= 50:
                errors.append(f"state {index}.{metric}.rank is invalid")
            if not isinstance(value.get("percentile"), int) or not 0 <= value["percentile"] <= 100:
                errors.append(f"state {index}.{metric}.percentile is invalid")

    if len(states) != 50:
        errors.append(f"expected 50 states; received {len(states)}")
    if len(postals) != len(set(postals)) or len(map_keys) != len(set(map_keys)):
        errors.append("state identifiers must be unique")
    checks.append(
        ValidationCheck(
            "state_resources",
            "failed" if errors else "passed",
            "; ".join(errors[:10]) if errors else "Validated solar and wind metrics for 50 states.",
            {"states": len(states), "errors": len(errors)},
        )
    )
    return ValidationReport("U.S. renewable resource map", _iso(checked), tuple(checks))


def _utc_now(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        raise ValueError("now must be timezone-aware.")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp must be a non-empty string")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_number(value: Any) -> TypeGuard[int | float]:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(value)
    )


def _contains_forbidden_source_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            normalized = str(key).lower().replace("-", "").replace("_", "")
            if normalized in {"apikey", "request"}:
                return True
            if _contains_forbidden_source_key(nested_value):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_forbidden_source_key(item) for item in value)
    return False


def _row_timestamp(
    row: Mapping[str, Any], index: int, errors: list[str]
) -> str | None:
    timestamp = row.get("timestamp")
    try:
        _parse_timestamp(timestamp)
        if not isinstance(timestamp, str):
            raise ValueError
    except (TypeError, ValueError):
        errors.append(f"row {index} has an invalid timestamp")
        return None
    return timestamp


def validate_source_document(
    document: Any,
    *,
    expected_route: str | None = None,
    now: datetime | None = None,
) -> ValidationReport:
    """Validate a sanitized document created by the EIA fetch layer."""
    checked = _utc_now(now)
    checks: list[ValidationCheck] = []
    subject = expected_route or "EIA source document"

    if not isinstance(document, Mapping):
        return ValidationReport(
            subject,
            _iso(checked),
            (
                ValidationCheck(
                    "source_structure", "failed", "Source document is not an object."
                ),
            ),
        )

    records = document.get("records")
    structure_errors: list[str] = []
    if not isinstance(records, list):
        structure_errors.append("records must be a list")
    if not isinstance(document.get("record_count"), int):
        structure_errors.append("record_count must be an integer")
    if not isinstance(document.get("pulled_at"), str):
        structure_errors.append("pulled_at must be a timestamp string")
    checks.append(
        ValidationCheck(
            "source_structure",
            "failed" if structure_errors else "passed",
            "; ".join(structure_errors) if structure_errors else "Required source fields are present.",
        )
    )

    if _contains_forbidden_source_key(document):
        checks.append(
            ValidationCheck(
                "secret_safety",
                "failed",
                "Source document contains request metadata or an API-key field.",
            )
        )
    else:
        checks.append(
            ValidationCheck(
                "secret_safety",
                "passed",
                "No API-key or echoed-request fields are present.",
            )
        )

    route = document.get("api_route")
    if expected_route is not None:
        route_matches = isinstance(route, str) and f"/{expected_route}/" in route
        checks.append(
            ValidationCheck(
                "source_route",
                "passed" if route_matches else "failed",
                (
                    f"Source route matches {expected_route}."
                    if route_matches
                    else f"Source route does not match {expected_route}."
                ),
            )
        )

    if not isinstance(records, list):
        return ValidationReport(subject, _iso(checked), tuple(checks))

    count_matches = document.get("record_count") == len(records)
    checks.append(
        ValidationCheck(
            "source_record_count",
            "passed" if count_matches else "failed",
            (
                f"Record count matches the {len(records):,} source rows."
                if count_matches
                else "record_count does not match the records list."
            ),
            {"records": len(records), "declared": document.get("record_count")},
        )
    )

    record_errors: list[str] = []
    type_field = "fueltype" if expected_route == "fuel-type-data" else "type"
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            record_errors.append(f"records[{index}] is not an object")
            continue
        for field_name in ("period", "respondent", type_field):
            if not isinstance(record.get(field_name), str) or not record.get(field_name):
                record_errors.append(f"records[{index}].{field_name} is missing")
        try:
            _parse_timestamp(record.get("period"))
        except (TypeError, ValueError):
            record_errors.append(f"records[{index}].period is invalid")
        raw_value = record.get("value")
        try:
            if isinstance(raw_value, bool) or not isinstance(
                raw_value, (str, int, float)
            ):
                raise ValueError
            numeric_value = float(raw_value)
            if not isfinite(numeric_value):
                raise ValueError
        except (TypeError, ValueError):
            record_errors.append(f"records[{index}].value is invalid")
        if len(record_errors) >= 10:
            break

    checks.append(
        ValidationCheck(
            "source_records",
            "failed" if record_errors else "passed",
            (
                "; ".join(record_errors)
                if record_errors
                else f"All {len(records):,} source records are usable."
            ),
            {"sampled_errors": len(record_errors)},
        )
    )
    return ValidationReport(subject, _iso(checked), tuple(checks))


def _validate_series(
    rows: Any,
    *,
    name: str,
    value_fields: tuple[str, ...],
) -> tuple[ValidationCheck, list[str], int, int]:
    if not isinstance(rows, list) or not rows:
        return (
            ValidationCheck(name, "failed", f"{name} must be a non-empty list."),
            [],
            0,
            0,
        )

    errors: list[str] = []
    timestamps: list[str] = []
    missing = 0
    possible = len(rows) * len(value_fields)
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            errors.append(f"row {index} is not an object")
            continue
        timestamp = _row_timestamp(row, index, errors)
        if timestamp is not None:
            timestamps.append(timestamp)
        for value_field in value_fields:
            value = row.get(value_field)
            if value is None:
                missing += 1
            elif not _is_number(value):
                errors.append(f"row {index}.{value_field} is not a finite number or null")

    if timestamps != sorted(timestamps):
        errors.append("timestamps are not sorted")
    if len(timestamps) != len(set(timestamps)):
        errors.append("timestamps are not unique")

    return (
        ValidationCheck(
            name,
            "failed" if errors else "passed",
            "; ".join(errors[:10]) if errors else f"Validated {len(rows):,} ordered rows.",
            {"rows": len(rows), "errors": len(errors)},
        ),
        timestamps,
        missing,
        possible,
    )


def validate_snapshot(
    snapshot: Any,
    *,
    now: datetime | None = None,
    warn_after: timedelta = timedelta(hours=12),
    fail_after: timedelta = timedelta(hours=48),
    warn_missing_ratio: float = 0.15,
    fail_missing_ratio: float = 0.35,
) -> ValidationReport:
    """Validate one transformed regional snapshot and return named checks."""
    checked = _utc_now(now)
    checks: list[ValidationCheck] = []

    if warn_after < timedelta(0) or fail_after <= warn_after:
        raise ValueError("Freshness thresholds must satisfy 0 <= warn_after < fail_after.")
    if not 0 <= warn_missing_ratio < fail_missing_ratio <= 1:
        raise ValueError(
            "Missing-data thresholds must satisfy 0 <= warning < failure <= 1."
        )
    if not isinstance(snapshot, Mapping):
        return ValidationReport(
            "regional snapshot",
            _iso(checked),
            (ValidationCheck("snapshot_structure", "failed", "Snapshot is not an object."),),
        )

    region = snapshot.get("region")
    region_id = region.get("id") if isinstance(region, Mapping) else None
    subject = region_id if isinstance(region_id, str) and region_id else "regional snapshot"
    required_objects = ("region", "units", "coverage", "kpis", "processing")
    structure_errors = [
        f"{name} must be an object"
        for name in required_objects
        if not isinstance(snapshot.get(name), Mapping)
    ]
    if snapshot.get("schema_version") != 1:
        structure_errors.append("schema_version must equal 1")
    for name in ("demand", "fuel_catalog", "generation_mix"):
        if not isinstance(snapshot.get(name), list):
            structure_errors.append(f"{name} must be a list")
    checks.append(
        ValidationCheck(
            "snapshot_structure",
            "failed" if structure_errors else "passed",
            "; ".join(structure_errors) if structure_errors else "Snapshot schema is present.",
        )
    )
    if structure_errors:
        return ValidationReport(subject, _iso(checked), tuple(checks))

    generated_at = snapshot.get("generated_at")
    try:
        generated_time = _parse_timestamp(generated_at)
        generation_age = checked - generated_time
        generated_valid = generation_age >= -timedelta(minutes=15)
    except (TypeError, ValueError):
        generated_valid = False
    checks.append(
        ValidationCheck(
            "generated_timestamp",
            "passed" if generated_valid else "failed",
            (
                "Generation timestamp is valid."
                if generated_valid
                else "generated_at is invalid or unreasonably far in the future."
            ),
        )
    )

    demand_check, demand_times, demand_missing, demand_possible = _validate_series(
        snapshot["demand"], name="demand_series", value_fields=DEMAND_FIELDS
    )
    checks.append(demand_check)

    catalog = snapshot["fuel_catalog"]
    catalog_errors: list[str] = []
    fuel_ids: list[str] = []
    fuel_codes: list[str] = []
    for index, fuel in enumerate(catalog):
        if not isinstance(fuel, Mapping):
            catalog_errors.append(f"fuel {index} is not an object")
            continue
        fuel_id = fuel.get("id")
        code = fuel.get("code")
        if not isinstance(fuel_id, str) or not fuel_id:
            catalog_errors.append(f"fuel {index}.id is missing")
        else:
            fuel_ids.append(fuel_id)
        if not isinstance(code, str) or not code:
            catalog_errors.append(f"fuel {index}.code is missing")
        else:
            fuel_codes.append(code)
        if not isinstance(fuel.get("label"), str) or not fuel.get("label"):
            catalog_errors.append(f"fuel {index}.label is missing")
        if not isinstance(fuel.get("renewable"), bool):
            catalog_errors.append(f"fuel {index}.renewable must be Boolean")
    if len(fuel_ids) != len(set(fuel_ids)) or len(fuel_codes) != len(set(fuel_codes)):
        catalog_errors.append("fuel IDs and codes must be unique")
    checks.append(
        ValidationCheck(
            "fuel_catalog",
            "failed" if catalog_errors or not catalog else "passed",
            (
                "; ".join(catalog_errors[:10])
                if catalog_errors
                else f"Validated {len(catalog):,} fuel definitions."
            ),
            {"fuels": len(catalog), "errors": len(catalog_errors)},
        )
    )

    mix = snapshot["generation_mix"]
    mix_errors: list[str] = []
    mix_times: list[str] = []
    mix_missing = 0
    mix_possible = len(mix) * len(fuel_ids)
    for index, row in enumerate(mix):
        if not isinstance(row, Mapping):
            mix_errors.append(f"row {index} is not an object")
            continue
        timestamp = _row_timestamp(row, index, mix_errors)
        if timestamp is not None:
            mix_times.append(timestamp)
        fuels = row.get("fuels")
        if not isinstance(fuels, Mapping):
            mix_errors.append(f"row {index}.fuels is not an object")
            continue
        if set(fuels) != set(fuel_ids):
            mix_errors.append(f"row {index}.fuels does not match fuel_catalog")
        available: list[int | float] = []
        for fuel_id in fuel_ids:
            value = fuels.get(fuel_id)
            if value is None:
                mix_missing += 1
            elif _is_number(value):
                available.append(value)
            else:
                mix_errors.append(f"row {index}.fuels.{fuel_id} is invalid")
        expected_total = sum(available) if available else None
        total = row.get("total_mwh")
        total_matches = (
            total is None
            if expected_total is None
            else _is_number(total) and isclose(float(total), float(expected_total))
        )
        if not total_matches:
            mix_errors.append(f"row {index}.total_mwh does not match its fuels")
    if mix_times != sorted(mix_times):
        mix_errors.append("timestamps are not sorted")
    if len(mix_times) != len(set(mix_times)):
        mix_errors.append("timestamps are not unique")
    checks.append(
        ValidationCheck(
            "generation_mix",
            "failed" if mix_errors or not mix else "passed",
            (
                "; ".join(mix_errors[:10])
                if mix_errors
                else f"Validated {len(mix):,} generation-mix rows."
            ),
            {"rows": len(mix), "errors": len(mix_errors)},
        )
    )

    all_times = [*demand_times, *mix_times]
    coverage = snapshot["coverage"]
    coverage_matches = bool(all_times) and coverage.get("start") == min(
        all_times
    ) and coverage.get("end") == max(all_times)
    checks.append(
        ValidationCheck(
            "coverage",
            "passed" if coverage_matches else "failed",
            (
                "Coverage matches the emitted series."
                if coverage_matches
                else "Coverage does not match the earliest and latest series timestamps."
            ),
        )
    )

    stale_series: dict[str, float] = {}
    freshness_status: Status = "passed"
    for name, timestamps in (("demand", demand_times), ("generation_mix", mix_times)):
        if not timestamps:
            freshness_status = "failed"
            stale_series[name] = float("inf")
            continue
        latest = max(_parse_timestamp(timestamp) for timestamp in timestamps)
        age = checked - latest
        age_hours = round(age.total_seconds() / 3600, 1)
        stale_series[name] = age_hours
        if age < -timedelta(hours=1) or age > fail_after:
            freshness_status = "failed"
        elif age > warn_after and freshness_status != "failed":
            freshness_status = "warning"
    checks.append(
        ValidationCheck(
            "freshness",
            freshness_status,
            (
                "All series are within the expected freshness window."
                if freshness_status == "passed"
                else "One or more series are older than the preferred refresh window."
            ),
            {"age_hours": stale_series},
        )
    )

    total_missing = demand_missing + mix_missing
    total_possible = demand_possible + mix_possible
    missing_ratio = total_missing / total_possible if total_possible else 1.0
    if missing_ratio > fail_missing_ratio:
        completeness_status: Status = "failed"
    elif missing_ratio > warn_missing_ratio:
        completeness_status = "warning"
    else:
        completeness_status = "passed"
    checks.append(
        ValidationCheck(
            "completeness",
            completeness_status,
            f"{missing_ratio:.1%} of expected values are missing.",
            {
                "missing_values": total_missing,
                "expected_values": total_possible,
                "missing_ratio": round(missing_ratio, 4),
            },
        )
    )

    kpis = snapshot["kpis"]
    kpi_errors: list[str] = []
    for name in REQUIRED_KPIS:
        metric = kpis.get(name)
        if not isinstance(metric, Mapping):
            kpi_errors.append(f"{name} is missing")
            continue
        value = metric.get("value")
        timestamp = metric.get("timestamp")
        if value is None or timestamp is None:
            if value is not None or timestamp is not None:
                kpi_errors.append(f"{name} must pair its value and timestamp")
        elif not _is_number(value):
            kpi_errors.append(f"{name}.value is invalid")
        else:
            try:
                _parse_timestamp(timestamp)
            except (TypeError, ValueError):
                kpi_errors.append(f"{name}.timestamp is invalid")
        if name == "renewable_share_pct" and _is_number(value):
            if not 0 <= value <= 100:
                kpi_errors.append("renewable_share_pct must be between 0 and 100")
    checks.append(
        ValidationCheck(
            "kpis",
            "failed" if kpi_errors else "passed",
            "; ".join(kpi_errors) if kpi_errors else "All KPI payloads are valid.",
        )
    )

    processing = snapshot["processing"]
    observed_operating = sum(
        1
        for row in snapshot["demand"]
        if isinstance(row, Mapping)
        for demand_field in DEMAND_FIELDS
        if row.get(demand_field) is not None
    )
    observed_fuel = sum(
        value is not None
        for row in mix
        if isinstance(row, Mapping) and isinstance(row.get("fuels"), Mapping)
        for value in row["fuels"].values()
    )
    counts_match = (
        processing.get("operating_records") == observed_operating
        and processing.get("fuel_records") == observed_fuel
    )
    checks.append(
        ValidationCheck(
            "record_counts",
            "passed" if counts_match else "failed",
            (
                "Processing counts reconcile with emitted values."
                if counts_match
                else "Processing counts do not reconcile with emitted values."
            ),
            {
                "operating_values": observed_operating,
                "fuel_values": observed_fuel,
            },
        )
    )

    return ValidationReport(subject, _iso(checked), tuple(checks))
