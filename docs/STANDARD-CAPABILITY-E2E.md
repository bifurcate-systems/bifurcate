# Standard Agent Capability E2E

This acceptance test is destructive only to the disposable CubeSandbox it
creates. It never publishes external content and it does not mutate Cilium,
Mihomos, Rook/Ceph or scheduler configuration.

## Provider contract

The task requests `sandbox.microvm.standard`; it does not name CubeSandbox as a
hard dependency. For the current acceptance environment the Claim resolver may
select CubeSandbox, after which the runner records a separate provider binding.

The runner expects a thin CubeSandbox adapter for that current binding. The adapter translates these
stable calls to CubeAPI/CubeMaster/envd without reproducing their scheduling or
snapshot behavior:

- `GET /health/ready`
- `POST /v1/sandboxes` with an idempotency key, template, resources, network
  profiles and CephFS workspace request
- `POST /v1/sandboxes/{id}/exec`
- `POST /v1/sandboxes/{id}/pause`
- `POST /v1/sandboxes/{id}/resume`
- `GET /v1/sandboxes/{id}`
- `DELETE /v1/sandboxes/{id}`

Create responses must contain `sandboxId`, `nodeRef`, `volumeRef`,
`networkPolicyRef`, `egressRouteRef`, and sanitized `infrastructureEvidence`.
Secrets and envd access tokens must remain server-side.

## Required environment references

```text
BIFURCATE_URL
CUBE_ADAPTER_URL
CUBE_ADAPTER_TOKEN
CUBE_TEMPLATE_ID
LITELLM_URL
LITELLM_API_KEY
```

Optional settings are `BIFURCATE_MODEL_ALIAS`, `BIFURCATE_SOURCE_URLS`,
`BIFURCATE_WORKSPACE`, and `BIFURCATE_E2E_TIMEOUT_SECONDS`. Credentials must be
injected from Kubernetes Secrets and must never be committed or printed.

Run with:

```bash
python scripts/run_standard_capability_e2e.py
```

Success returns a JSON document containing `status: passed`, the task and trace
IDs, artifact checksums, and the complete task view. Missing provider evidence,
failed deduplication, an unavailable model, a checksum mismatch, or a Sandbox
that survives cleanup makes the command fail.
