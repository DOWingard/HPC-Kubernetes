"""Prometheus exporter for Slurm node, partition and queue state.

A background thread polls slurmctld on a fixed interval and the collector serves
the last good snapshot, so a slow or unavailable controller degrades metrics
freshness (visible via slurm_exporter_last_success_timestamp_seconds) instead
of failing Prometheus scrapes.
"""

import logging
import os
import threading
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from prometheus_client import REGISTRY, start_http_server
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

from .logging_setup import configure_logging
from .slurm import JobInfo, NodeInfo, SlurmClient, SlurmError

log = logging.getLogger("hpc_portal.exporter")

# Pending jobs carry no node assignment yet; their CPU request still counts as demand on the partition.
_JOB_STATES = ("PENDING", "RUNNING", "CONFIGURING", "COMPLETING", "SUSPENDED")


@dataclass
class Snapshot:
    nodes: list[NodeInfo] = field(default_factory=list)
    jobs: list[JobInfo] = field(default_factory=list)
    taken_at: float = 0.0
    duration_s: float = 0.0


class SlurmCollector:
    def __init__(self, slurm: SlurmClient, interval_s: float):
        self.slurm = slurm
        self.interval_s = interval_s
        self.snapshot = Snapshot()
        self.errors: Counter[str] = Counter()
        self._lock = threading.Lock()

    def poll_once(self) -> None:
        start = time.monotonic()
        try:
            nodes = self.slurm.nodes()
        except SlurmError:
            self.errors["nodes"] += 1
            log.exception("failed to poll nodes; serving previous snapshot",
                          extra={"consecutive_errors": self.errors["nodes"]})
            return
        try:
            jobs = self.slurm.all_jobs()
        except SlurmError:
            self.errors["jobs"] += 1
            log.exception("failed to poll jobs; serving previous snapshot")
            return
        duration = time.monotonic() - start
        with self._lock:
            self.snapshot = Snapshot(nodes=nodes, jobs=jobs, taken_at=time.time(), duration_s=duration)
        log.debug("poll complete", extra={"nodes": len(nodes), "jobs": len(jobs), "duration_s": round(duration, 3)})

    def run_forever(self) -> None:
        while True:
            self.poll_once()
            time.sleep(self.interval_s)

    def collect(self):
        with self._lock:
            snap = self.snapshot

        node_cpus = GaugeMetricFamily("slurm_node_cpus", "CPUs on a node by allocation state",
                                      labels=["node", "partition", "state"])
        node_mem = GaugeMetricFamily("slurm_node_memory_bytes", "Memory on a node by allocation state",
                                     labels=["node", "partition", "state"])
        node_up = GaugeMetricFamily("slurm_node_powered_up", "1 if the node is powered up (burst pod exists)",
                                    labels=["node", "partition"])
        node_state = GaugeMetricFamily("slurm_node_state", "Current base state of each node (always 1)",
                                       labels=["node", "partition", "state"])
        part_cpus = GaugeMetricFamily("slurm_partition_cpus", "CPUs per partition by allocation state",
                                      labels=["partition", "state"])
        part_nodes = GaugeMetricFamily("slurm_partition_nodes_powered_up", "Powered-up nodes per partition",
                                       labels=["partition"])

        part_totals: dict[str, Counter] = defaultdict(Counter)
        for n in snap.nodes:
            for p in n.partitions or ["none"]:
                node_cpus.add_metric([n.name, p, "allocated"], n.alloc_cpus)
                node_cpus.add_metric([n.name, p, "total"], n.cpus)
                node_mem.add_metric([n.name, p, "allocated"], n.alloc_memory_mb * 2**20)
                node_mem.add_metric([n.name, p, "total"], n.real_memory_mb * 2**20)
                node_up.add_metric([n.name, p], 0 if n.powered_down else 1)
                node_state.add_metric([n.name, p, n.state.lower()], 1)
                t = part_totals[p]
                t["allocated"] += n.alloc_cpus
                t["total"] += n.cpus
                if not n.powered_down:
                    t["online"] += n.cpus
                    t["nodes_up"] += 1
        for p, t in part_totals.items():
            part_cpus.add_metric([p, "allocated"], t["allocated"])
            part_cpus.add_metric([p, "online"], t["online"])
            part_cpus.add_metric([p, "total"], t["total"])
            part_cpus.add_metric([p, "idle"], max(t["online"] - t["allocated"], 0))
            part_nodes.add_metric([p], t["nodes_up"])

        jobs = GaugeMetricFamily("slurm_jobs", "Jobs in the queue", labels=["partition", "state", "account"])
        job_cpus = GaugeMetricFamily("slurm_job_cpus", "CPUs requested/held by queued jobs",
                                     labels=["partition", "state", "account"])
        counts: Counter = Counter()
        cpus: Counter = Counter()
        for j in snap.jobs:
            key = (j.partition, j.state.lower(), j.account or "none")
            counts[key] += 1
            cpus[key] += j.cpus or 0
        for p in part_totals:
            for state in _JOB_STATES:
                counts.setdefault((p, state.lower(), "none"), 0)
        for key, value in counts.items():
            jobs.add_metric(list(key), value)
            job_cpus.add_metric(list(key), cpus[key])

        yield from (node_cpus, node_mem, node_up, node_state, part_cpus, part_nodes, jobs, job_cpus)

        last = GaugeMetricFamily("slurm_exporter_last_success_timestamp_seconds", "Unix time of last good poll")
        last.add_metric([], snap.taken_at)
        dur = GaugeMetricFamily("slurm_exporter_poll_duration_seconds", "Duration of the last good poll")
        dur.add_metric([], snap.duration_s)
        errs = CounterMetricFamily("slurm_exporter_poll_errors", "Failed polls by source", labels=["source"])
        for source in ("nodes", "jobs"):
            errs.add_metric([source], self.errors[source])
        yield from (last, dur, errs)


def main() -> None:
    configure_logging(os.environ.get("LOG_LEVEL", "info"))
    port = int(os.environ.get("EXPORTER_PORT", "9341"))
    interval = float(os.environ.get("EXPORTER_INTERVAL", "10"))
    collector = SlurmCollector(SlurmClient(timeout_s=float(os.environ.get("SLURM_COMMAND_TIMEOUT", "20"))), interval)
    collector.poll_once()
    REGISTRY.register(collector)
    threading.Thread(target=collector.run_forever, name="slurm-poller", daemon=True).start()
    start_http_server(port)
    log.info("slurm exporter listening", extra={"port": port, "interval_s": interval})
    threading.Event().wait()


if __name__ == "__main__":
    main()
