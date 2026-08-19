#!/usr/bin/env python3
"""Find defects by querying the inventory. Mostly code, not a model.

The orchestrator in an agentic pipeline is usually another model reading every
agent's output. Here it deliberately is not. Ninety-odd section summaries is
~28k tokens, and 28k tokens is exactly the long-context regime where judgement
was measured decaying (DESIGN 3.18). So the model builds the inventory and code
finds the patterns; a model is called back only to adjudicate a specific pair.

Four classes, none of which coverage tracing can reach, because each is a
property of the document as a whole rather than of any obligation:

  D5  dual binding      two sections claim authority over the same capability
  D6  ownership gap     a capability every section defers to another
  D8  orphan workstream something produced that nothing consumes
  D3  untrue self-claim "recorded in section N" where section N holds no such thing

Absence becomes legitimate here for the first time. A section agent never
asserts it; this queries an inventory built by reading 100% of the document.

    ./synthesize.py --project . --inventory inventory.json
    ./synthesize.py --project . --inventory inventory.json --ground-truth ground-truth.yaml
"""

import argparse
import difflib
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm                                                   # noqa: E402
from llm import Client, Embedder, LLMError, cosine        # noqa: E402

STOP = set("""the a an and or of to in for on with by is are be been shall must
should may not that this these those it its as at from any all each every per
which where when who whom whose and/or""".split())


def norm(text):
    words = [w for w in re.findall(r"[a-z0-9]{3,}", (text or "").lower())
             if w not in STOP]
    return frozenset(words)


def similar(a, b, threshold=0.5):
    if not a or not b:
        return False
    overlap = len(a & b) / min(len(a), len(b))
    return overlap >= threshold


def group_by_concept(entries, key, embedder=None, threshold=0.62):
    """Cluster entries whose `key` field names the same thing.

    Token overlap is not good enough here and the fixture proves it: the same
    architectural slot is called "execution scheduling" in one section and
    "determine evaluation order for pending alerts" in another. Those share no
    words and are the dual-binding defect. Concept identity is semantic, so the
    grouping has to be too.
    """
    labels = [e.get(key, "") for e in entries]
    keep = [i for i, l in enumerate(labels) if norm(l)]
    if not keep:
        return []
    if embedder is None:
        groups = []
        for i in keep:
            tokens = norm(labels[i])
            for group in groups:
                if similar(tokens, group["tokens"]):
                    group["members"].append(entries[i])
                    group["tokens"] |= tokens
                    break
            else:
                groups.append({"tokens": tokens, "members": [entries[i]],
                               "label": labels[i]})
        return groups
    vectors = embedder.embed([labels[i] for i in keep])
    groups = []
    for position, i in enumerate(keep):
        for group in groups:
            if any(cosine(vectors[position], vectors[j]) >= threshold
                   for j in group["vecs"]):
                group["members"].append(entries[i])
                group["vecs"].append(position)
                group["tokens"] |= norm(labels[i])
                break
        else:
            groups.append({"tokens": norm(labels[i]), "members": [entries[i]],
                           "vecs": [position], "label": labels[i]})
    for group in groups:
        group["vecs"] = [vectors[j] for j in group["vecs"]]
    return groups



PAIR_SYSTEM = """You compare two ownership statements taken from different
sections of one architecture document.

Reply with JSON only:
{"same_slot":true|false,"conflict":true|false,"reason":"one sentence"}

- "same_slot": do these govern the SAME decision or resource? Different wording
  is expected — "execution scheduling" and "determining evaluation order within
  a tick" are the same slot. "Sensor ingest" and "alert emission" are not.
- "conflict": same_slot AND different owners AND nothing reconciles them. If one
  statement explicitly subordinates itself to the other, that is not a conflict.
- Default both to false when unsure. A wrong conflict costs a reviewer more than
  a missed one costs you."""


