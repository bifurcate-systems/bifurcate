from __future__ import annotations

from datetime import timedelta

from fastapi.testclient import TestClient

from app.main import create_app
from app.models import now_utc


def make_client() -> TestClient:
    return TestClient(create_app())


def register_device(client: TestClient) -> tuple[dict, dict, dict]:
    executor = client.post(
        "/api/v1/executors",
        json={
            "name": "android-lab-01",
            "kind": "android_device_agent",
            "connection_hint": {"endpoint": "adb-proxy://lab/android-lab-01"},
        },
    )
    assert executor.status_code == 201
    resource = client.post(
        "/api/v1/resources",
        json={
            "name": "Pixel 8 - WeChat Publisher",
            "kind": "device",
            "binding": {"executor_id": executor.json()["id"], "device_serial": "lab-pixel-8"},
            "capabilities": ["view", "tap", "input", "open_app", "upload_file"],
            "metadata": {"apps": ["com.tencent.mm", "com.tencent.mp"], "login_state": "ready"},
        },
    )
    assert resource.status_code == 201
    account = client.post(
        "/api/v1/account-profiles",
        json={
            "resource_id": resource.json()["id"],
            "label": "WeChat main account",
            "account_ref": "device-local:wechat-main",
            "status": "ready",
            "login_strategy": "human verify_code when challenged",
        },
    )
    assert account.status_code == 201
    return executor.json(), resource.json(), account.json()


def create_request(client: TestClient, resource_id: str, agent_id: str = "agent-publisher-01") -> dict:
    response = client.post(
        "/api/v1/resource-requests",
        json={
            "agent_id": agent_id,
            "resource_id": resource_id,
            "purpose": "Publish article draft through Android app",
            "requested_actions": ["view", "tap", "input", "open_app", "upload_file"],
            "requested_minutes": 30,
        },
    )
    assert response.status_code == 201
    return response.json()


def approve_request(client: TestClient, request_id: str) -> dict:
    response = client.post(
        f"/api/v1/resource-requests/{request_id}/approve",
        json={"approver_id": "owner", "audit_required": True},
    )
    assert response.status_code == 200
    return response.json()


def test_register_android_resource_account_and_state() -> None:
    client = make_client()
    _, resource, account = register_device(client)

    state = client.get(f"/api/v1/resources/{resource['id']}/state")

    assert state.status_code == 200
    body = state.json()
    assert body["resource"]["kind"] == "device"
    assert body["resource"]["status"] == "online"
    assert body["executor"]["kind"] == "android_device_agent"
    assert body["account_profiles"][0]["id"] == account["id"]
    assert body["account_profiles"][0]["account_ref"] == "device-local:wechat-main"
    assert body["recent_audit_events"][0]["event_type"] == "resource.registered"


def test_request_approval_creates_time_boxed_grant_and_reserves_resource() -> None:
    client = make_client()
    _, resource, _ = register_device(client)
    request = create_request(client, resource["id"])

    grant = approve_request(client, request["id"])
    state = client.get(f"/api/v1/resources/{resource['id']}/state").json()

    assert grant["status"] == "active"
    assert grant["actions"] == ["view", "tap", "input", "open_app", "upload_file"]
    assert state["resource"]["status"] == "busy"
    assert state["active_grant"]["id"] == grant["id"]


def test_active_grant_and_run_session_prevent_conflicting_device_approval() -> None:
    client = make_client()
    _, resource, _ = register_device(client)
    first = create_request(client, resource["id"], "agent-a")
    second = create_request(client, resource["id"], "agent-b")
    approve_request(client, first["id"])

    conflict = client.post(
        f"/api/v1/resource-requests/{second['id']}/approve",
        json={"approver_id": "owner"},
    )

    assert conflict.status_code == 409
    assert "reserved" in conflict.json()["detail"]


def test_run_session_human_intervention_and_audit_flow() -> None:
    client = make_client()
    _, resource, _ = register_device(client)
    request = create_request(client, resource["id"])
    grant = approve_request(client, request["id"])

    run = client.post(
        "/api/v1/run-sessions",
        json={
            "grant_id": grant["id"],
            "agent_id": "agent-publisher-01",
            "objective": "Open publisher app and prepare article submission",
        },
    )
    assert run.status_code == 201
    run_body = run.json()

    action = client.post(
        f"/api/v1/run-sessions/{run_body['id']}/actions",
        json={"action": "tap", "summary": "Tapped compose button", "evidence_uri": "screen://latest"},
    )
    assert action.status_code == 201

    task = client.post(
        "/api/v1/human-tasks",
        json={
            "run_session_id": run_body["id"],
            "task_type": "verify_code",
            "title": "WeChat verification challenge",
            "instructions": "Complete verification on the phone.",
        },
    )
    assert task.status_code == 201

    waiting_state = client.get(f"/api/v1/resources/{resource['id']}/state").json()
    assert waiting_state["resource"]["status"] == "needs_intervention"
    assert waiting_state["active_run_session"]["status"] == "waiting_for_human"
    assert waiting_state["pending_human_tasks"][0]["task_type"] == "verify_code"

    completed_task = client.post(
        f"/api/v1/human-tasks/{task.json()['id']}/complete",
        json={"status": "completed", "result": "Verification completed."},
    )
    assert completed_task.status_code == 200

    resumed_state = client.get(f"/api/v1/resources/{resource['id']}/state").json()
    assert resumed_state["resource"]["status"] == "busy"
    assert resumed_state["active_run_session"]["status"] == "active"

    finished = client.post(
        f"/api/v1/run-sessions/{run_body['id']}/complete",
        json={"status": "completed", "summary": "Published draft and released phone."},
    )
    assert finished.status_code == 200

    audit = client.get(f"/api/v1/run-sessions/{run_body['id']}/audit-events").json()
    event_types = [event["event_type"] for event in audit]
    assert "run_session.started" in event_types
    assert "run_session.action_recorded" in event_types
    assert "human_task.created" in event_types
    assert "human_task.completed" in event_types
    assert "run_session.completed" in event_types


