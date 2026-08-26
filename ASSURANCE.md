# dossier — proposed assurance framework

Where every artefact fits, and where the holes are. Built by placing all 51
modules, 4 documents and 7 fixtures; anything that would not fit is called out
rather than filed under a vague heading.

---

## 1. The frame

Dossier's thesis is *findings that survive rebuttal*. The repository is the same
claim turned on itself: **why should anyone believe this tool's output?** That is
an **assurance case**, and safety-critical engineering has a standard shape for it
— Claims / Argument / Evidence (CAE, or GSN in DO-178C, ISO 26262, IEC 61508).

    CLAIM      A finding from this pipeline is trustworthy.
      │
      ├── ARGUMENT      DESIGN.md — 45 decisions, each with the measurement
      │                 that forced it. ARCHITECTURE.md — the stages and the
      │                 data contract between them.
      │
      └── EVIDENCE      four kinds, below

The evidence splits along the **verification / validation** line that IEEE 1012
and ISO/IEC 12207 have used for decades, and that the AI field has re-coined as
*tests vs evals*:

| | asks | fails when | gates? |
|---|---|---|---|
| **Verification** | did we build the thing **right**? | the code disagrees with the spec | yes — every push |
| **Validation** | did we build the **right thing**? | the method or the model is unfit | no — it informs design |
| **Integrity** | are the inputs what we say they are? | evidence drifts under a locator | yes |
| **Release** | is this artefact safe to publish? | client identity leaks, references rot | yes — at publish |

Integrity and Release are usually folded into verification. They are separated
here because both have already failed in ways verification could not have caught:
a re-saved `.docx` produces different bytes for the same words, and a working tree
can be clean while the git history is not.

---

## 2. Where everything fits

### 2.1 Argument — why this approach

| artefact | industry term | state |
|---|---|---|
| `DESIGN.md` §0 + 45 sections | **Architecture Decision Records** | ✅ each carries the measurement that forced it — rare |
| `ARCHITECTURE.md` | system description (arc42/C4-ish) | ✅ 8 stages + the data contract between them |
| `ADAPTERS.md` | **interface contract** | ✅ 6 invariants, machine-proved by `conformance.py` |
| `README.md` | entry point | ⚠️ leads with the tool, not the evidence |

### 2.2 The application — the checker itself

| group | modules | n |
|---|---|---|
| **Pipeline stages** | `freeze` `sweep` `closure` `obligations` `trace` `synthesize` `verify` `register` | 8 |
| **Alternate analyses** | `assess` `adjudicate` `claim` `action-check` `undefined` `cluster-findings` `panel` `converse` `inventory` | 9 |
| **Shared core** | `llm` `matrix` `vocabulary` `locate` | 4 |
| **Output & rendering** | `bundle` `render` `render-assess` `to-html` `writeback` `inputs-column` `insert-column` `assess-to-matrix` `combine-evaluators` `synthesize-panel` | 10 |
| **Recovery / ops** | `repair-cites` `repair-panel` `supervise` `doctor` | 4 |
| **Entry** | `dossier_cli` | 1 |

**Observation worth keeping:** *ten of fifty-one modules exist to make findings
readable.* That is not bloat — it is the thesis showing up in the file listing. A
finding that a reviewer cannot check is not evidence, so rendering is part of the
product, not a presentation afterthought.

### 2.3 Verification evidence — is the code right?

| tier | artefact | count | industry term |
|---|---|---|---|
| 1 | `tests/` | 215 | unit tests |
| 2 | `run-tests.py --mutate` | 15/15 | **mutation testing** |
| 3 | `conformance.py` | 6/6 | **contract testing**, proved by mutation |
| 3 | `smoke.py` | 27/27 | integration / E2E |
| 0 | `doctor.py` | — | preflight / environment check |

### 2.4 Validation evidence — is the method right?

| artefact | what it measures |
|---|---|
| `fixtures/{ntsb,cuad,sec,cm1,aries}` | labelled corpora, ground truth by others |
| `score.py` `score-claims.py` `score-register.py` `fixtures/*/score-*.py` | scorers |
| `compare-coverage.py` `agree.py` `judge-panel.py` | run-to-run and judge-to-judge comparison |
| `diagnose-retrieval.py` | why retrieval returned what it did |
| `prefix-bench.py` | latency/caching — a cost eval, currently orphaned |

Established so far: retrieval is blind to alternate-route compliance (**6% vs
53%**); no model tested is an outcome judge (**41–48%** where wording and outcome
diverge); a three-model panel splits on **10 of 25** rows with monotonic severity.

### 2.5 Integrity evidence — are the inputs what we say?

