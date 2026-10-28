"""Run paired selectors on identical states, seeded trees and budgets.

Fixed iterations reproduce decision records exactly. Timed mode measures budget
compliance; its iteration counts intentionally depend on machine speed.
"""

import argparse
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform
import random
import statistics
import time

from poke_engine import State, generate_instructions, root_info

from fp import constants
from fp.data import all_move_json
from fp.search.payoff import diagnostic_search, matrix_policy


def fingerprint():
    root = Path(__file__).resolve().parents[1]
    sources = {}
    for folder in ("fp", "scripts", "benchmarks"):
        for path in sorted((root / folder).rglob("*")):
            if path.is_file() and path.suffix in {".py", ".rs"}:
                sources[str(path.relative_to(root))] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
    import poke_engine.poke_engine as native

    return {
        "engine": version("poke-engine"),
        "native_sha256": hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "sources": sources,
        "baseline_manifest_sha256": hashlib.sha256(
            (root / "benchmarks/baseline/manifest.json").read_bytes()
        ).hexdigest(),
        "fixtures_sha256": hashlib.sha256(
            (root / "benchmarks/states.json").read_bytes()
        ).hexdigest(),
    }


def policies(state, seed, iterations, budget_ms):
    start = time.perf_counter()
    cpu_start = time.process_time()
    # Native duration excludes destruction of the search tree and Python result
    # conversion. Reserve 20% for cleanup and selection; measure overruns anyway.
    # Both selectors receive this identical tree and the same allocated cap.
    result = diagnostic_search(state, max(1, int(budget_ms * 0.8)), iterations, seed)
    shared_ms = (time.perf_counter() - start) * 1000
    shared_cpu_ms = (time.process_time() - cpu_start) * 1000
    rows = [option.move_choice for option in result.side_one]
    legacy = max(range(len(rows)), key=lambda i: result.side_one[i].visits)
    t = time.perf_counter()
    _, solution = matrix_policy([(result, 1.0, 0)])
    solve_ms = (time.perf_counter() - t) * 1000
    columns = len(result.side_two)
    return {
        "actions": rows,
        "opponent_actions": [o.move_choice for o in result.side_two],
        "visits_policy": [float(i == legacy) for i in range(len(rows))],
        "matrix_policy": solution.policy,
        "matrix": result.payoff_matrix,
        "immediate_matrix": result.immediate_payoffs,
        "visits_action": rows[legacy],
        "matrix_mode": rows[max(range(len(rows)), key=lambda i: solution.policy[i])],
        "iterations": result.total_visits,
        "joint_visits": result.root_joint_visits,
        "prior_only_fraction": sum(
            v == 0 for row in result.root_joint_visits for v in row
        )
        / (len(rows) * columns),
        "estimated_matrix_gap": solution.gap,
        "estimated_matrix_value": solution.value,
        "estimated_visits_worst_response": min(result.payoff_matrix[legacy]),
        "shared_search_ms": shared_ms,
        "shared_cpu_ms": shared_cpu_ms,
        "matrix_solver_ms": solve_ms,
        "total_matrix_ms": shared_ms + solve_ms,
    }


def swap(state):
    return State(
        side_one=state.side_two,
        side_two=state.side_one,
        weather=state.weather,
        weather_turns_remaining=state.weather_turns_remaining,
        terrain=state.terrain,
        terrain_turns_remaining=state.terrain_turns_remaining,
        trick_room=state.trick_room,
        trick_room_turns_remaining=state.trick_room_turns_remaining,
        team_preview=state.team_preview,
    )


