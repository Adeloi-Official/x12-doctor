// SPDX-License-Identifier: Apache-2.0
//
// Regenerate test/corpus from @adeloi/edi-fixtures.
//
// The corpus is committed to this repository, so running this is not part of
// the test suite and CI needs no Node. It exists so the corpus can be rebuilt
// when edi-fixtures adds a fault, and so that anyone can verify the committed
// files were not hand-edited into passing.
//
//   node tools/build-corpus.mjs [--fixtures ../edi-fixtures]
//
// Every filename encodes the diagnosis the file must produce, which is what
// test/test_corpus.py asserts:
//
//   850_seed42_A02_seCountWrong.edi
//   ^^^ ^^^^^^ ^^^ ^^^^^^^^^^^^
//   |   |      |   `- the edi-fixtures fault that produced it
//   |   |      `----- the x12-doctor code it must be diagnosed with
//   |   `------------ the seed, so the content is reproducible
//   `---------------- the transaction set

import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { argv } from "node:process";

const flagIndex = argv.indexOf("--fixtures");
const fixturesPath = resolve(
  flagIndex === -1 ? "../edi-fixtures" : argv[flagIndex + 1],
);

const {
  po850,
  ack855,
  asn856,
  invoice810,
  fa997,
  targetFrom,
  scenario,
  faults,
} = await import(join(fixturesPath, "dist/index.js"));

const CORPUS = resolve("test/corpus");
const SEED = 42;

rmSync(CORPUS, { recursive: true, force: true });
for (const dir of ["clean", "faults", "flows"]) {
  mkdirSync(join(CORPUS, dir), { recursive: true });
}

let written = 0;
function write(relative, contents) {
  writeFileSync(join(CORPUS, relative), contents, "utf8");
  written += 1;
}

// ---------------------------------------------------------------------------
// Clean documents — the false-positive corpus. Every one of these must come
// back from `diagnose()` with zero findings, which is the single most
// important property a linter has.
// ---------------------------------------------------------------------------

const builders = {
  850: (opts) => po850(opts),
  855: (opts) => ack855({ ...opts, poNumber: "PO173006" }),
  856: (opts) => asn856({ ...opts, poNumber: "PO173006" }),
  810: (opts) => invoice810({ ...opts, poNumber: "PO173006" }),
};

for (const seed of [42, 7, 1234]) {
  for (const [docType, build] of Object.entries(builders)) {
    for (const lines of [1, 3]) {
      write(
        `clean/${docType}_seed${seed}_lines${lines}.edi`,
        build({ seed, lines }).build(),
      );
    }
  }
  write(
    `clean/997_seed${seed}.edi`,
    fa997({
      seed,
      target: targetFrom("850", {
        isa: "000000001",
        gs: "123456",
        st: "0001",
      }),
    }).build(),
  );
}

// ---------------------------------------------------------------------------
// Single-document faults
// ---------------------------------------------------------------------------

// duplicateIsaControl is deliberately absent from this list. It rewrites ISA13
// and IEA02 to the same value, so the document it produces is internally
// consistent and no single-file check can see anything wrong with it. It needs
// a second document to be a duplicate of, and lives under scans/ below.

/** Faults that can be applied to any document, and the code they must yield. */
const universal = [
  ["A01", "isaTruncated", () => faults.isaTruncated()],
  ["A02", "seCountWrong", () => faults.seCountWrong()],
  ["A03", "controlNumberMismatch", () => faults.controlNumberMismatch()],
  ["A05", "nonstandardSeparators", () => faults.nonstandardSeparators()],
];

// dateFormatShort only shortens DTM dates, so it is a no-op on the transaction
// sets that carry their dates in BAK05 or BIG01/BIG03 instead. Applying it to
// an 855 or an 810 produces a file byte-identical to the clean one, which would
// make for a fixture asserting nothing.
const dtmOnly = [["B03", "dateFormatShort", () => faults.dateFormatShort()]];

/** Faults tied to line items. */
const lineLevel = [
  ["B01", "uomNonstandard", () => faults.uomNonstandard()],
  ["B02", "impliedDecimals", () => faults.impliedDecimals()],
  ["B04", "cttMismatch", () => faults.cttMismatch()],
  ["B05", "missingRef", () => faults.missingRef("DP")],
];

