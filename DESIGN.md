# dossier — design notes

Tooling for **adversarial review of a deliverable against a requirements source**,
running entirely on self-hosted models.

Given a requirements document (contract, RFO, SOW, spec, standard) and a
deliverable (an architecture, a design, a report), produce **findings that
survive rebuttal by whoever wrote the deliverable** — each carrying verbatim
evidence with a stable locator.

These notes exist so the reasoning survives the code. Examples are drawn from
the synthetic fixture in `fixtures/floodtwin`, never from client material.

---

## 1. The thesis: the problem is provenance, not reasoning

A finding is not good because it reads well. It is good because when the vendor
pushes back in a meeting, the evidence holds.

Three failure modes motivated everything here. All three are cheap to produce,
expensive to survive, and **none is fixed by a larger model**:

| Failure | Why it happens | Mechanised defence |
|---|---|---|
| **Fabricated absence** | "X is not defined anywhere", asserted from a keyword count. The definition is on the next line, under a synonym | `absence()` cannot return a verdict from a count. It requires a synonym set and returns every hit with surrounding context, forcing the read |
| **Unverifiable quote** | A phrase quoted from a copy of the source no longer held. It cannot be re-verified, and the finding collapses | Quotes enter findings only through tooling that reads the frozen corpus and returns a locator. A quote with no locator is not a quote |
| **Cross-reference rot** | Renumbering findings leaves references pointing at valid IDs that have come to mean something else. Nothing flags it — the IDs still resolve, just to the wrong thing | Reference checks resolve every ID **and print each target's title**. A dangling count of zero is not a pass |

Everything below follows from this: **build deterministic tooling the model
cannot route around, then let the model do bounded work inside it.**

---

## 2. The measured failure that shaped the architecture

An agentic coding assistant was pointed at a ~10,000-line architecture document
and a requirements PDF, with the prompt *"review the document to see if it
satisfies the requirements."* Result, from the session export:

```
153 minutes    496,949 input tokens    2,782 output tokens    0 findings
```

Seventeen turns, **none of which touched the document's content.** The time went
on locating files, installing PDF libraries, and re-extracting the same document
three times — each dump landing in a context that every subsequent turn resent.
The final message was empty.

Two distinct causes, and the second matters more:

- **Mechanical.** No frozen corpus, so the model had to do ingestion itself, in
  a conversation where every failed attempt stayed in context forever.
- **Framing.** *"Review this against those"* is not one question. It is forty.
  A document that does not fit in the context window cannot be reviewed by
  holding it in the context window.

The same work, decomposed into independent per-obligation questions, ran in
**2 minutes 19 seconds** on the fixture and surfaced 16 of 16 planted defects.
Same class of model. The difference is that one version asks forty answerable
questions and the other asks one unanswerable one.

---

## 3. Design decisions

### 3.1 Freeze before anything else

Findings cite locators. A locator into text that can still move is not a
locator. So documents are extracted **once**, hashed, and thereafter read-only.
Re-extraction is an explicit act that invalidates every existing citation.

`freeze.py --check` re-hashes sources and frozen text and exits non-zero on
drift. It belongs in a pre-commit hook.

### 3.2 Pin the anchor by hash

Not paranoia. On a real engagement, two files with **identical names**, 210 KB
apart, differed by a single front-matter paragraph — a scope statement that the
entire acceptance standard rested on. Without a pin, whichever file you happened
to open silently becomes the truth.

The pin protects at *first* freeze, when there is no prior record to compare
against. After that the manifest does the work.

### 3.3 Decompose; never converse

Every model call is independent: no history, no accumulation. Cost is linear in
questions rather than quadratic in turns. Calls are cached by content hash, so
an interrupted run resumes and a prompt edit invalidates exactly what it should.

This is the direct lesson of §2. A conversation is the wrong data structure for
a review.

### 3.4 The model may not invent the evidence for its own verdict

