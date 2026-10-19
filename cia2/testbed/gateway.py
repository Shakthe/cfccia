"""Fog gateway: receives tasks from wearables and schedules them.

The same policy classes used in the simulator (fogsim.policies) pick a node
for each task. The gateway keeps its own count of the work it has sent to
every node and measures the network delay to each node from real replies,
so its estimates come from the live system.
"""

import collections
import logging
import os
import random
import statistics
import threading
import time

import requests
from flask import Flask, jsonify, render_template_string, request

from fogsim import model
from fogsim.estimate import wait_for_core
from fogsim.policies import Option, Task, make_policy

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("vitalwatch.gateway")

app = Flask(__name__)

NODE_URLS = {
    "fog-1": os.environ.get("FOG1_URL", "http://fog-1:8000"),
    "fog-2": os.environ.get("FOG2_URL", "http://fog-2:8000"),
    "fog-3": os.environ.get("FOG3_URL", "http://fog-3:8000"),
    "cloud": os.environ.get("CLOUD_URL", "http://cloud:8000"),
}

_lock = threading.Lock()
_local = threading.local()
_ids = iter(range(1, 10**12))


class NodeState:
    def __init__(self, name, url):
        self.name, self.url = name, url
        self.tier = model.NODES[name]["tier"]
        self.cores, self.mips = 1, 1000.0
        self.outstanding = {}      # task id -> (mi, priority, time sent)
        self.net_s = 0.07 if self.tier == "cloud" else 0.003
        self.live = {}             # last /stats reply
        self.done = 0

    def load(self):
        info = requests.get(f"{self.url}/info", timeout=60).json()
        self.cores, self.mips = info["cores"], float(info["mips"])


NODES = {n: NodeState(n, u) for n, u in NODE_URLS.items()}
STATE = {"policy_key": os.environ.get("POLICY", "laps"), "started": time.time()}
STATE["policy"] = make_policy(STATE["policy_key"])
RECENT = collections.deque(maxlen=20000)


def _session():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
    return _local.s


def _options(task, policy):
    """Build one Option per node. Call with _lock held."""
    now = time.time()
    opts = []
    for name, n in NODES.items():
        # the oldest `cores` tasks sent to a node are taken to be running,
        # the rest to be waiting in its queue
        out = sorted(n.outstanding.values(), key=lambda o: o[2])
        running, queued = out[:n.cores], out[n.cores:]
        remaining = [mi / n.mips - (now - sent) for mi, _, sent in running]
        ahead = sum(mi for mi, p, _ in queued
                    if not policy.priority_queue or p <= task.priority)
        wait = wait_for_core(remaining, n.cores, ahead / n.mips)
        exec_s = task.mi / n.mips
        est = (now - task.created) + n.net_s + wait + exec_s
        energy = (exec_s * model.NODES[name]["core_watts"]
                  + model.network_energy(task.bits, task.gateway, name))
        inflight = collections.Counter(p for _, p, _ in out)
        opts.append(Option(node=name, tier=n.tier, local=(name == task.gateway),
                           est_response=est, energy=energy,
                           util=min(len(out), n.cores) / n.cores, cores=n.cores,
                           inflight={p: inflight.get(p, 0) for p in (1, 2, 3)}))
    return opts


@app.route("/health")
def health():
    return jsonify({"status": "healthy", "policy": STATE["policy"].name})


