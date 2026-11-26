---
name: review-pr
description: Review a Keelson pull request, or a group of related ones, for fit and then for implementation. Use when asked to review a PR by number or topic.
---

# Reviewing a Keelson pull request

Two questions come first: **does it fit well into Keelson**, and **is the
maintainer willing to maintain it?** If either answer is no, good code does not
change it.

Read `CONTRIBUTING.md` first. The maintainer decides: nothing is posted, pushed,
merged or closed without their word.

Review related pull requests together, so that a design question is settled
once.

## Step 1: the design brief

Read the description, the diff, the linked issue, the earlier reviews and the
parts of the spec it touches. Then write half a page of plain prose for a
reader who has not seen the pull request:

1. **Background:** the problem, and the terms needed to follow it.
2. **What changes:** what someone on the bus would see, with one concrete
   example.
3. **Fit and cost:** the questions below that matter here. Skip the rest.
4. **Decisions:** at most three, each with the options and a recommendation.
5. **Proposed outcome:** go on, make smaller, move elsewhere, park or close.

Questions for fit and cost:

- Is the need general, or one application's? Who uses it today?
- Does Keelson already have this, or something close? Could it live elsewhere?
- Is each mechanism (subject, RPC, liveliness token, QoS profile) used for what
  the spec says it is for?
- What must a reader know that the names and types do not say: sign, range,
  zero, frame?
- Who publishes it? What does a consumer see on silence, a crash or an older
  version?
- Who breaks? What does it depend on? Can it be tested without hardware?
- How hard is it to undo? Subjects, payloads, interfaces and spec are hardest
  (others build on them, recordings keep them), then the SDKs, then a connector.

No list of bugs. Then **stop and wait**: what the maintainer decides here can
make the rest unnecessary.

Judge the design against the spec, not against what an earlier review asked
for. Earlier reviews can be wrong, our own included. Don't let the convenience
of an existing consumer or build decide the design; say who breaks.

## Step 2: the implementation

Only for what passed step 1.

- Work in a detached worktree with current `dev` merged in. Regenerate the
  SDKs, then run the SDK tests and those of the affected connectors.
- **Reuse and structure:** ask a fresh reviewer (a subagent) to look for
  duplicates, misplaced code and needless complexity. Give it the diff, not
  your own conclusions, and check what it reports. Code needed in two connectors
  belongs in the SDK.
- **Tests:** break the code on purpose, one change at a time, and see that a
  test fails. A change that no test notices is a finding.
- **Correctness:** errors, reconnects, timing and concurrency.
- **Text:** comments, spec and description agree with the code and say no more
  than a reader needs.

Sort every finding:

| | What happens |
|---|---|
| **Decide** | A design question that step 1 missed: back to the maintainer |
| **Fix** | Prepare the fix, with a test where behaviour changes |
| **Elsewhere** | Concerns more than this pull request: propose an issue |
| **Drop** | Not worth anyone's time |

If the fixes amount to substantial rework, stop and recommend sending it back
or closing it.

## Step 3: one approval

Verify every claim before it is published, by running it or by reading the
code; leave out what cannot be verified. Then show the maintainer, together:

- the diff of the fixes, and what was tested
- the text to be posted
- what happens on approval: post, push, and merge when CI is green

One "go" covers all of that. Stop and ask again if the author's branch has
moved, CI fails, or a new design question appears.

Before merging, check that the head is the commit CI passed on, and retarget
stacked children to `dev`. Never force-push an author's branch.
