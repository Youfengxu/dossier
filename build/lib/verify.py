#!/usr/bin/env python3
"""Adversarially verify coverage findings before they reach a register.

A coverage run produces CANDIDATES. Most of the cost of a bad review is spent
defending candidates that should never have been raised, so every candidate is
attacked before it is kept.

Three independent lenses, each prompted to REFUTE, each defaulting to refuted
when uncertain:

  search      A wider net than the coverage run used — three times the passages,
              embedding and term-overlap results unioned. The dominant cause of
              a false "unmet" is a retrieval miss, not a judgement error, so the
              first attack is simply to look harder.
  rebuttal    Argue as the party who wrote the deliverable. Their best answer,
              with evidence. If a credible evidence-backed rebuttal exists, the
              finding would not survive the meeting.
  materiality Is this a substantive gap, or a wording or scope quibble? A
              finding that turns on vocabulary rather than substance costs
              credibility to raise.

Only `search` and `rebuttal` vote on truth — both refuting drops the candidate to
`refuted`, one leaves it `contested`, neither confirms it. `materiality` answers
a different question ("is this worth raising") and is reported as a separate
flag, because an immaterial finding is still true and a material one can still be
wrong. Nothing is deleted — refuted rows stay in the
output with the refutation attached, because a refutation is itself information.

    ./verify.py --project . --doc deliverable-v1 --coverage coverage-architecture.csv

This is what local hardware buys that a metered API does not: three extra calls
per candidate is unremarkable at 0.5s each and unaffordable at frontier prices.
"""

import argparse
import csv
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm                                                   # noqa: E402
from llm import Client, Embedder, LLMError, cosine, load_doc   # noqa: E402
from trace import chunk, retrieve, terms                       # noqa: E402

PROMPT_VERSION = "verify-4"

LENSES = {
    "search": """You are attacking a proposed review finding.

The finding claims a deliverable does NOT discharge an obligation. You are given
a WIDER set of passages than the original judgement saw. Your job is to REFUTE
the finding by locating text that does discharge the obligation, wholly or in
substance.

Reply with JSON only:
{"refuted":true|false,"evidence":"verbatim quote","gap_addressed":"the reviewer's specific gap, restated","reason":"one sentence"}

- "refuted": true only if a passage discharges the obligation COMPLETELY.
- Before refuting, read the reviewer's reason and restate THE SPECIFIC GAP it
  names in "gap_addressed". Then check your evidence closes that exact gap. If
  your evidence is about the general topic but leaves the named gap open, that
  is not a refutation.
- A quote saying where something is recorded ("X is described in Section 9",
  "see Appendix C") is NOT evidence. It is a claim about the document. Quote the
  content, or set refuted to false — the pointer may well be untrue, and that
  would be a finding in itself.
- "evidence" MUST be copied verbatim from the passages when refuted is true.
- Wording need not match the obligation. Substance must.
- WATCH THE QUANTIFIER. If the obligation says "every", "each" or "all", you
  must be able to say that NO instance is left unhandled. Quoting a table that
  covers most cases is not a refutation — it is evidence for the cases in the
  table and silence about the rest. Before refuting a universal obligation, ask
  yourself which instance the reviewer had in mind, and check that one.
- If the finding names a specific gap in its reason, address THAT gap. Refuting
  the general topic while ignoring the named gap is not a refutation.
- Addressing the same TOPIC is not the same as discharging the obligation. Ask
  what the obligation actually requires, then check that all of it is present.
- If you cannot find it, refuted is false. Do not speculate about the rest of
  the document.""",

    "rebuttal": """You wrote the deliverable under review. A reviewer has raised
a finding against it. Give your strongest honest rebuttal.

Reply with JSON only:
{"refuted":true|false,"evidence":"verbatim quote","gap_addressed":"the reviewer's specific gap, restated","reason":"your rebuttal, one or two sentences"}

- Restate the reviewer's specific gap in "gap_addressed" before answering it.
  A rebuttal that answers a different, easier objection is not a rebuttal.
- Do not cite a cross-reference ("recorded in Appendix C") as your evidence.
  Quote the content. If you cannot, you have no defence here.
- "refuted": true if you can defend the deliverable with EVIDENCE from the
  passages — the obligation is addressed, addressed elsewhere in substance, or
  the reviewer has misread scope.
- "evidence" MUST be verbatim from the passages when refuted is true.
- Do not bluff. An unsupported rebuttal is refuted=false. You are trying to be
  right, not to win.""",

    "materiality": """You are screening a proposed review finding for
materiality, to keep a register free of quibbles.

Reply with JSON only:
{"refuted":true|false,"evidence":"","reason":"one sentence"}

- "refuted": true if this finding is immaterial — it turns on vocabulary rather
  than substance, restates another finding, or asserts a gap that would not
  change what anyone builds or decides.
- "refuted": false if the gap is substantive: something required is genuinely
  absent, undefined, unowned or contradictory.
- Default to refuted=false when the finding is substantive but narrow. Narrow is
  not the same as immaterial.""",
}


