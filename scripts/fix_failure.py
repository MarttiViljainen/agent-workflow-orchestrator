"""
Stage 5: bounded, token-budgeted repair loop for a failing test run.

Two independent stop conditions, whichever trips first:
  * --max-attempts  — how many times we're willing to ask
  * --max-tokens    — total tokens across ALL attempts combined, not per call

The budget is checked BEFORE each API call, so an exhausted budget costs
nothing. To stop a single large call from blowing far past the ceiling, the
request's max_tokens is also clamped to whatever budget remains.

All state lives in one JSON file (--state) so the shell loop driving this
stays trivial and every attempt is auditable from the PR comment.

Modes:
    # propose a fix (exit 0 = wrote files, 2 = budget spent, 3 = no fix offered)
    python fix_failure.py --failure-log pytest.log --diff diff.patch \
        --state fix_attempt_state.json --attempt 1 \
        --max-attempts 2 --max-tokens 20000 [--base-dir .]

    # tell the state file how the re-run went
    python fix_failure.py --state fix_attempt_state.json \
        --record-test-result pass|fail [--branch my-pr-branch]

    # close out the log once the loop ends
    python fix_failure.py --state fix_attempt_state.json --finalize
"""
import argparse
import json
import os
import re
import sys

import anthropic

from git_publish import commit_and_push
from safe_write import write_model_file

# exit codes the shell loop branches on
EXIT_OK = 0
EXIT_BUDGET_SPENT = 2
EXIT_NO_FIX = 3

MODEL = "claude-sonnet-4-6"
# Smallest reply worth paying for. If the remaining budget cannot cover the
# input plus this much output, we stop rather than buy a truncated answer.
MIN_USEFUL_OUTPUT_TOKENS = 512
MAX_OUTPUT_TOKENS = 4000
# chars-per-token for the fallback estimator; deliberately low (code tokenises
# denser than prose) so the guess errs towards over-counting
FALLBACK_CHARS_PER_TOKEN = 3

SYSTEM_PROMPT = """You repair a failing test run. You are given the pytest failure
output and the diff currently under review.

Rules:
- Fix the cause of the failure. Never weaken, delete, skip or xfail a test to
  make it pass, and never loosen an assertion.
- Prefer the smallest change that addresses the reported failure. Do not
  refactor, reformat, or "improve" code that is not implicated.
- If the failure looks like a genuine conflict between the change and an
  existing expectation, do NOT guess: output no files at all and explain the
  conflict, so a human decides.

Reply format — one summary line, then each changed file in full:

SUMMARY: <one sentence describing what you changed and why>

===FILE path/to/file.py===
<full file content>
===END===
"""

BLOCK_RE = re.compile(r"===FILE (.+?)===\n(.*?)\n===END===", re.DOTALL)
SUMMARY_RE = re.compile(r"^SUMMARY:\s*(.+)$", re.MULTILINE)


def load_state(path, max_attempts=None, max_tokens=None):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {
        "max_attempts": max_attempts,
        "max_tokens": max_tokens,
        "tokens_used": 0,
        "attempts": [],
        "files_written": [],
        "stop_reason": None,
        "message": "",
    }


def save_state(path, state):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def stop(state, path, reason, message):
    state["stop_reason"] = reason
    state["message"] = message
    save_state(path, state)
    print(message)


def summarise(raw):
    m = SUMMARY_RE.search(raw)
    if m:
        return m.group(1).strip()
    stripped = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    return (stripped[0][:200] if stripped else "(no summary given)")


def estimate_input_tokens(client, system, messages):
    """Cost of the prompt BEFORE sending it.

    count_tokens is the real tokeniser and is not itself billed, so this is
    exact in normal operation. The fallback only matters on an SDK too old to
    expose it, and deliberately over-counts so a bad guess stops the loop
    early rather than overspending.
    """
    try:
        return client.messages.count_tokens(
            model=MODEL, system=system, messages=messages
        ).input_tokens
    except Exception as exc:
        text = system + "".join(m["content"] for m in messages)
        approx = len(text) // FALLBACK_CHARS_PER_TOKEN
        print(f"count_tokens unavailable ({type(exc).__name__}); estimating "
              f"{approx} input tokens from {len(text)} chars")
        return approx


