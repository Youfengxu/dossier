#!/usr/bin/env python3
"""Fail if code decides a tie by iteration order, or tallies votes after dropping
the ones that failed.

    ./check-aggregation.py [path ...]      # exits non-zero on any hit, and on
                                           # anything it could not read

DESIGN §0.1 says a failed check is not a passed check: wherever a guard can fail,
the failure must land on the cautious side of the decision it guards. That
principle was written down, and then violated twice more by someone who had read
it. Prose does not enforce; this does.

The two shapes, both from real bugs in this repository's own tooling:

  TIE BY INSERTION ORDER
      counts.most_common(1)[0]
    Counter.most_common breaks a tie by first-seen order. A panel of two models
    that disagreed on nine rows resolved every one of them to whichever model
    happened to be listed first in the array, and reported "23 not_addressed" —
    a true count containing nine coin flips.

  TALLY AFTER DROPPING FAILURES
      [v for v in votes if v.get("verdict") in VALID]
    Filtering errors out and then asking whether the survivors agree is how "panel
    split on 0 rows" got printed when two of three models had errored and the
    third agreed with itself. An error is not a vote; dropping it silently
    converts thin evidence into unanimity.

WHAT THIS CANNOT DO. It is a text scan, not a type checker. It cannot see that a
tie was handled three lines below, so it accepts an explicit acknowledgement, on
any line of the statement or alone on the line above it:

    # aggregation-ok: tie handled below
    top, _ = counts.most_common(1)[0]

That escape hatch is deliberate and is the point: the goal is to force the
question to be ASKED, not to ban a function. A reviewer seeing the marker knows
someone considered it. A reviewer seeing bare most_common knows nobody did.

WHAT IT READS. Statements, cut where the tokenizer cuts them. A comment is not
code and neither is what a string literal holds, so a bracket or a "#" inside
one opens nothing and ends nothing. Anything it was pointed at and could not
read fails the gate and is named — a file it cannot decode, tokenize to the end
or parse, a folder it cannot list, an argument with no Python under it. What
was not read was not checked, and "clean" printed over it would be this gate
committing the failure it exists to catch.
"""

import ast
import collections
import io
import keyword
import os
import re
import sys
import tokenize
import warnings

# What the walk does not enter. fixtures/ was here from the first commit, for
# no recorded reason. Its sixteen scripts tally verdicts and print the baselines
# the documents quote, and one of them held a tie decided by order.
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv"}
MARKER = re.compile(r"#\s*aggregation-ok\b")

PATTERNS = [
    (re.compile(r"\.most_common\(\s*1\s*\)|\.most_common\(\s*\)\s*\[\s*0\s*\]"),
     "tie decided by insertion order",
     "most_common breaks ties by first-seen order. Decide what a tie MEANS — "
     "contested, escalate, or a documented precedence — and say so."),
    (re.compile(r"""(?x)
        (?:for\s+[\w,\s]+?\s+in\s+|=\s*[\[{])   # list OR dict comprehension,
                                                # single var OR tuple unpacking:
                                                # the first version matched only
                                                # `= [` and `for x in`, so it
                                                # reported clean on the real
                                                # instance, a dict comprehension
                                                # over `for m, v in`.
        [^\n]*\bif\b[^\n]*
        (?:\bin\s+(?:VALID|ORDER|SCALE|ALLOWED)\b
           |\bnot\s+None\b
           |!=\s*["']error["']
           |\.get\(["']verdict["']\)\s*(?:in|is\s+not)\b)
     """),
     "votes filtered before tallying",
     "dropping failed answers and then asking whether the rest agree turns thin "
     "evidence into unanimity. Count how many actually answered and report it."),
]


# Tokens that are not code: comments, the line breaks inside a statement, and
# the tokenizer's own bookkeeping.
SILENT = {tokenize.COMMENT, tokenize.NL, tokenize.INDENT, tokenize.DEDENT,
          tokenize.ENDMARKER}
# The literal stretches of an f-string (3.12 on) and of a t-string (3.14 on),
# which those versions hand over apart from the expressions between them.
LITERAL = {getattr(tokenize, name)
           for name in ("FSTRING_MIDDLE", "TSTRING_MIDDLE")
           if hasattr(tokenize, name)}

Scan = collections.namedtuple("Scan", "hits unread files statements")


def literal(token):
    """Whether `token` is the text of a string, where nothing is code.

    Before 3.12 the tokenizer hands an f-string over whole, replacement fields
    included. There it is read as code throughout: reporting what sits inside
    one is the cautious mistake, and missing it is the other kind.
    """
    if token.type != tokenize.STRING:
        return token.type in LITERAL
    return "f" not in re.match(r"[A-Za-z]*", token.string).group().lower()


