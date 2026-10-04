"""synthesize.py, class D3, on documents made up for the purpose, where what
each section holds is known because the generator put it there.

tests/test_pointers.py pins one decision at a time on two documents written by
the author of the code. This file asks one question of many documents written
by somebody else: does D3 ever stand behind something false? The generator was
written by an independent review of the first version of the pointer work,
which it broke on text of the kind a .docx yields: "10 km" in a table read as
section 10, a numbered step read as a heading, a section's sub-sections no
longer searched. It is kept as it was written, so that it stays a second
opinion and not a restatement of the code.

For every (claim, pointer) pair the answer is known by construction:

    HOLDS     the place exists and the claimed content is inside it
    NOT       the place exists and the content is elsewhere
    MISSING   the document has no such place

Something false is asserted when a place that exists is called "no such
section in the document", or when a claim that HOLDS is reported and not
marked unverifiable. Listing a finding as unverifiable is never false: it is
D3 saying it could not tell.

Half of the documents are Markdown, with "#" on their headings. The other half
are plain text of the kind a .docx yields, and there D3 asserts nothing at
all: that is the rule the first two versions of the pointer work did not have,
and the reason this generator found them out.

Chunks come from inventory.split_sections, a capability is recorded against
the chunk that covers its line, nothing is unread, and main() runs in process.
"""

import random
import unittest

import synthesize
from tests.test_pointers import Frozen, sections_of

WORDS = ("alder birch cedar dogwood elder fig gorse hazel iris juniper kelp "
         "larch maple nettle olive poplar quince rowan sorrel thistle ulex "
         "vetch willow yarrow zinnia").split()
SENTENCES = (
    "The component runs once per tick and reports its state to the operator.",
    "It is owned by the twin team and reviewed at each quarterly meeting.",
    "A failure here is logged and raised to the operator within one minute.",
    "The interface is versioned and each change is recorded in the log.",
    "Readings are checked against their range before they are forwarded.",
    "The operator confirms each change before it is applied to the river model.",
)
# What a section may hold besides sentences. "md" is Markdown with "#"
# headings; "plain" is the text of a .docx, a paragraph a line and no marks.
NOISE = {
    "md": ([], ["steps"], ["steps_nodot"], ["table"], ["fence"], ["indented"],
           ["xref"], ["quantity"]),
    "plain": ([], ["steps"], ["steps_nodot"], ["table"], ["hashcell"], ["xref"],
              ["quantity"]),
}
SEEDS = range(6)