@app.route("/task", methods=["POST"])
def task():
    body = request.get_json(force=True)
    kind = body["kind"]
    spec = model.TASK_TYPES[kind]
    task = Task(id=next(_ids), kind=kind, priority=spec["priority"],
                mi=float(body.get("mi") or random.uniform(*spec["mi"])),
                bits=len(body.get("payload", "")) * 8.0,
                deadline=spec["deadline"], created=float(body.get("created", time.time())),
                gateway=body["ward"], patient=body.get("patient", 0))

    policy = STATE["policy"]
    # estimate, choose and book the node in one step, so two tasks arriving
    # together cannot both grab the same "free" core
    with _lock:
        choice = policy.choose(task, _options(task, policy))
        node = NODES[choice]
        node.outstanding[task.id] = (task.mi, task.priority, time.time())

    sent = time.time()
    try:
        r = _session().post(f"{node.url}/run", timeout=120, json={
            "id": task.id, "kind": kind, "priority": task.priority, "mi": task.mi,
            "deadline_at": task.created + task.deadline,
            "priority_queue": policy.priority_queue, "payload": body.get("payload", "")})
        r.raise_for_status()
        reply = r.json()
    except requests.RequestException:
        logger.exception("task %d to %s failed", task.id, choice)
        with _lock:
            node.outstanding.pop(task.id, None)
        return jsonify({"error": f"node {choice} failed"}), 502
    back = time.time()

    response_ms = (back - task.created) * 1000
    net = max(0.0, (back - sent) - (reply["queued_ms"] + reply["exec_ms"]) / 1000)
    rec = {"id": task.id, "t": back, "patient": task.patient, "ward": task.gateway,
           "kind": kind, "priority": task.priority, "node": choice,
           "response_ms": round(response_ms, 1), "met": response_ms <= task.deadline * 1000,
           "queued_ms": round(reply["queued_ms"], 1), "exec_ms": round(reply["exec_ms"], 1)}
    with _lock:
        node.outstanding.pop(task.id, None)
        node.net_s = 0.8 * node.net_s + 0.2 * net
        node.done += 1
        RECENT.append(rec)
    return jsonify(rec)


def _summary():
    with _lock:
        recs = list(RECENT)
    out = {"policy": STATE["policy"].name, "tasks": len(recs), "kinds": {}, "nodes": {}}
    for kind in model.TASK_TYPES:
        rk = sorted(r["response_ms"] for r in recs if r["kind"] == kind)
        if rk:
            out["kinds"][kind] = {
                "tasks": len(rk), "avg_ms": round(statistics.fmean(rk), 1),
                "p95_ms": rk[int(0.95 * (len(rk) - 1))],
                "met_pct": round(100 * sum(r["met"] for r in recs if r["kind"] == kind) / len(rk), 1)}
    for name, n in NODES.items():
        share = sum(r["node"] == name for r in recs)
        out["nodes"][name] = {"label": model.NODES[name]["label"], "cores": n.cores,
                              "mips": round(n.mips), "in_flight": len(n.outstanding),
                              "busy": n.live.get("busy", 0), "queued": n.live.get("queued", 0),
                              "share_pct": round(100 * share / len(recs), 1) if recs else 0.0,
                              "net_ms": round(n.net_s * 1000, 1)}
    return out


@app.route("/stats")
def stats():
    return jsonify(_summary())


@app.route("/export")
def export():
    with _lock:
        return jsonify(list(RECENT))


@app.route("/reset", methods=["POST"])
def reset():
    with _lock:
        RECENT.clear()
    return jsonify({"reset": True})


@app.route("/policy", methods=["POST"])
def set_policy():
    key = request.get_json(force=True)["policy"]
    STATE["policy_key"], STATE["policy"] = key, make_policy(key)
    logger.info("policy switched to %s", STATE["policy"].name)
    return jsonify({"policy": STATE["policy"].name})


