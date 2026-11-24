"""Export frame timings to an OTLP/HTTP collector on a background thread."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence
    from contextlib import AbstractContextManager

TRACER = trace.get_tracer("vsmp.playback")


def configure_tracing(endpoint: str) -> None:
    """Configure asynchronous trace export for the playback process.

    Args:
        endpoint: Full OTLP/HTTP trace ingestion URL.
    """
    provider = TracerProvider(resource=Resource.create({"service.name": "vsmp"}))
    exporter = OTLPSpanExporter(endpoint=endpoint, timeout=3)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)


@contextmanager
def stage(
    name: str,
    *,
    attributes: Mapping[str, str | int] | None = None,
    context: Context | None = None,
    links: Sequence[trace.Link] = (),
) -> Iterator[trace.Span]:
    """Time a playback operation and mark failures without exporting media details.

    Args:
        name: Stable operation name.
        attributes: Playback dimensions for filtering in Grafana.
        context: Explicit parent context, or the active span by default.
        links: Related work completed in another trace.

    Yields:
        Active span for the operation.
    """
    with TRACER.start_as_current_span(
        name,
        context=context,
        attributes=attributes,
        links=links,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            yield span
        except Exception as exc:
            span.set_attribute("error.type", type(exc).__name__)
            span.set_status(trace.StatusCode.ERROR)
            raise


def frame_refresh(
    source: str, kind: str, trigger: str, *, fetch: trace.SpanContext | None = None
) -> AbstractContextManager[trace.Span]:
    """Start one independent trace for a display attempt after scheduling.

    Args:
        source: Selected media source.
        kind: Photo or video.
        trigger: Playback or manual redisplay.
        fetch: Optional earlier Immich cache lookup or download.

    Returns:
        Context manager covering the refresh and its child operations.
    """
    return stage(
        "vsmp.frame.refresh",
        attributes={
            "vsmp.source": source,
            "vsmp.media.kind": kind,
            "vsmp.trigger": trigger,
        },
        context=Context(),
        links=[trace.Link(fetch)] if fetch is not None and fetch.is_valid else [],
    )
