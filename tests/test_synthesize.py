"""synthesize.py — _flat() and reached(), the anchor matcher.

reached() decides what the ground-truth score means. Its predecessor joined
every candidate into one string and asked whether 30% of a defect title's words
appeared anywhere in it; a bag of the document's own nouns, representing no
findings at all, scored 6/6 against this answer key. So the two load-bearing
cases are not symmetric niceties — they are the whole point:

    a word bag must NOT reach a defect      (or the scorer measures nothing)
    a reformatted quote MUST reach it       (or the scorer measures nothing else)

A matcher that fails the first flatters the tool. One that fails the second
under-reports it, and the honest-looking number is the more dangerous of the
two, because nobody audits a score that came out low.
"""

import os
import random
import unittest

import synthesize

FIXTURE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "fixtures", "floodtwin", "deliverable-v1.md")

# A real planted defect from the fixture's ground truth, quoted as the register
# records it — including the line break, which is what makes _flat() necessary.
ANCHOR = "Component maturity is assessed in Section 9."
WRAPPED_ANCHOR = "selections remain open under\nCAL-G-07"


class Flat(unittest.TestCase):
    """_flat() — only the words are stable, so only the words are compared."""

    def test_collapses_case_punctuation_and_line_breaks(self):
        self.assertEqual(synthesize._flat("Component maturity is assessed "
                                          "in Section 9."),
                         "component maturity is assessed in section 9")

    def test_a_wrapped_anchor_and_a_reflowed_quote_flatten_alike(self):
        # This is the entire reason the function exists: the anchor is copied
        # out of the document and carries its wrapping, while the finding quotes
        # the same sentence after the parser reflowed it.
        self.assertEqual(synthesize._flat("selections remain open under\n"
                                          "CAL-G-07"),
                         synthesize._flat("selections remain open under "
                                          "CAL-G-07"))

    def test_digits_survive(self):
        # Section and identifier numbers are most of what makes an anchor
        # distinctive; stripping them would leave prose that matches anywhere.
        self.assertEqual(synthesize._flat("IF-PA-014"), "if pa 014")

    def test_empty_and_missing_flatten_to_empty(self):
        self.assertEqual(synthesize._flat(""), "")

    def test_bug_none_flattens_to_the_word_none(self):
        # BUG (reported, not fixed): _flat() stringifies before matching, so a
        # member field present with a null value becomes the literal haystack
        # "none" rather than an empty one. reached() guards the anchor side with
        # `or ""` but not the member side. Harmless for anchors longer than a
        # few characters, which is all of them today — pinned so it is a known
        # quantity rather than a surprise.
        self.assertEqual(synthesize._flat(None), "none")


class ReachedRejectsAWordBag(unittest.TestCase):
    """The case that killed the previous scorer."""

    def setUp(self):
        self.defect = {"class": "D3", "anchor": ANCHOR}

    def test_rejects_the_anchors_own_words_in_scrambled_order(self):
        # The adversary is not a random string — it is the anchor's exact
        # vocabulary with none of its phrasing. Every word is present, so any
        # word-overlap metric scores this 100%.
        words = synthesize._flat(ANCHOR).split()
        random.Random(4).shuffle(words)
        bag = " ".join(words)
        self.assertNotEqual(bag, synthesize._flat(ANCHOR),
                            "shuffle produced the original; pick another seed")
        self.assertFalse(synthesize.reached(self.defect, "D3", bag, []))

    def test_rejects_a_bag_of_the_documents_own_words(self):
        # The real shape: 400 words sampled from the deliverable itself,
        # representing no finding at all. Read from the committed fixture rather
        # than invented, because the claim being tested is about this document's
        # vocabulary, and inventing the vocabulary would test the invention.
        with open(FIXTURE, encoding="utf-8") as handle:
            words = handle.read().split()
        bag = " ".join(random.Random(11).sample(words, 400))
        self.assertFalse(synthesize.reached(self.defect, "D3", bag, []))
        self.assertFalse(synthesize.reached(self.defect, "D3", "candidate",
                                            [{"quote": bag}]))

    def test_rejects_a_finding_that_shares_only_the_identifier(self):
        # Naming CAL-G-07 is not the same as reaching the passage that defers to
        # it; half the document's findings mention identifiers.
        defect = {"class": "D2", "anchor": WRAPPED_ANCHOR}
        self.assertFalse(synthesize.reached(
            defect, "D2", "x",
            [{"quote": "CAL-G-07 is referenced but the register defines "
                       "CAL-G-01 to CAL-G-05"}]))


