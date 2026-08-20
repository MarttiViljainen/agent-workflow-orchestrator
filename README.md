# agent-workflow-orchestrator

Reusable Claude-Code-driven change loop: **impact analysis → spec patch → test patch → CI → single-screen human accept.**

This repo contains no product code. Product repos call it as a
[reusable GitHub Actions workflow](https://docs.github.com/en/actions/using-workflows/reusing-workflows)
and supply their own paths, spec doc, and risk thresholds as inputs.
Nothing about your product logic, spec content, or tests needs to leave your private repo —
only the diff and metadata you choose to pass as inputs ever reach this workflow at runtime,
and it runs in *your* repo's Actions context, using *your* ANTHROPIC_API_KEY secret.

## How a product repo uses this

In the product repo, add `.github/workflows/agent-loop.yml`:

```yaml
name: Agent Change Loop
on:
  pull_request:
    types: [opened, synchronize, reopened]

jobs:
  agent-loop:
    uses: MarttiViljainen/agent-workflow-orchestrator/.github/workflows/agent-loop.yml@main
    with:
      spec_path: docs/architecture.md
      test_command: "pytest -q"
      diff_base: ${{ github.event.pull_request.base.sha }}
    secrets:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
```

That's the entire integration surface. Everything else (impact analysis prompt,
schema, spec-patch logic, test-patch logic, PR comment aggregation) lives here
and is versioned by the `@main` (or pin to a tag once stable).

## Contents

- `.github/workflows/agent-loop.yml` — the reusable workflow (`workflow_call`)
- `scripts/impact_analysis.py` — calls Claude, produces structured impact report (YAML schema)
- `scripts/spec_updater.py` — patches only the named sections of the target spec doc
- `scripts/test_updater.py` — proposes test additions/edits, never deletes failing tests
- `scripts/aggregate_comment.py` — builds the single PR comment: diff summary + impact + spec diff + test diff + CI result
- `templates/CLAUDE.md.template` — starting guardrails doc for any product repo using this loop
- `templates/review-checklist.md.template` — human accept checklist
- `docs/spec-convention.md` — the numbered-section-anchor rule specs must follow so patching stays safe

## Design principles (see docs/spec-convention.md and CLAUDE.md.template for the "why")

1. Agents never rewrite whole files — only named, anchored sections.
2. Impact report is a fixed schema, not prose — it's the contract between stages.
3. Test agent adds/modifies; it never deletes or weakens a failing test to pass CI.
4. CI pass/fail is the only hard gate — no agent self-certifies.
5. Human sees one aggregated comment, not four scattered outputs.
