"""The aggregation gate may not call a file clean that it has not read.

check-aggregation.py enforces DESIGN §0.1 — no tie decided by insertion order, no
votes filtered before tallying. Its source records the ways it went wrong while
it was being written: reading line by line, and matching only list
comprehensions, each reported clean on the very instance it was written for.
A third way was found later, in CI:

    it found where a statement ends by counting brackets, and it counted the
    ones inside string literals. One regex literal with an unmatched "(" left
    the statement open, nothing below it ever closed it, and whatever was still
    pending at the end of the file was discarded without being scanned.

Nothing errored. Twenty-four of the fifty-two top-level files ended that way, and
in llm.py the gate had read 50 lines of 595. "clean" was printed over 4,138 lines
it had not read, of the 17,095 it was pointed at: the failure the gate exists to
prevent, committed by the gate.

So these tests are about what is READ, more than about what is matched. The two
patterns were never at fault and are exercised only as far as it takes to show
that a statement reached them whole.

The gate is imported by name, hyphen and all, so that this is the same module
object run-tests.py --mutate breaks. test_checkers.load() builds a private copy,
which a mutation never reaches: every entry would read NOT CAUGHT however good
the test was.
"""

import ast
import contextlib
import importlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = importlib.import_module("check-aggregation")

TIE = "tie decided by insertion order"
FILTERED = "votes filtered before tallying"

# The two inputs the defect was reported with. The second differs from the first
# by one line, which holds no site and is not near one.
ALONE = ("import collections\n"
         "def worst(spread):\n"
         "    return spread.most_common(1)[0]\n")
AFTER_A_REGEX = ("import re\n"
                 'SPLIT = re.compile(r"(?<=[.?!])\\s+(?=[A-Z(])")\n'
                 "def worst(spread):\n"
                 "    return spread.most_common(1)[0]\n")


def found(source):
    """[(line, what)] for every site in `source`."""
    return [(line, what) for line, what, _why, _text in GATE.sites(source)[0]]


def first_line(node):
    """Where a statement starts on the page: a decorator sits above the line
    the parser gives its function."""
    return min([node.lineno]
               + [d.lineno for d in getattr(node, "decorator_list", [])])


