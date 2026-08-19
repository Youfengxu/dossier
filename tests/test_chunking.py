"""Heading detection, in the three places that each re-implement it.

    trace.chunk()               splits a document into retrievable passages
    inventory.split_sections()  splits it into units of model work
    sweep.headings_of()         compares two revisions for a deleted section

They disagree, and the disagreements are not cosmetic: a line that is a heading
to one and body text to another lands in a different chunk, gets a different
locator, and is reported under a different section. Each detector is pinned
separately here, and the cases where they diverge are pinned as divergences.

The fragile case they share: a heading is recognised by starting with a number,
so any body sentence that starts with a number is promoted to a heading. "3 of
the 5 nodes were rebuilt." opens a new passage and stops being retrievable text.
That is characterised below, not fixed.
"""

import os
import unittest

import inventory
import sweep
import trace
from tests.support import lift

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "fixtures", "floodtwin", "deliverable-v1.md")


def fixture_lines():
    with open(FIXTURE, encoding="utf-8") as handle:
        return handle.read().splitlines()


class TraceChunk(unittest.TestCase):
    """trace.chunk() — heading-delimited passages, capped so none dominates."""

    def test_markdown_headings_open_a_passage_and_lose_their_hashes(self):
        chunks = trace.chunk(["## 5. Runtime Orchestrator",
                              "It schedules ticks and commits state."])
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["heading"], "5. Runtime Orchestrator")
        self.assertEqual(chunks[0]["text"], "It schedules ticks and commits state.")

    def test_a_bare_numbered_line_is_a_heading_too(self):
        # Extracted .docx has no hashes. A detector that needs them reports one
        # passage for a 400-page document.
        chunks = trace.chunk(["5.1 Responsibility", "It commits state."])
        self.assertEqual(chunks[0]["heading"], "5.1 Responsibility")

    def test_appendix_headings_are_recognised(self):
        chunks = trace.chunk(["Appendix C", "The interface catalogue."])
        self.assertEqual(chunks[0]["heading"], "Appendix C")

    def test_text_before_the_first_heading_is_kept_as_front_matter(self):
        # Document control and version tables live here, and they are where a
        # revision's own account of what changed is written.
        chunks = trace.chunk(["Document version 1.4. Supersedes 1.3.",
                              "## 1. Scope", "In scope: the drainage network."])
        self.assertEqual(chunks[0]["heading"], "(front matter)")
        self.assertEqual(chunks[0]["start"], 1)

    def test_start_and_end_bracket_the_passage_one_based(self):
        chunks = trace.chunk(["front", "## 2. Body", "line one", "line two"])
        self.assertEqual((chunks[0]["start"], chunks[0]["end"]), (1, 1))
        self.assertEqual((chunks[1]["start"], chunks[1]["end"]), (2, 4))

    def test_a_long_line_is_prose_not_a_heading(self):
        # The 80-character bound is the only thing separating a heading from a
        # numbered list item or a sentence that opens with a figure.
        long_line = ("5.1 The Sensor Fabric ingests telemetry from third-party "
                     "operators and applies quality flags before use.")
        self.assertGreater(len(long_line), 80)
        chunks = trace.chunk(["## 4. Overview", "body", long_line])
        self.assertEqual(len(chunks), 1)
        self.assertIn(long_line, chunks[0]["text"])

    def test_an_empty_passage_is_dropped(self):
        # Two headings in a row produce nothing between them, and an empty
        # passage retrieved is a passage the model has to explain away.
        self.assertEqual(trace.chunk(["## 1. A", "## 2. B", ""]), [])

    def test_the_character_cap_splits_unwrapped_prose(self):
        # A line-only cap assumes every document wraps at ~80 characters.
        # Contracts do not: a 60-line window over unwrapped prose held 25,000
        # characters, one "passage" that was most of the document.
        chunks = trace.chunk(["## 1. Terms"] + ["x" * 400] * 20)
        self.assertGreater(len(chunks), 1)
        for item in chunks:
            self.assertLessEqual(len(item["text"]), 3000 + 400)

    def test_the_line_cap_splits_wrapped_prose(self):
        chunks = trace.chunk(["## 1. Terms"] + ["short line"] * 130)
        self.assertGreater(len(chunks), 1)
        for item in chunks:
            self.assertLessEqual(len(item["text"].splitlines()), 60)

    def test_a_split_passage_keeps_the_heading_it_came_from(self):
        # Otherwise the second half of a section is retrieved with no idea which
        # component it describes, which is the failure section grouping exists
        # to fix.
        chunks = trace.chunk(["## 6. Sensor Fabric"] + ["short line"] * 130)
        self.assertTrue(all(c["heading"] == "6. Sensor Fabric" for c in chunks))

    def test_fragile_a_sentence_starting_with_a_number_becomes_a_heading(self):
        # KNOWN FRAGILITY, characterised not fixed. The sentence is promoted to
        # a heading, which means it leaves the chunk TEXT entirely — retrieval
        # embeds text, so the sentence becomes unfindable, and the passage after
        # it is attributed to a heading that is really a claim about node
        # counts. Any prose opening with a figure hits this: "3 of the 5 gauges
        # failed", "2029 targets are unchanged", "15 minutes is the ceiling".
        chunks = trace.chunk(["## 9. Deployment topology",
                              "The topology is regional.",
                              "3 of the 5 nodes were rebuilt in place.",
                              "Rebuild is manual."])
        self.assertEqual([c["heading"] for c in chunks],
                         ["9. Deployment topology",
                          "3 of the 5 nodes were rebuilt in place."])
        self.assertNotIn("3 of the 5 nodes", chunks[1]["text"])
        self.assertNotIn("3 of the 5 nodes", chunks[0]["text"])


