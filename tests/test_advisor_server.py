import asyncio
import json
from types import SimpleNamespace

import pytest

from advisor_server import Session, format_from_battle_room
from fp.battle.protocol import update_battle
from fp.battle.state import Battle, Pokemon
from fp.format_spec import FormatSpec
from fp.modes.standard_battle import StandardBattleMode
from fp.search.main import _apply_opponent_model, _summarize_search
from fp.search.poke_engine_helpers import battle_to_poke_engine_state


def test_live_protocol_updates_are_applied_before_the_request():
    room = "battle-gen9badnboosted-test"
    session = Session(room)
    session.task = object()
    session.feed(
        [
            "|-terastallize|p2a: Mabosstiff|Dark",
            "|switch|p2a: Komala|Komala, L100|100/100",
            "|turn|13",
            f"|request|{json.dumps({'rqid': 40})}",
        ]
    )

    message = session.client.queue.get_nowait()
    battle = Battle(room)
    battle.pokemon_format = "gen9badnboosted"
    battle.generation = "gen9"
    battle.user.name = "p1"
    battle.opponent.name = "p2"
    battle.mode = StandardBattleMode()
    battle.user.active = Pokemon(
        "pikachu", 100, format_spec=FormatSpec.from_format_string("gen9badnboosted")
    )
    battle.opponent.active = Pokemon(
        "mabosstiff", 100, format_spec=FormatSpec.from_format_string("gen9badnboosted")
    )
    battle.opponent.reserve = [
        Pokemon(
            "komala", 100, format_spec=FormatSpec.from_format_string("gen9badnboosted")
        )
    ]

    assert update_battle(battle, message)
    assert battle.opponent.active.name == "komala"
    assert not battle.opponent.active.terastallized
    assert battle.opponent.reserve[0].terastallized
    assert battle.opponent.reserve[0].tera_type == "dark"

    engine_state = battle_to_poke_engine_state(battle)
    assert engine_state.side_two.pokemon[0].types == ("normal", "typeless")
    assert not engine_state.side_two.pokemon[0].terastallized
    assert engine_state.side_two.pokemon[1].terastallized
    assert engine_state.side_two.pokemon[1].tera_type == "dark"


def test_tiebreak_protocol_event_does_not_finish_the_battle():
    session = Session("battle-gen9badnboosted-test")
    session.task = object()

    session.feed(["|tiebreak|p1"])

    assert not session.finished


def test_wait_request_is_reported_as_waiting_not_thinking(monkeypatch):
    updates = []
    monkeypatch.setattr(
        "advisor_server.publish",
        lambda room, text, status, rqid=None: updates.append(
            (room, text, status, rqid)
        ),
    )
    session = Session("battle-gen9randombattle-test")
    session.task = object()
    session.feed([f"|request|{json.dumps({'rqid': 5, 'wait': True})}"])

    assert updates == [
        ("battle-gen9randombattle-test", "Waiting for opponent", "waiting", 5)
    ]


def test_format_from_battle_room_with_randomized_room_suffix():
    room = "battle-gen9badnboosted-2703079317-qw6e2444r8wntnotjr36wgeoa4qaqikpw"

    assert format_from_battle_room(room) == "gen9badnboosted"


def test_format_from_battle_room_with_numeric_room_id():
    assert format_from_battle_room("battle-gen9randombattle-44733") == "gen9randombattle"


def test_recovered_history_only_replays_events_after_the_known_prefix():
    room = "battle-gen9randombattle-test"
    session = Session(room)
    session.task = object()
    history = [
        "|title|Alice vs. Bob",
        "|player|p1|Alice|",
        "|player|p2|Bob|",
        "|start",
        "|turn|1",
    ]
    session.feed(history + [f"|request|{json.dumps({'rqid': 1})}"])

    recovered = history + [
        "|turn|2",
        f"|request|{json.dumps({'rqid': 2})}",
    ]
    session.feed(recovered, recovered=True)

    session.client.queue.get_nowait()
    assert session.client.queue.get_nowait() == (
        f">{room}\n|turn|2\n|request|{json.dumps({'rqid': 2})}"
    )

    session.feed(recovered, recovered=True)
    assert session.client.queue.empty()


