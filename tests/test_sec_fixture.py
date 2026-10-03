"""fixtures/sec/fetch.py: what the staff's second letter says about each comment.

The labels of that fixture are read out of letters by pattern, and a pattern
that is too narrow fails in one direction: a reissued comment is called
addressed. It happened with the hyphen in "re-issue", and a rebuild of the same
exchanges from the letters themselves found it had happened four more ways:

    the reissue in the NEXT sentence    "We note your response to prior comment
                                        24. We reissue the first bullet of the
                                        prior comment in part."
    a misspelling                       "We resissue prior comment 30 in full."
    a comment of an OLDER letter        "prior comment 15 of our letter dated
                                        March 14, 2023", counted as comment 15
                                        of the letter being answered
    the first-round letter itself       taken to be the nearest staff letter
                                        before this one, which was a notice
                                        that a review had closed

Every sentence quoted below is the staff's, from a letter in the default slice,
with the names taken out. The page headers are the shape EDGAR's text extract
gives them; the people and the company in them are invented.

No network: `get` is replaced wherever a test reaches it.
"""

import contextlib
import csv
import io
import json
import os
import re
import sys
import tempfile
import unittest

from tests.support import script

fetch = script("fixtures/sec/fetch.py")

OPENING = """\
United States securities and exchange commission logo

                           {date}

       Jane Doe
       Chief Executive Officer
       Example Corp.

       Dear Jane Doe:

            We have reviewed your amended registration statement and have the
following
       comments.

              After reviewing any amendment to your registration statement and
the information you
       provide in response to this letter, we may have additional comments.
{named}
       Amendment No. 2 to Registration Statement on Form S-1

"""
NAMED = """\
Unless we note otherwise,
       any references to prior comments are to comments in our {}
letter.
"""
CLOSING = """\
       Please contact Sam Roe at 202-555-0100 with any other
questions.

                                                             Sincerely,
"""


def staff_letter(*comments, date="May 8, 2024", named="April 17, 2024"):
    """A staff letter as the extract lays one out: the list number, a gap, the
    comment. A comment given as a tuple is written out as its own lines."""
    out = [OPENING.format(date=date, named=NAMED.format(named) if named else "")]
    for number, comment in enumerate(comments, 1):
        lines = list(comment) if isinstance(comment, tuple) else [comment]
        out.append(f"{number}.       {lines[0]}\n")
        out.extend(f"{line}\n" for line in lines[1:])
    return "".join(out) + CLOSING


def verdicts(text):
    labels, _ = fetch.classify(text)
    return {number: label["verdict"] for number, label in labels.items()}


