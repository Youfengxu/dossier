#!/usr/bin/env python3
"""Show what retrieval actually returned, per obligation. No model calls.

A false `unmet` has two very different causes and the verdict alone cannot tell
them apart: the model read the right passages and judged wrong, or it never saw
them. On a 270-chunk document where 12 passages is 4.4% coverage, the second is
far more likely — and this makes it visible.

For each obligation it reports which named sections the retriever returned, and
flags the ones that reached no substantive section at all.

    ./diagnose-retrieval.py --project <dir> --doc arch-v5 --obligations obligations.yaml
    ./diagnose-retrieval.py --project <dir> --doc arch-v5 --coverage cov.csv --unmet-only

With --coverage it restricts to flagged rows, which is usually what you want:
the question is not "does retrieval work" but "did retrieval work for the
obligations we are about to raise findings on".
"""

import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from trace import chunk, load_obligations                 # noqa: E402
from llm import Embedder, cosine, load_doc                # noqa: E402

# A heading that names a real part of the document, as opposed to front matter,
# a table row, or a fragment the chunker picked up.
SUBSTANTIVE = re.compile(r"^\d+(\.\d+)?\s+\S|^Appendix\s+[A-Z]\b", re.I)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--doc", required=True)
    parser.add_argument("--obligations", default="obligations.yaml")
    parser.add_argument("--coverage", default=None,
                        help="restrict to obligations flagged in this run")
    parser.add_argument("--unmet-only", action="store_true")
    parser.add_argument("--scope", default=None)
    parser.add_argument("--passages", type=int, default=12)
    parser.add_argument("--embed-url",
                        default="http://192.168.100.21:8085/v1/embeddings")
    parser.add_argument("--embed-model", default="Qwen3-Embedding-8B-Q4_K_M")
    parser.add_argument("--show", type=int, default=14)
    args = parser.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    obligations = load_obligations(os.path.join(project, args.obligations))
    if args.scope:
        obligations = [o for o in obligations if o.get("scope") == args.scope]

    keep = None
    verdicts = {}
    if args.coverage:
        rows = list(csv.DictReader(open(os.path.join(project, args.coverage),
                                        encoding="utf-8")))
        wanted = ("unmet",) if args.unmet_only else \
                 ("unmet", "partial", "unverifiable")
        keep = {r["obligation"] for r in rows if r["verdict"] in wanted}
        verdicts = {r["obligation"]: r["verdict"] for r in rows}
        obligations = [o for o in obligations if o["id"] in keep]

    doc, lines = load_doc(project, args.doc)
    chunks = chunk(lines)
    embedder = Embedder(project, model=args.embed_model, url=args.embed_url)
    if not embedder.available():
        sys.exit("embedding endpoint unavailable")
    vectors = embedder.embed([c["text"] for c in chunks])

    substantive = sum(1 for c in chunks if SUBSTANTIVE.match(c["heading"].strip()))
    print(f"{args.doc}: {len(chunks)} chunks, {substantive} under a numbered "
          f"section heading")
    print(f"{len(obligations)} obligations, top-{args.passages} retrieval "
          f"= {100*args.passages/len(chunks):.1f}% of the document\n")

    blind, section_use = [], Counter()
    per_obligation = []
    for obligation in obligations:
        query = embedder.embed([obligation.get("text", "")])[0]
        ranked = sorted(((cosine(query, v), i) for i, v in enumerate(vectors)),
                        reverse=True)[:args.passages]
        headings = [chunks[i]["heading"].strip() for _, i in ranked]
        named = [h for h in headings if SUBSTANTIVE.match(h)]
        top = [re.match(r"^(\d+)", h).group(1) for h in named
               if re.match(r"^(\d+)", h)]
        section_use.update(set(top))
        per_obligation.append((obligation, named, top))
        if not named:
            blind.append(obligation)

    print(f"-- obligations whose retrieval reached NO numbered section "
          f"({len(blind)}) --")
    print("   These were judged on front matter and table fragments alone. A")
    print("   verdict of 'unmet' from here says nothing about the document.")
    for obligation in blind[:args.show]:
        print(f"  {obligation['id']:7} [{verdicts.get(obligation['id'],'-'):12}] "
              f"{obligation.get('text','')[:60]}")
    if len(blind) > args.show:
        print(f"  … {len(blind) - args.show} more")

    print(f"\n-- which sections retrieval draws on --")
    for section, count in sorted(section_use.items(),
                                 key=lambda x: -x[1])[:12]:
        bar = "#" * min(40, count)
        print(f"  section {section:>3}  {count:3}  {bar}")

    print(f"\n-- sample: sections reached per obligation --")
    for obligation, named, top in per_obligation[:args.show]:
        unique = sorted(set(top), key=int) if top else []
        print(f"  {obligation['id']:7} [{verdicts.get(obligation['id'],'-'):12}] "
              f"-> sections {', '.join(unique) if unique else 'NONE'}")
        print(f"          {obligation.get('text','')[:74]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
