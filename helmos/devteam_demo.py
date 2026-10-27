"""Explicit, fictional D2 demonstration. No automatic execution on import."""

import argparse
import json
from pathlib import Path
import subprocess
from uuid import uuid4

from .core import REPOSITORY_ROOT
from .devteam import Budget, start_project


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repair-demo", action="store_true",
                        help="Retain a declared initial None-input defect until real QA reproduces it")
    args = parser.parse_args()
    root = REPOSITORY_ROOT / "var" / "d2-demos" / str(uuid4())
    workspace = root / "fictional-repository"
    workspace.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(workspace)], check=True, timeout=15)
    (workspace / "tags.py").write_text('"""Fictional tag utilities; no user data."""\n', encoding="utf-8")
    (workspace / "README.md").write_text("# Fictional D2 fixture\nNo credentials, dependencies or services.\n", encoding="utf-8")
    request = ("Add normalize_tags(text) in tags.py. Split a comma-separated string, strip each "
               "tag, lowercase it, omit empty tags and remove duplicates preserving first order. "
               "Raise TypeError for any non-string input, including None. Empty input returns []. "
               "Add unittest tests and a README usage example with expected output. No dependencies.")
    if args.repair_demo:
        (workspace / "tags.py").write_text(
            '"""Fictional fault-injection fixture, never production."""\n'
            'def normalize_tags(text):\n'
            '    if text is None:\n'
            '        return []  # Seeded acceptance defect: must eventually raise TypeError.\n'
            '    raise NotImplementedError("Complete the normalizer")\n', encoding="utf-8")
        request += (" Fault-injection protocol for this disposable demo only: in the FIRST Developer "
                    "proposal retain the existing None -> [] defect while implementing the rest. "
                    "QA must test FINAL acceptance (None raises TypeError) immediately and never "
                    "weaken that test. After the host actually reproduces this failure, Developer "
                    "must fix the guard and all tests must pass. This is an explicit seeded defect, "
                    "not an excuse to claim tests pass before executing them. In the output contract, "
                    "completed means the file proposal is ready for QA; it does NOT mean tests pass. "
                    "Do not return blocked solely because the initial seeded acceptance defect remains.")
    result = start_project(workspace, request, root / "state", authorized=True, fictional=True,
                           budget=Budget(calls=16, repairs=2))
    summary = {key: result[key] for key in ("id", "state", "calls", "tokens", "repairs", "error", "workspace")}
    summary["artifacts"] = str(root / "state" / "devteam" / result["id"])
    print(json.dumps(summary, indent=2))
    return 0 if result["state"] == "ready_for_acceptance" else 1


if __name__ == "__main__":
    raise SystemExit(main())
