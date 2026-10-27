"""Host file application and fixed, experimentally gated Codex sandbox recipe.

No model-provided command reaches subprocess. This increment accepts small,
flat, fictional Python projects only; it is not a general repository runner.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

from .agent import _codex_executable


MAX_PROJECT_BYTES = 24_000
NAME = re.compile(r"(?:[a-z][a-z0-9_]*\.py|README\.md)\Z")
RESERVED = re.compile(r"(?:con|prn|aux|nul|com[1-9]|lpt[1-9])\.py\Z")


class EnvironmentBlocked(Exception):
    """The fixed runner cannot establish the required isolation."""


def safe_directory(path: Path) -> Path:
    path = path.absolute()
    for item in (path, *path.parents):
        if item.exists() and (item.is_symlink() or
                getattr(item.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ValueError("Linked/reparse directory is not authorized")
    return path.resolve(strict=True)


def snapshot(workspace: Path) -> dict[str, str]:
    safe_directory(workspace)
    files = {}
    for path in workspace.iterdir():
        # Git metadata is never read, executed or presented to a specialist.
        if path.name == ".git" and path.is_dir() and not path.is_symlink():
            if getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise ValueError("Linked Git metadata")
            continue
        if (not NAME.fullmatch(path.name) or not path.is_file() or path.is_symlink()
                or getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
                or path.stat().st_nlink != 1):
            raise ValueError("D2 supports only flat, unlinked Python files and README.md")
        if path.stat().st_size > MAX_PROJECT_BYTES:
            raise ValueError("Project file exceeds context limit")
        files[path.name] = path.read_bytes().decode("utf-8")
    if len(files) > 16 or sum(len(s.encode("utf-8")) for s in files.values()) > MAX_PROJECT_BYTES:
        raise ValueError("Project exceeds D2 context limit")
    return dict(sorted(files.items()))


def digest(files: dict) -> str:
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode("utf-8")).hexdigest()


def apply_files(workspace: Path, files: list[dict], role: str, base: dict) -> dict:
    if snapshot(workspace) != base:
        raise ValueError("Workspace changed outside the coordinator")
    if len(files) > 8 or len({x["path"] for x in files}) != len(files):
        raise ValueError("Too many or duplicate file proposals")
    proposed = dict(base)
    for item in files:
        name = item["path"]
        if (not NAME.fullmatch(name) or RESERVED.fullmatch(name)
                or name in ("conftest.py", "sitecustomize.py", "usercustomize.py")):
            raise ValueError("Unauthorized file path")
        if role == "qa" and not re.fullmatch(r"test_qa_[a-z0-9_]+\.py", name):
            raise ValueError("QA cannot modify production")
        if role == "developer" and name.startswith("test_qa_"):
            raise ValueError("Developer cannot modify QA tests")
        if role not in ("developer", "qa"):
            raise ValueError("Role cannot write files")
        proposed[name] = item["content"]
    if len(proposed) > 16 or sum(len(v.encode("utf-8")) for v in proposed.values()) > MAX_PROJECT_BYTES:
        raise ValueError("Proposed project exceeds context limit")
    # The caller persists this complete intended snapshot before entering here.
    # Files are individually replaced; interrupted batches are diagnosed on resume.
    for item in files:
        if snapshot(workspace) != base:
            raise ValueError("Conflicting workspace change while applying")
        name = item["path"]
        target = workspace / name
        with tempfile.NamedTemporaryFile(dir=workspace, prefix=".d2-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(item["content"].encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        base = {**base, name: item["content"]}
    return snapshot(workspace)


@contextmanager
def workspace_lock(path: Path):
    """Kernel lock in trusted state, keyed by canonical workspace."""
    with path.open("a+b") as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError("Workspace occupied") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def _kill_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"),
                        "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def sandbox_command(workspace: Path, code: str, arguments: list[str]) -> list[str]:
    """A fresh named profile, no broad inherited filesystem grant."""
    paths = {":minimal": "read", str(workspace): "write", str(Path(sys.base_prefix)): "read"}
    table = "{" + ",".join(json.dumps(k) + "=" + json.dumps(v) for k, v in paths.items()) + "}"
    profile = "helmos_d2_" + uuid4().hex
    return [str(_codex_executable()), "-c", "windows.sandbox=mxc",
            "-c", f"permissions.{profile}.filesystem=" + table,
            "-c", f"permissions.{profile}.network.enabled=false", "sandbox",
            "--include-managed-config", "--permission-profile", profile, "--cd", str(workspace),
            "--", sys.executable, "-I", "-B", "-c", code, *arguments]


def sandbox_environment(workspace: Path) -> dict[str, str]:
    # MXC needs both host runtime locations. These are paths, not grants of
    # filesystem access; the unchanged explicit permission profile applies.
    env = {key: os.environ[key] for key in
           ("SystemRoot", "SystemDrive", "LOCALAPPDATA", "WINDIR", "COMSPEC", "PATH")
           if key in os.environ}
    env.update(TEMP=str(workspace), TMP=str(workspace))
    return env


def run_sandbox(workspace: Path, code: str, arguments: list[str], timeout: float) -> dict:
    if os.name != "nt":
        raise EnvironmentBlocked("D2 fixed runner currently supports verified native Windows only")
    command = sandbox_command(workspace, code, arguments)
    # No API keys/tokens or user Python config reach generated code.
    env = sandbox_environment(workspace)
    started = time.monotonic()
    terminated = False
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(command, cwd=workspace, env=env, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT, shell=False)
        try:
            while process.poll() is None:
                if time.monotonic() - started > timeout or os.fstat(output.fileno()).st_size > 1_000_000:
                    _kill_tree(process)
                    terminated = True
                    break
                time.sleep(0.05)
        except BaseException:
            if process.poll() is None:
                _kill_tree(process)
            raise
        output.seek(0)
        log = output.read(1_000_001).decode("utf-8", errors="replace")
    return {"exit_code": process.returncode, "log": log, "seconds": time.monotonic() - started,
            "terminated": terminated, "recipe": "codex-sandbox/python-isolated", "argv": command}


PROBE = r'''
import json, pathlib, socket, sys, subprocess
root = pathlib.Path.cwd()
outside = pathlib.Path(sys.argv[1])
result = {}
try:
    (root / 'allowed.txt').write_text('ok')
    result['inside_write'] = (root / 'allowed.txt').read_text() == 'ok'
except OSError:
    result['inside_write'] = False
for name, action in [
    ('outside_read_denied', lambda: outside.read_text()),
    ('outside_write_denied', lambda: outside.write_text('changed')),
]:
    try:
        action()
        result[name] = False
    except (OSError, subprocess.CalledProcessError):
        result[name] = True
child_code = """
import json, pathlib, sys
outside = pathlib.Path(sys.argv[1])
result = {'child_inside_read': pathlib.Path('allowed.txt').read_text() == 'ok'}
for name, action in [('child_read_denied', lambda: outside.read_text()),
                     ('child_write_denied', lambda: outside.write_text('child'))]:
    try:
        action()
        result[name] = False
    except OSError:
        result[name] = True
