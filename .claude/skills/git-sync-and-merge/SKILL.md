---
name: git-sync-and-merge
description: Use when the user says "sincronitza" or signals a session close, when integrating a working branch into the default branch, or when a push, pull request, or the close git sequence has to run without stalling on a missing remote or a conflict.
---

# Git sync and merge

## Overview

Branch integration here is **autonomous and goes through a pull request**. Open it,
merge it with a merge commit, clean up — no approval wait, no "should I merge it?".
The two moments that trigger it are the "sincronitza" command mid-session and a session
close. Both run the same sequence; only the surrounding work differs.

Core principle: **the sequence completes or the repo is left clean — never half-merged.**

## When to use

- The user says "sincronitza" / "sincronitzar".
- The user signals a session close and work has to be integrated.
- A branch is ready and needs to reach the default branch.
- A push fails or is refused and the chain must stop cleanly.
- A pull request cannot be merged because it conflicts.

## Quick reference

| Question | Answer |
|---|---|
| Pull request or local merge? | Pull request, always, through the `github` connector: `create_pull_request`, then `merge_pull_request` with `merge_method: merge`. Never `gh`, never a local merge into the default branch. |
| Merge flavor | `--merge` (merge commit), so the branch stays visible in history. Never squash, never rebase. |
| Default branch | Resolve it, do not assume — see below. |
| How many Bash calls | Three for the sequence itself, around the connector calls. Inspection and local verification before, and verification after, are outside the cap. |
| No GitHub remote | Stop: commit on the working branch and tell the user. No merge. |
| Push runs and fails | Stop: no pull request without the pushed branch. Report it. |
| Nothing to commit | Guard the commit — a clean tree makes `git commit` exit 1 and kill the chain. |
| Remote CI | Not a merge gate when local verification covers the same checks. |
| Pull request conflicts | Cap suspended: leave the PR open, end on the working branch, tell the user. |
| Remote branch | Not deleted by `sincronitza`. A session close deletes every branch already merged, local and remote. |

## Before the sequence

Inspect first — one call usually, more when a rung of the default-branch ladder answers
ambiguously. Inspection is **outside** the three-call cap — the cap covers the
integration sequence, not the reads that decide what the sequence contains. So is the
local verification, any verification call afterwards, and any call that was denied and
never executed.

The inspection resolves three things:

1. **The working branch** — `git rev-parse --abbrev-ref HEAD`. That is `<branch>` throughout;
   the sequence never invents one.
2. **Whether a GitHub remote exists** — `git remote -v`. Empty, or a remote that is not on
   GitHub, means a pull request is impossible: take the no-remote path below.
3. **The default branch** — in this order:
   - `git ls-remote --symref origin HEAD`, which answers even when the local
     `refs/remotes/origin/HEAD` was never set. **Empty output with exit 0 is not an answer** —
     it means the remote's own HEAD is a dangling symref (it names a branch that does not
     exist there). Treat empty as a fall-through to the next rung, never as "no default".
   - `git symbolic-ref --short refs/remotes/origin/HEAD` — only present in repos created by
     `clone` or fixed with `git remote set-head origin -a`. It fails with `fatal: ref ... is
     not a symbolic ref` in every other repo, which is the common case, not the exception.
   - `git ls-remote origin` and `git branch --list main master` — which branches actually
     exist, remote and local. This is the rung that answers when the two above go quiet.
     `git config --get init.defaultBranch` is a hint, not evidence: it happily returns `main`
     in a repo whose only branch is `master`. Confirm the branch you are about to target
     actually exists. A PR opened with the wrong `--base` fails, or worse, lands on the
     wrong branch.

When `<branch>` is the default branch itself, there is no pull request to make from it:
it would have the same head and base. Before the sequence, create a branch for the work
(`git checkout -b <new-branch>`, named for the change); that branch is `<branch>` from
here on, and the commits made on the default branch go with it.

