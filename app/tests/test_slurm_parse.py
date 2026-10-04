"""Parsers against JSON captured from a live Slurm 25.11 cluster."""

from hpc_portal.slurm import number, parse_node, parse_sacct_job, parse_squeue_job


def test_number_unwraps_slurm_encoding():
    assert number({"set": True, "infinite": False, "number": 4}) == 4
    assert number({"set": False, "infinite": False, "number": 0}) is None
    assert number({"set": True, "infinite": True, "number": 0}) is None
    assert number(7) == 7


def test_squeue_running_and_pending(load_fixture):
    jobs = {j.job_id: j for j in map(parse_squeue_job, load_fixture("squeue.json")["jobs"])}
    running = jobs[6]
    assert (running.state, running.user, running.nodes, running.cpus) == ("RUNNING", "bob", "bm-node-01", 4)
    assert running.source == "squeue"
    pending = jobs[7]
    assert (pending.state, pending.reason, pending.nodes) == ("PENDING", "Resources", "")


def test_sacct_completed_job(load_fixture):
    job = parse_sacct_job(load_fixture("sacct.json")["jobs"][0])
    assert (job.job_id, job.user, job.account, job.state) == (1, "alice", "physics", "COMPLETED")
    assert job.cpus == 2
    assert job.exit_code == 0
    assert job.end_time >= job.start_time >= job.submit_time > 0
    assert job.source == "sacct"


def test_nodes_power_state(load_fixture):
    nodes = {n.name: n for n in map(parse_node, load_fixture("nodes.json")["nodes"])}
    assert not nodes["bm-node-01"].powered_down
    assert nodes["bm-node-01"].address == "172.28.0.200"
    assert nodes["burst-0"].powered_down
    assert nodes["burst-0"].state == "IDLE"
    assert "CLOUD" in nodes["burst-2"].flags and not nodes["burst-2"].powered_down
    assert nodes["burst-2"].cpus == 2
