"""The knowledgebase guard: does it actually keep the copy it promises?

`tools/kb-guard.sh` exists because the documented update path is "extract the new
zip over your install", which REPLACES every file the toolkit ships -- including
two the user is told to edit. v3.8.4 moved future accumulation out of the shipped
file; this covers the install that already has notes in it, by taking a copy at
SessionStart before anything can overwrite it.

Every test here asserts on the STORE -- what is on disk afterwards -- not on the
text the tool printed. The report is cosmetic; the copy is the contract. The one
exception is the alarm, where the text IS the deliverable, and there the assertion
is that it names a real, readable file the user can diff against.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import BASH, REPO

TOOL = REPO / "tools" / "kb-guard.sh"
HOOK = REPO / ".claude" / "hooks" / "session-kb-guard.sh"
KB = "KNOWLEDGEBASE.md"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def make_install(tmp_path: Path, name: str = "install with space") -> Path:
    """A minimal install. The default name carries a SPACE on purpose: this
    project's real root is `C:/GOG Games/The Elder Scrolls V Skyrim VR`, and a
    guard that only works on space-free paths would be inert for its author."""
    root = tmp_path / name
    (root / "tools").mkdir(parents=True)
    shutil.copy2(TOOL, root / "tools" / "kb-guard.sh")
    return root


def run_guard(root: Path, extra_path: Path | None = None, keep: int | None = None):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    if keep is not None:
        env["KB_GUARD_KEEP"] = str(keep)
    if extra_path:
        env["PATH"] = f"{extra_path}{os.pathsep}{env['PATH']}"
    return subprocess.run(
        [BASH, str(root / "tools" / "kb-guard.sh")],
        capture_output=True, text=True, env=env, timeout=120,
    )


def store(root: Path) -> Path:
    return root / ".claude" / "backups" / "kb"


def stored_copies(root: Path, name: str = KB) -> list[Path]:
    return sorted(store(root).glob(f"*/{name}"))


def write(root: Path, name: str, body: str) -> None:
    (root / name).write_text(body, encoding="utf-8", newline="\n")


# --------------------------------------------------------------------------
# the copy itself
# --------------------------------------------------------------------------

def test_the_first_run_copies_the_knowledgebase_and_a_second_does_not_duplicate_it(tmp_path):
    root = make_install(tmp_path)
    write(root, KB, "notes\n" * 200)

    first = run_guard(root)
    assert first.returncode == 0, first.stdout + first.stderr
    copies = stored_copies(root)
    assert len(copies) == 1, f"expected one stored copy, got {copies}"
    assert copies[0].read_text(encoding="utf-8") == (root / KB).read_text(encoding="utf-8")

    second = run_guard(root)
    assert second.returncode == 0, second.stdout + second.stderr
    assert len(stored_copies(root)) == 1, (
        "an unchanged file was copied again; every session would add a directory "
        "and the rotation would then walk real history off the end")


def test_a_changed_knowledgebase_is_copied_again(tmp_path):
    root = make_install(tmp_path)
    write(root, KB, "one\n")
    run_guard(root)
    write(root, KB, "one\ntwo\n")
    r = run_guard(root)
    assert r.returncode == 0, r.stdout + r.stderr
    bodies = {p.read_text(encoding="utf-8") for p in stored_copies(root)}
    assert bodies == {"one\n", "one\ntwo\n"}, bodies


def test_an_install_with_nothing_to_watch_says_NOT_RUN_rather_than_nothing(tmp_path):
    """Silence is the failure mode this whole toolkit keeps re-learning: a guard
    that examined nothing must not be indistinguishable from one that found
    nothing wrong. Exit 2 and the words NOT RUN, never a clean line."""
    root = make_install(tmp_path)
    r = run_guard(root)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "NOT RUN" in (r.stdout + r.stderr)


# --------------------------------------------------------------------------
# the alarm
# --------------------------------------------------------------------------

def test_a_file_that_loses_half_its_bytes_raises_an_alarm_naming_a_readable_copy(tmp_path):
    """What an extract-over looks like from the inside. The alarm is only useful
    if the path it prints can actually be opened and still holds the old text --
    so that is what is asserted, not the wording."""
    root = make_install(tmp_path)
    big = "a real accumulated knowledgebase\n" * 400
    write(root, KB, big)
    run_guard(root)

    write(root, KB, "# Knowledgebase\n")           # the shipped stub, as an update leaves it
    r = run_guard(root)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "ALARM" in r.stdout

    named = [ln.split(":", 1)[1].strip() for ln in r.stdout.splitlines()
             if "previous copy:" in ln]
    assert named, f"the alarm named no previous copy:\n{r.stdout}"
    recovered = Path(named[0])
    assert recovered.is_file(), f"the alarm names a path that does not exist: {recovered}"
    assert recovered.read_text(encoding="utf-8") == big, (
        "the alarm points at a copy that is not the user's lost content")


def test_a_deleted_file_alarms_and_points_at_the_largest_copy_not_the_newest(tmp_path):
    """If a loss goes unnoticed for a session, the NEWEST stored copy is the
    damaged one. Handing a user that back and calling it a backup is worse than
    saying nothing, so the alarm names the biggest copy as well."""
    root = make_install(tmp_path)
    big = "x" * 4000
    write(root, KB, big)
    run_guard(root)
    write(root, KB, "x" * 50)                      # session 1: the loss, alarms
    run_guard(root)
    (root / KB).unlink()                           # session 2: gone entirely
    r = run_guard(root)

    assert r.returncode == 1, r.stdout + r.stderr
    assert "GONE" in r.stdout
    largest = [ln.split(":", 1)[1].strip() for ln in r.stdout.splitlines()
               if "LARGEST stored copy:" in ln]
    assert largest, f"no largest copy named:\n{r.stdout}"
    path = Path(largest[0].rsplit(" (", 1)[0])
    assert path.read_text(encoding="utf-8") == big


def test_the_hook_never_fails_the_session_even_when_the_guard_alarms(tmp_path):
    """SessionStart output is injected into Claude's context and a non-zero exit
    is visible to the user. The guard is allowed to be loud; it is not allowed to
    turn a lost knowledgebase into a session that will not start."""
    jq = shutil.which("jq")
    if not jq:
        pytest.skip("jq not installed")
    root = make_install(tmp_path)
    hooks = root / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    text = HOOK.read_text(encoding="utf-8").replace("{{JQ_PATH}}", jq.replace(chr(92), "/"))
    (hooks / "session-kb-guard.sh").write_text(text, encoding="utf-8", newline="\n")

    write(root, KB, "y" * 4000)
    run_guard(root)
    write(root, KB, "y" * 10)

    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    r = subprocess.run(
        [BASH, str(hooks / "session-kb-guard.sh")],
        input=json.dumps({"hook_event_name": "SessionStart", "source": "startup"}),
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert r.returncode == 0, f"the hook failed the session: {r.stdout}{r.stderr}"
    assert "ALARM" in r.stdout, "the hook swallowed the guard's alarm"

    hb = root / ".claude" / "backups" / ".hook-heartbeat" / "session-kb-guard"
    assert hb.is_file(), "no heartbeat written, so hook-canary.sh cannot see this hook"
    assert "payload_bytes=0" not in hb.read_text(encoding="utf-8")

    # `compact` is the same session continuing. Re-running would spend context
    # re-announcing a snapshot that was taken at its start.
    r2 = subprocess.run(
        [BASH, str(hooks / "session-kb-guard.sh")],
        input=json.dumps({"hook_event_name": "SessionStart", "source": "compact"}),
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert r2.returncode == 0
    assert r2.stdout.strip() == "", f"compact should be quiet, printed: {r2.stdout!r}"


def test_the_hook_says_so_when_the_tool_it_delegates_to_is_missing(tmp_path):
    """A thin hook shipped without its tool must report NOT RUN. The failure it
    must never produce is the quiet one: a hook that fires, does nothing, and
    leaves the session looking checked."""
    jq = shutil.which("jq")
    if not jq:
        pytest.skip("jq not installed")
    root = make_install(tmp_path)
    (root / "tools" / "kb-guard.sh").unlink()
    hooks = root / ".claude" / "hooks"
    hooks.mkdir(parents=True)
    text = HOOK.read_text(encoding="utf-8").replace("{{JQ_PATH}}", jq.replace(chr(92), "/"))
    (hooks / "session-kb-guard.sh").write_text(text, encoding="utf-8", newline="\n")

    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    r = subprocess.run(
        [BASH, str(hooks / "session-kb-guard.sh")],
        input=json.dumps({"hook_event_name": "SessionStart", "source": "startup"}),
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert r.returncode == 0
    assert "NOT RUN" in r.stdout


# --------------------------------------------------------------------------
# the store must not eat its own good copy
# --------------------------------------------------------------------------

def test_rotation_never_prunes_the_largest_copy(tmp_path):
    """The rotation runs in every session AFTER a loss too, and each of those
    sessions snapshots the truncated file. A plain oldest-first prune therefore
    walks the one full-size copy off the end a few sessions later -- protection
    that destroys the thing it protects, while still looking like a backup store.

    The big copy is deliberately NOT the oldest one. The first version of this
    test wrote it first, so the "never prune the oldest" rule kept it and the
    mutation gate reported that the largest-copy rule could be deleted with
    nothing going red. Two rules, one test, and only the first was being tested.
    """
    root = make_install(tmp_path)
    write(root, KB, "the install's first, small knowledgebase\n")
    run_guard(root, keep=3)
    big = "z" * 9000
    write(root, KB, big)                            # months of accumulation
    run_guard(root, keep=3)
    for i in range(1, 12):                          # eleven sessions of the damaged file
        write(root, KB, "z" * (10 + i))
        run_guard(root, keep=3)

    dirs = sorted(p for p in store(root).iterdir() if p.is_dir())
    assert len(dirs) <= 3 + 2, f"rotation kept far more than asked: {len(dirs)}"
    bodies = [p.read_text(encoding="utf-8") for p in stored_copies(root)]
    assert big in bodies, (
        "the largest stored copy was pruned; after an unnoticed loss the store "
        "would hold nothing but truncated files")


def test_rotation_never_prunes_the_oldest_copy(tmp_path):
    """The other clause, and it needs its own twin: here the file only ever GROWS,
    so the largest copy is always the newest and the largest-copy rule protects
    nothing. What is worth keeping is the state the install started in -- the one
    copy predating whatever went wrong."""
    root = make_install(tmp_path)
    original = "the state this install started in\n"
    write(root, KB, original)
    run_guard(root, keep=3)
    for i in range(1, 12):
        write(root, KB, original + ("more\n" * i))
        run_guard(root, keep=3)

    bodies = [p.read_text(encoding="utf-8") for p in stored_copies(root)]
    assert original in bodies, (
        "the oldest stored copy was pruned, so the store no longer holds any "
        "state from before the change the user wants to undo")


def test_two_runs_in_the_same_second_do_not_overwrite_each_other(tmp_path):
    """MEASURED while building this: the snapshot directory is timestamped to the
    second, so a second run inside that second wrote into the SAME directory and
    `cp` overwrote the first copy. A probe that emptied a 9000-byte file one
    second after snapshotting it left a 0-byte copy where the good one had been
    -- the tool committing the exact loss it exists to prevent.

    `date` is shimmed to a constant so the collision is forced rather than raced;
    without the shim this test would pass on a fast machine for the wrong reason.
    """
    root = make_install(tmp_path)
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "date").write_text(
        "#!/bin/sh\necho 20260101_000000\n", encoding="utf-8", newline="\n")
    (shim / "date").chmod(0o755)

    big = "keep me\n" * 500
    write(root, KB, big)
    run_guard(root, extra_path=shim)
    write(root, KB, "")                             # emptied one instant later
    run_guard(root, extra_path=shim)

    bodies = [p.read_text(encoding="utf-8") for p in stored_copies(root)]
    assert big in bodies, (
        "the second snapshot overwrote the first because both used the same "
        f"timestamped directory; store now holds only {[len(b) for b in bodies]}")
