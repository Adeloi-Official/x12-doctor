# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Diagnostic codes are part of the public interface. A code is never reused for a
different meaning, and removing one is a breaking change. Widening what an
existing code detects is a minor change; the JSON output carries its own
`schema_version` alongside the package version.

## [0.1.0] — Unreleased

First release. Twenty-eight diagnoses across the 004010 order-to-invoice flow.

Requires Python 3.11 or later. No runtime dependencies.

### Added

- `x12doctor check` — diagnose one or more documents, or standard input.
- `x12doctor explain` — translate a 997 functional acknowledgment into plain
  English. With `--original`, the positions it reports are resolved against the
  document it acknowledges, so the output names the values that were rejected
  rather than their coordinates.
- `x12doctor scan` — diagnose a directory, including the checks that need more
  than one file: duplicate control numbers, unacknowledged groups, flows
  stamped out of order, and shipped quantities against the order.
- `x12doctor codes` — the diagnostic catalog, or one entry as a manual page.
- Human and JSON output for every command, with a versioned JSON schema.
- Partner profiles in TOML or JSON, mirroring `definePartner()` in
  `@adeloi/edi-fixtures`. Both formats are first-class; JSON suits generated
  configuration, TOML suits a profile a human maintains.
- A pytest plugin (`assert_valid_x12`, `assert_diagnoses`) registered
  automatically via an entry point.
- Python API: `diagnose`, `explain_997`, `scan`, `PartnerProfile`, plus the
  tokenizer and envelope types for callers who want the structure rather than
  the diagnosis.

### Diagnoses

- **A00–A11** — structure and envelope: not X12, ISA width, SE/GE/IEA counts,
  control number agreement, duplicate interchange numbers, separators,
  versions, content outside the interchange, unclosed and out-of-order
  envelopes.
- **B01–B06** — semantics and content: units of measure, implied decimals, date
  formats, CTT totals, partner-required references, required segments.
- **C01–C06** — acknowledgments: 997 decoding with the 004010 code lists,
  unknown groups, rejections, positions resolved against the original, missing
  acknowledgments, out-of-order interchanges.
- **D01–D04** — advance ship notices: HL hierarchy, SSCC-18 check digits,
  shipped quantities against the order, implausible shipment dates.

Eighteen of these mirror a fault in `@adeloi/edi-fixtures` one for one, and the
correspondence is asserted by the test suite in both directions.

[0.1.0]: https://github.com/Adeloi-Official/x12-doctor/releases/tag/v0.1.0