class OnDisk(unittest.TestCase):
    """The gate as CI runs it: paths in, an exit status and a report out."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, source, name="planted.py"):
        path = os.path.join(self.tmp, name)
        with open(path, "wb" if isinstance(source, bytes) else "w") as handle:
            handle.write(source)
        return path

    def gate(self, *paths):
        """(exit status, report). A traceback is not a report: whatever the
        gate cannot read it has to name, and go on to the next file."""
        out, argv = io.StringIO(), sys.argv
        sys.argv = ["check-aggregation.py", *paths]
        try:
            with contextlib.redirect_stdout(out):
                status = GATE.main()
        except Exception as error:
            self.fail(f"the gate raised instead of reporting: {error!r}")
        finally:
            sys.argv = argv
        return status, out.getvalue()


class TheReportedFailure(OnDisk):
    """Both inputs hold one site. The gate found it in the first only."""

    def test_a_site_on_its_own_is_reported(self):
        status, report = self.gate(self.write(ALONE))
        self.assertEqual(status, 1, report)
        self.assertIn(f"planted.py:3  {TIE}", report)

    def test_a_site_below_a_regex_literal_is_reported(self):
        """This printed "clean" and exited 0."""
        status, report = self.gate(self.write(AFTER_A_REGEX))
        self.assertEqual(status, 1, report)
        self.assertIn(f"planted.py:4  {TIE}", report)
        self.assertEqual(report.count("planted.py:"), 1, report)

    def test_and_nothing_else_is_said_about_that_file(self):
        """One site, on line 4, in a file read to its end. A splitter that left
        the statement open and scanned it at the end of the file as one piece
        would still find the site; it must also not have had to."""
        scanned = GATE.scan([self.write(AFTER_A_REGEX)])
        self.assertEqual([(line, what) for _p, line, what, _w, _t in scanned.hits],
                         [(4, TIE)])
        self.assertEqual(scanned.unread, [])
        self.assertEqual((scanned.files, scanned.statements), (1, 4))


class WhereAStatementEnds(unittest.TestCase):
    """A statement ends where the tokenizer says it does, and nowhere else."""

    def spans(self, source):
        statements, _comments, stopped = GATE.statements(source)
        self.assertIsNone(stopped)
        return [(s.line, s.end) for s in statements]

    def test_a_bracket_inside_a_string_does_not_hold_a_statement_open(self):
        self.assertEqual(self.spans(AFTER_A_REGEX),
                         [(1, 1), (2, 2), (3, 3), (4, 4)])

    def test_a_closing_bracket_inside_a_string_does_not_end_one_early(self):
        """The same miscount in the other direction, and the quieter of the two:
        the comprehension is cut in half and neither half looks like a filter."""
        source = ('kept = [v for v in votes\n'
                  '        if v["why"] != ")"\n'
                  '        and v.get("verdict") in VALID]\n')
        self.assertEqual(self.spans(source), [(1, 3)])
        self.assertEqual(found(source), [(1, FILTERED)])

    def test_a_hash_inside_a_string_is_not_a_comment(self):
        """Comments were cut at the first "#" on the line, wherever it was."""
        source = 'top = lookup.get("#", spread.most_common(1)[0])\n'
        self.assertEqual(found(source), [(1, TIE)])

    def test_a_comment_is_not_code(self):
        source = ("# top = spread.most_common(1)[0]\n"
                  "top = None  # was spread.most_common(1)[0]\n")
        self.assertEqual(self.spans(source), [(2, 2)])
        self.assertEqual(found(source), [])

    def test_a_triple_quoted_string_is_one_token_whatever_it_holds(self):
        source = ('USAGE = """\n'
                  '    tool [options    # neither a bracket nor a comment\n'
                  '"""\n'
                  'def worst(spread):\n'
                  '    return spread.most_common(1)[0]\n')
        self.assertEqual(self.spans(source), [(1, 3), (4, 4), (5, 5)])
        self.assertEqual(found(source), [(5, TIE)])

    def test_a_filter_on_the_second_line_belongs_to_its_comprehension(self):
        """The first attempt read line by line and missed exactly this."""
        source = ('votes = {m: v for m, v in panel.items()\n'
                  '         if v.get("verdict") in VALID}\n')
        self.assertEqual(found(source), [(1, FILTERED)])

    def test_neighbouring_statements_are_not_joined(self):
        """The second attempt read a three-line window, and an `if` on the next
        line made a filter out of a comprehension that had none."""
        source = ('terms = [t.lower() for t in words]\n'
                  'if limit is not None:\n'
                  '    terms = terms[:limit]\n')
        self.assertEqual(self.spans(source), [(1, 1), (2, 2), (3, 3)])
        self.assertEqual(found(source), [])

    def test_a_site_is_reported_on_the_line_it_is_written_on(self):
        """Not on the first line of a statement that may run for a page."""
        source = ('SPREADS = {\n'
                  '    "worst": spread.most_common(1)[0],\n'
                  '}\n')
        self.assertEqual(found(source), [(2, TIE)])

    def test_and_on_that_line_when_a_string_above_it_runs_over_several(self):
        """A string is one token however many lines it covers, and the site
        here sits on the line that string ends on."""
        source = ('rows = fetch("""\n'
                  '    select verdict from votes\n'
                  '""") or spread.most_common(1)[0]\n')
        self.assertEqual(found(source), [(3, TIE)])

    def test_a_line_break_inside_a_call_does_not_hide_it(self):
        """The lines of a statement used to be joined by a space wherever the
        break fell, and the patterns allow none between a dot and a name, or a
        name and its bracket. Three layouts of one site, each read as clean."""
        layouts = ('top = (collections.Counter(votes).\n'
                   '       most_common(1)[0])\n',
                   'top = collections.Counter(votes).most_common \\\n'
                   '    (1)[0]\n',
                   'top = spread.most_common (1) [0]\n')
        self.assertEqual([found(source) for source in layouts],
                         [[(1, TIE)]] * 3)

    def test_nor_does_one_inside_the_literal_a_pattern_names(self):
        source = ('kept = [v for v in votes if v.get(\n'
                  '    "verdict") in wanted]\n')
        self.assertEqual(found(source), [(1, FILTERED)])

    def test_a_keyword_keeps_the_space_before_its_bracket(self):
        """`in (` is not a call. Closing that gap would cost the filter pattern
        the space it requires after `in`."""
        source = 'kept = list(v for v in (first, second) if v is not None)\n'
        self.assertEqual(found(source), [(1, FILTERED)])


