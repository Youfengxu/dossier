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
| precision of "unmet" | 97.0% | **83.3%** |
| recall on absent | 97.0% | **100.0%** |
| span fidelity | 100% | 100% |

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

- **No majority-class baseline.** At 79% absent, a classifier that answers
  "unmet" to everything scores 0.79 precision and 1.00 recall. The 83.3% above is
  roughly four points over that floor, not 83 points over zero. Reported without
  the baseline it reads far stronger than it is.
- **The span metric has no precision term.** It computes gold-token recall over
  deduplicated token sets, thresholded at 0.30, so a quote that is a *superset*
  of the annotated span scores 1.0. "Span fidelity 100%" therefore says the model
  did not miss the span; it does not say the model quoted it tightly. The
  prompt's instruction to quote short is probably doing more work than the metric
  is. Character-offset IoU, or adding a precision term, would fix it.
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
