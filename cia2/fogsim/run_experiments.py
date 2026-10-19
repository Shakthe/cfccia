"""Run every experiment in the report and draw the graphs.

    python -m fogsim.run_experiments            # full runs (about 5 minutes)
    python -m fogsim.run_experiments --quick    # short runs, used by CI

Writes CSV files to results/ and PNG graphs to results/figures/.
"""

import argparse
import csv
import json
import os
import statistics
from multiprocessing import Pool

from fogsim.engine import simulate

POLICY_ORDER = ["laps", "fcfs", "rr", "mct", "cloud"]
PATIENTS = [50, 100, 150, 200, 250, 300]
BURST = {"start": 120.0, "end": 180.0, "share": 0.2, "critical_x": 5, "ecg_x": 10}

# energy-weight sweep for the trade-off graph: priority -> (w_latency, w_energy, w_load)
WEIGHT_SETS = {
    "0.1": {1: (0.7, 0.1, 0.2), 2: (0.5, 0.25, 0.25), 3: (0.2, 0.4, 0.4)},
    "0.3": {1: (0.5, 0.3, 0.2), 2: (0.4, 0.35, 0.25), 3: (0.2, 0.4, 0.4)},
    "0.4": {1: (0.4, 0.4, 0.2), 2: (0.35, 0.4, 0.25), 3: (0.2, 0.4, 0.4)},
    "0.5": {1: (0.3, 0.5, 0.2), 2: (0.3, 0.45, 0.25), 3: (0.15, 0.5, 0.35)},
    "0.6": {1: (0.2, 0.6, 0.2), 2: (0.2, 0.55, 0.25), 3: (0.1, 0.6, 0.3)},
}

ABLATIONS = {
    "LAPS (full)": {},
    "no energy term": {"energy": False},
    "no core reservation": {"reserve": False},
    "FIFO queues": {"priority_queue": False},
}


def _run(job):
    tag, policy, n, seed, opts, kw = job
    sim = simulate(policy, n, seed=seed, policy_opts=opts, **kw)
    s = sim.summary()
    s["tag"], s["seed"] = tag, seed
    if kw.get("burst"):
        s["records"] = [(r["created"], r["kind"], r["response"], r["met"]) for r in sim.records]
    return s


def _mean_rows(rows, keys):
    groups = {}
    for r in rows:
        groups.setdefault(keys(r), []).append(r)
    out = []
    for k, rs in groups.items():
        m = {key: (statistics.fmean(x[key] for x in rs) if isinstance(rs[0][key], (int, float))
                   and not isinstance(rs[0][key], bool) else rs[0][key])
             for key in rs[0] if key not in ("records", "seed")}
        m["runs"] = len(rs)
        out.append(m)
    return out


def _write_csv(path, rows):
    cols = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: (round(v, 3) if isinstance(v, float) else v) for c, v in r.items()})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--out", default="results")
    args = ap.parse_args()

    if args.quick:
        seeds, kw, patients = [1], {"duration": 60.0, "warmup": 10.0}, [50, 150, 300]
        burst_kw = {"duration": 200.0, "warmup": 10.0}
    else:
        seeds, kw, patients = [1, 2, 3, 4, 5], {"duration": 300.0, "warmup": 30.0}, PATIENTS
        burst_kw = {"duration": 300.0, "warmup": 30.0}
    os.makedirs(os.path.join(args.out, "figures"), exist_ok=True)

    jobs = [("sweep", p, n, s, None, kw) for p in POLICY_ORDER for n in patients for s in seeds]
    jobs += [("burst", p, 150, 1, None, {**burst_kw, "burst": BURST}) for p in POLICY_ORDER]
    jobs += [(f"w{w}", "laps", n, s, {"weights": ws}, kw)
             for w, ws in WEIGHT_SETS.items() for n in (50, 150) for s in seeds]
    jobs += [(f"abl:{name}", "laps", 300, s, opts, kw)
             for name, opts in ABLATIONS.items() for s in seeds]

    with Pool() as pool:
        rows = pool.map(_run, jobs)

    sweep = _mean_rows([r for r in rows if r["tag"] == "sweep"],
                       lambda r: (r["policy"], r["patients"]))
    _write_csv(os.path.join(args.out, "sweep.csv"), sweep)

    weights = _mean_rows([r for r in rows if r["tag"].startswith("w")],
                         lambda r: (r["tag"], r["patients"]))
    _write_csv(os.path.join(args.out, "weights.csv"), weights)

    ablation = _mean_rows([r for r in rows if r["tag"].startswith("abl:")], lambda r: r["tag"])
    for r in ablation:
        r["variant"] = r["tag"][4:]
    _write_csv(os.path.join(args.out, "ablation.csv"), ablation)

    burst = []
    for r in (r for r in rows if r["tag"] == "burst"):
        bins = {}
        for created, kind, resp, met in r["records"]:
            if kind == "critical":
                bins.setdefault(int(created // 5) * 5, []).append((resp, met))
        for t in sorted(bins):
            b = bins[t]
            burst.append({"policy": r["policy"], "t": t,
                          "critical_avg_ms": 1000 * statistics.fmean(x[0] for x in b),
                          "critical_met_pct": 100 * sum(x[1] for x in b) / len(b)})
    _write_csv(os.path.join(args.out, "burst_timeseries.csv"), burst)
    burst_totals = [{k: v for k, v in r.items() if k != "records"}
                    for r in rows if r["tag"] == "burst"]
    _write_csv(os.path.join(args.out, "burst_summary.csv"), burst_totals)

    with open(os.path.join(args.out, "config.json"), "w") as f:
        json.dump({"seeds": seeds, "run": kw, "patients": patients, "burst": BURST}, f, indent=2)

    from fogsim import plots
    plots.draw_all(args.out)
    print(f"done: {len(rows)} simulation runs, results in {args.out}/")


if __name__ == "__main__":
    main()