# A cross-reference is a claim ABOUT the document, not content from it.
# "Component maturity is assessed in Section 9" does not show that maturity is
# assessed; it asserts that it is, somewhere else. Accepting such a pointer as
# refuting evidence is how a deliverable's own untrue self-claim gets used to
# dismiss the finding that the claim is untrue — observed on the fixture, where
# the pointer in question resolved to a section about something else entirely.
POINTER = re.compile(
    r"\b(?:is|are|was|were|shall\s+be|will\s+be|been)?\s*"
    r"(?:assessed|recorded|documented|described|provided|defined|specified|"
    r"listed|detailed|addressed|covered|contained|captured|set\s+out|stated)\s+"
    r"(?:in|under|at|within)\s+"
    r"(?:the\s+)?(?:section|appendix|annex|table|figure|chapter|clause|"
    r"paragraph|register|\u00a7)\b", re.I)
CROSS_REF = re.compile(r"\b(?:see|refer\s+to|as\s+per|per)\s+"
                       r"(?:the\s+)?(?:section|appendix|annex|table|figure|"
                       r"clause|\u00a7)\b", re.I)

# A universally-quantified obligation cannot be DISPROVED from a sample. Twelve
# retrieved passages can show a counterexample exists — which confirms a finding
# — but they can never show that none does. Observed on the fixture: a refuter
# correctly restated the gap ("some capabilities are linked to components ...")
# and then refuted it anyway by quoting a table covering six of them.
# So refutation of a universal is capped at `contested`: a human decides.
UNIVERSAL = re.compile(r"\b(?:every|each|all|any|no)\s+\w+", re.I)


def materiality_validator(obj):
    """Materiality is an argument about the finding, not a quote from the text.

    The shared validator below demands that `refuted: true` carry evidence
    quoted verbatim from the passages. Materiality is called with no passages at
    all (`body = ""` at the call site), so every refutation failed the verbatim
    check and every non-refutation passed — `immaterial` was False by
    construction, 25 times out of 25 on the fixture, and the lens that six
    design sections describe had never once fired. A quibble is not disproved by
    quoting the document; it is disproved by an argument. Ask for the argument.
    """
    if not isinstance(obj, dict):
        return "reply must be a JSON object"
    if not isinstance(obj.get("refuted"), bool):
        return "'refuted' must be true or false"
    if not (obj.get("reason") or "").strip():
        return "'reason' must not be empty"
    return None