DASHBOARD = """
<!doctype html><html><head><title>VitalWatch-Fog scheduler</title>
<meta http-equiv="refresh" content="2">
<style>
 body{font-family:-apple-system,Segoe UI,sans-serif;margin:2rem;background:#f4f7fb;color:#16324f}
 h1{color:#0b5394;margin-bottom:.2rem} .sub{color:#5a7184;margin-top:0}
 table{border-collapse:collapse;background:#fff;box-shadow:0 1px 3px rgba(22,50,79,.15);margin-bottom:1.6rem;width:100%}
 th,td{padding:.45rem .8rem;border-bottom:1px solid #e3eaf3;text-align:left;font-size:.9rem}
 th{background:#0b5394;color:#fff} .bad{color:#c0392b;font-weight:700} .ok{color:#1e7d3a;font-weight:600}
 .bar{background:#e3eaf3;border-radius:3px;height:10px;width:120px;display:inline-block;vertical-align:middle}
 .bar i{display:block;height:10px;border-radius:3px;background:#0b5394}
</style></head><body>
<h1>VitalWatch-Fog &mdash; latency-aware scheduler</h1>
<p class="sub">Policy: <b>{{s.policy}}</b> &middot; {{s.tasks}} tasks measured &middot; refreshes every 2 s</p>
<h2>Nodes</h2>
<table><tr><th>Node</th><th>Role</th><th>Cores</th><th>MIPS/core</th><th>CPU busy</th><th>Queue</th><th>Share of tasks</th><th>Net delay</th></tr>
{% for name, n in s.nodes.items() %}<tr><td><b>{{name}}</b></td><td>{{n.label}}</td><td>{{n.cores}}</td><td>{{n.mips}}</td>
<td><span class="bar"><i style="width:{{(100*n.busy/n.cores)|round|int}}%"></i></span> {{n.busy}}/{{n.cores}}</td>
<td>{{n.queued}}</td><td>{{n.share_pct}}%</td><td>{{n.net_ms}} ms</td></tr>{% endfor %}</table>
<h2>Deadlines by task type</h2>
<table><tr><th>Task type</th><th>Deadline</th><th>Tasks</th><th>Avg response</th><th>95th pct</th><th>Deadline met</th></tr>
{% for k, d in s.kinds.items() %}<tr><td>{{k}}</td><td>{{(types[k].deadline*1000)|int}} ms</td><td>{{d.tasks}}</td>
<td>{{d.avg_ms}} ms</td><td>{{d.p95_ms}} ms</td>
<td class="{{'ok' if d.met_pct>=95 else 'bad'}}">{{d.met_pct}}%</td></tr>{% endfor %}</table>
<h2>Latest tasks</h2>
<table><tr><th>Task</th><th>Patient</th><th>Ward gateway</th><th>Type</th><th>Sent to</th><th>Queued</th><th>Run</th><th>Response</th><th>Deadline</th></tr>
{% for r in recent %}<tr><td>#{{r.id}}</td><td>P-{{1000 + r.patient}}</td><td>{{r.ward}}</td><td>{{r.kind}}</td><td>{{r.node}}</td>
<td>{{r.queued_ms}} ms</td><td>{{r.exec_ms}} ms</td><td>{{r.response_ms}} ms</td>
<td class="{{'ok' if r.met else 'bad'}}">{{'met' if r.met else 'MISSED'}}</td></tr>{% endfor %}</table>
</body></html>
"""


@app.route("/")
def dashboard():
    with _lock:
        recent = list(RECENT)[-12:][::-1]
    return render_template_string(DASHBOARD, s=_summary(), recent=recent, types=model.TASK_TYPES)


def _poll_nodes():
    while True:
        for n in NODES.values():
            try:
                n.live = _session().get(f"{n.url}/stats", timeout=2).json()
            except requests.RequestException:
                logger.warning("could not reach %s", n.name)
        time.sleep(1)


def start():
    for n in NODES.values():
        for attempt in range(30):
            try:
                n.load()
                break
            except requests.RequestException:
                time.sleep(2)
        logger.info("%s: %d cores, %.0f MIPS/core", n.name, n.cores, n.mips)
    threading.Thread(target=_poll_nodes, daemon=True).start()
    logger.info("gateway ready, policy %s", STATE["policy"].name)


if os.environ.get("GATEWAY_AUTOSTART", "1") == "1":
    threading.Thread(target=start, daemon=True).start()
