import logging

from fp.config import FoulPlayConfig, SaveReplay
from fp.battle.protocol import async_update_battle
from fp.format_spec import FormatSpec
from fp.modes import battle_mode
from fp.modes.base import async_pick_move

logger = logging.getLogger(__name__)


def battle_is_finished(battle_tag, msg):
    if not msg.startswith(">{}".format(battle_tag)):
        return False

    return any(
        len(split_line := line.split("|", 2)) > 1
        and split_line[1] in {"win", "tie"}
        for line in msg.splitlines()
    )


async def start_battle(
    ps_websocket_client,
    pokemon_battle_type,
    team_dict,
    analysis_callback=None,
    opponent_model_callback=None,
):
    format_spec = FormatSpec.from_format_string(pokemon_battle_type)
    battle = await battle_mode(format_spec.battle_type).start_battle(
        ps_websocket_client,
        pokemon_battle_type,
        team_dict,
        analysis_callback=analysis_callback,
        opponent_model_callback=opponent_model_callback,
    )

    await ps_websocket_client.send_message(battle.battle_tag, ["hf"])
    await ps_websocket_client.send_message(battle.battle_tag, ["/timer on"])

    return battle


async def pokemon_battle(
    ps_websocket_client,
    pokemon_battle_type,
    team_dict,
    analysis_callback=None,
    opponent_model_callback=None,
):
    battle = await start_battle(
        ps_websocket_client,
        pokemon_battle_type,
        team_dict,
        analysis_callback=analysis_callback,
        opponent_model_callback=opponent_model_callback,
    )
    while True:
        msg = await ps_websocket_client.receive_message()
        if battle_is_finished(battle.battle_tag, msg):
            winner = next(
                (
                    split_line[2].strip()
                    for line in msg.splitlines()
                    if len(split_line := line.split("|", 2)) > 2
                    and split_line[1] == "win"
                ),
                None,
            )
            logger.info("Winner: {}".format(winner))
            await ps_websocket_client.send_message(battle.battle_tag, ["gg"])
            if (
                FoulPlayConfig.save_replay == SaveReplay.always
                or (
                    FoulPlayConfig.save_replay == SaveReplay.on_loss
                    and winner != FoulPlayConfig.username
                )
                or (
                    FoulPlayConfig.save_replay == SaveReplay.on_win
                    and winner == FoulPlayConfig.username
                )
            ):
                await ps_websocket_client.save_replay(battle.battle_tag)
            await ps_websocket_client.leave_battle(battle.battle_tag)
            return winner
        else:
            action_required = await async_update_battle(battle, msg)
            if action_required and not battle.wait:
                best_move = await async_pick_move(
                    battle,
                    analysis_callback=analysis_callback,
                    opponent_model_callback=opponent_model_callback,
                )
                await ps_websocket_client.send_message(battle.battle_tag, best_move)
