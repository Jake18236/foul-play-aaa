"""Run `python -m fp.neural --help` for the offline training stages."""

import argparse
import json
from pathlib import Path

from .teams import build_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    teams = commands.add_parser("teams")
    teams.add_argument(
        "--source",
        default="fp/data/pkmn_sets_cache/gen9almostanyability/showdown_sets.json",
    )
    teams.add_argument(
        "--showdown", default=".neural-tools/node_modules/pokemon-showdown"
    )
    teams.add_argument("--output", required=True)
    teams.add_argument("--count", type=int, default=12)
    teams.add_argument("--seed", type=int, default=0)
    teams.add_argument("--heldout-abilities", nargs="*", default=[])
    p = commands.add_parser("benchmark")
    p.add_argument("--checkpoint", required=True)
    p.add_argument("--data", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--device", default="auto", choices=["auto", "cpu", "mps"])
    p.add_argument("--repeats", type=int, default=20)
    for name in ("generate", "evaluate"):
        p = commands.add_parser(name)
        p.add_argument("--manifest", required=True)
        p.add_argument(
            "--source",
            default="fp/data/pkmn_sets_cache/gen9almostanyability/showdown_sets.json",
        )
        p.add_argument("--output", required=True)
        p.add_argument(
            "--split",
            choices=["train", "validation", "test"],
            default="test" if name == "evaluate" else "train",
        )
        p.add_argument("--games", type=int, default=8)
        p.add_argument("--seed", type=int, default=0)
        p.add_argument("--first", default="random")
        p.add_argument("--opponents", nargs="+", default=["random"])
        p.add_argument("--workers", type=int, default=1)
        p.add_argument("--device", choices=["auto", "mps", "cpu"], default="auto")
        p.add_argument("--batch-size", type=int, default=256)
        p.add_argument("--simulations", type=int, default=128)
        p.add_argument("--budget-ms", type=int, default=50)
        p.add_argument("--worlds", type=int, default=2)
        p.add_argument("--max-steps", type=int, default=500)
    p = commands.add_parser("train")
    p.add_argument("--train", required=True)
    p.add_argument("--validation", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--resume")
    p.add_argument("--epochs", type=int, default=5)
    p.add_argument("--batch-size", type=int, default=128)
    p.add_argument("--lr", type=float, default=0.001)
    p.add_argument("--device", default="auto", choices=["auto", "cpu", "mps"])
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--hidden", type=int, default=128)
    p.add_argument("--embedding", type=int, default=12)
    p.add_argument("--policy-weight", type=float, default=0.0)
    p = commands.add_parser("selfplay")
    p.add_argument("--manifest", required=True)
    p.add_argument(
        "--source",
        default="fp/data/pkmn_sets_cache/gen9almostanyability/showdown_sets.json",
    )
    p.add_argument("--validation", required=True)
    p.add_argument("--checkpoint", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--iterations", type=int, default=2)
    p.add_argument("--games", type=int, default=12)
    p.add_argument("--epochs", type=int, default=2)
    p.add_argument("--seed", type=int, default=100)
    p.add_argument("--device", default="auto")
    p.add_argument("--batch-size", type=int, default=128)
    p.add_argument("--worlds", type=int, default=1)
    p.add_argument("--simulations", type=int, default=128)
    p.add_argument("--max-steps", type=int, default=500)
    args = vars(parser.parse_args())
    command = args.pop("command")
    if command == "teams":
        report = build_manifest(**args)
        report = {
            "id": report["id"],
            "teams": {k: len(v) for k, v in report["splits"].items()},
            "validator_sha256": report["validator_sha256"],
        }
    elif command in ("generate", "evaluate"):
        from .runner import generate

        args["manifest_path"] = args.pop("manifest")
        if command == "evaluate" and args["split"] != "test":
            parser.error("Head-to-head evaluation requires the test split")
        report = generate(**args, paired=command == "evaluate")
        report.pop("results", None)
    elif command == "benchmark":
        from .benchmark import benchmark

        report = benchmark(**args)
    elif command == "train":
        from .training import train

        args["train_path"] = args.pop("train")
        args["validation_path"] = args.pop("validation")
        report = train(**args)
    else:
        from .runner import generate
        from .training import train

        root = Path(args["output_dir"])
        root.mkdir(parents=True, exist_ok=True)
        previous = args["checkpoint"]
        pool = ["random", "original", "foulplay-search", "neural:" + previous]
        report = []
        for iteration in range(args["iterations"]):
            trajectory = root / f"iteration-{iteration:03}.jsonl"
            checkpoint = root / f"iteration-{iteration:03}.pt"
            generated = generate(
                args["manifest"],
                args["source"],
                trajectory,
                games=args["games"],
                seed=args["seed"] + iteration,
                first="search:" + previous,
                opponents=[*pool, "search:" + previous],
                device=args["device"],
                batch_size=args["batch_size"],
                worlds=args["worlds"],
                simulations=args["simulations"],
                max_steps=args["max_steps"],
            )
            trained = train(
                trajectory,
                args["validation"],
                checkpoint,
                epochs=args["epochs"],
                resume=previous,
                policy_weight=1.0,
                device=args["device"],
                batch_size=args["batch_size"],
                seed=args["seed"] + iteration,
            )
            report.append(
                {
                    "iteration": iteration,
                    "completed": generated["completed"],
                    "training": trained,
                }
            )
            previous = str(checkpoint)
            pool.append("neural:" + previous)
        (root / "iterations.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
