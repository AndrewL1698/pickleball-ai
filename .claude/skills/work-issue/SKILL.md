---
name: work-issue
description: Implement one GitHub issue end to end and open a PR. Used as /work-issue <number>.
disable-model-invocation: true
---

Work GitHub issue #$ARGUMENTS.

1. Read it with `gh issue view $ARGUMENTS --comments`. If it has a "Context for
   implementation" comment (written by /prep-issue), treat it as part of the
   spec.
2. Check blockers. This repo records them as GitHub issue dependencies, which
   `gh issue view` does not show:
   `gh api repos/{owner}/{repo}/issues/$ARGUMENTS/dependencies/blocked_by --jq '.[] | select(.state=="open") | "#\(.number) \(.title)"'`
   Also check the body for a `Blocked by: #n` line. If any blocker is still
   open, stop and tell me.
3. Get on a branch. If the session is on `main`, run `git fetch origin` and create
   `feature/issue-$ARGUMENTS-<short-name>` from `origin/main`. If it is already
   on another branch (for example a worktree's), make sure it is based on the
   current `origin/main`; rebase if it is behind. Never commit to `main`.
4. Read GLOSSARY.md and any ADR the issue mentions. Use the glossary's terms in
   names and code.
5. Build it in small steps, using the `mattpocock-skills:tdd` skill at the seams
   the issue or ADRs describe. Run typechecking and single test files often,
   and the full suite at the end.
6. Stay in scope. If the issue is wrong or incomplete, or you find a separate
   problem, comment on the issue with `gh issue comment` and tell me. Don't
   widen the change.
7. Never delete, skip or weaken a test to make it pass. Don't add a database
   migration the issue doesn't call for without asking me.
8. Review your work with the `mattpocock-skills:code-review` skill against
   `origin/main` (it checks the change against this issue and the repo's
   standards) and fix what it finds. Then commit.
9. Push the branch and open a PR with `gh pr create`. Write the body with the
   `mattpocock-skills:pr` skill, include "Closes #$ARGUMENTS", and map every
   acceptance criterion to its evidence: test names, commands to reproduce,
   screenshots or metrics.
