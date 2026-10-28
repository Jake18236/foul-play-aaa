"""Mechanics regression discovered while auditing replay 2703189072.

The replay hides EVs, so these are explicit fixtures, not exact private teams.
Showdown's Unaware onAnyModifyBoost applies on critical hits as well as normal hits.
"""

import pytest

from fp import constants
from fp.battle.state import Battle, Pokemon
from fp.format_spec import FormatSpec
from fp.search.poke_engine_helpers import poke_engine_get_damage_rolls


def matchup(attacker="fluttermane", defender="dondozo"):
    battle = Battle("battle-gen9badnboosted-2703189072")
    battle.pokemon_format = "gen9badnboosted"
    battle.generation = "gen9"
    spec = FormatSpec.from_format_string(battle.pokemon_format)
    battle.user.active = Pokemon(attacker, 100, format_spec=spec)
    battle.opponent.active = Pokemon(defender, 100, format_spec=spec)
    battle.user.active.ability = "noability"
    battle.opponent.active.ability = "unaware"
    battle.user.active.item = None
    battle.opponent.active.item = None
    return battle


@pytest.mark.parametrize(
    "species,move,stat",
    [
        ("fluttermane", "shadowball", constants.SPECIAL_ATTACK),
        ("fluttermane", "moonblast", constants.SPECIAL_ATTACK),
        ("zacian", "playrough", constants.ATTACK),
        ("zamazenta", "bodypress", constants.DEFENSE),
        ("mewtwo", "psystrike", constants.SPECIAL_ATTACK),
    ],
)
def test_unaware_ignores_offensive_boosts_on_critical_hits(species, move, stat):
    battle = matchup(attacker=species)
    baseline, _ = poke_engine_get_damage_rolls(battle, move, "liquidation", True)
    battle.user.active.boosts[stat] = 2
    boosted, _ = poke_engine_get_damage_rolls(battle, move, "liquidation", True)
    # Native calculate_damage returns [max_normal_damage, max_critical_damage].
    assert boosted == baseline


@pytest.mark.parametrize(
    "species,move,stat",
    [
        ("fluttermane", "shadowball", constants.SPECIAL_DEFENSE),
        ("zacian", "playrough", constants.DEFENSE),
        ("mewtwo", "psystrike", constants.DEFENSE),
    ],
)
def test_unaware_ignores_defensive_drops_on_critical_hits(species, move, stat):
    battle = matchup(attacker=species)
    battle.user.active.ability = "unaware"
    battle.opponent.active.ability = "noability"
    baseline, _ = poke_engine_get_damage_rolls(battle, move, "liquidation", True)
    battle.opponent.active.boosts[stat] = -2
    dropped, _ = poke_engine_get_damage_rolls(battle, move, "liquidation", True)
    assert dropped == baseline


def test_mold_breaker_still_bypasses_unaware():
    battle = matchup()
    battle.user.active.ability = "moldbreaker"
    baseline, _ = poke_engine_get_damage_rolls(
        battle, "shadowball", "liquidation", True
    )
    battle.user.active.boosts[constants.SPECIAL_ATTACK] = 2
    boosted, _ = poke_engine_get_damage_rolls(battle, "shadowball", "liquidation", True)
    assert all(b > a for a, b in zip(baseline, boosted))


def test_stored_power_still_gains_base_power_against_unaware():
    battle = matchup(attacker="ribombee")
    baseline, _ = poke_engine_get_damage_rolls(
        battle, "storedpower", "liquidation", True
    )
    for stat in (constants.SPECIAL_ATTACK, constants.SPECIAL_DEFENSE, constants.SPEED):
        battle.user.active.boosts[stat] = 1
    boosted, _ = poke_engine_get_damage_rolls(
        battle, "storedpower", "liquidation", True
    )
    assert boosted[0] > baseline[0]


def test_moonblast_has_higher_immediate_damage_than_shadow_ball_into_dondozo():
    battle = matchup()
    shadow_ball, _ = poke_engine_get_damage_rolls(
        battle, "shadowball", "liquidation", True
    )
    moonblast, _ = poke_engine_get_damage_rolls(
        battle, "moonblast", "liquidation", True
    )
    assert moonblast[0] > shadow_ball[0]
