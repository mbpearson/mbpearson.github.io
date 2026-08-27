"""Tests for annual state EIA extraction."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from pipeline.eia_state_client import fetch_state_documents


class StubClient:
    def __init__(self, row: dict) -> None:
        self.row = row
        self.calls = []

    def _request_page(self, route, params):
        self.calls.append((route, params))
        return {"total": "1", "data": [self.row]}


class StateEIAClientTests(unittest.TestCase):
    def test_fetches_sanitized_annual_documents(self) -> None:
        generation = StubClient({
            "period": "2024", "location": "CA", "fueltypeid": "ALL", "generation": "10"
        })
        emissions = StubClient({
            "period": "2024", "stateid": "CA", "fuelid": "ALL", "co2-rate-lbs-mwh": "400"
        })
        documents = fetch_state_documents(
            "secret", start_year=2019, end_year=2024,
            now=datetime(2026, 1, 1, tzinfo=timezone.utc),
            generation_client=generation, emissions_client=emissions,
        )
        serialized = json.dumps(documents)
        self.assertNotIn("secret", serialized)
        self.assertEqual(documents["state-generation"]["record_count"], 1)
        generation_params = dict(generation.calls[0][1])
        self.assertEqual(generation_params["frequency"], "annual")
        self.assertEqual(generation_params["start"], 2019)
        self.assertEqual(generation_params["end"], 2024)
        self.assertEqual(generation_params["facets[sectorid][]"], "99")

    def test_rejects_reversed_window(self) -> None:
        with self.assertRaisesRegex(ValueError, "start_year"):
            fetch_state_documents("secret", start_year=2025, end_year=2024)


if __name__ == "__main__":
    unittest.main()
