# agent-workflow-orchestrator

Reusable Claude-Code-driven change loop: **impact analysis → spec patch → test patch → CI → bounded fix retry → single-screen human accept.**

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

### Inputs

| input | required | default | meaning |
|---|---|---|---|
| `spec_path` | yes | — | the numbered-section spec doc to patch |
| `test_command` | yes | — | how to run the suite |
| `test_dir` | no | `tests` | agent-written tests are confined to this directory |
| `diff_base` | yes | — | commit the diff is taken against |
| `max_fix_attempts` | no | `2` | repair attempts after a failing run |
| `max_fix_tokens` | no | `20000` | token budget across **all** fix attempts combined |

`max_fix_attempts` and `max_fix_tokens` are independent ceilings — the retry loop
stops at whichever trips first, and the PR comment says which one it was, so
"gave up" and "ran out of budget" are never confused. The budget is checked
*before* each call, so an exhausted budget costs nothing; note that the attempt
which crosses the line still completes, so final usage can exceed the ceiling by
up to one request.

That's the entire integration surface. Everything else (impact analysis prompt,
schema, spec-patch logic, test-patch logic, PR comment aggregation) lives here
and is versioned by the `@main` (or pin to a tag once stable).

## Contents

- `.github/workflows/agent-loop.yml` — the reusable workflow (`workflow_call`)
- `scripts/impact_analysis.py` — calls Claude, produces structured impact report (YAML schema)
- `scripts/spec_updater.py` — patches only the named sections of the target spec doc
- `scripts/test_updater.py` — proposes test additions/edits, never deletes failing tests
- `scripts/fix_failure.py` — bounded, token-budgeted repair loop for a failing run; owns all attempt state
- `scripts/safe_write.py` — confines model-proposed file paths to a base directory
- `scripts/git_publish.py` — commits and pushes agent output back to the PR branch
- `scripts/aggregate_comment.py` — builds the single PR comment: diff summary + impact + spec diff + test diff + fix attempts + CI result
- `templates/CLAUDE.md.template` — starting guardrails doc for any product repo using this loop
- `templates/review-checklist.md.template` — human accept checklist
- `docs/spec-convention.md` — the numbered-section-anchor rule specs must follow so patching stays safe

## Design principles (see docs/spec-convention.md and CLAUDE.md.template for the "why")

1. Agents never rewrite whole files — only named, anchored sections.
2. Impact report is a fixed schema, not prose — it's the contract between stages.
3. Test agent adds/modifies; it never deletes or weakens a failing test to pass CI.
4. CI pass/fail is the only hard gate — no agent self-certifies.
5. Human sees one aggregated comment, not four scattered outputs.
6. Repair is bounded twice over (attempts *and* tokens), and every attempt is
   logged whether it worked or not — an agent that fails expensively should be
   as visible as one that fails cheaply.
7. The model never chooses where a file lands; every proposed path is confined
   to a base directory before it is written.
