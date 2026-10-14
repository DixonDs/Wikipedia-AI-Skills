"""Tests for scripts/verify-script-guards.py — the zero-argument guard verifier.

The verifier guards the bug class that arrived with the Toolforge maintainer audit:
a script whose every CLI option is optional ran its *default action* when invoked
bare, which for a network script means an unrequested crawl (count-maintainers.py,
~4,600 requests at 12 threads to a Wikimedia service).

It is an execution check, not a source check — the first draft pattern-matched and
was wrong in both directions (96 flags, 95 of them false). These tests pin the
criterion against fixtures with known behaviour, so the criterion cannot silently
drift back to a spelling test.

Kept fast and offline: the fixtures are synthetic, and the full-tree run belongs to
CI (`python3 scripts/verify-script-guards.py`), not to the unit suite.
"""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

_SPEC = importlib.util.spec_from_file_location(
    "verify_script_guards", REPO_ROOT / "scripts" / "verify-script-guards.py"
)
vsg = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vsg)

SKILLS_DIR = REPO_ROOT / ".claude" / "skills"


class Args:
    """Stand-in for the parsed CLI namespace scan() reads."""

    def __init__(self, files=None, timeout=1.0, max_hangs=10):
        self.files = files
        self.timeout = timeout
        self.max_hangs = max_hangs


# --- the criterion ------------------------------------------------------------

class TestVerdict:
    """Pass = exits in time AND (nonzero + said something, or zero + usage text)."""

    def test_hanging_is_a_violation(self):
        reason = vsg.verdict("TIMEOUT", "", True, 5.0)
        assert reason and "did not exit" in reason

    def test_quiet_success_is_a_violation(self):
        """Exit 0 having printed no usage-looking text = did its default action."""
        reason = vsg.verdict(0, "Preview saved to file:///tmp/x.html", False, 5.0)
        assert reason and "default action" in reason

    def test_quiet_success_with_no_output_is_a_violation(self):
        reason = vsg.verdict(0, "", False, 5.0)
        assert reason and "no output" in reason

    def test_nonzero_with_a_helpful_refusal_passes(self):
        """`No API key: pass --api-key <KEY>` is a guard, just not a pretty one."""
        assert vsg.verdict(1, "No API key: pass --api-key <KEY> or set FLICKR_API_KEY",
                           False, 5.0) is None

    def test_argparse_usage_passes(self):
        assert vsg.verdict(2, "usage: x.py [-h] --name NAME\nx.py: error: required",
                           False, 5.0) is None

    def test_usage_helper_that_exits_zero_passes(self):
        """`usage() { ...; exit 0; }` is a common shell idiom — not a violation."""
        assert vsg.verdict(0, "Usage: commons-search.sh \"query\"", False, 5.0) is None

    def test_unlaunchable_script_is_not_judged(self):
        """A script that could not be started tells us nothing about its guard."""
        assert vsg.verdict("EXC", "ValueError: bad shebang", False, 5.0) is None


class TestMissingDependency:
    """An uncaught import error means the guard never ran — not a pass, not a failure.

    Regression for the environment-sensitive verdict: with `requests` installed,
    language_explorer.py and api_client.py reached their default action under
    what CI (no `requests`) saw as a clean tree.
    """

    TRACEBACK = (
        "Traceback (most recent call last):\n"
        '  File "/tmp/x/lang.py", line 28, in <module>\n'
        "    import requests\n"
        "ModuleNotFoundError: No module named 'requests'\n"
    )

    def test_uncaught_import_error_reports_the_module(self):
        assert vsg.missing_dependency(1, self.TRACEBACK, False) == "requests"

    def test_import_error_without_a_traceback_is_not_unexercised(self):
        """A deliberate refusal may mention ImportError; that is still a guard."""
        assert vsg.missing_dependency(1, "ModuleNotFoundError: No module named 'requests'",
                                      False) is None

    def test_caught_dependency_is_guarded_not_unexercised(self):
        out = ("Traceback (most recent call last):\n"
               "ImportError: cannot import name 'session' from 'requests'\n"
               "missing optional dep: pip install requests\n")
        assert vsg.missing_dependency(1, out, False) is None

    def test_successful_run_is_not_unexercised(self):
        assert vsg.missing_dependency(0, self.TRACEBACK, False) is None

    def test_hang_is_not_unexercised(self):
        assert vsg.missing_dependency("TIMEOUT", self.TRACEBACK, True) is None


