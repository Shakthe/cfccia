"""Scheduling policies: the proposed LAPS scheduler and four common baselines.

A policy gets one task and a list of Options (one per node it could run on)
and returns the name of the chosen node. The simulator and the live testbed
both build Options from their own view of the system, so the same policy
code runs in both places.
"""

from dataclasses import dataclass, field


@dataclass
class Task:
    id: int
    kind: str
    priority: int
    mi: float
    bits: float
    deadline: float          # relative, seconds
    created: float
    gateway: str
    patient: int = 0


@dataclass
class Option:
    node: str
    tier: str
    local: bool
    est_response: float      # estimated seconds from reading taken to result back
    energy: float            # estimated joules (compute + network)
    util: float              # share of cores busy right now, 0..1
    cores: int = 1
    inflight: dict = field(default_factory=dict)   # priority -> tasks queued/running/on the way


class FCFS:
    """Every task runs on the wearable's own gateway, first come first served."""
    name = "FCFS"
    priority_queue = False

    def choose(self, task, options):
        return next(o.node for o in options if o.local)


class RoundRobin:
    """Tasks are handed to fog-1, fog-2, fog-3 and the cloud in turn."""
    name = "Round Robin"
    priority_queue = False

    def __init__(self):
        self._turn = 0

    def choose(self, task, options):
        node = options[self._turn % len(options)].node
        self._turn += 1
        return node


class MinCompletion:
    """Greedy minimum completion time (online form of Min-Min), FIFO queues."""
    name = "Min-Min (MCT)"
    priority_queue = False

    def choose(self, task, options):
        return min(options, key=lambda o: o.est_response).node


class CloudOnly:
    """Everything is sent to the cloud."""
    name = "Cloud only"
    priority_queue = False

    def choose(self, task, options):
        return "cloud"


class LAPS:
    """Latency-Aware Priority Scheduler with load balancing (proposed).

    1. Drop every node whose estimated response time misses the deadline.
    2. Core reservation: ECG-or-lower work may hold at most 75% of a fog
       node's cores and bulk work at most 50%, so urgent checks always find
       a free core close to the patient.
    3. Among the nodes left, pick the lowest weighted cost of latency,
       energy and current CPU load. The weights depend on task priority.
    4. If nothing is left, critical tasks go to the fastest node anyway;
       other tasks are pushed to the cloud.
    Fog queues are ordered by priority, then earliest deadline.
    """

    name = "LAPS (proposed)"
    # priority -> (w_latency, w_energy, w_load)
    WEIGHTS = {1: (0.5, 0.3, 0.2), 2: (0.4, 0.35, 0.25), 3: (0.2, 0.4, 0.4)}
    # priority -> share of fog cores that tasks of this priority or lower may hold
    RESERVE = {2: 0.75, 3: 0.50}
    SAFETY = 0.9   # aim to finish within 90% of the deadline

    def __init__(self, energy=True, reserve=True, priority_queue=True, weights=None, label=None):
        if weights:
            self.WEIGHTS = {**self.WEIGHTS, **weights}
        self.use_energy = energy
        self.use_reserve = reserve
        self.priority_queue = priority_queue
        if label:
            self.name = label

    def _has_room(self, task, o):
        if not self.use_reserve or o.tier != "fog" or task.priority not in self.RESERVE:
            return True
        held = sum(n for p, n in o.inflight.items() if p >= task.priority)
        return held < self.RESERVE[task.priority] * o.cores

    def choose(self, task, options):
        wl, we, wu = self.WEIGHTS[task.priority]
        if not self.use_energy:
            wl, we, wu = wl + we / 2, 0.0, wu + we / 2

        ok = [o for o in options
              if o.est_response <= task.deadline * self.SAFETY and self._has_room(task, o)]
        if not ok:
            if task.priority == 1:
                return min(options, key=lambda o: o.est_response).node
            return "cloud"

        e_max = max(o.energy for o in ok) or 1.0

        def cost(o):
            return (wl * o.est_response / task.deadline
                    + we * o.energy / e_max
                    + wu * o.util)

        return min(ok, key=cost).node


POLICIES = {
    "fcfs": FCFS,
    "rr": RoundRobin,
    "mct": MinCompletion,
    "cloud": CloudOnly,
    "laps": LAPS,
}


def make_policy(key, **opts):
    return POLICIES[key](**opts)
