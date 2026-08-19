# kep — a real-scale corpus from Kubernetes Enhancement Proposals

The second tier of the fixture suite. [`floodtwin`](../floodtwin) is synthetic,
has an exact answer key, and is the regression gate. This one has **no complete
answer key** — what it has is real documents, written by people who had no idea
they would be parsed.

```sh
./fetch.py                                   # pull the pinned corpus
../../freeze.py --project .
../../sweep.py xref --project . --doc kep-2400 --compare kep-2400-2024
../../obligations.py --project . --doc prr-template --mode chunk \
    --scopes "kep-content,prr-operational,process"
../../trace.py --project . --doc kep-2400 --scope prr-operational
```

## What it is

| slug | role | what |
|---|---|---|
| `prr-template` | requirements | The KEP template, including the **Production Readiness Review questionnaire** — a list of questions a KEP must answer before it can graduate |
| `kep-2400` | anchor | KEP-2400 node swap, current |
| `kep-2400-2024` | draft | The same KEP eighteen months earlier — the revision-diff target |
| `kep-1287` | reference | KEP-1287 in-place pod resources, a second deliverable under the same questionnaire |

Source: [kubernetes/enhancements](https://github.com/kubernetes/enhancements),
Apache-2.0. **Not vendored** — `fetch.py` pulls each file at a pinned commit SHA
and verifies a pinned content hash, so the corpus is reproducible without
carrying several thousand lines of upstream text in our history, and the licence
question stays upstream. `source/` is gitignored.

## Why this pairing

A requirements source and a deliverable that answers it, both real, both free:

- **A genuine questionnaire.** *"Can the feature be disabled once it has been
  enabled?"*, *"What are the reasonable SLOs for the enhancement?"* Not written
  as a test.
- **A genuine revision history.** 49 commits from a 2021 draft to 2025. The
  pinned pair drops one section while adding ten — real regression hidden by
  real growth.
- **One source, several deliverables.** Every KEP answers the same
  questionnaire, so `--scope` has a real target.
- **Weak ground truth for free.** KEP review comments are findings written by
  domain experts, timestamped and public. Not yet mined; see `labels.yaml`.
- **A false-positive detector.** KEP-2400 reached beta, so it has passed
  production-readiness review by people whose job is exactly this. Most PRR
  obligations *should* come back met. A run reporting many unmet against a
  graduated KEP is more likely wrong than the KEP is.

## What it caught on the first run

Three bugs that `floodtwin` structurally could not surface:

1. **Unnumbered headings were invisible.** `make_chunks` required a leading
   number, so all 831 lines of the requirements document parsed as a single
   `(front matter)` section. Every floodtwin heading is numbered, so the bug
   could never appear there. Fixed: 17 unlabelled chunks → 58 named sections.
2. **`xref` found nothing at all.** Its identifier patterns (`BM-G01`,
   `Appendix E`) are fitted to register-style architecture documents. KEPs use
   markdown headings and prose, and the sweep returned an empty result rather
   than saying so. It now reports when a document family carries no identifiers.
3. **Revision diffing compared only identifiers.** The real regression here is a
   *section* removal, which nothing checked. Heading-level diff added — and it
   then found 32 removed sections in a live engagement's revision pair, where
   identifier comparison alone had shown only dangling references.

That last one is the argument for this fixture in one line: **a tool validated
only on documents of one shape is fitted to that shape.**

## Limits

- **No complete answer key.** Only the partial truth in `labels.yaml`:
  structural facts, the real revision diff, and a six-item sampled set.
- **Retrieval regime is closer, not equal.** 91 chunks against a real
  architecture's 270 — 13.2% coverage at 12 passages versus 4.4%. Better than
  floodtwin's 16.7%, still not parity.
- **Requirements are instructional.** 60 HTML comment blocks and 28 checkbox
  items, so obligation extraction over-produces. That is realistic, and it is
  the point.

## Re-run 2026-08-19

`fetch.py` verified all four documents after a hash-comparison fix (it had been
reporting an unchanged corpus as corrupt). Inventory extraction with
`qwen3.6-35b-a3b`, one pass:

| doc | sections | authority found | exclusions |
|---|---|---|---|
| kep-2400 | 98 | 4 | 3 |
| kep-1287 | 144 | 3 | 0 |

DESIGN §3.29 used this corpus as the cross-genre control for polarity and
recorded 16 and 17 authority assertions. **That does not reproduce** — see the
note in §3.29. The fixture's own README already predicted the shape of the
problem: "a KEP yields about seven non-exclusion authority assertions across a
hundred sections, because KEPs barely assign component ownership". At four, the
corpus cannot serve as a control for anything measured as a proportion.

Worth keeping anyway, and for the reason it was built: it is 1,400 lines of
markdown written by people who never imagined it being parsed, and it exercises
the extractor against HTML comment blocks, checkbox lists and embedded code
fences that no synthetic fixture reproduces. It survived that. It just cannot
carry a statistical claim.
