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

## Second run — prompt revised, hypothesis refuted

The first run diagnosed `assess.py` as asking whether the document did *what was
asked* rather than *what was required*, and predicted that saying so explicitly
would recover the alternate-route class. One change was made to the verdict
definitions and measured once. It did not.

| NTSB status | qwen v1 | qwen v2 | gemma v1 | gemma v2 |
|---|---|---|---|---|
| Closed — Acceptable Action | 4/7 | **5/7** | 5/7 | **6/7** |
| Closed — Acceptable **Alternate** Action | 0/4 | 0/4 | 1/4 | 1/4 |
| Closed — Exceeds Recommended Action | 2/2 | 2/2 | 2/2 | 2/2 |
| Closed — Unacceptable Action | 10/10 | 10/10 | 8/10 | 8/10 |
| Open — Unacceptable Response | 1/1 | 1/1 | 1/1 | 1/1 |
| **overall** | 17/24 | **18/24** | 17/24 | **18/24** |

Both models gained exactly one row and neither lost any failure detection — qwen
still catches 11 of 11. But the gain came from plain compliance, not from the class
the change targeted. **The alternate-route blindness is untouched by instruction.**

Three model families and two prompts now fail it the same way, Laguna S 2.1
included at 1/4. The likelier reading is no longer "the question is worded wrong"
but "recognising that method Y satisfies a requirement stated as method X is a
judgement these models do not reliably make". That is a different kind of problem
and probably needs a different technique — asking separately what outcome the
comment requires, then whether that outcome is reached — rather than better wording
in one call.

**The revised prompt is kept**, because asking about the outcome rather than the
method is more correct regardless of the score. It is not kept because the number
moved: +1 of 24 is inside the noise this fixture warns about, and the change has
not been validated anywhere else. Validate on SEC (100 rows) before believing it.

## Screening five models on the enriched slice — 2026-08-22

98 rows, 34 alternate-route positives with matched controls, majority baseline
55.1%. Scored on the pair, because either half alone is buyable by having no
opinion.

| run | alt-route | failures | overall |
|---|---|---|---|
| **Qwen3-235B-A22B Instruct** | **18/34 = 53%** | 37/54 = 69% | **65.3%** |
| MiniMax M3 (428B) | 9/34 = 26% | 43/54 = 80% | 58.2% |
| Nemotron 3 Super 120B | 6/34 = 18% | 42/54 = 78% | 57.1% |
| Qwen3.8-27B | 7/34 = 21% | 39/54 = 72% | 53.1% |
| Qwen3-235B-A22B **Thinking** | 9/34 = 26% | 29/54 = 54% | **45.9%** |

### Thinking mode made it worse, and that was the hypothesis

The Instruct/Thinking pair is the same weights with reasoning on and off — the one
controlled comparison in the set. The prediction was that reasoning would help,
because judging whether method Y satisfies a requirement written as method X is an
equivalence judgement and that is what reasoning modes are for.

It halved the alternate-route score (53% -> 26%), cost fifteen points of failure
recall (69% -> 54%), and produced **the only run below the majority baseline**.
Turning reasoning on made the model worse at every part of the task.

### What the spread says

Failure recall clusters at 54-80% across every model. Alternate-route ranges 18%
to 53%. So the models do not differ much in catching failure — they differ almost
entirely in whether they can see compliance reached by another route, which is
what this fixture was built to isolate and is evidence it measures something real
rather than general quality.

Scale does not predict it either: 428B scored 26%, 235B scored 53%, 120B scored
18%, 27B scored 21%.

### The recommendation

**Qwen3-235B-A22B Instruct**, which already sits on GX10 at 97GB in Q3_K_XL. No
download, no llama.cpp fork, no eviction of anything that is not already displaced
by a 97GB model. Do not enable thinking.

53% is the best available and is not good. The remaining rows still need the
two-stage technique or human escalation; this chooses the model, it does not solve
the problem.

Cost: $10.54 across five hosted screens.

## The two-stage question — moves the target, trades the wrong way

Qwen3-235B-A22B Instruct, 54 rows (all 34 alternate-route positives plus 20
failure controls), scored against the single-stage run on the **same rows**:

| run | alt-route | failures | overall |
|---|---|---|---|
| single-stage | 18/34 = 53% | 13/20 = 65% | 57% |
| two-stage | **24/34 = 71%** | **9/20 = 45%** | 61% |