A `met` or `partial` verdict must carry a quote that appears **verbatim** in the
passages supplied. The check is mechanical: normalise whitespace, substring
match, reject and retry on failure, downgrade to `unverifiable` if it fails
again.

On the clean fixture this fired 4 times in 34 obligations. On a real deliverable
it fired 22 times in 109 and hard-failed 5. That gap is the point — the guard is
load-bearing exactly where prose is messiest.

### 3.5 Establish a deterministic floor before adding a model

Obligation extraction runs a deterministic RFC 2119 pass **first**, so the
number of candidate obligations is known before any model runs. `--no-model`
stops there.

That floor is what makes model contribution measurable. Without it you cannot
tell whether inference is adding anything or merely producing fluent output.

### 3.6 Scope obligations to deliverables

One requirements document usually governs several deliverables. Tracing all of
its obligations against one of them produces a matrix that is mostly false
`unmet` — on a real run, 60 unmet collapsed to a plausible handful once
obligations were tagged with the deliverable that discharges them.

An obligation on the roadmap is not a defect in the architecture. Every
obligation carries a `scope`; coverage runs take `--scope`.

### 3.7 Retrieve by embedding, not term overlap

Term-overlap retrieval fails precisely in the case that matters most: when the
deliverable answers an obligation in **different words**. And a deliverable that
never uses the requirement's vocabulary is itself a finding — so the retriever
must still surface the passage for a human to judge, rather than returning
nothing and letting the verdict default to `unmet` for the wrong reason.

### 3.8 Batch and interactive want different endpoints

A shared inference endpoint serving several clients will thrash: a batch job
requesting model A queues behind another process requesting model B, and each
swap costs ~100 s. Measured: **1 call in 12 minutes** on a contended endpoint,
**0.5 s per call** on a dedicated one with the model pinned resident.

Interactive work on the shared host; batch on a dedicated endpoint.

### 3.9 Lenses that answer different questions must not vote together

Verification attacks each candidate from three angles: search harder for
evidence, argue the vendor's rebuttal, and screen for materiality. The first
version tallied all three into one majority vote, and it wrongly killed real
findings — the materiality lens said "substantive gap" and was outvoted.

`search` and `rebuttal` answer *is this true*. `materiality` answers *is this
worth raising*. An immaterial finding is still true; a material one can still be
wrong. Only the first two vote; materiality is reported as a separate flag.

### 3.10 Verification must respect quantifiers

The same first version refuted *"name exactly one component accountable for
**every** capability"* by quoting a table that covers most of them. Addressing
the same topic is not discharging the obligation, and a passage handling some
instances does not satisfy a universal. Both are now explicit in the prompt, and
the case is in the fixture.

### 3.11 A failed check is not a passed check

A verification lens that errors must not read as "did not refute". The first
version defaulted a failed lens to `refuted=False`, which silently promoted
candidates to `confirmed` on infrastructure noise — 20 confirmed became 15 once
failures were made to force `contested` instead. Wherever a guard can fail, the
failure must land on the cautious side of the decision it guards.

### 3.12 Ground truth is a hypothesis, not an oracle

Measuring precision required writing an expected verdict for every obligation in
the fixture, not just the defective ones. Two of those expectations were **wrong**
— the tool disagreed, and on adjudication against the obligation text the tool
was right. One obligation had two limbs where only one was discharged; another
asked *how* and was answered with *when*.

When tool and ground truth disagree, adjudicate against the source text. Do not
assume the tool is wrong, and do not edit ground truth merely to improve a score
— every correction carries its reasoning so it can be challenged later.

### 3.13 A cross-reference is not evidence

A refutation quoting *"Component maturity is assessed in Section 9"* was accepted
as proof that maturity is assessed. It is not — it is a claim **about** the
document, and in the fixture that pointer resolves to a section about something
else entirely. Accepting pointers as evidence lets a deliverable's own untrue
self-claim dismiss the finding that the claim is untrue. Rejected structurally in
the validator, not by asking the model nicely.