class StringsAreNotCode(unittest.TestCase):
    """What a string literal holds is text, including text that looks like a
    site. The gate used to skip its own source because its docstring matched."""

    def test_the_pattern_written_inside_a_string_is_not_a_site(self):
        source = ('"""Never write counts.most_common(1)[0] without a tie rule."""\n'
                  'PLANTED = "kept = [v for v in votes if v is not None]"\n')
        self.assertEqual(found(source), [])

    def test_a_literal_the_pattern_names_still_counts(self):
        """Strings stay in the statement, because two of the alternatives name
        one. Blanking them would have made those two unable to match."""
        self.assertEqual(found('kept = [v for v in votes if v != "error"]\n'),
                         [(1, FILTERED)])
        self.assertEqual(
            found("kept = {m: v for m, v in votes.items()"
                  " if v.get('verdict') is not None}\n"),
            [(1, FILTERED)])

    def test_code_inside_an_f_string_is_code(self):
        """A replacement field is an expression. Before 3.12 the tokenizer
        hands the whole f-string over as one string, so there it is read as
        code throughout rather than not at all."""
        source = 'print(f"top: {spread.most_common(1)[0][0]}")\n'
        self.assertEqual(found(source), [(1, TIE)])

    def test_a_string_running_over_several_lines_does_not_hide_what_follows(self):
        """The patterns stop at a line break, and the statement is one line of
        text. A break inside a string must not put one back in the middle."""
        source = ('kept = [r for r in fetch("""\n'
                  '    select verdict from votes\n'
                  '""") if r is not None]\n')
        self.assertEqual(found(source), [(1, FILTERED)])

    def test_a_site_is_found_past_a_string_that_reads_like_the_start_of_one(self):
        """The leftmost match begins inside the string and is not a site. The
        search has to go on from there, not from where that match ended."""
        source = 'note("for a in b") or [v for v in votes if v != "error"]\n'
        self.assertEqual(found(source), [(1, FILTERED)])


