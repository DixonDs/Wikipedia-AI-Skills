#!/usr/bin/env python3
"""Normalise pasted Wikimedia Commons file references to `File:` titles.

Handles the forms a user actually pastes (bare filenames, wikitext, commons URLs,
upload/thumb URLs, Special:FilePath, other wikis) and reports which wiki each one
belongs to. Standard library only.

    $ python3 parse-file-refs.py "Foo.jpg" "[[File:Bar.jpg|thumb]]" \
        "https://upload.wikimedia.org/wikipedia/commons/0/06/Baz.jpg"
    File:Foo.jpg
    File:Bar.jpg
    File:Baz.jpg

    $ printf '%s\n' "https://en.wikipedia.org/wiki/File:Wiki.png" | python3 parse-file-refs.py --json
    [{"raw": "https://en.wikipedia.org/wiki/File:Wiki.png", "title": "File:Wiki.png",
      "wiki": "en.wikipedia.org"}]

Exit codes: 0 = every line normalised, 1 = at least one line could not be
normalised (the offending line is reported on stderr), 2 = usage error.

Never splits a line that already reads as one file name: commas, semicolons and
parentheses are legal in Commons titles (measured 2026-10-13 —
`File:MKr377445 Susanne Wuest (In die Sonne schauen, Cannes 2025).jpg` resolves).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse

THUMB = re.compile(r"/thumb/(.+?)/[^/]*?(\d+)px-[^/]*$")
FILEURL = re.compile(r"/(?:wiki/|index\.php\?title=)((?:File|Image):[^?#]+)", re.I)
SPECIAL = re.compile(r"special:(?:filepath|redirect/file)/(.+)$", re.I)
PREFIXED_SEP = re.compile(r"[,;]\s*(?=(?:File|Image|\[\[|https?://|//|Special:))", re.I)
EXT = (".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".webp", ".svg", ".pdf", ".djvu",
       ".xcf", ".webm", ".ogv", ".mp4", ".mp3", ".ogg", ".flac", ".wav", ".stl")

# upload.wikimedia.org/wikipedia/<project>/… names a project, not always Commons.
UPLOAD_PROJECT = {"commons": "commons.wikimedia.org", "meta": "meta.wikimedia.org",
                  "species": "species.wikimedia.org", "wikidata": "www.wikidata.org"}
PROJECT_SUFFIXES = (".wikipedia.org", ".wikimedia.org", ".wikisource.org", ".wiktionary.org",
                    ".wikibooks.org", ".wikinews.org", ".wikiversity.org", ".wikivoyage.org",
                    ".wikiquote.org")


def to_title(ref: str) -> str | None:
    """Return the `File:` title for one reference, or None if it is not one."""
    ref = ref.strip().strip("\"'").lstrip("*-# ").strip()
    ref = re.sub(r"^\[\[|\]\]$", "", ref).split("|")[0]
    u = urllib.parse.urlsplit(ref if ref.startswith("http")
                              else "https://" + ref if ref.startswith("//") else "")
    if u.netloc:                                    # keep the query: ?title= carries the name
        full = urllib.parse.unquote(u.path)
        if u.query:
            full += "?" + urllib.parse.unquote(u.query)
    else:
        full = ref
    full = full.split("#", 1)[0]                    # a fragment is not part of the name
    if u.netloc.startswith(("upload.", "thumb.")):
        # /thumb/{h}/{h}/Name.jpg/640px-Name.jpg and /…/Name.jpg/640px-Name.jpg both occur
        name = re.sub(r"^\d+px-", "", os.path.basename(full)).replace("_", " ")
        if not os.path.splitext(name)[1]:
            m = THUMB.search(full)
            name = os.path.basename(m.group(1)).replace("_", " ") if m else name
        return "File:" + name if os.path.splitext(name)[1].lower() in EXT else None
    m = SPECIAL.search(full)
    if m:
        name = m.group(1).removeprefix("File:").removeprefix("file:").replace("_", " ")
        return "File:" + name if os.path.splitext(name)[1].lower() in EXT else None
    m = FILEURL.search(full)
    if m:
        return m.group(1).replace("_", " ")
    if re.match(r"^(file|image):", full, re.I):
        return full[0].upper() + full[1:] if os.path.splitext(full)[1].lower() in EXT else None
    if os.path.splitext(full)[1].lower() in EXT:
        return "File:" + full.replace("_", " ")
    return None


def wiki_for(ref: str, default: str = "commons.wikimedia.org") -> str:
    """Which wiki's api.php can resolve this reference (SOP 5)."""
    low = ref.lower()
    if not (re.match(r"^(https?:)?//", low) or re.match(r"^[\w.-]+\.(?:org|com|net|io)/", low)):
        return default
    url = ref if low.startswith("http") else ("https:" + ref if ref.startswith("//")
                                             else "https://" + ref)
    u = urllib.parse.urlsplit(url)
    host, path = u.netloc.lower(), urllib.parse.unquote(u.path)
    if host.startswith(("upload.", "thumb.")):
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2 and parts[0] == "wikipedia":
            code = parts[1]
            return UPLOAD_PROJECT.get(code, f"{code}.wikipedia.org" if len(code) <= 3
                                      else f"{code}.wikimedia.org")
        return default
    if host.startswith("commons.m."):
        return "commons.wikimedia.org"
    if host.endswith(PROJECT_SUFFIXES):
        return host
    return default


