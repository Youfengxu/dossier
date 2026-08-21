#!/usr/bin/env python3
"""Recover quotes from panel replies whose JSON did not quite parse.

    ./repair-panel.py --project . --doc arch-v8-1 out/panel-qwen.jsonl

`response_format: json_object` is a constraint on some servers and a suggestion
on others. One reply here came back as valid JSON right up to character 1058,
where the model wrote a quotation mark inside a string without escaping it --
and a strict parser throws the whole reply away for it, quotes included.

That is a waste, because the reply is already paid for and the evidence is
sitting in the text. This walks the raw string, pulls out whatever complete
quoted spans it can find, and re-checks each against the frozen document. A
fragment that survives verification is as good as one that arrived in clean
JSON; one that does not is discarded exactly as before. Nothing is trusted
because it was recovered -- the corpus still decides.
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
from llm import load_doc                                       # noqa: E402

STRING = re.compile(r'"((?:[^"\\]|\\.)*)"', re.S)


def unescape(s):
    try:
        return json.loads('"' + s + '"')
    except Exception:
        return s


def salvage(raw):
    quotes, answer = [], ""
    block = re.search(r'"quotes"\s*:\s*\[(.*?)\](?=\s*,\s*"answer")', raw, re.S) \
        or re.search(r'"quotes"\s*:\s*\[(.*)', raw, re.S)
    if block:
        quotes = [unescape(m.group(1)) for m in STRING.finditer(block.group(1))]
    hit = re.search(r'"answer"\s*:\s*"((?:[^"\\]|\\.)*)"', raw, re.S)
    if hit:
        answer = unescape(hit.group(1))
    return [q for q in quotes if q.strip()], answer.strip()


def flat(s):
    return " ".join(s.split()).lower()


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", required=True)
    p.add_argument("--doc", required=True)
    p.add_argument("files", nargs="+")
    args = p.parse_args()

    _meta, lines = load_doc(os.path.abspath(os.path.expanduser(args.project)),
                            args.doc)
    body = flat("\n".join(lines))

    for path in args.files:
        if not os.path.exists(path):
            print(f"  {path}: missing")
            continue
        rows = [json.loads(l) for l in open(path, encoding="utf-8")]
        changed = 0
        for r in rows:
            if not r.get("unparsed"):
                continue
            quotes, answer = salvage(r["answer"])
            if not quotes and not answer:
                print(f"  {os.path.basename(path)} Q{r['qid']}: nothing recoverable")
                continue
            good = [q for q in quotes if flat(q) and flat(q) in body]
            bad = [q for q in quotes if q not in good]
            r["answer"] = answer or r["answer"]
            r["quotes"], r["quotes_verified"], r["quotes_unverified"] = \
                quotes, good, bad
            r["repaired"] = True
            r["unparsed"] = False
            changed += 1
            print(f"  {os.path.basename(path)} Q{r['qid']}: recovered "
                  f"{len(quotes)} quote(s), {len(good)} verbatim; "
                  f"answer {len(r['answer']):,} chars")
        if changed:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r) + "\n")
            os.replace(tmp, path)
            print(f"  {path}: {changed} record(s) repaired")
    return 0


if __name__ == "__main__":
    sys.exit(main())