### 3.14 A universal cannot be disproved from a sample

*"Every capability shall name exactly one accountable component"* can be refuted
by one counterexample and never confirmed by twelve retrieved passages. A refuter
correctly restated that gap and then refuted it anyway by quoting a table
covering six components.

So refutation of a universally-quantified obligation is capped at `contested`: a
human decides. The asymmetry is real and worth respecting — presence of a
counterexample is strong evidence, absence of one in a sample is almost none.

### 3.15 A permission is not a duty

*"The Contractor MAY propose alternative structures, provided the mapping is
stated"* was judged `unmet` because the passages showed no sign alternatives had
been proposed. That is the expected state for a permission — nothing was owed.
Telling the model this in the prompt did not stop it, so permissive obligations
are now filtered out of the coverage run structurally.

The real question for a `MAY` is different in kind — *did they exercise it, and
if so was the condition met?* — and needs asking separately rather than as
coverage.

### 3.16 Retrieval width has an optimum, not a direction

Measured on the fixture: 6 passages → 77.8% precision, 12 → 82.4%, 24 → 81.2%
with recall falling too. More context is not monotonically better; past a point
the model finds something tangentially relevant in the extra material and talks
itself into a verdict. 12 is the default because it was measured, not chosen.

### 3.17 Where a deterministic check exists, it wins

*"Cross-references within the deliverable SHALL resolve"* regressed to `met`
under wider retrieval — the model saw many references that do resolve and missed
the two that do not. `sweep.py xref` answers that obligation exactly, by
construction, in milliseconds.

An obligation that a deterministic check can settle should be routed to the
check, not re-litigated by a model. Coverage tracing is for obligations that
genuinely require judgement.

### 3.18 Confidence in absence decays with document length

Measured on CUAD, where lawyers labelled which clauses are genuinely missing.
The same 41 clause questions, the same model, two contracts — and in both the
retrieved passages covered the WHOLE document, so retrieval is not the variable:

| | 8k chars | 52k chars |
|---|---|---|
| precision of "unmet" | 97.0% | **83.3%** |
| recall on absent | 97.0% | **100.0%** |
| span fidelity | 100% | 100% |

Both numbers move the wrong way together. As the document grows the model becomes
*more* willing to assert absence and *less* often right to. It reported "the
passages do not contain any mention of 'Agreement Date'" about a contract that
has one — from material it was shown and did not finish reading.

This is the measured form of the thesis in section 1: **an absence claim is the
least reliable thing a reader can produce, and it gets worse the more there is to
read.** It is why `absence()` demands a synonym sweep rather than accepting a
judgement, and it is a warning about long real deliverables — a live architecture
document is 10,000 lines, five times the large contract here.

Span fidelity held at 100% in both. When the model does find evidence it quotes
the right text; the failure is entirely in claiming there is none.

### 3.19 Three things that did not work

Recorded because a negative result measured is worth more than a feature shipped
on a hunch, and because each was a plausible idea that the numbers refused.

**Section-aware retrieval made things worse.** Flat retrieval returns two or
three fragments of a 700-line package, so ranking whole sections first and
passing each winner's opening context looked obviously right. Measured: fixture
recall 95% → 100% but precision 86.4% → 76.9%, and on a real architecture the
register match fell 10/37 → 8/37 with corroboration 28% → 20%. Kept as
`--retrieval section` for the recall case; not the default.

**Retrieval was not the bottleneck at all.** The prior hypothesis — that on a
270-chunk document at 4.4% coverage the model never sees the right package — is
false. Tested directly: 6 of 8 obligations reached their owning section. The
failure is judgement on long documents, which is what §3.18 measured.

