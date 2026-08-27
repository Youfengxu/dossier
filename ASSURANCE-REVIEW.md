# Note on ASSURANCE.md — correlated agreement

From the `local-bench` session, 2026-08-26. Written for whoever picks up dossier next.

This is a narrow review. It does not re-audit the framework; it takes one measured
result from a separate experiment and applies it to three places in `ASSURANCE.md`
where agreement between model readings is doing evidential work.

**Standing of the claims below.** Every figure in §1 was re-derived from
`~/coding/local-bench/blindspot/` immediately before writing, not recalled — the
appendix names the script for each. Claims about dossier's own code were checked by
reading the code, not the document. Anything I did not check is marked *unverified*.

---

## 1. The result

An experiment ran 1,628 auditing records: 30 artefacts carrying 48 planted defects
drawn from 23 archetypes, audited by 9 models across 8 vendors, at 3 replicates
crossed with 2 prompts. Registered before data existed. The question was whether two
auditors drawn from the same model miss the same defects.

**Scope of every table below.** One model (`qwen/qwen3.7-flash`, 30% unparseable
output) breached a pre-registered 25% exclusion ceiling and is reported separately,
never scored as a negative result. All figures below are therefore computed on the
remaining **8 models across 7 vendors — 48 auditor instances**, not on the 9 run.

They do. Error consistency (Geirhos et al., NeurIPS 2020) between auditor pairs:

| pairing | n pairs | κ |
|---|---|---|
| same model | 120 | **+0.736** |
| different model | 1008 | **+0.500** |
| Δκ | | **+0.236**, permutation p < 0.0001 |

The lineage breakdown matters more here than the headline:

| pairing | n pairs | κ |
|---|---|---|
| same model | 120 | +0.736 |
| **different model, same vendor** | 36 | **+0.702** |
| different vendor | 972 | +0.493 |

Two models from one vendor are nearly as correlated as two runs of one model. The
gap between them (0.034) is small beside the gap to cross-vendor (0.209).

Varying the *prompt* while holding weights fixed moves κ by **+0.012**. The
correlation is in the weights, not the instructions. This is the actionable part:
rephrasing, re-framing or re-tasking the same model buys almost nothing.

Converted to coverage — how many genuinely independent auditors a group is worth:

| condition | k | observed catch | if independent | effective auditors |
|---|---|---|---|---|
| same model | 2 | 77.8% | 91.8% | **1.16 of 2** |
| same model | 6 | 82.9% | 99.9% | **1.39 of 6** |
| cross vendor | 2 | 81.7% | 91.8% | **1.38 of 2** |
| cross vendor | 7 | 90.3% | 100.0% | **1.90 of 7** |

Two confounds, both measured, both reported: 54% of same-model repeat draws at
temperature 0.7 were byte-identical, and same-model pairs have better-matched
marginals than cross-model ones. Holding prompt different leaves Δκ = +0.202 (86% of
the effect); matching on marginals across 598 pairs leaves Δκ = +0.151. **Treat
+0.15 to +0.20 as the defensible range and +0.236 as an upper bound.**

The exclusion above is a failure to measure, not a measured negative: 54 of qwen's 180
records were unparseable, and an auditor whose output could not be read has not
demonstrated that it missed anything.

---

## 2. Where this lands in ASSURANCE.md

### 2.1 §5 — the two-route convergence

§5 calls this the strongest thing in the document:

> Two independent routes reached the same conclusion. The gaps in §3 came from a
> bottom-up inventory. The standards in §2.7 came top-down from three published
> frameworks. They agree.

Both routes were produced by the same agent, on the same repository, in the same
session. On the numbers above, that is prompt variation — worth **+0.012** — not
independence. Two same-model passes are worth **1.16 auditors**, not 2.

The conclusion is not thereby wrong; G1 looks real on its own merits. What is
overstated is the *evidence* for it. A sufficient repair is one sentence:

> A bottom-up audit and a top-down standards mapping both landed on the same
> missing baseline. Both were produced by the same model in one session, so this is
> corroboration rather than independent replication.

This exact error has already been retracted once in the local-bench work, in a paper
claiming "two independently written harnesses converging on the same taxonomy" as
replication when both harnesses had one author. It is easy to make and hard to see
from inside.

### 2.2 §2.4 — `readers AGREE 14/18 = 78%`

Three separate problems, in increasing order of how much they cost.

**The document does not record which models produced it.** This is G2 biting a second
time, in a way §3 does not note. G2 is scoped as "cannot be reproduced or attributed."
It is also *cannot be interpreted*: without knowing whether the two readers shared
weights, a vendor, or nothing, there is no way to tell whether 78% should be
discounted against the +0.736 row or the +0.493 row. Add a `models:` field to the run
manifest planned in §6 item 3 and this closes with the rest of G2.

