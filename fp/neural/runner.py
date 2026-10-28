"""Seeded trajectory generation and paired evaluation, with process workers."""

from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path
import random
import time

from .agents import RandomAgent, NeuralAgent, SearchAgent, HeuristicAgent, BeliefSampler
from .environment import play
from .teams import candidates, fingerprint, set_identity, load_manifest
from .observation import norm


def make_agent(spec, catalog, settings):
    if spec == "random":
        return RandomAgent()
    if spec == "original":
        from .original import OriginalAgent

        return OriginalAgent(settings.get("budget_ms", 50))
    sampler = BeliefSampler(catalog)
    if spec == "foulplay-search":
        return HeuristicAgent(sampler, settings["simulations"], settings["worlds"])
    if spec.startswith(("neural:", "search:")):
        from .model import load_checkpoint

        mode, path = spec.split(":", 1)
        evaluator, _ = load_checkpoint(path, settings["device"], settings["batch_size"])
        agent = (
            NeuralAgent(evaluator)
            if mode == "neural"
            else SearchAgent(evaluator, sampler, settings["worlds"])
        )
        agent.name = (
            mode + ":" + hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
        )
        return agent
    raise ValueError(f"Unknown agent {spec}")


def _game(job):
    first, second, specs, seed, split, manifest, catalog, settings = job
    agents = [make_agent(spec, catalog, settings) for spec in specs]
    start = time.perf_counter()
    game = play(first["team"], second["team"], agents, seed, settings["max_steps"])
    game.update(
        split=split,
        manifest=manifest,
        teams=[first["id"], second["id"]],
        sets=sorted({set_identity(p) for t in (first, second) for p in t["team"]}),
        agents=[a.name for a in agents],
        seconds=time.perf_counter() - start,
    )
    game["abilities"] = sorted(
        {norm(p["ability"]) for t in (first, second) for p in t["team"]}
    )
    game["id"] = fingerprint(
        {k: game[k] for k in ("seed", "teams", "agents", "manifest", "engine")}
    )
    game["performance"] = [
        {
            "search_seconds": getattr(a, "search_seconds", 0),
            "search_positions": getattr(a, "positions", getattr(a, "visits", 0)),
            "inference_seconds": getattr(getattr(a, "evaluator", None), "seconds", 0),
            "inference_calls": getattr(getattr(a, "evaluator", None), "calls", 0),
            "inference_positions": getattr(
                getattr(a, "evaluator", None), "positions", 0
            ),
        }
        for a in agents
    ]
    return game


def generate(
    manifest_path,
    source,
    output,
    *,
    split="train",
    games=8,
    seed=0,
    first="random",
    opponents=("random",),
    workers=1,
    device="auto",
    batch_size=256,
    simulations=128,
    worlds=2,
    max_steps=500,
    paired=False,
    budget_ms=50,
):
    if games < 1 or workers < 1 or simulations < 1 or worlds < 1:
        raise ValueError("Budgets must be positive")
    manifest = load_manifest(manifest_path)
    pool = manifest["splits"][split]
    catalog = candidates(source)
    if (
        hashlib.sha256(Path(source).read_bytes()).hexdigest()
        != manifest["source_sha256"]
    ):
        raise ValueError("Candidate catalog differs from validated manifest")
    if (
        workers > 1
        and device != "cpu"
        and any(s.startswith(("neural:", "search:")) for s in [first, *opponents])
    ):
        raise ValueError(
            "Use one MPS worker or explicitly choose CPU for multiple workers"
        )
    settings = dict(
        device=device,
        batch_size=batch_size,
        simulations=simulations,
        worlds=worlds,
        max_steps=max_steps,
        budget_ms=budget_ms,
    )
    rng = random.Random(seed)
    jobs = []
    for game_index in range(games):
        a, b = rng.sample(pool, 2)
        opponent = opponents[game_index % len(opponents)]
        game_seed = seed * 1000003 + game_index
        jobs.append(
            (
                a,
                b,
                (first, opponent),
                game_seed,
                split,
                manifest["id"],
                catalog,
                settings,
            )
        )
        if paired:
            jobs.append(
                (
                    b,
                    a,
                    (opponent, first),
                    game_seed,
                    split,
                    manifest["id"],
                    catalog,
                    settings,
                )
            )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite trajectories: {output}")
    results = []
    with output.open("x") as handle:
        if workers == 1:
            iterator = map(_game, jobs)
            executor = None
        else:
            import multiprocessing

            executor = ProcessPoolExecutor(
                workers, mp_context=multiprocessing.get_context("spawn")
            )
            iterator = executor.map(_game, jobs)
        try:
            for game in iterator:
                handle.write(json.dumps(game, separators=(",", ":")) + "\n")
                handle.flush()
                results.append({k: v for k, v in game.items() if k != "records"})
        finally:
            if executor:
                executor.shutdown()
    report = {
        "games": len(results),
        "completed": sum(g["termination"] == "terminal" for g in results),
        "terminations": {
            kind: sum(g["termination"] == kind for g in results)
            for kind in {g["termination"] for g in results}
        },
        "seconds": sum(g["seconds"] for g in results),
        "results": results,
    }
    if paired:
        scores = [
            g["outcome"] if i % 2 == 0 else 1 - g["outcome"]
            for i, g in enumerate(results)
            if g["outcome"] is not None
        ]
        # Report censoring separately; caps are never drawn or heuristically adjudicated.
        report["completed_win_rate"] = sum(scores) / len(scores) if scores else None
        report["all_games_win_bounds"] = [
            sum(scores) / len(results),
            (sum(scores) + len(results) - len(scores)) / len(results),
        ]
        unseen = [
            (i, g)
            for i, g in enumerate(results)
            if set(g["abilities"]) & set(manifest["heldout_abilities"])
        ]
        unseen_scores = [
            g["outcome"] if i % 2 == 0 else 1 - g["outcome"]
            for i, g in unseen
            if g["outcome"] is not None
        ]
        report["unseen_abilities"] = {
            "abilities": manifest["heldout_abilities"],
            "games": len(unseen),
            "completed": len(unseen_scores),
            "win_rate": sum(unseen_scores) / len(unseen_scores)
            if unseen_scores
            else None,
        }
        pair_scores = [
            (results[i]["outcome"] + 1 - results[i + 1]["outcome"]) / 2
            for i in range(0, len(results), 2)
            if results[i]["outcome"] is not None
            and results[i + 1]["outcome"] is not None
        ]
        if len(pair_scores) >= 2:
            boot = random.Random(seed)
            means = sorted(
                sum(boot.choices(pair_scores, k=len(pair_scores))) / len(pair_scores)
                for _ in range(2000)
            )
            report["paired_bootstrap_95"] = [means[50], means[1949]]
    output.with_suffix(".summary.json").write_text(json.dumps(report, indent=2) + "\n")
    return report
