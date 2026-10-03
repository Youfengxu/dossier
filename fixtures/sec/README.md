# sec — the regulator comes back, comment by comment, to the answers that were not enough

SEC staff comment letters where a second-round letter comes back to the first
round's numbered comments. Public, no key. **Nothing is committed** — `fetch.py`
pulls a slice and verifies a pinned digest.

| | |
|---|---|
| **obligation** | numbered comments raised by the staff |
| **claim** | the company's reply, asserting it addressed them |
| **adjudication** | the staff's next letter, on each comment it cites again |

The staff cite a comment by number and say what they make of the answer:

```
We note your response to prior comment 1, which we reissue.      -> not_addressed
We note your response to prior comment 11 and re-issue in part.  -> partial
We note your response to prior comment 7. Please revise ...      -> addressed
```

The label is read from the whole numbered comment that cites it, not from the
sentence with the number in it, because the two can be a full stop apart:

```
We note your response to prior comment 24. We reissue the first
bullet of the prior comment in part.                             -> partial
```

## The frame does the balancing

This is the useful finding, and it was not the plan. The design called for taking
every negative and oversampling to match, because across all comment letters
genuine negatives run at a few percent — a corpus where answering "addressed" to
everything scores well and measures nothing.

That work turned out to be unnecessary. **Restricting the frame to letters that
engage with prior comments changes the base rate by itself**: measured over the
default slice, **31% of the comments cited again are reissued in whole or in
part** (32 of 102).

| | |
|---|---|
| addressed | 70 |
| partial | 19 |
| not_addressed | 13 |

No reweighting, no thumb on the scale — just asking the question of the documents
where it was actually asked. A frame chosen for relevance turned out to be a
frame that balances.

**Quote both numbers or neither.** 31% holds within this frame. Across all
comment letters it is a few percent, and a result read against the wrong
population will look like calibration it has not earned.

## What `addressed` means here

**Not that the staff were satisfied.** The staff do not write back about an
answer they accept. The comment is not mentioned again, and it is not a row in
this fixture. Every row is a comment the staff came back to, and `addressed` is
the mildest of the three ways of coming back: cited, and not reissued.

The eight first-round letters of the default slice raised 237 comments. The
second-round letters cite 102 of them again, and those are the rows:

| | | what the staff did |
|---|---|---|
| addressed | 70 | cited it and did not reissue it. 68 go on to ask for something more; two say only that the answer is still being looked at ("We continue to consider your response to prior comment 6.") |
| partial | 19 | reissued it in part |
| not_addressed | 13 | reissued it |

The other 135 are not cited again. That is where the staff's acceptance is, if
it is anywhere, and this fixture does not hold it.

So a tool that scores well here has shown that it can tell a reissue from a
further request. It has not shown that it can tell an adequate answer from an
inadequate one: no row is an answer the staff accepted.

## Running it

```sh
export DOSSIER_SEC_UA="Your Name your@email"
./fetch.py                  # default slice: 8 letters, ~100 comments
./fetch.py --letters 16     # wider
./fetch.py --check          # verify the pin, write nothing
./fetch.py --update         # print the digest, to re-pin once the labels are read
```

**No default user-agent is shipped.** The SEC permits scripted access and asks
that it identify itself; a fixture that sends someone else's address on your
behalf is worse than one that refuses to start.

`comments.csv` has one row per cited comment. `staff_sentence` is what decided
the verdict: the sentence that cites the comment and, where another one reissues
it, that one too, with `[...]` for anything between them. `staff_comment` is the
whole numbered comment they came from and `round2_comment` its number, so a
verdict can be checked without fetching the letter. `labels.json` has the
counts, including what the run did not label and why.

## What this fixture does not give you

Stated plainly, because the balance figures above will get quoted.

- **The first-round letter and the company's reply are identified, not
  extracted.** Their accession numbers are recorded so the chain is traceable,
  but their text is not pulled. Document naming varies per filing, and
  roughly a third of what EDGAR files as a comment letter is something else —
  a notice that a filing will not be reviewed, or a closing letter — with no
  metadata distinguishing them. Extraction is left to whoever needs it.