**A deterministic absence gate demotes true positives.** Sweeping the
obligation's vocabulary across the whole document before letting "unmet" stand
looked like the §1 thesis applied to the tool's own output. It fires on 32 of 36
verdicts, including the true ones: the Sensor Fabric finding is "contested"
because *inject* appears 58 times — which IS the finding, since injects are
scheduled and the interface is not specified.

**Term presence is not obligation discharge.** In architecture review the gaps
are rarely lexical: the topic is discussed and the required artefact is missing.
The `absence()` discipline — sweep synonyms, read the passages — still holds as a
discipline. It does not automate into a verdict. What survives is the asymmetry:
vocabulary entirely absent is high-confidence (2 of 36); vocabulary present means
nothing.

### 3.20 Convergence is the signal that survived

Three attempts to raise precision failed (§3.19). The one that worked attacks the
problem from the other end: not "which verdicts are wrong" but "which order
should a reviewer read them in".

When several obligations independently report the same gap, that gap is real far
more often than a lone verdict is. Measured on a live corpus: 50 flagged
obligations collapse to 13 candidate findings, and reading the **top three**
reaches six of the ten register findings that map at all; the top five reach
seven. A finding buried at rank 32 under tight clustering surfaces at rank 3.

This is why the many-to-one matcher in §A mattered. One-best-row matching
reported convergence as unmatched noise, which inverted the most useful signal
the pipeline produces.

Note what it does NOT claim. Clustering changes nothing about which obligations
were flagged or whether the verdicts are right. Precision is unchanged. What
changes is that the reviewer meets the real findings first, which is the cost
that actually bites.

### 3.21 Read the document; do not sample it

Coverage tracing is obligation-first: retrieve some passages per obligation and
judge. Measured on a real architecture, **53% of the document is never read by
anything** — every verdict, including every "unmet", is made against a sample.

Inverting it: one bounded agent per section, asked what the section PROVIDES
rather than whether an obligation is met. Description, not judgement.

- each agent works on ~3k characters — the regime where absence precision was
  97%, not the 83% at 52k (§3.18)
- no agent asserts absence. Absence is a property of the whole document, and
  becomes a deterministic query over a finished inventory
- 100% coverage instead of 47%

Measured on the fixture: 72 sections yield 84 capabilities, 127 identifiers, 62
evidence claims, 20 authority assertions, 8 deferrals, 24 produces / 31 consumes.
**Every one of the six planted D3/D5/D6/D8 defects has its evidence in the
inventory.** The extraction stage is not the hard part.

### 3.22 The orchestrator should be code — until the candidate set is small

Ninety section summaries is ~28k tokens, exactly the long-context regime where
judgement decays. So code queries the inventory and a model is called back only
for specific pairs.

Deterministic queries alone found **3 of 6**. Token overlap could not see that
"execution scheduling" and "determine evaluation order for pending alerts" are
one slot; embeddings could not either. That identity is domain reasoning.

What works is the hybrid. The inventory has already reduced the pairwise problem
from thousands of claims to **20 authority assertions — 163 cross-section pairs**
— and at that size a model can adjudicate every one. Result: **5 of 6**, and the
two that flipped are D5 dual binding and D6 ownership gap, the classes coverage
tracing structurally cannot reach.

Precision is poor in absolute terms — 9 dual-binding candidates, one real. It is
excellent in practical terms: a 561-line document reduced to nine things to read,
one of which is a defect that took an expert to find.

### 3.23 Concurrency needs vLLM, and it is worth switching for

The section workload is embarrassingly parallel. Measured on one host, 72
sections, same fixture:

| serving path | model | concurrency | sections/s |
|---|---|---|---|
| llama-swap | Coder-Next (80B MoE, 3B active) | 1, 4, 8 | **0.26, 0.26, 0.26** |
| vLLM | Qwen2.5-32B AWQ (dense) | 1 | 0.07 |
| vLLM | " | 8 | 0.46 |
| vLLM | " | 16 | 0.65 |
| vLLM | " | 48 | **1.02** |

Two facts that only appear together.

