"""Uniform candidate sampling, whole-team legality validation, disjoint pools."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import random
import subprocess

from fp.battle.state import Pokemon
from fp.format_spec import FormatSpec
from fp.search.poke_engine_helpers import pokemon_to_poke_engine_pkmn
from .observation import norm

ROOT = Path(__file__).resolve().parents[2]
STATS = ("hp", "atk", "def", "spa", "spd", "spe")


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def set_identity(p):
    """Order-independent moves; exclude cosmetic names and source frequencies."""
    return fingerprint(
        {
            "species": norm(p["species"]),
            "ability": norm(p["ability"]),
            "item": norm(p.get("item", "")),
            "nature": norm(p.get("nature", "serious")),
            "moves": sorted(norm(m) for m in p["moves"]),
            "evs": [p.get("evs", {}).get(s, 0) for s in STATS],
            "ivs": [p.get("ivs", {}).get(s, 31) for s in STATS],
            "level": p.get("level", 100),
        }
    )


def team_identity(team):
    return fingerprint(sorted(set_identity(p) for p in team))


def candidates(path):
    data = json.loads(Path(path).read_text())
    unique = {}
    for section in ("dex", "stats"):
        for species, sets in data.get(section, {}).items():
            for raw in sets.values():
                p = deepcopy(raw)
                p.update(species=species, level=100)
                p["ivs"] = {k: p.get("ivs", {}).get(k, 31) for k in STATS}
                p["evs"] = {k: p.get("evs", {}).get(k, 0) for k in STATS}
                p.pop("teraType", None)  # AAA forbids Tera
                if len(p.get("moves", [])) != 4 or any(
                    not isinstance(m, str) for m in p["moves"]
                ):
                    continue
                unique[set_identity(p)] = p
    return list(unique.values())


def build_manifest(source, showdown, output, count=12, seed=0, heldout_abilities=()):
    """Species-disjoint train/validation/test pools, stronger than team splitting."""
    pool = candidates(source)
    rng = random.Random(seed)
    species = sorted({norm(p["species"]) for p in pool})
    rng.shuffle(species)
    if len(species) < 18:
        raise ValueError(
            "Need at least 18 candidate species for disjoint six-member pools"
        )
    n = max(6, len(species) // 5)
    groups = {
        "test": set(species[:n]),
        "validation": set(species[n : 2 * n]),
        "train": set(species[2 * n :]),
    }
    heldout = {norm(a) for a in heldout_abilities}
    # Ensure a held-out ability is actually exercised, rather than reporting an
    # empty generalization suite when its species lands in the training pool.
    for ability in sorted(heldout):
        supported = sorted({norm(p["species"]) for p in pool if norm(p["ability"]) == ability})
        if not supported:
            raise ValueError(f"No candidate supports held-out ability {ability}")
        selected = next((s for s in supported if s in groups["test"]), supported[0])
        if selected not in groups["test"]:
            source_group = next(k for k,v in groups.items() if selected in v)
            replacement = sorted(groups["test"])[-1]
            groups["test"].remove(replacement)
            groups["test"].add(selected)
            groups[source_group].remove(selected)
            groups[source_group].add(replacement)
    proposals, splits = [], []
    for split, names in groups.items():
        by_species = {}
        for p in pool:
            if norm(p["species"]) in names and (
                split == "test" or norm(p["ability"]) not in heldout
            ):
                by_species.setdefault(norm(p["species"]), []).append(p)
        if len(by_species) < 6:
            raise ValueError(f"Too few species in {split}")
        for attempt in range(count * 40):
            team = [
                rng.choice(by_species[name])
                for name in rng.sample(sorted(by_species), 6)
            ]
            if split == "test" and heldout:
                ability = sorted(heldout)[attempt % len(heldout)]
                forced = rng.choice([p for p in pool if norm(p["species"]) in names and norm(p["ability"]) == ability])
                team = [forced] + [rng.choice(by_species[name]) for name in rng.sample(sorted(set(by_species)-{norm(forced["species"])}), 5)]
            proposals.append(team)
            splits.append(split)
    result = subprocess.run(
        [
            "node",
            str(ROOT / "scripts/validate_aaa_teams.cjs"),
            str(Path(showdown).resolve()),
        ],
        input=json.dumps(proposals),
        capture_output=True,
        text=True,
        check=True,
    )
    validation = json.loads(result.stdout)
    accepted = {split: [] for split in groups}
    rejected = {}
    seen = set()
    for split, entry in zip(splits, validation["results"], strict=True):
        if entry["problems"]:
            for problem in entry["problems"]:
                rejected[problem] = rejected.get(problem, 0) + 1
            continue
        team = entry["team"]
        ident = team_identity(team)
        if ident in seen or len(accepted[split]) >= count:
            continue
        # Native conversion and serialization is an additional capability gate.
        try:
            native_team(team)
        except (ValueError, KeyError) as error:
            rejected[str(error)] = rejected.get(str(error), 0) + 1
            continue
        seen.add(ident)
        accepted[split].append({"id": ident, "team": team})
    if any(len(teams) < count for teams in accepted.values()):
        raise ValueError(
            f"Insufficient valid native teams: { {k: len(v) for k,v in accepted.items()} }; rejects: {rejected}"
        )
    manifest = {
        "schema": 1,
        "format": validation["format"],
        "seed": seed,
        "source_sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest(),
        "validator_sha256": validation["validator_sha256"],
        "splits": accepted,
        "heldout_abilities": sorted(heldout),
        "rejections": rejected,
    }
    manifest["id"] = fingerprint(manifest)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def native_team(team):
    from poke_engine import Side, State
    from fp.config import FoulPlayConfig

    FoulPlayConfig.pokemon_format = "gen9almostanyability"
    members = []
    for data in team:
        p = Pokemon(
            norm(data["species"]),
            data.get("level", 100),
            nature=norm(data.get("nature", "serious")),
            evs=tuple(data.get("evs", {}).get(k, 0) for k in STATS),
            format_spec=FormatSpec.from_format_string("gen9almostanyability"),
        )
        p.ability, p.item = norm(data["ability"]), norm(data.get("item", "none"))
        # The existing converter assumes 31 IVs. Correct explicit IV deviations.
        for i, key in enumerate(STATS):
            iv = data.get("ivs", {}).get(key, 31)
            if not 0 <= iv <= 31:
                raise ValueError("Invalid IV")
            if iv != 31:
                from fp.data import pokedex
                from fp import constants

                stat_key = (
                    constants.HITPOINTS,
                    constants.ATTACK,
                    constants.DEFENSE,
                    constants.SPECIAL_ATTACK,
                    constants.SPECIAL_DEFENSE,
                    constants.SPEED,
                )[i]
                base = pokedex[p.name]["baseStats"][stat_key]
                raw31 = ((2 * base + 31 + p.evs[i] // 4) * p.level) // 100
                raw = ((2 * base + iv + p.evs[i] // 4) * p.level) // 100
                if i == 0:
                    p.hp = p.max_hp = raw + p.level + 10
                else:
                    ratio = p.stats[stat_key] / (raw31 + 5)
                    multiplier = min((0.9, 1.0, 1.1), key=lambda x: abs(x - ratio))
                    p.stats[stat_key] = int((raw + 5) * multiplier)
        for move in data["moves"]:
            p.add_move(norm(move))
        members.append(pokemon_to_poke_engine_pkmn(p))
    side = Side(pokemon=members)
    State(side_one=side, terastallization_allowed=False).to_string()
    return side


def load_manifest(path):
    manifest = json.loads(Path(path).read_text())
    claimed = manifest.pop("id")
    if fingerprint(manifest) != claimed or manifest["format"] != "gen9almostanyability":
        raise ValueError("Manifest integrity/format mismatch")
    manifest["id"] = claimed
    seen_teams, seen_sets, seen_species = set(), set(), set()
    for split in ("train", "validation", "test"):
        teams = manifest["splits"][split]
        ids = {team_identity(t["team"]) for t in teams}
        sets = {set_identity(p) for t in teams for p in t["team"]}
        species = {norm(p["species"]) for t in teams for p in t["team"]}
        if ids & seen_teams or sets & seen_sets or species & seen_species:
            raise ValueError("Train/evaluation team, set or species leakage")
        if any(t["id"] != team_identity(t["team"]) for t in teams):
            raise ValueError("Team identity mismatch")
        seen_teams |= ids
        seen_sets |= sets
        seen_species |= species
    return manifest
