#!/usr/bin/env bash
# scoped-bringup.sh — controlled bring-up for the ts-travel scoped demo.
#
# Why this exists: the OpsPAI trainticket chart hardcodes -Xmx2048m per Java
# service in its deployment template, and `helm upgrade` revert any post-deploy
# env tweaks. Some optimisations therefore can't be expressed as values overlays
# and must be applied imperatively after each `helm upgrade`.
#
# This script is the chart-agnostic safety net. It's idempotent, fast, and
# survives across reboots (its kubectl scale / set env changes persist in the
# Deployment spec).
#
# Verbs:
#   scale-down   Scale out-of-closure ts-* Deployments + rabbitmq StatefulSet to 0.
#   cap-heap     Patch JAVA_TOOL_OPTIONS on closure ts-* to -Xmx384m.
#   trim-chaos   Drop chaos-controller-manager to 1 replica; scale dashboard
#                and dns-server to 0 (NetworkChaos only needs controller+daemon).
#   all          scale-down + cap-heap + trim-chaos (the full safety net).
#   status       Print pod counts + memory-relevant settings (read-only).
#
# Usage:
#   scripts/scoped-bringup.sh all
#   scripts/scoped-bringup.sh status
#
# Re-run after every `helm upgrade ts ...` — cap-heap in particular is reverted
# by Helm reconciliation since the chart's env block isn't values-parameterised.

set -euo pipefail

# ---------------------------------------------------------------------------
# Closure / out-of-closure sets — kept in lock-step with
# deploy/k8s/train-ticket-values-scoped.yaml. If you edit one, edit both.
# ---------------------------------------------------------------------------
NS_TS="ts"
NS_CHAOS="chaos-mesh"

# Deployment names (not chart camelCase keys) — what `kubectl scale` expects.
#
# Closure split into two groups:
#   ts-travel search call-graph (request-fan-out dependencies of /trips/left),
#   AND auth-flow services that the OpsPAI loadgenerator's behavior chains require
#   to clear /users/login → /verifycode/verify/* → downstream behaviors. Without
#   the auth-flow group, loadgen never gets past login and produces no traffic to
#   ts-order/route/train/etc. — so those SLOs are data-starved.
CLOSURE_DEPLOYS=(
  # ts-travel search fan-out
  ts-travel-service
  ts-ui-dashboard
  ts-basic-service
  ts-route-service
  ts-train-service
  ts-station-service
  ts-price-service
  ts-config-service
  ts-seat-service
  ts-order-service
  ts-order-other-service
  # auth-flow (loadgen unblockers; also a direct SLO target)
  ts-auth-service
  ts-user-service
  ts-verification-code-service
  ts-security-service
)

OUT_OF_CLOSURE_DEPLOYS=(
  ts-admin-basic-info-service
  ts-admin-order-service
  ts-admin-route-service
  ts-admin-travel-service
  ts-admin-user-service
  ts-assurance-service
  ts-avatar-service
  ts-cancel-service
  ts-consign-price-service
  ts-consign-service
  ts-contacts-service
  ts-delivery-service
  ts-execute-service
  ts-food-delivery-service
  ts-food-service
  ts-inside-payment-service
  ts-news-service
  ts-notification-service
  ts-payment-service
  ts-preserve-other-service
  ts-preserve-service
  ts-rebook-service
  ts-route-plan-service
  ts-station-food-service
  ts-ticket-office-service
  ts-train-food-service
  ts-travel2-service
  ts-travel-plan-service
  ts-user-service
  ts-verification-code-service
  ts-voucher-service
  ts-wait-order-service
)

# JVM heap target. The chart template's value is -Xmx2048m; 384m comfortably
# fits a Spring Boot service whose normal RSS sits ~200-300m. Tune up if any
# closure service starts OOMKilling.
JAVA_OPTS_OVERRIDE='-javaagent:/otel-agent/otel-agent.jar -Dotel.instrumentation.common.experimental.controller-telemetry.enabled=true -Xmx384m'

log() { printf '  • %s\n' "$*" >&2; }

scale_down() {
  echo "==> scale-down: out-of-closure ts-* Deployments -> 0"
  for d in "${OUT_OF_CLOSURE_DEPLOYS[@]}"; do
    if kubectl -n "$NS_TS" get deploy "$d" >/dev/null 2>&1; then
      kubectl -n "$NS_TS" scale deploy "$d" --replicas=0 >/dev/null
      log "scaled $d -> 0"
    else
      log "skip (not found): $d"
    fi
  done

  echo "==> scale-down: rabbitmq StatefulSet -> 0 (async path; not needed for search)"
  if kubectl -n "$NS_TS" get statefulset rabbitmq >/dev/null 2>&1; then
    kubectl -n "$NS_TS" scale statefulset rabbitmq --replicas=0 >/dev/null
    log "scaled statefulset/rabbitmq -> 0"
  fi
}

