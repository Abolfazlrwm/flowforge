# Deploying FlowForge on Kubernetes

The deployment guide for this chart lives in the documentation site:
**[`docs/deployment/kubernetes.md`](../../../docs/deployment/kubernetes.md)**.

This directory holds the chart (`helm/flowforge`) and a rendered copy of its
default output (`manifests/flowforge.yaml`). The chart is the source of truth;
the rendered manifest is committed for operators who prefer `kubectl apply` over
Helm and is regenerated with `helm template` whenever the chart changes. CI runs
`helm lint` and `kubeconform -strict` on every push and fails if the committed
manifest drifts from the chart.
