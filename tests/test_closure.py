"""closure.py — term expansion, line matching, and the eight-state verdict.

classify() is the only place in the toolkit that says "they did not touch it".
That claim goes into a client-facing cell via writeback.py, so every branch here
is a sentence somebody will read as fact. Eight states, and the difference
between two of them (ABSENT and STILL ABSENT) is a single boolean the register
supplies — get it wrong and a typo in a search term is reported as "still
missing" with the same confidence as a real finding.
"""

import unittest

import closure


class Expand(unittest.TestCase):
    """expand() — one term, plus every variant the lexicon knows."""

    LEXICON = {"behaviour": ["behavior"], "modelling": ["modeling"],
               "sensor fabric": ["telemetry fabric"]}

    def test_lowercases_and_keeps_the_original(self):
        self.assertEqual(closure.expand(["Hydrology Model"], {}),
                         ["hydrology model"])

    def test_whole_term_lookup(self):
        # The lexicon key is the whole phrase here, which is the easy case and
        # the only one the first version handled.
        self.assertEqual(closure.expand(["Sensor Fabric"], self.LEXICON),
                         ["sensor fabric", "telemetry fabric"])

    def test_substitutes_word_by_word_inside_a_phrase(self):
        # The bug the docstring is about: "catchment behaviour" is not a lexicon
        # key, so a whole-term lookup would never reach the US spelling and the
        # finding would report ABSENT against a document that uses it
        # throughout. Word-by-word substitution is what stops that.
        self.assertEqual(closure.expand(["catchment behaviour"], self.LEXICON),
                         ["catchment behavior", "catchment behaviour"])

    def test_substitution_is_one_word_at_a_time(self):
        # LIMITATION, pinned deliberately rather than asserted as correct. Each
        # variant substitutes exactly one word, so a term with two
        # variant-bearing words never produces the fully-substituted form: a
        # document written entirely in the other spelling is not reached. This
        # is the failure the function exists to prevent, surviving in the
        # two-word case. Reported, not fixed — see the suite's report.
        self.assertEqual(
            closure.expand(["behaviour modelling"], self.LEXICON),
            ["behavior modelling", "behaviour modeling", "behaviour modelling"])

    def test_unknown_term_passes_through_unchanged(self):
        self.assertEqual(closure.expand(["CAL-G-07"], self.LEXICON),
                         ["cal-g-07"])

    def test_no_terms_expands_to_nothing(self):
        # Not the same as "expands to everything". hits() uses any(), so an
        # empty term list must match no lines rather than all of them.
        self.assertEqual(closure.expand([], self.LEXICON), [])
        self.assertEqual(closure.hits(["anything at all"], []), {})

    def test_duplicate_terms_collapse(self):
        self.assertEqual(closure.expand(["Fabric", "fabric", "FABRIC"], {}),
                         ["fabric"])


class Hits(unittest.TestCase):
    """hits() — which lines carry any term, numbered as a human would count."""

    LINES = ["## 7. Calibration Pipeline",
             "The pipeline reads CAL-G-07 and stops.",
             "",
             "Unrelated prose about drainage."]

    def test_line_numbers_are_one_based(self):
        # These numbers are printed as locators into a frozen document. Off by
        # one here and every citation in the run points at the line above.
        self.assertEqual(list(closure.hits(self.LINES, ["cal-g-07"])), [2])

    def test_matching_is_case_insensitive_on_the_line(self):
        self.assertEqual(list(closure.hits(self.LINES, ["calibration"])), [1])

    def test_returns_the_line_text_unaltered(self):
        self.assertEqual(closure.hits(self.LINES, ["cal-g-07"])[2],
                         "The pipeline reads CAL-G-07 and stops.")

    def test_terms_must_already_be_lowercase(self):
        # hits() lowercases the line but NOT the term, so it is only ever
        # correct when fed expand() output. Pinned because every caller pairs
        # the two, and a future caller that skips expand() would silently match
        # nothing — which reads as ABSENT, a verdict, not as a mistake.
        self.assertEqual(closure.hits(self.LINES, ["CAL-G-07"]), {})


class Normalise(unittest.TestCase):
    def test_collapses_all_whitespace_and_strips(self):
        self.assertEqual(closure.normalise("  a\t b\n  c  "), "a b c")


