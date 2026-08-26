"""Tests for the Grid Pulse static artifact builder."""

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from pipeline.build import BuildError, build_artifacts
from pipeline.validate import ValidationError


NOW = datetime(2026, 8, 25, 13, tzinfo=timezone.utc)


def source_documents(region: str = "MISO") -> tuple[dict, dict]:
    operating_records = []
    fuel_records = []
    for hour in range(8, 13):
        period = f"2026-08-25T{hour:02d}"
        for type_code, value in (
            ("D", 100 + hour),
            ("DF", 102 + hour),
            ("NG", 95 + hour),
            ("TI", -5),
        ):
            operating_records.append(
                {
                    "period": period,
                    "respondent": region,
                    "respondent-name": f"{region} test region",
                    "type": type_code,
                    "value": str(value),
                }
            )
        for code, name, value in (
            ("SUN", "Solar", 20),
            ("WND", "Wind", 30),
            ("NG", "Natural Gas", 50),
        ):
            fuel_records.append(
                {
                    "period": period,
                    "respondent": region,
                    "respondent-name": f"{region} test region",
                    "fueltype": code,
                    "type-name": name,
                    "value": str(value),
                }
            )

    common = {
        "source": "EIA test data",
        "pulled_at": "2026-08-25T12:30:00Z",
        "regions": [region],
    }
    operating = {
        **common,
        "api_route": "/v2/electricity/rto/region-data/data/",
        "record_count": len(operating_records),
        "records": operating_records,
    }
    fuel = {
        **common,
        "api_route": "/v2/electricity/rto/fuel-type-data/data/",
        "record_count": len(fuel_records),
        "records": fuel_records,
    }
    return operating, fuel


def write_sources(directory: Path, operating: dict, fuel: dict) -> None:
    directory.mkdir(parents=True)
    (directory / "region-data.json").write_text(
        json.dumps(operating), encoding="utf-8"
    )
    (directory / "fuel-type-data.json").write_text(
        json.dumps(fuel), encoding="utf-8"
    )


class BuildArtifactsTests(unittest.TestCase):
    def test_writes_manifest_and_validated_region_artifact(self) -> None:
        with TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            source_dir = root / "source"
            output_dir = root / "output"
            operating, fuel = source_documents()
            write_sources(source_dir, operating, fuel)

            manifest = build_artifacts(
                source_dir,
                output_dir,
                required_regions=("miso",),
                now=NOW,
            )

            manifest_on_disk = json.loads(
                (output_dir / "manifest.json").read_text(encoding="utf-8")
            )
            region_on_disk = json.loads(
                (output_dir / "regions" / "miso.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(manifest, manifest_on_disk)
            self.assertEqual(manifest["pipeline"]["status"], "passed")
            self.assertEqual(manifest["pipeline"]["rows_processed"]["source"], 35)
            self.assertEqual(manifest["pipeline"]["rows_processed"]["total"], 35)
            self.assertEqual(manifest["regions"][0]["data_file"], "regions/miso.json")
            self.assertEqual(region_on_disk["quality"]["status"], "passed")

    def test_does_not_publish_when_source_validation_fails(self) -> None:
        with TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            source_dir = root / "source"
            output_dir = root / "output"
            operating, fuel = source_documents()
            operating["record_count"] += 1
            write_sources(source_dir, operating, fuel)

            with self.assertRaises(ValidationError):
                build_artifacts(
                    source_dir,
                    output_dir,
                    required_regions=("miso",),
                    now=NOW,
                )

            self.assertFalse((output_dir / "manifest.json").exists())

    def test_requires_configured_regions(self) -> None:
        with TemporaryDirectory() as temporary_name:
            root = Path(temporary_name)
            source_dir = root / "source"
            output_dir = root / "output"
            operating, fuel = source_documents()
            write_sources(source_dir, operating, fuel)

            with self.assertRaisesRegex(BuildError, "caiso"):
                build_artifacts(
                    source_dir,
                    output_dir,
                    required_regions=("miso", "caiso"),
                    now=NOW,
                )

            self.assertFalse((output_dir / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
