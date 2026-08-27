"""Tests for the combined EIA and NASA state artifact."""

from __future__ import annotations

import unittest

from pipeline.eia_state_client import STATE_CODES
from pipeline.state_energy import build_state_energy_artifact
from pipeline.validate import validate_state_energy


def fixtures():
    generation = []
    resources = []
    emissions = []
    for index, code in enumerate(STATE_CODES, start=1):
        for year, multiplier in ((2019, 1), (2024, 2)):
            fuels = {
                "ALL": 100 + index, "DPV": index / 10, "REN": 20 + index,
                "TSN": index * multiplier, "WND": (51 - index) * multiplier,
                "COW": 20, "NGO": 30, "NUC": 10, "PET": 2,
                "HYC": 8, "GEO": 1, "BIO": 3,
            }
            for fuel, value in fuels.items():
                generation.append({
                    "period": str(year), "location": code, "stateDescription": f"State {code}",
                    "fueltypeid": fuel, "generation": str(value),
                })
        emissions.append({"stateid": code, "co2-rate-lbs-mwh": str(300 + index)})
        resources.append({
            "postal_code": code, "name": f"State {code}", "hc_key": f"us-{code.lower()}",
            "metrics": {
                "solar": {"value": 3 + index / 20, "rank": 51 - index, "percentile": index * 2},
                "wind": {"value": 4 + index / 20, "rank": index, "percentile": 100 - index * 2},
            },
        })
    return (
        {"records": generation},
        {"records": emissions},
        {
            "source": {"nasa": "NASA POWER", "boundaries": "Census", "method": "weighted"},
            "metrics": {"solar": {"label": "Solar", "unit": "kWh/m²/day"}, "wind": {"label": "Wind", "unit": "m/s"}},
            "states": resources,
        },
    )


class StateEnergyTests(unittest.TestCase):
    def test_builds_fifty_joined_state_profiles(self) -> None:
        artifact = build_state_energy_artifact(*fixtures(), generated_at="2026-01-01T00:00:00Z")
        self.assertEqual(len(artifact["states"]), 50)
        self.assertTrue(validate_state_energy(artifact).passed)
        alabama = next(row for row in artifact["states"] if row["postal_code"] == "AL")
        self.assertEqual(alabama["current"]["year"], 2024)
        self.assertEqual(len(alabama["history"]), 2)
        self.assertAlmostEqual(
            sum(row["share_pct"] for row in alabama["current"]["generation_mix"]),
            100, delta=0.2,
        )
        self.assertIn("score", alabama["opportunity"]["solar"])

    def test_validation_rejects_missing_state(self) -> None:
        artifact = build_state_energy_artifact(*fixtures())
        artifact["states"].pop()
        self.assertFalse(validate_state_energy(artifact).passed)


if __name__ == "__main__":
    unittest.main()
