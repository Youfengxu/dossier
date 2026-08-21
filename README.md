# dossier

Review a large engineering deliverable against its requirements, using models you
run yourself.

Built for a review where the client's documents could not be sent to a hosted
model. The naive approach — hand the whole document to a long-context model and
ask what is missing — was tried first and measured: **153 minutes, 496,949 input
tokens, zero findings.** Everything here is the reaction to that.

**One dependency: PyYAML.** (`pypdf` too, and only if you ingest PDFs — the
earlier version of this line said PyYAML alone, which was wrong.) The toolkit
reads and writes `.docx` and `.xlsx` by hand, because `openpyxl` would not
install on the review machine. That constraint is why it runs on a locked-down
laptop, so it stayed after the constraint lifted.

## Try it in thirty seconds, with no model and no configuration

```sh
git clone <this repo> && cd dossier
./dossier review fixtures/floodtwin --doc deliverable-v2 --against deliverable-v1
```

That runs in about **0.15 seconds**, calls nothing, and prints real findings
against a synthetic deliverable that ships with the repo: every identifier
referenced once and never elaborated, every cross-reference that resolves to no
heading, every section that vanished between revisions, and every purpose-term
the document never uses.

Nothing on that path needs a GPU, an API key or an endpoint. Roughly half the
toolkit is deterministic and stays that way on purpose.

## What it does

Eight stages. Each consumes the previous one's output, and each is a separate
script you can run alone.

| Stage | Question | Model? |
|---|---|---|
| `freeze` | what exactly are we reviewing? | no |
| `sweep` | what does the document contradict about itself? | no |
| `closure` | which of our findings did this revision touch? | no |
| `obligations` | what does the requirements document actually require? | yes |
| `coverage` | is each obligation discharged, and where? | yes |
| `synthesize` | what is wrong that no single obligation asks about? | yes |
| `verify` | which candidates survive an attempt to refute them? | yes |
| `report` | the work product | no |

The rule the design turns on: **absence is computed, never asked.** A model is
never asked "is this missing?" — it cannot see the whole document, and the
question invites a confident guess. Models propose; deterministic code disposes.
`undefined.py` is the clearest case: a model nominates terms a section leaves
unexplained, and then every nomination is checked against the entire document for
a definition, a glossary entry or a heading before it can survive.

## Measured, including where it fails

Everything below is reproducible from artefacts in this repository.

| | |
|---|---|
| Retrieval width, precision | 6 passages 77.8% · **12 → 82.4%** · 24 → 81.2% |
| Cross-model agreement, 122 comments | 105 agree · 15 differ by one step · **2 contradict** |
| Run-to-run churn, temperature 0 | **10 of 58 verdicts moved**, leaning lenient |
| Absence-verdict precision vs length | 97.0% at 12k chars → **83.3% at 52k** |
| Verification pass | 25 candidates → 19 kept, 5 killed; precision 0.76 → 0.79 |
| Discovery, re-measured | **4 of 6** planted defects reached, at **4 of 40** candidates |

And the negative results, which are kept in the repository rather than deleted:

- **Five retrieval strategies** were implemented against ground truth — hybrid
  RRF, generated query expansion, cross-encoder reranking, section-first
  retrieval, wider windows. **None cleared the bar** on more than one test
  document.
- **`claim.py`** (contradiction detection) underperforms badly enough that it is
  not wired into the pipeline. It ships anyway because the class is real. Its
  recall has not been re-measured since `score-claims.py` was tightened, so no
  number is quoted here — an unbacked figure in this table would undermine every
  other row in it.
- **The term sweep** was superseded by `undefined.py` (18 of 26 versus a handful)
  and is kept, marked, so the failed approach stays visible.
- **Two of three "stability" runs** turned out to be byte-identical cache
  replays. The cache key is a hash of model, prompt and temperature, so a rerun
  without `DOSSIER_NO_CACHE` measures its own disk. `compare-coverage.py` exists
  because of that.

`DESIGN.md` is the long form: every decision, the evidence for it, and §4a, an
audit of where the author's own parameter choices may be overfitted to a
21-requirement fixture.

