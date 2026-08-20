"""
Stage 3: patch ONLY the spec sections named in the impact report.
Refuses to touch anything else — if Claude's proposed patch includes edits
outside the allowed section numbers, those edits are dropped and flagged,
not applied.

Usage:
    python spec_updater.py --diff diff.patch --spec docs/architecture.md \
        --impact impact_report.yaml --out spec_patch.diff [--branch my-pr-branch]
"""
import argparse
import re
import anthropic
from git_publish import commit_and_push
from schema import ImpactReport

SYSTEM_PROMPT = """You update ONE OR MORE numbered sections of an architecture spec
to reflect a code change. You will be given: the diff, the full current spec,
and the list of section numbers you are allowed to touch.

Rules:
- Only propose new text for the listed section numbers. Do not touch any other section.
- Keep the section heading format identical (e.g. "## 3.2 Data Model").
- Output ONLY the full replacement text for each allowed section, in this format,
  no other prose:

===SECTION 3.2===
<full new text of section 3.2, heading included>
===END===
"""


def extract_section(spec_text: str, number: str) -> str:
    pattern = rf"(^#{{1,3}} {re.escape(number)} .*?)(?=^#{{1,3}} \d|\Z)"
    m = re.search(pattern, spec_text, re.MULTILINE | re.DOTALL)
    return m.group(1) if m else ""


def apply_section_replacement(spec_text: str, number: str, new_text: str) -> str:
    pattern = rf"(^#{{1,3}} {re.escape(number)} .*?)(?=^#{{1,3}} \d|\Z)"
    return re.sub(pattern, new_text.rstrip() + "\n\n", spec_text, flags=re.MULTILINE | re.DOTALL)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--diff", required=True)
    p.add_argument("--spec", required=True)
    p.add_argument("--impact", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--branch", default=None,
                   help="PR head branch to push to; defaults to $GITHUB_HEAD_REF")
    args = p.parse_args()

    diff_text = open(args.diff).read()
    spec_text = open(args.spec).read()
    report = ImpactReport.from_yaml(open(args.impact).read())

    if not report.spec_sections_affected:
        print("No spec sections flagged as affected; skipping spec update.")
        open(args.out, "w").write("")
        return

    allowed = report.spec_sections_affected
    excerpt = "\n\n".join(extract_section(spec_text, n) for n in allowed)

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": f"Allowed sections: {allowed}\n\n## Diff\n{diff_text}\n\n## Current text of allowed sections\n{excerpt}",
        }],
    )
    raw = "".join(b.text for b in resp.content if b.type == "text")

    updated_spec = spec_text
    for block in re.finditer(r"===SECTION ([\d.]+)===\n(.*?)\n===END===", raw, re.DOTALL):
        number, new_text = block.group(1), block.group(2)
        if number not in allowed:
            print(f"REJECTED: model tried to patch disallowed section {number}")
            continue
        updated_spec = apply_section_replacement(updated_spec, number, new_text)

    with open(args.spec, "w") as f:
        f.write(updated_spec)

    # emit a diff for the PR comment / review — must run before staging,
    # since `git diff` without --cached goes empty once the file is added
    import subprocess
    subprocess.run(["git", "diff", "--", args.spec], stdout=open(args.out, "w"))
    print(f"Spec patch written, diff saved to {args.out}")

    commit_and_push(
        [args.spec],
        "agent-loop: update spec sections affected by this change",
        branch=args.branch,
    )


if __name__ == "__main__":
    main()
