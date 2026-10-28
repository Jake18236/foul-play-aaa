"""Adapter for the unchanged full Foul Play find_best_move entry point."""

import random

from fp import constants
from fp.battle.state import Battle, Pokemon, LastUsedMove
from fp.config import FoulPlayConfig
from fp.modes.standard_battle import StandardBattleMode
from fp.search.main import find_best_move
from .observation import UNKNOWN, norm, BOOSTS, CONDITIONS


def to_battle(decision):
    b = Battle("offline-neural-benchmark")
    b.pokemon_format, b.generation = "gen9almostanyability", "gen9"
    b.mode = StandardBattleMode()
    b.turn = 1
    # The adapter's snapshot already identifies the lead; preview is handled
    # explicitly by the caller, not by fabricating an opposing active reveal.
    b.team_preview = False
    effects = dict(decision.observation["effects"])
    status = {
        "none": None,
        "burn": constants.Status.BURN,
        "sleep": constants.Status.SLEEP,
        "freeze": constants.Status.FROZEN,
        "paralyze": constants.Status.PARALYZED,
        "poison": constants.Status.POISON,
        "toxic": constants.Status.TOXIC,
    }
    stat_names = (
        constants.ATTACK,
        constants.DEFENSE,
        constants.SPECIAL_ATTACK,
        constants.SPECIAL_DEFENSE,
        constants.SPEED,
    )
    for side_index, side in enumerate((b.user, b.opponent)):
        members = []
        shown = decision.observation["team"][side_index * 6 : side_index * 6 + 6]
        for index, data in enumerate(shown):
            if not data["present"]:
                continue
            p = Pokemon(data["species"], 100, format_spec=b.format_spec)
            p.revealed = bool(data["active"])
            p.hp = data["hp"] * p.max_hp if data["hp_known"] else p.max_hp
            p.status = status.get(data["status"])
            p.ability = None if data["ability"] == UNKNOWN else data["ability"]
            p.item = constants.UNKNOWN_ITEM if data["item"] == UNKNOWN else data["item"]
            p.tera_type = None if data["tera"] == UNKNOWN else data["tera"]
            for i in range(4):
                name = data[f"move{i}"]
                if name not in (UNKNOWN, "none"):
                    p.add_move(name)
            if side_index == 0:
                native = decision.own.pokemon[index]
                p.level, p.hp, p.max_hp = native.level, native.hp, native.maxhp
                p.nature, p.evs, p.types = (
                    norm(native.nature),
                    tuple(native.evs),
                    list(native.types),
                )
                p.stats_known = True
                for key, attribute in zip(
                    stat_names,
                    ("attack", "defense", "special_attack", "special_defense", "speed"),
                ):
                    p.stats[key] = getattr(native, attribute)
                for move, native_move in zip(p.moves, native.moves):
                    move.current_pp, move.disabled = (
                        native_move.pp,
                        native_move.disabled,
                    )
            members.append((p, data["active"]))
        side.active = next((p for p, active in members if active), members[0][0])
        side.reserve = [p for p, _ in members if p is not side.active]
        prefix = "own" if side_index == 0 else "opponent"
        for key, native_key in zip(stat_names, BOOSTS):
            side.active.boosts[key] = round(
                effects.get(prefix + ":" + native_key, 0) * 6
            )
        side.active.volatile_statuses = [
            key.split(":", 2)[2]
            for key in effects
            if key.startswith(prefix + ":volatile:")
        ]
        for k in CONDITIONS:
            side.side_conditions[k.replace("_", "")] = int(
                effects.get(prefix + ":" + k, 0)
            )
        for k in ("spikes", "toxic_spikes"):
            side.side_conditions[k.replace("_", "")] = round(
                effects.get(prefix + ":layers:" + k, 0) * 3
            )
    weather = next(k[8:] for k in effects if k.startswith("weather:"))
    weather_map = {
        "rain": constants.Weather.RAIN,
        "sun": constants.Weather.SUN,
        "sand": constants.Weather.SAND,
        "snow": constants.Weather.SNOW,
        "hail": constants.Weather.HAIL,
        "harshsun": constants.Weather.DESOLATE_LAND,
        "heavyrain": constants.Weather.HEAVY_RAIN,
    }
    b.weather = weather_map.get(weather)
    b.weather_turns_remaining = 5
    terrain = next(k[8:] for k in effects if k.startswith("terrain:"))
    terrain_map = {
        "electricterrain": constants.Terrain.ELECTRIC,
        "grassyterrain": constants.Terrain.GRASSY,
        "mistyterrain": constants.Terrain.MISTY,
        "psychicterrain": constants.Terrain.PSYCHIC,
    }
    b.field = terrain_map.get(terrain)
    b.field_turns_remaining = 5
    b.trick_room = bool(effects["trick_room"])
    b.trick_room_turns_remaining = 3 if b.trick_room else 0
    b.force_switch = bool(decision.own.force_switch or b.user.active.hp <= 0)
    b.user.trapped = bool(decision.own.force_trapped)
    b.user.wish = decision.own.wish
    b.user.future_sight = (0, None)  # source identities are not part of this projection
    b.user.baton_passing = decision.own.baton_passing
    b.user.shed_tailing = decision.own.shed_tailing
    last = decision.own.last_used_move
    if last.startswith("move:") and last[5:].isdigit():
        b.user.last_used_move = LastUsedMove(
            b.user.active.name, b.user.active.moves[int(last[5:])].name, 0
        )
    names = {p.name for p in [b.opponent.active, *b.opponent.reserve]}
    b.mode.team_datasets.initialize(b.format_spec, names)
    b.mode.smogon_sets.initialize(b.format_spec, names)
    return b


class OriginalAgent:
    name = "original-foulplay"

    def __init__(self, budget_ms=50):
        self.budget_ms = budget_ms
        self.search_seconds = 0.0

    def policy(self, decision, rng):
        import time

        # Foul Play is a wall-clock agent; reproducible teams/seeds do not make
        # iteration counts deterministic. Reports must retain this distinction.
        if decision.preview or len(decision.actions) == 1:
            from .agents import RandomAgent

            return RandomAgent().policy(decision, rng)
        FoulPlayConfig.pokemon_format = "gen9almostanyability"
        FoulPlayConfig.parallelism = FoulPlayConfig.search_threads = 1
        FoulPlayConfig.search_time_ms = self.budget_ms
        FoulPlayConfig.search_selector = "visits"
        start = time.perf_counter()
        saved_rng = random.getstate()
        random.seed(rng.getrandbits(64))
        try:
            choice = find_best_move(to_battle(decision), use_opponent_memory=False)
        finally:
            random.setstate(saved_rng)
        self.search_seconds += time.perf_counter() - start
        if choice not in decision.actions.values():
            raise ValueError(f"Original agent returned an illegal action: {choice}")
        return [float(decision.actions.get(i) == choice) for i in range(15)]
