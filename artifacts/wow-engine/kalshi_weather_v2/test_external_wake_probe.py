"""Network-free safety and retry tests for the external Kalshi wake probe."""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import external_wake_probe as probe

HEALTH = {
    "status": "ok",
    "service": "KALSHI_WEATHER_V2",
    "database_status": "AVAILABLE",
    "database_diagnostic": "DATABASE_READ_VERIFIED",
    "can_execute": False,
    "probability_publishable": False,
}
READY = {
    "mode": "SHADOW",
    "trigger_installed": True,
    "startup_enabled": True,
    "can_execute": False,
    "probability_publishable": False,
}


class WakeProbeTests(unittest.TestCase):
    def test_healthy_readiness_no_writer(self):
        seen = []
        def reader(path):
            seen.append(path)
            return HEALTH if path == "/health" else READY
        with redirect_stdout(io.StringIO()):
            self.assertTrue(probe.run(reader=reader, pause=lambda _: None))
        self.assertEqual(seen, ["/health", "/collector/readiness"])

    def test_fails_on_database_unavailable(self):
        with self.assertRaisesRegex(probe.ProbeError, "WEATHER_DATABASE_READ_NOT_VERIFIED"):
            probe.verify({**HEALTH, "database_status": "UNAVAILABLE"}, READY)

    def test_fails_on_unauthorized_publication(self):
        with self.assertRaisesRegex(probe.ProbeError, "WEATHER_PROBABILITY_SAFETY_INVALID"):
            probe.verify(HEALTH, {**READY, "can_execute": True})

    def test_fails_on_disabled_collector(self):
        with self.assertRaisesRegex(probe.ProbeError, "WEATHER_COLLECTOR_NOT_CONFIGURED"):
            probe.verify(HEALTH, {**READY, "startup_enabled": False})

    def test_retry_on_temporary_cold_start(self):
        calls = []
        delays = []
        def reader(path):
            calls.append(path)
            if len(calls) == 1:
                raise TimeoutError("transient")
            return HEALTH if path == "/health" else READY
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertTrue(probe.run(reader=reader, pause=delays.append))
        self.assertEqual(delays, [8.0])
        self.assertEqual(calls, ["/health", "/health", "/collector/readiness"])

    def test_fail_closed_after_three_attempts(self):
        calls = []
        def bad_reader(path):
            calls.append(path)
            raise ConnectionError("secrets must never be printed")
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            self.assertFalse(probe.run(reader=bad_reader, pause=lambda _: None))
        self.assertEqual(len(calls), 3)
        self.assertNotIn("secrets", stderr.getvalue())

    def test_redirect_handler_refuses_all_locations_without_followup_get(self):
        from urllib.request import Request
        request = Request(probe.BASE_URL + "/health", method="GET")
        handler = probe._RejectRedirects()
        for destination in (
            "https://example.org/collector/readiness",
            probe.BASE_URL + "/unapproved",
            probe.BASE_URL + "/collector/readiness",
        ):
            with self.subTest(destination=destination):
                self.assertIsNone(handler.redirect_request(
                    request, None, 302, "Found", {"Location": destination}, destination
                ))

    def test_installed_opener_refuses_redirect_response_before_followup_get(self):
        """Exercise the real opener with a fake HTTPS transport, without network."""
        from io import BytesIO
        from urllib.error import HTTPError
        from urllib.request import HTTPSHandler
        from urllib.response import addinfourl

        self.assertTrue(any(isinstance(h, probe._RejectRedirects)
                            for h in probe.HTTP_CLIENT.handlers))
        transport = next(h for h in probe.HTTP_CLIENT.handlers
                         if isinstance(h, HTTPSHandler))
        for destination in (
            "https://example.net/health",
            probe.BASE_URL + "/unapproved",
            probe.BASE_URL + "/collector/readiness",
        ):
            visits = []
            def fake_get(request):
                visits.append(request.full_url)
                result = addinfourl(
                    BytesIO(b""), {"Location": destination},
                    request.full_url, code=302,
                )
                result.msg = "Found"
                return result

            with self.subTest(destination=destination):
                with patch.object(transport, "https_open", side_effect=fake_get):
                    with self.assertRaises(HTTPError) as error:
                        probe.read_json("/health")
                self.assertEqual(error.exception.code, 302)
                self.assertEqual(visits, [probe.BASE_URL + "/health"])

    def test_proxy_url_mismatch_is_rejected_even_on_same_host(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b"{}"
        response.geturl.return_value = probe.BASE_URL + "/unapproved"
        with patch.object(probe.HTTP_CLIENT, "open", return_value=response):
            with self.assertRaisesRegex(probe.ProbeError, "PROBE_RESPONSE_ORIGIN_CHANGED"):
                probe.read_json("/health")

    def test_reject_unexpected_path_without_network(self):
        with self.assertRaises(probe.ProbeError), patch.object(probe.HTTP_CLIENT, "open") as fn:
            probe.read_json("/internal/kalshi-weather/v17/empirical-cohort/cron-run")
        fn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
