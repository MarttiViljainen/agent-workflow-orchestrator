"""
The token budget in fix_failure.py is a spend cap, so the property that matters
is arithmetic, not behavioural: cumulative usage must never meaningfully exceed
--max-tokens, whatever the input size or attempt count.

Regression origin: a real run spent 26,711 tokens against a 20,000 cap. The
pre-call gate only asked "have we already spent the budget?", so a final
attempt whose prompt alone cost ~9k was waved through, and the MIN floor meant
a call was forced even when almost nothing remained.
"""
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

# Cumulative spend may exceed the cap only by input-estimation error. With the
# real API count_tokens is exact and this is 0; the allowance covers the
# conservative fallback estimator and any tokeniser drift.
TOLERANCE = 0.05


class FakeUsage:
    def __init__(self, input_tokens, output_tokens):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeMessages:
    """Worst-case client: every reply consumes the entire output allowance."""

    def __init__(self, input_tokens, estimate_error=0.0, support_count=True):
        self.actual_input = input_tokens
        self.estimate_error = estimate_error
        self.support_count = support_count
        self.calls = 0

    def count_tokens(self, **kw):
        if not self.support_count:
            raise AttributeError("count_tokens not available on this SDK")
        # what the estimator sees may drift slightly from what we get billed
        return SimpleNamespace(
            input_tokens=int(self.actual_input * (1 - self.estimate_error))
        )

    def create(self, max_tokens, **kw):
        self.calls += 1
        block = SimpleNamespace(
            type="text",
            text="SUMMARY: try a fix\n\n===FILE app/x.py===\nx = 1\n===END===",
        )
        return SimpleNamespace(
            content=[block],
            usage=FakeUsage(self.actual_input, max_tokens),
        )


@pytest.fixture
def fix_failure(monkeypatch):
    fake_sdk = types.ModuleType("anthropic")
    fake_sdk.Anthropic = lambda *a, **k: SimpleNamespace(messages=fake_sdk._messages)
    monkeypatch.setitem(sys.modules, "anthropic", fake_sdk)
    for mod in ("fix_failure", "git_publish", "safe_write"):
        monkeypatch.delitem(sys.modules, mod, raising=False)
    import fix_failure as mod
    mod._sdk = fake_sdk
    return mod


def drive_loop(mod, tmp_path, budget, max_attempts, input_tokens,
               estimate_error=0.0, support_count=True):
    """Run the retry loop the way the workflow's bash loop does."""
    mod._sdk._messages = FakeMessages(input_tokens, estimate_error, support_count)

    # Size the prompt so a char-counting estimator would arrive at roughly the
    # same figure the fake bills. Without this the fallback path is measured
    # against a stub that disagrees with itself, not against the code.
    target_chars = input_tokens * mod.FALLBACK_CHARS_PER_TOKEN
    diff = tmp_path / "diff.patch"
    diff.write_text("+ changed line\n" * 20, encoding="utf-8")
    log = tmp_path / "pytest.log"
    log.write_text(
        "E   assert 1 == 2\n" * max(
            1, (target_chars - len(mod.SYSTEM_PROMPT) - len(diff.read_text())) // 18
        ),
        encoding="utf-8",
    )
    state_path = tmp_path / "state.json"

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        state = mod.load_state(str(state_path), max_attempts, budget)
        for n in range(1, max_attempts + 1):
            args = SimpleNamespace(
                state=str(state_path), failure_log=str(log), diff=str(diff),
                attempt=n, max_attempts=max_attempts, max_tokens=budget,
                base_dir=".", branch=None,
            )
            if mod.propose_fix(args, state) != mod.EXIT_OK:
                break
    finally:
        os.chdir(cwd)
    return state, mod._sdk._messages


# (budget, attempts, input_tokens_per_call)
SCENARIOS = [
    (20_000, 2, 9_500),    # the reported regression: input dominates
    (20_000, 5, 9_500),    # more attempts than the budget can fund
    (20_000, 2, 25_000),   # a single prompt exceeds the whole budget
    (20_000, 3, 6_000),
    (5_000, 4, 1_000),
    (2_000, 2, 1_800),     # only a sliver left after one call
    (100_000, 6, 8_000),
]


