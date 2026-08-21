# Adapter contract — revision 2, under review

**Nothing is implemented against this yet, and that is deliberate.** Every future
format binds to it, so it is being reviewed before it is written rather than
after three adapters exist and the addresses are already inside client
deliverables. Reversibility is the trigger for review here, not completion.

Revised after three independent reviews of revision 1, each with a different
lens: format fidelity, verification safety, and adoptability. All three reached
the same verdict — the two-adapter split and the `locate`/`navigate` separation
are right, and most of the signatures underneath them are wrong. Three of the six
have changed.

The reviews also found three live bugs in code this contract would wrap. Two are
fixed; one remains open and is called out below.

---

## What changed from revision 1, and why

| revision 1 | revision 2 | because |
|---|---|---|
| `parse -> list[str]` | `parse -> list[Unit]`, each carrying its own address | the pipeline's dominant flow is index → address, and revision 1 had no function for it |
| `addr` an opaque string | scheme-prefixed, adapter-declared | an address outlives the run: it goes into client workbooks and is consumed against a later revision |
| `meta` unspecified | closed core, persisted, **hashed** | a locator resolved via meta is not pinned unless meta is pinned |
| `write(rows, path, source)` | `emit` and `annotate`, with a capability flag | `writeback` patches one column preserving a workbook; that is a different verb, not a styling option |
| "adapters must not call models" | declared `fidelity` | the ban is unenforceable and the need (OCR) is real; a blanket ban gets violated silently |
| `role: comments` | `shape:` + optional `adapter:` | `role` is a closed set that `freeze.py` hard-exits on, and it already governs citation policy |
| invariant "no absolute paths" | invariant "no ambient state", enumerated | revision 1 patched the one bug it knew about and left the class open |

---

## Two adapter kinds

Unchanged and endorsed by all three reviews. The role decides, not the extension:
a spreadsheet is a document when it *is* the deliverable and a record source when
it carries the comments.

---

## Document adapter

```python
class Unit(NamedTuple):
    text: str        # ONE BLOCK. Must not contain a newline.
    addr: str        # this block's native address, scheme-prefixed

def parse(path) -> tuple[list[Unit], Meta]
def locate(units, meta, addr: str) -> Resolved | None
def navigate(units, meta, addr: str) -> Hint | None
def address(units, meta, start: int, end: int) -> str
```

### The unit is a block, not a visual line

This is the largest change and it is not cosmetic. A PDF extractor emitting one
unit per *visual line* on a two-column page produced this:

```
1 | 'The system shall retain Retention MUST be'
2 | 'audit records for a period verifiable by ORC-G01'
```

Two failures at once. A phrase spanning a line break scores **zero hits**, so
`closure` reports a requirement ABSENT that is plainly present. And the columns
concatenate into sentences that *do not exist* — which the verbatim-quote
validator then **passes**, because it checks the quote against the parsed text.
A fabricated sentence, verified by our own guarantee, cited to a client.

That corruption happens in `parse`, upstream of every check. So: one unit per
block of meaning — a paragraph, a list item, a table cell, a spreadsheet row —
and never a rendering artefact.

**No unit may contain a newline.** Frozen text is joined with `\n` and re-split
by consumers, and `"\n".join()` / `str.splitlines()` are not inverses:
`splitlines()` also breaks on `\x0b \x0c \x1c \x1d \x1e \x85    `.
`\x0c` is the standard PDF page break, so the very first adapter anyone writes
would index a different array than every renderer, silently, with a shift that
grows through the document.

### Addresses are scheme-prefixed and adapter-declared

```python
ADDR_SCHEMES = ("page", "line")     # each adapter declares what it emits
```

`line:4312` · `page:87` · `sec:8.10` · `cell:Comments!A12` · `id:#sec-parsing`

Revision 1 asked whether an opaque address was a real risk. It is, but not for
the reason given. Two schemes coexisting in one project is harmless. What is not
harmless is that an address is **persisted and long-lived** — it goes into
`register-map.yaml`, into the annotated workbook sent to the client, into bundle
exports — and is **generated against one revision and consumed against another**.

The existing grammar already accepts `5:9` as a line range. A page-based scheme
addressing "page 5, span 9" as `5:9` resolves silently to lines 5–9, and the
frozen text at lines 5–9 is rendered as verification. Plausible, well-formed,
wrong. Prefixing costs one line per adapter today and a corpus migration later.

