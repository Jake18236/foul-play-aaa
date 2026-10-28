import logging
import random
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from copy import deepcopy

from fp import constants
from fp.battle.helpers import normalize_name
from fp.battle.state import Battle, Pokemon
from fp.config import FoulPlayConfig

from poke_engine import State as PokeEngineState, monte_carlo_tree_search, MctsResult

from fp.search.poke_engine_helpers import battle_to_poke_engine_state
from fp.opponent_memory import recall

logger = logging.getLogger(__name__)


def select_move_from_mcts_results(mcts_results: list[(MctsResult, float, int)]) -> str:
    final_policy = {}
    for mcts_result, sample_chance, index in mcts_results:
        this_policy = max(mcts_result.side_one, key=lambda x: x.visits)
        logger.info(
            "Policy {}: {} visited {}% avg_score={} sample_chance_multiplier={}".format(
                index,
                this_policy.move_choice,
                round(100 * this_policy.visits / mcts_result.total_visits, 2),
                round(this_policy.total_score / this_policy.visits, 3),
                round(sample_chance, 3),
            )
        )
        for s1_option in mcts_result.side_one:
            final_policy[s1_option.move_choice] = final_policy.get(
                s1_option.move_choice, 0
            ) + (sample_chance * (s1_option.visits / mcts_result.total_visits))

    final_policy = sorted(final_policy.items(), key=lambda x: x[1], reverse=True)

    choice, probability = final_policy[0]
    logger.info("Selected highest-visit choice: %s (%.3f%%)", choice, probability * 100)
    return choice


def get_result_from_mcts(
    state: str, search_time_ms: int, index: int, threads: int, selector="visits"
) -> MctsResult:
    logger.debug("Calling with {} state: {}".format(index, state))
    poke_engine_state = PokeEngineState.from_string(state)

    if selector == "matrix":
        from fp.search.payoff import diagnostic_search

        return diagnostic_search(
            poke_engine_state, search_time_ms, seed=random.getrandbits(64)
        )

    res = monte_carlo_tree_search(poke_engine_state, search_time_ms, threads=threads)
    logger.info("Iterations {}: {}".format(index, res.total_visits))
    return res


def _choice_text(choice) -> str:
    if isinstance(choice, str):
        return choice
    if isinstance(choice, (tuple, list)):
        return " ".join(str(part) for part in choice if part)
    return str(choice)


def _summarize_opponent_team(battles):
    samples = defaultdict(
        lambda: {
            "probability": 0.0,
            "known": True,
            "moves": defaultdict(float),
            "abilities": defaultdict(float),
            "items": defaultdict(float),
        }
    )
    total_weight = sum(chance for _, chance in battles)

    if not total_weight:
        return []

    for battle, chance in battles:
        seen = set()
        pokemon = [battle.opponent.active, *battle.opponent.reserve]
        for pkmn in pokemon:
            if pkmn is None or pkmn.name in seen:
                continue
            seen.add(pkmn.name)
            sample = samples[pkmn.name]
            sample["probability"] += chance
            sample["known"] = sample["known"] and (
                pkmn.revealed or getattr(battle, "team_preview", False)
            )
            for move in pkmn.moves:
                sample["moves"][move.name] += chance
            if pkmn.ability:
                sample["abilities"][pkmn.ability] += chance
            if pkmn.item != constants.UNKNOWN_ITEM:
                sample["items"][pkmn.item] += chance

    result = []
    for name, sample in samples.items():
        probability = sample["probability"] / total_weight
        ability = max(sample["abilities"], key=sample["abilities"].get, default=None)
        item = max(sample["items"], key=sample["items"].get, default=None)
        result.append(
            {
                "pokemon": name,
                "probability": round(probability, 4),
                "known": sample["known"],
                "moves": [
                    {
                        "move": move,
                        "probability": round(weight / sample["probability"], 4),
                    }
                    for move, weight in sorted(
                        sample["moves"].items(), key=lambda item: item[1], reverse=True
                    )[:4]
                ],
                "ability": ability,
                "ability_probability": round(
                    sample["abilities"][ability] / sample["probability"], 4
                )
                if ability
                else None,
                "item": item,
                "item_probability": round(
                    sample["items"][item] / sample["probability"], 4
                )
                if item
                else None,
            }
        )

    return sorted(result, key=lambda entry: entry["probability"], reverse=True)[:12]


