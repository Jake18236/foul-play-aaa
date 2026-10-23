from fp import constants
from fp.battle.state import Battle, Battler, Pokemon
from fp.config import FoulPlayConfig
from fp.constants import BattleType
from fp.format_spec import FormatSpec
from fp.search.poke_engine_helpers import (
    battle_to_poke_engine_state,
    poke_engine_get_damage_rolls,
)
from fp.modes.standard_battle import StandardBattleMode
from poke_engine import State as PokeEngineState


SHOWDOWN_FORMAT = "gen9badnboosted"


def make_battle():
    format_spec = FormatSpec.from_format_string(SHOWDOWN_FORMAT)
    battle = Battle("test")
    battle.pokemon_format = format_spec.full_name
    battle.generation = format_spec.generation
    battle.battle_type = format_spec.battle_type

    pikachu = Pokemon(
        "pikachu",
        50,
        nature="modest",
        evs=(0, 0, 0, 252, 0, 0),
        format_spec=format_spec,
    )
    pikachu.ability = "static"
    pikachu.item = None
    pikachu.add_move("thunderbolt")
    battle.user.active = pikachu

    snorlax = Pokemon(
        "snorlax",
        50,
        nature="careful",
        evs=(252, 0, 0, 0, 252, 0),
        format_spec=format_spec,
    )
    snorlax.ability = "thickfat"
    snorlax.item = None
    snorlax.add_move("tackle")
    battle.opponent.active = snorlax

    return battle


class TestBadnBoosted:
    def test_first_turn_request_accepts_empty_advisor_team_dict(self):
        battler = Battler()
        battler.team_dict = {}
        request = {
            "active": [{"moves": []}],
            "side": {
                "id": "p1",
                "pokemon": [
                    {
                        "ident": "p1: Deoxys",
                        "details": "Deoxys, L100, M",
                        "condition": "100/100",
                        "active": True,
                        "stats": {
                            "atk": 260,
                            "def": 260,
                            "spa": 260,
                            "spd": 260,
                            "spe": 260,
                        },
                        "moves": [],
                        "ability": "pressure",
                        "item": "",
                    }
                ],
            },
        }

        battler.initialize_first_turn_user_from_json(
            request, FormatSpec.from_format_string(SHOWDOWN_FORMAT)
        )

        assert battler.active.name == "deoxys"

    def test_pikachu_stats_match_showdown_species_and_stat_calculation(self):
        FoulPlayConfig.pokemon_format = "gen9ou"
        battle = make_battle()
        pikachu = battle.user.active

        # Reference: Showdown simulator gen9badnboosted, 1636bf6d0848fb3c9330576963a647d4e179d278.
        assert {
            constants.HITPOINTS: 70,
            constants.ATTACK: 110,
            constants.DEFENSE: 80,
            constants.SPECIAL_ATTACK: 100,
            constants.SPECIAL_DEFENSE: 100,
            constants.SPEED: 90,
        } == pikachu.base_stats
        assert pikachu.max_hp == 145
        assert pikachu.stats == {
            constants.ATTACK: 117,
            constants.DEFENSE: 100,
            constants.SPECIAL_ATTACK: 167,
            constants.SPECIAL_DEFENSE: 120,
            constants.SPEED: 110,
        }

    def test_poke_engine_damage_matches_showdown_max_normal_damage(self):
        FoulPlayConfig.pokemon_format = "gen9ou"
        battle = make_battle()

        # Showdown's max non-critical Thunderbolt roll is 58 against this Snorlax set.
        engine_state = battle_to_poke_engine_state(battle)
        restored_state = PokeEngineState.from_string(engine_state.to_string())
        assert restored_state.side_one.pokemon[0].special_attack == 167
        assert restored_state.side_two.pokemon[0].special_defense == 178

        side_one_rolls, _ = poke_engine_get_damage_rolls(
            battle, "thunderbolt", "tackle", True
        )
        assert side_one_rolls[0] == 58

    def test_badnboosted_remains_a_gen9_standard_battle(self):
        format_spec = FormatSpec.from_format_string(SHOWDOWN_FORMAT)
        assert format_spec.battle_type == BattleType.STANDARD_BATTLE
        assert not format_spec.champions
        assert not format_spec.national_dex

    def test_gen9_randombattle_stats_are_unchanged(self):
        pikachu = Pokemon(
            "pikachu",
            50,
            format_spec=FormatSpec.from_format_string("gen9randombattle"),
        )
        assert pikachu.base_stats[constants.HITPOINTS] == 35
        assert pikachu.base_stats[constants.ATTACK] == 55

    def test_standard_battle_uses_gen9_ou_priors_for_badnboosted(self, monkeypatch):
        monkeypatch.setattr(FoulPlayConfig, "smogon_stats", None)
        mode = StandardBattleMode()
        initialized_formats = {}
        monkeypatch.setattr(
            mode.smogon_sets,
            "initialize",
            lambda format_spec, names: initialized_formats.update(
                smogon=format_spec.full_name
            ),
        )
        monkeypatch.setattr(
            mode.team_datasets,
            "initialize",
            lambda format_spec, names: initialized_formats.update(
                team=format_spec.full_name
            ),
        )

        mode.initialize_team_preview_datasets(SHOWDOWN_FORMAT, {"pikachu"}, "")

        assert initialized_formats == {
            "smogon": "gen9badnboosted",
            "team": "gen9ou",
        }