class AFileNotReadToItsEnd(OnDisk):
    """What was not read was not checked, and the gate has to say so."""

    OPEN = ("import collections\n"
            "SPREADS = [\n"
            "    spread.most_common(1)[0],\n")

    def test_a_statement_still_open_at_the_end_is_scanned(self):
        """It was discarded: the loop appended a statement when it closed, and
        this one never did."""
        self.assertEqual(found(self.OPEN), [(3, TIE)])

    def test_and_the_file_is_reported_from_the_line_that_opened_it(self):
        _hits, _count, stopped = GATE.sites(self.OPEN)
        self.assertIsNotNone(stopped)
        self.assertEqual(stopped[0], 2)

    def test_an_open_statement_fails_the_gate_with_no_site_in_it(self):
        status, report = self.gate(self.write("TOTALS = [\n    1,\n"))
        self.assertEqual(status, 1, report)
        self.assertIn("planted.py:1", report)
        self.assertNotIn("clean —", report)

    def test_a_string_that_never_closes_stops_the_scan_and_says_where(self):
        """Nothing after the opening quote is tokenized, so nothing after it
        can be reported as a site. It is reported as unread instead."""
        source = ('LIMIT = 3\n'
                  'USAGE = """never closed\n'
                  'top = spread.most_common(1)[0]\n')
        hits, _count, stopped = GATE.sites(source)
        self.assertEqual(hits, [])
        self.assertIsNotNone(stopped, "the scan stopped and said nothing")
        self.assertEqual(stopped[0], 2)
        status, report = self.gate(self.write(source))
        self.assertEqual(status, 1, report)

    def test_a_scan_that_stops_between_statements_says_where(self):
        """Nothing is left open here: the tokenizer gives up at a dedent it
        cannot place, with every statement above it closed. The site below is
        never reached, and the only evidence of that is the tokenizer's error."""
        source = ('if limit:\n'
                  '        terms = terms[:limit]\n'
                  '    limit = None\n'
                  'top = spread.most_common(1)[0]\n')
        hits, count, stopped = GATE.sites(source)
        self.assertEqual((hits, count), ([], 2))
        self.assertIsNotNone(stopped, "the scan stopped and said nothing")
        self.assertEqual(stopped[0], 3)
        self.assertIn("tokenizer stopped", stopped[1])
        status, report = self.gate(self.write(source))
        self.assertEqual(status, 1, report)
        self.assertIn("planted.py:3", report)

    def test_a_file_this_python_cannot_parse_is_not_called_clean(self):
        """A tokenizer does not reject what it does not understand; it cuts it
        some other way and says nothing. `= =` is cut without complaint on
        every version, so this is the case that can be tested on all of them."""
        hits, count, stopped = GATE.sites("limit = = 3\ntop = 1\n")
        self.assertEqual((hits, count), ([], 2))
        self.assertIsNotNone(stopped, "unparseable source read as sound")
        self.assertEqual(stopped[0], 1)
        status, report = self.gate(self.write("limit = = 3\ntop = 1\n"))
        self.assertEqual(status, 1, report)
        self.assertNotIn("clean —", report)

    def test_syntax_newer_than_the_python_running_the_gate_is_not_clean(self):
        """The case that forced the check above. Before 3.12 an f-string that
        reuses its own quote is tokenized as two plain strings with a name
        between them, and the site lies inside the second: read as text, on
        the word of a tokenizer older than the file. From 3.12 it is flagged;
        before, the file has to be reported as one this Python cannot read."""
        source = ('names = {"top": "winner"}\n'
                  'print(f"{names["top"]}: {spread.most_common(1)[0]}")\n')
        status, report = self.gate(self.write(source))
        self.assertEqual(status, 1, report)
        self.assertIn("planted.py:2", report)

    def test_whatever_the_tokenizer_raises_the_file_is_reported(self):
        """Its contract is TokenError and SyntaxError. Its C implementation
        also raises SystemError (3.12) and MemoryError (3.14) on one valid
        f-string, and UnicodeEncodeError on a lone surrogate. Catching only
        the two it promises ends the run in a traceback on such a file, with
        every other file unreported."""
        with mock.patch.object(GATE.tokenize, "generate_tokens",
                               side_effect=MemoryError()):
            try:
                _hits, _count, stopped = GATE.sites("limit = 3\n")
            except MemoryError:
                self.fail("the tokenizer's error ended the run")
        self.assertIsNotNone(stopped, "the scan stopped and said nothing")
        self.assertIn("MemoryError", stopped[1])

    def test_the_f_string_that_breaks_the_tokenizer_fails_the_gate_quietly(self):
        """That f-string, for real. Valid everywhere; where the tokenizer
        chokes on it the file is reported unread, elsewhere the site below it
        is found. Either way the gate fails and says which file."""
        source = ('a = 1\n'
                  'x = f"""{a=:\n'
                  '}"""\n'
                  'top = spread.most_common(1)[0]\n')
        self.write("limit = 3\n", name="readable.py")
        self.write(source)
        status, report = self.gate(self.tmp)
        self.assertEqual(status, 1, report)
        self.assertIn("planted.py:", report)

    def test_a_coding_line_python_cannot_honour_is_reported_not_raised(self):
        self.write("limit = 3\n", name="readable.py")
        self.write(b"# coding: hex\nlimit = 3\n")
        status, report = self.gate(self.tmp)
        self.assertEqual(status, 1, report)
        self.assertIn("planted.py", report)
        self.assertNotIn("clean —", report)

    def test_a_folder_that_cannot_be_listed_is_reported_not_skipped(self):
        """os.walk swallows the error unless it is asked for it."""
        self.write("limit = 3\n", name="readable.py")
        locked = os.path.join(self.tmp, "locked")
        os.mkdir(locked)
        with open(os.path.join(locked, "planted.py"), "w") as handle:
            handle.write(ALONE)
        os.chmod(locked, 0)
        try:
            if os.access(locked, os.R_OK):
                self.skipTest("this user can list a folder with no permissions")
            status, report = self.gate(self.tmp)
        finally:
            os.chmod(locked, 0o700)
        self.assertEqual(status, 1, report)
        self.assertIn("locked", report)
        self.assertNotIn("clean —", report)

    def test_a_file_that_cannot_be_decoded_is_reported_not_skipped(self):
        """It was skipped with `continue`, so a folder holding one such file
        beside a readable one was reported clean on the strength of the other."""
        self.write("limit = 3\n", name="readable.py")
        self.write(b"top = 1\nname = '\xff\xfe'\n")
        status, report = self.gate(self.tmp)
        self.assertEqual(status, 1, report)
        self.assertIn("planted.py", report)
        self.assertNotIn("clean —", report)

    def test_a_path_that_does_not_exist_is_not_clean(self):
        """Beside a path that does exist, because that is the mistyped
        argument: the other one is read, and its result is taken for both."""
        status, report = self.gate(self.write("limit = 3\n", name="readable.py"),
                                   os.path.join(self.tmp, "no-such-file.py"))
        self.assertEqual(status, 1, report)
        self.assertIn("no-such-file.py", report)
        self.assertNotIn("clean —", report)

    def test_a_folder_holding_no_python_is_not_clean(self):
        """Nothing read is not the same result as nothing found."""
        status, report = self.gate(self.tmp)
        self.assertEqual(status, 1, report)
        self.assertNotIn("clean —", report)

    def test_nor_is_one_given_beside_a_path_that_does_hold_some(self):
        """Each argument answers for itself. Otherwise the folder that was
        mistyped, or is empty, is covered by the file next to it."""
        docs = os.path.join(self.tmp, "docs")
        os.mkdir(docs)
        status, report = self.gate(self.write("limit = 3\n"), docs)
        self.assertEqual(status, 1, report)
        self.assertIn("docs", report)
        self.assertNotIn("clean —", report)

    def test_an_empty_argument_is_reported_like_any_other_missing_path(self):
        status, report = self.gate(self.write("limit = 3\n"), "")
        self.assertEqual(status, 1, report)
        self.assertNotIn("clean —", report)

    def test_a_file_with_no_final_newline_is_read_to_its_end(self):
        """3.10 and 3.11 emit no NEWLINE for a last line that starts with "#",
        even when that line is the end of a string, which leaves a finished
        statement looking open where the file ends."""
        hits, count, stopped = GATE.sites('x = """\n#"""')
        self.assertEqual((hits, count, stopped), ([], 1, None))
        self.assertEqual(found("limit = 3\ntop = spread.most_common(1)[0]"),
                         [(2, TIE)])

    def test_help_describes_the_gate_and_does_not_run_it(self):
        """`--help` was taken for a path. No such folder exists, so nothing was
        walked and the answer was "clean", which CI's every-tool-answers-help
        step accepted as help because it was long enough."""
        status, report = self.gate("--help")
        self.assertEqual(status, 0, report)
        self.assertIn("aggregation-ok", report)
        self.assertNotIn("clean —", report)

    def test_a_clean_report_says_how_much_it_read(self):
        status, report = self.gate(self.write("limit = 3\nterms = []\n"))
        self.assertEqual(status, 0, report)
        self.assertIn("2 statements", report)
        self.assertIn("1 file", report)


