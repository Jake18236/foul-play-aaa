import json

import pytest
from poke_engine import generate_instructions

from advisor_server import Session
from fp import opponent_memory
from fp.battle.state import Battle, Pokemon
from fp.modes.standard_battle import StandardBattleMode
from fp.search.helpers import populate_pkmn_from_set
from fp.search.main import _apply_opponent_model
from fp.search.poke_engine_helpers import battle_to_poke_engine_state
from fp.search.standard_battles import prepare_battles
from tests.test_search_standard_battles import make_predicted_set


def battle_with_phazers():
    battle = Battle("battle-gen9badnboosted-123")
    battle.pokemon_format = "gen9badnboosted"
    battle.generation = "gen9"
    battle.mode = StandardBattleMode()
    battle.user.name = "p1"
    battle.opponent.name = "p2"
    battle.opponent.account_name = "Botside Benny"
    battle.user.active = Pokemon("volcarona", 100, format_spec=battle.format_spec)
    battle.user.active.add_move("quiverdance")
    battle.user.reserve = [Pokemon("pikachu", 100, format_spec=battle.format_spec)]
    battle.opponent.active = Pokemon("hooh", 100, format_spec=battle.format_spec)
    battle.opponent.reserve = [Pokemon("skarmory", 100, format_spec=battle.format_spec)]
    return battle


def test_revealed_whirlwind_survives_incompatible_full_set_and_conversion():
    battle = battle_with_phazers()
    hooh = battle.opponent.active
    hooh.add_move("whirlwind")
    observed = hooh.get_move("whirlwind")
    observed.current_pp = 7
    observed.disabled = True
    populate_pkmn_from_set(
        hooh,
        make_predicted_set(
            ["sacredfire", "bravebird", "recover", "earthquake"], ability="regenerator"
        ),
    )

    assert hooh.get_move("whirlwind") is observed
    state = battle_to_poke_engine_state(battle)
    moves = state.side_two.pokemon[0].moves
    assert len(moves) == 4
    whirlwind = next(move for move in moves if move.id == "whirlwind")
    assert whirlwind.pp == 7
    assert whirlwind.disabled


def test_model_cannot_remove_revealed_phazing_or_overwrite_known_traits():
    battle = battle_with_phazers()
    hooh = battle.opponent.active
    hooh.add_move("whirlwind")
    hooh.ability = "regenerator"
    hooh.item = None  # Already consumed/removed in this battle.
    _apply_opponent_model(
        battle,
        [
            {
                "pokemon": "hooh",
                "moves": ["sacredfire", "bravebird", "recover", "earthquake"],
                "ability": "pressure",
                "item": "heavydutyboots",
            }
        ],
    )
    assert hooh.get_move("whirlwind") is not None
    assert len(hooh.moves) == 4
    assert hooh.ability == "regenerator"
    assert hooh.item is None


def test_two_scenarios_include_remembered_phazing_without_mutating_live_battle(
    monkeypatch,
):
    battle = battle_with_phazers()
    battle.opponent_memory = {"hooh": {"moves": ["whirlwind"]}}

    def populate(pokemon, mode):
        populate_pkmn_from_set(
            pokemon,
            make_predicted_set(
                ["sacredfire", "bravebird", "recover", "earthquake"], ability="pressure"
            ),
        )

    monkeypatch.setattr("fp.search.standard_battles.sample_pokemon", populate)
    samples = prepare_battles(battle, 2)
    assert samples[0][0].opponent.active.get_move("whirlwind") is not None
    assert samples[1][0].opponent.active.get_move("whirlwind") is None
    assert battle.opponent.active.moves == []


def test_memory_can_save_before_game_over_and_recall_by_request_name(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(opponent_memory, "MEMORY_PATH", tmp_path / "memory.json")
    session = Session("battle-gen9badnboosted-456")
    session.protocol_history = [
        "|player|p1|Alice|",
        "|player|p2|Botside Benny|",
        "|poke|p2|Ho-Oh, L100",
        "|poke|p2|Skarmory, L100",
        "|switch|p2a: Ho-Oh|Ho-Oh, L100|100/100",
        "|move|p2a: Ho-Oh|Whirlwind|p1a: Volcarona",
    ]
    # Client recovery can provide the name without a side id.
    session.memory_request = "|request|" + json.dumps({"side": {"name": "Alice"}})
    session.remember()
    assert opponent_memory.recall(battle_with_phazers())["hooh"]["moves"] == [
        "whirlwind"
    ]


@pytest.mark.parametrize("phazer", ["hooh", "skarmory"])
def test_native_engine_removes_quiver_dance_boosts_when_whirlwind_connects(phazer):
    battle = battle_with_phazers()
    battle.opponent.active = Pokemon(phazer, 100, format_spec=battle.format_spec)
    battle.opponent.active.add_move("whirlwind")
    state = battle_to_poke_engine_state(battle)
    branches = generate_instructions(state, "quiverdance", "whirlwind")
    assert branches
    for branch in branches:
        after = state.apply_instructions(branch)
        assert str(after.side_one.active_index) != "0"
        assert after.side_one.special_attack_boost == 0
        assert after.side_one.special_defense_boost == 0
        assert after.side_one.speed_boost == 0
        state.reverse_instructions(branch)
