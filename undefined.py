#!/usr/bin/env python3
"""Terms the document leans on and never defines.

The largest class hiding inside "framing". On a live review, 15 of 26 findings
filed as framing — the class DESIGN 5.5 calls permanently human — were one
shape: a term used repeatedly and defined nowhere. On that review one term
appeared 77 times with no definition, a second 19 times, a third 5. None of them
needed knowledge from outside the document, which is what "framing" was supposed
to mean — they were filed there because nothing detected them.

    ./undefined.py --project . --doc deliverable-v1 --model gpt-oss-120b --url ...
    ./undefined.py --project . --doc deliverable-v1 --score      # against the register

WHY NOT A DETERMINISTIC SWEEP. That was tried first and abandoned, and the
record is worth more than the code: n-gram frequency over the document gave
3,370 candidates, and tightening it three times reached 593 — still noise, and
still structurally unable to see a one-word coinage, because a single word is not an
n-gram. Frequency cannot tell jargon from prose. A model can.

So the shape is matrix.py's, which has worked repeatedly here: THE MODEL
PROPOSES, THE CORPUS DISPOSES. One bounded agent per section nominates terms a
reader would need defined; every nomination is then checked deterministically
against the whole document for a definition, a glossary entry, or a heading of
its own. Nothing the model says survives without corroboration, and the model
is never asked whether something is absent — it cannot see the rest of the
document, and absence is computed here.
"""

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from llm import Client, LLMError, load_doc                  # noqa: E402
import closure                                              # noqa: E402
import inventory                                            # noqa: E402

PROMPT_VERSION = "undefined-1"

SYSTEM = """You read one section of a technical document and name the terms it
uses without explaining them.

Reply with JSON only:
{"terms":[{"term":"...","quote":"..."}]}

A term qualifies when a competent reader of this document — an engineer who
knows the domain but not this project — would have to guess what it means.
Include:
  - project coinages and named concepts used as though already agreed
    ("frozen execution order", "escalation contracts", "calibration baselines")
  - status or state words carrying specific weight ("provisional", "remain open")
  - compound nouns naming a mechanism, boundary or artefact

Do NOT include:
  - ordinary technical vocabulary any engineer knows (latency, schema, API)
  - things this section itself explains
  - component or section names the document is structured around
  - acronyms expanded on first use

"quote" must be copied verbatim from the section and must contain the term.

You cannot see the rest of the document, so do not judge whether a term is
defined elsewhere — name what THIS section leaves unexplained and let the check
that follows decide. Most sections yield none or one; returning
{"terms":[]} is a normal answer."""


