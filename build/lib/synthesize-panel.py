#!/usr/bin/env python3
"""Merge several panellists' answers into one, and write both documents.

    ./synthesize-panel.py --project . --doc arch-v8-1 \
        --panel out/panel.jsonl --panel out/panel-gx10.jsonl \
        --model Qwen3-235B-A22B-UD-Q3_K_XL \
        --url http://192.168.100.148:8085/v1/chat/completions \
        --consolidated out/panel-all.md --final out/panel-final.md

WHY THE SYNTHESIZER DOES NOT READ THE DOCUMENT. Two reasons, and the second is
the one that matters. The first is capacity: the largest model here serves a 64k
context and the document is ~105k tokens, so it does not fit. The second is that
a synthesizer which reads the source stops being a synthesizer and becomes
another panellist -- one whose opinion silently outranks the three it was meant
to weigh, and whose absence claims degrade with document length exactly as the
CUAD fixture measured (precision on "unmet" fell 100% -> 87.1% as the document
grew from 8k to 52k characters).

So it is grounded a different way: it sees every quote the panellists gave, each
already checked character-for-character against the frozen text, plus a window of
surrounding lines for each one. That is short, it is anchored to real locations,
and it carries no length penalty. Grounding where grounding is needed, rather
than everywhere.

The consolidated document is written whether or not synthesis succeeds, because
three independent answers are useful on their own and should never be hostage to
a fourth model being available.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import llm
from llm import load_doc                                       # noqa: E402

CONTEXT_LINES = 12

SYSTEM = """Several independent readers answered the same question about one
engineering document. You did not read the document. You are given their answers,
the verbatim quotes each supplied, and the document lines surrounding those
quotes.

Reply with JSON only:
{"answer": "...", "agreement": "...", "confidence": "high|medium|low"}

  - "answer": the best single answer, at most 400 words. Prefer claims supported
    by a quote. Where the readers agree, state it once, plainly.
  - "agreement": where they DISAGREE or where only one of them made a claim. Name
    the reader. This is the most useful part of your reply -- do not smooth it
    away, and do not average two incompatible readings into a vague one.
  - "confidence": low unless the quotes actually support the answer.

