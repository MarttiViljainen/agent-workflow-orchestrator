"""
Stage 2 of the loop: given a diff + current spec, ask Claude for a
structured ImpactReport (YAML only, no prose) and validate it against
schema.py before anything downstream touches it.

Usage:
    python impact_analysis.py --diff diff.patch --spec docs/architecture.md \
        --out impact_report.yaml
"""
import argparse
import os
import sys
import anthropic
from schema import ImpactReport

SYSTEM_PROMPT = """You are an impact-analysis agent for a software change.
You will be given a unified diff and the current architecture spec (with
numbered section anchors). Respond with ONLY valid YAML matching this schema,
no prose, no markdown fences:

components_touched: [list of module/component names touched]
spec_sections_affected: [list of section numbers as strings, e.g. "3.2"]
tests_to_add: [short descriptions of new test cases needed]
tests_to_modify: [short descriptions of existing tests that need updating]
risk_tier: trivial | moderate | needs-deep-review
confidence: one short sentence explaining your confidence level
open_questions: [anything you are not sure about — leave empty list if none]

risk_tier guide:
- trivial: docs/comments/formatting only, no logic change
- moderate: isolated logic change, clear blast radius, existing patterns followed
- needs-deep-review: touches shared state, data model, auth, external calls,
  or anything the diff itself doesn't make obviously safe
"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--diff", required=True)
    p.add_argument("--spec", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    diff_text = open(args.diff).read()
    spec_text = open(args.spec).read() if os.path.exists(args.spec) else "(no spec yet)"

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"## Diff\n{diff_text}\n\n## Current spec\n{spec_text}",
        }],
    )
    raw = "".join(b.text for b in resp.content if b.type == "text").strip()
    raw = raw.replace("```yaml", "").replace("```", "").strip()

    report = ImpactReport.from_yaml(raw)
    problems = report.validate()
    if problems:
        print("Impact report failed validation:", problems, file=sys.stderr)
        # Fail safe: force deep review rather than silently proceeding
        report.risk_tier = "needs-deep-review"
        report.open_questions.append("Schema validation failed: " + "; ".join(problems))

    with open(args.out, "w") as f:
        f.write(report.to_yaml())
    print(f"Impact report written to {args.out} (risk_tier={report.risk_tier})")


if __name__ == "__main__":
    main()
