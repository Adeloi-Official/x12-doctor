# x12-doctor

Your 850 got rejected. This tells you why — in plain English.

A trading partner sends back a 997 that says `AK3*PO1*8**8` and `AK4*3*355*7`,
and somebody has to work out that the eighth segment of the transaction set had
an invalid code value in element 3. Or an order silently never arrives and the
only clue is an SE01 that was correct before someone added a segment. This tool
reads X12 documents that are already wrong and says what is wrong with them,
where, why documents end up that way, and what to change.

Built by [Adeloi](https://adeloi.com), an engineering partner for industrial
suppliers and manufacturers.

[![CI](https://github.com/Adeloi-Official/x12-doctor/actions/workflows/ci.yml/badge.svg)](https://github.com/Adeloi-Official/x12-doctor/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/x12-doctor)](https://pypi.org/project/x12-doctor/)
[![Python](https://img.shields.io/pypi/pyversions/x12-doctor)](https://pypi.org/project/x12-doctor/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](./LICENSE)

**Status: Beta. 28 diagnoses across the 004010 order-to-invoice flow.**

---

### See it in action

A purchase order that a partner rejected:

```
$ x12doctor check rejected_po.edi
```

```
rejected_po.edi — 850 Purchase Order (004010)

  WARN   B01  segment 10 (PO1), element 03
         Unit of measure 'EACH' is not an X12 code. Did you mean 'EA'?
         Why this happens: The value came straight from an ERP field that
         stores display text, so a spelled-out unit passes internal
         validation — internally it was never a code at all.
         Fix: Map the internal unit to its X12 code at the boundary and
         reject unmapped values instead of passing them through.

  ERROR  A02  segment 13 (SE), element 01
         SE01 says 12 segments, but ST through SE actually contains 11 — 1
         fewer than declared.
         Why this happens: The count is written when the transaction set is
         assembled, then a segment is added or removed afterwards — a
         conditional REF, an extra line item, a stripped-out empty segment —
         and the count is never recalculated.
         Fix: Count ST through SE inclusive and write that number into SE01.
         Compute it after the transaction set is complete, never before.

2 findings (1 error, 1 warning)
```

And the 997 that came back, read together with the order it rejected:

```
$ x12doctor explain rejected.997 --original original.850
```

```
rejected.997 — 997 Functional Acknowledgment

  Rejected

  Group 837337 (PO) — rejected
         0 of 1 transaction sets accepted.

    850 0001 — rejected
         segment 8 of the transaction set (PO1): segment has data element
         errors.
           The segment reads: PO1*1*228*EACH*220.64*PE*BP*AX-31566
           element 03: invalid code value — the value was 'EACH' (data
           element 355).

  Positions above were resolved against the original document.
```

That last line is the point. A 997 on its own reports positions, not content.
Paired with the document it acknowledges, "element 3 of segment 8" becomes
`EACH`, which is a thing you can go and fix.

---

## Contents

- [Install](#install)
- [Quickstart](#quickstart)
- [What this is](#what-this-is)
- [What this is NOT](#what-this-is-not)
- [The diagnostic catalog](#the-diagnostic-catalog)
- [Commands](#commands)
- [Exit codes](#exit-codes)
- [Partner profiles](#partner-profiles)
- [Python API](#python-api)
- [Testing with edi-fixtures](#testing-with-edi-fixtures)
- [Contributing](#contributing)
- [License](#license)

## Install

```sh
pipx install x12-doctor      # as a command line tool
pip install x12-doctor       # as a library
```

Python 3.11 or later. No runtime dependencies.

## Quickstart

```sh
x12doctor check order.edi                    # diagnose one document
cat order.edi | x12doctor check -            # read from a pipe
x12doctor check order.edi --format json      # for CI and further processing
x12doctor explain ack.997 --original order.edi
x12doctor scan ./outbound --missing-997      # a whole directory
x12doctor codes B01                          # explain one diagnosis
```

In Python:

```python
from x12doctor import diagnose

report = diagnose(open("order.edi", "rb").read())

if not report.ok:
    for finding in report.errors:
        print(finding.code, finding.location, finding.message)
```

## What this is

- **A diagnosis, not a verdict.** Every finding says what is wrong, where it
  is, why real integrations produce it, and what to change. A code and a line
  number is not an answer.
- **A 997 decoder.** The AK error codes are numbers with no meaning in the
  file itself. This carries the 004010 code lists and pairs an acknowledgment
  with the document it rejected.
- **Tolerant.** The files this exists for are already broken. Nothing here
  raises on malformed input — a document too damaged to parse comes back as a
  report saying exactly that.
- **Deterministic.** The same bytes always produce the same report, and the
  JSON output is stable and versioned, so it can be diffed and gated on.
- **Zero runtime dependencies.** Standard library only, which is what makes it
  safe to drop into a build pipeline that has to run for a decade.

## What this is NOT

- **Not a generator.** For realistic test documents — including the eighteen
  ways they break in production — see
  [`@adeloi/edi-fixtures`](https://github.com/Adeloi-Official/edi-fixtures),
  which is the sister project this one is tested against.
- **Not a repair tool.** It diagnoses and never rewrites. Silently correcting a
  production document is how a wrong order becomes a wrong order nobody can
  trace.
- **Not a parser framework.** It reads X12 only as far as a diagnosis needs.
  For general parsing see [node-x12](https://github.com/aaronhuggins/node-x12)
  or the [Stedi](https://www.stedi.com/) ecosystem.
- **Not a mapper or translator.**
- **Not a source of trading-partner implementation guides.** Guides for
  specific partners are proprietary. `PartnerProfile` gives you a generic,
  configurable profile instead.
- **Not EDIFACT, TRADACOMS, or HIPAA transactions** (270/271/837/…).
- **Not an AS2/SFTP/VAN transport client.**

## The diagnostic catalog

Twenty-eight diagnoses in four categories. `x12doctor codes` lists them all;
`x12doctor codes A02` explains one in full. The complete reference is in
[docs/diagnostics.md](docs/diagnostics.md).

**A — Structure & envelope**
`A00` not X12 · `A01` ISA not 106 characters · `A02` SE segment count ·
`A03` control numbers do not match · `A04` duplicate interchange control ·
`A05` nonstandard separators · `A06` GE transaction set count ·
`A07` IEA group count · `A08` unexpected version · `A09` content outside the
interchange · `A10` envelope not closed · `A11` envelope out of order

**B — Semantics & content**
`B01` nonstandard unit of measure · `B02` suspected implied decimals ·
`B03` wrong date format · `B04` CTT totals · `B05` missing partner reference ·
`B06` missing required segment

**C — Acknowledgments (997)**
`C01` 997 reports errors · `C02` 997 references an unknown group ·
`C03` 997 rejected the document · `C04` rejection located in the original ·
`C05` no acknowledgment received · `C06` interchanges out of order

**D — Advance ship notices (856)**
`D01` broken HL hierarchy · `D02` invalid SSCC-18 check digit ·
`D03` shipped quantity does not match the order · `D04` implausible dates

Eighteen of these mirror a fault in `@adeloi/edi-fixtures` one for one, and the
test suite asserts that correspondence in both directions.

## Commands

```
x12doctor check FILE...        diagnose documents; '-' reads standard input
x12doctor explain FILE         translate a 997; --original resolves positions
x12doctor scan DIR             diagnose a directory, including cross-file checks
x12doctor codes [CODE]         show the catalog, or explain one diagnosis
```

Shared options: `--format human|json`, `--partner FILE`,
`--fail-on error|warning|info`, `--brief`, `--no-color`.

Three diagnoses can only be made across files, and so are only reachable via
`scan`: a duplicate control number needs a second document to be a duplicate
of, a missing acknowledgment is defined by absence, and an out-of-order flow is
a property of the sequence.

`scan --missing-997` reports every functional group with no matching
acknowledgment. Without the flag only groups whose ISA14 is `1` are held to it,
because that field is literally the one that asks to be acknowledged.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | no findings at or above the `--fail-on` threshold |
| `1` | findings at or above the threshold |
| `2` | a file could not be read, or is not X12 at all |

The `2` is deliberately distinct: a CI job needs to tell "this document has
problems" apart from "this is not a document".

```yaml
- name: Validate outbound EDI
  run: x12doctor scan ./outbound --fail-on warning --format json > edi-report.json
```

## Partner profiles

Most of what a trading partner rejects is not in the X12 standard — it is in
their implementation guide. A profile carries those rules:

```toml
# grainger.toml
name = "Example Partner"
isa_qualifier = "ZZ"
sender_id = "SENDERID"
receiver_id = "RECEIVERID"
required_refs = ["DP", "IA"]
uom_whitelist = ["EA", "CS", "BX"]
usage_indicator = "P"
version = "004010"

[required_refs_by_doc]
856 = ["BM"]

[required_segments]
850 = ["FOB"]
```

```sh
x12doctor check order.edi --partner grainger.toml
```

The fields mirror `definePartner()` in `@adeloi/edi-fixtures`, so the same
profile shape describes a partner in both projects.

JSON works too, and is the better choice when the profile is generated rather
than hand-written — emitted by a provisioning script, pulled from a config
service, or shared with a system that has no TOML reader:

```sh
x12doctor check order.edi --partner partners/grainger.json
```

The format is chosen by the file extension; the resulting profile is identical.

## Python API

```python
from x12doctor import diagnose, explain_997, scan, PartnerProfile

report = diagnose(raw_bytes)
report.ok            # False when anything is an error
report.doc_type      # "850" | "855" | "856" | "810" | "997" | "unknown"
report.findings      # list[Finding]
report.to_json()     # stable, versioned, diffable

finding = report.findings[0]
finding.code         # "A02"
finding.severity     # "error" | "warning" | "info"
finding.segment_index, finding.segment_id, finding.element, finding.line
finding.message      # what is wrong, with this document's values in it
finding.why          # why integrations produce this
finding.hint         # what to change

ack = explain_997(fa_bytes, original=po_bytes)
ack.status           # "accepted" | "accepted_with_errors" | "rejected" | ...
ack.rejections       # located AK3/AK4 objections, mapped onto the original

result = scan("./outbound", partner=PartnerProfile.load("grainger.toml"))
result.entries       # per-file reports
result.findings      # the cross-file findings
```

## Testing with edi-fixtures

The two projects are designed to be used together: one generates documents that
are broken in a specific realistic way, the other proves your pipeline notices.
A pytest plugin ships with the package and needs no wiring:

```python
from x12doctor.pytest_plugin import assert_valid_x12, assert_diagnoses

def test_our_order_writer_emits_valid_x12():
    assert_valid_x12(build_purchase_order(order))

def test_we_reject_spelled_out_units():
    assert_diagnoses(document_from_fixtures, "B01", exactly=True)
```

The corpus under `test/corpus` is generated by `edi-fixtures` at fixed seeds and
committed, so the test suite needs no Node. `tools/build-corpus.mjs` rebuilds
it. Each filename states the diagnosis the file must produce:

```
850_seed42_A02_seCountWrong.edi
^^^ ^^^^^^ ^^^ ^^^^^^^^^^^^
|   |      |   `- the edi-fixtures fault that produced it
|   |      `----- the x12-doctor code it must be diagnosed with
|   `------------ the seed, so the content is reproducible
`---------------- the transaction set
```

The most important assertion in the suite is the boring one: every clean
document must produce zero findings. A linter that cries wolf gets switched
off, and then it catches nothing.

## Contributing

Issues and pull requests are welcome. A new diagnosis needs an entry in
`src/x12doctor/catalog.py` — the single source of truth that `docs/diagnostics.md`
and `x12doctor codes` are both generated from — plus a corpus file that
triggers it and a clean file that does not.

```sh
python -m pytest          # the suite, including the golden corpus
python -m mypy            # strict
python -m ruff check .
python tools/gen-docs.py  # regenerate docs/diagnostics.md from the catalog
```

## License

[Apache License 2.0](./LICENSE). Copyright 2026 Olevis LLC.
