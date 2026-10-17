from __future__ import annotations

from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import RLock

from fastapi import HTTPException

from .models import (
    ALLOWED_ACTIONS,
    AccountProfile,
    AccountProfileCreate,
    A2AMessage,
    A2AMessageCreate,
    Agent,
    AgentHeartbeat,
    AgentRegistration,
    AgentTask,
    ApprovalDecision,
    Bifurcation,
    BifurcationConsumption,
    BifurcationCreate,
    BifurcationLineage,
    AuditEvent,
    AuditEventType,
    CompleteHumanTaskRequest,
    CompleteRunSessionRequest,
    Executor,
    ExecutorCreate,
    EntityEvidence,
    EntityEvidenceCreate,
    Grant,
    GrantStatus,
    HumanTask,
    HumanTaskCreate,
    HumanTaskStatus,
    MessagePart,
    Resource,
    ResourceCreate,
    ResourceDependency,
    ResourceDependencyCreate,
    ResourceKind,
    ResourceStage,
    ResourceStatus,
    ResourceRequest,
    ResourceRequestCreate,
    ResourceIntent,
    ResourceIntentCreate,
    PolicyDecision,
    PolicyEffect,
    RequestStatus,
    ResourceState,
    RunAction,
    RunSession,
    RunSessionCreate,
    RunSessionStatus,
    TaskClaim,
    TaskEvent,
    TaskEventCreate,
    TaskStatus,
    TaskResult,
    TaskView,
    ServiceDefinition,
    ServiceDefinitionCreate,
    ExternalComputeProvider,
    ExternalComputeProviderCreate,
    UsageEvent,
    UsageEventCreate,
    now_utc,
)