# --- fixtures with known behaviour -------------------------------------------

GUARDED_REFUSAL = '''#!/usr/bin/env python3
import argparse

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=None)
    args = ap.parse_args()
    if not args.key:
        raise SystemExit("No API key: pass --key <KEY>")
    print("did work")

if __name__ == "__main__":
    main()
'''

GUARDED_USAGE_ZERO = '''#!/usr/bin/env python3
import argparse

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", default=None)
    args = ap.parse_args()
    if not args.x:
        print("Usage: tool.py --x VALUE")
        raise SystemExit(0)
    print("did work")

if __name__ == "__main__":
    main()
'''

SILENT_WORK = '''#!/usr/bin/env python3
import argparse

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", default="all")
    args = ap.parse_args()
    print("crawled everything")

if __name__ == "__main__":
    main()
'''

HANGS = '''#!/usr/bin/env python3
import argparse, time

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", default=None)
    args = ap.parse_args()
    time.sleep(60)

if __name__ == "__main__":
    main()
'''

SILENT_SH = '''#!/usr/bin/env bash
set -euo pipefail
echo "did the default thing"
'''

GUARDED_SH = '''#!/usr/bin/env bash
set -euo pipefail
if [ $# -eq 0 ]; then
    echo "Usage: $(basename "$0") <arg>" >&2
    exit 1
fi
'''

NOT_AN_ENTRY_POINT = '''#!/usr/bin/env python3
"""A helper module, imported not executed."""
import argparse

def run(args): return args
'''

WRITES_A_FILE = '''#!/usr/bin/env python3
import argparse, pathlib

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", default=None)
    ap.parse_args()
    pathlib.Path(__file__).with_name("side-effect.txt").write_text("wrote here")
    print("Usage: wrote.py --x VALUE")

if __name__ == "__main__":
    main()
'''

NETWORK_PROBE = '''#!/usr/bin/env python3
import argparse, socket

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", default=None)
    ap.parse_args()
    try:
        socket.create_connection(("127.0.0.1", 9), timeout=1)
        print("REACHED")
    except OSError as e:
        print(f"blocked: {e}")

if __name__ == "__main__":
    main()
'''

# Mirrors setup-tunnel.sh as it was: no arity guard, and its only early exit is a
# *missing environment variable* — so it behaves differently on a configured machine
# than on CI, and only the configured machine reveals the violation.
ENV_DEPENDENT_SH = '''#!/usr/bin/env bash
set -euo pipefail
if [ -z "${TOOLFORGE_USER:-}" ]; then
    echo "Usage: env-dependent.sh [db_host]" >&2
    echo "   export TOOLFORGE_USER='name'" >&2
    exit 1
fi
echo "opened a tunnel as ${TOOLFORGE_USER}"
'''

DIES_ON_IMPORT = '''#!/usr/bin/env python3
import argparse
import this_module_does_not_exist_anywhere

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", default=None)
    ap.parse_args()
    print("did work")

if __name__ == "__main__":
    main()
'''

CATCHES_MISSING_DEP = '''#!/usr/bin/env python3
import argparse

try:
    import this_module_does_not_exist_anywhere as dep
except ImportError:
    dep = None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", default=None)
    ap.parse_args()
    if dep is None:
        raise SystemExit("missing optional dep: pip install this-module")
    print("did work")

if __name__ == "__main__":
    main()
'''

# A REPL-style default action: exits 0 having printed a banner, exactly what
# language_explorer.py used to do when invoked bare with no TTY (the banner is
# not usage-looking, so it must be flagged).
BANNER_THEN_EXIT = '''#!/usr/bin/env python3
import argparse

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?")
    ap.parse_args()
    print("=" * 20)
    print("Interactive mode — type text to identify its language")
    print("=" * 20)

if __name__ == "__main__":
    main()
'''


def write_skill_scripts(tmp_path: Path, scripts: dict[str, str]) -> Path:
    """Create a fake skills tree with one script per {relpath: source}."""
    root = tmp_path / "skills"
    for rel, body in scripts.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    return root


def init_repo(path: Path) -> None:
    """Make `path` a git repo, or skip when git is unavailable."""
    try:
        subprocess.run(["git", "init", "-q"], cwd=path, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):  # pragma: no cover
        pytest.skip("git is unavailable")


