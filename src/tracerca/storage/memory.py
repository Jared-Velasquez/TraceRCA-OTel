from collections import defaultdict

from tracerca.models.result import RCAResult
from tracerca.models.trace import InternalTrace
from tracerca.models.window import AnalysisWindow


class MemoryTraceStore:
    def __init__(self):
        self._traces: dict[str, list[InternalTrace]] = defaultdict(list)

    def save_trace(self, window_id: str, trace: InternalTrace) -> None:
        self._traces[window_id].append(trace)

    def get_traces(self, window_id: str) -> list[InternalTrace]:
        return list(self._traces[window_id])


class MemoryResultStore:
    def __init__(self) -> None:
        self._results: dict[str, RCAResult] = {}
        self._order: list[str] = []  # insertion order for newest-first listing

    def save_result(self, result: RCAResult) -> None:
        if result.window_id not in self._results:
            self._order.append(result.window_id)
        self._results[result.window_id] = result

    def get_result(self, window_id: str) -> RCAResult | None:
        return self._results.get(window_id)

    def list_results(self, limit: int = 20, offset: int = 0) -> list[RCAResult]:
        ids = self._order[-(offset + limit): len(self._order) - offset or None]
        return [self._results[wid] for wid in reversed(ids)]


class MemoryWindowStore:
    def __init__(self) -> None:
        self._windows: dict[str, AnalysisWindow] = {}

    def save_window(self, window: AnalysisWindow) -> None:
        self._windows[window.window_id] = window

    def get_window(self, window_id: str) -> AnalysisWindow | None:
        return self._windows.get(window_id)