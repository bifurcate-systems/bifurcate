from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from fastapi.testclient import TestClient
from app.main import create_app


def test_custom_service_and_usage_are_tenant_isolated():
    client = TestClient(create_app())
    headers = {"X-RE8CH-Tenant": "seller-org"}
    definition = client.post("/api/v1/service-definitions", headers=headers, json={
        "name": "External GPU", "resource_class": "custom.compute.gpu.v1", "version": 1,
        "meters": [{"code": "gpu_second", "unit": "GPU-second", "aggregation": "sum", "source": "signed-provider"}],
    })
    assert definition.status_code == 201
    provider = client.post("/api/v1/external-compute-providers", headers=headers, json={
        "name": "Owner GPU", "resource_class": "custom.compute.gpu.v1", "region_ref": "cn-south",
        "service_definition_version": 1,
        "capabilities": ["cuda"], "capacity": {"gpu": 1}, "evidence_ref": "sha256:" + "a" * 64,
        "meter_codes": ["gpu_second"],
    })
    assert provider.status_code == 201
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    event = client.post("/api/v1/usage-events", headers=headers, json={
        "event_id": "ue_" + "b" * 32, "resource_ref": provider.json()["id"],
        "service_class": "custom.compute.gpu.v1", "service_definition_version": 1,
        "meter": "gpu_second", "quantity": 60,
        "unit": "GPU-second", "period_start": start.isoformat(),
        "period_end": (start + timedelta(seconds=60)).isoformat(), "region_ref": "cn-south",
        "evidence_sha256": "c" * 64,
    })
    assert event.status_code == 202
    assert event.json()["pricing_applied"] is False
    assert client.get("/api/v1/usage-events", headers={"X-RE8CH-Tenant": "other"}).json() == []


def test_resource_dependency_cannot_cross_tenants():
    client = TestClient(create_app())
    one = client.post("/api/v1/resources", headers={"X-RE8CH-Tenant": "one"},
                      json={"name": "one", "kind": "compute"}).json()
    two = client.post("/api/v1/resources", headers={"X-RE8CH-Tenant": "two"},
                      json={"name": "two", "kind": "compute"}).json()
    response = client.post("/api/v1/resource-dependencies", headers={"X-RE8CH-Tenant": "one"}, json={
        "upstream_resource_id": one["id"], "downstream_resource_id": two["id"],
        "purpose": "forbidden",
    })
    assert response.status_code == 403


def test_secret_like_provider_metadata_is_rejected():
    client = TestClient(create_app())
    response = client.post("/api/v1/external-compute-providers", headers={"X-RE8CH-Tenant": "one"}, json={
        "name": "bad", "resource_class": "custom.compute.v1", "region_ref": "x",
        "service_definition_version": 1, "evidence_ref": "sha256:" + "a" * 64,
        "metadata": {"api_key": "must-not-store"},
    })
    assert response.status_code == 422


def test_signed_tenant_context_rejects_spoofing(monkeypatch):
    monkeypatch.setenv("BIFURCATE_TENANT_CONTEXT_SECRET", "test-context-secret")
    client = TestClient(create_app())
    assert client.get("/api/v1/resources", headers={"X-RE8CH-Tenant": "victim"}).status_code == 401
    signature = hmac.new(b"test-context-secret", b"victim", hashlib.sha256).hexdigest()
    response = client.get("/api/v1/resources", headers={
        "X-RE8CH-Tenant": "victim", "X-RE8CH-Tenant-Signature": f"sha256={signature}",
    })
    assert response.status_code == 200


def test_usage_must_reference_owned_provider_and_definition_version():
    client = TestClient(create_app())
    headers = {"X-RE8CH-Tenant": "seller-org"}
    assert client.post("/api/v1/service-definitions", headers=headers, json={
        "name": "GPU", "resource_class": "custom.compute.gpu.v1", "version": 1,
        "meters": [{"code": "gpu_second", "unit": "GPU-second", "aggregation": "sum", "source": "provider"}],
    }).status_code == 201
    start = datetime.now(timezone.utc) - timedelta(minutes=1)
    response = client.post("/api/v1/usage-events", headers=headers, json={
        "event_id": "ue_" + "d" * 32, "resource_ref": "provider_missing",
        "service_class": "custom.compute.gpu.v1", "service_definition_version": 1,
        "meter": "gpu_second", "quantity": 1, "unit": "GPU-second",
        "period_start": start.isoformat(), "period_end": (start + timedelta(seconds=1)).isoformat(),
        "region_ref": "cn-south", "evidence_sha256": "e" * 64,
    })
    assert response.status_code == 422
