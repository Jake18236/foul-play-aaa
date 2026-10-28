"""Information boundary: encoders accept observations, never simulator states.

The ledger intentionally under-observes abilities/items until a public event
adapter is available. Private native state is not evidence of a public reveal.
"""

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import math

from fp.data import pokedex

UNKNOWN = "?"
SCHEMA = 1
ACTION_COUNT = 15  # four moves, four Tera moves, six team-slot switches, pass
CATEGORIES = (
    "species",
    "type0",
    "type1",
    "ability",
    "item",
    "nature",
    "tera",
    "status",
    "move0",
    "move1",
    "move2",
    "move3",
    "base_ability",
)
NUMBERS = (
    "hp",
    "hp_known",
    "level",
    "active",
    "terastallized",
    "present",
    "attack",
    "defense",
    "special_attack",
    "special_defense",
    "speed",
    "ev0",
    "ev1",
    "ev2",
    "ev3",
    "ev4",
    "ev5",
    "pp0",
    "pp1",
    "pp2",
    "pp3",
    "disabled0",
    "disabled1",
    "disabled2",
    "disabled3",
)
CONDITIONS = (
    "aurora_veil",
    "crafty_shield",
    "healing_wish",
    "light_screen",
    "lucky_chant",
    "lunar_dance",
    "mat_block",
    "mist",
    "protect",
    "quick_guard",
    "reflect",
    "safeguard",
    "spikes",
    "stealth_rock",
    "sticky_web",
    "tailwind",
    "toxic_count",
    "toxic_spikes",
    "wide_guard",
)
BOOSTS = (
    "attack_boost",
    "defense_boost",
    "special_attack_boost",
    "special_defense_boost",
    "speed_boost",
    "accuracy_boost",
    "evasion_boost",
)
DURATIONS = ("confusion", "encore", "lockedmove", "slowstart", "taunt", "yawn")
EFFECT_LIMIT = 160


def norm(value):
    return str(value).lower().replace("_", "").replace(" ", "").replace("-", "")


def token(value, size=8192):
    if value == UNKNOWN:
        return 1
    return 2 + int.from_bytes(
        hashlib.blake2b(str(value).encode(), digest_size=8).digest(), "little"
    ) % (size - 2)


def private_pokemon(p, active=False):
    out = {k: UNKNOWN for k in CATEGORIES}
    out.update({k: 0.0 for k in NUMBERS})
    out.update(
        species=norm(p.id),
        type0=norm(p.types[0]),
        type1=norm(p.types[1]),
        ability=norm(p.ability),
        item=norm(p.item),
        nature=norm(p.nature),
        tera=norm(p.tera_type),
        status=norm(p.status),
        base_ability=norm(p.base_ability),
        hp=p.hp / max(1, p.maxhp),
        hp_known=1.0,
        level=p.level / 100,
        active=float(active),
        terastallized=float(p.terastallized),
        present=float(norm(p.id) != "none"),
    )
    for k in ("attack", "defense", "special_attack", "special_defense", "speed"):
        out[k] = getattr(p, k) / 500
    for i, ev in enumerate(p.evs):
        out[f"ev{i}"] = ev / 252
    for i, move in enumerate(p.moves):
        out[f"move{i}"] = norm(move.id)
        out[f"pp{i}"] = move.pp / 64
        out[f"disabled{i}"] = float(move.disabled)
    return out


@dataclass
class PublicLedger:
    """Stable team slots. No references to native objects survive projection."""

    team: list[dict]
    beliefs: dict = field(default_factory=dict)

    @classmethod
    def preview(cls, species):
        team = []
        for name in species:
            p = {k: UNKNOWN for k in CATEGORIES}
            p.update({k: 0.0 for k in NUMBERS})
            name = norm(name)
            types = pokedex.get(name, {}).get("types", [])
            p.update(
                species=name,
                type0=types[0] if types else UNKNOWN,
                type1=types[1] if len(types) > 1 else "typeless",
                present=float(name != "none"),
            )
            team.append(p)
        if len(team) != 6:
            raise ValueError(
                "Ledger requires six stable slots, including explicit empty slots"
            )
        return cls(team)

    def see_active(self, side):
        index = int(side.active_index)
        p = side.pokemon[index]
        for slot in self.team:
            slot["active"] = 0.0
        # Showdown exposes opposing HP as a percentage, not exact HP/max HP.
        self.team[index].update(
            hp=math.ceil(100 * p.hp / max(1, p.maxhp)) / 100,
            hp_known=1.0,
            active=1.0,
            status=norm(p.status),
            terastallized=float(p.terastallized),
        )
        if p.terastallized:
            self.team[index]["tera"] = norm(p.tera_type)

    def reveal(self, slot, field_name, value):
        """Only call with an observed public event, never a sampled set."""
        if field_name not in (
            "ability",
            "item",
            "tera",
            "move0",
            "move1",
            "move2",
            "move3",
        ):
            raise ValueError("Unsupported public reveal")
        self.team[slot][field_name] = norm(value)

    def infer(self, slot, field_name, probabilities):
        if field_name not in ("ability", "item"):
            raise ValueError("Only ability/item beliefs currently supported")
        if any(not math.isfinite(v) or v < 0 or v >= 1 for v in probabilities.values()):
            raise ValueError("Inferred traits must have probability strictly below one")
        if sum(probabilities.values()) >= 1:
            raise ValueError("Beliefs must retain positive unknown mass")
        self.beliefs[f"{slot}:{field_name}"] = dict(probabilities)


