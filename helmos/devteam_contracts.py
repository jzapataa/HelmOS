"""Closed contracts for the bounded D2 Python development circuit."""

from __future__ import annotations

import json


def object_schema(properties: dict) -> dict:
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


TEXT = {"type": "string"}
TEXTS = {"type": "array", "items": TEXT}
FILE = object_schema({"path": TEXT, "content": TEXT})
FILES = {"type": "array", "items": FILE}
DEFECT = object_schema({"id": TEXT, "severity": {"type": "string", "enum": ["P1", "P2", "P3"]},
                        "path": TEXT, "reproduction": TEXT, "expected": TEXT, "observed": TEXT})
DEFECTS = {"type": "array", "items": DEFECT}
BASE = {"outcome": {"type": "string", "enum": ["completed", "blocked"]},
        "summary": TEXT, "limitations": TEXTS}
SCHEMAS = {
    "planner": object_schema({**BASE, "acceptance": TEXTS, "steps": TEXTS, "manual_qa": TEXTS}),
    "developer": object_schema({**BASE, "files": FILES}),
    "qa": object_schema({**BASE, "files": FILES, "recipe": {"type": "string", "enum": ["unittest"]},
                         "defects": DEFECTS, "coverage": TEXTS}),
    "reviewer": object_schema({**BASE, "defects": DEFECTS, "manual_qa": TEXTS}),
}
INSTRUCTIONS = {
    "planner": "Act as Tech Lead. Plan this small Python standard-library change. Define numbered "
               "acceptance criteria from the user request, a bounded plan and manual QA steps with "
               "expected results. Do not expand scope or require routine approvals.",
    "developer": "Act as Developer. Implement the request and repair ALL supplied defects/test "
                 "failures. Return full UTF-8 files, not patches. You may change production and "
                 "test_dev_*.py, never test_qa_*.py. Add meaningful unit tests. No dependencies, "
                 "file deletion, shell commands or external services.",
    "qa": "Act as independent QA. Derive executable unittest cases from original acceptance "
          "criteria, including edge/negative cases. Return only test_qa_*.py files at root; "
          "import the application normally. Request the fixed unittest recipe. Host executes ALL "
          "tests and supplies real failures in the next cycle. Do not modify production. Defects "
          "must have reproducible steps, expected and observed result. Do not claim execution. "
          "Report only defects present in the current supplied files. verification_targets are "
          "previous defects to retest, not evidence that the current source still fails. The host "
          "will execute your tests before deciding whether a repair is needed.",
    "reviewer": "Act as independent Reviewer. Inspect supplied source changes, original request "
                "and actual test results. Do not modify any files. Identify concrete bugs/security "
                "issues with file, reproduction, expected and observed behavior. P1/P2 block; P3 "
                "does not. Do not manufacture defects or relax acceptance. Include manual QA "
                "steps and expected results. Independent review is not human acceptance.",
}


def validate(value: object, schema: dict) -> None:
    """Validate the entire small schema subset locally, not just the model's promise."""
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict) or set(value) != set(schema["properties"]):
            raise ValueError("Specialist object fields do not match contract")
        for key, child in schema["properties"].items():
            validate(value[key], child)
    elif kind == "array":
        if not isinstance(value, list) or len(value) > 32:
            raise ValueError("Specialist array exceeds contract")
        for item in value:
            validate(item, schema["items"])
    elif kind == "string":
        if not isinstance(value, str) or len(value.encode("utf-8")) > 16_384:
            raise ValueError("Invalid specialist string")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError("Invalid specialist enum")
    if len(json.dumps(value).encode("utf-8")) > 32_768:
        raise ValueError("Specialist result too large")