**llama-swap cannot serve concurrency at all** — `llama-server --parallel 1`, so
requests queue and the wall clock is identical at 1, 4 and 8. Any aggregate
throughput figure recorded for such a host is a vLLM number and does not
describe the llama-swap path.

**A slower model wins on aggregate.** The dense 32B is 3.7x slower per stream
than the sparse MoE, and still finishes the batch **3.9x faster** at 48-way,
because it scales 14.6x from serial while the MoE scales not at all.

The rule that follows: interactive work on the swapping endpoint, batch work on
the concurrent one — and judge a batch server by aggregate throughput, never by
single-stream speed. Starting vLLM stops llama-swap, so this is a drawer tool;
the full inventory pipeline goes from roughly eight minutes to two.

### 3.24 Normalise the entity vocabulary before comparing entities

The inventory records the owner of an authority assertion as free text. On a real
architecture that gives **240 distinct owner strings for 788 assertions** — and
788 assertions is 204,748 candidate pairs, which is not searchable. Sampling 900
of them found 54 "conflicts" that were a 0.4% sample presented as a result.

But the architecture names **nine components**. Authority conflicts that matter
are between components, not between free-text strings. Normalising owners onto
that set — with aliases, and an explicit exclude list for strings that name no
component ("this section", "release authority", "the system") — resolves 488 of
788 assertions and collapses the space to **10 complete D5 candidates**. No
sampling, no model calls, every one auditable.

Longest alias wins, so "platform adapter" does not resolve to "platform" and
"runtime orchestrator" does not resolve to "context".

The general rule: before comparing entities, agree what the entities ARE. A
document's own component table is that vocabulary, and it is usually sitting in
section 3.

### 3.25 Reduce only when exhaustive is out of reach

Clustering the pair space by similarity is the obvious way to make it tractable,
and it silently broke the fixture: 5/6 fell to 3/6 because "execution scheduling"
and "determine evaluation order for pending alerts" land in different clusters,
and their pair IS the defect.

So the reduction is now conditional. Below the cap every pair is adjudicated;
above it, clustering applies and says so. A small document gets exhaustive
treatment and a large one gets an honest note about what was skipped — rather
than a number that looks like a search and is a sample.

### 3.26 An ownership claim needs polarity

Architecture sections routinely carry a heading "Ownership and exclusions" with
an **Owns** list and a **Does not own** list beneath it. The extractor flattened
both into `authority`, so a component explicitly DISCLAIMING a capability was
recorded as claiming it.

That inverted the strongest-looking finding on a real document: Population was
shown contesting `node_id` creation with Network, when the source says under
"Does not own" — *"Authority to create agent_id, node_id, or account_id"* — and
the document is entirely consistent.

With a required `polarity` field, **463 of 1051 authority assertions on that
document turned out to be exclusions** — 44%. D5 candidates halved, 10 to 5.

Exclusions are not waste. A capability that every component disclaims and none
claims is a D6 ownership gap, stated outright.

### 3.27 Single-pass extraction is lossy and varies between runs

Two extractions of the same fixture, same model, temperature 0, differing only in
prompt wording: 20 authority entries versus 17. The second captured a
frozen-schedule exclusion the first missed, and lost the evaluation-order claim
the first caught — which flipped one planted defect from found to missed and
another from missed to found.

The inventory is a sample of what a section says, not a complete reading of it.
The obvious mitigation is the convergence principle applied to extraction: run it
more than once and take the union. Under vLLM that costs about twenty minutes.

### 3.28 Capability identity needs the verb, not just the object

After polarity, the surviving false positives share one cause: the grouping
treats different actions on the same object as the same capability. "Runtime
returns AgentTurnResult" and "Orchestrator evaluates AgentTurnResult" are
complementary; "Population commits its transitions" and "Runtime commits
Runtime-owned state" are the design working as intended.

A capability is a verb applied to an object by an owner. Recording only the
object manufactures conflicts between components doing different things to the
same artefact.

