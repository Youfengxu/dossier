#!/usr/bin/env python3
"""Read the whole document once, section by section, into a structured inventory.

The coverage pipeline is obligation-first: for each obligation, retrieve some
passages and judge. Measured on a real architecture, that means **53% of the
document is never read by anything** — every verdict, including every "unmet",
is made against a sample.

This inverts it. One bounded agent per section, asked what the section PROVIDES
rather than whether some obligation is met. Description, not judgement:

  * each agent works on ~3k characters, the regime where absence precision
    measured 97% rather than the 83% seen at 52k
  * no agent is ever asked whether something is absent — absence is a property
    of the whole document, and becomes a deterministic query over the finished
    inventory rather than a model's impression
  * every entry carries a locator, so the inventory is auditable
  * every line that holds text is in one section, and the run checks that
    against the text instead of saying it: a line in none is printed, and
    written to the inventory as `unread_lines`

Concurrency is supported because the workload is embarrassingly parallel — every
section is independent. Whether it helps depends on the server: llama.cpp with
--parallel 1 will queue, vLLM will not.

    ./inventory.py --project . --doc deliverable-v1 --concurrency 8
    ./inventory.py --project . --doc deliverable-v1 --out inventory-arch.json

Writes inventory.json. Feed it to synthesize.py.
"""

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm                                                   # noqa: E402
from llm import Client, LLMError, load_doc               # noqa: E402

PROMPT_VERSION = "inventory-3"

MINIMAL_SYSTEM = """You catalogue one section of a technical document, briefly.

Reply with JSON only:
{"capabilities":[{"name":"...","quote":"..."}],
 "authority":[{"capability":"...","action":"...","owner":"...","polarity":"owns|excludes","quote":"..."}],
 "defers_to":[{"capability":"...","to":"..."}]}

- capabilities: what this section says the system does.
- authority: where it states which component owns a capability ("owns") or
  explicitly does NOT ("excludes" — under "Does not own", "Exclusions", or a
  statement that it never does that).
- defers_to: where it says another component decides or handles something.
Empty lists are fine. Quotes verbatim. Never assert that anything is missing."""


SYSTEM = """You catalogue one section of a technical document. You describe what
is there. You never judge whether anything is missing — you cannot see the rest
of the document, so absence is not yours to assert.

Reply with JSON only:
{"capabilities":[{"name":"...","quote":"..."}],
 "identifiers":["..."],
 "deferred":[{"item":"...","id":"...","owner":"..."}],
 "evidence_claims":[{"claim":"...","points_to":"..."}],
 "authority":[{"capability":"...","action":"...","owner":"...","polarity":"owns|excludes","quote":"..."}],
 "defers_to":[{"capability":"...","to":"..."}],
 "consumes":["..."],
 "produces":["..."]}

Definitions, and be strict about them:
- capabilities: what this section says the system DOES. Short noun phrases.
  "quote" must be copied verbatim from the section.
- identifiers: gap/interface/register IDs defined or referenced here, verbatim.
- deferred: items this section says are open, TBD, not yet selected or decided.
  "id" is the register ID if one is given, else "". "owner" if named, else "".
- evidence_claims: statements that something is recorded, described or assessed
  SOMEWHERE ELSE. "points_to" is the place named ("Section 9", "Appendix C",
  "the delivery plan"). This is a claim about the document, not content.
- authority: where this section states which component is or is NOT
  authoritative for a capability. "quote" verbatim.

  POLARITY IS NOT OPTIONAL AND IT IS EASY TO GET WRONG. Architecture sections
  routinely carry a heading "Ownership and exclusions" with an "Owns" list and a
  "Does not own" list underneath it. Items under "Does not own", "Exclusions",
  "Out of scope" or "never" are polarity "excludes" — that component is
  DISCLAIMING the capability. Recording a disclaimer as a claim inverts the
  meaning and manufactures conflicts that do not exist.

  Read which sub-heading each item falls under before deciding. When a line says
  a component never does something, that is "excludes".

  AUTHORITY IS ABOUT RIGHTS, NOT ABOUT DATA FLOW. An authority entry records who
  is accountable for, owns, decides, or is authoritative over something. It does
  NOT record what a component does with a value. "X returns Y", "X evaluates Y",
  "X reads Y", "X publishes Y" are data flow — put them in produces/consumes, not
  here. Two components doing different things to the same artefact is a handoff,
  which is how architectures work; recorded as authority it looks like a dispute.

  "action" is the verb of the right being asserted — owns, allocates, decides,
  commits, is authoritative for. If you cannot name such a verb, this is probably
  not an authority statement.
- defers_to: where this section says another component decides, resolves or
  handles something. The capability, and which component it is passed to.
- consumes / produces: named inputs and outputs. Short noun phrases.

Every list may be empty. Do not invent entries to fill the schema. Prose that
states no capability, defers nothing and names no owner returns empty lists."""