class SandboxStore:
    def __init__(self, state_path: str | None = None) -> None:
        self._lock = RLock()
        self.executors: dict[str, Executor] = {}
        self.resources: dict[str, Resource] = {}
        self.dependencies: dict[str, ResourceDependency] = {}
        self.bifurcations: dict[str, Bifurcation] = {}
        self.account_profiles: dict[str, AccountProfile] = {}
        self.requests: dict[str, ResourceRequest] = {}
        self.grants: dict[str, Grant] = {}
        self.run_sessions: dict[str, RunSession] = {}
        self.human_tasks: dict[str, HumanTask] = {}
        self.audit_events: list[AuditEvent] = []
        self.agents: dict[str, Agent] = {}
        self.messages: dict[str, A2AMessage] = {}
        self.tasks: dict[str, AgentTask] = {}
        self.task_events: dict[str, list[TaskEvent]] = {}
        self.resource_intents: dict[str, ResourceIntent] = {}
        self.intent_by_idempotency_key: dict[str, str] = {}
        self.entity_evidence: dict[str, EntityEvidence] = {}
        self.standard_resource_ids: dict[str, str] = {}
        self.service_definitions: dict[str, ServiceDefinition] = {}
        self.external_compute_providers: dict[str, ExternalComputeProvider] = {}
        self.usage_events: dict[str, UsageEvent] = {}
        self.state_path = Path(state_path) if state_path else None
        self._load_state()

    _DICT_MODELS = {
        "executors": Executor,
        "resources": Resource,
        "dependencies": ResourceDependency,
        "bifurcations": Bifurcation,
        "account_profiles": AccountProfile,
        "requests": ResourceRequest,
        "grants": Grant,
        "run_sessions": RunSession,
        "human_tasks": HumanTask,
        "agents": Agent,
        "messages": A2AMessage,
        "tasks": AgentTask,
        "resource_intents": ResourceIntent,
        "entity_evidence": EntityEvidence,
        "service_definitions": ServiceDefinition,
        "external_compute_providers": ExternalComputeProvider,
        "usage_events": UsageEvent,
    }

    def persist(self) -> None:
        """Atomically persist control-plane state on the mounted CephFS workspace."""
        if self.state_path is None:
            return
        with self._lock:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                name: {key: value.model_dump(mode="json") for key, value in getattr(self, name).items()}
                for name in self._DICT_MODELS
            }
            payload["audit_events"] = [item.model_dump(mode="json") for item in self.audit_events]
            payload["task_events"] = {
                key: [item.model_dump(mode="json") for item in values]
                for key, values in self.task_events.items()
            }
            payload["intent_by_idempotency_key"] = self.intent_by_idempotency_key
            payload["standard_resource_ids"] = self.standard_resource_ids
            fd, temporary_name = tempfile.mkstemp(prefix="bifurcate-", suffix=".json", dir=self.state_path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_name, self.state_path)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)

    def _load_state(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        with self.state_path.open(encoding="utf-8") as handle:
            payload = json.load(handle)
        for name, model in self._DICT_MODELS.items():
            setattr(self, name, {
                key: model.model_validate(value) for key, value in payload.get(name, {}).items()
            })
        self.audit_events = [AuditEvent.model_validate(value) for value in payload.get("audit_events", [])]
        self.task_events = {
            key: [TaskEvent.model_validate(value) for value in values]
            for key, values in payload.get("task_events", {}).items()
        }
        self.intent_by_idempotency_key = payload.get("intent_by_idempotency_key", {})
        self.standard_resource_ids = payload.get("standard_resource_ids", {})

    def register_agent(self, payload: AgentRegistration) -> Agent:
        with self._lock:
            current = now_utc()
            existing = self.agents.get(payload.agent_id)
            if existing:
                existing.version = payload.version
                existing.capabilities = payload.capabilities
                existing.lease_seconds = payload.lease_seconds
                existing.metadata = payload.metadata
                existing.last_heartbeat_at = current
                existing.lease_expires_at = current + timedelta(seconds=payload.lease_seconds)
                return existing
            agent = Agent(
                **payload.model_dump(),
                registered_at=current,
                last_heartbeat_at=current,
                lease_expires_at=current + timedelta(seconds=payload.lease_seconds),
            )
            self.agents[agent.agent_id] = agent
            return agent

    def heartbeat_agent(self, agent_id: str, payload: AgentHeartbeat) -> Agent:
        with self._lock:
            agent = self._require_agent(agent_id)
            current = now_utc()
            if payload.lease_seconds is not None:
                agent.lease_seconds = payload.lease_seconds
            agent.last_heartbeat_at = current
            agent.lease_expires_at = current + timedelta(seconds=agent.lease_seconds)
            return agent

    def receive_message(self, payload: A2AMessageCreate) -> A2AMessage:
        with self._lock:
            self._require_live_agent(payload.recipient)
            existing = self.messages.get(payload.message_id)
            if existing:
                expected = payload.model_dump(exclude={"created_at"}, by_alias=False)
                actual = existing.model_dump(exclude={"accepted_at", "duplicate", "created_at"}, by_alias=False)
                if actual != expected:
                    raise HTTPException(status_code=409, detail="messageId was reused with a different envelope")
                return existing.model_copy(update={"duplicate": True})
            existing_task = self.tasks.get(payload.task_id)
            if existing_task:
                raise HTTPException(status_code=409, detail="taskId already belongs to another message")
            message = A2AMessage(**payload.model_dump(), duplicate=False)
            task = AgentTask(
                taskId=payload.task_id,
                messageId=payload.message_id,
                sender=payload.sender,
                recipient=payload.recipient,
                traceId=payload.trace_id,
            )
            self.messages[message.message_id] = message
            self.tasks[task.task_id] = task
            self.task_events[task.task_id] = []
            return message

    def claim_task(self, task_id: str, payload: TaskClaim) -> AgentTask:
        with self._lock:
            task = self._require_task(task_id)
            self._require_live_agent(payload.agent_id)
            if task.recipient != payload.agent_id:
                raise HTTPException(status_code=403, detail="task recipient does not match claiming agent")
            if task.claimed_by == payload.agent_id:
                return task
            if task.status != TaskStatus.SUBMITTED:
                raise HTTPException(status_code=409, detail="task cannot be claimed in its current state")
            task.claimed_by = payload.agent_id
            task.status = TaskStatus.CLAIMED
            task.updated_at = now_utc()
            self._append_task_event(task, TaskEventCreate(
                eventType="task.claimed", targetStatus=TaskStatus.CLAIMED,
                actorId=payload.agent_id, summary="agent claimed task",
            ))
            return task

    def add_task_event(self, task_id: str, payload: TaskEventCreate) -> TaskEvent:
        with self._lock:
            task = self._require_task(task_id)
            if payload.actor_id != task.claimed_by:
                raise HTTPException(status_code=403, detail="only the claiming agent may advance the task")
            allowed = {
                TaskStatus.CLAIMED: {TaskStatus.PLANNING, TaskStatus.FAILED},
                TaskStatus.PLANNING: {TaskStatus.RESOURCE_PENDING, TaskStatus.FAILED},
                TaskStatus.RESOURCE_PENDING: {TaskStatus.PROVISIONED, TaskStatus.FAILED},
                TaskStatus.PROVISIONED: {TaskStatus.RUNNING, TaskStatus.FAILED},
                TaskStatus.RUNNING: {TaskStatus.COMPLETED, TaskStatus.FAILED},
                TaskStatus.COMPLETED: {TaskStatus.RELEASING},
                TaskStatus.FAILED: {TaskStatus.RELEASING},
                TaskStatus.RELEASING: {TaskStatus.RELEASED},
            }
            if payload.target_status not in allowed.get(task.status, set()):
                raise HTTPException(
                    status_code=409,
                    detail=f"invalid task transition {task.status.value}->{payload.target_status.value}",
                )
            if payload.target_status == TaskStatus.COMPLETED and payload.result is None:
                raise HTTPException(status_code=400, detail="completed task event requires result")
            task.status = payload.target_status
            task.updated_at = now_utc()
            if payload.result is not None:
                task.result = payload.result
            return self._append_task_event(task, payload)

    def submit_resource_intent(self, task_id: str, payload: ResourceIntentCreate) -> ResourceIntent:
        with self._lock:
            task = self._require_task(task_id)
            if task.status not in {TaskStatus.PLANNING, TaskStatus.RESOURCE_PENDING}:
                raise HTTPException(status_code=409, detail="resource intent requires a planning task")
            existing_id = self.intent_by_idempotency_key.get(payload.idempotency_key)
            if existing_id:
                existing = self.resource_intents[existing_id]
                if existing.task_id != task_id or existing.model_dump(exclude={"id", "task_id", "trace_id", "policy_decision", "bifurcation_id", "created_at"}) != payload.model_dump():
                    raise HTTPException(status_code=409, detail="idempotency key was reused with a different resource intent")
                return existing
            decision = self._evaluate_resource_intent(payload)
            intent = ResourceIntent(
                **payload.model_dump(), task_id=task_id, trace_id=task.trace_id, policy_decision=decision
            )
            self.resource_intents[intent.id] = intent
            self.intent_by_idempotency_key[payload.idempotency_key] = intent.id
            task.resource_intent_id = intent.id
            if decision.effect == PolicyEffect.DENY:
                task.status = TaskStatus.FAILED
                task.updated_at = now_utc()
                self._append_task_event(task, TaskEventCreate(
                    eventType="resource.denied", targetStatus=TaskStatus.FAILED,
                    actorId=task.claimed_by or task.recipient,
                    summary="resource intent denied by automatic policy",
                    metadata={"reasons": decision.reasons, "policy_version": decision.policy_version},
                ))
                return intent
            terminal_id = self._ensure_standard_resources()
            bifurcation = self.create_bifurcation(BifurcationCreate(
                agent_id=task.claimed_by or task.recipient,
                purpose=payload.purpose,
                terminal_resource_id=terminal_id,
                requested_capabilities=payload.capabilities,
                sandbox_template=payload.runtime.template,
                model_alias=payload.model.alias,
                ttl_minutes=payload.compute.max_minutes,
                metadata={"task_id": task_id, "trace_id": task.trace_id, "intent_id": intent.id},
            ))
            intent.bifurcation_id = bifurcation.id
            task.bifurcation_id = bifurcation.id
            if task.status == TaskStatus.PLANNING:
                task.status = TaskStatus.RESOURCE_PENDING
                task.updated_at = now_utc()
                self._append_task_event(task, TaskEventCreate(
                    eventType="resource.approved", targetStatus=TaskStatus.RESOURCE_PENDING,
                    actorId=task.claimed_by or task.recipient,
                    summary="resource intent approved and bifurcated",
                    metadata={"intent_id": intent.id, "bifurcation_id": bifurcation.id},
                ))
            return intent

    def add_entity_evidence(self, task_id: str, payload: EntityEvidenceCreate) -> EntityEvidence:
        with self._lock:
            task = self._require_task(task_id)
            evidence = EntityEvidence(**payload.model_dump(), task_id=task_id, trace_id=task.trace_id)
            self.entity_evidence[evidence.id] = evidence
            return evidence

    def get_task_view(self, task_id: str) -> TaskView:
        with self._lock:
            task = self._require_task(task_id)
            intent = self.resource_intents.get(task.resource_intent_id or "")
            bifurcation = self.bifurcations.get(task.bifurcation_id or "")
            return TaskView(
                task=task,
                message=self.messages[task.message_id],
                events=self.task_events.get(task_id, []),
                resource_intent=intent,
                bifurcation=bifurcation,
                evidence=[item for item in self.entity_evidence.values() if item.task_id == task_id],
            )

    def tasks_for_entity(self, entity_ref: str) -> list[TaskView]:
        with self._lock:
            task_ids = {item.task_id for item in self.entity_evidence.values() if item.entity_ref == entity_ref}
            return [self.get_task_view(task_id) for task_id in sorted(task_ids)]

    def list_task_views(self) -> list[TaskView]:
        with self._lock:
            ordered = sorted(self.tasks.values(), key=lambda item: item.updated_at, reverse=True)
            return [self.get_task_view(item.task_id) for item in ordered]

    def run_simulated_a2(self) -> TaskView:
        with self._lock:
            suffix = now_utc().strftime("%Y%m%d%H%M%S%f")
            agent_id = "agent-a2"
            task_id = f"task-a2-{suffix}"
            trace_id = f"trace-a2-{suffix}"
            self.register_agent(AgentRegistration(
                agent_id=agent_id,
                version="simulated-a2/v1",
                capabilities=["a2a", "resource-intent", "research"],
                lease_seconds=600,
            ))
            message = A2AMessageCreate(
                messageId=f"msg-a2-{suffix}", taskId=task_id,
                sender="bifurcate-trace-console", recipient=agent_id, traceId=trace_id,
                parts=[MessagePart(kind="text", content="运行标准服务能力测试并生成可追溯成果。")],
            )
            self.receive_message(message)
            self.receive_message(message)
            self.claim_task(task_id, TaskClaim(agentId=agent_id))
            self.add_task_event(task_id, TaskEventCreate(
                eventType="agent.planning", targetStatus=TaskStatus.PLANNING,
                actorId=agent_id, summary="A2 parsed the message and planned the task",
            ))
            intent = self.submit_resource_intent(task_id, ResourceIntentCreate(
                purpose="complete the standard service capability task",
                idempotencyKey=f"{task_id}/resources/v1",
            ))
            if not intent.bifurcation_id:
                raise HTTPException(status_code=500, detail="standard simulated intent was denied")
            self.consume_bifurcation(intent.bifurcation_id, BifurcationConsumption(
                sandbox_provider="simulated-provider-a2",
                sandbox_instance_ref=f"sandbox://{task_id}",
                model_route_ref="litellm://deepseek-v4-flash",
                evidence_uri=f"audit://{trace_id}/binding",
            ))
            evidence = [
                ("sandbox-provider-binding", f"sandbox://{task_id}", "fulfills-sandbox-claim", {"simulation": True}),
                ("network-profile", "cilium://public-web", "governs-network", {"reused": True}),
                ("egress-profile", "mihomos://global", "routes-egress", {"reused": True}),
                ("storage-profile", "cephfs://ephemeral-1g", "persists-artifacts", {"reused": True}),
                ("model-route", "litellm://deepseek-v4-flash", "models-task", {"simulation": True}),
                ("compute-profile", "scheduler://standard-2cpu-4g", "executes-task", {"simulation": True}),
            ]
            for entity_type, entity_ref, relation, details in evidence:
                self.add_entity_evidence(task_id, EntityEvidenceCreate(
                    entityType=entity_type, entityRef=entity_ref, relation=relation,
                    details=details, evidenceUri=f"audit://{trace_id}/{entity_type}",
                ))
            self.add_task_event(task_id, TaskEventCreate(
                eventType="resource.provisioned", targetStatus=TaskStatus.PROVISIONED,
                actorId=agent_id, summary="A2 received approved Claim bindings",
            ))
            self.add_task_event(task_id, TaskEventCreate(
                eventType="task.running", targetStatus=TaskStatus.RUNNING,
                actorId=agent_id, summary="A2 executed the simulated standard task",
            ))
            artifacts = {
                "report.md": "# A2 标准任务报告\n\nMessaging、Claim、Bifurcation、Provider Binding 与回收流程完成。",
                "sources.json": json.dumps([{"type": "service-flow", "traceId": trace_id}], ensure_ascii=False),
                "execution-summary.json": json.dumps({"agent": "A2", "taskId": task_id, "simulation": True}, ensure_ascii=False),
            }
            result = TaskResult(
                summary="A2 completed the simulated standard service task",
                artifactUris=[f"memory://{task_id}/{name}" for name in artifacts],
                checksums={name: "sha256:" + hashlib.sha256(content.encode()).hexdigest() for name, content in artifacts.items()},
            )
            self.add_task_event(task_id, TaskEventCreate(
                eventType="task.completed", targetStatus=TaskStatus.COMPLETED,
                actorId=agent_id, summary=result.summary, result=result,
            ))
            self.add_task_event(task_id, TaskEventCreate(
                eventType="resource.releasing", targetStatus=TaskStatus.RELEASING,
                actorId=agent_id, summary="A2 released temporary Claims",
            ))
            self.add_task_event(task_id, TaskEventCreate(
                eventType="resource.released", targetStatus=TaskStatus.RELEASED,
                actorId=agent_id, summary="A2 cleanup completed",
            ))
            return self.get_task_view(task_id)

    def create_executor(self, payload: ExecutorCreate) -> Executor:
        with self._lock:
            executor = Executor(**payload.model_dump())
            self.executors[executor.id] = executor
            return executor

    def create_resource(self, payload: ResourceCreate, actor_id: str = "owner") -> Resource:
        with self._lock:
            if payload.binding.executor_id and payload.binding.executor_id not in self.executors:
                raise HTTPException(status_code=404, detail="binding executor not found")
            resource = Resource(**payload.model_dump())
            self.resources[resource.id] = resource
            self._audit(
                AuditEventType.RESOURCE_REGISTERED,
                actor_id=actor_id,
                resource_id=resource.id,
                summary=f"registered {resource.kind.value} resource {resource.name}",
            )
            return resource

    def list_resources(self, tenant_ref: str | None = None) -> list[Resource]:
        with self._lock:
            self._expire_grants()
            return [item for item in self.resources.values()
                    if tenant_ref is None or item.owner_tenant_ref == tenant_ref]

    def create_dependency(self, payload: ResourceDependencyCreate, actor_id: str = "owner") -> ResourceDependency:
        with self._lock:
            upstream = self._require_resource(payload.upstream_resource_id)
            downstream = self._require_resource(payload.downstream_resource_id)
            if upstream.owner_tenant_ref != payload.owner_tenant_ref or downstream.owner_tenant_ref != payload.owner_tenant_ref:
                raise HTTPException(status_code=403, detail="cross-tenant resource dependency denied")
            dependency = ResourceDependency(**payload.model_dump())
            self.dependencies[dependency.id] = dependency
            if self._has_dependency_cycle():
                del self.dependencies[dependency.id]
                raise HTTPException(status_code=409, detail="resource dependency would create a cycle")
            self._audit(
                AuditEventType.DEPENDENCY_REGISTERED,
                actor_id=actor_id,
                resource_id=dependency.downstream_resource_id,
                summary=f"registered {dependency.relation.value} dependency",
                metadata={"dependency_id": dependency.id, "upstream_resource_id": dependency.upstream_resource_id},
            )
            return dependency

    def list_dependencies(self, tenant_ref: str | None = None) -> list[ResourceDependency]:
        with self._lock:
            return [item for item in self.dependencies.values()
                    if tenant_ref is None or item.owner_tenant_ref == tenant_ref]

    def create_bifurcation(self, payload: BifurcationCreate) -> Bifurcation:
        with self._lock:
            terminal = self._require_resource(payload.terminal_resource_id)
            if terminal.owner_tenant_ref != payload.owner_tenant_ref:
                raise HTTPException(status_code=403, detail="cross-tenant bifurcation denied")
            resource_ids, edge_ids = self._lineage_for(terminal.id)
            bifurcation = Bifurcation(
                **payload.model_dump(),
                dependency_resource_ids=resource_ids,
                dependency_edge_ids=edge_ids,
            )
            self.bifurcations[bifurcation.id] = bifurcation
            self._audit(
                AuditEventType.BIFURCATION_CREATED,
                actor_id=payload.agent_id,
                resource_id=terminal.id,
                summary=f"differentiated resources for {payload.purpose}",
                metadata={
                    "bifurcation_id": bifurcation.id,
                    "sandbox_backend": bifurcation.sandbox_backend,
                    "model_gateway": bifurcation.model_gateway,
                    "resource_ids": resource_ids,
                },
            )
            return bifurcation

    def create_service_definition(self, tenant_ref: str, payload: ServiceDefinitionCreate) -> ServiceDefinition:
        with self._lock:
            key = f"{tenant_ref}:{payload.resource_class}:v{payload.version}"
            if key in self.service_definitions:
                existing = self.service_definitions[key]
                if existing.model_dump(exclude={"id", "published_at"}) != {
                    **payload.model_dump(), "owner_tenant_ref": tenant_ref
                }:
                    raise HTTPException(status_code=409, detail="published service definition is immutable")
                return existing
            definition = ServiceDefinition(**payload.model_dump(), owner_tenant_ref=tenant_ref)
            self.service_definitions[key] = definition
            return definition

    def list_service_definitions(self, tenant_ref: str) -> list[ServiceDefinition]:
        return [item for item in self.service_definitions.values() if item.owner_tenant_ref == tenant_ref]

    def create_external_compute_provider(self, tenant_ref: str,
                                         payload: ExternalComputeProviderCreate) -> ExternalComputeProvider:
        with self._lock:
            definition_key = (
                f"{tenant_ref}:{payload.resource_class}:v{payload.service_definition_version}"
            )
            definition = self.service_definitions.get(definition_key)
            if definition is None:
                raise HTTPException(status_code=422, detail="service definition is not published by tenant")
            declared_meters = {meter.code for meter in definition.meters}
            if not set(payload.meter_codes).issubset(declared_meters):
                raise HTTPException(status_code=422, detail="provider contains undeclared meters")
            provider = ExternalComputeProvider(**payload.model_dump(), owner_tenant_ref=tenant_ref)
            self.external_compute_providers[provider.id] = provider
            return provider

    def list_external_compute_providers(self, tenant_ref: str) -> list[ExternalComputeProvider]:
        return [item for item in self.external_compute_providers.values() if item.owner_tenant_ref == tenant_ref]

    def record_usage_event(self, tenant_ref: str, payload: UsageEventCreate) -> UsageEvent:
        with self._lock:
            existing = self.usage_events.get(payload.event_id)
            candidate = UsageEvent(**payload.model_dump(), owner_tenant_ref=tenant_ref)
            if existing:
                if existing != candidate:
                    raise HTTPException(status_code=409, detail="usage event idempotency conflict")
                return existing
            definition_key = (
                f"{tenant_ref}:{payload.service_class}:v{payload.service_definition_version}"
            )
            definition = self.service_definitions.get(definition_key)
            if definition is None:
                raise HTTPException(status_code=422, detail="service definition is not published by tenant")
            provider = self.external_compute_providers.get(payload.resource_ref)
            if (
                provider is None
                or provider.owner_tenant_ref != tenant_ref
                or provider.resource_class != payload.service_class
                or provider.service_definition_version != payload.service_definition_version
            ):
                raise HTTPException(status_code=422, detail="usage resource is not an owned provider")
            meter = next((item for item in definition.meters if item.code == payload.meter), None)
            if meter is None or meter.unit != payload.unit or payload.meter not in provider.meter_codes:
                raise HTTPException(status_code=422, detail="usage meter is not declared")
            self.usage_events[payload.event_id] = candidate
            return candidate

    def list_usage_events(self, tenant_ref: str) -> list[UsageEvent]:
        return [item for item in self.usage_events.values() if item.owner_tenant_ref == tenant_ref]

    def consume_bifurcation(self, bifurcation_id: str, payload: BifurcationConsumption,
                            tenant_ref: str | None = None) -> Bifurcation:
        with self._lock:
            bifurcation = self._require_bifurcation(bifurcation_id)
            if tenant_ref is not None and bifurcation.owner_tenant_ref != tenant_ref:
                raise HTTPException(status_code=404, detail="bifurcation not found")
            if bifurcation.status != "ready":
                raise HTTPException(status_code=409, detail="bifurcation is not ready")
            bifurcation.status = "consumed"
            bifurcation.consumed_at = now_utc()
            bifurcation.sandbox_provider_ref = (
                payload.sandbox_instance_ref
                if "://" in payload.sandbox_instance_ref
                else f"{payload.sandbox_provider}://{payload.sandbox_instance_ref}"
            )
            self._audit(
                AuditEventType.BIFURCATION_CONSUMED,
                actor_id=bifurcation.agent_id,
                resource_id=bifurcation.terminal_resource_id,
                summary=f"bound Sandbox Claim to {payload.sandbox_provider} and consumed through {bifurcation.model_gateway}",
                evidence_uri=payload.evidence_uri,
                metadata={
                    "bifurcation_id": bifurcation.id,
                    "sandbox_provider": payload.sandbox_provider,
                    "sandbox_instance_ref": payload.sandbox_instance_ref,
                    "model_route_ref": payload.model_route_ref,
                },
            )
            return bifurcation

    def get_bifurcation_lineage(self, bifurcation_id: str,
                                tenant_ref: str | None = None) -> BifurcationLineage:
        with self._lock:
            bifurcation = self._require_bifurcation(bifurcation_id)
            if tenant_ref is not None and bifurcation.owner_tenant_ref != tenant_ref:
                raise HTTPException(status_code=404, detail="bifurcation not found")
            return BifurcationLineage(
                bifurcation=bifurcation,
                resources=[self.resources[item] for item in bifurcation.dependency_resource_ids],
                dependencies=[self.dependencies[item] for item in bifurcation.dependency_edge_ids],
            )

    def create_account_profile(self, payload: AccountProfileCreate, actor_id: str = "owner") -> AccountProfile:
        with self._lock:
            if payload.resource_id not in self.resources:
                raise HTTPException(status_code=404, detail="resource not found")
            account = AccountProfile(**payload.model_dump())
            self.account_profiles[account.id] = account
            self._audit(
                AuditEventType.ACCOUNT_PROFILE_REGISTERED,
                actor_id=actor_id,
                resource_id=account.resource_id,
                summary=f"registered account profile {account.label}",
            )
            return account

    def create_resource_request(self, payload: ResourceRequestCreate) -> ResourceRequest:
        with self._lock:
            resource = self._require_resource(payload.resource_id)
            if payload.account_profile_id:
                account = self._require_account(payload.account_profile_id)
                if account.resource_id != resource.id:
                    raise HTTPException(status_code=400, detail="account profile does not belong to resource")
            self._validate_actions(resource.kind, payload.requested_actions)
            request = ResourceRequest(**payload.model_dump())
            self.requests[request.id] = request
            self._audit(
                AuditEventType.REQUEST_CREATED,
                actor_id=payload.agent_id,
                resource_id=resource.id,
                request_id=request.id,
                summary=f"requested {resource.name} for {payload.requested_minutes} minutes",
                metadata={"actions": payload.requested_actions},
            )
            return request

    def approve_request(self, request_id: str, decision: ApprovalDecision) -> Grant:
        with self._lock:
            self._expire_grants()
            request = self._require_request(request_id)
            if request.status != RequestStatus.PENDING:
                raise HTTPException(status_code=409, detail="request is already decided")
            resource = self._require_resource(request.resource_id)
            if self._active_grant_for_resource(resource.id) or self._active_run_for_resource(resource.id):
                raise HTTPException(status_code=409, detail="resource already reserved or active")
            actions = decision.approved_actions or request.requested_actions
            self._validate_actions(resource.kind, actions)
            if not set(actions).issubset(set(request.requested_actions)):
                raise HTTPException(status_code=400, detail="approved actions must be a subset of requested actions")
            minutes = decision.minutes or request.requested_minutes
            grant = Grant(
                request_id=request.id,
                resource_id=resource.id,
                account_profile_id=request.account_profile_id,
                executor_id=resource.binding.executor_id,
                actions=actions,
                expires_at=now_utc() + timedelta(minutes=minutes),
                audit_required=decision.audit_required,
            )
            request.status = RequestStatus.APPROVED
            request.decided_at = now_utc()
            request.decision_reason = decision.reason
            request.grant_id = grant.id
            self.grants[grant.id] = grant
            resource.status = ResourceStatus.BUSY
            resource.updated_at = now_utc()
            self._audit(
                AuditEventType.REQUEST_APPROVED,
                actor_id=decision.approver_id,
                resource_id=resource.id,
                request_id=request.id,
                grant_id=grant.id,
                summary=f"approved request {request.id}",
            )
            self._audit(
                AuditEventType.GRANT_CREATED,
                actor_id=decision.approver_id,
                resource_id=resource.id,
                request_id=request.id,
                grant_id=grant.id,
                summary=f"created grant {grant.id}",
                metadata={"actions": actions, "expires_at": grant.expires_at.isoformat()},
            )
            return grant

    def start_run_session(self, payload: RunSessionCreate) -> RunSession:
        with self._lock:
            self._expire_grants()
            grant = self._require_grant(payload.grant_id)
            if grant.status != GrantStatus.ACTIVE or grant.expires_at <= now_utc():
                raise HTTPException(status_code=409, detail="grant is not active")
            if self._active_run_for_resource(grant.resource_id):
                raise HTTPException(status_code=409, detail="resource already has an active run session")
            request = self._require_request(grant.request_id)
            if request.agent_id != payload.agent_id:
                raise HTTPException(status_code=403, detail="grant belongs to a different agent")
            run = RunSession(
                grant_id=grant.id,
                request_id=request.id,
                agent_id=payload.agent_id,
                resource_id=grant.resource_id,
                executor_id=grant.executor_id,
                objective=payload.objective,
            )
            self.run_sessions[run.id] = run
            grant.status = GrantStatus.CONSUMED
            self._audit(
                AuditEventType.RUN_SESSION_STARTED,
                actor_id=payload.agent_id,
                resource_id=run.resource_id,
                request_id=run.request_id,
                grant_id=run.grant_id,
                run_session_id=run.id,
                summary=f"started run session {run.id}",
            )
            return run

    def record_run_action(self, session_id: str, payload: RunAction, actor_id: str | None = None) -> AuditEvent:
        with self._lock:
            run = self._require_run(session_id)
            if run.status not in {RunSessionStatus.ACTIVE, RunSessionStatus.WAITING_FOR_HUMAN}:
                raise HTTPException(status_code=409, detail="run session is not active")
            grant = self._require_grant(run.grant_id)
            if payload.action not in grant.actions:
                raise HTTPException(status_code=403, detail="action is outside grant scope")
            return self._audit(
                AuditEventType.RUN_ACTION_RECORDED,
                actor_id=actor_id or run.agent_id,
                resource_id=run.resource_id,
                request_id=run.request_id,
                grant_id=run.grant_id,
                run_session_id=run.id,
                summary=payload.summary,
                evidence_uri=payload.evidence_uri,
                metadata={"action": payload.action, **payload.metadata},
            )

    def complete_run_session(self, session_id: str, payload: CompleteRunSessionRequest) -> RunSession:
        with self._lock:
            run = self._require_run(session_id)
            if run.status in {RunSessionStatus.COMPLETED, RunSessionStatus.FAILED}:
                raise HTTPException(status_code=409, detail="run session already completed")
            run.status = payload.status
            run.summary = payload.summary
            run.ended_at = now_utc()
            resource = self._require_resource(run.resource_id)
            if resource.status == ResourceStatus.BUSY:
                resource.status = ResourceStatus.ONLINE
                resource.updated_at = now_utc()
            self._audit(
                AuditEventType.RUN_SESSION_COMPLETED,
                actor_id=run.agent_id,
                resource_id=run.resource_id,
                request_id=run.request_id,
                grant_id=run.grant_id,
                run_session_id=run.id,
                summary=payload.summary or f"run session {payload.status.value}",
            )
            return run

    def create_human_task(self, payload: HumanTaskCreate, actor_id: str = "workflow") -> HumanTask:
        with self._lock:
            run = self._require_run(payload.run_session_id)
            if payload.assigned_human_resource_id:
                human = self._require_resource(payload.assigned_human_resource_id)
                if human.kind != ResourceKind.HUMAN:
                    raise HTTPException(status_code=400, detail="assigned resource must be human")
            task = HumanTask(**payload.model_dump())
            self.human_tasks[task.id] = task
            run.status = RunSessionStatus.WAITING_FOR_HUMAN
            resource = self._require_resource(run.resource_id)
            resource.status = ResourceStatus.NEEDS_INTERVENTION
            resource.updated_at = now_utc()
            self._audit(
                AuditEventType.HUMAN_TASK_CREATED,
                actor_id=actor_id,
                resource_id=run.resource_id,
                request_id=run.request_id,
                grant_id=run.grant_id,
                run_session_id=run.id,
                human_task_id=task.id,
                summary=f"created human task {task.title}",
                metadata={"task_type": task.task_type},
            )
            return task

    def complete_human_task(self, task_id: str, payload: CompleteHumanTaskRequest, actor_id: str = "human") -> HumanTask:
        with self._lock:
            task = self._require_human_task(task_id)
            if task.status in {HumanTaskStatus.COMPLETED, HumanTaskStatus.FAILED}:
                raise HTTPException(status_code=409, detail="human task already completed")
            run = self._require_run(task.run_session_id)
            task.status = payload.status
            task.result = payload.result
            task.completed_at = now_utc()
            if payload.status == HumanTaskStatus.COMPLETED:
                run.status = RunSessionStatus.ACTIVE
                resource = self._require_resource(run.resource_id)
                resource.status = ResourceStatus.BUSY
                resource.updated_at = now_utc()
                event_type = AuditEventType.HUMAN_TASK_COMPLETED
            else:
                run.status = RunSessionStatus.WAITING_FOR_HUMAN
                event_type = AuditEventType.HUMAN_TASK_FAILED
            self._audit(
                event_type,
                actor_id=actor_id,
                resource_id=run.resource_id,
                request_id=run.request_id,
                grant_id=run.grant_id,
                run_session_id=run.id,
                human_task_id=task.id,
                summary=payload.result,
            )
            return task

    def get_resource_state(self, resource_id: str, tenant_ref: str | None = None) -> ResourceState:
        with self._lock:
            self._expire_grants()
            resource = self._require_resource(resource_id)
            if tenant_ref is not None and resource.owner_tenant_ref != tenant_ref:
                raise HTTPException(status_code=404, detail="resource not found")
            executor = self.executors.get(resource.binding.executor_id or "")
            account_profiles = [account for account in self.account_profiles.values() if account.resource_id == resource_id]
            active_grant = self._active_grant_for_resource(resource_id)
            active_run_session = self._active_run_for_resource(resource_id)
            pending_human_tasks = [
                task
                for task in self.human_tasks.values()
                if task.status in {HumanTaskStatus.OPEN, HumanTaskStatus.CLAIMED}
                and self.run_sessions.get(task.run_session_id, None)
                and self.run_sessions[task.run_session_id].resource_id == resource_id
            ]
            recent_audit_events = [event for event in self.audit_events if event.resource_id == resource_id][-20:]
            return ResourceState(
                resource=resource,
                executor=executor,
                account_profiles=account_profiles,
                active_grant=active_grant,
                active_run_session=active_run_session,
                pending_human_tasks=pending_human_tasks,
                recent_audit_events=recent_audit_events,
            )

    def list_resource_requests(self) -> list[ResourceRequest]:
        with self._lock:
            return list(self.requests.values())

    def list_human_tasks(self) -> list[HumanTask]:
        with self._lock:
            return list(self.human_tasks.values())

    def list_run_sessions(self) -> list[RunSession]:
        with self._lock:
            return list(self.run_sessions.values())

    def audit_for_run_session(self, session_id: str) -> list[AuditEvent]:
        with self._lock:
            self._require_run(session_id)
            return [event for event in self.audit_events if event.run_session_id == session_id]

    def _validate_actions(self, kind: ResourceKind, actions: list[str]) -> None:
        allowed = ALLOWED_ACTIONS[kind]
        unknown = [action for action in actions if action not in allowed]
        if unknown:
            raise HTTPException(status_code=400, detail=f"unsupported actions for {kind.value}: {', '.join(unknown)}")

    def _append_task_event(self, task: AgentTask, payload: TaskEventCreate) -> TaskEvent:
        event = TaskEvent(
            **payload.model_dump(), task_id=task.task_id, trace_id=task.trace_id
        )
        self.task_events.setdefault(task.task_id, []).append(event)
        return event

    def _evaluate_resource_intent(self, payload: ResourceIntentCreate) -> PolicyDecision:
        reasons: list[str] = []
        if payload.runtime.backend != "sandbox-claim":
            reasons.append("runtime must request a sandbox-claim")
        if payload.runtime.claim_class != "sandbox.microvm.standard":
            reasons.append("sandbox claim class is not approved")
        if payload.runtime.template != "sandbox-code":
            reasons.append("runtime template is not approved")
        if payload.model.gateway != "litellm":
            reasons.append("model gateway must be litellm")
        if payload.compute.cpu > 2 or payload.compute.memory_mib > 4096 or payload.compute.max_minutes > 20:
            reasons.append("compute request exceeds the standard test budget")
        if payload.storage.backend != "cephfs" or payload.storage.capacity_mib > 1024 or payload.storage.retention != "ephemeral":
            reasons.append("storage request exceeds the ephemeral CephFS policy")
        if payload.model.max_tokens > 8192 or payload.model.max_cost_usd > 0.10:
            reasons.append("model request exceeds the token or cost budget")
        allowed_profiles = {"cluster-dns", "public-web", "mihomos-global"}
        if not set(payload.network.profiles).issubset(allowed_profiles):
            reasons.append("network profile is not approved")
        if set(payload.network.allowed_methods) - {"GET"}:
            reasons.append("external write methods are not approved")
        allowed_capabilities = {"http_get", "dns", "file_read", "file_write", "exec_restricted"}
        if not set(payload.capabilities).issubset(allowed_capabilities):
            reasons.append("one or more capabilities are privileged or unsupported")
        return PolicyDecision(
            effect=PolicyEffect.DENY if reasons else PolicyEffect.ALLOW,
            reasons=reasons or ["request matches the standard agent capability profile"],
        )

    def _ensure_standard_resources(self) -> str:
        specs = {
            "compute": ResourceCreate(
                name="cluster-cpu-ram", kind=ResourceKind.COMPUTE,
                capabilities=["reserve", "execute"], metadata={"profile": "standard-agent-compute"},
            ),
            "network": ResourceCreate(
                name="cilium-mihomos-global", kind=ResourceKind.NETWORK,
                capabilities=["connect", "egress"], metadata={"profile": "mihomos-global"},
            ),
            "storage": ResourceCreate(
                name="rook-cephfs-ephemeral", kind=ResourceKind.STORAGE,
                capabilities=["mount", "read", "write"], metadata={"profile": "cephfs-ephemeral"},
            ),
            "model": ResourceCreate(
                name="litellm-model-route", kind=ResourceKind.MODEL,
                capabilities=["infer"], metadata={"gateway": "litellm"},
            ),
            "sandbox": ResourceCreate(
                name="sandbox-microvm-standard-claim", kind=ResourceKind.SANDBOX,
                stage=ResourceStage.DIFFERENTIATED,
                capabilities=["create", "execute", "destroy"],
                metadata={"claim_class": "sandbox.microvm.standard", "provider_binding": "deferred"},
            ),
        }
        for key, spec in specs.items():
            if key not in self.standard_resource_ids:
                self.standard_resource_ids[key] = self.create_resource(spec, actor_id="bifurcate-policy").id
        terminal_id = self.standard_resource_ids["sandbox"]
        relations = {
            "compute": "executes_on",
            "network": "routes_through",
            "storage": "mounts",
            "model": "modeled_by",
        }
        from .models import DependencyRelation
        for key, relation in relations.items():
            upstream_id = self.standard_resource_ids[key]
            exists = any(
                item.upstream_resource_id == upstream_id and item.downstream_resource_id == terminal_id
                for item in self.dependencies.values()
            )
            if not exists:
                self.create_dependency(ResourceDependencyCreate(
                    upstream_resource_id=upstream_id,
                    downstream_resource_id=terminal_id,
                    relation=DependencyRelation(relation),
                    purpose="standard agent capability execution",
                ), actor_id="bifurcate-policy")
        return terminal_id

    def _lineage_for(self, terminal_resource_id: str) -> tuple[list[str], list[str]]:
        resource_ids: list[str] = []
        edge_ids: list[str] = []
        visited: set[str] = set()

        def visit(resource_id: str) -> None:
            if resource_id in visited:
                return
            for dependency in self.dependencies.values():
                if dependency.downstream_resource_id == resource_id:
                    visit(dependency.upstream_resource_id)
                    edge_ids.append(dependency.id)
            visited.add(resource_id)
            resource_ids.append(resource_id)

        visit(terminal_resource_id)
        return resource_ids, edge_ids

    def _has_dependency_cycle(self) -> bool:
        visiting: set[str] = set()
        visited: set[str] = set()
        adjacency: dict[str, list[str]] = {}
        for dependency in self.dependencies.values():
            adjacency.setdefault(dependency.upstream_resource_id, []).append(dependency.downstream_resource_id)

        def visit(resource_id: str) -> bool:
            if resource_id in visiting:
                return True
            if resource_id in visited:
                return False
            visiting.add(resource_id)
            if any(visit(item) for item in adjacency.get(resource_id, [])):
                return True
            visiting.remove(resource_id)
            visited.add(resource_id)
            return False

        return any(visit(resource_id) for resource_id in self.resources)

    def _active_grant_for_resource(self, resource_id: str) -> Grant | None:
        current = now_utc()
        for grant in self.grants.values():
            if grant.resource_id == resource_id and grant.status == GrantStatus.ACTIVE and grant.expires_at > current:
                return grant
        return None

    def _active_run_for_resource(self, resource_id: str) -> RunSession | None:
        for run in self.run_sessions.values():
            if run.resource_id == resource_id and run.status in {RunSessionStatus.ACTIVE, RunSessionStatus.WAITING_FOR_HUMAN}:
                return run
        return None

    def _expire_grants(self) -> None:
        current = now_utc()
        for grant in self.grants.values():
            if grant.status == GrantStatus.ACTIVE and grant.expires_at <= current:
                grant.status = GrantStatus.EXPIRED
                resource = self.resources.get(grant.resource_id)
                if resource and not self._active_run_for_resource(resource.id):
                    resource.status = ResourceStatus.ONLINE
                    resource.updated_at = now_utc()

    def _audit(
        self,
        event_type: AuditEventType,
        *,
        actor_id: str,
        summary: str,
        resource_id: str | None = None,
        request_id: str | None = None,
        grant_id: str | None = None,
        run_session_id: str | None = None,
        human_task_id: str | None = None,
        evidence_uri: str | None = None,
        metadata: dict | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            event_type=event_type,
            actor_id=actor_id,
            resource_id=resource_id,
            request_id=request_id,
            grant_id=grant_id,
            run_session_id=run_session_id,
            human_task_id=human_task_id,
            summary=summary,
            evidence_uri=evidence_uri,
            metadata=metadata or {},
        )
        self.audit_events.append(event)
        return event

    def _require_resource(self, resource_id: str) -> Resource:
        resource = self.resources.get(resource_id)
        if not resource:
            raise HTTPException(status_code=404, detail="resource not found")
        return resource

    def _require_account(self, account_id: str) -> AccountProfile:
        account = self.account_profiles.get(account_id)
        if not account:
            raise HTTPException(status_code=404, detail="account profile not found")
        return account

    def _require_request(self, request_id: str) -> ResourceRequest:
        request = self.requests.get(request_id)
        if not request:
            raise HTTPException(status_code=404, detail="request not found")
        return request

    def _require_grant(self, grant_id: str) -> Grant:
        grant = self.grants.get(grant_id)
        if not grant:
            raise HTTPException(status_code=404, detail="grant not found")
        return grant

    def _require_run(self, session_id: str) -> RunSession:
        run = self.run_sessions.get(session_id)
        if not run:
            raise HTTPException(status_code=404, detail="run session not found")
        return run

    def _require_human_task(self, task_id: str) -> HumanTask:
        task = self.human_tasks.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="human task not found")
        return task

    def _require_bifurcation(self, bifurcation_id: str) -> Bifurcation:
        bifurcation = self.bifurcations.get(bifurcation_id)
        if not bifurcation:
            raise HTTPException(status_code=404, detail="bifurcation not found")
        return bifurcation

    def _require_agent(self, agent_id: str) -> Agent:
        agent = self.agents.get(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="agent not found")
        return agent

    def _require_live_agent(self, agent_id: str) -> Agent:
        agent = self._require_agent(agent_id)
        if agent.lease_expires_at <= now_utc():
            raise HTTPException(status_code=409, detail="agent lease expired")
        return agent

    def _require_task(self, task_id: str) -> AgentTask:
        task = self.tasks.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="task not found")
        return task
