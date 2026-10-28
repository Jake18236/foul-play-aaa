"""Explicit constructed BnB positions, not recovered private replay sets."""

import json
from pathlib import Path

from fp import constants
from fp.battle.state import Battle, Pokemon
from fp.config import FoulPlayConfig
from fp.search.poke_engine_helpers import battle_to_poke_engine_state


def pokemon(
    battle,
    name,
    ability,
    moves,
    *,
    item=None,
    hp=1,
    nature="serious",
    evs=(252, 0, 0, 0, 252, 0),
):
    p = Pokemon(name, 100, nature=nature, evs=evs, format_spec=battle.format_spec)
    p.ability, p.item = ability, item
    p.hp = max(1, int(p.max_hp * hp))
    p.tera_type = p.types[0]
    for move in moves:
        p.add_move(move)
    return p


def build_fixtures():
    FoulPlayConfig.pokemon_format = "gen9badnboosted"
    fixtures = []

    def battle():
        b = Battle("benchmark")
        b.generation = "gen9"
        b.pokemon_format = "gen9badnboosted"
        return b

    def save(b, name, family):
        fixtures.append(
            {
                "name": name,
                "family": family,
                "state": battle_to_poke_engine_state(b).to_string(),
                "assumptions": "Constructed, full-information sets; no live/replay private information recovered",
            }
        )

    for boost, health in [(0, 0.88), (1, 0.62), (2, 0.36)]:
        b = battle()
        b.user.active = pokemon(
            b,
            "fluttermane",
            "protosynthesis",
            ["moonblast", "shadowball", "calmmind", "taunt"],
            hp=health,
            nature="timid",
            evs=(252, 0, 0, 4, 0, 252),
        )
        b.user.active.boosts[constants.SPECIAL_ATTACK] = boost
        b.user.active.boosts[constants.SPECIAL_DEFENSE] = boost
        b.user.reserve = [
            pokemon(
                b,
                "deoxys",
                "pressure",
                ["taunt", "knockoff", "stealthrock", "psychoboost"],
                item="ejectpack",
            )
        ]
        b.opponent.active = pokemon(
            b,
            "dondozo",
            "unaware",
            ["liquidation", "bodyslam", "rest", "curse"],
            nature="careful",
        )
        save(b, f"flutter-unaware-{boost}", "unaware")

    for spikes in [0, 2, 3]:
        b = battle()
        b.user.active = pokemon(
            b,
            "ribombee",
            "shielddust",
            ["moonblast", "storedpower", "quiverdance", "substitute"],
            item="leftovers",
            nature="timid",
        )
        b.user.reserve = [
            pokemon(
                b,
                "deoxys",
                "pressure",
                ["taunt", "knockoff", "stealthrock", "psychoboost"],
                hp=0.63,
                item="ejectpack",
            )
        ]
        b.opponent.active = pokemon(
            b,
            "skarmory",
            "sturdy",
            ["spikes", "whirlwind", "roost", "thief"],
            hp=0.79,
            item="leftovers",
        )
        b.opponent.reserve = [
            pokemon(
                b,
                "dondozo",
                "unaware",
                ["liquidation", "bodyslam", "rest", "curse"],
                hp=0.67,
            )
        ]
        b.user.side_conditions[constants.STEALTH_ROCK] = 1
        b.user.side_conditions[constants.SPIKES] = spikes
        save(b, f"skarmory-hazards-{spikes}", "phazing-hazards")

    for hp in [0.05, 0.26]:
        b = battle()
        b.user.active = pokemon(
            b,
            "ribombee",
            "shielddust",
            ["storedpower", "moonblast", "quiverdance", "substitute"],
            hp=hp,
        )
        for stat in (
            constants.SPECIAL_ATTACK,
            constants.SPECIAL_DEFENSE,
            constants.SPEED,
        ):
            b.user.active.boosts[stat] = 3
        b.user.active.tera_type = "psychic"
        b.opponent.active = pokemon(
            b,
            "dondozo",
            "unaware",
            ["liquidation", "bodyslam", "rest", "curse"],
            hp=0.39,
        )
        save(b, f"stored-power-{hp}", "unaware")

    for hp in [0.15, 0.6]:
        b = battle()
        b.user.active = pokemon(
            b,
            "pikachu",
            "static",
            ["thunderbolt", "surf", "nastyplot", "voltswitch"],
            item="lightball",
            nature="timid",
            evs=(0, 0, 0, 252, 4, 252),
        )
        b.opponent.active = pokemon(
            b,
            "hooh",
            "regenerator",
            ["bravebird", "sacredfire", "recover", "whirlwind"],
            hp=hp,
        )
        save(b, f"finish-or-setup-{hp}", "endgame")
    return fixtures


if __name__ == "__main__":
    path = Path(__file__).with_name("states.json")
    if path.exists():
        raise SystemExit("Fixtures already frozen; create a new version explicitly")
    path.write_text(json.dumps(build_fixtures(), indent=2) + "\n")
    print(path)