def action_map(side, legal):
    """Native labels -> stable action slots, with duplicate-label rejection."""
    active = side.pokemon[int(side.active_index)]
    mapping = {}
    for action in legal:
        if action in ("No Move", "none"):
            index = 14
        elif action.startswith("switch "):
            matches = [
                i for i, p in enumerate(side.pokemon) if norm(p.id) == norm(action[7:])
            ]
            if len(matches) != 1:
                raise ValueError(f"Ambiguous native switch: {action}")
            index = 8 + matches[0]
        else:
            tera = action.endswith("-tera")
            move = action.removesuffix("-tera")
            matches = [
                i for i, m in enumerate(active.moves) if norm(m.id) == norm(move)
            ]
            if not matches and move == "struggle":
                index = 0
            elif len(matches) == 1:
                index = matches[0] + 4 * int(tera)
            else:
                raise ValueError(f"Unknown native move: {action}")
        if index in mapping:
            raise ValueError(f"Duplicate native action: {action}")
        mapping[index] = action
    if not mapping:
        raise ValueError("Nonterminal observation has no legal action")
    return mapping


def observe(own, opponent_public, ledger, state, legal, step=0):
    """Explicit public whitelist. Never serialize the opponent native team."""
    effects = [
        ("weather:" + norm(state.weather), 1.0),
        ("terrain:" + norm(state.terrain), 1.0),
        ("trick_room", float(state.trick_room)),
        ("step", step / 500),
        ("tera_allowed", float(state.terastallization_allowed)),
    ]
    for i, p in enumerate(own.pokemon):
        effects.extend([(f"own:{i}:maxhp", p.maxhp/500),
                        (f"own:{i}:rest_turns", p.rest_turns/3),
                        (f"own:{i}:sleep_turns", p.sleep_turns/3)])
    # Remaining durations may depend on hidden extender items: omit them on purpose.
    for prefix, side in (("own", own), ("opponent", opponent_public)):
        effects += [(prefix + ":" + k, getattr(side, k) / 6) for k in BOOSTS]
        effects += [
            (prefix + ":" + k, float(getattr(side.side_conditions, k) > 0))
            for k in CONDITIONS
        ]
        for k in ("spikes", "toxic_spikes"):
            effects.append(
                (prefix + ":layers:" + k, getattr(side.side_conditions, k) / 3)
            )
        effects += [
            (prefix + ":volatile:" + norm(k), 1.0)
            for k in sorted(side.volatile_statuses)
        ]
        if prefix == "own":
            effects += [
                (prefix + ":" + k, float(getattr(side, k)))
                for k in (
                    "force_switch",
                    "force_trapped",
                    "baton_passing",
                    "shed_tailing",
                    "slow_uturn_move",
                )
            ]
            effects += [
                (
                    prefix + ":duration:" + k,
                    getattr(side.volatile_status_durations, k) / 8,
                )
                for k in DURATIONS
            ]
        effects += [
            (prefix + ":wish", float(side.wish[0] > 0)),
            (prefix + ":future_sight", float(side.future_sight[0] > 0)),
        ]
    for key, distribution in sorted(ledger.beliefs.items()):
        effects.extend(
            ("belief:" + key + ":" + value, probability)
            for value, probability in sorted(distribution.items())
        )
        effects.append(("belief:" + key + ":unknown", 1 - sum(distribution.values())))
    if len(effects) > EFFECT_LIMIT:
        raise ValueError("Effect capacity exceeded; refusing silent truncation")
    mapping = action_map(own, legal)
    return {
        "schema": SCHEMA,
        "team": [
            private_pokemon(p, i == int(own.active_index))
            for i, p in enumerate(own.pokemon)
        ]
        + deepcopy(ledger.team),
        "effects": effects,
        "legal": [i in mapping for i in range(ACTION_COUNT)],
    }


def encode(observations):
    """Stable hashed categorical embeddings; IDs are never numeric features."""
    import torch

    cats, nums, effects, weights, masks = [], [], [], [], []
    for observation in observations:
        if observation["schema"] != SCHEMA:
            raise ValueError("Observation schema mismatch")
        cats.append(
            [
                [token(k + ":" + p[k]) if p[k] != UNKNOWN else 1 for k in CATEGORIES]
                for p in observation["team"]
            ]
        )
        nums.append([[p[k] for k in NUMBERS] for p in observation["team"]])
        entries = observation["effects"]
        effects.append(
            [token(k) for k, _ in entries] + [0] * (EFFECT_LIMIT - len(entries))
        )
        weights.append([v for _, v in entries] + [0.0] * (EFFECT_LIMIT - len(entries)))
        masks.append(observation["legal"])
    return {
        "categorical": torch.tensor(cats, dtype=torch.long),
        "numeric": torch.tensor(nums, dtype=torch.float32),
        "effects": torch.tensor(effects, dtype=torch.long),
        "effect_values": torch.tensor(weights, dtype=torch.float32),
        "legal": torch.tensor(masks, dtype=torch.bool),
    }
