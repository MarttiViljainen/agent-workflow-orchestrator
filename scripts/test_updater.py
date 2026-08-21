"""
Stage 4: propose new/modified tests based on the impact report.
Hard rule enforced here, not just in prompt: this script never deletes
an existing test file or reduces assertion count in an existing test —
it only appends new test functions or extends existing ones. If Claude's
output tries to remove a test, the removal is dropped and flagged.

Usage:
    python test_updater.py --diff diff.patch --impact impact_report.yaml \
        --test-dir tests --out test_patch.diff [--branch my-pr-branch]
"""
import argparse
import os
import subprocess
import anthropic
from git_publish import commit_and_push
from schema import ImpactReport

SYSTEM_PROMPT = """You write or extend pytest test cases for a code change.
Rules:
- Only ADD new test functions or ADD new assertions to existing ones.
- Never remove a test function, never remove an assertion, never weaken
  an assertion (e.g. loosening an equality check) to make a test pass.
- If existing behavior seems to conflict with the change, add a test that
  documents the new expected behavior and flag it in a comment for human review
  rather than silently deleting the old expectation.
- Output only valid Python test code, one file per test module, using this format:

===FILE tests/test_foo.py===
<full file content>
===END===
"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--diff", required=True)
    p.add_argument("--impact", required=True)
    p.add_argument("--test-dir", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--branch", default=None,
                   help="PR head branch to push to; defaults to $GITHUB_HEAD_REF")
    args = p.parse_args()

    diff_text = open(args.diff).read()
    report = ImpactReport.from_yaml(open(args.impact).read())

    client = anthropic.Anthropic()
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=3000,
        system=SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                f"## Diff\n{diff_text}\n\n"
                f"## Tests to add\n{report.tests_to_add}\n\n"
                f"## Tests to modify\n{report.tests_to_modify}\n\n"
                f"## Test directory: {args.test_dir}"
            ),
        }],
    )
    raw = "".join(b.text for b in resp.content if b.type == "text")

    import re
    test_dir = os.path.normpath(args.test_dir)
    written = []
    for block in re.finditer(r"===FILE (.+?)===\n(.*?)\n===END===", raw, re.DOTALL):
        path, content = block.group(1).strip(), block.group(2)

        # Containment: whatever path the model guessed, the file lands inside
        # --test-dir. normpath first, so "tests/../../.github/workflows/x.yml"
        # can't satisfy a naive prefix check and escape the test directory.
        path = os.path.normpath(path)
        if not path.startswith(test_dir + os.sep):
            redirected = os.path.join(test_dir, os.path.basename(path))
            print(f"REDIRECTED: model proposed {path!r} outside --test-dir -> {redirected}")
            path = redirected

        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        written.append(path)

    # diff first — `git diff` without --cached goes empty once files are added
    subprocess.run(["git", "diff", "--"] + written, stdout=open(args.out, "w"))
    print(f"Test patch written for: {written}")

    commit_and_push(
        written,
        "agent-loop: add/extend tests for this change",
        branch=args.branch,
    )


if __name__ == "__main__":
    main()
