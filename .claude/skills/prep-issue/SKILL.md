---
name: prep-issue
description: Get a GitHub issue ready to dispatch to an agent. Used as /prep-issue <number>.
disable-model-invocation: true
---

Get issue #$ARGUMENTS ready for an agent to implement with /work-issue. Don't
write or edit code.

1. Read the issue and its comments (`gh issue view $ARGUMENTS --comments`),
   GLOSSARY.md, and any ADR it mentions. Find its blockers with
   `gh api repos/{owner}/{repo}/issues/$ARGUMENTS/dependencies/blocked_by --jq '.[] | "#\(.number) \(.state) \(.title)"'`
   and read each one, plus the PR that closed it if it is closed. If a blocker
   is still open, say so up front: the issue can be prepped but not dispatched.
2. Check what has changed since the issue was written: recently merged PRs
   (`gh pr list --state merged --limit 10`), open PRs that might overlap
   (`gh pr list --state open`), and the code on `origin/main` it will touch
   (run `git fetch origin` first).
3. Work out what an implementing agent would otherwise have to guess: names or
   decisions made since the issue was written, resources it needs, risks from
   other open work, and what evidence would prove the work is done.
4. Look up facts yourself. Ask me about anything that is a decision. If there is
   more than one, use the `mattpocock-skills:grilling` skill to go through them
   one at a time.
5. Show me the draft comment. When I confirm, post it as one comment on the
   issue titled "Context for implementation" with `gh issue comment`. Never put
   local file paths, machine details or anything private in it, because this
   repo is public. If a decision changes the issue's scope or acceptance
   criteria, edit the issue body too (`gh issue edit`) rather than leaving the
   change only in the comment.
6. Give me the exact dispatch command, for example:
   `claude --bg -w issue-$ARGUMENTS-<short-name> --permission-mode auto "/work-issue $ARGUMENTS"`
   plus any machine-specific lines (like local paths) that didn't belong in the
   comment, to append to the prompt.
