"""
Stage 6 helper: build ONE PR comment from impact report + spec diff +
test diff + CI result, so the human accept step is one screen, not four.

Usage:
    python aggregate_comment.py --impact impact_report.yaml \
        --spec-diff spec_patch.diff --test-diff test_patch.diff \
        --ci-result pass|fail --out comment.md
"""
import argparse
from schema import ImpactReport


def read_or_empty(path):
    try:
        return open(path).read().strip()
    except FileNotFoundError:
        return ""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--impact", required=True)
    p.add_argument("--spec-diff", required=True)
    p.add_argument("--test-diff", required=True)
    p.add_argument("--ci-result", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    report = ImpactReport.from_yaml(open(args.impact).read())
    spec_diff = read_or_empty(args.spec_diff)
    test_diff = read_or_empty(args.test_diff)

    badge = {"pass": "✅ CI passed", "fail": "❌ CI failed"}[args.ci_result]
    tier_badge = {"trivial": "🟢 trivial", "moderate": "🟡 moderate",
                  "needs-deep-review": "🔴 needs-deep-review"}[report.risk_tier]

    lines = [
        f"## Agent change loop — {tier_badge} — {badge}",
        "",
        f"**Confidence:** {report.confidence or '(none stated)'}",
        f"**Components touched:** {', '.join(report.components_touched) or '—'}",
        f"**Spec sections affected:** {', '.join(report.spec_sections_affected) or '—'}",
    ]
    if report.open_questions:
        lines += ["", "**Open questions:**"] + [f"- {q}" for q in report.open_questions]

    lines += ["", "### Spec changes"]
    lines += ["```diff\n" + spec_diff + "\n```"] if spec_diff else ["_no spec changes_"]

    lines += ["", "### Test changes"]
    lines += ["```diff\n" + test_diff + "\n```"] if test_diff else ["_no test changes_"]

    with open(args.out, "w") as f:
        f.write("\n".join(lines))
    print(f"Comment written to {args.out}")


if __name__ == "__main__":
    main()
