"""Samples CPU and memory of the running agent process (TAD 12, Performance).

Usage (agent already running):
  .venv\\Scripts\\python scripts\\measure_usage.py --seconds 120 --interval 2

Reports average and peak CPU percent (of one core) and resident memory in MB. Reads OS counters only.
"""

import argparse
import statistics
import time

import psutil


def find_agent(name: str) -> psutil.Process | None:
    for proc in psutil.process_iter(["name"]):
        if (proc.info["name"] or "").lower() == name.lower():
            return proc
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure agent CPU and memory")
    parser.add_argument("--process", default="winagent.exe")
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()

    proc = find_agent(args.process)
    if proc is None:
        print(f"{args.process} is not running")
        return 1

    proc.cpu_percent(None)  # prime the counter
    cpu, rss = [], []
    end = time.time() + args.seconds
    while time.time() < end:
        time.sleep(args.interval)
        try:
            cpu.append(proc.cpu_percent(None))
            rss.append(proc.memory_info().rss / (1024 * 1024))
        except psutil.NoSuchProcess:
            print("process exited during measurement")
            return 1

    # psutil reports per-core percent; divide by logical CPU count for share of the whole machine.
    cores = psutil.cpu_count(logical=True) or 1
    print(f"samples: {len(cpu)} over {args.seconds}s (pid {proc.pid})")
    print(f"CPU: avg {statistics.mean(cpu):.2f}% of one core, peak {max(cpu):.2f}% of one core")
    print(f"CPU share of machine: avg {statistics.mean(cpu) / cores:.2f}%, peak {max(cpu) / cores:.2f}% ({cores} logical CPUs)")
    print(f"Memory (RSS): avg {statistics.mean(rss):.1f} MB, peak {max(rss):.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
