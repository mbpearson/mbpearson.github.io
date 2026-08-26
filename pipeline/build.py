"""Build validated, static Grid Pulse artifacts from sanitized EIA data."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Mapping, Sequence

from pipeline.transform import TransformError, transform_documents
from pipeline.validate import (
    ValidationError,
    ValidationReport,
    require_valid,
    validate_snapshot,
    validate_source_document,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = PROJECT_ROOT / "grid-pulse" / "data" / "sample"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "grid-pulse" / "data"
DEFAULT_REGIONS = ("caiso", "miso", "pjm")


class BuildError(RuntimeError):
    """Raised when static artifacts cannot be built safely."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build validated JSON artifacts for the Grid Pulse frontend."
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        help="Directory containing region-data.json and fuel-type-data.json.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where manifest.json and regions/*.json are written.",
    )
    parser.add_argument(
        "--required-regions",
        nargs="+",
        default=list(DEFAULT_REGIONS),
        help="Region slugs that must be present before artifacts are published.",
    )
    parser.add_argument(
        "--warn-after-hours",
        type=float,
        default=12,
        help="Age at which an otherwise valid time series receives a warning.",
    )
    parser.add_argument(
        "--fail-after-hours",
        type=float,
        default=48,
        help="Age at which a time series fails validation.",
    )
    return parser.parse_args()


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as source_file:
            document = json.load(source_file)
    except FileNotFoundError as exc:
        raise BuildError(f"Required source file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BuildError(
            f"Source file is not valid JSON: {path} (line {exc.lineno})"
        ) from exc
    except OSError as exc:
        raise BuildError(f"Could not read source file {path}: {exc}") from exc

    if not isinstance(document, dict):
        raise BuildError(f"Source file must contain a JSON object: {path}")
    return document


def _write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8", newline="\n") as output_file:
            json.dump(
                document,
                output_file,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            )
            output_file.write("\n")
    except (OSError, TypeError, ValueError) as exc:
        raise BuildError(f"Could not write JSON artifact {path}: {exc}") from exc


def _quality_summary(reports: Sequence[ValidationReport]) -> dict[str, Any]:
    counts = {
        status: sum(
            check.status == status
            for report in reports
            for check in report.checks
        )
        for status in ("passed", "warning", "failed")
    }
    status = "failed" if counts["failed"] else "warning" if counts["warning"] else "passed"
    return {"status": status, **counts, "total": sum(counts.values())}


def _check_details(report: ValidationReport, name: str) -> dict[str, Any]:
    for check in report.checks:
        if check.name == name:
            return dict(check.details)
    return {}


def _build_manifest(
    snapshots: Mapping[str, Mapping[str, Any]],
    source_reports: Mapping[str, ValidationReport],
    region_reports: Mapping[str, ValidationReport],
    *,
    built_at: datetime,
) -> dict[str, Any]:
    all_reports = [*source_reports.values(), *region_reports.values()]
    region_entries = []
    operating_rows = 0
    fuel_rows = 0
    demand_points = 0
    mix_points = 0

    for slug, snapshot in sorted(snapshots.items()):
        processing = snapshot["processing"]
        report = region_reports[slug]
        operating_rows += int(processing["operating_records"])
        fuel_rows += int(processing["fuel_records"])
        demand_points += len(snapshot["demand"])
        mix_points += len(snapshot["generation_mix"])
        region_entries.append(
            {
                **dict(snapshot["region"]),
                "data_file": f"regions/{slug}.json",
                "coverage": dict(snapshot["coverage"]),
                "quality": {
                    "status": report.status,
                    "summary": report.to_dict()["summary"],
                    "freshness": _check_details(report, "freshness"),
                    "completeness": _check_details(report, "completeness"),
                },
            }
        )

    coverage_starts = [
        str(snapshot["coverage"]["start"]) for snapshot in snapshots.values()
    ]
    coverage_ends = [
        str(snapshot["coverage"]["end"]) for snapshot in snapshots.values()
    ]
    generated_times = [str(snapshot["generated_at"]) for snapshot in snapshots.values()]
    source_rows = sum(
        int(_check_details(report, "source_record_count").get("records", 0))
        for report in source_reports.values()
    )

    return {
        "schema_version": 1,
        "generated_at": max(generated_times),
        "built_at": _iso(built_at),
        "source": {
            "name": "U.S. Energy Information Administration (EIA), Form EIA-930",
            "routes": [
                "/v2/electricity/rto/region-data/data/",
                "/v2/electricity/rto/fuel-type-data/data/",
            ],
        },
        "window": {"start": min(coverage_starts), "end": max(coverage_ends)},
        "regions": region_entries,
        "pipeline": {
            "status": _quality_summary(all_reports)["status"],
            "rows_processed": {
                "source": source_rows,
                "operating": operating_rows,
                "fuel": fuel_rows,
                "total": operating_rows + fuel_rows,
            },
            "points_emitted": {
                "demand": demand_points,
                "generation_mix": mix_points,
            },
            "quality_checks": _quality_summary(all_reports),
            "source_validation": {
                name: report.to_dict() for name, report in source_reports.items()
            },
            "region_validation": {
                slug: report.to_dict() for slug, report in region_reports.items()
            },
        },
    }


