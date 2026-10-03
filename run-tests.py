#!/usr/bin/env python3
"""Run the test suite for the deterministic core. Stdlib only, no network.

    ./run-tests.py                 # everything
    ./run-tests.py closure matrix  # only these modules
    ./run-tests.py -v              # one line per test
    ./run-tests.py --mutate        # check that the tests can still fail

WHY THIS EXISTS. 1,831 lines across closure.py, freeze.py, sweep.py, matrix.py,
extract.py and writeback.py decided what a finding is, with no test and no
assert. In one day that code produced a heading boundary that sliced nested
sections to zero length, a row-alignment invariant contradicted one line below
its own comment, a cache that corrupted entries under concurrency, a hash
compared against a truncated pin, and two scorers that measured nothing. Every
one is what a unit test catches. Every one was found by accident.

The CI already had three gates and none of them checked an answer: they check
that nothing names the client, that no citation dangles, and that every tool
loads. A tool that loads and returns the wrong verdict passes all three.

WHY --mutate. DESIGN §0.1 — a failed check is not a passed check — applies to
the tests as much as to anything they cover. A test that passes against broken
code is worse than no test, because it converts an absence of checking into a
claim of checking. --mutate breaks one function at a time, in memory, and
requires the named tests to fail. If a mutation is not caught, the run fails:
the suite is reporting confidence it has not earned.

Nothing here reaches a model or an endpoint. That is deliberate — DESIGN §3.17,
where a deterministic check exists it wins — and it is why this can run on every
push next to the other three gates.
"""

import argparse
import io
import os
import re
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.realpath(__file__))