def adjudicate_pairs(client, entries, key, concurrency=1, groups=None,
                     cap=900):
    """Cross-section pairs with differing owners, reduced by clustering first.

    On a fixture with 20 authority assertions, every pair is affordable. On a
    real architecture with 788 it is 310,000 — so the candidate set is cut to
    pairs that fall inside the SAME embedding cluster, i.e. plausibly the same
    slot. That reintroduces a similarity assumption the fixture warned about,
    so the clusters are deliberately loose and every exact-string collision is
    admitted regardless of cluster.
    """
    from concurrent.futures import ThreadPoolExecutor
    pairs, seen = [], set()

    def consider(a, b):
        if a.get("_locator") == b.get("_locator"):
            return
        if norm(a.get("owner") or a.get("to") or "") == \
           norm(b.get("owner") or b.get("to") or ""):
            return
        mark = (id(a), id(b))
        if mark in seen:
            return
        seen.add(mark)
        pairs.append((a, b))

    # Exhaustive first, and only fall back to clustering when exhaustive is
    # genuinely out of reach. Reducing by similarity reintroduces the assumption
    # the fixture disproves — "execution scheduling" and "determine evaluation
    # order for pending alerts" land in different clusters and their pair is the
    # defect. On a small document every pair is affordable, so take them all.
    exhaustive = []
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            a, b = entries[i], entries[j]
            if a.get("_locator") == b.get("_locator"):
                continue
            if norm(a.get("owner") or a.get("to") or "") == \
               norm(b.get("owner") or b.get("to") or ""):
                continue
            exhaustive.append((a, b))

    if groups is None or len(exhaustive) <= cap:
        if groups is not None:
            print(f"  {len(exhaustive)} pairs — under the cap, adjudicating all "
                  f"of them rather than clustering")
        pairs = exhaustive
        seen.update((id(a), id(b)) for a, b in pairs)
    else:
        for group in groups:
            members = group["members"]
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    consider(members[i], members[j])
        exact = {}
        for entry in entries:
            exact.setdefault(norm(entry.get(key, "")), []).append(entry)
        for members in exact.values():
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    consider(members[i], members[j])
    if len(pairs) > cap:
        print(f"  {len(exhaustive)} pairs exhaustively, reduced to {len(pairs)} "
              f"by clustering, capping at {cap}")
        pairs = pairs[:cap]
    elif groups is not None and len(exhaustive) > cap:
        print(f"  {len(exhaustive)} pairs reduced to {len(pairs)} by clustering")

    def check(pair):
        a, b = pair
        user = (f"A. [{a.get('_heading','')}] {a.get(key,'')} "
                f"— owner: {a.get('owner') or a.get('to','')}\n"
                f"   {a.get('quote','')[:220]}\n\n"
                f"B. [{b.get('_heading','')}] {b.get(key,'')} "
                f"— owner: {b.get('owner') or b.get('to','')}\n"
                f"   {b.get('quote','')[:220]}\n\n"
                "Same slot? Conflict?")
        try:
            reply = client.ask(PAIR_SYSTEM, user, validate=lambda o: (
                None if isinstance(o, dict)
                and isinstance(o.get("same_slot"), bool)
                and isinstance(o.get("conflict"), bool)
                else "need boolean 'same_slot' and 'conflict'"),
                label=f"pair:{len(pairs)}")
        except LLMError:
            return None
        return (a, b, reply) if reply.get("conflict") else None

    if concurrency > 1:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            results = list(pool.map(check, pairs))
    else:
        results = [check(p) for p in pairs]
    return len(pairs), [r for r in results if r]




SECTION_NO = re.compile(r"^(\d+)(?:\.\d+)*[.\s]")


def section_owner_map(sections):
    """Top-level section number -> the component that section is about.

    A deferral records what is passed and to whom, never who is passing it —
    that is implicit in the section it appears in. Without the source, "SF defers
    to HM" and "HM defers to AS" are two unrelated facts; with it they are two
    edges of a chain, and a chain that closes is an ownership gap.
    """
    owners = {}
    for section in sections:
        heading = (section.get("heading") or "").strip()
        match = re.match(r"^(\d+)\.\s+(\S.*)$", heading)
        if match and match.group(1) not in owners:
            owners[match.group(1)] = match.group(2).strip()
    return owners


