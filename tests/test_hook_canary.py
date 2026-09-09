"""The hook canary: can it ever go back to green?

`tools/hook-canary.sh` exists because a hook can be alive, be invoked, and still
see nothing -- the inert-hook defect, which no test suite could catch because a
suite pipes stdin. The canary reads heartbeats written from inside REAL
invocations.

MEASURED 2026-09-09: it branched on the mere EXISTENCE of a `.blind` marker and
never compared it against the good heartbeat, so a single legitimate empty-payload
invocation pinned it BLIND permanently. Two markers left by a verification harness
held `protect-bash` and `protect-files` red while their good heartbeats were 107
SECONDS newer and the hooks were demonstrably receiving 900+ byte payloads.

That is the INVERSE of the failure the tool was built for, and it is not benign: an
instrument stuck red gets ignored, which is the same end state as no instrument.

Assertions here are on the VERDICT and the exit code, which are the contract. The
two directions are tested separately because they fail independently -- a fix that
clears a stale marker is worthless if it also swallows a real one.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conftest import BASH, REPO

TOOL = REPO / "tools" / "hook-canary.sh"
HOOK = "protect-bash"


def _run(tmp_path: Path, blind_ts, good_ts):
    """Build a heartbeat store and run the real canary against it."""
    hb = tmp_path / ".claude" / "backups" / ".hook-heartbeat"
    hb.mkdir(parents=True)
    (tmp_path / ".claude" / "hooks").mkdir(parents=True)  # canary refuses without it
    if blind_ts is not None:
        (hb / (HOOK + ".blind")).write_text("%s NO PAYLOAD\n" % blind_ts, encoding="utf-8")
    if good_ts is not None:
        (hb / HOOK).write_text("%s payload_bytes=908\n" % good_ts, encoding="utf-8")
    env = {
        "CLAUDE_PROJECT_DIR": str(tmp_path).replace("\\", "/"),
        "HOOK_CANARY_STALE_MIN": "999999",  # keep age out of the verdict
        "PATH": subprocess.os.environ.get("PATH", ""),
        "SYSTEMROOT": subprocess.os.environ.get("SYSTEMROOT", ""),
    }
    p = subprocess.run([BASH, str(TOOL)], capture_output=True, text=True, env=env, timeout=120)
    line = next((l for l in p.stdout.splitlines() if HOOK in l), "")
    return p.returncode, line


@pytest.mark.parametrize(
    "label,blind_ts,good_ts,expect",
    [
        ("blind only, never recovered", "20260909_150000", None, "BLIND"),
        ("blind is the NEWEST event", "20260909_150500", "20260909_150000", "BLIND"),
        # A truncated marker must fail LOUD. Treating an unparseable timestamp as
        # "no marker" would silently disarm the one signal this tool exists to give.
        ("blind marker is unparseable", "", "20260909_150500", "BLIND"),
    ],
)
def test_a_real_blind_event_is_reported(tmp_path, label, blind_ts, good_ts, expect):
    rc, line = _run(tmp_path, blind_ts, good_ts)
    assert expect in line, "%s: expected %s, got %r" % (label, expect, line)
    assert rc == 1, "%s: a blind hook must exit 1, got %s" % (label, rc)


def test_a_hook_that_recovered_reads_green_again(tmp_path):
    """The regression this file was added for: a good payload AFTER a blind event
    means the hook is working now, and the canary must say so -- while still
    surfacing that the blind event happened."""
    rc, line = _run(tmp_path, "20260909_150000", "20260909_150500")
    assert "ALIVE" in line, "a recovered hook must not read BLIND: %r" % line
    assert rc == 0, "a recovered hook must exit 0, got %s" % rc
    assert "recovered" in line, "the earlier blind event must still be surfaced: %r" % line


def test_a_hook_with_no_blind_history_is_plain_alive(tmp_path):
    rc, line = _run(tmp_path, None, "20260909_150000")
    assert "ALIVE" in line and "recovered" not in line, line
    assert rc == 0
