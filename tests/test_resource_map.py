"""Tests for NASA POWER state aggregation and resource-map validation."""

import unittest

from pipeline.resource_map import aggregate_resources
from pipeline.validate import validate_resource_map


def state_features() -> list[dict]:
    features = []
    for index in range(50):
        lon = -124 + index * 0.2
        features.append(
            {
                "type": "Feature",
                "properties": {"STATEFP": f"{index + 1:02d}", "STUSPS": f"{index:02d}", "NAME": f"State {index:02d}"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[lon, 35], [lon + 0.1, 35], [lon + 0.1, 35.1], [lon, 35.1], [lon, 35]]],
                },
            }
        )
    return features


def nasa_document() -> dict:
    datasets = []
    for metric, parameter in (("solar", "ALLSKY_SFC_SW_DWN"), ("wind", "WS50M")):
        features = []
        for index in range(50):
            lon = -124 + index * 0.2 + 0.05
            features.append(
                {
                    "geometry": {"type": "Point", "coordinates": [lon, 35.05]},
                    "properties": {"parameter": {parameter: {"ANN": 2 + index / 10}}},
                }
            )
        datasets.append({"metric": metric, "parameter": parameter, "features": features})
    return {"datasets": datasets}


class ResourceMapTests(unittest.TestCase):
    def test_aggregates_and_ranks_fifty_states(self) -> None:
        artifact = aggregate_resources(
            nasa_document(), state_features(), generated_at="2026-08-27T12:00:00Z"
        )

        self.assertEqual(len(artifact["states"]), 50)
        self.assertEqual(artifact["states"][-1]["metrics"]["solar"]["rank"], 1)
        self.assertTrue(validate_resource_map(artifact).passed)

    def test_validation_rejects_missing_state(self) -> None:
        artifact = aggregate_resources(nasa_document(), state_features())
        artifact["states"].pop()

        self.assertFalse(validate_resource_map(artifact).passed)


if __name__ == "__main__":
    unittest.main()
