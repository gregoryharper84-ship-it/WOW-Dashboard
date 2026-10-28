"""Offline regression tests for the read-only verification doctor."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "doctor.py"
spec = importlib.util.spec_from_file_location("wow_verify_doctor", SCRIPT)
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)


class Response:
    def __init__(self, status=200, body=b'{"ok":true}'):
        self.status = status
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, size):
        return self.body[:size]


class DoctorTests(unittest.TestCase):
    def test_reads_exact_public_health_path_only(self):
        requested = []

        def opener(request, timeout):
            requested.append((request.full_url, request.get_method(), timeout))
            return Response()

        result = doctor.probe("https://wow-governed-probability-engine.onrender.com", opener=opener)
        self.assertEqual(
            requested,
            [("https://wow-governed-probability-engine.onrender.com/health/live", "GET", 15)],
        )
        self.assertEqual(result["status"], "BACKEND_LIVENESS_PASS")
        self.assertFalse(result["user_journey_verified"])
        self.assertFalse(result["deployment_sha_verified"])
        self.assertFalse(result["model_probability_verified"])
        self.assertFalse(result["can_execute"])
        self.assertIsNotNone(result["response_sha256"])
        self.assertFalse(result["response_body_recorded"])

    def test_unhealthy_http_is_not_pass(self):
        result = doctor.probe("https://example.com", opener=lambda request, timeout: Response(status=503))
        self.assertEqual(result["status"], "BACKEND_LIVENESS_FAIL")

    def test_transport_exception_preserved_as_unproven(self):
        def fail(*_args, **_kwargs):
            raise TimeoutError("no response")

        result = doctor.probe("https://example.com", opener=fail)
        self.assertEqual(result["status"], "LIVENESS_UNPROVEN")
        self.assertEqual(result["error_type"], "TimeoutError")

    def test_external_cleartext_and_credential_urls_rejected(self):
        for url in (
            "http://example.com",
            "https://username:secret@example.com",
            "https://example.com/path",
            "https://example.com?token=secret",
            "file:///etc/passwd",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                doctor.checked_origin(url)
        self.assertEqual(doctor.checked_origin("http://127.0.0.1:8042"), "http://127.0.0.1:8042")

    def test_feature_map_has_all_covered_journeys(self):
        features = ROOT / "features"
        text = (features / "README.md").read_text(encoding="utf-8")
        for name in (
            "backend-liveness.md",
            "canonical-prop-action.md",
            "live-chatgpt-golden-path.md",
            "llp-team-event.md",
            "engineering-recovery.md",
        ):
            self.assertTrue((features / name).is_file(), name)
            self.assertIn(name, text)

    def test_evidence_script_has_no_write_or_scoring_api_path(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn('method="POST"', source)
        self.assertNotIn('method="DELETE"', source)
        self.assertNotIn('method="PATCH"', source)
        self.assertNotIn('method="PUT"', source)
        self.assertIn('"user_journey_verified": False', source)


    def test_redirect_is_not_successful_liveness(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread

        class Handler(BaseHTTPRequestHandler):
            hits = []

            def do_GET(self):
                Handler.hits.append(self.path)
                if self.path == "/health/live":
                    self.send_response(302)
                    self.send_header("Location", "/unrelated")
                else:
                    self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *_):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                result = doctor.probe(f"http://127.0.0.1:{server.server_address[1]}")
                self.assertEqual(result["status"], "LIVENESS_UNPROVEN")
                self.assertEqual(result["http_status"], 302)
                self.assertEqual(result["error_type"], "HTTPError")
                self.assertEqual(Handler.hits, ["/health/live"])
            finally:
                server.shutdown()
                thread.join(timeout=3)

if __name__ == "__main__":
    unittest.main()
