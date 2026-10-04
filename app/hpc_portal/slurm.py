"""Thin wrapper over the Slurm CLI (sbatch/squeue/sacct/scontrol) and its JSON output.

Commands that act on behalf of a tenant run through ``runuser`` so Slurm sees the
tenant's real UID: munge credentials, accounting associations, PrivateData
visibility and file ownership on /shared all follow from that.
"""

import json
import logging
import subprocess
import time
from dataclasses import asdict, dataclass
from typing import Any

log = logging.getLogger("hpc_portal.slurm")


class SlurmError(RuntimeError):
    """A Slurm command failed. ``user_error`` distinguishes bad requests from cluster faults."""

    def __init__(self, message: str, *, user_error: bool = False):
        super().__init__(message)
        self.user_error = user_error


class SlurmUnavailable(SlurmError):
    pass


class JobNotFound(SlurmError):
    pass


# Substrings of Slurm error messages that mean the controller/dbd is down rather than the request being wrong.
_UNAVAILABLE_MARKERS = (
    "Unable to contact slurm controller",
    "Socket timed out",
    "Connection refused",
    "slurmdbd: Connection",
    "Zero Bytes were transmitted",
)


def number(value: Any) -> int | float | None:
    """Unwraps Slurm's {"set": bool, "infinite": bool, "number": n} encoding."""
    if isinstance(value, dict):
        if value.get("infinite"):
            return None
        return value.get("number") if value.get("set", True) else None
    return value


@dataclass
class JobInfo:
    job_id: int
    name: str
    user: str
    account: str
    partition: str
    state: str
    reason: str
    nodes: str
    cpus: int | None
    submit_time: int | None
    start_time: int | None
    end_time: int | None
    exit_code: int | None
    source: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_squeue_job(job: dict[str, Any]) -> JobInfo:
    states = job.get("job_state") or ["UNKNOWN"]
    exit_code = job.get("exit_code") or {}
    return JobInfo(
        job_id=int(job["job_id"]),
        name=job.get("name", ""),
        user=job.get("user_name", ""),
        account=job.get("account", ""),
        partition=job.get("partition", ""),
        state=states[0] if isinstance(states, list) else str(states),
        reason=job.get("state_reason", ""),
        nodes=job.get("nodes", ""),
        cpus=number(job.get("cpus")),
        submit_time=number(job.get("submit_time")),
        start_time=number(job.get("start_time")) or None,
        end_time=number(job.get("end_time")) or None,
        exit_code=number((exit_code.get("return_code") if isinstance(exit_code, dict) else exit_code)),
        source="squeue",
    )


def parse_sacct_job(job: dict[str, Any]) -> JobInfo:
    state = job.get("state") or {}
    current = state.get("current") or ["UNKNOWN"]
    times = job.get("time") or {}
    exit_code = job.get("exit_code") or {}
    cpus = None
    for tres in (job.get("tres") or {}).get("allocated") or []:
        if tres.get("type") == "cpu":
            cpus = tres.get("count")
    return JobInfo(
        job_id=int(job["job_id"]),
        name=job.get("name", ""),
        user=job.get("user", ""),
        account=job.get("account", ""),
        partition=job.get("partition", ""),
        state=current[0] if isinstance(current, list) else str(current),
        reason=state.get("reason", ""),
        nodes=job.get("nodes", ""),
        cpus=cpus,
        submit_time=times.get("submission") or None,
        start_time=times.get("start") or None,
        end_time=times.get("end") or None,
        exit_code=number(exit_code.get("return_code")) if isinstance(exit_code, dict) else None,
        source="sacct",
    )


@dataclass
class NodeInfo:
    name: str
    partitions: list[str]
    state: str
    flags: list[str]
    cpus: int
    alloc_cpus: int
    real_memory_mb: int
    alloc_memory_mb: int
    address: str

    @property
    def powered_down(self) -> bool:
        return "POWERED_DOWN" in self.flags

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"powered_down": self.powered_down}


def parse_node(node: dict[str, Any]) -> NodeInfo:
    states = node.get("state") or ["UNKNOWN"]
    if not isinstance(states, list):
        states = [str(states)]
    return NodeInfo(
        name=node.get("name") or node.get("hostname", ""),
        partitions=list(node.get("partitions") or []),
        state=states[0],
        flags=states[1:],
        cpus=int(number(node.get("cpus")) or 0),
        alloc_cpus=int(number(node.get("alloc_cpus")) or 0),
        real_memory_mb=int(number(node.get("real_memory")) or 0),
        alloc_memory_mb=int(number(node.get("alloc_memory")) or 0),
        address=node.get("address", ""),
    )