Stage one restates what the comment REQUIRES with the document absent, so the
model cannot anchor on the method the comment names; stage two judges against that
outcome, with the method labelled as one acceptable route rather than the only one.

It is the first thing that has moved alternate-route substantially: +18 points.
It also cost 20 points of failure recall, and that is the expensive direction —
a missed failure is worse than an extra flag in review work.

**Read it as a threshold shift, not better discrimination.** It gained six
alternate-route rows and lost four failure rows: net two rows on fifty-four, which
is inside the noise. Making the model more willing to say `addressed` produces
exactly this signature, and the paired metric is what makes it visible; the
alternate-route number alone would have read as a clear win.

**The subset is deliberately enriched** — 63% positives against a natural 35% —
so the overall column is not comparable to the 98-row figures above, and a model
biased toward `addressed` flatters itself here. Validate on SEC (100 rows,
representative) before adopting.

Not adopted. Worth refining rather than discarding: it is the only intervention
so far that touched the target class at all.

## Retrieval cannot see alternate-route compliance

Same model (Qwen3-235B-A22B Instruct), same 98 rows, same labels. Only the way the
evidence reaches it differs: the whole document in the prompt, or passages selected
by bge-m3 embedding similarity.

| run | alt-route | failures | overall |
|---|---|---|---|
| whole-document | 18/34 = 53% | 37/54 = 69% | 65% |
| retrieval | **2/34 = 6%** | **47/52 = 90%** | 57% |

**Alternate-route collapses from 53% to 6%, and the mechanism is not subtle.**
Retrieval selects passages resembling the recommendation, and a recommendation's
distinctive vocabulary is the vocabulary of the METHOD it names. When the addressee
satisfied it by another route, the passage describing that route shares little
language with the recommendation, so it is never retrieved — and the judge, shown
only passages about a method nobody used, correctly reports that the method is
absent. The verdict is right about what it saw and wrong about the document.

The definition of the class is that the same outcome is reached in different words.
That is precisely what similarity search is worst at.

Failure recall moves the other way, 69% to 90%. Retrieval is a BETTER failure
detector and a much worse compliance adjudicator, which is a coherent position
rather than a defect: it is being shown less, so it asserts absence more, and most
absences are real.

### What follows

Use retrieval to find failures, not to clear compliance. An `unmet` from a
retrieval pass means "no supporting passage was retrieved", which is a weaker claim
than "the document does not do this" and must not be written into a register as
though it were the latter.

## Coder-Next, screened last and second overall

Screened after it had already produced three client deliverables, which is the
wrong order and the reason it is worth recording.

| run | alt-route | failures | overall |
|---|---|---|---|
| Qwen3-235B-A22B Instruct | 18/34 = 53% | 37/54 = 69% | 65.3% |
| **Qwen3-Coder-Next-80B** | **17/34 = 50%** | 35/54 = 65% | **61.2%** |
| MiniMax M3 (428B) | 9/34 = 26% | 43/54 = 80% | 58.2% |
| Nemotron 3 Super | 6/34 = 18% | 42/54 = 78% | 57.1% |
| Qwen3.8-27B | 7/34 = 21% | 39/54 = 72% | 53.1% |

One row separates it from the leader on alternate-route and four rows of
ninety-eight overall — inside what this sample can resolve. So the practical
ranking inverts. Qwen3-235B wins on paper and cannot be deployed: it exceeds its
64k context on a 127k-token deliverable, runs at 2-3 tok/s against Coder-Next's
46.6, and evicts the orchestrator's worker to load. Coder-Next is pinned, holds
262k, and is already serving.

**Prediction recorded and wrong.** Before running this I wrote that Coder-Next
was "an 80B/3B-active coding model; I would guess worse, not better" — reasoning
from Laguna, where coding strength did not transfer. It transferred here. The
guess was cheap and the measurement was ten minutes; there was no reason to prefer
the guess.

## The fourth cell: what the ranking was actually measuring

Crossing what the requirement SAYS with what the response DID gives four cells. The
two agreeing cells are uninformative — any policy that tracks wording scores well.
The off-diagonal is where a vocabulary matcher and an outcome judge must disagree.

                        addressed              not_addressed
    high overlap    A  both agree (24)     C  words present, nothing done (25)
    low  overlap    B  ALTERNATE ROUTE(20) D  both agree (29)

