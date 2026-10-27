"""Fixed Task Scheduler integration. Only the explicit install action registers a task."""

from __future__ import annotations

import csv
import io
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import asdict
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

from .scheduled_instagram import SyncConfig
from .social_connectors import ConnectorError


TASK_NAME = "HelmOS-Instagram-jorgebytes"
NAMESPACE = "http://schemas.microsoft.com/windows/2004/02/mit/task"


def task_xml(python: Path, repository: Path, state_dir: Path, sid: str,
             config: SyncConfig, at: str = "09:00") -> str:
    if (not re.fullmatch(r"S-1-5-21-(?:[0-9]+-){3}[0-9]+", sid)
            or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", at)):
        raise ConnectorError("invalid_task_identity_or_time")
    for path in (python, repository, state_dir):
        if not path.is_absolute() or any(c in str(path) for c in ('"', '\n', '\r', '\x00')):
            raise ConnectorError("absolute_task_paths_required")
    ET.register_namespace("", NAMESPACE)
    root = ET.Element(f"{{{NAMESPACE}}}Task", version="1.2")

    def add(parent, tag, text=None, **attributes):
        node = ET.SubElement(parent, f"{{{NAMESPACE}}}{tag}", **attributes)
        node.text = text
        return node

    triggers = add(root, "Triggers")
    trigger = add(triggers, "CalendarTrigger")
    add(trigger, "StartBoundary", f"{date.today().isoformat()}T{at}:00")
    add(trigger, "Enabled", "true")
    daily = add(trigger, "ScheduleByDay")
    add(daily, "DaysInterval", str(config.interval_days))
    principals = add(root, "Principals")
    principal = add(principals, "Principal", id="Owner")
    add(principal, "UserId", sid)
    add(principal, "LogonType", "InteractiveToken")
    add(principal, "RunLevel", "LeastPrivilege")
    settings = add(root, "Settings")
    for key, value in (("MultipleInstancesPolicy", "IgnoreNew"),
                       ("DisallowStartIfOnBatteries", "false"),
                       ("StopIfGoingOnBatteries", "false"),
                       ("StartWhenAvailable", "true"), ("Enabled", "true"),
                       ("WakeToRun", "false"), ("ExecutionTimeLimit", "PT15M")):
        add(settings, key, value)
    actions = add(root, "Actions", Context="Owner")
    action = add(actions, "Exec")
    add(action, "Command", str(python))
    args = ["-m", "helmos", "--state-dir", str(state_dir), "social", "scheduled-sync", "run"]
    for key, value in asdict(config).items():
        args.extend(["--" + key.replace("_", "-"), str(value)])
    add(action, "Arguments", subprocess.list2cmdline(args))
    add(action, "WorkingDirectory", str(repository))
    return ET.tostring(root, encoding="utf-16", xml_declaration=True).decode("utf-16")


def _system_tool(name: str) -> str:
    return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / name)


def _execute(arguments: list[str]) -> str:
    try:
        result = subprocess.run(arguments, capture_output=True, text=True,
                                timeout=30, shell=False, stdin=subprocess.DEVNULL)
    except Exception:
        raise ConnectorError("task_scheduler_unavailable") from None
    if result.returncode:
        raise ConnectorError("task_scheduler_failed")
    return result.stdout


def manage_task(action: str, *, state_dir: Path, repository: Path,
                config: SyncConfig, at: str = "09:00") -> dict:
    if os.name != "nt":
        raise ConnectorError("windows_task_scheduler_required")
    scheduler = _system_tool("schtasks.exe")
    if action == "install":
        python = Path(sys.executable).resolve()
        repository, state_dir = repository.resolve(), state_dir.resolve()
        if not python.is_file() or not (repository / "helmos" / "__main__.py").is_file():
            raise ConnectorError("invalid_task_paths")
        identity = _execute([_system_tool("whoami.exe"), "/user", "/fo", "csv", "/nh"])
        try:
            sid = next(csv.reader(io.StringIO(identity)))[1]
        except (StopIteration, IndexError):
            raise ConnectorError("invalid_task_identity_or_time") from None
        xml = task_xml(python, repository, state_dir, sid, config, at)
        # No /F: an existing task is never silently replaced.
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "instagram-task.xml"
            path.write_text(xml, encoding="utf-16")
            _execute([scheduler, "/Create", "/TN", TASK_NAME, "/XML", str(path)])
        return {"task": TASK_NAME, "action": action, "status": "registered"}
    commands = {
        "query": ["/Query", "/TN", TASK_NAME, "/XML"],
        "last-run": ["/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"],
        "run": ["/Run", "/TN", TASK_NAME],
        "disable": ["/Change", "/TN", TASK_NAME, "/DISABLE"],
        "remove": ["/Delete", "/TN", TASK_NAME, "/F"],
    }
    if action not in commands:
        raise ConnectorError("invalid_task_action")
    output = _execute([scheduler, *commands[action]])
    return {"task": TASK_NAME, "action": action, "output": output,
            "status": "requested" if action == "run" else "completed"}
