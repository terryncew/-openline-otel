# openline-otel

`openline-otel` attaches portable signed receipts to OpenTelemetry traces without
replacing the application's existing observability exporter.

## Receipt tiers

- Ordinary spans produce a provisional `trace_receipt`. No semantic graph is inferred.
- Explicit `olp.claim`, `olp.evidence`, `olp.relation`, and `olp.signal` span events
  produce a provisional `coherence_input_receipt`.
- The package never upgrades beyond `attestation: self`. A stronger tier requires
  independently verifiable capture, key-isolation, and routing evidence.

Every receipt is provisional. Root closure plus a grace interval is a capture policy,
not proof that no span was omitted. Late spans create ordered signed amendments.

## Deterministic boundaries

- OLP typed events are strict and reject unknown fields, duplicate IDs, broken
  relations, signal gaps, mixed signal schemas, floats, and malformed hashes.
- Ordinary OTel float attributes are committed as tagged IEEE-754 binary64 bytes;
  they never enter COLE as floating-point inputs.
- OTel integers outside the cross-runtime safe range, including epoch-nanosecond
  timestamps, are committed as tagged canonical decimal strings.
- Trace records use domain-separated RFC 6962-style Merkle hashing with unpaired
  odd nodes promoted unchanged.
- Receipt JSON uses `olp-canonical-json-int-v1`: ASCII keys, safe integers, compact
  serialization, sorted keys, and ASCII-escaped strings.

## Integration status

The processor implements the real OpenTelemetry `SpanProcessor` interface and uses
`ReadableSpan.events`. Its receipt engine has been exercised with concurrent callback
threads and SDK-shaped immutable span fixtures. Real `opentelemetry-sdk` integration
remains a release gate and must be run where the package can be installed.

## Local conformance test

```bash
PYTHONPATH=tests/stubs:src python -m unittest discover -s tests -v
node verify-node.mjs artifacts/conformance-receipt.json
```

## Real SDK release gate

Run this in an environment that can install project dependencies. Do not include
`tests/stubs` on `PYTHONPATH`:

```bash
python -m venv .venv
.venv/bin/pip install -e .
.venv/bin/python -m unittest tests.test_real_sdk_integration -v
```

This suite covers typed span events, ordinary float attributes, links, parallel
callbacks, deterministic queue loss, late-span amendments, dashboard fan-out,
force-flush semantics, and shutdown partials. Self-declared terminal events stay
provisional and cannot close the amendment chain.

For an iPhone-only workflow, upload this directory to GitHub and open **Actions >
Real SDK Release Gate > Run workflow**. Both Python jobs must finish green. A
skipped integration test does not close the gate; the run must show all three
`RealSdkIntegrationTests` as `ok` on Python 3.11 and 3.12.
