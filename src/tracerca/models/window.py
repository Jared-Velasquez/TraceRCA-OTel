from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from tracerca.models.trace import InternalTrace


class WindowState(Enum):
    """Lifecycle states for an analysis window

    Transitions: OPEN -> DRAINING -> ANALYSING -> CLOSED
    """

    OPEN = "open" # Accepting new traces
    DRAINING = "draining" # Sealed; grace period for late-arriving spans
    ANALYSING = "analysing" # Pipeline is running
    CLOSED = "closed" # Pipeline complete; result persisted


@dataclass # adds boilerplate code for field initialization
class AnalysisWindow:
    """A bounded time window that accumulates traces for one RCA pipeline run"""

    window_id: str
    start_us: int # microseconds epoch (inclusive)
    end_us: int # microseconds epoch (exclusive)
    state: WindowState = WindowState.OPEN
    traces: list[InternalTrace] = field(default_factory=list)

    sealed_at_us: int | None = None # when state moved to DRAINING
    analysed_at_us: int | None = None # when state moved to CLOSED

    def add_trace(self, trace: InternalTrace) -> None:
        self.traces.append(trace)

    @property
    def trace_count(self) -> int:
        return len(self.traces)

    @property
    def is_accepting(self) -> bool:
        """True when the window is still open to new traces."""
        return self.state in (WindowState.OPEN, WindowState.DRAINING)
