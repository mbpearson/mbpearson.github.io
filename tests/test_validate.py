"""Tests for Grid Pulse data-quality validation."""

import copy
import unittest
from datetime import datetime, timedelta, timezone

from pipeline.transform import transform_region
from pipeline.validate import (
    ValidationError,
    require_valid,
    validate_snapshot,
    validate_source_document,
)


NOW = datetime(2026, 8, 25, 13, tzinfo=timezone.utc)


def make_snapshot() -> dict:
    operating = []
    fuels = []
    for hour in range(8, 13):
        period = f"2026-08-25T{hour:02d}"
        for type_code, value in (
            ("D", 100 + hour),
            ("DF", 102 + hour),
            ("NG", 95 + hour),
            ("TI", -5),
        ):
            operating.append(
                {
                    "period": period,
                    "respondent": "MISO",
                    "respondent-name": "Midcontinent Independent System Operator",
                    "type": type_code,
                    "value": str(value),
                }
            )
        for code, name, value in (
            ("SUN", "Solar", 20),
            ("WND", "Wind", 30),
            ("NG", "Natural Gas", 50),
        ):
            fuels.append(
                {
                    "period": period,
                    "respondent": "MISO",
                    "respondent-name": "Midcontinent Independent System Operator",
                    "fueltype": code,
                    "type-name": name,
                    "value": str(value),
                }
            )
    return transform_region(
        "MISO", operating, fuels, generated_at="2026-08-25T12:30:00Z"
    )


class SourceValidationTests(unittest.TestCase):
    def test_accepts_sanitized_source_document(self) -> None:
        document = {
            "api_route": "/v2/electricity/rto/region-data/data/",
            "pulled_at": "2026-08-25T12:30:00Z",
            "record_count": 1,
            "records": [
                {
                    "period": "2026-08-25T12",
                    "respondent": "MISO",
                    "type": "D",
                    "value": "100",
                }
            ],
        }

        report = validate_source_document(
            document, expected_route="region-data", now=NOW
        )

        self.assertTrue(report.passed)
        self.assertEqual(report.status, "passed")

    def test_rejects_count_mismatch_and_request_echo(self) -> None:
        document = {
            "api_route": "/v2/electricity/rto/region-data/data/",
            "pulled_at": "2026-08-25T12:30:00Z",
            "record_count": 2,
            "request": {"api_key": "must-not-be-saved"},
            "records": [
                {
                    "period": "2026-08-25T12",
                    "respondent": "MISO",
                    "type": "D",
                    "value": "100",
                }
            ],
        }

        report = validate_source_document(
            document, expected_route="region-data", now=NOW
        )

        self.assertFalse(report.passed)
        failed = {check.name for check in report.checks if check.status == "failed"}
        self.assertIn("secret_safety", failed)
        self.assertIn("source_record_count", failed)


class SnapshotValidationTests(unittest.TestCase):
    def test_accepts_complete_fresh_snapshot(self) -> None:
        report = validate_snapshot(make_snapshot(), now=NOW)

        self.assertTrue(report.passed)
        self.assertEqual(report.status, "passed")
        self.assertEqual(report.to_dict()["summary"]["failed"], 0)

    def test_warns_when_data_exceeds_preferred_age(self) -> None:
        report = validate_snapshot(
            make_snapshot(),
            now=NOW + timedelta(hours=14),
            warn_after=timedelta(hours=12),
            fail_after=timedelta(hours=48),
        )

        self.assertTrue(report.passed)
        self.assertEqual(report.status, "warning")

    def test_fails_when_data_is_stale(self) -> None:
        report = validate_snapshot(
            make_snapshot(), now=NOW + timedelta(hours=72)
        )

        self.assertFalse(report.passed)
        with self.assertRaises(ValidationError):
            require_valid(report)

    def test_detects_coverage_and_count_mismatches(self) -> None:
        snapshot = make_snapshot()
        snapshot["coverage"]["end"] = "2026-08-25T10:00:00Z"
        snapshot["processing"]["fuel_records"] += 1

        report = validate_snapshot(snapshot, now=NOW)

        failed = {check.name for check in report.checks if check.status == "failed"}
        self.assertIn("coverage", failed)
        self.assertIn("record_counts", failed)

    def test_fails_excessive_missing_data(self) -> None:
        snapshot = copy.deepcopy(make_snapshot())
        for row in snapshot["demand"]:
            row["actual_mwh"] = None
            row["forecast_mwh"] = None

        report = validate_snapshot(
            snapshot,
            now=NOW,
            warn_missing_ratio=0.1,
            fail_missing_ratio=0.2,
        )

        failed = {check.name for check in report.checks if check.status == "failed"}
        self.assertIn("completeness", failed)
        self.assertIn("record_counts", failed)


if __name__ == "__main__":
    unittest.main()
