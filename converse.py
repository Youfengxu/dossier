#!/usr/bin/env python3
"""Interactive querying of one frozen document, against two models at once.

    ./converse.py --project . --doc deliverable-v2 \
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
punctuation folding recovered none of the rest. So this asks for LINE MARKERS and
renders the text from the frozen file itself. A model cannot fabricate a span it
does not write: the worst case is a wrong marker, visible the moment the rendered
lines do not match the claim. That is how the hosted notebook tools reach high
citation accuracy — they cite what they retrieved rather than what they remember.

MARK SPARSELY. Numbering every line of a 6,000-line document costs 35,653 tokens,
36% of the prompt, and pushes it past a 131k window. A marker every ten lines
costs 3,518 — a tenth of that — and the model cites the marker at or above what
it means, so the block it heads renders instead of a single line. Compacting the
format is the obvious idea and barely helps (127k, still over): the cost is
having a number on every line at all, not how it is padded. Sparse marking is
what makes a second reader possible on a 36 GB machine, so it is not a tuning
knob, it is the difference between one opinion and two.

A LOCATOR IS NOT A WAY TO FIND ANYTHING. This was got wrong first time round.
`deliverable-v2:4880` proves a passage exists; it appears nowhere in the .docx a
reviewer is actually reading, because Word shows no line numbers and the parse
flattens tables. Nor can a page number stand in — a .docx contains no pagination
at all, since Word computes it at render time from fonts and margins. So every
citation prints three things: the locator for verification, the nearest heading
for the navigation pane, and a phrase to search for. They answer different
questions and conflating them helps nobody.

The search phrase comes from parsed text, so a passage inside a table may be
joined differently than Word displays it. If a search fails, shorten it before
concluding the citation is wrong.

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
  - "cites": locators supporting your answer. __HOWTO__ Do NOT quote text — the
    document renders itself from what you cite. Three to six of them.

Answer only from this document. If it does not say, say so.

=== DOCUMENT (line-numbered) ===
%s
=== END DOCUMENT ==="""

RANGE = re.compile(r"^\s*(\d+)\s*(?:[-:\u2013\u2014]\s*(\d+))?\s*$")

# A line number in parsed text appears NOWHERE in the .docx the reviewer is
# reading. Word shows no line numbers, the parse flattens tables, and a page
# number does not exist in the file at all — Word computes pagination at render
# time. So a locator proves the model did not invent the passage, and does
# nothing to help anyone find it. Every citation therefore also carries the
# nearest heading above it and a phrase to search for.
HEADING = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?\s+)?[A-Z][^.!?]{2,78}$")


def nearest_heading(lines, n):
    """The closest plausible heading at or above line n, for navigation."""
    for i in range(min(n, len(lines) - 1), max(-1, n - 400), -1):
        line = lines[i].strip()
        if not line or len(line) > 80:
            continue
        if HEADING.match(line) and not line.endswith((",", ";", ":")):
            words = line.split()
            if 1 < len(words) < 14:
                return line, i
    return None, None


def searchable(text, words=9):
    """A phrase distinctive enough to Ctrl-F in the source document."""
    flat = " ".join(text.split())
    picked = flat.split(" ")[:words]
    return " ".join(picked).strip(" ,;:.")


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


def render(lines, cites, doc, every=1):
    """Turn claimed locators into real document text. Bad ones say so.

    A bare marker cites the block it heads, so it renders that whole block
    rather than the single line the number sits on."""
    out = []
    for c in cites:
        m = RANGE.match(c)
        if not m:
            out.append((c, None, "not a line locator"))
            continue
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else min(a + max(every - 1, 0),
                                                   len(lines) - 1)
        if a < 0 or b >= len(lines) or a > b:
            out.append((c, None, f"outside the document (0-{len(lines)-1})"))
            continue
        if b - a > max(40, every * 4):
            out.append((c, None, f"range too wide ({b-a} lines)"))
            continue
        out.append((f"{doc}:{a}-{b}", "\n".join(lines[a:b + 1]), None))
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", required=True,
                   help="review directory holding parsed/MANIFEST.json")
    p.add_argument("--doc", required=True,
                   help="slug of the frozen document to load, e.g. deliverable-v2")
    p.add_argument("--node", action="append", required=True,
                   metavar="NAME=MODEL@URL",
                   help="repeatable. Each node keeps its own history and cache "
                        "and answers concurrently; give two to see where they "
                        "disagree, which is the only thing agreement cannot "
                        "tell you")
    p.add_argument("--mark-every", type=int, default=10,
                   help="line-number marker interval; 0 numbers every line. "
                        "Numbering every line of a 6,000-line document costs "
                        "35,653 tokens (36%% of the prompt) and pushes it past a "
                        "131k window; a marker every 10 lines costs 3,518 and "
                        "fits, which is the difference between one reader and two")
    p.add_argument("--max-tokens", type=int, default=3000,
                   help="reply budget. Reasoning models spend it before they "
                        "answer, so raise it for those rather than reading an "
                        "empty reply as a refusal")
    p.add_argument("--timeout", type=int, default=1800,
                   help="seconds per request; the first turn prefills the whole "
                        "document and is minutes, not seconds")
    p.add_argument("--transcript", default="",
                   help="write the session here as JSON on /quit")
    p.add_argument("--seed-panel", action="append", default=[],
                   help="panel.py jsonl whose answer count is reported at start, "
                        "so a follow-up session knows what has already been asked")
    args = p.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    _meta, lines = load_doc(project, args.doc)
    if args.mark_every and args.mark_every > 1:
        every = args.mark_every
        body = "\n".join((f"[{i}]\n{l}" if i % every == 0 else l)
                         for i, l in enumerate(lines))
        howto = (f"Every {every}th line carries a marker like [120]. Cite the "
                 f"marker at or before the text you mean; the {every} lines it "
                 f"heads are rendered from the document for the reader.")
    else:
        every = 1
        body = "\n".join(f"{i:>5} | {l}" for i, l in enumerate(lines))
        howto = "Every line is numbered in the margin. Cite those numbers."
    system = (SYSTEM % body).replace("__HOWTO__", howto)

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
            a = int(m.group(1))
            b = min(int(m.group(2)) if m.group(2) else a, len(lines) - 1)
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
            shown = render(lines, cites, args.doc, every)
            if shown:
                print("\n    Cited, rendered from the frozen document:")
            for loc, text, problem in shown:
                if problem:
                    print(f"      [{loc}] REJECTED — {problem}")
                    continue
                start = int(loc.rsplit(":", 1)[1].split("-")[0])
                head, hline = nearest_heading(lines, start)
                print(f"      [{loc}] {text.split(chr(10))[0][:92]}")
                if head:
                    print(f"          under: {head}")
                print(f"          find it: search for \"{searchable(text)}\"")
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