class ReissueOutsideTheCitingSentence(unittest.TestCase):
    """The verdict on a comment is in the numbered comment that cites it, not
    in the one sentence that carries its number."""

    def test_a_reissue_in_the_next_sentence_reaches_the_comment_cited_before_it(self):
        labels, _ = fetch.classify(staff_letter(
            "We note your response to prior comment 24. We reissue the first "
            "bullet of the prior comment in part. Please revise to disclose "
            "the feedback the Company received."))
        self.assertEqual(labels[24]["verdict"], "partial")
        self.assertEqual(
            labels[24]["staff_sentence"],
            "We note your response to prior comment 24. We reissue the first "
            "bullet of the prior comment in part.")

    def test_a_reissue_further_on_is_quoted_with_the_gap_marked(self):
        labels, _ = fetch.classify(staff_letter(
            "We note your response to prior comment 8 and revised disclosure "
            "on page 16. Please disclose whether you are relying on an opinion "
            "of counsel with respect to this conclusion. Additionally, we "
            "reissue certain portions of the comment related to the Trial "
            "Measures. Revise to state whether the offering is contingent."))
        self.assertEqual(labels[8]["verdict"], "partial")
        self.assertEqual(
            labels[8]["staff_sentence"],
            "We note your response to prior comment 8 and revised disclosure "
            "on page 16. [...] Additionally, we reissue certain portions of "
            "the comment related to the Trial Measures.")

    def test_it_does_not_reach_a_comment_cited_in_another_numbered_comment(self):
        self.assertEqual(verdicts(staff_letter(
            "We note your response to prior comment 5. Please revise.",
            "We note your response to prior comment 6. As we are unable to "
            "locate responsive disclosure, we reissue our comment.")),
            {5: "addressed", 6: "not_addressed"})

    def test_a_reissue_that_names_one_of_two_cited_comments_leaves_the_other(self):
        self.assertEqual(verdicts(staff_letter(
            "We note your responses to prior comments 5 and 6. Please revise "
            "page 4. We reissue prior comment 6.")),
            {5: "addressed", 6: "not_addressed"})

    def test_one_that_names_none_reaches_every_comment_cited_around_it(self):
        self.assertEqual(verdicts(staff_letter(
            "We note your response to prior comments 8 and 30. We reissue the "
            "comments in part.")),
            {8: "partial", 30: "partial"})

    def test_the_word_used_of_something_else_reissues_nothing(self):
        """The reach across sentences is what makes this matter: a staff
        accountant asking about a reissued audit report is not reissuing a
        comment, and the number is two sentences away."""
        labels, notes = fetch.classify(staff_letter(
            "We note your response to prior comment 12. Please tell us why the "
            "audit report was reissued. Tell us when the reissued financial "
            "statements were filed."))
        self.assertEqual(labels[12]["verdict"], "addressed")
        # ...and each is said, with its sentence, so that a wrong call here
        # can be seen.
        self.assertEqual(notes, [
            ("sentence with the word, not read as the staff reissuing a comment",
             "Please tell us why the audit report was reissued."),
            ("sentence with the word, not read as the staff reissuing a comment",
             "Tell us when the reissued financial statements were filed.")])

    def test_a_reissue_with_nothing_cited_beside_it_is_noted_not_dropped(self):
        """The staff reissued something and the reader cannot say what. No row
        can be made of it; a note can."""
        labels, notes = fetch.classify(staff_letter(
            "We note the table on page 3. As we are unable to locate "
            "responsive disclosure, we reissue our comment."))
        self.assertEqual(labels, {})
        self.assertEqual(notes, [(
            "reissue with no prior comment cited beside it",
            "As we are unable to locate responsive disclosure, we reissue our "
            "comment.")])

    def test_the_harshest_reading_wins_across_the_letter(self):
        self.assertEqual(verdicts(staff_letter(
            "We note your response to prior comment 7. Please revise.",
            "We note the table on page 3. We reissue prior comment 7.")),
            {7: "not_addressed"})

    def test_which_numbered_comment_ruled_is_recorded(self):
        labels, _ = fetch.classify(staff_letter(
            "Please revise the cover page.",
            "We note your response to prior comment 9, which we reissue. "
            "Please revise the Summary."))
        self.assertEqual(labels[9]["round2_comment"], 2)
        self.assertEqual(
            labels[9]["staff_comment"],
            "We note your response to prior comment 9, which we reissue. "
            "Please revise the Summary.")


class TheWordsForReissuing(unittest.TestCase):

    def test_the_misspelling_one_letter_uses(self):
        self.assertEqual(verdicts(staff_letter(
            "Although your response letter advises that page 86 was revised to "
            "address prior comment 30, we are unable to locate responsive "
            "revisions. We resissue prior comment 30 in full.")),
            {30: "not_addressed"})

    def test_the_pattern_this_replaced_did_not_match_it(self):
        """What the test above would have to fail against. If this ever
        matches, the test above has stopped telling the two patterns apart."""
        replaced = re.compile(r"re-?issu\w*|reiterat\w*", re.I)
        self.assertIsNone(replaced.search("We resissue prior comment 30 in full."))

    def test_the_spellings_already_in_use(self):
        self.assertEqual(verdicts(staff_letter(
            "We note your response to prior comment 1, which we reissue.",
            "We note your response to prior comment 11 and re-issue in part.",
            "We reiterate prior comment 4.",
            "We note your response to prior comment 7. Please revise page 2.")),
            {1: "not_addressed", 11: "partial", 4: "not_addressed", 7: "addressed"})

    def test_the_ways_of_saying_in_part(self):
        for sentence in (
                "We note your response to prior comment 3 and reissue in part.",
                "We note your response to prior comment 3, which we reissue "
                "with respect to the first bullet.",
                "We note your response to prior comment 3. We reissue certain "
                "portions of the comment.",
                "We note your response to prior comment 3. We reissue the "
                "second bullet of the prior comment."):
            with self.subTest(sentence=sentence):
                self.assertEqual(verdicts(staff_letter(sentence)), {3: "partial"})

    def test_in_full_is_not_in_part(self):
        self.assertEqual(verdicts(staff_letter(
            "We note your response to prior comment 23, which we reissue in "
            "full with respect to both bullets.")),
            {23: "not_addressed"})


