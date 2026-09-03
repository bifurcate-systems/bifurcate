from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


SECRET_KEY_FRAGMENTS = ("password", "passwd", "secret", "token", "api_key", "apikey", "private_key")


class ResourceKind(str, Enum):
    FILE = "file"
    CLI = "cli"
    DEVICE = "device"
    HUMAN = "human"
    NETWORK = "network"
    STORAGE = "storage"
    COMPUTE = "compute"
    RUNTIME = "runtime"
    MODEL = "model"
    SANDBOX = "sandbox"


class ResourceStage(str, Enum):
    HOMOGENEOUS = "homogeneous"
    DIFFERENTIATED = "differentiated"
    CONSUMED = "consumed"


class DependencyRelation(str, Enum):
    REQUIRES = "requires"
    ROUTES_THROUGH = "routes_through"
    MOUNTS = "mounts"
    EXECUTES_ON = "executes_on"
    MODELED_BY = "modeled_by"


class ExecutorKind(str, Enum):
    DEVBOX = "devbox"
    ANDROID_DEVICE_AGENT = "android_device_agent"
    HUMAN_WORKBENCH = "human_workbench"
    FILE_AGENT = "file_agent"


class ResourceStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    BUSY = "busy"
    NEEDS_INTERVENTION = "needs_intervention"


class AccountStatus(str, Enum):
    READY = "ready"
    EXPIRED = "expired"
    NEEDS_LOGIN = "needs_login"
    UNKNOWN = "unknown"


class RequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class GrantStatus(str, Enum):
    ACTIVE = "active"
    CONSUMED = "consumed"
    EXPIRED = "expired"
    REVOKED = "revoked"


class RunSessionStatus(str, Enum):
    ACTIVE = "active"
    WAITING_FOR_HUMAN = "waiting_for_human"
    COMPLETED = "completed"
    FAILED = "failed"


class HumanTaskStatus(str, Enum):
    OPEN = "open"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    FAILED = "failed"


class AuditEventType(str, Enum):
    RESOURCE_REGISTERED = "resource.registered"
    ACCOUNT_PROFILE_REGISTERED = "account_profile.registered"
    REQUEST_CREATED = "request.created"
    REQUEST_APPROVED = "request.approved"
    REQUEST_REJECTED = "request.rejected"
    GRANT_CREATED = "grant.created"
    RUN_SESSION_STARTED = "run_session.started"
    RUN_ACTION_RECORDED = "run_session.action_recorded"
    RUN_SESSION_COMPLETED = "run_session.completed"
    HUMAN_TASK_CREATED = "human_task.created"
    HUMAN_TASK_COMPLETED = "human_task.completed"
    HUMAN_TASK_FAILED = "human_task.failed"
    DEPENDENCY_REGISTERED = "dependency.registered"
    BIFURCATION_CREATED = "bifurcation.created"
    BIFURCATION_CONSUMED = "bifurcation.consumed"


