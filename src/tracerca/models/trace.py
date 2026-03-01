from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple


class ServicePair(NamedTuple):
    """Immutable and hashable (source, target) service pair for dict keys."""

    source: str
    target: str

    def __str__(self) -> str:
        return f"{self.source}->{self.target}"


@dataclass
class Invocation:
    """Single (source -> target) call extracted from a distributed trace span."""

    source: str
    target: str
    trace_id: str
    span_id: str
    start_time_us: int  # microseconds epoch
    end_time_us: int  # microseconds epoch
    latency_us: float  # (end - start) in microseconds
    http_status: int | None = None
    cpu_percent: float | None = None
    memory_mb: float | None = None

    @property
    def service_pair(self) -> ServicePair:
        return ServicePair(self.source, self.target)

    @property
    def features(self) -> dict[str, float]:
        """All non-null numeric features as a flat dict (used by algorithm stages)."""
        f: dict[str, float] = {"latency_us": self.latency_us}
        if self.http_status is not None:
            f["http_status"] = float(self.http_status)
        if self.cpu_percent is not None:
            f["cpu_percent"] = self.cpu_percent
        if self.memory_mb is not None:
            f["memory_mb"] = self.memory_mb
        return f


@dataclass
class InternalTrace:
    """All invocations belonging to one distributed trace, keyed by trace_id."""

    trace_id: str
    invocations: list[Invocation] = field(default_factory=list)
    received_at_us: int = 0  # when the trace was fully assembled

    # Ground-truth fields — populated by compat/pickle_loader; None for live traces
    label: int | None = None  # 0 = normal, 1 = fault
    root_cause: str | None = None  # service name of the injected fault
    fault_type: str | None = None

    @property
    def services(self) -> set[str]:
        """All service names touched by this trace."""
        names: set[str] = set()
        for inv in self.invocations:
            names.add(inv.source)
            names.add(inv.target)
        return names

    @property
    def service_pairs(self) -> set[ServicePair]:
        return {inv.service_pair for inv in self.invocations}

    @property
    def is_anomalous(self) -> bool | None:
        """True when ground-truth label == 1; None for live traces with no label."""
        if self.label is None:
            return None
        return self.label == 1