class ACommentOfAnotherLetter(unittest.TestCase):

    def test_a_comment_of_an_older_letter_is_not_a_comment_of_this_one(self):
        labels, notes = fetch.classify(staff_letter(
            "We note your response to prior comment 15 of our letter dated "
            "March 14, 2023. Please revise your disclosures accordingly.",
            "We note your response to prior comment 16. Please tell us more.",
            named="November 29, 2023"))
        self.assertEqual(sorted(labels), [16])
        self.assertEqual(notes, [(
            "citation of another letter's comment",
            "We note your response to prior comment 15 of our letter dated "
            "March 14, 2023.")])

    def test_the_letter_being_answered_named_by_its_date_still_counts(self):
        self.assertEqual(verdicts(staff_letter(
            "We note your response to prior comment 16 of our March 13, 2024 "
            "letter. Please revise.",
            "We note your response to prior comment 17 of our letter dated "
            "March 13, 2024, which we reissue.",
            named="March 13, 2024,")),
            {16: "addressed", 17: "not_addressed"})

    def test_another_letter_mentioned_beside_the_citation_changes_nothing(self):
        self.assertEqual(verdicts(staff_letter(
            "We note your response to prior comment 29, and your reference to "
            "a response letter dated May 11, 2021 with respect to a comment "
            "letter issued to an affiliate. Please remove the comparison.")),
            {29: "addressed"})

    def test_reissuing_another_letters_comment_reissues_nothing_here(self):
        self.assertEqual(verdicts(staff_letter(
            "We note your response to prior comment 3. We reissue prior "
            "comment 9 of our letter dated May 1, 2023.")),
            {3: "addressed"})

    def test_a_letter_that_names_none_cannot_vouch_for_a_dated_citation(self):
        """With nothing to compare the date against, the citation is somebody
        else's until shown otherwise. The undated one beside it stays."""
        self.assertEqual(verdicts(staff_letter(
            "We note your response to prior comment 15 of our letter dated "
            "March 14, 2023. Please revise.",
            "We note your response to prior comment 2, which we reissue.",
            named=None)),
            {2: "not_addressed"})