def _summarize_search(mcts_results, battles, selected_choice):
    move_probabilities = defaultdict(float)
    move_score_totals = defaultdict(float)
    move_score_weights = defaultdict(float)
    opponent_probabilities = defaultdict(float)

    for mcts_result, sample_chance, _ in mcts_results:
        total_visits = mcts_result.total_visits
        if not total_visits:
            continue
        for option in mcts_result.side_one:
            choice = _choice_text(option.move_choice)
            move_probabilities[choice] += sample_chance * option.visits / total_visits
            if option.visits:
                # Scenario probabilities must not depend on how many iterations
                # the engine happened to complete for that scenario.
                move_score_totals[choice] += (
                    sample_chance * option.total_score / option.visits
                )
                move_score_weights[choice] += sample_chance
        for option in mcts_result.side_two:
            choice = _choice_text(option.move_choice)
            opponent_probabilities[choice] += (
                sample_chance * option.visits / total_visits
            )

    choices = []
    for move, probability in sorted(
        move_probabilities.items(), key=lambda item: item[1], reverse=True
    ):
        score_weight = move_score_weights[move]
        choices.append(
            {
                "move": move,
                "probability": round(probability, 4),
                "score": (
                    round(move_score_totals[move] / score_weight, 4)
                    if score_weight
                    else None
                ),
            }
        )

    opponent_moves = [
        {"move": move, "probability": round(probability, 4)}
        for move, probability in sorted(
            opponent_probabilities.items(), key=lambda item: item[1], reverse=True
        )[:5]
    ]
    selected_text = _choice_text(selected_choice)
    selected_rank = next(
        (
            rank
            for rank, choice in enumerate(choices, 1)
            if choice["move"] == selected_text
        ),
        None,
    )
    selected = next(
        (choice for choice in choices if choice["move"] == selected_text), None
    )
    reason = None
    if selected and selected_rank:
        reason = (
            f"Ranked #{selected_rank} by weighted search visits "
            f"({selected['probability'] * 100:.1f}%) across {len(battles)} opponent scenarios."
        )
        if selected["score"] is not None:
            reason += f" Estimated score: {selected['score']:+.2f}."

    return {
        # Native MCTS scores are in [0, 1], relative to the root heuristic.
        # Center the panel's signed scale without calling this a win probability.
        "evaluation": 2 * selected["score"] - 1
        if selected and selected["score"] is not None
        else None,
        "evaluation_kind": "relative_search_score",
        "selected_rank": selected_rank,
        "reason": reason,
        "choices": choices[:8],
        "opponent_moves": opponent_moves,
        "opponent_team": _summarize_opponent_team(battles),
        "sample_count": len(battles),
    }


def _apply_opponent_model(battle: Battle, model):
    if not model:
        return

    existing = [
        pokemon
        for pokemon in [battle.opponent.active, *battle.opponent.reserve]
        if pokemon is not None
    ]
    known = [
        pokemon
        for pokemon in existing
        if pokemon is battle.opponent.active or pokemon.revealed or battle.team_preview
    ]
    roster = known[:6]
    seen = {pokemon.name for pokemon in roster}

    for entry in model:
        name = normalize_name(entry["pokemon"])
        if not name:
            continue

        pokemon = next((p for p in existing if p.name == name), None)
        if pokemon is None:
            pokemon = Pokemon(name, 100, format_spec=battle.format_spec)

        ability = normalize_name(entry.get("ability", ""))
        if ability and pokemon.ability is None:
            pokemon.ability = None if ability == "none" else ability

        item = normalize_name(entry.get("item", ""))
        if item and pokemon.item == constants.UNKNOWN_ITEM:
            pokemon.item = item

        moves = [
            normalize_name(move)
            for move in entry.get("moves", [])
            if isinstance(move, str) and normalize_name(move)
        ]
        if moves:
            for move in moves:
                if len(pokemon.moves) < 4 and pokemon.get_move(move) is None:
                    pokemon.add_move(move)

        if name not in seen and len(roster) < 6:
            roster.append(pokemon)
            seen.add(name)

    for pokemon in existing:
        if pokemon.name not in seen and len(roster) < 6:
            roster.append(pokemon)
            seen.add(pokemon.name)

    active_name = battle.opponent.active.name if battle.opponent.active else None
    battle.opponent.active = next(
        (p for p in roster if p.name == active_name),
        battle.opponent.active,
    )
    battle.opponent.reserve = [
        pokemon for pokemon in roster if pokemon is not battle.opponent.active
    ]


