# TODO: step 1: feature selection. Corresponds to run_selecting_features.py in the original codebase.

from src.tracerca.algorithm.baseline import BaselineModel
from src.tracerca.models.trace import Invocation, ServicePair


def select_features(
        window_invocations: list[Invocation], 
        baseline: BaselineModel, 
        threshold: float = 1.0
    ) -> dict[ServicePair, list[str]]:
    pass