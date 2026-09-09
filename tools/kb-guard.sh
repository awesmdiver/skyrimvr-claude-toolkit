#!/usr/bin/env bash
# Keep a copy of the files nothing else can rebuild -- and say so when one shrinks.
#
# WHY THIS EXISTS. The documented update path for this toolkit is "extract the new zip
# over your install", so every file the toolkit ships is REPLACED on update. Two of
# them are files you are told to edit: KNOWLEDGEBASE.md (the standing instruction in
# CLAUDE.md appends findings to it after every session) and CLAUDE.md itself (which
# carries your install's own paths). v3.8.4 moved future accumulation to
# KNOWLEDGEBASE.local.md, which is untracked and gated out of the release payload --
# but that protects only what has not been written yet. An install whose notes are
# ALREADY in the shipped file is covered by exactly one thing: a copy taken BEFORE the
# extract. This takes that copy, once per session, from SessionStart.
#
# ⚠ MEASURED on the author's install 2026-09-08: the backup store held ZERO copies of a
# 120 KB KNOWLEDGEBASE.md across seven months. Not because backup-before-edit.sh is
# broken -- it works, and it has no .md or size filter. The cause is the write CHANNEL:
# the knowledgebase is edited through Python invoked via Bash, which an Edit/Write hook
# structurally cannot see. A file can be edited hundreds of times, backed up never, and
# nothing anywhere reports it. That is the gap this closes.
#
# ⚠ WHAT IT DOES NOT DO. The alarm fires on shrinkage; an update that replaces your
# 100 KB knowledgebase with a 140 KB shipped one is a total loss that shrinks nothing
# and will NOT alarm. The SNAPSHOT is the protection here; the alarm is a convenience
# on top of it. Recovery is always "the previous copy is in the store".
#
# Usage:  bash tools/kb-guard.sh [--verbose]
# Exit:   0 = checked · 1 = ALARM (a watched file shrank or vanished) · 2 = cannot check

set -uo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)}"
STORE="$ROOT/.claude/backups/kb"

# The irreplaceable set: files a user writes into that this toolkit also ships.
# Anything the toolkit can regenerate (skyrim-paths.env, settings.json) is NOT here --
# a store full of reproducible files makes the copies that matter harder to find.
WATCHED="KNOWLEDGEBASE.md KNOWLEDGEBASE.local.md CLAUDE.md"

KEEP="${KB_GUARD_KEEP:-30}"
VERBOSE=0
[ "${1:-}" = "--verbose" ] && VERBOSE=1

if [ -z "$ROOT" ] || [ ! -d "$ROOT" ]; then
    echo "KB GUARD: NOT RUN -- could not resolve the install root, so nothing was checked" >&2
    exit 2
fi

bytes() {
    local n
    n=$(wc -c < "$1" 2>/dev/null) || n=0
    echo $((n))
}

# The newest stored copy of $1, or empty. The glob expands in sorted order and the
# directories are timestamp-named, so the LAST match is the most recent.
prior_copy() {
    local name="$1" d found=""
    for d in "$STORE"/*/; do
        [ -f "$d$name" ] && found="$d$name"
    done
    printf '%s' "$found"
}

# The BIGGEST stored copy of $1, which is not always the newest and is what someone
# recovering actually wants. If an extract-over went unnoticed for two sessions, the
# newest stored copy is the truncated one; pointing a user at that would hand them the
# damage back and call it a backup.
largest_copy() {
    local name="$1" d sz best="" bestsize=0
    for d in "$STORE"/*/; do
        [ -f "$d$name" ] || continue
        sz=$(bytes "$d$name")
        if [ "$sz" -gt "$bestsize" ]; then bestsize=$sz; best="$d$name"; fi
    done
    printf '%s' "$best"
}

# The two lines every alarm ends with: where the last copy is, and -- when they differ
# -- where the biggest one is.
where_to_recover() {
    local name="$1" prior="$2" best
    best=$(largest_copy "$name")
    printf '    previous copy: %s' "$prior"
    if [ -n "$best" ] && [ "$best" != "$prior" ]; then
        printf '\n    LARGEST stored copy: %s (%s bytes)' "$best" "$(bytes "$best")"
    fi
}

if ! mkdir -p "$STORE" 2>/dev/null; then
    echo "KB GUARD: NOT RUN -- cannot create $STORE, so nothing was checked" >&2
    exit 2
fi

# ⚠ The stamp must be UNIQUE, not merely current. Two runs inside the same second
# otherwise share a directory and the second `cp` overwrites the first -- which is the
# exact loss this tool exists to prevent, committed by the tool itself. MEASURED while
# building it: a probe that emptied a 9000-byte file one second after snapshotting it
# left a 0-byte copy in the store where the 9000-byte one had been, and the rotation's
# "never prune the largest" rule then had nothing left to protect. Sessions rarely
# start twice in a second; `bash tools/kb-guard.sh` run twice by hand does.
# The suffix sorts AFTER the bare stamp, so newest-last ordering survives it.
BASE_STAMP=$(date +%Y%m%d_%H%M%S)
STAMP="$BASE_STAMP"
_n=2
while [ -d "$STORE/$STAMP" ]; do
    STAMP="${BASE_STAMP}_$_n"
    _n=$((_n + 1))
done
DEST="$STORE/$STAMP"
alarms=""
copied=""
checked=0