# Each entry breaks ONE decision in ONE function and names the tests that must
# notice. The fragment must still be present in the source; if it is not, the
# mutation is stale and the run says so rather than quietly checking nothing —
# a mutation that no longer applies is the same failure as a test that cannot
# fail.
#
# NAME ONLY IN-PROCESS TESTS HERE. mutate() applies a mutation by re-executing
# source into the imported module object and never writes to disk (see its
# docstring for why). A test that shells out therefore runs a fresh interpreter
# reading the UNMUTATED file, cannot fail, and makes the entry read NOT CAUGHT
# however good the test is. That is indistinguishable from a weak test, and it
# is what happened to the locate.quoted entry until bundle.markdown was lifted
# out of main() so the check could run in-process. If an entry is stubbornly
# NOT CAUGHT, check that its tests are not subprocess tests before rewriting
# them.
MUTATIONS = [
    {
        "what": "bundle — body cells fall back to the unstyled default",
        "why": "the default format does not wrap, so a multi-line evidence "
               "quote is stored intact and shown clipped at the column edge — "
               "a reviewer sees fifty characters of a five-hundred-character "
               "citation with nothing to say more exists",
        "module": "bundle",
        "old": 'BODY_STYLE, HEADER_STYLE = 1, 2',
        "new": 'BODY_STYLE, HEADER_STYLE = 0, 0',
        "tests": [
            "tests.test_xlsx_styles.StylesPart."
            "test_body_cells_reference_a_style_that_wraps",
            "tests.test_xlsx_styles.StylesPart."
            "test_header_cells_reference_a_style_that_wraps_and_is_bold",
        ],
    },
    {
        "what": "locate.quoted — a table quote reflowed like prose",
        "why": "every character reaches the report and none of it can be "
               "checked: a table's meaning is the alignment of cell to column, "
               "and the collapse discards exactly that. Evidence that is "
               "decorative, and harder to spot than evidence that is missing",
        "module": "locate",
        "old": '    rows = [ln for ln in lines if ln.count("|") >= 2]',
        "new": '    rows = []',
        "tests": [
            "tests.test_quoting.Quoted.test_a_table_keeps_one_row_per_line",
            "tests.test_quoting.RenderersAreWired."
            "test_markdown_emits_a_table_quote_as_a_table",
        ],
    },
    {
        "what": "matrix.Record — letters resolved before header names",
        "why": "a matrix headed with criteria labels \"A\"/\"B\"/\"C\" is ordinary, "
               "and there row[\"C\"] would read column C while resolve_column "
               "picked the column HEADED C — one string, two columns",
        "module": "matrix",
        "old": '        found = self._names.get(normalise(key))',
        "new": '        if dict.__contains__(self, key):\n            return key\n        found = self._names.get(normalise(key))',
        "tests": [
            "tests.test_matrix.NameAddressing."
            "test_a_name_beats_a_letter_and_matches_resolve_column",
        ],
    },
    {
        "what": "matrix.resolve_column — returns a bare str, losing the Letter tag",
        "why": "the letter it returns goes straight back into row[col] at "
               "nineteen sites; untagged it is re-resolved as a header NAME and "
               "reads a different column than the one just selected",
        "module": "matrix",
        "old": '        return Letter(hits[0])',
        "new": '        return hits[0]',
        "tests": [
            "tests.test_matrix.NameAddressing."
            "test_the_resolve_column_round_trip_survives_a_letter_shaped_header",
        ],
    },
    {
        "what": "closure.classify — absence and broken-query verdicts swapped",
        "why": "a mistyped search term then reports 'still missing' with the "
               "same confidence as a real finding",
        "module": "closure",
        "old": '        return ("STILL ABSENT", None) if is_absence else ("ABSENT", None)',
        "new": '        return ("ABSENT", None) if is_absence else ("STILL ABSENT", None)',
        "tests": [
            "tests.test_closure.Classify."
            "test_nothing_anywhere_is_absent_when_the_finding_is_not_an_absence",
            "tests.test_closure.Classify."
            "test_nothing_anywhere_is_still_absent_when_the_register_says_so",
        ],
    },
    {
        "what": "synthesize.reached — anchor coverage threshold dropped to zero",
        "why": "this is the previous scorer, which a bag of the document's own "
               "nouns scored 6/6 against",
        "module": "synthesize",
        "old": "            if match.size >= ANCHOR_COVERAGE * len(anchor):",
        "new": "            if match.size >= 0 * len(anchor):",
        "tests": [
            "tests.test_synthesize.ReachedRejectsAWordBag."
            "test_rejects_the_anchors_own_words_in_scrambled_order",
            "tests.test_synthesize.ReachedRejectsAWordBag."
            "test_rejects_a_bag_of_the_documents_own_words",
            "tests.test_synthesize.ReachedGates."
            "test_coverage_threshold_is_a_fraction_of_the_anchor",
        ],
    },
    {
        "what": "undefined.same — the containment length floor removed",
        "why": "bare 'order' then satisfies 'frozen execution order', and the "
               "scorer starts rewarding vague output",
        "module": "undefined",
        "old": "                return len(short) >= 0.6 * len(long)",
        "new": "                return True",
        "tests": [
            "tests.test_undefined.Same.test_a_bare_word_inside_a_phrase_does_not_match",
            "tests.test_undefined.Same.test_the_contained_side_must_be_most_of_the_container",
        ],
    },
    {
        "what": "matrix.read_sheet — blank rows no longer dropped",
        "why": "the behaviour the row-alignment defect rests on; if changing it "
               "breaks nothing, nothing was pinning it. NOTE: this entry named a "
               "test that was later renamed out from under it, and the mutation "
               "then read as UNCAUGHT rather than as a broken name — so a rename "
               "can quietly disarm a mutation. If you rename a test, grep here.",
        "module": "matrix",
        # Anchored on the line above it as well. The bare `if any(...)` line
        # appeared verbatim in read_csv once delimited input landed, and a
        # substring patch then rewrote BOTH -- breaking the indentation of the
        # other and turning a mutation into an import error, which reads as
        # UNCAUGHT rather than as the harness misfiring.
        "old": ('            cells[re.sub(r"\\d", "", cell.get("r"))] = value(cell)\n'
                '        if any(v.strip() for v in cells.values()):'),
        "new": ('            cells[re.sub(r"\\d", "", cell.get("r"))] = value(cell)\n'
                '        if True:'),
        "tests": [
            "tests.test_matrix.BlankRows.test_a_row_of_empty_cells_is_dropped",
            "tests.test_matrix.BlankRows.test_list_position_still_diverges_but_the_row_key_does_not",
        ],
    },
    {
        "what": "matrix.numbered — the sheet row taken from list position",
        "why": "the reported bug, closed once as already fixed because the WRITER "
               "had been corrected and the four callers building its row-keyed "
               "dict had not; one deleted row in Excel files every verdict below "
               "it against the wrong comment, in a workbook that opens cleanly",
        "module": "matrix",
        "old": "        yield row.get(ROW_KEY, position), row",
        "new": "        yield position, row",
        "tests": [
            "tests.test_matrix.NumberedRows.test_numbers_come_from_the_sheet_not_the_list",
            "tests.test_matrix.NumberedRows.test_a_verdict_lands_against_its_own_comment_across_a_gap",
        ],
    },
    {
        "what": "vocabulary.flagged — the unknown value dropped",
        "why": "eight tools ask what a reviewer must look at; dropping the "
               "unknown value silently stops flagging 'we could not verify this', "
               "which is a request for evidence and the thing most worth reading",
        "module": "vocabulary",
        "old": "        return tuple(self.scale[:-1]) + (self.unknown,)",
        "new": "        return tuple(self.scale[:-1])",
        "tests": [
            "tests.test_vocabulary.DerivedViews.test_the_literals_eight_files_used_are_reproduced_exactly",
            "tests.test_vocabulary.DerivedViews.test_flagged_and_shortfall_are_not_the_same_question",
        ],
    },
    {
        "what": "extract.docx — runs joined with a space",
        "why": "Word splits words across runs, so this inserts a space into the "
               "middle of every term an anchor quotes",
        "module": "extract",
        "old": "            txt = ''.join(t.text or '' for t in el.iter()",
        "new": "            txt = ' '.join(t.text or '' for t in el.iter()",
        "tests": [
            "tests.test_extract.Docx.test_runs_inside_a_paragraph_are_joined_with_no_separator",
        ],
    },
    {
        "what": "sweep.headings_of — the table-of-contents filter removed",
        "why": "the heading set then doubles and a real deletion is buried in "
               "the churn",
        "module": "sweep",
        "old": '                    if re.search(r"[^\\s\\d]\\d{1,4}$", stripped):',
        "new": "                    if False:",
        "tests": [
            "tests.test_chunking.SweepHeadings.test_a_table_of_contents_line_is_excluded",
            "tests.test_chunking.SweepHeadings."
            "test_bug_a_heading_ending_in_an_identifier_is_discarded",
        ],
    },
    {
        "what": "inventory.split_sections — the minimum-lines floor removed",
        "why": "changes which heading owns which passage, and every locator "
               "undefined.py prints is a section start",
        "module": "inventory",
        "old": "def split_sections(lines, min_lines=4, max_lines=90, max_chars=1200):",
        "new": "def split_sections(lines, min_lines=0, max_lines=90, max_chars=1200):",
        "tests": [
            "tests.test_chunking.InventorySections."
            "test_bug_a_heading_arriving_too_soon_is_swallowed_as_body_text",
            "tests.test_chunking.InventorySections."
            "test_bug_the_fixtures_own_subsections_are_swallowed",
        ],
    },
    {
        "what": "freeze.slugify — the empty-slug fallback removed",
        "why": "a document then freezes to parsed/.txt, which no --doc argument "
               "can name",
        "module": "freeze",
        "old": '    return re.sub(r"-{2,}", "-", stem)[:40] or "doc"',
        "new": '    return re.sub(r"-{2,}", "-", stem)[:40]',
        "tests": [
            "tests.test_freeze.Slugify.test_a_filename_with_nothing_usable_still_gets_a_slug",
        ],
    },
    {
        "what": "fixtures/sec — the reissue pattern loses its misspelling",
        "why": "\"We resissue prior comment 30 in full\" is then read as the "
               "staff accepting the response: the wrong label, in the direction "
               "that flatters whatever is scored against it",
        "module": "fixtures/sec/fetch",
        "old": 'REISSUE = re.compile(r"\\bre-?s?issu\\w*|\\breiterat\\w*", re.I)',
        "new": 'REISSUE = re.compile(r"\\bre-?issu\\w*|\\breiterat\\w*", re.I)',
        "tests": [
            "tests.test_sec_fixture.TheWordsForReissuing."
            "test_the_misspelling_one_letter_uses",
        ],
    },
    {
        "what": "fixtures/sec — a reissue reaches only the sentence it is in",
        "why": "\"We note your response to prior comment 24. We reissue the "
               "first bullet of the prior comment in part.\" is then addressed, "
               "because the number and the reissue are a full stop apart",
        "module": "fixtures/sec/fetch",
        "old": "        for number in here or sorted(at):",
        "new": "        for number in here:",
        "tests": [
            "tests.test_sec_fixture.ReissueOutsideTheCitingSentence."
            "test_a_reissue_in_the_next_sentence_reaches_the_comment_cited_before_it",
            "tests.test_sec_fixture.ReissueOutsideTheCitingSentence."
            "test_a_reissue_further_on_is_quoted_with_the_gap_marked",
            "tests.test_sec_fixture.ReissueOutsideTheCitingSentence."
            "test_one_that_names_none_reaches_every_comment_cited_around_it",
        ],
    },
    {
        "what": "fixtures/sec — any use of the word reissues the comments around it",
        "why": "a question about a reissued audit report then reissues every "
               "prior comment its numbered comment cites; reading across "
               "sentences is only safe while this guard holds",
        "module": "fixtures/sec/fetch",
        "old": '            ours = OURS.search(parts[i]) or re.search(r"\\bcomments?\\b", parts[i], re.I)',
        "new": '            ours = True',
        "tests": [
            "tests.test_sec_fixture.ReissueOutsideTheCitingSentence."
            "test_the_word_used_of_something_else_reissues_nothing",
        ],
    },
    {
        "what": "fixtures/sec — a comment of an older letter counted as this one's",
        "why": "\"prior comment 15 of our letter dated March 14, 2023\" becomes a "
               "row about comment 15 of a letter that never said it",
        "module": "fixtures/sec/fetch",
        "old": "        if other and iso(other) != first:",
        "new": "        if False:",
        "tests": [
            "tests.test_sec_fixture.ACommentOfAnotherLetter."
            "test_a_comment_of_an_older_letter_is_not_a_comment_of_this_one",
            "tests.test_sec_fixture.ACommentOfAnotherLetter."
            "test_reissuing_another_letters_comment_reissues_nothing_here",
            "tests.test_sec_fixture.TheWholeRun."
            "test_what_is_labelled_and_what_is_set_aside",
        ],
    },
    {
        "what": "fixtures/sec — only a comment called \"prior\" is cited",
        "why": "\"We note your response to comment 4 and reissue it in part\" "
               "is then a reissue of nothing: no row, and one negative fewer",
        "module": "fixtures/sec/fetch",
        "old": '    r"\\b(?:prior comments?|responses? to comments?)\\s+(?:(?:nos?\\.?|numbers?)\\s+)?"',
        "new": '    r"\\b(?:prior comments?)\\s+(?:(?:nos?\\.?|numbers?)\\s+)?"',
        "tests": [
            "tests.test_sec_fixture.WhichNumbersAreCited."
            "test_a_comment_cited_without_the_word_prior",
        ],
    },
    {
        "what": "fixtures/sec — a comment number spelled out is not a number",
        "why": "\"prior comment five and reissue it in part\" cites nothing, so "
               "a letter that spells its numbers keeps its follow-ups and loses "
               "its reissues",
        "module": "fixtures/sec/fetch",
        "old": 'NUM = r"(?:\\d{1,3}(?![\\d%%]|,\\d{3}|\\.\\d)|(?:%s)\\b)" % "|".join(WORDS)',
        "new": 'NUM = r"(?:\\d{1,3}(?![\\d%%]|,\\d{3}|\\.\\d)|(?:%s)\\b)" % "zero"',
        "tests": [
            "tests.test_sec_fixture.WhichNumbersAreCited."
            "test_a_number_spelled_out",
        ],
    },
    {
        "what": "fixtures/sec — running text that starts with a number opens a comment",
        "why": "a line wrapped before \"18. Please revise\" takes comment 18's "
               "place and the real one is folded into it; one letter of the "
               "default slice's eight does this",
        "module": "fixtures/sec/fetch",
        "old": 'NUMBER = re.compile(r"^\\s{0,12}(\\d{1,3})\\.(?:\\s{2,}|\\s*$)")',
        "new": 'NUMBER = re.compile(r"^\\s{0,12}(\\d{1,3})\\.(?!\\d)")',
        "tests": [
            "tests.test_sec_fixture.WhereACommentStarts."
            "test_a_wrapped_line_that_starts_with_the_next_number_opens_nothing",
            "tests.test_sec_fixture.WhereACommentStarts."
            "test_a_numbered_heading_from_the_filing_opens_nothing",
        ],
    },
    {
        "what": "fixtures/sec — a letter whose numbering breaks is labelled anyway",
        "why": "every comment after the lost number is read as one, and a "
               "reissue in any of them lands on prior comments it never named",
        "module": "fixtures/sec/fetch",
        "old": "    if not starts or any(found and int(found.group(1)) == want + 1",
        "new": "    if not starts or any(False and int(found.group(1)) == want + 1",
        "tests": [
            "tests.test_sec_fixture.WhereACommentStarts."
            "test_a_letter_whose_numbering_breaks_is_set_aside_not_labelled",
            "tests.test_sec_fixture.TheWholeRun."
            "test_what_is_labelled_and_what_is_set_aside",
        ],
    },
    {
        "what": "fixtures/sec — the first-round letter taken without reading its date",
        "why": "the staff letter nearest the date named is then the first-round "
               "letter whatever is printed on it, which is how a closing notice "
               "came to stand for a comment letter",
        "module": "fixtures/sec/fetch",
        "old": "        if dated(text) != first or not comments:",
        "new": "        if False:",
        "tests": [
            "tests.test_sec_fixture.WhichLetterIsBeingAnswered."
            "test_a_letter_filed_the_day_after_the_date_printed_on_it",
            "tests.test_sec_fixture.WhichLetterIsBeingAnswered."
            "test_a_notice_dated_the_same_day_is_not_the_comment_letter",
        ],
    },
    {
        "what": "fixtures/sec — a page header left inside a sentence",
        "why": "its \"Corp.\" ends the sentence, \"which we reissue\" and \"in "
               "part\" land in different ones, and a partial reissue is read "
               "as a full one",
        "module": "fixtures/sec/fetch",
        "old": "    return \"\\n\".join(line for i, line in enumerate(lines) if i not in drop",
        "new": "    return \"\\n\".join(line for i, line in enumerate(lines) if True",
        "tests": [
            "tests.test_sec_fixture.PageHeaders."
            "test_a_header_inside_the_reissuing_sentence_does_not_end_it",
            "tests.test_sec_fixture.PageHeaders."
            "test_nor_does_the_plain_header_of_a_later_page",
        ],
    },
    {
        "what": "trace.chunk — the character cap disabled",
        "why": "one passage then holds most of an unwrapped contract, useless "
               "to retrieve and too large to send",
        "module": "trace",
        "old": "                    sum(len(x) for x in current) >= max_chars:",
        "new": "                    False:",
        "tests": [
            "tests.test_chunking.TraceChunk.test_the_character_cap_splits_unwrapped_prose",
        ],
    },
    # check-aggregation.py. The module name has a hyphen, which `import` cannot
    # spell and __import__ can; tests/test_aggregation.py loads it the same way,
    # so the tests and these mutations hold one module object between them.
    {
        "what": "check-aggregation — brackets inside a string literal counted again",
        "why": "the gate as it shipped: one regex literal with an unmatched "
               "\"(\" holds its statement open to the end of the file. It read "
               "50 of llm.py's 595 lines and printed clean over the rest, and "
               "24 of the 52 top-level files ended the same way",
        "module": "check-aggregation",
        "old": '            if token.type == tokenize.NEWLINE:',
        "new": ('            if token.type == tokenize.NEWLINE and '
                'sum(t.string.count(c) for t in pending for c in "([{") <= '
                'sum(t.string.count(c) for t in pending for c in ")]}"):'),
        "tests": [
            "tests.test_aggregation.TheReportedFailure."
            "test_a_site_below_a_regex_literal_is_reported",
            "tests.test_aggregation.TheReportedFailure."
            "test_and_nothing_else_is_said_about_that_file",
            "tests.test_aggregation.WhereAStatementEnds."
            "test_a_bracket_inside_a_string_does_not_hold_a_statement_open",
            "tests.test_aggregation.TheTreeItself."
            "test_every_file_is_read_to_its_end",
            "tests.test_aggregation.TheTreeItself."
            "test_the_statements_are_the_ones_the_parser_sees",
        ],
    },
    {
        "what": "check-aggregation — a line break inside a statement ends it",
        "why": "the first version of the gate, which read line by line: a "
               "comprehension whose filter sits on its second line is two "
               "halves, and neither half is a filtered tally",
        "module": "check-aggregation",
        "old": '            if token.type == tokenize.NEWLINE:',
        "new": '            if token.type in (tokenize.NEWLINE, tokenize.NL):',
        "tests": [
            "tests.test_aggregation.WhereAStatementEnds."
            "test_a_filter_on_the_second_line_belongs_to_its_comprehension",
            "tests.test_aggregation.WhereAStatementEnds."
            "test_a_closing_bracket_inside_a_string_does_not_end_one_early",
            "tests.test_aggregation.TheTreeItself."
            "test_the_statements_are_the_ones_the_parser_sees",
        ],
    },
    {
        "what": "check-aggregation — the statement still open at the end of a "
                "file is dropped",
        "why": "the other half of the same defect. The loop kept a statement "
               "when it closed, and one that never closed was thrown away "
               "with everything it had swallowed, unscanned and unmentioned",
        "module": "check-aggregation",
        "old": '    if pending:\n        # Still open when the tokens ran out.',
        "new": '    if False:\n        # Still open when the tokens ran out.',
        "tests": [
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_a_statement_still_open_at_the_end_is_scanned",
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_and_the_file_is_reported_from_the_line_that_opened_it",
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_an_open_statement_fails_the_gate_with_no_site_in_it",
        ],
    },
    {
        "what": "check-aggregation — a scan the tokenizer abandons says nothing",
        "why": "every statement above the break is closed, so nothing is left "
               "pending to give it away, and the parser does not always "
               "object either: under 3.12 and 3.14 the tokenizer gives up on "
               "one VALID f-string. The gate reads to that line, stops, and "
               "reports on what it read as though that were the file",
        "module": "check-aggregation",
        "old": '        stopped = (line or reached, f"not read past this line: {reason}")',
        "new": '        stopped = None',
        "tests": [
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_a_scan_that_stops_between_statements_says_where",
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_whatever_the_tokenizer_raises_the_file_is_reported",
        ],
    },
    {
        "what": "check-aggregation — a file that cannot be read is skipped",
        "why": "this was `continue`. A folder holding one undecodable file "
               "beside a readable one was reported clean on the strength of "
               "the readable one",
        "module": "check-aggregation",
        "old": '                unread.append((path, 0, f"could not be read ({said(error)})"))',
        "new": '                pass',
        "tests": [
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_a_file_that_cannot_be_decoded_is_reported_not_skipped",
        ],
    },
    {
        "what": "check-aggregation — a file not read to its end still passes",
        "why": "CI consumes the exit status and nothing else. A report that "
               "names the unread file and exits 0 is a paragraph nobody is "
               "shown",
        "module": "check-aggregation",
        "old": '    if not hits and not unread:',
        "new": '    if not hits:',
        "tests": [
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_an_open_statement_fails_the_gate_with_no_site_in_it",
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_a_scan_that_stops_between_statements_says_where",
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_a_file_that_cannot_be_decoded_is_reported_not_skipped",
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_a_path_that_does_not_exist_is_not_clean",
        ],
    },
    {
        "what": "check-aggregation — string literals blanked out of the statement",
        "why": "the tidy way to stop a docstring matching, and it disarms the "
               "gate: two of the filter pattern's four endings name a literal, "
               "so with the strings gone neither can match anything",
        "module": "check-aggregation",
        "old": '            for n, piece in enumerate(token.string.split("\\n")):',
        "new": ('            for n, piece in enumerate((\'""\' if literal(token) '
                'else token.string).split("\\n")):'),
        "tests": [
            "tests.test_aggregation.StringsAreNotCode."
            "test_a_literal_the_pattern_names_still_counts",
        ],
    },
    {
        "what": "check-aggregation — an acknowledgement reaches the statement "
                "above it",
        "why": "the window as it was: one line past the statement's first. An "
               "`# aggregation-ok` written for one site then silences the "
               "site on the line above it, which nobody looked at",
        "module": "check-aggregation",
        "old": '    rows = range(statement.line, statement.end + 1)',
        "new": '    rows = range(statement.line, statement.end + 2)',
        "tests": [
            "tests.test_aggregation.TheAcknowledgement."
            "test_one_written_for_the_next_statement_does_not_reach_back",
        ],
    },
    {
        "what": "check-aggregation — a file this Python cannot parse is trusted",
        "why": "a tokenizer older than the file does not refuse it. It cuts an "
               "f-string that reuses its own quote into two plain strings, "
               "and the site between the braces is read as text: clean under "
               "3.9 to 3.11, where the bracket counter had flagged it",
        "module": "check-aggregation",
        "old": '    return hits, len(found), stopped or unparsed(source)',
        "new": '    return hits, len(found), stopped',
        "tests": [
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_a_file_this_python_cannot_parse_is_not_called_clean",
        ],
    },
    {
        "what": "check-aggregation — a line break inside a call is a space",
        "why": "the lines of a statement were joined by a space wherever the "
               "break fell, and the patterns allow none in `.most_common(` or "
               "`.get(\"verdict\")`: a site wrapped after its dot, or inside "
               "its call, read as clean",
        "module": "check-aggregation",
        "old": '    a = before.string if before.type == tokenize.OP else ""',
        "new": '    return False',
        "tests": [
            "tests.test_aggregation.WhereAStatementEnds."
            "test_a_line_break_inside_a_call_does_not_hide_it",
            "tests.test_aggregation.WhereAStatementEnds."
            "test_nor_does_one_inside_the_literal_a_pattern_names",
        ],
    },
    {
        "what": "check-aggregation — an argument with nothing to read passes",
        "why": "a folder with no Python under it, named beside one that has "
               "some, is covered by its neighbour: the mistyped argument, "
               "reported as checked",
        "module": "check-aggregation",
        "old": '            unread.append((argument, 0, "no Python source here"))',
        "new": '            pass',
        "tests": [
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_a_folder_holding_no_python_is_not_clean",
            "tests.test_aggregation.AFileNotReadToItsEnd."
            "test_nor_is_one_given_beside_a_path_that_does_hold_some",
        ],
    },
    {
        "what": "check-aggregation — fixtures/ goes unread again",
        "why": "sixteen scripts that tally verdicts and print the baselines "
               "the documents quote were skipped from the gate's first "
               "commit, for no recorded reason, and one of them held the "
               "first shape the gate names",
        "module": "check-aggregation",
        "old": 'SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv"}',
        "new": ('SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", '
                '"fixtures"}'),
        "tests": [
            "tests.test_aggregation.TheTreeItself."
            "test_the_default_walk_reaches_everything_it_is_meant_to",
        ],
    },
    {
        "what": "fixtures/ntsb — a tied majority named by whichever came first",
        "why": "the site the aggregation gate found on the day it first read "
               "fixtures/. The baseline is the same count either way; the "
               "class printed beside it as the answer to give was decided by "
               "the order of labels.json",
        "module": "fixtures/ntsb/score-ntsb",
        "old": "    return sorted(k for k, n in counts.items() if n == most), most",
        "new": "    return [max(counts, key=counts.get)], most",
        "tests": [
            "tests.test_ntsb_fixture.TheMajorityClass."
            "test_a_tie_names_every_class_in_it",
            "tests.test_ntsb_fixture.TheMajorityClass."
            "test_and_does_not_depend_on_which_was_counted_first",
            "tests.test_ntsb_fixture.TheLineItPrints."
            "test_a_tie_is_said_to_be_one",
        ],
    },
    # What a tool may say about a document it did not read all of. synthesize.py
    # asserts absences over an inventory, and three places upstream of it
    # reported a failure as a result.
    {
        "what": "synthesize.read_inventory — the sections that failed are forgotten",
        "why": "the defect as it shipped: `[s for s in data[\"sections\"] if s "
               "and \"error\" not in s]`. On a sound three-section document "
               "one failed section produced a false D3, D6 or D8, an exit "
               "status of 0, and a section count one lower as the only trace",
        "module": "synthesize",
        "old": '    return sections, unread, partial, short',
        "new": '    return sections, [], partial, short',
        "tests": [
            "tests.test_unread_sections.WhatTheInventoryLineSays."
            "test_a_section_that_failed_is_counted_and_named",
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_a_pointer_to_an_unread_section_is_not_called_a_missing_section",
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_an_ownership_gap_the_unread_section_may_close",
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_an_orphan_the_unread_section_may_consume",
        ],
    },
    {
        "what": "synthesize.read_inventory — a section read by the fallback "
                "alone counts as read",
        "why": "the fallback schema never asks what a section consumes. Taken "
               "as a full reading, the consumer's silence makes an orphan of "
               "whatever it consumes",
        "module": "synthesize",
        "old": '        if entry.get("degraded") and not fully_read(entry):',
        "new": '        if False:',
        "tests": [
            "tests.test_unread_sections.WhatTheInventoryLineSays."
            "test_a_section_read_by_the_fallback_alone_is_named_as_read_in_part",
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_an_orphan_when_the_consumer_was_read_without_its_consumes",
        ],
    },
    {
        "what": "synthesize.fully_read — an old inventory's fallback entries "
                "are taken as read",
        "why": "an entry written before `full_passes` existed does not say "
               "whether a full pass followed the fallback. With five empty "
               "fields and no sign of one, assuming it did turns \"nothing "
               "asked\" back into \"consumes nothing\"",
        "module": "synthesize",
        "old": '    return any(entry.get(key) for key in UNASKED)',
        "new": '    return True',
        "tests": [
            "tests.test_unread_sections.WhatTheInventoryLineSays."
            "test_an_old_inventory_is_read_in_part_only_where_it_shows_no_full_pass",
        ],
    },
    {
        "what": "synthesize — a pointer to an unread section is a missing "
                "section again",
        "why": "\"Section 3 — no such section in the document\", printed about "
               "a section that is in the document and failed at extraction: "
               "the tool's own failure, reported as the author's defect",
        "module": "synthesize",
        "old": '        if not hits and unread:',
        "new": '        if False:',
        "tests": [
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_a_pointer_to_an_unread_section_is_not_called_a_missing_section",
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_nor_when_the_inventory_cannot_say_which_section_went_unread",
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_no_pointer_is_cleared_by_what_an_unread_heading_says",
        ],
    },
    {
        "what": "synthesize.open_question — no absence is ever in doubt",
        "why": "nothing owns it, nothing consumes it: each is a claim about "
               "every section of the document, made over the ones that "
               "happened to be read",
        "module": "synthesize",
        "old": '    if not sections:\n        return ""',
        "new": '    if True:\n        return ""',
        "tests": [
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_an_ownership_gap_the_unread_section_may_close",
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_an_orphan_the_unread_section_may_consume",
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_an_orphan_when_the_consumer_was_read_without_its_consumes",
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_the_summary_counts_them_apart",
        ],
    },
    {
        "what": "synthesize.open_question — an absence is in doubt with "
                "every section read",
        "why": "marking that cries wolf is marking nobody reads. When nothing "
               "went unread the absence is the inventory's to assert, and "
               "the finding has to stand as one",
        "module": "synthesize",
        "old": '    if not sections:\n        return ""',
        "new": '    if False:\n        return ""',
        "tests": [
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_and_so_is_a_claim_the_section_it_names_does_not_hold",
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_the_fallback_does_not_put_an_ownership_finding_in_doubt",
        ],
    },
    {
        "what": "synthesize — the dataflow flag forgets the sections read "
                "in part",
        "why": "--authority-as-dataflow takes ownership from produces and "
               "consumes. The fallback never asks for either, so under the "
               "flag a section read in part can be hiding the owner, and "
               "the ownership gap was asserted over it",
        "module": "synthesize",
        "old": '        owners_unseen = unread + (partial if args.authority_as_dataflow else [])',
        "new": '        owners_unseen = unread',
        "tests": [
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_under_the_dataflow_flag_a_section_read_in_part_may_be_the_owner",
        ],
    },
    {
        "what": "synthesize — the candidate file calls a finding in doubt unmet",
        "why": "the CSV is what score-register.py reads and what a reviewer is "
               "handed. `unverifiable` is the coverage scale's own word for "
               "evidence that could not be reached; `unmet` there would be a "
               "verdict nobody reached",
        "module": "synthesize",
        "old": '                    label, UNVERIFIABLE if doubt else UNMET, quote, where,',
        "new": '                    label, UNMET, quote, where,',
        "tests": [
            "tests.test_unread_sections.AnAbsenceAnUnreadSectionCouldAnswer."
            "test_the_candidate_file_carries_the_verdict",
        ],
    },
    {
        "what": "synthesize.adjudicate_pairs — a call that failed says no conflict",
        "why": "DESIGN §0.1's first example, a second time: the lens that "
               "errored defaulted to not refuted. Here the pair came back as "
               "None, which is what a pair judged clear comes back as, and "
               "was counted in \"adjudicated N pairs\"",
        "module": "synthesize",
        "old": '            return UNJUDGED',
        "new": '            return None',
        "tests": [
            "tests.test_unread_sections.APairThatCouldNotBeJudged."
            "test_a_failed_call_is_counted_as_failed",
            "tests.test_unread_sections.APairThatCouldNotBeJudged."
            "test_and_a_run_where_every_call_failed_has_judged_nothing",
            "tests.test_unread_sections.TheAdjudicationLine."
            "test_it_says_how_many_pairs_were_judged_not_how_many_were_tried",
        ],
    },
    {
        "what": "synthesize — a score that could not be computed exits clean",
        "why": "--ground-truth without PyYAML returned 0 and printed no score. "
               "Asked for a measurement, it reported nothing and called that "
               "success",
        "module": "synthesize",
        "old": '                  file=sys.stderr)\n            return 1',
        "new": '                  file=sys.stderr)\n            return 0',
        "tests": [
            "tests.test_unread_sections.WithoutPyYAML."
            "test_a_score_that_cannot_be_computed_is_not_a_clean_exit",
        ],
    },
    {
        "what": "inventory.catalogue — a section that failed loses its name",
        "why": "{\"error\": ...} and nothing else is what was stored. The "
               "inventory could say that a section had gone unread and not "
               "which, and a pointer to it could not be told from a pointer "
               "to a section the document does not have",
        "module": "inventory",
        "old": ('            return {"error": str(exc), "heading": section["heading"],\n'
                '                    "locator": locator}'),
        "new": '            return {"error": str(exc)}',
        "tests": [
            "tests.test_inventory.ASectionThatFailed.test_it_is_still_named",
            "tests.test_inventory.ASectionThatFailed."
            "test_and_holds_nothing_that_reads_as_content",
        ],
    },
    {
        "what": "inventory.catalogue — a pass that failed counts as one that "
                "answered",
        "why": "--runs defaults to 3 because one pass misses things: at one "
               "pass the fixture's deferral cycle is invisible. A failed pass "
               "was swallowed, so a section read once looked the same as one "
               "read three times",
        "module": "inventory",
        "old": '            continue                # not merged, and not counted: see `passes`',
        "new": '            more = {}',
        "tests": [
            "tests.test_inventory.ThePassesBehindAnEntry."
            "test_a_pass_that_failed_is_not",
        ],
    },
    {
        "what": "inventory.catalogue — the fallback schema recorded as a full pass",
        "why": "the fallback asks for three fields and stores the other five "
               "as empty lists. Recorded as a full pass, `consumes: []` reads "
               "as \"consumes nothing\" when nothing asked",
        "module": "inventory",
        "old": '        degraded, passes, full = True, 1, 0',
        "new": '        degraded, passes, full = True, 1, 1',
        "tests": [
            "tests.test_inventory.ThePassesBehindAnEntry."
            "test_the_fallback_is_one_pass_and_says_which_schema_it_used",
        ],
    },
    {
        "what": "inventory.limited — --limit leaves no record of what it "
                "left out",
        "why": "the inventory held the first N sections under a line that "
               "read \"reading 100% of the document\", and synthesize.py "
               "called a pointer to section N+1 a pointer to a section that "
               "does not exist",
        "module": "inventory",
        "old": '    return sections[:limit], beyond',
        "new": '    return sections[:limit], []',
        "tests": [
            "tests.test_inventory.ARunToldToStopEarly."
            "test_the_sections_it_stopped_before_are_recorded_as_not_read",
        ],
    },
    {
        "what": "claim.judged — a pair nobody judged is dropped",
        "why": "\"judged 200/200, 0 contradictions\" is what a run printed "
               "when every call had been refused. The pair was skipped by "
               "`if result:` and the progress line went on counting it",
        "module": "claim",
        "old": '                unjudged.append(candidates[index - 1])',
        "new": '                pass',
        "tests": [
            "tests.test_claim.APairNobodyJudged."
            "test_it_is_kept_and_counted_apart",
            "tests.test_claim.APairNobodyJudged."
            "test_the_progress_line_counts_what_was_judged_not_what_was_tried",
            "tests.test_claim.APairNobodyJudged."
            "test_a_run_whose_calls_all_failed_judged_nothing",
        ],
    },
    {
        "what": "check-aggregation — the gate forgets `\"error\" not in`",
        "why": "it knew `!= \"error\"` and not this spelling, so "
               "synthesize.py's first line dropped every failed section in "
               "plain view of a gate that read it on every push",
        "module": "check-aggregation",
        "old": ('           |["\']error["\']\\s+not\\s+in\\b          '
                '# synthesize.py dropped every'),
        "new": ('                                                '
                '# synthesize.py dropped every'),
        "tests": [
            "tests.test_aggregation.AFilterThatDropsTheFailures."
            "test_keeping_only_the_entries_that_carry_no_error",
        ],
    },
]


