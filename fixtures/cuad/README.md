# cuad — expert-labelled absence, and the right quote

The Contract Understanding Atticus Dataset: 510+ commercial contracts, 13,000+
annotations across 41 clause categories, written by lawyers at
[The Atticus Project](https://www.atticusprojectai.org/cuad). CC BY 4.0.
**Nothing here is committed** — `fetch.py` downloads and slices it on demand.

Every other fixture in this repository tests the same bias. floodtwin and the
live reviews it stands in for are corpora where most obligations are *met*, and a
tool measured only against those has been shown one half of the problem. CUAD is
the other half, and it is labelled by people who do this for a living.

## What only this fixture can measure

**Absence, with a ground truth.** About 79% of the clause/contract pairs are
marked `is_impossible` — a lawyer read the contract and recorded that the clause
genuinely is not there. That makes it possible to ask the question the whole
toolkit turns on: when the tool says "unmet", is it right? And the harder one:
when the clause *is* present, does the tool avoid saying "unmet" anyway?

**Whether the quote is the right quote.** Where a clause is present, the
annotation marks the exact span. floodtwin only ever checked that a quote was
verbatim from the passages supplied — never that it was the *correct* passage. A
verdict can be right for entirely the wrong reason, and until this fixture
existed that was invisible.

## Running it

```sh
./fetch.py                      # download, verify the pinned hash, slice
./fetch.py --contracts 5        # a wider slice
./fetch.py --update             # print the archive hash, to re-pin after an upstream change

dossier coverage . --doc <slug>
./score-spans.py --contract <slug> --coverage coverage-<slug>.csv
```

Three contracts by default — small, medium and large — so the corpus stays
runnable while still covering the retrieval regime honestly. The archive is
hash-pinned; a silent upstream change fails rather than quietly altering results.

## What it found

`DESIGN.md` §3.18. The same 41 questions, the same model, two contracts, with
retrieval covering the whole document in both cases so retrieval is not the
variable:

| | 8k chars | 52k chars |
|---|---|---|
| precision of "unmet" | 100.0% | **87.1%** |
| recall on absent | 87.9% | 90.0% |
| overall accuracy | 90.2% | **82.9%** |
| quote tightness (mean Jaccard) | 0.62 | 0.77 |
| always-unmet baseline, precision | — | 73.2% |

*(Re-measured 2026-08-19 with `qwen3.6-35b-a3b`. The original run read 97.0% →
83.3% on precision; a different model, the same direction.)*

As the document grows the model becomes *more* willing to assert absence and
*less* often right to. It reported that the passages contained no mention of a
clause the contract does contain — from material it had been shown and had not
finished reading.

That is the measured form of the argument in §1: an absence claim is the least
reliable thing a reader can produce, and it degrades with length. It is why the
absence path demands a deterministic synonym sweep instead of accepting a
judgement.

## Known weaknesses of this fixture

Stated here because the numbers above are quoted elsewhere and should not be
quoted without them.

- **The majority-class baseline is now reported.** 30 of the large contract's 41
  clauses are absent, so answering "unmet" to everything scores 73.2% precision
  and 100% recall. The re-measured 87.1% is about fourteen points over that
  floor. Any absence result on this corpus should be read against it.
- **The span metric now reports two numbers, and the old one meant less than it
  looked.** "Span fidelity 100%" was gold-token recall — it says the quote did
  not *miss* the annotated span, and says nothing about whether it quoted
  tightly, because quoting the whole contract scores 1.0 against every span in
  it. A mean Jaccard is reported alongside it: 1.0 means the quote *is* the span,
  0.01 means a haystack containing it. Both are kept rather than one replacing
  the other — the 0.30 threshold was calibrated against coverage, and a correctly
  tight quote of a short span scores about 0.29 by Jaccard, so swapping the
  metric outright would have failed every honest answer.
- **n = 41, no confidence intervals.** 97.0% against 83.3% on 41 items has
  overlapping Wilson intervals. The direction is consistent and the mechanism is
  convincing; the two numbers are not statistically separable as they stand.
- **One model.** The length effect is measured on a single model, so it is a
  property of that model on this data until someone runs a second.

## Why it is not committed

`source/`, `parsed/`, `obligations.yaml`, `answers.yaml` and any `coverage*.csv`
are gitignored. CUAD is CC BY 4.0 and redistributable, but vendoring a slice
would pin this repository to one snapshot of someone else's corpus and hide
upstream changes behind our own copy. Fetching against a pinned hash keeps the
provenance visible: if the upstream archive changes, the fetch fails and says so.
