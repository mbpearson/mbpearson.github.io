"""Tests for the Grid Pulse EIA transformation layer."""

import unittest

from pipeline.transform import TransformError, transform_documents, transform_region


class TransformRegionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.operating = [
            self.operating_row("2026-08-25T10", "D", "100"),
            self.operating_row("2026-08-25T10", "DF", "90"),
            self.operating_row("2026-08-25T11", "D", "120"),
            self.operating_row("2026-08-25T11", "DF", "126"),
            self.operating_row("2026-08-25T11", "NG", "115"),
            self.operating_row("2026-08-25T11", "TI", "-12"),
        ]
        self.fuels = [
            self.fuel_row("2026-08-25T11", "SUN", "30", "Solar"),
            self.fuel_row("2026-08-25T11", "WND", "20", "Wind"),
            self.fuel_row("2026-08-25T11", "NG", "50", "Natural Gas"),
            self.fuel_row("2026-08-25T11", "OTH", "-5", "Other"),
        ]

    @staticmethod
    def operating_row(period: str, type_code: str, value: str) -> dict[str, str]:
        return {
            "period": period,
            "respondent": "CISO",
            "respondent-name": "California Independent System Operator",
            "type": type_code,
            "value": value,
        }

    @staticmethod
    def fuel_row(
        period: str, code: str, value: str, name: str
    ) -> dict[str, str]:
        return {
            "period": period,
            "respondent": "CISO",
            "respondent-name": "California Independent System Operator",
            "fueltype": code,
            "type-name": name,
            "value": value,
        }

    def test_builds_frontend_snapshot_and_kpis(self) -> None:
        snapshot = transform_region(
            "CISO",
            self.operating,
            self.fuels,
            generated_at="2026-08-25T12:30:00Z",
        )

        self.assertEqual(snapshot["region"]["id"], "CAISO")
        self.assertEqual(snapshot["region"]["slug"], "caiso")
        self.assertEqual(snapshot["coverage"]["start"], "2026-08-25T10:00:00Z")
        self.assertEqual(snapshot["kpis"]["demand_mwh"]["value"], 120)
        self.assertEqual(snapshot["kpis"]["forecast_error_pct"]["value"], 5.0)
        self.assertEqual(snapshot["kpis"]["renewable_share_pct"]["value"], 50.0)
        self.assertEqual(snapshot["kpis"]["net_interchange_mwh"]["value"], -12)

    def test_aligns_operating_values_and_preserves_missing_data(self) -> None:
        snapshot = transform_region("CISO", self.operating, self.fuels)

        self.assertEqual(len(snapshot["demand"]), 2)
        self.assertIsNone(snapshot["demand"][0]["net_generation_mwh"])
        self.assertEqual(snapshot["demand"][1]["forecast_mwh"], 126)

    def test_pivots_generation_mix_and_preserves_negative_adjustments(self) -> None:
        snapshot = transform_region("CISO", self.operating, self.fuels)
        mix = snapshot["generation_mix"][0]

        self.assertEqual(mix["fuels"]["solar"], 30)
        self.assertEqual(mix["fuels"]["other"], -5)
        self.assertEqual(mix["total_mwh"], 95)

    def test_rejects_conflicting_duplicates(self) -> None:
        duplicate = self.operating_row("2026-08-25T11", "D", "121")

        with self.assertRaisesRegex(TransformError, "Conflicting duplicate"):
            transform_region("CISO", [*self.operating, duplicate], self.fuels)

    def test_rejects_invalid_numeric_values(self) -> None:
        invalid = self.operating_row("2026-08-25T12", "D", "unavailable")

        with self.assertRaisesRegex(TransformError, "Invalid numeric value"):
            transform_region("CISO", [*self.operating, invalid], self.fuels)


class TransformDocumentsTests(unittest.TestCase):
    def test_returns_snapshots_keyed_by_display_slug(self) -> None:
        operating = {
            "pulled_at": "2026-08-25T12:30:00+00:00",
            "records": [
                {
                    "period": "2026-08-25T11",
                    "respondent": "CISO",
                    "type": "D",
                    "value": "120",
                }
            ],
        }
        fuel = {"records": []}

        snapshots = transform_documents(operating, fuel)

        self.assertEqual(list(snapshots), ["caiso"])
        self.assertEqual(
            snapshots["caiso"]["generated_at"], "2026-08-25T12:30:00Z"
        )


if __name__ == "__main__":
    unittest.main()
