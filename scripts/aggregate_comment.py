"""
Stage 6 helper: build ONE PR comment from impact report + spec diff +
test diff + CI result, so the human accept step is one screen, not four.

Usage:
    python aggregate_comment.py --impact impact_report.yaml \
        --spec-diff spec_patch.diff --test-diff test_patch.diff \
        --ci-result pass|fail --out comment.md [--fix-log fix_attempt_state.json]
"""
import argparse
import json
from schema import ImpactReport

# why the fix loop stopped — the distinction matters to the reviewer, since
# "budget exhausted" means unfinished work whereas "max attempts" means the
# model had its chances
STOP_LABELS = {
    "fixed": "✅ fixed",
    "token-budget-exhausted": "⛔ stopped: token budget exhausted",
    "max-attempts-reached": "⛔ stopped: max attempts reached",
    "no-fix-proposed": "⛔ stopped: model proposed no change",
}


def read_or_empty(path):
    try:
        return open(path, encoding="utf-8").read().strip()
    except FileNotFoundError:
        return ""


def read_json_or_none(path):
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None


def fix_section(state):
    """Render the per-attempt log. Empty list if the loop never ran."""
    if not state:
        return []

    attempts = state.get("attempts", [])
    used = state.get("tokens_used", 0)
    budget = state.get("max_tokens")
    reason = state.get("stop_reason")
    label = STOP_LABELS.get(reason, f"stopped: {reason}")

    lines = ["", "### Fix attempts"]
    if attempts:
        lines += [
            "| # | tokens (attempt) | cumulative | tests | what was tried |",
            "|---|---|---|---|---|",
        ]
        for a in attempts:
            mark = {"pass": "✅ pass", "fail": "❌ fail"}.get(a.get("result"), "—")
            lines.append(
                f"| {a['attempt']} | {a['tokens_this_attempt']:,} | "
                f"{a['cumulative_tokens']:,} | {mark} | {a['summary']} |"
            )
    else:
        lines.append("_no attempt reached the API_")

    budget_note = f" — {used:,} / {budget:,} tokens" if budget else f" — {used:,} tokens"
    lines += ["", f"**Outcome:** {label} after {len(attempts)} attempt(s){budget_note}"]
    if state.get("message"):
        lines.append(f"> {state['message']}")
    return lines


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--impact", required=True)
    p.add_argument("--spec-diff", required=True)
    p.add_argument("--test-diff", required=True)
    p.add_argument("--ci-result", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--fix-log", default=None,
                   help="fix_attempt_state.json; omitted or absent if no fix loop ran")
    args = p.parse_args()

    report = ImpactReport.from_yaml(open(args.impact, encoding="utf-8").read())
    spec_diff = read_or_empty(args.spec_diff)
    test_diff = read_or_empty(args.test_diff)
    fix_state = read_json_or_none(args.fix_log)

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

    lines += fix_section(fix_state)

    # explicit utf-8: the badges below are non-ASCII and the default encoding
    # is platform-dependent
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Comment written to {args.out}")


if __name__ == "__main__":
    main()