class TestGitignoredArtifacts:
    """Ignores must not decide the verdict: CI never sees a developer's scratch dir."""

    def test_gitignored_scripts_are_not_run(self, tmp_path):
        root = write_skill_scripts(tmp_path, {
            "demo-skill/scripts/real.sh": SILENT_SH,          # a genuine violation
            "demo-skill/scratch/ignored.sh": SILENT_SH,       # ignored -> must not run
        })
        init_repo(tmp_path)
        (tmp_path / ".gitignore").write_text("scratch/\n")

        violations, examined, _, _ = vsg.scan(root, Args())
        assert examined == 1, "the gitignored script was still examined"
        assert [v["file"] for v in violations] == ["demo-skill/scripts/real.sh"]

    def test_no_repo_still_scans_everything(self, tmp_path):
        """A bare checkout of the skills tree must stay scannable, not silently empty."""
        root = write_skill_scripts(tmp_path, {
            "demo-skill/scripts/real.sh": SILENT_SH,
        })
        violations, examined, _, _ = vsg.scan(root, Args())
        assert examined == 1
        assert [v["file"] for v in violations] == ["demo-skill/scripts/real.sh"]

    def test_gitignored_paths_helper(self, tmp_path):
        init_repo(tmp_path)
        (tmp_path / ".gitignore").write_text("scratch/\n")
        kept = tmp_path / "keep.md"
        kept.write_text("x")
        ignored = tmp_path / "scratch" / "out.md"
        ignored.parent.mkdir()
        ignored.write_text("x")
        got = vsg.gitignored_paths([kept, ignored])
        assert ignored.resolve() in got
        assert kept.resolve() not in got
        assert vsg.gitignored_paths([]) == set()


class TestScanCriterion:
    """End-to-end scan of fixtures whose no-arg behaviour is known by construction."""

    def test_flags_exactly_the_unguarded_scripts(self, tmp_path):
        root = write_skill_scripts(tmp_path, {
            "demo-skill/scripts/guarded_refusal.py": GUARDED_REFUSAL,
            "demo-skill/scripts/guarded_usage_zero.py": GUARDED_USAGE_ZERO,
            "demo-skill/scripts/guarded.sh": GUARDED_SH,
            "demo-skill/scripts/silent_work.py": SILENT_WORK,
            "demo-skill/scripts/silent.sh": SILENT_SH,
            "demo-skill/scripts/hangs.py": HANGS,
        })
        violations, examined, _, _ = vsg.scan(root, Args(timeout=1.5))
        flagged = {v["file"] for v in violations}
        assert flagged == {"demo-skill/scripts/silent_work.py",
                           "demo-skill/scripts/silent.sh",
                           "demo-skill/scripts/hangs.py"}
        assert examined == 6

    def test_module_without_main_is_skipped(self, tmp_path):
        """An importable helper is not an entry point and has nothing to guard."""
        root = write_skill_scripts(tmp_path, {
            "demo-skill/assets/helper.py": NOT_AN_ENTRY_POINT,
        })
        violations, examined, _, _ = vsg.scan(root, Args())
        assert violations == [] and examined == 0

    def test_exempt_scripts_are_not_run(self, tmp_path):
        root = write_skill_scripts(tmp_path, {
            "demo-skill/scripts/silent_work.py": SILENT_WORK,
        })
        vsg.EXEMPT["demo-skill/scripts/silent_work.py"] = "test fixture"
        try:
            violations, examined, _, _ = vsg.scan(root, Args())
        finally:
            del vsg.EXEMPT["demo-skill/scripts/silent_work.py"]
        assert violations == [] and examined == 0

    def test_max_hangs_stops_early(self, tmp_path):
        root = write_skill_scripts(tmp_path, {
            "demo-skill/scripts/silent_a.py": SILENT_WORK,
            "demo-skill/scripts/silent_b.py": SILENT_WORK,
            "demo-skill/scripts/silent_c.py": SILENT_WORK,
        })
        violations, examined, stopped, _ = vsg.scan(root, Args(max_hangs=2))
        assert len(violations) == 2 and stopped and examined == 2

    def test_scan_counts_unexercised_separately_from_violations(self, tmp_path):
        """A dependency crash is reported, never silently read as a pass."""
        root = write_skill_scripts(tmp_path, {
            "demo-skill/scripts/dies_on_import.py": DIES_ON_IMPORT,
            "demo-skill/scripts/catches_dep.py": CATCHES_MISSING_DEP,
            "demo-skill/scripts/silent_work.py": SILENT_WORK,
        })
        violations, examined, _, unexercised = vsg.scan(root, Args(timeout=5.0))
        assert examined == 3
        assert {v["file"] for v in violations} == {"demo-skill/scripts/silent_work.py"}
        assert [(u["file"], u["module"]) for u in unexercised] == [
            ("demo-skill/scripts/dies_on_import.py",
             "this_module_does_not_exist_anywhere")]

    def test_banner_then_exit_is_still_a_violation(self, tmp_path):
        """The fix must not turn language_explorer.py's old bare behaviour into a pass."""
        root = write_skill_scripts(tmp_path, {
            "demo-skill/scripts/banner.py": BANNER_THEN_EXIT,
        })
        violations, _, _, unexercised = vsg.scan(root, Args(timeout=5.0))
        assert unexercised == []
        assert {v["file"] for v in violations} == {"demo-skill/scripts/banner.py"}


