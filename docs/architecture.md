# TraceRCA-CD Live Deployment — Implementation Architecture

**Status:** Implementation-ready.
**Companion docs:** [handoff-architecture-design.md](handoff-architecture-design.md) (settled decisions), [rca-research-report.md](rca-research-report.md) (literature grounding).
**Goal of this document:** turn the settled stack into an executable plan — every config paste-ready, every version pinned, every claim cited.

---

## 0. Overview and verified-before-design findings

The system is a K8s/host split. Kubernetes (kind) hosts the workload (Train-Ticket, OperationsPAI fork), the fault injector (Chaos Mesh), and the OTel Collector. The host (docker-compose) hosts Tempo, Prometheus, Alertmanager, Sloth (CLI), and the RCA shim. The shim shells out to a Python inference CLI that wraps the unmodified [TraceRCA-CD](https://github.com/Jared-Velasquez/TraceRCA-CD) algorithm; the trainer produces the isolation-forest model from a fault-free baseline window. Eval artifacts (`eval/eval.jsonl`, `eval/ground_truth.jsonl`, `eval/inputs/*.pkl`) close the loop for AC@K / MTTL computation.

Four verified-before-design findings shape the design. Read these before anything else.

### 0.1 TraceRCA-CD is not a Python package

The TraceRCA-CD repo (HEAD `8df3e4431d849f96c206079db3e50c00963cb848`) has no `pyproject.toml`, no `setup.py`, no `__init__.py`, no package surface. Every entry point is a `run_*.py` script at repo root, orchestrated by a `Makefile`. Module-global state (e.g. `from trainticket_config import FEATURE_NAMES`) means it cannot be `pip install`-ed from git.

**Packaging decision: git submodule at `vendor/TraceRCA-CD/`**, pinned to commit `8df3e4431d849f96c206079db3e50c00963cb848`. The trainer and inference CLI invoke its CLI scripts via `subprocess.run` against `sys.executable`, with `vendor/TraceRCA-CD/` prepended to `PYTHONPATH`. `pip install git+...` is not viable for this repo.

### 0.2 TraceRCA-CD has two pkl schemas, not one

| Schema | Producer | Consumer | Shape |
|---|---|---|---|
| **1a — raw trace pkl** | `preprocess_re2tt.py` (or our OTLP converter) | `run_invo_encoding.py` | `list[dict]`, one dict per trace, parallel lists per span |
| **1b — invo-encoded pkl** | `run_invo_encoding.py` | `run_anomaly_detection_invo.py`, `run_anomaly_detection_prepare_model.py`, `run_localization_association_rule_mining_20210516.py` | `pandas.DataFrame`, one row per `(source_service, target_service)` invocation edge |

The OTLP→pkl converter produces **schema 1a only**. `run_invo_encoding.py` deterministically derives 1b. The trainer and inference CLI both run the encoding step as part of their pipeline.

### 0.3 TraceRCA-CD's "inference" is three CLI invocations, not one function

1. `run_invo_encoding.py` — derives invo DataFrame (1b) from raw pkl (1a)
2. `run_anomaly_detection_invo.py` — adds `predict` column using the trained model dict
3. `run_localization_association_rule_mining_20210516.py` — emits the ranked service list

The new inference CLI wraps these three sequential subprocess calls. The training routine (`run_anomaly_detection_prepare_model.py`) is similarly a single CLI invocation that pickles a `dict` of `IsolationForest` objects keyed by `IF-{source}-{target}`.

### 0.4 The OperationsPAI Train-Ticket fork ships its own in-cluster OTel Collector

`global.monitoring: "opentelemetry"` (the default in the fork's `values.yaml`) also turns on `otelCollector.enabled: true`, which deploys a per-namespace `otel/opentelemetry-collector-contrib:0.142.0` that exports to a ClickHouse backend. We **disable** that sidecar (`--set otelCollector.enabled=false`) and point every `ts-*` service at our standalone Collector in the `observability` namespace via `--set global.otelcollector="http://otel-collector.observability.svc.cluster.local:4317"`.

Verified pod label key for Chaos Mesh selectors: `app: ts-{name}-service` (e.g. `app: ts-order-service`). The handoff's short names (`ts-order`, etc.) are logical names; selectors and label values use the `-service` suffix.

---

## 1. Component inventory

All versions pinned. Resource requests/limits are sized for a 16 GB / 8 vCPU laptop.

### K8s side (in cluster)

| Component | Image / source | Version | Namespace | Replicas | CPU req / lim | Mem req / lim |
|---|---|---|---|---|---|---|
| kind | `kindest/node` | `v1.35.0` (sha pinned in `kind-config.yaml`) | — | 1 node | — | — |
| cert-manager | Static manifest | `v1.20.2` | `cert-manager` | 1 each (3 deploys) | 50m / 200m | 64Mi / 256Mi |
| Chaos Mesh | Helm `chaos-mesh/chaos-mesh` | `2.8.2` | `chaos-mesh` | controller=3, daemon=1/node, dashboard=1, dns=1 | chart defaults | chart defaults |
| OTel Collector | `otel/opentelemetry-collector-contrib` | `0.152.0` | `observability` | 1 | 100m / 500m | 256Mi / 512Mi |
| Train-Ticket | Helm chart in [OperationsPAI/train-ticket](https://github.com/OperationsPAI/train-ticket) `manifests/helm/trainticket` (record `git rev-parse HEAD` at clone time) | chart `0.3.1`, image tag `637600ea` | `ts` | 1 per `ts-*` service (~45) + rabbit + per-svc MySQL | chart defaults | chart defaults |

### Host side (docker-compose)

| Component | Image / source | Version | Replicas | CPU | Mem | Volumes |
|---|---|---|---|---|---|---|
| Tempo (single-binary) | `grafana/tempo` | `2.10.5` | 1 | unbounded | unbounded | `tempo-data` (named) |
| Prometheus | `prom/prometheus` | `v2.53.5` | 1 | unbounded | unbounded | `prom-data` (named); `slo/generated/` bind |
| Alertmanager | `prom/alertmanager` | `v0.27.0` | 1 | unbounded | unbounded | config bind |
| Sloth (CLI) | `ghcr.io/slok/sloth` | `v0.12.0` | run-once container | — | — | spec + windows + out binds |
| Shim | local Python (stdlib) `python:3.11-slim` or host `python3` | Python ≥3.11 | 1 | — | — | binds `eval/`, `models/` |
| TraceRCA-CD trainer + inference CLI | local Python, `vendor/TraceRCA-CD@8df3e44` (submodule) | submodule SHA | invoked by shim/cron | — | — | binds `eval/`, `models/`, `vendor/` |

---

## 2. Configuration artifacts

### 2.1 kind cluster — `deploy/k8s/kind-config.yaml`

Cluster tool **kind** is chosen because (a) `extraPortMappings` deterministically expose specific NodePorts to host loopback (cleanest of the three options for our host→cluster scrape requirement) and (b) on Docker Desktop, `host.docker.internal` resolves from inside kind nodes — required for the in-cluster Collector to push OTLP to host-side Tempo.

Citations: [kind extra port mappings](https://kind.sigs.k8s.io/docs/user/configuration/#extra-port-mappings), [kind v0.31.0 release](https://github.com/kubernetes-sigs/kind/releases/tag/v0.31.0).

```yaml
# deploy/k8s/kind-config.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: tracerca
nodes:
  - role: control-plane
    image: kindest/node:v1.35.0@sha256:452d707d4862f52530247495d180205e029056831160e22870e37e3f6c1ac31f
    extraPortMappings:
      - { containerPort: 30889, hostPort: 8889,  protocol: TCP }  # OTel Collector /metrics
      - { containerPort: 30080, hostPort: 30080, protocol: TCP }  # ts-ui-dashboard
      - { containerPort: 30233, hostPort: 2333,  protocol: TCP }  # Chaos Mesh dashboard
```

### 2.2 OTel Collector — `deploy/k8s/otel-collector/*.yaml`

Contrib (not core) is **required** because the `spanmetrics` connector ships only in the contrib distribution. The connector is wired as a *connector* (declared under `connectors:`, listed as an exporter in the trace pipeline and as a receiver in the metrics pipeline).

Citations: [OTel Collector connector concept](https://opentelemetry.io/docs/collector/configuration/#connectors), [spanmetrics connector README @ v0.152.0](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/v0.152.0/connector/spanmetricsconnector).

```yaml
# deploy/k8s/otel-collector/00-namespace.yaml
apiVersion: v1
kind: Namespace
metadata: { name: observability }
---
# deploy/k8s/otel-collector/10-configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata: { name: otel-collector-config, namespace: observability }
data:
  config.yaml: |
    receivers:
      otlp:
        protocols:
          grpc: { endpoint: 0.0.0.0:4317 }
          http: { endpoint: 0.0.0.0:4318 }

    processors:
      memory_limiter:
        check_interval: 1s
        limit_percentage: 75
        spike_limit_percentage: 20
      probabilistic_sampler:
        sampling_percentage: 100   # 100 / 10 / 1 — sampling-sweep knob
        hash_seed: 22
      resource:
        attributes:
          - { key: deployment.environment, value: capstone, action: upsert }
      batch:
        send_batch_size: 1024
        timeout: 2s

    connectors:
      spanmetrics:
        histogram:
          explicit:
            buckets: [1ms, 2ms, 5ms, 10ms, 25ms, 50ms, 100ms, 250ms, 500ms, 1s, 2s, 5s, 10s]
          unit: ms
        dimensions:
          - { name: service.name }
          - { name: span.kind }
          - { name: status.code }
        exemplars: { enabled: true }
        metrics_flush_interval: 15s
        aggregation_temporality: AGGREGATION_TEMPORALITY_CUMULATIVE
        namespace: traces.spanmetrics

    exporters:
      otlp/tempo:
        endpoint: host.docker.internal:4317   # Linux: replace with 172.17.0.1:4317
        tls: { insecure: true }
        sending_queue: { enabled: true, queue_size: 1000 }
        retry_on_failure: { enabled: true }
      prometheus:
        endpoint: 0.0.0.0:8889
        enable_open_metrics: true
        resource_to_telemetry_conversion: { enabled: true }

    extensions:
      health_check: { endpoint: 0.0.0.0:13133 }

    service:
      extensions: [health_check]
      pipelines:
        traces:
          receivers:  [otlp]
          processors: [memory_limiter, probabilistic_sampler, resource, batch]
          exporters:  [otlp/tempo, spanmetrics]
        metrics:
          receivers:  [spanmetrics]
          processors: [batch]
          exporters:  [prometheus]
      telemetry:
        metrics: { level: basic }
---
# deploy/k8s/otel-collector/20-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata: { name: otel-collector, namespace: observability, labels: { app: otel-collector } }
spec:
  replicas: 1
  selector: { matchLabels: { app: otel-collector } }
  template:
    metadata: { labels: { app: otel-collector } }
    spec:
      containers:
        - name: otelcol
          image: otel/opentelemetry-collector-contrib:0.152.0
          args: ["--config=/etc/otelcol/config.yaml"]
          ports:
            - { name: otlp-grpc, containerPort: 4317 }
            - { name: otlp-http, containerPort: 4318 }
            - { name: prom,      containerPort: 8889 }
            - { name: health,    containerPort: 13133 }
          resources:
            requests: { cpu: "100m", memory: "256Mi" }
            limits:   { cpu: "500m", memory: "512Mi" }
          volumeMounts:
            - { name: cfg, mountPath: /etc/otelcol }
          readinessProbe:
            httpGet: { path: /, port: 13133 }
            initialDelaySeconds: 5
      volumes:
        - { name: cfg, configMap: { name: otel-collector-config } }
---
# deploy/k8s/otel-collector/30-service-clusterip.yaml (ts-* pods send here)
apiVersion: v1
kind: Service
metadata: { name: otel-collector, namespace: observability }
spec:
  type: ClusterIP
  selector: { app: otel-collector }
  ports:
    - { name: otlp-grpc, port: 4317, targetPort: 4317 }
    - { name: otlp-http, port: 4318, targetPort: 4318 }
---
# deploy/k8s/otel-collector/31-service-nodeport.yaml (host Prom scrapes here)
apiVersion: v1
kind: Service
metadata: { name: otel-collector-prom, namespace: observability }
spec:
  type: NodePort
  selector: { app: otel-collector }
  ports:
    - { name: prom, port: 8889, targetPort: 8889, nodePort: 30889 }
```

**Emitted metric names** (verify with `curl :8889/metrics | grep traces_spanmetrics_`):

- `traces_spanmetrics_calls_total{service_name, span_kind, status_code, ...}` — counter
- `traces_spanmetrics_duration_milliseconds_bucket{service_name, le, ...}` — histogram
- `traces_spanmetrics_duration_milliseconds_count{service_name, ...}`
- `traces_spanmetrics_duration_milliseconds_sum{service_name, ...}`

The dot→underscore promotion is OpenMetrics-standard. These are the names the Sloth SLO queries (§2.4) must reference.

### 2.3 Tempo — `deploy/compose/tempo.yaml`

Single-binary mode (not the microservices distribution) — capstone scale. `metrics_generator.processors: []` is set explicitly to disable Tempo's own span-metrics derivation; the in-cluster Collector's `spanmetrics` connector is the single source of truth.

Citations: [Tempo configuration reference](https://grafana.com/docs/tempo/latest/configuration/), [Tempo API docs](https://grafana.com/docs/tempo/latest/api_docs/), [TraceQL](https://grafana.com/docs/tempo/latest/traceql/).

```yaml
# deploy/compose/tempo.yaml — mounted into the Tempo container at /etc/tempo/tempo.yaml
server:
  http_listen_address: 0.0.0.0
  http_listen_port: 3200
  grpc_listen_address: 0.0.0.0
  grpc_listen_port: 9095

distributor:
  receivers:
    otlp:
      protocols:
        grpc: { endpoint: 0.0.0.0:4317 }
        http: { endpoint: 0.0.0.0:4318 }

ingester:
  lifecycler: { ring: { replication_factor: 1 } }
  trace_idle_period: 10s
  max_block_duration: 5m
  flush_check_period: 10s

compactor:
  compaction: { block_retention: 24h }

storage:
  trace:
    backend: local
    local: { path: /var/tempo/traces }
    wal:   { path: /var/tempo/wal }

query_frontend:
  search:
    duration_slo: 5s
    throughput_bytes_slo: 1.073741824e+09

metrics_generator:
  processors: []
  storage: { path: /var/tempo/generator/wal }

usage_report: { reporting_enabled: false }
```

**TraceQL query shape used by trainer + inference CLI** (Unix-epoch-second start/end):

```bash
# Single service, time-bounded — used by inference CLI
curl -G -s http://localhost:3200/api/search \
  --data-urlencode 'q={ resource.service.name = "ts-order-service" }' \
  --data-urlencode "start=$T0" --data-urlencode "end=$T1" --data-urlencode "limit=1000"

# All 5 RE2TT services — used by trainer baseline collection
curl -G -s http://localhost:3200/api/search \
  --data-urlencode 'q={ resource.service.name =~ "ts-(auth|order|route|train|travel)-service" }' \
  --data-urlencode "start=$T0" --data-urlencode "end=$T1" --data-urlencode "limit=5000"

# Full trace body (search returns summaries only — must fan out per trace_id)
curl -s "http://localhost:3200/api/traces/$TRACE_ID?start=$T0&end=$T1"
```

### 2.4 Sloth SLO spec for `ts-order` — `slo/specs/ts-order.yaml`

The settled SLO windows are **5min/1min** (fast) and **15min/3min** (slow) — justified by the ~3-min chaos fault duration in [handoff §3](handoff-architecture-design.md). Sloth's default 1h/5min + 6h/30min won't fire within a 3-min fault, so we override the catalog via the [AlertWindows custom spec](https://sloth.dev/usage/slo-period-windows/) and feed it to `sloth generate --slo-period-windows-path`.

> Note on the deliverable text: §2 of the prompt says "Use the standard Google SRE multi-window multi-burn-rate config (1h/5min, 6h/30min)" — that wording is **stale**. §3 constraints (settled) and the "SLO windows are settled" line in the prompt's constraints block explicitly mandate the short windows. The settled values win; flagged in Open Questions §10.

Citations: [Sloth CLI](https://sloth.dev/usage/cli/), [Sloth SLO period windows](https://sloth.dev/usage/slo-period-windows/), [Sloth alert_rules/v1 plugin](https://sloth.dev/slo-plugins/core/alert_rules_v1/), [SRE workbook — Alerting on SLOs](https://sre.google/workbook/alerting-on-slos/).

```yaml
# slo/windows/short-catalog.yaml — fed to sloth via --slo-period-windows-path
apiVersion: sloth.slok.dev/v1
kind: AlertWindows
spec:
  sloPeriod: 30d
  page:
    quick:
      errorBudgetPercent: 2
      shortWindow: 1m
      longWindow:  5m
    slow:
      errorBudgetPercent: 5
      shortWindow: 3m
      longWindow:  15m
  ticket:
    quick:
      errorBudgetPercent: 10
      shortWindow: 3m
      longWindow:  15m
    slow:
      errorBudgetPercent: 10
      shortWindow: 3m
      longWindow:  15m
```

Burn-rate factors implied by these windows over a 30-day, 99% SLO budget (~1% budget):

| Severity | Long / Short | Budget consumed | Burn rate |
|---|---|---|---|
| page (quick) | 5m / 1m  | 2%  | ~172.8× |
| page (slow)  | 15m / 3m | 5%  | ~144× |
| ticket       | 15m / 3m | 10% | ~288× |

These are mathematically correct for the compressed windows but extreme by SRE-workbook standards. The trade-off (fast trigger ↔ false-positive sensitivity) is exactly what the SLO trigger precision/recall metric in §7 quantifies.

```yaml
# slo/specs/ts-order.yaml — Sloth-native v1
version: "prometheus/v1"
service: "ts-order-service"
labels:
  team: "trains"
  service_name: "ts-order-service"   # OTel semantic convention; AM routes on this
slos:
  - name: "availability"
    objective: 99.0
    description: "99% of ts-order-service spans complete with status.code != STATUS_CODE_ERROR"
    sli:
      events:
        error_query: |
          sum(rate(traces_spanmetrics_calls_total{
            service_name="ts-order-service",
            status_code="STATUS_CODE_ERROR"
          }[{{.window}}]))
        total_query: |
          sum(rate(traces_spanmetrics_calls_total{
            service_name="ts-order-service"
          }[{{.window}}]))
    alerting:
      name: TsOrderAvailability
      labels:    { service_name: "ts-order-service", slo_kind: "availability" }
      page_alert:   { labels: { severity: "page" } }
      ticket_alert: { labels: { severity: "ticket" } }

  - name: "latency-p99-500ms"
    objective: 99.0
    description: "99% of ts-order-service spans complete under 500ms"
    sli:
      events:
        error_query: |
          (
            sum(rate(traces_spanmetrics_duration_milliseconds_count{service_name="ts-order-service"}[{{.window}}]))
            -
            sum(rate(traces_spanmetrics_duration_milliseconds_bucket{service_name="ts-order-service",le="500"}[{{.window}}]))
          )
        total_query: |
          sum(rate(traces_spanmetrics_duration_milliseconds_count{service_name="ts-order-service"}[{{.window}}]))
    alerting:
      name: TsOrderLatencyP99
      labels:    { service_name: "ts-order-service", slo_kind: "latency" }
      page_alert:   { labels: { severity: "page" } }
      ticket_alert: { labels: { severity: "ticket" } }
```

The same template is duplicated for `ts-auth-service`, `ts-route-service`, `ts-train-service`, `ts-travel-service` → 5 spec files, 10 SLOs total.

**Generate the committed rule files** (run in CI; commit `slo/generated/*.yaml`):

```bash
docker run --rm \
  -v "$PWD/slo/specs:/in:ro" \
  -v "$PWD/slo/generated:/out" \
  -v "$PWD/slo/windows:/windows:ro" \
  ghcr.io/slok/sloth:v0.12.0 \
  generate \
    -i /in \
    -o /out \
    --default-slo-period=30d \
    --slo-period-windows-path=/windows
```

Generated output (`slo/generated/ts-order.rules.yaml`, head) — the labels `sloth_severity` and `service_name` are what Alertmanager routes on:

```yaml
groups:
  - name: sloth-slo-sli-recordings-ts-order-availability
    rules:
      - record: slo:sli_error:ratio_rate1m
        expr: |
          (sum(rate(traces_spanmetrics_calls_total{service_name="ts-order-service",status_code="STATUS_CODE_ERROR"}[1m])))
          /
          (sum(rate(traces_spanmetrics_calls_total{service_name="ts-order-service"}[1m])))
        labels:
          sloth_id: "ts-order-service-availability"
          sloth_service: "ts-order-service"
          sloth_slo: "availability"
          sloth_window: "1m"
      # ... 3m, 5m, 15m, 30m, 1h, 6h, 1d, 3d, 30d ...

  - name: sloth-slo-alerts-ts-order-availability
    rules:
      - alert: TsOrderAvailability
        expr: |
          (
            slo:sli_error:ratio_rate5m{sloth_id="ts-order-service-availability"}  > (14.4 * 0.01)
            and
            slo:sli_error:ratio_rate1m{sloth_id="ts-order-service-availability"}  > (14.4 * 0.01)
          )
          or
          (
            slo:sli_error:ratio_rate15m{sloth_id="ts-order-service-availability"} > (6 * 0.01)
            and
            slo:sli_error:ratio_rate3m{sloth_id="ts-order-service-availability"}  > (6 * 0.01)
          )
        labels:
          sloth_severity: "page"
          severity: "page"
          service_name: "ts-order-service"
          slo_kind: "availability"
          sloth_id: "ts-order-service-availability"
        annotations:
          summary: "High error budget burn for ts-order-service availability"
```

### 2.5 Prometheus — `deploy/compose/prometheus.yml`

Citations: [Prometheus configuration reference](https://prometheus.io/docs/prometheus/latest/configuration/configuration/).

```yaml
# deploy/compose/prometheus.yml
global:
  scrape_interval:     15s
  evaluation_interval: 15s

rule_files:
  - /etc/prometheus/rules/*.yaml

alerting:
  alertmanagers:
    - static_configs:
        - targets: ["alertmanager:9093"]

scrape_configs:
  - job_name: prometheus
    static_configs: [{ targets: ["localhost:9090"] }]

  - job_name: otel-collector-spanmetrics
    metrics_path: /metrics
    static_configs:
      # macOS/Win: host.docker.internal resolves natively.
      # Linux: include `extra_hosts: host-gateway` in docker-compose service.
      # The cluster's kind extraPortMapping forwards container 30889 → host 8889.
      - targets: ["host.docker.internal:8889"]
```

### 2.6 Alertmanager — `deploy/compose/alertmanager.yml`

Routes any alert with `sloth_severity =~ "page|ticket"` to the shim. `group_by: [service_name]` so per-service alerts don't get coalesced; `group_wait: 5s` keeps trigger latency low.

Citations: [Alertmanager configuration](https://prometheus.io/docs/alerting/latest/configuration/).

```yaml
# deploy/compose/alertmanager.yml
global:
  resolve_timeout: 1m

route:
  receiver: noop
  group_by: ["service_name"]
  group_wait: 5s
  group_interval: 30s
  repeat_interval: 1h
  routes:
    - matchers: [ 'sloth_severity =~ "page|ticket"' ]
      receiver: rca-shim
      continue: false

receivers:
  - name: noop
  - name: rca-shim
    webhook_configs:
      - url: http://host.docker.internal:8080/webhook
        send_resolved: true
        max_alerts: 0
```

### 2.7 docker-compose stack — `deploy/compose/docker-compose.yaml`

Single-command up/down for the host side.

```yaml
# deploy/compose/docker-compose.yaml
services:
  tempo:
    image: grafana/tempo:2.10.5
    command: ["-config.file=/etc/tempo/tempo.yaml"]
    ports:
      - "4317:4317"
      - "4318:4318"
      - "3200:3200"
    volumes:
      - ./tempo.yaml:/etc/tempo/tempo.yaml:ro
      - tempo-data:/var/tempo
    restart: unless-stopped

  prometheus:
    image: prom/prometheus:v2.53.5
    ports: ["9090:9090"]
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - ../../slo/generated:/etc/prometheus/rules:ro
      - prom-data:/prometheus
    command:
      - --config.file=/etc/prometheus/prometheus.yml
      - --storage.tsdb.path=/prometheus
      - --storage.tsdb.retention.time=15d
      - --web.enable-lifecycle
    extra_hosts: ["host.docker.internal:host-gateway"]
    restart: unless-stopped

  alertmanager:
    image: prom/alertmanager:v0.27.0
    ports: ["9093:9093"]
    volumes:
      - ./alertmanager.yml:/etc/alertmanager/alertmanager.yml:ro
    command: ["--config.file=/etc/alertmanager/alertmanager.yml"]
    extra_hosts: ["host.docker.internal:host-gateway"]
    restart: unless-stopped

  # Shim runs on the host directly (Python stdlib only). See §3.5.
  # The container path is intentionally avoided so it can shell out to
  # the trainer/inference CLI without container nesting.

volumes:
  tempo-data: {}
  prom-data:  {}
```

---

## 3. RCA CLI + shim + trainer specification

### 3.1 PKL schema spec — concrete and copy-pasteable

#### Schema 1a — raw trace pkl (converter output, training/inference input)

Python object: `list[dict]`. One dict per trace. Per-span fields are parallel lists indexed by span position; entries with `source == target` are dropped during invo encoding, so the converter should retain them and let the encoder filter.

| Key | Type | Unit / domain | Source (OTLP) |
|---|---|---|---|
| `trace_id`    | `str`                 | hex                                  | `Span.trace_id` |
| `label`       | `int`                 | 0 normal, 1 anomalous                | derived from chaos-inject window (trainer: always 0; inference: ignored downstream) |
| `fault_type`  | `str`                 | `'cpu' \| 'mem' \| 'delay' \| 'loss' \| 'disk' \| 'socket' \| ''` | inject metadata; `''` for live |
| `root_cause`  | `list[str]`           | simplified service names             | inject metadata; `[]` for live |
| `s_t`         | `list[tuple[str,str]]`| `(source_service, target_service)` per parent→child edge | parent span `service.name` → child span `service.name` |
| `timestamp`   | `list[int]`           | microseconds since epoch             | `Span.start_time_unix_nano // 1000` |
| `endtime`     | `list[int]`           | microseconds since epoch             | `(start + duration) // 1000` |
| `latency`     | `list[int]`           | microseconds                         | `Span.end - Span.start` in µs |
| `http_status` | `list[int]`           | HTTP code or `0` if absent           | `attributes["http.status_code"]`, fallback 0 |

#### Schema 1b — invo-encoded pkl (intermediate, derived by `run_invo_encoding.py`)

The converter does **not** produce this; it's a deterministic transform on 1a.

| Column | dtype | Meaning |
|---|---|---|
| `source`, `target` | `object` (str) | caller / callee service (simplified name) |
| `start_timestamp`, `end_timestamp` | `float64` | seconds since epoch |
| `latency` | `float64` | seconds (feature) |
| `http_status` | `int64` | bucketed: 2/3/4/5/9 (feature) |
| `trace_label`, `trace_id` | repeated per-trace | |
| `trace_start_timestamp`, `trace_end_timestamp` | `float64` | min/max of trace, seconds |

#### Schema 2 — model pkl (trainer output, inference input)

`dict` (not a class). Pickled via `pickle.dump(d, f)`. Keys:

| Key pattern | Value | Notes |
|---|---|---|
| `"IF-{source}-{target}"` | `sklearn.ensemble.IsolationForest` | `contamination=0.01`, `n_jobs=10` (TraceRCA-CD defaults) |
| `"reference-{source}-{target}-{feature}-mean-variance"` | `{"mean": float, "std": float}` | `std` floor = 0.1; `feature ∈ {"latency","http_status"}` |
| `"RF-Trace"` | `RandomForestClassifier` | unused on localization path |
| `"MLP-Trace"` | `MLPClassifier` | unused on localization path |

#### Sidecar metadata — written next to every model and input pkl

TraceRCA-CD's pkls carry no version. We add `<pkl_path>.meta.json`:

```json
{
  "schema_version": "1.0",
  "producer": "tracerca.converter",
  "producer_commit": "<git rev-parse HEAD of this repo>",
  "tracerca_cd_commit": "8df3e4431d849f96c206079db3e50c00963cb848",
  "feature_names": ["latency", "http_status"],
  "window_start_ts": 1715632800,
  "window_end_ts":   1715634600,
  "source_mode": "tempo-url",
  "source": "http://localhost:3200",
  "hyperparams": { "contamination": 0.01, "sigma_threshold": 1, "min_support_rate": 0.1 },
  "trace_count": 12483,
  "row_count":   54219
}
```

The inference CLI refuses to load a model whose sidecar `schema_version` is missing or not in its allowlist and exits non-zero with a clear message.

### 3.2 Packaging — git submodule

```bash
# One-time:
git submodule add https://github.com/Jared-Velasquez/TraceRCA-CD.git vendor/TraceRCA-CD
cd vendor/TraceRCA-CD && git checkout 8df3e4431d849f96c206079db3e50c00963cb848 && cd -
git add .gitmodules vendor/TraceRCA-CD
```

`pyproject.toml` then declares the runtime Python deps (`pandas`, `scikit-learn`, `numpy`, `click`, `httpx`, `pyyaml`); TraceRCA-CD itself is not listed there — it's invoked as scripts.

**Justification:** TraceRCA-CD has no `pyproject.toml`/`setup.py`/`__init__.py` and uses module-global state (`from trainticket_config import FEATURE_NAMES`), so `pip install git+https://...@8df3e44` fails. Submodule pins the SHA, lets us patch any missing helpers (e.g. `data/trainticket/download.py:simple_name`) locally, and avoids forking publicly.

### 3.3 The OTLP→pkl converter — `src/tracerca/converter.py`

Single module. Public surface: one function.

```
convert_otlp_to_raw_pkl(
    traces:  Iterable[OTLPTrace],     # output of TempoClient.fetch_traces(...)
    out_path: Path,
    *,
    label: int = 0,
    fault_type: str = "",
    root_cause: list[str] | None = None,
) -> Path                              # returns out_path
```

Behavior:

1. For each trace, build a `dict` matching schema 1a. Spans within a trace are ordered by `start_time_unix_nano`.
2. The `s_t` list is built by indexing spans by `span_id`, then walking each non-root span's `parent_span_id` to its parent span and emitting `(parent.service.name, child.service.name)` simplified by a local `_simple_name(service_name)` that strips the `-service` suffix and any namespace prefix. (Required to interoperate with TraceRCA-CD's `run_invo_encoding.py`, which deduplicates and filters by simple service name.)
3. Drop spans whose parent isn't in the same trace (orphan spans — rare but possible under sampling).
4. Write `pickle.dump(traces_list, f)` to `out_path`; write the sidecar JSON to `f"{out_path}.meta.json"`.
5. No filtering on http_status or label — the trainer is run against fault-free baselines via the experimental protocol, not via in-converter filtering ([handoff §6, "Baseline-training filtering: no filtering"](handoff-architecture-design.md)).

Companion: `src/tracerca/tempo_client.py` — thin `httpx` wrapper over `/api/search` + `/api/traces/{id}` returning a generator of `OTLPTrace`. Search returns trace summaries; the client fans out per-trace fetches for full span bodies, with a configurable concurrency limit (default 8).

**Test the converter standalone first**: feed it a known TraceQL window with hand-crafted spans, assert the resulting pkl loads and `run_invo_encoding.py` accepts it.

### 3.4 The trainer — `python -m tracerca.train`

Entry point: `src/tracerca/train.py`. Implemented as a Click command.

```
python -m tracerca.train \
  --baseline-source <tempo-url | pkl-dir> \
  --window <e.g. 30m> \
  --out models/model_live.pkl \
  [--tempo-url http://localhost:3200] \
  [--hyperparams hp.yaml]
```

Modes:

- `tempo-url` — pulls fault-free traces from Tempo via TraceQL over the requested window across all 5 RE2TT services, runs the converter (§3.3), writes a raw pkl to a temp dir, then proceeds.
- `pkl-dir` — reads `*.pkl` files directly from the directory (these must already be schema-1a). Used for `model_re2tt.pkl` *after* RE2TT CSVs are preprocessed via the upstream `preprocess_re2tt.py` (see §3.6).

Pipeline:

1. Either fetch + convert (mode A) or list pkls (mode B) → a directory of raw pkls.
2. `subprocess.run([sys.executable, "vendor/TraceRCA-CD/run_invo_encoding.py", "-i", <pkl_dir>, "-o", <invo_path>])` — emits the invo-encoded DataFrame.
3. `subprocess.run([sys.executable, "vendor/TraceRCA-CD/run_anomaly_detection_prepare_model.py", "-i", <invo_path>, "-t", <invo_path>, "-o", <out>])`. (`-t` shares the invo path because the localization path doesn't use the trace classifier; this matches the TraceRCA-CD Makefile invocation.)
4. Write the sidecar JSON (§3.1) next to `<out>`. Record `git -C vendor/TraceRCA-CD rev-parse HEAD`, source mode, window timestamps, hyperparams, and the schema version.

**Default hyperparameters** (from TraceRCA-CD's RE2TT evaluation — these are now the trainer defaults, overridable via `--hyperparams hp.yaml`):

```yaml
# default hyperparams — keep aligned with TraceRCA-CD/RE2TT runs
isolation_forest:
  contamination: 0.01
  n_jobs: 10
threshold:
  sigma: 1
feature_selection:
  fisher_cutoff: 3
localization:
  min_support_rate: 0.1
  k: 100
  forbidden_names: ["gateway"]
  caller_discount_alpha: 0.0   # TraceRCA baseline; set >0 for TraceRCA-CD's discount mode
```

### 3.5 The inference CLI — `python -m tracerca`

Entry point: `src/tracerca/__main__.py`. Loads the model dict, queries Tempo, runs the three-step TraceRCA-CD pipeline, parses the ranked output, appends to `eval/eval.jsonl`.

```
python -m tracerca \
  --service ts-order-service \
  --window 5m \
  --model models/model_live.pkl \
  [--tempo-url http://localhost:3200] \
  [--out-dir eval/] \
  [--save-input-pkl / --no-save-input-pkl]      # default ON
```

Pipeline:

1. Record `invoke_time = time.time()`.
2. Load the model: `pickle.load(open(model_path, "rb"))`. Read sidecar JSON; if `schema_version` not in allowlist, exit 2 with a clear message.
3. Compute `[t_end - window, t_end]` (now); query Tempo for spans where `resource.service.name = <service>` plus the 1-hop call neighbors (fan out using the parent_span_id linkage).
4. If zero traces are returned: log `"no spans for service=<X> window=[t0..t1]"`, write an `eval.jsonl` line with `ranked_list: []` and `note: "empty_window"`, exit 3.
5. Run the converter (§3.3) to produce a temp raw pkl. If `--save-input-pkl` is on (default), copy/link to `eval/inputs/<invoke_time>.pkl`.
6. Subprocess: `run_invo_encoding.py -i <pkl_dir> -o <invo.pkl>`.
7. Subprocess: `run_anomaly_detection_invo.py -i <invo.pkl> -o <invo.predicted.pkl> -c <model_path> -t 1 -u <useful_features.txt>`. `useful_features.txt` is generated by the trainer step (or shipped as a small static file containing `{"latency","http_status"}`).
8. Subprocess: `run_localization_association_rule_mining_20210516.py` with `--injected-file <invo.predicted.pkl>` and the localization hyperparams; capture stdout containing the ranked list (or read the resulting pkl which contains `{"Ours-noise=0": list[str]}`).
9. Record `complete_time = time.time()`.
10. Print the ranked list to stdout (one rank per line).
11. Append one JSONL line to `eval/eval.jsonl`:

```json
{
  "invoke_time": 1715632803.124,
  "complete_time": 1715632807.481,
  "service": "ts-order-service",
  "window": "5m",
  "tempo_url": "http://localhost:3200",
  "input_pkl_path": "eval/inputs/1715632803.124.pkl",
  "ranked_list": ["ts-order-service", "ts-station-service", "ts-travel-service"],
  "model_id": "model_live@<sha256_prefix>",
  "model_path": "models/model_live.pkl",
  "source": "cli"
}
```

Exit codes: 0 success, 2 schema/model mismatch, 3 empty window or unknown service, 4 TraceRCA-CD subprocess failed (full stderr captured).

### 3.6 RE2TT control model — `model_re2tt.pkl`

RCAEval RE2-TT ships CSVs, not pkls. To produce `model_re2tt.pkl`:

```bash
# One-time, off the eval path:
mkdir -p data/re2tt
python -m RCAEval.utility.download_re2_dataset --target trainticket --out data/re2tt

# Run TraceRCA-CD's upstream preprocess on the CSVs to make schema-1a pkls:
python vendor/TraceRCA-CD/preprocess_re2tt.py --in data/re2tt --out data/re2tt/pkl

# Train using the same code path as model_live:
python -m tracerca.train \
  --baseline-source pkl-dir \
  --window unused \
  --out models/model_re2tt.pkl \
  --baseline-pkl-dir data/re2tt/pkl/normal
```

This is the control column for the experiment: same algorithm, different training data, isolating the effect of retraining on live baseline.

### 3.7 The shim — `src/shim.py`

Single file, stdlib `http.server` only. ~25 lines. Listens on `127.0.0.1:8080`, parses Alertmanager v4 webhook payloads, shells out to the CLI per alert, appends timing to `eval/eval.jsonl` with `source: "shim"`. Serial; second concurrent alert blocks until the first completes (acceptable per [handoff §6](handoff-architecture-design.md): experimental protocol is serial with recovery windows).

```python
# src/shim.py
import json, os, subprocess, sys, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

EVAL = Path("eval/eval.jsonl")
MODEL = os.environ.get("TRACERCA_MODEL", "models/model_live.pkl")

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        payload = json.loads(body)
        self.send_response(200); self.end_headers()
        for alert in payload.get("alerts", []):
            if alert.get("status") != "firing":
                continue
            service = alert["labels"].get("service_name")
            if not service:
                continue
            t0 = time.time()
            rc = subprocess.run(
                [sys.executable, "-m", "tracerca",
                 "--service", service, "--window", "5m", "--model", MODEL],
                capture_output=True, text=True,
            ).returncode
            t1 = time.time()
            EVAL.parent.mkdir(exist_ok=True)
            with EVAL.open("a") as f:
                f.write(json.dumps({
                    "invoke_time": t0, "complete_time": t1, "service": service,
                    "window": "5m", "source": "shim", "subprocess_rc": rc,
                    "model_path": MODEL,
                }) + "\n")

if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8080), Handler).serve_forever()
```

Documented limitations (intentional, do not "fix"):

- No auth, no health endpoint, no retries, no graceful shutdown.
- Serial: concurrent alerts queue at the Python interpreter; the CLI subprocess is the bottleneck.
- The shim's only reason to exist is to record `t0`/`t1` without human-reaction latency. The CLI also writes its own `eval.jsonl` line (with `source: "cli"`); the two are joined on `invoke_time` proximity at analysis time.

**MTTL definition (used throughout the eval):**

`MTTL = complete_time (shim or CLI line) − wall_clock_apply_time (ground_truth.jsonl)`.

Decomposed:

- `trigger_latency = first_firing_alert_time − apply_time`
- `rca_latency     = complete_time − first_firing_alert_time`

---

## 4. Repo file structure

```
tracerca-prod/
├── CLAUDE.md
├── README.md
├── pyproject.toml                 # Python deps: pandas, scikit-learn, numpy, click, httpx, pyyaml
├── .gitignore                     # ignores eval/, models/, data/, __pycache__, .venv
├── .gitmodules                    # pins vendor/TraceRCA-CD@8df3e44
│
├── docs/
│   ├── handoff-architecture-design.md
│   ├── rca-research-report.md
│   └── architecture.md            # this file
│
├── src/
│   ├── tracerca/                  # the algorithm wrapper (importable package)
│   │   ├── __init__.py
│   │   ├── __main__.py            # inference CLI (python -m tracerca)
│   │   ├── train.py               # trainer (python -m tracerca.train)
│   │   ├── converter.py           # OTLP → schema-1a pkl
│   │   ├── tempo_client.py        # httpx wrapper over /api/search + /api/traces
│   │   ├── schema.py              # schema-version allowlist + sidecar writer/reader
│   │   └── ranked_output.py       # parses TraceRCA-CD's localization output
│   ├── shim.py                    # Alertmanager webhook → CLI shim (stdlib-only)
│   ├── chaos_apply.py             # ground-truth wrapper around kubectl apply
│   ├── eval_runner.py             # serial experiment loop (YAML-driven)
│   └── analysis.py                # AC@K, MTTL, trigger precision/recall
│
├── vendor/
│   └── TraceRCA-CD/               # git submodule, pinned to 8df3e44
│
├── slo/
│   ├── windows/
│   │   └── short-catalog.yaml     # 5m/1m + 15m/3m AlertWindows
│   ├── specs/
│   │   ├── ts-auth.yaml
│   │   ├── ts-order.yaml
│   │   ├── ts-route.yaml
│   │   ├── ts-train.yaml
│   │   └── ts-travel.yaml
│   └── generated/                 # committed sloth-generated rule files
│       ├── ts-auth.rules.yaml
│       ├── ts-order.rules.yaml
│       ├── ts-route.rules.yaml
│       ├── ts-train.rules.yaml
│       └── ts-travel.rules.yaml
│
├── chaos/                         # one manifest per fault × target service
│   ├── cpu_ts-order.yaml
│   ├── memory_ts-order.yaml
│   ├── network-delay_ts-order.yaml
│   ├── network-loss_ts-order.yaml
│   ├── http-error_ts-order.yaml
│   ├── http-delay_ts-order.yaml
│   └── ...                        # same six per other RE2TT service
│
├── deploy/
│   ├── k8s/
│   │   ├── kind-config.yaml
│   │   └── otel-collector/
│   │       ├── 00-namespace.yaml
│   │       ├── 10-configmap.yaml
│   │       ├── 20-deployment.yaml
│   │       ├── 30-service-clusterip.yaml
│   │       └── 31-service-nodeport.yaml
│   └── compose/
│       ├── docker-compose.yaml
│       ├── tempo.yaml
│       ├── prometheus.yml
│       └── alertmanager.yml
│
├── experiments/
│   ├── re2tt-replication.yaml     # eval_runner spec — 5 services × 6 faults × N replicates
│   └── sampling-sweep.yaml        # one spec per head-sampling rate (rerun pipeline manually)
│
├── eval/                          # gitignored, append-only
│   ├── eval.jsonl
│   ├── ground_truth.jsonl
│   └── inputs/
│       └── <invoke_time>.pkl      # saved input pkls for replay
│
├── models/                        # gitignored
│   ├── model_live.pkl
│   ├── model_live.pkl.meta.json
│   ├── model_re2tt.pkl
│   └── model_re2tt.pkl.meta.json
│
└── tasks/
    ├── todo.md
    └── lessons.md
```

---

## 5. Deployment plan

Ordered, executable. After each step, run the listed smoke test.

### 5.1 Time-sync check (one-time)

```bash
date +%s   # host
docker run --rm alpine date +%s   # container
# Within ±1s of each other.
```

### 5.2 K8s side

```bash
# 5.2.1 Create cluster
kind create cluster --config deploy/k8s/kind-config.yaml --wait 5m
# Smoke
kubectl cluster-info --context kind-tracerca

# 5.2.2 cert-manager
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.20.2/cert-manager.yaml
# Smoke
kubectl -n cert-manager rollout status deploy/cert-manager-webhook --timeout=180s

# 5.2.3 Chaos Mesh
kubectl create namespace chaos-mesh
helm repo add chaos-mesh https://charts.chaos-mesh.org && helm repo update
helm install chaos-mesh chaos-mesh/chaos-mesh \
  --namespace chaos-mesh --version 2.8.2 \
  --set chaosDaemon.runtime=containerd \
  --set chaosDaemon.socketPath=/run/containerd/containerd.sock \
  --set dashboard.service.type=NodePort \
  --set dashboard.service.nodePort=30233
# Smoke
kubectl -n chaos-mesh wait --for=condition=Ready pod \
  -l app.kubernetes.io/component=controller-manager --timeout=180s
open http://localhost:2333

# 5.2.4 OTel Collector
kubectl apply -f deploy/k8s/otel-collector/
# Smoke
kubectl -n observability rollout status deploy/otel-collector --timeout=120s
kubectl -n observability port-forward svc/otel-collector 13133:13133 &
curl -s http://127.0.0.1:13133/ && echo OK

# 5.2.5 Train-Ticket (OperationsPAI fork)
git clone --depth 1 https://github.com/OperationsPAI/train-ticket.git /tmp/operationspai-tt
cd /tmp/operationspai-tt && git rev-parse HEAD | tee /tmp/operationspai-tt.sha
# loadgenerator is a remote chart dependency; its repo must be registered
# before `helm dependency build` (rabbitmq resolves from its OCI ref).
helm repo add loadgenerator https://operationspai.github.io/loadgenerator
helm repo update loadgenerator
helm dependency build manifests/helm/trainticket
# Image tag, resource sizing, and the Bitnami insecure-image flag are pinned in
# deploy/k8s/train-ticket-values.yaml (see that file for the why of each):
#  - global.image.tag=v1.2.9  (637600ea / latest do NOT exist on docker.io/opspai)
#  - shrunk resource requests so the ~46-svc fleet packs onto one 8-CPU node
helm install ts manifests/helm/trainticket \
  --namespace ts --create-namespace \
  -f <repo>/deploy/k8s/train-ticket-values.yaml \
  --set global.monitoring=opentelemetry \
  --set skywalking.enabled=false \
  --set opentelemetry.enabled=true \
  --set otelCollector.enabled=false \
  --set global.security.allowInsecureImages=true \
  --set global.otelcollector="http://otel-collector.observability.svc.cluster.local:4317" \
  --set services.tsUiDashboard.nodePort=30080
cd -
# Smoke (~5-10 min)
kubectl -n ts get pods --no-headers | awk '{print $2}' | awk -F/ '{if($1==$2)r++;t++} END{print r"/"t" Ready"}'
# Wait until ≥ 90%, then:
curl -sf http://localhost:30080 | grep -qi "train" && echo "UI OK"
```

### 5.3 Host side

```bash
# 5.3.1 Compose stack
cd deploy/compose
docker compose up -d tempo prometheus alertmanager
# Smoke — Tempo
curl -s http://localhost:3200/ready
# Smoke — Prometheus targets healthy
curl -s 'http://localhost:9090/api/v1/targets' | jq '.data.activeTargets[] | {job: .labels.job, health: .health}'
# Expect otel-collector-spanmetrics: up
# Smoke — Alertmanager
curl -sf http://localhost:9093/-/ready

# 5.3.2 Generate Sloth rules (one-shot)
docker run --rm \
  -v "$PWD/../../slo/specs:/in:ro" \
  -v "$PWD/../../slo/generated:/out" \
  -v "$PWD/../../slo/windows:/windows:ro" \
  ghcr.io/slok/sloth:v0.12.0 \
  generate -i /in -o /out --default-slo-period=30d --slo-period-windows-path=/windows
# Smoke — Prometheus reloads rules
curl -sX POST http://localhost:9090/-/reload
curl -s 'http://localhost:9090/api/v1/rules' | jq '.data.groups | length'  # > 0

# 5.3.3 Shim
cd ../..  # back to repo root
python -m venv .venv && source .venv/bin/activate
pip install -e .
nohup python -m src.shim > shim.log 2>&1 &
# Smoke — POST a fake alert at Alertmanager, watch the shim shell out
curl -sS -X POST http://localhost:9093/api/v2/alerts \
  -H "Content-Type: application/json" \
  -d '[{
    "labels": {"alertname":"TsOrderAvailability","service_name":"ts-order-service","sloth_severity":"page","severity":"page","slo_kind":"availability"},
    "annotations": {"summary":"synthetic"},
    "startsAt": "'"$(date -u +%Y-%m-%dT%H:%M:%SZ)"'"
  }]'
tail -f shim.log   # expect a python -m tracerca invocation within ~10s
```

### 5.4 Baseline-training step (no chaos)

```bash
# Drive load — RCAEval Locust scripts, 30 minutes at moderate RPS
git clone --depth 1 https://github.com/phamquiluan/RCAEval /tmp/rcaeval
locust -f /tmp/rcaeval/load/trainticket/locustfile.py \
  --host http://localhost:30080 \
  --users 50 --spawn-rate 5 --run-time 30m --headless

# Train. Window: last 30 min of fault-free traffic.
python -m tracerca.train \
  --baseline-source tempo-url \
  --tempo-url http://localhost:3200 \
  --window 30m \
  --out models/model_live.pkl
# Smoke — model + sidecar exist; CLI runs end-to-end against a known service
ls -la models/model_live.pkl models/model_live.pkl.meta.json
python -m tracerca --service ts-order-service --window 5m --model models/model_live.pkl
tail -1 eval/eval.jsonl | jq '.ranked_list | length'   # > 0
```

**During this window the shim is up but must NOT fire.** If it does, the SLO is mis-configured (likely false positive from natural Train-Ticket flakiness — adjust the latency budget or per-service objective). Fix before proceeding.

### 5.5 Optional control-model step

```bash
mkdir -p data/re2tt
python -m RCAEval.utility.download_re2_dataset --target trainticket --out data/re2tt
python vendor/TraceRCA-CD/preprocess_re2tt.py --in data/re2tt --out data/re2tt/pkl
python -m tracerca.train \
  --baseline-source pkl-dir \
  --baseline-pkl-dir data/re2tt/pkl/normal \
  --window unused \
  --out models/model_re2tt.pkl
```

---

## 6. End-to-end verification recipe

Assumes §5 is complete and `models/model_live.pkl` exists. This proves the live pipeline works end-to-end.

```bash
# 1. Inject chaos — wrapper logs ground truth then applies the manifest
python -m src.chaos_apply chaos/cpu_ts-order.yaml ts-order-service cpu
# eval/ground_truth.jsonl gains a line:
# {"wall_clock_apply_time": 1715632800.0, "target_service": "ts-order-service",
#  "fault_type": "cpu", "manifest_path": "chaos/cpu_ts-order.yaml"}

# 2. Watch the SLO breach fire
watch -n5 "curl -s http://localhost:9093/api/v2/alerts | jq '.[] | {alert:.labels.alertname, service:.labels.service_name, state:.status.state}'"

# 3. The shim shells out — check shim.log
tail -f shim.log
# expect:
#   python -m tracerca --service ts-order-service --window 5m --model models/model_live.pkl

# 4. Ranked list lands in eval.jsonl with the right model_id and input pkl
tail -1 eval/eval.jsonl | jq '.ranked_list, .model_id, .input_pkl_path'
# expect ranked_list[0] == "ts-order-service"

# 5. Compute the metrics
python -m src.analysis
# Per-fault-type table prints; AC@1 for this case should be 1.0; MTTL printed.

# 6. (Optional) Re-run with the control model
python -m tracerca --service ts-order-service --window 5m \
  --model models/model_re2tt.pkl --save-input-pkl
# AC@1 difference between rows is the retraining-matters evidence.

# Cleanup
kubectl delete -f chaos/cpu_ts-order.yaml
```

---

## 7. Operational-metric instrumentation plan

| Metric (handoff §6) | Where measured | How exported |
|---|---|---|
| **AC@1, AC@3, Avg@5** | `src/analysis.py` joins `eval/eval.jsonl` × `eval/ground_truth.jsonl` by timestamp proximity | Printed table + CSV next to `eval.jsonl` |
| **MTTL** | `complete_time − wall_clock_apply_time`; decomposed via the first `firing` event timestamp pulled from Prometheus (`ALERTS{alertstate="firing"}` series) | Same analysis script |
| **SLO trigger precision / recall** | For each ground-truth fault, check whether an alert with matching `service_name` fired within `apply_time + alert_window` (default 5m). Precision = matched / total alerts; recall = matched / total faults | Analysis script |
| **Throughput (spans/sec sustained)** | Prometheus self-scrape: `rate(traces_spanmetrics_calls_total[1m])` — the spanmetrics counter is the canonical throughput proxy | Grafana panel or `curl /api/v1/query` |
| **Sampling robustness** | Re-run the experiment per `probabilistic_sampler.sampling_percentage` ∈ {100, 10, 1} — flip the OTel Collector ConfigMap, `kubectl rollout restart deployment/otel-collector`, run `eval_runner.py` again | Output table from `analysis.py` with sampling rate as a column |
| **Cold-start time** | Service start (k8s pod Running) → first usable RCA (first eval.jsonl line with `ranked_list != []`). Measured ad hoc via `kubectl get pod -w` + `tail -f eval.jsonl` | One-line script `src/analysis.py --cold-start` |

---

## 8. Eval harness scripts

### 8.1 `src/chaos_apply.py` — ground-truth wrapper

```python
# src/chaos_apply.py
import json, subprocess, sys, time
from pathlib import Path

def main():
    manifest, target, fault = sys.argv[1], sys.argv[2], sys.argv[3]
    Path("eval").mkdir(exist_ok=True)
    with open("eval/ground_truth.jsonl", "a") as f:
        f.write(json.dumps({
            "wall_clock_apply_time": time.time(),
            "target_service": target,
            "fault_type": fault,
            "manifest_path": manifest,
        }) + "\n")
    subprocess.run(["kubectl", "apply", "-f", manifest], check=True)

if __name__ == "__main__":
    main()
```

Call as: `python -m src.chaos_apply chaos/cpu_ts-order.yaml ts-order-service cpu`. The eval runner uses this wrapper exclusively; raw `kubectl apply` is never invoked from the harness.

### 8.2 `src/eval_runner.py` — serial experiment loop

Driven by a YAML spec (`experiments/re2tt-replication.yaml`):

```yaml
# experiments/re2tt-replication.yaml
fault_duration_s: 180          # 3 min
recovery_s: 120                # 2 min between faults
replicates_per_combo: 3
services:
  - ts-auth-service
  - ts-order-service
  - ts-route-service
  - ts-train-service
  - ts-travel-service
faults:
  - { type: cpu,           template: chaos/cpu_{service}.yaml }
  - { type: memory,        template: chaos/memory_{service}.yaml }
  - { type: network-delay, template: chaos/network-delay_{service}.yaml }
  - { type: network-loss,  template: chaos/network-loss_{service}.yaml }
  - { type: http-error,    template: chaos/http-error_{service}.yaml }
  - { type: http-delay,    template: chaos/http-delay_{service}.yaml }
```

```python
# src/eval_runner.py — ~120 lines, serial loop, no magic
import itertools, subprocess, sys, time, yaml
from pathlib import Path

def main(spec_path):
    spec = yaml.safe_load(open(spec_path))
    combos = list(itertools.product(spec["services"], spec["faults"], range(spec["replicates_per_combo"])))
    print(f"[runner] {len(combos)} runs scheduled")
    for service, fault, rep in combos:
        manifest = fault["template"].format(service=service)
        if not Path(manifest).exists():
            print(f"[runner] SKIP {manifest} (not found)")
            continue
        print(f"[runner] apply {service} {fault['type']} rep={rep}")
        subprocess.run([sys.executable, "-m", "src.chaos_apply",
                        manifest, service, fault["type"]], check=True)
        time.sleep(spec["fault_duration_s"])
        # Chaos Mesh experiments have spec.duration; we delete to guarantee cleanup
        subprocess.run(["kubectl", "delete", "-f", manifest, "--ignore-not-found"], check=False)
        print(f"[runner] recovery sleep {spec['recovery_s']}s")
        time.sleep(spec["recovery_s"])
    print("[runner] done")

if __name__ == "__main__":
    main(sys.argv[1])
```

Run: `python -m src.eval_runner experiments/re2tt-replication.yaml`. For sampling-robustness sweeps, the operator flips the Collector ConfigMap manually (`kubectl edit cm/otel-collector-config -n observability && kubectl rollout restart deploy/otel-collector -n observability`) and re-runs the runner.

### 8.3 `src/analysis.py` — post-hoc metrics

Joins `eval/eval.jsonl` × `eval/ground_truth.jsonl` by proximity (default: each ground-truth fault is matched to the *first* `eval.jsonl` line with `complete_time > apply_time` and `service == target_service`, within a window of `fault_duration_s + 5min`). Computes per-pair AC@1, AC@3, Avg@5, MTTL = `complete_time - apply_time`. Aggregates by fault type. Computes SLO trigger precision and recall from the same joined data plus Prometheus `ALERTS{alertstate="firing"}` history.

Outputs:

```
[analysis] per-fault-type results:
fault          n   AC@1   AC@3   Avg@5    MTTL_p50   MTTL_p95   trigger_recall   trigger_precision
cpu            15  0.93   1.00   0.97     12.4s      18.1s      1.00             0.94
memory         15  0.80   0.93   0.89     14.8s      22.0s      1.00             0.92
...
OVERALL        90  0.83   0.94   0.91     14.0s      24.1s      0.99             0.93
```

A single `--model-id` filter lets the operator slice live vs. control. Two runs (`--model-id model_live@*` and `--model-id model_re2tt@*`) produce the side-by-side comparison.

---

## 9. Decisions on stale-instruction conflicts (resolved)

These are conflicts between the prompt's deliverable list and the handoff's settled constraints. Resolved in favor of the constraints, recorded here so the choice is auditable.

| Conflict | Resolution |
|---|---|
| Prompt §2 says "Use the standard Google SRE multi-window multi-burn-rate config (1h/5min, 6h/30min)" for the example Sloth SLO | Used 5min/1min + 15min/3min per the explicit "SLO windows are settled" constraint and handoff §3 |
| Handoff uses short service names (`ts-order`) in some places | Used full Helm-chart names (`ts-order-service`) everywhere selectors, labels, and metric queries appear; short names only in user-facing CLI args and prose |
| Subagent draft used `traces_spanmetrics_latency_bucket` (legacy connector schema) | Standardized on `traces_spanmetrics_duration_milliseconds_bucket` (the actual metric name emitted by spanmetrics connector v0.152.0 with `namespace: traces.spanmetrics`) |

---

## 10. Open questions

Surfaced for the capstone owner — these could not be resolved from the handoff + research report + repo state alone.

1. **`generic_service` vs `trainticket` Helm chart path discrepancy in the OperationsPAI fork.** The fork's README and Makefile reference `manifests/helm/generic_service`, but only `manifests/helm/trainticket/` exists today. The deploy plan uses `trainticket`; if `helm install` errors with "chart not found," check `git log -- manifests/helm/` on the fork.
2. **HTTPChaos percentage targeting.** Chaos Mesh's HTTPChaos CRD has no native `percent` field on `replace`. "Inject on 50% of requests" is not directly expressible. Options: (a) run the experiment for half the test window, (b) use `mode: fixed-percent` to hit half the *pods* (only meaningful if `ts-order-service` runs with `replicas ≥ 2`). The Helm chart's default replica count for `ts-order-service` is not in the `values.yaml` excerpt we read — confirm and decide.
3. **Sampling-fidelity placement for SLO metrics.** The current Collector config puts `probabilistic_sampler` upstream of the `spanmetrics` connector, so the sampling-robustness sweep also degrades SLO-metric statistical power. If we want SLO metrics to stay sampled at 100% while only Tempo storage is sampled, split the trace pipeline via the `forward` connector (unsampled → `spanmetrics`; sampled → `otlp/tempo`). Decide before the sampling-robustness sweep runs — this affects what the sweep actually measures.
4. **`status_code` label value vs. OTel feature gate.** The SLO query assumes `status_code="STATUS_CODE_ERROR"`. If the Collector has `spanmetrics.statusCodeConvention.useOtelPrefix` enabled, the dimension becomes `otel_status_code`. Verify against the live `/metrics` scrape after deployment; update the SLO `error_query` if needed.
5. **`host.docker.internal` on Linux hosts.** Works out-of-the-box on Docker Desktop (macOS/Windows). On a Linux laptop running plain Docker Engine, it isn't bound inside kind nodes by default — workaround is `--add-host=host.docker.internal:host-gateway` on the kind node or use `docker network inspect kind | jq '.[0].IPAM.Config[0].Gateway'`. Document the capstone host platform.
6. **`OperationsPAI/train-ticket master` SHA pinning.** WebFetch of `api.github.com` is not on the allowlist, so the SHA can't be pre-pinned from research time. The deploy plan records it at clone time via `git rev-parse HEAD`; if strict pre-pinning is required for the writeup, look it up via browser and pin in the Helm command as a comment.
7. **`caller_discount_alpha` default value.** The TraceRCA-CD repo's `caller_discount.py` is the headline contribution but its README defaults `α = 0.0` (baseline TraceRCA behavior). To exercise the CD-specific re-ranking pass, the trainer/inference CLI must default this > 0 (the paper used 0.5, but we couldn't verify the exact value from the repo alone). Confirm with the capstone owner.
8. **`run_invo_encoding.py` directory vs file input.** TraceRCA-CD's Makefile invokes it with a directory of raw pkls; we infer the CLI accepts `-i <dir>` but couldn't confirm without trial. If it requires a single pkl, the trainer needs to concatenate; trivially handled but flagged.
9. **RCAEval Locust load profile parameters.** The handoff says "drive realistic load ... document the load profile (RPS, duration, recommend ≥30 min)" but RCAEval doesn't publish a canonical RPS for the offline RE2TT runs. Settled here: 50 users, 5/s spawn, 30 min — the offline-comparability claim weakens slightly without an exact match.
10. **Whether to commit `models/*.pkl`.** Handoff §6 says "gitignored, regenerable from the trainer + sidecar metadata." We honor that. If the capstone owner wants frozen reproducibility (e.g. for a paper artifact submission), they should upload the pkls to a release/Figshare and link from the sidecar. Out of scope for v1.

---

## Citations

Primary documentation pages referenced (each linked at the relevant section):

- OpenTelemetry Collector — [Connector concept](https://opentelemetry.io/docs/collector/configuration/#connectors)
- spanmetrics connector — [README @ v0.152.0](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/v0.152.0/connector/spanmetricsconnector)
- Tempo — [Configuration reference](https://grafana.com/docs/tempo/latest/configuration/), [API docs](https://grafana.com/docs/tempo/latest/api_docs/), [TraceQL](https://grafana.com/docs/tempo/latest/traceql/)
- Prometheus — [Configuration reference](https://prometheus.io/docs/prometheus/latest/configuration/configuration/)
- Alertmanager — [Configuration reference](https://prometheus.io/docs/alerting/latest/configuration/)
- Sloth — [CLI](https://sloth.dev/usage/cli/), [SLO period windows](https://sloth.dev/usage/slo-period-windows/), [alert_rules/v1 plugin](https://sloth.dev/slo-plugins/core/alert_rules_v1/), [v0.12.0 release](https://github.com/slok/sloth/releases/tag/v0.12.0)
- Google SRE Workbook — [Alerting on SLOs](https://sre.google/workbook/alerting-on-slos/)
- kind — [Extra port mappings](https://kind.sigs.k8s.io/docs/user/configuration/#extra-port-mappings), [v0.31.0 release](https://github.com/kubernetes-sigs/kind/releases/tag/v0.31.0)
- cert-manager — [Releases](https://github.com/cert-manager/cert-manager/releases/tag/v1.20.2)
- Chaos Mesh — [Install via Helm](https://chaos-mesh.org/docs/production-installation-using-helm/), [HTTPChaos API types](https://github.com/chaos-mesh/chaos-mesh/blob/v2.8.2/api/v1alpha1/httpchaos_types.go)
- OperationsPAI fork — [Repo](https://github.com/OperationsPAI/train-ticket), `manifests/helm/trainticket/values.yaml`
- TraceRCA-CD — [Repo @ 8df3e44](https://github.com/Jared-Velasquez/TraceRCA-CD/tree/8df3e4431d849f96c206079db3e50c00963cb848)
- RCAEval — [Repo](https://github.com/phamquiluan/RCAEval), [paper](https://arxiv.org/abs/2412.17015), [RE2TT dataset on Figshare](https://figshare.com/articles/dataset/RCAEval_A_Benchmark_for_Root_Cause_Analysis_of_Microservice_Systems/31048672)