def play(state_text, seed, first, second, iterations, budget_ms, max_turns):
    """Controlled full-information native-engine arena, not a Showdown oracle."""
    state = State.from_string(state_text)
    chance = random.Random(seed)
    actions_rng = [random.Random(seed + 17), random.Random(seed + 31)]
    history = []
    for turn in range(max_turns):
        terminal = root_info(state)[3]
        if terminal:
            return {
                "outcome": float(terminal > 0),
                "turns": turn,
                "termination": "terminal",
                "history": history,
            }
        choices = []
        for side, selector in enumerate((first, second)):
            view = state if side == 0 else swap(state)
            record = policies(
                view, seed * 10000 + 2 * turn + side, iterations, budget_ms
            )
            if selector == "attack":
                attacks = [
                    i
                    for i, action in enumerate(record["actions"])
                    if (move := action.removesuffix("-tera")) in all_move_json
                    and all_move_json[move][constants.CATEGORY]
                    in constants.DAMAGING_CATEGORIES
                    and move not in constants.SWITCH_OUT_MOVES
                ]
                index = (
                    max(attacks, key=lambda i: min(record["immediate_matrix"][i]))
                    if attacks
                    else max(
                        range(len(record["actions"])),
                        key=lambda i: min(record["immediate_matrix"][i]),
                    )
                )
                choice = record["actions"][index]
            else:
                choice = actions_rng[side].choices(
                    record["actions"], weights=record[f"{selector}_policy"]
                )[0]
            choices.append(choice)
        branches = generate_instructions(
            state,
            *(
                "none" if choice == "No Move" else choice.removeprefix("switch ")
                for choice in choices
            ),
        )
        branch = chance.choices(branches, weights=[b.percentage for b in branches])[0]
        state = state.apply_instructions(branch)
        history.append(choices)
    # Censored games are not adjudicated by the candidate's own heuristic.
    return {
        "outcome": 0.5,
        "turns": max_turns,
        "termination": "turn_cap",
        "history": history,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=10000)
    parser.add_argument("--budget-ms", type=int, default=100)
    parser.add_argument("--seeds", type=int, default=4)
    parser.add_argument("--games", action="store_true")
    parser.add_argument(
        "--opponent", choices=["head-to-head", "attack"], default="head-to-head"
    )
    parser.add_argument("--max-turns", type=int, default=80)
    parser.add_argument(
        "--output", type=Path, default=Path("benchmarks/results/comparison.json")
    )
    args = parser.parse_args()
    if args.iterations < 0 or args.budget_ms < 20 or args.seeds < 1:
        parser.error("Use iterations >=0, budget-ms >=20, seeds >=1")
    cases = json.loads(Path(__file__).with_name("states.json").read_text())
    manifest = fingerprint()
    records, games = [], []
    for case in cases:
        for seed in range(args.seeds):
            record = policies(
                State.from_string(case["state"]), seed, args.iterations, args.budget_ms
            )
            records.append({"case": case["name"], "seed": seed, **record})
            if args.games:
                pairings = (
                    [("matrix", "visits"), ("visits", "matrix")]
                    if args.opponent == "head-to-head"
                    else [
                        ("matrix", "attack"),
                        ("attack", "matrix"),
                        ("visits", "attack"),
                        ("attack", "visits"),
                    ]
                )
                for first, second in pairings:
                    game = play(
                        case["state"],
                        seed,
                        first,
                        second,
                        args.iterations,
                        args.budget_ms,
                        args.max_turns,
                    )
                    games.append(
                        {
                            "case": case["name"],
                            "seed": seed,
                            "first": first,
                            "second": second,
                            **game,
                        }
                    )
        print(f"Completed {case['name']}", flush=True)
    matrix_scores = [
        g["outcome"] if g["first"] == "matrix" else 1 - g["outcome"]
        for g in games
        if "matrix" in (g["first"], g["second"])
    ]
    visits_scores = [
        g["outcome"] if g["first"] == "visits" else 1 - g["outcome"]
        for g in games
        if "visits" in (g["first"], g["second"])
    ]
    totals = sorted(r["total_matrix_ms"] for r in records)
    decision_payload = {
        "positions": [
            {k: v for k, v in r.items() if not k.endswith("_ms")} for r in records
        ],
        "games": games,
    }
    report = {
        "manifest": manifest,
        "config": vars(args) | {"output": str(args.output)},
        "scope": "Constructed full-information native-engine development suite. Not live BnB strength or held-out Showdown validation.",
        "summary": {
            "positions": len(records),
            "games": len(games),
            "matrix_game_score": statistics.mean(matrix_scores) if games else None,
            "visits_game_score": statistics.mean(visits_scores) if games else None,
            "terminal_games": sum(g["termination"] == "terminal" for g in games),
            "turn_cap_games": sum(g["termination"] == "turn_cap" for g in games),
            "selection_disagreements": sum(
                r["visits_action"] != r["matrix_mode"] for r in records
            ),
            "matrix_p95_ms": totals[min(len(totals) - 1, int(0.95 * len(totals)))],
            "budget_exceedances": sum(
                r["total_matrix_ms"] > args.budget_ms for r in records
            )
            if args.iterations == 0
            else None,
        },
        "decision_sha256": hashlib.sha256(
            json.dumps(decision_payload, sort_keys=True).encode()
        ).hexdigest(),
        "positions": records,
        "games": games,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
