#!/usr/bin/env python3
"""Measure RETRIEVAL against a human-built answer matrix.

Every precision and recall figure elsewhere in this project is end to end, which
means a retrieval miss and a judgement error look identical. They are not: one
is fixed by changing how passages are selected, the other by changing what the
model is asked. CM-1 separates them, because its answer matrix says exactly
which design elements a human tracer considered relevant to each requirement.

The question asked here is narrow and answerable: **for each requirement, do the
top-k retrieved passages contain the ones a human linked?**

    ./score-retrieval.py                       # embedding retrieval, k = 4,6,12,24
    ./score-retrieval.py --retrieval terms
    ./score-retrieval.py --k 12

recall@k is the number that matters. If recall@12 is high, a wrong verdict is
the model's fault. If it is low, no amount of prompt work will help — the
evidence never reached the model.
"""

import argparse
import json
import urllib.request
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..")))
from trace import chunk, retrieve, terms                    # noqa: E402
import llm                                                   # noqa: E402
from llm import Embedder, cosine, load_doc                  # noqa: E402
from collections import Counter                             # noqa: E402


def load_answers(path):
    gold, unlinked = {}, []
    section = None
    for raw in open(path, encoding="utf-8"):
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.startswith("gold:"):
            section = "gold"
            continue
        if line.startswith("unlinked:"):
            unlinked = [x.strip() for x in
                        line.split("[", 1)[1].rstrip("]").split(",") if x.strip()]
            section = None
            continue
        if section == "gold" and ":" in line:
            key, value = line.strip().split(":", 1)
            items = [x.strip() for x in
                     value.strip().strip("[]").split(",") if x.strip()]
            gold[key.strip()] = items
    return gold, unlinked


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", default=HERE)
    parser.add_argument("--doc", default="design")
    parser.add_argument("--requirements", default="requirements")
    parser.add_argument("--retrieval", choices=("embed", "terms", "hybrid", "expand", "rerank"), default="embed")
    parser.add_argument("--rerank-model", default="bge-reranker-v2-m3")
    parser.add_argument("--rerank-url",
                        default=None)
    parser.add_argument("--rerank-depth", type=int, default=40,
                        help="candidates to score jointly before taking top-k")
    parser.add_argument("--model")
    parser.add_argument("--url")
    parser.add_argument("--embed-url",
                        default=llm.DEFAULT_EMBED_URL)
    parser.add_argument("--embed-model", default="Qwen3-Embedding-8B-Q4_K_M")
    parser.add_argument("--k", type=int, nargs="*", default=[4, 6, 12, 24])
    args = parser.parse_args()

    project = os.path.abspath(args.project)
    gold, unlinked = load_answers(os.path.join(project, "answers.yaml"))

    _, design_lines = load_doc(project, args.doc)
    chunks = chunk(design_lines)
    # Each chunk begins at a "## <artifact id>" heading, so a chunk maps to one
    # artefact and a retrieval hit can be scored directly against the matrix.
    ids = [c["heading"].strip() for c in chunks]

    _, req_lines = load_doc(project, args.requirements)
    requirements, current = {}, None
    for line in req_lines:
        if line.startswith("## "):
            current = line[3:].strip()
            requirements[current] = ""
        elif current and line.strip():
            requirements[current] += " " + line.strip()

    scored = {r: g for r, g in gold.items() if g and r in requirements}
    print(f"CM-1 retrieval: {len(scored)} requirements with gold links, "
          f"{len(chunks)} design chunks, {args.retrieval}\n")

    def rrf(*orders, kappa=60):
        """Reciprocal rank fusion.

        Combines rankings without needing their scores to be comparable, which
        matters because a cosine and an IDF sum are not on the same scale. Each
        list contributes 1/(kappa + rank); kappa=60 is the value from the
        original Cormack et al. formulation and is not tuned here — tuning it
        against a 19-requirement answer key would fit noise.
        """
        score = {}
        for order in orders:
            for rank, index in enumerate(order):
                score[index] = score.get(index, 0.0) + 1.0 / (kappa + rank)
        return [i for i, _ in sorted(score.items(), key=lambda x: -x[1])]

    # Generated query expansion. A requirement and the passage that satisfies it
    # often share no vocabulary — "tidal surge overtopping of coastal defences"
    # against "coastal overtopping of sea defences" — and a bi-encoder compares
    # the two texts as written. Paraphrasing the requirement first and fusing
    # the rankings gives the retriever several chances to land on the wording
    # the document actually used.
    EXPAND_SYSTEM = """Rewrite one requirement three different ways.

Reply with JSON only: {"paraphrases":["...","...","..."]}

Each must state the same obligation in DIFFERENT vocabulary — the words an
engineer might have used had they written it independently. Change the nouns
and verbs, not the meaning. Do not add requirements, do not explain, do not
hedge. Keep each under 30 words."""

    def expansions(client, text):
        def check(obj):
            if not isinstance(obj.get("paraphrases"), list) or \
                    not obj["paraphrases"]:
                return "'paraphrases' must be a non-empty array"
            return None
        try:
            return client.ask(EXPAND_SYSTEM, text, validate=check,
                              label="expand")["paraphrases"][:3]
        except Exception:                                   # noqa: BLE001
            return []

    def rerank(query, order, chunk_texts, depth):
        """Cross-encoder rerank of the top `depth` candidates.

        A bi-encoder embeds query and passage independently and compares the
        two vectors, so it can only see similarity that survived compression
        into a single point each. A cross-encoder reads the pair together and
        scores it, which is why it handles paraphrase: on the case that broke a
        coverage run, "coastal overtopping of sea defences" against a
        requirement about "tidal surge overtopping of coastal defences" scores
        +4.45 where unrelated text scores -10.6.

        Retrieval stays cheap and wide; only the shortlist is scored jointly.
        """
        head = order[:depth]
        payload = json.dumps({"model": args.rerank_model, "query": query,
                              "documents": [chunk_texts[i] for i in head]})
        request = urllib.request.Request(
            args.rerank_url, data=payload.encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=300) as response:
            body = json.load(response)
        scored = sorted(body["results"], key=lambda r: -r["relevance_score"])
        return [head[r["index"]] for r in scored] + order[depth:]

    embedder = vectors = None
    if args.retrieval in ("embed", "hybrid", "expand", "rerank"):
        embedder = Embedder(project, model=args.embed_model, url=args.embed_url)
        if not embedder.available():
            sys.exit("embedding endpoint unavailable")
        vectors = embedder.embed([c["text"] for c in chunks])
    idf = None
    if args.retrieval in ("terms", "hybrid"):
        freq = Counter()
        for item in chunks:
            freq.update(set(terms(item["text"])))
        idf = {w: (len(chunks) / (1 + n)) ** 0.5 for w, n in freq.items()}

    expander = None
    if args.retrieval == "expand":
        from llm import Client
        expander = Client(project, model=args.model, url=args.url,
                          prompt_version="expand-1", max_tokens=400, quiet=True)

    ranked_cache = {}
    for requirement, want in scored.items():
        text = requirements[requirement]
        dense = lexical = None
        if embedder:
            query = embedder.embed([text])[0]
            dense = [i for _, i in sorted(
                ((cosine(query, v), i) for i, v in enumerate(vectors)), reverse=True)]
        if idf is not None:
            hits = retrieve(text, chunks, idf, k=len(chunks))
            index = {(c["start"], c["end"]): i for i, c in enumerate(chunks)}
            lexical = [index[(h["start"], h["end"])] for h in hits]
            # retrieve() drops zero-score chunks, so append the rest to keep
            # the ranking a full permutation; fusion needs comparable lengths.
            lexical += [i for i in range(len(chunks)) if i not in set(lexical)]
        if args.retrieval == "rerank":
            query = embedder.embed([text])[0]
            order = [i for _, i in sorted(
                ((cosine(query, v), i) for i, v in enumerate(vectors)),
                reverse=True)]
            order = rerank(text, order, [c["text"] for c in chunks],
                           args.rerank_depth)
        elif args.retrieval == "expand":
            variants = [text] + expansions(expander, text)
            orders = []
            for variant in variants:
                query = embedder.embed([variant])[0]
                orders.append([i for _, i in sorted(
                    ((cosine(query, v), i) for i, v in enumerate(vectors)),
                    reverse=True)])
            order = rrf(*orders)
        elif args.retrieval == "hybrid":
            order = rrf(dense, lexical)
        elif embedder:
            order = dense
        else:
            order = lexical
        ranked_cache[requirement] = order

    print(f"{'k':>4}  {'recall@k':>9}  {'reqs fully covered':>19}  "
          f"{'precision@k':>12}")
    for k in args.k:
        found = total = hit_slots = 0
        full = 0
        for requirement, want in scored.items():
            top = {ids[i] for i in ranked_cache[requirement][:k]}
            got = len(top & set(want))
            found += got
            total += len(want)
            hit_slots += got
            full += (got == len(want))
        recall = found / total if total else 0
        precision = hit_slots / (k * len(scored)) if scored else 0
        print(f"{k:>4}  {recall*100:8.1f}%  {full:>10}/{len(scored):<8}  "
              f"{precision*100:11.1f}%")

    print(f"\n{len(unlinked)} requirements have no gold link at all "
          f"({', '.join(unlinked)}).")
    print("Those are the only ones a coverage run should call unmet; anything "
          "else it\ncalls unmet is either a retrieval miss or a judgement error, "
          "and recall@k\nabove tells you which.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
