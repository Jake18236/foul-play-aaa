"""Synchronized inference timing and held-out value/tactical diagnostics."""

import json
from pathlib import Path
import statistics
import time

from .model import load_checkpoint
from .training import load_games, rows, value_metrics


def benchmark(
    checkpoint, data, output, device="auto", repeats=20, batch_sizes=(1, 16, 64, 256)
):
    games, completed = load_games(data, "test")
    evaluator, metadata = load_checkpoint(checkpoint, device)
    if {g["id"] for g in games} & set(metadata["training_battles"]):
        raise ValueError("Checkpoint trained on evaluation battles")
    if {g["manifest"] for g in games} != {metadata["manifest"]}:
        raise ValueError("Checkpoint/evaluation manifest mismatch")
    examples = rows(completed)
    timings = []
    for size in batch_sizes:
        evaluator.batch_size = size
        observations = [examples[i % len(examples)]["observation"] for i in range(size)]
        for _ in range(3):
            evaluator.evaluate(observations)
        elapsed = []
        for _ in range(repeats):
            start = time.perf_counter()
            evaluator.evaluate(observations)  # evaluator explicitly synchronizes MPS
            elapsed.append(time.perf_counter() - start)
        elapsed.sort()
        timings.append(
            {
                "batch_size": size,
                "p50_ms": statistics.median(elapsed) * 1000,
                "p95_ms": elapsed[min(len(elapsed) - 1, int(0.95 * len(elapsed)))]
                * 1000,
                "positions_per_second": size / statistics.median(elapsed),
            }
        )
    tactical = {}
    for name, select in {
        "forced_replacement": lambda o: any(o["legal"][8:14])
        and not any(o["legal"][:8]),
        "hazards_present": lambda o: any(
            v and any(h in k for h in ("spikes", "stealth_rock", "sticky_web"))
            for k, v in o["effects"]
        ),
        "boosted_active": lambda o: any(v and "boost" in k for k, v in o["effects"]),
        "low_health": lambda o: any(
            p["active"] and 0 < p["hp"] <= 0.25 for p in o["team"]
        ),
    }.items():
        subset = [r for r in examples if select(r["observation"])]
        tactical[name] = (
            value_metrics(evaluator.model, subset, evaluator.device, 128)
            if subset
            else {"positions": 0}
        )
    report = {
        "checkpoint": str(checkpoint),
        "device": str(evaluator.device),
        "dtype": "float32",
        "inference": timings,
        "heldout_battles": len(completed),
        "value": value_metrics(evaluator.model, examples, evaluator.device, 128),
        "tactical_slices": tactical,
        "scope": "Unseen-species native-model battles; tactical slices are diagnostics, not optimal-action certificates",
    }
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(report, indent=2) + "\n")
    return report
