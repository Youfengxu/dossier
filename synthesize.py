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

WHEN THE INVENTORY DID NOT READ 100% OF IT. That sentence is a premise, and for
a long time nothing checked it. A section whose extraction failed was dropped on
the way in, and a section read by the fallback schema arrived with five fields
nobody had asked it for. Three of the four classes then assert an absence over
what is left, so an unread section was not merely unreported: a pointer to it
became "no such section in the document", and whatever it owned or consumed
became an ownership gap and an orphan. The run exited 0 and the only trace was a
section count one lower than it should have been.

So the inventory line now says how many sections were read, names the ones that
were not, and every finding that asserts an absence one of those could answer is
marked `unverifiable` and says which. It is still listed: a reviewer is owed the
question, not a verdict the tool could not reach.

LINES IN NO SECTION are the same premise one level down. "74 of 74 sections
read" says nothing about a line that is in none of the 74, and until 2026-10-04
the splitter left such lines: a heading-like line replaced by the next, a
passage of sixty characters or fewer. inventory.py now checks for them and
writes them as `unread_lines`; an older inventory shows them only as gaps
between its sections' line numbers. Either way they are printed under the
inventory line, and an ownership gap or an orphan, which assert an absence over
every line of the document, is marked `unverifiable` over them. D3 asks the
same of the place a pointer names and of nothing else, as the next paragraphs
say: lines of that place which are in no chunk put the finding in doubt.

WHICH SECTIONS THE DOCUMENT HAS is not the inventory's to say either. It keeps
the first heading of each chunk, and a chunk also holds every heading that came
too soon to start one of its own, so D3 called a sub-section, a stub, and a
heading spelled "Section 3: ..." missing when each was in the document. D3 now
reads the headings from the frozen document beside the inventory, looks for
what a pointer claims in the chunks that cover the place's own lines, and
prints how many evidence claims it checked and why it did not check the rest.

