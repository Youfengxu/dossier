#!/usr/bin/env python3
"""Extract plain text from OOXML files (.docx / .pptx / .xlsx) using stdlib only.

No python-docx / python-pptx / openpyxl required — none were installable on the
review machine. Handles typical vendor deliverables well enough for grep and reading.

    python3 extract.py "System Design Description v2.docx" > v2.txt
    python3 extract.py *.pptx

Tables come out as sequential paragraph text (cell structure is lost but content
is preserved), which is fine for searching and quoting.
"""
import sys, zipfile, re
from xml.etree import ElementTree as ET

NS = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    's': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
}
R_ID = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'


def docx(z):
    out = []
    if 'word/document.xml' not in z.namelist():
        return ''
    root = ET.fromstring(z.read('word/document.xml'))
    for el in root.iter():
        if el.tag.split('}')[-1] == 'p':
            txt = ''.join(t.text or '' for t in el.iter('{%s}t' % NS['w']))
            if txt.strip():
                out.append(txt)
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
        print('CANNOT OPEN %s: %s' % (path, e))
        return
    low = path.lower()
    if low.endswith(('.docx', '.dotx')):
        print(docx(z))
    elif low.endswith(('.pptx', '.potx')):
        print(pptx(z))
    elif low.endswith(('.xlsx', '.xlsm')):
        print(xlsx(z))
    else:
        print('unknown type: ' + path)


if __name__ == '__main__':
    for p in sys.argv[1:]:
        print('\n########## %s ##########' % p)
        main(p)