def defer_source(entry, owners):
    match = SECTION_NO.match((entry.get("_heading") or "").strip())
    return owners.get(match.group(1)) if match else None


def find_cycles(edges):
    """Cycles in a small directed graph, as node lists. Depth-first, no library."""
    graph = {}
    for a, b in edges:
        graph.setdefault(a, set()).add(b)
    found, seen = [], set()

    def walk(node, path):
        for nxt in graph.get(node, ()):
            if nxt in path:
                cycle = path[path.index(nxt):] + [nxt]
                key = tuple(sorted(set(cycle)))
                if key not in seen and len(key) > 1:
                    seen.add(key)
                    found.append(cycle)
            elif len(path) < 8:
                walk(nxt, path + [nxt])

    for node in list(graph):
        walk(node, [node])
    return found


def load_components(path):
    """canonical name -> aliases, plus strings that name no component."""
    if not os.path.exists(path):
        return {}, set()
    try:
        import yaml
    except ImportError:
        return {}, set()
    data = yaml.safe_load(open(path, encoding="utf-8")) or {}
    comps = {k: [k.lower()] + [a.lower() for a in (v or [])]
             for k, v in (data.get("components") or {}).items()}
    return comps, {x.lower() for x in (data.get("exclude") or [])}


def to_component(owner, comps, excluded):
    """Longest alias wins, so "platform adapter" does not resolve to "platform"
    by accident and "runtime orchestrator" does not resolve to "context"."""
    text = re.sub(r"\s+", " ", (owner or "")).strip().lower()
    if not text or text in excluded:
        return None
    best, best_len = None, 0
    for canonical, aliases in comps.items():
        for alias in aliases:
            if alias in text and len(alias) > best_len:
                best, best_len = canonical, len(alias)
    return best


# Fraction of a ground-truth anchor that one finding must contain contiguously.
# 0.6 accepts reformatting and truncation; it rejects word bags, which share
# vocabulary but no phrasing.
ANCHOR_COVERAGE = 0.6


