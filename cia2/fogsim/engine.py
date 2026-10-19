"""Discrete-event simulator of the hospital fog system.

Path of one task:
wearable -> Wi-Fi -> ward gateway (scheduler decides here) -> chosen node
(same gateway, another fog node over the hospital LAN, or the cloud over the
WAN uplink) -> queue -> CPU core -> result back to the wearable.
"""

import heapq
import random
import statistics

from fogsim import model
from fogsim.estimate import wait_for_core
from fogsim.policies import Option, Task, make_policy


class Link:
    """Shared FIFO link: one transfer at a time, then propagation delay."""

    def __init__(self, mbps, prop_ms, jitter_ms, **_):
        self.bw = mbps * 1e6
        self.prop = prop_ms / 1000
        self.jitter = jitter_ms / 1000
        self.free_at = 0.0

    def estimate(self, now, bits):
        return max(0.0, self.free_at - now) + bits / self.bw + self.prop + self.jitter / 2

    def send(self, now, bits, rng):
        start = max(now, self.free_at)
        self.free_at = start + bits / self.bw
        return self.free_at + self.prop + (rng.uniform(0, self.jitter) if self.jitter else 0.0)


class Node:
    """A fog node or the cloud: a few CPU cores in front of one task queue."""

    def __init__(self, name, sim, priority_queue):
        spec = model.NODES[name]
        self.name, self.tier = name, spec["tier"]
        self.cores, self.mips = spec["cores"], spec["mips"]
        self.sim = sim
        self.priority_queue = priority_queue
        self.queue = []
        self.running = {}                      # task id -> finish time
        self.queued_mi = {1: 0.0, 2: 0.0, 3: 0.0}
        self.incoming_mi = {1: 0.0, 2: 0.0, 3: 0.0}
        self.inflight = {1: 0, 2: 0, 3: 0}     # on the way + queued + running
        self.busy_s = 0.0                      # core-seconds inside the measured window
        self._seq = 0

    def util(self):
        return len(self.running) / self.cores

    def est_wait(self, now, priority):
        """Seconds until a core would be free for a new task of this priority.

        Work already ahead of it is spread over the cores as they free up.
        With a priority queue, lower-priority work waiting in the queue is
        not counted because the new task jumps ahead of it.
        """
        ahead = sum(self.queued_mi[p] + self.incoming_mi[p] for p in (1, 2, 3)
                    if not self.priority_queue or p <= priority)
        remaining = [f - now for f in self.running.values()]
        return wait_for_core(remaining, self.cores, max(0.0, ahead) / self.mips)

    def dispatch(self, task):
        self.incoming_mi[task.priority] += task.mi
        self.inflight[task.priority] += 1

    def arrive(self, now, task):
        p = task.priority
        self.incoming_mi[p] -= task.mi
        self.queued_mi[p] += task.mi
        self._seq += 1
        if self.priority_queue:
            key = (p, task.created + task.deadline, self._seq)
        else:
            key = (self._seq,)
        heapq.heappush(self.queue, (key, task))
        self._start(now)

    def _start(self, now):
        while self.queue and len(self.running) < self.cores:
            _, task = heapq.heappop(self.queue)
            self.queued_mi[task.priority] -= task.mi
            done = now + task.mi / self.mips
            self.running[task.id] = done
            task.start = now
            self.sim.at(done, self.finish, task)

    def finish(self, now, task):
        del self.running[task.id]
        self.inflight[task.priority] -= 1
        self.sim.completed(self, task, now)
        self._start(now)