ALLOWED_ACTIONS: dict[ResourceKind, set[str]] = {
    ResourceKind.FILE: {"read", "append", "edit", "delete"},
    ResourceKind.CLI: {"inspect", "plan", "apply", "destroy"},
    ResourceKind.DEVICE: {"view", "tap", "input", "open_app", "upload_file", "download_artifact"},
    ResourceKind.HUMAN: {"login", "verify_code", "review", "manual_execute", "recover_session"},
    ResourceKind.NETWORK: {"connect", "publish", "egress"},
    ResourceKind.STORAGE: {"mount", "read", "write", "snapshot"},
    ResourceKind.COMPUTE: {"reserve", "execute"},
    ResourceKind.RUNTIME: {"execute"},
    ResourceKind.MODEL: {"infer"},
    ResourceKind.SANDBOX: {"create", "execute", "destroy"},
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def ensure_no_secret_keys(value: dict[str, Any], field_name: str) -> dict[str, Any]:
    def inspect(item: Any, path: str) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                lowered = str(key).lower().replace("-", "_")
                if lowered in SECRET_KEY_FRAGMENTS or any(lowered.endswith(f"_{fragment}") for fragment in SECRET_KEY_FRAGMENTS):
                    raise ValueError(f"{field_name} must not contain credential-like key '{path}{key}'")
                inspect(nested, f"{path}{key}.")
        elif isinstance(item, list):
            for index, nested in enumerate(item):
                inspect(nested, f"{path}{index}.")

    inspect(value, "")
    return value


class ExecutorCreate(BaseModel):
    name: str = Field(min_length=1)
    kind: ExecutorKind
    status: ResourceStatus = ResourceStatus.ONLINE
    connection_hint: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None

    @field_validator("connection_hint")
    @classmethod
    def reject_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_no_secret_keys(value, "connection_hint")


class Executor(ExecutorCreate):
    id: str = Field(default_factory=lambda: new_id("exe"))
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class ResourceBinding(BaseModel):
    executor_id: str | None = None
    command: str | None = None
    device_serial: str | None = None
    mount_label: str | None = None


class ResourceCreate(BaseModel):
    name: str = Field(min_length=1)
    kind: ResourceKind
    owner: str = Field(default="owner", min_length=1)
    status: ResourceStatus = ResourceStatus.ONLINE
    binding: ResourceBinding = Field(default_factory=ResourceBinding)
    capabilities: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    intervention_policy: str | None = None
    stage: ResourceStage = ResourceStage.HOMOGENEOUS

    @field_validator("metadata")
    @classmethod
    def reject_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_no_secret_keys(value, "metadata")

    @model_validator(mode="after")
    def validate_binding(self) -> "ResourceCreate":
        if self.kind == ResourceKind.CLI and (not self.binding.executor_id or not self.binding.command):
            raise ValueError("cli resources require binding.executor_id and binding.command")
        if self.kind == ResourceKind.DEVICE and not self.binding.executor_id:
            raise ValueError("device resources require binding.executor_id")
        return self


class Resource(ResourceCreate):
    id: str = Field(default_factory=lambda: new_id("res"))
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class ResourceDependencyCreate(BaseModel):
    upstream_resource_id: str
    downstream_resource_id: str
    relation: DependencyRelation = DependencyRelation.REQUIRES
    purpose: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def reject_self_dependency(self) -> "ResourceDependencyCreate":
        if self.upstream_resource_id == self.downstream_resource_id:
            raise ValueError("a resource cannot depend on itself")
        return self

    @field_validator("metadata")
    @classmethod
    def reject_dependency_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_no_secret_keys(value, "dependency metadata")


class ResourceDependency(ResourceDependencyCreate):
    id: str = Field(default_factory=lambda: new_id("dep"))
    created_at: datetime = Field(default_factory=now_utc)


class BifurcationCreate(BaseModel):
    agent_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    terminal_resource_id: str
    requested_capabilities: list[str] = Field(default_factory=list)
    sandbox_template: str = Field(min_length=1)
    sandbox_claim_class: str = "sandbox.microvm.standard"
    model_alias: str = Field(min_length=1)
    ttl_minutes: int = Field(default=30, ge=1, le=24 * 60)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def reject_bifurcation_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_no_secret_keys(value, "bifurcation metadata")


class Bifurcation(BaseModel):
    id: str = Field(default_factory=lambda: new_id("bif"))
    agent_id: str
    purpose: str
    terminal_resource_id: str
    requested_capabilities: list[str]
    sandbox_backend: str = "claim"
    sandbox_claim_class: str = "sandbox.microvm.standard"
    sandbox_template: str
    sandbox_provider_ref: str | None = None
    model_gateway: str = "litellm"
    model_protocol: str = "openai-compatible/v1"
    model_alias: str
    dependency_resource_ids: list[str]
    dependency_edge_ids: list[str]
    status: str = "ready"
    ttl_minutes: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=now_utc)
    consumed_at: datetime | None = None


class BifurcationConsumption(BaseModel):
    sandbox_provider: str = Field(min_length=1)
    sandbox_instance_ref: str = Field(min_length=1)
    model_route_ref: str = Field(min_length=1)
    evidence_uri: str | None = None


