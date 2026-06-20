import hashlib
import threading
import time
import unittest
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openline_otel.processor import (
    OpenLineReceiptProcessor,
    ReceiptStore,
    SpanSnapshot,
    canonical_json,
    merkle_root,
    verify_receipt,
)


class Value:
    def __init__(self, value):
        self.value = value


@dataclass
class Context:
    trace_id: int
    span_id: int


@dataclass
class Event:
    name: str
    attributes: dict
    timestamp: int = 1


@dataclass
class Link:
    context: Context
    attributes: dict = field(default_factory=dict)


@dataclass
class Resource:
    attributes: dict = field(default_factory=dict)


@dataclass
class Scope:
    name: str = "test"
    version: str = "1"
    schema_url: str | None = None
    attributes: dict = field(default_factory=dict)


@dataclass
class Status:
    status_code: Value = field(default_factory=lambda: Value(1))
    description: str | None = None


class FakeSpan:
    def __init__(
        self,
        trace_id,
        span_id,
        *,
        parent_span_id=None,
        name="operation",
        attributes=None,
        events=(),
        start_time=None,
        end_time=None,
    ):
        self.context = Context(trace_id, span_id)
        self.parent = Context(trace_id, parent_span_id) if parent_span_id else None
        self.name = name
        self.kind = Value(1)
        self.start_time = start_time if start_time is not None else span_id * 10
        self.end_time = end_time if end_time is not None else span_id * 10 + 5
        self.status = Status()
        self.attributes = attributes or {}
        self.events = tuple(events)
        self.links = ()
        self.resource = Resource({"service.name": "test-agent"})
        self.instrumentation_scope = Scope()


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def typed_events(duplicate=False):
    events = [
        Event("olp.claim", {"id": "c1", "content_hash": digest("claim"), "material": True}),
        Event("olp.evidence", {"id": "e1", "content_hash": digest("evidence"), "observed": True}),
        Event("olp.relation", {"src": "e1", "dst": "c1", "relation_type": "supports"}),
        Event("olp.signal", {"sequence": 0, "value_micros": 250_000, "signal_schema_id": "test.signal.v1"}),
    ]
    if duplicate:
        events.append(Event("olp.claim", {"id": "c1", "content_hash": digest("other"), "material": True}))
    return events