class TraceSectionIndex(unittest.TestCase):
    """section_of() / section_index() — which passages travel together."""

    def test_a_subsection_belongs_to_its_top_level_section(self):
        self.assertEqual(trace.section_of("5.1 Responsibility"), "5")

    def test_a_bare_numbered_heading_belongs_to_itself(self):
        self.assertEqual(trace.section_of("5 Runtime Orchestrator"), "5")

    def test_bug_a_number_followed_by_a_period_has_no_section(self):
        # BUG (reported, not fixed): SECTION_ID requires whitespace after the
        # number or after a ".n" subsection, so the "5. Title" form — what
        # markdown headings use, and what extract.py produces from a numbered
        # Word style — yields None. Those passages are dropped from the section
        # index entirely, and `--retrieval section` can only retrieve passages
        # that are IN the index.
        self.assertIsNone(trace.section_of("5. Runtime Orchestrator"))
        self.assertIsNone(trace.section_of("Appendix C — Interfaces"))

    def test_bug_whole_sections_of_the_fixture_are_unreachable_by_section(self):
        # The same defect measured on the committed fixture rather than argued
        # from the regex. Sections that have subsections survive, because "5.1"
        # parses and drags section 5 into the index; sections written as a
        # single "N. Title" block have no member at all. Section 9 is the
        # passage behind a planted defect — "Component maturity is assessed in
        # Section 9" — and in section mode it cannot be retrieved.
        index = trace.section_index(trace.chunk(fixture_lines()))
        self.assertIn("5", index)          # has 5.1, so section 5 exists
        self.assertNotIn("9", index)       # no subsections, so section 9 does not
        self.assertNotIn("1", index)
        self.assertNotIn("2", index)