def test_midbattle_recovery_initializes_engine_from_full_history(monkeypatch):
    async def check_recovery():
        monkeypatch.setattr("advisor_server.configure_once", lambda name: None)
        session = Session("battle-gen9randombattle-test")

        async def ready():
            return None

        session.run = ready
        request = {"rqid": 13, "side": {"name": "Alice", "pokemon": []}}
        session.feed(
            [
                "|title|Alice vs. Bob",
                "|player|p1|Alice|",
                "|player|p2|Bob|",
                "|start",
                "|turn|13",
                f"|request|{json.dumps(request)}",
            ],
            recovered=True,
        )

        assert session.task is not None
        messages = []
        while not session.client.queue.empty():
            messages.append(session.client.queue.get_nowait())
        assert any("|turn|13" in message for message in messages)
        assert any("|request|" in message for message in messages)
        await session.task

    asyncio.run(check_recovery())


def test_team_preview_without_start_event_starts_advisor_in_order(monkeypatch):
    async def check_team_preview():
        monkeypatch.setattr("advisor_server.configure_once", lambda name: None)
        session = Session("battle-gen9ou-test")

        async def ready():
            return None

        session.run = ready
        request = {"rqid": 2, "side": {"name": "Alice", "pokemon": []}}
        session.feed(
            [
                "|title|Alice vs. Bob",
                "|player|p1|Alice|",
                "|player|p2|Bob|",
                "|teampreview",
                f"|request|{json.dumps(request)}",
            ]
        )

        assert session.task is not None
        messages = []
        while not session.client.queue.empty():
            messages.append(session.client.queue.get_nowait())
        preview_index = next(
            index for index, message in enumerate(messages)
            if "|teampreview" in message
        )
        request_index = next(
            index for index, message in enumerate(messages)
            if "|request|" in message
        )
        assert preview_index < request_index
        await session.task

    asyncio.run(check_team_preview())


def test_recovered_team_preview_keeps_cleared_roster_events():
    from advisor_server import messages_for_foul_play

    room = "battle-gen9badnboosted-test"
    lines = [
        "|title|Alice vs. Bob",
        "|player|p1|Alice|",
        "|player|p2|Bob|",
        "|clearpoke",
        "|poke|p2|Mabosstiff, L100",
        "|poke|p2|Komala, L100",
        "|teampreview",
        "|request|{}",
    ]

    messages = list(messages_for_foul_play(room, lines))
    preview = next(message for message in messages if "|clearpoke" in message)

    assert preview.index("|clearpoke") < preview.index("|poke|p2|Mabosstiff")
    assert preview.index("|poke|p2|Komala") < preview.index("|teampreview")
    assert all("|request|" not in message for message in messages if message is preview)
    assert messages[-1] == f">{room}\n|request|{{}}"


def test_search_summary_includes_ranked_moves_and_opponent_predictions():
    def option(move, visits, score):
        return SimpleNamespace(
            move_choice=move, visits=visits, total_score=score
        )

    result = SimpleNamespace(
        total_visits=100,
        side_one=[
            option("tackle", 60, 30),
            option("switch pikachu", 40, -12),
        ],
        side_two=[option("earthquake", 80, 0), option("protect", 20, 0)],
    )
    pokemon = SimpleNamespace(
        name="garchomp",
        revealed=False,
        moves=[SimpleNamespace(name="earthquake")],
        ability="roughskin",
        item="rockyhelmet",
    )
    battle = SimpleNamespace(
        opponent=SimpleNamespace(active=pokemon, reserve=[])
    )

    analysis = _summarize_search(
        [(result, 1.0, 0)], [(battle, 1.0)], "tackle"
    )

    assert analysis["evaluation"] == 0.0
    assert analysis["selected_rank"] == 1
    assert analysis["choices"][0]["move"] == "tackle"
    assert analysis["choices"][0]["probability"] == 0.6
    assert analysis["opponent_moves"][0]["move"] == "earthquake"
    assert analysis["opponent_team"][0]["pokemon"] == "garchomp"
    assert analysis["opponent_team"][0]["moves"][0]["move"] == "earthquake"


def test_manual_opponent_model_updates_sampled_search_battles():
    battle = Battle("battle-gen9ou-test")
    battle.pokemon_format = "gen9ou"
    battle.generation = "gen9"
    battle.mode = StandardBattleMode()
    active = Pokemon("garchomp", 100, format_spec=battle.format_spec)
    active.revealed = True
    reserve = Pokemon("tyranitar", 100, format_spec=battle.format_spec)
    battle.opponent.active = active
    battle.opponent.reserve = [reserve]

    _apply_opponent_model(
        battle,
        [
            {
                "pokemon": "Garchomp",
                "moves": ["Earthquake"],
                "ability": "Rough Skin",
                "item": "Rocky Helmet",
            },
            {"pokemon": "Tyranitar", "moves": ["Stone Edge"]},
        ],
    )

    assert battle.opponent.active is active
    assert active.revealed
    assert [move.name for move in active.moves] == ["earthquake"]
    assert active.ability == "roughskin"
    assert active.item == "rockyhelmet"
    assert battle.opponent.reserve == [reserve]
    assert [move.name for move in reserve.moves] == ["stoneedge"]