class WhichNumbersAreCited(unittest.TestCase):

    def cited(self, sentence):
        return fetch.cites(sentence, "2024-04-17")[0]

    def test_a_number_spelled_out(self):
        """One letter in a wider slice spells every one of them, and read with
        digits only it had two reissues and no comment for either."""
        self.assertEqual(verdicts(staff_letter(
            "We note your response to prior comment one, including your added "
            "disclosure. Please revise.",
            "We note your response to prior comment five and reissue it in part.",
            "We note your responses to prior comments seven and seventeen, "
            "which we reissue.")),
            {1: "addressed", 5: "partial", 7: "not_addressed", 17: "not_addressed"})

    def test_a_comment_cited_without_the_word_prior(self):
        """Six of the 375 rows of a 40-letter slice: three in the default
        slice, and among the other three a reissue."""
        self.assertEqual(verdicts(staff_letter(
            "We note your response to comment 4 and reissue it in part.",
            "We continue to evaluate your response to comment 31 and may have "
            "further comments.",
            "We note your responses to comments 18 and 21. Please revise.")),
            {4: "partial", 31: "addressed", 18: "addressed", 21: "addressed"})

    def test_but_not_every_mention_of_a_numbered_comment(self):
        labels, notes = fetch.classify(staff_letter(
            "Please revise your disclosure consistent with your September 8, "
            "2023 response to comment 59 in our May 24, 2023 letter.",
            "Please refer to comment 1 of our letter dated November 14, 2023.",
            "Also in connection with comment 12, please clarify who owns the "
            "shares. We note your response to comments from the exchange."))
        self.assertEqual(labels, {})
        self.assertEqual([what for what, _ in notes],
                         ["citation of another letter's comment"])

    def test_lists_and_ranges(self):
        for sentence, want in (
                ("We note your response to prior comments 8 and 30.", [8, 30]),
                ("your responses to prior comments 3, 4 and 7.", [3, 4, 7]),
                ("your responses to prior comments 3, 4, and 7.", [3, 4, 7]),
                ("in response to prior comments 12 and 13, including that you "
                 "do not appear to have relied upon an opinion", [12, 13]),
                ("your responses to prior comments 5 through 8.", [5, 6, 7, 8]),
                ("your responses to prior comments 5-8.", [5, 6, 7, 8]),
                ("revised to address prior comment 30, we are unable", [30]),
                ("your response to prior comment 9; however, we could", [9]),
                ("your response to prior comment No. 5.", [5])):
            with self.subTest(sentence=sentence):
                self.assertEqual(self.cited(sentence), want)

    def test_a_figure_after_the_citation_is_not_a_comment(self):
        for sentence, want in (
                ("prior comment 27 and 1,802,444 restricted shares", [27]),
                ("prior comment 4 and 2023 annual report", [4]),
                ("prior comment 5 and 10% holders", [5]),
                ("prior comment 6 and 2.5 million shares", [6])):
            with self.subTest(sentence=sentence):
                self.assertEqual(self.cited(sentence), want)


class WhereACommentStarts(unittest.TestCase):

    def test_a_wrapped_line_that_starts_with_the_next_number_opens_nothing(self):
        """From the default slice. Comment 17 ends "...footnotes 12, 13, 14,
        and 18. Please revise", the line wraps before the 18, and comment 18
        is the next thing in the letter. Read as a comment, the wrapped line
        takes the real one's place."""
        seventeen = ("We note that you still include the black line in various "
                     "footnotes, specifically footnotes 2, 3, 4, and",
                     "       2. Please revise your next amendment.")
        bodies = fetch.outline(staff_letter(
            seventeen,
            "We acknowledge your response to prior comment 28. Please address "
            "the following."))
        self.assertEqual(sorted(bodies), [1, 2])
        self.assertTrue(bodies[1].endswith("and 2. Please revise your next "
                                           "amendment."))
        self.assertTrue(bodies[2].startswith("We acknowledge your response"))

    def test_a_numbered_heading_from_the_filing_opens_nothing(self):
        bodies = fetch.outline(staff_letter(
            "Please revise the cover page.",
            ("We note your response to prior comment 19. Please provide details.",
             "Consolidated Financial Statements",
             "3. Summary of significant accounting policies",
             "(s) Recent accounting pronouncements, page F-15"),
            "We note your response to prior comment 20. Please revise."))
        self.assertEqual(sorted(bodies), [1, 2, 3])
        self.assertTrue(bodies[3].startswith("We note your response to prior "
                                             "comment 20."))

    def test_a_number_alone_on_its_line(self):
        text = staff_letter("Please revise page 1.").replace(
            "1.       Please revise page 1.", "   1.\nPlease revise page 1.")
        self.assertEqual(fetch.outline(text), {1: "Please revise page 1."})

    def test_the_closing_is_not_part_of_the_last_comment(self):
        self.assertEqual(fetch.outline(staff_letter("A.", "B."))[2], "B.")

    def test_a_letter_whose_numbering_breaks_is_set_aside_not_labelled(self):
        """Comment 2's number is lost, so 3 and 4 would be read as part of 1,
        and the reissue in 3 would land on the comment that 1 cites."""
        text = staff_letter(
            "We note your response to prior comment 5. Please revise.",
            "Please revise page 9.",
            "We note the table on page 3. We reissue our comment.",
            "Please file the agreement.").replace("2.       Please", "Please")
        self.assertIsNone(fetch.outline(text))
        self.assertEqual(fetch.classify(text), (None, []))

    def test_a_letter_with_no_numbered_comments_is_set_aside(self):
        text = staff_letter() + "We have completed our review of your filing."
        self.assertEqual(fetch.classify(text), (None, []))


