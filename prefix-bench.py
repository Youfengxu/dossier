#!/usr/bin/env python3
"""Does this endpoint reuse the prompt prefix? Measure it, do not assume it.

    ./prefix-bench.py --project fixtures/ntsb --doc claims \
        --model qwen3.6-35b-a3b --url http://localhost:8085/v1/chat/completions

WHY THIS EXISTS. `converse.py` is usable only because llama.cpp matches the longest
common prefix in its slot: the document goes in the system message and never
changes, so the first turn pays the full prefill and every later turn does not.

Measured on `fixtures/ntsb` (78,026 chars of prefix), which anyone with this
repository can reproduce:

    mac,  qwen3.6-35b-a3b            43.7s, then 0.5s, 0.2s, 0.4s   -> 105x
    gx10, Qwen3-Coder-Next-Q4_K_M    14.6s, then 0.7s, 0.3s, 0.9s   ->  19x

That property belongs to the SERVER, not to the model, and it is not portable. A
hosted endpoint may not do it at all. A model with mixed sliding-window and global
attention — Laguna S 2.1, for instance — may reuse slots differently. Adopting a
new model for interactive work without measuring this risks turning every turn back
into a full prefill, which is the difference between a conversation and a batch job.

WHAT IT MEASURES. Same system message, different questions, sequentially. If the
second and later turns are much faster than the first, the prefix is being reused.
The ratio is the number that matters; absolute times are hardware.

It sends only the document named by --doc. Point it at a public fixture unless the
endpoint is one you are allowed to send the document to.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import llm                                                      # noqa: E402
from llm import load_doc                                        # noqa: E402

QUESTIONS = [
    "In one sentence, what kind of document is this?",
    "Name one topic the document covers. One sentence.",
    "Does the document contain any dates? Answer yes or no.",
    "In one sentence, who is the intended reader?",
]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--doc", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--turns", type=int, default=4)
    parser.add_argument("--max-tokens", type=int, default=64)
    args = parser.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    _meta, lines = load_doc(project, args.doc)
    document = "\n".join(lines)
    system = ("You are answering questions about the document below. Answer only "
              "from it, in one short sentence.\n\n" + document)

    print(f"  {args.doc}: {len(lines):,} lines, {len(document):,} chars of prefix")
    print(f"  {args.model} at {args.url}")
    print("  " + "-" * 66)

    timings = []
    for turn in range(min(args.turns, len(QUESTIONS))):
        started = time.time()
        try:
            content, _reasoning, stop = llm.chat(
                args.url, args.model, system=system, user=QUESTIONS[turn],
                max_tokens=args.max_tokens, json_object=False, timeout=600)
        except Exception as exc:
            print(f"  turn {turn + 1}: FAILED — {type(exc).__name__}: {exc}")
            return 2
        elapsed = time.time() - started
        timings.append(elapsed)
        print(f"  turn {turn + 1}: {elapsed:7.1f}s   {len(content):>4} chars"
              f"   stop={stop!r}")

    print("  " + "-" * 66)
    first, rest = timings[0], timings[1:]
    if not rest:
        print("  one turn only — nothing to compare")
        return 0
    typical = sorted(rest)[len(rest) // 2]
    ratio = first / typical if typical else float("inf")
    print(f"  first turn {first:.1f}s, median of the rest {typical:.1f}s "
          f"-> {ratio:.1f}x")
    # A server that re-prefills every turn shows no meaningful drop. The 2x line
    # is deliberately generous: the point is to catch "no reuse at all", not to
    # grade a cache.
    if ratio >= 2.0:
        print("  PREFIX IS REUSED — later turns skip the prefill. Interactive use "
              "is viable\n  on this endpoint.")
    else:
        print("  NO MEANINGFUL REUSE — every turn re-pays the prefill. converse.py "
              "will be\n  as slow on turn ten as on turn one; do not adopt this "
              "endpoint for it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
