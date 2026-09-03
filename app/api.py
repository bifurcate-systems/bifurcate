from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from .models import (
    AccountProfile,
    AccountProfileCreate,
    A2AMessage,
    A2AMessageCreate,
    Agent,
    AgentHeartbeat,
    AgentRegistration,
    AgentTask,
    ApprovalDecision,
    AuditEvent,
    Bifurcation,
    BifurcationConsumption,
    BifurcationCreate,
    BifurcationLineage,
    CompleteHumanTaskRequest,
    CompleteRunSessionRequest,
    Executor,
    ExecutorCreate,
    EntityEvidence,
    EntityEvidenceCreate,
    Grant,
    HumanTask,
    HumanTaskCreate,
    Resource,
    ResourceCreate,
    ResourceDependency,
    ResourceDependencyCreate,
    ResourceRequest,
    ResourceRequestCreate,
    ResourceIntent,
    ResourceIntentCreate,
    ResourceState,
    RunAction,
    RunSession,
    RunSessionCreate,
    TaskClaim,
    TaskEvent,
    TaskEventCreate,
    TaskView,
)
from .store import SandboxStore

router = APIRouter(prefix="/api/v1", tags=["bifurcate"])


def get_store(request: Request) -> SandboxStore:
    return request.app.state.store


@router.post("/agents/register", response_model=Agent, status_code=201)
def register_agent(payload: AgentRegistration, store: SandboxStore = Depends(get_store)) -> Agent:
    return store.register_agent(payload)


@router.post("/agents/{agent_id}/heartbeat", response_model=Agent)
def heartbeat_agent(
    agent_id: str, payload: AgentHeartbeat, store: SandboxStore = Depends(get_store)
) -> Agent:
    return store.heartbeat_agent(agent_id, payload)


@router.post("/messages", response_model=A2AMessage, status_code=202)
def receive_message(payload: A2AMessageCreate, store: SandboxStore = Depends(get_store)) -> A2AMessage:
    return store.receive_message(payload)


@router.post("/tasks/{task_id}/claim", response_model=AgentTask)
def claim_task(task_id: str, payload: TaskClaim, store: SandboxStore = Depends(get_store)) -> AgentTask:
    return store.claim_task(task_id, payload)


@router.post("/tasks/{task_id}/events", response_model=TaskEvent, status_code=201)
def add_task_event(
    task_id: str, payload: TaskEventCreate, store: SandboxStore = Depends(get_store)
) -> TaskEvent:
    return store.add_task_event(task_id, payload)


@router.post("/tasks/{task_id}/resource-intent", response_model=ResourceIntent, status_code=201)
def submit_resource_intent(
    task_id: str, payload: ResourceIntentCreate, store: SandboxStore = Depends(get_store)
) -> ResourceIntent:
    return store.submit_resource_intent(task_id, payload)


@router.post("/tasks/{task_id}/evidence", response_model=EntityEvidence, status_code=201)
def add_task_evidence(
    task_id: str, payload: EntityEvidenceCreate, store: SandboxStore = Depends(get_store)
) -> EntityEvidence:
    return store.add_entity_evidence(task_id, payload)


@router.get("/tasks/{task_id}", response_model=TaskView)
def get_task(task_id: str, store: SandboxStore = Depends(get_store)) -> TaskView:
    return store.get_task_view(task_id)


@router.get("/tasks", response_model=list[TaskView])
def list_tasks(store: SandboxStore = Depends(get_store)) -> list[TaskView]:
    return store.list_task_views()


@router.post("/capability-tests/simulated-a2", response_model=TaskView, status_code=201)
def run_simulated_a2(store: SandboxStore = Depends(get_store)) -> TaskView:
    return store.run_simulated_a2()


@router.get("/entities/tasks", response_model=list[TaskView])
def get_tasks_for_entity(entityRef: str, store: SandboxStore = Depends(get_store)) -> list[TaskView]:
    return store.tasks_for_entity(entityRef)


@router.post("/executors", response_model=Executor, status_code=201)
def create_executor(payload: ExecutorCreate, store: SandboxStore = Depends(get_store)) -> Executor:
    return store.create_executor(payload)


@router.post("/resources", response_model=Resource, status_code=201)
def create_resource(payload: ResourceCreate, store: SandboxStore = Depends(get_store)) -> Resource:
    return store.create_resource(payload)


@router.get("/resources", response_model=list[Resource])
def list_resources(store: SandboxStore = Depends(get_store)) -> list[Resource]:
    return store.list_resources()


