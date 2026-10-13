"""Tests for the commons-file-resolution skill — SOP 6 and scripts/parse-file-refs.py.

Offline only: the parser is pure string handling, so these tests run the script as a
subprocess against fixtures and never touch a Wikimedia API.
"""
from __future__ import annotations

import json
import py_compile
import subprocess
import sys

from conftest import SKILLS_DIR, read_skill  # noqa: E402

SKILL = "commons-file-resolution"
SCRIPT = SKILLS_DIR / SKILL / "scripts" / "parse-file-refs.py"

# The file that exposed the comma bug (exists on Commons, pageid 165943740).
COMMA_FILE = ("File:MKr377445 Susanne Wuest (In die Sonne schauen, Cannes 2025).jpg")


def run(refs: list[str], *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args, *refs],
                          capture_output=True, text=True, timeout=60)


def titles(result: subprocess.CompletedProcess) -> list[str]:
    return [line for line in result.stdout.splitlines() if line.strip()]


# --------------------------------------------------------------- frontmatter/content


def test_frontmatter_declares_the_skill_and_a_fresh_date():
    text = read_skill(SKILL)
    assert "name: commons-file-resolution" in text
    assert "last_verified: 2026-" in text
    assert "license: MIT" in text


def test_sop6_is_registered_in_the_quick_reference():
    text = " ".join(read_skill(SKILL).split())
    assert "## SOP 6: From Pasted References to `File:` Titles" in text
    assert "Pasted list of references" in text
    assert "Multi-title queries are **pipe-separated**" in text


def test_the_comma_rule_is_documented():
    text = " ".join(read_skill(SKILL).split())
    assert "Never split a pasted list on commas" in text
    assert "does not treat a comma as a separator" in text
    # the batch example must join with a pipe, and say so
    assert '"|".join(titles)' in text
    assert '"|", never ","' in text


def test_normalisation_pitfalls_are_documented():
    text = " ".join(read_skill(SKILL).split())
    for anchor in ("`#` silently truncates",
                   "Namespace aliases are per-wiki",
                   "answers 301 *or* 302",
                   "names the wiki",
                   "Deduplicate on `(wiki, case-folded title)`"):
        assert anchor in text, f"missing pitfall: {anchor}"


def test_cross_references_resolve():
    text = read_skill(SKILL)
    for target in ("wikimedia-commons-thumbnails", "pattypan", "wikimedia-commons",
                   "wikimedia-api-access"):
        assert f"../{target}/SKILL.md" in text, f"missing cross-reference to {target}"
        assert (SKILLS_DIR / target / "SKILL.md").exists(), f"{target} does not exist"


# ------------------------------------------------------------------------- script


def test_script_compiles():
    py_compile.compile(str(SCRIPT), doraise=True)


def test_script_reports_usage_when_run_with_nothing_to_do():
    result = run([], "--help")
    assert result.returncode == 0
    assert "Normalise pasted" in result.stdout


def test_script_normalises_every_documented_form():
    refs = [
        "Foo.jpg",                                              # bare name
        "File:Bar.jpg",                                         # wikitext title
        "[[File:Baz.jpg|thumb|Caption, with commas]]",           # embedded wikitext
        "https://commons.wikimedia.org/wiki/File:Quux.jpg",      # page URL
        "https://commons.wikimedia.org/w/index.php?title=File%3AQuux2.jpg",
        "https://commons.wikimedia.org/wiki/Special:FilePath/Quux3.jpg",
        "https://commons.wikimedia.org/wiki/Special:Redirect/file/Quux4.jpg",
        "https://upload.wikimedia.org/wikipedia/commons/0/06/Quux5.jpg",
        "https://upload.wikimedia.org/wikipedia/commons/thumb/0/06/Quux6.jpg/640px-Quux6.jpg",
        "https://thumb.wikimedia.org/wikipedia/commons/0/06/Quux7.jpg/960px-Quux7.jpg",
        "https://en.wikipedia.org/wiki/File:Quux8.jpg",
    ]
    result = run(refs)
    assert result.returncode == 0, result.stderr
    assert titles(result) == [
        "File:Foo.jpg", "File:Bar.jpg", "File:Baz.jpg", "File:Quux.jpg", "File:Quux2.jpg",
        "File:Quux3.jpg", "File:Quux4.jpg", "File:Quux5.jpg", "File:Quux6.jpg",
        "File:Quux7.jpg", "File:Quux8.jpg",
    ]


def test_commas_inside_a_file_name_survive():
    """The regression this SOP exists for: a comma is not an entry separator."""
    result = run([COMMA_FILE])
    assert result.returncode == 0, result.stderr
    assert titles(result) == [COMMA_FILE]


def test_a_bare_list_on_one_line_still_splits():
    result = run(["Foo.jpg, Bar.jpg; Baz.jpg"])
    assert result.returncode == 0, result.stderr
    assert titles(result) == ["File:Foo.jpg", "File:Bar.jpg", "File:Baz.jpg"]


def test_prefixed_entries_on_one_line_split():
    result = run(["File:Foo.jpg, File:Bar.jpg"])
    assert result.returncode == 0, result.stderr
    assert titles(result) == ["File:Foo.jpg", "File:Bar.jpg"]


def test_fragment_is_dropped():
    result = run([f"{COMMA_FILE}#/media/File:Other.jpg"])
    assert titles(result) == [COMMA_FILE.split("#")[0]]


def test_upload_path_reveals_the_wiki():
    result = run(["https://upload.wikimedia.org/wikipedia/en/8/8f/Wiki.png"], "--json")
    assert result.returncode == 0, result.stderr
    entry = json.loads(result.stdout)[0]
    assert entry["title"] == "File:Wiki.png"
    assert entry["wiki"] == "en.wikipedia.org"


def test_unparseable_input_is_reported_not_dropped():
    result = run(["not a file"])
    assert result.returncode == 1
    assert "could not parse" in result.stderr


def test_characters_no_title_can_contain_are_reported():
    result = run(["Bad<Name>.jpg"])
    assert result.returncode == 1
    assert "cannot appear in a MediaWiki title" in result.stderr


def test_stdin_mode_matches_argument_mode():
    result = subprocess.run([sys.executable, str(SCRIPT)],
                            input="Foo.jpg\n" + COMMA_FILE + "\n",
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert titles(result) == ["File:Foo.jpg", COMMA_FILE]


def test_nothing_to_do_is_refused_rather_than_silently_succeeding():
    """Bare invocation with no input must not look like a successful no-op."""
    result = subprocess.run([sys.executable, str(SCRIPT)], input="",
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 2
    assert "no references" in result.stderr
