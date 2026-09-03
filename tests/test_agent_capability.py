from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def client_with_agent(agent_id: str = "research-agent") -> TestClient:
    client = TestClient(create_app())
    response = client.post(
        "/api/v1/agents/register",
        json={
            "agent_id": agent_id,
            "version": "1.0.0",
            "capabilities": ["a2a", "resource-intent", "web-research"],
            "lease_seconds": 60,
        },
    )
    assert response.status_code == 201
    return client


def message(task_id: str = "task-standard-001", recipient: str = "research-agent") -> dict:
    return {
        "messageId": "msg-standard-001",
        "taskId": task_id,
        "sender": "capability-test",
        "recipient": recipient,
        "traceId": "trace-standard-001",
        "parts": [
            {
                "kind": "text",
                "content": "Research CubeSandbox versus Kubernetes Pod sandbox and write three artifacts.",
                "media_type": "text/plain",
            }
        ],
    }


def claim_and_plan(client: TestClient, task_id: str = "task-standard-001") -> None:
    assert client.post(
        "/api/v1/tasks/%s/claim" % task_id, json={"agentId": "research-agent"}
    ).status_code == 200
    assert client.post(
        "/api/v1/tasks/%s/events" % task_id,
        json={
            "eventType": "agent.planning",
            "targetStatus": "planning",
            "actorId": "research-agent",
            "summary": "planning standard research task",
        },
    ).status_code == 201


def test_a2a_delivery_is_idempotent_and_identity_scoped() -> None:
    client = client_with_agent()
    first = client.post("/api/v1/messages", json=message())
    duplicate = client.post("/api/v1/messages", json=message())
    assert first.status_code == 202
    assert first.json()["duplicate"] is False
    assert duplicate.status_code == 202
    assert duplicate.json()["duplicate"] is True

    wrong_agent = client.post(
        "/api/v1/tasks/task-standard-001/claim", json={"agentId": "different-agent"}
    )
    assert wrong_agent.status_code == 404

    claimed = client.post(
        "/api/v1/tasks/task-standard-001/claim", json={"agentId": "research-agent"}
    )
    reclaimed = client.post(
        "/api/v1/tasks/task-standard-001/claim", json={"agentId": "research-agent"}
    )
    assert claimed.status_code == 200
    assert reclaimed.status_code == 200
    assert claimed.json()["taskId"] == reclaimed.json()["taskId"]


def test_standard_intent_creates_one_bifurcation_and_entity_reverse_lookup() -> None:
    client = client_with_agent()
    assert client.post("/api/v1/messages", json=message()).status_code == 202
    claim_and_plan(client)
    intent_payload = {
        "purpose": "public web research with durable artifacts",
        "idempotencyKey": "task-standard-001/resources/v1",
    }
    first = client.post("/api/v1/tasks/task-standard-001/resource-intent", json=intent_payload)
    duplicate = client.post("/api/v1/tasks/task-standard-001/resource-intent", json=intent_payload)
    assert first.status_code == 201
    assert duplicate.status_code == 201
    assert first.json()["id"] == duplicate.json()["id"]
    assert first.json()["policy_decision"]["effect"] == "allow"
    bifurcation_id = first.json()["bifurcation_id"]
    assert bifurcation_id
    task_view = client.get("/api/v1/tasks/task-standard-001").json()
    assert task_view["bifurcation"]["sandbox_backend"] == "claim"
    assert task_view["bifurcation"]["sandbox_provider_ref"] is None

    sandbox_ref = "cube://sandbox-standard-001"
    evidence = client.post(
        "/api/v1/tasks/task-standard-001/evidence",
        json={
            "entityType": "cube-sandbox",
            "entityRef": sandbox_ref,
            "relation": "executes-task",
            "details": {"node": "r640", "template": "sandbox-code"},
            "evidenceUri": "audit://cube/standard-001",
        },
    )
    assert evidence.status_code == 201
    reverse = client.get("/api/v1/entities/tasks", params={"entityRef": sandbox_ref})
    assert reverse.status_code == 200
    assert reverse.json()[0]["task"]["taskId"] == "task-standard-001"
    assert reverse.json()[0]["bifurcation"]["id"] == bifurcation_id


def test_policy_denies_privileged_and_over_budget_intent() -> None:
    client = client_with_agent()
    assert client.post("/api/v1/messages", json=message()).status_code == 202
    claim_and_plan(client)
    denied = client.post(
        "/api/v1/tasks/task-standard-001/resource-intent",
        json={
            "purpose": "attempt privileged execution",
            "idempotencyKey": "task-standard-001/denied/v1",
            "compute": {"cpu": 8, "memory_mib": 16384, "max_minutes": 60},
            "network": {"profiles": ["cluster-dns", "unrestricted"], "allowed_methods": ["GET", "POST"]},
            "capabilities": ["host_root", "kubernetes_api"],
        },
    )
    assert denied.status_code == 201
    assert denied.json()["policy_decision"]["effect"] == "deny"
    assert denied.json()["bifurcation_id"] is None
    task = client.get("/api/v1/tasks/task-standard-001").json()
    assert task["task"]["status"] == "failed"


def test_task_state_machine_requires_result_and_releases_cleanly() -> None:
    client = client_with_agent()
    assert client.post("/api/v1/messages", json=message()).status_code == 202
    claim_and_plan(client)
    assert client.post(
        "/api/v1/tasks/task-standard-001/resource-intent",
        json={"purpose": "standard research", "idempotencyKey": "task-standard-001/resources/v1"},
    ).status_code == 201

    def event(target: str, result: dict | None = None) -> int:
        payload = {
            "eventType": "task.%s" % target,
            "targetStatus": target,
            "actorId": "research-agent",
            "summary": "transition to %s" % target,
        }
        if result is not None:
            payload["result"] = result
        return client.post("/api/v1/tasks/task-standard-001/events", json=payload).status_code

    assert event("running") == 409
    assert event("provisioned") == 201
    assert event("running") == 201
    assert event("completed") == 400
    result = {
        "summary": "research completed",
        "artifactUris": ["cephfs://task-standard-001/report.md"],
        "checksums": {"report.md": "sha256:abc"},
    }
    assert event("completed", result) == 201
    assert event("releasing") == 201
    assert event("released") == 201
    assert event("running") == 409


def test_nested_credential_material_is_rejected_from_lineage() -> None:
    client = client_with_agent()
    assert client.post("/api/v1/messages", json=message()).status_code == 202
    leaked = client.post(
        "/api/v1/tasks/task-standard-001/evidence",
        json={
            "entityType": "litellm-request",
            "entityRef": "request-1",
            "relation": "models-task",
            "details": {"provider": {"api_key": "must-not-enter-lineage"}},
        },
    )
    assert leaked.status_code == 422


def test_trace_console_runs_and_lists_simulated_a2() -> None:
    client = TestClient(create_app())
    page = client.get("/trace")
    assert page.status_code == 200
    assert "Bifurcate 能力测试追溯" in page.text

    run = client.post("/api/v1/capability-tests/simulated-a2")
    assert run.status_code == 201
    body = run.json()
    assert body["task"]["status"] == "released"
    assert body["task"]["claimedBy"] == "agent-a2"
    assert body["bifurcation"]["sandbox_backend"] == "claim"
    assert body["bifurcation"]["sandbox_provider_ref"].startswith("sandbox://")
    assert len(body["evidence"]) == 6

    listed = client.get("/api/v1/tasks")
    assert listed.status_code == 200
    assert listed.json()[0]["task"]["taskId"] == body["task"]["taskId"]