def abuts(before, token):
    """Whether these two tokens are written with nothing between them.

    After a dot or an opening bracket, before a dot, a comma or a closing
    bracket, and between a name and the bracket that calls or indexes it. The
    patterns allow no space in `.most_common(` or `.get("verdict")`, and the
    lines of a statement used to be joined by one wherever the break fell, so a
    site wrapped after its dot or inside its call was read as clean.

    A keyword is not a name here: `in (a, b)` keeps its space, which the filter
    pattern requires.
    """
    a = before.string if before.type == tokenize.OP else ""
    b = token.string if token.type == tokenize.OP else ""
    if a in (".", "(", "[", "{") or b in (".", ",", ")", "]", "}"):
        return True
    called = a in (")", "]") or (before.type == tokenize.NAME
                                 and not keyword.iskeyword(before.string))
    return called and b in ("(", "[")


class Statement:
    """One logical line, as a single line of text.

    Comments are gone and the physical lines are joined, which is the text the
    patterns were written against. `rows` maps a position in that text back to
    the line it was written on, and `quoted` lists the stretches of it that are
    string literals. `end` is the line the statement's NEWLINE is on, which is
    below its last token when a continued line ends in a comment.
    """

    def __init__(self, tokens, end=None):
        self.line, self.end = tokens[0].start[0], end or tokens[-1].end[0]
        self.text, self.rows, self.quoted = "", [], []
        before = None
        for token in tokens:
            same_row = before is not None and token.start[0] == before.end[0]
            if before is not None and not abuts(before, token):
                self.text += (" " * (token.start[1] - before.end[1])
                              if same_row else " ")
            if not same_row:
                self.rows.append((len(self.text), token.start[0]))
            begin = len(self.text)
            # A token can cover several lines: a triple-quoted string, or the
            # whole of an f-string before 3.12. Each of its lines is mapped, so
            # that what follows it is still reported where it was written.
            for n, piece in enumerate(token.string.split("\n")):
                if n:
                    self.text += " "
                    self.rows.append((len(self.text), token.start[0] + n))
                self.text += piece
            if literal(token):
                self.quoted.append((begin, len(self.text)))
            before = token

    def where(self, offset):
        """(where that line's code starts in the text, the line's number)."""
        return max(entry for entry in self.rows if entry[0] <= offset)


def said(error):
    """An exception as the report words it: its type, then what it says."""
    if isinstance(error, tokenize.TokenError):
        text = error.args[0]
    else:
        text = getattr(error, "msg", None) or str(error)
    return f"{type(error).__name__}: {text}" if text else type(error).__name__


def statements(source):
    """Cut `source` into logical lines: (statements, comments, stopped).

    LOGICAL lines, not a fixed window. Four attempts here, the first three each
    failing a different way, which is the argument for testing a checker
    against a known answer rather than reading it:
      line-by-line      missed a comprehension whose filter sat on line two
      3-line window     joined UNRELATED neighbours, so `terms = [t.lower()
                        for t in ...]` matched because an `if` sat nearby
      bracket counting  accumulated until the brackets balanced, and counted
                        the ones inside string literals. One regex literal with
                        an unmatched "(" held its statement open, nothing below
                        closed it, and whatever was pending at the end of the
                        file was dropped unscanned: the tail of 24 of the 52
                        top-level files, 545 of llm.py's 595 lines. It cut
                        comments at the first "#" as well, in a string or not.
      this              asks the tokenizer, which is the one thing that knows
                        where a string ends. NEWLINE closes a logical line; NL
                        is a line break inside one.

    `comments` is {line: (text, whether it stands between statements)}.
    `stopped` is None, or (line, why) when the file could not be read to its
    end; nothing from that line on has been checked.
    """
    found, comments, pending, stopped, reached = [], {}, [], None, 1
    if not source.endswith("\n"):
        # 3.10 and 3.11 emit no NEWLINE for a last line that starts with "#",
        # even when that line is the end of a string.
        source += "\n"
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            reached = token.end[0]
            if token.type == tokenize.COMMENT:
                comments[token.start[0]] = (token.string, not pending)
            if token.type == tokenize.NEWLINE:
                if pending:
                    found.append(Statement(pending, token.start[0]))
                pending = []
            elif token.type not in SILENT:
                pending.append(token)
    except Exception as error:
        # TokenError and SyntaxError are the tokenizer's contract. From 3.12 it
        # is C underneath and also raises whatever that trips over: SystemError
        # (3.12) and MemoryError (3.14) on one valid f-string, UnicodeEncodeError
        # on a lone surrogate. None of them is a reason to stop reading the
        # OTHER files, and every one of them means this file was not read.
        line = getattr(error, "lineno", None)
        if line is None and isinstance(error, tokenize.TokenError):
            line = error.args[1][0]
        reason = f"the tokenizer stopped ({said(error)})"
        stopped = (line or reached, f"not read past this line: {reason}")
    if pending:
        # Still open when the tokens ran out. It is scanned as far as it goes
        # AND the file is reported from the line that opened it. Dropping it is
        # what the bracket counter did.
        found.append(Statement(pending))
        stopped = (pending[0].start[0], stopped[1] if stopped else
                   "not read to its end: the statement opened on this line is "
                   "still open where the file ends")
    return found, comments, stopped