Addresses must not contain `:` beyond the scheme separator — the current
serialization is `<slug>:<addr>` and consumers parse it with `rsplit(":", 1)`.

### `address()` is the direction the toolkit actually runs in

`closure.hits` produces integer indices; `writeback`, `register`, `bundle` and
`trace` render them as citations. Revision 1 had no function for index →
address, so a PDF hit could never be cited as "p. 87" in a client-facing cell —
the format's own addressing was reachable as an input and unreachable as output.

Carrying `addr` on the `Unit` makes the law structural rather than tested:

> `start <= locate(units, meta, units[start].addr).start <= end`

### `locate` returns a resolution, not a bare range

```python
class Resolved(NamedTuple):
    start: int
    end: int
    note: str | None      # "§4.1 is now §4"; "resolved to parent §8"
    exact: bool
```

Revision 1 said `locate` must return None rather than guess. Too strict:
`adjudicate.pick` deliberately falls back to a parent section when a subsection
is gone, which is correct behaviour and revision 1 would have forbidden it. The
distinction that matters is not resolved-or-not but **exact-or-inferred**, and an
inferred resolution must carry its reason to the reader.

### `navigate`, and the uniqueness rule revision 1 missed

```python
class Hint(NamedTuple):
    heading: str | None
    phrase: str | None     # MUST occur exactly once in the parsed text
```

A search phrase occurring forty times sends the reader to the wrong paragraph;
one occurring zero times sends them nowhere. Both fail silently, and this project
already learned that lesson once — `matrix.py` exists largely because a
non-distinctive search term fails in both directions without saying so. Extend
the phrase until unique, or return `None` and say why.

---

## Record adapter

```python
def records(path, sheet: str | None = None) -> list[Record]
def sheets(path) -> list[str]
def emit(rows, path) -> Report
def annotate(source, dest, key_column, column, values, *, overwrite=False) -> Report
SUPPORTS_ANNOTATE: bool
```

### The data model, stated because every gap here diverges silently

A reviewer implemented a CSV adapter against revision 1 in fifteen minutes, then
spent two hours discovering it returned nothing usable — twelve decisions had to
be guessed and about five of them fail silently. So:

- Values are `str`. Missing is `""`, **never `None`** — every caller does
  `row.get(k, "").strip()`, which raises on `None`.
- Every column name present on every record.
- **The header is consumed, not returned.** Ambiguity here drops the first real
  row, because every existing consumer does `rows[1:]`.
- Order is file order.
- **`__row__`** carries the native source row, 1-based, dunder-flanked so it
  cannot collide with a column name. *(Already implemented for `.xlsx`: without
  it, blank-row skipping made `writeback` write every value after a gap one row
  too high, into the wrong comment, in a workbook that opens cleanly.)*
- Blank rows are **retained** with `__row__` intact.
- Names are normalised: strip whitespace and BOM; duplicates become `Name`,
  `Name.1`, `Name.2` in file order; blank headers become `column_N`.
- Encoding is a fixed decode ladder (`utf-8-sig`, then a declared fallback).
  **Dialect sniffing is forbidden** — it is where two adapters silently disagree
  about the same bytes. Declare `encoding:` and `dialect:` in `corpus.yaml`.

### Two write verbs, because there are two operations

`writeback` does not emit a table. It patches **one column**, matching rows by
ID, preserving every other cell, skipping non-empty cells unless `--overwrite`,
refusing to touch the input, and reporting `written, skipped` as evidence it did
not clobber someone's work. None of that fits `write(rows, path, source=None)`,
and `-> None` discards the report.

CSV and JSON set `SUPPORTS_ANNOTATE = False` and callers fall back to `emit`
plus a sidecar. That is honest about a real capability difference rather than
hiding it behind a default argument.

### Invariant 4 is retained, with its migration named

`records` returns column **names**, never letters. Revision 1 stated this without
noting the cost: the current key space *is* letters, and fourteen consumers plus
their CLI flags (`--id-col A`, `--comment-col D`) depend on it. The migration is
part of the work, not a footnote.

---

## Registration

```yaml
documents:
  - path: register.xlsx
    role: reference        # evidentiary status — CLOSED SET, unchanged
    shape: rows            # prose | rows — which adapter kind
    adapter: xlsx-record   # optional explicit override
    encoding: utf-8-sig
```

Revision 1's `role: comments` **does not run** — `freeze.py` hard-exits on any
role outside `("anchor", "requirements", "draft", "reference")`, and DESIGN §3.32
defends that as deliberate because role drives citation behaviour. Overloading it
with a second, orthogonal axis was wrong.

