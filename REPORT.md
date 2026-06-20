# Verification report

## Uploaded artifact

The uploaded `openline otel v2.py` was the earlier single-threaded prototype, not
the corrected implementation described in the accompanying report. It still used
pseudo-spans for `olp.*`, boolean gateway promotion, callable immediate finalization,
flat JSON hashing, and the old `coherence_receipt` name.

## Rebuilt artifact

This directory contains a new implementation against the OpenTelemetry
`SpanProcessor`/`ReadableSpan` API surface with:

- explicit `ReadableSpan.events` mapping;
- ordinary `trace_receipt` and typed `coherence_input_receipt` tiers;
- one bounded callback queue and one trace-state worker;
- root-close-plus-grace provisional sealing;
- ordered, chained late-span amendments;
- per-trace queue-loss accounting and signed loss amendments;
- unconditional `attestation: self` and `capture_status: provisional`;
- strict typed-event schemas and no semantic inference;
- exact tagged binary64 commitments for ordinary OTel float attributes;
- exact tagged decimal commitments for epoch nanoseconds and other large OTel integers;
- safe-integer, ASCII-keyed canonical receipt JSON;
- RFC 6962-style domain-separated Merkle trace roots with odd-node promotion;
- Ed25519 signing and independent Node verification.

## Executed tests

Ten Python conformance tests passed under genuine thread contention using SDK-shaped
immutable fixtures. The generated receipt also verified with Node's independent
Ed25519 implementation.

## Real SDK release gate

The clean external Google Colab run completed on 2026-06-20. All three real SDK
integration tests passed without skips. The run covered SDK batching and dashboard
fan-out, typed span events, context propagation, links, ordinary float attributes,
concurrent callbacks, deterministic queue loss, late-span amendments, force-flush,
and shutdown partials.

`tests/test_real_sdk_integration.py` now provides the exact external baseline suite
for this gate. It ran without `tests/stubs` on `PYTHONPATH` and reported three tests
passed.

`.github/workflows/real-sdk-release-gate.yml` installs the package on a clean
GitHub runner and executes this gate on Python 3.11 and 3.12, followed by an
independent Node verification of a newly generated receipt.

The same external run regenerated `artifacts/conformance-receipt.json`; the Node
verifier accepted the Ed25519 signature and reported `coherence_input_receipt`
verified. The implementation release gate is closed.

The conformance generator is runtime-self-contained and does not import fixtures
from `tests`. Its deterministic span commits an epoch-nanosecond timestamp and an
ordinary binary64 float to cover both portable tagging rules.
