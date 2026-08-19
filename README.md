# dossier

Reviewing a large deliverable against its requirements, without sending it to a
commercial model. Everything here runs against the homelab.

`DESIGN.md` is the reasoning — every decision, and the evidence for it, including
the things that did not work. This file is the operating instructions.

## The one command to remember

```sh
~/homelab/tools/dossier/dossier review <project> --doc <new> --against <old>
```

Runs every check that needs no model, in the order a reviewer wants them, in
about ten seconds. It ends by printing the next command. If you remember nothing
else on this page, remember that line — `dossier --help` recovers the rest.

Put it on your PATH once. `~/.local/bin` is on this Mac's PATH (`.zshrc`);
`~/bin` is **not**, despite holding scripts — check with `env -i HOME="$HOME"
zsh -lic 'echo $PATH'` rather than `echo $PATH`, since a shell spawned from a
tool inherits that tool's PATH and will happily report a directory the login
shell never sees.

```sh
ln -s ~/homelab/tools/dossier/dossier ~/.local/bin/dossier
```

## Starting from scratch

**Read**, in this order, and stop when you can run the two workflows below:

1. This file, end to end. Twenty minutes.
2. `DESIGN.md` **§4a (the overfitting audit)** and the **"Known not to work"**
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
standard library. Point the tools at your own inference with `DOSSIER_CHAT_URL`,
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
   Nine of ODIN's B-series pointed at the wrong finding for weeks because the
   map was built from a stale copy. `tools/check-map.py` in that project gates
   it in pre-commit; write the equivalent for a new engagement.

## Two workflows

### A new engagement

```sh
mkdir ~/reviews/acme && cd ~/reviews/acme && git init
mkdir source && cp ~/Downloads/*.docx source/         # originals, gitignored
dossier freeze . --init                               # scaffolds corpus.yaml
dossier add . source/architecture-v3.docx --role draft --slug arch-v3
dossier add . source/rfo.pdf --role requirements --slug rfo
dossier status .                                      # what exists, what drifted
```

Roles are a closed set — `anchor` (the revision findings are raised against),
`requirements` (what the deliverable must satisfy), `draft` (under review),
`reference` (context, never the source of an obligation). Anything you want to
say beyond that goes in a free-text `note`.

Then extract obligations and trace them:

```sh
dossier coverage . --doc arch-v3 --scope architecture
```

This is the long one — roughly fifteen minutes, unattended, resumable from cache
if interrupted. It writes `cov-arch-v3.csv` and then ranks it by convergence.

**Always pass `--scope`.** One requirements document governs several
deliverables, and each obligation is tagged at extraction with the one that
discharges it. Without the flag, the architecture is judged against obligations
the roadmap or the MVP definition owes, and every one of those comes back
`unmet` — on ODIN that is 62 structural false positives against 58 real
obligations, which buries the signal and doubles the model calls. `dossier
status` lists the scopes in your project and their counts. The tags are a model
judgement recorded in `obligations.yaml`, so read and correct any you disagree
with. Reasoning in `DESIGN.md` §3.6.

### A new revision lands

```sh
dossier add . source/architecture-v4.docx --role draft --slug arch-v4
dossier review . --doc arch-v4 --against arch-v3          # seconds, no model
dossier coverage . --doc arch-v4 --scope architecture     # ~15 min, unattended
dossier report . --from arch-v3 --to arch-v4              # the work product
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

**If the engagement has an agreed adjudication matrix**, put it at
`source/adjudication-matrix.xlsx` and `report` finds it automatically. Build the
register map from it rather than hand-writing one — its IDs are the ones the
client and vendor both use, and a map keyed on anything else has to be
translated by hand before it can be sent:

```sh
dossier matrix . --xlsx source/adjudication-matrix.xlsx --sheet "D2 - Architecture" --doc arch-v5
```

The report then leads with an **adjudication check** instead of the closure
summary: every row's claimed status against what the revision shows. The verdict
worth having is `DISPUTED` — marked done, on text that did not move. It needs no
judgement and its evidence is a line number rather than an opinion. `UNCLAIMED
CHANGE` is the mirror image: not marked done, but the text moved anyway.

Finally, put the evidence back where the client will read it:

```sh
dossier writeback . --from arch-v5 --to arch-v7
```

This fills the *How/where comment adjudicated* column with a verdict and line
references per row, writing a **new** `-annotated.xlsx` plus a CSV of the same
content. It never edits the input — the matrix is jointly agreed, and a tool
that rewrites it in place can silently destroy the other side's entries. Cells
that already have content are left alone unless you pass `--overwrite`. Use
`--col K` to write into the DSTA/DIS inputs column instead.

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
  term-based check reports something absent that you know is present. On the ODIN
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
   title: "Community Behaviour and Effects has no component",
   terms: ["Community Behaviour and Effects", "community behaviour"]}
```

They report `STILL ABSENT` while nothing appears, and `ADDED` the moment it
does. The register has to declare this — zero and zero mean opposite things for
an absence finding and a mistyped term, and nothing in the counts distinguishes
them. Where the finding also names things that *do* exist and would have to
change, list those too: B7 is about an unspecified content-inject interface, and
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