HEADER = """\
 Jane Doe
FirstName  LastNameJane Doe
Example Corp.
Comapany
May  8, 2024NameExample Corp.
May 8,
Page 2 2024 Page 2
FirstName LastName
"""
PLAIN = """\
 Jane Doe
Example Corp.
May 8, 2024
Page 4
"""
WOVEN = """\
FirstName LastNameJane Doe
       how your business activities implicate these laws, particularly given
your indication
Comapany    NameExample
       elsewhere        Corp.
February 6, 2024 Page 4 that you do not operate online platforms.
FirstName LastName
"""


class PageHeaders(unittest.TestCase):
    """The extract prints a header wherever a page ends. Inside a sentence, its
    "Corp." is a full stop that the letter never had."""

    def split_by(self, header):
        labels, _ = fetch.classify(staff_letter(
            ("We note your response to prior comment 22, which we reissue",
             header.rstrip("\n"),
             "         in part. Please revise to disclose the trial dates.")))
        return labels[22]["verdict"], labels[22]["staff_sentence"]

    # Two tests, not one with subTests: --mutate counts a subTest failure as a
    # failure of its own, and two failures in one test read as NOT CAUGHT.
    def test_a_header_inside_the_reissuing_sentence_does_not_end_it(self):
        self.assertEqual(self.split_by(HEADER), (
            "partial",
            "We note your response to prior comment 22, which we reissue in part."))

    def test_nor_does_the_plain_header_of_a_later_page(self):
        self.assertEqual(self.split_by(PLAIN), (
            "partial",
            "We note your response to prior comment 22, which we reissue in part."))

    def test_text_woven_through_a_last_page_header_is_kept(self):
        body = fetch.outline(staff_letter(
            ("It remains unclear as a threshold matter why and",
             WOVEN.rstrip("\n"))))[1]
        self.assertIn("how your business activities implicate these laws", body)
        self.assertIn("that you do not operate online platforms.", body)
        self.assertNotIn("FirstName", body)
        self.assertNotIn("Comapany", body)

    def test_a_company_with_a_lower_case_word_keeps_its_lines(self):
        """The block goes whole only when nothing in it could be the letter's
        own text. "Bank of Example" could be, so only the field lines go."""
        header = HEADER.replace("Example Corp.", "Bank of Example")
        body = fetch.outline(staff_letter(
            ("Please revise the", header.rstrip("\n"), "cover page.")))[1]
        self.assertIn("Bank of Example", body)
        self.assertNotIn("LastName", body)


FEED = {"filings": {"recent": {
    "form": ["UPLOAD", "CORRESP", "UPLOAD", "UPLOAD", "CORRESP", "UPLOAD"],
    "filingDate": ["2024-02-15", "2024-01-29", "2023-12-21", "2023-11-29",
                   "2023-11-13", "2023-03-14"],
    "accessionNumber": ["0000000000-24-000005", "0000000001-24-000004",
                        "0000000000-23-000003", "0000000000-23-000002",
                        "0000000001-23-000001", "0000000000-23-000000"],
}}}
NOTICE = OPENING.format(date="December 21, 2023", named="") + (
    "We have completed our review of your filing.\n")


