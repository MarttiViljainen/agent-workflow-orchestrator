"""
Fixed schema for the impact report — the contract between
impact_analysis.py, spec_updater.py, test_updater.py, and aggregate_comment.py.

Keeping this a small, explicit dict/dataclass (rather than letting each
stage improvise from prose) is what makes the pipeline composable and
lets you eventually mine risk_tier / confidence against actual outcomes.
"""
from dataclasses import dataclass, field, asdict
from typing import List
import yaml


@dataclass
class ImpactReport:
    components_touched: List[str] = field(default_factory=list)
    spec_sections_affected: List[str] = field(default_factory=list)   # e.g. ["3.2", "4.1"]
    tests_to_add: List[str] = field(default_factory=list)             # short descriptions
    tests_to_modify: List[str] = field(default_factory=list)
    risk_tier: str = "moderate"                                       # trivial | moderate | needs-deep-review
    confidence: str = ""                                              # short human-readable rationale
    open_questions: List[str] = field(default_factory=list)

    def to_yaml(self) -> str:
        return yaml.safe_dump(asdict(self), sort_keys=False)

    @staticmethod
    def from_yaml(text: str) -> "ImpactReport":
        data = yaml.safe_load(text) or {}
        return ImpactReport(**{k: data.get(k, default) for k, default in {
            "components_touched": [], "spec_sections_affected": [],
            "tests_to_add": [], "tests_to_modify": [], "risk_tier": "moderate",
            "confidence": "", "open_questions": [],
        }.items()})

    def validate(self) -> List[str]:
        problems = []
        if self.risk_tier not in ("trivial", "moderate", "needs-deep-review"):
            problems.append(f"invalid risk_tier: {self.risk_tier}")
        for s in self.spec_sections_affected:
            if not s[0].isdigit():
                problems.append(f"spec section not numeric-anchored: {s}")
        return problems
