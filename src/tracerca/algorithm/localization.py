"""Stage 3: FP-Growth frequent-pattern mining + Jaccard suspicion scoring.

Corresponds to run_localization_*.py in the original TraceRCA codebase.
"""

from __future__ import annotations

import pandas as pd
from mlxtend.frequent_patterns import fpgrowth

from tracerca.models.result import SuspiciousService
from tracerca.models.trace import InternalTrace


def localize(
    traces: list[InternalTrace],
    anomaly_labels: dict[str, int],
    min_support: float = 0.1,
) -> list[SuspiciousService]:
    """FP-Growth + Jaccard suspicion scoring.

    1. Build transaction database from anomalous traces.
    2. Run FP-Growth to find frequent service sets.
    3. Score each frequent singleton service via Jaccard.
    4. Rank by Jaccard score descending.

    Returns:
        Ranked list of suspicious services (1-indexed).
        Empty list if there are no anomalous traces.
    """

    anomalous_traces = [t for t in traces if anomaly_labels.get(t.trace_id) == 1]
    if not anomalous_traces:
        return []

    # Build one-hot transaction DataFrame from anomalous traces
    transactions: list[set[str]] = [t.services for t in anomalous_traces]
    all_services = sorted({s for txn in transactions for s in txn})
    rows = [{svc: (svc in txn) for svc in all_services} for txn in transactions]
    df = pd.DataFrame(rows)

    frequent = fpgrowth(df, min_support=min_support, use_colnames=True)
    if frequent.empty:
        return []

    # Filter singletons like original codebase
    singletons = frequent[frequent["itemsets"].apply(len) == 1].copy()
    if singletons.empty:
        return []

    # Extract service name from each frozenset
    singletons["service"] = singletons["itemsets"].apply(lambda fs: next(iter(fs)))

    # Jaccard scoring
    normal_traces = [t for t in traces if anomaly_labels.get(t.trace_id) == 0]

    results: list[SuspiciousService] = []
    for _, row in singletons.iterrows():
        svc = row["service"]
        ab = sum(1 for t in anomalous_traces if svc in t.services)
        n = sum(1 for t in normal_traces if svc in t.services)
        jaccard = ab / (ab + n) if (ab + n) > 0 else 0.0
        results.append(
            SuspiciousService(
                service=svc,
                suspicion_score=jaccard,
                support=float(row["support"]),
                confidence=float(row["support"]),  # singleton; no association rule
                rank=0,  # assigned below
            )
        )

    # Rank in descending order
    results.sort(key=lambda r: r.suspicion_score, reverse=True)
    for i, r in enumerate(results, start=1):
        r.rank = i

    return results
