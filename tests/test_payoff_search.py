import json
from pathlib import Path
import random
from copy import deepcopy

import pytest
from poke_engine import State

from benchmarks.compare import play, policies
from fp.search.payoff import diagnostic_search, solve_matrix, solve_worlds
from fp.config import FoulPlayConfig
from fp.modes.standard_battle import StandardBattleMode
from fp.search import main
from tests.test_badnboosted import make_battle


def frozen_state():
    return json.loads(
        (Path(__file__).parents[1] / "benchmarks/states.json").read_text()
    )[0]["state"]


def test_mixed_strategy_differs_from_safest_pure_action():
    solution = solve_matrix([[1, 0], [0, 0.5], [0.2, 0.2]])
    assert solution.policy == pytest.approx([1 / 3, 2 / 3, 0])
    assert solution.value == pytest.approx(1 / 3)
    assert solution.gap < 1e-8


def test_rock_paper_scissors():
    solution = solve_matrix([[0, -1, 1], [1, 0, -1], [-1, 1, 0]])
    assert solution.policy == pytest.approx([1 / 3] * 3)
    assert solution.opponent_policy == pytest.approx([1 / 3] * 3)


@pytest.mark.parametrize("matrix", [[], [[]], [[1], [1, 2]], [[float("nan")]]])
def test_invalid_games_rejected(matrix):
    with pytest.raises(ValueError):
        solve_matrix(matrix)


def test_seeded_games_have_verified_best_response_bounds():
    rng = random.Random(4)
    for _ in range(30):
        matrix = [[rng.random() for _ in range(7)] for _ in range(5)]
        solution = solve_matrix(matrix)
        assert sum(solution.policy) == pytest.approx(1)
        assert min(solution.policy) >= 0
        assert solution.gap <= 1e-6


def test_own_policy_cannot_choose_differently_in_hidden_worlds():
    solution = solve_worlds([[[1], [0]], [[0], [1]]], [0.5, 0.5])
    assert solution.value == pytest.approx(0.5)  # Not the clairvoyant value 1.


def test_world_response_is_against_our_mixture_not_observed_action():
    solution = solve_worlds([[[1, 0], [0, 1]]], [1])
    assert solution.value == pytest.approx(0.5)  # Per-row minima would give zero.


def test_native_search_is_seeded_exact_and_preserves_state():
    state = State.from_string(frozen_state())
    before = state.to_string()
    a = diagnostic_search(state, 0, 2345, 7)
    b = diagnostic_search(state, 0, 2345, 7)
    assert a.__dict__ == b.__dict__
    assert a.total_visits == 2345
    assert state.to_string() == before
    assert sum(map(sum, a.root_joint_visits)) == a.total_visits
    assert [sum(row) for row in a.root_joint_visits] == [o.visits for o in a.side_one]
    assert [
        sum(row[j] for row in a.root_joint_visits) for j in range(len(a.side_two))
    ] == [o.visits for o in a.side_two]
    assert all(0 <= x <= 1 for row in a.immediate_payoffs for x in row)


def test_paired_selector_comparison_reuses_identical_search():
    a = policies(State.from_string(frozen_state()), 3, 3000, 100)
    b = policies(State.from_string(frozen_state()), 3, 3000, 100)
    for key in ("visits_policy", "matrix_policy", "matrix", "joint_visits"):
        assert a[key] == b[key]
    assert a["iterations"] == b["iterations"] == 3000


def test_arena_replays_same_choices_and_chance():
    a = play(frozen_state(), 1, "matrix", "visits", 1000, 100, 8)
    b = play(frozen_state(), 1, "matrix", "visits", 1000, 100, 8)
    assert a == b


def test_experimental_selector_runs_through_real_worker_pool(monkeypatch):
    battle = make_battle()
    battle.mode = StandardBattleMode()
    monkeypatch.setattr(FoulPlayConfig, "parallelism", 1, raising=False)
    monkeypatch.setattr(FoulPlayConfig, "search_threads", 1, raising=False)
    monkeypatch.setattr(FoulPlayConfig, "search_selector", "matrix")
    monkeypatch.setattr(StandardBattleMode, "search_params", lambda self, b: (2, 10))
    monkeypatch.setattr(
        StandardBattleMode,
        "prepare_battles",
        lambda self, b, count: [(deepcopy(b), 1 / count) for _ in range(count)],
    )
    monkeypatch.setattr(main, "recall", lambda b: {})
    choice = main.find_best_move(battle)
    assert choice in {"thunderbolt", "thunderbolt-tera"}


def test_fixture_moves_have_recorded_gen9_sources():
    root = Path(__file__).parents[1] / "benchmarks"
    contract = json.loads((root / "learnset-contract.json").read_text())["used_moves"]
    for fixture in json.loads((root / "states.json").read_text()):
        state = State.from_string(fixture["state"])
        for side in (state.side_one, state.side_two):
            for pokemon in side.pokemon:
                if pokemon.id.lower() == "none":
                    continue
                for move in pokemon.moves:
                    if move.id.lower() != "none":
                        assert any(
                            source.startswith("9")
                            for source in contract[pokemon.id.lower()][move.id.lower()][
                                "sources"
                            ]
                        )