def test_grant_expiry_releases_reserved_resource() -> None:
    client = make_client()
    _, resource, _ = register_device(client)
    request = create_request(client, resource["id"])
    grant = approve_request(client, request["id"])

    store = client.app.state.store
    store.grants[grant["id"]].expires_at = now_utc() - timedelta(seconds=1)

    state = client.get(f"/api/v1/resources/{resource['id']}/state").json()

    assert state["active_grant"] is None
    assert state["resource"]["status"] == "online"


def test_credential_like_metadata_keys_are_rejected() -> None:
    client = make_client()
    executor = client.post(
        "/api/v1/executors",
        json={
            "name": "android-lab-01",
            "kind": "android_device_agent",
            "connection_hint": {"endpoint": "adb-proxy://lab/android-lab-01"},
        },
    ).json()

    resource_with_token = client.post(
        "/api/v1/resources",
        json={
            "name": "Unsafe phone",
            "kind": "device",
            "binding": {"executor_id": executor["id"]},
            "metadata": {"refresh_token": "do-not-store"},
        },
    )
    assert resource_with_token.status_code == 422

    resource = client.post(
        "/api/v1/resources",
        json={
            "name": "Safe phone",
            "kind": "device",
            "binding": {"executor_id": executor["id"]},
            "metadata": {"credential_ref": "device-local:wechat-main"},
        },
    )
    assert resource.status_code == 201

    account_with_password = client.post(
        "/api/v1/account-profiles",
        json={
            "resource_id": resource.json()["id"],
            "label": "Unsafe account",
            "account_ref": "device-local:wechat-main",
            "metadata": {"password": "do-not-store"},
        },
    )
    assert account_with_password.status_code == 422


def test_bifurcation_traces_homogeneous_resources_to_fixed_consumers() -> None:
    client = make_client()

    def resource(name: str, kind: str, capabilities: list[str]) -> dict:
        response = client.post(
            "/api/v1/resources", json={"name": name, "kind": kind, "capabilities": capabilities}
        )
        assert response.status_code == 201
        return response.json()

    compute = resource("shared-cpu-ram", "compute", ["reserve", "execute"])
    network = resource("advanced-fabric-egress", "network", ["connect", "egress"])
    storage = resource("rook-cephfs", "storage", ["mount", "read", "write"])
    sandbox = resource("cube-sandbox-runtime", "sandbox", ["create", "execute", "destroy"])

    for upstream, relation in [(compute, "executes_on"), (network, "routes_through"), (storage, "mounts")]:
        response = client.post(
            "/api/v1/resource-dependencies",
            json={
                "upstream_resource_id": upstream["id"],
                "downstream_resource_id": sandbox["id"],
                "relation": relation,
                "purpose": "provide an isolated agent execution cell",
            },
        )
        assert response.status_code == 201

    created = client.post(
        "/api/v1/bifurcations",
        json={
            "agent_id": "agent-research-01",
            "purpose": "run a web research plugin",
            "terminal_resource_id": sandbox["id"],
            "requested_capabilities": ["execute", "egress", "write"],
            "sandbox_template": "agent-general-v1",
            "model_alias": "deepseek-v4-flash",
        },
    )
    assert created.status_code == 201
    bifurcation = created.json()
    assert bifurcation["sandbox_backend"] == "claim"
    assert bifurcation["sandbox_claim_class"] == "sandbox.microvm.standard"
    assert bifurcation["sandbox_provider_ref"] is None
    assert bifurcation["model_gateway"] == "litellm"
    assert bifurcation["model_protocol"] == "openai-compatible/v1"

    lineage = client.get(f"/api/v1/bifurcations/{bifurcation['id']}/lineage")
    assert lineage.status_code == 200
    assert {item["id"] for item in lineage.json()["resources"]} == {
        compute["id"], network["id"], storage["id"], sandbox["id"]
    }
    assert len(lineage.json()["dependencies"]) == 3

    consumed = client.post(
        f"/api/v1/bifurcations/{bifurcation['id']}/consume",
        json={
            "sandbox_provider": "cube-sandbox",
            "sandbox_instance_ref": "cube://sandbox-123",
            "model_route_ref": "litellm://deepseek-v4-flash",
            "evidence_uri": "audit://run-123",
        },
    )
    assert consumed.status_code == 200
    assert consumed.json()["status"] == "consumed"
    assert consumed.json()["sandbox_provider_ref"] == "cube://sandbox-123"


def test_resource_dependency_graph_rejects_cycles() -> None:
    client = make_client()
    first = client.post("/api/v1/resources", json={"name": "first", "kind": "compute"}).json()
    second = client.post("/api/v1/resources", json={"name": "second", "kind": "runtime"}).json()
    assert client.post(
        "/api/v1/resource-dependencies",
        json={"upstream_resource_id": first["id"], "downstream_resource_id": second["id"], "purpose": "runtime uses compute"},
    ).status_code == 201
    cycle = client.post(
        "/api/v1/resource-dependencies",
        json={"upstream_resource_id": second["id"], "downstream_resource_id": first["id"], "purpose": "invalid reverse dependency"},
    )
    assert cycle.status_code == 409