**It is raw agreement with no chance floor.** I grepped: `kappa`, `Cohen` and `Wilson`
appear nowhere in the agreement path (only in `fixtures/cuad/README.md` and
`fixtures/cm1/score-retrieval.py`). If the verdict vocabulary is skewed — most rows
landing on one value — a large share of 78% is the skew. Because `agree.py` has an
`ADJACENT` class, the verdicts are ordinal, so the right statistic is **linearly
weighted κ**, which credits ADJACENT as partial agreement rather than scoring it as a
miss. Plain Cohen's κ would understate agreement here.

**No interval, on n = 18.** The normal-approximation interval is far tighter than 18
rows support, and at counts near the ceiling it is actively misleading. Wilson is
four lines and is already used elsewhere in this repo's neighbourhood.

### 2.3 `judge-panel.py`

The blind-labelling design is right and I am not suggesting changing it. But "a
three-model panel splits on 10 of 25 rows" is evidence about independence only if the
three models do not share lineage. On the table in §1, three models from one vendor
sit at κ ≈ +0.70 — closer to three runs of one model than to a genuinely mixed panel.

*Unverified:* I did not determine which models the 25-row panel actually used;
`--model` is a required CLI argument and the choice is not recorded in the document.
If they span three vendors, the concern does not apply and saying so in one line
closes it permanently.

---

## 3. What `agree.py` already gets right

Recorded because a review that only lists faults misrepresents the code.

`agree.py` does **not** collapse a missing judgement into agreement. Rows where
neither judge answered are dropped; rows where exactly one answered are classified
`ONE JUDGE ONLY`, counted separately, and pushed into the "rows a human should read
first" list. The header prints `"N row(s) with at least one verdict"` — the denominator
states its own composition. That is `DESIGN.md` §0.1 applied correctly, and step 3 of
the `failed-check` skill satisfied before that skill existed.

**The loss happens at the reporting layer, not in the tool.** Compressing a
five-category output to `14/18 = 78%` discards which of the four non-AGREE rows were
DISAGREE, ADJACENT, UNRANKABLE or ONE JUDGE ONLY. Those mean different things: four
disagreements is a signal about judge quality; four one-judge-only rows is a signal
about coverage. Carry the breakdown into the prose, or quote the ratio over rows both
judges answered and say so.

---

## 4. G1's fix needs one thing it does not have

§6 item 2 is "two people independently adjudicating ~30 NTSB rows, reported with
agreement." Two annotators agreeing establishes nothing if neither annotator was
checked. That is §2.1 of this note, one level up: human agreement is also correlated
agreement, and humans reading the same rubric correlate too.

The fix is cheap and the tooling transplants. `~/coding/local-bench/annotate/` holds a
151-record stratified sample with **12 known-wrong verdicts planted in `planted.json`
and committed before any labelling began** (seed 20260825, 7.9% planting rate). The
annotator's catch rate on the planted subset is what licenses trust in their labels on
the other 139.

Two consequences worth having:

- **No annotator qualifications are needed.** Ground truth on the planted rows is
  fixed by construction, so competence is measured rather than assumed. That matters
  when the constraint is "roughly a day" and the pool is whoever is available.
- **A failed baseline is detectable.** Without planting, two annotators who both
  misunderstood the rubric produce high agreement and a confident wrong ceiling.

Additions to §6 item 2, then: plant ~10% known-wrong rows; report human–human
agreement chance-corrected and with a Wilson interval. Thirty rows gives a wide
interval, and stating that is better than a bare percentage that reads as precise.

---

## 5. The pipeline emits categories, and categories fail differently

Dossier's stages emit named verdicts — obligations, findings, register entries — not
scores. The rule below was paid for by a specific failure in the local-bench work:

> An evaluation that outputs a named category can be confidently wrong in a way a
> score cannot. A score is uninformative and honest about it. A wrong category
> arrives well-formed and looks exactly like a right one.

The instrument there reported all four systems as `stop,stop,stop,stop`, which matched
the "always abstains" row of the paper's own table exactly. The real cause was a
missing `amount` field in a fixture: the systems could not act because the input was
malformed. The signature of a correctly-identified policy and the signature of a
broken fixture were identical, and the design contained no check that distinguished
them. It was found by reading transcripts to see *why* systems behaved as they did.

The transferable rule: **when you enumerate candidate findings, also enumerate the
instrument failures that produce the same signature, and specify in advance what
distinguishes them.**

For dossier the live instance is: *no obligation in this clause* and *the extractor
dropped this clause* produce the same output. `freeze --check` proves the corpus bytes
have not drifted since the freeze; it cannot prove `extract.py` did not silently lose
a paragraph on the way in. That sits between G5 (schema validation on config inputs)
and integrity as currently scoped, and I do not think either covers it.

*Unverified:* I did not read `extract.py` and do not know whether it already has a
paragraph-count or coverage assertion. If it does, this is closed.

---

## 6. One transplantable gate for G2/G3

`~/.claude/hooks/check_figures.py` re-derives every number appearing in prose from a
single generator script and fails on drift. It is harness-independent — the git
`pre-commit` entry point works regardless of which agent or editor made the change.

