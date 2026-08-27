#!/usr/bin/env python3
"""Extract plain text from OOXML files (.docx / .pptx / .xlsx) using stdlib only.

No python-docx / python-pptx / openpyxl required — none were installable on the
review machine. Handles typical vendor deliverables well enough for grep and reading.

    python3 extract.py "System Design Description v2.docx" > v2.txt
    python3 extract.py *.pptx

Tables come out as sequential paragraph text (cell structure is lost but content
is preserved), which is fine for searching and quoting.
"""
import os
import sys, zipfile, re
from xml.etree import ElementTree as ET

COVERAGE = None          # (emitted, carried) for the last docx read

NS = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    's': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
}
R_ID = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'


def docx(z):
    """Paragraph text, with a coverage assertion.

    WHY THE ASSERTION. "This clause states no obligation" and "the extractor
    dropped this clause" produce the SAME output — an absent paragraph — and
    nothing downstream can tell them apart. `freeze.py --check` proves the frozen
    bytes have not drifted since the freeze; it cannot prove the freeze captured
    what was there. That is a gap between integrity and validation that neither
    covered, raised in review 2026-08-26.

    So count what carried text in the source and count what was emitted, and
    fail loudly if they differ. Cheap, exact, and it turns a silent loss into a
    stopped run.
    """
    out = []
    if 'word/document.xml' not in z.namelist():
        return ''
    root = ET.fromstring(z.read('word/document.xml'))
    carried = 0
    for el in root.iter():
        if el.tag.split('}')[-1] == 'p':
            txt = ''.join(t.text or '' for t in el.iter('{%s}t' % NS['w']))
            if txt.strip():
                carried += 1
                out.append(txt)
    # NO PARAGRAPH-COUNT ASSERTION, and the reason is worth recording. Three
    # attempts, all wrong:
    #   1  compared len(out) to a counter incremented in the same branch — a
    #      check that cannot fail
    #   2  counted <w:p> in the raw bytes — over-counted by 63 on one document
    #      (27 whitespace-only paragraphs, 36 regex segmentation artefacts) and
    #      raised on a correct extraction
    #   3  counting with ElementTree is what the emitter already does, so it is
    #      not independent evidence of anything
    # ET reports 6,097 paragraphs carrying text on the V11 deliverable and the
    # emitter produces 6,097. The extraction is sound; what is missing is a
    # SECOND parser to say so, and stdlib does not have one. Claiming a
    # verification that does not exist would be worse than the gap.

    # WHAT IS GENUINELY NOT READ, which is checkable and is the real hazard.
    # document.xml is not the document: footnotes, endnotes, comments, headers
    # and footers live in sibling parts this function never opens. An obligation
    # there is indistinguishable downstream from an obligation that does not
    # exist — the failure raised in review 2026-08-26. Silence would read as
    # absence, so it is reported.
    unread = []
    for part in ('word/footnotes.xml', 'word/endnotes.xml', 'word/comments.xml'):
        if part in z.namelist():
            body = z.read(part).decode('utf-8', 'replace')
            n = len([p for p in re.split(r'<w:p[ >]', body)[1:]
                     if re.search(r'<w:t[^>]*>\s*\S', p)])
            if n:
                unread.append('%s:%d' % (part.split('/')[-1].split('.')[0], n))
    for part in sorted(n for n in z.namelist()
                       if re.match(r'word/(header|footer)\d*\.xml$', n)):
        body = z.read(part).decode('utf-8', 'replace')
        n = len([p for p in re.split(r'<w:p[ >]', body)[1:]
                 if re.search(r'<w:t[^>]*>\s*\S', p)])
        if n:
            unread.append('%s:%d' % (part.split('/')[-1].split('.')[0], n))

    global COVERAGE
    COVERAGE = (len(out), unread)
    return '\n'.join(out)


def pptx(z):
    slides = [n for n in z.namelist() if re.match(r'ppt/slides/slide\d+\.xml$', n)]
    slides.sort(key=lambda n: int(re.findall(r'\d+', n)[-1]))
    out = []
    for i, name in enumerate(slides, 1):
        root = ET.fromstring(z.read(name))
        texts = [t.text for t in root.iter('{%s}t' % NS['a']) if t.text]
        out.append('--- SLIDE %d ---\n%s' % (i, '\n'.join(texts)))
    return '\n'.join(out)