### 3.29 Polarity generalises; the control said so

Polarity was diagnosed on one architecture, which is exactly the
single-document risk §4a warns about. Run as a control on a completely different
document family — Kubernetes Enhancement Proposals, whose template carries a
**Non-Goals** section:

| corpus | authority | exclusions |
|---|---|---|
| kep-2400 | 16 | **9 (56%)** |
| kep-1287 | 17 | **9 (53%)** |
| an architecture | 1051 | 463 (44%) |

Different mechanism, same phenomenon at higher density. Technical proposals state
what they will not do, and an extractor without polarity turns every one of those
statements into a false capability claim. This is a property of the genre.

The same control could NOT test verb-blindness: a KEP yields about seven
non-exclusion authority assertions across a hundred sections, because KEPs barely
assign component ownership. Absence of a control is not confirmation, so that fix
was justified by principle and made measurable in the fixture instead (DC-009).

### 3.30 Authority is about rights, not data flow

The surviving false positives after polarity were all complementary roles read as
disputes: "Runtime returns AgentTurnResult" against "Orchestrator evaluates
AgentTurnResult".

The fix is not a better similarity measure. Those were never authority claims —
they are data flow, and belong in produces/consumes. An authority entry records
who is accountable for, owns, decides or is authoritative over something, and now
carries the `action` verb of the right asserted. Two components doing different
things to one artefact is a handoff, which is how architectures work.