class Simulator:
    def __init__(self, policy, n_patients, seed=1, duration=300.0, warmup=30.0, burst=None):
        self.policy = policy
        self.n_patients = n_patients
        self.rng = random.Random(seed)
        self.duration, self.warmup = duration, warmup
        self.burst = burst
        self.events = []
        self._eseq = 0
        self.nodes = {n: Node(n, self, policy.priority_queue) for n in model.NODES}
        self.wifi = {g: Link(**model.LINKS["wifi"]) for g in model.FOG_NODES}
        self.lan = Link(**model.LINKS["lan"])
        self.wan = Link(**model.LINKS["wan"])
        self.records = []
        self._task_id = 0

    # --- event plumbing -------------------------------------------------
    def at(self, t, fn, *args):
        self._eseq += 1
        heapq.heappush(self.events, (t, self._eseq, fn, args))

    def run(self):
        self._make_arrivals()
        while self.events:
            t, _, fn, args = heapq.heappop(self.events)
            fn(t, *args)
        return self

    # --- workload ---------------------------------------------------------
    def _segments(self, emergency):
        """(start, end, multiplier per task kind) pieces of the timeline."""
        if not (self.burst and emergency):
            return [(0.0, self.duration, {})]
        b = self.burst
        boost = {"critical": b["critical_x"], "ecg": b["ecg_x"]}
        return [(0.0, b["start"], {}), (b["start"], b["end"], boost), (b["end"], self.duration, {})]

    def _make_arrivals(self):
        pid = 0
        for g, share in model.ATTACH_SHARE.items():
            n_g = round(self.n_patients * share)
            n_emerg = round(n_g * self.burst["share"]) if self.burst else 0
            for emergency, size in ((True, n_emerg), (False, n_g - n_emerg)):
                if size == 0:
                    continue
                first = pid
                pid += size
                for kind, spec in model.TASK_TYPES.items():
                    for start, end, boost in self._segments(emergency):
                        rate = size * spec["rate"] * boost.get(kind, 1)
                        t = start
                        while True:
                            t += self.rng.expovariate(rate)
                            if t >= end:
                                break
                            patient = self.rng.randrange(first, first + size)
                            self.at(t, self._create, g, kind, patient)

    def _create(self, now, gateway, kind, patient):
        spec = model.TASK_TYPES[kind]
        self._task_id += 1
        task = Task(id=self._task_id, kind=kind, priority=spec["priority"],
                    mi=self.rng.uniform(*spec["mi"]),
                    bits=self.rng.uniform(*spec["kb"]) * 8000,
                    deadline=spec["deadline"], created=now,
                    gateway=gateway, patient=patient)
        reach = self.wifi[gateway].send(now, task.bits, self.rng)
        self.at(reach, self._decide, task)

    # --- scheduling -------------------------------------------------------
    def options(self, now, task):
        opts = []
        for name, node in self.nodes.items():
            if name == task.gateway:
                tx = 0.0
            elif name == "cloud":
                tx = self.wan.estimate(now, task.bits)
            else:
                tx = self.lan.estimate(now, task.bits)
            wait = node.est_wait(now, task.priority)
            back = model.return_delay(task.gateway, name)
            if name == "cloud":
                back += model.LINKS["wan"]["jitter_ms"] / 2000
            est = (now - task.created) + max(tx, wait) + model.exec_time(task.mi, name) + back
            energy = (model.compute_energy(task.mi, name)
                      + model.network_energy(task.bits, task.gateway, name))
            opts.append(Option(node=name, tier=node.tier, local=(name == task.gateway),
                               est_response=est, energy=energy, util=node.util(),
                               cores=node.cores, inflight=dict(node.inflight)))
        return opts

    def _decide(self, now, task):
        choice = self.policy.choose(task, self.options(now, task))
        task.node = choice
        node = self.nodes[choice]
        node.dispatch(task)
        if choice == task.gateway:
            reach = now
        elif choice == "cloud":
            reach = self.wan.send(now, task.bits, self.rng)
        else:
            reach = self.lan.send(now, task.bits, self.rng)
        self.at(reach, node.arrive, task)

    # --- bookkeeping ------------------------------------------------------
    def completed(self, node, task, now):
        lo, hi = self.warmup, self.duration
        node.busy_s += max(0.0, min(now, hi) - max(task.start, lo))
        if not (lo <= task.created < hi):
            return
        back = now + model.return_delay(task.gateway, node.name)
        if node.name == "cloud":
            back += self.rng.uniform(0, model.LINKS["wan"]["jitter_ms"] / 1000)
        response = back - task.created
        self.records.append({
            "created": task.created, "kind": task.kind, "node": node.name,
            "response": response, "met": response <= task.deadline,
            "energy": (model.compute_energy(task.mi, node.name)
                       + model.network_energy(task.bits, task.gateway, node.name)),
            "wan_bits": task.bits if node.name == "cloud" else 0.0,
        })

    def summary(self):
        recs = self.records
        window = self.duration - self.warmup
        out = {"policy": self.policy.name, "patients": self.n_patients, "tasks": len(recs)}
        resp = [r["response"] for r in recs]
        out["avg_ms"] = statistics.fmean(resp) * 1000
        out["met_pct"] = 100 * sum(r["met"] for r in recs) / len(recs)
        for kind in model.TASK_TYPES:
            rk = sorted(r["response"] for r in recs if r["kind"] == kind)
            out[f"{kind}_avg_ms"] = statistics.fmean(rk) * 1000
            out[f"{kind}_p95_ms"] = rk[int(0.95 * (len(rk) - 1))] * 1000
            out[f"{kind}_met_pct"] = 100 * sum(
                r["met"] for r in recs if r["kind"] == kind) / len(rk)
        out["energy_j"] = sum(r["energy"] for r in recs)
        out["energy_mj_per_task"] = 1000 * out["energy_j"] / len(recs)
        out["wan_mbps"] = sum(r["wan_bits"] for r in recs) / window / 1e6
        out["wan_util_pct"] = 100 * out["wan_mbps"] / model.LINKS["wan"]["mbps"]
        out["cloud_pct"] = 100 * sum(r["node"] == "cloud" for r in recs) / len(recs)
        utils = []
        for name in model.FOG_NODES:
            n = self.nodes[name]
            u = 100 * n.busy_s / (n.cores * window)
            out[f"{name}_util_pct"] = u
            utils.append(u)
        out["fog_util_avg_pct"] = statistics.fmean(utils)
        out["fog_util_std_pct"] = statistics.pstdev(utils)
        return out


def simulate(policy_key, n_patients, seed=1, policy_opts=None, **kw):
    policy = make_policy(policy_key, **(policy_opts or {}))
    return Simulator(policy, n_patients, seed=seed, **kw).run()