This is the half of G2 that a run manifest does not cover. A manifest fixes
*attribution*: which model, which prompt, which date produced `41–48%`. It does not
stop that figure from being carried into a README sentence whose denominator has since
moved. Three of the eight retractions in the local-bench project were exactly that,
and the pattern is consistent: **stale figures survive retelling because they were
true once.** Recomputing a number confirms it; only tracing its provenance catches one
whose scope has drifted.

Point it at the `.md` files and a `derive.py` and it works unmodified. It needs a
generator script to exist first, which is §6 item 4 anyway.

Two cautions from running it: the checker itself shipped with two bugs (a relative
path that broke under a set `cwd`, and a regex that matched p-values as figures). Per
the `failed-check` skill's closing rule — break it deliberately and confirm it goes
red before believing a clean report.

---

## 7. What goes the other way

Three things in `ASSURANCE.md` are better than their counterparts in the local-bench
papers, and are being adopted rather than merely noted.

**§7.5 — the baseline is a distribution problem, not a quality problem.** The
local-bench blindspot paper justifies its own missing human baseline on
interpretability: without it we cannot say whether κ = +0.49 is high or low. §7.5's
framing is stronger and more actionable — without a ceiling, anyone else running the
benchmark learns their score and has nothing to compare it against except yours, which
makes it your results rather than a benchmark. That argument is being taken across.

**The V&V split, with integrity and release broken out.** Cleaner than anything in
either local-bench paper, and the justification is the right kind: they were separated
*because both had already failed in ways verification could not have caught*. Same
discipline as naming each method rule after the failure that bought it.

**§7.1 — refusing standards language the evidence does not support.** A project whose
thesis is findings that survive rebuttal cannot make a claim about itself that would
not. The tiered claim table in §7.2 is a better artefact than most published
model cards.

---

## 8. The standing of this note

It comes from a session that logged eight retractions in one day. Seven were found by
someone or something other than the session that made them — a reviewer's objection, a
different vendor's model, a provenance audit, the user asking a question. Near enough
none was self-initiated. Weight it accordingly, and check the parts that matter.

And the obvious caveat applies to the note itself: two sessions of the same model,
working separately, both concluded that the highest-value missing artefact was a human
baseline. By the table in §1, that convergence is worth about **1.16 auditors**. It is
weak evidence that the conclusion is right, and it is exactly the pattern the note is
about.

The way to make it stronger is not another model of the same lineage agreeing. It is
the thing both sessions already identified: one person, thirty rows, a day.

---

## Appendix — figure provenance

Every number in §1, and the script that produced it. Re-run before quoting.

| figure | source |
|---|---|
| 1,628 records; 48 defects; 30 artefacts | `blindspot/lib/analyze.py results/stage2/*.jsonl` |
| κ same-model +0.736 / diff-model +0.500 / Δκ +0.236 | `blindspot/lib/consistency.py`, WEIGHTS (primary) |
| permutation p < 0.0001 (20,000 permutations, labels permuted at model level) | same |
| lineage +0.736 / +0.702 / +0.493 | same, LINEAGE |
| prompt Δκ +0.012 | same, PROMPT (secondary) |
| determinism-free Δκ +0.202 (n = 72) | same, DETERMINISM-FREE |
| marginal-matched Δκ +0.151 (n = 598) | same, matched block |
| effective auditors 1.16 / 1.39 / 1.38 / 1.90 | `blindspot/lib/coverage.py` |
| qwen 54/180 = 30% excluded, ceiling 25% | `blindspot/PREREG.md`, applied in `analyze.py` |
| 151-record sample, 12 planted, seed 20260825 | `annotate/sample.jsonl`, `annotate/planted.json` |

Two cautions on quoting these:

**Do not pair +0.736 with the cross-vendor +0.493 and then quote Δκ = +0.236.** The
+0.236 delta is same-model against different-*model* (0.736 − 0.500). Same-model
against different-*vendor* is 0.243. The two deltas have different denominators and
mixing them is the denominator error this project keeps paying for. It appears in
`local-bench/paper/HANDOFF.md` and was caught here only by re-deriving.

**Effective auditors are not comparable across conditions with different base rates.**
The statistic is `j = log(1−C_obs)/log(1−p̄)`, so a condition whose individual auditors
are simply better can show *fewer* effective auditors at the same k. The same-vendor
row reads 1.14 of 2 against same-model's 1.16 for this reason, and should not be read
as same-vendor being worse. For lineage comparisons use the κ table, which is
marginal-corrected; use coverage only within a condition.

---

## Source documents

- `~/coding/local-bench/blindspot/PREREG.md` — registration, exclusion rule, two recorded deviations
- `~/coding/local-bench/blindspot/RESULT.md` — full result including the coverage section
- `~/coding/local-bench/paper/blindspot/MATHS.md` — the nine formulas, verified numerically
- `~/.claude/skills/research-method/SKILL.md` — the method rules, each named for the failure that bought it
- `~/coding/local-bench/paper/HANDOFF.md` — state of both papers; contains the denominator error noted above
