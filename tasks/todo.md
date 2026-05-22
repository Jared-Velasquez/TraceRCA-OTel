# Capstone Plan — Live-Demo-First, Scoped Helm-on-kind

## Context / decisions locked

- **RCA accuracy already proven** (TraceRCA-CD on static RE2TT, +5–7pp HR@1). Not re-litigated.
- **Priority**: minimal live OTLP→SLO→trigger demo is PRIMARY; design note is minimal/supporting.
- **Deploy path = scoped Helm-on-kind** (NOT compose). The OperationsPAI fork is Helm-only and
  the whole repo (SLO selectors `app: ts-*-service`, Chaos Mesh selectors, OTel wiring,
  bundled loadgenerator) is built around it. Scope down via a values override extending the
  existing `deploy/k8s/train-ticket-values.yaml` precedent.
- **Trigger contract already exists** (architecture.md §2.6/§3.5): Sloth → Prometheus →
  Alertmanager → webhook → stdlib `src/shim.py` (Alertmanager v4). Demo adopts it.
- **Fault injection = existing Chaos Mesh path** (`chaos/*.yaml` NetworkChaos delay +
  `src/chaos_apply.py`/`src/eval_runner.py`, already built). pumba is moot (was a compose artifact).
- **Demo target**: `ts-travel` (fallback `ts-route` = 2 pods if memory-constrained).
- **Artifact / DoD**: markdown timeline + recorded `eval/eval.jsonl` line + Grafana/Prom
  screenshots of the burn-rate rule firing and trace fan-out.
- **Real resource ceiling**: Docker Desktop VM ≈ 19.5 GiB. Scoped closure must fit under it
  (full ~45-pod fleet is what was OOMing).

## B0 — Investigation (DONE)

- [x] Docker/kind/kubectl/helm verified working; kind cluster `tracerca` exists.
- [x] OperationsPAI fork is Helm-only → decision: scoped Helm, not compose.
- [x] `ts-travel` closure derived: 11 ts-* + 8 mongo (~19–21 pods robust; ~15 strict 2xx
      dropping ts-order/ts-order-other + their mongos). `ts-route` fallback = 2 pods.
- [x] Load source: OperationsPAI `loadgenerator` subchart (already in architecture.md;
      "Advanced search" exercises ts-travel) + optional tiny custom search-only Locust.
      No canonical RE2TT locustfile exists (known open question).

### ts-travel closure (from source-verified call graph)
Required ts-*: travel, ticketinfo, basic, route, train, station, price, config, seat,
order, order-other. MongoDBs: travel, route, train, station, price, config, order,
order-other (ticketinfo/basic/seat are stateless). RabbitMQ NOT needed for search path.
Auth/user NOT needed (UserNoLogin search). Entrypoint: gateway; ui optional for API load.

---

## Track B — Minimal live demo (PRIMARY)

### B0.1 Cluster inspection findings (DONE 2026-05-19)
- Full ~45-svc fleet ALREADY deployed (Helm `ts`, 2d19h) + chaos-mesh + observability.
- Idle memory 8.3/19.5 GiB → crash was cold-start/under-load, not steady state.
- **otel-collector 0/1, readiness failing 2d18h** → THE critical blocker (no pipeline).
- loadgenerator not running; ts-voucher CrashLoop (out of closure, ignore);
  rabbitmq/chaos-dns recovering from node restart; no metrics-server.