class BifurcationLineage(BaseModel):
    bifurcation: Bifurcation
    resources: list[Resource]
    dependencies: list[ResourceDependency]


class TaskStatus(str, Enum):
    SUBMITTED = "submitted"
    CLAIMED = "claimed"
    PLANNING = "planning"
    RESOURCE_PENDING = "resource_pending"
    PROVISIONED = "provisioned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RELEASING = "releasing"
    RELEASED = "released"


class PolicyEffect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"


class AgentRegistration(BaseModel):
    agent_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    capabilities: list[str] = Field(min_length=1)
    lease_seconds: int = Field(default=60, ge=10, le=600)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def reject_agent_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_no_secret_keys(value, "agent metadata")


class Agent(BaseModel):
    agent_id: str
    version: str
    capabilities: list[str]
    lease_seconds: int
    metadata: dict[str, Any] = Field(default_factory=dict)
    registered_at: datetime = Field(default_factory=now_utc)
    last_heartbeat_at: datetime = Field(default_factory=now_utc)
    lease_expires_at: datetime


class AgentHeartbeat(BaseModel):
    lease_seconds: int | None = Field(default=None, ge=10, le=600)


class MessagePart(BaseModel):
    kind: str = Field(pattern="^(text|data|artifact)$")
    content: str | dict[str, Any]
    media_type: str | None = None


class A2AMessageCreate(BaseModel):
    message_id: str = Field(alias="messageId", min_length=1)
    task_id: str = Field(alias="taskId", min_length=1)
    sender: str = Field(min_length=1)
    recipient: str = Field(min_length=1)
    parts: list[MessagePart] = Field(min_length=1)
    trace_id: str = Field(alias="traceId", min_length=1)
    created_at: datetime = Field(default_factory=now_utc, alias="createdAt")

    model_config = {"populate_by_name": True}


class A2AMessage(A2AMessageCreate):
    accepted_at: datetime = Field(default_factory=now_utc)
    duplicate: bool = False


class TaskClaim(BaseModel):
    agent_id: str = Field(alias="agentId", min_length=1)

    model_config = {"populate_by_name": True}


class TaskResult(BaseModel):
    summary: str
    artifact_uris: list[str] = Field(alias="artifactUris", min_length=1)
    checksums: dict[str, str] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class TaskEventCreate(BaseModel):
    event_type: str = Field(alias="eventType", min_length=1)
    target_status: TaskStatus = Field(alias="targetStatus")
    actor_id: str = Field(alias="actorId", min_length=1)
    summary: str = Field(min_length=1)
    evidence_uri: str | None = Field(default=None, alias="evidenceUri")
    metadata: dict[str, Any] = Field(default_factory=dict)
    result: TaskResult | None = None

    model_config = {"populate_by_name": True}

    @field_validator("metadata")
    @classmethod
    def reject_task_event_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_no_secret_keys(value, "task event metadata")


class TaskEvent(TaskEventCreate):
    id: str = Field(default_factory=lambda: new_id("tev"))
    task_id: str
    trace_id: str
    created_at: datetime = Field(default_factory=now_utc)


class ComputeIntent(BaseModel):
    cpu: float = Field(default=2, gt=0)
    memory_mib: int = Field(default=4096, ge=128)
    max_minutes: int = Field(default=20, ge=1)


class RuntimeIntent(BaseModel):
    backend: str = "sandbox-claim"
    claim_class: str = "sandbox.microvm.standard"
    template: str = "sandbox-code"
    provider_preferences: list[str] = Field(default_factory=list)


class NetworkIntent(BaseModel):
    profiles: list[str] = Field(default_factory=lambda: ["cluster-dns", "public-web", "mihomos-global"])
    allowed_methods: list[str] = Field(default_factory=lambda: ["GET"])


class StorageIntent(BaseModel):
    backend: str = "cephfs"
    capacity_mib: int = Field(default=1024, ge=1)
    retention: str = Field(default="ephemeral", pattern="^(ephemeral|retain)$")