def unparsed(source):
    """None, or (line, why) when this Python cannot parse `source`.

    A tokenizer does not reject what it does not understand. It cuts it some
    other way and says nothing: before 3.12, f"{names["top"]}: {c.most_common(1)[0]}"
    is two plain strings with a name between them, the site lies inside the
    second, and a gate that trusts the cut reads it as text. So the parser is
    asked whether the file is Python at all, as this interpreter knows it.
    """
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ast.parse(source)
    except Exception as error:
        version = "%d.%d" % sys.version_info[:2]
        return (getattr(error, "lineno", None) or 1,
                f"Python {version} cannot parse this file, so where the "
                f"tokenizer cut its statements cannot be relied on "
                f"({said(error)})")
    return None


def acknowledged(statement, comments):
    """Whether `# aggregation-ok` is written on this statement, or on the line
    above it with no statement of its own.

    The window used to be the statement's first line and one line either side.
    The line below belongs to the next statement, so an acknowledgement written
    for one site silenced the site above it too.
    """
    rows = range(statement.line, statement.end + 1)
    notes = [comments[row][0] for row in rows if row in comments]
    text, alone = comments.get(statement.line - 1, ("", False))
    if alone:
        notes.append(text)
    return any(MARKER.search(note) for note in notes)


def in_code(pattern, statement):
    """The first match that begins in code, not inside a string literal.

    Strings stay in the text because two of the alternatives name one
    ("error", "verdict"), so a match may run INTO a string. One that starts
    inside a string is prose or quoted source: this file's own docstring, or a
    test's planted input.
    """
    at = 0
    while True:
        match = pattern.search(statement.text, at)
        if match is None or not any(a <= match.start() < b
                                    for a, b in statement.quoted):
            return match
        at = match.start() + 1


def sites(source):
    """(sites, statements read, stopped) for the text of one file."""
    found, comments, stopped = statements(source)
    hits = []
    for statement in found:
        if acknowledged(statement, comments):
            continue
        for pattern, what, why in PATTERNS:
            match = in_code(pattern, statement)
            if match:
                start, line = statement.where(match.start())
                hits.append((line, what, why, statement.text[start:start + 76]))
    return hits, len(found), stopped or unparsed(source)


def sources(path, unlisted):
    """Every Python file at or under `path`. A folder that cannot be listed is
    appended to `unlisted` as the OSError it raised; os.walk swallows it
    otherwise, and a folder skipped in silence reads as clean."""
    if os.path.isfile(path):
        yield path
        return
    for root, dirs, files in os.walk(path, onerror=unlisted.append):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in sorted(files):
            if name.endswith(".py") and name != os.path.basename(__file__):
                yield os.path.join(root, name)


def scan(paths):
    """Every site under `paths`, and everything there that was not read."""
    hits, unread, files, count = [], [], 0, 0
    for argument in paths:
        unlisted, offered = [], 0
        for path in sources(argument, unlisted):
            offered += 1
            try:
                with tokenize.open(path) as handle:
                    source = handle.read()
            except Exception as error:
                # This was `continue`, and a file skipped in silence reads as
                # clean. Not only OSError and UnicodeDecodeError: a coding
                # line naming a codec that is not text raises LookupError.
                unread.append((path, 0, f"could not be read ({said(error)})"))
                continue
            found, read, stopped = sites(source)
            files, count = files + 1, count + read
            hits += [(path,) + hit for hit in found]
            if stopped:
                unread.append((path,) + stopped)
        for error in unlisted:
            unread.append((error.filename or argument, 0,
                           f"could not be opened "
                           f"({error.strerror or said(error)})"))
        if not offered and not unlisted:
            # Each argument answers for itself, or an empty folder is covered
            # by the file named beside it.
            unread.append((argument, 0, "no Python source here"))
    return Scan(hits, unread, files, count)


def shown(path):
    """A path as the report prints it. An empty argument has no relative form."""
    return os.path.relpath(path) if path else "''"


def main():
    # Describe the gate when asked to. It used to take "--help" for a path,
    # find no such folder, walk nothing and print "clean": fifty-three
    # characters, which is enough to pass CI's check that a tool printed help.
    if '-h' in sys.argv[1:] or '--help' in sys.argv[1:]:
        print(__doc__.strip())
        return 0
    paths = sys.argv[1:] or [os.path.dirname(os.path.abspath(__file__))]
    hits, unread, files, count = scan(paths)

    if not hits and not unread:
        print(f"  clean — no unguarded tie-breaks or filtered tallies in "
              f"{count} statements across {files} file(s), each read to its end")
        return 0

    if hits:
        print(f"  {len(hits)} site(s) where a failure may not land on the "
              f"cautious side (DESIGN §0.1)\n")
    for path, line, what, why, text in hits:
        print(f"  {shown(path)}:{line}  {what}")
        print(f"      {text}")
        print(f"      {why}")
        print(f"      If it is already handled, mark it: # aggregation-ok: <how>\n")
    if unread:
        print(f"  {len(unread)} place(s) not read. What was not read was not "
              f"checked, so this is not a pass.\n")
    for path, line, reason in unread:
        print(f"  {shown(path)}:{line}" if line else f"  {shown(path)}")
        print(f"      {reason}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