@router.post("/resource-dependencies", response_model=ResourceDependency, status_code=201)
def create_resource_dependency(
    payload: ResourceDependencyCreate, store: SandboxStore = Depends(get_store)
) -> ResourceDependency:
    return store.create_dependency(payload)


@router.get("/resource-dependencies", response_model=list[ResourceDependency])
def list_resource_dependencies(store: SandboxStore = Depends(get_store)) -> list[ResourceDependency]:
    return store.list_dependencies()


@router.post("/bifurcations", response_model=Bifurcation, status_code=201)
def create_bifurcation(payload: BifurcationCreate, store: SandboxStore = Depends(get_store)) -> Bifurcation:
    return store.create_bifurcation(payload)


@router.post("/bifurcations/{bifurcation_id}/consume", response_model=Bifurcation)
def consume_bifurcation(
    bifurcation_id: str,
    payload: BifurcationConsumption,
    store: SandboxStore = Depends(get_store),
) -> Bifurcation:
    return store.consume_bifurcation(bifurcation_id, payload)


@router.get("/bifurcations/{bifurcation_id}/lineage", response_model=BifurcationLineage)
def get_bifurcation_lineage(
    bifurcation_id: str, store: SandboxStore = Depends(get_store)
) -> BifurcationLineage:
    return store.get_bifurcation_lineage(bifurcation_id)


@router.get("/resources/{resource_id}/state", response_model=ResourceState)
def get_resource_state(resource_id: str, store: SandboxStore = Depends(get_store)) -> ResourceState:
    return store.get_resource_state(resource_id)


@router.post("/account-profiles", response_model=AccountProfile, status_code=201)
def create_account_profile(payload: AccountProfileCreate, store: SandboxStore = Depends(get_store)) -> AccountProfile:
    return store.create_account_profile(payload)


@router.post("/resource-requests", response_model=ResourceRequest, status_code=201)
def create_resource_request(payload: ResourceRequestCreate, store: SandboxStore = Depends(get_store)) -> ResourceRequest:
    return store.create_resource_request(payload)


@router.get("/resource-requests", response_model=list[ResourceRequest])
def list_resource_requests(store: SandboxStore = Depends(get_store)) -> list[ResourceRequest]:
    return store.list_resource_requests()


@router.post("/resource-requests/{request_id}/approve", response_model=Grant)
def approve_resource_request(
    request_id: str,
    payload: ApprovalDecision,
    store: SandboxStore = Depends(get_store),
) -> Grant:
    return store.approve_request(request_id, payload)


@router.post("/run-sessions", response_model=RunSession, status_code=201)
def start_run_session(payload: RunSessionCreate, store: SandboxStore = Depends(get_store)) -> RunSession:
    return store.start_run_session(payload)


@router.get("/run-sessions", response_model=list[RunSession])
def list_run_sessions(store: SandboxStore = Depends(get_store)) -> list[RunSession]:
    return store.list_run_sessions()


@router.post("/run-sessions/{session_id}/actions", response_model=AuditEvent, status_code=201)
def record_run_action(session_id: str, payload: RunAction, store: SandboxStore = Depends(get_store)) -> AuditEvent:
    return store.record_run_action(session_id, payload)


@router.post("/run-sessions/{session_id}/complete", response_model=RunSession)
def complete_run_session(
    session_id: str,
    payload: CompleteRunSessionRequest,
    store: SandboxStore = Depends(get_store),
) -> RunSession:
    return store.complete_run_session(session_id, payload)


@router.get("/run-sessions/{session_id}/audit-events", response_model=list[AuditEvent])
def get_run_audit_events(session_id: str, store: SandboxStore = Depends(get_store)) -> list[AuditEvent]:
    return store.audit_for_run_session(session_id)


@router.post("/human-tasks", response_model=HumanTask, status_code=201)
def create_human_task(payload: HumanTaskCreate, store: SandboxStore = Depends(get_store)) -> HumanTask:
    return store.create_human_task(payload)


@router.get("/human-tasks", response_model=list[HumanTask])
def list_human_tasks(store: SandboxStore = Depends(get_store)) -> list[HumanTask]:
    return store.list_human_tasks()


@router.post("/human-tasks/{task_id}/complete", response_model=HumanTask)
def complete_human_task(
    task_id: str,
    payload: CompleteHumanTaskRequest,
    store: SandboxStore = Depends(get_store),
) -> HumanTask:
    return store.complete_human_task(task_id, payload)
