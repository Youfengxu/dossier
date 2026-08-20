#!/usr/bin/env python3
"""Interactive querying of one frozen document, against two models at once.

    ./converse.py --project . --doc arch-v8-1 \
        --node "coder=Qwen3-Coder-Next-UD-Q4_K_M@http://192.168.100.148:8085/v1/chat/completions" \
        --node "qwen=qwen3.6-35b-a3b@http://localhost:8085/v1/chat/completions"

THE DOCUMENT IS THE SYSTEM MESSAGE AND NEVER CHANGES. That is the whole trick:
llama.cpp matches the longest common prefix in its slot, so the first turn pays
the full prefill and every later turn reuses it. Measured on this corpus: 111.5s,
then 12.0s, 7.2s, 16.0s. Put the document beside the question instead and the
prefix changes every turn, which is the difference between a conversation and a
batch job.

TWO NODES, ONE TURN. Each node keeps its own history and its own cache, and both
answer concurrently, so the wall clock is the slower of the two rather than the
sum. Two readings side by side is the point: on the panel run, the question where
three readers shared ZERO defined terms was the question whose answer turned out
to be unreliable. Agreement is weak evidence; disagreement is a flag you can act
on immediately instead of discovering later.

CITATION BY LOCATOR, NOT BY REPRODUCTION. Models asked to reproduce quotations
fabricated half of them here — 33 of 65 verbatim across four families, and
punctuation folding recovered none of the rest. So this asks for LINE RANGES and
renders the text from the frozen file itself. A model cannot fabricate a span it
does not write: the worst case is a wrong range, which is visible the moment the
rendered lines do not match the claim. That is how the hosted notebook tools get
high citation accuracy — they cite what they retrieved rather than what they
remember — and the corpus here is already built for it, with hash-pinned text and
stable slug:line locators.

/where is the other half. It counts literal occurrences with no model involved.
It exists because a synonym sweep run against the QUESTION's vocabulary reported
a concept absent that the document described under its own name; the fix is to be
able to ask the document what words it actually uses, mid-conversation.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from llm import load_doc                                       # noqa: E402

SYSTEM = """You are answering questions about one engineering document, given in
full below. Every line is numbered; the numbers are stable and are how you cite.

Reply with JSON only: {"answer": "...", "cites": ["4818-4830", "912-915"]}

  - "answer": at most 300 words. Specific. Name the document's own defined terms
    and mechanisms. Say plainly when the document does not address something, and
    say where you looked.
  - "cites": line ranges supporting your answer, from the numbers in the margin.
    Do NOT quote text — the ranges are rendered from the document itself. Three
    to six ranges, each under 20 lines.

Answer only from this document. If it does not say, say so.