Never introduce a fact that appears in no answer and no quote. If the readers
collectively do not settle the question, say that."""


def ask(url, model, system, user, max_tokens, timeout):
    """Delegates to the shared client. This function used to build its own
    request and was missing response_format and the penalty overrides — it
    worked only because the model it happened to meet complied."""
    content, _reasoning, stop = llm.chat(url, model, system, user,
                                         max_tokens=max_tokens, timeout=timeout)
    return content, stop


def windows(lines, quotes):
    """Document lines around each verified quote, merged where they overlap."""
    flat = [" ".join(l.split()).lower() for l in lines]
    spans = []
    for q in quotes:
        needle = " ".join(q.split()).lower()[:80]
        if not needle:
            continue
        for n, line in enumerate(flat):
            if needle and needle in line:
                spans.append((max(0, n - CONTEXT_LINES),
                              min(len(lines), n + CONTEXT_LINES)))
                break
    if not spans:
        return ""
    spans.sort()
    merged = [list(spans[0])]
    for a, b in spans[1:]:
        if a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out = []
    for a, b in merged:
        out.append(f"[lines {a}-{b}]\n" + "\n".join(lines[a:b]))
    return "\n\n".join(out)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", required=True)
    p.add_argument("--doc", required=True)
    p.add_argument("--panel", action="append", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--consolidated", required=True)
    p.add_argument("--final", required=True)
    p.add_argument("--state", default="", help="JSONL of syntheses, resumed")
    p.add_argument("--max-tokens", type=int, default=4000)
    p.add_argument("--timeout", type=int, default=3600)
    args = p.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    _meta, lines = load_doc(project, args.doc)

    records = []
    for path in args.panel:
        if not os.path.exists(path):
            print(f"  missing {path}, skipping", file=sys.stderr)
            continue
        for line in open(path, encoding="utf-8"):
            try:
                records.append(json.loads(line))
            except Exception:
                continue
    if not records:
        sys.exit("no panel records found")

    by_q = {}
    for r in records:
        by_q.setdefault(r["qid"], []).append(r)
    order = sorted(by_q, key=lambda q: int(q) if q.isdigit() else 0)
    print(f"  {len(records)} answers across {len(order)} question(s) "
          f"from {len({r['model'] for r in records})} panellist(s)")

    # -- consolidated document, written first and unconditionally -------------
    out = ["# Panel answers — every reader, side by side", "",
           f"Document: `{args.doc}`. Each answer is one model reading the whole "
           f"document independently. Quotes are marked verbatim only where they "
           f"were found character-for-character in the frozen text.", ""]
    for qid in order:
        rs = by_q[qid]
        out += [f"## Q{qid}. {rs[0]['question']}", ""]
        for r in sorted(rs, key=lambda x: x["model"]):
            ok, total = len(r["quotes_verified"]), len(r["quotes"])
            out += [f"### {r['model']}", "",
                    f"*{ok}/{total} quotes verified verbatim · {r['seconds']}s*", "",
                    r["answer"] or "*(no answer)*", ""]
            if r["quotes_verified"]:
                out.append("Supporting quotes, confirmed present in the document:")
                out += [f"> {q}" for q in r["quotes_verified"]] + [""]
            if r["quotes_unverified"]:
                out.append("**Quotes NOT found in the document** — treat the "
                           "claims they support as unsupported:")
                out += [f"> {q}" for q in r["quotes_unverified"]] + [""]
    open(os.path.expanduser(args.consolidated), "w",
         encoding="utf-8").write("\n".join(out))
    print(f"  wrote {args.consolidated}")

    # -- synthesis ------------------------------------------------------------
    state = os.path.expanduser(args.state or (args.final + ".jsonl"))
    done = {}
    if os.path.exists(state):
        for line in open(state, encoding="utf-8"):
            try:
                r = json.loads(line)
                done[r["qid"]] = r
            except Exception:
                continue

    for qid in order:
        if qid in done:
            print(f"  Q{qid}: already synthesized")
            continue
        rs = by_q[qid]
        quotes = [q for r in rs for q in r["quotes_verified"]]
        parts = [f"QUESTION: {rs[0]['question']}", ""]
        for r in sorted(rs, key=lambda x: x["model"]):
            parts += [f"--- READER: {r['model']} ---", r["answer"], ""]
            if r["quotes_verified"]:
                parts += ["Its verified quotes:"] + \
                         [f"  \"{q}\"" for q in r["quotes_verified"]] + [""]
        ctx = windows(lines, quotes)
        if ctx:
            parts += ["--- DOCUMENT LINES AROUND THOSE QUOTES ---", ctx]
        started = time.time()
        try:
            content, why = ask(args.url, args.model, SYSTEM, "\n".join(parts),
                               args.max_tokens, args.timeout)
        except Exception as e:
            print(f"  Q{qid}: synthesis failed: {type(e).__name__}: {e}",
                  file=sys.stderr)
            continue
        m = re.search(r"\{.*\}", content, re.S)
        try:
            obj = json.loads(m.group(0)) if m else {}
        except json.JSONDecodeError:
            obj = {}
        rec = {"qid": qid, "question": rs[0]["question"],
               "answer": (obj.get("answer") or content).strip(),
               "agreement": (obj.get("agreement") or "").strip(),
               "confidence": (obj.get("confidence") or "").strip(),
               "readers": sorted(r["model"] for r in rs),
               "seconds": round(time.time() - started, 1), "finish_reason": why}
        with open(state, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        done[qid] = rec
        print(f"  Q{qid}: synthesized, confidence={rec['confidence']}, "
              f"{rec['seconds']}s")

    fin = ["# Final answers — synthesized", "",
           f"Document: `{args.doc}`. One answer per question, merged from the "
           f"panel. The synthesizer did not read the document; it saw the "
           f"answers, the verified quotes, and the lines around them.", ""]
    for qid in order:
        r = done.get(qid)
        fin += [f"## Q{qid}. {by_q[qid][0]['question']}", ""]
        if not r:
            fin += ["*Not synthesized. See the consolidated document.*", ""]
            continue
        fin += [r["answer"], ""]
        if r["agreement"]:
            fin += ["**Where the readers differed**", "", r["agreement"], ""]
        fin += [f"*Confidence: {r['confidence'] or 'unstated'} · readers: "
                f"{', '.join(r['readers'])}*", ""]
    open(os.path.expanduser(args.final), "w", encoding="utf-8").write("\n".join(fin))
    print(f"  wrote {args.final}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