class SlurmClient:
    def __init__(self, timeout_s: float = 20.0):
        self.timeout_s = timeout_s

    def _run(self, args: list[str], *, user: str | None = None, stdin: str | None = None) -> str:
        cmd = (["runuser", "-u", user, "--"] if user else []) + args
        start = time.monotonic()
        log.debug("running slurm command", extra={"cmd": args[0], "as_user": user or "root"})
        try:
            proc = subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=self.timeout_s)
        except subprocess.TimeoutExpired as exc:
            log.error("slurm command timed out", extra={"cmd": args, "timeout_s": self.timeout_s, "as_user": user})
            raise SlurmUnavailable(f"{args[0]} timed out after {self.timeout_s}s") from exc
        except OSError as exc:
            log.exception("slurm command could not be executed", extra={"cmd": args})
            raise SlurmUnavailable(f"cannot execute {args[0]}: {exc}") from exc
        duration_ms = round((time.monotonic() - start) * 1000, 1)
        if proc.returncode != 0:
            stderr = proc.stderr.strip()
            log.warning(
                "slurm command failed",
                extra={"cmd": args, "as_user": user, "rc": proc.returncode, "stderr": stderr[:500],
                       "duration_ms": duration_ms},
            )
            if any(marker in stderr for marker in _UNAVAILABLE_MARKERS):
                raise SlurmUnavailable(stderr)
            raise SlurmError(stderr or f"{args[0]} exited with {proc.returncode}", user_error=True)
        log.debug("slurm command ok", extra={"cmd": args[0], "duration_ms": duration_ms})
        return proc.stdout

    def _json(self, args: list[str], *, user: str | None = None) -> dict[str, Any]:
        out = self._run(args, user=user)
        try:
            data = json.loads(out)
        except ValueError as exc:
            log.error("unparseable JSON from slurm", extra={"cmd": args, "output_head": out[:300]})
            raise SlurmError(f"{args[0]} returned invalid JSON") from exc
        for err in data.get("errors") or []:
            log.warning("slurm reported error in JSON payload", extra={"cmd": args, "slurm_error": err})
        return data

    def ping(self) -> bool:
        out = self._run(["scontrol", "ping"])
        return "UP" in out

    def submit(self, *, user: str, script: str, sbatch_args: list[str]) -> int:
        out = self._run(["sbatch", "--parsable", *sbatch_args], user=user, stdin=script).strip()
        try:
            job_id = int(out.split(";")[0])
        except ValueError as exc:
            log.error("unexpected sbatch output", extra={"output": out[:200], "as_user": user})
            raise SlurmError(f"unexpected sbatch output: {out[:200]}") from exc
        log.info("job submitted", extra={"job_id": job_id, "as_user": user})
        return job_id

    def job(self, job_id: int, *, user: str) -> JobInfo:
        """Live state from slurmctld, falling back to accounting once the job has aged out of the queue."""
        try:
            jobs = self._json(["squeue", "--json", "--jobs", str(job_id)], user=user).get("jobs") or []
        except SlurmError as exc:
            if isinstance(exc, SlurmUnavailable) or "Invalid job id" not in str(exc):
                raise
            jobs = []
        if jobs:
            return parse_squeue_job(jobs[0])
        log.debug("job not in queue; checking accounting", extra={"job_id": job_id})
        records = self._json(["sacct", "--json", "--jobs", str(job_id)], user=user).get("jobs") or []
        records = [r for r in records if int(r.get("job_id", -1)) == job_id]
        if not records:
            raise JobNotFound(f"job {job_id} not found or not visible to {user}", user_error=True)
        return parse_sacct_job(records[-1])

    def user_jobs(self, *, user: str) -> list[JobInfo]:
        data = self._json(["squeue", "--json", "--user", user], user=user)
        return [parse_squeue_job(j) for j in data.get("jobs") or []]

    def all_jobs(self) -> list[JobInfo]:
        return [parse_squeue_job(j) for j in self._json(["squeue", "--json"]).get("jobs") or []]

    def nodes(self) -> list[NodeInfo]:
        return [parse_node(n) for n in self._json(["scontrol", "--json", "show", "nodes"]).get("nodes") or []]