@pytest.mark.parametrize("budget,attempts,input_tokens", SCENARIOS)
def test_never_exceeds_budget(fix_failure, tmp_path, budget, attempts, input_tokens):
    state, _ = drive_loop(fix_failure, tmp_path, budget, attempts, input_tokens)
    ceiling = budget * (1 + TOLERANCE)
    assert state["tokens_used"] <= ceiling, (
        f"spent {state['tokens_used']} against a {budget} cap "
        f"(tolerance {TOLERANCE:.0%} -> {ceiling:.0f})"
    )


@pytest.mark.parametrize("budget,attempts,input_tokens", SCENARIOS)
def test_never_exceeds_budget_with_estimator_drift(
    fix_failure, tmp_path, budget, attempts, input_tokens
):
    """Billed input running 3% above the estimate must stay inside tolerance."""
    state, _ = drive_loop(fix_failure, tmp_path, budget, attempts, input_tokens,
                          estimate_error=0.03)
    assert state["tokens_used"] <= budget * (1 + TOLERANCE)


def test_reported_regression_stays_under_cap(fix_failure, tmp_path):
    """The exact shape that spent 26,711 against 20,000."""
    state, _ = drive_loop(fix_failure, tmp_path, budget=20_000, max_attempts=2,
                          input_tokens=9_500)
    assert state["tokens_used"] <= 20_000
    assert state["tokens_used"] < 26_711


def test_prompt_larger_than_budget_never_calls_api(fix_failure, tmp_path):
    """No attempt at all, rather than one unaffordable one."""
    state, client = drive_loop(fix_failure, tmp_path, budget=5_000,
                               max_attempts=3, input_tokens=8_000)
    assert client.calls == 0
    assert state["tokens_used"] == 0
    assert state["stop_reason"] == "token-budget-exhausted"


def test_min_output_floor_does_not_force_an_unaffordable_call(fix_failure, tmp_path):
    """Candidate (1): the MIN floor must not override the remaining check.

    Budget leaves less headroom than MIN_USEFUL_OUTPUT_TOKENS after the input
    is priced, so the loop must stop instead of requesting the floor anyway.
    """
    budget = 10_000
    input_tokens = budget - fix_failure.MIN_USEFUL_OUTPUT_TOKENS + 1
    state, client = drive_loop(fix_failure, tmp_path, budget=budget,
                               max_attempts=2, input_tokens=input_tokens)
    assert client.calls == 0
    assert state["tokens_used"] == 0


def test_stops_before_the_attempt_that_would_overshoot(fix_failure, tmp_path):
    """Budget funds exactly one call; the second must be refused, not truncated."""
    state, client = drive_loop(fix_failure, tmp_path, budget=12_000,
                               max_attempts=4, input_tokens=7_000)
    assert client.calls == 1
    assert state["stop_reason"] == "token-budget-exhausted"
    assert state["tokens_used"] <= 12_000


def test_fallback_estimator_also_respects_budget(fix_failure, tmp_path):
    """An SDK without count_tokens must still not overshoot."""
    state, _ = drive_loop(fix_failure, tmp_path, budget=20_000, max_attempts=4,
                          input_tokens=9_500, support_count=False)
    assert state["tokens_used"] <= 20_000 * (1 + TOLERANCE)


def test_budget_is_cumulative_not_per_attempt(fix_failure, tmp_path):
    """Several affordable attempts must still sum to within the cap."""
    state, client = drive_loop(fix_failure, tmp_path, budget=30_000,
                               max_attempts=6, input_tokens=2_000)
    assert client.calls > 1, "scenario should fund more than one attempt"
    assert state["tokens_used"] <= 30_000
    assert state["attempts"][-1]["cumulative_tokens"] == state["tokens_used"]
