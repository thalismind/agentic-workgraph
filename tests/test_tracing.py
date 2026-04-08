from __future__ import annotations

import sys
import types

import pytest

from workgraph.tracing import Telemetry


def test_telemetry_otlp_requires_exporter_when_endpoint_is_configured(monkeypatch):
    monkeypatch.setitem(sys.modules, "opentelemetry.exporter.otlp.proto.grpc.trace_exporter", None)

    with pytest.raises(ImportError):
        Telemetry(store=object(), endpoint="http://localhost:4317")


def test_telemetry_otlp_configures_exporter_when_available(monkeypatch):
    class FakeExporter:
        def __init__(self, endpoint: str) -> None:
            self.endpoint = endpoint

        def export(self, spans):
            return None

        def shutdown(self):
            return None

    module = types.ModuleType("trace_exporter")
    module.OTLPSpanExporter = FakeExporter
    monkeypatch.setitem(sys.modules, "opentelemetry.exporter.otlp.proto.grpc.trace_exporter", module)

    telemetry = Telemetry(store=object(), endpoint="http://localhost:4317")
    processors = telemetry.provider._active_span_processor._span_processors

    assert len(processors) == 2
