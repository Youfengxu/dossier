#!/usr/bin/env python3
"""Put one whole document in front of several models and ask the same questions.

    ./panel.py --project . --doc arch-v8-1 --questions q.txt \
        --model gemma-4-31b-it-qat-ud-q4-k-xl \
        --url http://localhost:8085/v1/chat/completions --out panel.jsonl

WHY THE WHOLE DOCUMENT AND NOT RETRIEVAL. Retrieval answers a question with the
passages a scorer liked. On this corpus that failed in a way worth remembering: a
reference to section 13 resolved to the table of contents, and the detail the
question was actually about was sitting in a table further down. When the
document fits the context window, the honest thing is to let the model read it.

WHY THE DOCUMENT GOES IN THE SYSTEM MESSAGE. It never varies, so it forms a
stable token prefix across every question, and llama.cpp's slot cache matches the
longest common prefix. The first question pays the full prefill; the rest reuse
it. Put the document in the user message beside the question and that saving
disappears, because the prefix now changes every call.

That is also why this runs one question at a time against one slot. Concurrency
would give each request a different slot and re-prefill the document per slot.

EVERY ANSWER IS WRITTEN THE MOMENT IT ARRIVES, and a rerun skips pairs already on
disk. That is the whole recovery story: any failure -- eviction, restart, OOM,
network -- becomes "run it again", and it resumes rather than restarts. A run
that has to complete in one uninterrupted pass will eventually not complete.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from llm import load_doc                                       # noqa: E402

SYSTEM = """You are reading one engineering document in full. Answer the question
using only what the document says.

Reply with JSON only: {"quotes": ["...", "..."], "answer": "..."}

  - "quotes": three to six SHORT verbatim spans copied EXACTLY from the document,
    each under 40 words, each supporting a claim in your answer. Copy character
    for character; do not paraphrase, join fragments, or tidy punctuation. A
    quote that is not in the document is worse than no quote. Write these FIRST.
  - "answer": AT MOST 350 WORDS. Specific, not long: name the sections, defined
    terms and mechanisms the document actually uses. Where the document is
    silent or inconsistent, say so plainly and say where you looked. Do not
    restate the question, do not summarise the document, do not pad.

Do not speculate about what the system probably does. The question is about this
document.

