"""Environment probe: installed versions of the assurance-layer dependencies."""
import importlib
import sys

print("python:", sys.version.split()[0])

for mod in [
    "pytest", "pact", "opentelemetry", "opentelemetry.sdk",
    "opentelemetry.exporter.otlp.proto.grpc.trace_exporter",
    "opentelemetry.instrumentation.fastapi",
    "opentelemetry.instrumentation.requests",
    "msgpack", "websocket", "yaml",
]:
    try:
        m = importlib.import_module(mod)
        print(f"OK   {mod:60s} {getattr(m, '__version__', '?')}")
    except Exception as exc:
        print(f"MISS {mod:60s} {type(exc).__name__}: {exc}")