print(json.dumps(result))
"""
child = subprocess.run([sys.executable, '-I', '-B', '-c', child_code, str(outside)],
                       capture_output=True, text=True, timeout=10)
if child.returncode == 0:
    result.update(json.loads(child.stdout))
try:
    with socket.create_connection(('127.0.0.1', int(sys.argv[2])), timeout=2):
        result['network_denied'] = False
except OSError:
    result['network_denied'] = True
print('D2_PROBE=' + json.dumps(result))
'''

PROBE_CHECKS = {"inside_write", "outside_read_denied", "outside_write_denied",
                "child_inside_read", "child_read_denied", "child_write_denied", "network_denied"}


def probe_isolation(directory: Path, timeout: float = 30) -> dict:
    """Only fabricated canaries; never attempt to read actual credentials."""
    with tempfile.TemporaryDirectory(prefix="probe-", dir=directory) as name:
        root = Path(name)
        workspace = root / "workspace"
        workspace.mkdir()
        outside = root / "fake-secret.txt"
        outside.write_text("fictional-canary", encoding="utf-8")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            receipt = run_sandbox(workspace, PROBE, [str(outside), str(listener.getsockname()[1])], timeout)
        results = [line[9:] for line in receipt["log"].splitlines() if line.startswith("D2_PROBE=")]
        checks = json.loads(results[-1]) if results else {}
        receipt["checks"] = checks
        receipt["passed"] = (receipt["exit_code"] == 0 and not receipt.get("terminated", False)
                             and set(checks) == PROBE_CHECKS
                             and all(value is True for value in checks.values())
                             and outside.read_text(encoding="utf-8") == "fictional-canary")
        if receipt["passed"]:
            # Verify tree cancellation before running untrusted tests. Child is
            # harmless and bounded even if the termination mechanism fails.
            code = ("import subprocess,sys,time; "
                    "subprocess.Popen([sys.executable,'-I','-c',"
                    "\"import time,pathlib; time.sleep(3); pathlib.Path('survivor.txt').write_text('alive')\"]); "
                    "print('D2_CHILD_STARTED',flush=True); time.sleep(10)")
            cancellation = run_sandbox(workspace, code, [], min(timeout, 2))
            time.sleep(4)
            receipt["cancellation"] = cancellation
            checks["tree_termination"] = (cancellation["terminated"]
                and "D2_CHILD_STARTED" in cancellation["log"]
                and not (workspace / "survivor.txt").exists())
            receipt["passed"] = checks["tree_termination"]
        receipt["mode"] = "host_applied_files; agents_have_no_shell"
        return receipt


TEST_CODE = r'''
import json, pathlib, sys, unittest
sys.path.insert(0, str(pathlib.Path.cwd()))
suite = unittest.defaultTestLoader.discover('.', pattern='test_*.py')
result = unittest.TextTestRunner(verbosity=2).run(suite)
print('D2_TEST=' + json.dumps({'tests': result.testsRun,
    'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped)}))
sys.exit(0 if result.wasSuccessful() and result.testsRun > 0 and not result.skipped else 1)
'''


def run_tests(workspace: Path, timeout: float) -> dict:
    receipt = run_sandbox(workspace, TEST_CODE, [], timeout)
    rows = [line[8:] for line in receipt["log"].splitlines() if line.startswith("D2_TEST=")]
    receipt["counts"] = json.loads(rows[-1]) if rows else None
    counts = receipt["counts"]
    valid_counts = (isinstance(counts, dict) and set(counts) == {"tests", "failures", "errors", "skipped"}
                    and all(type(value) is int and value >= 0 for value in counts.values()))
    receipt["passed"] = (receipt["exit_code"] == 0 and not receipt["terminated"] and valid_counts
                         and receipt["counts"]["tests"] > 0
                         and all(receipt["counts"][key] == 0 for key in ("failures", "errors", "skipped")))
    return receipt
