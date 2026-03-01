from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SuspiciousService:
    """One ranked candidate root-cause service (FP-Growth + Jaccard)."""

    service: str
    suspicion_score: float  # Jaccard: |Ab(s)| / (|Ab(s)| + |N(s)|)
    support: float  # FP-Growth itemset support
    confidence: float  # FP-Growth rule confidence
    rank: int  # 1-indexed; rank 1 is most suspicious


@dataclass
class RCAResult:
    """Output of one complete TraceRCA pipeline run over a single analysis window."""

    window_id: str
    window_start_us: int  # microseconds epoch
    window_end_us: int  # microseconds epoch
    analysed_at_us: int  # microseconds epoch
    total_traces: int
    anomalous_traces: int
    suspicious_services: list[SuspiciousService] = field(default_factory=list)
    # Maps "src->tgt" string to the list of anomalous feature names (Stage 1 output)
    selected_features: dict[str, list[str]] = field(default_factory=dict)
    pipeline_duration_ms: float = 0.0

    @property
    def anomaly_rate(self) -> float:
        if self.total_traces == 0:
            return 0.0
        return self.anomalous_traces / self.total_traces