/** 856-only faults. */
const asnOnly = [
  ["D01", "hlOrphan", () => faults.hlOrphan()],
  ["D02", "ssccInvalid", () => faults.ssccInvalid()],
  ["D04", "asnAfterDelivery", () => faults.asnAfterDelivery()],
];

const perDocument = {
  850: [...universal, ...lineLevel, ...dtmOnly],
  855: [...universal, ...lineLevel],
  // impliedDecimals on an 810 is the case the TDS cross-check catches exactly,
  // rather than by heuristic. It is the most valuable single fixture here.
  810: [...universal, ...lineLevel],
  856: [
    ...universal,
    ...dtmOnly,
    ...asnOnly,
    ["B01", "uomNonstandard", () => faults.uomNonstandard()],
  ],
};

for (const [docType, cases] of Object.entries(perDocument)) {
  for (const [code, name, make] of cases) {
    const built = builders[docType]({ seed: SEED, lines: 3 })
      .with(make())
      .build();
    write(`faults/${docType}_seed${SEED}_${code}_${name}.edi`, built);
  }
}

// ---------------------------------------------------------------------------
// Flow-level faults — these need the whole order-to-invoice bundle, because a
// missing acknowledgment or an out-of-order interchange is not a property any
// single file has.
// ---------------------------------------------------------------------------

const flows = [
  ["clean", null, null],
  ["C05_fa997Missing", "C05", () => faults.fa997Missing()],
  ["C03_fa997Rejected", "C03", () => faults.fa997Rejected()],
  ["C02_fa997UnknownGroup", "C02", () => faults.fa997UnknownGroup()],
  ["C06_outOfOrderInterchange", "C06", () => faults.outOfOrderInterchange()],
  ["D03_asnQtyMismatch", "D03", () => faults.asnQtyMismatch()],
];

for (const [dirName, _code, make] of flows) {
  const dir = join("flows", dirName);
  mkdirSync(join(CORPUS, dir), { recursive: true });

  let builder = scenario.orderToInvoice({ seed: 7, lines: 3 });
  if (make) builder = builder.with(make());
  const bundle = builder.build();

  const order = { po: "850", ack: "855", asn: "856", invoice: "810", fa: "997" };
  for (const [key, docType] of Object.entries(order)) {
    const contents = bundle[key];
    // fa997Missing produces a null acknowledgment; the absence is the fixture.
    if (contents == null) continue;
    write(join(dir, `${docType}_${key}.edi`), contents);
  }
}

// ---------------------------------------------------------------------------
// Directory-level faults — a duplicate control number needs two documents.
// ---------------------------------------------------------------------------

const duplicateDir = join("scans", "A04_duplicateIsaControl");
mkdirSync(join(CORPUS, duplicateDir), { recursive: true });
for (const seed of [42, 99]) {
  // Two genuinely different orders, both stamped with the same interchange
  // control number — a counter that was reset between the two sends.
  write(
    join(duplicateDir, `850_seed${seed}.edi`),
    po850({ seed, lines: 2 }).with(faults.duplicateIsaControl()).build(),
  );
}

const uniqueDir = join("scans", "clean_controls");
mkdirSync(join(CORPUS, uniqueDir), { recursive: true });
for (const seed of [42, 99]) {
  write(join(uniqueDir, `850_seed${seed}.edi`), po850({ seed, lines: 2 }).build());
}

// ---------------------------------------------------------------------------
// Hand-written edge cases the generator cannot produce, because edi-fixtures
// only ever emits documents that are broken in one deliberate way.
// ---------------------------------------------------------------------------

mkdirSync(join(CORPUS, "edge"), { recursive: true });

write("edge/empty.edi", "");
write("edge/not_x12.edi", "id,name,qty\n1,Hex Bolt,228\n2,Shop Towel,5\n");
write(
  "edge/truncated_mid_segment.edi",
  po850({ seed: SEED, lines: 3 }).build().slice(0, 240),
);
write(
  "edge/no_trailers.edi",
  po850({ seed: SEED, lines: 2 })
    .build()
    .replace(/(SE|GE|IEA)\*[^~]*~/g, ""),
);

console.log(`wrote ${written} files to ${CORPUS}`);