## Installing

```sh
ln -s "$PWD/dossier" ~/.local/bin/dossier      # or anywhere on your PATH
pip3 install pyyaml                            # required by the scorers
```

Point it at any OpenAI-compatible endpoint — Ollama, llama.cpp, vLLM, LM Studio:

```sh
export DOSSIER_CHAT_URL=http://localhost:11434/v1/chat/completions
export DOSSIER_MODEL=qwen3:8b
dossier doctor                                 # says what answers and what does not
```

## The one command to remember

```sh
dossier review <project> --doc <new> --against <old>
```

Every check that needs no model, in the order a reviewer wants them, in about ten
seconds. It ends by printing the command to run next.

## Starting from scratch

**Read**, in this order, and stop when you can run the two workflows below:

1. This file, end to end. Twenty minutes.
2. `DESIGN.md` **§4a (the overfitting audit)** and the **§3.19, "Three things that did not work"**
   entries. Read these before you improve anything — several obvious ideas were
   tried and measured worse, and the reasoning is recorded so you can disagree
   with evidence rather than repeat the experiment.
3. `--help` on any script you are about to run. Each carries its own rationale,
   not just its flags.

Everything else in `DESIGN.md` is reference. Read it when a tool surprises you.

**Set up:**

```sh
pip3 install pyyaml                 # required
pip3 install pypdf                  # only if a corpus holds PDFs
ln -s <repo>/tools/dossier/dossier ~/.local/bin/dossier
```

`.docx`, `.pptx` and `.xlsx` need nothing — `extract.py` reads them with the
standard library. A comment register may be `.xlsx`, `.csv`, `.tsv` or a table in
a `.docx`; the tools address its columns by letter in every case, so nothing
downstream changes with the format. For a `.docx`, `--sheet` takes the number of
the table when the document holds more than one — the reader refuses to guess
rather than confidently reading a layout table.

Every `--*-col` argument takes a header name as well as a letter, so
`--comment-col "Reviewer comment"` beats counting across to `D`. A name that
matches nothing is refused with the headers that do exist, and a name matching
two columns is refused rather than picked between. Delimiter and encoding are detected, which matters more than it sounds:
a semicolon-separated European export otherwise reads as one column per row, and
an Excel CSV carries a byte-order mark that turns the first header cell into
`﻿id` and makes every lookup by name miss without saying so. Point the tools at your own inference with `DOSSIER_CHAT_URL`,
`DOSSIER_MODEL`, `DOSSIER_EMBED_URL` and `DOSSIER_EMBED_MODEL`; the defaults are
this homelab's GX10 and Mac.

**Prove the install before touching client material.** `fixtures/floodtwin` is a
fictional procurement with 24 planted defects, 9 decoys and a ground-truth file
— no client content, safe to share or debug against any model:

```sh
dossier coverage tools/dossier/fixtures/floodtwin --doc deliverable-v1 \
    --out cov-check.csv
tools/dossier/score.py --project tools/dossier/fixtures/floodtwin \
    --coverage cov-check.csv
```

You should land near **recall 95%, precision 86%**. Materially below that and
the problem is your setup — model, endpoint or embeddings — not the corpus.
`score.py --project fixtures/floodtwin` with no other arguments scores the
committed reference run, which checks the scorer rather than your inference.

**Five things that are not obvious and will cost you a day each:**

1. **Client material never goes to a commercial model.** The whole toolkit
   exists for that constraint. Build tooling with whatever you like; run it on
   self-hosted inference only.
2. **A zero count is a lead, not a finding.** Absence claims need a synonym
   sweep and a read of the surrounding passage. Write `lexicon.yaml` the first
   time a check reports something absent that you know is present — on the live
   corpus the requirements are UK-spelled and the deliverable US-spelled, so
   `behaviour` is 0 in a document that says `behavior` 419 times.
3. **Always pass `--scope`** to a coverage run. See below.
4. **A finding's `terms` come from that finding's own wording** — an identifier,
   a quoted phrase. Never sweep for strings that happen to match.
