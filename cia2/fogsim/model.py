"""Hospital fog setup used by both the simulator and the live testbed.

Every number used in the experiments is kept here so it is easy to change
and easy to quote in the report.
"""

# Three kinds of work coming from each patient's wearable.
#   mi       - processing needed, million instructions (low, high)
#   kb       - data sent with the task, kilobytes (low, high)
#   deadline - seconds from the moment the reading is taken
#   rate     - tasks per second per patient
TASK_TYPES = {
    "critical": {"priority": 1, "mi": (30, 70), "kb": (1, 2),
                 "deadline": 0.100, "rate": 1.0},          # vitals check every second
    "ecg":      {"priority": 2, "mi": (300, 700), "kb": (10, 20),
                 "deadline": 1.0, "rate": 0.1},            # 10 s ECG window analysis
    "bulk":     {"priority": 3, "mi": (4000, 12000), "kb": (200, 400),
                 "deadline": 10.0, "rate": 1 / 300},       # trend / risk model every 5 min
}

PRIORITY_OF = {kind: t["priority"] for kind, t in TASK_TYPES.items()}

# Compute nodes. Power figures are the extra (dynamic) watts a fully busy core
# draws above idle; the cloud figure already includes a data-centre PUE of 1.5.
NODES = {
    "fog-1": {"tier": "fog", "cores": 4, "mips": 1000, "core_watts": (6.4 - 2.7) / 4,
              "label": "Ward A gateway (Raspberry Pi 4 class)"},
    "fog-2": {"tier": "fog", "cores": 4, "mips": 1400, "core_watts": (10.0 - 2.0) / 4,
              "label": "ICU gateway (Jetson Nano class)"},
    "fog-3": {"tier": "fog", "cores": 4, "mips": 3000, "core_watts": (45.0 - 10.0) / 4,
              "label": "Hospital fog server (Intel NUC class)"},
    "cloud": {"tier": "cloud", "cores": 16, "mips": 4000, "core_watts": 8.0 * 1.5,
              "label": "Cloud VM pool (AWS, 16 vCPU)"},
}

FOG_NODES = [n for n, s in NODES.items() if s["tier"] == "fog"]

# Which gateway each patient's wearable connects to (share of all patients).
ATTACH_SHARE = {"fog-1": 0.25, "fog-2": 0.25, "fog-3": 0.50}

# Network links: bandwidth (Mbps), one-way propagation (ms), random jitter (ms),
# and energy per bit sent (micro-joules).
LINKS = {
    "wifi":  {"mbps": 50, "prop_ms": 2.0, "jitter_ms": 0.0, "uj_per_bit": 0.10},
    "lan":   {"mbps": 100, "prop_ms": 1.0, "jitter_ms": 0.0, "uj_per_bit": 0.05},
    "wan":   {"mbps": 10, "prop_ms": 35.0, "jitter_ms": 10.0, "uj_per_bit": 0.50},
}

RESULT_BITS = 200 * 8   # the reply (alert / result) is tiny


def exec_time(mi, node):
    return mi / NODES[node]["mips"]


def compute_energy(mi, node):
    """Joules spent by one core of `node` running a task of `mi` MI."""
    return exec_time(mi, node) * NODES[node]["core_watts"]


def network_energy(bits, gateway, node):
    """Joules for moving the task data from the wearable to where it runs."""
    j = bits * LINKS["wifi"]["uj_per_bit"] * 1e-6
    if node == "cloud":
        j += bits * LINKS["wan"]["uj_per_bit"] * 1e-6
    elif node != gateway:
        j += bits * LINKS["lan"]["uj_per_bit"] * 1e-6
    return j


def return_delay(gateway, node):
    """Fixed time for the small result to travel back to the wearable."""
    d = LINKS["wifi"]["prop_ms"] / 1000
    if node == "cloud":
        d += LINKS["wan"]["prop_ms"] / 1000
    elif node != gateway:
        d += LINKS["lan"]["prop_ms"] / 1000
    return d
