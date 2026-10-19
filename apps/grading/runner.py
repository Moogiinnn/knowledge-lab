"""Run untrusted student code against test cases inside a locked-down Docker container.

One container per submission: no network, read-only root filesystem, a small tmpfs
for the program and its output, memory/CPU/process limits, no capabilities, and an
unprivileged user. Nothing from the host is mounted, so the server's files (.env,
database) are not reachable. Each test case runs as its own process inside the
container with its own timeout.

C and C++ programs are compiled once (gcc -std=c11 / g++ -std=c++17) inside the same
locked-down container (a compile error fails every case with the compiler's message),
and the binary then runs per case. The compiler image is python:3.12-slim plus gcc and
g++, built locally from docker/runner-c/Dockerfile by `manage.py check_code_runner --build-c`.
"""

import json
import subprocess
import uuid
from dataclasses import dataclass, field

from django.conf import settings

# Runs inside the container. Student output goes to files on the size-limited tmpfs
# rather than pipes, so a program that prints forever cannot exhaust memory.
HARNESS = r"""
import json, subprocess, sys, time
payload = json.load(sys.stdin)
COMPILERS = {"C": ["gcc", "-std=c11"], "CPP": ["g++", "-std=c++17"]}
SOURCES = {"C": "/tmp/solution.c", "CPP": "/tmp/solution.cpp"}
language = payload["language"]
source = SOURCES.get(language, "/tmp/solution.py")
with open(source, "w", encoding="utf-8") as f:
    f.write(payload["code"])
    if payload["driver"]:
        f.write("\n\n" + payload["driver"] + "\n")
limit = payload["max_output"]
results = []
if language in COMPILERS:
    run = ["/tmp/solution"]
    try:
        cc = subprocess.run(COMPILERS[language] + ["-O2", "-Wall", "-o", "/tmp/solution", source, "-lm"],
                            capture_output=True, timeout=payload["compile_timeout"])
        error = None if cc.returncode == 0 else cc.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired:
        error = "compilation timed out"
    if error is not None:
        error = error.replace(source, source.rsplit("/", 1)[1])[-3000:]
        results = [{"order": c["order"], "status": "compile_error", "duration_ms": 0, "stdout": "",
                    "stdout_truncated": False, "stderr": error} for c in payload["cases"]]
        sys.stdout.write("\n__RESULTS__" + json.dumps(results))
        sys.exit(0)
else:
    run = [sys.executable, "-I", source]
for case in payload["cases"]:
    with open("/tmp/stdin", "wb") as f:
        f.write(case["stdin"].encode("utf-8"))
    start = time.monotonic()
    with open("/tmp/stdin", "rb") as fin, open("/tmp/stdout", "wb") as fout, open("/tmp/stderr", "wb") as ferr:
        try:
            code = subprocess.run(run, stdin=fin, stdout=fout, stderr=ferr, timeout=payload["timeout"]).returncode
            status = "ok" if code == 0 else "runtime_error"
        except subprocess.TimeoutExpired:
            status = "timeout"
        except OSError:
            status = "runtime_error"
    duration = round((time.monotonic() - start) * 1000)
    with open("/tmp/stdout", "rb") as f:
        out = f.read(limit + 1)
    with open("/tmp/stderr", "rb") as f:
        err = f.read()[-2000:]
    results.append({"order": case["order"], "status": status, "duration_ms": duration,
                    "stdout": out[:limit].decode("utf-8", "replace"), "stdout_truncated": len(out) > limit,
                    "stderr": err.decode("utf-8", "replace")})
sys.stdout.write("\n__RESULTS__" + json.dumps(results))
"""

RESULTS_MARKER = "__RESULTS__"
C_DOCKERFILE = "docker/runner-c"
COMPILED = frozenset({"C", "CPP"})  # languages built with the compiler image


class InfrastructureError(Exception):
    """Docker could not run the container at all; retry later, do not grade."""


@dataclass(frozen=True)
class Case:
    order: int
    stdin: str


@dataclass(frozen=True)
class CaseResult:
    order: int
    status: str  # ok | runtime_error | timeout | sandbox_error | compile_error
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    stdout_truncated: bool = False

    def as_json(self) -> dict:
        return {
            "order": self.order,
            "status": self.status,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "stdout_truncated": self.stdout_truncated,
        }