| artefact | role |
|---|---|
| `freeze.py` + `parsed/MANIFEST.json` | hash-pinned corpus — a lockfile for evidence |
| `freeze.py --check` | drift detection, exits non-zero |
| `extract.py` | the one extractor, so bytes are reproducible |
| `locate.py` | `slug:line` — the address every claim resolves through |

### 2.6 Release evidence — safe to publish?

| artefact | role |
|---|---|
| `check-clean.py` | scrub gate — hashed denylist, working tree |
| `make-denylist.py` | regenerates the denylist from private plaintext |
| `check-refs.py` | cross-reference integrity across the docs |
| `hooks/pre-commit` | scrub + tests + workflow parse |

### 2.7 Alignment to published standards

Three standards are worth naming, and they are **not interchangeable** — they
operate at different levels, and mapping all three onto a repository would be
box-ticking. Each earns a different job.

> **Precision caveat.** What follows uses the structure of each standard, not
> clause-level detail. Before any of this is quoted to a client, check the wording
> against the published text — the framing below is reliable, the exact control
> identifiers are not asserted.

### ISO/IEC 25059 — the best fit, and it organises the evidence

25059 extends SQuaRE (ISO/IEC 25010) to AI systems: a **product quality model**.
That is the right altitude for a tool, because it asks what qualities the thing
has and how each is measured — which is what §2.3–2.6 already are, in local
vocabulary.

| 25059-style characteristic | where dossier already evidences it | strength |
|---|---|---|
| Functional correctness | 215 unit tests, 15 mutations, 6 contract checks | **strong** |
| Robustness | fixtures carry deliberate decoys; adversarial checker tests | moderate |
| Transparency / explicability | every finding carries `slug:line` + verbatim quote | **strong** — this is the thesis |
| Accuracy (task-level) | eval suite on 5 labelled corpora | **weak — no human baseline (G1)** |
| Reliability / repeatability | hash-pinned corpus, `--refreeze` invalidates locators loudly | **strong** |
| Maintainability | ADRs with measurements, contract-proved adapters | strong |

Adopting 25059's vocabulary costs nothing and makes the evidence sections legible
to anyone who has met SQuaRE. It also names the hole precisely: **accuracy is the
one characteristic with no target.**

### NIST AI RMF 1.0 — organises the argument, and names our central risk

The four functions map onto this framework unevenly, and the unevenness is itself
informative:

| RMF function | dossier | state |
|---|---|---|
| **MAP** — context, capabilities, limits | `DESIGN.md`, `ARCHITECTURE.md`, the "what this cannot see" notes in every report | **strong** |
| **MEASURE** — analyse, benchmark, track | the eval suite (§2.4) | present, but **no provenance, no trend (G2, G3)** |
| **MANAGE** — respond, recover, communicate | `repair-cites`, `repair-panel`, `supervise`, `doctor`; release gates | moderate |
| **GOVERN** — policy, roles, accountability | *almost nothing* | **absent, and honestly so** |

GOVERN being empty is the correct answer for a single-maintainer repository, and
it should be written down as such rather than left blank — the same discipline the
reports already apply to diagrams they cannot read.

**The specific win is NIST AI 600-1**, the Generative AI profile. Its risk list
names **confabulation** — plausible content asserted without support. Dossier's
entire citation apparatus is a confabulation control, and it is unusual in being
*measured*: a citation must exist, sit near its quote, and discuss the claim
attached to it, with the failure modes documented. Framing it that way turns an
idiosyncratic feature into a recognised control against a named risk.

### ISO/IEC 42001 — mostly not applicable to the repository

42001 certifies an **organisation's** AI management system: policies, roles,
impact assessment, supplier management, lifecycle governance. A repository cannot
be 42001-conformant; a company operating it can.

So it does not belong in this framework — **but it is commercially relevant.** For
a consultancy reviewing deliverables for public-sector clients, being able to say
the review method has documented decision records, a measured evaluation suite,
provenance controls and release gates is most of the evidence an AIMS audit would
ask for. The artefacts here would feed a 42001 programme; they do not constitute
one.

**Recommendation:** adopt 25059 vocabulary in the evidence sections, use the RMF
functions to structure the risk narrative (including the honest GOVERN gap), and
keep 42001 out of the repository — carry it in the consultancy's own material,
where it belongs.

---

## 3. The gaps

Ordered by what they cost. **Every one is in validation, integrity or release —
none is in verification.**

### G1 — No human baseline. *(validation; blocking for publication)*

The only agreement data is **model-vs-model** (`readers AGREE 14/18 = 78%`). There
is no human ceiling anywhere in the repo. So when a model scores 53% on
alternate-route recall, nothing says whether that is poor, adequate, or near the
limit of a task humans also find hard.

