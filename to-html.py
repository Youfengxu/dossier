#!/usr/bin/env python3
"""A spreadsheet as a read-only web page.

Opening a workbook to read it means opening an editor, and an editor can save.
The round trip through one of them truncated a 1054-character cell to a single
character — silently, and in the one cell that mattered. A browser cannot do
that: there is no write path.

    ./to-html.py --xlsx D2-inputs-column.xlsx --sheet "D2 - Architecture" \
                 --cols A,C,D,K,L --out D2-inputs-column.html

Long cells are the point, so nothing is truncated and nothing is ellipsised;
the page wraps. Dark and light both, because this is read on a tablet as often
as a desktop.
"""

import argparse
import html
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import matrix as matrix_reader                              # noqa: E402

CSS = """
:root { --bg:#fff; --fg:#1a1a1a; --line:#d6d6d6; --head:#f2f2f2; --muted:#666; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#16181c; --fg:#e6e6e6; --line:#33363d; --head:#22252b; --muted:#9aa0a6; }
}
* { box-sizing: border-box; }
body { background:var(--bg); color:var(--fg); margin:0; padding:2rem 1.25rem;
  font:16px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,sans-serif; }
h1 { font-size:1.4rem; margin:0 0 .25rem; }
p.meta { color:var(--muted); margin:0 0 1.5rem; font-size:.9rem; }
.wrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; min-width:720px; }
th, td { border:1px solid var(--line); padding:.6rem .7rem; text-align:left;
  vertical-align:top; }
th { background:var(--head); position:sticky; top:0; font-weight:600; }
tr:nth-child(even) td { background:color-mix(in srgb, var(--head) 45%, transparent); }
td.id { white-space:nowrap; font-variant-numeric:tabular-nums; font-weight:600; }
@media print { body { padding:0; } th { position:static; } }
"""


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--xlsx", required=True)
    parser.add_argument("--sheet", required=True)
    parser.add_argument("--cols", help="comma-separated column letters")
    parser.add_argument("--headers", help="comma-separated display names")
    parser.add_argument("--title")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    rows = matrix_reader.read_sheet(args.xlsx, args.sheet)
    cols = [c.strip().upper() for c in args.cols.split(",")] if args.cols \
        else sorted({k for r in rows for k in r}, key=lambda x: (len(x), x))
    if args.headers:
        names = [h.strip() for h in args.headers.split(",")]
    else:
        names = [rows[0].get(c, "").strip() or c for c in cols]

    body = []
    shown = 0
    for row in rows[1:]:
        if not any(row.get(c, "").strip() for c in cols):
            continue
        shown += 1
        cells = "".join(
            f'<td class="{"id" if i == 0 else ""}">'
            f'{html.escape(row.get(c, "").strip())}</td>'
            for i, c in enumerate(cols))
        body.append(f"<tr>{cells}</tr>")

    title = args.title or f"{args.sheet}"
    page = (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>"
        f"<h1>{html.escape(title)}</h1>"
        f"<p class=\"meta\">{shown} rows &middot; read-only view of "
        f"{html.escape(os.path.basename(args.xlsx))}</p>"
        "<div class=\"wrap\"><table><thead><tr>"
        + "".join(f"<th>{html.escape(n)}</th>" for n in names)
        + "</tr></thead><tbody>" + "".join(body)
        + "</tbody></table></div></body></html>")

    out_path = os.path.abspath(os.path.expanduser(args.out))
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(page)
    print(f"wrote {out_path}  ({shown} rows, {len(cols)} columns)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
