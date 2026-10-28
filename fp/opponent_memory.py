"""Confirmed public observations, kept separate from sampled battle states."""

import json
import logging
import os
import re
import threading
import time
from pathlib import Path

from fp import constants
from fp.battle.helpers import normalize_name
from fp.data import all_move_json, pokedex

logger = logging.getLogger(__name__)
MEMORY_PATH = Path(__file__).resolve().parent / "data/local_data/opponent_memory.json"
_lock = threading.Lock()


def _read():
    try:
        data = json.loads(MEMORY_PATH.read_text())
        if not isinstance(data, dict):
            return {}
        return {
            key: [
                entry
                for entry in history
                if isinstance(entry, dict)
                and isinstance(entry.get("time"), (int, float))
                and isinstance(entry.get("sets"), dict)
                and isinstance(entry.get("roster"), list)
            ]
            for key, history in data.items()
            if isinstance(history, list)
        }
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.warning("Could not read opponent memory", exc_info=True)
        return {}


def remember_protocol(room, fmt, lines):
    """Save only direct revelations, never values from sampled battle states."""
    players = {}
    own_side = None
    own_name = None
    for line in lines:
        parts = line.split("|")
        if len(parts) > 3 and parts[1] == "player":
            players[parts[2]] = parts[3]
        if line.startswith("|request|"):
            try:
                own_name = (
                    json.loads(line[len("|request|") :]).get("side", {}).get("name")
                    or own_name
                )
                own_side = (
                    json.loads(line[len("|request|") :]).get("side", {}).get("id")
                    or own_side
                )
            except (ValueError, AttributeError):
                pass
    if own_side not in {"p1", "p2"} and own_name:
        user_id = re.sub(r"[^a-z0-9]", "", own_name.lower())
        own_side = next(
            (
                side
                for side, name in players.items()
                if re.sub(r"[^a-z0-9]", "", name.lower()) == user_id
            ),
            None,
        )
    if own_side not in {"p1", "p2"}:
        return
    side = "p2" if own_side == "p1" else "p1"
    username = re.sub(r"[^a-z0-9]", "", players.get(side, "").lower())
    if not username:
        return

    roster, sets, active = set(), {}, {}
    changed_items, changed_abilities = set(), set()
    for line in lines:
        p = line.split("|")
        if len(p) < 3:
            continue
        event, ident = p[1:3]
        # Illusion/Transform can misattribute observations. Omit these games.
        if event in {"replace", "-transform"}:
            return
        if event == "poke" and ident == side and len(p) > 3:
            roster.add(normalize_name(p[3].split(",")[0]))
        if event in {"switch", "drag"} and ident.startswith(side) and len(p) > 3:
            species = normalize_name(p[3].split(",")[0])
            roster.add(species)
            active[ident.split(":")[0]] = species
            sets.setdefault(species, {"moves": []})
        species = active.get(ident.split(":")[0])
        if not species or len(p) < 4:
            continue
        entry = sets[species]
        value = normalize_name(p[3])
        if event == "-item" and any("[from]" in tag for tag in p[4:]):
            changed_items.add(species)
        if event == "-ability" and any("[from]" in tag for tag in p[4:]):
            changed_abilities.add(species)
        if event == "move" and not any("[from]" in tag for tag in p[4:]):
            if value in all_move_json and value not in entry["moves"]:
                entry["moves"].append(value)
        # Item acquisition and ability changes aren't evidence of the original set.
        if (
            species not in changed_items
            and event in {"-item", "-enditem"}
            and not any("[from]" in tag for tag in p[4:])
        ):
            entry.setdefault("item", value)
        if (
            species not in changed_abilities
            and event == "-ability"
            and not any("[from]" in tag for tag in p[4:])
        ):
            entry.setdefault("ability", value)
    sets = {
        name: entry
        for name, entry in sets.items()
        if name in pokedex and len(entry["moves"]) <= 4
    }
    if not sets:
        return
    key = f"{fmt}:{username}"
    with _lock:
        data = _read()
        history = data.get(key, [])
        if not isinstance(history, list):
            history = []
        history = [entry for entry in history if entry.get("room") != room]
        history.append(
            {"room": room, "time": time.time(), "roster": sorted(roster), "sets": sets}
        )
        data[key] = history[-10:]
        # Bound disk usage and age even for opponents who are never seen again.
        cutoff = time.time() - 90 * 86400
        data = {
            key: [e for e in history if e.get("time", 0) > cutoff]
            for key, history in data.items()
            if isinstance(history, list)
        }
        data = {key: history for key, history in data.items() if history}
        try:
            MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            temporary = MEMORY_PATH.with_suffix(".tmp")
            temporary.write_text(json.dumps(data, indent=2))
            os.replace(temporary, MEMORY_PATH)
        except OSError:
            logger.warning("Could not save opponent memory", exc_info=True)
    logger.info("Remembered revealed sets for %s in %s", username, fmt)


def recall(battle):
    username = re.sub(r"[^a-z0-9]", "", (battle.opponent.account_name or "").lower())
    key = f"{battle.pokemon_format}:{username}"
    roster = {
        p.name
        for p in [battle.opponent.active, *battle.opponent.reserve]
        if p is not None and p.name and p.name != "dummy"
    }
    with _lock:
        history = _read().get(key, [])
    if not isinstance(history, list):
        return {}
    for entry in reversed(history):
        if (
            entry.get("room") != battle.battle_tag
            and entry.get("time", 0) > time.time() - 90 * 86400
            and roster
            and roster.issubset(entry.get("roster", []))
        ):
            logger.info(
                "Using rematch profile for %s from %s", username, entry.get("room")
            )
            return entry.get("sets", {})
    return {}


def apply_prior(battle, remembered):
    """Apply a prior to a selected scenario, never to the live battle."""
    if not remembered:
        return
    for pokemon in [battle.opponent.active, *battle.opponent.reserve]:
        if pokemon is None:
            continue
        entry = remembered.get(pokemon.name)
        if not entry:
            continue
        if (
            pokemon.ability
            and entry.get("ability")
            and pokemon.ability != entry["ability"]
        ):
            continue
        if (
            pokemon.item not in {None, constants.UNKNOWN_ITEM}
            and entry.get("item")
            and pokemon.item != entry["item"]
        ):
            continue
        old_moves = entry.get("moves", [])
        current = {m.name for m in pokemon.moves}
        # New revelations incompatible with an old full set invalidate that prior.
        if len(set(old_moves) | current) > 4:
            continue
        for move in old_moves:
            if move in all_move_json and move not in current:
                pokemon.add_move(move)
        ability = entry.get("ability")
        if (
            pokemon.ability is None
            and ability
            and ability not in pokemon.impossible_abilities
        ):
            pokemon.ability = ability
        item = entry.get("item")
        if (
            pokemon.item == constants.UNKNOWN_ITEM
            and item
            and item not in pokemon.impossible_items
            and (pokemon.can_have_choice_item or item not in constants.CHOICE_ITEMS)
        ):
            pokemon.item = item
