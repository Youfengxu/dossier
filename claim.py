#!/usr/bin/env python3
"""Claim index: find statements in one document that cannot both be true.

This is the class coverage tracing structurally cannot reach. A deliverable can
discharge every obligation in the requirements and still contradict itself, and
no amount of obligation-by-obligation checking will notice — each obligation is
judged against passages that happen to satisfy it, and the passage that says the
opposite is somewhere else entirely.

Three stages, and the middle one is the whole design:

  1  EXTRACT   one bounded agent per section, asked for claims that could be
               contradicted — decisions, properties, constraints, statuses.
               Never asked to find contradictions: it can see one section.
  2  GROUP     claims are bucketed by normalised subject, deterministically.
               Two claims can only conflict if they are about the same thing,
               so this is what makes the problem tractable — all-pairs over a
               10,000-line document is 50 million comparisons and nearly all of
               them are nonsense.
  3  ADJUDICATE  each candidate pair gets one independent call asking whether
               both can hold, with both quotes verified verbatim first.

    ./claim.py --project . --doc arch-v5 --out claims-v5.json
    ./claim.py --project . --doc arch-v5 --report          # from a saved index

WHY GROUPING IS THE HARD PART. If subjects do not normalise, claims about the
same thing never meet and the tool finds nothing while appearing to work — the
failure mode this toolkit keeps producing. So grouping is deterministic and
reported: --show-groups prints what merged with what, and a subject that should
have merged and did not is visible rather than silent.

WHAT IT DOES NOT DO. It finds contradictions between STATED claims. A document
that omits a decision entirely is not contradicting itself, and this will say
nothing about it — that is the coverage pipeline's job. It also cannot reach
contradictions that require derivation ("these two rates are inconsistent given
the tick budget"); see DESIGN on mechanism-level entailment.
"""

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm import Client, LLMError, load_doc, Embedder, cosine  # noqa: E402
import closure  # noqa: E402
import inventory  # noqa: E402

PROMPT_VERSION = "claim-3"

EXTRACT_SYSTEM = """You catalogue the claims one section of a technical document makes.

Reply with JSON only:
{"claims":[{"subject":"...","object":"...","predicate":"...","polarity":"affirm|deny","kind":"decision|property|constraint|status","quote":"..."}]}

A CLAIM is a statement that could be contradicted by another statement. Extract
only these four kinds:
  decision    a choice the document states has been made ("X is implemented as Y",
              "the engine uses Z", "deployment is on-premises")
  property    an asserted fact about a component ("X is stateless", "X owns Y",
              "the store is append-only")
  constraint  a limit or rule stated as binding ("must complete within one tick",
              "no component may write to X")
  status      a lifecycle or maturity assertion ("X is out of scope for the MVP",
              "Y is research-only", "Z is approved")

SUBJECT is the thing the claim is asserting ABOUT — usually the actor. A short
noun phrase in the document's own words. Use the most specific named thing
available: "REDACTED-14", not "the system".

OBJECT is the capability, artefact, quantity or decision the claim concerns —
what the subject acts on, owns, constrains or is assigned. "" if the claim has
no object ("the Decision Engine is stateless").

The object matters as much as the subject and is easy to leave out. Two
statements that conflict often have DIFFERENT subjects and the SAME object:
"the Orchestrator is authoritative for execution order" and "the Alerting
Service determines evaluation order" contradict each other, and nothing about
either sentence alone reveals it. Name the object in the document's own words so
the two can be matched: "execution order", not "ordering".

POLARITY is "deny" when the statement FORBIDS or NEGATES, "affirm" otherwise.
Put the negation here, never in the subject or predicate. "No component may
modify a frozen schedule" is subject "any component", object "frozen schedule",
predicate "modifies", polarity "deny" — NOT subject "No component". A negation
buried in a subject cannot be compared against the positive statement that
violates it, which is the entire point of recording the claim.

PREDICATE is what is asserted about the subject, in your own words, in under 15
words. Make it self-contained: it will be read next to another claim about the
same subject and with no other context.

QUOTE must be copied verbatim from the section text, including punctuation. It
is checked against the document and the claim is discarded if it does not match.

Extract EVERY statement of the four kinds above — a section commonly makes
several. Completeness matters here: a claim not extracted is a contradiction
that cannot be found later, and no downstream stage can recover it.

NUMBERED OR BULLETED STEPS COUNT. "3. AS reorders the frozen execution order"
names an actor, an action and an artefact, and is exactly the kind of statement
that contradicts an authority rule stated elsewhere. Extract every step that
names who does what to what. Skipping one because it looks like narrative is the
single commonest way this stage loses a real finding.

DO NOT extract: headings, bare cross-references, lists of names with nothing
asserted about them, or anything hedged so heavily it asserts nothing ("may in
future be considered"). A section that asserts nothing returns {"claims":[]}."""