def validate(obj):
    if not isinstance(obj, dict) or not isinstance(obj.get("terms"), list):
        return "top level must be an object with a 'terms' array"
    for item in obj["terms"]:
        if not isinstance(item, dict):
            return "each entry must be an object"
        for key in ("term", "quote"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                return f"each entry needs a non-empty '{key}'"
    return None


# A definition can take several shapes and any one of them clears the term.
# Being generous here is deliberate: a false "undefined" wastes a reviewer's
# time on a term the document does explain, which is the expensive error.
# Tight on purpose. A first version accepted a colon, an em-dash OR A HYPHEN
# after the term, and cleared 70 of 112 nominations — hyphens join compound
# words on every page, so nearly everything looked defined. A definitional
# copula needs an article after it: "X is a ...", not "X is stored in Y".
DEFINITION = (r"(?:\s+(?:is|are)\s+(?:a|an|the)\b"
              r"|\s+(?:means|denotes|refers to|is defined as|shall mean)\b)")
DEFINITION_LIST = r"^\s*{}\s*[:\u2014]"          # term at line start, then : or em-dash


def _loose(text):
    """Hyphens and case are not meaning. The document writes a component as
    "Platform Adapter" in its heading and "platform-adapter" in prose, and matching
    literally left 181 uses of platform-adapter at the top of the output as
    undefined — when section 9 defines it."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def is_defined(term, lowered, lines, headings, glossary_text):
    loose = _loose(term)
    if loose and loose in _loose(headings):
        return "has its own heading"
    if loose and loose in _loose(glossary_text):
        return "glossary entry"
    needle = re.escape(term.lower())
    if re.search(needle + DEFINITION, lowered):
        return "definition sentence"
    pattern = re.compile(DEFINITION_LIST.format(needle), re.I | re.M)
    if any(pattern.search(l) for l in lines):
        return "definition list entry"
    if term.lower() in glossary_text:
        return "glossary entry"
    if term.lower() in headings:
        return "has its own heading"
    return None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True)
    parser.add_argument("--doc", required=True)
    parser.add_argument("--model")
    parser.add_argument("--url")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--max-chars", type=int, default=1200)
    parser.add_argument("--min-uses", type=int, default=2,
                        help="a term used once may simply be incidental")
    parser.add_argument("--limit", type=int, help="first N sections")
    # Reasoning counts against this budget and the answer comes last, so a
    # model that thinks past it returns no content and the reply parses as
    # nothing. At 700 against gpt-oss-120b this run failed 30% of its calls —
    # 74 of them cut off mid-string — and each failure looked like a section
    # with no terms worth naming rather than a section never answered.
    parser.add_argument("--max-tokens", type=int, default=700)
    parser.add_argument("--out")
    parser.add_argument("--score", metavar="MAP",
                        help="score against a register map's framing findings")
    args = parser.parse_args()

    project = os.path.abspath(os.path.expanduser(args.project))
    _doc, lines = load_doc(project, args.doc)
    text = "\n".join(lines)
    lowered = text.lower()
    headings = " ".join(l.lower() for l in lines
                        if l.strip().startswith("#")
                        or re.match(r"^\s*(\d+(\.\d+)*[.\s]|Appendix|Annex)", l))
    # The glossary is a SECTION, not any line that says "definitions". A first
    # version grabbed 120 lines after every such mention and built a 388k-char
    # blob — 56% of the document — inside which every term looked defined and
    # the detector reported nothing at all. Require a heading, and stop at the
    # next one.
    HEADING = re.compile(r"^\s*(#|\d+(\.\d+)*[.\s]|Appendix|Annex)")
    glossary_text = ""
    for index, line in enumerate(lines):
        if HEADING.match(line) and re.search(
                r"\bglossary\b|\bdefinitions\b|\bterminology\b", line, re.I):
            for offset in range(index + 1, min(len(lines), index + 400)):
                if HEADING.match(lines[offset]):
                    break
                glossary_text += lines[offset].lower() + " "
    if glossary_text:
        print(f"  glossary section: {len(glossary_text):,} chars")

    kwargs = {}
    if args.model:
        kwargs["model"] = args.model
    if args.url:
        kwargs["url"] = args.url
    client = Client(project, prompt_version=PROMPT_VERSION,
                    max_tokens=args.max_tokens, **kwargs)

    sections = inventory.split_sections(lines, max_chars=args.max_chars)
    if args.limit:
        sections = sections[:args.limit]
    print(f"{args.doc}: {len(sections)} sections")

    def one(section):
        user = (f"SECTION: {section['heading']}\n"
                f"LOCATOR: {args.doc}:{section['start']}-{section['end']}\n\n"
                f"{section['text']}")
        try:
            reply = client.ask(SYSTEM, user, validate=validate,
                               label=f"undef:{section['start']}")
        except LLMError:
            return []
        return [(item["term"].strip(), item["quote"].strip(), section)
                for item in reply["terms"]]

    proposed = []
    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        for index, batch in enumerate(pool.map(one, sections), 1):
            proposed.extend(batch)
            if index % 50 == 0 or index == len(sections):
                print(f"  {index}/{len(sections)} sections, "
                      f"{len(proposed)} nominations")

    # THE CORPUS DISPOSES. Every nomination is checked against the whole
    # document; the model's opinion that a term is unexplained counts for
    # nothing if the document explains it somewhere the model could not see.
    best, cleared = {}, 0
    for term, quote, section in proposed:
        if closure.normalise(quote).lower() not in closure.normalise(text).lower():
            continue                                   # unquotable, so unusable
        uses = lowered.count(term.lower())
        if uses < args.min_uses:
            continue
        why = is_defined(term, lowered, lines, headings, glossary_text)
        if why:
            cleared += 1
            continue
        key = term.lower()
        if key not in best or uses > best[key]["uses"]:
            best[key] = {"term": term, "uses": uses, "quote": quote,
                         "locator": f"{args.doc}:{section['start']}",
                         "heading": section["heading"]}

    findings = sorted(best.values(), key=lambda f: -f["uses"])
    print(f"\n  {len(proposed)} nominations -> {len(findings)} undefined "
          f"({cleared} cleared as defined, rest below --min-uses or unquotable)")
    print("=" * 74)
    for finding in findings[:60]:
        print(f"  {finding['uses']:>4}x  {finding['term'][:38]:<40} "
              f"{finding['locator']}")
    if args.out:
        path = os.path.join(project, args.out)
        json.dump(findings, open(path, "w"), indent=1)
        print(f"\nwrote {path}")

    if args.score:
        truth = closure.load_yaml(os.path.join(project, args.score))
        wanted = [f for f in truth["findings"] if f.get("class") == "framing"
                  and f.get("terms")]
        got = " ".join(f["term"].lower() for f in findings)
        hit = [f for f in wanted
               if any(t.lower() in got or
                      any(w in got for w in t.lower().split() if len(w) > 5)
                      for t in f["terms"])]
        print(f"\n-- against the register's framing findings --")
        print(f"  {len(hit)}/{len(wanted)} surfaced")
        for f in wanted:
            mark = "FOUND" if f in hit else "miss "
            print(f"  {mark} {f['id']:8} {str(f['terms'])[:58]}")
    print(f"\nmodel calls: {client.stats}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
