# sec — the regulator says, comment by comment, whether the answer was good enough

SEC staff comment letters where a second-round letter re-adjudicates the first
round's numbered comments. Public, no key. **Nothing is committed** — `fetch.py`
pulls a slice and verifies a pinned digest.

| | |
|---|---|
| **obligation** | numbered comments raised by the staff |
| **claim** | the company's reply, asserting it addressed them |
| **adjudication** | the staff's next letter, comment by comment |

The adjudication is a sentence, and the sentence is the label:

```
We note your response to prior comment 7.                        -> addressed
We note your response to prior comment 1, which we reissue.      -> not_addressed
We note your response to prior comment 11 and re-issue in part.  -> partial
```

## The frame does the balancing

This is the useful finding, and it was not the plan. The design called for taking
every negative and oversampling to match, because across all comment letters
genuine negatives run at a few percent — a corpus where answering "addressed" to
everything scores well and measures nothing.

That work turned out to be unnecessary. **Restricting the frame to letters that
engage with prior comments changes the base rate by itself**: measured over the
default slice, **29% of adjudicated comments are reissued in whole or in part.**

| | |
|---|---|
| addressed | 71 |
| partial | 17 |
| not_addressed | 12 |

No reweighting, no thumb on the scale — just asking the question of the documents
where it was actually asked. A frame chosen for relevance turned out to be a
frame that balances.

**Quote both numbers or neither.** 29% holds within this frame. Across all
comment letters it is a few percent, and a result read against the wrong
population will look like calibration it has not earned.

## Running it

```sh
export DOSSIER_SEC_UA="Your Name your@email"
./fetch.py                  # default slice: 8 letters, ~100 comments
./fetch.py --letters 16     # wider
./fetch.py --check          # verify the pin, write nothing
```

**No default user-agent is shipped.** The SEC permits scripted access and asks
that it identify itself; a fixture that sends someone else's address on your
behalf is worse than one that refuses to start.

## What this fixture does not give you

Stated plainly, because the balance figures above will get quoted.

- **The first-round letter and the company's reply are identified, not
  extracted.** Their accession numbers are recorded on every row so the chain is
  traceable, but their text is not pulled. Document naming varies per filing, and
  roughly a third of what EDGAR files as a comment letter is something else —
  a notice that a filing will not be reviewed, or a closing letter — with no
  metadata distinguishing them. Extraction is left to whoever needs it.
- **So this is obligation-reference plus adjudication**, not the full triple. It
  tests whether a tool agrees with the regulator's verdict, given the staff's own
  restatement — not whether it can read the original comment and judge the reply.
- **The label is behavioural, not truth-functional.** A reissue means the staff
  found the response inadequate. A comment quietly dropped means they stopped
  asking, which may be satisfaction or may be deprioritisation. Negatives are the
  stronger label; positives are the weaker one, and they are not symmetric.
- **The SEC disclaims it explicitly** — its boilerplate says the staff's action or
  absence of action implies no finding. The label is a fact about what the staff
  did, not an endorsement.
- **n = 100 comments across 8 letters.** Enough to catch a tool that is badly
  wrong, not enough to separate two that are close.
- **Search ranking moves**, so a re-fetch may draw a different slice. The digest
  is pinned so that surfaces as a mismatch rather than as a quiet shift.

## One thing this corpus taught the toolkit

The staff write "reissue", "re-issue" and "reiterate" interchangeably. A pattern
matching only the unhyphenated form labels every re-issued comment as **accepted**
— silently, and in the direction that flatters whatever is being measured. That
bug was written here first and caught only because the extractor prints the source
sentence beside each verdict instead of reporting a count. It is the fourth
too-narrow checker this project has found in itself, and the reason the checkers
now have adversarial tests of their own.