Verified in both directions on the fixture: the planted dual binding is still
found, and a planted complementary handoff (DC-009, "Reporting produces the
RunSummary" against "operations evaluates the RunSummary") is correctly ignored.
No new threshold — the action grouping reuses the object grouping's.

### 3.31 A deferral needs its source, and more passes do help after all

A deferral records what is passed on and to whom. It never records **who is
passing it** — that is implicit in the section it appears in. Without the source,
"defers adjudication to Hydrology" and "defers preference policy to Alerting" are
two unrelated facts. With it they are two edges of a chain, and a chain that
closes is an ownership gap that no single section reveals:

    Sensor Fabric -> Hydrology Model -> Alerting Service -> Sensor Fabric

Each section is locally reasonable. The capability is owned nowhere. Resolving
the source from the section's top-level heading and looking for cycles in the
resulting graph finds it, deterministically, and names the loop in the finding.

**This also overturned a negative result.** Union-of-runs was recorded here as
useless because recall stayed at 5/6 across 1, 2 and 3 passes. It was not
useless: at one pass only two legs of the cycle were captured, at three all
three were — and the broken detector was masking the difference. With cycle
detection the fixture goes to **6/6 at three passes and 4/6 at one**.

A negative result measured through a broken instrument is not a negative result.
Both were wrong together, and only fixing the detector made the extraction
finding visible.

### 3.31a What the fixture cost to solve

Six planted defects, and every one needed something different: polarity (§3.26),
verb sense (§3.30), component vocabulary (§3.24), cycle detection with source
attribution (§3.31), conditional reduction (§3.25), and three extraction passes.
None was a threshold. All six were about representing the document correctly.

### 3.32 Closed vocabularies where tooling branches, free text everywhere else

`role` is one of `anchor | requirements | draft | reference`, enforced, because
downstream tools branch on it — a coverage matrix must know which document
states the obligations, and a citation check must refuse to quote a draft. A
typo in a free-text field would not fail loudly; it would fail silently, later.

Plain-English description goes in `note`, which is carried through untouched.

---

## 4. The fixture, and why it has decoys

`fixtures/floodtwin` is a fictional procurement — a requirements document and an
architecture deliverable with **24 planted defects and 6 decoys**, plus a
`ground-truth.yaml` answer key. No client material, so it can be committed,
shared, or handed to a third party when debugging.

**The decoys matter as much as the defects.** A fixture that scores only recall
trains tools to flag everything. Three reproduce failures seen in real review
work:

- an enumeration sitting one line below a claim of absence
- a threshold with no value but a **named owner**, so the obligation is *met* —
  contrasted with one that has neither, which is a silent omission
- identifiers appearing exactly once but **defined in place** — singleton is a
  prior for a dangling reference, never a verdict

### It has caught four real bugs

1. **Markdown headings.** The reference-closure check recognised only bare
   `Appendix C` headings as produced by `.docx` extraction. Against markdown it
   found zero headings and reported every appendix and section reference in the
   document as unresolved — nine false positives.
2. **Line-wrapped obligations.** Candidate detection worked line-by-line, so
   wrapped paragraphs reached the model truncated: *"Every interface SHALL carry
   a unique identifier and appear in a"*. The model correctly refused to guess
   the missing half, and the obligations vanished silently. Recall 14/21 → 21/21
   after making it paragraph-aware.
3. **Lens voting.** Verification refuted real defects because three lenses
   answering two different questions were tallied into one majority (§3.9), and
   because the search lens ignored universal quantifiers (§3.10). Caught on the
   first fixture run, before it ever touched a real candidate.
4. **Identifier grammar.** A pattern of `[A-Z]{2,4}-G\d+` silently missed an
   entire class numbered `CAL-G-01` rather than `CAL-G01`. This one was found on
   real data first and is now planted in the fixture permanently.

**The lesson generalises: a tool validated only on the document it was written
for is overfit to it.** Build against a surrogate; run against the target.

---

---

## 4a. Overfitting audit

Every tuning change in this project was measured on the fixture. That is a real
hazard and it was audited rather than assumed away.

### The measurement instrument is too small to tune on

The fixture has 21 requirements. Roughly six configuration and prompt changes
were evaluated against it. The headline gain — coverage precision 77.8% → 86.7%
— is **two requirements changing verdict**. At N=21 a single requirement moves
the number by 4.8 points, so that entire improvement sits inside the noise of
almost any change.

**Conclusion: the fixture is a regression suite, not a benchmark.** It is
excellent at "did I break something" — it caught four real bugs that way, each
before it reached client data. It cannot answer "is this better". Use
`score.py --min` as a commit gate; do not use it to select parameters.

**Partly addressed by growing it.** The fixture went from 21 requirements to 35,
42 chunks to 72, and 6 decoys to 8, with expected verdicts for 30 requirements
rather than 21. Coverage precision held at 86.4% on the 45%-larger sample — which
is the first evidence that the earlier 86.7% was not purely noise. It is still
too small to select parameters on; it is now large enough that a single
requirement moves the number by 3.4 points instead of 4.8.

### The retrieval regime does not transfer

| corpus | chunks | 12 passages covers |
|---|---|---|
| fixture deliverable (before growth) | 42 | **28.6%** of the document |
| fixture deliverable (after growth) | 72 | **16.7%** of the document |
| a real architecture | 270 | **4.4%** of the document |

`passages=12` was selected where retrieval returns nearly a third of the
document. The measured curve (6 → 77.8%, 12 → 82.4%, 24 → 81.2%) shows a
"too much context" degradation at 24 — where 24 passages is more than half the
fixture. On a 270-chunk document 24 passages is under 9%, so that failure mode
may not exist there at all.

The constant is retained but **labelled unvalidated at scale**. Replacing it with
another constant chosen from the same data would not be an improvement.

### Which changes actually generalise

**Low risk — true by definition, not by measurement.** A permission is not a duty
(§3.15). A universal cannot be disproved from a sample (§3.14). A failed check is
not a passed check (§3.11). A refutation must carry verbatim evidence. A
cross-reference is not evidence (§3.13) — though the *regex* that detects one is
single-case-derived and brittle.

**High risk — fitted to fixture data.** `passages=12`. The `POINTER` pattern.
Prompt wording for the partial/unmet boundary.

**Methodological.** Two of 21 ground-truth entries (~10%) were corrected after
the tool disagreed with them. Each was adjudicated against the obligation text
and each carries its reasoning (§3.12) — but a measuring instrument that moves
toward the thing it measures is exactly how overfitting enters, and the ratio is
worth watching.

### What the real corpus said

Re-running a live engagement with all fixture-derived tuning applied:

```
exact verdict agreement     41/58 = 71%
flagged-vs-clean agreement  54/58 = 93%
flagged -> clean 4     clean -> flagged 0
```

All four independently corroborated findings survived. The `unverifiable` count
fell 5 → 2, confirming the reply-truncation fix on real prose. So the tuning did
not damage the real corpus — but 71% exact agreement shows severity labels are
unstable to configuration even where the flagged set is not.


## 5. What is built

| | |
|---|---|
| `freeze.py` | Extract once, hash, treat as read-only. `--init` scaffolds `corpus.yaml`; `--check` reports drift |
| `sweep.py` | Deterministic checks: reference closure, revision regressions, vocabulary gaps. No model |
| `obligations.py` | Requirements → atomic obligations. Deterministic floor, then bounded model calls. `--mode chunk` for nested lists under a stem |
| `trace.py` | Coverage matrix with quote verification, scoping and embedding retrieval |
| `verify.py` | Adversarial verification: attacks each candidate with independent lenses before it reaches a register |
| `llm.py` | Independent, cached, logged, schema-checked calls. Stdlib only |
| `fixtures/floodtwin` | The regression suite |

Deliberately **not** built: a vector database, a workflow engine, an agent
framework. The tools are CLIs so they can run in hooks, in batch, and with no
model at all.

## 6. Open problems

- **Multi-file anchors.** A deliverable is sometimes a document *plus* a
  repository export. The corpus model allows one file per document and one
  anchor per project.
- **Verification now filters very little.** Fixed: 3 real findings lost → 0
  (§3.13, §3.14). But the precision it buys fell with it, 0.76 → 0.79, and it now
  kills 1 candidate in 25. Its value has shifted from filtering to triage and to
  capturing the vendor's best rebuttal against every candidate. That is a
  defensible trade — a false finding costs an argument, a lost one costs the
  point — but it should be called what it is.
- **Coverage precision is 86.7%**, up from 77.8% via §3.15 and §3.16. The two
  remaining false positives have clear diagnoses and no clean fix yet: one is a
  document-level property ("is a single document with numbered sections") that
  excerpts can never confirm, and one is an over-literal reading of "unique
  identifier" that rejects section-scoped IDs.
- **Deterministic obligations are not yet routed** to the deterministic checks
  (§3.17), so coverage still re-litigates — and sometimes loses — questions
  `sweep.py` answers exactly.
- **Unmet precision decays with length** (§3.18) and nothing compensates. The
  obvious move — re-ask every "unmet" against a narrowed window, or require a
  deterministic term sweep before an absence verdict stands — is unbuilt.
- **Obligations carry no applicability conditions.** `--scope` says who owes an
  obligation; nothing says WHEN it falls due. Half the false positives against a
  beta-stage KEP were GA-stage obligations that are simply not yet owed.
- **Framing errors remain out of reach.** Nothing here detects a deliverable
  that is internally consistent and answering the wrong question. That needs an
  external referent — prior art, a counterfactual design, a reference corpus —
  and it does not decompose into per-obligation questions.
- **Requirements quality bounds everything.** Obligations extracted from a
  well-numbered spec are reliable; from narrative prose they are suggestions.
  Report confidence rather than pretending otherwise.

## 7. The part that compounds

The code is the least valuable output. The **lexicons** accumulate domain
knowledge every time a sweep surprises you, and the **fixture** accumulates every
failure mode ever hit. Both improve across engagements; the Python is
replaceable.

They also contain no client material — which makes them the parts that can be
shared, and debugged in the open.