class Network:
    """Stands in for fetch.get: the filing index of one company, and its staff
    letters by accession. Anything else is a failed request."""

    def __init__(self, feed, letters):
        self.feed, self.letters, self.asked = feed, letters, []

    def __call__(self, url, ua, pause=0):
        self.asked.append(url)
        if "/submissions/" in url:
            if self.feed is None:
                raise OSError("no route to host")
            return json.dumps(self.feed)
        for accession, text in self.letters.items():
            if accession.replace("-", "") in url:
                return f"<DOCUMENT>\n<TEXT>\n{text}</TEXT>\n</DOCUMENT>\n"
        raise OSError("404")


class WhichLetterIsBeingAnswered(unittest.TestCase):

    def setUp(self):
        self.real = fetch.get
        self.addCleanup(setattr, fetch, "get", self.real)

    def chain(self, feed, letters, named, before="2024-02-15"):
        fetch.get = Network(feed, letters)
        return fetch.chain_for("1234567", named, before, "test")

    def test_the_letter_of_the_date_named_not_the_nearest_one_before(self):
        """The default slice's own case: three weeks after the comment letter,
        the staff closed its review of a different filing by the same company."""
        upload, reply, why = self.chain(FEED, {
            "0000000000-23-000003": NOTICE,
            "0000000000-23-000002": staff_letter(
                "Please revise.", "Please advise.", date="November 29, 2023",
                named="March 14, 2023"),
        }, "2023-11-29")
        self.assertEqual(why, "")
        self.assertEqual(upload, {"date": "2023-11-29", "comments": 2,
                                  "accession": "0000000000-23-000002"})
        self.assertEqual(reply, {"date": "2024-01-29",
                                 "accession": "0000000001-24-000004"})

    def test_a_letter_filed_the_day_after_the_date_printed_on_it(self):
        feed = {"filings": {"recent": {
            "form": ["UPLOAD", "UPLOAD"],
            "filingDate": ["2024-03-14", "2024-03-12"],
            "accessionNumber": ["0000000000-24-000002", "0000000000-24-000001"]}}}
        upload, _, why = self.chain(feed, {
            "0000000000-24-000001": staff_letter("Please revise.",
                                                 date="March 12, 2024"),
            "0000000000-24-000002": staff_letter("Please revise.",
                                                 date="March 13, 2024"),
        }, "2024-03-13", before="2024-06-13")
        self.assertEqual((upload.get("accession"), why),
                         ("0000000000-24-000002", ""))

    def test_a_notice_dated_the_same_day_is_not_the_comment_letter(self):
        feed = {"filings": {"recent": {
            "form": ["UPLOAD", "UPLOAD"],
            "filingDate": ["2023-12-21", "2023-12-21"],
            "accessionNumber": ["0000000000-23-000002", "0000000000-23-000001"]}}}
        upload, _, _ = self.chain(feed, {
            "0000000000-23-000001": NOTICE,
            "0000000000-23-000002": staff_letter("Please revise.",
                                                 date="December 21, 2023"),
        }, "2023-12-21")
        self.assertEqual(upload.get("accession"), "0000000000-23-000002")

    def test_no_letter_of_that_date_is_left_unpaired_and_says_why(self):
        upload, reply, why = self.chain(FEED, {"0000000000-23-000003": NOTICE},
                                        "2023-12-01")
        self.assertEqual((upload, reply), ({}, {}))
        self.assertEqual(why, "1 staff letter(s) filed near 2023-12-01 could "
                              "not be read")
        _, _, why = self.chain(FEED, {"0000000000-23-000003": NOTICE},
                               "2023-12-21")
        self.assertEqual(why, "no comment letter dated 2023-12-21 in its "
                              "recent filings")

    def test_a_letter_that_names_none_is_left_unpaired_without_asking(self):
        upload, reply, why = self.chain(FEED, {}, None)
        self.assertEqual((upload, reply, why), ({}, {}, "it names no letter"))
        self.assertEqual(fetch.get.asked, [])

    def test_an_index_that_cannot_be_read_is_not_an_absent_letter(self):
        _, _, why = self.chain(None, {}, "2023-11-29")
        self.assertIn("was not read", why)

    def test_the_reply_is_the_first_thing_filed_between_the_two_letters(self):
        """A reply to comments on a draft registration statement is filed as
        DRSLTR, not CORRESP. Three of the default slice's eight are."""
        feed = {"filings": {"recent": {
            "form": ["CORRESP", "UPLOAD", "CORRESP", "DRSLTR", "UPLOAD", "CORRESP"],
            "filingDate": ["2024-05-10", "2024-05-08", "2024-05-01", "2024-04-23",
                           "2024-04-17", "2024-04-01"],
            "accessionNumber": ["0000000001-24-000006", "0000000000-24-000005",
                                "0000000001-24-000004", "0000000001-24-000003",
                                "0000000000-24-000002", "0000000001-24-000001"]}}}
        _, reply, _ = self.chain(feed, {
            "0000000000-24-000002": staff_letter("Please revise.",
                                                 date="April 17, 2024"),
        }, "2024-04-17", before="2024-05-08")
        self.assertEqual(reply, {"date": "2024-04-23",
                                 "accession": "0000000001-24-000003"})

    def test_no_reply_between_the_letters_is_recorded_as_none(self):
        feed = {"filings": {"recent": {
            "form": ["UPLOAD", "UPLOAD"],
            "filingDate": ["2024-02-28", "2024-01-18"],
            "accessionNumber": ["0000000000-24-000002", "0000000000-24-000001"]}}}
        upload, reply, why = self.chain(feed, {
            "0000000000-24-000001": staff_letter("Please revise.",
                                                 date="January 18, 2024"),
        }, "2024-01-18", before="2024-02-28")
        self.assertEqual((upload["accession"], reply, why),
                         ("0000000000-24-000001",
                          {"date": None, "accession": None}, ""))


