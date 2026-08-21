#!/usr/bin/env python3
"""Run every tool against the fixture, with a stubbed model. Exit non-zero on any
tool that cannot complete.

    ./smoke.py                  # all of them
    ./smoke.py --only trace     # substring filter, for one

WHY THIS EXISTS. Thirty-eight of forty-nine tools had never been executed against
data by any gate — CI asked each one for `--help` and nothing more. `to-html.py`
crashed on EVERY input from the moment records began carrying `__row__`, and the
gate reported it healthy for as long as that was true, because describing yourself
is not the same as working. The unit tests did not catch it either: they test
functions, and that bug lived in the seam between a shared data structure and the
tools that consume it.

The blocker was always that most tools need a model and CI has none. That stopped
being true when the transport became one function — `llm.chat()` — so one stub
covers every tool at once. `DOSSIER_FAKE_CHAT=1` returns a canned reply; the
embedder returns deterministic vectors derived from the text.

WHAT THIS DOES AND DOES NOT CHECK. It checks that a tool runs to completion on
plausible arguments and writes what it says it writes. It does NOT check that the
output is any good — the model's answers are stubs, so every verdict is meaningless
by construction. This catches crashes, import errors, argument drift, unpacking
errors on changed record shapes, and writers that produce nothing. That is exactly
the class that has been getting through.

A tool that cannot be driven here is listed in CANNOT_DRIVE with a reason, never
silently skipped. A harness that quietly covers less than it appears to is the
failure it was built to prevent.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.realpath(__file__))
FIXTURE = os.path.join(ROOT, "fixtures", "floodtwin")

# {p} project dir, {m} the comment register, {o} a scratch output dir.
CASES = [
    ("extract.py", "{f}/extractor-smoke.docx"),
    ("freeze.py", "--project {p} --check"),
    ("matrix.py", "--project {p} --matrix {m} --sheet Comments --doc deliverable-v2 "
                  "--id-col id --comment-col comment --out {p}/register-map.yaml"),
    ("to-html.py", "--matrix {m} --sheet Comments --title T --out {o}/m.html"),
    ("render.py", "--matrix {m} --sheet Comments --title T --out {o}/r.md "
                  "--id-col id --section-col section_ref --comment-col comment"),
    ("insert-column.py", "--matrix {p}/comments.xlsx --sheet Comments --after B "
                         "--header Added --out {o}/ins.xlsx"),
    ("sweep.py", "vocab --project {p} --doc deliverable-v2 "
               "--terms {p}/purpose-terms.txt"),
    ("undefined.py", "--project {p} --doc deliverable-v2"),
    ("inventory.py", "--project {p} --doc deliverable-v2 --out {o}/inv.json --limit 2"),
    ("obligations.py", "--project {p} --doc requirements --out {o}/obl.yaml --limit 2"),
    ("trace.py", "--project {p} --doc deliverable-v2 "
                 "--obligations {p}/obligations.yaml --out {o}/cov.csv --limit 2"),
    ("score.py", "--project {p} --ground-truth {p}/ground-truth.yaml "
                 "--obligations {p}/obligations.yaml --coverage cov-embed-12.csv"),
    ("verify.py", "--project {p} --doc deliverable-v2 "
                  "--coverage cov-embed-12.csv --out {o}/ver.csv --limit 2"),
    ("closure.py", "--project {p} --from deliverable-v1 --to deliverable-v2"),
    # Against the coverage trace.py wrote earlier in this run, not a second
    # committed file: cov-embed-6.csv exists on the machine this was written on
    # and is gitignored, so the case passed locally and failed in CI, which is
    # the exact shape of "works on my disk" this harness is meant to end.
    ("compare-coverage.py", "--a {p}/cov-embed-12.csv --b {o}/cov.csv "
                            "--label-a committed --label-b fresh"),
    ("diagnose-retrieval.py", "--project {p} --doc deliverable-v2 "
                              "--obligations {p}/obligations.yaml "
                              "--coverage cov-embed-12.csv"),
    ("cluster-findings.py", "--project {p} --coverage cov-embed-12.csv"),
    ("synthesize.py", "--project {p} --inventory {p}/inv-ablation.json --out {o}/syn.md"),
    ("claim.py", "--project {p} --doc deliverable-v2 --out {o}/claims.json --limit 2"),
    ("assess.py", "--project {p} --doc deliverable-v2 --matrix {m} --sheet Comments "
                  "--id-col id --comment-col comment --out {o}/assess.jsonl "
                  "--model stub --url http://stub.invalid/v1/chat/completions"),
    ("adjudicate.py", "--project {p} --matrix {m} --sheet Comments "
                      "--from deliverable-v1 --to deliverable-v2 "
                      "--id-col id --section-col section_ref --comment-col comment "
                      "--out {o}/adj.csv"),   # a matrix, not a log: it writes columns
    ("panel.py", "--project {p} --doc deliverable-v2 "
                 "--questions {p}/purpose-terms.txt --out {o}/panel.jsonl "
                 "--model stub --url http://stub.invalid/v1/chat/completions"),
    ("repair-cites.py", "--project {p} --doc deliverable-v2 {o}/assess.jsonl"),
    ("bundle.py", "--project {p} --doc deliverable-v2 --coverage cov-embed-12.csv "
                  "--out-xlsx {o}/b.xlsx --out-md {o}/b.md"),
    ("register.py", "--project {p} --from deliverable-v1 --to deliverable-v2 "
                    "--coverage cov-embed-12.csv"),
    ("inputs-column.py", "--matrix {m} --sheet Comments --out {o}/in.csv "
                         "--id-col id"),
    ("dossier", "review {p} --doc deliverable-v2 --against deliverable-v1"),
]

# Named, not skipped. Each of these needs something a fixture cannot supply.
CANNOT_DRIVE = {
    "doctor.py": "its whole job is checking a real endpoint; under a stub the "
                 "check is vacuous, and a green result would be a lie",
    "prefix-bench.py": "measures wall-clock against a real endpoint; under the "
                       "stub every turn is instant and the ratio it reports is "
                       "meaningless, which is worse than not running it",
    "supervise.py": "restarts a systemd unit and polls a real endpoint",
    "converse.py": "an interactive REPL; it reads stdin and never returns",
    "agree.py": "needs two completed evaluator workbooks, not one register",
    "combine-evaluators.py": "same — two evaluator outputs to put side by side",
    "assess-to-matrix.py": "same — consumes evaluator JSONL keyed to a workbook",
    "render-assess.py": "covered by tests/test_checkers.py, which drives it directly",
    "action-check.py": "needs a matrix carrying vendor action and adjudication columns",
    "writeback.py": "needs a closure map from a completed run",
    "score-claims.py": "needs a claims file scored against its own ground truth",
    "score-register.py": "needs a register map plus findings from a completed run",
    "judge-panel.py": "needs a completed panel with several readers",
    "synthesize-panel.py": "same",
    "repair-panel.py": "same",
    "make-denylist.py": "regenerates the scrub list from private material",
    "conformance.py": "is itself a gate; CI runs it directly",
    "run-tests.py": "is itself a gate",
    "check-clean.py": "is itself a gate",
    "check-refs.py": "is itself a gate",
    "smoke.py": "is this",
    "llm.py": "library, no CLI",
    "vocabulary.py": "library, no CLI",
    "dossier_cli.py": "the dispatcher; driven through ./dossier",
}


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="substring filter on the tool name")
    parser.add_argument("--keep", action="store_true",
                        help="leave the scratch project on disk")
    args = parser.parse_args()

    workspace = tempfile.mkdtemp(prefix="dossier-smoke-")
    project = os.path.join(workspace, "project")
    shutil.copytree(FIXTURE, project)
    out = os.path.join(workspace, "out")
    os.makedirs(out)
    matrix = os.path.join(project, "comments.csv")

    environment = dict(os.environ, DOSSIER_FAKE_CHAT="1",
                       DOSSIER_NO_CACHE="1", PYTHONWARNINGS="ignore")

    print(__doc__.strip().split("\n")[0])
    print("=" * 74)
    passed, failed = [], []
    for tool, template in CASES:
        if args.only and args.only not in tool:
            continue
        argv = template.format(p=project, m=matrix, o=out, f=project).split()
        command = ([sys.executable, os.path.join(ROOT, tool)] if tool.endswith(".py")
                   else [os.path.join(ROOT, tool)]) + argv
        done = subprocess.run(command, capture_output=True, text=True,
                              cwd=workspace, env=environment, timeout=300)
        if done.returncode == 0:
            passed.append(tool)
            print(f"  ok    {tool}")
        else:
            failed.append(tool)
            tail = (done.stderr or done.stdout).strip().splitlines()
            print(f"  FAIL  {tool}  (exit {done.returncode})")
            for line in tail[-4:]:
                print(f"        {line[:110]}")

    print("=" * 74)
    print(f"  {len(passed)}/{len(passed) + len(failed)} tools ran to completion")
    if not args.only:
        print(f"  {len(CANNOT_DRIVE)} not driven here:")
        for tool, why in sorted(CANNOT_DRIVE.items()):
            print(f"      {tool:<24} {why}")
    if args.keep:
        print(f"\n  workspace kept at {workspace}")
    else:
        shutil.rmtree(workspace, ignore_errors=True)
    if failed:
        print("\n  A tool that cannot run against the fixture cannot run against a\n"
              "  deliverable either. Fix the tool, or move it to CANNOT_DRIVE with\n"
              "  a reason someone else can check.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
