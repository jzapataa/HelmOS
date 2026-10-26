"""Small, local execution path for the first Core increment."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from .agent import AgentExecutionError, MODEL, REASONING_EFFORT, load_context, run_agent, validate_task
from .social_sample import SampleValidationError, analyze_csv
from .social_import import SocialImportError, import_csv
from .social_analyze import SocialAnalysisError, analyze
from .recommendations import register_run


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SUPPORTED_OPERATION = "core.status"
SAMPLE_OPERATION = "social.sample"
IMPORT_OPERATION = "social.import"
AGENT_OPERATION = "core.agent"
ANALYZE_OPERATION = "social.analyze"
ROUTES = {
    SUPPORTED_OPERATION: {
        "domain": "core", "handler": "local_status",
        "reason": "Explicit rule for core.status",
    },
    SAMPLE_OPERATION: {
        "domain": "social_sample", "handler": "analyze_csv",
        "reason": "Explicit rule for the bounded social sample analysis",
    },
    IMPORT_OPERATION: {
        "domain": "social", "handler": "import_csv",
        "reason": "Explicit rule for canonical Social publication CSV import",
    },
    ANALYZE_OPERATION: {
        "domain": "social", "handler": "analyze",
        "reason": "Explicit on-demand editorial analysis of imported publications",
    },
    AGENT_OPERATION: {
        "domain": "core", "handler": "codex_exec",
        "reason": "Explicit rule for one read-only Codex task",
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def route(operation: str) -> dict[str, str]:
    try:
        return ROUTES[operation].copy()
    except (KeyError, TypeError) as error:
        raise ValueError(f"Unsupported operation: {operation}") from error


def local_status() -> dict[str, object]:
    """Report which local inputs for the Core are present, without reading them."""
    required = {
        "v1_spec": REPOSITORY_ROOT / "docs" / "SYSTEM_SPEC_V1.0.md",
        "developer_profile": REPOSITORY_ROOT / ".codex" / "agents" / "developer.toml",
        "qa_profile": REPOSITORY_ROOT / ".codex" / "agents" / "qa.toml",
        "reviewer_profile": REPOSITORY_ROOT / ".codex" / "agents" / "reviewer.toml",
    }
    available = {name: path.is_file() for name, path in required.items()}
    return {"ready": all(available.values()), "local_inputs": available}


def _write_atomic(path: Path, record: dict[str, object]) -> None:
    if path.parent.is_symlink():
        raise ValueError("Execution directory cannot be a symbolic link")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("Execution path cannot be a symbolic link")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".run-", suffix=".tmp", dir=path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise FileExistsError(f"Execution already exists: {path.name}")
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def execute(
    operation: str, state_dir: Path, csv_path: Path | None = None,
    task: str | None = None, context_path: Path | None = None,
    platform: str | None = None, from_date: str | None = None, to_date: str | None = None,
) -> dict[str, object]:
    selected_route = route(operation)
    if operation in (SAMPLE_OPERATION, IMPORT_OPERATION) and csv_path is None:
        raise ValueError(f"{operation} requires --csv PATH")
    if operation not in (SAMPLE_OPERATION, IMPORT_OPERATION) and csv_path is not None:
        raise ValueError(f"{operation} does not accept --csv")
    if operation == AGENT_OPERATION:
        task = validate_task(task)
        context_name, context_text = load_context(context_path)
    elif task is not None:
        raise ValueError(f"{operation} does not accept --task")
    elif context_path is not None:
        raise ValueError(f"{operation} does not accept --context")
    if operation != ANALYZE_OPERATION and any(value is not None for value in (platform, from_date, to_date)):
        raise ValueError(f"{operation} does not accept analysis filters")
    request: dict[str, str] = {"operation": operation}
    if operation == ANALYZE_OPERATION:
        request.update({key: value for key, value in
                        (("platform", platform), ("from", from_date), ("to", to_date)) if value is not None})
    if csv_path is not None:
        request["csv_path"] = str(csv_path)
    if task is not None:
        request["task"] = task
        request["context_path"] = context_name
    record: dict[str, object] = {
        "id": str(uuid4()),
        "input": request,
        "route": selected_route,
        "context": (
            ["presence of local specification and agent profiles"]
            if operation == SUPPORTED_OPERATION else
            ["user-selected CSV"] if operation in (SAMPLE_OPERATION, IMPORT_OPERATION) else
            ["imported Social publications", "editorial skill"] if operation == ANALYZE_OPERATION else
            ["user task", f"supplied Markdown: {context_name}"]
        ),
        "capabilities": (
            ["read local file metadata", "write execution record"]
            if operation == SUPPORTED_OPERATION else
            ["read selected CSV", "write execution record"] if operation == SAMPLE_OPERATION else
            ["read selected CSV", "write Social posts", "write execution record"]
            if operation == IMPORT_OPERATION else
            ["read imported publications", "Codex read-only sandbox", "write Markdown report", "write execution record", "write recommendations"]
            if operation == ANALYZE_OPERATION else
            ["Codex read-only sandbox", "write execution record"]
        ),
        "agent": (
            {
                "engine": "codex exec", "profile": None, "thread_id": None,
                "model_requested": MODEL, "reasoning_effort_requested": REASONING_EFFORT,
            }
            if operation in (AGENT_OPERATION, ANALYZE_OPERATION) else None
        ),
        "status": "running",
        "started_at": utc_now(),
        "finished_at": None,
        "result": None,
        "error": None,
        "consumption": {"tokens": None, "cost": None, "measurement": "unknown"},
    }
    try:
        if operation == SUPPORTED_OPERATION:
            record["result"] = local_status()
        elif operation == SAMPLE_OPERATION:
            record["result"] = analyze_csv(csv_path)
        elif operation == IMPORT_OPERATION:
            record["result"] = import_csv(csv_path, state_dir)
        elif operation == ANALYZE_OPERATION:
            outcome = analyze(state_dir, record["id"], platform, from_date, to_date)
            record["result"] = outcome["result"]
            record["consumption"] = outcome["consumption"]
            record["agent"]["thread_id"] = outcome["thread_id"]
        elif operation == AGENT_OPERATION:
            outcome = run_agent(task, state_dir, context_name, context_text)
            record["result"] = outcome["result"]
            record["consumption"] = outcome["consumption"]
            record["agent"]["thread_id"] = outcome["thread_id"]
        else:
            raise ValueError(f"Unsupported operation: {operation}")
        if operation == AGENT_OPERATION and record["result"]["outcome"] == "blocked":
            record["status"] = "failed"
            record["error"] = "Agent reported that it could not complete the task"
        else:
            record["status"] = "succeeded"
    except (OSError, SampleValidationError, SocialImportError, SocialAnalysisError, AgentExecutionError) as error:
        record["status"] = "failed"
        record["error"] = (
            f"Local status check failed: {error}"
            if operation == SUPPORTED_OPERATION else
            f"Social sample analysis failed: {error}" if operation == SAMPLE_OPERATION else
            f"Social import failed: {error}" if operation == IMPORT_OPERATION else
            f"Social analysis failed: {error}" if operation == ANALYZE_OPERATION else
            f"Agent execution failed: {error}"
        )
    record["finished_at"] = utc_now()
    _write_atomic(state_dir / "runs" / f"{record['id']}.json", record)
    if operation == ANALYZE_OPERATION and record["status"] == "succeeded":
        register_run(record, state_dir)
    return record


def read_execution(identifier: str, state_dir: Path) -> dict[str, object]:
    try:
        normalized = str(UUID(identifier))
    except ValueError as error:
        raise ValueError("Invalid execution ID") from error
    path = state_dir / "runs" / f"{normalized}.json"
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("Execution path cannot be a symbolic link")
    with path.open("r", encoding="utf-8") as stream:
        record = json.load(stream)
    if not _valid_record(record, normalized):
        raise ValueError("Execution record is invalid")
    return record


def _valid_record(record: object, identifier: str) -> bool:
    if not isinstance(record, dict) or record.get("id") != identifier:
        return False
    request = record.get("input")
    selected_route = record.get("route")
    consumption = record.get("consumption")
    if not isinstance(request, dict) or not isinstance(selected_route, dict):
        return False
    if not isinstance(consumption, dict):
        return False
    if not isinstance(request.get("operation"), str) or not request["operation"]:
        return False
    if request["operation"] in (AGENT_OPERATION, ANALYZE_OPERATION):
        agent = record.get("agent")
        if (
            (request["operation"] == AGENT_OPERATION and (
                not isinstance(request.get("task"), str)
                or not isinstance(request.get("context_path"), str)))
            or not isinstance(agent, dict)
            or agent.get("engine") != "codex exec"
            or agent.get("profile") is not None
            or (agent.get("thread_id") is not None and not isinstance(agent["thread_id"], str))
        ):
            return False
    if any(
        not isinstance(selected_route.get(key), str) or not selected_route[key]
        for key in ("domain", "handler", "reason")
    ):
        return False
    if any(
        not isinstance(record.get(key), str) or not record[key]
        for key in ("started_at", "finished_at")
    ):
        return False
    if not all(key in consumption for key in ("tokens", "cost", "measurement")):
        return False
    tokens = consumption["tokens"]
    cost = consumption["cost"]
    if tokens is not None and (type(tokens) is not int or tokens < 0):
        return False
    if cost is not None and (type(cost) not in (int, float) or cost < 0):
        return False
    if not isinstance(consumption["measurement"], str) or not consumption["measurement"]:
        return False
    if record.get("status") == "succeeded":
        return isinstance(record.get("result"), dict) and record.get("error") is None
    if record.get("status") == "failed":
        return (
            (record.get("result") is None or (
                request["operation"] == AGENT_OPERATION
                and isinstance(record.get("result"), dict)
                and record["result"].get("outcome") == "blocked"
            ))
            and isinstance(record.get("error"), str)
            and bool(record["error"])
        )
    return False