class ReachedAcceptsARealFinding(unittest.TestCase):
    """The other half. A matcher that only rejects is not a matcher."""

    def test_accepts_a_quote_of_the_anchor_with_the_wrapping_gone(self):
        defect = {"class": "D2", "anchor": WRAPPED_ANCHOR}
        self.assertTrue(synthesize.reached(
            defect, "D2", "x",
            [{"quote": "the text says selections remain open under CAL-G-07, "
                       "which is never defined"}]))

    def test_accepts_the_anchor_reformatted_into_a_longer_sentence(self):
        defect = {"class": "D3", "anchor": ANCHOR}
        self.assertTrue(synthesize.reached(
            defect, "D3", "x",
            [{"quote": "Section 9 states that \"Component  maturity is "
                       "assessed in Section 9\" but Section 9 is deployment "
                       "topology"}]))

    def test_the_finding_label_alone_can_carry_the_evidence(self):
        defect = {"class": "D3", "anchor": ANCHOR}
        self.assertTrue(synthesize.reached(
            defect, "D3", "component maturity is assessed in section 9", []))

    def test_any_of_the_five_member_fields_carries_evidence(self):
        # quote, claim, name, capability, label — one per lens. A lens whose
        # field is dropped from that list scores zero while looking healthy,
        # which is how two scorers here came to measure nothing.
        defect = {"class": "D3", "anchor": ANCHOR}
        for field in ("quote", "claim", "name", "capability", "label"):
            with self.subTest(field=field):
                self.assertTrue(synthesize.reached(
                    defect, "D3", "x", [{field: ANCHOR}]))


class ReachedGates(unittest.TestCase):
    """Class, span, and the refusal to guess."""

    def test_class_must_match(self):
        # A coherence finding that happens to quote a compliance defect's
        # passage has not found that defect.
        defect = {"class": "D3", "anchor": ANCHOR}
        self.assertFalse(synthesize.reached(defect, "D5", ANCHOR, []))

    def test_a_locator_inside_the_span_reaches_the_defect(self):
        defect = {"class": "D2", "lines": [120, 140]}
        self.assertTrue(synthesize.reached(defect, "D2", "x",
                                           [{"_locator": "doc:131"}]))

    def test_the_span_is_inclusive_at_both_ends(self):
        defect = {"class": "D2", "lines": [10, 20]}
        for line, expected in ((9, False), (10, True), (20, True), (21, False)):
            with self.subTest(line=line):
                self.assertIs(synthesize.reached(
                    defect, "D2", "x", [{"_locator": f"doc:{line}"}]), expected)

    def test_bug_a_version_digit_in_the_slug_is_read_as_a_line_number(self):
        # BUG (reported, not fixed): the span test pulls EVERY digit run out of
        # the locator, and a slug like "deliverable-v1:400" contributes a "1".
        # Any defect whose span starts at or below the version number therefore
        # matches on the slug alone, from anywhere in the document — line 400
        # scores against a defect spanning lines 1 to 5. This is precisely the
        # "loose enough to flatter the tool" failure the function's own
        # docstring was written to close, surviving in the span branch.
        wrong = {"class": "D2", "lines": [1, 5]}
        self.assertTrue(synthesize.reached(wrong, "D2", "x",
                                           [{"_locator": "deliverable-v1:400"}]))
        # The control: same locator, a span that excludes the version digit.
        right = {"class": "D2", "lines": [6, 9]}
        self.assertFalse(synthesize.reached(right, "D2", "x",
                                            [{"_locator": "deliverable-v1:400"}]))

    def test_a_defect_with_neither_anchor_nor_span_is_never_reached(self):
        # "Not auto-scorable" has to beat "matched", or the score quietly
        # includes defects nothing could have been checked against.
        self.assertFalse(synthesize.reached(
            {"class": "D2"}, "D2", ANCHOR, [{"quote": ANCHOR}]))

    def test_a_malformed_span_is_ignored_rather_than_trusted(self):
        defect = {"class": "D2", "lines": [120]}
        self.assertFalse(synthesize.reached(defect, "D2", "x",
                                            [{"_locator": "doc:120"}]))

    def test_coverage_threshold_is_a_fraction_of_the_anchor(self):
        # Pins the constant to its behaviour rather than to its value: a
        # haystack holding just under 60% of the anchor contiguously must fail
        # and one holding just over must pass. Written this way because the
        # number is meaningless on its own — what matters is that a truncated
        # quote is still a quote and a fragment is not.
        anchor = "the calibration pipeline emits no coverage record at all"
        flat = synthesize._flat(anchor)
        cut = int(synthesize.ANCHOR_COVERAGE * len(flat))
        defect = {"class": "D5", "anchor": anchor}
        self.assertTrue(synthesize.reached(defect, "D5", flat[:cut + 2], []))
        self.assertFalse(synthesize.reached(defect, "D5", flat[:cut - 2], []))


if __name__ == "__main__":
    unittest.main()