class ProcessorTests(unittest.TestCase):
    def make_processor(self, **kwargs):
        store = ReceiptStore()
        processor = OpenLineReceiptProcessor(
            Ed25519PrivateKey.generate(),
            receipt_store=store,
            grace_interval_seconds=kwargs.pop("grace", 0.02),
            **kwargs,
        )
        self.addCleanup(processor.shutdown)
        return processor, store

    def test_real_event_shape_yields_coherence_input_without_extra_spans(self):
        processor, store = self.make_processor()
        processor.on_end(FakeSpan(1, 1, events=typed_events()))
        receipt = store.wait_for(lambda item: item["kind"] == "coherence_input_receipt")
        self.assertEqual(receipt["observed_span_count"], 1)
        self.assertEqual(receipt["state_cap"], "white")
        self.assertTrue(receipt["semantic_claims"])
        self.assertTrue(verify_receipt(receipt))

    def test_ordinary_span_is_trace_only_and_exact_float_is_hashable(self):
        processor, store = self.make_processor()
        processor.on_end(FakeSpan(2, 1, attributes={"temperature": 0.1}))
        receipt = store.wait_for(lambda item: item.get("trace_id") == f"{2:032x}")
        self.assertEqual(receipt["kind"], "trace_receipt")
        self.assertFalse(receipt["semantic_claims"])
        self.assertTrue(verify_receipt(receipt))

    def test_epoch_nanoseconds_and_large_otel_integers_are_exactly_tagged(self):
        timestamp = 1_750_000_000_123_456_789
        span = FakeSpan(
            20,
            1,
            start_time=timestamp,
            end_time=timestamp + 5,
            attributes={"large_counter": timestamp},
            events=(Event("checkpoint", {"large_counter": timestamp}, timestamp),),
        )
        snapshot = SpanSnapshot.from_readable_span(span).as_dict()
        self.assertEqual(snapshot["start_time_unix_nano"], {"$int": str(timestamp)})
        self.assertEqual(snapshot["end_time_unix_nano"], {"$int": str(timestamp + 5)})
        self.assertEqual(snapshot["attributes"]["large_counter"], {"$int": str(timestamp)})
        self.assertEqual(
            snapshot["events"][0]["timestamp_unix_nano"], {"$int": str(timestamp)}
        )

        processor, store = self.make_processor()
        processor.on_end(span)
        receipt = store.wait_for(lambda item: item.get("trace_id") == f"{20:032x}")
        self.assertTrue(verify_receipt(receipt))

    def test_duplicate_typed_id_downgrades_to_trace_receipt(self):
        processor, store = self.make_processor()
        processor.on_end(FakeSpan(3, 1, events=typed_events(duplicate=True)))
        receipt = store.wait_for(lambda item: item.get("trace_id") == f"{3:032x}")
        self.assertEqual(receipt["kind"], "trace_receipt")
        self.assertEqual(receipt["typed_event_status"], "invalid")
        self.assertIn("duplicate claim id", receipt["typed_event_error"])

    def test_concurrent_callbacks_are_assembled_by_one_worker(self):
        processor, store = self.make_processor(grace=0.05, queue_size=256)
        trace_id = 4
        children = [FakeSpan(trace_id, index, parent_span_id=1) for index in range(2, 42)]
        threads = [threading.Thread(target=processor.on_end, args=(span,)) for span in children]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        processor.on_end(FakeSpan(trace_id, 1))
        receipt = store.wait_for(lambda item: item.get("trace_id") == f"{trace_id:032x}")
        self.assertEqual(receipt["observed_span_count"], 41)
        self.assertFalse(receipt["capture_loss"])

    def test_late_spans_form_ordered_amendment_chain(self):
        processor, store = self.make_processor()
        trace_id = 5
        processor.on_end(FakeSpan(trace_id, 1))
        initial = store.wait_for(lambda item: item.get("trace_id") == f"{trace_id:032x}")
        processor.on_end(FakeSpan(trace_id, 2, parent_span_id=1))
        processor.on_end(FakeSpan(trace_id, 3, parent_span_id=1))
        second = store.wait_for(lambda item: item.get("amendment_sequence") == 2)
        amendments = [item for item in store.all() if item["kind"] == "amendment_receipt"]
        first = next(item for item in amendments if item["amendment_sequence"] == 1)
        self.assertEqual(first["previous_receipt_hash"], initial["payload_hash"])
        self.assertEqual(second["previous_receipt_hash"], first["payload_hash"])
        self.assertTrue(all(verify_receipt(item) for item in amendments))

    def test_queue_loss_is_trace_specific_and_signed(self):
        processor, store = self.make_processor(grace=0.05, queue_size=1)
        original_consume = processor._consume
        gate = threading.Event()

        def blocked(item):
            gate.wait(1)
            original_consume(item)

        processor._consume = blocked
        trace_id = 6
        processor.on_end(FakeSpan(trace_id, 2, parent_span_id=1))
        time.sleep(0.01)
        for index in range(3, 30):
            processor.on_end(FakeSpan(trace_id, index, parent_span_id=1))
        gate.set()
        time.sleep(0.05)
        processor.on_end(FakeSpan(trace_id, 1))
        receipt = store.wait_for(lambda item: item.get("trace_id") == f"{trace_id:032x}")
        self.assertTrue(receipt["capture_loss"])
        self.assertGreater(receipt["dropped_span_count"], 0)

    def test_shutdown_emits_signed_partial_receipt(self):
        store = ReceiptStore()
        processor = OpenLineReceiptProcessor(
            Ed25519PrivateKey.generate(),
            receipt_store=store,
            grace_interval_seconds=60,
        )
        processor.on_end(FakeSpan(7, 1))
        processor.force_flush()
        processor.shutdown()
        receipt = store.wait_for(lambda item: item.get("trace_id") == f"{7:032x}")
        self.assertEqual(receipt["seal_reason"], "shutdown_before_grace_elapsed")
        self.assertEqual(receipt["capture_status"], "provisional")
        self.assertTrue(verify_receipt(receipt))

    def test_merkle_odd_leaf_is_promoted_not_duplicated(self):
        three = merkle_root([{"n": 1}, {"n": 2}, {"n": 3}])
        duplicated = merkle_root([{"n": 1}, {"n": 2}, {"n": 3}, {"n": 3}])
        self.assertNotEqual(three, duplicated)

    def test_receipt_tampering_fails_signature(self):
        processor, store = self.make_processor()
        processor.on_end(FakeSpan(8, 1))
        receipt = store.wait_for(lambda item: item.get("trace_id") == f"{8:032x}")
        receipt["observed_span_count"] = 999
        self.assertFalse(verify_receipt(receipt))

    def test_canonical_profile_rejects_unsafe_integer(self):
        with self.assertRaisesRegex(ValueError, "interoperable range"):
            canonical_json({"value": 1 << 60})


if __name__ == "__main__":
    unittest.main()
