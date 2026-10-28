from copy import deepcopy
from types import SimpleNamespace

import pytest

from fp.config import FoulPlayConfig
from fp.modes.standard_battle import StandardBattleMode
from fp.search import main
from tests.test_badnboosted import make_battle


def result(visits, score, leader="thunderbolt"):
    return SimpleNamespace(
        total_visits=visits,
        side_one=[
            SimpleNamespace(
                move_choice=leader, visits=visits, total_score=score * visits
            )
        ],
        side_two=[],
    )


@pytest.mark.parametrize("score,expected", [(0.1, -0.8), (0.5, 0), (0.9, 0.8)])
def test_search_score_is_centered_for_panel(score, expected):
    summary = main._summarize_search([(result(100, score), 1, 0)], [], "thunderbolt")
    assert summary["evaluation"] == pytest.approx(expected)
    assert summary["evaluation_kind"] == "relative_search_score"


def test_score_respects_scenario_weights_instead_of_iteration_counts():
    summary = main._summarize_search(
        [(result(100, 0.2), 0.5, 0), (result(10000, 0.8), 0.5, 1)],
        [],
        "thunderbolt",
    )
    assert summary["choices"][0]["score"] == 0.5
    assert summary["evaluation"] == 0


@pytest.mark.parametrize(
    "time_remaining,expected_durations", [(120, [30, 30, 60, 60]), (60, [30, 30])]
)
def test_bnb_deepens_same_scenarios_without_exceeding_old_budget(
    monkeypatch, time_remaining, expected_durations
):
    battle = make_battle()
    battle.mode = StandardBattleMode()
    battle.time_remaining = time_remaining
    prepared = []
    submitted = []
    monkeypatch.setattr(FoulPlayConfig, "parallelism", 1, raising=False)
    monkeypatch.setattr(FoulPlayConfig, "search_threads", 1, raising=False)
    monkeypatch.setattr(StandardBattleMode, "search_params", lambda self, b: (2, 30))

    def prepare(self, b, count):
        prepared.append(count)
        return [(deepcopy(b), 0.25), (deepcopy(b), 0.75)]

    monkeypatch.setattr(StandardBattleMode, "prepare_battles", prepare)
    monkeypatch.setattr(main, "recall", lambda b: {})
    monkeypatch.setattr(
        main,
        "battle_to_poke_engine_state",
        lambda b: SimpleNamespace(to_string=lambda: "position"),
    )

    class Executor:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def submit(self, function, state, duration, index, threads):
            submitted.append((state, duration, index))
            # Shallow searches disagree, deeper ones agree on Thunderbolt.
            leader = "thunderbolt" if index == 0 or duration == 60 else "tackle"
            return SimpleNamespace(result=lambda: result(100, 0.7, leader))

    monkeypatch.setattr(main, "ProcessPoolExecutor", Executor)
    choice, analysis = main.find_best_move(battle, include_analysis=True)
    assert [duration for _, duration, _ in submitted] == expected_durations
    assert prepared == [2]
    assert sum(expected_durations) <= 3 * 2 * 30
    if time_remaining > 60:
        assert choice == "thunderbolt"
        assert [p["weight"] for p in analysis["sample_policies"]] == [0.25, 0.75]
        assert all(
            p["choices"][0]["move"] == "thunderbolt"
            for p in analysis["sample_policies"]
        )
    assert analysis["sample_count"] == 2
