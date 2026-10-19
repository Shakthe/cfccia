"""Fog / cloud worker node for the live testbed.

A task queue sits in front of CORES worker processes. Every task burns real
CPU (rounds of SHA-256) in proportion to its size in MI, so a container that
docker-compose gives a smaller CPU share really is a slower node.
"""

import hashlib
import heapq
import itertools
import logging
import multiprocessing
import os
import threading
import time
from concurrent.futures import ProcessPoolExecutor

from flask import Flask, jsonify, request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("vitalwatch.node")

NAME = os.environ.get("NODE_NAME", "fog-1")
CORES = int(os.environ.get("CORES", 2))
HASHES_PER_MI = int(os.environ.get("HASHES_PER_MI", 1400))

app = Flask(__name__)


def burn(mi):
    h = b"vitalwatch"
    for _ in range(int(mi * HASHES_PER_MI)):
        h = hashlib.sha256(h).digest()
    return len(h)


class WorkQueue:
    """Priority (or FIFO) queue feeding a fixed number of CPU workers."""

    def __init__(self, cores):
        self.cores = cores
        self.pool = ProcessPoolExecutor(max_workers=cores,
                                        mp_context=multiprocessing.get_context("spawn"))
        self.heap = []
        self.cv = threading.Condition()
        self.seq = itertools.count()
        self.busy = 0
        self.done = 0
        self.busy_s = 0.0
        self.mips = self._benchmark()
        for _ in range(cores):
            threading.Thread(target=self._worker, daemon=True).start()
        logger.info("%s ready: %d cores, %.0f MIPS per core", NAME, cores, self.mips)

    def _benchmark(self, mi=300):
        """Run one job on every core at once and time it: MIPS per core."""
        list(self.pool.map(burn, [5] * self.cores))   # warm up the workers
        t = time.time()
        list(self.pool.map(burn, [mi] * self.cores))
        return mi / (time.time() - t)

    def submit(self, task):
        done, box = threading.Event(), {}
        if task.get("priority_queue"):
            key = (task["priority"], task["deadline_at"], next(self.seq))
        else:
            key = (0, 0.0, next(self.seq))
        with self.cv:
            heapq.heappush(self.heap, (key, task, done, box, time.time()))
            self.cv.notify()
        done.wait()
        return box

    def _worker(self):
        while True:
            with self.cv:
                while not self.heap:
                    self.cv.wait()
                _, task, done, box, queued_at = heapq.heappop(self.heap)
                self.busy += 1
            start = time.time()
            try:
                self.pool.submit(burn, task["mi"]).result()
            except Exception:
                logger.exception("task %s failed", task.get("id"))
                box["error"] = True
            end = time.time()
            with self.cv:
                self.busy -= 1
                self.done += 1
                self.busy_s += end - start
            box.update(node=NAME, queued_ms=(start - queued_at) * 1000,
                       exec_ms=(end - start) * 1000)
            done.set()

    def stats(self):
        with self.cv:
            return {"node": NAME, "cores": self.cores, "mips": round(self.mips),
                    "busy": self.busy, "queued": len(self.heap), "done": self.done,
                    "util": self.busy / self.cores, "busy_s": round(self.busy_s, 2)}


_queue = None
_queue_lock = threading.Lock()


def work_queue():
    # created on first use so the spawned worker processes never start their own
    global _queue
    with _queue_lock:
        if _queue is None:
            _queue = WorkQueue(CORES)
    return _queue


@app.route("/health")
def health():
    return jsonify({"status": "healthy", "node": NAME})


@app.route("/info")
def info():
    return jsonify(work_queue().stats())


@app.route("/stats")
def stats():
    return jsonify(work_queue().stats())


@app.route("/run", methods=["POST"])
def run():
    task = request.get_json(force=True)
    result = work_queue().submit(task)
    return jsonify(result), (500 if result.get("error") else 200)
