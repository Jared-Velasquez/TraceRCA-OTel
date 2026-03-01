# TraceRCA-OTel

**Production-Ready Root Cause Localization Backend for Microservice Systems via OpenTelemetry**

---

## Overview

TraceRCA-OTel productionizes the TraceRCA algorithm (Li et al., IWQoS 2021) as a self-contained, deployable observability backend that speaks the OpenTelemetry wire protocol. It accepts live OTLP trace streams from any instrumented service fleet, continuously runs the TraceRCA 3-stage root-cause localization algorithm over sliding time windows, and exposes ranked suspicious services via a REST API.

The system is designed to be dropped into any OTel-instrumented environment with zero changes to application code. A single Docker image with an optional Redis sidecar is all that is required.

---

## How It Works

Traces arrive over OTLP (gRPC on port 4317 or HTTP on port 4318). The ingestion layer parses and assembles spans into `(source, target)` service-pair invocations, which are bucketed into tumbling time windows (default: 5 minutes). At the end of each window, a three-stage analysis pipeline runs:

1. **Feature Selection** — K-S test and 3-sigma rule identify which metrics are anomalous per service pair relative to a running EWMA baseline.
2. **Anomaly Detection** — Per-invocation z-score labeling flags individual traces as normal or anomalous.
3. **Root Cause Localization** — FP-Growth association rule mining over anomalous traces, ranked by Jaccard suspicion score, produces a top-K list of likely root-cause services.

Results are immediately available via the REST API and stored in Redis (production) or in-memory (development).

### Batch to Streaming

The original TraceRCA is a research artifact; it assumes you have already collected all traces, stored them as pickle files, and will run the analysis once offline. However, TraceRCA-OTel runs as a persistent service alongside your microservice fleet, accepting traces as they are produced and delivering root-cause rankings continuously.

### OpenTelemetry

Any service that emits OTel traces (via an OTel SDK or through a Collector pipeline) can feed TraceRCA-OTel without modification.

## Tech Stack

Python 3.12, FastAPI, grpc.aio, OpenTelemetry, APScheduler, Redis

---

## Reference

Li, M., et al. _"Practical Root Cause Localization for Microservice Systems via Trace Analysis."_ IEEE/ACM IWQoS 2021.
