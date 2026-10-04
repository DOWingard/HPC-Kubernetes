import pytest
from fastapi.testclient import TestClient

from hpc_portal.api import build_script, create_app
from hpc_portal.config import Settings
from hpc_portal.slurm import JobInfo, JobNotFound, SlurmError, SlurmUnavailable

ALICE = "alice-token-0123456789abcdef"
BOB = "bob-token-0123456789abcdef00"


def job(job_id, user, state="RUNNING"):
    return JobInfo(job_id=job_id, name="t", user=user, account="physics", partition="local-node", state=state,
                   reason="None", nodes="bm-node-01", cpus=1, submit_time=1, start_time=2, end_time=None,
                   exit_code=0, source="squeue")


class FakeSlurm:
    def __init__(self):
        self.submitted = []
        self.jobs = {1: job(1, "alice")}
        self.ping_result = True
        self.submit_error = None

    def ping(self):
        if isinstance(self.ping_result, Exception):
            raise self.ping_result
        return self.ping_result

    def submit(self, *, user, script, sbatch_args):
        if self.submit_error:
            raise self.submit_error
        self.submitted.append((user, script, sbatch_args))
        return 42

    def job(self, job_id, *, user):
        if job_id not in self.jobs:
            raise JobNotFound(f"job {job_id} not found", user_error=True)
        return self.jobs[job_id]

    def user_jobs(self, *, user):
        return [j for j in self.jobs.values() if j.user == user]

    def nodes(self):
        return []


@pytest.fixture
def fake():
    return FakeSlurm()


@pytest.fixture
def client(fake):
    settings = Settings(api_tokens={ALICE: "alice", BOB: "bob"}, partitions=("local-node", "k8s-burst"),
                        shared_root="/shared", command_timeout_s=5, log_level="info")
    return TestClient(create_app(settings, fake))


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_requires_bearer_token(client):
    assert client.get("/jobs").status_code == 401
    assert client.get("/jobs", headers=auth("wrong")).status_code == 401


def test_submit_runs_as_token_owner(client, fake):
    resp = client.post("/submit-job", headers=auth(BOB),
                       json={"name": "sim", "partition": "k8s-burst", "command": "echo hi", "cpus": 2, "mem_mb": 512})
    assert resp.status_code == 202
    assert resp.json()["job_id"] == 42
    user, script, args = fake.submitted[0]
    assert user == "bob"
    assert "echo hi" in script
    assert "--partition=k8s-burst" in args and "--cpus-per-task=2" in args and "--mem=512M" in args
    assert "--chdir=/shared/home/bob" in args
    assert resp.headers["X-Request-ID"]


def test_submit_rejects_unknown_partition(client, fake):
    resp = client.post("/submit-job", headers=auth(ALICE), json={"partition": "gpu", "command": "true"})
    assert resp.status_code == 400
    assert not fake.submitted


def test_submit_rejects_bad_job_name(client):
    resp = client.post("/submit-job", headers=auth(ALICE), json={"name": "x; rm -rf /", "command": "true"})
    assert resp.status_code == 422


def test_slurm_rejection_is_client_error(client, fake):
    fake.submit_error = SlurmError("More processors requested than permitted", user_error=True)
    resp = client.post("/submit-job", headers=auth(ALICE), json={"command": "true", "cpus": 64})
    assert resp.status_code == 400
    assert "More processors" in resp.json()["detail"]


def test_controller_down_is_503(client, fake):
    fake.submit_error = SlurmUnavailable("Unable to contact slurm controller")
    assert client.post("/submit-job", headers=auth(ALICE), json={"command": "true"}).status_code == 503


def test_job_status_owner_and_cross_tenant(client):
    assert client.get("/job-status/1", headers=auth(ALICE)).json()["state"] == "RUNNING"
    assert client.get("/job-status/1", headers=auth(BOB)).status_code == 404
    assert client.get("/job-status/999", headers=auth(ALICE)).status_code == 404


def test_readyz_reflects_controller(client, fake):
    assert client.get("/readyz").status_code == 200
    fake.ping_result = SlurmError("scontrol exited with 1", user_error=True)
    assert client.get("/readyz").status_code == 503


def test_build_script_runs_command_under_strict_bash():
    script = build_script("python3 sim.py")
    assert script.startswith("#!/bin/bash\nset -euo pipefail\n")
    assert script.rstrip().endswith("python3 sim.py")