class ModelIntent(BaseModel):
    gateway: str = "litellm"
    alias: str = "deepseek-v4-flash"
    max_tokens: int = Field(default=8192, ge=1)
    max_cost_usd: float = Field(default=0.10, ge=0)


class ResourceIntentCreate(BaseModel):
    purpose: str = Field(min_length=1)
    compute: ComputeIntent = Field(default_factory=ComputeIntent)
    runtime: RuntimeIntent = Field(default_factory=RuntimeIntent)
    network: NetworkIntent = Field(default_factory=NetworkIntent)
    storage: StorageIntent = Field(default_factory=StorageIntent)
    model: ModelIntent = Field(default_factory=ModelIntent)
    capabilities: list[str] = Field(default_factory=lambda: ["http_get", "dns", "file_read", "file_write", "exec_restricted"])
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1)

    model_config = {"populate_by_name": True}


class PolicyDecision(BaseModel):
    effect: PolicyEffect
    policy_version: str = "bifurcate-agent-e2e/v1"
    reasons: list[str]
    evaluated_at: datetime = Field(default_factory=now_utc)


class ResourceIntent(ResourceIntentCreate):
    id: str = Field(default_factory=lambda: new_id("int"))
    task_id: str
    trace_id: str
    policy_decision: PolicyDecision
    bifurcation_id: str | None = None
    created_at: datetime = Field(default_factory=now_utc)


class EntityEvidenceCreate(BaseModel):
    entity_type: str = Field(alias="entityType", min_length=1)
    entity_ref: str = Field(alias="entityRef", min_length=1)
    relation: str = Field(min_length=1)
    details: dict[str, Any] = Field(default_factory=dict)
    evidence_uri: str | None = Field(default=None, alias="evidenceUri")

    model_config = {"populate_by_name": True}

    @field_validator("details")
    @classmethod
    def reject_evidence_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_no_secret_keys(value, "entity evidence")


class EntityEvidence(EntityEvidenceCreate):
    id: str = Field(default_factory=lambda: new_id("evd"))
    task_id: str
    trace_id: str
    observed_at: datetime = Field(default_factory=now_utc)


class AgentTask(BaseModel):
    task_id: str = Field(alias="taskId")
    message_id: str = Field(alias="messageId")
    sender: str
    recipient: str
    trace_id: str = Field(alias="traceId")
    status: TaskStatus = TaskStatus.SUBMITTED
    claimed_by: str | None = Field(default=None, alias="claimedBy")
    bifurcation_id: str | None = Field(default=None, alias="bifurcationId")
    resource_intent_id: str | None = Field(default=None, alias="resourceIntentId")
    result: TaskResult | None = None
    created_at: datetime = Field(default_factory=now_utc, alias="createdAt")
    updated_at: datetime = Field(default_factory=now_utc, alias="updatedAt")

    model_config = {"populate_by_name": True}


class TaskView(BaseModel):
    task: AgentTask
    message: A2AMessage
    events: list[TaskEvent]
    resource_intent: ResourceIntent | None = None
    bifurcation: Bifurcation | None = None
    evidence: list[EntityEvidence]


class AccountProfileCreate(BaseModel):
    resource_id: str
    label: str = Field(min_length=1)
    account_ref: str = Field(min_length=1)
    status: AccountStatus = AccountStatus.UNKNOWN
    login_strategy: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def reject_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_no_secret_keys(value, "metadata")


class AccountProfile(AccountProfileCreate):
    id: str = Field(default_factory=lambda: new_id("acct"))
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)


class ResourceRequestCreate(BaseModel):
    agent_id: str = Field(min_length=1)
    resource_id: str
    account_profile_id: str | None = None
    purpose: str = Field(min_length=1)
    requested_actions: list[str] = Field(min_length=1)
    requested_minutes: int = Field(default=30, ge=1, le=24 * 60)
    budget: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata", "budget")
    @classmethod
    def reject_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_no_secret_keys(value, "request data")