**Validation without a target is not validation.** Two people independently
adjudicating ~30 NTSB rows, reported with agreement, would fix it. It is the
single highest-value missing artefact, and it is roughly a day.

### G2 — Eval runs have no provenance. *(validation)*

No scorer records which model, which prompt, which endpoint, which date produced a
number. `41–48%` cannot be reproduced or attributed, and it will be quoted in a
public README. Standard practice is a run manifest per result.

### G3 — No results store, so no trend. *(validation)*

Findings live in prose and commit messages. When a model or prompt changes there
is no way to detect regression except memory. `evals/results/<date>-<run>.json`
plus a one-line summary table is the conventional fix.

### G4 — `check-clean` cannot see git history. *(release; blocking for publication)*

Verified working-tree-only. engagement identifiers sit in three commits
reachable from `main`, including the scrub commit whose own diff contains what it
removed. The gate reports clean; `git clone` ships all of it.

### G5 — Input validation stops at bytes. *(integrity)*

`freeze --check` proves the corpus has not drifted. Nothing proves
`obligations.yaml`, `register-map.yaml` or `corpus.yaml` are well-formed before a
run consumes them — schema validation, not hashing.

### G6 — No monitoring. *(runtime; probably N/A)*

The fourth ML Test Score category. Nothing watches live output. For batch review
this is defensible — but it should be a recorded *"not applicable, because…"*
rather than a silence, since a reader cannot tell absence from oversight.

### G7 — `prefix-bench` is an orphan eval. *(validation; minor)*

It measures a real property (105× on Mac, 19× on GX10) and belongs in the eval
suite with the others rather than sitting loose as a script.
### G8 — GOVERN is empty. *(organisational; record as N/A)*

The only gap the standards lens found that the bottom-up inventory did not. NIST
AI RMF's GOVERN function — policy, roles, accountability, escalation — has
essentially no counterpart here, and ISO/IEC 42001 is entirely about this layer.

For a single-maintainer repository that is the **correct** answer, not a failing.
But it should be written down with its reason, exactly as G6 should be. An empty
GOVERN section that says *"single maintainer; accountability sits with the
operator; this repository makes no organisational claim"* is a different artefact
from a silence, because a reader can tell the difference between a decision and
an oversight. If the consultancy later pursues 42001, this is where that work
attaches — and the artefacts in §2.1–2.6 are most of its evidence base.

---

## 4. What is extra

Present here and rare in normal practice. None is waste; all four are evidence in
the assurance case, and they are why the verification half is unusually strong.

| | why it is rare |
|---|---|
| **Mutation testing** | most teams never do it; it is the only tier that can prove a green suite is meaningless |
| **Contract testing proved by mutation** | contract tests are common; breaking the adapter to prove they fire is not |
| **ADRs carrying their measurement** | ADRs are common; ADRs that cite the experiment are not |
| **Corpus hash-pinning** | reproducibility this strict is a regulated-domain habit |
| **Doc cross-reference + scrub gates** | almost unheard of outside compliance work |

---

## 5. The shape of the imbalance

> **Verification is stronger than most production ML systems. Validation has a
> hole where the target should be.**

**Two independent routes reached the same conclusion**, which is the strongest
thing in this document. The gaps in §3 came from a bottom-up inventory — placing
51 modules and seeing what had no home. The standards in §2.7 came top-down from
three published frameworks. They agree: ISO/IEC 25059 identifies *accuracy* as the
one quality characteristic with no target (G1); NIST AI RMF finds MEASURE present
but untracked (G2, G3). Only GOVERN (G8) appeared from the top down and not from
the bottom up, and it is the one gap that is arguably correct as it stands.

Convergence from two directions is not proof, but a bottom-up audit and three
standards independently landing on *the same missing baseline* is much better
evidence than either alone.

Every gap above is in validation, integrity, release or governance. That is a coherent
pattern, not an accident: verification is checkable from inside the repo, and the
other three need something outside it — a human adjudicator, a stored history, a
view of git rather than the tree.

For the public repo this matters twice over, because the eval numbers are the
reason to trust everything else, and today they have no baseline, no provenance
and no history.

---

## 6. Sequence

| # | do | closes | why first |
|---|---|---|---|
| 1 | `check-clean --history` | G4 | gates the publish and confirms the blocker independently |
| 2 | human baseline on ~30 NTSB rows | G1 | gives every existing number a meaning — **and is what makes the benchmark portable rather than a case study (§7.5)** |
| 3 | run manifest in the scorers | G2 | cheap, and G3 needs it |
| 4 | `evals/results/` + summary table | G3, G7 | makes claims traceable to runs |
| 5 | schema validation on inputs | G5 | |
| 6 | record monitoring and GOVERN as N/A, with reasons | G6, G8 | a stated decision is not a silence |

