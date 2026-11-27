"""OpenTelemetry wiring (optional extra `obs`). Traces carry the correlation id; prompts and PII are never attributes."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def init(app, endpoint: str) -> bool:
    if not endpoint:
        return False
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning("AG_OTEL_ENDPOINT set but the `obs` extra is not installed")
        return False
    provider = TracerProvider(resource=Resource.create({"service.name": "advisorgro-api"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))
    trace.set_tracer_provider(provider)
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,internal/metrics")
    except ImportError:
        pass
    return True