def build(rng, style, features):
    """-> (lines, {key: (first line, last line)}, {line: (capability, key)})"""
    lines, spans, caps = [], {}, {}
    heading_style = rng.choice(["dot", "space", "word"]) if style == "md" else \
        rng.choice(["dot", "space"])

    def head(number, title, depth):
        if "." in number or heading_style == "space":
            text = f"{number} {title}"
        elif heading_style == "dot":
            text = f"{number}. {title}"
        else:
            text = f"Section {number}: {title}"
        return ("#" * (depth + 1) + " " + text) if style == "md" else text

    def cap(key):
        name = " ".join(f"{rng.choice(WORDS)}{len(caps)}x{i}" for i in range(3))
        lines.append(f"The {name} is kept here and is reviewed by the owner "
                     f"each quarter.")
        caps[len(lines)] = (name, key)

    def body(key, n):
        position = rng.randrange(n)
        for i in range(n):
            if i == position:
                cap(key)
            else:
                lines.append(rng.choice(SENTENCES))

    def noise():
        kind = rng.choice(features) if features else None
        if kind == "steps":
            lines.append("The steps are these." if style == "plain"
                         else "The steps are these:")
            for i in range(1, 4):
                lines.append(f"{i}. {rng.choice(['Ingest', 'Check', 'Forward'])} "
                             f"the readings for the tick.")
        elif kind == "steps_nodot":
            lines.append("The steps are these.")
            for i in range(1, 4):
                lines.append(f"{i}. {rng.choice(['Ingest', 'Check', 'Forward'])} "
                             f"the readings for the tick")
        elif kind == "table":
            if style == "md":
                lines.extend(["| Gauge | Reach | Datum |", "|---|---|---|",
                              "| G-01 | 10 km | 12.5 m |", "| G-02 | 7 km | 9.0 m |"])
            else:
                lines.extend(["Gauge", "Reach", "Datum", "G-01", "10 km",
                              "12.5 m", "G-02", "7 km", "9.0 m"])
        elif kind == "fence" and style == "md":
            lines.extend(["```", "# 2. restart the service", "twin restart", "```"])
        elif kind == "indented" and style == "md":
            lines.extend(["", "    # restart the service", "    twin restart", ""])
        elif kind == "hashcell" and style == "plain":
            lines.extend(["#", "Gap", "Owner", "G1", "Provider not selected",
                          "IA lead"])
        elif kind == "xref":
            lines.append("Section 2 of the delivery plan gives the dates for "
                         "this work")
        elif kind == "quantity":
            lines.append(f"{rng.randrange(2, 30)} gauges are covered on this reach")

    if style == "md":
        lines.extend(["# Flood twin design", "", "Status: draft for review.", ""])
    else:
        lines.extend(["Flood twin design", "Status: draft for review."])
    for n in range(1, rng.randrange(3, 8) + 1):
        key = ("section", str(n))
        start = len(lines) + 1
        lines.append(head(str(n), f"Component {n}", 1))
        if style == "md" and rng.random() < 0.5:
            lines.append("")
        body(key, rng.randrange(1, 6))
        if rng.random() < 0.6:
            noise()
            if rng.random() < 0.5:
                body(key, rng.randrange(1, 4))
        for m in range(1, rng.randrange(0, 4) + 1):
            sub = ("section", f"{n}.{m}")
            sub_start = len(lines) + 1
            if style == "md":
                lines.append("")
            lines.append(head(f"{n}.{m}", f"Part {m}", 2))
            if style == "md" and rng.random() < 0.5:
                lines.append("")
            body(sub, rng.randrange(1, 6))
            if rng.random() < 0.3:
                noise()
            spans[sub] = (sub_start + (1 if style == "md" else 0), len(lines))
        spans[key] = (start, len(lines))
        if style == "md":
            lines.append("")
    for letter in "abc"[:rng.randrange(0, 4)]:
        key = ("appendix", letter)
        start = len(lines) + 1
        text = f"Appendix {letter.upper()} \N{EM DASH} Register {letter.upper()}"
        lines.append("## " + text if style == "md" else text)
        body(key, rng.randrange(2, 6))
        spans[key] = (start, len(lines))
        if style == "md":
            lines.append("")
    return lines, spans, caps


def spelled(key):
    return f"Section {key[1]}" if key[0] == "section" else \
        f"Appendix {key[1].upper()}"


def questions(seed, style, features):
    """-> (lines, {line: capability}, [(claim, pointer, what is true)])"""
    rng = random.Random(seed)
    lines, spans, caps = build(rng, style, features)
    keys, asked = sorted(spans), []
    for _ in range(6):
        place = rng.choice(keys)
        line = rng.choice(sorted(caps))
        first, last = spans[place]
        asked.append((f"{caps[line][0]} is recorded", spelled(place),
                      "HOLDS" if first <= line <= last else "NOT"))
    tops = [int(key[1]) for key in keys
            if key[0] == "section" and "." not in key[1]]
    name = caps[rng.choice(sorted(caps))][0]
    asked.append((f"{name} is recorded", f"Section {max(tops) + 2}", "MISSING"))
    if any(key[0] == "appendix" for key in keys):
        asked.append((f"{name} is recorded", "Appendix Q", "MISSING"))
    return lines, {line: name for line, (name, _) in caps.items()}, asked


