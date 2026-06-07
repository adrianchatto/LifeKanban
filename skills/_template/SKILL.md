---
name: <action-name>
description: >
  <One or two sentences: when should Claude use this skill? Include the phrases
  Ch@o is likely to say. e.g. "Use when asked to draft a NEO-015 weekly status.">
---

# <Action name>

## When to use
<Trigger conditions.>

## Inputs needed
<What the card description must contain for this to run unattended.>

## Steps
1. <Step.>
2. <Step.>
3. Save the deliverable to `results/<card-id>.<ext>`.

## Output
<What "done" looks like — the format and where it lands.>

## Risky steps (pause for OK)
<List any send/publish/delete/irreversible steps. If present, the worker must
park the card in `needs_ok` after prep instead of completing it.>