def validate(obj):
    if not isinstance(obj, dict):
        return "reply must be a JSON object"
    for key in ("capabilities", "identifiers", "deferred", "evidence_claims",
                "authority", "defers_to", "consumes", "produces"):
        if key not in obj:
            return f"missing key '{key}'"
        if not isinstance(obj[key], list):
            return f"'{key}' must be an array"
    for item in obj["capabilities"]:
        if not isinstance(item, dict) or not item.get("name"):
            return "each capability needs a 'name'"
    for item in obj["authority"]:
        if not isinstance(item, dict) or not item.get("capability") \
                or not item.get("owner"):
            return "each authority entry needs 'capability' and 'owner'"
        if item.get("polarity") not in ("owns", "excludes"):
            return ("each authority entry needs 'polarity' of exactly 'owns' or "
                    "'excludes' — check which sub-heading it falls under")
        if not item.get("action"):
            return ("each authority entry needs 'action' — the verb of the right "
                    "asserted (owns, decides, commits). If there is no such verb "
                    "this is data flow, not authority")
    return None


def split_sections(lines, min_lines=4, max_lines=90, max_chars=1200):
    """One record per heading, so a unit of work is a unit a human would read.

    Every line that holds text goes into exactly one section, as its heading
    or as a line of its text. Until 2026-10-04 two kinds did not, and the run
    still printed "reading 100% of the document":

      a heading-like line straight after another   replaced it. The first was
          then in no section. A numbered list is a run of such lines, so each
          step replaced the one before: on the fixture, two of the five steps
          of the per-tick sequence, one of them a corroborating anchor of a
          planted defect.
      a section of sixty characters or fewer       was dropped whole, heading
          included: a stub ("To be written."), a short front matter, a short
          last section. Sixty characters is room for "Component maturity is
          assessed in Section 9", which is what D3 exists to check.

    Now the first heading keeps the section and the next becomes its first
    line of text, which is what a heading arriving within `min_lines` already
    did; and a section is kept if it has a heading line of its own or any
    text. What is still left out is blank lines with no heading over them.
    unread_lines() checks on every run that no line which holds text is left
    out. That no line is in two sections is checked by the tests alone.
    """
    sections, current, heading, start = [], [], "(front matter)", 1
    opened = False      # the pending section starts on its own heading line

    def flush(end):
        if opened or any(x.strip() for x in current):
            sections.append({"heading": heading, "start": start, "end": end,
                             "text": "\n".join(current)})

    for number, line in enumerate(lines, 1):
        stripped = line.strip()
        bare = stripped.lstrip("#").strip()
        is_heading = stripped.startswith("#") or (
            0 < len(bare) < 90 and
            re.match(r"^(\d+(\.\d+)*[.\s]\s*\S|Appendix\s+[A-Z]\b|Annex\s)", bare))
        if is_heading and len(current) >= min_lines:
            flush(number - 1)
            current, heading, start, opened = [], bare, number, True
        elif is_heading and not current and not opened:
            heading, start, opened = bare, number, True
        else:
            current.append(line)
            # Cap by characters as well as lines. Measured on a real
            # architecture: 284 sections averaging 2.4k characters produced a
            # 39% retry rate and 26% hard failures, against 6% retries on a
            # fixture whose sections are six times smaller. Schema compliance
            # degrades with input size exactly as judgement does.
            if len(current) >= max_lines or \
                    sum(len(x) for x in current) >= max_chars:
                flush(number)
                current, start, opened = [], number + 1, False
    flush(len(lines))
    return sections