=== DOCUMENT (line-numbered) ===
%s
=== END DOCUMENT ==="""

RANGE = re.compile(r"^\s*(\d+)\s*[-:]\s*(\d+)\s*$")


def ask(url, model, messages, max_tokens, timeout):
    payload = {"model": model, "temperature": 0, "max_tokens": max_tokens,
               "response_format": {"type": "json_object"},
               "presence_penalty": 0.0, "frequency_penalty": 0.0,
               "messages": messages}
    req = urllib.request.Request(
        url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as f:
        body = json.load(f)
    c = body["choices"][0]
    return (c["message"].get("content") or "").strip(), c.get("finish_reason") or ""


def parse(raw):
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        try:
            o = json.loads(m.group(0))
            return (o.get("answer") or "").strip(), \
                   [c for c in (o.get("cites") or []) if isinstance(c, str)]
        except json.JSONDecodeError:
            pass
    hit = re.search(r'"answer"\s*:\s*"((?:[^"\\]|\\.)*)"', raw, re.S)
    if hit:
        try:
            return json.loads('"' + hit.group(1) + '"'), []
        except Exception:
            pass
    return raw, []


def render(lines, cites, doc):
    """Turn claimed ranges into real document text. Bad ranges say so."""
    out = []
    for c in cites:
        m = RANGE.match(c)
        if not m:
            out.append((c, None, "not a line range"))
            continue
        a, b = int(m.group(1)), int(m.group(2))
        if a < 0 or b >= len(lines) or a > b:
            out.append((c, None, f"outside the document (0-{len(lines)-1})"))
            continue
        if b - a > 40:
            out.append((c, None, f"range too wide ({b-a} lines)"))
            continue
        out.append((f"{doc}:{a}-{b}", "\n".join(lines[a:b + 1]), None))
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", required=True)
    p.add_argument("--doc", required=True)
    p.add_argument("--node", action="append", required=True,
                   metavar="NAME=MODEL@URL")
    p.add_argument("--max-tokens", type=int, default=3000)
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--transcript", default="")
    p.add_argument("--seed-panel", action="append", default=[],
                   help="panel jsonl to show as prior context on start")
    args = p.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    _meta, lines = load_doc(project, args.doc)
    numbered = "\n".join(f"{i:>5} | {l}" for i, l in enumerate(lines))
    system = SYSTEM % numbered

    nodes = []
    for spec in args.node:
        name, rest = spec.split("=", 1)
        model, url = rest.rsplit("@", 1)
        nodes.append({"name": name.strip(), "model": model.strip(),
                      "url": url.strip(),
                      "messages": [{"role": "system", "content": system}]})

    print(f"\n  document {args.doc}: {len(lines):,} lines, "
          f"{len(system):,} chars of prefix")
    for n in nodes:
        print(f"  node {n['name']:<10} {n['model']}")
    for path in args.seed_panel:
        if os.path.exists(path):
            k = sum(1 for _ in open(path))
            print(f"  prior answers available in {os.path.basename(path)}: {k}")
    print("\n  /where <term>     count literal occurrences (no model)"
          "\n  /show a-b         print document lines"
          "\n  /quit             end\n")

    transcript = []
    while True:
        try:
            q = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            continue
        if q in ("/quit", "/exit"):
            break
        if q.startswith("/where "):
            term = q[7:].strip()
            rx = re.compile(re.escape(term), re.I)
            hits = [i for i, l in enumerate(lines) if rx.search(l)]
            print(f"    '{term}': {len(hits)} occurrence(s)"
                  + (f" — lines {', '.join(str(h) for h in hits[:12])}"
                     + (" ..." if len(hits) > 12 else "") if hits else
                     "  <- a LEAD, not a finding; try the document's own wording"))
            continue
        if q.startswith("/show "):
            m = RANGE.match(q[6:])
            if not m:
                print("    usage: /show 4818-4830")
                continue
            a, b = int(m.group(1)), min(int(m.group(2)), len(lines) - 1)
            for i in range(a, b + 1):
                print(f"    {i:>5} | {lines[i]}")
            continue

        for n in nodes:
            n["messages"].append({"role": "user", "content": q})

        def run(n):
            t = time.time()
            try:
                raw, why = ask(n["url"], n["model"], n["messages"],
                               args.max_tokens, args.timeout)
            except Exception as e:
                return n, None, [], f"{type(e).__name__}: {e}", time.time() - t
            a, c = parse(raw)
            n["messages"].append({"role": "assistant", "content": raw})
            return n, a, c, None, time.time() - t

        with ThreadPoolExecutor(max_workers=len(nodes)) as pool:
            results = list(pool.map(run, nodes))

        answers = {}
        for n, answer, cites, err, secs in results:
            print(f"\n  ── {n['name']} ({secs:.1f}s) "
                  + "─" * max(4, 46 - len(n['name'])))
            if err:
                print(f"    ERROR {err}")
                continue
            answers[n["name"]] = answer
            print("   ", (answer or "(empty)").replace("\n", "\n    "))
            shown = render(lines, cites, args.doc)
            if shown:
                print("\n    Cited, rendered from the frozen document:")
            for loc, text, problem in shown:
                if problem:
                    print(f"      [{loc}] REJECTED — {problem}")
                else:
                    first = text.split("\n")[0][:96]
                    print(f"      [{loc}] {first}"
                          + (" ..." if len(text.split("\n")) > 1 else ""))
        if len(answers) > 1:
            terms = {k: set(re.findall(r"\b(?:[A-Z][a-z]+)(?:\s+[A-Z][a-z]+){1,3}\b", v))
                     for k, v in answers.items()}
            shared = set.intersection(*terms.values()) if terms else set()
            only = {k: v - shared for k, v in terms.items()}
            print(f"\n  ── agreement " + "─" * 42)
            print(f"    shared defined terms: {len(shared)}"
                  + (f" — {', '.join(sorted(shared)[:6])}" if shared else
                     "   <- ZERO overlap; treat this answer as unreliable"))
            for k, v in only.items():
                if v:
                    print(f"    only {k}: {', '.join(sorted(v)[:5])}")
        transcript.append({"q": q, "answers": answers})
        print()

    if args.transcript and transcript:
        path = os.path.expanduser(args.transcript)
        json.dump(transcript, open(path, "w"), indent=1)
        print(f"  transcript -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