class OnDocumentsWhoseAnswersAreKnown(Frozen):

    def verdicts(self, style, features, **recorded):
        """[(what is true, what D3 said, the case)] over every seed."""
        out = []
        for seed in SEEDS:
            lines, holds, asked = questions(seed, style, features)
            for claim, pointer, truth in asked:
                self.again()
                if recorded.get("text_sha256"):
                    recorded["text_sha256"] = self.freeze(lines)
                report = self.run_on(
                    sections_of(lines, (claim, pointer), holds=holds),
                    document=lines, **recorded,
                    path="design.md" if style == "md" else "design.docx")
                found = self.d3(report)
                said = ("nothing" if not found
                        else "no such section" if "no such section in the "
                        "document" in found[0]
                        else "unverifiable" if "unverifiable" in found[0]
                        else "does not hold")
                out.append((truth, said, f"{style} {features} seed {seed}: "
                                         f"{claim!r} -> {pointer!r}"))
        return out

    def test_nothing_false_is_asserted(self):
        false = []
        for style in NOISE:
            for features in NOISE[style]:
                for truth, said, case in self.verdicts(style, features,
                                                       text_sha256=True):
                    if (said == "no such section" and truth != "MISSING") or \
                            (said == "does not hold" and truth == "HOLDS"):
                        false.append(f"{case}: it {truth}, and D3 said {said!r}")
        self.assertEqual(false, [])

    def test_nor_by_an_inventory_from_before_the_texts_hash_was_kept(self):
        """The same, with the inventory tied to the text by its chunks'
        headings. That either holds for the text it was split from, or the
        report says the document was not consulted and asserts no absence."""
        false = []
        for style in NOISE:
            for truth, said, case in self.verdicts(style, []):
                if (said == "no such section" and truth != "MISSING") or \
                        (said == "does not hold" and truth == "HOLDS"):
                    false.append(f"{case}: it {truth}, and D3 said {said!r}")
        self.assertEqual(false, [])

    def test_on_a_clean_document_that_marks_its_headings_it_is_not_all_doubt(self):
        """A check that calls everything unverifiable asserts nothing false
        either. Where a Markdown document holds nothing but headings and
        sentences, a place that is missing is called missing, a claim that
        holds is passed in silence, and no finding is left in doubt."""
        wrong = [f"{case}: it {truth}, and D3 said {said!r}"
                 for truth, said, case in self.verdicts("md", [],
                                                        text_sha256=True)
                 if (truth == "MISSING" and said != "no such section")
                 or (truth == "HOLDS" and said != "nothing")
                 or said == "unverifiable"]
        self.assertEqual(wrong, [])

    def test_in_text_that_marks_no_headings_it_asserts_nothing(self):
        """Whatever the text holds: steps, a table, a column headed "#",
        quantities. A claim it finds is passed, and the rest is listed as
        unverifiable."""
        asserted = []
        for features in NOISE["plain"]:
            for truth, said, case in self.verdicts("plain", features,
                                                   text_sha256=True):
                if said not in ("nothing", "unverifiable"):
                    asserted.append(f"{case}: it {truth}, and D3 said {said!r}")
        self.assertEqual(asserted, [])

    def test_and_on_a_clean_one_a_claim_that_holds_is_still_passed(self):
        """Not asserting is not the same as not looking. A section no line
        opens with is listed, and a claim the section holds is not."""
        wrong = [f"{case}: it {truth}, and D3 said {said!r}"
                 for truth, said, case in self.verdicts("plain", [],
                                                        text_sha256=True)
                 if (truth == "HOLDS" and said != "nothing")
                 or (truth == "MISSING" and said != "unverifiable")]
        self.assertEqual(wrong, [])


class TheTextTheInventoryWasSplitFrom(unittest.TestCase):
    """moved(): an inventory with no hash of the text is tied to it by the
    heading at the head of each chunk."""

    def test_is_never_taken_for_a_text_that_moved(self):
        refused = []
        for style in NOISE:
            for features in NOISE[style]:
                for seed in SEEDS:
                    lines, holds, _ = questions(seed, style, features)
                    why = synthesize.moved(sections_of(lines, holds=holds), lines)
                    if why and not why.startswith("no chunk of the inventory"):
                        refused.append(f"{style} {features} seed {seed}: {why}")
        self.assertEqual(refused, [])

    def test_and_a_line_added_above_a_chunk_that_starts_on_a_heading_is_noticed(self):
        """The line goes in just above the last such chunk, so the chunks
        before it are still where the inventory has them."""
        missed = []
        for style in NOISE:
            for features in NOISE[style]:
                for seed in SEEDS:
                    lines, holds, _ = questions(seed, style, features)
                    sections = sections_of(lines, holds=holds)
                    starts = [int(now["locator"].split(":")[1].split("-")[0])
                              for before, now in zip([{}] + sections, sections)
                              if now["heading"] not in ("(front matter)",
                                                        before.get("heading"))]
                    if not starts:
                        continue
                    at = starts[-1] - 1
                    if not synthesize.moved(sections,
                                            lines[:at] + [""] + lines[at:]):
                        missed.append(f"{style} {features} seed {seed}")
        self.assertEqual(missed, [])


if __name__ == "__main__":
    unittest.main()