class TestSandbox:
    """The scripts under test are not trusted: writes and network are contained."""

    def test_writes_land_in_the_throwaway_copy(self, tmp_path):
        root = write_skill_scripts(tmp_path, {
            "demo-skill/scripts/writer.py": WRITES_A_FILE,
        })
        vsg.scan(root, Args())
        assert not (root / "demo-skill" / "scripts" / "side-effect.txt").exists(), \
            "a scanned script wrote into the real tree"

    def test_network_is_blocked(self, tmp_path):
        """Sockets are blocked by the injected sitecustomize, not merely unrouted."""
        root = write_skill_scripts(tmp_path, {
            "demo-skill/scripts/probe.py": NETWORK_PROBE,
        })
        work, harness = vsg.build_sandbox(root)
        try:
            env = vsg.sandbox_env(harness)
            probe = work / "skills" / "demo-skill" / "scripts" / "probe.py"
            _, out, hung = vsg.run_bare([sys.executable, str(probe)], work, env, 10)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        assert not hung
        assert "NETWORK BLOCKED" in out, f"socket was not blocked; got: {out!r}"

    def test_proxy_env_points_somewhere_unroutable(self, tmp_path):
        """Shell scripts calling curl/wget must fail fast, not reach the network."""
        env = vsg.sandbox_env(tmp_path)
        for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            assert "127.0.0.1:9" in env[var]
        assert "NO_PROXY" not in env and "no_proxy" not in env

    def test_env_scrub_removes_caller_specific_config(self, tmp_path, monkeypatch):
        """TOOLFORGE_*/WIKIMEDIA_*/WMF_*/SSH_* must not reach the scripts under test."""
        monkeypatch.setenv("TOOLFORGE_USER", "someone")
        monkeypatch.setenv("TOOLFORGE_DB_PORT", "3307")
        monkeypatch.setenv("WIKIMEDIA_USER_AGENT", "X/1.0 (https://example.org)")
        monkeypatch.setenv("WMCLOUD_TOKEN", "secret")
        monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
        env = vsg.sandbox_env(tmp_path)
        for var in ("TOOLFORGE_USER", "TOOLFORGE_DB_PORT", "WIKIMEDIA_USER_AGENT",
                    "WMCLOUD_TOKEN", "SSH_AUTH_SOCK"):
            assert var not in env, f"{var} leaked into the sandbox"

    def test_unrelated_env_is_preserved(self, tmp_path, monkeypatch):
        """Scrubbing must not strip everything — PATH/HOME are needed to run scripts."""
        monkeypatch.setenv("MY_OWN_SETTING", "keep-me")
        env = vsg.sandbox_env(tmp_path)
        assert env.get("MY_OWN_SETTING") == "keep-me"
        assert "PATH" in env

    def test_verdict_is_invariant_to_the_callers_environment(self, tmp_path, monkeypatch):
        """The same script must get the same verdict on CI and on a configured box.

        Before the scrub, `setup-tunnel.sh` was flagged on a machine with
        $TOOLFORGE_USER set and passed on CI, because that variable was its only
        early exit. This asserts the two runs now agree.
        """
        root = write_skill_scripts(tmp_path, {
            "demo-skill/scripts/env_dependent.sh": ENV_DEPENDENT_SH,
        })
        monkeypatch.setenv("TOOLFORGE_USER", "someone")
        configured = vsg.scan(root, Args())
        monkeypatch.delenv("TOOLFORGE_USER", raising=False)
        bare_ci = vsg.scan(root, Args())
        assert [v["file"] for v in configured[0]] == [v["file"] for v in bare_ci[0]]
        assert configured[1] == bare_ci[1] == 1

    def test_env_gated_guard_is_not_detected__known_limitation(self, tmp_path, monkeypatch):
        """Documented limit: a guard gated on a *missing* env var is not caught.

        Scrubbing makes the verdict reproducible, it does not make it diagnostic: the
        scrubbed env makes an env-gated script take its early exit, so it looks
        guarded. `setup-tunnel.sh` therefore needed a real arity guard (see
        test_setup_tunnel_refuses_a_bare_invocation), not just this scrub.
        """
        monkeypatch.setenv("TOOLFORGE_USER", "someone")
        root = write_skill_scripts(tmp_path, {
            "demo-skill/scripts/env_dependent.sh": ENV_DEPENDENT_SH,
        })
        violations, examined, _, _ = vsg.scan(root, Args())
        assert examined == 1
        assert violations == [], (
            "if this now FAILS, the verifier has gained env-gated detection — "
            "update the docs and this limitation note rather than reverting it")


