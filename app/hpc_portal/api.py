"""REST front door for the cluster: authenticated tenants submit and track Slurm jobs."""

import hmac
import logging
import os
import shlex
import time
import uuid
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import Settings
from .logging_setup import configure_logging, request_id_var
from .slurm import JobNotFound, SlurmClient, SlurmError, SlurmUnavailable

log = logging.getLogger("hpc_portal.api")


class SubmitJob(BaseModel):
    name: str = Field(default="portal-job", pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    partition: str = Field(default="local-node", max_length=64)
    command: str = Field(min_length=1, max_length=8192, description="Shell commands run by bash inside the job")
    cpus: int = Field(default=1, ge=1, le=64)
    mem_mb: int = Field(default=256, ge=16, le=65536)
    time_limit_min: int = Field(default=10, ge=1, le=60)


def build_script(command: str) -> str:
    return (
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        'echo "[portal] job ${SLURM_JOB_ID} on $(hostname) partition=${SLURM_JOB_PARTITION} '
        'cpus=${SLURM_CPUS_ON_NODE} user=$(id -un)"\n'
        f"{command}\n"
    )


def create_app(settings: Settings | None = None, slurm: SlurmClient | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    slurm = slurm or SlurmClient(timeout_s=settings.command_timeout_s)
    app = FastAPI(title="hpclab job portal", version=__version__)

    def current_user(authorization: Annotated[str | None, Header()] = None) -> str:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() == "bearer" and token:
            for known, user in settings.api_tokens.items():
                if hmac.compare_digest(known, token):
                    return user
        log.warning("rejected request with missing or invalid bearer token")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing or invalid bearer token",
                            headers={"WWW-Authenticate": "Bearer"})

    User = Annotated[str, Depends(current_user)]

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(rid)
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error", extra={"method": request.method, "path": request.url.path})
            response = JSONResponse({"detail": "internal error", "request_id": rid}, status_code=500)
        duration_ms = round((time.monotonic() - start) * 1000, 1)
        level = logging.INFO if response.status_code < 500 else logging.ERROR
        if request.url.path not in ("/healthz", "/readyz") or response.status_code >= 400:
            log.log(level, "request handled", extra={"method": request.method, "path": request.url.path,
                                                     "status": response.status_code, "duration_ms": duration_ms})
        response.headers["X-Request-ID"] = rid
        request_id_var.reset(token)
        return response

    @app.exception_handler(SlurmError)
    async def slurm_error_handler(request: Request, exc: SlurmError):
        if isinstance(exc, JobNotFound):
            code = status.HTTP_404_NOT_FOUND
        elif isinstance(exc, SlurmUnavailable):
            code = status.HTTP_503_SERVICE_UNAVAILABLE
        elif exc.user_error:
            code = status.HTTP_400_BAD_REQUEST
        else:
            code = status.HTTP_502_BAD_GATEWAY
        log.warning("slurm error returned to client", extra={"status": code, "error": str(exc)[:300]})
        return JSONResponse({"detail": str(exc), "request_id": request_id_var.get()}, status_code=code)

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz():
        try:
            up = slurm.ping()
        except SlurmError as exc:
            raise SlurmUnavailable(f"slurmctld ping failed: {exc}") from exc
        if not up:
            raise SlurmUnavailable("slurmctld did not answer ping")
        return {"status": "ready"}

    @app.post("/submit-job", status_code=status.HTTP_202_ACCEPTED)
    def submit_job(body: SubmitJob, user: User):
        if body.partition not in settings.partitions:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                f"partition must be one of {', '.join(settings.partitions)}")
        workdir = f"{settings.shared_root}/home/{user}"
        sbatch_args = [
            f"--job-name={body.name}",
            f"--partition={body.partition}",
            f"--cpus-per-task={body.cpus}",
            f"--mem={body.mem_mb}M",
            f"--time={body.time_limit_min}",
            f"--chdir={workdir}",
            f"--output={workdir}/slurm-%j.out",
        ]
        log.info("submitting job", extra={"as_user": user, "sbatch_args": shlex.join(sbatch_args)})
        job_id = slurm.submit(user=user, script=build_script(body.command), sbatch_args=sbatch_args)
        return {
            "job_id": job_id,
            "user": user,
            "partition": body.partition,
            "output": f"{workdir}/slurm-{job_id}.out",
            "status_url": f"/job-status/{job_id}",
        }

    @app.get("/job-status/{job_id}")
    def job_status(job_id: int, user: User):
        info = slurm.job(job_id, user=user)
        # Slurm's PrivateData already hides other tenants' jobs; this guards against
        # that setting being dropped from either slurm.conf or slurmdbd.conf.
        if info.user != user:
            log.warning("blocked cross-tenant job lookup", extra={"job_id": job_id, "as_user": user,
                                                                  "owner": info.user})
            raise JobNotFound(f"job {job_id} not found or not visible to {user}", user_error=True)
        return info.to_dict()

    @app.get("/jobs")
    def jobs(user: User):
        return {"user": user, "jobs": [j.to_dict() for j in slurm.user_jobs(user=user)]}

    @app.get("/cluster")
    def cluster(user: User):
        nodes = slurm.nodes()
        partitions: dict[str, dict[str, int]] = {}
        for n in nodes:
            for p in n.partitions:
                agg = partitions.setdefault(p, {"nodes": 0, "nodes_powered_up": 0, "cpus": 0, "alloc_cpus": 0})
                agg["nodes"] += 1
                agg["nodes_powered_up"] += 0 if n.powered_down else 1
                agg["cpus"] += n.cpus
                agg["alloc_cpus"] += n.alloc_cpus
        return {"partitions": partitions, "nodes": [n.to_dict() for n in nodes]}

    log.info("portal api initialised", extra={"version": __version__})
    return app


def main() -> FastAPI:
    """uvicorn factory entrypoint: ``uvicorn --factory hpc_portal.api:main``."""
    configure_logging(os.environ.get("LOG_LEVEL", "info"))
    return create_app()
