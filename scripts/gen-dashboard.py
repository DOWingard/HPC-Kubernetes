#!/usr/bin/env python3
"""Generates the Grafana dashboard JSON so panel layout and colors stay consistent.

Run: python3 scripts/gen-dashboard.py > k8s/apps/observability/dashboards/hpc-cpu-allocation.json
"""
import json

DS = {"type": "prometheus", "uid": "prometheus"}
# Fixed identity colors: a partition keeps its color in every panel.
PARTITION_COLORS = {"local-node": "#3987e5", "k8s-burst": "#d95926"}
CATEGORICAL = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
NAMESPACES = ["hpc", "monitoring", "argocd", "kube-system"]

_id = 0


def nid():
    global _id
    _id += 1
    return _id


def color_overrides(mapping):
    return [
        {"matcher": {"id": "byName", "options": name},
         "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": color}}]}
        for name, color in mapping.items()
    ]


def stat(title, expr, x, unit="none", desc=""):
    return {
        "id": nid(), "type": "stat", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"h": 4, "w": 4, "x": x, "y": 0},
        "targets": [{"refId": "A", "expr": expr, "datasource": DS}],
        "fieldConfig": {"defaults": {"unit": unit, "color": {"mode": "fixed", "fixedColor": "text"},
                                     "decimals": 0 if unit == "none" else 1}, "overrides": []},
        "options": {"colorMode": "none", "graphMode": "area", "textMode": "value",
                    "reduceOptions": {"calcs": ["lastNotNull"]}},
    }


def timeseries(title, targets, x, y, w=12, h=8, unit="none", stack=True, overrides=None, desc="", fill=25):
    return {
        "id": nid(), "type": "timeseries", "title": title, "description": desc, "datasource": DS,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "targets": [{"refId": chr(65 + i), "expr": e, "legendFormat": lf, "datasource": DS}
                    for i, (e, lf) in enumerate(targets)],
        "fieldConfig": {
            "defaults": {
                "unit": unit, "min": 0,
                "color": {"mode": "palette-classic"},
                "custom": {"lineWidth": 2, "fillOpacity": fill, "showPoints": "never",
                           "stacking": {"mode": "normal" if stack else "none", "group": "A"},
                           "axisSoftMin": 0, "gradientMode": "none"},
            },
            "overrides": overrides or [],
        },
        "options": {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
                    "tooltip": {"mode": "multi", "sort": "desc"}},
    }


ns_colors = dict(zip(NAMESPACES, CATEGORICAL))
panels = [
    stat("Slurm CPUs allocated", 'sum(slurm_partition_cpus{state="allocated"})', 0,
         desc="CPUs held by running Slurm jobs across both partitions"),
    stat("Slurm jobs pending", 'sum(slurm_jobs{state="pending"})', 4),
    stat("Slurm jobs running", 'sum(slurm_jobs{state="running"})', 8),
    stat("Burst nodes powered up", 'sum(slurm_partition_nodes_powered_up{partition="k8s-burst"})', 12,
         desc="Slurm CLOUD nodes currently backed by a Kubernetes pod"),
    stat("K8s CPU requested (cores)", "sum(kube_pod_container_resource_requests{resource=\"cpu\"})", 16, unit="short"),
    stat("K8s CPU used (cores)", 'sum(rate(container_cpu_usage_seconds_total{container!="",image!=""}[2m]))', 20,
         unit="short"),
    timeseries("Slurm — allocated CPUs by partition",
               [('sum by (partition) (slurm_partition_cpus{state="allocated"})', "{{partition}}")],
               0, 4, overrides=color_overrides(PARTITION_COLORS),
               desc="CPUs Slurm has granted to jobs. local-node is the bare-metal container, k8s-burst are pods."),
    timeseries("Kubernetes — CPU requests by namespace (cores)",
               [('sum by (namespace) (kube_pod_container_resource_requests{resource="cpu"})', "{{namespace}}")],
               12, 4, unit="short", overrides=color_overrides(ns_colors),
               desc="CPU reserved by the Kubernetes scheduler. Burst pods appear under hpc while powered up."),
    timeseries("Slurm — online vs allocated CPUs (k8s-burst)",
               [('sum(slurm_partition_cpus{partition="k8s-burst",state="online"})', "online capacity"),
                ('sum(slurm_partition_cpus{partition="k8s-burst",state="allocated"})', "allocated")],
               0, 12, stack=False, fill=10,
               overrides=color_overrides({"online capacity": "#898781", "allocated": "#d95926"}),
               desc="Capacity appears as Slurm powers burst nodes up (pods created) and disappears after SuspendTime."),
    timeseries("Kubernetes — CPU usage by namespace (cores)",
               [('sum by (namespace) (rate(container_cpu_usage_seconds_total{container!="",image!=""}[2m]))',
                 "{{namespace}}")],
               12, 12, unit="short", overrides=color_overrides(ns_colors),
               desc="Actual CPU consumed (cAdvisor). Burst jobs show up in hpc."),
    timeseries("Slurm — queued jobs by partition and state",
               [('sum by (partition, state) (slurm_jobs{state=~"pending|running"})', "{{partition}} {{state}}")],
               0, 20, stack=False, fill=0,
               overrides=color_overrides({"local-node running": "#3987e5", "k8s-burst running": "#d95926",
                                          "local-node pending": "#86b6ef", "k8s-burst pending": "#ec835a"})),
    timeseries("Kubernetes — burst pod CPU usage (cores)",
               [('sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="hpc",pod=~"burst-.*",'
                 'container="slurmd"}[1m]))', "{{pod}}")],
               12, 20, unit="short", stack=False, fill=10,
               desc="Per-pod usage of burst nodes; each pod is limited to 2 cores, matching its Slurm CPUs."),
    {
        "id": nid(), "type": "table", "title": "Slurm nodes", "datasource": DS,
        "gridPos": {"h": 7, "w": 24, "x": 0, "y": 28},
        "targets": [
            {"refId": "A", "expr": 'sum by (node, partition) (slurm_node_cpus{state="total"})', "format": "table", "instant": True, "datasource": DS},
            {"refId": "B", "expr": 'sum by (node, partition) (slurm_node_cpus{state="allocated"})', "format": "table", "instant": True, "datasource": DS},
            {"refId": "C", "expr": 'sum by (node, partition) (slurm_node_powered_up)', "format": "table", "instant": True, "datasource": DS},
        ],
        # Each query is reduced to the same (node, partition) labels so "merge" joins them row by row.
        "transformations": [
            {"id": "merge", "options": {}},
            {"id": "organize", "options": {
                "excludeByName": {"Time": True, "__name__": True, "container": True, "endpoint": True,
                                  "instance": True, "job": True, "namespace": True, "pod": True,
                                  "service": True, "state": True},
                "renameByName": {"Value #A": "CPUs", "Value #B": "allocated", "Value #C": "powered up"},
            }},
        ],
        "fieldConfig": {"defaults": {}, "overrides": []},
        "options": {"showHeader": True},
    },
]

dashboard = {
    "uid": "hpclab-cpu",
    "title": "HPC Lab — Slurm & Kubernetes CPU allocation",
    "tags": ["hpclab", "slurm", "kubernetes"],
    "timezone": "browser",
    "schemaVersion": 39,
    "refresh": "10s",
    "time": {"from": "now-30m", "to": "now"},
    "graphTooltip": 1,
    "panels": panels,
}
print(json.dumps(dashboard, indent=2))
