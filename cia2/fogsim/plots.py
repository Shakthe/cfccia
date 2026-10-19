"""Graphs for the report, drawn from the CSV files in results/."""

import csv
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# one fixed colour + marker per scheduler, used in every graph
STYLE = {
    "LAPS (proposed)": ("#2a78d6", "o"),
    "FCFS": ("#eb6834", "s"),
    "Round Robin": ("#1baf7a", "^"),
    "Min-Min (MCT)": ("#eda100", "D"),
    "Cloud only": ("#e87ba4", "v"),
}
INK, MUTED, GRID = "#0b0b0b", "#52514e", "#e4e3df"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": MUTED,
    "axes.labelcolor": INK, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
    "grid.color": GRID, "grid.linewidth": 0.8, "axes.axisbelow": True,
    "legend.frameon": False, "figure.dpi": 200, "savefig.bbox": "tight",
})


def _read(path):
    with open(path) as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k, v in r.items():
            try:
                r[k] = float(v)
            except ValueError:
                pass
    return rows


def _lines(rows, key, ylabel, title, out, log=False, ylim=None):
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    for policy, (color, marker) in STYLE.items():
        pts = sorted((r["patients"], r[key]) for r in rows if r["policy"] == policy)
        if not pts:
            continue
        xs, ys = zip(*pts)
        ax.plot(xs, ys, color=color, marker=marker, markersize=5, linewidth=2,
                label=policy, zorder=3 if policy.startswith("LAPS") else 2)
    ax.set_xlabel("Number of monitored patients")
    ax.set_ylabel(ylabel)
    ax.set_title(title, loc="left", fontsize=10, color=INK)
    if log:
        ax.set_yscale("log")
    if ylim:
        ax.set_ylim(*ylim)
    ax.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0), fontsize=8)
    fig.savefig(out)
    plt.close(fig)


def draw_all(results_dir):
    figs = os.path.join(results_dir, "figures")
    sweep = _read(os.path.join(results_dir, "sweep.csv"))

    _lines(sweep, "critical_met_pct", "Critical tasks within 100 ms deadline (%)",
           "Critical alert tasks that met their deadline", os.path.join(figs, "g1_critical_met.png"),
           ylim=(0, 102))
    _lines(sweep, "critical_p95_ms", "95th percentile response (ms, log scale)",
           "95th percentile response time of critical tasks", os.path.join(figs, "g2_critical_p95.png"),
           log=True)
    _lines(sweep, "avg_ms", "Average response time (ms, log scale)",
           "Average response time, all tasks", os.path.join(figs, "g3_avg_response.png"), log=True)
    _lines(sweep, "energy_mj_per_task", "Energy per task (mJ)",
           "Energy used per task (compute + network)", os.path.join(figs, "g4_energy.png"))
    _lines(sweep, "wan_util_pct", "WAN uplink utilisation (%)",
           "Share of the 10 Mbps hospital uplink in use", os.path.join(figs, "g5_wan.png"),
           ylim=(0, 102))
    _lines(sweep, "fog_util_std_pct", "Std. dev. of fog CPU utilisation (% points)",
           "Load imbalance across the three fog nodes (lower is better)",
           os.path.join(figs, "g6_imbalance.png"))

    # per-node CPU utilisation at 200 patients
    at = [r for r in sweep if r["patients"] == 200]
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    nodes = ["fog-1", "fog-2", "fog-3"]
    width = 0.15
    for i, (policy, (color, _)) in enumerate(STYLE.items()):
        r = next((x for x in at if x["policy"] == policy), None)
        if r is None:
            continue
        xs = [j + (i - 2) * (width + 0.01) for j in range(3)]
        ax.bar(xs, [r[f"{n}_util_pct"] for n in nodes], width=width, color=color, label=policy)
    ax.set_xticks(range(3))
    ax.set_xticklabels(["fog-1 (Pi 4 class)", "fog-2 (Jetson class)", "fog-3 (NUC class)"])
    ax.set_ylabel("CPU utilisation (%)")
    ax.set_ylim(0, 105)
    ax.set_title("CPU utilisation of each fog node at 200 patients", loc="left", fontsize=10)
    ax.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0), fontsize=8)
    fig.savefig(os.path.join(figs, "g7_node_util.png"))
    plt.close(fig)

    # emergency burst timeline
    burst = _read(os.path.join(results_dir, "burst_timeseries.csv"))
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    ax.axvspan(120, 180, color="#f0efec", zorder=0)
    ax.text(150, 1.02, "emergency burst", transform=ax.get_xaxis_transform(),
            ha="center", va="bottom", fontsize=8, color=MUTED)
    for policy, (color, marker) in STYLE.items():
        pts = sorted((r["t"], r["critical_avg_ms"]) for r in burst if r["policy"] == policy)
        if pts:
            xs, ys = zip(*pts)
            ax.plot(xs, ys, color=color, linewidth=2, label=policy,
                    zorder=3 if policy.startswith("LAPS") else 2)
    ax.axhline(100, color=MUTED, linewidth=1, linestyle="--")
    ax.text(32, 108, "100 ms deadline", fontsize=7.5, color=MUTED)
    ax.set_yscale("log")
    ax.set_xlabel("Simulation time (s)")
    ax.set_ylabel("Avg critical response (ms, log)")
    ax.set_title("Critical-task response during an emergency burst (150 patients)",
                 loc="left", fontsize=10, pad=14)
    ax.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0), fontsize=8)
    fig.savefig(os.path.join(figs, "g8_burst.png"))
    plt.close(fig)

    # energy weight trade-off
    w = _read(os.path.join(results_dir, "weights.csv"))
    fig, axes = plt.subplots(1, 2, figsize=(6.4, 2.8))
    for n, color in ((50, "#2a78d6"), (150, "#eb6834")):
        pts = sorted((float(r["tag"][1:]), r["energy_mj_per_task"], r["critical_p95_ms"])
                     for r in w if r["patients"] == n)
        xs, es, ps = zip(*pts)
        axes[0].plot(xs, es, color=color, marker="o", markersize=5, linewidth=2, label=f"{n} patients")
        axes[1].plot(xs, ps, color=color, marker="o", markersize=5, linewidth=2, label=f"{n} patients")
    axes[0].set_title("Energy per task (mJ)", loc="left", fontsize=9)
    axes[1].set_title("Critical p95 response (ms)", loc="left", fontsize=9)
    for ax in axes:
        ax.set_xlabel("Energy weight for critical tasks (w_E)")
        ax.axvline(0.3, color=MUTED, linewidth=1, linestyle="--")
    axes[1].legend(fontsize=8, loc="lower right")
    fig.tight_layout()
    fig.savefig(os.path.join(figs, "g9_tradeoff.png"))
    plt.close(fig)
