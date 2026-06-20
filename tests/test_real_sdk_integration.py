"""Integration tests that must run without tests/stubs on PYTHONPATH.

These tests exercise OpenLineReceiptProcessor through the real OpenTelemetry
SDK. They are skipped when the SDK is unavailable so the deterministic core
suite remains runnable in restricted environments.
"""

from __future__ import annotations

import hashlib
import threading
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    try:
        from opentelemetry.sdk.trace.export import InMemorySpanExporter
    except ImportError:
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
            InMemorySpanExporter,
        )
    from opentelemetry.trace import SpanContext, TraceFlags
except ImportError as exc:  # pragma: no cover - exercised only without SDK
    SDK_IMPORT_ERROR = exc
else:
    SDK_IMPORT_ERROR = None

from openline_otel.processor import OpenLineReceiptProcessor, ReceiptStore, verify_receipt


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


@unittest.skipIf(SDK_IMPORT_ERROR is not None, f"real OpenTelemetry SDK unavailable: {SDK_IMPORT_ERROR}")
class RealSdkIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.providers: list[TracerProvider] = []

    def tearDown(self) -> None:
        for provider in reversed(self.providers):
            provider.shutdown()

    def make_stack(self, *, grace: float = 0.05, queue_size: int = 256):
        provider = TracerProvider()
        self.providers.append(provider)
        exporter = InMemorySpanExporter()
        provider.add_span_processor(BatchSpanProcessor(exporter))
        store = ReceiptStore()
        processor = OpenLineReceiptProcessor(
            Ed25519PrivateKey.generate(),
            grace_interval_seconds=grace,
            queue_size=queue_size,
            receipt_store=store,
        )
        provider.add_span_processor(processor)
        tracer = provider.get_tracer("openline.real-sdk.integration")
        return provider, tracer, exporter, processor, store

    @staticmethod
    def add_typed_events(span) -> None:
        span.add_event(
            "olp.claim",
            {
                "id": "claim_A",
                "content_hash": digest("claim A"),
                "material": True,
            },
        )
        span.add_event(
            "olp.evidence",
            {
                "id": "evidence_A",
                "content_hash": digest("evidence A"),
                "observed": True,
            },
        )
        span.add_event(
            "olp.relation",
            {
                "src": "evidence_A",
                "dst": "claim_A",
                "relation_type": "supports",
            },
        )
        span.add_event(
            "olp.signal",
            {
                "sequence": 0,
                "value_micros": 250_000,
                "signal_schema_id": "openline.test.signal.v1",
            },
        )

    def test_real_sdk_events_float_link_dashboard_and_amendment(self):
        provider, tracer, exporter, _, store = self.make_stack()
        linked_context = SpanContext(
            trace_id=0x0123456789ABCDEF0123456789ABCDEF,
            span_id=0x0123456789ABCDEF,
            is_remote=True,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )

        with tracer.start_as_current_span(
            "agent_loop", links=[trace.Link(linked_context)]
        ) as root:
            root.set_attribute("gen_ai.operation.name", "invoke")
            root.set_attribute("temperature", 0.7)
            self.add_typed_events(root)
            root_context = root.get_span_context()

        trace_id = f"{root_context.trace_id:032x}"
        receipt = store.wait_for(
            lambda item: item.get("trace_id") == trace_id
            and item["kind"] == "coherence_input_receipt"
        )
        self.assertEqual(receipt["observed_span_count"], 1)
        self.assertEqual(receipt["typed_event_status"], "valid")
        self.assertEqual(receipt["capture_status"], "provisional")
        self.assertEqual(receipt["state_cap"], "white")
        self.assertTrue(verify_receipt(receipt))

        late_context = trace.set_span_in_context(trace.NonRecordingSpan(root_context))
        with tracer.start_as_current_span("late_async_tool", context=late_context) as late:
            late.set_attribute("gen_ai.tool.name", "calculator")
            late.add_event(
                "olp.evidence",
                {
                    "id": "self_declared_terminal",
                    "content_hash": digest("not an external attestation"),
                    "observed": True,
                },
            )

        amendment = store.wait_for(
            lambda item: item.get("trace_id") == trace_id
            and item["kind"] == "amendment_receipt"
        )
        self.assertEqual(amendment["previous_receipt_hash"], receipt["payload_hash"])
        self.assertEqual(amendment["capture_status"], "provisional")
        self.assertNotIn("terminal", amendment["kind"])
        self.assertTrue(verify_receipt(amendment))

        self.assertTrue(provider.force_flush())
        self.assertEqual(len(exporter.get_finished_spans()), 2)

    def test_real_sdk_concurrency_and_trace_specific_queue_loss(self):
        _, tracer, _, processor, store = self.make_stack(grace=0.05, queue_size=1)
        original_consume = processor._consume
        worker_entered = threading.Event()
        release_worker = threading.Event()

        def blocked_consume(item):
            worker_entered.set()
            release_worker.wait(2)
            original_consume(item)

        processor._consume = blocked_consume
        root = tracer.start_span("flood_root")
        root_context = root.get_span_context()
        trace_id = f"{root_context.trace_id:032x}"
        root.end()
        self.assertTrue(worker_entered.wait(1), "OpenLine worker did not receive root span")

        parent_context = trace.set_span_in_context(trace.NonRecordingSpan(root_context))

        def emit(index: int) -> None:
            with tracer.start_as_current_span(f"flood_{index}", context=parent_context):
                pass

        threads = [threading.Thread(target=emit, args=(index,)) for index in range(64)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        release_worker.set()

        receipt = store.wait_for(lambda item: item.get("trace_id") == trace_id)
        self.assertTrue(receipt["capture_loss"])
        self.assertGreater(receipt["dropped_span_count"], 0)
        self.assertTrue(verify_receipt(receipt))

    def test_force_flush_preserves_grace_and_shutdown_emits_partial(self):
        provider, tracer, _, _, store = self.make_stack(grace=60)
        with tracer.start_as_current_span("shutdown_root") as root:
            trace_id = f"{root.get_span_context().trace_id:032x}"

        self.assertTrue(provider.force_flush())
        self.assertFalse(any(item.get("trace_id") == trace_id for item in store.all()))

        provider.shutdown()
        self.providers.remove(provider)
        receipt = store.wait_for(lambda item: item.get("trace_id") == trace_id)
        self.assertEqual(receipt["seal_reason"], "shutdown_before_grace_elapsed")
        self.assertEqual(receipt["capture_status"], "provisional")
        self.assertTrue(verify_receipt(receipt))


if __name__ == "__main__":
    unittest.main(verbosity=2)
