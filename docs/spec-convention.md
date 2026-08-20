# Architecture spec convention

Any spec doc used with this workflow (e.g. `docs/architecture.md` in the product repo)
must use **numbered, anchored headings** so agents can patch a section without
touching the rest of the file, and so diffs stay small and reviewable.

## Rules

1. Every section heading is numbered and stable once created: `## 3.2 Data Model`.
   Numbers are never reused for a different topic, even if a section is removed —
   mark it `## 3.2 (removed — see 6.1)` instead of deleting the number.
2. Agents (impact-analysis, spec-updater) reference sections **only by number**,
   never by title text, since titles may be edited for clarity.
3. spec_updater.py only emits patches scoped to sections listed in the impact
   report's `spec_sections_affected`. Any edit outside that list is rejected
   by the aggregator and flagged for human review instead of applied.
4. New sections get the next number under the correct top-level heading
   (e.g. a new data-model subsection becomes `3.3`, not inserted as `3.2.5`).
5. Each section should be self-contained enough to read in isolation —
   avoid "as mentioned above" references across sections; cross-reference
   by number instead (`see 4.1`).

## Minimal skeleton

```markdown
# Architecture Spec

## 1. Overview
## 2. Components
## 3. Data Model
### 3.1 ...
### 3.2 ...
## 4. Flows
### 4.1 ...
## 5. Non-functional constraints
## 6. Deprecated / removed
```

This convention is what keeps step 3 of the loop (spec patching) mechanical
and safe enough to auto-apply instead of always requiring human judgment.
