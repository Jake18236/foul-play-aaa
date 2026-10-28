import pytest

from advisor_server import format_from_battle_room
from fp import constants
from fp.battle.inference import (
    can_have_priority_modified,
    can_have_speed_modified,
    check_heavydutyboots,
)
from fp.battle.protocol import move, remove_item, switch_or_drag
from fp.battle.state import Battle, Pokemon
from fp.config import FoulPlayConfig
from fp.data.sets.smogon import SmogonSets
from fp.format_spec import FormatSpec
from fp.modes.standard_battle import StandardBattleMode
from fp.search.poke_engine_helpers import battle_to_poke_engine_state
from fp.search.standard_battles import sample_pokemon


FORMAT = "gen9almostanyability"


@pytest.fixture
def battle():
    battle = Battle("battle-gen9almostanyability-123")
    battle.pokemon_format = FORMAT
    battle.generation = "gen9"
    battle.mode = StandardBattleMode()
    battle.user.name = "p1"
    battle.opponent.name = "p2"
    battle.user.active = Pokemon("pikachu", 100, format_spec=battle.format_spec)
    battle.user.active.add_move("tackle")
    battle.opponent.active = Pokemon("gengar", 100, format_spec=battle.format_spec)
    return battle


@pytest.mark.parametrize("suffix", ["", "blitz"])
def test_aaa_uses_its_own_data_sources(monkeypatch, suffix):
    spec = FormatSpec.from_format_string(FORMAT + suffix)
    assert spec.almost_any_ability
    assert spec.generation == "gen9"
    assert spec.battle_type == constants.BattleType.STANDARD_BATTLE
    assert spec.sets_format == FORMAT
    assert spec.smogon_stats_format == FORMAT
    assert (
        format_from_battle_room(f"battle-{FORMAT + suffix}-123-abc") == FORMAT + suffix
    )
    monkeypatch.setattr(FoulPlayConfig, "smogon_stats", None)
    mode = StandardBattleMode()
    initialized = []
    for dataset in (mode.smogon_sets, mode.team_datasets):
        monkeypatch.setattr(
            dataset,
            "initialize",
            lambda spec, names: initialized.append(spec.full_name),
        )
    mode.initialize_team_preview_datasets(FORMAT + suffix, {"gengar"}, "")
    assert initialized == [FORMAT, FORMAT]


def test_unknown_and_revealed_custom_abilities(battle):
    pkmn = battle.opponent.active
    assert pkmn.can_have_ability("magicguard")
    pkmn.impossible_abilities.add("magicguard")
    assert not pkmn.can_have_ability("magicguard")
    pkmn.ability = "sheerforce"
    assert pkmn.can_have_ability("sheerforce")
    assert not pkmn.can_have_ability("cursedbody")
    assert not Pokemon("gengar", 100).can_have_ability("magicguard")


def test_custom_abilities_prevent_false_speed_inferences(battle):
    battle.weather = constants.Weather.RAIN
    pkmn = battle.opponent.active
    assert can_have_speed_modified(battle, pkmn)
    assert can_have_priority_modified(battle, pkmn, "willowisp")
    pkmn.ability = "sheerforce"
    assert not can_have_speed_modified(battle, pkmn)
    assert not can_have_priority_modified(battle, pkmn, "willowisp")


def test_magic_guard_does_not_imply_boots(battle):
    battle.opponent.side_conditions[constants.STEALTH_ROCK] = 1
    check_heavydutyboots(battle, [])
    assert battle.opponent.active.item == constants.UNKNOWN_ITEM
    battle.opponent.active.ability = "sheerforce"
    check_heavydutyboots(battle, [])
    assert battle.opponent.active.item == "heavydutyboots"


def test_custom_recoil_abilities_preserve_life_orb(battle):
    move(battle, ["", "move", "p2a: Gengar", "Shadow Ball"])
    assert "lifeorb" not in battle.opponent.active.impossible_items


def test_slaking_without_truant_can_keep_attacking(battle):
    battle.opponent.active = Pokemon("slaking", 100, format_spec=battle.format_spec)
    battle.opponent.active.ability = "swordofruin"
    move(battle, ["", "move", "p2a: Slaking", "Earthquake"])
    assert "truant" not in battle.opponent.active.volatile_statuses


@pytest.mark.parametrize(
    "ability,activated", [(None, False), ("unburden", True), ("sheerforce", False)]
)
def test_unburden_only_activates_when_known(battle, ability, activated):
    pkmn = battle.opponent.active
    pkmn.ability = ability
    pkmn.item = "sitrusberry"
    remove_item(battle, ["", "-enditem", "p2a: Gengar", "Sitrus Berry"])
    assert ("unburden" in pkmn.volatile_statuses) == activated


@pytest.mark.parametrize("hp,expected", [(50, "regenerator"), (90, None)])
def test_regenerator_inference_requires_healing(battle, hp, expected):
    gengar = battle.opponent.active
    gengar.hp = gengar.max_hp * hp / 100
    battle.opponent.reserve = [gengar]
    battle.opponent.active = Pokemon("pikachu", 100, format_spec=battle.format_spec)
    switch_or_drag(battle, ["", "switch", "p2a: Gengar", "Gengar, L100", "83/100"])
    assert battle.opponent.active.ability == expected


@pytest.mark.parametrize("tera_stats", [None, {}, {"nothing": 100}])
def test_aaa_usage_without_tera_samples_custom_ability(monkeypatch, battle, tera_stats):
    # Representative Smogon chaos data for a Sheer Force Gengar.
    info = {
        "Raw count": 100,
        "Teammates": {},
        "Checks and Counters": {},
        "Spreads": {"Timid:0/0/0/252/4/252": 100},
        "Items": {"lifeorb": 100},
        "Moves": {
            "shadowball": 100,
            "focusblast": 100,
            "sludgewave": 100,
            "nastyplot": 100,
        },
        "Abilities": {"sheerforce": 100},
    }
    if tera_stats is not None:
        info["Tera Types"] = tera_stats
    sets = battle.mode.smogon_sets
    monkeypatch.setattr(sets, "_get_smogon_stats_json", lambda url: {"Gengar": info})
    sets.initialize(battle.format_spec, {"gengar"})
    assert sets.pkmn_sets["gengar"][0].tera_type == "typeless"
    sample_pokemon(battle.opponent.active, battle.mode)
    assert battle.opponent.active.ability == "sheerforce"
    assert battle.opponent.active.item == "lifeorb"
    assert len(battle.opponent.active.moves) == 4
    engine_state = battle_to_poke_engine_state(battle)
    assert str(engine_state.side_two.pokemon[0].ability).lower() == "sheerforce"


def test_aaa_does_not_fall_back_to_ou_stats(monkeypatch, tmp_path):
    from fp.data.sets import smogon

    monkeypatch.setattr(smogon, "SMOGON_CACHE_DIR", str(tmp_path))
    requested = []

    class Missing:
        status_code = 404

    def get(url):
        requested.append(url)
        return Missing()

    monkeypatch.setattr(smogon.requests, "get", get)
    sets = SmogonSets()
    with pytest.raises(FileNotFoundError, match=FORMAT):
        sets._get_smogon_stats_json(sets._get_smogon_stats_file_name(FORMAT))
    assert len(requested) == smogon.SMOGON_STATS_HISTORY_MONTHS + 1
    assert all(FORMAT in url for url in requested)