5. **The finding register the tooling reads must be the one your client holds.**
   `register-map.yaml` keys on IDs, and IDs shift when a register is renumbered.
   Nine of one review's B-series pointed at the wrong finding for weeks because the
   map was built from a stale copy. `tools/check-map.py` in that project gates
   it in pre-commit; write the equivalent for a new engagement.

## Two workflows

### A new engagement

```sh
mkdir ~/reviews/acme && cd ~/reviews/acme && git init
mkdir source && cp ~/Downloads/*.docx source/         # originals, gitignored
dossier freeze . --init                               # scaffolds corpus.yaml
dossier add . source/deliverable-v1.docx --role draft --slug deliverable-v1
dossier add . source/rfo.pdf --role requirements --slug rfo
dossier status .                                      # what exists, what drifted
```

Roles are a closed set — `anchor` (the revision findings are raised against),
`requirements` (what the deliverable must satisfy), `draft` (under review),
`reference` (context, never the source of an obligation). Anything you want to
say beyond that goes in a free-text `note`.

Then extract obligations and trace them:

```sh
dossier coverage . --doc deliverable-v1 --scope architecture
```

This is the long one — roughly fifteen minutes, unattended, resumable from cache
if interrupted. It writes `cov-deliverable-v1.csv` and then ranks it by convergence.

**Always pass `--scope`.** One requirements document governs several
deliverables, and each obligation is tagged at extraction with the one that
discharges it. Without the flag, the architecture is judged against obligations
the roadmap or the MVP definition owes, and every one of those comes back
`unmet` — on one live review that is 62 structural false positives against 58 real
obligations, which buries the signal and doubles the model calls. `dossier
status` lists the scopes in your project and their counts. The tags are a model
judgement recorded in `obligations.yaml`, so read and correct any you disagree
with. Reasoning in `DESIGN.md` §3.6.

### A new revision lands

```sh
dossier add . source/deliverable-v2.docx --role draft --slug deliverable-v2
dossier review . --doc deliverable-v2 --against deliverable-v1          # seconds, no model
dossier coverage . --doc deliverable-v2 --scope architecture     # ~15 min, unattended
dossier report . --from deliverable-v1 --to deliverable-v2              # the work product
```

Read the `UNCHANGED` block first. Those are findings sitting on text that did not
move between revisions — not addressed, and no judgement required to say so.

`report` merges both halves into `review-<slug>.md`, ordered by how much
judgement each section needs: what was not addressed, what was removed, what was
touched and needs reading, and finally coverage clusters that match no open
finding. Where a finding the revision left alone *also* has obligations the RFO
trace reports undischarged, it is marked corroborated — that is the one claim in
the document supported by two methods sharing no machinery, and it is the one to
lead with.

It degrades: run it without a coverage file and you get the closure half alone.

**If the engagement has an agreed comment matrix**, put it at
`source/feedback-matrix.xlsx` (or `.csv`) and `report` finds it automatically. Build the
register map from it rather than hand-writing one — its IDs are the ones the
client and vendor both use, and a map keyed on anything else has to be
translated by hand before it can be sent:

```sh
dossier matrix . --matrix source/feedback-matrix.xlsx --sheet "Comments" --doc deliverable-v1
# --matrix accepts .xlsx, .csv or .tsv. --xlsx is still accepted as the old spelling.
# A .csv has no sheets, so --sheet is ignored for one.
```

The report then leads with an **adjudication check** instead of the closure
summary: every row's claimed status against what the revision shows. The verdict
worth having is `DISPUTED` — marked done, on text that did not move. It needs no
judgement and its evidence is a line number rather than an opinion. `UNCLAIMED
CHANGE` is the mirror image: not marked done, but the text moved anyway.

Finally, put the evidence back where the client will read it:

```sh
dossier writeback . --from deliverable-v1 --to deliverable-v2
```

This fills the *How/where comment adjudicated* column with a verdict and line
references per row, writing a **new** `-annotated.xlsx` plus a CSV of the same
content. It never edits the input — the matrix is jointly agreed, and a tool
that rewrites it in place can silently destroy the other side's entries. Cells
that already have content are left alone unless you pass `--overwrite`. Use
`--col K` to write into the client inputs column instead.

