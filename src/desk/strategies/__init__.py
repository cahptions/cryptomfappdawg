"""Pure SHADOW hypothesis evaluation; no execution, wallet, or network imports."""

from desk.strategies.evaluate import evaluate_decision
from desk.strategies.specs import builtin_specs, revise_spec

__all__ = ["builtin_specs", "evaluate_decision", "revise_spec"]