=== DOCUMENT ===
%s
=== END DOCUMENT ==="""

# A reasoning model spends the budget before it writes anything: gpt-oss-120b and
# Glimmer both return a populated reasoning_content beside an empty content when
# max_tokens is too small. That is not a refusal and not an error, it is a budget
# problem, so it is retried with a bigger one rather than recorded as a failure.
ESCALATION = 3


def questions_from(path):
    """Split a numbered list, keeping questions that wrap across lines."""
    text = open(path, encoding="utf-8").read()
    parts = re.split(r"^\s*(\d+)[.)]\s+", text, flags=re.M)
    out = []
    for i in range(1, len(parts), 2):
        body = " ".join(parts[i + 1].split())
        if body:
            out.append((parts[i], body))
    if not out:                                   # not numbered: one per line
        out = [(str(n), ln.strip()) for n, ln in enumerate(text.splitlines(), 1)
               if ln.strip()]
    return out


def exact_tokens(chat_url, model, text):
    """Ask the server to count. A chars/token guess is not good enough when the
    answer decides whether the run starts: at 4 chars/token this document
    estimates 127k against a real 105k, which is the difference between running
    and a false refusal on a 131k endpoint."""
    root = chat_url.split("/v1/")[0]
    for url, body in ((root + "/tokenize", {"model": model, "prompt": text}),
                      (root + "/tokenize", {"model": model, "content": text})):
        try:
            req = urllib.request.Request(
                url, json.dumps(body).encode(), {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=300) as f:
                out = json.load(f)
            n = out.get("count") or len(out.get("tokens") or [])
            if n:
                return int(n)
        except Exception:
            continue
    return None


def endpoint_limit(chat_url, model):
    """Ask the server how much context it will actually accept."""
    base = chat_url.split("/chat/completions")[0]
    try:
        with urllib.request.urlopen(base + "/models", timeout=15) as f:
            for m in json.load(f).get("data", []):
                if m.get("id", "").endswith(model) or m.get("id") == model:
                    for k in ("max_model_len", "context_length", "max_context"):
                        if m.get(k):
                            return int(m[k])
    except Exception:
        pass
    return None


def ask(url, model, system, user, max_tokens, timeout):
    payload = {"model": model, "temperature": 0, "max_tokens": max_tokens,
               "response_format": {"type": "json_object"},
               # Inherited server-side penalties wreck structured output: every
               # JSON object repeats the same keys, so penalising seen tokens
               # pushes the model off "quotes" and "answer" exactly when it needs
               # them again. The Mac serves gemma with --presence-penalty 1.5 and
               # this run produced 63,961 characters of degenerate JSON for a
               # question bounded at 350 words. llm.py has always overridden
               # these; sending them is not optional.
               "presence_penalty": 0.0,
               "frequency_penalty": 0.0,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}]}
    req = urllib.request.Request(
        url, json.dumps(payload).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as f:
        body = json.load(f)
    choice = body["choices"][0]
    msg = choice["message"]
    return ((msg.get("content") or "").strip(),
            (msg.get("reasoning_content") or "").strip(),
            choice.get("finish_reason") or "")


def parse(raw):
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {"answer": raw, "quotes": [], "unparsed": True}
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"answer": raw, "quotes": [], "unparsed": True}
    return {"answer": (obj.get("answer") or "").strip(),
            "quotes": [q for q in (obj.get("quotes") or []) if isinstance(q, str)],
            "unparsed": False}


def flat(s):
    return " ".join(s.split()).lower()


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", required=True)
    p.add_argument("--doc", required=True, help="slug in parsed/")
    p.add_argument("--questions", required=True)
    p.add_argument("--model", required=True)
    p.add_argument("--url", required=True)
    p.add_argument("--out", required=True, help="JSONL, appended and resumed")
    p.add_argument("--max-tokens", type=int, default=10000)
    p.add_argument("--timeout", type=int, default=2400)
    # Escalation has a ceiling because the reply shares the window with the
    # document. arch-v8-1 is ~105k tokens in a 131k context, so a reply budget
    # over ~26k does not overflow gracefully -- it 400s or is silently cut.
    p.add_argument("--max-tokens-cap", type=int, default=20000)
    p.add_argument("--label", default="", help="name for this panellist")
    args = p.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    _sha, lines = load_doc(project, args.doc)
    document = "\n".join(lines)
    system = SYSTEM % document
    label = args.label or args.model

    counted = exact_tokens(args.url, args.model, system)
    approx = counted if counted else int(len(system) / 4.5)
    limit = endpoint_limit(args.url, args.model)
    how = "counted by the server" if counted else "estimated at 4.5 chars/token"
    print(f"  document {args.doc}: {len(document):,} chars, "
          f"{approx:,} prompt tokens ({how})")
    print(f"  endpoint limit: {limit if limit else 'not advertised'}")
    if limit and approx + args.max_tokens > limit:
        sys.exit(f"REFUSING: ~{approx:,} prompt + {args.max_tokens} reply exceeds "
                 f"the endpoint's {limit:,}. Raise the server's context first.")

    out = os.path.abspath(os.path.expanduser(args.out))
    done = set()
    if os.path.exists(out):
        for line in open(out, encoding="utf-8"):
            try:
                r = json.loads(line)
                done.add((r["model"], r["qid"]))
            except Exception:
                continue
    questions = questions_from(args.questions)
    print(f"  {len(questions)} question(s); {len(done)} already answered\n")

    body = flat(document)
    for qid, question in questions:
        if (label, qid) in done:
            print(f"  Q{qid}: already answered, skipping")
            continue
        budget, started = args.max_tokens, time.time()
        for attempt in range(1, 4):
            try:
                content, reasoning, why = ask(args.url, args.model, system,
                                              question, budget, args.timeout)
            except urllib.error.HTTPError as e:
                detail = e.read()[:200].decode("utf-8", "replace")
                print(f"  Q{qid}: HTTP {e.code} {detail}", file=sys.stderr)
                sys.exit(2)
            except Exception as e:
                print(f"  Q{qid}: {type(e).__name__}: {e}", file=sys.stderr)
                if attempt == 3:
                    sys.exit(3)
                time.sleep(20 * attempt)
                continue
            if content and why != "length":
                break
            reason = ("empty content, "
                      f"{len(reasoning)} chars of reasoning" if not content
                      else f"truncated at the token limit ({len(content):,} chars)")
            raised = min(budget * ESCALATION, args.max_tokens_cap)
            if raised == budget:
                print(f"  Q{qid}: {reason}; already at the "
                      f"{budget} cap", file=sys.stderr)
                break
            print(f"  Q{qid}: {reason}; raising max_tokens "
                  f"{budget} -> {raised}", file=sys.stderr)
            budget = raised
        else:
            sys.exit(4)

        obj = parse(content)
        good = [q for q in obj["quotes"] if flat(q) and flat(q) in body]
        bad = [q for q in obj["quotes"] if q not in good]
        record = {"model": label, "qid": qid, "question": question,
                  "answer": obj["answer"], "quotes": obj["quotes"],
                  "quotes_verified": good, "quotes_unverified": bad,
                  "seconds": round(time.time() - started, 1),
                  "max_tokens": budget, "finish_reason": why,
                  "unparsed": obj.get("unparsed", False)}
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        warn = "  NOT JSON — no quotes to verify" if obj.get("unparsed") else ""
        print(f"  Q{qid}: {len(obj['answer']):,} chars, "
              f"quotes {len(good)}/{len(obj['quotes'])} verbatim, "
              f"{record['seconds']}s{warn}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
