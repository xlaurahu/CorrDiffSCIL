# Exposing Your CorrDiff NIM as a Public Endpoint

This is a detailed, step-by-step guide for turning your **already-deployed** CorrDiff NIM into a
public HTTPS endpoint that others can call from any Python environment — no `kubectl`, no NGC
account, no GPU, no cluster access needed on their end. It documents exactly what was done (and
verified working) for `corrdiff-nim-laurahu` on the `sdsu-shen-climate-lab` namespace, so you can
replicate it for your own deployment on the same cluster.

## Prerequisites

- You've already completed the main [README.md](README.md) deployment steps — your own
  `corrdiff-nim-<USERNAME>` Deployment and `corrdiff-nim-service-<USERNAME>` Service are running.
  Confirm with:
  ```
  kubectl get pods,svc -n <YOUR NAMESPACE> | grep corrdiff
  ```
  You should see your pod `Running` and a `corrdiff-nim-service-<USERNAME>` of `TYPE: ClusterIP`.

## Why this is needed

`ClusterIP` services are only reachable from other pods inside the same Nautilus cluster (e.g.
your own JupyterHub notebook) — not from someone's laptop. To let external users hit it, you need
a Kubernetes **Ingress**, which gives the Service a real public hostname and TLS certificate.

## Step 1 — Copy and edit the Ingress manifest

Start from [corrdiff-nim-ingress.yaml](corrdiff-nim-ingress.yaml) in this repo — it's the
manifest already confirmed working on this cluster, not a generic template. Copy it and replace
every instance of `laurahu` with your own username:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: corrdiff-nim-ingress-<USERNAME>
  namespace: sdsu-shen-climate-lab
  annotations:
    # Default haproxy-ingress timeouts (~50s) cut off long-running diffusion inference
    # requests mid-response even though the NIM finished successfully. Raise them here.
    haproxy-ingress.github.io/timeout-server: "600s"
    haproxy-ingress.github.io/timeout-server-fin: "600s"
    haproxy-ingress.github.io/timeout-client: "600s"
    haproxy-ingress.github.io/timeout-client-fin: "600s"
    haproxy-ingress.github.io/timeout-connect: "600s"
spec:
  ingressClassName: haproxy
  tls:
    - hosts:
        - corrdiff-<USERNAME>.nrp-nautilus.io
  rules:
    - host: corrdiff-<USERNAME>.nrp-nautilus.io
      http:
        paths:
          - path: /
            pathType: ImplementationSpecific
            backend:
              service:
                name: corrdiff-nim-service-<USERNAME>
                port:
                  number: 8000
```

A few things worth knowing about *why* it's written this way, since deviating from these will
break it:

- **`ingressClassName: haproxy`, not `nginx`.** This cluster's ingress controller is haproxy —
  confirmed by inspecting another already-working Ingress in this namespace
  (`kubectl get ingress -n sdsu-shen-climate-lab -o yaml`). Nginx-style annotations are silently
  ignored here.
- **No `cert-manager.io/cluster-issuer` annotation.** TLS for `*.nrp-nautilus.io` hosts is
  auto-provisioned by the cluster; adding a cert-manager annotation isn't necessary and wasn't
  present on the working reference Ingress either.
- **The `timeout-*` annotations are not optional.** Without them, haproxy's default ~50-second
  timeout kills the connection mid-response on any real inference request (e.g.
  `samples=5, steps=10`) — the NIM finishes successfully server-side, but the client sees a
  `ChunkedEncodingError: Response ended prematurely`. This was confirmed by reproducing the exact
  50.3-second cutoff before adding these annotations.
- **`pathType: ImplementationSpecific`, not `Prefix`.** Matches the working reference Ingress on
  this cluster; `Prefix` isn't guaranteed to behave the same way under this controller.

## Step 2 — Apply it

```
kubectl apply -f corrdiff-nim-ingress-<USERNAME>.yaml -n <YOUR NAMESPACE>
```

Check it came up:

```
kubectl get ingress corrdiff-nim-ingress-<USERNAME> -n <YOUR NAMESPACE>
kubectl describe ingress corrdiff-nim-ingress-<USERNAME> -n <YOUR NAMESPACE>
```

The `describe` output should show your host under `TLS: SNI routes ...` and your Service listed
under `Rules` with a backend pod IP — if the backend shows no IP, your NIM pod likely isn't
`Running` yet.

## Step 3 — Verify it's actually reachable

```
curl -sS https://corrdiff-<USERNAME>.nrp-nautilus.io/v1/health/ready
```

Expect `{"status":"ready"}`. If this hangs or errors, give it a minute — TLS cert provisioning
for a brand-new hostname isn't always instant.

## Step 4 — Run a real end-to-end test

Don't stop at the health check — it doesn't exercise the timeout annotations or a full inference
round trip. From a clone of this repo:

```
uv sync
uv run python test_hosted_endpoint.py https://corrdiff-<USERNAME>.nrp-nautilus.io
```

This sends a synthetic (not real weather data) array through the full pipeline — upload, GPU
inference, tar response — in seconds. If you want to specifically confirm the timeout fix, edit
the script's `samples`/`steps` up to something like `5`/`10` and time it; it should now complete
in whatever time the model actually takes rather than dying at ~50 seconds.

## Step 5 — Point people to it

Your endpoint is now live at `https://corrdiff-<USERNAME>.nrp-nautilus.io`. External users can
follow [HostedInference.md](HostedInference.md) to call it from any Python environment — **but
that guide currently hardcodes `corrdiff-laurahu.nrp-nautilus.io` throughout**. You have two
options:

- Tell people to follow `HostedInference.md` but substitute your hostname everywhere it appears
  (`BASE_URL` in Step 1, and the smoke-test command in Setup), or
- Make your own copy of `HostedInference.md` with your hostname substituted, so people don't have
  to do that substitution themselves.

> [!CAUTION]
> This makes your GPU inference endpoint reachable from the public internet with **no
> authentication**. Anyone with the URL can submit inference requests and consume your shared GPU
> quota. Share it only with people you trust to use it responsibly.

## Tearing it down

When you're done hosting (or freeing up the shared GPU per the main README's guidance), delete
both the Ingress and, if you're done entirely, the underlying deployment:

```
kubectl delete ingress corrdiff-nim-ingress-<USERNAME> -n <YOUR NAMESPACE>
# and, per the main README, once you're done with the NIM itself:
kubectl delete deployment corrdiff-nim-<USERNAME> -n <YOUR NAMESPACE>
kubectl delete service corrdiff-nim-service-<USERNAME> -n <YOUR NAMESPACE>
```

## Troubleshooting

- **`describe ingress` shows no backend IP** — your NIM pod isn't `Running` yet, or its labels
  don't match the Service's selector. Check `kubectl get pods -n <YOUR NAMESPACE>`.
- **Health check hangs/times out** — TLS cert not provisioned yet (wait a minute and retry), or
  the Ingress didn't actually apply — recheck `kubectl get ingress`.
- **Real inference requests die at ~50 seconds with `ChunkedEncodingError`** — the timeout
  annotations are missing or didn't apply; recheck they're present in `describe ingress` output
  under `Annotations:`.
- **Other Nautilus namespace, different behavior** — this guide's specifics (haproxy class, no
  cert-manager annotation) were confirmed for `sdsu-shen-climate-lab` specifically. On a different
  namespace, run `kubectl get ingress -n <your namespace> -o yaml` first to find a working example
  to match, rather than assuming these exact values carry over.
