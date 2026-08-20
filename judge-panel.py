#!/usr/bin/env python3
"""Have a local model judge the reasoning in each panel answer, blind.

    ./judge-panel.py --project . --doc arch-v8-1 \
        --panel out/panel-gx10.jsonl --panel out/panel-qwen.jsonl \
        --model Qwen3-235B-A22B-UD-Q3_K_XL \
        --url http://192.168.100.148:8085/v1/chat/completions \
        --out out/panel-judged.jsonl --report out/panel-judged.md

WHY BLIND. The judge is told "Reader A", never "gpt-oss-120b". A judge that knows
which model it is grading has a second reason to prefer one answer, and on a
panel whose whole purpose is disagreement that is the one bias worth spending
effort to remove. Labels are restored after scoring, not before.

WHY A LOCAL JUDGE. The material is a client deliverable. The measurable proxies —
defined-term density, section references, quote fidelity — can be computed
without a model reading anything. Judging the ARGUMENT cannot, so the model that
reads it has to be one that runs on hardware you own.

WHAT IT IS AND IS NOT TOLD. It sees the question, the answer, the quotes that
were CONFIRMED verbatim against the frozen text, and the document lines around
them. It does not see the whole document, and it is told which quotes failed
verification without being shown them, so it can weigh an answer that leant on
evidence that turned out not to exist.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from llm import load_doc                                       # noqa: E402

SYSTEM = """You are grading how well a reader answered a question about an
engineering document you have not read in full. You are shown the question, the
reader's answer, the quotes from that answer that were mechanically CONFIRMED to
appear in the document, and the document lines around those quotes.

Reply with JSON only:
{"specificity": 1-5, "support": 1-5, "candour": 1-5,
 "verdict": "...", "strongest": "...", "weakest": "..."}

  - specificity: does it engage the document's own named mechanisms and defined
    terms, or could the answer have been written about any system of this kind?
    5 = names the actual components and how they connect. 1 = generic prose.
  - support: do the confirmed quotes and surrounding lines actually establish the
    claims made? An answer whose central claim rests on a quote that failed
    verification scores 2 or below.
  - candour: does it distinguish what the document states from what the reader
    inferred, and name gaps plainly? Confident silence about a gap scores low.
  - verdict: two sentences, concrete.
  - strongest / weakest: one clause each, quoting or naming the specific claim.