1 and 2 are independent and can run in parallel. Nothing here requires the
directory reorganisation; that is cosmetic by comparison and can follow.

---

## 7. Positioning: what may be claimed, and what makes it portable

### 7.1 The constraint this project puts on its own marketing

A method whose thesis is *findings that survive rebuttal* cannot make a claim
about itself that would not survive rebuttal. Branding this as a standards
implementation would violate §0.1 — asserting what the evidence does not support.

| claim | allowed | why |
|---|---|---|
| "Implements ISO/IEC 25059" | **no** | a quality *model*, not a spec; nothing to implement, no certification scheme |
| "Implements / complies with NIST AI RMF" | **no** | voluntary and non-prescriptive; NIST certifies nobody, and GOVERN is empty here |
| "ISO/IEC 42001 compliant" | **no, and legally risky** | needs an accredited audit of an organisation's management system |
| "certified" / "conformant" (any standard) | **no** | without an audit these are false statements, not stretches |

There is also a self-inflicted limit: **until G1 is closed, accuracy cannot be
claimed at all** — only *checkability*. Standards language would paper over
exactly the gap this document just identified.

### 7.2 Claim language, tiered by evidence

**Defensible today**

- "Structured as an assurance case — claim, argument, evidence."
- "Every finding carries a stable locator and verbatim quote; citations are
  machine-verified for existence, proximity and topical support."
- "Evaluated against five independently-labelled public corpora, with failure
  modes published."
- "Implements a confabulation control in the sense of NIST AI 600-1."

**Defensible after the work in §6**

- "Evidence organised using ISO/IEC 25059 quality characteristics."
- "Risk narrative structured on NIST AI RMF functions; GOVERN out of scope for a
  single-maintainer tool."
- "Task accuracy measured against a human baseline of *n* adjudicators." *(G1)*

**Never without an audit:** certified, compliant, conformant.

### 7.3 Is this a case study? No — but only because of the corpora

The distinction is sharp and worth defending:

| | case study | what this is |
|---|---|---|
| evidence | "it worked on our client's document" | measured on public, third-party-labelled corpora |
| claim | about one engagement | about a **technique** — retrieval vs whole-document, panel vs single model |
| reproducible by others | no | yes — the corpora are downloadable and the scorers ship |

*"On NTSB's published adjudications, retrieval recovers 6% of alternate-route
compliance against 53% for whole-document"* is a **benchmark result**, not an
anecdote. Anyone can rerun it, including against a tool that is not this one.

The genuine case-study material is the engagement itself, and it cannot be
published anyway. That is a constraint that turned out to help: it forced every
claim onto public corpora, which is precisely what makes the claims portable.

### 7.4 Three shippable artefacts, not one

Portability comes from separating them. Each is usable without the others:

| # | artefact | who uses it | usable alone? |
|---|---|---|---|
| **1** | **The benchmark** — 5 labelled corpora + scorers + a stated protocol | anyone measuring *their own* review tool | **yes** — the most portable thing here |
| **2** | **The method** — ADRs, the assurance-case skeleton, §0.1 and its checklist | anyone building an evidence-bound AI pipeline | **yes** — prose and patterns |
| **3** | **The reference implementation** — the 8-stage pipeline | anyone reviewing documents; everyone else reads it as proof the method is buildable | yes, but the least differentiated |

A case study would be a fourth thing. It is not on this list, and it is not
publishable.

### 7.5 The human baseline is the portability unlock

G1 reads like a quality gap. It is really a **distribution** gap.

Without a baseline the eval numbers are *your* numbers: someone else running the
benchmark against their own tool learns their score and has nothing to compare it
to except yours. With a baseline — even a small one, two adjudicators over thirty
rows — the benchmark acquires a **ceiling**, and a score becomes interpretable
without reference to this project at all.

That is what converts artefact 1 from "our results" into a benchmark other people
can use, which is the whole difference between publishing a case study and
publishing something portable. **G1 is therefore the highest-leverage item in
§6 — not because accuracy is unknown, but because portability depends on it.**

### 7.6 The position

Standards alignment is table stakes; every vendor claims it and it differentiates
nothing. What is rare here is:

> **A document-review method that publishes where it fails, on corpora anyone can
> check.**

Lead with the measurements and the failure modes. Standards references then serve
as supporting structure rather than the headline — which is also the only honest
ordering, since the evals would survive an auditor's questions today and the
standards claims would not.