class AskingEdgar(unittest.TestCase):
    """The search endpoint answers 500 now and then and is fine a moment later.
    It did on the run that re-pinned this fixture."""

    class Response:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"the letter"

    def ask(self, codes):
        """get() against a server that fails with each code in turn, then answers."""
        waits, pending = [], list(codes)

        def urlopen(request, timeout):
            self.assertEqual(request.get_header("User-agent"), "Test Runner")
            if pending:
                raise fetch.urllib.error.HTTPError(
                    request.full_url, pending.pop(0), "no", {}, None)
            return self.Response()

        for owner, name, value in ((fetch.urllib.request, "urlopen", urlopen),
                                   (fetch.time, "sleep", waits.append)):
            self.addCleanup(setattr, owner, name, getattr(owner, name))
            setattr(owner, name, value)
        return fetch.get("https://example.org/letter", "Test Runner"), waits

    def test_a_server_error_is_asked_again_and_the_waits_grow(self):
        body, waits = self.ask([500, 503])
        self.assertEqual((body, waits), ("the letter", [2, 4, 0.2]))

    def test_it_gives_up_after_three_more_tries(self):
        with self.assertRaises(fetch.urllib.error.HTTPError):
            self.ask([500, 500, 500, 500])

    def test_a_refusal_is_not_asked_again(self):
        """403 is the SEC declining the user-agent. Asking again is not the
        answer, and main() has something to say about it."""
        with self.assertRaises(fetch.urllib.error.HTTPError) as refused:
            self.ask([403])
        self.assertEqual(refused.exception.code, 403)


