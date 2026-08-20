#!/usr/bin/env python3
"""Group flagged obligations into candidate findings, ranked by convergence.

A coverage run hands a reviewer fifty flat rows of unknown quality and no order
to read them in. That is the actual cost of poor precision: not the wrong
verdicts themselves, but the triage burden they impose.

The one signal that measurably works is CONVERGENCE. When six obligations
independently report the same gap, that gap is real far more often than a lone
verdict is — on a live corpus, six obligations converged on a missing community
component and three on a missing telemetry-ingest interface, and both were findings
a human reviewer had already made. A lone verdict on a long document is where the
false positives live (DESIGN 3.18).

So: cluster the flagged rows by what they are about, rank clusters by how many
obligations converge, and read downward.

    ./cluster-findings.py --project <dir> --coverage cov.csv
    ./cluster-findings.py --project <dir> --coverage cov.csv --register D2.md --map register-map.yaml

With --register it reports precision@k over clusters: if you read the top five
clusters, how many known findings do you reach? That is the number a reviewer
actually cares about.
"""

import argparse
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm                                                   # noqa: E402
from llm import Embedder, cosine                          # noqa: E402

FLAGGED = ("unmet", "partial", "unverifiable")
WEIGHT = {"unmet": 1.0, "partial": 0.6, "unverifiable": 0.4}


def text_of(row):
    return re.sub(r"\s+", " ", f"{row['requirement']} {row.get('reason','')}")


def cluster(rows, vectors, threshold):
    """Single-link agglomeration. Crude, deterministic, and inspectable —
    a reviewer must be able to see why two rows were grouped."""
    groups = []
    for index, row in enumerate(rows):
        placed = False
        for group in groups:
            if any(cosine(vectors[index], vectors[j]) >= threshold
                   for j in group):
                group.append(index)
                placed = True
                break
        if not placed:
            groups.append([index])
    return groups


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--coverage", default="coverage.csv")
    parser.add_argument("--threshold", type=float, default=0.55,
                        help="chosen by measuring precision@k on one real "
                             "corpus; it groups for reading and changes nothing "
                             "about what was flagged, so it is a presentation "
                             "parameter rather than a correctness one")
    parser.add_argument("--register", default=None)
    parser.add_argument("--map", default="register-map.yaml")
    parser.add_argument("--embed-url",
                        default=llm.DEFAULT_EMBED_URL)
    parser.add_argument("--embed-model", default="Qwen3-Embedding-8B-Q4_K_M")
    parser.add_argument("--out", default="candidates.csv")
    parser.add_argument("--show", type=int, default=12)
    args = parser.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    rows = [r for r in csv.DictReader(
        open(os.path.join(project, args.coverage), encoding="utf-8"))
        if r["verdict"] in FLAGGED]

    embedder = Embedder(project, model=args.embed_model, url=args.embed_url)
    if not embedder.available():
        sys.exit("embedding endpoint unavailable")
    vectors = embedder.embed([text_of(r) for r in rows])

    groups = cluster(rows, vectors, args.threshold)
    scored = []
    for group in groups:
        members = [rows[i] for i in group]
        strength = sum(WEIGHT.get(m["verdict"], 0.4) for m in members)
        scored.append((strength, len(members), group))
    scored.sort(key=lambda x: (-x[0], -x[1]))

    print(f"{len(rows)} flagged obligations -> {len(groups)} candidate findings\n")
    print(f"{'#':>3} {'obls':>5} {'strength':>9}  members / lead requirement")
    for rank, (strength, size, group) in enumerate(scored[:args.show], 1):
        members = [rows[i] for i in group]
        ids = ",".join(m["obligation"] for m in members[:5])
        lead = max(members, key=lambda m: WEIGHT.get(m["verdict"], 0))
        print(f"{rank:>3} {size:>5} {strength:>9.1f}  {ids}")
        print(f"                     {lead['requirement'][:72]}")

    out_path = os.path.join(project, args.out)
    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["rank", "obligations", "strength", "verdicts",
                         "lead_requirement", "lead_reason"])
        for rank, (strength, size, group) in enumerate(scored, 1):
            members = [rows[i] for i in group]
            lead = max(members, key=lambda m: WEIGHT.get(m["verdict"], 0))
            writer.writerow([
                rank, " ".join(m["obligation"] for m in members),
                f"{strength:.1f}",
                " ".join(m["verdict"] for m in members),
                lead["requirement"], lead.get("reason", "")])
    print(f"\nwrote {os.path.relpath(out_path, project)}")

    if not args.register:
        return 0

    # precision@k over clusters: read the top k, how many register findings do
    # you reach? This is the number that decides whether ordering is worth it.
    try:
        import yaml
    except ImportError:
        return 0
    findings = yaml.safe_load(
        open(os.path.join(project, args.map), encoding="utf-8"))["findings"]
    body = open(os.path.join(project, args.register),
                encoding="utf-8", errors="replace").read()
    text_by_id = {}
    for match in re.finditer(r"^\|\s*\**([BARE]\d{1,2})\**\s*\|(.+?)(?=\n\|)",
                             body, re.M | re.S):
        text_by_id[match.group(1)] = re.sub(r"[|*`]", " ", match.group(2))[:1200]
    labelled = [(f["id"], text_by_id.get(f["id"], f.get("title", "")))
                for f in findings if text_by_id.get(f["id"])]
    finding_vectors = embedder.embed([t for _, t in labelled])

    cluster_vectors = []
    for _, _, group in scored:
        centroid = [sum(vectors[i][d] for i in group) / len(group)
                    for d in range(len(vectors[0]))]
        cluster_vectors.append(centroid)

    reached_at = {}
    for (identifier, _), fv in zip(labelled, finding_vectors):
        best_rank, best_score = None, 0.0
        for rank, cv in enumerate(cluster_vectors, 1):
            value = cosine(fv, cv)
            if value > best_score:
                best_rank, best_score = rank, value
        if best_score >= 0.60:
            reached_at[identifier] = best_rank

    print(f"\n-- precision@k over clusters --")
    print(f"   {len(reached_at)} of {len(labelled)} register findings map to a "
          f"cluster at all")
    for k in (3, 5, 10, len(scored)):
        hit = sum(1 for r in reached_at.values() if r <= k)
        print(f"   read top {k:>3} clusters -> reach {hit:2}/{len(labelled)} "
              f"findings   ({100*hit/len(labelled):.0f}%)")
    ordered = sorted(reached_at.items(), key=lambda x: x[1])
    print("   " + ", ".join(f"{i}@{r}" for i, r in ordered[:14]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
