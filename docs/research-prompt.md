# Research Prompt: SLO-Driven RCA Triggering in Production

Use this prompt with a research-capable agent (web search + reading recent vendor docs, SRE blog posts, conference papers, and open-source project READMEs). The goal is to ground the design of TraceRCA-OTel in **how this is actually done in production**, not how it is described in academic papers.

---

## Context for the researcher

I am building an OpenTelemetry-native observability backend whose job is narrow:

1. Ingest OTLP traces / metrics / logs from an instrumented microservice fleet.
2. Continuously evaluate user-defined **SLOs** (latency, error rate, availability, saturation, custom).
3. When an SLO is violated (or its error budget burns past a threshold), **trigger an external Root Cause Analysis (RCA) system** — passing it enough context (time window, affected service/route, signal that fired) to investigate.

The RCA system itself is *out of scope* — it is a pluggable downstream consumer. My backend's job is detection + dispatch, not localization.

I need to validate that this architecture mirrors real production practice before I commit to it. Please answer the questions below with **citations to specific products, repos, papers, or post-mortems**. Where the literature and production diverge, say so explicitly.

---

## Questions

### 1. Does the "observability backend triggers a separate RCA system" pattern actually exist in production?

- Which commercial vendors (Datadog Watchdog, New Relic AIOps / Lookout, Dynatrace Davis, Splunk ITSI, Grafana Cloud, Honeycomb BubbleUp, Chronosphere, Coralogix, Lightstep / ServiceNow Cloud Observability, Instana, Elastic AIOps) ship an explicit **SLO-violation → RCA-pipeline** flow? For each one, is RCA an *integrated* feature of the backend, a *separate product* the backend hands off to, or absent entirely?
- Which open-source stacks (Prometheus + Alertmanager, OpenSLO, Sloth, Pyrra, Nobl9, Grafana SLO, Keptn / Cloud Native Lifecycle, OpenTelemetry-Collector + processors, SigNoz, Uptrace, Jaeger) provide SLO evaluation, and how do they pass the violation downstream?
- Are there published cases (post-mortems, KubeCon / SREcon talks, engineering blogs from Netflix, Uber, LinkedIn, Meta, Google, Alibaba, ByteDance, eBay) describing a decoupled detection-vs-localization split? Or do production systems usually fuse the two?

### 2. What is the trigger contract — what does the "RCA call" actually look like in prod?

- **Transport**: webhook (HTTP POST JSON), message queue (Kafka / Pub/Sub / SQS / Redis Streams / NATS), gRPC, or alert-manager-style fan-out?
- **Payload**: what context does an RCA system actually need? SLO ID, time window, affected service/route, signal type, link back to raw traces? Are there emerging standards (CloudEvents, OpenTelemetry alerts, OpenSLO data plane proposals)?
- **Idempotency / deduplication**: how do prod systems avoid firing 1000 RCA jobs for one incident? (Alert grouping, error-budget burn-rate windows, hysteresis, cooldowns.)
- **Latency budget**: how fast does the trigger need to fire after a violation? (Real-time vs. batch-window analysis like the original TraceRCA paper's 5-minute windows.)

### 3. SLO evaluation itself — what is the dominant approach?

- **Multi-window, multi-burn-rate** alerting (the Google SRE Workbook chapter). Is this the de-facto standard? Which OSS/SaaS implements it natively?
- **Tumbling vs. sliding vs. burn-rate** windows: tradeoffs in practice (noisy alerts vs. detection lag).
- **SLI sources**: are SLOs typically evaluated over **metrics** (Prometheus-style rate/histogram), **trace-derived metrics** (RED computed from spans), or **logs** (parsed events)? My backend speaks OTLP for all three — which signal type drives most SLO violations in production?
- **Configuration format**: OpenSLO, Sloth YAML, Prometheus recording-rule + alert pairs, vendor-proprietary. Is there a winner?

### 4. Where does RCA actually live in modern AIOps?

- Are production RCA systems usually (a) **trace-based** (TraceRCA, MicroRank, MEPFL, TraceAnomaly), (b) **metric-based** (causal-graph methods like CIRCA, MicroCause, MicroScope), (c) **log-based** (LogCluster, Drain + clustering), (d) **multi-modal** (Eadro, DeepHunt, Nezha), or (e) **LLM-driven** (recent 2024–2026 work)?
- How do they get their input? Does the RCA system **pull** raw telemetry from the same backend that fired the alert, or does the trigger **push** a pre-bundled context blob? This decision shapes my backend's API surface.
- Does the trigger usually point at a **single canonical RCA service**, or are there parallel investigators (chaos-correlation, deployment-correlation, dependency-graph) that all fire on the same SLO event?

### 5. Anti-patterns and pitfalls

- Where does the naive "SLO violation → fire RCA" loop break down in production? (Alert storms, transitive blame, RCA-on-RCA recursion, gray failures the SLO doesn't capture, RCA latency exceeding MTTR target.)
- Why have some teams *abandoned* automated RCA triggering in favor of human-driven investigation tools (Honeycomb's BubbleUp philosophy)?
- What does the recent literature (ICSE / FSE / ASE / SoCC / NSDI 2023–2026) say about the *limits* of trace-based RCA in real fleets?

---

## Deliverables

A written report covering:

1. **One-paragraph verdict**: is the decoupled "OTel backend detects SLO violation → triggers external RCA" architecture a real production pattern, a niche choice, or a research-only construct?
2. **Reference architecture diagram** (ASCII is fine) drawn from the closest real-world analog you found.
3. **Concrete trigger-contract recommendation**: transport, payload schema (with field list), dedup strategy, latency target — with citation to whichever production system you're cribbing from.
4. **SLO evaluation recommendation**: window strategy, configuration format, primary signal source — same citation requirement.
5. **List of 5–10 reference implementations** (repos / products) most worth studying before writing code, ranked by relevance to my decoupled-trigger design.
6. **Risks**: the top 3 ways this architecture has failed elsewhere, and what to design in from day one to avoid them.

Prefer primary sources (vendor docs, OSS repos, talks, papers) over secondary summaries. Note publication dates — observability tooling moved a lot between 2022 and 2026, so prefer recent material.