def split_units(line: str) -> list[str]:
    """Split one pasted line into entries without breaking commas inside a file name."""
    line = line.strip()
    if not line:
        return []
    if PREFIXED_SEP.search(line):                   # "File:A.jpg, File:B.jpg"
        return [p for p in PREFIXED_SEP.split(line) if p.strip()]
    pieces = [p.strip() for p in re.split(r"[,;]+", line) if p.strip()]
    if len(pieces) > 1 and all(os.path.splitext(p)[1].lower() in EXT for p in pieces):
        return pieces                               # "Foo.jpg, Bar.jpg" — a bare list
    return [line]                                   # "…(In die Sonne schauen, Cannes 2025).jpg"


ILLEGAL = "#<>[]|{}"


def normalise(text: str, default_wiki: str = "commons.wikimedia.org") -> tuple[list[dict], list[str]]:
    """Return (entries, unparsed_lines) for pasted text (one reference per line).

    A line that cannot be a title, or that carries characters no MediaWiki title can
    contain (`# < > [ ] | { }`), is reported in the second list — never dropped silently.
    """
    entries, bad = [], []
    for raw_line in text.splitlines():
        for unit in split_units(raw_line):
            title = to_title(unit)
            if not title:
                bad.append(unit.strip())
                continue
            bad_chars = sorted(set(ILLEGAL) & set(title))
            entries.append({"raw": unit.strip(), "title": title,
                            "wiki": wiki_for(unit, default_wiki)})
            if bad_chars:
                bad.append(f"{unit.strip()} (contains {' '.join(bad_chars)} — "
                           f"cannot appear in a MediaWiki title)")
    return entries, bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Normalise pasted Commons/Wikimedia file references to File: titles.")
    ap.add_argument("refs", nargs="*", help="references; omit to read one per line on stdin")
    ap.add_argument("--json", action="store_true", help="emit raw/title/wiki objects as JSON")
    ap.add_argument("--default-wiki", default="commons.wikimedia.org",
                    help="wiki for bare titles and File: references (default: %(default)s)")
    args = ap.parse_args(argv)

    text = "\n".join(args.refs) if args.refs else sys.stdin.read()
    if not text.strip():
        print("no references: pass them as arguments, or pipe one per line on stdin",
              file=sys.stderr)
        return 2
    entries, bad = normalise(text, args.default_wiki)
    if args.json:
        json.dump(entries, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        for e in entries:
            print(e["title"])
    for line in bad:
        print(f"could not parse: {line!r}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