def build_artifacts(
    source_dir: Path,
    output_dir: Path,
    *,
    required_regions: Sequence[str] = DEFAULT_REGIONS,
    now: datetime | None = None,
    warn_after: timedelta = timedelta(hours=12),
    fail_after: timedelta = timedelta(hours=48),
) -> dict[str, Any]:
    """Validate, transform, and publish a complete set of static JSON files."""
    build_time = now or datetime.now(timezone.utc)
    if build_time.tzinfo is None:
        raise BuildError("Build time must be timezone-aware.")
    build_time = build_time.astimezone(timezone.utc)

    source_dir = source_dir.resolve()
    output_dir = output_dir.resolve()
    operating_document = _load_json(source_dir / "region-data.json")
    fuel_document = _load_json(source_dir / "fuel-type-data.json")

    source_reports = {
        "region-data": validate_source_document(
            operating_document,
            expected_route="region-data",
            now=build_time,
        ),
        "fuel-type-data": validate_source_document(
            fuel_document,
            expected_route="fuel-type-data",
            now=build_time,
        ),
    }
    for report in source_reports.values():
        require_valid(report)

    snapshots = transform_documents(operating_document, fuel_document)
    missing_regions = sorted(set(required_regions) - set(snapshots))
    if missing_regions:
        raise BuildError(
            f"Required regional data is missing: {', '.join(missing_regions)}"
        )

    region_reports = {
        slug: validate_snapshot(
            snapshot,
            now=build_time,
            warn_after=warn_after,
            fail_after=fail_after,
        )
        for slug, snapshot in snapshots.items()
    }
    for report in region_reports.values():
        require_valid(report)

    manifest = _build_manifest(
        snapshots,
        source_reports,
        region_reports,
        built_at=build_time,
    )

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix=".grid-pulse-build-", dir=output_dir.parent
    ) as staging_name:
        staging_dir = Path(staging_name)
        staged_regions = staging_dir / "regions"
        for slug, snapshot in sorted(snapshots.items()):
            artifact = {
                **snapshot,
                "quality": region_reports[slug].to_dict(),
            }
            _write_json(staged_regions / f"{slug}.json", artifact)
        _write_json(staging_dir / "manifest.json", manifest)

        regions_dir = output_dir / "regions"
        regions_dir.mkdir(parents=True, exist_ok=True)
        for slug in sorted(snapshots):
            os.replace(
                staged_regions / f"{slug}.json",
                regions_dir / f"{slug}.json",
            )
        os.replace(staging_dir / "manifest.json", output_dir / "manifest.json")

    return manifest


def main() -> int:
    args = parse_args()
    try:
        manifest = build_artifacts(
            args.source_dir,
            args.output_dir,
            required_regions=args.required_regions,
            warn_after=timedelta(hours=args.warn_after_hours),
            fail_after=timedelta(hours=args.fail_after_hours),
        )
    except (BuildError, TransformError, ValidationError, ValueError) as exc:
        print(f"Build failed: {exc}", file=sys.stderr)
        return 1

    rows = manifest["pipeline"]["rows_processed"]["total"]
    status = manifest["pipeline"]["status"]
    print(
        f"Built {len(manifest['regions'])} regional artifacts from "
        f"{rows:,} records ({status})."
    )
    print(f"Manifest: {(args.output_dir.resolve() / 'manifest.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
