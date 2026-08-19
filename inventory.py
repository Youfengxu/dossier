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
    """One record per heading, so a unit of work is a unit a human would read."""
    sections, current, heading, start = [], [], "(front matter)", 1

    def flush(end):
        if current and sum(len(x.strip()) for x in current) > 60:
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
            current, heading, start = [], bare, number
        elif is_heading and not current:
            heading, start = bare, number
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
                current, start = [], number + 1
    flush(len(lines))
    return sections


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--doc", required=True)
    parser.add_argument("--model", default="Qwen3-Coder-Next-UD-Q4_K_M")
    parser.add_argument("--url",
                        default="http://192.168.100.148:8085/v1/chat/completions")
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
    sections = split_sections(lines, max_chars=args.max_chars)
    if args.limit:
        sections = sections[:args.limit]

    total_chars = sum(len(s["text"]) for s in sections)
    print(f"{args.doc}: {len(sections)} sections, {total_chars:,} characters")
    print(f"reading 100% of the document at concurrency {args.concurrency}\n")

    clients = [Client(project, model=args.model, url=args.url,
                      prompt_version=f"{PROMPT_VERSION}/r{r}",
                      temperature=0.0 if r == 0 else 0.25 * r,
                      quiet=args.concurrency > 1)
               for r in range(args.runs)]
    client = clients[0]

    def merge(target, extra):
        """Union by content, so a second pass adds what the first missed."""
        for key in ("capabilities", "identifiers", "deferred", "evidence_claims",
                    "authority", "defers_to", "consumes", "produces"):
            seen = {json.dumps(x, sort_keys=True) for x in target.get(key, [])}
            for item in extra.get(key, []):
                mark = json.dumps(item, sort_keys=True)
                if mark not in seen:
                    seen.add(mark)
                    target.setdefault(key, []).append(item)
        return target

    def work(item):
        index, section = item
        user = (f"SECTION: {section['heading']}\n"
                f"LOCATOR: {args.doc}:{section['start']}-{section['end']}\n\n"
                f"{section['text']}\n\nCatalogue this section.")
        try:
            reply = client.ask(SYSTEM, user, validate=validate,
                               label=f"inv:{section['start']}")
        except LLMError:
            # Falling back to a three-field schema keeps the ownership signal —
            # which is what D5 and D6 are built on — instead of losing the
            # section entirely. A quarter of a real document was dropped this
            # way before the fallback existed.
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
            try:
                reply = client.ask(MINIMAL_SYSTEM, user, validate=minimal_validate,
                                   label=f"inv-min:{section['start']}")
                reply["degraded"] = True
                for key in ("identifiers", "deferred", "evidence_claims",
                            "consumes", "produces"):
                    reply.setdefault(key, [])
            except LLMError as exc:
                return index, {"error": str(exc)}
        for extra_client in clients[1:]:
            try:
                more = extra_client.ask(SYSTEM, user, validate=validate,
                                        label=f"inv:{section['start']}")
                reply = merge(reply, more)
            except LLMError:
                pass
        reply["heading"] = section["heading"]
        reply["locator"] = f"{args.doc}:{section['start']}-{section['end']}"
        return index, reply

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

    failed = sum(1 for r in results if r and "error" in r)
    degraded = sum(1 for r in results if r and r.get("degraded"))
    counts = {k: sum(len(r.get(k, [])) for r in results if r and "error" not in r)
              for k in ("capabilities", "identifiers", "deferred",
                        "evidence_claims", "authority", "defers_to",
                        "consumes", "produces")}

    with open(os.path.join(project, args.out), "w", encoding="utf-8") as handle:
        json.dump({"doc": args.doc, "source_sha256": doc["source_sha256"],
                   "sections": results}, handle, indent=1)

    print(f"\n{client.summary()}")
    print(f"  {elapsed:.0f}s wall clock, {len(sections)/elapsed:.2f} sections/s"
          f"  ({failed} failed, {degraded} degraded to the minimal schema)")
    print("  " + "  ".join(f"{k}={v}" for k, v in counts.items()))
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