def load(names):
    loader = unittest.TestLoader()
    if not names:
        return loader.discover(os.path.join(ROOT, "tests"), top_level_dir=ROOT)
    suite = unittest.TestSuite()
    for name in names:
        if not name.startswith("tests."):
            name = f"tests.test_{name}"
        suite.addTests(loader.loadTestsFromName(name))
    return suite


def cause(trace_text):
    """The assertion, and the line that made it — not the whole traceback.

    unittest ends a failure with a multi-line diff, so the LAST line is
    routinely "?    ^^ ^" and says nothing. The useful pair is the exception
    line and the frame inside tests/ that raised it.
    """
    lines = trace_text.strip().splitlines()
    message = next((l for l in lines if re.match(r"^\w*(Error|Exception):", l)),
                   lines[-1])
    where = ""
    for line in lines:
        found = re.match(r'\s*File "(.+/tests/[^"]+)", line (\d+)', line)
        if found:
            where = f"{os.path.basename(found.group(1))}:{found.group(2)}"
    return where, message.strip()


def report(result, seconds, stream=sys.stdout):
    """One summary line, then the failures with their assertion, and nothing
    else. A runner that prints more than the failures makes the failures harder
    to find, which is the only job it has on the day it matters."""
    broken = result.failures + result.errors
    print(f"\n{'-' * 74}", file=stream)
    if broken:
        for case, trace_text in broken:
            where, message = cause(trace_text)
            print(f"  FAIL  {case.id()}", file=stream)
            print(f"        {where}  {message[:88]}", file=stream)
        print(file=stream)
    print(f"  {result.testsRun} tests, {len(result.failures)} failed, "
          f"{len(result.errors)} errored, {seconds:.2f}s", file=stream)
    return not broken