cap_heap() {
  echo "==> cap-heap: JAVA_TOOL_OPTIONS=-Xmx384m on closure Java services"
  for d in "${CLOSURE_DEPLOYS[@]}"; do
    # ts-ui-dashboard is the nginx frontend, not Java — skip.
    [[ "$d" == "ts-ui-dashboard" ]] && { log "skip (non-Java): $d"; continue; }
    if kubectl -n "$NS_TS" get deploy "$d" >/dev/null 2>&1; then
      kubectl -n "$NS_TS" set env deploy/"$d" \
        JAVA_TOOL_OPTIONS="$JAVA_OPTS_OVERRIDE" >/dev/null
      log "patched JAVA_TOOL_OPTIONS on $d"
    else
      log "skip (not found): $d"
    fi
  done
  echo "    note: helm upgrade reverts these; re-run cap-heap after every upgrade."
}

trim_chaos() {
  echo "==> trim-chaos: minimise chaos-mesh footprint for NetworkChaos-only use"
  if ! kubectl get ns "$NS_CHAOS" >/dev/null 2>&1; then
    log "namespace $NS_CHAOS not found — skipping"
    return 0
  fi
  # NetworkChaos needs: chaos-controller-manager (>=1) + chaos-daemon (DaemonSet).
  # Dashboard and dns-server are non-essential.
  if kubectl -n "$NS_CHAOS" get deploy chaos-controller-manager >/dev/null 2>&1; then
    kubectl -n "$NS_CHAOS" scale deploy chaos-controller-manager --replicas=1 >/dev/null
    log "chaos-controller-manager -> 1 replica"
  fi
  for d in chaos-dashboard chaos-dns-server; do
    if kubectl -n "$NS_CHAOS" get deploy "$d" >/dev/null 2>&1; then
      kubectl -n "$NS_CHAOS" scale deploy "$d" --replicas=0 >/dev/null
      log "$d -> 0"
    fi
  done
}

status() {
  echo "==> status"
  echo "-- ts namespace pod counts (Running / Total) --"
  kubectl -n "$NS_TS" get pods --no-headers 2>/dev/null \
    | awk '{ if ($3=="Running") r++; t++ } END { printf "  %d / %d\n", r+0, t+0 }'
  echo "-- ts Deployments at replicas=0 --"
  kubectl -n "$NS_TS" get deploy --no-headers \
    -o custom-columns=N:.metadata.name,R:.spec.replicas 2>/dev/null \
    | awk '$2==0 {print "  "$1}' | sort
  echo "-- closure Deployments JAVA_TOOL_OPTIONS heap setting --"
  for d in "${CLOSURE_DEPLOYS[@]}"; do
    [[ "$d" == "ts-ui-dashboard" ]] && continue
    v=$(kubectl -n "$NS_TS" get deploy "$d" \
      -o jsonpath='{.spec.template.spec.containers[0].env[?(@.name=="JAVA_TOOL_OPTIONS")].value}' \
      2>/dev/null || true)
    case "$v" in
      *"-Xmx384m"*) printf "  %-32s  Xmx384m ✓\n" "$d" ;;
      *"-Xmx2048m"*) printf "  %-32s  Xmx2048m (CHART DEFAULT — run cap-heap)\n" "$d" ;;
      "") printf "  %-32s  (no JAVA_TOOL_OPTIONS env / not found)\n" "$d" ;;
      *) printf "  %-32s  %s\n" "$d" "$v" ;;
    esac
  done
  echo "-- chaos-mesh pod count --"
  kubectl -n "$NS_CHAOS" get pods --no-headers 2>/dev/null | wc -l \
    | awk '{print "  "$1" pods"}'
}

case "${1:-}" in
  scale-down) scale_down ;;
  cap-heap)   cap_heap ;;
  trim-chaos) trim_chaos ;;
  all)        scale_down; cap_heap; trim_chaos ;;
  status)     status ;;
  *)
    cat >&2 <<EOF
usage: $0 {scale-down|cap-heap|trim-chaos|all|status}

  scale-down  out-of-closure ts-* Deployments + rabbitmq -> 0 replicas
  cap-heap    JAVA_TOOL_OPTIONS=-Xmx384m on closure Java services
  trim-chaos  shrink chaos-mesh (controller=1, dashboard/dns=0)
  all         scale-down + cap-heap + trim-chaos
  status      read-only check of the above
EOF
    exit 2
    ;;
esac
