"""The hook canary: can it ever go back to green, and can it ever go green wrongly?

`tools/hook-canary.sh` exists because a hook can be alive, be invoked, and still see
nothing -- the inert-hook defect, which no test suite could catch because a suite pipes
stdin. The canary reads heartbeats written from inside REAL invocations.

MEASURED 2026-09-09, in two rounds, both of them regressions in the fix for the round
before:

1. It branched on the mere EXISTENCE of a `.blind` marker and never compared it against
   the good heartbeat, so one legitimate empty-payload invocation pinned it BLIND
   permanently -- with good heartbeats 107 SECONDS newer and the hooks demonstrably
   receiving 900+ byte payloads. An instrument stuck red gets ignored, which is the
   same end state as no instrument.
2. The fix for (1) then cleared a marker too eagerly: on EQUAL timestamps, and on a
   truncated marker like `20260909_15` that sorts BELOW a real stamp. Both read green.

So the contract is narrow and tested in both directions: a blind marker is CURRENT
unless a **well-formed** marker is followed by a **strictly newer** good heartbeat.
Anything less is not proof of recovery.

A recovered hook gets its own `RECOVERED` status rather than being folded into ALIVE,
because for `backup-before-edit.sh` and `snapshot-before-tool.sh` -- which exit 0
silently on an empty payload -- this report is the only place intermittent starvation
could ever surface. Its exit code stays 0: the hook IS working now, and saying
otherwise would be the false positive that gets the tool ignored.

⚠ Clock skew is out of reach by construction: no pair of timestamps can reveal a
non-monotonic clock. That risk is inherent, not a defect, and is not tested here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conftest import BASH, REPO

TOOL = REPO / "tools" / "hook-canary.sh"
HOOK = "protect-bash"


def _run(tmp_path: Path, blind_ts, good_ts):
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


# A RECOVERED line contains the substring "WAS BLIND", so order matters when
# classifying. Getting this backwards made a passing fix look broken.
def _status(line):
    for word in ("RECOVERED", "BLIND", "ALIVE"):
        if word in line:
            return word
    return "?"


@pytest.mark.parametrize(
    "label,blind_ts,good_ts",
    [
        ("no good heartbeat at all", "20260909_150000", None),
        ("blind is the newest event", "20260909_150500", "20260909_150000"),
        # Round-2 regressions. Each of these read green before being tightened.
        ("timestamps are EQUAL", "20260909_150000", "20260909_150000"),
        ("marker is empty", "", "20260909_150500"),
        ("marker is truncated and sorts low", "20260909_15", "20260909_150500"),
        ("marker is not a timestamp", "NOPAYLOAD", "20260909_150500"),
    ],
)
def test_an_unproven_recovery_stays_blind(tmp_path, label, blind_ts, good_ts):
    """Only a well-formed marker followed by a STRICTLY newer good heartbeat is proof."""
    rc, line = _run(tmp_path, blind_ts, good_ts)
    assert _status(line) == "BLIND", "%s: expected BLIND, got %r" % (label, line)
    assert rc == 1, "%s: a blind hook must exit 1, got %s" % (label, rc)


def test_a_proven_recovery_reads_recovered_not_alive(tmp_path):
    """The regression this file was added for -- but it must not read as plain green
    either, or intermittent starvation has nowhere to show up."""
    rc, line = _run(tmp_path, "20260909_150000", "20260909_150500")
    assert _status(line) == "RECOVERED", "a recovered hook must be distinguishable: %r" % line
    assert "WAS BLIND" in line, "the earlier blind event must still be named: %r" % line
    assert rc == 0, "a hook that is working now must exit 0, got %s" % rc


def test_a_hook_with_no_blind_history_is_plain_alive(tmp_path):
    rc, line = _run(tmp_path, None, "20260909_150000")
    assert _status(line) == "ALIVE", line
    assert "WAS BLIND" not in line
    assert rc == 0


def test_the_summary_counts_recovered_separately(tmp_path):
    """A recovered hook folded into the alive count is the same information loss as
    folding it into the ALIVE line."""
    hb = tmp_path / ".claude" / "backups" / ".hook-heartbeat"
    hb.mkdir(parents=True)
    (tmp_path / ".claude" / "hooks").mkdir(parents=True)
    (hb / (HOOK + ".blind")).write_text("20260909_150000 NO PAYLOAD\n", encoding="utf-8")
    (hb / HOOK).write_text("20260909_150500 payload_bytes=908\n", encoding="utf-8")
    env = {
        "CLAUDE_PROJECT_DIR": str(tmp_path).replace("\\", "/"),
        "HOOK_CANARY_STALE_MIN": "999999",
        "PATH": subprocess.os.environ.get("PATH", ""),
        "SYSTEMROOT": subprocess.os.environ.get("SYSTEMROOT", ""),
    }
    p = subprocess.run([BASH, str(TOOL)], capture_output=True, text=True, env=env, timeout=120)
    summary = next((l for l in p.stdout.splitlines() if l.startswith("alive:")), "")
    assert "recovered-after-blind: 1" in summary, summary
    assert "alive: 0" in summary, "a recovered hook must not also count as alive: %s" % summary
    # and it must not fall through to "no hook has recorded a payload yet"
    assert "UNPROVEN" not in p.stdout