def run(names, verbosity):
    # At verbosity 0 the per-test stream is discarded and only report() speaks.
    # That is the mode --mutate uses for its precondition run, where unittest's
    # own "OK" between the summary and the mutation results reads as a result
    # for the mutation check rather than for the suite.
    started = time.time()
    stream = sys.stderr if verbosity else io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=verbosity).run(
        load(names))
    return report(result, time.time() - started)


def imported(name):
    """The module object a mutation is applied to.

    A top-level tool imports by name. A script below the top level
    ("fixtures/sec/fetch") cannot, so it comes from tests.support.script, which
    hands the tests and the mutation the same object. Loading a second copy here
    would break a module no test is looking at, and every mutation of it would
    read NOT CAUGHT — the subprocess trap described above MUTATIONS, by another
    route.
    """
    if "/" in name:
        from tests.support import script
        return script(name + ".py")
    return __import__(name)


def mutate():
    """Break one function at a time and require the named tests to notice.

    The mutation is applied by re-executing the module's source into the module
    object that is already imported, so every reference to it — including the
    nested functions lifted out of main() — sees the change. Nothing on disk is
    touched: a mutation run that crashed halfway through and left a file
    modified would be a far worse failure than the one it was checking for.
    """
    print("mutation check — each entry breaks one function and requires the "
          "tests to fail\n" + "=" * 74)
    caught, missed = 0, []
    for mutation in MUTATIONS:
        path = os.path.join(ROOT, mutation["module"] + ".py")
        original = open(path, encoding="utf-8").read()
        if original.count(mutation["old"]) != 1:
            print(f"\n  STALE  {mutation['what']}")
            print(f"         the line it patches is no longer in "
                  f"{mutation['module']}.py — update or drop this mutation")
            missed.append(("STALE ANCHOR", mutation["what"]))
            continue

        module = imported(mutation["module"])
        # Compile the ORIGINAL before touching anything. Several sessions edit
        # this repository at once, and reading a file mid-write would otherwise
        # mutate the module and then fail to put it back.
        restore = compile(original, path, "exec")
        broken_source = original.replace(mutation["old"], mutation["new"])
        try:
            exec(compile(broken_source, path, "exec"), module.__dict__)
            result = unittest.TextTestRunner(
                stream=io.StringIO(), verbosity=0).run(
                    load(mutation["tests"]))
        finally:
            exec(restore, module.__dict__)

        # A named test that does not exist is turned by unittest's loader into
        # a _FailedTest that ERRORS, so it counted 1/1 "failed" and scored as
        # caught. Renaming a test therefore kept the mutation green — the exact
        # thing this harness exists to prevent, one level up. An empty test list
        # scored 0 == 0 and passed too. So: the named tests must exist, there
        # must be some, and the failures have to be assertion failures rather
        # than the loader complaining it cannot find them.
        missing = [str(e[0]) for e in result.errors
                   if "_FailedTest" in str(e[0]) or "ModuleNotFound" in str(e[1])]
        noticed = len(result.failures) + len(result.errors) - len(missing)
        if missing:
            status = "BROKEN TEST NAME"
        elif result.testsRun == 0:
            status = "NO TESTS NAMED"
        elif noticed == result.testsRun:
            status = "caught"
        else:
            status = "NOT CAUGHT"
        print(f"\n  {status:>10}  {mutation['what']}")
        print(f"              {mutation['why']}")
        print(f"              {noticed}/{result.testsRun} of the named tests "
              f"failed")
        if status == "caught":
            caught += 1
        else:
            missed.append((status, mutation["what"]))

    print("\n" + "=" * 74)
    print(f"  {caught}/{len(MUTATIONS)} mutations caught")
    # The summary used to print every miss as UNCAUGHT, including the ones the
    # per-mutation line had correctly called STALE ANCHOR or BROKEN TEST NAME.
    # Those are opposite diagnoses — "nothing pins this behaviour" versus "the
    # harness cannot find the thing that does" — and they call for opposite
    # actions. Reading only the summary sent this session down the wrong path
    # twice in one night, which is a good argument for a summary that does not
    # discard the distinction it was handed.
    for status, what in missed:
        print(f"  {status:>16}  {what}")
    if any(s == "NOT CAUGHT" for s, _ in missed):
        print("\n  A test that passes against broken code is worse than no "
              "test: it turns\n  an absence of checking into a claim of "
              "checking. Fix the test, not this file.")
    if any(s != "NOT CAUGHT" for s, _ in missed):
        print("\n  A mutation the harness could not apply or could not find "
              "tests for is\n  not a passing mutation. Repair the entry — the "
              "behaviour it names is\n  currently unchecked either way.")
    return not missed


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", metavar="MODULE",
                        help="module short names (closure, matrix, ...) or "
                             "full dotted test names; default is everything")
    parser.add_argument("-v", "--verbose", action="count", default=1,
                        help="one line per test")
    parser.add_argument("--mutate", action="store_true",
                        help="break each covered function in memory and "
                             "require the tests to fail")
    args = parser.parse_args()

    sys.path.insert(0, ROOT)
    if args.mutate:
        # The suite has to be green before its failures mean anything.
        if not run(args.names, 0):
            print("\n  suite is already failing — fix that before asking "
                  "whether it can fail")
            return 1
        return 0 if mutate() else 1
    return 0 if run(args.names, args.verbose) else 1


if __name__ == "__main__":
    sys.exit(main())