def test_repeated_preview_keeps_new_request_in_same_frame(monkeypatch):
    monkeypatch.setattr("advisor_server.publish", lambda *args: None)
    session = Session("battle-gen9almostanyability-123")
    session.task = object()
    session.seen_team_preview = True
    session.feed(["|teampreview", '|request|{"rqid": 3}', "|sentchoice|"])
    message = session.client.queue.get_nowait()
    assert '|request|{"rqid": 3}' in message
    assert "|sentchoice|" in message
    assert "|teampreview" not in message


@pytest.mark.parametrize("phase", ["preview", "playing", "waiting"])
def test_aaa_midbattle_recovery_replays_history_before_search(monkeypatch, phase):
    from unittest.mock import AsyncMock

    from advisor_server import FakeClient, messages_for_foul_play
    from fp.config import FoulPlayConfig

    async def check():
        room = "battle-gen9almostanyability-123"
        request = {
            "rqid": 27,
            "active": [{"moves": [{"id": "tackle", "move": "Tackle", "pp": 35,
                                    "maxpp": 35, "disabled": False}]}],
            "side": {"name": "Alice", "id": "p1", "pokemon": [{
                "ident": "p1: Pikachu", "details": "Pikachu, L100",
                "condition": "100/200", "active": True,
                "stats": {"atk": 100, "def": 100, "spa": 100, "spd": 100, "spe": 100},
                "moves": ["tackle"], "baseAbility": "magicguard",
                "ability": "magicguard", "item": "",
            }]},
        }
        history = [
            "|title|Alice vs. Bob", "|player|p1|Alice|", "|player|p2|Bob|",
            "|clearpoke", "|poke|p1|Pikachu, L100", "|poke|p2|Gengar, L100",
            "|teampreview", "|start",
            "|switch|p1a: Pikachu|Pikachu, L100|200/200",
            "|switch|p2a: Gengar|Gengar, L100|100/100",
            "|turn|1", "|-ability|p2a: Gengar|Levitate",
            "|-damage|p2a: Gengar|40/100", "|-boost|p2a: Gengar|spa|2",
            "|-fieldstart|move: Electric Terrain", "|turn|7",
            "|request|" + json.dumps(request),
        ]
        if phase == "preview":
            request["teamPreview"] = True
            request.pop("active")
            history = history[:history.index("|start")] + ["|request|" + json.dumps(request)]
        elif phase == "waiting":
            request["wait"] = True
            request.pop("active")
            history[-1] = "|request|" + json.dumps(request)
        client = FakeClient(room)
        client.send_message = AsyncMock()
        for message in messages_for_foul_play(room, history):
            client.queue.put_nowait(message)
        mode = StandardBattleMode()
        monkeypatch.setattr(FoulPlayConfig, "username", "Alice", raising=False)
        monkeypatch.setattr(FoulPlayConfig, "log_to_file", False, raising=False)
        monkeypatch.setattr(mode, "initialize_team_preview_datasets", lambda *args: None)
        preview = AsyncMock()
        monkeypatch.setattr(mode, "handle_team_preview", preview)
        snapshots = []

        async def pick(battle, **kwargs):
            snapshots.append((battle.turn, battle.opponent.active.name,
                              battle.opponent.active.hp / battle.opponent.active.max_hp,
                              battle.opponent.active.ability, battle.opponent.active.boosts["special-attack"],
                              battle.field, battle.user.active.hp, battle.rqid))
            return ["/choose move tackle", "27"]

        monkeypatch.setattr("fp.modes.standard_battle.async_pick_move", pick)
        battle = await asyncio.wait_for(mode.start_battle(client, "gen9almostanyability", {}), 1)
        if phase == "preview":
            preview.assert_awaited_once()
            assert not snapshots
            return
        preview.assert_not_awaited()
        assert battle.opponent.active is not None
        if phase == "waiting":
            assert battle.wait
            assert battle.turn == 7
            assert not snapshots
            client.send_message.assert_not_awaited()
        else:
            assert snapshots == [(7, "gengar", 0.4, "levitate", 2, "electricterrain", 100, 27)]
            client.send_message.assert_awaited_once_with(room, ["/choose move tackle", "27"])

    asyncio.run(check())
