import logging
import time

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.logging.handler import LoggingHandler
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor, ConsoleLogRecordExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

SERVICE_NAME = "order-tracker"
METRIC_EXPORT_INTERVAL_MS = 10_000

tracer = trace.get_tracer(SERVICE_NAME)
meter = metrics.get_meter(SERVICE_NAME)
logger = logging.getLogger(SERVICE_NAME)

request_counter = meter.create_counter(
    "order_tracker.http.requests",
    unit="{request}",
    description="HTTP requests handled, by route and status code",
)
request_duration = meter.create_histogram(
    "order_tracker.http.request.duration",
    unit="s",
    description="HTTP request duration, by route and status code",
)
lookup_counter = meter.create_counter(
    "order_tracker.order_lookups",
    unit="{lookup}",
    description="Order lookups by id, by result",
)


def setup_telemetry(app):
    """Configure console exporters for traces, metrics and logs."""
    resource = Resource.create({"service.name": SERVICE_NAME})

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    reader = PeriodicExportingMetricReader(
        ConsoleMetricExporter(), export_interval_millis=METRIC_EXPORT_INTERVAL_MS
    )
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(ConsoleLogRecordExporter()))
    set_logger_provider(logger_provider)
    logging.getLogger().addHandler(LoggingHandler(level=logging.INFO, logger_provider=logger_provider))
    logger.setLevel(logging.INFO)

    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=tracer_provider,
        excluded_urls="healthz",
        exclude_spans=["send", "receive"],
    )

    @app.middleware("http")
    async def record_request_metrics(request, call_next):
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            attributes = {
                "http.route": route.path if route else "unmatched",
                "http.request.method": request.method,
                "http.response.status_code": status_code,
            }
            request_counter.add(1, attributes)
            request_duration.record(time.perf_counter() - start, attributes)
