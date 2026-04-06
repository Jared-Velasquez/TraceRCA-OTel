from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from tracerca.models.trace import InternalTrace, ServicePair

RING_BUFFER_MAXLEN = 1000


@dataclass
class PairBaseline:
    """Per-service-pair historical feature values stored as a bounded ring buffer.

    Provides dict-like access so that ``select_features()`` works unchanged:
      - ``for feature in pair_baseline`` iterates feature names
      - ``pair_baseline[feature]`` returns the raw deque of observations
    """

    values: dict[str, deque[float]] = field(default_factory=dict)
    _mean: dict[str, float] = field(default_factory=dict, repr=False)
    _std: dict[str, float] = field(default_factory=dict, repr=False)

    @property
    def n(self) -> int:
        if not self.values:
            return 0
        return min(len(d) for d in self.values.values())

    def mean(self, feature: str) -> float:
        return self._mean.get(feature, 0.0)

    def std(self, feature: str) -> float:
        return self._std.get(feature, 0.0)

    def append(self, feature: str, value: float) -> None:
        if feature not in self.values:
            self.values[feature] = deque(maxlen=RING_BUFFER_MAXLEN)
        self.values[feature].append(value)

    def recompute_stats(self) -> None:
        for feature, buf in self.values.items():
            arr = np.array(buf)
            self._mean[feature] = float(np.mean(arr))
            self._std[feature] = float(np.std(arr))

    # -- dict-like protocol so select_features() works unchanged --

    def __iter__(self):
        return iter(self.values)

    def __getitem__(self, feature: str) -> deque[float]:
        return self.values[feature]

    def __contains__(self, feature: str) -> bool:
        return feature in self.values


@dataclass
class BaselineModel:
    pairs: dict[ServicePair, PairBaseline] = field(default_factory=dict)
    windows_seen: int = 0

    def is_ready(self) -> bool:
        """Baseline is usable after 10 windows of warmup data."""
        return self.windows_seen >= 10

    def update(self, traces: list[InternalTrace]) -> None:
        """Append invocation features from *normal* traces into ring buffers.

        Call only with non-anomalous traces to prevent baseline poisoning.
        Recomputes cached mean/std after all appends.
        """
        touched_pairs: set[ServicePair] = set()
        for trace in traces:
            for inv in trace.invocations:
                pair = inv.service_pair
                if pair not in self.pairs:
                    self.pairs[pair] = PairBaseline()
                for feat_name, feat_val in inv.features.items():
                    self.pairs[pair].append(feat_name, feat_val)
                touched_pairs.add(pair)
        for pair in touched_pairs:
            self.pairs[pair].recompute_stats()
        self.windows_seen += 1

    def snapshot(self) -> BaselineModel:
        """Return a deep copy for use during pipeline execution."""
        import copy
        return copy.deepcopy(self)