JUDGE_SYSTEM = """You are given two statements from the same technical document,
about the same subject. Decide whether they can both be true.

Reply with JSON only:
{"verdict":"contradiction|tension|compatible","confidence":"high|low","reason":"..."}

  contradiction  they cannot both hold. One says X, the other says not-X, or
                 they assign the same exclusive role, value or owner differently.
  tension        they sit awkwardly together and a reader would need to
                 reconcile them, but there is a reading where both hold.
  compatible     they are consistent, or simply about different aspects.

Be strict. Restatement is not contradiction. Different level of detail is not
contradiction. A general statement and a specific exception are not a
contradiction unless the general one is stated as absolute. Two components each
doing part of a job is not a contradiction unless the document says one of them
does all of it or the other does none.

Default to "compatible" when unsure, and set confidence "low" whenever the
judgement depends on document context you have not been shown. A false
contradiction costs a reviewer more than a missed one: it sends them to read a
passage that turns out to be fine, and enough of those and they stop reading."""


def validate_extract(obj):
    if not isinstance(obj, dict) or not isinstance(obj.get("claims"), list):
        return "top level must be an object with a 'claims' array"
    for item in obj["claims"]:
        if not isinstance(item, dict):
            return "each claim must be an object"
        if not isinstance(item.get("object"), str):
            return "each claim needs an 'object' string (\"\" if none)"
        for key in ("subject", "predicate", "quote"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                return f"each claim needs a non-empty '{key}'"
        if item.get("polarity") not in ("affirm", "deny"):
            return "'polarity' must be affirm or deny"
        if item.get("kind") not in ("decision", "property", "constraint",
                                    "status"):
            return "'kind' must be decision, property, constraint or status"
    return None


def validate_judge(obj):
    if not isinstance(obj, dict):
        return "reply must be a JSON object"
    if obj.get("verdict") not in ("contradiction", "tension", "compatible"):
        return "'verdict' must be contradiction, tension or compatible"
    if obj.get("confidence") not in ("high", "low"):
        return "'confidence' must be high or low"
    if not isinstance(obj.get("reason"), str) or not obj["reason"].strip():
        return "'reason' must be a non-empty string"
    return None


# Words that carry no distinguishing weight in a subject. Kept deliberately
# short: aggressive stripping merges subjects that are genuinely different, and
# a wrong merge manufactures contradictions between unrelated things.
NOISE = {"the", "a", "an", "this", "that", "its", "their", "system", "platform",
         "component", "service", "module", "layer", "document"}


def normalise_subject(text, aliases):
    low = re.sub(r"[^a-z0-9 ]", " ", text.lower())
    low = re.sub(r"\s+", " ", low).strip()
    if low in aliases:
        return aliases[low]
    words = [w for w in low.split() if w not in NOISE]
    if not words:
        words = low.split()
    stem = " ".join(words)
    # Singularise the trailing token only. Blanket stemming collapses
    # "Orchestrator" and "Orchestration", which name different things in a
    # document that has both.
    if stem.endswith("s") and not stem.endswith("ss"):
        stem = stem[:-1]
    return aliases.get(stem, stem)


def load_aliases(project):
    """components.yaml, if the project has one: alias -> canonical name."""
    path = os.path.join(project, "components.yaml")
    if not os.path.exists(path):
        return {}
    data = closure.load_yaml(path) or {}
    out = {}
    entries = data.get("components", data) if isinstance(data, dict) else data
    if isinstance(entries, dict):
        entries = [{"name": k, **(v or {})} for k, v in entries.items()]
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        canonical = str(entry.get("name", "")).strip().lower()
        if not canonical:
            continue
        for alias in [canonical] + [str(a).lower()
                                    for a in entry.get("aliases", []) or []]:
            clean = re.sub(r"\s+", " ",
                           re.sub(r"[^a-z0-9 ]", " ", alias)).strip()
            if clean:
                out[clean] = canonical
    return out


def extract(client, sections, concurrency, runs=1, verbose=True):
    """Union of `runs` passes.

    Extraction samples what a section says rather than enumerating it: on the
    fixture, steps 1, 4 and 5 of a five-step sequence came back and step 3 —
    the one that contradicted an authority rule forty lines earlier — did not.
    A claim missed here cannot be recovered downstream, so recall at this stage
    is worth paying for twice.
    """
    claims = []
    def one(index_section):
        index, section = index_section
        user = (f"Section: {section['heading']}\n"
                f"Lines {section['start']}-{section['end']}\n\n"
                f"{section['text']}")
        try:
            reply = client.ask(EXTRACT_SYSTEM, user,
                               validate=validate_extract,
                               label=f"claim:{section['start']}")
        except LLMError:
            return []
        out = []
        for item in reply["claims"]:
            out.append({
                "subject": item["subject"].strip(),
                "object": item.get("object", "").strip(),
                "predicate": item["predicate"].strip(),
                "polarity": item.get("polarity", "affirm"),
                "kind": item["kind"],
                "quote": item["quote"].strip(),
                "heading": section["heading"],
                "start": section["start"], "end": section["end"],
            })
        return out

    for run in range(runs):
        client.prompt_version = f"{PROMPT_VERSION}-r{run}" if run else PROMPT_VERSION
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            for index, batch in enumerate(
                    pool.map(one, enumerate(sections)), 1):
                claims.extend(batch)
                if verbose and (index % 25 == 0 or index == len(sections)):
                    print(f"  run {run + 1}/{runs}: {index}/{len(sections)} "
                          f"sections, {len(claims)} claims")
    client.prompt_version = PROMPT_VERSION
    seen, unique = set(), []
    for claim in claims:
        key = (claim["start"], closure.normalise(claim["quote"]).lower(),
               claim["subject"].lower())
        if key not in seen:
            seen.add(key)
            unique.append(claim)
    if runs > 1 and verbose:
        print(f"  union of {runs} runs: {len(unique)} distinct claims "
              f"from {len(claims)}")
    return unique


def verify_quotes(claims, lines):
    """Drop claims whose quote is not in the document.

    A claim resting on a quote the document does not contain is a fabrication,
    and it would be adjudicated against another claim as though it were real.
    Normalised comparison only — extraction reflows whitespace.
    """
    blob = closure.normalise(" ".join(lines)).lower()
    kept, dropped, fragments = [], 0, 0
    for claim in claims:
        needle = closure.normalise(claim["quote"]).lower()
        # A quote must be long enough to BE an assertion. Table cells produce
        # claims like subject "Policy statement", quote "Policy statement" —
        # a label, not a statement, and the judge duly found it contradicting
        # an unrelated table cell elsewhere. Roughly four words is the shortest
        # thing that can carry a subject and a predicate.
        # Table rows are catalogue data, not assertions. A gap register or
        # interface table row — "IF-AS-001 | Estimated state intake | in" —
        # states no proposition, but read next to prose it looks like one, and
        # every false positive gpt-oss-120b produced on the fixture had such a
        # row on one side of the pair. Identifiers and deferred items are
        # inventory.py's job; the claim index wants sentences.
        if " | " in claim["quote"]:
            fragments += 1
        elif len(needle) < 25 or " " not in needle:
            fragments += 1
        elif needle in blob:
            kept.append(claim)
        else:
            dropped += 1
    return kept, dropped, fragments


def initial_aliases(keys):
    """Map an all-initials key onto the multiword key it abbreviates.

    Documents introduce "RO" for "Runtime Orchestrator" and then use it for
    fifty pages. Embeddings cannot recover this — measured on the fixture,
    "Runtime Orchestrator" ~ "RO" scores 0.496 while "Runtime Orchestrator" ~
    "Alerting Service" scores 0.502, so any threshold that merges the
    abbreviation also merges two unrelated components. Initials are exact and
    deterministic, which is what this needs to be.
    """
    out = {}
    expansions = [k for k in keys if " " in k]
    for key in keys:
        if " " in key or not 2 <= len(key) <= 4 or not key.isalpha():
            continue
        matches = [e for e in expansions
                   if "".join(w[0] for w in e.split()) == key]
        # Exactly one expansion, or none. "RO" matching both "runtime
        # orchestrator" and "replay output" is not a resolvable abbreviation,
        # and picking either merges claims about unrelated things.
        if len(matches) == 1:
            out[key] = matches[0]
    return out


def object_pairs(claims, embedder, threshold, per_claim):
    """Pairs of claims whose OBJECTS are near-synonymous.

    The commonest real contradiction has two different subjects and one shared
    object — one component claiming authority a second component also exercises.
    Subject grouping cannot see it, and the object is free text, so this is the
    one place embeddings earn their keep.

    Nearest-neighbour rather than transitive clustering: clustering chains, and
    a chain merges A with C on the strength of B, which is how a grouping stage
    manufactures pairs nobody can defend.
    """
    with_object = [c for c in claims if c.get("object")]
    if len(with_object) < 2 or embedder is None:
        return []
    texts = sorted({c["object"] for c in with_object})
    vectors = dict(zip(texts, embedder.embed(texts)))
    pairs, seen = [], set()
    for index, a in enumerate(with_object):
        scored = []
        for b in with_object[index + 1:]:
            if a["start"] == b["start"]:
                continue
            similarity = cosine(vectors[a["object"]], vectors[b["object"]])
            if similarity >= threshold:
                scored.append((similarity, b))
        scored.sort(key=lambda x: -x[0])
        for similarity, b in scored[:per_claim]:
            fingerprint = tuple(sorted([a["start"], b["start"]]))
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            pairs.append((a["object"], a, b))
    return pairs


def pair_up(group, embedder, max_pairs):
    """Candidate pairs within one subject group.

    Every pair, when the group is small. When it is large, rank by predicate
    DISSIMILARITY: two claims about one subject that say nearly the same thing
    are a restatement, and restatement is the single most common false positive
    this stage can produce.
    """
    # Never pair two claims from the same line. They are almost always
    # fragments of one sentence, judging them against each other asks whether a
    # statement contradicts itself, and on the fixture such pairs were half of
    # every judge call the endpoint rejected outright.
    pairs = [(a, b) for i, a in enumerate(group) for b in group[i + 1:]
             if a["start"] != b["start"]]
    if len(pairs) <= max_pairs or embedder is None:
        return pairs[:max_pairs]
    vectors = {}
    texts = list({c["predicate"] for c in group})
    for text, vector in zip(texts, embedder.embed(texts)):
        vectors[text] = vector
    scored = []
    for a, b in pairs:
        va, vb = vectors.get(a["predicate"]), vectors.get(b["predicate"])
        similarity = cosine(va, vb) if va and vb else 0.0
        scored.append((similarity, a, b))
    scored.sort(key=lambda x: x[0])
    return [(a, b) for _, a, b in scored[:max_pairs]]


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--doc", required=True)
    parser.add_argument("--model")
    parser.add_argument("--url")
    parser.add_argument("--embed-url")
    parser.add_argument("--embed-model")
    parser.add_argument("--out")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--limit", type=int, help="first N sections")
    parser.add_argument("--max-chars", type=int, default=1200)
    parser.add_argument("--max-pairs", type=int, default=12,
                        help="candidate pairs per subject group (default 12)")
    parser.add_argument("--runs", type=int, default=2,
                        help="extraction passes to union (default 2)")
    parser.add_argument("--min-group", type=int, default=2)
    parser.add_argument("--object-threshold", type=float, default=0.52,
                        help="cosine above which two claims are about the same "
                             "object. Measured on the fixture: true merges "
                             "bottom out at 0.530, false merges top out at "
                             "0.478; 0.52 sits in that gap.")
    parser.add_argument("--per-claim", type=int, default=4,
                        help="object-neighbour pairs per claim (default 4)")
    parser.add_argument("--show-groups", action="store_true")
    parser.add_argument("--reuse", help="skip extraction, load this index")
    parser.add_argument("--include-tension", action="store_true",
                        help="report tension verdicts as well as contradictions")
    args = parser.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    _doc, lines = load_doc(project, args.doc)
    aliases = load_aliases(project)

    kwargs = {}
    if args.model:
        kwargs["model"] = args.model
    if args.url:
        kwargs["url"] = args.url
    client = Client(project, prompt_version=PROMPT_VERSION, max_tokens=1200,
                    **kwargs)

    embed_kwargs = {}
    if args.embed_url:
        embed_kwargs["url"] = args.embed_url
    if args.embed_model:
        embed_kwargs["model"] = args.embed_model
    embedder = Embedder(project, **embed_kwargs)

    if args.reuse:
        claims = json.load(open(os.path.join(project, args.reuse)))["claims"]
        print(f"reusing {len(claims)} claims from {args.reuse}")
    else:
        sections = inventory.split_sections(lines, max_chars=args.max_chars)
        if args.limit:
            sections = sections[:args.limit]
        print(f"{args.doc}: {len(sections)} sections")
        claims = extract(client, sections, args.concurrency, args.runs)
        claims, dropped, fragments = verify_quotes(claims, lines)
        print(f"  {len(claims)} claims survive verification "
              f"({dropped} unquotable, {fragments} too short to assert)")

    # Group on BOTH axes. A claim joins the bucket for its subject and the
    # bucket for its object, so two claims meet if they are about the same
    # actor OR about the same thing being acted on. Subject-only grouping
    # cannot see the commonest shape of a real contradiction: two components
    # each claiming exclusive authority over one capability, stated in
    # sections that never mention each other.
    for claim in claims:
        claim["subject_key"] = normalise_subject(claim["subject"], aliases)
    extra = initial_aliases({c["subject_key"] for c in claims})
    if extra:
        print(f"  abbreviations resolved: "
              + ", ".join(f"{k}->{v}" for k, v in sorted(extra.items())))
        for claim in claims:
            claim["subject_key"] = extra.get(claim["subject_key"],
                                             claim["subject_key"])
    groups = {}
    for claim in claims:
        groups.setdefault(("subject", claim["subject_key"]), []).append(claim)
    multi = {k: v for k, v in groups.items() if len(v) >= args.min_group}
    by_axis = {}
    for axis, _ in multi:
        by_axis[axis] = by_axis.get(axis, 0) + 1
    print(f"  {len(groups)} groups, {len(multi)} with {args.min_group}+ claims "
          f"({by_axis.get('subject',0)} by subject, "
          f"{by_axis.get('object',0)} by object)")

    if args.show_groups:
        for key, group in sorted(multi.items(), key=lambda x: -len(x[1]))[:25]:
            print(f"\n  [{key[0]}] {key[1]}  ({len(group)})")
            for claim in group[:6]:
                print(f"    {args.doc}:{claim['start']}  [{claim['kind']}] "
                      f"{claim['predicate'][:80]}")

    candidates, seen_pairs = [], set()
    for label, a, b in object_pairs(claims, embedder, args.object_threshold,
                                    args.per_claim):
        fingerprint = tuple(sorted([(a["start"], a["quote"][:40]),
                                    (b["start"], b["quote"][:40])]))
        seen_pairs.add(fingerprint)
        candidates.append((label, a, b))
    print(f"  {len(candidates)} pair(s) from shared objects "
          f"(similarity >= {args.object_threshold})")
    for key, group in multi.items():
        for a, b in pair_up(group, embedder, args.max_pairs):
            # One pair can share both a subject and an object. Judge it once.
            fingerprint = tuple(sorted([(a["start"], a["quote"][:40]),
                                        (b["start"], b["quote"][:40])]))
            if fingerprint in seen_pairs:
                continue
            seen_pairs.add(fingerprint)
            candidates.append((key[1], a, b))
    print(f"  {len(candidates)} candidate pairs to adjudicate")

    def judge(candidate):
        key, a, b = candidate
        user = (f"Subject: {key}\n\n"
                f"Statement 1 ({args.doc}:{a['start']}, {a['kind']}, "
                f"section \"{a['heading']}\"):\n"
                f"  {a['predicate']}\n  Quote: \"{a['quote']}\"\n\n"
                f"Statement 2 ({args.doc}:{b['start']}, {b['kind']}, "
                f"section \"{b['heading']}\"):\n"
                f"  {b['predicate']}\n  Quote: \"{b['quote']}\"")
        try:
            reply = client.ask(JUDGE_SYSTEM, user, validate=validate_judge,
                               label=f"judge:{a['start']}-{b['start']}")
        except LLMError:
            return None
        return {"subject": key, "verdict": reply["verdict"],
                "confidence": reply["confidence"], "reason": reply["reason"],
                "a": a, "b": b}

    findings = []
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        for index, result in enumerate(pool.map(judge, candidates), 1):
            if result:
                findings.append(result)
            if index % 25 == 0 or index == len(candidates):
                hits = sum(1 for f in findings
                           if f["verdict"] == "contradiction")
                print(f"  judged {index}/{len(candidates)}, {hits} contradictions")

    wanted = ["contradiction"] + (["tension"] if args.include_tension else [])
    hits = [f for f in findings if f["verdict"] in wanted]
    # Same subject, same pair of sections, judged twice — one finding.
    seen, unique = set(), []
    for finding in sorted(hits, key=lambda f: (f["verdict"] != "contradiction",
                                               f["confidence"] != "high")):
        key = (finding["subject"], finding["a"]["start"], finding["b"]["start"])
        if key not in seen:
            seen.add(key)
            unique.append(finding)

    print("\n" + "=" * 74)
    print(f"{len(unique)} finding(s)")
    print("=" * 74)
    for finding in unique:
        a, b = finding["a"], finding["b"]
        flag = "" if finding["confidence"] == "high" else "  (low confidence)"
        print(f"\n{finding['verdict'].upper()}  {finding['subject']}{flag}")
        print(f"  {args.doc}:{a['start']}  {a['predicate']}")
        print(f"      \"{a['quote'][:110]}\"")
        print(f"  {args.doc}:{b['start']}  {b['predicate']}")
        print(f"      \"{b['quote'][:110]}\"")
        print(f"  -> {finding['reason'][:200]}")

    if args.out:
        # Write to a temporary file and rename. Two runs writing the same path
        # interleaved their output and produced a JSON file that ended validly
        # and was corrupt in the middle — the kind of damage that is invisible
        # until something tries to parse it hours later. rename is atomic.
        path = os.path.join(project, args.out)
        temporary = f"{path}.{os.getpid()}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump({"doc": args.doc, "claims": claims,
                       "findings": findings}, handle, indent=1)
        os.replace(temporary, path)
        print(f"\nwrote {path}")
    print(f"model calls: {client.stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