The inspection also reads `git status --short`. The paths to stage are the ones this
session itself created or modified, and no others. An untracked path the session did not
create -- build output, a cache, another tool's directory, a file of the user's -- is never
staged and never deleted: leave it where it is and name it in the report (2026-10-08, code
review: staging everything `git status` lists is `git add -A` under another name).

Then verify locally that the project builds and its tests pass — the project's
`CLAUDE.md` names the exact commands when it names any. A change touching only
documentation or tooling runs neither. A failing build or test stops here: nothing is
pushed, and the user is told.

## The sequence

Three Bash calls, with the pull request made between the second and the third through
the `github` connector (`mcp__github__*`). Nothing in the sequence goes through `gh`:
owner and repository come from the `git remote -v` of the inspection.

**Call 1** — isolated, alone:

```bash
git checkout <branch>
```

It is isolated because branch guards inspect git state at the moment of the call. A
`checkout` buried later in a chain leaves those guards reading the branch you were on,
not the one you will be on.

**Call 2** — commit and push, chained with `&&`:

```bash
{ git add -- <path>... && git commit -m "<subject>" || echo "NOTHING TO COMMIT - continuing"; } \
  && git push origin <branch>
```

No `-u`: the branch is merged and deleted in the same run, so tracking buys nothing,
and setting it writes `.git/config`, which a sandboxed session may not be allowed to
write -- the push lands, then reports an error the chain has to explain away.

**Connector calls** — only once the push succeeded:

1. Look for a pull request already open for `<branch>` (`list_pull_requests`). If one
   exists, reuse it; otherwise `create_pull_request` with `base` the default branch,
   `head` `<branch>`, a title and a body.
2. `merge_pull_request` with `merge_method: merge` and `commit_title`
   `merge: <branch> into <default>`.

**Call 3** — back on the default branch, chained with `&&`:

```bash
git checkout <default> \
  && git pull --ff-only origin <default> \
  && git branch -d <branch>
```

Stage the paths by name, the ones the commit message describes. Never `git add -A` or
`git add .`: they sweep in whatever else sits in the tree, and the commit then carries
files its message does not mention. Nothing to stage is not an error here: with no path
to name, skip the `git add` and let the guard below absorb the empty commit.

The commit guard is load-bearing. `git commit` exits 1 on a clean tree, and a mid-session
"sincronitza" with everything already committed is an ordinary case, not an edge one — an
unguarded commit aborts the chain before the pull request is ever opened.

The push is deliberately **not** guarded: a pull request needs the pushed branch, so a
failed push must stop the sequence before the connector calls. Looking for an open pull
request first reuses one already open for the branch instead of failing on a duplicate.

The PR title and body follow the repository's language rule (English). End the body with
the attribution lines the session provides, if any.

Agent contexts reset the working directory between calls: prefix each Bash call with
`cd <repo> &&`. That does not split a call, so the cap is unaffected.

## After the sequence

Verify rather than trusting the command output: read the pull request back (`pull_request_read`)
for its state and merge commit, and run `git log --oneline --graph -10`. This is outside the cap.

The run ends on the default branch with the local working branch deleted. After a
mid-session "sincronitza", further work starts on a new branch.

After `sincronitza` the remote branch is left alone (a session close deletes it, see
below). Do not try to delete it if the environment has no permission for it, and if a
deletion fails, do not retry it or report it.

If `refs/remotes/origin/HEAD` was missing during inspection, `git remote set-head origin -a`
usually fixes it once and spares the next run the fallback. It fails with
`error: Cannot determine remote HEAD` when the remote's HEAD itself dangles; the repair is
then on the remote, which is outside this skill's scope — report it rather than working
around it every run.

## A session close leaves the repo clean

The session close is the `session-close` skill of the `new-session` plugin
(2026-10-07, explicit user request: opening, closing and archiving a session
live together). Its git part is this skill's, in two steps:

1. **Leave nothing of this session's to commit, push or merge.** Run the
   sequence above on the session's own working branch until no change the
   session made is left uncommitted, nothing on that branch is unpushed and
   that branch is not waiting for a pull request. Clean here means nothing
   tracked that the session changed is left uncommitted; untracked files the
   session did not create do not count, are never deleted to get there, and
   are reported. Other local branches are left alone: another session's
   unfinished work is not this close's to merge (2026-10-08, code review).
2. **Delete every branch already merged into the default branch, local and
   remote.** Local: `git branch --merged <default>`, then `git branch -d`.
   Remote: `git branch -r --merged origin/<default>`, then
   `git push origin --delete <branch>`. Never the default branch, never the branch
   checked out, never a branch not fully merged. Skip a branch whose tip is the
   default branch's own tip (`git rev-parse <branch>` equals `git rev-parse <default>`):
   it holds no work, typically because it was just created, and `--merged` lists it
   only because it contains nothing. A remote deletion that fails is reported once,
   not retried.

   In a sandboxed session `git branch -d` prints
   `error: could not lock config file .git/config` and still deletes the branch
   (2026-10-09). The sandbox denies writes to `.git/config`, and git tries to
   remove the branch's `[branch "<name>"]` section even when it has none, which
   a branch pushed without `-u` never gets. It is expected and harmless: report
   it as such, do not retry it and do not go outside the sandbox to avoid it.

`sincronitza` does not do step 2: the work goes on, and the remote branch is
left alone. This skill never closes the window.

## Failure paths

**No GitHub remote.** `git remote -v` came back empty, or pointing somewhere that is not
GitHub, during inspection. A pull request is impossible, so there is no merge. Commit the
pending work on the working branch (guarded, as in call 2), stay on it, and tell the user
the branch was not integrated and why. Never fall back to a local merge.

**Push runs and is rejected, or is refused before it runs.** The chain stops before the
pull request. Nothing reached the default branch. Report the failure; do not silently
swallow it.

**Nothing to commit.** The guard absorbs it and the chain continues to the push. Expected
whenever the work was already committed.

**Pull request conflicts.** `merge_pull_request` refuses a PR that does not merge cleanly, and the
sequence stops with HEAD still on the working branch. The three-call cap is suspended from that
point. Leave the pull request open, tell the user what conflicted, and ask which side wins.
Do not resolve it by picking a winner yourself.

**Merged remotely, local cleanup fails.** The integration is done; report which cleanup
step failed (`git pull --ff-only`, `git branch -d`) rather than retrying blindly.

**A call is denied.** A denied call changes no state. Re-issuing it corrected is not a
third call — only executed calls count against the cap.

## Commit subjects

Conventional Commits.

| Commit | Subject |
|---|---|
| Merge | `merge: <branch> into <default>` |
| Pending work swept up by a mid-session sync | `chore: sync pending work` |

## Common mistakes

| Mistake | Consequence |
|---|---|
| Merging locally into the default branch | Bypasses the pull request the integration rule requires. |
| Asking "should I merge it?" | Integration stalls waiting for an approval nobody needs to give. |
| Squashing or rebasing the PR | Loses the branch in history; explicitly forbidden. |
| Waiting for remote CI | Stalls on a check local verification already covered. |
| Guarding the push with `\|\| echo` | The chain goes on to open a PR for a branch that is not on the remote. |
| Putting the checkout inside the chain | Branch guards read stale state and fire on the wrong call. |
| Unguarded `git commit` in the chain | A clean tree exits 1 and kills the chain before the pull request. |
| Assuming the default branch is `main` | The PR targets a base that does not exist in a `master` repo. |
| Resolving a PR conflict by picking a side | Discards someone's intent without asking. |
| Retrying a failed remote-branch deletion | Noise; the remote branch is not this sequence's job. |
| Closing the window from this skill | Kills a session that was going to carry on working; only `session-close` closes the window. |
| Closing the window before the reply is written | The session dies with what it still had to say; the user reads nothing. |
