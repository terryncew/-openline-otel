import hashlib
import json
import pathlib
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from openline_otel import OpenLineReceiptProcessor, ReceiptStore


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def conformance_span():
    """Return a deterministic SDK-shaped span without importing test code."""
    timestamp = 1_750_000_000_123_456_789
    events = (
        SimpleNamespace(
            name="olp.claim",
            timestamp=timestamp,
            attributes={"id": "c1", "content_hash": digest("claim"), "material": True},
        ),
        SimpleNamespace(
            name="olp.evidence",
            timestamp=timestamp + 1,
            attributes={"id": "e1", "content_hash": digest("evidence"), "observed": True},
        ),
        SimpleNamespace(
            name="olp.relation",
            timestamp=timestamp + 2,
            attributes={"src": "e1", "dst": "c1", "relation_type": "supports"},
        ),
        SimpleNamespace(
            name="olp.signal",
            timestamp=timestamp + 3,
            attributes={
                "sequence": 0,
                "value_micros": 250_000,
                "signal_schema_id": "conformance.signal.v1",
            },
        ),
    )
    return SimpleNamespace(
        context=SimpleNamespace(trace_id=0xA11CE, span_id=1),
        parent=None,
        name="conformance_root",
        kind=SimpleNamespace(value=1),
        start_time=timestamp,
        end_time=timestamp + 4,
        status=SimpleNamespace(status_code=SimpleNamespace(value=1), description=None),
        attributes={"temperature": 0.7},
        events=events,
        links=(),
        resource=SimpleNamespace(attributes={"service.name": "openline-conformance"}),
        instrumentation_scope=SimpleNamespace(
            name="openline.conformance",
            version="1",
            schema_url=None,
            attributes={},
        ),
    )


def main():
    store = ReceiptStore()
    key = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
    processor = OpenLineReceiptProcessor(
        key,
        grace_interval_seconds=0,
        receipt_store=store,
        semconv_schema_id="conformance.fixture.v1",
    )
    processor.on_end(conformance_span())
    receipt = store.wait_for(lambda item: item["kind"] == "coherence_input_receipt")
    processor.shutdown()
    output = pathlib.Path("artifacts/conformance-receipt.json")
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="ascii")
    print(output)


if __name__ == "__main__":
    main()