class SweepHeadings(unittest.TestCase):
    """sweep.headings_of() — the set compared to find a deleted section.

    This one answers "what did the revision quietly remove?", so a heading it
    cannot see is a deletion it cannot report, and the output still reads as a
    clean comparison.
    """

    def setUp(self):
        self.headings_of = lift(sweep, "cmd_xref", "headings_of")

    def test_both_document_families_are_detected(self):
        self.assertEqual(
            self.headings_of(["## Summary", "5.1 Responsibility",
                              "Appendix C", "Annex B", "ordinary prose"]),
            ["Summary", "5.1 Responsibility", "Appendix C", "Annex B"])

    def test_the_extractor_banner_is_not_a_heading(self):
        # extract.py prints "########## file.docx ##########" between inputs.
        # Counted as a heading it appears once per document and reads as a
        # section added or removed on every comparison.
        self.assertEqual(self.headings_of(["########## v1.docx ##########"]), [])

    def test_internal_whitespace_is_normalised(self):
        self.assertEqual(self.headings_of(["##   5.1    Responsibility  "]),
                         ["5.1 Responsibility"])

    def test_repeated_headings_collapse_to_one_entry(self):
        # "Responsibility" recurs under every component. Compared as a multiset
        # of occurrences, adding one component reports every later one as
        # changed.
        self.assertEqual(self.headings_of(["## Summary", "## Summary"]),
                         ["Summary"])

    def test_a_table_of_contents_line_is_excluded(self):
        # A ToC repeats every heading with the page number welded on. Left in,
        # it doubles the heading set and buries a real deletion in churn.
        self.assertEqual(
            self.headings_of(["5. Runtime Orchestrator ..........11"]), [])

    def test_bug_a_heading_ending_in_an_identifier_is_discarded(self):
        # BUG (reported, not fixed): the ToC filter drops any line ending in a
        # non-space, non-digit character followed by up to four digits. That was
        # meant to catch "...summary11", but it also catches every heading that
        # ends in an identifier or a version — the punctuation before the digits
        # satisfies the same pattern. Such a heading is invisible in BOTH
        # revisions, so deleting the section is reported as no change at all,
        # which is the one answer this comparison exists to rule out.
        self.assertEqual(self.headings_of(["12. Interface catalogue IF-PA-014"]),
                         [])
        self.assertEqual(self.headings_of(["Appendix D-2"]), [])
        self.assertEqual(self.headings_of(["1.4 Supersedes 1.3"]), [])
        # And the ToC form it was aimed at survives whenever a space separates
        # the page number, so it does not reliably do its own job either.
        self.assertEqual(self.headings_of(["5.1 Responsibility 12"]),
                         ["5.1 Responsibility 12"])

    def test_fragile_a_sentence_starting_with_a_number_becomes_a_heading(self):
        # Same fragility as trace.chunk, different consequence: a body sentence
        # that changes wording between revisions is reported as a section
        # removed and a section added.
        self.assertEqual(self.headings_of(["3 of the 5 nodes were rebuilt."]),
                         ["3 of the 5 nodes were rebuilt."])


class InventorySections(unittest.TestCase):
    """inventory.split_sections() — one record per heading, in principle."""

    HEADING = "## 2. Introduction"
    BODY = ["The twin models the drainage and coastal defence network of the "
            "metropolitan area."] * 5

    def test_a_section_carries_its_heading_and_its_own_line_span(self):
        sections = inventory.split_sections([self.HEADING] + self.BODY)
        self.assertEqual(sections[0]["heading"], "2. Introduction")
        self.assertEqual((sections[0]["start"], sections[0]["end"]), (1, 6))

    def test_a_section_with_almost_no_text_is_not_emitted(self):
        # Sixty characters of content is the floor. A section of one short line
        # is a model call that cannot produce anything, and this pipeline makes
        # one call per section.
        self.assertEqual(inventory.split_sections(["## 1. A", "Short."]), [])

    def test_the_character_cap_splits_a_long_section(self):
        sections = inventory.split_sections(["## 1. Terms"] + ["x" * 400] * 6)
        self.assertGreater(len(sections), 1)

    def test_bug_a_heading_arriving_too_soon_is_swallowed_as_body_text(self):
        # BUG (reported, not fixed): a new heading only opens a new section once
        # min_lines have accumulated. A heading followed by a blank line and
        # then a subheading — the ordinary shape of every component section in
        # the fixture — arrives with one line buffered, falls through to the
        # body branch, and is appended AS TEXT. The subsection's content is then
        # reported under the parent's heading and the parent's start line.
        #
        # Measured on the committed fixture: 88 markdown headings produce 74
        # sections, and 15 headings — "5.1 Responsibility" through
        # "25.1 Reporting responsibility" — never become a section heading at
        # all. undefined.py locates every nomination by section, so those
        # nominations are cited against the wrong heading and the wrong line.
        lines = ["## 2. Beta", "", "### 3. Gamma"] + [
            "Gamma body line padded out past the sixty character floor."] * 5
        sections = inventory.split_sections(lines)
        self.assertEqual([s["heading"] for s in sections], ["2. Beta"])
        self.assertIn("3. Gamma", sections[0]["text"])

    def test_bug_the_fixtures_own_subsections_are_swallowed(self):
        sections = inventory.split_sections(fixture_lines())
        emitted = {s["heading"] for s in sections}
        self.assertNotIn("5.1 Responsibility", emitted)
        self.assertIn("5. Runtime Orchestrator", emitted)


if __name__ == "__main__":
    unittest.main()