class Classify(unittest.TestCase):
    """The state machine. One test per state, plus the precedence between them.

    Every state is a different instruction to a reviewer, so the tests are
    written as the instruction rather than as the branch: "nothing in either
    revision and the register says the finding IS an absence" rather than
    "not old and not new and is_absence".
    """

    def test_no_terms_beats_everything(self):
        # A finding that was never asked a question has no result to report.
        # This is checked before the counts precisely so that an empty term list
        # cannot arrive at ABSENT, which would read as "we looked and found
        # nothing" when nothing was looked for.
        self.assertEqual(closure.classify({}, {}, 120, has_terms=False),
                         ("NO TERMS", None))

    def test_no_terms_beats_too_broad(self):
        many = {n: "x" for n in range(5)}
        self.assertEqual(
            closure.classify(many, many, 3, has_terms=False)[0], "NO TERMS")

    def test_nothing_anywhere_is_absent_when_the_finding_is_not_an_absence(self):
        self.assertEqual(closure.classify({}, {}, 120), ("ABSENT", None))

    def test_nothing_anywhere_is_still_absent_when_the_register_says_so(self):
        # Same two counts, opposite meanings. Only the register can tell them
        # apart, which is why is_absence is a parameter and not an inference.
        self.assertEqual(closure.classify({}, {}, 120, is_absence=True),
                         ("STILL ABSENT", None))

    def test_present_before_gone_now_is_removed(self):
        self.assertEqual(closure.classify({4: "text"}, {}, 120),
                         ("REMOVED", None))

    def test_absent_before_present_now_is_added(self):
        self.assertEqual(closure.classify({}, {9: "text"}, 120),
                         ("ADDED", None))

    def test_identical_passages_are_unchanged_with_no_detail(self):
        self.assertEqual(closure.classify({4: "a b"}, {9: "a b"}, 120),
                         ("UNCHANGED", None))

    def test_unchanged_line_numbers_may_move(self):
        # The comparison is of TEXT, not of position. A revision that inserts a
        # paragraph earlier in the document shifts every line number after it,
        # and reporting that as CHANGED would bury the real edits.
        self.assertEqual(closure.classify({4: "a b"}, {400: "a b"}, 120)[0],
                         "UNCHANGED")

    def test_whitespace_only_edit_is_unchanged_but_says_so(self):
        # Still "they did not touch it", but the reviewer is told the bytes
        # moved, so a diff that looks non-empty is explained rather than
        # contradicting the verdict.
        self.assertEqual(closure.classify({4: "a  b"}, {9: "a b"}, 120),
                         ("UNCHANGED", "reformatted only"))

    def test_different_text_is_changed_and_counts_the_diff(self):
        self.assertEqual(closure.classify({1: "a", 2: "b"},
                                          {1: "a", 2: "c", 3: "d"}, 120),
                         ("CHANGED", "+2 -1"))

    def test_too_broad_when_either_side_exceeds_the_cap(self):
        many = {n: "x" for n in range(5)}
        self.assertEqual(closure.classify(many, {}, 3), ("TOO BROAD", None))
        self.assertEqual(closure.classify({}, many, 3), ("TOO BROAD", None))

    def test_too_broad_beats_absence(self):
        # An absence finding whose terms match half the document has not been
        # answered either, so it must not collect a STILL ABSENT.
        many = {n: "x" for n in range(5)}
        self.assertEqual(
            closure.classify(many, many, 3, is_absence=True)[0], "TOO BROAD")

    def test_the_cap_is_exclusive(self):
        # At exactly the cap the finding is still assessable; only above it is
        # the signal declared unusable. Pinned because an off-by-one here
        # silently converts real verdicts into "not assessed".
        three = {n: "x" for n in range(3)}
        self.assertEqual(closure.classify(three, three, 3)[0], "UNCHANGED")

    def test_every_state_is_reachable_and_declared(self):
        # closure.STATES drives the printed summary and writeback's PHRASING
        # table. A state produced by classify() but missing from STATES is
        # invisible in the summary and lands in a client cell as a bare word.
        many = {n: "x" for n in range(5)}
        produced = {
            closure.classify({}, {}, 120, has_terms=False)[0],
            closure.classify({}, {}, 120)[0],
            closure.classify({}, {}, 120, is_absence=True)[0],
            closure.classify({1: "a"}, {}, 120)[0],
            closure.classify({}, {1: "a"}, 120)[0],
            closure.classify({1: "a"}, {1: "a"}, 120)[0],
            closure.classify({1: "a"}, {1: "b"}, 120)[0],
            closure.classify(many, many, 3)[0],
        }
        self.assertEqual(produced, set(closure.STATES))


if __name__ == "__main__":
    unittest.main()