- **The first-round letter is the one the second-round letter names**, by date
  ("any references to prior comments are to comments in our November 29, 2023
  letter"), and `fetch.py` fetches it to confirm the date printed on it. A
  second-round letter that names none is left unpaired, not paired with the
  nearest staff letter before it: 5 of the 40 letters in a wider slice name
  none. The reply is the first correspondence the company filed between the two
  letters, found by form type and date and not read. In the default slice all
  eight first-round letters are found, and seven replies; the eighth company
  filed its reply inside an amended registration statement, so the 14 rows of
  that exchange carry no reply.
- **So this is obligation-reference plus adjudication**, not the full triple. It
  tests whether a tool agrees with the regulator's verdict, given the staff's own
  restatement — not whether it can read the original comment and judge the reply.
- **The label is behavioural, not truth-functional.** A reissue means the staff
  found the response inadequate and said so in that word. A comment asked about
  again without the word is `addressed`, and may have gone down no better: "As
  previously noted, please revise to provide all of the property-related
  disclosure" is `addressed`. A comment quietly dropped is not a row at all, and
  dropping it may be satisfaction or may be deprioritisation. Negatives are the
  stronger label; `addressed` is the weaker one, and they are not symmetric.
- **Only a comment cited by number is a row**: "prior comment 7", "prior comment
  seven", or "your response to comment 7". A citation of a comment in an older
  letter ("prior comment 15 of our letter dated March 14, 2023") is not counted
  as a comment of the letter being answered: three in the default slice. A
  letter whose comment numbering cannot be followed is set aside whole: none in
  the default slice, and none in a wider one of 40 letters. `labels.json` counts
  both, and the run prints each sentence it did not count.
- **The SEC disclaims it explicitly** — its boilerplate says the staff's action or
  absence of action implies no finding. The label is a fact about what the staff
  did, not an endorsement.
- **n = 102 comments across 8 letters.** Enough to catch a tool that is badly
  wrong, not enough to separate two that are close.
- **Search ranking moves**, so a re-fetch may draw a different slice. The digest
  is pinned so that surfaces as a mismatch rather than as a quiet shift.

## What this corpus taught the toolkit, twice

The staff write "reissue", "re-issue" and "reiterate" interchangeably. A pattern
matching only the unhyphenated form labels every re-issued comment as **accepted**
— silently, and in the direction that flatters whatever is being measured. That
bug was written here first and caught only because the extractor prints the source
sentence beside each verdict instead of reporting a count. It is the fourth
too-narrow checker this project has found in itself, and the reason the checkers
now have adversarial tests of their own.

Then it happened again, and the printed sentence did not catch it. On 3 October
2026 the same eight exchanges were rebuilt from the full text of the letters,
and four of this fixture's 100 rows were wrong, every one of them labelled
`addressed`:

- two where the reissue comes in a later sentence than the number;
- one where the letter spells it "We resissue prior comment 30 in full";
- one where "prior comment 15 of our letter dated March 14, 2023" was counted as
  comment 15 of the letter being answered, which is a different comment.

One exchange was also paired with the wrong first-round letter: the nearest staff
letter before it, which was a notice that the review of another filing had
closed. Each wrong verdict had its source sentence printed beside it, and each
read as the staff noting an answer and moving on: what made it wrong was a later
sentence, or which letter the comment belonged to. The reader now takes the
whole numbered comment and pairs by the date the letter names.

Reading 40 letters with the corrected reader, instead of the eight it had been
written against, then found two more ways a citation was missed, and both lose
reissues: a number spelled out ("We note your response to prior comment five and
reissue it in part") and a comment cited without the word "prior" ("We note
your response to comment 4 and reissue it in part"). Four of that slice's 375
rows come from the first and six from the second, three of the ten reissued in
part. The second added three rows to the default slice, all `addressed`.

`tests/test_sec_fixture.py` holds each of these cases, and `run-tests.py
--mutate` requires every one of them to fail when its fix is taken out.
