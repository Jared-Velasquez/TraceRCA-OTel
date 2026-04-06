"""Stage 2: Per-invocation z-score anomaly detection.

Corresponds to run_anomaly_detection_*.py in the original TraceRCA codebase.
"""

from __future__ import annotations

from tracerca.algorithm.baseline import BaselineModel
from tracerca.models.trace import InternalTrace, ServicePair


def detect_anomalies(
    traces: list[InternalTrace],
    baseline: BaselineModel,
    selected_features: dict[ServicePair, list[str]],
    z_threshold: float = 3.0,
) -> dict[str, int]:
    """Label each trace as normal (0) or anomalous (1).

    Returns:
        Mapping of trace_id -> 0 (normal) or 1 (anomalous).
    """
    labels: dict[str, int] = {}

    for trace in traces:
        is_anomalous = False
        for inv in trace.invocations:
            pair = inv.service_pair
            if pair not in selected_features:
                continue
            features = inv.features
            for feat_name in selected_features[pair]:
                if feat_name not in features:
                    continue
                pair_baseline = baseline.pairs.get(pair)
                if pair_baseline is None:
                    continue
                mean = pair_baseline.mean(feat_name)
                std = pair_baseline.std(feat_name)
                if std == 0.0:
                    continue
                z = abs(features[feat_name] - mean) / std
                if z > z_threshold:
                    is_anomalous = True
                    break
            if is_anomalous:
                break

        label = 1 if is_anomalous else 0
        labels[trace.trace_id] = label
        trace.label = label

    return labels
