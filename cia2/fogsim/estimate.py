"""Queue-wait estimate shared by the simulator and the live gateway."""


def wait_for_core(remaining, cores, work_ahead_s):
    """Seconds until a core is free for a newly arriving task.

    remaining     - seconds left on each task that is running now
    cores         - number of cores on the node
    work_ahead_s  - core-seconds of queued work that will run before it

    The queued work is spread over the cores as they free up (water filling);
    the answer is the moment the first core is free after that work is done.
    Tasks are not pre-empted, so a running task always counts, whatever its
    priority.
    """
    free = sorted(max(0.0, r) for r in remaining)[:cores]
    free = [0.0] * (cores - len(free)) + free
    total = 0.0
    for k in range(1, cores + 1):
        total += free[k - 1]
        w = (work_ahead_s + total) / k
        if k == cores or w <= free[k]:
            return w
    return 0.0
