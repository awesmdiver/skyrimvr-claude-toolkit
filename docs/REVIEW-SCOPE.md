# Review scope: what a release review covers, and what it does not

Adapted from the X4 toolkit's equivalent, which this project's release rule already
cites. It exists because three consecutive release reviews here turned into
whole-repository audits that never converged, and because the round that finally
measured it found that **15 of 19 defect sites PREDATED the arc** — both CRITICALs
included, already shipped in the previous release, byte-identical.

That is not an argument for skipping those findings. It is an argument for not
letting them hold a release, because **a defect the release did not introduce is one
the users already have, and holding the release does not fix it.**

---

## Derive the scope. Never list it from memory.

The first command of any release review is not a file list, it is a range:

```bash
git log --oneline <last-tag>..HEAD                       # the commits
git diff --stat <last-tag>..HEAD                         # what they touched
git diff --diff-filter=A --name-only <last-tag>..HEAD    # what they ADDED
```

Working from memory loses files. Preparing the v3.9 review from memory omitted
`tools/verify-release.sh` — a 136-line addition that had never shipped and would have
had its first release with nobody having read it.

**A file the range MODIFIES is read IN FULL, not merely its hunks.** A hunk tells you
what changed; it cannot tell you whether the change is right in the file it landed in.

**A subsystem the range ADDS is read in full before its first release, however large.**
`live_query.lua` (2,501 lines) was added on 2026-08-31, first read six days later, and
carried every in-arc finding of that round.

---

## Track 1 — the release gate

Everything the release CHANGES:

- every commit in `<last-tag>..HEAD`;
- every file that range adds or modifies, read in full;
- the shipped file set versus what the port/build check reports — the repo passing is
  not the same claim as the artifact passing, and the two have diverged twice here;
- anything whose failure mode is SILENT: guards, gates, hooks, refusal paths, backups,
  rotations. These are the ones nobody notices, because nothing goes red;
- whether every claim in the CHANGELOG, the README and `CLAUDE.md` still matches the
  code. A false reassurance outranks a stale sentence: the README promised "Your
  knowledgebase additions are preserved" while updating destroyed them, which is a
  reassurance that fires exactly when the thing it names has been destroyed.

**Track 1 findings block the release.**

## Track 2 — worth doing, does not gate

Defects found in code the range did not touch. Real, worth fixing, scheduled on their
own merits — but they are already in users' hands, and holding the release does not
change that.

**Blame every finding IN-ARC vs PRE-ARC against an explicit range before ranking it.**
An unblamed finding list silently converts an audit into a release blocker.

## The third bucket — PRE-ARC, but the release RECRUITS FOR IT

The one case where a pre-arc defect gates the release: the arc makes a latent defect
reachable, more likely, or more damaging.

Concrete, from this project: the game-directory delete rule had been narrow enough that
eleven ordinary spellings of "delete the game folder" walked past it. That was pre-arc
and had never mattered — **because the hooks were inert, so the gap had never been
exercised.** The arc that fixed the hooks is what made the narrowness reachable. The
fix was pre-arc; the exposure was in-arc.

Ask of every PRE-ARC finding: *does anything in this range make it easier to hit?* If
yes, it is Track 1.

---

## Standing traps, all of which have cost this project a release or nearly so

- **A green determined before the check ran is the default failure mode.** Of every
  verification ask out loud: "what, concretely, would have made this go red?" Every hook
  in the shipped bundle was inert for five weeks while its suites stayed green.
- **A check whose pattern matches its own documentation is worse than none.** It goes
  red when someone removes the explanation, and green on the broken state. Hit three
  times in one day: a grep for `cat /dev/stdin` matching the comment explaining the
  defect; a fork-count grep matching my own comments; an invariant satisfied by the
  comment above the gate it asserted. Match the CONSTRUCT and skip comments.
- **Prove a guard by mutation.** Break it on purpose and require the NAMED check to go
  red. "The suite failed" launders a survivor into a kill. Baseline green first.
- **One twin per clause.** Each guard shadows the ones behind it, so a single test only
  ever exercises the first clause it trips.
- **Read the output, never the exit code**, on any tool you did not run bare. A trailing
  `echo` has made a failing run report exit 0 here more than once.
- **Do not edit the tree while a review or a mutating pass is running.** A release port
  ran while a mutating gate had a file rewritten in place and carried
  `if len(targets) > 99999:` into a public bundle. The port reported success and the
  tracked diff looked right.
- **A search finding nothing is a LEAD, not a fact.** Run a second, differently-shaped
  search before any negative claim.
- **Someone else's measurement is evidence, not truth** — including another agent's,
  and including my own earlier turns.

---

## A clean review is necessary, not sufficient

It does not replace the release's own gates, the cold-clone verification of the
published artifact (`tools/verify-release.sh`), or the end-to-end check on a real
install. State the reviewer's verdict in the release notes discussion, not only in chat.