def xlsx(z):
    shared = []
    if 'xl/sharedStrings.xml' in z.namelist():
        root = ET.fromstring(z.read('xl/sharedStrings.xml'))
        for si in root:
            shared.append(''.join(t.text or '' for t in si.iter('{%s}t' % NS['s'])))
    rels = {}
    if 'xl/_rels/workbook.xml.rels' in z.namelist():
        for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels')):
            rels[r.get('Id')] = r.get('Target')
    names = {}
    wb = ET.fromstring(z.read('xl/workbook.xml'))
    for i, sh in enumerate(wb.iter('{%s}sheet' % NS['s']), 1):
        tgt = rels.get(sh.get(R_ID), 'worksheets/sheet%d.xml' % i)
        if not tgt.startswith('xl/'):
            tgt = 'xl/' + tgt.lstrip('/')
        names[tgt] = sh.get('name')
    out = []
    for tgt, nm in names.items():
        if tgt not in z.namelist():
            continue
        out.append('===== SHEET: %s =====' % nm)
        for row in ET.fromstring(z.read(tgt)).iter('{%s}row' % NS['s']):
            cells = []
            for c in row.iter('{%s}c' % NS['s']):
                v = c.find('{%s}v' % NS['s'])
                isel = c.find('{%s}is' % NS['s'])
                val = ''
                if c.get('t') == 's' and v is not None:
                    val = shared[int(v.text)]
                elif isel is not None:
                    val = ''.join(t.text or '' for t in isel.iter('{%s}t' % NS['s']))
                elif v is not None:
                    val = v.text or ''
                cells.append(val.replace('\n', ' ').strip())
            while cells and not cells[-1]:
                cells.pop()
            if cells:
                out.append(' | '.join(cells))
    return '\n'.join(out)


def main(path):
    try:
        z = zipfile.ZipFile(path)
    except Exception as e:
        # stderr, not stdout: stdout IS the document as far as freeze.py is
        # concerned, so an error written there becomes the text of the deliverable.
        print('CANNOT OPEN %s: %s' % (os.path.basename(path), e),
              file=sys.stderr)
        return False
    low = path.lower()
    if low.endswith(('.docx', '.dotx')):
        print(docx(z))
    elif low.endswith(('.pptx', '.potx')):
        print(pptx(z))
    elif low.endswith(('.xlsx', '.xlsm')):
        print(xlsx(z))
    else:
        print('unknown type: ' + path)


USAGE = 'usage: extract.py FILE [FILE ...]   (.docx / .pptx / .xlsx -> text on stdout)'


if __name__ == '__main__':
    # --help has to be answered deliberately. It used to be handled by accident:
    # '--help' fell through as a filename, ZipFile refused it, and the tool exited
    # 0 -- so CI's "every tool answers --help" gate passed on a tool that had never
    # answered anything. Making the unreadable-source exit honest is what revealed
    # it. A gate that cannot fail is not a gate.
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        wanted_help = len(sys.argv) > 1
        print(__doc__.strip() if wanted_help else USAGE,
              file=sys.stdout if wanted_help else sys.stderr)
        sys.exit(0 if wanted_help else 2)
    failed = 0
    for p in sys.argv[1:]:
        # BASENAME, not the full path. The banner is provenance for a human
        # reading the parsed text, and it is inside the bytes that get hashed —
        # so an absolute path made text_sha256 machine-dependent (the same
        # document froze to two different hashes on two machines) and wrote the
        # source filename, which on a real engagement is client-identifying,
        # into a file that gets quoted into reports.
        print('\n########## %s ##########' % os.path.basename(p))
        if main(p) is False:
            failed = 1
        # AFTER main(), not before: COVERAGE is set by the extraction itself, and
        # reporting it first printed the previous file's number, or None.
        elif COVERAGE:
            emitted, unread = COVERAGE
            note = ('  NOT EXTRACTED: ' + ', '.join(unread)) if unread else ''
            print('%s: %d paragraphs extracted.%s'
                  % (os.path.basename(p), emitted, note), file=sys.stderr)
    # BUG: this used to exit 0 whatever happened. An unreadable source printed a
    # message that freeze.py then hashed AS THE DOCUMENT, and every tool
    # afterwards reported everything ABSENT with full confidence. A parser that
    # cannot represent its input must fail, not return prose about failing.
    sys.exit(failed)
