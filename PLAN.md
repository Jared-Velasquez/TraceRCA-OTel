# TraceRCA: MVP Implementation Plan

## Goal

Get the 3-stage TraceRCA algorithm running end-to-end against the published TrainTicket
dataset and verify it matches the paper's reported metrics. Everything else (OTLP ingestion,
Redis, Docker, gRPC) comes after correctness is proven.

---

## MVP Scope

**In:**
- Core algorithm (all 3 stages — this is the point)
- In-memory storage (plain dicts, no abstractions)
- Compat layer (pickle loader + replayer) — validate against real data
- Window manager (manual trigger, no scheduler)
- Minimal FastAPI REST API (health, status, results)

**Out (deferred):**
- gRPC / HTTP OTLP ingestion
- Redis storage
- APScheduler / automated window triggering
- Protocol/interface abstractions and factory pattern
- Docker / deployment config
- Prometheus metrics, structlog, request middleware
- Full YAML + env-var config system

---

## Tech Stack (MVP)

| Layer | Choice |
|---|---|
| Language | Python 3.12 |
| Algorithm | scipy, numpy, mlxtend (K-S, 3-sigma, FP-Growth) |
| Storage | Plain `dict` / `list` in-memory |
| Web | FastAPI (minimal routes) |
| Config | Simple dataclass with hardcoded defaults |
| Testing | pytest + pytest-asyncio |

---

## Directory Structure (MVP)

```
src/tracerca/
├── models/
│   ├── trace.py        ✅ done
│   ├── window.py       ✅ done
│   └── result.py       ✅ done
│
├── storage/
│   └── memory.py       # Concrete in-memory store (no interface yet)
│
├── algorithm/
│   ├── baseline.py     # EWMA running mean/std per (pair, feature)
│   ├── feature_selection.py  # Stage 1: K-S / 3-sigma divergence
│   ├── anomaly_detection.py  # Stage 2: per-invocation z-score labelling
│   ├── localization.py       # Stage 3: FP-Growth + Jaccard scoring
│   └── pipeline.py           # Orchestrates stages 1-3 for one window
│
├── windowing/
│   └── manager.py      # WindowManager: ingest traces, manually seal + run pipeline
│
├── compat/
│   ├── pickle_loader.py  # Loads original .pkl files -> list[InternalTrace]
│   ├── replayer.py       # Feeds InternalTrace list into WindowManager
│   └── metrics.py        # precision@k, recall@k, MRR
│
└── api/
    └── app.py            # FastAPI: /health, /api/v1/status, /api/v1/results
```

---

## Implementation Order

1. `storage/memory.py` — needed by everything downstream
2. `algorithm/baseline.py` — EWMA state; prerequisite for stages 1–2
3. `algorithm/feature_selection.py` — Stage 1
4. `algorithm/anomaly_detection.py` — Stage 2
5. `algorithm/localization.py` — Stage 3
6. `algorithm/pipeline.py` — wires stages 1–3 together
7. `compat/pickle_loader.py` + `compat/metrics.py`
8. `windowing/manager.py` + `compat/replayer.py`
9. Run benchmark against TrainTicket dataset; assert precision@1/3/5 and MRR
10. `api/app.py` — expose results once algorithm is proven

---

## TraceRCA Algorithm (3 Stages)

### Stage 1 — Feature Selection (`algorithm/feature_selection.py`)

Identifies anomalous metrics per `(source, target)` pair by comparing the current window's
distribution against the historical baseline.

- **Input**: Current window traces + baseline per service pair
- **Method**: Two-sample K-S test (non-Gaussian) and 3-sigma rule (Gaussian)
- **Output**: `dict[ServicePair, list[str]]` — anomalous feature names per pair

### Stage 2 — Anomaly Detection (`algorithm/anomaly_detection.py`)

Labels each invocation as normal or anomalous.

- **Input**: Traces + selected features + baseline mean/std
- **Method**: Per-(source, target) z-score with threshold σ=3
- **Output**: `dict[str, int]` — binary anomaly label per trace_id

### Stage 3 — Root Cause Localization (`algorithm/localization.py`)

Mines frequent service co-occurrence patterns and ranks by suspicion score.

- **Input**: Anomaly-labelled traces
- **Method**: FP-Growth + Jaccard: `score(s) = |Ab(s)| / (|Ab(s)| + |N(s)|)`
- **Output**: Ranked `list[SuspiciousService]`

---

## Baseline Model (`algorithm/baseline.py`)

Stores per-(source, target) per-feature running mean/std using EWMA (α=0.1).

```python
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
```

---

## Storage MVP (`storage/memory.py`)

No protocols. Three plain classes, each backed by a dict:

```python
class MemoryTraceStore:
    _traces: dict[str, list[InternalTrace]]   # window_id -> traces

class MemoryResultStore:
    _results: dict[str, RCAResult]            # window_id -> result
    _order: list[str]                         # insertion order for listing

class MemoryWindowStore:
    _windows: dict[str, AnalysisWindow]       # window_id -> window
```

---

## Window Manager (`windowing/manager.py`)

Simple class; no scheduler dependency.

```python
class WindowManager:
    def ingest(self, trace: InternalTrace) -> None: ...   # adds to current OPEN window
    async def seal_and_run(self) -> RCAResult: ...        # OPEN -> DRAINING -> ANALYSING -> CLOSED
    def current_window(self) -> AnalysisWindow: ...
```

`seal_and_run()` is called manually by the replayer (compat) or later by the scheduler.

---

## Compat Layer

**`pickle_loader.py`**: reads original `.pkl` files → `list[InternalTrace]` with ground-truth
`label` and `root_cause` fields.

**`replayer.py`**: feeds a list of `InternalTrace` into `WindowManager`. Supports:
- Batch mode: all traces → one window, then `seal_and_run()`
- Multi-window mode: split by original timestamps, trigger per window

**`metrics.py`**: `precision_at_k`, `recall_at_k`, `mean_reciprocal_rank` matching the
IWQoS 2021 paper evaluation methodology.

---

## API (Minimal)

Three endpoints only:

| Method | Path | Returns |
|---|---|---|
| GET | /health | `{"status": "ok"}` |
| GET | /api/v1/status | Current window state, baseline readiness |
| GET | /api/v1/results | List of completed RCA results (newest first) |

---

## Verification

- [ ] `pytest tests/unit/` — algorithm stages pass on synthetic data
- [ ] `pytest tests/benchmark/` — TrainTicket dataset: precision@1/3/5 and MRR meet paper values
- [ ] `GET /api/v1/results` returns ranked suspicious services after replaying a fault batch