class TheAcknowledgement(unittest.TestCase):
    """`# aggregation-ok` covers the statement it is written on or directly
    above, and no other."""

    SITE = "top, _ = counts.most_common(1)[0]"

    def test_on_the_line_above(self):
        self.assertEqual(found("# aggregation-ok: tie handled below\n"
                               + self.SITE + "\n"), [])

    def test_on_the_same_line(self):
        self.assertEqual(found(self.SITE + "  # aggregation-ok: handled below\n"),
                         [])

    def test_on_any_line_of_a_statement_that_runs_over_several(self):
        source = ('votes = {m: v\n'
                  '         for m, v in panel.items()\n'
                  '         # aggregation-ok: the errors are counted below\n'
                  '         if v.get("verdict") in VALID}\n')
        self.assertEqual(found(source), [])

    def test_one_written_for_the_next_statement_does_not_reach_back(self):
        """The window used to run one line past the statement's first line, so
        the acknowledgement of one site silenced the site above it too."""
        source = (self.SITE + "\n"
                  "# aggregation-ok: tie handled below\n"
                  "other, _ = spread.most_common(1)[0]\n")
        self.assertEqual(found(source), [(1, TIE)])

    def test_one_trailing_the_statement_above_does_not_reach_down(self):
        source = (self.SITE + "  # aggregation-ok: tie handled below\n"
                  "other, _ = spread.most_common(1)[0]\n")
        self.assertEqual(found(source), [(2, TIE)])

    def test_a_comment_closing_a_continued_line_belongs_to_that_statement(self):
        """After a backslash the comment line is the END of the statement
        above it, though it looks like a line of its own above the next one."""
        source = (self.SITE + " \\\n"
                  "    # aggregation-ok: tie handled below\n"
                  "other, _ = spread.most_common(1)[0]\n")
        self.assertEqual(found(source), [(3, TIE)])

    def test_the_words_inside_a_string_acknowledge_nothing(self):
        source = ('NOTE = "# aggregation-ok: said in a string"\n'
                  + self.SITE + "\n")
        self.assertEqual(found(source), [(2, TIE)])


