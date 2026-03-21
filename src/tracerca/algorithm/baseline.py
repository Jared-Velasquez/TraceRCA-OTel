from dataclasses import dataclass

from src.tracerca.models.trace import ServicePair


@dataclass
class PairBaseline:
    mean: dict[str, float]   # feature -> running mean
    std:  dict[str, float]   # feature -> running std
    n:    int                # sample count

@dataclass
class BaselineModel:
    pairs: dict[ServicePair, PairBaseline]
    windows_seen: int

    def is_ready(self) -> bool: ...      # False during warmup (< 10 windows)
    def update(self, window_traces) -> None: ...