def unread_lines(lines, sections):
    """The numbers of the lines that hold text and that no section shows a
    model, as its heading or as a line of its text. [] is what "reading 100%
    of the document" means.

    Worked out from the text each section carries, not from its line numbers
    alone: the claim is about what a call is shown, and a locator is the
    splitter's own account of that. A section whose text is not the lines its
    locator names shows none of them, as far as this can tell, and nor does
    one whose lines the text does not have: a start of 0 is a slice from the
    end of the list in Python, and was read as the last line.
    """
    shown = set()
    for section in sections:
        start, end = section["start"], section["end"]
        if not 1 <= start <= end <= len(lines):
            continue
        span = lines[start - 1:end]
        whole = "\n".join(span) == section["text"]
        headed = "\n".join(span[1:]) == section["text"] and \
            span[0].strip().lstrip("#").strip() == section["heading"]
        if whole or headed:
            shown.update(range(start, end + 1))
    return [number for number, line in enumerate(lines, 1)
            if line.strip() and number not in shown]


def runs_of(numbers):
    """[284, 285, 300] as [(284, 285), (300, 300)]."""
    runs = []
    for number in sorted(set(numbers)):
        if runs and runs[-1][1] == number - 1:
            runs[-1][1] = number
        else:
            runs.append([number, number])
    return [tuple(run) for run in runs]


def line_runs(runs):
    """[(284, 285), (300, 300)] as "lines 284-285, 300"; [(7, 7)] as "line 7"."""
    one = len(runs) == 1 and runs[0][0] == runs[0][1]
    return ("line " if one else "lines ") + ", ".join(
        str(first) if first == last else f"{first}-{last}"
        for first, last in runs)


# What a section is asked for. The fallback asks for three of them.
FIELDS = ("capabilities", "identifiers", "deferred", "evidence_claims",
          "authority", "defers_to", "consumes", "produces")


def merge(target, extra):
    """Union by content, so a second pass adds what the first missed."""
    for key in FIELDS:
        seen = {json.dumps(x, sort_keys=True) for x in target.get(key, [])}
        for item in extra.get(key, []):
            mark = json.dumps(item, sort_keys=True)
            if mark not in seen:
                seen.add(mark)
                target.setdefault(key, []).append(item)
    return target


def minimal_validate(obj):
    if not isinstance(obj, dict):
        return "reply must be a JSON object"
    for key in ("capabilities", "authority", "defers_to"):
        if not isinstance(obj.get(key), list):
            return f"'{key}' must be an array"
    for item in obj["authority"]:
        if isinstance(item, dict) and \
                item.get("polarity") not in ("owns", "excludes"):
            return "each authority entry needs polarity owns/excludes"
    return None