def validator(haystack):
    normalised = re.sub(r"\s+", " ", haystack).lower()

    def validate(obj):
        if not isinstance(obj, dict):
            return "reply must be a JSON object"
        if not isinstance(obj.get("refuted"), bool):
            return "'refuted' must be true or false"
        if not (obj.get("reason") or "").strip():
            return "'reason' must not be empty"
        evidence = (obj.get("evidence") or "").strip()
        if obj["refuted"]:
            if not evidence:
                return ("a refutation must quote the evidence that discharges "
                        "the obligation; without a quote, refuted must be false")
            if re.sub(r"\s+", " ", evidence).lower() not in normalised:
                return ("evidence must be copied verbatim from the passages; "
                        "quote an exact span or set refuted to false")
            if POINTER.search(evidence) or CROSS_REF.search(evidence):
                return ("that quote is a CROSS-REFERENCE, not evidence — it says "
                        "where something is recorded rather than showing it. "
                        "Quote the content itself, or set refuted to false")
            if not (obj.get("gap_addressed") or "").strip():
                return ("'gap_addressed' must restate, in your own words, the "
                        "specific gap the reviewer named — not the obligation")
        return None
    return validate


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--doc", required=True)
    parser.add_argument("--coverage", default="coverage.csv")
    parser.add_argument("--out", default="findings.csv")
    parser.add_argument("--model", default="qwen3.6-35b-a3b")
    parser.add_argument("--url", default="http://localhost:8085/v1/chat/completions")
    parser.add_argument("--embed-url",
                        default=llm.DEFAULT_EMBED_URL)
    parser.add_argument("--embed-model", default="Qwen3-Embedding-8B-Q4_K_M")
    parser.add_argument("--passages", type=int, default=12,
                        help="wider than the coverage run on purpose")
    parser.add_argument("--verdicts", default="unmet,partial,unverifiable",
                        help="which coverage verdicts to attack")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    coverage_path = os.path.join(project, args.coverage)
    if not os.path.exists(coverage_path):
        sys.exit(f"no {args.coverage} — run trace.py first")

    wanted = {v.strip() for v in args.verdicts.split(",")}
    rows = [r for r in csv.DictReader(open(coverage_path))
            if r["verdict"] in wanted]
    if args.limit:
        rows = rows[:args.limit]
    if not rows:
        sys.exit(f"no rows with verdict in {sorted(wanted)}")

    doc, lines = load_doc(project, args.doc)
    chunks = chunk(lines)
    document_freq = Counter()
    for item in chunks:
        document_freq.update(set(terms(item["text"])))
    idf = {w: (len(chunks) / (1 + n)) ** 0.5 for w, n in document_freq.items()}

    embedder = Embedder(project, model=args.embed_model, url=args.embed_url)
    vectors = None
    if embedder.available():
        vectors = embedder.embed([c["text"] for c in chunks])
    else:
        print("embedding endpoint unavailable — term overlap only", file=sys.stderr)

    client = Client(project, model=args.model, url=args.url,
                    prompt_version=PROMPT_VERSION)
    print(f"attacking {len(rows)} candidates with {len(LENSES)} lenses each "
          f"({len(rows) * len(LENSES)} calls)\n")

    out, tally = [], Counter()
    for index, row in enumerate(rows, 1):
        requirement = row["requirement"]

        # Union of both retrievers, deliberately wide.
        picked, seen = [], set()
        if vectors:
            query = embedder.embed([requirement])[0]
            ranked = sorted(((cosine(query, v), i)
                             for i, v in enumerate(vectors)), reverse=True)
            for _, i in ranked[:args.passages]:
                if i not in seen:
                    seen.add(i); picked.append(chunks[i])
        for item in retrieve(requirement, chunks, idf, k=args.passages // 2):
            key = (item["start"], item["end"])
            if key not in {(p["start"], p["end"]) for p in picked}:
                picked.append(item)

        supplied = "\n\n".join(
            f"[{p['heading']} | {args.doc}:{p['start']}-{p['end']}]\n{p['text']}"
            for p in picked)

        base = (f"OBLIGATION ({row.get('modality','')})\n{requirement}\n\n"
                f"FINDING: the deliverable does not discharge this "
                f"(coverage verdict: {row['verdict']})\n"
                f"Reviewer's reason: {row['reason']}\n\n"
                f"PASSAGES FROM THE DELIVERABLE\n{supplied}\n")

        results = {}
        for name, system in LENSES.items():
            body = supplied if name != "materiality" else ""
            try:
                check = (materiality_validator if name == "materiality"
                         else validator(body))
                results[name] = client.ask(system, base, validate=check,
                                           label=f"{name}:{row['obligation']}")
            except LLMError as exc:
                # A lens that errors must not read as "did not refute" — that
                # silently promotes candidates to confirmed on infrastructure
                # noise. Mark it failed and force the row to contested.
                results[name] = {"refuted": False, "evidence": "", "failed": True,
                                 "reason": f"lens failed: {exc}"}

        # search and rebuttal answer "is this true"; materiality answers "is it
        # worth raising". They are different questions and must not vote in one
        # tally — an immaterial finding is still true, and a material one can
        # still be wrong.
        truth_lenses = ("search", "rebuttal")
        refutations = sum(1 for name in truth_lenses if results[name]["refuted"])
        failed = [n for n in truth_lenses if results[n].get("failed")]
        universal = bool(UNIVERSAL.search(requirement))
        if failed:
            status = "contested"
        else:
            status = ("refuted" if refutations == 2
                      else "contested" if refutations == 1 else "confirmed")
            if status == "refuted" and universal:
                status = "contested"          # cannot disprove a universal
        immaterial = results["materiality"]["refuted"]
        tally[status] += 1

        out.append({
            "obligation": row["obligation"],
            "scope": row.get("scope", ""),
            "requirement": requirement,
            "coverage_verdict": row["verdict"],
            "status": status,
            "refutations": refutations,
            "lens_failures": ",".join(failed),
            "universal": universal,
            "search_refuted": results["search"]["refuted"],
            "search_evidence": results["search"].get("evidence", ""),
            "search_gap": results["search"].get("gap_addressed", ""),
            "rebuttal": results["rebuttal"]["reason"],
            "rebuttal_evidence": results["rebuttal"].get("evidence", ""),
            "immaterial": immaterial,
            "materiality": results["materiality"]["reason"],
            "requirement_locator": row.get("requirement_locator", ""),
        })
        mark = {"confirmed": " +", "contested": " ?", "refuted": " -"}[status]
        note = " universal" if universal and refutations == 2 else ""
        print(f"{mark} {row['obligation']:7} {status:10} ({refutations}/2){note}"
              f"{' immaterial' if immaterial else '           '}  "
              f"{requirement[:50]}")

    out_path = os.path.join(project, args.out)
    with open(out_path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(out[0].keys()))
        writer.writeheader()
        writer.writerows(out)

    print(f"\n{client.summary()}")
    print("  " + "  ".join(f"{k}={tally[k]}" for k in
                           ("confirmed", "contested", "refuted"))
          + f"   |  flagged immaterial: {sum(1 for r in out if r['immaterial'])}")
    print(f"\nwrote {os.path.relpath(out_path, project)}")
    print("Confirmed rows survived three attacks. Contested rows need you to "
          "read the\nrefutation. Refuted rows are kept, not deleted — a "
          "refutation is information.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
