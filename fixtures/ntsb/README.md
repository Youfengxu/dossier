# ntsb — a third party rules on whether the claim was good enough

NTSB safety recommendations and their adjudicated outcomes, from the public CAROL
API. No key, no auth, no rate limit encountered. **Nothing here is committed** —
`fetch.py` pulls the slice and verifies a pinned digest.

Every other fixture in this repository is requirements-versus-deliverable. This
is the other shape, and the one that is hard to find in public:

| | |
|---|---|
| **obligation** | the recommendation text, issued to a named addressee |
| **claim** | the addressee's own letters saying what they did |
| **adjudication** | NTSB's ruling on whether that was good enough |

The third leg is what makes it worth having. Neither party to the dispute
produces the label: the regulator raises the point, the recipient claims to have
met it, and the regulator rules separately on the claim.

## What only this fixture can measure

**A confident claim that was rejected.** On A-10-012 the FAA writes that it
"has effectively addressed" the recommendation and considers its actions
complete. NTSB's status for that same record is **Open — Unacceptable Response**.
That is the exact pattern a review toolkit exists to catch, adjudicated by
someone with no stake in either answer, and it is one API call away.

**Class balance that does not flatter a lazy tool.** Measured across the default
slice:

| | |
|---|---|
| not_addressed | **11** |
| addressed | **13** |
| excluded as not a verdict on the claim | 1 |

Most compliance corpora run 5–10% negative, where answering "addressed" to
everything scores well and measures nothing. Report the majority-class baseline
next to any result from this fixture anyway — it is 54%, and a number that does
not beat it comfortably is not evidence of anything.

## Running it

```sh
./fetch.py                 # fetch the slice, verify the pin, write the corpus
./fetch.py --check         # verify only, write nothing
./fetch.py --update        # print the digest, to re-pin after an upstream change
```

The default slice is 25 recommendations from **one issuance letter to one
addressee**. That is deliberate: positives and negatives share an author, a
subject and a decade, so a tool cannot separate the classes on register instead
of substance. Drawing the two classes from different sources is the easiest way
to build a corpus that measures writing style and looks like it measures
compliance.

## How the labels map

NTSB's vocabulary is richer than the review scale, and three of its values mean
the same thing here — the addressee satisfied the recommendation by the method
asked for, by another route, or beyond it.

| NTSB status | maps to |
|---|---|
| Closed — Acceptable Action | addressed |
| Closed — Acceptable Alternate Action | addressed |
| Closed — Exceeds Recommended Action | addressed |
| Closed — Unacceptable Action | not_addressed |
| Open — Unacceptable Response | not_addressed |
| Closed — Reconsidered | **excluded** |

`Closed — Reconsidered` is excluded rather than forced onto the scale. It means
NTSB withdrew or reframed the recommendation, not that it judged the response, so
scoring a tool against it would be scoring it on a question nobody asked.

## Known weaknesses

Stated here because the numbers above get quoted elsewhere.

- **There is no revised document.** NTSB tracks correspondence and the artefacts
  it cites, not a redlined text. This fixture exercises *does the claim hold up*
  and not *what changed between two drafts*. For the latter, use a fixture with
  two revisions.
- **The claim field is the addressee's latest letter**, not the whole exchange. A
  recommendation with eleven rounds is compressed to its most recent position,
  which is the fair thing to judge but discards how the position moved.
- **Correspondence from NTSB is excluded from the claim on purpose.** It contains
  the adjudication, and feeding it to a tool being tested on the adjudication
  hands over the answer.
- **n = 24 labelled.** Enough to catch a tool that is badly wrong, not enough to
  separate two tools that are close.
- **Statuses move.** These are live records; a recommendation reconsidered next
  year changes its label. The digest is pinned so that shows up as a mismatch
  rather than as a silent shift in your results.

## First run — 2026-08-21

Baseline, untuned. Both readers ran `assess.py` over one document containing all
25 claims, with the recommendations as the register, through the same pipeline the
engagement uses. Every citation resolved: **0 of 50 verdicts rested on an
unverified locator.**

| | strict | lenient | vs baseline (54.2%) |
|---|---|---|---|
| qwen3.6-35b-a3b | 17/24 = 70.8% | 79.2% | +16.7 |
| gemma-4-31b-qat | 17/24 = 70.8% | 75.0% | +16.7 |

Identical accuracy, different error profiles. qwen3.6 caught **11 of 11** genuine
failures and over-flagged 7 acceptable ones; gemma missed 2 failures and
over-flagged 5. For a review tool the first profile is the better one — a missed
failure costs more than an extra flag — and the two scores being equal hides that
completely, which is the argument for reporting profiles rather than accuracy.

### Disagreement did its job

    where the readers AGREE (18 rows)     14/18 = 78% correct
    where they DISAGREE  (6 rows)         6/6 had at least one correct answer

In every disagreement the right answer was on the table, so a reviewer reading
only those six rows resolves all six. That is the whole premise of putting
verdicts side by side rather than merging them, measured rather than asserted —
on n=6, which is encouraging and not yet evidence.

### The failure the consensus cannot see

Four rows had both readers agreeing and both wrong. Broken down by NTSB's own
status, both readers pooled:

| NTSB status | correct |
|---|---|
| Closed — Acceptable **Alternate** Action | **1/8 = 12%** |
| Closed — Acceptable Action | 9/14 = 64% |
| Closed — Unacceptable Action | 18/20 = 90% |
| Open — Unacceptable Response | 2/2 = 100% |
| Closed — Exceeds Recommended Action | 4/4 = 100% |

**The tools are near-perfect at detecting failure and nearly blind to compliance
by another route.** Note that "Exceeds Recommended Action" scores 4/4 — so this is
not a general intolerance of deviation. It is specific to substitution of method:
asked for X, the addressee did Y, achieved the intent, and both readers called it
unaddressed.

That is a prompt-level defect, not a model one — it shows up identically in two
unrelated model families. `assess.py` asks whether the document does what the
comment asked for, where the question a reviewer actually needs is whether it
achieves what the comment required.

**Not fixed here on purpose.** With 8 alternate-action rows out of 24, rewriting
the prompt and re-scoring against this same slice would fit the toolkit to this
answer key and the number would stop meaning anything. The fix should be validated
against a fixture that did not diagnose it.