Grade only what is in front of you. Do not reward length or fluency. An answer
that says "the document does not define this" and is right is better than one
that fills the space."""


def ask(url, model, system, user, max_tokens, timeout):
    payload = {"model": model, "temperature": 0, "max_tokens": max_tokens,
               "response_format": {"type": "json_object"},
               "presence_penalty": 0.0, "frequency_penalty": 0.0,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}]}
    req = urllib.request.Request(
        url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as f:
        body = json.load(f)
    c = body["choices"][0]
    return (c["message"].get("content") or "").strip(), c.get("finish_reason") or ""


CONTEXT = 10


def windows(lines, quotes):
    flat = [" ".join(l.split()).lower() for l in lines]
    spans = []
    for q in quotes:
        needle = " ".join(q.split()).lower()[:80]
        if not needle:
            continue
        for n, line in enumerate(flat):
            if needle in line:
                spans.append((max(0, n - CONTEXT), min(len(lines), n + CONTEXT)))
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
    return "\n\n".join(f"[lines {a}-{b}]\n" + "\n".join(lines[a:b]) for a, b in merged)


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", required=True)
    p.add_argument("--doc", required=True)
    p.add_argument("--panel", action="append", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--report", required=True)
    p.add_argument("--max-tokens", type=int, default=2000)
    p.add_argument("--timeout", type=int, default=1800)
    args = p.parse_args()

    _meta, lines = load_doc(os.path.abspath(os.path.expanduser(args.project)),
                            args.doc)
    rows = [json.loads(l) for path in args.panel if os.path.exists(path)
            for l in open(path, encoding="utf-8")]
    if not rows:
        sys.exit("no panel answers found")

    out = os.path.abspath(os.path.expanduser(args.out))
    done = set()
    if os.path.exists(out):
        for line in open(out, encoding="utf-8"):
            try:
                r = json.loads(line)
                done.add((r["model"], r["qid"]))
            except Exception:
                continue

    # Blind label, stable per (question, model) but carrying no brand.
    order = sorted({r["model"] for r in rows})
    blind = {m: f"Reader {chr(65 + i)}" for i, m in enumerate(order)}
    print(f"  {len(rows)} answers, {len(order)} readers, judging blind")

    for r in sorted(rows, key=lambda x: (x["qid"], x["model"])):
        if (r["model"], r["qid"]) in done:
            print(f"  Q{r['qid']} {r['model']}: already judged")
            continue
        ctx = windows(lines, r["quotes_verified"])
        failed = len(r["quotes"]) - len(r["quotes_verified"])
        parts = [f"QUESTION: {r['question']}", "",
                 f"--- {blind[r['model']]}'s ANSWER ---", r["answer"], "",
                 f"Quotes it supplied: {len(r['quotes'])}. "
                 f"Confirmed present in the document: {len(r['quotes_verified'])}. "
                 f"Failed verification: {failed}.", ""]
        if r["quotes_verified"]:
            parts += ["--- ITS CONFIRMED QUOTES ---"] + \
                     [f'  "{q}"' for q in r["quotes_verified"]] + [""]
        if ctx:
            parts += ["--- DOCUMENT LINES AROUND THOSE QUOTES ---", ctx]
        started = time.time()
        try:
            content, why = ask(args.url, args.model, SYSTEM, "\n".join(parts),
                               args.max_tokens, args.timeout)
        except Exception as e:
            print(f"  Q{r['qid']} {r['model']}: {type(e).__name__}: {e}",
                  file=sys.stderr)
            continue
        m = re.search(r"\{.*\}", content, re.S)
        try:
            obj = json.loads(m.group(0)) if m else {}
        except json.JSONDecodeError:
            obj = {}
        rec = {"model": r["model"], "blind": blind[r["model"]], "qid": r["qid"],
               "specificity": obj.get("specificity"), "support": obj.get("support"),
               "candour": obj.get("candour"), "verdict": (obj.get("verdict") or "").strip(),
               "strongest": (obj.get("strongest") or "").strip(),
               "weakest": (obj.get("weakest") or "").strip(),
               "quotes_verified": len(r["quotes_verified"]),
               "quotes_total": len(r["quotes"]),
               "seconds": round(time.time() - started, 1), "finish_reason": why}
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        print(f"  Q{r['qid']} {r['model']:<22} spec={rec['specificity']} "
              f"sup={rec['support']} can={rec['candour']}  {rec['seconds']}s")

    judged = [json.loads(l) for l in open(out, encoding="utf-8")]
    num = lambda v: v if isinstance(v, (int, float)) else 0
    doc = ["# Panel reasoning, judged blind", "",
           f"Judge: `{args.model}`, run locally. It graded answers labelled "
           f"*Reader A–D*, never by model name; labels are restored below.", "",
           "## Scores", "",
           "| Reader | Q | Specificity | Support | Candour | Quotes verified |",
           "|---|---|---|---|---|---|"]
    for r in sorted(judged, key=lambda x: (x["qid"], x["model"])):
        doc.append(f"| {r['model']} | {r['qid']} | {r['specificity']} | "
                   f"{r['support']} | {r['candour']} | "
                   f"{r['quotes_verified']}/{r['quotes_total']} |")
    doc += ["", "## Averages", "",
            "| Reader | Specificity | Support | Candour | Answers |", "|---|---|---|---|---|"]
    for mdl in sorted({r["model"] for r in judged}):
        v = [r for r in judged if r["model"] == mdl]
        n = len(v)
        doc.append(f"| {mdl} | {sum(num(r['specificity']) for r in v)/n:.1f} | "
                   f"{sum(num(r['support']) for r in v)/n:.1f} | "
                   f"{sum(num(r['candour']) for r in v)/n:.1f} | {n} |")
    doc += ["", "## Per-answer verdicts", ""]
    for r in sorted(judged, key=lambda x: (x["qid"], x["model"])):
        doc += [f"### Q{r['qid']} — {r['model']}", "", r["verdict"] or "*none*", "",
                f"- **Strongest:** {r['strongest'] or '—'}",
                f"- **Weakest:** {r['weakest'] or '—'}", ""]
    open(os.path.expanduser(args.report), "w", encoding="utf-8").write("\n".join(doc))
    print(f"\nwrote {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
