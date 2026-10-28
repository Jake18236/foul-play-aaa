"""Summarize paired game scores with a case-cluster bootstrap interval."""

from collections import defaultdict
import json
from pathlib import Path
import random
import statistics
import sys


def interval(games, selector):
    clusters = defaultdict(list)
    for game in games:
        if selector not in (game["first"], game["second"]):
            continue
        clusters[game["case"]].append(
            game["outcome"] if game["first"] == selector else 1 - game["outcome"]
        )
    if not clusters:
        return None
    # Cases are constructed variants, not independent sampled ladder games.
    # This interval describes only the finite development fixture collection.
    rng = random.Random(447)
    values = list(clusters.values())
    estimates = sorted(
        statistics.mean(
            score for cluster in rng.choices(values, k=len(values)) for score in cluster
        )
        for _ in range(5000)
    )
    return [estimates[125], estimates[4874]]


def main():
    report = json.loads(Path(sys.argv[1]).read_text())
    print(
        json.dumps(
            {
                "summary": report["summary"],
                "matrix_case_cluster_95_interval": interval(report["games"], "matrix"),
                "interpretation": "Development fixtures only; not a ladder strength confidence interval.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