for name in $WATCHED; do
    live="$ROOT/$name"
    prior=$(prior_copy "$name")

    if [ ! -f "$live" ]; then
        # Absent and never seen is normal -- KNOWLEDGEBASE.local.md does not exist
        # until the first finding is written. Absent after we HAD one is not.
        if [ -n "$prior" ] && [ "$(bytes "$prior")" -gt 0 ]; then
            alarms="$alarms
  $name is GONE. It was $(bytes "$prior") bytes at the last snapshot.
$(where_to_recover "$name" "$prior")"
        fi
        continue
    fi

    checked=$((checked + 1))
    cur=$(bytes "$live")

    if [ -z "$prior" ]; then
        mkdir -p "$DEST" 2>/dev/null
        cp -p "$live" "$DEST/$name" 2>/dev/null && copied="$copied $name(first,${cur}b)"
        continue
    fi

    # cmp, not a hash: exact, and it needs no sha256sum/md5sum to exist.
    if cmp -s "$live" "$prior"; then
        continue
    fi

    prev=$(bytes "$prior")
    if [ "$cur" -eq 0 ] && [ "$prev" -gt 0 ]; then
        alarms="$alarms
  $name is EMPTY. It was $prev bytes at the last snapshot.
$(where_to_recover "$name" "$prior")"
    elif [ "$prev" -gt 0 ] && [ $((cur * 2)) -lt "$prev" ]; then
        alarms="$alarms
  $name lost more than half its bytes: $prev -> $cur since the last snapshot.
    That is what extracting a toolkit update over your install looks like.
$(where_to_recover "$name" "$prior")"
    fi

    # Snapshot regardless -- including when it alarmed. The alarm names the PREVIOUS
    # copy, which the store already holds and the rotation below is forbidden to eat;
    # skipping the new copy would only mean losing the shrunken state as well.
    mkdir -p "$DEST" 2>/dev/null
    cp -p "$live" "$DEST/$name" 2>/dev/null && copied="$copied $name(${prev}->${cur}b)"
done

# ---------------------------------------------------------------- rotation
#
# AFTER the new copy is on disk, never before: a prune that runs first can delete the
# last good copy and then fail to write a new one.
#
# ⚠ Three kinds of directory are never eligible, whatever the limit says: the OLDEST,
# the NEWEST, and whichever holds the LARGEST copy of each watched file. Without the
# largest rule the store eats its own good copy in the sessions following a loss --
# every session after an extract-over snapshots the truncated file, and a plain
# oldest-first rotation walks the full-size copy off the end. That is worse than having
# no store at all, because it looks like protection the whole time it is destroying the
# thing it protects. (The newest is pinned because at a small KEEP the oldest-first
# walk would otherwise delete every unprotected directory, including the one written
# seconds earlier -- correct arithmetic, useless result.)
prune_store() {
    local dirs=() d p name best bestsize sz skip removed target
    for d in "$STORE"/*/; do
        [ -d "$d" ] && dirs+=("$d")
    done
    [ "${#dirs[@]}" -le "$KEEP" ] && return 0

    local protected=("${dirs[0]}" "${dirs[$(( ${#dirs[@]} - 1 ))]}")
    for name in $WATCHED; do
        best=""; bestsize=0
        for d in "${dirs[@]}"; do
            [ -f "$d$name" ] || continue
            sz=$(bytes "$d$name")
            if [ "$sz" -ge "$bestsize" ]; then bestsize=$sz; best="$d"; fi
        done
        [ -n "$best" ] && protected+=("$best")
    done

    removed=0
    target=$(( ${#dirs[@]} - KEEP ))
    for d in "${dirs[@]}"; do
        [ "$removed" -ge "$target" ] && break
        skip=0
        for p in "${protected[@]}"; do
            [ "$d" = "$p" ] && skip=1
        done
        [ "$skip" -eq 1 ] && continue
        rm -rf "$d" 2>/dev/null && removed=$((removed + 1))
    done
}
prune_store

# ---------------------------------------------------------------- report
kept=0
for d in "$STORE"/*/; do
    [ -d "$d" ] && kept=$((kept + 1))
done

if [ -n "$alarms" ]; then
    echo "=============================================================="
    echo "KB GUARD -- ALARM: an irreplaceable file lost content"
    echo "=============================================================="
    printf '%s\n' "$alarms"
    echo
    echo "  Compare before writing anything else into it, then merge by hand:"
    echo "    diff \"<previous copy>\" \"$ROOT/<file>\""
    echo "  Extracting a toolkit update over an install replaces every file the"
    echo "  toolkit ships. Keep your own findings in KNOWLEDGEBASE.local.md, which"
    echo "  is never shipped."
    exit 1
fi

if [ "$checked" -eq 0 ]; then
    echo "KB GUARD: NOT RUN -- none of the watched files exist under $ROOT ($WATCHED)" >&2
    exit 2
fi

if [ -n "$copied" ]; then
    echo "KB GUARD: snapshot $STAMP --$copied ($kept kept)"
elif [ "$VERBOSE" -eq 1 ]; then
    echo "KB GUARD: $checked file(s) unchanged since the last snapshot ($kept kept)"
else
    echo "KB GUARD: $checked file(s) checked, unchanged ($kept kept)"
fi
exit 0
