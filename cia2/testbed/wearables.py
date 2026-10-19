"""Load generator: the wearables of N patients sending tasks to the gateway.

    python -m testbed.wearables --patients 80 --duration 90

Each patient sends a vitals check every second on average, an ECG window
every 10 s and a heavy trend job every 5 minutes (Poisson arrivals, same
rates as the simulator). Patients are spread over the three ward gateways.
"""

import argparse
import json
import logging
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from fogsim import model

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("vitalwatch.wearables")

_local = threading.local()


def _post(url, body):
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
    try:
        _local.s.post(url, json=body, timeout=180)
    except requests.RequestException as e:
        logger.warning("task failed: %s", e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gateway", default=os.environ.get("GATEWAY_URL", "http://gateway:8000"))
    ap.add_argument("--patients", type=int, default=int(os.environ.get("PATIENTS", 80)))
    ap.add_argument("--duration", type=float, default=float(os.environ.get("DURATION", 90)))
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    wards = []
    for g, share in model.ATTACH_SHARE.items():
        wards += [g] * round(args.patients * share)

    # one merged Poisson stream per task type
    events = []
    for kind, spec in model.TASK_TYPES.items():
        t = 0.0
        while True:
            t += rng.expovariate(args.patients * spec["rate"])
            if t >= args.duration:
                break
            patient = rng.randrange(len(wards))
            events.append((t, kind, patient))
    events.sort()
    logger.info("%d patients, %d tasks over %.0f s -> %s",
                args.patients, len(events), args.duration, args.gateway)

    url = f"{args.gateway}/task"
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=256) as pool:
        for at, kind, patient in events:
            delay = t0 + at - time.time()
            if delay > 0:
                time.sleep(delay)
            spec = model.TASK_TYPES[kind]
            size = int(rng.uniform(*spec["kb"]) * 1000)
            pool.submit(_post, url, {"kind": kind, "ward": wards[patient], "patient": patient,
                                     "mi": rng.uniform(*spec["mi"]), "created": time.time(),
                                     "payload": "v" * size})
    stats = requests.get(f"{args.gateway}/stats", timeout=10).json()
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