class TestExemptions:
    """EXEMPT is how bare invocation is declared intentional — with a reason."""

    def test_every_exemption_states_why(self):
        for name, reason in vsg.EXEMPT.items():
            assert reason.strip(), f"{name} is exempt with no reason"
            assert len(reason) > 30, f"{name} needs a real reason, not a stub"

    def test_every_exempt_file_exists(self):
        for name in vsg.EXEMPT:
            assert (SKILLS_DIR / name).is_file(), f"{name} is exempt but does not exist"

    def test_exemptions_are_script_paths(self):
        for name in vsg.EXEMPT:
            assert name.endswith((".py", ".sh")), f"{name} is not a script"


class TestRealTree:
    """The shipped tree must be clean — this is what CI enforces."""

    def test_scope_covers_the_scripts_the_readme_advertises(self):
        """Guard against a scope that quietly stops looking at anything."""
        count = sum(1 for p in SKILLS_DIR.rglob("*")
                    if p.is_file() and p.suffix in (".py", ".sh"))
        assert count > 100, f"only {count} scripts in scope; scope is too narrow"

    def test_count_maintainers_is_guarded(self):
        """The regression that motivated this verifier: it must stay refused."""
        script = SKILLS_DIR / "wikimedia-toolforge" / "scripts" / "count-maintainers.py"
        violations, examined, _, _ = vsg.scan(SKILLS_DIR, Args(files=[script], timeout=10))
        assert examined == 1
        assert violations == [], "count-maintainers.py lost its zero-argument guard"

    def test_language_explorer_never_silently_succeeds_when_bare(self):
        """Fixed 2026-10-09: bare + no TTY used to print a banner and exit 0."""
        script = (SKILLS_DIR / "wikimedia-ml-services" / "assets" / "language_explorer.py")
        violations, examined, _, _ = vsg.scan(SKILLS_DIR, Args(files=[script], timeout=10))
        assert examined == 1
        assert violations == [], (
            "language_explorer.py must refuse a bare, non-interactive invocation "
            "(it may also be 'not exercised' when requests is absent)")

    def test_api_client_never_fires_a_live_probe_when_bare(self):
        """Fixed 2026-10-09: --endpoint defaulted to enwiki, so bare = live request."""
        script = (SKILLS_DIR / "wikipedia-error-handling" / "assets" / "api_client.py")
        violations, examined, _, _ = vsg.scan(SKILLS_DIR, Args(files=[script], timeout=10))
        assert examined == 1
        assert violations == [], "api_client.py must require --endpoint"

    def test_setup_tunnel_refuses_a_bare_invocation(self):
        """Fixed 2026-10-05: its only early exit was $TOOLFORGE_USER being unset, so on
        a configured machine a bare run opened a real SSH tunnel (exit 0) while CI,
        where the variable is absent, saw exit 1 and passed."""
        script = SKILLS_DIR / "wikimedia-database" / "scripts" / "setup-tunnel.sh"
        violations, examined, _, _ = vsg.scan(SKILLS_DIR, Args(files=[script], timeout=10))
        assert examined == 1
        assert violations == [], (
            "setup-tunnel.sh must refuse a bare invocation; a missing $TOOLFORGE_USER "
            "is not a guard because it is set on every configured machine")