class TheTreeItself(unittest.TestCase):
    """The splitter against every Python file this repository ships.

    The planted cases above are the ones somebody thought of. The defect was in
    twenty-four files nobody had thought of, so the real tree is the test that
    would have caught it — and the parser is the witness, because it is a
    different implementation of where a statement starts and ends.
    """

    @classmethod
    def setUpClass(cls):
        listed = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT,
                                capture_output=True, text=True).stdout
        cls.files = [os.path.join(ROOT, name) for name in listed.splitlines()]
        cls.read = {}
        for path in cls.files:
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            cls.read[path] = (source, GATE.statements(source))

    def test_the_listing_is_not_empty(self):
        """A check over nothing passes."""
        self.assertGreater(len(self.files), 50)

    def test_every_file_is_read_to_its_end(self):
        scanned = GATE.scan(self.files)
        self.assertEqual(scanned.unread, [])
        self.assertEqual(scanned.files, len(self.files))

    def test_the_default_walk_reaches_everything_it_is_meant_to(self):
        """The tests above hand the gate its files. CI hands it a folder, and
        what it reads is then decided by the walk.

        The two exclusions are written out here, not read from SKIP_DIRS: an
        expectation built from the constant it checks cannot fail, and what
        the gate leaves unread is a decision someone should have to make
        twice. Stated as a subset, because the walk also reads untracked files.
        """
        meant = {os.path.relpath(path, ROOT) for path in self.files}
        meant = {name for name in meant
                 if not name.startswith("fixtures" + os.sep)
                 and name != "check-aggregation.py"}
        walked = {os.path.relpath(path, ROOT)
                  for path in GATE.sources(ROOT, [])}
        self.assertGreater(len(meant), 50)
        self.assertEqual(sorted(meant - walked), [])

    def test_no_line_of_code_lies_outside_a_statement(self):
        loose = {}
        for path, (source, (statements, _comments, _stopped)) in self.read.items():
            inside = {n for s in statements for n in range(s.line, s.end + 1)}
            outside = [n for n, line in enumerate(source.splitlines(), 1)
                       if n not in inside and line.strip()
                       and not line.lstrip().startswith("#")]
            if outside:
                loose[os.path.relpath(path, ROOT)] = outside
        self.assertEqual(loose, {})

    def test_the_statements_are_the_ones_the_parser_sees(self):
        """Two relations, each stated as what is believed rather than as
        equality, since the two legitimately differ: a logical line can hold
        several statements (`if x: return y`) or none (`else:`).

          joined   every statement the parser sees begins on the first line of
                   a logical line, which it may share with another
          cut      no logical line begins part-way through a simple statement,
                   or between a compound statement's keyword and its body
        """
        disagree = {}
        for path, (source, (statements, _comments, _stopped)) in self.read.items():
            starts = {s.line for s in statements}
            joined, cut = [], []
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, ast.stmt):
                    continue
                if node.lineno not in starts:
                    joined.append(node.lineno)
                body = getattr(node, "body", None)
                last = first_line(body[0]) - 1 if body else node.end_lineno
                cut += [n for n in starts if node.lineno < n <= last]
            if joined or cut:
                disagree[os.path.relpath(path, ROOT)] = {"joined": joined,
                                                         "cut": cut}
        self.assertEqual(disagree, {})


if __name__ == "__main__":
    unittest.main()