def catalogue(section, doc, clients):
    """One section's entry: what every pass that answered said, and how many did.

    synthesize.py asserts absences over this file, so an entry has to say how
    much of its section was read, not only what was found there:

      error        nothing was. The entry still carries the heading and the
                   lines; without them the inventory can say that a section is
                   missing and not which one.
      degraded     the first pass fell back to the three-field schema.
      full_passes  how many passes used the whole schema. At 0, the five fields
                   the fallback never asks for are empty because nothing asked,
                   not because the section has none.
      passes       how many of the passes asked for answered at all. --runs
                   exists because one pass misses things. A pass that failed
                   used to be swallowed here, and a section read once looked
                   the same as one read three times.
    """
    locator = f"{doc}:{section['start']}-{section['end']}"
    user = (f"SECTION: {section['heading']}\n"
            f"LOCATOR: {locator}\n\n"
            f"{section['text']}\n\nCatalogue this section.")
    degraded = False
    try:
        reply = clients[0].ask(SYSTEM, user, validate=validate,
                               label=f"inv:{section['start']}")
        passes = full = 1
    except LLMError:
        # Falling back to a three-field schema keeps the ownership signal —
        # which is what D5 and D6 are built on — instead of losing the
        # section entirely. A quarter of a real document was dropped this
        # way before the fallback existed.
        try:
            reply = clients[0].ask(MINIMAL_SYSTEM, user,
                                   validate=minimal_validate,
                                   label=f"inv-min:{section['start']}")
        except LLMError as exc:
            return {"error": str(exc), "heading": section["heading"],
                    "locator": locator}
        degraded, passes, full = True, 1, 0
    # Only the fields that were asked for are taken from a reply. The keys
    # below this line say how the section was read, and a reply that happens to
    # carry "error" or "degraded" of its own must not be able to say it for us.
    entry = {key: reply[key] if isinstance(reply.get(key), list) else []
             for key in FIELDS}
    for extra_client in clients[1:]:
        try:
            more = extra_client.ask(SYSTEM, user, validate=validate,
                                    label=f"inv:{section['start']}")
        except LLMError:
            continue                # not merged, and not counted: see `passes`
        entry = merge(entry, more)
        passes, full = passes + 1, full + 1
    if degraded:
        entry["degraded"] = True
    entry["passes"], entry["full_passes"] = passes, full
    entry["heading"] = section["heading"]
    entry["locator"] = locator
    return entry


def limited(sections, doc, limit):
    """(the sections to read, entries for the ones --limit stops before).

    --limit used to cut the list and say nothing: the inventory held the first
    N sections, the line above them read "reading 100% of the document", and
    synthesize.py reported a pointer to section N+1 as a pointer to a section
    that does not exist. The sections left out are still sections of the
    document, so they go into the inventory as what they are: not read.
    """
    if not limit:
        return sections, []
    beyond = [
        {"error": f"not read: --limit {limit} stopped before this section",
         "skipped": True, "heading": section["heading"],
         "locator": f"{doc}:{section['start']}-{section['end']}"}
        for section in sections[limit:]]
    return sections[:limit], beyond


def shortfall(results, runs):
    """How far a run fell short of reading every section `runs` times: the
    sections that failed, that fell back, that got fewer passes, and that
    --limit stopped before."""
    skipped = sum(1 for r in results if r and r.get("skipped"))
    failed = sum(1 for r in results if r and "error" in r) - skipped
    degraded = sum(1 for r in results if r and r.get("degraded"))
    short = sum(1 for r in results if r and r.get("passes", runs) < runs)
    return {"failed": failed, "degraded": degraded, "short": short,
            "skipped": skipped}


def reading(sections, everything, beyond, left_out, concurrency):
    """What a run says it is about to read.

    "100% of the document" was a constant: printed under --limit, and printed
    over a splitter that left lines in no section. It is said now only when
    no section is held back and unread_lines() finds no line left out. A
    document with no text in it is not read 100%: there is nothing to read,
    and the run says that.
    """
    if not everything and not left_out:
        return "nothing to read: no line of the document holds text"
    if not beyond and not left_out:
        return (f"reading 100% of the document at concurrency {concurrency}: "
                f"every line that holds text is in a section")
    said = (f"reading the first {len(sections)} of {len(everything)} sections "
            f"(--limit)" if beyond else f"reading {len(sections)} sections")
    said += f" at concurrency {concurrency}: NOT the whole document."
    if beyond:
        said += (f"\nThe other {len(beyond)} go into the inventory as not "
                 f"read.")
    if left_out:
        said += (f"\n{len(left_out)} line(s) that hold text are in no section, "
                 f"and no call is shown them: {line_runs(runs_of(left_out))}."
                 f"\nThey go into the inventory as lines not read.")
    return said