## Which tool answers which question

| Question | Command | Model? |
|---|---|---|
| Has anything drifted since I froze it? | `dossier status` | no |
| What identifiers dangle, what pointers resolve nowhere? | `dossier sweep --doc X` | no |
| What vanished between revisions? | `dossier sweep --doc X --compare Y` | no |
| Did they touch the passages my findings were about? | `dossier closure --from X --to Y` | no |
| Does the deliverable discharge each requirement? | `dossier coverage --doc X` | yes |
| Which flagged rows are worth reading first? | `dossier cluster --coverage F.csv` | yes (embeddings) |
| Give me the review document to send | `dossier report --from X --to Y` | no |
| Put the evidence back in the client's matrix | `dossier writeback --from X --to Y` | no |
| What does each section claim, own, and defer? | `dossier inventory --doc X --vllm` | yes, heavily |

Everything in the "no" rows finishes in seconds and is exactly reproducible. Run
those before anything that costs GPU time.

## Per-project files

`corpus.yaml` is the only required one. The rest earn their place when a sweep
surprises you:

- **`lexicon.yaml`** — spelling and synonym variants. Write this the first time a
  term-based check reports something absent that you know is present. On one
  corpus the requirements are UK-spelled and the deliverable US-spelled, so
  `behaviour` is 0 and `behavior` is 419; without the lexicon every check across
  that pair is silently wrong.
- **`register-map.yaml`** — your findings, each classified by the kind of
  reasoning that produced it, with distinctive `terms`. `closure.py` needs this.
- **`components.yaml`** — named components and aliases, for the synthesis passes.
- **`purpose-terms.txt`** — terms the vocabulary sweep should look for.

### Choosing `terms` for a finding

This is the one place where a careless choice quietly destroys the signal. Terms
must come from the finding's own wording — an identifier, a quoted phrase, a
function name. A common word matches hundreds of lines, and "did these lines
change" then tells you about the whole document rather than about your finding.
`closure.py` reports that case as `TOO BROAD` rather than pretending.

The opposite failure shows as `ABSENT`: terms that match nothing in either
revision. Fix those by reading how the document actually words the thing, not by
trying variants until something matches — that is fitting the tool to the
corpus.

Some findings, though, are *about* something being missing, and for those a zero
count is the answer rather than a bug. Mark them and the tool says so:

```yaml
- {id: B4, class: compliance, absence: true,
   title: "Hydrology Model has no component",
   terms: ["Hydrology Model", "hydrology model"]}
```

They report `STILL ABSENT` while nothing appears, and `ADDED` the moment it
does. The register has to declare this — zero and zero mean opposite things for
an absence finding and a mistyped term, and nothing in the counts distinguishes
them. Where the finding also names things that *do* exist and would have to
change, list those too: B7 is about an unspecified telemetry-ingest interface, and
naming the narrative inject contracts alongside turns a permanent `ABSENT` into
an `UNCHANGED` that proves those contracts were not extended either.

## What this does not do

- A `CHANGED` verdict means the text moved, not that the finding is closed. A
  real fix and a cosmetic reword are indistinguishable here.
- Coverage tracing reaches compliance gaps. It structurally cannot reach
  coherence findings — a document can satisfy every stated obligation and still
  contradict itself. On the live corpus roughly 40–45% of a findings register is
  reachable at best; the rest is yours.
- Absence precision decays with document length, measured 97% at 8k characters
  down to 83% at 52k. A zero count is a lead, never a finding.

## Endpoints

Defaults point at the homelab: GX10 `:8085` for chat, k11 for embeddings.
Override with `DOSSIER_CHAT_URL`, `DOSSIER_MODEL`, `DOSSIER_EMBED_URL`. Pass
`--vllm` on `coverage` or `inventory` to use the concurrent path instead —
llama-swap runs `--parallel 1` and serialises everything, so anything fanning out
over hundreds of sections wants vLLM.