`shape:` also answers a case revision 1 raised and could not express: requirements
arrive as prose *or* rows, and one field cannot say "requirements **and**
row-shaped".

`adapter:` solves wrong extensions, two adapters claiming one extension, and
content-detected formats without needing a sniffing protocol. It is needed:
`.csv` is already claimed on both sides, and `fixtures/floodtwin` alone holds a
comment matrix and three tool outputs, all `.csv`, indistinguishable by extension.

Extension resolution remains the default. A missing adapter is an error, never a
silent fall back to prose.

---

## Invariants

1. **`parse` is deterministic against ambient state.** No absolute paths, no
   wall-clock or timezone, no locale-dependent formatting, no unordered-container
   iteration, no environment variables, no network. *(Revision 1 named only
   absolute paths — one instance of the rule, patching the known bug and leaving
   the class open. `extract.py` now writes only the basename: the same bytes frozen
   from two different directories produce the same `text_sha256`, where they
   previously did not. The wider class — clocks, locales, unordered iteration — is
   stated but not yet enforced by anything.)*
2. **`parse` raises on a source it cannot faithfully represent.** Empty output is
   never a valid parse of a non-empty source. *(Implemented: `extract.py` writes
   the error to stderr — stdout IS the document as far as `freeze.py` is concerned
   — and exits non-zero, which `freeze.py` already checked. It previously printed
   the error to stdout and exited 0, so `freeze.py` hashed the words "CANNOT OPEN"
   as the deliverable and every tool then reported ABSENT for everything with full
   confidence.)*
3. **`locate` never invents.** Inferred resolutions set `exact=False` and carry a
   note; unresolvable returns `None`.
4. **`records` returns names, never letters**, and every record carries `__row__`.
   *(Partial: `__row__` is carried, and `matrix.columns()` now answers "which
   columns are these" in one place so the bookkeeping key stops leaking into
   consumers that enumerate keys — `to-html.py` did, and crashed on every input
   for as long as `__row__` existed. Letters-to-names remains unscheduled.)*
5. **Adapters declare fidelity** (below) — replacing revision 1's unenforceable
   ban on calling models.
6. **An address is meaningful only against `(adapter_id, adapter_version,
   text_sha256)`.** The manifest records the first two **per document**; the
   third is verified on every load. *(Implemented for `text_sha256`: `load_doc`
   now hashes and refuses on mismatch. It previously wrote the pin and never
   checked it — a hand-edited frozen file loaded silently.)*

---

## Fidelity, replacing the ban on models

```python
FIDELITY = "derived"        # or "transcribed"
```

`derived` — every character traceable to input bytes by a deterministic function.
`transcribed` — a model or lossy recogniser sat in the path.

The ban in revision 1 was the wrong shape. It is unenforceable (an adapter is a
module in a dict), and the need is real — OCR on a scanned PDF is a reasonable
thing to want. But consider what happens: a model transcribes pixels to text,
`freeze` pins that text, a second model makes claims about it, and `locate`
renders the "frozen text" as proof. The guarantee becomes circular — the second
model checked against the first model's output — and **the hash pins the
fabrication**. Every `--check` reports clean forever.

So: keep the hard ban for `derived`, and require `transcribed` to be recorded per
document, marked in **every** rendered output, and warned about by `--check`
rather than reported as "no drift".

---

## Conformance harness — required, not optional

Without this, invariants 1 and 2 are aspirations. An adapter is registered only
once it passes. **Implemented as `conformance.py`, run in CI.** Seven checks pass;
the two that need the unresolved `locate`/`Unit` half of the contract are reported
as NOT IMPLEMENTED rather than omitted, because a harness that silently skips part
of its own specification is the failure it exists to prevent.

It earned its place on the first run. The committed `.xlsx` twin failed the drift
check while its contents were byte-identical: `zipfile` stamps every entry with
the wall clock unless told otherwise, so no `.xlsx` this toolkit produced was
byte-reproducible — invariant 1's forbidden class, sitting in the code the whole
time. Two builds in the same second agreed, which is why "generate twice and
compare" had already passed. `writeback.zip_entry()` is now the one place a part
is written from a bare name, and the check inspects the stamps rather than
building twice and sleeping, because a test that needs a delay to fail is a test
that gets deleted.

