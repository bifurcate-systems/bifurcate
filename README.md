# Bifurcate

> Experimental source import from `re8ch/cloud-functions` commit `b35cdf1`.
> Tenant-scoped Catalog resources use verified organization context, immutable
> service-definition versions, provider bindings, and neutral usage events.

Bifurcate is the purpose-to-consumption resource lineage control plane for
agents. It records how homogeneous compute, network, storage, model and context
resources become differentiated for a purpose, which dependency chain was
invoked, and which concrete execution consumed the result.

The execution boundary is a provider-neutral Sandbox Claim. CubeSandbox can
fulfill that Claim, but Agent tasks do not bind directly to it. LiteLLM's
OpenAI-compatible `/v1` API remains the model boundary. Provider names such as
`deepseek-v4-flash` remain aliases behind LiteLLM. Cilium/Advanced
Fabric/Mihomos, Rook/CephFS and cluster compute/runtime remain homogeneous
providers; Bifurcate records their purpose-specific derivation without
replacing their native controllers or the Kubernetes scheduler.

The platform does not store real credentials. CLI tokens, phone login state,
cloud drive sessions, and app passwords stay on the remote executor. This
service stores resource metadata, dependency DAGs, bifurcations, approval
workflow state, time-boxed grants, run sessions, human intervention tasks, and
audit events.

## Resource Lineage

```text
homogeneous compute/network/storage
             │ dependency DAG
             ▼
      purpose bifurcation
         ├─> Sandbox Claim ─> provider binding
         └─> LiteLLM model alias
```

A bifurcation snapshots the ordered resource and dependency IDs used at creation
time. Consumption binds that intent to a CubeSandbox instance, a LiteLLM model
route, and optional evidence URI.

## Run

```bash
python -m pip install -e ".[test]"
python -m uvicorn app.main:create_app --factory --reload --port 8090
```

Open `http://127.0.0.1:8090/console/`.

## MVP Flow

1. Register an Agent and maintain its heartbeat lease.
2. Deliver an A2A message; duplicate `messageId` values are idempotent.
3. Claim the task and submit a purpose-scoped ResourceIntent.
4. Apply the versioned automatic policy and create one Bifurcation.
5. Bind real CubeSandbox, LiteLLM, network, storage and placement evidence.
6. Complete the task with artifact checksums, then release temporary resources.
7. Query by task or entity reference to verify bidirectional lineage.

## API

- `POST /api/v1/agents/register`
- `POST /api/v1/agents/{agent_id}/heartbeat`
- `POST /api/v1/messages`
- `POST /api/v1/tasks/{task_id}/claim`
- `POST /api/v1/tasks/{task_id}/events`
- `POST /api/v1/tasks/{task_id}/resource-intent`
- `POST /api/v1/tasks/{task_id}/evidence`
- `GET /api/v1/tasks/{task_id}`
- `GET /api/v1/entities/tasks?entityRef=...`
- `POST /api/v1/executors`
- `POST /api/v1/resources`
- `GET /api/v1/resources`
- `POST /api/v1/resource-dependencies`
- `GET /api/v1/resource-dependencies`
- `POST /api/v1/bifurcations`
- `POST /api/v1/service-definitions`
- `GET /api/v1/service-definitions`
- `POST /api/v1/external-compute-providers`
- `GET /api/v1/external-compute-providers`
- `POST /api/v1/usage-events`
- `GET /api/v1/usage-events`
- `GET /api/v1/bifurcations/{id}/lineage`
- `POST /api/v1/bifurcations/{id}/consume`
- `GET /api/v1/resources/{resource_id}/state`
- `POST /api/v1/account-profiles`
- `POST /api/v1/resource-requests`
- `POST /api/v1/resource-requests/{request_id}/approve`
- `POST /api/v1/run-sessions`
- `POST /api/v1/run-sessions/{session_id}/actions`
- `POST /api/v1/run-sessions/{session_id}/complete`
- `POST /api/v1/human-tasks`
- `POST /api/v1/human-tasks/{task_id}/complete`
- `GET /api/v1/run-sessions/{session_id}/audit-events`

The live acceptance runner and Cube adapter contract are documented in
[`docs/STANDARD-CAPABILITY-E2E.md`](docs/STANDARD-CAPABILITY-E2E.md).

## Credential Boundary

Metadata is accepted only for coordination state. Requests containing obvious
secret-like keys such as `password`, `token`, `secret`, `api_key`, or
`private_key` are rejected. Store references such as `device-local:wechat-main`
or `devbox-profile:aliyun-prod`, not credentials.

## Tenant Context

Set `BIFURCATE_TENANT_CONTEXT_SECRET` in shared deployments. The trusted Catalog
BFF sends `X-RE8CH-Tenant` together with `X-RE8CH-Tenant-Signature`, computed as
`sha256=<HMAC-SHA256(secret, tenantRef)>`. Direct clients must not be given this
secret. `BIFURCATE_WRITE_TOKEN` continues to protect mutation endpoints.