def propose_fix(args, state):
    """One repair attempt. Returns the process exit code."""
    used, budget = state["tokens_used"], args.max_tokens
    remaining = budget - used

    if remaining <= 0:
        stop(state, args.state, "token-budget-exhausted",
             f"Token budget exhausted ({used}/{budget} used), stopping without "
             f"calling the API. Completed {len(state['attempts'])} attempt(s).")
        return EXIT_BUDGET_SPENT

    failure_log = open(args.failure_log, encoding="utf-8", errors="replace").read()
    diff_text = open(args.diff, encoding="utf-8", errors="replace").read()
    messages = [{
        "role": "user",
        "content": (
            f"## Failing test output\n{failure_log}\n\n"
            f"## Diff under review\n{diff_text}"
        ),
    }]

    client = anthropic.Anthropic()

    # The prompt carries the whole failure log and diff and is re-sent in full
    # on every attempt, so it usually dwarfs the reply. Budgeting on output
    # alone is what let earlier runs sail past the cap — price the input first.
    est_input = estimate_input_tokens(client, SYSTEM_PROMPT, messages)
    affordable_output = remaining - est_input

    # No MIN floor override here: if what's left cannot buy a useful reply,
    # stop. Forcing a minimum-size call is precisely how the cap got breached.
    if affordable_output < MIN_USEFUL_OUTPUT_TOKENS:
        stop(state, args.state, "token-budget-exhausted",
             f"Token budget exhausted: {used}/{budget} used, {remaining} left, "
             f"but the next call needs ~{est_input} input + at least "
             f"{MIN_USEFUL_OUTPUT_TOKENS} output tokens. Stopping without "
             f"calling the API after {len(state['attempts'])} attempt(s).")
        return EXIT_BUDGET_SPENT

    max_output = min(MAX_OUTPUT_TOKENS, affordable_output)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=max_output,
        system=SYSTEM_PROMPT,
        messages=messages,
    )
    raw = "".join(b.text for b in resp.content if b.type == "text")

    spent = resp.usage.input_tokens + resp.usage.output_tokens
    state["tokens_used"] = used + spent

    summary = summarise(raw)
    written = [
        write_model_file(m.group(1).strip(), m.group(2), args.base_dir)
        for m in BLOCK_RE.finditer(raw)
    ]
    for path in written:
        if path not in state["files_written"]:
            state["files_written"].append(path)

    state["attempts"].append({
        "attempt": args.attempt,
        "estimated_input_tokens": est_input,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        "tokens_this_attempt": spent,
        "cumulative_tokens": state["tokens_used"],
        "summary": summary,
        "files": written,
        "result": "pending",
    })
    save_state(args.state, state)
    print(f"Attempt {args.attempt}: {spent} tokens "
          f"({state['tokens_used']}/{budget} cumulative) — {summary}")

    if not written:
        stop(state, args.state, "no-fix-proposed",
             f"Model proposed no file changes on attempt {args.attempt}: {summary}")
        return EXIT_NO_FIX

    print(f"Wrote: {written}")
    return EXIT_OK


def record_result(args, state):
    if not state["attempts"]:
        return EXIT_OK
    state["attempts"][-1]["result"] = args.record_test_result

    if args.record_test_result == "pass":
        n = len(state["attempts"])
        state["stop_reason"] = "fixed"
        state["message"] = (f"Tests pass after {n} fix attempt(s), "
                            f"{state['tokens_used']}/{state['max_tokens']} tokens.")
        save_state(args.state, state)
        # the state file rides along as the audit trail for the fix
        commit_and_push(
            state["files_written"] + [args.state],
            f"agent-loop: fix failing tests ({n} attempt(s), "
            f"{state['tokens_used']} tokens)",
            branch=args.branch,
        )
    else:
        save_state(args.state, state)
    return EXIT_OK


def finalize(args, state):
    if state["stop_reason"] is None:
        n = len(state["attempts"])
        state["stop_reason"] = "max-attempts-reached"
        state["message"] = (f"Stopped: max attempts reached ({n}), "
                            f"{state['tokens_used']}/{state['max_tokens']} tokens used.")
    save_state(args.state, state)
    print(state["message"])
    return EXIT_OK


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--state", required=True)
    p.add_argument("--failure-log")
    p.add_argument("--diff")
    p.add_argument("--attempt", type=int, default=1)
    p.add_argument("--max-attempts", type=int, default=2)
    p.add_argument("--max-tokens", type=int, default=20000,
                   help="total budget across ALL attempts, not per attempt")
    p.add_argument("--base-dir", default=".",
                   help="model-proposed writes are confined to this directory")
    p.add_argument("--branch", default=None,
                   help="PR head branch to push to; defaults to $GITHUB_HEAD_REF")
    p.add_argument("--record-test-result", choices=("pass", "fail"))
    p.add_argument("--finalize", action="store_true")
    args = p.parse_args()

    state = load_state(args.state, args.max_attempts, args.max_tokens)

    if args.record_test_result:
        return record_result(args, state)
    if args.finalize:
        return finalize(args, state)

    if not (args.failure_log and args.diff):
        p.error("--failure-log and --diff are required when proposing a fix")
    return propose_fix(args, state)


if __name__ == "__main__":
    sys.exit(main())
