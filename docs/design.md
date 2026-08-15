# Design

Why x12-doctor is built the way it is. The short version: the files this tool
exists for are already broken, and everything below follows from taking that
seriously.

## Nothing raises

A validator that throws on malformed input is useless precisely when it is
needed. `diagnose()` never raises, on any bytes, ever. A file that is not X12
at all comes back as a report holding one `A00` finding rather than an
exception, which is what lets the CLI separate exit code `2` ("this is not a
document") from exit code `1` ("this is a document with problems").

The property is enforced by `test/test_robustness.py`, which mutates every
clean corpus document in nine realistic ways — truncation, byte corruption,
delimiter loss, null injection, encoding damage — and asserts only that a
report comes back.

The tokenizer carries the weight of this. It never validates; it records
problems as issues and keeps going with the best guess available. A file whose
ISA is too damaged to read positionally still gets its body split, because the
segments after the ISA are usually intact and usually where the answer is.

## The catalog is the single source of truth

`src/x12doctor/catalog.py` holds every diagnosis: its code, severity, and the
three pieces of prose that make up a finding. Nothing else in the codebase
contains explanatory text about what a code means.

That has three consequences worth stating:

- `docs/diagnostics.md` is **generated** from it by `tools/gen-docs.py`, and CI
  fails if the checked-in copy is stale. A hand-maintained parallel table would
  drift, and a wrong explanation of why a document was rejected is worse than
  no explanation.
- `x12doctor codes B01` reads the same object the check does, so the manual
  page and the finding can never disagree.
- The test suite asserts in both directions that every catalog entry is raised
  somewhere and every raised code is in the catalog. A documented diagnosis no
  check can produce is a promise not kept.

## Findings say why, not just what

Every finding carries three fields, and the split is deliberate:

| Field | Answers |
|---|---|
| `message` | What is wrong, with this document's actual values in it |
| `why` | How a real integration produces this |
| `hint` | What to change |

The `why` is the field that does the work. Someone reading "SE01 says 12, ST
through SE contains 11" still has to guess whether that is their bug or their
partner's. "The count is written when the transaction set is assembled, then a
segment is added afterwards" points at the line of code responsible.

`message` is generated per document and names concrete values; `why` and `hint`
are static per code and come from the catalog. A check may override severity,
`why` or `hint` when it knows something the catalog cannot — a partner profile
turning an optional segment into a required one, for instance.

## One defect, one finding

A single mistake in a document can trip several checks. Reporting all of them
buries the one that matters, so the pipeline actively suppresses follow-ons:

- A truncated ISA means the separators had to be inferred. That is `A01`'s
  story; the `A05` "separators were inferred" note is dropped when `A01` fired.
- A `A00` (not an X12 file at all) short-circuits the whole pipeline. Without
  that, a CSV gets each of its lines reported as a malformed segment.
- `AK304 = 8` ("segment has data element errors") and `AK502 = 5` ("one or more
  segments are in error") are suppressed when the AK4 detail they summarise is
  present, because that detail says the same thing with specifics.
- A short date in a DTM is `B03`. The 856 timeline check deliberately refuses
  to guess a century for it, because doing so would report the same defect a
  second time as an implausible shipment date.

The golden corpus tests this directly: each fixture is broken exactly one way,
and must produce exactly one code.

## Determinism

The same bytes always produce the same report, and `to_json()` produces the
same string. Findings are sorted by position, then severity, then code — never
by iteration order of a set or dict. Directory scans sort their file list before
reading. This is what makes the JSON output diffable and safe to gate a build
on.

There is one deliberate exception to "no I/O affects output": `render_report`
consults the terminal width and `NO_COLOR`. The JSON output does neither.

## Zero runtime dependencies

Standard library only. This is a tool that gets dropped into a build pipeline
for a supply chain integration, and those pipelines run for a decade. Every
dependency is a future incompatibility, and none of what this does — splitting
strings, comparing counts, checking a mod-10 digit — needs one.

The floor is Python 3.11, which is what makes this free: `tomllib` is in the
standard library from 3.11, so partner profiles need no TOML dependency.
Supporting 3.10 would have meant either a dependency or a documented gap, and
3.10 reaches end of life in October 2026 — carrying it would cost most exactly
when it is worth least.

`argparse` over `click` follows the same rule.

## Tolerance over strictness in parsing

The envelope parser builds whatever structure it can recover:

- A `GS` with no enclosing `ISA` gets a synthetic interchange, so the groups
  inside it are still checked.
- An unclosed `ST` is closed implicitly at the next boundary and reported.
- Segments that belong to no transaction set land in `orphans` and are
  reported, rather than being dropped.

The alternative — refusing to parse and reporting one structural error — throws
away every content finding in the file, which is usually most of the value.

One place tolerance is deliberately *not* applied: delimiters read positionally
from the ISA must be punctuation. A truncated ISA puts the fixed offsets inside
the following segment, and accepting a letter there as the segment terminator
shreds the document into hundreds of nonsense findings. Requiring punctuation
makes the fallback scanner kick in instead.

## Checks are driven by specs, not duplicated

An 850 and an 810 differ, for most content rules, only in which segment carries
the line items and which element within it holds the quantity. Each transaction
set declares a `DocSpec` and the shared checks in `semantics/common.py` read
it. What genuinely differs gets its own module: the HL hierarchy and SSCC
digits of an 856, the AK loop of a 997.

A transaction set with no spec still gets the checks that do not need one —
dates, partner-required references. Diagnosing half of an unknown document
beats diagnosing none of it.

## Some things need more than one file

Three diagnoses cannot be made from a single document, by definition:

- `A04` — a duplicate control number needs a second document to be a duplicate
  of.
- `C05` — a missing acknowledgment is defined by absence.
- `C06`, `D03` — an out-of-order flow and a quantity mismatch are properties of
  a set of documents, not of any one of them.

These live in `scan.py` and are only reachable through `x12doctor scan`. The
sister project's `duplicateIsaControl` fault illustrates why: it rewrites ISA13
*and* IEA02 to the same value, so the document it produces is internally
consistent and no single-file check can find anything wrong with it.

`C05` defaults to reporting only groups whose ISA14 is `1`, because that field
is literally the one that asks to be acknowledged. `--missing-997` widens it to
every group, which is what you want when the partner agreement requires an
acknowledgment regardless — the common case, since most senders leave ISA14 at
`0` out of habit.

## The relationship with edi-fixtures

[`@adeloi/edi-fixtures`](https://github.com/Adeloi-Official/edi-fixtures)
generates realistic X12 documents and can break them in eighteen specific ways.
x12-doctor diagnoses documents. The two share one fault taxonomy, and each of
those eighteen faults maps to exactly one diagnostic code.

That gives the strongest test either project can have. The corpus under
`test/corpus` is generated by `edi-fixtures` at fixed seeds and committed, so
CI needs no Node. Each filename states the diagnosis it must produce:

```
850_seed42_A02_seCountWrong.edi
```

Two properties are asserted, and the first matters more:

**No false positives.** Every clean document must produce zero findings. A
linter that cries wolf gets switched off, and then it catches nothing. The
corpus covers three seeds and two line counts per transaction set for exactly
this reason.

**No false negatives.** Every broken document must produce its expected code
and only that code. A check that fires on the right file for the wrong reason
is not a working check.

The mapping table lives in `test/test_corpus.py` and is asserted against the
catalog's own `fixture` attributions, so the two cannot drift. A fault added to
`edi-fixtures` without a diagnosis here fails the build.

## What is deliberately absent

**No auto-fix.** Diagnosis only. Silently rewriting a production document is
how a wrong order becomes a wrong order nobody can trace.

**No bundled implementation guides.** Real partner guides are proprietary.
`PartnerProfile` is a generic shape, mirroring `definePartner()` in
`edi-fixtures` field for field so one profile describes a partner in both.

**No transaction sets beyond the order-to-invoice flow.** The taxonomy stays in
step with `edi-fixtures`; 860 and 865 arrive here when they arrive there.

**No full X12 element dictionary.** The unit-of-measure list is a working
subset of element 355, wide enough that a legitimate code is never flagged.
Carrying all eight hundred entries would add weight without adding a diagnosis
anyone has needed.
