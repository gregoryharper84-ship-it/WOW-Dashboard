from services.wow_engineering_telemetry import build_opentelemetry_trace_sink


def test_opentelemetry_trace_sink_accepts_governed_engineering_event_without_exporter():
    sink = build_opentelemetry_trace_sink()
    sink(
        {
            "stage": "SANDBOX_TEST",
            "status": "COMPLETED",
            "issue_id": "WOW-ENG-OTEL-001",
            "attempt_count": 2,
            "detail": "synthetic regression trace",
        }
    )
