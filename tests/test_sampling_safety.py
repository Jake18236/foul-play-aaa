from copy import deepcopy

import pytest

from fp import constants
from fp.battle.state import Pokemon
from fp.battle.state import Battle
from fp.data.sets.base import PokemonMoveset, repair_traits
from fp.modes.standard_battle import StandardBattleMode
from fp.search.helpers import populate_pkmn_from_set
from fp.search.standard_battles import (
    sample_pokemon,
    sample_pokemon_moveset_with_known_pkmn_set,
    prepare_battles,
    predict_team_likelihood,
)
from tests.test_search_standard_battles import make_pkmn_set, make_predicted_set


def test_population_never_overwrites_confirmed_fields():
    p = Pokemon("gengar", 100)
    p.ability, p.item, p.tera_type = "cursedbody", None, "fairy"
    p.stats_known = True
    p.hp = 79
    p.stats[constants.SPECIAL_ATTACK] = 411
    p.add_move("taunt")
    p.moves[0].current_pp = 3
    p.moves[0].disabled = True
    before = deepcopy(p)
    populate_pkmn_from_set(
        p,
        make_predicted_set(
            ["shadowball", "sludgewave", "focusblast", "nastyplot"],
            ability="levitate",
            item="lifeorb",
            tera_type="ghost",
        ),
    )
    for field in (
        "ability",
        "item",
        "tera_type",
        "stats",
        "max_hp",
        "hp",
        "level",
        "nature",
        "evs",
        "types",
    ):
        assert getattr(p, field) == getattr(before, field)
    assert len(p.moves) == 4
    assert p.moves[0].name == "taunt"
    assert p.moves[0].current_pp == 3 and p.moves[0].disabled


def test_population_does_not_revive_impossible_traits():
    p = Pokemon("gengar", 100)
    p.impossible_abilities.add("levitate")
    p.impossible_items.add("lifeorb")
    populate_pkmn_from_set(
        p, make_predicted_set(["shadowball"], ability="levitate", item="lifeorb")
    )
    assert p.ability is None
    assert p.item == constants.UNKNOWN_ITEM


def test_transformed_stats_and_consumed_item_are_preserved():
    p = Pokemon("ditto", 100)
    p.volatile_statuses.append("transform")
    p.stats[constants.SPECIAL_ATTACK] = 411
    p.hp = 79
    p.item = None
    p.removed_item = "leftovers"
    before = deepcopy(p)
    populate_pkmn_from_set(p, make_predicted_set(["transform"], item="leftovers"))
    assert p.stats == before.stats
    assert p.hp == before.hp and p.max_hp == before.max_hp
    assert p.item is None and p.removed_item == "leftovers"


def test_zero_nan_and_negative_usage_rates_terminate_without_guesses():
    p = Pokemon("gengar", 100)
    p.add_move("taunt")
    mode = StandardBattleMode()
    mode.smogon_sets.raw_pkmn_sets = {
        "gengar": {
            "moves": [
                ("shadowball", 0),
                ("sludgewave", float("nan")),
                ("focusblast", -1),
                ("nastyplot", float("inf")),
            ]
        }
    }
    assert sample_pokemon_moveset_with_known_pkmn_set(p, make_pkmn_set(), mode) == [
        "taunt"
    ]


def test_moveset_fill_never_exceeds_four(monkeypatch):
    p = Pokemon("gengar", 100)
    p.add_move("taunt")
    p.add_move("shadowball")
    mode = StandardBattleMode()
    monkeypatch.setattr(
        mode.team_datasets,
        "get_all_possible_move_combinations",
        lambda *args: [
            PokemonMoveset(
                ("taunt", "shadowball", "focusblast", "sludgewave", "energyball")
            )
        ],
    )
    moves = sample_pokemon_moveset_with_known_pkmn_set(p, make_pkmn_set(), mode)
    assert len(moves) == len(set(moves)) == 4
    assert moves[:2] == ["taunt", "shadowball"]


def test_contradicted_prior_is_repaired_without_mutating_dataset():
    p = Pokemon("gengar", 100)
    p.ability, p.item, p.tera_type = "cursedbody", None, "fairy"
    mode = StandardBattleMode()
    candidate = make_pkmn_set(ability="levitate", item="lifeorb", tera_type="ghost")
    mode.smogon_sets.pkmn_sets = {"gengar": [candidate]}
    repaired = mode.smogon_sets.get_all_remaining_trait_combinations(p)[0]
    assert (repaired.ability, repaired.item, repaired.tera_type) == (
        "cursedbody",
        None,
        "fairy",
    )
    assert (candidate.ability, candidate.item, candidate.tera_type) == (
        "levitate",
        "lifeorb",
        "ghost",
    )


def test_stale_full_set_fallback_keeps_revealed_phazing():
    p = Pokemon("hooh", 100)
    p.add_move("whirlwind")
    p.ability, p.item = "regenerator", None
    mode = StandardBattleMode()
    mode.team_datasets.pkmn_sets = {
        "hooh": [
            make_predicted_set(
                ["sacredfire", "bravebird", "recover", "earthquake"],
                ability="pressure",
                item="heavydutyboots",
            )
        ]
    }
    sample_pokemon(p, mode)
    assert p.ability == "regenerator" and p.item is None
    assert p.get_move("whirlwind") is not None
    assert len(p.moves) == 4


def test_speed_constraints_not_silently_relaxed():
    p = Pokemon("gengar", 100)
    p.speed_range = p.speed_range._replace(min=9999)
    assert repair_traits(p, make_pkmn_set()) is None


def test_no_silent_opponent_with_zero_moves():
    battle = Battle("empty-data")
    battle.generation = "gen9"
    battle.pokemon_format = "gen9ou"
    battle.mode = StandardBattleMode()
    battle.opponent.active = Pokemon("snorlax", 100)
    with pytest.raises(ValueError, match="No usable move evidence"):
        prepare_battles(battle, 1)


def test_unseen_or_zero_count_revelations_do_not_crash_teammate_prior():
    counts = {
        "pikachu": {"raw_count": 0, "teammates": {}},
        "gengar": {"raw_count": 20, "teammates": {}},
    }
    assert predict_team_likelihood(["pikachu", "missing"], counts) == {"gengar": 1.0}
