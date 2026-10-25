# Deploying FlowForge on Kubernetes

FlowForge ships a Helm chart at [`deployment/kubernetes/helm/flowforge`](https://github.com/Abolfazlrwm/flowforge/tree/main/deployment/kubernetes/helm/flowforge)
and a rendered copy of its default output at
`deployment/kubernetes/manifests/flowforge.yaml`. The chart is the source of
truth; the rendered manifest is committed for operators who prefer
`kubectl apply` over Helm and is regenerated with `helm template` whenever the
chart changes.

## What gets deployed

| Resource | Kind | Notes |
|----------|------|-------|
| `flowforge` | Namespace | The target namespace. |
| `flowforge-secret` | Secret | Created only if `existingSecret` is empty. Holds credentials by reference. |
| `flowforge-config` | ConfigMap | Non-sensitive settings (settings module, throttle rates, …). |
| `flowforge-migrate` | Job (Helm hook) | Runs `manage.py migrate` before workloads start. |
| `flowforge-web` | Deployment + Service | Gunicorn API; `/health/live/` and `/health/ready/` probes. |
| `flowforge-scheduler` | Deployment | Runs `manage.py scheduler`. |
| `flowforge-publisher` | Deployment | Runs `manage.py publisher`. |
| `flowforge-worker` | Deployment + HPA + PDB | Runs `manage.py worker`; autoscaled; disruption-budgeted. |
| NetworkPolicy | ×2 | Default-deny ingress; ingress allowed to web only. |

Every workload runs as a **non-root** user with a read-only root filesystem,
dropped capabilities, `seccompProfile: RuntimeDefault`, resource requests/limits,
and an init container that blocks startup until `manage.py migrate --check`
passes — so no pod serves traffic against an un-migrated schema.

## Prerequisites

The chart does **not** deploy PostgreSQL, RabbitMQ or Redis. Create them
in-cluster (e.g. the Bitnami charts) or point the credentials at managed
services before installing — see [High Availability](ha.md) for how to make
those three survive a failure. Then provide credentials via a Secret:

```bash
kubectl create secret generic flowforge-secret -n flowforge \
  --from-literal=django-secret-key="$(openssl rand -hex 32)" \
  --from-literal=database-url="postgres://flowforge:PASSWORD@postgres:5432/flowforge" \
  --from-literal=rabbitmq-url="amqp://flowforge:PASSWORD@rabbitmq:5672/" \
  --from-literal=redis-url="redis://redis:6379/0"
```

## Install with Helm

**Set the image tag.** `image.tag` is empty by default, which makes the chart fall
back to `.Chart.AppVersion` — and CI publishes only `main` and `main-<commit-sha>`
to `ghcr.io/abolfazlrwm/flowforge`, never a per-version tag. Without
`--set image.tag=…` the pods will fail to pull. Use a `main-<sha>` tag or, better,
a digest:

```bash
IMAGE_TAG="main-$(git rev-list -n1 HEAD)"   # or sha256:... for a pinned deploy

helm install flowforge deployment/kubernetes/helm/flowforge \
  --namespace flowforge --create-namespace \
  --set existingSecret=flowforge-secret \
  --set image.tag="$IMAGE_TAG" \
  --set config.allowedHosts="api.example.com"
```

Or, if you let the chart create its own Secret (not for production):

```bash
helm install flowforge deployment/kubernetes/helm/flowforge \
  --namespace flowforge --create-namespace \
  --set image.tag="$IMAGE_TAG" \
  --set secret.databaseUrl="postgres://..." --set secret.rabbitmqUrl="..." \
  --set secret.redisUrl="..." --set secret.djangoSecretKey="$(openssl rand -hex 32)"
```

## Install with kubectl (no Helm)

```bash
kubectl apply -f deployment/kubernetes/manifests/flowforge.yaml
# then patch/replace the Secret with real values, or delete the chart-managed
# Secret and create your own named flowforge-secret before applying.
```

The committed manifest is the chart's default output, so it pins
`image: ghcr.io/abolfazlrwm/flowforge:1.0.0` — a tag CI does not publish. Replace
the image with a `main-<sha>` tag or a digest before applying (or render the chart
with `--set image.tag=…`, which is the supported path).

## Validation

`helm lint` and `kubeconform -strict` run in CI on every push (see the `k8s`
job in `.github/workflows/ci.yml`). A kind-based end-to-end smoke test is
available on demand via `.github/workflows/k8s-e2e.yml` (manual dispatch) and
`scripts/kind-e2e.sh`.
