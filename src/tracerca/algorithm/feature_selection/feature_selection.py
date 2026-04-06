# TODO: step 1: feature selection. Corresponds to run_selecting_features.py in the original codebase.

from collections import defaultdict

from tracerca.algorithm.baseline import BaselineModel
from tracerca.models.trace import Invocation, ServicePair
from tracerca.algorithm.feature_selection.strategy import FeatureSelectionStrategy

"""
For each service pair, determine which features are distributional outliers compared to the baseline.
Corresponds to the selecting_features_main() function in run_selecting_features.py in the original codebase.
"""
def select_features(
        window_invocations: list[Invocation], 
        baseline: BaselineModel, 
        strategy: FeatureSelectionStrategy
    ) -> dict[ServicePair, list[str]]:

    # section 3A.1: multi-metric invocation anomaly detection;
    # perform adaptive selection features for each fault (fault is determined
    # by comparing invocation features to baseline features)
    # group window_invocations by service pair

    pair_to_invocations: dict[ServicePair, list[Invocation]] = defaultdict(list)

    for inv in window_invocations:
        pair_to_invocations[inv.service_pair].append(inv)

    # for each pair in baseline, for each feature in pair, utilize the provided strategy
    # to determine if feature is an outlier compared to baseline; if so, add to output dict
    pair_to_anomalous_features: dict[ServicePair, list[str]] = defaultdict(list)

    for pair, invocations in pair_to_invocations.items():
        if pair not in baseline.pairs:
            continue  # skip pairs not in baseline
        for feature in baseline.pairs[pair]:
            empirical_values = [inv.features[feature] for inv in invocations]
            reference_values = baseline.pairs[pair][feature]
            if strategy.is_anomalous(empirical_values, reference_values):
                pair_to_anomalous_features[pair].append(feature)

    return pair_to_anomalous_features