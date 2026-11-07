"""
OpenTelemetry instrumentation for Nirvana microservices.

Initializes OTel SDK with OTLP exporter and instruments FastAPI/Requests
so that distributed traces (spans) are emitted for every incoming request
and downstream call. Traces can be consumed by Tracetest, SigNoz, or Grafana Tempo.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_OTEL_INITIALIZED = False


def init_telemetry(service_name: str = "whitelabel-service") -> None:
    """Initialize OpenTelemetry SDK with OTLP exporter.

    Reads OTEL_EXPORTER_OTLP_ENDPOINT from env (default: http://localhost:4317).
    Safe to call multiple times; subsequent calls are no-ops.
    """
    global _OTEL_INITIALIZED
    if _OTEL_INITIALIZED:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
        resource = Resource.create(
            {
                "service.name": service_name,
                "service.instance.id": os.getenv("HOSTNAME", "local"),
                "deployment.environment": os.getenv("ENVIRONMENT", "development"),
            }
        )

        provider = TracerProvider(resource=resource)
        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)

        _OTEL_INITIALIZED = True
        logger.info("OpenTelemetry initialized for service=%s endpoint=%s", service_name, endpoint)
    except ImportError as exc:
        logger.warning("OpenTelemetry not installed — telemetry disabled: %s", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("OpenTelemetry initialization failed: %s", exc)


def instrument_fastapi(app) -> None:
    """Instrument a FastAPI application with OpenTelemetry middleware.

    Safe no-op if opentelemetry-instrumentation-fastapi is not installed.
    """
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor().instrument_app(app)
        logger.info("FastAPI instrumented with OpenTelemetry")
    except ImportError:
        logger.debug("opentelemetry-instrumentation-fastapi not installed — skipping")
    except Exception as exc:  # noqa: BLE001
        logger.warning("FastAPI instrumentation failed: %s", exc)


def instrument_requests() -> None:
    """Instrument the requests library for outbound HTTP trace propagation."""
    try:
        from opentelemetry.instrumentation.requests import RequestsInstrumentor

        RequestsInstrumentor().instrument()
        logger.info("Requests instrumented with OpenTelemetry")
    except ImportError:
        logger.debug("opentelemetry-instrumentation-requests not installed — skipping")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Requests instrumentation failed: %s", exc)


def get_tracer(name: str):
    """Return a tracer for custom span creation inside business logic."""
    from opentelemetry import trace

    return trace.get_tracer(name)


__all__ = ["init_telemetry", "instrument_fastapi", "instrument_requests", "get_tracer"]
