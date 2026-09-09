"""The safety hooks, exercised as processes with a real payload on stdin.

WHY THIS FILE EXISTS, AND WHAT IT STILL CANNOT SEE

Every hook in this toolkit was INERT for months. They fired on every call, read zero
bytes, fell through their first guard and exited 0 -- which is byte-identical to
deciding "this is fine". The audit log recorded an empty command field in 2,454 of
2,454 entries and nothing looked wrong.

The cause was `INPUT=$(cat /dev/stdin)`, which returns ZERO BYTES in the Claude Code
hook environment while a bare `cat` returns the payload (MEASURED: X4 toolkit
2026-08-29, reproduced on a second machine 2026-09-06 -- seven consecutive probes,
0 bytes via /dev/stdin, 641-2840 via bare cat).

**This file DOES catch that defect -- but only on Windows.** `/dev/stdin` is a
symlink to `/proc/self/fd/0`: it resolves when fd 0 is a real file or a pipe made
by an MSYS shell, and fails when fd 0 is a Win32 pipe from a non-MSYS parent.
Python's `subprocess` hands bash a Win32 pipe, exactly as Claude Code's Node
process does -- so reinstating `cat /dev/stdin` here gives **18 failed, 16 passed**
(measured). On Linux `/dev/stdin` resolves for any pipe and every case below
passes with the defect in place.

So this suite is a real detector on one platform and blind on the other, and the
honest reason the defect survived in the first place is neither: **nothing ran the
hooks as processes at all.** Two platform-independent checks carry what this
cannot:

  * `tests/test_repo_invariants.py` asserts no shipped hook USES `$(cat /dev/stdin)`.
    That is a property of the text, so it holds on every platform and in CI's Linux
    leg, where the behavioural check above is blind.
  * `tools/hook-canary.sh` reads heartbeats written from inside REAL invocations,
    which is the only evidence that survives the harness/production gap.

Every deny below has a control beside it that must NOT fire. A rule that fires on
ordinary work gets worked around, and then it protects nothing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import REPO

HOOKS = REPO / ".claude" / "hooks"
BS = chr(92)

#: The install these tests talk about. Defined HERE, above the fixture, because the
#: fixture now has to declare it in `.claude/skyrim-paths.env`: protect-bash.sh resolves
#: the real install root instead of matching the word "Skyrim" anywhere in a path, so a
#: row naming some third directory would describe a place the hook has no reason to
#: guard. One constant, used by the fixture and by every row, so the two cannot disagree.
GAME = "C:/GOG Games/The Elder Scrolls V Skyrim VR"
GAMEW = GAME.replace("/", BS)


def _bash() -> str:
    """Git Bash by path, never `bash` from PATH.

    On Windows `bash` resolves to the WSL launcher, which cannot see these scripts.
    It exits without running anything, and a harness that reads empty output as
    "allowed" then reports every case as passing -- a whole matrix of green that
    measured nothing. This has happened here.
    """
    for c in (r"C:\Program Files\Git\bin\bash.exe",
              r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if Path(c).is_file():
            return c
    found = shutil.which("bash")
    if found and "System32" not in found:
        return found
    pytest.skip("no non-WSL bash available")


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    """A throwaway install with the hooks configured, as setup.sh would leave them."""
    jq = shutil.which("jq")
    if not jq:
        pytest.skip("jq not installed")
    root = tmp_path_factory.mktemp("install") / "Skyrim VR"
    (root / ".claude" / "hooks").mkdir(parents=True)
    (root / "Data" / "Scripts" / "Source").mkdir(parents=True)
    for f in HOOKS.glob("*.sh"):
        text = f.read_text(encoding="utf-8").replace("{{JQ_PATH}}", jq.replace(BS, "/"))
        (root / ".claude" / "hooks" / f.name).write_text(text, encoding="utf-8", newline="\n")
    # DECLARE THE INSTALL THE ASSERTIONS TALK ABOUT.
    #
    # protect-bash.sh no longer decides "is this the game" by looking for the word
    # Skyrim in a path -- that matched the session scratchpad, this repo's own
    # checkout, and C:/Temp/skyrim-notes.txt. It resolves the REAL install root from
    # the hook's own location, plus whatever setup.sh recorded here.
    #
    # In this fixture the hook's location is a pytest tmp dir, so without this file the
    # rows below -- which assert about `C:/GOG Games/...` -- would describe a directory
    # the hook has no reason to guard, and every deletion row would (correctly) stop
    # denying. Writing it also means the configured-paths arm is exercised rather than
    # assumed; INSTALL_ROOT alone would never test it.
    # Only the GAME root is declared. The config directory keeps a generic literal
    # rule in the hook (`Documents/My Games/Skyrim`), because it lives OUTSIDE any
    # install root and its INIs are not recoverable from a mod manager -- so it must
    # be guarded even on a machine setup.sh has never configured.
    (root / ".claude" / "skyrim-paths.env").write_text(
        f'SKYRIM_GAME_ROOT="{GAME}"\n', encoding="utf-8", newline="\n",
    )
    return root


def fire(project, hook, payload):
    """Run a hook the way Claude Code does and classify its decision.

    `payload=None` sends genuinely EMPTY stdin -- the condition the defect produced.
    That is NOT the same as a well-formed payload with a field missing, and the two
    are tested separately.
    """
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(project))
    r = subprocess.run(
        [_bash(), str(project / ".claude" / "hooks" / hook)],
        input="" if payload is None else json.dumps(payload),
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert r.returncode == 0, f"{hook} exited {r.returncode}: {r.stderr[:300]}"
    out = r.stdout.strip()
    if not out:
        return "allow", ""
    j = json.loads(out)
    h = j.get("hookSpecificOutput", {})
    if "permissionDecision" in h:
        return h["permissionDecision"], h.get("permissionDecisionReason", "")
    if "additionalContext" in h:
        return "advise", h["additionalContext"]
    return "allow", out


def cmd(c):
    return {"tool_name": "Bash", "tool_input": {"command": c}}


def fpath(p):
    return {"tool_name": "Write", "tool_input": {"file_path": p}}


# --------------------------------------------------------------------------
# protect-files
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path,expected,why", [
    ("C:/Games/Skyrim VR/Data/MyMod.esp", "deny", "a text write into a binary plugin"),
    ("C:" + BS + "Games" + BS + "Skyrim VR" + BS + "Data" + BS + "M.esp", "deny", "same, backslashes"),
    ("/c/games/skyrim/Data/x.BSA", "deny", "archives too, and case-insensitively"),
    # Controls. Each must NOT deny.
    ("C:/Games/Skyrim VR/Data/Textures/x.dds", "advise", "an ordinary asset is a note"),
    ("C:/Games/Skyrim VR/.claude/hooks/x.sh", "allow", "our own workspace is silent"),
    ("C:/work/notes.md", "allow", "nothing to do with the install"),
    ("C:/Users/x/Documents/My Games/Skyrim VR/SkyrimVR.ini", "advise", "config is consequential"),
    ("C:/Games/Skyrim VR/Data/Scripts/Source/A.psc", "advise", "a .pex loads at startup only"),
])
def test_protect_files_decides(project, path, expected, why):
    got, _ = fire(project, "protect-files.sh", fpath(path))
    assert got == expected, f"{why}: {path}"


# --------------------------------------------------------------------------
# protect-bash
# --------------------------------------------------------------------------

@pytest.mark.parametrize("command,expected,why", [
    (f'rm -rf "{GAME}"', "deny", "deleting the install"),
    (f'rm -rf "{GAMEW}"', "deny",
     "the backslash form -- cmd.exe and powershell are both callable, so this is "
     "the spelling that got through a forward-slash-only guard"),
    (f'rm -rf "/c/{GAME[3:]}"', "deny", "the MSYS form"),
    ('rm -rf "C:/Users/x/Documents/My Games/Skyrim VR"', "deny", "the config directory"),
    ('reg delete "HKLM' + BS + 'SOFTWARE' + BS + 'Bethesda"', "deny", "registry keys"),
    (f'Champollion.exe "{GAME}/Data/Scripts/A.pex"', "deny",
     "Champollion writes to Data/Scripts/Source regardless of flags and can leave "
     "the output empty; this has destroyed a .psc twice. Scoped to the LIVE install: "
     "a .pex copied to a temp dir -- the remedy this refusal prescribes -- must not "
     "itself be refused"),
    ('tool --output "C:/tmp/A.psc"', "deny", "output aimed straight at a source file"),
    # Controls. A guard that fires on these is one that gets deleted.
    ("ls -la", "allow", "plain ls"),
    ("git status", "allow", "git status"),
    ("rm -rf /tmp/scratch", "allow", "an rm outside the install"),
    ('python -c "print(1)"', "allow", "ordinary work"),
    ('Champollion.exe "C:/tmp/A.pex" -p C:/tmp', "allow", "Champollion somewhere safe"),
    ("rm mydata/cache.bin", "allow", "the word boundary: mydata is not Data"),
    # Advisories.
    ('cat "C:/Games/Skyrim VR/Data/x.esp"', "advise", "reading a plugin is fine"),
    ("rm Data/Textures/x.dds", "advise", "a relative delete inside Data"),
    ("rm ./Data/x.dds", "advise", "dot-relative too"),
    ("cat loadorder.txt", "advise", "the mod manager owns load order"),
    # The single ask -- genuinely the user's decision.
    ("bash tools/resaver-cli.sh reset-havok save.ess --apply", "ask",
     "--apply MUTATES a save through ReSaver's write path"),
    ("bash tools/resaver-cli.sh reset-havok save.ess", "allow", "the dry-run does not"),
])
def test_protect_bash_decides(project, command, expected, why):
    got, _ = fire(project, "protect-bash.sh", cmd(command))
    assert got == expected, f"{why}: {command}"


# --------------------------------------------------------------------------
# The defect itself
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["protect-bash.sh", "protect-files.sh"])
def test_a_windows_jq_path_still_produces_parseable_json(tmp_path, name):
    r"""The hook header tells the user to paste the output of `where jq`, which is
    `C:\Users\...\jq.exe`. That went into a JSON string via printf, and `\U` is not
    a valid JSON escape -- so the one refusal deliberately built to survive a broken
    jq was itself unparseable, i.e. an allow. The branch had an off switch shaped
    exactly like the thing it was guarding against."""
    root = tmp_path / "install"
    (root / ".claude" / "hooks").mkdir(parents=True)
    text = (HOOKS / name).read_text(encoding="utf-8").replace(
        "{{JQ_PATH}}", "C:" + BS + "Users" + BS + "me" + BS + "AppData" + BS + "jq.exe")
    (root / ".claude" / "hooks" / name).write_text(text, encoding="utf-8", newline="\n")
    payload = (cmd('rm -rf "C:/x"') if name == "protect-bash.sh"
               else fpath("C:/Games/Skyrim VR/Data/M.esp"))
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    r = subprocess.run([_bash(), str(root / ".claude" / "hooks" / name)],
                       input=json.dumps(payload), capture_output=True, text=True,
                       env=env, timeout=60)
    j = json.loads(r.stdout)          # the assertion IS that this does not raise
    assert j["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize("hook,field", [
    ("protect-files.sh", "file_path"),
    ("protect-bash.sh", "command"),
])
def test_a_blocking_hook_refuses_when_it_cannot_see_its_input(project, hook, field):
    """Silence IS allow. A guard that evaluated nothing must say so, not exit 0 --
    exiting 0 is what made the inert hooks indistinguishable from working ones."""
    tool = "Write" if field == "file_path" else "Bash"
    got, reason = fire(project, hook, {"tool_name": tool, "tool_input": {}})
    assert got == "deny", f"{hook} allowed a call it could not evaluate"
    assert "GUARD INERT" in reason, reason


@pytest.mark.parametrize("hook", ["protect-files.sh", "protect-bash.sh"])
def test_a_blocking_hook_refuses_on_genuinely_empty_stdin(project, hook):
    """The exact shape `cat /dev/stdin` produced on every invocation."""
    got, reason = fire(project, hook, None)
    assert got == "deny", f"{hook} allowed a call after reading nothing"
    assert "GUARD INERT" in reason, reason


@pytest.mark.parametrize("hook", ["backup-before-edit.sh", "snapshot-before-tool.sh"])
def test_a_recording_hook_logs_rather_than_blocks_on_empty_stdin(project, hook):
    """These two must never block -- that is not their job -- but they must not pass
    in silence either, because the silence is what hid the outage."""
    got, _ = fire(project, hook, None)
    assert got == "allow"
    log = project / ".claude" / "backups" / "AUDIT_LOG.txt"
    assert log.is_file(), "nothing was written to the audit log"
    assert "BLIND" in log.read_text(encoding="utf-8", errors="replace")


def test_every_hook_writes_a_liveness_heartbeat(project):
    """`tools/hook-canary.sh` reads these back. They are the only signal that
    survives the gap between a piped harness and the real hook environment, so a
    hook that stops writing one has removed the sole detector for its own death."""
    for hook, payload in (("protect-files.sh", fpath("C:/work/x.md")),
                          ("protect-bash.sh", cmd("ls -la")),
                          ("backup-before-edit.sh", fpath("C:/work/x.md")),
                          ("snapshot-before-tool.sh", cmd("ls -la"))):
        fire(project, hook, payload)
    hb = project / ".claude" / "backups" / ".hook-heartbeat"
    alive = {f.name for f in hb.iterdir() if not f.name.endswith(".blind")}
    assert alive == {"protect-files", "protect-bash",
                     "backup-before-edit", "snapshot-before-tool"}, sorted(alive)
    for f in hb.iterdir():
        if not f.name.endswith(".blind"):
            assert "payload_bytes=" in f.read_text(encoding="utf-8")
            assert "payload_bytes=0" not in f.read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Deleting the install: every spelling a review found reaching it unrefused
#
# The old rule was `rm\s+(-[a-z]*f[a-z]*\s+)?<path>` -- exactly ONE flag token,
# which had to contain an `f`. The README meanwhile promised the game directory
# "cannot" be deleted. Each row below was MEASURED reaching the directory before
# the rule was re-anchored on (destroyer AND qualified path) instead of on one
# spelling of rm.
# --------------------------------------------------------------------------



@pytest.mark.parametrize("command,why", [
    (f'rm -rf "{GAME}"', "the spelling that was already caught"),
    (f'rm -f -r "{GAME}"', "two flag tokens"),
    (f'rm -r -f "{GAME}"', "two flag tokens, reversed"),
    (f'rm --recursive --force "{GAME}"', "long flags"),
    (f'rm -rf -- "{GAME}"', "the end-of-options marker"),
    (f'cd "{GAME}" && rm -rf .', "the path is in the cd, not the rm"),
    (f'G="{GAME}"; rm -rf "$G"', "the path is in a variable"),
    (f'rmdir /s /q "{GAMEW}"', "rmdir, not rm"),
    (f'del /f /s /q "{GAMEW}"', "del, not rm"),
    (f'powershell -c "Remove-Item -Recurse -Force \'{GAMEW}\'"',
     "Remove-Item -- and powershell is allow-listed"),
    (f'find "{GAME}" -delete', "find -delete"),
    (f'python -c "import shutil; shutil.rmtree(r\'{GAME}\')"', "shutil.rmtree"),
    ('rm -rf "C:/Users/Moona/Documents/My Games/Skyrim VR"', "the config directory"),
    ('reg.exe delete "HKLM' + BS + 'SOFTWARE' + BS + 'Bethesda Softworks" /f',
     "reg.exe -- the old pattern needed whitespace straight after `reg`"),
    # DOUBLED separators. `shutil.rmtree('C:\\Games\\Skyrim VR')` is not an exotic
    # spelling -- it is how you WRITE a Windows path in Python or PowerShell source,
    # and the hook is handed the command TEXT, not the string Python will build from
    # it. The root-resolution rework emitted exactly one separator per separator, so
    # every doubled form produced NO hook output at all, which the runtime reads as
    # ALLOW. The pre-arc rule matched with `[^"']*` and was separator-agnostic, so
    # this was a REGRESSION -- and the 7,641-command replay could not see it, because
    # no historical command spells the install that way. Replay prices over-blocking
    # well and under-blocking not at all.
    (f'python -c "import shutil; shutil.rmtree(\'{GAME.replace("/", BS * 2)}\')"',
     "doubled backslashes -- the canonical Python spelling"),
    (f'powershell -c "Remove-Item -Recurse -Force \'{GAME.replace("/", BS * 2)}\'"',
     "doubled backslashes via PowerShell"),
    (f'rm -rf "{GAME.replace("/", "//")}"', "doubled forward slashes"),
])
def test_the_install_cannot_be_deleted_however_it_is_spelled(project, command, why):
    got, _ = fire(project, "protect-bash.sh", cmd(command))
    assert got == "deny", f"{why}: {command}"


@pytest.mark.parametrize("command,expected,why", [
    # The controls that keep the rule above from being a blanket refusal.
    ("rm -rf node_modules", "allow", "an rm with no game path"),
    ("rm -rf /tmp/scratch", "allow", "an rm somewhere else entirely"),
    ('grep -rn "rm" README.md', "allow", "the WORD rm inside a quoted argument"),
    ("python -m pytest tests/ -q", "allow", "running the suite"),
    (f'cat "{GAME}/Data/x.esp"', "advise", "READING a game path is not deleting one"),
    (f'cp "{GAME}/Data/x.nif" /tmp/', "advise", "copying OUT is not deleting"),
    (f'ls "{GAME}/Data/Scripts"', "allow", "listing is not writing"),

    # ---- paths that merely CONTAIN the word Skyrim, and are not the install ----
    #
    # The rule used to ask "does this command mention a path containing Skyrim", which
    # matched all four of these. The scratchpad one bit five times in a single session:
    # a Claude Code scratchpad path contains `C--GOG-Games-The-Elder-Scrolls-V-Skyrim-VR`,
    # so tidying a temp file read as deleting the game.
    #
    # Note what these rows are NOT: they are not a scoping problem, and a shell parser
    # would not fix them. It would bind the verb to its target correctly and still
    # refuse, because the TARGET matches. That is why the rule resolves a real install
    # root instead.
    ("rm -rf \"C:/Users/x/AppData/Local/Temp/claude/"
     "C--GOG-Games-The-Elder-Scrolls-V-Skyrim-VR/sess/scratchpad/tmp\"",
     "allow", "the session SCRATCHPAD is not the install"),
    ('rm -rf "C:/Users/x/Projects/skyrimvr-claude-toolkit/build"',
     "allow", "this toolkit's own checkout is not the install"),
    ('rm -rf "C:/Temp/skyrim-notes"',
     "allow", "a temp file merely NAMED skyrim"),
    ('git clone https://github.com/x/Open-Composite-Unleashed-for-Skyrim-VR.git /tmp/b',
     "allow", "a repository whose NAME contains Skyrim VR"),

    # Champollion is scoped to the live install, so the remedy its own refusal
    # prescribes -- copy the .pex to a temp directory and run it there -- must work.
    ('Champollion.exe "C:/Temp/work/Data/Scripts/A.pex"',
     "allow", "a .pex copied OUT of the install is the prescribed workaround"),

    # ...and the relative-Data advisory, which no resolved root can cover because a
    # hook is never told the caller's working directory.
    ("rm -rf Data/Meshes/x.nif", "advise", "a relative delete inside Data, with flags"),
    ("rm mydata/cache.bin", "allow", "the word boundary: mydata is not Data"),
])
def test_the_delete_rule_does_not_swallow_ordinary_work(project, command, expected, why):
    got, _ = fire(project, "protect-bash.sh", cmd(command))
    assert got == expected, f"{why}: {command}"


def test_a_redirect_elsewhere_is_not_called_a_redirect_into_the_game(project):
    """`[^"']*` spanned the whole command, so any redirect plus a later mention of
    Data or Skyrim produced "redirecting output into the game/config directory" --
    naming a redirect that goes to /tmp. A warning that misdescribes what it saw
    teaches the reader to stop reading warnings."""
    got, ctx = fire(project, "protect-bash.sh",
                    cmd("grep foo bar.txt > /tmp/o.txt && ls Data/Scripts"))
    assert "Redirecting output into the game" not in ctx, ctx


def test_editing_this_toolkits_own_checkout_is_not_editing_a_game_install(project):
    """The catch-all matched a bare `Skyrim`, and this repository's directory name
    contains it -- so every edit to CHANGELOG.md or tools/*.py was annotated
    "Editing inside the live game/config install", which is simply false."""
    for path in ("C:/Users/x/Projects/skyrimvr-claude-toolkit/CHANGELOG.md",
                 "C:/Users/x/Projects/skyrimvr-claude-toolkit/tools/crash-triage.py"):
        got, ctx = fire(project, "protect-files.sh", fpath(path))
        assert got == "allow", f"{path} -> {got}: {ctx}"


def test_a_plugin_path_with_a_trailing_space_is_still_denied(project):
    r"""The deny was anchored `\.(esp|...)$`, so a trailing space slipped past it --
    and Win32 strips the trailing space, so the write lands on the plugin anyway."""
    got, _ = fire(project, "protect-files.sh", fpath("C:/Games/Skyrim VR/Data/M.esp "))
    assert got == "deny"


# --------------------------------------------------------------------------
# jq is how a hook SPEAKS
# --------------------------------------------------------------------------

def _hook_with_jq(project, tmp_path, name, jq_value):
    src = (HOOKS / name).read_text(encoding="utf-8").replace("{{JQ_PATH}}", jq_value)
    d = tmp_path / "hooks_jq" / ".claude" / "hooks"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(src, encoding="utf-8", newline="\n")
    return d.parent.parent


def test_the_delete_guard_refuses_when_it_cannot_locate_the_install(tmp_path):
    """A guard that cannot work out WHICH directory the install is has evaluated
    nothing, and must refuse rather than shrug.

    The delete rules now key on a resolved install root instead of the word "Skyrim"
    appearing anywhere in a path. That trades one failure mode for another: if the root
    cannot be derived, the rules match nothing and every deletion sails through -- the
    inert-hook state again, by a fourth door.

    The branch is otherwise unreachable from the suite (INSTALL_ROOT is `cd ... && pwd`,
    which always yields an absolute path), so it is exercised the way the jq-unusable
    case is: on a DOCTORED COPY with the resolution knocked out. Written because the
    mutation gate reported the fail-closed branch could be deleted with nothing going
    red -- i.e. it was shipped, load-bearing, and untested.
    """
    jq = shutil.which("jq")
    if not jq:
        pytest.skip("jq not installed")
    root = tmp_path / "install"
    (root / ".claude" / "hooks").mkdir(parents=True)
    text = (HOOKS / "protect-bash.sh").read_text(encoding="utf-8")
    text = text.replace("{{JQ_PATH}}", jq.replace(BS, "/"))
    # BOTH derived roots must be knocked out. The hook resolves the install twice --
    # the MSYS spelling from `pwd` and the Windows spelling from `pwd -W` -- because
    # `pwd` can return a mount alias (/tmp/...) that shares no prefix with the
    # drive-letter form a command actually names. Blanking only one leaves GAME_PATH
    # non-empty, so the fail-closed branch never fires and this test would be
    # asserting nothing. It caught exactly that when the second root was added.
    markers = ['INSTALL_ROOT="$(cd ', 'INSTALL_ROOT_WIN="$(cd ']
    for marker in markers:
        assert text.count(marker) == 1, f"{marker} moved; this test is stale"
        line = next(l for l in text.splitlines() if l.startswith(marker))
        text = text.replace(line, marker.split('=')[0] + '=""')
    hook = root / ".claude" / "hooks" / "protect-bash.sh"
    hook.write_text(text, encoding="utf-8", newline="\n")

    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    r = subprocess.run([_bash(), str(hook)],
                       input=json.dumps(cmd(f'rm -rf "{GAME}"')),
                       capture_output=True, text=True, env=env, timeout=60)
    assert r.stdout.strip(), "the hook emitted NOTHING, which the runtime reads as allow"
    h = json.loads(r.stdout)["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny", "an unlocatable install must not allow"
    assert "GUARD INERT" in h["permissionDecisionReason"]


def _hook_at(tmp_path, root_name, paths_env=""):
    """A throwaway install whose ROOT NAME is chosen by the test, so the rules are
    built from that path rather than from a fixture constant."""
    jq = shutil.which("jq")
    if not jq:
        pytest.skip("jq not installed")
    root = tmp_path / root_name
    (root / ".claude" / "hooks").mkdir(parents=True)
    text = (HOOKS / "protect-bash.sh").read_text(encoding="utf-8")
    text = text.replace("{{JQ_PATH}}", jq.replace(BS, "/"))
    (root / ".claude" / "hooks" / "protect-bash.sh").write_text(
        text, encoding="utf-8", newline="\n")
    if paths_env:
        (root / ".claude" / "skyrim-paths.env").write_text(
            paths_env, encoding="utf-8", newline="\n")
    return root


def _decide(root, command):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    r = subprocess.run(
        [_bash(), str(root / ".claude" / "hooks" / "protect-bash.sh")],
        input=json.dumps(cmd(command)),
        capture_output=True, text=True, env=env, timeout=60)
    if not r.stdout.strip():
        return "allow"
    return json.loads(r.stdout)["hookSpecificOutput"].get("permissionDecision", "advise")


def test_an_install_path_containing_regex_metacharacters_is_still_guarded(tmp_path):
    """`C:/Program Files (x86)/Steam/steamapps/common/Skyrim VR` is the most common
    real Steam layout there is, and `(x86)` unescaped is a valid ERE GROUP -- it
    matches `Program Files x86`, so the guard would simply never fire on the actual
    directory. `GAME_PATH` stays non-empty, so the fail-closed branch does not save
    it either: it is the inert-guard state by another door.

    MEASURED by the release review: deleting the escaping arm from `path_to_ere`
    left all 70 tests in this file green while this exact deletion flipped to allow.
    """
    root = _hook_at(tmp_path, "Program Files (x86)")
    target = str(root).replace(BS, "/")
    assert _decide(root, f'rm -rf "{target}"') == "deny"


def test_a_configured_config_directory_outside_documents_is_guarded(tmp_path):
    """MO2/Wabbajack/Nolvus put the INIs in the instance's profile folder, which is
    NOT under `Documents/My Games` -- so for exactly those users the hardcoded
    literal rule matches nothing and `SKYRIM_CONFIG_DIR` is the ONLY thing guarding
    their config. That arm had no test: replacing it with `:` left 88 tests green.
    """
    cfg = tmp_path / "MO2" / "profiles" / "Default"
    cfg.mkdir(parents=True)
    cfg_win = str(cfg).replace(BS, "/")
    root = _hook_at(tmp_path, "game", paths_env=f'SKYRIM_CONFIG_DIR="{cfg_win}"\n')
    assert _decide(root, f'rm -rf "{cfg_win}"') == "deny"
    # ...and the control: a same-shaped path that is NOT the configured one.
    other = str(tmp_path / "MO2" / "profiles" / "Someone Else").replace(BS, "/")
    assert _decide(root, f'rm -rf "{other}"') != "deny"


@pytest.mark.parametrize("name", ["protect-bash.sh", "protect-files.sh"])
@pytest.mark.parametrize("jq_value,why", [
    ("/c/nonexistent/jq.exe", "jq uninstalled after setup"),
    ("{{JQ_PATH}}", "the zip was extracted and setup.sh never run"),
])
def test_a_blocking_hook_refuses_when_jq_is_unusable(tmp_path, name, jq_value, why):
    """deny() emits its JSON THROUGH jq, so without jq the refusal itself vanishes
    and the hook produces nothing -- which the runtime reads as ALLOW. That is a
    second live route to the inert state, by another door, and it needs no jq to
    close: the one refusal that must not depend on jq is printed with printf."""
    root = _hook_with_jq(None, tmp_path, name, jq_value)
    payload = (cmd('rm -rf "C:/Games/Skyrim VR"') if name == "protect-bash.sh"
               else fpath("C:/Games/Skyrim VR/Data/M.esp"))
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(root))
    r = subprocess.run([_bash(), str(root / ".claude" / "hooks" / name)],
                       input=json.dumps(payload), capture_output=True, text=True,
                       env=env, timeout=60)
    assert r.stdout.strip(), f"{why}: hook emitted NOTHING, which reads as allow"
    j = json.loads(r.stdout)
    h = j["hookSpecificOutput"]
    assert h["permissionDecision"] == "deny", why
    assert "GUARD INERT" in h["permissionDecisionReason"]
