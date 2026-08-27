"""Tests for the NASA POWER extraction client."""

import unittest
from unittest.mock import patch

from pipeline.nasa_power_client import fetch_climatology


class NasaPowerClientTests(unittest.TestCase):
    @patch("pipeline.nasa_power_client._get_json")
    def test_fetches_each_metric_for_three_us_extents(self, get_json) -> None:
        get_json.return_value = {
            "header": {"start": "20010101", "end": "20201231"},
            "parameters": {},
            "features": [{"type": "Feature"}],
        }

        document = fetch_climatology()

        self.assertGreater(get_json.call_count, 6)
        self.assertEqual(len(document["datasets"]), get_json.call_count)
        self.assertEqual({item["metric"] for item in document["datasets"]}, {"solar", "wind"})
        self.assertNotIn("api_key", str(document).lower())


if __name__ == "__main__":
    unittest.main()