def find_best_move(battle: Battle, include_analysis=False, opponent_model=None):
    battle = deepcopy(battle)
    if battle.team_preview:
        battle.user.active = battle.user.reserve.pop(0)
        battle.opponent.active = battle.opponent.reserve.pop(0)

    # Constrain sampling with model hints instead of replacing a completed
    # sampled moveset (which could erase revealed phazing/recovery moves).
    if opponent_model:
        _apply_opponent_model(battle, opponent_model)

    num_battles, search_time_per_battle = battle.mode.search_params(battle)
    selector = "visits" if battle.team_preview else FoulPlayConfig.search_selector
    # Only standard formats reuse teams across rematches.
    battle.opponent_memory = (
        recall(battle)
        if battle.mode.name == constants.BattleType.STANDARD_BATTLE
        else {}
    )
    in_time_pressure = battle.time_remaining is not None and battle.time_remaining <= 60
    if not in_time_pressure:
        num_battles = max(2, num_battles)
    battles = []
    mcts_results = []
    search_states = []

    logger.info("Searching for a move using MCTS...")
    logger.info(
        "Sampling {} battles at {}ms each".format(num_battles, search_time_per_battle)
    )
    with ProcessPoolExecutor(max_workers=FoulPlayConfig.parallelism) as executor:
        # At most three batches, using new states each time. Under clock pressure
        # retain the mode's original budget.
        for batch in range(1 if in_time_pressure else 3):
            new_battles = battle.mode.prepare_battles(battle, num_battles)
            # Preserve the mode's within-batch probabilities (e.g. weighted
            # random-battle scenarios), with equal weight for each fresh batch.
            batch_total = sum(chance for _, chance in new_battles)
            if not new_battles or batch_total <= 0:
                raise ValueError("No opponent scenarios available for search")
            scale = batch / (batch + 1)
            battles = [(b, chance * scale) for b, chance in battles]
            mcts_results = [(res, chance * scale, i) for res, chance, i in mcts_results]
            futures = []
            for b, chance in new_battles:
                chance = chance / batch_total / (batch + 1)
                index = len(battles)
                battles.append((b, chance))
                state = battle_to_poke_engine_state(b).to_string()
                search_states.append(state)
                futures.append(
                    (
                        executor.submit(
                            get_result_from_mcts,
                            state,
                            search_time_per_battle,
                            index,
                            FoulPlayConfig.search_threads,
                            **({"selector": "matrix"} if selector == "matrix" else {}),
                        ),
                        chance,
                        index,
                    )
                )
            for future, chance, index in futures:
                result = future.result()
                if result.total_visits <= 0 or not any(
                    option.visits for option in result.side_one
                ):
                    raise ValueError("Search returned no visited actions")
                mcts_results.append((result, chance, index))
            if batch == 2 or in_time_pressure or not _samples_disagree(mcts_results):
                break
            if battle.format_spec.badnboosted:
                # Spend the remaining two batches' budget deepening the same
                # hypotheses, rather than restarting short trees on new guesses.
                # The native API cannot resume trees, so replace shallow results
                # with independent searches given twice the original duration.
                logger.info("BnB samples disagree; deepening existing scenarios")
                futures = [
                    (
                        executor.submit(
                            get_result_from_mcts,
                            search_states[index],
                            2 * search_time_per_battle,
                            index,
                            FoulPlayConfig.search_threads,
                            **({"selector": "matrix"} if selector == "matrix" else {}),
                        ),
                        chance,
                        index,
                    )
                    for _, chance, index in mcts_results
                ]
                refined_results = []
                for future, chance, index in futures:
                    result = future.result()
                    if result.total_visits <= 0 or not any(
                        option.visits for option in result.side_one
                    ):
                        raise ValueError("Search returned no visited actions")
                    refined_results.append((result, chance, index))
                mcts_results = refined_results
                break
            logger.info(
                "Opponent samples disagree; searching %s additional states", num_battles
            )

    choice = select_move_from_mcts_results(mcts_results)
    if selector == "matrix":
        from fp.search.payoff import select_matrix_action

        try:
            choice = select_matrix_action(mcts_results)
        except ValueError:
            logger.exception("Matrix solver failed; retaining MCTS visit choice")
    logger.info("Choice: {}".format(choice))
    if include_analysis:
        analysis = _summarize_search(mcts_results, battles, choice)
        analysis["rematch_profile"] = sorted(battle.opponent_memory)
        analysis["search_states"] = search_states
        analysis["sample_policies"] = [
            {
                "index": index,
                "weight": weight,
                "choices": [
                    {
                        "move": option.move_choice,
                        "visits": option.visits,
                        "score": option.total_score / option.visits
                        if option.visits
                        else None,
                    }
                    for option in result.side_one
                ],
            }
            for result, weight, index in mcts_results
        ]
        return choice, analysis
    return choice


def _samples_disagree(results):
    leaders = {
        max(res.side_one, key=lambda option: option.visits).move_choice
        for res, _, _ in results
    }
    policy = defaultdict(float)
    for res, weight, _ in results:
        for option in res.side_one:
            policy[option.move_choice] += weight * option.visits / res.total_visits
    ranked = sorted(policy.values(), reverse=True)
    return len(leaders) > 1 or (len(ranked) > 1 and ranked[0] - ranked[1] < 0.10)
