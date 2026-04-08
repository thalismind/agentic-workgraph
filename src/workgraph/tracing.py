from __future__ import annotations

from typing import Any

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace import StatusCode


class StoreSpanExporter(SpanExporter):
    def __init__(self, store) -> None:
        self.store = store

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        for span in spans:
            run_id = span.attributes.get("workgraph.run.id")
            if not run_id:
                continue
            self.store.add_span(
                run_id,
                {
                    "name": span.name,
                    "trace_id": format(span.context.trace_id, "032x"),
                    "span_id": format(span.context.span_id, "016x"),
                    "parent_span_id": format(span.parent.span_id, "016x") if span.parent else None,
                    "start_time": span.start_time,
                    "end_time": span.end_time,
                    "status": span.status.status_code.name.lower()
                    if span.status.status_code is not StatusCode.UNSET
                    else "unset",
                    "attributes": dict(span.attributes),
                },
            )
        return SpanExportResult.SUCCESS


class Telemetry:
    def __init__(self, store, *, service_name: str = "workgraph") -> None:
        provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
        provider.add_span_processor(SimpleSpanProcessor(StoreSpanExporter(store)))
        self.provider = provider
        self.tracer = provider.get_tracer("workgraph")

    def get_tracer(self):
        return self.tracer
