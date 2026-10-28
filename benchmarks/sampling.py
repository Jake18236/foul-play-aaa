"""Run identical safety cases against the frozen and current source in isolation."""

import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile


PROGRAM = """
import json, sys
from copy import deepcopy
from fp.config import FoulPlayConfig
FoulPlayConfig.pokemon_format = "gen9ou"
from fp.battle.state import Pokemon
from fp.data.sets import PokemonSet, PokemonMoveset, PredictedPokemonSet
from fp.modes.standard_battle import StandardBattleMode
from fp.search.helpers import populate_pkmn_from_set
from fp.search.standard_battles import sample_pokemon_moveset_with_known_pkmn_set
p = Pokemon("gengar",100)
s = PokemonSet("levitate", "lifeorb", "timid", (0,0,0,252,4,252),1)
mode = StandardBattleMode()
case = sys.argv[1]
violations = []
if case == "known_stats":
    p.stats_known = True
    p.stats["special-attack"] = 411
    p.hp = 79
    before = (deepcopy(p.stats),p.hp,p.max_hp)
    populate_pkmn_from_set(p, PredictedPokemonSet(s,PokemonMoveset(("shadowball",))))
    if (p.stats,p.hp,p.max_hp) != before: violations.append("known stats/HP overwritten")
elif case == "impossible_traits":
    p.impossible_abilities.add("levitate")
    p.impossible_items.add("lifeorb")
    populate_pkmn_from_set(p, PredictedPokemonSet(s,PokemonMoveset(("shadowball",))))
    if p.ability == "levitate": violations.append("impossible ability restored")
    if p.item == "lifeorb": violations.append("impossible item restored")
elif case == "missing_usage":
    assert mode.smogon_sets.move_usage_rates(p) == []
elif case == "zero_rates":
    p.add_move("taunt")
    mode.smogon_sets.raw_pkmn_sets = {"gengar":{"moves":[("shadowball",0)]}}
    assert sample_pokemon_moveset_with_known_pkmn_set(p,s,mode) == ["taunt"]
elif case == "four_move_cap":
    p.add_move("taunt")
    mode.team_datasets.get_all_possible_move_combinations = lambda *args: [PokemonMoveset(("taunt","shadowball","sludgewave","focusblast","energyball"))]
    moves = sample_pokemon_moveset_with_known_pkmn_set(p,s,mode)
    if len(moves)>4: violations.append("moves exceed four")
elif case == "revealed_traits":
    p.ability,p.item,p.tera_type = "cursedbody",None,"fairy"
    mode.smogon_sets.pkmn_sets = {"gengar":[s]}
    repaired = mode.smogon_sets.get_all_remaining_trait_combinations(p)[0]
    if (repaired.ability,repaired.item,repaired.tera_type) != ("cursedbody",None,"fairy"):
        violations.append("fallback contradicts revealed traits")
print(json.dumps({"violations":violations}))
"""


def main():
    root = Path(__file__).parents[1]
    records = []
    with tempfile.TemporaryDirectory(prefix="foulplay-baseline-") as temp:
        with tarfile.open(root / "benchmarks/baseline/source.tar.gz") as archive:
            archive.extractall(temp, filter="data")
        for source, cwd in [("baseline", temp), ("current", root)]:
            for case in [
                "known_stats",
                "impossible_traits",
                "missing_usage",
                "zero_rates",
                "four_move_cap",
                "revealed_traits",
            ]:
                try:
                    result = subprocess.run(
                        [sys.executable, "-c", PROGRAM, case],
                        cwd=cwd,
                        capture_output=True,
                        text=True,
                        timeout=2,
                    )
                    record = (
                        json.loads(result.stdout)
                        if result.returncode == 0
                        else {"error": result.stderr.splitlines()[-1]}
                    )
                except subprocess.TimeoutExpired:
                    record = {"error": "timeout"}
                records.append({"source": source, "case": case, **record})
    output = root / "benchmarks/results/sampling.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(records, indent=2) + "\n")
    print(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
