# TODO: step 1: feature selection. Corresponds to run_selecting_features.py in the original codebase.

from collections import defaultdict

from src.tracerca.algorithm.baseline import BaselineModel
from src.tracerca.models.trace import Invocation, ServicePair


def select_features(
        window_invocations: list[Invocation], 
        baseline: BaselineModel, 
        threshold: float = 1.0
    ) -> dict[ServicePair, list[str]]:
    
    # group window_invocations by service pair
    pair_to_invocations: dict[ServicePair, list[Invocation]] = defaultdict(list)

    for inv in window_invocations:
        pair_to_invocations[inv.service_pair].append(inv)

    # for each pair in baseline, for each feature in