class TheWholeRun(unittest.TestCase):
    """main() against a search result of three letters: one that is labelled,
    one whose numbering breaks, and nothing fetched from anywhere real."""

    FIRST = staff_letter("Please revise.", "Please advise.", "Please file.",
                         date="November 29, 2023", named="March 14, 2023")
    SECOND = staff_letter(
        "We note your response to prior comment 1. Please reconcile.",
        "We note your response to prior comment 15 of our letter dated March "
        "14, 2023. Please revise.",
        "We note your response to prior comment 2. We reissue the comment in "
        "part.",
        "We note your response to prior comment 3, which we reissue.",
        "We note your response to prior comment 9. Please revise.",
        date="February 15, 2024", named="November 29, 2023")
    BROKEN = staff_letter(
        "We note your response to prior comment 5. Please revise.",
        "Please revise page 9.", "We reissue our comment.",
        date="February 20, 2024").replace("2.       Please", "Please")

    def setUp(self):
        hits = [{"_id": f"{accession}:filename2.txt",
                 "_source": {"ciks": ["0001234567"], "file_date": date}}
                for accession, date in (("0000000000-24-000009", "2024-02-20"),
                                        ("0000000000-24-000005", "2024-02-15"))]
        hits.insert(1, {"_id": "0000000000-24-000009:filename1.pdf",
                        "_source": {"ciks": ["0001234567"],
                                    "file_date": "2024-02-20"}})
        search = json.dumps({"hits": {"total": {"value": 903}, "hits": hits}})
        letters = Network(FEED, {"0000000000-24-000009": self.BROKEN,
                                 "0000000000-24-000005": self.SECOND,
                                 "0000000000-23-000003": NOTICE,
                                 "0000000000-23-000002": self.FIRST})

        def get(url, ua, pause=0):
            return search if "search-index" in url else letters(url, ua)

        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        for name, value in (("get", get), ("HERE", self.folder.name),
                            ("argv", ["fetch.py"])):
            owner = sys if name == "argv" else fetch
            self.addCleanup(setattr, owner, name, getattr(owner, name))
            setattr(owner, name, value)
        self.addCleanup(os.environ.pop, "DOSSIER_SEC_UA", None)
        os.environ["DOSSIER_SEC_UA"] = "Test Runner test@example.org"

    def run_main(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = fetch.main()
        return code, out.getvalue(), err.getvalue()

    def test_what_is_labelled_and_what_is_set_aside(self):
        code, out, err = self.run_main()
        self.assertEqual(code, 0)
        self.assertIn("DIGEST MISMATCH", err)      # these are not the pinned letters
        with open(os.path.join(self.folder.name, "comments.csv"),
                  encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(
            [(r["id"], r["verdict"]) for r in rows],
            [("0000000000-24-000005-1", "addressed"),
             ("0000000000-24-000005-2", "partial"),
             ("0000000000-24-000005-3", "not_addressed")])
        self.assertEqual(
            {(r["round1_accession"], r["round1_date"], r["response_accession"])
             for r in rows},
            {("0000000000-23-000002", "2023-11-29", "0000000001-24-000004")})
        self.assertEqual(rows[1]["round2_comment"], "3")
        self.assertEqual(rows[1]["staff_sentence"],
                         "We note your response to prior comment 2. We reissue "
                         "the comment in part.")
        with open(os.path.join(self.folder.name, "labels.json"),
                  encoding="utf-8") as handle:
            summary = json.load(handle)
        self.assertEqual(summary["comments"], 3)
        self.assertEqual(summary["spread"], {"addressed": 1, "partial": 1,
                                             "not_addressed": 1})
        self.assertEqual(summary["negative_share_within_frame"], 66.7)
        self.assertEqual(
            {k: summary["first_round"][k] for k in (
                "letters_paired", "letters_not_paired", "comments_raised",
                "of_which_cited_again")},
            {"letters_paired": 1, "letters_not_paired": 0,
             "comments_raised": 3, "of_which_cited_again": 3})
        self.assertEqual(summary["not_labelled"], {
            "citation of another letter's comment": 1,
            "cited number the first-round letter does not have": 1,
            "letter whose numbering could not be followed": 1})
        self.assertEqual((summary["letters_examined"], summary["letters_used"]),
                         (2, 1))
        self.assertIn("quote both", out)

    def test_check_writes_nothing(self):
        sys.argv = ["fetch.py", "--check"]
        code, _, _ = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual(os.listdir(self.folder.name), [])


if __name__ == "__main__":
    unittest.main()
