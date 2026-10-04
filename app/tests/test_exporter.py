from hpc_portal.exporter import SlurmCollector
from hpc_portal.slurm import parse_node, parse_squeue_job


class StubSlurm:
    def __init__(self, nodes, jobs):
        self._nodes, self._jobs = nodes, jobs

    def nodes(self):
        return self._nodes

    def all_jobs(self):
        return self._jobs


def samples(collector):
    out = {}
    for family in collector.collect():
        for s in family.samples:
            out[(s.name, tuple(sorted(s.labels.items())))] = s.value
    return out


def test_partition_totals_from_real_snapshot(load_fixture):
    nodes = [parse_node(n) for n in load_fixture("nodes.json")["nodes"]]
    jobs = [parse_squeue_job(j) for j in load_fixture("squeue.json")["jobs"]]
    collector = SlurmCollector(StubSlurm(nodes, jobs), interval_s=10)
    collector.poll_once()
    m = samples(collector)

    assert m[("slurm_partition_cpus", (("partition", "k8s-burst"), ("state", "total")))] == 8
    # Only burst-2 is powered up in the fixture.
    assert m[("slurm_partition_cpus", (("partition", "k8s-burst"), ("state", "online")))] == 2
    assert m[("slurm_partition_nodes_powered_up", (("partition", "k8s-burst"),))] == 1
    assert m[("slurm_jobs", (("account", "chemistry"), ("partition", "local-node"), ("state", "pending")))] == 1
    assert m[("slurm_job_cpus", (("account", "chemistry"), ("partition", "local-node"), ("state", "running")))] == 4


def test_failed_poll_keeps_previous_snapshot(load_fixture):
    nodes = [parse_node(n) for n in load_fixture("nodes.json")["nodes"]]
    stub = StubSlurm(nodes, [])
    collector = SlurmCollector(stub, interval_s=10)
    collector.poll_once()

    from hpc_portal.slurm import SlurmUnavailable

    def boom():
        raise SlurmUnavailable("down")

    stub.nodes = boom
    collector.poll_once()
    m = samples(collector)
    assert m[("slurm_exporter_poll_errors_total", (("source", "nodes"),))] == 1
    assert m[("slurm_partition_cpus", (("partition", "local-node"), ("state", "total")))] == 4
