---
description: Write a restart brief capturing in-flight work, so a fresh session can continue cheaply without re-reading the codebase
---

# Write the restart brief

Run this at the END of a working session, before starting a fresh conversation.
The point: right now the expensive context is already loaded and already paid for.
Spending ~250 output tokens here saves thousands of reconstruction tokens later.

Write to `.claude/restart-brief.md`. **Overwrite it completely — never append.**
An appended file grows into a decaying changelog, which is the exact failure mode
this is designed to avoid.

## Step 1 — Capture mechanical state (tool output, not prose)

Run these and embed the raw output verbatim:

```bash
git rev-parse --short HEAD
git status --short
git diff --stat
git log --oneline -5
```

This part cannot hallucinate. It is the trustworthy half of the brief.

## Step 2 — Write the intent (the only part git can't produce)

Fill in this exact shape. Keep the whole file under ~35 lines.

```markdown
# Restart brief — <YYYY-MM-DD>
HEAD at write time: <sha>

## Task
<One sentence. What we were actually trying to accomplish.>

## Files in play
<3-5 paths, max. Only files being edited or read repeatedly. NOT the whole repo.>

## Done
<What is finished and verified. If tests were run, say which and the result.>

## Next
<The single next concrete action. Not a plan — the next step.>

## Ruled out
<Approaches tried or considered and rejected, WITH the reason.
 This is the highest-value section — it is the only content no tool
 can regenerate. Without it the next session re-proposes rejected ideas
 and you pay to re-litigate them.>

## Unverified claims
<Any line numbers, function names, or behavior asserted above.
 Flag them explicitly so the next session re-checks instead of trusting.>

---
<raw git output from Step 1>
```

## Rules

- **No conversation narrative.** Don't recap what was discussed. Only current state
  and next action. A transcript summary is what we're trying to avoid paying for.
- **Pointers over copies.** Reference `file.py:function`, don't paste code bodies.
  Pasted code goes stale; a pointer stays correct.
- **Mark every code claim as unverified.** Line numbers drift. The next session must
  treat them as leads to check, never as facts.
- **Under 35 lines.** If it doesn't fit, the task is too big to hand off in one brief —
  say so explicitly rather than writing more.
- **Don't invent progress.** If something is half-broken, say it's half-broken. A brief
  that overstates completion is worse than no brief, because the next session builds
  on a false foundation and the failure is silent.