| run | A | B | C | D | agree | diverge | gap | policy |
|---|---|---|---|---|---|---|---|---|
| coder-next | 71% | 40% | 56% | 72% | 72% | 48% | +24 | vocabulary matcher |
| q235-inst | 79% | 40% | 52% | 83% | 81% | 46% | **+35** | vocabulary matcher |
| minimax | 46% | 15% | 76% | 83% | 64% | 46% | +19 | blanket refuse |
| nemotron | 38% | 25% | 68% | 86% | 62% | 46% | +15 | blanket refuse |
| qwen38 | 38% | 20% | 68% | 76% | 57% | 44% | +13 | blanket refuse |
| q235-think | 42% | 30% | 52% | 55% | 48% | 41% | +7 | blanket refuse |

**No run is an outcome judge.** Every one lands on a shortcut policy, and every one
scores 41–48% on the diverging cells — chance. The spread that produced the earlier
ranking lives almost entirely in the agreeing cells, where vocabulary and truth
coincide, so the ranking was substantially a measure of how often the fixture lets
wording stand in for judgement.

### The errors are directional, which rules out "these cells are just harder"

An imperfect but genuine judge would fail B and C without a preferred direction. A
vocabulary matcher must fail them in a specific direction: on B say not_addressed
because the words are missing, on C say addressed because the words are there.

    run           B errors -> not_addressed    C errors -> addressed
    coder-next        12/12   100%                 11/11   100%
    q235-inst         12/12   100%                 12/12   100%
    minimax           16/17    94%                  6/6    100%
    qwen38            15/16    94%                  3/8     38%
    nemotron          13/15    87%                  4/8     50%
    q235-think         7/14    50%                  6/12    50%

**Every error the two leading models make on the off-diagonal runs the predicted
way — 23/23 and 24/24.** That is a policy, not noise. q235-think is undirected at
50/50 on both, which is what incompetence rather than shortcut looks like.

### What this costs the earlier conclusions

Coder-Next's 50% alternate-route was read as "second-best reader, deliverables are
sound". It is better read as: it matches vocabulary well, which is worth something
because vocabulary usually tracks the outcome, and it is at chance precisely where
that breaks down. The model recommendation does not change — it is still the best
available and still fits — but the reason to trust it has narrowed, and the case for
human review of the flagged rows is now much stronger than a 50% figure suggested.

### A design constraint, from the instrument this borrows

Two reviewers of the Referent Gate instrument flagged that pooling its act cells
against its abstain cells destroys it: a shortcut policy scores at chance once
pooled and becomes invisible. The same holds here. **Never collapse A/D against
B/C into one index.** The vocabulary matcher identified above is precisely the
policy that disappears under pooling — and the earlier ranking in this file, which
sorted on a pooled total, is what hid it for a day.

A second warning worth carrying if this ever gets pre-registered: in that
instrument, two cells were named as controls and one of them was not, because the
manipulation could move it. Before calling a cell a control, check what actually
cannot move under the intervention being tested.

### The general form of the mistake, arrived at from two directions

Over two days this fixture and the Referent Gate instrument each produced a
ranking, and each ranking turned out to be a claim about the instrument rather
than about the systems ranked.

  Here      models were ordered on a pooled total. The order was substantially
            the order of how well each exploited rows where wording happens to
            track outcome. Splitting the cells put every run at chance on the
            half that requires judgement.

  There     a model scored 39/40 on a hand-written four-cell fixture and 260/400
            when the same structure was generated over 400 items — below a model
            the hand-written version had shown failing the discriminating cell
            0/10. The ordering did not degrade, it inverted.

Different domains, different instruments, same failure: **a ranking was published
before anything established that the instrument measured what it was taken to
measure.** Both were caught the same way — by someone reading the artifact rather
than the prose describing it.

The practical rule this leaves: before reporting an order, show a cell, a control
or a directional prediction that the order would fail if the instrument were
measuring something else. If that cannot be produced, the number is a description
of the fixture.

Three errors in the exchange were caught this way and none by argument: a
saturation null that was a decomposition result, a scale claim that quoted
alternate-route recall as though it were the cell result, and the pooled ranking
above. Two of the three were mine.