- Node `tracerca-control-plane` recovered via `docker start` (kind doesn't auto-restart).

### B1. Telemetry pipeline fix (DONE — files written)
- [x] Root cause: collector exports to `host.docker.internal:4317`, which resolves
      to an unroutable IPv6 from kind pods (CoreDNS lookup fails, then "network
      is unreachable" on the IPv6 fallback). Pod itself was healthy; the 2d18h
      "readiness failures" were the earlier crash-loop, not this defect.
- [x] Fix applied to `deploy/k8s/otel-collector/10-configmap.yaml`: endpoint now
      `172.20.0.1:4317` (kind docker network gateway, stable until kind net is
      recreated). Apply via `kubectl apply -f` after cluster is up.
- [ ] Post-apply verify: spans land in Tempo, spanmetrics in Prometheus.

### B1b. Scope for headroom (built — files written)
The OpsPAI chart was discovered to have:
  - HPA on by default (maxReplicas=3, targetMemUtil=200) — silent 3x multiplier;
  - JVM heap hardcoded to `-Xmx2048m` in deployment.yaml — not values-parameterised;
  - per-svc `replicas` honoured ONLY when `autoscaling.enabled=false`;
  - no mongos (single shared MySQL — closure shrinks to 11 svcs, not 19-21).

Built:
- [x] `deploy/k8s/train-ticket-values-scoped.yaml` — autoscaling off, loadgen off,
      11 closure svcs at replicas=1, 34 out-of-closure svcs at replicas=0.
- [x] `scripts/scoped-bringup.sh` — chart-agnostic safety net. Verbs:
      `scale-down | cap-heap | trim-chaos | all | status`.
      `cap-heap` is mandatory because helm can't set -Xmx via values; re-run after
      every `helm upgrade`.

### B1c. Controlled bring-up sequence (run in order; do not skip)

  # 0. Pre-flight: cluster is down post-reboot. Bring node back.
  docker start tracerca-control-plane
  # Wait for API:
  until kubectl get --raw=/readyz >/dev/null 2>&1; do sleep 3; done

  # 1. Race the start: scale down + cap heap BEFORE the 34 out-of-closure JVMs
  #    finish warming. The Deployment controller will recreate pods on node
  #    start; scaling to 0 here permanently stops that.
  scripts/scoped-bringup.sh all

  # 2. Reconcile via Helm. NOTE: this REVERTS scale-down + cap-heap (chart bugs:
  #    `replicas: 0` is treated as unset by Go templates' `default` function,
  #    and the chart hardcodes -Xmx2048m with no values knob). The HPAs WILL be
  #    deleted because autoscaling.enabled=false is honoured.
  helm dependency build /home/jaredvel25/.cache/opspai-tt/manifests/helm/trainticket
  helm upgrade --install ts /home/jaredvel25/.cache/opspai-tt/manifests/helm/trainticket \
    -n ts \
    -f deploy/k8s/train-ticket-values.yaml \
    -f deploy/k8s/train-ticket-values-scoped.yaml \
    --set global.security.allowInsecureImages=true \
    --set global.otelcollector="http://otel-collector.observability.svc.cluster.local:4317" \
    --set otelCollector.enabled=false \
    --set skywalking.enabled=false \
    --set opentelemetry.enabled=true \
    --set services.tsUiDashboard.nodePort=30080

  # 3. Re-apply BOTH scale-down and cap-heap (helm reverted both).
  scripts/scoped-bringup.sh all

  # 4. Attach host compose services to the kind network so the collector can
  #    reach Tempo (compose_default ↔ kind are isolated; host port publishing
  #    doesn't reach kind pods on Docker Desktop WSL2). Idempotent.
  for c in compose-tempo-1 compose-prometheus-1 compose-alertmanager-1; do
    docker network connect kind "$c" 2>/dev/null || true
  done
  # Re-confirm tempo's IP on the kind network matches the configmap; update if not:
  docker inspect compose-tempo-1 \
    --format '{{.NetworkSettings.Networks.kind.IPAddress}}'

  # 5. Apply collector configmap fix and restart collector.
  kubectl apply -f deploy/k8s/otel-collector/10-configmap.yaml
  kubectl -n observability rollout restart deploy/otel-collector

  # 6. Verify.
  scripts/scoped-bringup.sh status
  kubectl -n observability logs deploy/otel-collector --tail=30 \
    | grep -iE "ready|error|unreachable|timeout" || echo "no errors"

### B1c verified — final state 2026-05-19

- 12/12 closure pods Ready (mysql + 11 ts-* incl. ts-ui-dashboard).
- Docker VM 5.94 / 19.5 GiB (down from 10.4 GiB unsafeguarded).
- WSL distro 6.7 GiB free, 10 GiB available (was 316 MiB free at 93% pressure).
- 0 HPAs in `ts`; chaos-mesh trimmed to 2 pods.
- Closure JVMs all on -Xmx384m.
- Collector logs: "Everything is ready. Begin running and processing data" — quiet.
  No errors/timeouts/unreachable in 15s post-restart window.

---

## B2 — Live demo end-to-end (DONE — trigger fired)

### Sequence proved

  T+  0s  StressChaos cpu applied to ts-travel-service (4 workers, 100% load, 7 min)
  T+ 30s  p99 climbs 2ms → 87ms (above 50ms threshold)
  T+ 30s  slo:sli_error:ratio_rate1m reaches 0.02 (page threshold)
  T+151s  Prometheus marks TsTravelLatencyP99 firing, sev=page
  T+165s  Alertmanager POSTs webhook → src/shim.py writes eval/eval.jsonl line

End-to-end trigger latency: **~165s** apply → shim receipt.

### Bugs fixed along the way (each was real, not demo-only)

1. **Shim bound 127.0.0.1:8080** — Alertmanager in compose can't reach loopback.
   Changed `src/shim.py` to bind 0.0.0.0:8080.
2. **Sloth `le="500"` vs OTel spanmetrics `le="500.0"`** — exact string label mismatch
   silently produced empty rules. ALL pre-existing latency SLIs (auth/order/route/
   train/travel) were broken. Edited `slo/generated/ts-travel.yaml`; same fix
   needed for the other 4 services if their SLOs matter.
3. **Sloth burn rate 172.8 → threshold 1.728** mathematically unreachable
   (SLI ratio is 0..1). Architecture's "extreme by SRE-workbook standards" note
   undersells it — alert can never fire. Calibrated to 2/1/3 for the demo;
   long-term fix requires correcting the AlertWindows catalog math.
4. **Chaos type vs SLI layer**: NetworkChaos `mode:all` self-blinds the OTLP
   export; HTTPChaos delays before the app (invisible to in-app spans).
   Only StressChaos (and target-spec NetworkChaos on dependencies) intersect
   span-based latency SLIs.

### Demo-only calibrations (clearly documented, not bugs)

- SLO threshold tuned 500ms → 50ms (chart's `limits.cpu: 1` caps achievable
  fault-induced p99 at ~200ms; 500ms unreachable in the scoped closure).
- Burn-rate thresholds 5/3/10 → 2/1/3 (sli5m only reaches ~0.04 in a 5-min
  fault since 5m rate window averages partly-quiet history; needed ≤0.02 to fire).
- Direct curl loop against gateway replaces loadgenerator for search traffic
  (loadgen's flows depend on auth services that are out-of-closure).

### Live demo state (still running at end of session)

- 7-min StressChaos active, ~5 min remaining (auto-recovers).
- Alert TsTravelLatencyP99 firing in Prometheus and Alertmanager.
- Webhook delivery to shim recorded in `eval/eval.jsonl` (invoke_time=1779234348).
- Ground truth chain in `eval/ground_truth.jsonl` (8 fault apply entries).

### Cleanup when ready

- Stop the curl loop: `pkill -f curl-loop || pkill -f 'travelservice/trips/left'`
- Stop the shim: `pkill -f 'shim.py'`
- Clear active chaos: `kubectl delete stresschaos -n chaos-mesh --all`

---

## B2 — Generalized SLO fixes to the other 4 services (DONE 2026-05-20)

Applied the two REAL bug fixes (le format + burn-rate calibration) to ts-auth,
ts-order, ts-route, ts-train via sed. Did NOT apply the 500ms→50ms threshold
tuning — that was a closure-specific calibration for ts-travel-service only.

Final state of all 5 SLO services in Prometheus:

| Service | bucket_label | page burn_rates | ticket | health |
|---|---|---|---|---|
| ts-auth-service    | `le="500.0"` | `[1, 2]` | `[3]`  | ok |
| ts-order-service   | `le="500.0"` | `[1, 2]` | `[3]`  | ok |
| ts-route-service   | `le="500.0"` | `[1, 2]` | `[3]`  | ok |
| ts-train-service   | `le="500.0"` | `[1, 2]` | `[3]`  | ok |
| ts-travel-service  | `le="50.0"`  | `[2, 3]` | `[10]` | ok (proven firing) |

Secondary discovery: only ts-travel-service and ts-ui-dashboard currently
receive traffic (curl loop hits ts-travel directly; the simple search call
short-circuits without exercising downstream services). The other 4 SLOs are
structurally valid but data-starved. To breach them via fault injection,
either:
  - re-enable a proper loadgenerator with all flows reachable, OR
  - apply faults to the other services individually after driving traffic
    through their endpoints.

## B2 — Full 5-service SLO uniformity (DONE 2026-05-20)

After the ts-route demo fired successfully, generalised the same calibration
to the remaining three services. All 5 SLO targets now uniform:

  - Histogram bucket: `le="150.0"`  (added 150ms to spanmetrics buckets in
    [deploy/k8s/otel-collector/10-configmap.yaml](deploy/k8s/otel-collector/10-configmap.yaml#L32))
  - Sloth slo label: `latency-p99-150ms`
  - Burn-rate thresholds: 2/1/3 (page first branch / page second branch / ticket)
  - All `health=ok` in Prometheus.

Baseline p99 vs threshold:

  ts-auth-service     97ms / 150ms   (35% headroom)
  ts-order-service     5ms / 150ms   (97% headroom)
  ts-route-service     5ms / 150ms   (97% headroom)
  ts-train-service     5ms / 150ms   (97% headroom)
  ts-travel-service  108ms / 150ms   (28% headroom — possibly noisy)

Live fires recorded in `eval/eval.jsonl`:
  - ts-travel-service ×3 (T+151s, T+165s, cascade from ts-route stress)
  - ts-route-service  ×1 (T+91s under aggressive stress)

## Open follow-ups (not blocking the demo)

- [ ] **Per-service thresholds** — ts-travel's natural ~108ms p99 leaves only
      28% headroom under the uniform 150ms threshold; production practice is
      per-service SLO calibration anyway. Either raise ts-travel to 200ms
      (loses signal at ts-order/route/train) or split per service.
- [ ] **Source-of-truth fix for `le` label format** — patch the Sloth spec or
      add a post-generation sed step so re-running `sloth generate` doesn't
      undo the `le="150.0"` correction. Same for the burn-rate calibration.
- [ ] **Correct the AlertWindows catalog** referenced in architecture.md §2.5
      — the 172.8/144/288 burn rates produce unreachable thresholds; rewrite
      with realistic compressed-window math.
- [ ] **Make subprocess_rc=0** — install the `tracerca` CLI from the sibling
      TraceRCA-CD repo (and provide `models/model_live.pkl`), so the shim's
      RCA-invocation actually completes successfully and the eval.jsonl line
      shows a localization result instead of `rc=1`.
- [ ] **Track A — minimal cited design note** justifying webhook-via-
      Alertmanager transport and the compressed burn-rate windows.

### B2. Load
- [ ] Enable OperationsPAI loadgenerator (or minimal custom Locust: POST
      `/api/v1/travelservice/trips/left`, ~20–50 RPS ≥30 min) so spanmetrics volume is
      non-trivial and the burn-rate rule can evaluate.

### B3. Fault (existing Chaos Mesh path)
- [ ] Confirm Chaos Mesh installed in the cluster (or install; mind its ~6-pod overhead
      vs the 19.5 GiB ceiling — fall back to `ts-route` if tight).
- [ ] Apply `chaos/network-delay_ts-travel.yaml` (or equivalent) via `src/chaos_apply.py`;
      record wall-clock apply time → `eval/ground_truth.jsonl`.

### B4. Trigger path (already built)
- [ ] Run `src/shim.py` in record-only mode (skip TraceRCA-CD subprocess).
- [ ] Verify Alertmanager routes the firing alert → shim webhook → `eval/eval.jsonl`.

### B5. Capture artifact
- [ ] Timeline doc: fault apply → spanmetric shift → burn-rate rule fires (5m/1m) →
      webhook received, with timestamps + the `eval/eval.jsonl` line.
- [ ] Grafana/Prometheus screenshots: burn-rate rule firing + trace fan-out.

---

## Track A — Design note (MINIMAL, supporting, after/alongside B)

- [ ] Short cited note justifying: (a) webhook-via-Alertmanager transport,
      (b) compressed 5m/1m + 15m/3m burn-rate windows vs Google SRE standard given the
      ~3-min fault. Not the full `docs/research-prompt.md`.
- [ ] Reconcile any demo-vs-architecture.md drift.

---

## Verification before "done"

- [ ] Demo reproducible from clean Helm install + one fault script.
- [ ] Trigger latency / fault→fire timeline measured, not asserted.
- [ ] Scoped closure provably fits the ~19.5 GiB VM across a full demo run (no OOM).
- [ ] Would a staff engineer accept the timeline + screenshots as end-to-end evidence?

## Review (fill in after execution)

- _Pending._