@dataclass(frozen=True)
class RunnerConfig:
    image: str = "python:3.12-slim"
    c_image: str = "knowledge-lab-c:2"  # gcc + g++ (":1" had gcc only)
    per_test_timeout_sec: float = 2.0
    compile_timeout_sec: float = 15.0
    memory: str = "256m"
    cpus: str = "1"
    pids_limit: int = 64
    tmpfs_size: str = "16m"
    max_output_bytes: int = 64 * 1024
    startup_allowance_sec: float = 20.0
    docker: str = "docker"
    extra_args: list[str] = field(default_factory=list)

    @classmethod
    def from_settings(cls) -> "RunnerConfig":
        return cls(**getattr(settings, "CODE_RUNNER", {}))

    def image_for(self, language: str) -> str:
        return self.c_image if language in COMPILED else self.image

    def tag_for(self, language: str) -> str:
        return f"docker:{self.image_for(language)}:t{self.per_test_timeout_sec:g}:m{self.memory}"

    @property
    def version_tag(self) -> str:
        return self.tag_for("PYTHON")


class DockerRunner:
    def __init__(self, config: RunnerConfig | None = None):
        self.config = config or RunnerConfig.from_settings()

    def command(self, name: str, language: str = "PYTHON") -> list[str]:
        c = self.config
        # A compiled binary has to execute from /tmp; Python code never needs that.
        exec_flag = "exec" if language in COMPILED else "noexec"
        return [
            c.docker, "run", "--rm", "-i", "--name", name,
            "--network", "none",
            "--read-only",
            "--tmpfs", f"/tmp:rw,{exec_flag},nosuid,nodev,size={c.tmpfs_size},mode=1777",
            "--memory", c.memory, "--memory-swap", c.memory,
            "--cpus", c.cpus,
            "--pids-limit", str(c.pids_limit),
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--user", "65534:65534",
            "--env", "PYTHONDONTWRITEBYTECODE=1",
            *c.extra_args,
            c.image_for(language), "python", "-I", "-c", HARNESS,
        ]  # fmt: skip

    def run(self, code: str, driver: str, cases: list[Case], language: str = "PYTHON") -> list[CaseResult]:
        c = self.config
        payload = {
            "language": language,
            "compile_timeout": c.compile_timeout_sec,
            "code": code,
            "driver": driver,
            "cases": [{"order": case.order, "stdin": case.stdin} for case in cases],
            "timeout": c.per_test_timeout_sec,
            "max_output": c.max_output_bytes,
        }
        name = f"kl-grade-{uuid.uuid4().hex[:12]}"
        overall = c.startup_allowance_sec + len(cases) * (c.per_test_timeout_sec + 1)
        if language in COMPILED:
            overall += c.compile_timeout_sec
        try:
            proc = subprocess.run(
                self.command(name, language), input=json.dumps(payload).encode(), capture_output=True, timeout=overall
            )
        except FileNotFoundError as exc:
            raise InfrastructureError(f"docker CLI not found: {exc}") from exc
        except subprocess.TimeoutExpired:
            subprocess.run([c.docker, "rm", "-f", name], capture_output=True)
            return [CaseResult(case.order, "timeout") for case in cases]

        stdout = proc.stdout.decode("utf-8", "replace")
        if RESULTS_MARKER in stdout:
            raw = json.loads(stdout.rsplit(RESULTS_MARKER, 1)[1])
            return [CaseResult(**item) for item in raw]
        # 125: docker itself failed (e.g. image missing); 126/127: the container command could not start.
        if proc.returncode in (125, 126, 127):
            raise InfrastructureError(proc.stderr.decode("utf-8", "replace").strip()[:2000])
        # The container ran but the harness did not report (e.g. the student's code
        # killed it, or the container hit its memory limit). Count as failed tests.
        stderr = proc.stderr.decode("utf-8", "replace")[-2000:]
        return [CaseResult(case.order, "sandbox_error", stderr=stderr) for case in cases]


def docker_available(config: RunnerConfig | None = None) -> bool:
    config = config or RunnerConfig.from_settings()
    try:
        return subprocess.run([config.docker, "info"], capture_output=True, timeout=20).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def image_present(image: str, config: RunnerConfig | None = None) -> bool:
    config = config or RunnerConfig.from_settings()
    try:
        return subprocess.run([config.docker, "image", "inspect", image], capture_output=True, timeout=30).returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
