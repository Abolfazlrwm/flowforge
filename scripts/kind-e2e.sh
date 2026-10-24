#!/usr/bin/env bash
#
# End-to-end smoke test of the FlowForge Kubernetes chart on a local kind cluster.
#
# This is OPT-IN and not part of the required CI gate (which is `helm lint` +
# `kubeconform` in .github/workflows/ci.yml). Run it locally, or via the
# `k8s-e2e` workflow (manual dispatch). It requires Docker, kind, kubectl, helm
# and openssl on PATH.
#
# What it does:
#   1. Creates a kind cluster.
#   2. Deploys PostgreSQL, RabbitMQ and Redis as plain Deployments.
#   3. Installs the FlowForge Helm chart, pointing at those services.
#   4. Obtains an e2e token from `manage.py e2e_token`.
#   5. Runs examples/01_publish_and_run.py against the deployed API.
#
# All credentials are throwaway local values; nothing here is for production.

set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-flowforge-e2e}"
NAMESPACE="${NAMESPACE:-flowforge}"
KIND_IMAGE="${KIND_IMAGE:-kindest/node:v1.31.0}"
PG_PASSWORD="${PG_PASSWORD:-flowforge}"
RABBIT_PASSWORD="${RABBIT_PASSWORD:-flowforge}"

cleanup() {
  echo "==> cleaning up kind cluster"
  kind delete cluster --name "$CLUSTER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

command -v kind >/dev/null 2>&1 || { echo "kind not found on PATH" >&2; exit 1; }
command -v kubectl >/dev/null 2>&1 || { echo "kubectl not found on PATH" >&2; exit 1; }
command -v helm >/dev/null 2>&1 || { echo "helm not found on PATH" >&2; exit 1; }
command -v openssl >/dev/null 2>&1 || { echo "openssl not found on PATH" >&2; exit 1; }

echo "==> creating kind cluster $CLUSTER_NAME"
kind create cluster --name "$CLUSTER_NAME" --image "$KIND_IMAGE" --wait 120s

echo "==> deploying datastores (postgres, rabbitmq, redis)"
kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
kubectl -n "$NAMESPACE" apply -f - <<'YAML'
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres
  labels: { app: postgres }
spec:
  replicas: 1
  selector: { matchLabels: { app: postgres } }
  template:
    metadata: { labels: { app: postgres } }
    spec:
      containers:
        - name: postgres
          image: postgres:16-alpine
          env:
            - { name: POSTGRES_USER, value: postgres }
            - { name: POSTGRES_PASSWORD, value: "${PG_PASSWORD}" }
            - { name: POSTGRES_DB, value: flowforge }
          ports: [ { containerPort: 5432 } ]
          readinessProbe:
            exec: { command: [ pg_isready, -U, postgres, -d, flowforge ] }
            initialDelaySeconds: 5
            periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
spec:
  selector: { app: postgres }
  ports: [ { port: 5432, targetPort: 5432 } ]
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: rabbitmq
  labels: { app: rabbitmq }
spec:
  replicas: 1
  selector: { matchLabels: { app: rabbitmq } }
  template:
    metadata: { labels: { app: rabbitmq } }
    spec:
      containers:
        - name: rabbitmq
          image: rabbitmq:3.13-alpine
          env:
            - { name: RABBITMQ_DEFAULT_USER, value: flowforge }
            - { name: RABBITMQ_DEFAULT_PASS, value: "${RABBIT_PASSWORD}" }
          ports: [ { containerPort: 5672 } ]
          readinessProbe:
            exec: { command: [ rabbitmq-diagnostics, ping ] }
            initialDelaySeconds: 10
            periodSeconds: 10
---
apiVersion: v1
kind: Service
metadata:
  name: rabbitmq
spec:
  selector: { app: rabbitmq }
  ports: [ { port: 5672, targetPort: 5672 } ]
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis
  labels: { app: redis }
spec:
  replicas: 1
  selector: { matchLabels: { app: redis } }
  template:
    metadata: { labels: { app: redis } }
    spec:
      containers:
        - name: redis
          image: redis:7-alpine
          ports: [ { containerPort: 6379 } ]
          readinessProbe:
            exec: { command: [ redis-cli, ping ] }
            initialDelaySeconds: 5
            periodSeconds: 5
---
apiVersion: v1
kind: Service
metadata:
  name: redis
spec:
  selector: { app: redis }
  ports: [ { port: 6379, targetPort: 6379 } ]
YAML

echo "==> waiting for datastores"
kubectl -n "$NAMESPACE" rollout status deploy/postgres --timeout=180s
kubectl -n "$NAMESPACE" rollout status deploy/rabbitmq --timeout=180s
kubectl -n "$NAMESPACE" rollout status deploy/redis --timeout=180s

echo "==> creating flowforge secret"
kubectl -n "$NAMESPACE" create secret generic flowforge-secret \
  --from-literal=django-secret-key="$(openssl rand -hex 32)" \
  --from-literal=database-url="postgres://postgres:${PG_PASSWORD}@postgres:5432/flowforge" \
  --from-literal=rabbitmq-url="amqp://flowforge:${RABBIT_PASSWORD}@rabbitmq:5672/" \
  --from-literal=redis-url="redis://redis:6379/0" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "==> installing flowforge chart"
helm upgrade --install flowforge deployment/kubernetes/helm/flowforge \
  --namespace "$NAMESPACE" --create-namespace \
  --set existingSecret=flowforge-secret \
  --set config.allowedHosts="*" \
  --set worker.autoscaling.enabled=false

kubectl -n "$NAMESPACE" rollout status deploy/flowforge-web --timeout=300s
kubectl -n "$NAMESPACE" rollout status deploy/flowforge-worker --timeout=300s

echo "==> port-forwarding web"
kubectl -n "$NAMESPACE" port-forward svc/flowforge-web 8000:8000 >/tmp/ff-pf.log 2>&1 &
PF="$!"
sleep 5
trap "kill $PF 2>/dev/null || true; cleanup" EXIT

echo "==> obtaining e2e token"
TOKEN="$(kubectl -n "$NAMESPACE" exec deploy/flowforge-web -- python manage.py e2e_token | tail -1)"
export FLOWFORGE_URL=http://localhost:8000
export FLOWFORGE_TOKEN="$TOKEN"

echo "==> installing sdk"
pip install ./sdk >/dev/null 2>&1 || pip install --user ./sdk >/dev/null 2>&1 || true

echo "==> running e2e sample (examples/01_publish_and_run.py)"
python examples/01_publish_and_run.py

echo "==> e2e PASSED"