def record(slug, doc, runs, results, left_out):
    """What is written to disk: the sections, which text they were read from,
    and the lines of it that are in none of them.

    `text_sha256` is the frozen text's own hash, and every locator below is a
    line number in that text. Only the source's hash used to be kept, and a
    re-freeze leaves that unchanged while it moves every line: synthesize.py
    now looks sections up in the document by line, and has to be able to tell
    that the document is still the one these line numbers are in.

    `unread_lines` is written even when it is empty. synthesize.py asserts
    absences over this file, and an inventory that does not say which lines
    it left out cannot be told from one that left out none.
    """
    return {"doc": slug, "source_sha256": doc["source_sha256"],
            "text_sha256": doc.get("text_sha256"),
            "runs": runs, "unread_lines": left_out, "sections": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--doc", required=True)
    parser.add_argument("--model", default="Qwen3-Coder-Next-UD-Q4_K_M")
    parser.add_argument("--url",
                        default=llm.DEFAULT_URL)
    parser.add_argument("--out", default="inventory.json")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--runs", type=int, default=3,
                        help="passes to union. Extraction samples what a section "
                             "says rather than reading it completely — two runs "
                             "of one fixture gave 20 and 17 authority entries, "
                             "flipping planted defects in both directions. More "
                             "passes strictly increase what is captured, and also "
                             "what is spurious. Default 3: at one pass only two "
                             "legs of the fixture's deferral cycle are captured "
                             "and the defect is invisible; at three all three "
                             "are, and the fixture goes 4/6 -> 6/6.")
    parser.add_argument("--max-chars", type=int, default=1200,
                        help="hard cap on section size; schema compliance "
                             "degrades sharply above roughly this")
    args = parser.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    doc, lines = load_doc(project, args.doc)
    everything = split_sections(lines, max_chars=args.max_chars)
    left_out = unread_lines(lines, everything)
    sections, beyond = limited(everything, args.doc, args.limit)

    total_chars = sum(len(s["text"]) for s in sections)
    print(f"{args.doc}: {len(sections)} sections, {total_chars:,} characters")
    print(reading(sections, everything, beyond, left_out, args.concurrency)
          + "\n")

    clients = [Client(project, model=args.model, url=args.url,
                      prompt_version=f"{PROMPT_VERSION}/r{r}",
                      temperature=0.0 if r == 0 else 0.25 * r,
                      quiet=args.concurrency > 1)
               for r in range(args.runs)]
    client = clients[0]

    def work(item):
        index, section = item
        return index, catalogue(section, args.doc, clients)

    started = time.time()
    results = [None] * len(sections)
    if args.concurrency > 1:
        with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
            for done, (index, reply) in enumerate(
                    pool.map(work, enumerate(sections)), 1):
                results[index] = reply
                if done % 10 == 0 or done == len(sections):
                    print(f"  {done}/{len(sections)}")
    else:
        for index, section in enumerate(sections):
            _, reply = work((index, section))
            results[index] = reply
            if (index + 1) % 10 == 0 or index + 1 == len(sections):
                print(f"  {index+1}/{len(sections)}")
    elapsed = time.time() - started

    results += beyond
    short = shortfall(results, args.runs)
    # aggregation-ok: failed sections are counted by shortfall(), printed first
    counts = {k: sum(len(r.get(k, [])) for r in results if r and "error" not in r)
              for k in FIELDS}

    with open(os.path.join(project, args.out), "w", encoding="utf-8") as handle:
        json.dump(record(args.doc, doc, args.runs, results, left_out), handle,
                  indent=1)

    # With nothing to read the loop above takes no time, and a rate is a
    # division by it: an empty document wrote its inventory and then crashed.
    rate = len(sections) / elapsed if elapsed else 0.0
    print(f"\n{client.summary()}")
    print(f"  {elapsed:.0f}s wall clock, {rate:.2f} sections/s"
          f"  ({short['failed']} failed, {short['degraded']} degraded to the "
          f"minimal schema,")
    print(f"  {short['short']} read in fewer than the {args.runs} passes "
          f"asked for"
          + (f", {short['skipped']} not read: --limit" if beyond else "")
          + (f", {len(left_out)} line(s) in no section" if left_out else "")
          + ")")
    print("  " + "  ".join(f"{k}={v}" for k, v in counts.items()))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