D3 STANDS BEHIND A FINDING ONLY IN A DOCUMENT THAT MARKS ITS HEADINGS: a
Markdown source with "#" on two of them or more, and only for a pointer that is
its places and nothing else ("Section 14 and Appendix F", not "Tables 4 and 5
of the calibration section"). There it asserts two things and no others. "No
such section in the document": the document's own headings show it numbering
its sections the way the pointer does, no line of it opens with that number or
letter, and no heading has it anywhere in it. "Section N holds no such thing":
every place the pointer names was found under a heading that is the document's
own, or shown to be missing, and every line of what was found is in a chunk
that was read. Whatever falls short of that is listed as `unverifiable` with
the reason. Text extracted from a .docx or a .pdf does not say which of its
lines are headings. There D3 reads them off the numbering to decide where to
look, passes a claim it finds, and lists the rest as `unverifiable`, as it
does when the document could not be consulted at all.

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
import inventory                                             # noqa: E402
import llm                                                   # noqa: E402
from llm import Client, Embedder, LLMError, cosine        # noqa: E402
import vocabulary                                            # noqa: E402

# The words score-register.py reads a candidate's verdict in. `unverifiable` is
# off the scale on purpose: "could not check" is not a degree of unmet.
COVERAGE = vocabulary.load(name="coverage")
UNMET, UNVERIFIABLE = COVERAGE.scale[0], COVERAGE.unknown

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


UNJUDGED = object()         # a pair whose adjudication call failed


def adjudicate_pairs(client, entries, key, concurrency=1, groups=None,
                     cap=900):
    """Cross-section pairs with differing owners, reduced by clustering first.

    On a fixture with 20 authority assertions, every pair is affordable. On a
    real architecture with 788 it is 310,000 — so the candidate set is cut to
    pairs that fall inside the SAME embedding cluster, i.e. plausibly the same
    slot. That reintroduces a similarity assumption the fixture warned about,
    so the clusters are deliberately loose and every exact-string collision is
    admitted regardless of cluster.

    Returns (pairs attempted, pairs that could not be judged, conflicts). A call
    that fails has not said "no conflict". It used to come back as None, which
    is what a pair judged free of conflict comes back as, and the line printed
    afterwards counted it as adjudicated.
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
            return UNJUDGED
        return (a, b, reply) if reply.get("conflict") else None

    if concurrency > 1:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            results = list(pool.map(check, pairs))
    else:
        results = [check(p) for p in pairs]
    unjudged, conflicts = 0, []
    for result in results:
        if result is UNJUDGED:
            unjudged += 1
        elif result:
            conflicts.append(result)
    return len(pairs), unjudged, conflicts




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
        # A project with no components file is an ordinary project. One whose
        # file is there and went unread is a different run from the one that
        # was asked for, and it used to look the same.
        print(f"{os.path.basename(path)} is present and was NOT read: PyYAML "
              f"is not installed. Owners are compared as free text, which is "
              f"--no-normalise and not the shipped configuration.",
              file=sys.stderr)
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


def read_inventory(data):
    """(sections read, sections not read, sections read in part, short).

    Every entry of the inventory is a section of the document. One whose
    extraction failed carries "error" and nothing that was extracted. Those
    used to be filtered out here, and a section count one lower than it should
    have been was the only sign that anything had gone unread.

      unread   {"position", "of", "heading", "locator", "why"}. An inventory
               written before a failure kept its heading has "" for both, and
               its position is all that can be said about it. `why` is
               "extraction failed", or what inventory.py wrote when --limit
               stopped it before the section.
      partial  read by the fallback schema and by no full pass. What it says of
               capabilities, authority and deferrals is a reading. Its
               produces, consumes and evidence claims are empty because nothing
               asked for them.
      short    read in fewer passes than the inventory asked for.
    """
    entries, runs = data["sections"], data.get("runs")
    sections, unread, partial, short = [], [], [], []
    for position, entry in enumerate(entries, 1):
        if not entry or "error" in entry:
            entry = entry or {}
            unread.append({"position": position, "of": len(entries),
                           "heading": entry.get("heading") or "",
                           "locator": entry.get("locator") or "",
                           "why": (str(entry.get("error")) if entry.get("skipped")
                                   else "extraction failed")})
            continue
        sections.append(entry)
        if entry.get("degraded") and not fully_read(entry):
            partial.append(entry)
        if runs and entry.get("passes", runs) < runs:
            short.append(entry)
    return sections, unread, partial, short


# The five fields the fallback schema does not ask for.
UNASKED = ("identifiers", "deferred", "evidence_claims", "consumes", "produces")


def fully_read(entry):
    """Whether any pass read this section with the whole schema.

    An entry says so since inventory.py began recording `full_passes`. One
    written before that does not, and the fallback may or may not have been
    followed by a full pass that was merged in. If any field the fallback
    never asks for holds something, a full pass put it there. If all five are
    empty there is no sign of one, and none is assumed.
    """
    if "full_passes" in entry:
        return bool(entry["full_passes"])
    return any(entry.get(key) for key in UNASKED)


# How the lines in no section are known: from the inventory's own list, from
# the gaps between its sections' line numbers, or not at all.
RECORDED, BY_LOCATOR, NOT_KNOWN = "recorded", "by locator", "not known"


def line_numbers(value):
    """Whether this is a list of line numbers and nothing else: the only
    thing taken as an inventory's own account of the lines it left out.

    Entries that were not line numbers used to be dropped and what was left
    taken as the record, so ["21", "22"], [21.0, 22.0] and [[21, 22]] each
    read as "no line left out", and the absences below were asserted.
    """
    if not isinstance(value, list):
        return False
    return all(type(n) is int and n > 0 for n in value)


def lines_left_out(data):
    """(the lines of the document that are in no section, as runs
    [(first, last), ...]; how that is known: RECORDED, BY_LOCATOR, NOT_KNOWN).

    inventory.py checks its sections against the text and writes the lines no
    call was shown as `unread_lines`: [] when there are none. An inventory
    written before 2026-10-04 has no such list, and its splitter left lines
    out: a heading-like line replaced by the next, a passage of sixty
    characters or fewer. Those show as gaps between the sections' own line
    numbers, and that is all there is to go on here. It cannot tell a blank
    line from one that holds text, and it cannot see past the last section,
    where a short last section was dropped the same way.

    A locator that runs backwards names no lines, and with no locator that can
    be read there is nothing to find a gap between.
    """
    record = data.get("unread_lines")
    if line_numbers(record):
        return inventory.runs_of(record), RECORDED
    spans = []
    for entry in data.get("sections") or []:
        locator = entry.get("locator") if isinstance(entry, dict) else None
        found = re.search(r":(\d{1,9})-(\d{1,9})$", str(locator or ""))
        if found and int(found.group(1)) <= int(found.group(2)):
            spans.append((int(found.group(1)), int(found.group(2))))
    gaps, reached = [], 0
    for first, last in sorted(spans):
        if first > reached + 1:
            gaps.append((reached + 1, first - 1))
        reached = max(reached, last)
    return gaps, BY_LOCATOR if spans else NOT_KNOWN


def how_many(left_out):
    """How many lines these runs come to. A function and not a name in main():
    main() already counts the pairs it adjudicates under `count`, and a line
    count kept there was printed as that number once --adjudicate had run."""
    return sum(last - first + 1 for first, last in left_out)


def where_left_out(left_out, most):
    """The lines in no section as a reader is told them: the first `most`
    runs, and how many lines the rest of them come to."""
    rest = how_many(left_out[most:])
    return inventory.line_runs(left_out[:most]) + (
        f", and {rest} more" if rest else "")


def line_question(left_out, what):
    """Why an absence over the whole document cannot be asserted: lines of it
    are in no section, and no call was shown them. "" when none is."""
    if not left_out:
        return ""
    return f"a line that is in no section {what}: {where_left_out(left_out, 3)}"


def in_doubt(*questions):
    """The open questions behind one finding as one line, "" when there is
    none: sections not read and lines in no section are separate reasons, and
    a finding can have both."""
    return "; ".join(filter(None, questions))


def named(section):
    """A section as a reader can find it, whether or not it was read."""
    heading, locator = section.get("heading"), section.get("locator")
    if heading:
        return f"{heading} ({locator})" if locator else heading
    if locator:
        return f"the section at {locator}, which has no heading"
    if section.get("position"):
        return (f"section {section['position']} of {section['of']}, which the "
                f"inventory does not name")
    return "a section the inventory does not name"


def open_question(sections, what):
    """Why an absence cannot be asserted: the sections that could answer it.

    "" when there are none, and that is the whole difference between a finding
    and a question. A reviewer is owed the question either way, so the finding
    is still listed; it is listed as unverifiable, with this beside it.
    """
    if not sections:
        return ""
    names = "; ".join(named(section) for section in sections[:3])
    if len(sections) > 3:
        names += f"; and {len(sections) - 3} more"
    return f"{what}: {names}"


# -- D3: where a pointer points ----------------------------------------------
#
# "Recorded in Section 3.1" is checked two ways: is there such a place, and
# does it hold what the sentence says. The first is a question about the
# DOCUMENT. It used to be put to the inventory, which keeps the first heading
# of each chunk and nothing else. A sub-section folded into its parent's chunk,
# a heading spelled "Section 3: ...", a section too short to be given a chunk:
# each was reported as "no such section in the document", and each is in the
# document. So the document is asked, and the inventory only for what the
# place holds.
#
# WHAT D3 STANDS BEHIND. Two independent reviews of earlier versions of this
# each found a dozen ways to make it assert something false, and nearly all
# ran through one step: deciding which lines of a text are headings when the
# text does not say. A cell of a table reads "10 km", a step reads "1. Stop
# the pump", a tab lost on extraction leaves "3.1Drift limits", and every
# rule that tells those from a heading is wrong about some document. So the
# line is drawn where no rule is needed. D3 asserts a finding only in a
# document that marks its headings, which is a Markdown source with "#" on
# them. In any other text it reads the headings off the numbering to decide
# where to look, clears a claim it finds there, and lists the rest as
# unverifiable: questions for a reviewer, with what it looked at.
#
# A third review then found a dozen more on the Markdown side. They came down
# to four things, and each now puts a finding in doubt where it used to be
# asserted: a pointer with more in it than its places, where whose a number
# is has to be guessed (bare); a numbered heading that is not one of the
# document's own sections, such as a step under "## Operations" (apart); a
# line that may be the place and is not a heading (unlisted, renamed); and a
# heading that is not one, in a listing or in the front matter of the file
# (prose). A place is also taken wide. It runs to the next NUMBERED heading
# that is not inside it (inside), because taking in too little reports a
# claim that the place holds.

NUMBER = r"\d+(?:\.\d+)*"
SIGN = "\N{SECTION SIGN}"
# Whatever a range is written with: "2-4", and the same with a hyphen, a
# non-breaking hyphen, a figure dash, an en or em dash, a minus sign.
DASH = ("\N{HYPHEN}\N{NON-BREAKING HYPHEN}\N{FIGURE DASH}\N{EN DASH}"
        "\N{EM DASH}\N{MINUS SIGN}-")
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"    # "2…4", and so is "2..4"
YEAR = re.compile(r"(?:19|20)\d\d$")
# The words a numbered part of a document goes by. Not as the tail of another
# word: a river's cross-section 12 is not Section 12.
PART = (r"(?<![\w-])(?i:(?:sub-?)?sections?\b|sect?\b\.?|clauses?\b"
        r"|chapters?\b|chap\.|ch\.)")
TOKEN = re.compile("|".join((
    # R-012, IF-RO-001: an identifier, and its digits are nobody's section
    r"(?P<ident>\b[A-Z]{1,6}(?:[-_][A-Z]{1,6})*[-_]?\d+\b)",
    # A.1: a part of Appendix A, whatever word stands in front of it
    r"(?P<lettered>\b[A-Z]\.\d+(?:\.\d+)*)",
    rf"(?P<section>{PART}|{SIGN}+)",
    r"(?P<appendix>(?<![\w-])(?i:appendix|appendices)\b)",
    r"(?P<annex>(?<![\w-])(?i:annex|annexes)\b)",
    # Places D3 does not look up, and words that count something. The number
    # straight after one is its own: "Table 4" was looked up as section 4.
    r"(?P<other>(?<![\w-])(?i:tables?\b|figures?\b|figs?\b\.?|pages?\b|pp?\."
    r"|paragraphs?\b|paras?\b\.?|par\.))",
    r"(?P<count>(?<![\w-])(?i:lines?\b|rows?\b|items?\b|steps?\b|versions?\b"
    r"|revisions?\b|issues?\b|rev\.|v\.))",
    r"(?P<between>\b(?i:between)\b)",
    rf"(?P<range>\b(?i:up\s+to|through\s+to|to|through|thru|until|till)\b"
    rf"|[{ELLIPSIS}{DASH}]|\.{{2,}})",
    # "Section 2 onwards": more places than the pointer lists
    r"(?P<open>\b(?i:onwards?|et\s+seq|ff|following|subsequent|passim"
    r"|elsewhere|throughout|various|several|etc)\b)",
    rf"(?P<number>{NUMBER}[A-Za-z]?(?!\w))",
    r"(?P<letter>\b(?:[IVX]{2,5}|[A-Z])\b"
    r"|(?<=(?i:appendix)\s)[a-z]\b|(?<=(?i:annex)\s)[a-z]\b)",
    r"(?P<word>\w+)")))
# A place in this document, named by what it is. "Closure evidence: Load test
# report" is a deliverable still to be produced, not an assertion that
# something is already recorded here: only a pointer at a place inside this
# document is a self-claim.
LOCATION = re.compile(rf"{PART}|{SIGN}|(?<![\w-])(?i:appendix|appendices"
                      rf"|annex|annexes)\b")
# "this section", "the same section", "here" point at the passage making the
# claim, and "the above section", "the following section" at one beside it,
# which D3 does not go and find. Alone none of them is checked, and beside
# another place ("this section and Appendix A") the passage is one of the
# places.
OWN = re.compile(r"(?i)\b(?:this|the\s+(?:same|above|following|preceding))\s+"
                 r"(?:sub-?)?(?:section|clause|chapter|appendix|annex|paragraph"
                 r"|table|figure|passage|page)\b|\bhere(?:in)?\b")
# Traceability boilerplate. A claim reading "Requirement R-050 is addressed in
# Section 21" has nothing to verify once the identifier and the pointer are
# removed: whether the requirement is genuinely discharged is the coverage
# pipeline's question, and matching "requirement addressed" against a section's
# capability names is noise. Ten of nineteen candidates on the fixture were
# this shape. "through" and "across" joined the list when pointers to several
# sections began to be read: "Requirements R-001 through R-042 are addressed
# across Sections 2 to 13" is the same sentence about a range.
BOILERPLATE = {"requirement", "requirements", "addressed", "covered",
               "satisfied", "section", "sections", "appendix", "annex",
               "described", "detailed", "provided", "given", "listed",
               "shown", "documented", "stated", "above", "below", "this",
               "through", "across"}
IDENTIFIER = re.compile(r"^[A-Z]{1,4}[-_]?\d{1,4}$|^\d+(\.\d+)*$")


def content_words(text):
    out = set()
    for word in re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", text or ""):
        if word.lower() in BOILERPLATE or IDENTIFIER.match(word):
            continue
        out.add(word.lower())
    return out


def plain(name):
    """A number or a letter as it is compared: "03.1" is 3.1, "3.0" is 3, "B"
    is b."""
    parts = [part.lstrip("0") or "0" for part in name.lower().split(".")]
    while len(parts) > 1 and parts[-1] == "0":
        parts.pop()
    return ".".join(parts)


ROMAN = ((10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i"))


def roman(name):
    """The number a Roman numeral stands for, or 0."""
    total, rest = 0, name
    for value, letters in ROMAN:
        while rest.startswith(letters):
            total, rest = total + value, rest[len(letters):]
    return total if name and not rest and numeral(total) == name else 0


def numeral(number):
    out = ""
    for value, letters in ROMAN:
        while number >= value:
            out, number = out + letters, number - value
    return out


def run_of(first, last):
    """What a range names, or [] when that cannot be told. "5" to "8": 5 6 7
    8. "5.2" to "5.4": 5.2 5.3 5.4. "4a" to "4c": 4a 4b 4c. "a" to "c": a b c.
    "i" to "iii": i ii iii. "3.1" to "4.2" crosses from one parent to the
    next, and is taken as the whole of 3 and the whole of 4, which hold it.
    "i" to "v" is five appendices, or the letters from I to V, and is taken as
    both."""
    if first[:1].isalpha() or last[:1].isalpha():
        out = []
        if len(first) == len(last) == 1 and first < last:
            out += [chr(n) for n in range(ord(first), ord(last) + 1)]
        low, high = roman(first), roman(last)
        if 0 < low < high:
            out += [numeral(n) for n in range(low, high + 1)]
        return list(dict.fromkeys(out))
    a, b = first.split("."), last.split(".")
    if a[:-1] != b[:-1]:
        a, b = a[:1], b[:1]
    ends = [re.fullmatch(r"(\d+)([a-z])", part) for part in (a[-1], b[-1])]
    if all(ends) and ends[0].group(1) == ends[1].group(1) \
            and ends[0].group(2) < ends[1].group(2):
        return [".".join(a[:-1] + [ends[0].group(1) + chr(n)]) for n in
                range(ord(ends[0].group(2)), ord(ends[1].group(2)) + 1)]
    if not (a[-1].isdigit() and b[-1].isdigit()) \
            or max(len(a[-1]), len(b[-1])) > 4:
        return []                   # "4a" to "7", or a number no section has
    low, high = int(a[-1]), int(b[-1])
    if not low <= high <= low + 200:
        return []                   # not a run that can be filled in
    return [".".join(a[:-1] + [str(n)]) for n in range(low, high + 1)]


def places(target):
    """What a pointer names: ([(kind, id), ...], whether it names a table, a
    figure, a page or a paragraph, whether it names more than it lists).

    kind is "section", "appendix" or "annex". Once a pointer has said
    "Section", every number after it is a section's, through commas, "and",
    brackets and whatever words stand between. The one exception is the
    number straight after a word for another kind of thing: the 4 of "Table
    4", the 12 of "page 12", the 3 of "step 3". Reading too few is the worse
    mistake. "Sections 5, 6, and 7" read as 5 and 6 reports a claim that
    Section 7 holds, so a number is given up only to the word that owns it.

    "Section 2 onwards" names more sections than it lists, and so does a range
    that cannot be filled in ("Sections 7 to 3", "4a to 7"). The third value
    says so: what such a pointer does not hold cannot be told from its list.

    The test for a place used to be the whole word `section`, which "Sections
    6.4, 7.4" does not contain, so a pointer to several sections was skipped
    as pointing outside the document.
    """
    tokens = [(found.lastgroup, found.group()) for found in TOKEN.finditer(target)]
    # A number in front of every place word is a section's if the pointer
    # names sections at all: "3.1 and Section 4".
    carrier = "section" if any(kind == "section" for kind, _ in tokens) else None
    keys, other, more = [], False, False
    last = start = None         # the last place named; where a range runs from
    owed = 0                    # 1: a table's number is due; 2: it has just come
    between = False             # "between Sections 2 and 4"
    named = False               # the token before this one was a place word
    for kind, text in tokens:
        after, named = named, kind in ("section", "appendix", "annex")
        if owed == 1 and kind == "number":
            owed = 2            # the table's own number, the page's, the step's
            continue
        if owed == 2 and kind == "range":
            owed = 1            # "Tables 4 to 6", "pp. 12-14": all of them
            continue
        owed = 0
        if kind in ("section", "appendix", "annex"):
            if kind != carrier:
                start = None        # no range runs from one kind into another
            carrier, last = kind, None
        elif kind in ("other", "count"):
            other, owed, last, start = other or kind == "other", 1, None, None
        elif kind == "between":
            between = True
        elif kind == "range" or (between and last and text.lower() == "and"):
            # "2--4" and "2 thru to 4" are one range written with two marks
            start, last, between = last or start, None, False
        elif kind == "open":
            more, last, start = True, None, None
        elif kind == "lettered":
            keys.append(("appendix", text[0].lower()))
            last = start = None
        else:
            name = None
            # "the 2024 roadmap" is not a section. "Section 2024" is.
            if kind == "number" and carrier and not (
                    carrier == "section" and YEAR.match(text) and not after):
                name = plain(text)
            elif kind == "letter" and carrier in ("appendix", "annex"):
                name = text.lower()
            if name is None:
                last = start = None         # a word: no range runs across it
                continue
            if start and keys[-1:] == [(carrier, start)]:
                keys.pop()      # the range says what its first end stood for
            run = run_of(start, name) if start else [name]
            # "5 to 2", "4a to 7": a range that cannot be filled in names
            # more than its two ends, whatever lies between them.
            more = more or not run
            keys += [(carrier, each) for each in run or [start, name]]
            last, start = name, None
    return list(dict.fromkeys(keys)), other, more


# What may stand between the places of a pointer that names nothing else.
JOINT = re.compile(r"""[\s,;&/()\[\].:'"]*""")


def bare(target):
    """Whether a pointer is its places and nothing else: the words for a
    place, numbers and letters, "and", "or", commas and ranges.

    "Section 14 and Appendix F" is. "Tables 4 and 5 of the calibration
    section", "Section 4.2 of the ICD", "Sections 2 up to and including 4"
    and "the calibration section (2 tables)" are not, and each was read wrong
    by a rule that guesses whose a number is. places() still reads them as
    far as it can, to know where to look. What is not found there is listed
    and not asserted.
    """
    at = 0
    for found in TOKEN.finditer(target):
        if not JOINT.fullmatch(target, at, found.start()) \
                or found.lastgroup in ("ident", "other", "count", "open") \
                or (found.lastgroup == "word"
                    and found.group().lower() not in ("and", "or")):
            return False
        at = found.end()
    return bool(JOINT.fullmatch(target, at))


LETTERED = re.compile(rf"(?i:(appendix|annex))\s+((?i:[a-z])(?![A-Za-z])"
                      rf"|[IVX]{{2,5}}(?![A-Za-z])|{NUMBER})")
NUMBERED = re.compile(rf"(?:(?i:section|clause|chapter)\s+)?({NUMBER})")
# What stands in front of a heading's number without being part of it: a list
# mark, a table bar, a quotation mark, asterisks, an anchor.
FRONT = re.compile(r"^(?:<[^>]*>|[\W_])+")


def leading(text):
    """The place a line opens with, and the rest of the line:
    (("section", "3.1"), " Drift limits"), or None.

    The number is taken whole. "3.1Drift limits", which is what a tab between
    them becomes on extraction, opens with 3.1 and not with 3. One letter
    after it is part of it when it stands alone ("4a.", and "4A." but not the
    3D of "3D flood twin design"), and a number that reads as a year is
    nobody's section.
    """
    found = LETTERED.match(text)
    if found:
        return ((found.group(1).lower(), plain(found.group(2))),
                text[found.end():])
    found = NUMBERED.match(text)
    if not found:
        return None
    number, rest = found.group(1), text[found.end():]
    if re.match(r"[a-z](?=[\s.:)]|$)|[A-Z](?=[.:)]|$)", rest):
        number, rest = number + rest[0], rest[1:]
    elif YEAR.match(number):
        return None
    return ("section", plain(number)), rest


def heading_key(text, strict=False):
    """What a heading says it is: ("section", "5.1"), ("appendix", "b"),
    ("annex", "3"), or None. Whatever marks stand in front are not part of it.

    `strict` is for a heading the document has marked as one. There the number
    has to end where a number ends: "3D flood twin design" is a title, not
    section 3. A line of extracted text is not held to that, because its
    tabs are gone.
    """
    found = leading(FRONT.sub("", str(text or "")))
    if not found or (strict and found[1] and found[1][0] not in " \t.:)]*_`<"
                     + DASH):
        return None
    return found[0]


def same(key, other):
    """Whether two keys name one place. An appendix numbered II is Appendix
    2, and one lettered I may be Appendix 1."""
    if key == other or not other or key[0] != other[0]:
        return key == other
    return key[0] != "section" and any(
        a.isdigit() and len(a) < 5 and roman(b) == int(a)
        for a, b in ((key[1], other[1]), (other[1], key[1])))


def under(key, other):
    """Whether `other` is `key` or a sub-section of it."""
    return same(key, other) or (
        bool(other) and key[0] == other[0] == "section"
        and other[1].startswith(key[1] + "."))


FENCE = re.compile(r"(\s*)(`{3,}|~{3,})(.*)$")
# Raw HTML that Markdown does not read into: from the line that opens with one
# of these tags to the line that closes it.
RAW = re.compile(r"(?i)\s*<(pre|script|style|textarea)\b")
# One to six "#", then a space, at most three spaces in: Markdown's own rule.
# "#1 priority" is not a heading, nor is "# restart the service" in an
# indented block of shell.
MARK = re.compile(r" {0,3}(#{1,6})(?:[ \t]+(.*))?$")


def prose(lines):
    """(number, line) for each line Markdown reads as the document's own: not
    in a fenced block, not between <pre> and </pre>, not in a comment that
    runs over several lines. "# install" there is a comment in somebody's
    shell, and "## 5. Deployment (old)" a heading the author took out.

    A fence is closed only by one of its own mark, at least as long, with
    nothing after it and set in no more than three spaces further: "```sh"
    inside "````markdown" closes nothing. And "```twin status``` shows the
    state" opens nothing: it is a code span. Where this is wrong about a
    fence it is wrong towards reading less, which finds fewer headings and so
    asserts less. Other raw HTML (a <div>, say) is read as Markdown.
    """
    fence = raw = ""            # the fence that is open; the tag that is
    depth, commented = 0, False     # how far in the open fence is set
    # The front matter of a file: from a first line of "---" to the next one,
    # or to "...". "# template version 3" in it is a comment in YAML, and as
    # a heading it stood where the title of the document is looked for.
    front = lines[:1] == ["---"] and next(
        (number for number, line in enumerate(lines[1:], 2)
         if line.rstrip() in ("---", "...")), 0)
    for number, line in enumerate(lines, 1):
        found = FENCE.match(line)
        opened = RAW.match(line)
        if number <= front:
            continue
        if commented:
            commented = "-->" not in line
        elif raw:
            raw = "" if f"</{raw}>" in line.lower() else raw
        elif fence:
            if found and found.group(2)[0] == fence[0] \
                    and len(found.group(2)) >= len(fence) \
                    and not found.group(3).strip() \
                    and len(found.group(1)) < depth + 4:
                fence = ""
        elif found and not (found.group(2)[0] == "`" and "`" in found.group(3)):
            fence, depth = found.group(2), len(found.group(1))
        elif opened and f"</{opened.group(1).lower()}>" not in line.lower():
            raw = opened.group(1).lower()
        elif line.lstrip().startswith("<!--") and "-->" not in line:
            commented = True
        else:
            yield number, line


def marks(lines):
    """The headings a Markdown document marks with "#": [(line, depth, title)].

    Markdown has another way, a rule of "=" or "-" under the line. That is
    not taken for a mark. A rule under the last item of a list is a rule, and
    one under "title: 1. design" closes the front matter of a file: telling
    those from a heading takes the kind of rule that asserted false things.
    Such a line is still one that opens with its number, which is enough to
    keep its section from being called missing.
    """
    found = []
    for number, line in prose(lines):
        mark = MARK.match(line.rstrip())
        if mark:
            title = re.sub(r"[ \t]+#+$", "", (mark.group(2) or "").strip())
            found.append((number, len(mark.group(1)), title))
    return found


def outline(lines, marked):
    """The places a document is divided into, each with the lines it runs
    over: [{"key", "text", "level", "line", "end"}].

    A document that marks its headings has those and no others: a numbered
    step in a list is not a section, though the splitter takes it for one and
    gives it a chunk. A place runs until the next heading that is not inside
    it, and inside() says which those are.

    Text that marks nothing has no headings to go by. Every line that opens
    with a section number and a word with a capital, or with an appendix
    letter, is taken as the start of something, and what it starts runs to
    the next such line that is not one of its own sub-sections. That takes
    "5 GHz" in a table for a section and stops a section at a numbered step,
    and it is why nothing found this way is asserted: it says where to look.
    """
    if marked:
        heads = [{"key": heading_key(title, strict=True), "text": title,
                  "level": level, "line": number}
                 for number, level, title in marks(lines)]
    else:
        heads = []
        for number, line in enumerate(lines, 1):
            found = leading(line.strip())
            # "3.1 Drift limits", "3.1Drift limits", "Appendix A". Not "3)",
            # which is a step; nor a bare "7", a cell or a page; nor "10 km"
            # and "Section 4 describes", which go on in a small letter.
            title = found[1].lstrip(" \t.:") if found else ""
            if found and (found[0][0] != "section" or (
                    title[:1].isalpha() and not title[:1].islower())):
                heads.append({"key": found[0], "text": line.strip(),
                              "level": 1, "line": number})
    above = []                  # the headings a heading stands under
    for position, head in enumerate(heads):
        head["end"] = next((later["line"] - 1 for later in heads[position + 1:]
                            if not inside(head, later, marked)), len(lines))
        above = [other for other in above if other["level"] < head["level"]]
        head["over"] = next((other for other in reversed(above)
                             if apart(other, head, heads[0])), None)
        above.append(head)
    return heads


def inside(head, later, marked):
    """Whether the heading `later` is still inside what `head` starts.

    Its sub-sections are, by number, at whatever depth they are marked: "5."
    and "5.1" are sometimes marked alike. So is every heading marked deeper.

    Where headings are marked, a numbered place ends at the next numbered
    heading that is neither of those, and at no other. A heading with no
    number does not end it, at any depth: "## Detailed design" after "## 3.
    Design" and "## A.1 Method" after "## Appendix A" say nothing of being
    another place, and "# restart the fabric" in a listing is no heading at
    all. Nor, under an appendix, does a numbered section at its own depth:
    numbering that starts again under "Appendix A" is the appendix's, which
    ends at the next appendix. Taking in too much looks for a claim in more
    lines than it need. Taking in too little reports a claim that the place
    holds.
    """
    if head["key"] and under(head["key"], later["key"]):
        return True
    if not (marked and head["key"]):
        return later["level"] > head["level"]
    if not later["key"] or later["level"] > head["level"]:
        return True
    return later["level"] == head["level"] and \
        head["key"][0] != "section" and later["key"][0] == "section"


def apart(other, head, title):
    """Whether a numbered section, standing under the heading `other`, is
    numbered apart from the document's sections.

    "### 2. Apply the migration" under "## Operations" is the second step of
    that heading, in a document whose own sections may have no numbers in the
    source at all, and "### 1. Method" under "## Appendix A" is the appendix's.
    A section stands under the document's title, and under the section whose
    number its own begins with, and under those it is the document's.
    """
    return bool(head["key"]) and head["key"][0] == "section" \
        and other is not title \
        and not (other["key"] and under(other["key"], head["key"]))


def located(key, heads):
    """The headings a place names: its own and its sub-sections'. A document
    can number 5.1 and 5.2 and never write a line for 5."""
    return [head for head in heads if head["key"] and under(key, head["key"])]


def lines_of(entry):
    """(first line, last line) of a chunk, from its locator "slug:a-b"."""
    found = re.search(r":(\d{1,9})-(\d{1,9})$", str(entry.get("locator", "")))
    return (int(found.group(1)), int(found.group(2))) if found else None


def moved(entries, lines):
    """How the text differs from the one the inventory was split from, by the
    first chunk that is not where its locator says, or "" when every chunk is.

    The splitter starts a chunk on its heading line, or carries the heading of
    the chunk before when it cut one for size. An entry with no heading (a
    failed section, as it was once recorded) says nothing, and the chunk after
    it may be a later piece of it. This sees a text that moved above the last
    chunk that starts on a heading, and not one that only changed below it.
    It refuses, on the cautious side, a text in which the splitter dropped
    the first piece of a section it cut for size.

    A bare "#" is a heading with nothing in it, and so is a blank line once
    its hashes are taken off. Such a chunk is tied by a line of hashes and by
    no other: compared as text alone, a line added above it went unseen.

    NOT SEEN, and not seeable from the entries: a chunk whose heading is the
    heading before it is taken for a later piece of that section and is not
    looked at. Two sections in a row under one heading (two tables that each
    open on "#", two "Notes") are told from one section cut in two by nothing
    an entry holds, so a line added between them moves the second and this
    returns "". An inventory that records the text's own hash is not checked
    here at all.
    """
    before, blind, tied = None, False, 0
    for entry in entries:
        heading = entry.get("heading") if isinstance(entry, dict) else None
        span = lines_of(entry) if isinstance(entry, dict) else None
        if heading is None or not span:
            blind = True
            continue
        if not 1 <= span[0] <= span[1] <= len(lines):
            return (f"the inventory has {heading!r} on lines {span[0]}-"
                    f"{span[1]}, and the text has {len(lines)} lines")
        if heading not in ("(front matter)", before):
            first = lines[span[0] - 1].strip()
            if first.lstrip("#").strip() == heading and (heading or first):
                tied += 1
            elif not blind:
                return (f"line {span[0]} is not the heading the inventory "
                        f"has there, {heading!r}")
        before, blind = heading, False
    if not tied:
        return ("no chunk of the inventory starts on a heading, so nothing "
                "ties its line numbers to this text")
    return ""


# freeze.py keeps a .md as it is, and puts a .markdown through an extractor
# that cannot read one.
MARKDOWN = ".md"


def consult(project, data):
    """(what the document says its sections are, None), or (None, why the
    document was not consulted).

    The document is the frozen text beside the inventory. Its line numbers
    have to be the inventory's, or a heading found at line 300 says nothing
    about the chunk the inventory has at 300. An inventory records the hash of
    the text it read. One written before it did records the hash of the
    SOURCE, which a re-freeze leaves unchanged while it moves every line
    (freeze.py says so when it happens), so for those each chunk's heading is
    looked for at the line its locator gives. Not where the text is the
    source itself, as a .md is: there the source's hash is the text's.

    Whether the document marks its headings is not read off its text. Two
    lines of shell in a .docx open with "# ", and taken for marks they left
    no real heading a heading. The manifest says what the source was: a
    Markdown file marks its headings if it marks two, and nothing else does.
    """
    slug = data.get("doc")
    if not slug:
        return None, "the inventory does not name the document it was built from"
    try:
        doc, lines = llm.load_doc(project, slug)
    except Exception as error:      # noqa: BLE001: whatever a manifest can do
        said = (str(error).strip() or type(error).__name__).splitlines()[0]
        return None, f"{slug} could not be read: {said}"
    changed = (f"the frozen text of {slug} is not the text this inventory was "
               f"built from")
    if data.get("text_sha256"):
        if data["text_sha256"] != doc.get("text_sha256"):
            return None, changed
    elif data.get("source_sha256"):
        if data["source_sha256"] != doc.get("source_sha256"):
            return None, changed
        # Where the text is the source, as it is for a .md, the source's hash
        # is the text's own and there is nothing more to ask.
        astray = data["source_sha256"] != doc.get("text_sha256") and \
            moved(data.get("sections") or [], lines)
        if astray:
            return None, f"{changed}: {astray}"
    else:
        return None, (f"the inventory records no hash of {slug}, so nothing "
                      f"says the frozen text is the one it was built from")
    marked = str(doc.get("path") or "").lower().endswith(MARKDOWN) and \
        sum(1 for _, _, title in marks(lines) if title) >= 2
    # Every line that opens the way a heading would, heading or not, behind
    # whatever a list, a table or a quotation puts in front of it. A section
    # is called missing only when no line at all opens with it.
    opens = {}
    for number, line in enumerate(lines, 1):
        key = heading_key(line)
        if key:
            opens.setdefault(key, number)
    heads = outline(lines, marked)
    return {"slug": slug, "marked": marked, "heads": heads, "opens": opens,
            "loose": unlisted(lines, heads), "lines": lines}, None


def unlisted(lines, heads):
    """{section: line} for each line that opens with a section's number, is
    not one of the headings, and is not an item of a list: blank and indented
    lines apart, neither the line before it nor the one after opens with a
    number too.

    "2. Hydrology Model" with a paragraph of its own under it is a heading in
    all but the mark, and so is the same line set in bold. A document whose
    sections are written that way may still mark two numbered steps with "#",
    and a pointer to Section 2 was looked for in the second step.
    """
    marked = {head["line"] for head in heads}
    keys = {number: heading_key(line) for number, line in enumerate(lines, 1)
            if number not in marked}
    flush = [number for number, line in enumerate(lines, 1)
             if line.strip() and not line[:1].isspace()]
    found = {}
    for position, number in enumerate(flush):
        key = keys.get(number)
        beside = flush[max(position - 1, 0):position] + flush[position + 1:position + 2]
        if key and key[0] == "section" and not any(
                (keys.get(other) or ("",))[0] == "section" for other in beside):
            found.setdefault(key, number)
    return found


KIND = {"section": "a numbered section", "appendix": "an appendix",
        "annex": "an annex"}
UNMARKED = ("the document does not mark its headings: which of its lines are "
            "headings is read off its numbering")
UNCONSULTED = ("the document was not consulted: only the chunks the "
               "inventory heads with that number were looked in")


def numbered(name, names):
    """Whether its headings show the document numbering its sections the way
    a pointer does: two sections numbered one after the other, or, for 3.7, a
    section 3 or another 3.x. One heading that happens to open with a number
    ("3 options considered") shows nothing."""
    tops = {int(other) for other in names if other.isdigit() and len(other) < 5}
    if any(n + 1 in tops for n in tops):
        return True
    stem = name.rpartition(".")[0]
    return bool(stem) and any(other == stem or other.startswith(stem + ".")
                              for other in names)


def spelled(key):
    return key[1] if key[0] == "section" else \
        f"{key[0].capitalize()} {key[1].upper()}"


def own(kind, heads):
    """The numbers, or the letters, of the document's own headings of one
    kind: not those of a section numbered apart from its sections."""
    return [head["key"][1] for head in heads
            if head["key"] and head["key"][0] == kind and not head["over"]]


# A number or a letter that stands on its own in a heading.
STANDING = re.compile(rf"(?<![\w.])(?:{NUMBER}[A-Za-z]?|[A-Z]|[IVX]{{2,5}})(?!\w)")


def renamed(key, heads):
    """Why a heading that is not headed with a place's number or letter may
    be that place all the same, or "": the 3 of "Part 3: Design" and of
    "Design (3)", the C of "C. Register of gauges" under "Appendices"."""
    for head in heads:
        if head["key"] and under(key, head["key"]):
            continue                    # headed with it: found, not renamed
        for token in STANDING.findall(head["text"]):
            if under(key, (key[0], plain(token))):
                return (f"the heading on line {head['line']} has {token} in "
                        f"it, and may be the place under another name")
    return ""


def unsettled(key, document):
    """Why a place with no heading of its own cannot be called missing, or ""
    when it can: the document marks its headings, has headings of that kind,
    numbered the way the pointer numbers them, no line of it opens with this
    one's number or letter, and no heading has it standing anywhere in it."""
    if not document["marked"]:
        return UNMARKED
    names = own(key[0], document["heads"])
    if not names:
        return f"none of the document's headings is {KIND[key[0]]}"
    if key[0] == "section" and not numbered(key[1], names):
        return ("the document's headings do not show it numbering its "
                "sections this way")
    for other, line in document["opens"].items():
        if under(key, other):
            return (f"line {line} of the document opens with "
                    f"{spelled(other)}, and is not one of its headings")
    return renamed(key, document["heads"])


def runs(numbers):
    """[3, 4, 5, 9] as "lines 3-5, 9"; [7] as "line 7"."""
    out, numbers = [], sorted(numbers)
    for number in numbers:
        if out and out[-1][1] == number - 1:
            out[-1][1] = number
        else:
            out.append([number, number])
    return ("line " if len(numbers) == 1 else "lines ") + ", ".join(
        str(a) if a == b else f"{a}-{b}" for a, b in out)


# Why a claim was not checked, in the order the reasons are tried.
NO_POINTER = "no pointer"
NO_PLACE = "a pointer that names no place in the document"
OWN_SECTION = "a pointer to the passage it is in or the one beside it"
NOT_A_SECTION = "a pointer to a table, figure, page or paragraph"
NO_NUMBER = "a pointer to a place without a number or a letter"
TOO_LITTLE = "a claim that says too little to check"
REPEAT = "a repeat of an earlier claim"
NOT_CHECKED = (NO_POINTER, NO_PLACE, OWN_SECTION, NOT_A_SECTION, NO_NUMBER,
               TOO_LITTLE, REPEAT)


def self_claims(claims, sections, unread, document):
    """D3: an evidence claim pointing somewhere that holds no such thing.
    -> ([finding, ...], {why a claim was not checked: how many}, how many
    claims were looked for in the place they point to).

    A claim is checked when its pointer names a section, an appendix or an
    annex by number or letter, and the claim has two words of content. Every
    other claim is counted under the reason it was left out, so that the
    report can say how many of the document's evidence claims D3 answers for.

    `document` is what consult() returned, or None. A finding is asserted
    only when it is there and marks its headings; `doubts` below is
    everything else that stands between a claim the place does not hold and
    saying so.
    """
    findings, skipped, seen, looked = [], defaultdict(int), set(), 0
    homes = {str(section.get("locator")): section for section in sections}
    if document:
        read = set()
        for section in sections:
            held = lines_of(section)
            if held:        # no further than the text: a locator can say anything
                read.update(range(held[0], min(held[1], len(document["lines"])) + 1))
        headings = {head["line"] for head in document["heads"]}
        names = own("section", document["heads"])
        known = {}      # each place, asked of the document once
    for claim in claims:
        # The extraction schema does not say the pointer must be one string,
        # and "Section 3 and Appendix B" has come back as a list of two.
        target = claim.get("points_to") or ""
        if isinstance(target, (list, tuple)):
            target = " and ".join(str(place) for place in target)
        target = str(target).strip()
        said = str(claim.get("claim") or "")
        # One pointer can name several places — "Section 14 and Appendix F".
        # The claim is only untrue if NONE of them holds the content.
        keys, other, more = places(target)
        # A claim must carry something checkable. Two content words is the
        # least that can distinguish "maturity is assessed" from "is addressed".
        words = content_words(said)
        # Deduplicate on what the finding actually is: this claim, these
        # places. "This section" is a different place wherever it is said.
        fingerprint = (" ".join(sorted(words)), tuple(sorted(keys)),
                       OWN.search(target) and str(claim.get("_locator")))
        if not target:
            why = NO_POINTER
        elif keys:
            why = TOO_LITTLE if len(words) < 2 else \
                REPEAT if fingerprint in seen else ""
        elif OWN.search(target):
            why = OWN_SECTION
        else:
            why = NOT_A_SECTION if other else \
                NO_NUMBER if LOCATION.search(target) else NO_PLACE
        if why:
            skipped[why] += 1
            continue
        seen.add(fingerprint)

        spans, doubts, hits = [], [], []
        if document:
            for key in keys:
                if key not in known:
                    heads = located(key, document["heads"])
                    known[key] = heads, "" if heads else unsettled(key, document)
                heads, why = known[key]
                # A sub-section that may be there without a heading of its
                # own (one set in bold, say) is somewhere in the section it is
                # part of. A claim found there is no finding, and one that is
                # not found there is still only a question.
                stem = key[1]
                while why and key[0] == "section" and "." in stem and not heads:
                    stem = stem.rpartition(".")[0]
                    heads = located(("section", stem), document["heads"])
                spans += [(head["line"], head["end"]) for head in heads]
                if why or not heads:
                    doubts.append(why)
                    continue
                if not document["marked"]:
                    doubts.append(UNMARKED)
                    continue
                if not any(same(key, head["key"]) for head in heads):
                    # Found by its sub-sections alone. A line that opens with
                    # its own number (a heading set in bold, say), or a
                    # heading that has the number in it ("Part 3: Design"),
                    # may be its start, and the text between that and 3.1 was
                    # looked at by nobody.
                    start = (f"a line of the document opens with {spelled(key)} "
                             f"and is not one of its headings"
                             if any(same(key, opened)
                                    for opened in document["opens"])
                             else renamed(key, document["heads"]))
                    doubts.append(start and f"{start}, so only its "
                                            f"sub-sections were looked in")
                elif key in document["loose"]:
                    doubts.append(
                        f"line {document['loose'][key]} of the document opens "
                        f"with {spelled(key)}, is not in a list and is not one "
                        f"of its headings: it may be the place the pointer "
                        f"means")
                if all(head["over"] for head in heads):
                    # "### 2. Apply the migration" under "## Operations": a
                    # step of that heading, which a pointer to Section 2 may
                    # not mean at all.
                    doubts.append(
                        f"{spelled(key)} is found only under the heading on "
                        f"line {heads[0]['over']['line']}, and may be numbered "
                        f"apart from the document's sections")
                if key[0] == "section" and "." not in key[1] \
                        and not numbered(key[1], names):
                    doubts.append("the document's headings do not show it "
                                  "numbering its sections this way")
            # What the place holds is what the inventory read on its lines.
            for section in sections:
                held = lines_of(section)
                if held and any(first <= held[1] and held[0] <= last
                                for first, last in spans):
                    hits.append(section)
            # An inventory cut before 2026-10-04 has lines in no chunk: a
            # heading-like line replaced by the next, a passage of sixty
            # characters or fewer. inventory.py no longer leaves them.
            unseen = {number for first, last in spans
                      for number in range(first, last + 1)
                      if number not in read and number not in headings
                      and document["lines"][number - 1].strip()}
            if hits and unseen:
                doubts.append(f"{runs(unseen)} of it "
                              f"{'is' if len(unseen) == 1 else 'are'} in no "
                              f"section of the inventory")
        else:
            hits = [section for section in sections
                    if any(under(key, heading_key(section.get("heading")))
                           for key in keys)]
            doubts.append(UNCONSULTED)
        if more:
            doubts.append("the pointer names more places than it lists")
        elif not bare(target):
            doubts.append("the pointer has more in it than the places it "
                          "names, and only those were read")
        doubt = "; ".join(dict.fromkeys(filter(None, doubts)))
        # "This section and Appendix A": the passage the claim is in is one
        # of the places, and the claim is not untrue if it is held there, or
        # further on in the section that passage is part of. Said in the
        # first lines of Section 3, "this section" takes in 3.1.
        home = homes.get(str(claim.get("_locator")))
        if home and OWN.search(target):
            at = lines_of(home) or (0, 0)
            part = [(head["line"], head["end"])
                    for head in (document["heads"] if document else ())
                    if head["key"] and head["line"] <= at[0] <= head["end"]][-1:]
            hits += [section for section in sections
                     if section not in hits and (section is home or any(
                         first <= (lines_of(section) or (0, 0))[1]
                         and (lines_of(section) or (0, 0))[0] <= last
                         for first, last in part))]
        looked += bool(hits)
        # With a section unread, neither form of this finding is asserted. No
        # unread section can be ruled out by its heading: the inventory keeps
        # the FIRST heading of each chunk, and a chunk also holds whatever
        # headings followed too soon to start one of their own, so an unread
        # "3. Calibration Register" may hold 3.1, a table can sit in any
        # section, and a heading may be spelled "Section 3: ...". An earlier
        # version compared the pointer with each unread heading and let the
        # finding stand where they differed; a review broke that seven ways.
        if not hits and unread:
            # This used to read "no such section in the document", about a
            # section that is in the document and failed at extraction.
            findings.append(("D3", f"{target} — not among the sections that "
                                   f"were read", [claim], open_question(
                unread, "a section that was not read could be, or could hold, "
                        "the place it points to")))
            continue
        if not hits and not document:
            findings.append(("D3", f"{target} — not among the headings the "
                                   f"inventory records", [claim],
                             "the document was not consulted, and the "
                             "inventory records the first heading of each "
                             "chunk only"))
            continue
        if not hits and spans:
            # A heading with a line or two under it was too short for the
            # splitter to make a chunk of until 2026-10-04, and an inventory
            # cut before then never saw it.
            where = runs({number for first, last in spans
                          for number in range(first, last + 1)})
            findings.append(("D3", f"{target} — in the document, and not in "
                                   f"the inventory", [claim],
                             f"the inventory has no section on {where}, "
                             f"where the document has it"))
            continue
        if not hits and doubt:
            findings.append(("D3", f"{target} — not among the document's "
                                   f"headings", [claim], doubt))
            continue
        if not hits:
            findings.append(("D3", f"{target} — no such section in the document",
                             [claim], ""))
            continue
        wanted = norm(said)
        present = set()
        for section in hits:
            held = section.get("capabilities")
            for capability in held if isinstance(held, list) else []:
                # The fallback's validator accepts a capability as a bare
                # string, where the full schema requires {"name": ...}.
                present |= norm(str(capability.get("name") or "")
                                if isinstance(capability, dict) else str(capability))
        if wanted and not similar(wanted, present, 0.34):
            findings.append(("D3", f"{said[:50]} -> {target}",
                             [claim], open_question(
                unread, "a section that was not read could hold it, as part "
                        "of the place the pointer names") or doubt))
    return findings, skipped, looked


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
    with open(os.path.join(project, args.inventory), encoding="utf-8") as handle:
        data = json.load(handle)
    sections, unread, partial, short = read_inventory(data)
    no_section, line_record = lines_left_out(data)
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
    print(f"inventory: {len(sections)} of {len(data['sections'])} sections "
          f"read, {len(authority)} authority assertions, {len(defers)} "
          f"deferrals,\n"
          f"           {len(produces)} produces / {len(consumes)} consumes, "
          f"{len(claims)} evidence claims")
    # What the counts above do not rest on. Said here, before any finding,
    # because every absence below is an absence in what WAS read.
    pad = " " * 11

    def listed(sections, note):
        # The count above each list is exact. The list itself stops at twelve:
        # a quarter of a real document has failed at this stage before, and
        # seventy names here would bury the findings they qualify.
        for section in sections[:12]:
            print(f"{pad}    {named(section)}{note(section)}")
        if len(sections) > 12:
            print(f"{pad}    and {len(sections) - 12} more, all of them in "
                  f"{args.inventory}")

    if unread:
        print(f"{pad}NOT READ: {len(unread)} section(s)")
        listed(unread, lambda s: f"  [{s['why']}]")
    if partial:
        print(f"{pad}READ IN PART, by the fallback schema only, which asks for "
              f"no produces,\n{pad}consumes or evidence claims: {len(partial)} "
              f"section(s)")
        listed(partial, lambda s: "")
    if short:
        # Counted and named, and no finding is put in doubt by it: every
        # extraction is a sample (DESIGN 3.27), and this one is a smaller one.
        print(f"{pad}READ IN FEWER than the {data['runs']} passes the "
              f"inventory asked for: {len(short)} section(s)")
        listed(short, lambda s: f"  [{s.get('passes')} of {data['runs']}]")
    # A line in no section was shown to no call. "N of N sections read" says
    # nothing about it, and said nothing for as long as the splitter left
    # lines out: the inventory DESIGN 3.24-3.30 are measured on has two.
    if "unread_lines" in data and line_record != RECORDED:
        print(f"{pad}The inventory's `unread_lines` is not a list of line "
              f"numbers, and is not\n{pad}taken as its account of the lines it "
              f"left out.")
    if no_section and line_record == RECORDED:
        print(f"{pad}NOT IN ANY SECTION: {how_many(no_section)} line(s) that "
              f"hold text, and no call was shown\n{pad}them: "
              f"{where_left_out(no_section, 12)}")
    elif no_section:
        print(f"{pad}NOT IN ANY SECTION: {how_many(no_section)} line(s), going "
              f"by the sections' own line\n{pad}numbers: "
              f"{where_left_out(no_section, 12)}\n{pad}The inventory does not "
              f"record the lines it left out, so whether\n{pad}these hold text "
              f"is not known here, nor whether the document runs\n{pad}on past "
              f"its last section.")
    elif line_record == RECORDED:
        print(f"{pad}no line that holds text is outside those "
              f"{len(data['sections'])} sections")
    elif line_record == BY_LOCATOR:
        print(f"{pad}The inventory does not record the lines it left out. "
              f"Going by its\n{pad}sections' line numbers there is none before "
              f"the last of them;\n{pad}whether the document runs on past that "
              f"is not known here.")
    else:
        print(f"{pad}The inventory does not record the lines it left out, "
              f"and none of its\n{pad}sections says which lines it holds: "
              f"whether any line of the document\n{pad}is in no section is "
              f"not known here.")
    if unread or partial:
        print(f"{pad}A finding that asserts an absence one of the sections not "
              f"read, or read in\n{pad}part, could answer is marked "
              f"{UNVERIFIABLE} below, and says which.")
    if no_section:
        # Said by what a finding rests on and not by its class: a D6 that
        # names a cycle, or two deferrals a model judged to be one slot, is
        # something found and is not marked. Nor is D3 marked by this. Its
        # absence is about the place a pointer names: it reads that place's
        # own lines (below), and says under the finding when some of them
        # are in no section.
        print(f"{pad}A finding that rests on nothing owning a capability (D6) "
              f"or nothing\n{pad}consuming an output (D8) asserts an absence "
              f"over every line of the\n{pad}document, and is marked "
              f"{UNVERIFIABLE} below. A pointer (D3) is put in\n{pad}doubt by "
              f"such a line only where it is a line of the place it names.")
    # D3 is run here, ahead of the other classes, because what it rests on
    # belongs with the lines above: which document told it where the sections
    # are, and how many of the evidence claims it answers for. It checked 9 of
    # the fixture's 122 and said nothing about the other 113.
    document, unconsulted = consult(project, data)
    if document and document["marked"]:
        kinds = defaultdict(int)
        for head in document["heads"]:
            kinds[head["key"][0] if head["key"] else ""] += 1
        print(f"document:  {document['slug']}, {len(document['heads'])} "
              f"headings (" + (", ".join(
                  f"{name}: {kinds[kind]}" for kind, name in (
                      ("section", "numbered sections"),
                      ("appendix", "appendices"), ("annex", "annexes"))
                  if kinds[kind]) or "none numbered or lettered") + ")")
    elif document:
        # Text from a .docx or a .pdf, or Markdown with fewer than two
        # headings marked "#".
        print(f"document:  {document['slug']} DOES NOT MARK ITS HEADINGS. D3 "
              f"takes each line that opens with\n{pad}a section number or an "
              f"appendix letter as the start of one, to decide\n{pad}where to "
              f"look, and asserts nothing found that way: each D3 finding\n"
              f"{pad}is listed as {UNVERIFIABLE}.")
    else:
        print(f"document:  NOT CONSULTED: {unconsulted.rstrip('.')}.\n{pad}D3 "
              f"looks each pointer up among the headings the inventory "
              f"records, the first\n{pad}of each chunk, and asserts nothing "
              f"found that way: each D3 finding is listed\n{pad}as "
              f"{UNVERIFIABLE}.")
    untrue, unchecked, looked = self_claims(claims, sections, unread, document)
    left_out = sum(unchecked.values())
    print(f"claims:    D3 checked {looked} of {len(claims)} evidence claims "
          f"against the place each points to")
    if len(claims) - left_out - looked:
        # Not "checked": there was nothing to check them against. Each is a
        # D3 finding, a missing section or a place that could not be found.
        print(f"{pad}no place to check against: "
              f"{len(claims) - left_out - looked}, each a D3 finding")
    if left_out:
        print(f"{pad}not checked: {left_out}")
        for why in NOT_CHECKED:
            if unchecked[why]:
                print(f"{pad}    {why}: {unchecked[why]}")
    print()

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

    # (class, label, the entries it rests on, doubt). `doubt` is "" for a
    # finding the inventory establishes: two owners found, a cycle found, or
    # an absence in an inventory that read every section the absence is about.
    # It is the open question otherwise, and the finding is then unverifiable.
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
                findings.append(("D5", label, list(owners.values()), ""))

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
                             labelled, ""))
            continue
        if embedder is not None and owned_vectors:
            query = embedder.embed([group["label"]])[0]
            if any(cosine(query, v) >= 0.62 for v in owned_vectors):
                continue
        elif any(similar(group["tokens"], norm(e.get("capability", "")))
                 for e in authority):
            continue
        # "Owned nowhere" is a statement about every section. The fallback
        # schema does ask for authority, so only a section that was not read
        # at all can be holding the owner. Under --authority-as-dataflow
        # ownership is also taken from produces and consumes, which the
        # fallback never asks for, and a section read in part can hold it too.
        # It is a statement about every LINE as well: one that is in no
        # section was shown to no call, and can name the owner.
        owners_unseen = unread + (partial if args.authority_as_dataflow else [])
        findings.append(("D6", group["label"], group["members"], in_doubt(
            open_question(owners_unseen,
                          "a section that was not read could own it"),
            line_question(no_section, "could own it"))))

    # -- D8: produced and never consumed -------------------------------------
    #
    # "Never consumed" needs every section's consumes. A section read by the
    # fallback alone has none recorded, and it was never asked for any. Nor
    # was a line in no section: in the fixture's committed inventory one of
    # the two is "SF ingests telemetry and applies quality flags."
    consumed = [norm(e.get("value", "")) for e in consumes]
    for group in group_by_concept(produces, "value", embedder):
        if not any(similar(group["tokens"], c, 0.6) for c in consumed if c):
            findings.append((
                "D8", group["label"], group["members"][:2], in_doubt(
                    open_question(unread + partial,
                                  "a section that was not read, or was read "
                                  "without its consumes, could consume it"),
                    line_question(no_section, "could consume it"))))

    # -- D3: evidence claim pointing somewhere that holds no such thing ------
    findings += untrue

    if args.adjudicate:
        client = Client(project, model=args.model, url=args.url,
                        prompt_version="pair-1", quiet=args.concurrency > 1)
        auth_groups = group_by_concept(authority, "capability", embedder)
        def judged(tried, lost, kind, found, what):
            # How many pairs were JUDGED, not how many were tried. The two were
            # one number, and the client's total of failed calls two lines down
            # was the only sign that they differed.
            line = (f"adjudicated {tried - lost} of {tried} {kind} pairs -> "
                    f"{found} {what}")
            if lost:
                line += (f"\n            {lost} could not be judged: the call "
                         f"failed, which is not a verdict of no conflict")
            print(line)

        count, lost, conflicts = adjudicate_pairs(
            client, authority, "capability", args.concurrency, auth_groups,
            args.cap)
        judged(count, lost, "authority", len(conflicts), "conflicts")
        for a, b, reply in conflicts:
            findings.append(("D5", f"{a.get('capability','')} vs "
                                   f"{b.get('capability','')}", [a, b], ""))
        defer_groups = group_by_concept(defers, "capability", embedder)
        dcount, dlost, dconf = adjudicate_pairs(
            client, defers, "capability", args.concurrency, defer_groups,
            args.cap)
        judged(dcount, dlost, "deferral", len(dconf), "same-slot chains")
        for a, b, reply in dconf:
            findings.append(("D6", f"{a.get('capability','')} deferred to "
                                   f"{a.get('to','')}, and to {b.get('to','')}",
                             [a, b], ""))
        print(f"{client.summary()}\n")

    order = {"D5": 0, "D6": 1, "D8": 2, "D3": 3}
    findings.sort(key=lambda f: order.get(f[0], 9))
    counts, doubted = defaultdict(int), 0
    for kind, _, _, doubt in findings:
        counts[kind] += 1
        doubted += bool(doubt)
    print(f"-- candidate defects: " +
          ", ".join(f"{k}={counts[k]}" for k in sorted(counts)))
    if doubted:
        print(f"   {UNVERIFIABLE}: {doubted} of these {len(findings)}. Each "
              f"asserts an absence this run could not establish:\n   a section "
              f"the inventory did not read, or read only in part, or a line "
              f"it has in\n   no section, could answer it, or the place a "
              f"pointer names could not be\n   looked up. Those are questions "
              f"for a reviewer, not findings.")
    print()

    shown = defaultdict(int)
    for kind, label, members, doubt in findings:
        if shown[kind] >= args.show:
            continue
        shown[kind] += 1
        print(f"[{kind}] {label[:76]}")
        for entry in members[:3]:
            who = entry.get("owner") or entry.get("to") or entry.get("points_to") or ""
            # str(): none of the three is a string by any validator's promise.
            # A locator of null, a pointer given as {"place": ...}, raised here.
            print(f"      {str(entry.get('_locator') or ''):22} "
                  f"{str(entry.get('_heading') or '')[:30]:32} {str(who)[:26]}")
        if doubt:
            print(f"      {UNVERIFIABLE}: {doubt}")

    if args.out:
        import csv as _csv
        out_path = os.path.join(project, args.out)
        with open(out_path, "w", newline="", encoding="utf-8") as handle:
            writer = _csv.writer(handle)
            writer.writerow(["obligation", "source_ref", "modality",
                             "requirement", "verdict", "quote", "locator",
                             "reason", "requirement_locator"])
            for position, (kind, label, members, doubt) in enumerate(findings, 1):
                first = members[0] if members else {}
                # Every locator the finding rests on, so a reader can go
                # straight to the passages rather than back to the tool.
                where = " ".join(
                    str(m.get("_locator", "")) for m in members[:4] if m.get("_locator"))
                quote = str(first.get("quote", "") or first.get("claim", "") or "")
                writer.writerow([
                    # "unmet" is score-register.py's vocabulary for a flagged
                    # row; the discovery class is carried in source_ref so the
                    # per-class breakdown still works. A finding in doubt is
                    # "unverifiable", which that scale keeps off its ranks and
                    # score-register.py still counts as flagged.
                    f"{kind}-{position:03d}", kind, "",
                    label, UNVERIFIABLE if doubt else UNMET, quote, where,
                    " | ".join(filter(None, [doubt] + [
                        str(m.get("_heading", "")) for m in members[:4]])),
                    ""])
        print(f"\nwrote {out_path} ({len(findings)} candidates)")

    if args.ground_truth:
        try:
            import yaml
        except ImportError:
            # This was `return 0`: asked for a score, printed none, and exited
            # clean. A score that could not be computed is not a result.
            print(f"NOT SCORED against {args.ground_truth}: PyYAML is not "
                  f"installed, so the ground truth could not be read.",
                  file=sys.stderr)
            return 1
        with open(os.path.join(project, args.ground_truth),
                  encoding="utf-8") as handle:
            gt = yaml.safe_load(handle)
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
                (i for i, (kind, label, members, _) in enumerate(findings)
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
        resting = sum(1 for i in claimed if findings[i][3])
        if unread or partial or short or no_section:
            # The score is of the inventory, not of the document.
            print(f"  scored over an inventory that left {len(unread)} "
                  f"section(s) unread, {len(partial)} read in part and "
                  f"{len(short)} read in\n  fewer passes than asked, and has "
                  f"{how_many(no_section)} line(s) in no section: a miss may "
                  f"be a defect it\n  never saw, and {resting} of the hits "
                  f"rest on a finding marked {UNVERIFIABLE}")
        elif resting:
            # D3 has reasons of its own to list a finding and not assert it:
            # a document that marks no headings, or was not consulted. With
            # every section read this line was not printed, and a run in
            # which every D3 finding was a question scored them as found.
            print(f"  {resting} of the hits rest on a finding marked "
                  f"{UNVERIFIABLE}: a question the\n  tool raised, not a "
                  f"defect it established")
    return 0


if __name__ == "__main__":
    sys.exit(main())
