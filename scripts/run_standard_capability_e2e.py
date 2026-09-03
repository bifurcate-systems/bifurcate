#!/usr/bin/env python3
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class E2EFailure(RuntimeError):
    pass


class JsonClient:
    def __init__(self, base_url: str, token: str = "", timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(self, method: str, path: str, body: Any | None = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        headers = {"accept": "application/json"}
        if data is not None:
            headers["content-type"] = "application/json"
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(self.base_url + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
                return json.loads(payload) if payload else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:2000]
            raise E2EFailure(f"{method} {path} returned {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise E2EFailure(f"{method} {path} failed: {exc.reason}") from exc


@dataclass(frozen=True)
class Config:
    bifurcate_url: str
    cube_adapter_url: str
    cube_token: str
    litellm_url: str
    litellm_token: str
    template_id: str
    model_alias: str
    workspace: str
    source_urls: tuple[str, str, str]
    timeout_seconds: int

    @classmethod
    def from_env(cls) -> "Config":
        required = [
            "BIFURCATE_URL", "CUBE_ADAPTER_URL", "CUBE_ADAPTER_TOKEN",
            "LITELLM_URL", "LITELLM_API_KEY", "CUBE_TEMPLATE_ID",
        ]
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise E2EFailure("missing required environment references: " + ", ".join(missing))
        sources = tuple(filter(None, os.getenv(
            "BIFURCATE_SOURCE_URLS",
            "https://github.com/TencentCloud/CubeSandbox,"
            "https://kubernetes.io/docs/concepts/workloads/pods/,"
            "https://docs.ceph.com/en/latest/cephfs/",
        ).split(",")))
        if len(sources) != 3 or any(not item.startswith("https://") for item in sources):
            raise E2EFailure("BIFURCATE_SOURCE_URLS must contain exactly three HTTPS URLs")
        return cls(
            bifurcate_url=os.environ["BIFURCATE_URL"],
            cube_adapter_url=os.environ["CUBE_ADAPTER_URL"],
            cube_token=os.environ["CUBE_ADAPTER_TOKEN"],
            litellm_url=os.environ["LITELLM_URL"],
            litellm_token=os.environ["LITELLM_API_KEY"],
            template_id=os.environ["CUBE_TEMPLATE_ID"],
            model_alias=os.getenv("BIFURCATE_MODEL_ALIAS", "deepseek-v4-flash"),
            workspace=os.getenv("BIFURCATE_WORKSPACE", "/workspace/bifurcate-e2e"),
            source_urls=sources,  # type: ignore[arg-type]
            timeout_seconds=int(os.getenv("BIFURCATE_E2E_TIMEOUT_SECONDS", "600")),
        )


class StandardCapabilityRun:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.bifurcate = JsonClient(config.bifurcate_url)
        self.cube = JsonClient(config.cube_adapter_url, config.cube_token, timeout=120)
        self.litellm = JsonClient(config.litellm_url, config.litellm_token, timeout=120)
        suffix = uuid4().hex[:12]
        self.task_id = f"capability-{suffix}"
        self.message_id = f"message-{suffix}"
        self.trace_id = f"trace-{suffix}"
        self.agent_id = "bifurcate-standard-research-agent"
        self.sandbox_id = ""
        self.started = time.monotonic()

    def run(self) -> dict[str, Any]:
        self.preflight()
        self.register_and_message()
        self.claim_and_plan()
        intent = self.request_resources()
        try:
            sandbox = self.create_sandbox(intent)
            self.record_evidence("sandbox-provider-binding", f"cube-sandbox://{self.sandbox_id}", "fulfills-sandbox-claim", sandbox)
            self.advance("provisioned", "resource.provisioned", "CubeSandbox and CephFS workspace provisioned")
            self.advance("running", "task.running", "standard web research started")
            source_documents = self.fetch_sources()
            artifacts, model_details = self.generate_artifacts(source_documents)
            self.write_and_verify_artifacts(artifacts)
            self.record_evidence("litellm-request", model_details["request_id"], "models-task", model_details)
            self.record_infrastructure_evidence(sandbox)
            result = {
                "summary": "CubeSandbox and Kubernetes Pod sandbox research completed",
                "artifactUris": [f"cephfs://{self.task_id}/{name}" for name in artifacts],
                "checksums": {name: "sha256:" + hashlib.sha256(content.encode()).hexdigest() for name, content in artifacts.items()},
            }
            self.advance("completed", "task.completed", "research artifacts verified", result=result)
            return self.release(result)
        except Exception as exc:
            self.fail_and_release(str(exc))
            raise

    def preflight(self) -> None:
        health = self.litellm.request("GET", "/health/readiness")
        if health.get("status") not in {"healthy", "ok"}:
            raise E2EFailure(f"LiteLLM is not ready: {health}")
        models = self.litellm.request("GET", "/v1/models")
        model_ids = {item.get("id") for item in models.get("data", [])}
        if self.config.model_alias not in model_ids:
            raise E2EFailure(f"LiteLLM does not expose {self.config.model_alias}")
        cube_health = self.cube.request("GET", "/health/ready")
        if not cube_health.get("ready"):
            raise E2EFailure(f"Cube adapter is not ready: {cube_health}")

    def register_and_message(self) -> None:
        self.bifurcate.request("POST", "/api/v1/agents/register", {
            "agent_id": self.agent_id,
            "version": "e2e/v1",
            "capabilities": ["a2a", "resource-intent", "web-research"],
            "lease_seconds": 600,
        })
        envelope = {
            "messageId": self.message_id,
            "taskId": self.task_id,
            "sender": "bifurcate-capability-suite",
            "recipient": self.agent_id,
            "traceId": self.trace_id,
            "parts": [{"kind": "text", "content": "调研 CubeSandbox 与 Kubernetes Pod Sandbox，并生成三个成果文件。"}],
        }
        first = self.bifurcate.request("POST", "/api/v1/messages", envelope)
        duplicate = self.bifurcate.request("POST", "/api/v1/messages", envelope)
        if first.get("duplicate") or not duplicate.get("duplicate"):
            raise E2EFailure("A2A at-least-once deduplication failed")

    def claim_and_plan(self) -> None:
        self.bifurcate.request("POST", f"/api/v1/tasks/{self.task_id}/claim", {"agentId": self.agent_id})
        self.advance("planning", "agent.planning", "agent generated the standard research plan")

    def request_resources(self) -> dict[str, Any]:
        intent = self.bifurcate.request("POST", f"/api/v1/tasks/{self.task_id}/resource-intent", {
            "purpose": "research public sources and persist three verified artifacts",
            "idempotencyKey": f"{self.task_id}/resources/v1",
            "runtime": {
                "backend": "sandbox-claim",
                "claim_class": "sandbox.microvm.standard",
                "template": "sandbox-code",
                "provider_preferences": ["cube-sandbox"],
            },
            "model": {"gateway": "litellm", "alias": self.config.model_alias, "max_tokens": 8192, "max_cost_usd": 0.10},
        })
        if intent.get("policy_decision", {}).get("effect") != "allow" or not intent.get("bifurcation_id"):
            raise E2EFailure(f"standard resource intent was not approved: {intent}")
        return intent

    def create_sandbox(self, intent: dict[str, Any]) -> dict[str, Any]:
        sandbox = self.cube.request("POST", "/v1/sandboxes", {
            "idempotencyKey": f"{self.task_id}/sandbox/v1",
            "templateId": self.config.template_id,
            "taskId": self.task_id,
            "traceId": self.trace_id,
            "cpu": 2,
            "memoryMiB": 4096,
            "ttlSeconds": 1200,
            "workspace": {"backend": "cephfs", "capacityMiB": 1024, "mountPath": self.config.workspace},
            "networkProfiles": ["cluster-dns", "public-web", "mihomos-global"],
            "bifurcationId": intent["bifurcation_id"],
        })
        self.sandbox_id = str(sandbox.get("sandboxId", ""))
        if not self.sandbox_id or not sandbox.get("nodeRef") or not sandbox.get("volumeRef"):
            raise E2EFailure(f"Cube adapter returned incomplete entity bindings: {sandbox}")
        self.bifurcate.request("POST", f"/api/v1/bifurcations/{intent['bifurcation_id']}/consume", {
            "sandbox_provider": "cube-sandbox",
            "sandbox_instance_ref": self.sandbox_id,
            "model_route_ref": f"litellm://{self.config.model_alias}",
            "evidence_uri": f"audit://{self.trace_id}/sandbox-claim-binding",
        })
        return sandbox

    def fetch_sources(self) -> list[dict[str, str]]:
        documents = []
        for url in self.config.source_urls:
            result = self.exec(["curl", "-fsSL", "--max-time", "30", url])
            text = str(result.get("stdout", ""))
            if result.get("exitCode") != 0 or len(text) < 200:
                raise E2EFailure(f"source fetch failed inside CubeSandbox: {url}")
            documents.append({"url": url, "content": text[:16000]})
        return documents

    def generate_artifacts(self, documents: list[dict[str, str]]) -> tuple[dict[str, str], dict[str, Any]]:
        prompt = {
            "role": "user",
            "content": "Return JSON with keys report_markdown and execution_summary. Compare isolation, startup and persistence. Cite all three source URLs.\n" + json.dumps(documents, ensure_ascii=False),
        }
        response = self.litellm.request("POST", "/v1/chat/completions", {
            "model": self.config.model_alias,
            "messages": [prompt],
            "response_format": {"type": "json_object"},
            "max_tokens": 4096,
        })
        request_id = response.get("id")
        content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not request_id or not content:
            raise E2EFailure(f"LiteLLM returned an incomplete response: {response}")
        generated = json.loads(content)
        sources = [{"url": item["url"], "sha256": hashlib.sha256(item["content"].encode()).hexdigest()} for item in documents]
        summary = {
            "taskId": self.task_id, "messageId": self.message_id, "traceId": self.trace_id,
            "sandboxId": self.sandbox_id, "modelRequestId": request_id, "completedAt": utc_now(),
            "summary": generated["execution_summary"],
        }
        artifacts = {
            "report.md": generated["report_markdown"],
            "sources.json": json.dumps(sources, ensure_ascii=False, indent=2),
            "execution-summary.json": json.dumps(summary, ensure_ascii=False, indent=2),
        }
        usage = response.get("usage", {})
        return artifacts, {"request_id": request_id, "model_alias": self.config.model_alias, "provider": "deepseek", "usage": usage}

    def write_and_verify_artifacts(self, artifacts: dict[str, str]) -> None:
        for name, content in artifacts.items():
            encoded = base64.b64encode(content.encode()).decode()
            script = "import base64,pathlib,sys;p=pathlib.Path(sys.argv[1]);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(base64.b64decode(sys.argv[2]))"
            result = self.exec(["python3", "-c", script, f"{self.config.workspace}/{name}", encoded])
            if result.get("exitCode") != 0:
                raise E2EFailure(f"failed to write {name} in CubeSandbox")
        self.cube.request("POST", f"/v1/sandboxes/{self.sandbox_id}/pause", {})
        self.cube.request("POST", f"/v1/sandboxes/{self.sandbox_id}/resume", {})
        for name, content in artifacts.items():
            result = self.exec(["sha256sum", f"{self.config.workspace}/{name}"])
            expected = hashlib.sha256(content.encode()).hexdigest()
            if result.get("exitCode") != 0 or not str(result.get("stdout", "")).startswith(expected):
                raise E2EFailure(f"CephFS persistence checksum failed for {name}")

    def record_infrastructure_evidence(self, sandbox: dict[str, Any]) -> None:
        mappings = [
            ("kubernetes-node", sandbox["nodeRef"], "scheduled-on"),
            ("ceph-volume", sandbox["volumeRef"], "persists-workspace"),
            ("cilium-policy", sandbox.get("networkPolicyRef", "unknown"), "governs-egress"),
            ("mihomos-route", sandbox.get("egressRouteRef", "unknown"), "routes-global-egress"),
        ]
        for entity_type, entity_ref, relation in mappings:
            if entity_ref == "unknown":
                raise E2EFailure(f"Cube adapter omitted required {entity_type} evidence")
            self.record_evidence(entity_type, entity_ref, relation, sandbox.get("infrastructureEvidence", {}))

    def record_evidence(self, entity_type: str, entity_ref: str, relation: str, details: dict[str, Any]) -> None:
        self.bifurcate.request("POST", f"/api/v1/tasks/{self.task_id}/evidence", {
            "entityType": entity_type,
            "entityRef": entity_ref,
            "relation": relation,
            "details": details,
            "evidenceUri": f"audit://{self.trace_id}/{urllib.parse.quote(entity_type)}",
        })

    def exec(self, argv: list[str]) -> dict[str, Any]:
        return self.cube.request("POST", f"/v1/sandboxes/{self.sandbox_id}/exec", {"argv": argv, "timeoutSeconds": 60})

    def advance(self, status: str, event_type: str, summary: str, result: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"eventType": event_type, "targetStatus": status, "actorId": self.agent_id, "summary": summary}
        if result is not None:
            payload["result"] = result
        self.bifurcate.request("POST", f"/api/v1/tasks/{self.task_id}/events", payload)

    def release(self, result: dict[str, Any]) -> dict[str, Any]:
        self.advance("releasing", "resource.releasing", "releasing sandbox and temporary grants")
        if self.sandbox_id:
            self.cube.request("DELETE", f"/v1/sandboxes/{self.sandbox_id}")
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                state = self.cube.request("GET", f"/v1/sandboxes/{self.sandbox_id}")
                if state.get("phase") in {"deleted", "not-found"}:
                    break
                time.sleep(2)
            else:
                raise E2EFailure("CubeSandbox was not released within 60 seconds")
        self.advance("released", "resource.released", "all temporary resources released")
        view = self.bifurcate.request("GET", f"/api/v1/tasks/{self.task_id}")
        if view.get("task", {}).get("status") != "released" or len(view.get("evidence", [])) < 6:
            raise E2EFailure("final lineage is incomplete")
        return {"status": "passed", "taskId": self.task_id, "traceId": self.trace_id, "result": result, "task": view}

    def fail_and_release(self, reason: str) -> None:
        try:
            view = self.bifurcate.request("GET", f"/api/v1/tasks/{self.task_id}")
            status = view.get("task", {}).get("status")
            if status not in {"failed", "completed", "releasing", "released"}:
                self.advance("failed", "task.failed", reason[:1000])
            if status not in {"releasing", "released"}:
                self.advance("releasing", "resource.releasing", "compensating after failure")
        except Exception:
            pass
        if self.sandbox_id:
            try:
                self.cube.request("DELETE", f"/v1/sandboxes/{self.sandbox_id}")
            except Exception:
                pass
        try:
            self.advance("released", "resource.released", "compensation completed")
        except Exception:
            pass


def main() -> int:
    try:
        result = StandardCapabilityRun(Config.from_env()).run()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": str(exc), "time": utc_now()}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