- **Determinism**: parse the same fixture in two processes under
  `PYTHONHASHSEED=random`, from two working directories, under two locales and
  two timezones; assert byte equality.
- **Round trip**: `parse(p).units == resplit(join(parse(p).units))` — catches the
  embedded-newline class.
- **Address law**: `start <= locate(address(start, end)).start <= end`.
- **Record model**: values are `str`, no `None`, header consumed, `__row__`
  present and correct across a gap, blank rows retained.
- **Nasty inputs**: BOM, duplicate headers, blank header, ragged rows, embedded
  newline, empty file, header-only file.
- **Cross-format agreement**: the same table as `.csv`, `.xlsx` and `.docx` must
  produce identical records. *This one test would have caught four of the twelve
  ambiguities a reviewer hit, mechanically, in five minutes.*

The twins now exist: `fixtures/floodtwin/comments.xlsx` and `comments.docx`, both
built from `comments.csv` by `make-twins.py`. The workbook has  half its cells written as shared strings and half
inline, because Excel writes shared and `writeback.py` writes inline. Both are
committed rather than generated inside the test, because a generated twin can
only agree with its generator; the harness compares the committed bytes against a fresh
build, so drift is detected instead of papered over.

Two CI gaps to close alongside: the "every tool answers `--help`" step loops over
root `*.py` and would not see `adapters/`, and `--mutate` needs a generic adapter
mutation or the honesty gate does not cover contributed adapters.

---

## What consumers must do

The contract is not only about adapters. Three obligations fall on callers, each
because a review traced what a reviewer actually sees today:

1. **Render the resolved text.** No reviewer-facing artefact may present a
   citation without the passage it resolves to. *(Implemented: `render-assess.py`
   reproduces the cited lines alongside the reasoning, capped at two quotes so a
   review does not become a second copy of the deliverable. It previously printed
   rationale, heading and search phrase — navigation, where the job is
   verification.)*
2. **`locate` is the only way to turn an address into a range.** No consumer may
   index units directly.
3. **Discards are output, not log.** *(Implemented: a reader whose locators all
   failed now says so in the deliverable, and is distinguished from one where some
   citations merely did not resolve. `cites_rejected` was written to JSONL and read
   by exactly one tool — the one that un-rejects them — so a verdict resting on
   nothing read exactly like one resting on evidence.)*

---

## Still open

- **The letters-to-names migration** across fourteen consumers is unscheduled.
- **`conformance.py` covers the record half of the contract, not the unit half.**
  Address law and unit round trip need `locate`/`Unit`, which have no
  implementation. They are named and reported, not quietly dropped.
- **CI cannot see a contributed adapter.** The `--help` loop globs root `*.py` and
  would miss an `adapters/` package, and `--mutate` has no generic adapter
  mutation — so a contributed adapter sits outside the honesty gate that the
  built-in tools are inside.
- **`.docx` registers are read, not written.** `writeback.annotate` handles
  `.xlsx` and delimited files; a Word table can be assessed but the verdicts
  cannot be written back into it.

*Closed since revision 2, listed because the entries above are worth reading
against how these went:*

- ~~`extract.py` writes absolute paths into frozen text.~~ Now the basename.
- ~~`--refreeze` invalidates every locator, exits 0, and says nothing.~~ It now
  prints each document's old and new hash, the line-count delta, and that every
  locator shifts. It was already gated behind an explicit flag.
- ~~A comment matrix pasted into Word is a shape the toolkit cannot take.~~
  `read_docx_table` reads it, honouring `gridSpan` — a merged cell advances the
  grid, so every cell after it keeps the column it belongs to. `--prove` reported
  that mutation as MISSED at first, because the committed twin is a clean
  rectangle and nothing exercised a merge; the awkward shapes now live in their
  own check.
- ~~The conformance harness is specified but not implemented.~~ `conformance.py`,
  7/7, in CI. It found the wall-clock zip stamp on its first run.
- ~~The wider determinism class in invariant 1 is stated and unenforced.~~ Now
  enforced for what exists: readers and extractor are required to be byte-identical
  across hash seed, locale, timezone and working directory.
- ~~No CI gate has ever executed `extract.py`.~~ `fixtures/floodtwin/extractor-
  smoke.docx` exists and CI asserts the extractor produces text, writes no
  absolute path, and refuses an unreadable source. All fixture documents were
  `.md`, which takes the plain-text fast path, which is how two bugs survived in
  a repository with a mutation-checked test suite.