def _flat(text):
    """Lowercase, punctuation and line breaks collapsed to single spaces.

    Ground-truth anchors are copied out of the document and carry its wrapping;
    a finding quotes the same sentence after the parser has reflowed it. Only
    the words are stable, so only the words are compared.
    """
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def reached(defect, kind, label, members):
    """Does THIS finding, on its own evidence, reach THIS defect?

    Replaces a scorer that joined every finding into one string and asked
    whether 30% of a defect title's words appeared anywhere in it. That version
    had no attribution and no precision term, and it got easier to satisfy as
    the tool emitted more candidates: a bag of the document's own nouns,
    representing no findings at all, scored 6/6 against this answer key. The
    repo already rejected the same shape once — score-claims.py records a fuzzy
    matcher that "reported 200% precision" and was deleted, because a scorer
    loose enough to flatter the tool measures nothing.

    So: the class must match, and the finding's own evidence must land on the
    defect — inside its recorded line span, or quoting its recorded anchor. A
    defect with neither is not auto-scorable, and saying so beats guessing.
    """
    if defect.get("class") != kind:
        return False

    span = defect.get("lines")
    if span and isinstance(span, list) and len(span) == 2:
        for member in members:
            for number in re.findall(r"\d+", str(member.get("_locator", ""))):
                if span[0] <= int(number) <= span[1]:
                    return True

    anchor = _flat(defect.get("anchor") or "")
    if anchor:
        # Longest CONTIGUOUS run shared with one finding's own text, as a
        # fraction of the anchor. Contiguity is what keeps this honest: a bag of
        # the document's words contains every word of the anchor and almost none
        # of its phrasing, so it scores near zero, while a real finding that
        # reformats the sentence — "assessed -> Section 9" for "assessed in
        # Section 9" — still shares most of it. A plain prefix test was tried
        # first and under-counted, missing two defects the pipeline had in fact
        # reported, because the finding label truncates and rewrites the middle.
        haystacks = [_flat(label)]
        for member in members:
            haystacks += [_flat(member.get(f, "")) for f in
                          ("quote", "claim", "name", "capability", "label")]
        for hay in haystacks:
            if not hay:
                continue
            match = difflib.SequenceMatcher(None, anchor, hay).find_longest_match(
                0, len(anchor), 0, len(hay))
            if match.size >= ANCHOR_COVERAGE * len(anchor):
                return True
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--inventory", default="inventory.json")
    parser.add_argument("--out",
                        help="write candidates as CSV in the shape "
                             "score-register.py reads, so the discovery "
                             "classes can be scored against a human register")
    parser.add_argument("--ground-truth", default=None)
    parser.add_argument("--show", type=int, default=6)
    parser.add_argument("--no-embed", action="store_true")
    parser.add_argument("--adjudicate", action="store_true",
                        help="ask the model about authority/deferral pairs. The "
                             "inventory has already reduced the pairwise problem "
                             "from thousands of claims to a couple of hundred "
                             "pairs, which is what makes it affordable.")
    parser.add_argument("--model", default="Qwen3-Coder-Next-UD-Q4_K_M")
    parser.add_argument("--url",
                        default=llm.DEFAULT_URL)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--components", default="components.yaml",
                        help="normalise owner strings onto the document's named "
                             "components; without it, 240 free-text owners make "
                             "the pair space unsearchable")
    # Four decisions below (DESIGN 3.24, 3.26, 3.28, 3.30) were each argued in
    # DESIGN with a number, and none of those numbers could be re-derived: the
    # scorer that produced them has been deleted for measuring nothing, and the
    # behaviour they describe was unconditional, so there was no second arm to
    # compare against. A claim with no way to switch it off is an assertion, not
    # a measurement. Each flag below restores the behaviour that preceded the
    # decision, so DESIGN can state what flipping it costs.
    parser.add_argument("--no-normalise", action="store_true",
                        help="DESIGN 3.24 off: compare owner strings as free "
                             "text instead of resolving them onto the "
                             "document's component vocabulary")
    parser.add_argument("--no-polarity", action="store_true",
                        help="DESIGN 3.26 off: count 'Does not own' entries as "
                             "ownership claims, which is what the pipeline did "
                             "before polarity was recorded")
    parser.add_argument("--object-identity", action="store_true",
                        help="DESIGN 3.28 off: a capability is its object "
                             "alone, so different verbs on one artefact are one "
                             "slot again")
    parser.add_argument("--authority-as-dataflow", action="store_true",
                        help="DESIGN 3.30 off: re-admit produces/consumes as "
                             "ownership claims, where data-flow sentences sat "
                             "before they were routed out of authority")
    parser.add_argument("--cap", type=int, default=900,
                        help="maximum pairs to adjudicate per class")
    parser.add_argument("--embed-url",
                        default=llm.DEFAULT_EMBED_URL)
    parser.add_argument("--embed-model", default="Qwen3-Embedding-8B-Q4_K_M")
    args = parser.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    data = json.load(open(os.path.join(project, args.inventory), encoding="utf-8"))
    sections = [s for s in data["sections"] if s and "error" not in s]
    # Needed by D6, and by --authority-as-dataflow to attribute a produces entry
    # to somebody, so it is resolved once here rather than at either use.
    owners_by_section = section_owner_map(sections)

    def flatten(key):
        out = []
        for section in sections:
            for entry in section.get(key, []):
                if isinstance(entry, dict):
                    item = dict(entry)
                else:
                    item = {"value": entry}
                item["_heading"] = section.get("heading", "")
                item["_locator"] = section.get("locator", "")
                out.append(item)
        return out

    authority_all = flatten("authority")
    # A component under "Does not own" is DISCLAIMING the capability. Counting a
    # disclaimer as a claim inverts the meaning and manufactures conflicts: on a
    # real architecture the strongest-looking dual binding was Population's
    # explicit exclusion of node_id creation, which Network legitimately owns.
    #
    # --no-polarity is the pipeline before that: both sub-lists flattened into
    # one pool, disclaimers indistinguishable from claims. The inventory still
    # records the field, so the ablation ignores it rather than re-extracting —
    # extraction is held constant across every arm or the comparison means
    # nothing.
    if args.no_polarity:
        authority, excluded_claims = list(authority_all), []
    else:
        authority = [e for e in authority_all
                     if (e.get("polarity") or "owns") != "excludes"]
        excluded_claims = [e for e in authority_all
                           if e.get("polarity") == "excludes"]
    defers = flatten("defers_to")
    produces = flatten("produces")
    consumes = flatten("consumes")
    claims = flatten("evidence_claims")

    # --authority-as-dataflow. The 3.30 fix itself lives in the extraction
    # prompt — "X returns Y" goes to produces/consumes, not to authority — and
    # that file is held constant here, so the restoration has to happen
    # downstream: fold produces/consumes back into the ownership pool, which is
    # where a data-flow sentence sat before the split.
    #
    # One thing genuinely cannot be restored. The old extractor read the owner
    # off the sentence; produces/consumes are bare noun phrases with no owner at
    # all, so the component the section is about is the only attribution left.
    # Entries in a section that names no component are dropped rather than given
    # an empty owner, which would collide with every other empty one and invent
    # conflicts this flag is not meant to test. Produces/consumes are left in
    # place for D8: 3.30 moved data flow OUT of authority, it did not move the
    # inputs and outputs list anywhere.
    folded = []
    if args.authority_as_dataflow:
        for verb, entries in (("produces", produces), ("consumes", consumes)):
            for entry in entries:
                who = defer_source(entry, owners_by_section)
                value = (entry.get("value") or entry.get("name") or "").strip()
                if not who or not value:
                    continue
                item = dict(entry)
                item.update({"capability": value, "action": verb, "owner": who,
                             "polarity": "owns", "_dataflow": True})
                folded.append(item)
        authority = authority + folded

    ablations = [name for name, on in (
        ("--no-normalise", args.no_normalise),
        ("--no-polarity", args.no_polarity),
        ("--object-identity", args.object_identity),
        ("--authority-as-dataflow", args.authority_as_dataflow)) if on]
    if ablations:
        print("ablation:  " + "  ".join(ablations) +
              "\n           this run restores older behaviour and is NOT the "
              "shipped configuration")
    if args.no_polarity:
        disclaimers = sum(1 for e in authority_all
                          if e.get("polarity") == "excludes")
        print(f"           {disclaimers} explicit exclusions counted AS "
              f"ownership claims")
    elif excluded_claims:
        print(f"           {len(excluded_claims)} explicit exclusions dropped "
              f"from the ownership comparison")
    if folded:
        print(f"           {len(folded)} produces/consumes entries re-admitted "
              f"as ownership claims")
    print(f"inventory: {len(sections)} sections, {len(authority)} authority "
          f"assertions, {len(defers)} deferrals,\n"
          f"           {len(produces)} produces / {len(consumes)} consumes, "
          f"{len(claims)} evidence claims\n")

    embedder = None
    if not args.no_embed:
        embedder = Embedder(project, model=args.embed_model, url=args.embed_url)
        embedder.preflight()
        if not embedder.available():
            print("embedding endpoint unavailable — falling back to token overlap",
                  file=sys.stderr)
            embedder = None

    # --no-normalise is simply not having the vocabulary. Without it the owner
    # key falls back to the free text the document happens to use, which is the
    # state DESIGN 3.24 was written against.
    comps, excluded = ({}, set()) if args.no_normalise else \
        load_components(os.path.join(project, args.components))
    if comps:
        kept = 0
        for entry in authority:
            entry["_component"] = to_component(entry.get("owner", ""),
                                               comps, excluded)
            kept += entry["_component"] is not None
        print(f"components: {len(comps)} named, {kept}/{len(authority)} authority "
              f"assertions resolve to one\n")

    findings = []

    # -- D5: two COMPONENTS asserting the same RIGHT over the same thing -----
    #
    # Grouping by the object alone treats complementary roles as competing:
    # "Reporting produces the RunSummary" and "operations evaluates the
    # RunSummary" is a handoff, not a dispute. So a conflict needs the same
    # object AND corresponding actions — using the same similarity threshold as
    # the object grouping rather than a second tuned one.
    owner_key = "_component" if comps else "owner"
    for group in group_by_concept(authority, "capability", embedder):
        # --object-identity collapses the action grouping to a single bucket, so
        # a capability is its object again. This also readmits entries the
        # action grouping drops on the floor — group_by_concept keeps only
        # members whose key field has words in it, and an authority entry with
        # no action verb has none — which is part of the older behaviour rather
        # than a side effect of the flag.
        subgroups = ([{"members": group["members"], "label": ""}]
                     if args.object_identity
                     else group_by_concept(group["members"], "action", embedder))
        for sub in subgroups:
            owners = {}
            for entry in sub["members"]:
                who = entry.get(owner_key) if comps else norm(entry.get("owner", ""))
                if comps and not who:
                    continue
                owners.setdefault(who, entry)
            if len(owners) > 1:
                label = f"{group['label']}"
                if sub.get("label"):
                    label += f"  [{sub['label']}]"
                findings.append(("D5", label, list(owners.values())))

    # -- D6: a capability every component passes on, owned by none -----------
    #
    # Two shapes. A CYCLE is the strong one: Sensor Fabric defers adjudication to
    # Hydrology, Hydrology defers it to Alerting, Alerting hands it back to
    # Sensor Fabric. Each section is locally reasonable and the capability has no
    # owner anywhere. Detecting it needs the source of each deferral, which is
    # the component the section is about, not anything the deferral records.
    #
    # The weak shape is deferred-and-never-owned, kept as a fallback for when
    # only part of a chain was captured.
    owned_vectors = None
    if embedder is not None and authority:
        owned_vectors = embedder.embed([e.get("capability", "") for e in authority])

    for group in group_by_concept(defers, "capability", embedder):
        edges, labelled = [], []
        for entry in group["members"]:
            source = defer_source(entry, owners_by_section)
            target = (entry.get("to") or "").strip()
            if source and target:
                edges.append((norm(source), norm(target)))
                labelled.append(entry)
        cycles = find_cycles(edges) if len(edges) > 1 else []
        if cycles:
            names = " -> ".join(" ".join(sorted(n)) for n in cycles[0])
            findings.append(("D6", f"{group['label']}  [cycle: {names}]",
                             labelled))
            continue
        if embedder is not None and owned_vectors:
            query = embedder.embed([group["label"]])[0]
            if any(cosine(query, v) >= 0.62 for v in owned_vectors):
                continue
        elif any(similar(group["tokens"], norm(e.get("capability", "")))
                 for e in authority):
            continue
        findings.append(("D6", group["label"], group["members"]))

    # -- D8: produced and never consumed -------------------------------------
    consumed = [norm(e.get("value", "")) for e in consumes]
    for group in group_by_concept(produces, "value", embedder):
        if not any(similar(group["tokens"], c, 0.6) for c in consumed if c):
            findings.append(("D8", group["label"], group["members"][:2]))

    # -- D3: evidence claim pointing somewhere that holds no such thing ------
    by_heading = {}
    for section in sections:
        key = re.sub(r"\s+", " ", section.get("heading", "")).lower()
        by_heading[key] = section
    # "Closure evidence: Load test report" is a deliverable still to be
    # produced, not an assertion that something is already recorded in the
    # document. Only pointers at a place inside this document are self-claims.
    LOCATION = re.compile(r"\b(section|appendix|annex|table|figure|chapter|"
                          r"clause|paragraph|\u00a7)\b", re.I)
    # "this section", "the section above" point at the passage making the claim,
    # not elsewhere. They cannot be an untrue cross-reference.
    SELF_REF = re.compile(r"\b(this|the (same|above|following|preceding)|here)\b",
                          re.I)
    # Traceability boilerplate. A claim reading "Requirement R-050 is addressed
    # in Section 21" has nothing to verify once the identifier and the pointer
    # are removed: whether the requirement is genuinely discharged is the
    # coverage pipeline's question, and matching "requirement addressed" against
    # a section's capability names is noise. Ten of nineteen candidates on the
    # fixture were this shape.
    BOILERPLATE = {"requirement", "requirements", "addressed", "covered",
                   "satisfied", "section", "sections", "appendix", "annex",
                   "described", "detailed", "provided", "given", "listed",
                   "shown", "documented", "stated", "above", "below", "this"}
    IDENTIFIER = re.compile(r"^[A-Z]{1,4}[-_]?\d{1,4}$|^\d+(\.\d+)*$")

    def content_words(text):
        out = set()
        for word in re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", text or ""):
            if word.lower() in BOILERPLATE or IDENTIFIER.match(word):
                continue
            out.add(word.lower())
        return out

    seen_d3 = set()
    for claim in claims:
        target = (claim.get("points_to") or "").strip()
        if not target or not LOCATION.search(target):
            continue
        if SELF_REF.search(target) and not re.search(r"\d", target):
            continue
        # A claim must carry something checkable. Two content words is the
        # least that can distinguish "maturity is assessed" from "is addressed".
        if len(content_words(claim.get("claim", ""))) < 2:
            continue
        # One pointer can name several places — "Section 14 and Appendix F".
        # The claim is only untrue if NONE of them holds the content.
        numbers = re.findall(r"(\d+(?:\.\d+)*)", target)
        appendices = re.findall(r"appendix\s+([a-z])\b", target, re.I)
        hits = []
        for key, section in by_heading.items():
            if any(f"appendix {letter.lower()}" in key for letter in appendices):
                hits.append(section)
            elif any(re.match(rf"^{re.escape(n)}\b", key) for n in numbers):
                hits.append(section)
        # Deduplicate on what the finding actually is: this claim, these places.
        fingerprint = (" ".join(sorted(content_words(claim.get("claim", "")))),
                       tuple(sorted(numbers)), tuple(sorted(appendices)))
        if fingerprint in seen_d3:
            continue
        seen_d3.add(fingerprint)
        if not hits:
            findings.append(("D3", f"{target} — no such section in the document",
                             [claim]))
            continue
        wanted = norm(claim.get("claim", ""))
        present = set()
        for section in hits:
            for capability in section.get("capabilities", []):
                present |= norm(capability.get("name", ""))
        if wanted and not similar(wanted, present, 0.34):
            findings.append(("D3", f"{claim.get('claim','')[:50]} -> {target}",
                             [claim]))

    if args.adjudicate:
        client = Client(project, model=args.model, url=args.url,
                        prompt_version="pair-1", quiet=args.concurrency > 1)
        auth_groups = group_by_concept(authority, "capability", embedder)
        count, conflicts = adjudicate_pairs(client, authority, "capability",
                                            args.concurrency, auth_groups,
                                            args.cap)
        print(f"adjudicated {count} authority pairs -> {len(conflicts)} conflicts")
        for a, b, reply in conflicts:
            findings.append(("D5", f"{a.get('capability','')} vs "
                                   f"{b.get('capability','')}", [a, b]))
        defer_groups = group_by_concept(defers, "capability", embedder)
        dcount, dconf = adjudicate_pairs(client, defers, "capability",
                                         args.concurrency, defer_groups,
                                         args.cap)
        print(f"adjudicated {dcount} deferral pairs -> {len(dconf)} same-slot "
              f"chains")
        for a, b, reply in dconf:
            findings.append(("D6", f"{a.get('capability','')} deferred to "
                                   f"{a.get('to','')}, and to {b.get('to','')}",
                             [a, b]))
        print(f"{client.summary()}\n")

    order = {"D5": 0, "D6": 1, "D8": 2, "D3": 3}
    findings.sort(key=lambda f: order.get(f[0], 9))
    counts = defaultdict(int)
    for kind, _, _ in findings:
        counts[kind] += 1
    print(f"-- candidate defects: " +
          ", ".join(f"{k}={counts[k]}" for k in sorted(counts)) + "\n")

    shown = defaultdict(int)
    for kind, label, members in findings:
        if shown[kind] >= args.show:
            continue
        shown[kind] += 1
        print(f"[{kind}] {label[:76]}")
        for entry in members[:3]:
            who = entry.get("owner") or entry.get("to") or entry.get("points_to") or ""
            print(f"      {entry.get('_locator',''):22} {entry.get('_heading','')[:30]:32}"
                  f" {who[:26]}")

    if args.out:
        import csv as _csv
        out_path = os.path.join(project, args.out)
        with open(out_path, "w", newline="", encoding="utf-8") as handle:
            writer = _csv.writer(handle)
            writer.writerow(["obligation", "source_ref", "modality",
                             "requirement", "verdict", "quote", "locator",
                             "reason", "requirement_locator"])
            for position, (kind, label, members) in enumerate(findings, 1):
                first = members[0] if members else {}
                # Every locator the finding rests on, so a reader can go
                # straight to the passages rather than back to the tool.
                where = " ".join(
                    str(m.get("_locator", "")) for m in members[:4] if m.get("_locator"))
                quote = str(first.get("quote", "") or first.get("claim", "") or "")
                writer.writerow([
                    # "unmet" is score-register.py's vocabulary for a flagged
                    # row; the discovery class is carried in source_ref so the
                    # per-class breakdown still works.
                    f"{kind}-{position:03d}", kind, "",
                    label, "unmet", quote, where,
                    " | ".join(filter(None, [
                        str(m.get("_heading", "")) for m in members[:4]])),
                    ""])
        print(f"\nwrote {out_path} ({len(findings)} candidates)")

    if args.ground_truth:
        try:
            import yaml
        except ImportError:
            return 0
        gt = yaml.safe_load(open(os.path.join(project, args.ground_truth),
                                 encoding="utf-8"))
        wanted = [d for d in gt["defects"]
                  if d["class"] in ("D3", "D5", "D6", "D8")]
        print(f"\n-- against ground truth ({len(wanted)} planted "
              f"D3/D5/D6/D8 defects) --")
        # One finding satisfies at most one defect, and one defect is satisfied
        # by at most one finding — without that, a single broad candidate can
        # claim the whole answer key.
        claimed, hit, unscorable = set(), 0, 0
        for defect in wanted:
            if not defect.get("anchor") and not defect.get("lines"):
                unscorable += 1
                print(f"  n/a   {defect['id']:11} {defect['class']}  "
                      f"{defect.get('title','')[:52]}  (no anchor or line span)")
                continue
            match = next(
                (i for i, (kind, label, members) in enumerate(findings)
                 if i not in claimed and reached(defect, kind, label, members)),
                None)
            if match is not None:
                claimed.add(match)
                hit += 1
            print(f"  {'FOUND' if match is not None else 'miss '} "
                  f"{defect['id']:11} {defect['class']}  "
                  f"{defect.get('title','')[:52]}")

        scorable = len(wanted) - unscorable
        print(f"\n  recall     {hit}/{scorable} planted defects reached by a "
              f"finding's own evidence")
        if findings:
            print(f"  precision  {len(claimed)}/{len(findings)} candidates "
                  f"landed on a planted defect")
        if unscorable:
            print(f"  {unscorable} defect(s) not auto-scorable — no anchor, no "
                  f"line span; read them by hand")
    return 0


if __name__ == "__main__":
    sys.exit(main())