class ResourceRequest(ResourceRequestCreate):
    id: str = Field(default_factory=lambda: new_id("req"))
    status: RequestStatus = RequestStatus.PENDING
    created_at: datetime = Field(default_factory=now_utc)
    decided_at: datetime | None = None
    decision_reason: str | None = None
    grant_id: str | None = None


class ApprovalDecision(BaseModel):
    approver_id: str = Field(default="owner", min_length=1)
    approved_actions: list[str] | None = None
    minutes: int | None = Field(default=None, ge=1, le=24 * 60)
    audit_required: bool = True
    reason: str | None = None


class Grant(BaseModel):
    id: str = Field(default_factory=lambda: new_id("grt"))
    request_id: str
    resource_id: str
    account_profile_id: str | None = None
    executor_id: str | None = None
    actions: list[str]
    starts_at: datetime = Field(default_factory=now_utc)
    expires_at: datetime
    status: GrantStatus = GrantStatus.ACTIVE
    audit_required: bool = True
    created_at: datetime = Field(default_factory=now_utc)


class RunSessionCreate(BaseModel):
    grant_id: str
    agent_id: str = Field(min_length=1)
    objective: str = Field(min_length=1)


class RunSession(BaseModel):
    id: str = Field(default_factory=lambda: new_id("run"))
    grant_id: str
    request_id: str
    agent_id: str
    resource_id: str
    executor_id: str | None = None
    objective: str
    status: RunSessionStatus = RunSessionStatus.ACTIVE
    started_at: datetime = Field(default_factory=now_utc)
    ended_at: datetime | None = None
    summary: str | None = None


class RunAction(BaseModel):
    action: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    evidence_uri: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def reject_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_no_secret_keys(value, "action metadata")


class CompleteRunSessionRequest(BaseModel):
    status: RunSessionStatus = RunSessionStatus.COMPLETED
    summary: str | None = None

    @field_validator("status")
    @classmethod
    def validate_terminal(cls, value: RunSessionStatus) -> RunSessionStatus:
        if value not in {RunSessionStatus.COMPLETED, RunSessionStatus.FAILED}:
            raise ValueError("run session completion status must be completed or failed")
        return value


class HumanTaskCreate(BaseModel):
    run_session_id: str
    task_type: str = Field(pattern="^(login|verify_code|review|manual_execute|recover_session)$")
    title: str = Field(min_length=1)
    instructions: str = Field(min_length=1)
    assigned_human_resource_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def reject_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_no_secret_keys(value, "human task metadata")


class HumanTask(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ht"))
    run_session_id: str
    task_type: str
    title: str
    instructions: str
    assigned_human_resource_id: str | None = None
    status: HumanTaskStatus = HumanTaskStatus.OPEN
    created_at: datetime = Field(default_factory=now_utc)
    completed_at: datetime | None = None
    result: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompleteHumanTaskRequest(BaseModel):
    status: HumanTaskStatus = HumanTaskStatus.COMPLETED
    result: str = Field(min_length=1)

    @field_validator("status")
    @classmethod
    def validate_terminal(cls, value: HumanTaskStatus) -> HumanTaskStatus:
        if value not in {HumanTaskStatus.COMPLETED, HumanTaskStatus.FAILED}:
            raise ValueError("human task completion status must be completed or failed")
        return value


class AuditEvent(BaseModel):
    id: str = Field(default_factory=lambda: new_id("aud"))
    event_type: AuditEventType
    actor_id: str
    resource_id: str | None = None
    request_id: str | None = None
    grant_id: str | None = None
    run_session_id: str | None = None
    human_task_id: str | None = None
    summary: str
    evidence_uri: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=now_utc)


class ResourceState(BaseModel):
    resource: Resource
    executor: Executor | None = None
    account_profiles: list[AccountProfile]
    active_grant: Grant | None = None
    active_run_session: RunSession | None = None
    pending_human_tasks: list[HumanTask]
    recent_audit_events: list[AuditEvent]
