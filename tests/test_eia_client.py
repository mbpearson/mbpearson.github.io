"""Tests for the Grid Pulse EIA API client."""

from __future__ import annotations

import io
import json
import os
import unittest
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

from pipeline.eia_client import (
    EIAClient,
    EIAClientError,
    load_api_key,
    write_documents,
)


NOW = datetime(2026, 8, 25, 13, 42, tzinfo=timezone.utc)


class FakeResponse(io.BytesIO):
    pass


def response(payload: dict) -> FakeResponse:
    return FakeResponse(json.dumps(payload).encode("utf-8"))


class EIAClientTests(unittest.TestCase):
    def test_paginates_with_deterministic_sort_and_sanitizes_documents(self) -> None:
        requests = []

        def opener(request, *, timeout):
            self.assertEqual(timeout, 10)
            requests.append(request)
            query = parse_qs(urlparse(request.full_url).query)
            offset = int(query["offset"][0])
            rows = [
                {
                    "period": f"2026-08-25T{offset + index:02d}",
                    "respondent": "MISO",
                    "value": str(100 + offset + index),
                }
                for index in range(min(2, 3 - offset))
            ]
            return response({"response": {"total": "3", "data": rows}})

        client = EIAClient(
            "super-secret",
            page_size=2,
            timeout=10,
            opener=opener,
            sleeper=lambda _delay: None,
        )
        documents = client.fetch_grid_pulse(
            region_labels=("MISO",), hours=24, now=NOW
        )

        self.assertEqual(len(requests), 4)
        self.assertEqual(documents["region-data"]["record_count"], 3)
        self.assertEqual(documents["fuel-type-data"]["record_count"], 3)
        serialized = json.dumps(documents)
        self.assertNotIn("super-secret", serialized)
        self.assertNotIn("request", documents["region-data"])
        self.assertEqual(
            documents["region-data"]["requested_period"],
            {"start": "2026-08-24T13", "end": "2026-08-25T13"},
        )

        first_query = parse_qs(urlparse(requests[0].full_url).query)
        self.assertEqual(first_query["facets[respondent][]"], ["MISO"])
        self.assertEqual(first_query["sort[0][column]"], ["period"])
        self.assertEqual(first_query["sort[1][column]"], ["respondent"])

    def test_retries_throttled_request_using_retry_after(self) -> None:
        attempts = 0
        delays = []

        def opener(request, **_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                headers = Message()
                headers["Retry-After"] = "2"
                raise HTTPError(
                    request.full_url,
                    429,
                    "Too Many Requests",
                    headers,
                    None,
                )
            return response(
                {
                    "response": {
                        "total": "1",
                        "data": [{"period": "2026-08-25T12", "value": "1"}],
                    }
                }
            )

        client = EIAClient(
            "secret",
            opener=opener,
            sleeper=delays.append,
            max_retries=1,
        )
        records = client.fetch_dataset(
            "region-data",
            ("MISO",),
            start=NOW.replace(hour=12),
            end=NOW.replace(hour=13),
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(attempts, 2)
        self.assertEqual(delays, [2.0])

    def test_redacts_key_from_api_errors(self) -> None:
        def opener(_request, **_kwargs):
            return response({"error": "invalid key secret-value"})

        client = EIAClient("secret-value", opener=opener, max_retries=0)
        with self.assertRaisesRegex(EIAClientError, r"\[REDACTED\]") as raised:
            client.fetch_dataset(
                "region-data",
                ("MISO",),
                start=NOW.replace(hour=12),
                end=NOW.replace(hour=13),
            )
        self.assertNotIn("secret-value", str(raised.exception))

    def test_rejects_invalid_windows_and_regions(self) -> None:
        client = EIAClient("secret", opener=lambda *_args, **_kwargs: None)
        with self.assertRaisesRegex(ValueError, "start must be earlier"):
            client.fetch_dataset(
                "region-data", ("MISO",), start=NOW, end=NOW
            )
        with self.assertRaisesRegex(ValueError, "Unsupported dashboard regions"):
            client.fetch_grid_pulse(region_labels=("ERCOT",), now=NOW)


class KeyAndOutputTests(unittest.TestCase):
    def test_environment_key_takes_precedence(self) -> None:
        with TemporaryDirectory() as temporary_name:
            key_file = Path(temporary_name) / "key"
            key_file.write_text("file-key", encoding="utf-8")
            with patch.dict(os.environ, {"EIA_API_KEY": "environment-key"}):
                self.assertEqual(load_api_key(key_file), "environment-key")

    def test_reads_assignment_style_key_file(self) -> None:
        with TemporaryDirectory() as temporary_name:
            key_file = Path(temporary_name) / "key"
            key_file.write_text('EIA_API_KEY="file-key"\n', encoding="utf-8")
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(load_api_key(key_file), "file-key")

    def test_writes_both_documents_without_secret_fields(self) -> None:
        documents = {
            route: {
                "api_route": f"/v2/electricity/rto/{route}/data/",
                "record_count": 1,
                "records": [{"value": "1"}],
            }
            for route in ("region-data", "fuel-type-data")
        }
        with TemporaryDirectory() as temporary_name:
            output_dir = Path(temporary_name) / "source"
            write_documents(output_dir, documents)

            for route in documents:
                saved = json.loads(
                    (output_dir / f"{route}.json").read_text(encoding="utf-8")
                )
                self.assertEqual(saved, documents[route])


if __name__ == "__main__":
    unittest.main()
