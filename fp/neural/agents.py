"""Policies receive own information and public observations, never private truth."""

from copy import deepcopy
import time

from poke_engine import (
    State,
    Side,
    SideConditions,
    root_info,
    MctsResult,
    seeded_search,
)
from fp.search.main import select_move_from_mcts_results
from fp.search.payoff import solve_worlds
from .environment import successors, outcome
from .observation import PublicLedger, UNKNOWN, observe, norm
from .teams import native_team


class RandomAgent:
    name = "random"

    def policy(self, decision, rng):
        return [float(i in decision.actions) / len(decision.actions) for i in range(15)]


class NeuralAgent:
    name = "neural"

    def __init__(self, evaluator):
        self.evaluator = evaluator

    def policy(self, decision, rng):
        return self.evaluator.evaluate([decision.observation])[0][0]


class BeliefSampler:
    """Uniform external candidates, never selected by matching private truth.

    Catalog support is not certainty. Observations retain unknown mass even if
    only one candidate exists. Missing support is a hard error, not an oracle.
    """

    def __init__(self, catalog):
        self.catalog = {}
        for p in catalog:
            self.catalog.setdefault(norm(p["species"]), []).append(p)

    def sample(self, decision, rng):
        team = []
        public = decision.observation["team"][6:]
        for p in public:
            candidates = self.catalog.get(p["species"], [])
            known_moves = {p[f"move{i}"] for i in range(4)} - {UNKNOWN}
            candidates = [
                c
                for c in candidates
                if (p["ability"] == UNKNOWN or norm(c["ability"]) == p["ability"])
                and (p["item"] == UNKNOWN or norm(c.get("item", "none")) == p["item"])
                and known_moves <= {norm(m) for m in c["moves"]}
            ]
            if not candidates:
                raise ValueError(
                    f"No belief support for {p['species']}; cannot consult private truth"
                )
            team.append(rng.choice(candidates))
        sampled = native_team(team)
        # Native objects are immutable: reconstruct HP/status snapshots via constructors.
        from poke_engine import Pokemon

        pokemon = []
        for p, shown in zip(sampled.pokemon, public):
            fields = {
                k: getattr(p, k)
                for k in (
                    "id",
                    "level",
                    "types",
                    "base_types",
                    "maxhp",
                    "ability",
                    "base_ability",
                    "item",
                    "nature",
                    "evs",
                    "attack",
                    "defense",
                    "special_attack",
                    "special_defense",
                    "speed",
                    "weight_kg",
                    "moves",
                    "tera_type",
                )
            }
            fields.update(
                hp=round(p.maxhp * shown["hp"]) if shown["hp_known"] else p.maxhp,
                status=shown["status"] if shown["status"] != UNKNOWN else "none",
            )
            pokemon.append(Pokemon(**fields))
        effects = dict(decision.observation["effects"])
        from .observation import BOOSTS, CONDITIONS

        boosts = {k: round(effects.get("opponent:" + k, 0) * 6) for k in BOOSTS}
        conditions = {k: int(effects.get("opponent:" + k, 0)) for k in CONDITIONS}
        for k in ("spikes", "toxic_spikes"):
            conditions[k] = round(effects.get("opponent:layers:" + k, 0) * 3)
        opponent = Side(
            pokemon=pokemon,
            active_index=str(next((i for i, p in enumerate(public) if p["active"]), 0)),
            side_conditions=SideConditions(**conditions),
            **boosts,
            volatile_statuses={
                k.split(":", 2)[2]
                for k in effects
                if k.startswith("opponent:volatile:")
            },
        )
        weather = next(k[8:] for k in effects if k.startswith("weather:"))
        terrain = next(k[8:] for k in effects if k.startswith("terrain:"))
        return State(
            side_one=decision.own,
            side_two=opponent,
            team_preview=decision.preview,
            weather=weather,
            weather_turns_remaining=5,
            terrain=terrain,
            terrain_turns_remaining=5,
            trick_room=bool(effects["trick_room"]),
            trick_room_turns_remaining=3,
            terastallization_allowed=bool(effects["tera_allowed"]),
        )


class HeuristicAgent:
    """Original native MCTS and Foul Play visit aggregator over belief samples.

    This controlled baseline is named explicitly: it is not the full live agent's
    adaptive sampler, memory and wall-clock scheduling.
    """

    name = "foulplay-search"

    def __init__(self, sampler, simulations=128, worlds=2):
        self.sampler, self.simulations, self.worlds = sampler, simulations, worlds
        self.search_seconds = self.visits = 0

    def policy(self, decision, rng):
        start = time.perf_counter()
        results = []
        for i in range(self.worlds):
            state = self.sampler.sample(decision, rng)
            native, _, _, _ = seeded_search(
                state, 1, self.simulations, rng.getrandbits(64)
            )
            result = MctsResult._from_rust(native)
            self.visits += result.total_visits
            results.append((result, 1 / self.worlds, i))
        choice = select_move_from_mcts_results(results)
        self.search_seconds += time.perf_counter() - start
        if choice not in decision.actions.values():
            # Sampled hidden traps can remove actions, but cannot authorize an illegal move.
            policy = [
                sum(
                    w
                    * next(
                        (o.visits for o in result.side_one if o.move_choice == action),
                        0,
                    )
                    / max(1, result.total_visits)
                    for result, w, _ in results
                )
                if (action := decision.actions.get(i))
                else 0.0
                for i in range(15)
            ]
            total = sum(policy)
            return (
                [p / total for p in policy]
                if total
                else RandomAgent().policy(decision, rng)
            )
        return [
            float(action == choice)
            for i in range(15)
            for action in [decision.actions.get(i)]
        ]


class SearchAgent:
    """Batched one-ply simultaneous belief search with neural leaf values."""

    name = "neural-search"

    def __init__(self, evaluator, sampler, worlds=2, mix=1.0):
        if worlds < 1 or not 0 <= mix <= 1:
            raise ValueError("Invalid search configuration")
        self.evaluator, self.sampler, self.worlds, self.mix = (
            evaluator,
            sampler,
            worlds,
            mix,
        )
        self.search_seconds = self.positions = 0

    def policy(self, decision, rng):
        start = time.perf_counter()
        actions = list(decision.actions.values())
        matrices, jobs, locations = [], [], []
        for world in range(self.worlds):
            state = self.sampler.sample(decision, rng)
            own_legal, opposing = root_info(state)[:2]
            # The true request is authoritative. A sampled trap cannot delete an
            # actually legal option; incompatible worlds are rejected, never repaired using truth.
            if any(a not in own_legal for a in actions):
                return NeuralAgent(self.evaluator).policy(decision, rng)
            matrices.append([[0.0] * len(opposing) for _ in actions])
            for i, a in enumerate(actions):
                for j, b in enumerate(opposing):
                    jobs.append((state, a, b))
                    locations.append((world, i, j))
        leaves, pending = [], []
        for location, branches in zip(locations, successors(jobs)):
            world, i, j = location
            for state, percentage in branches:
                result, termination = outcome(state)
                if termination == "terminal":
                    matrices[world][i][j] += percentage / 100 * result
                else:
                    ledger = PublicLedger(deepcopy(decision.observation["team"][6:]))
                    ledger.see_active(state.side_two)
                    legal = root_info(state)[0]
                    leaves.append(
                        observe(state.side_one, state.side_two, ledger, state, legal)
                    )
                    pending.append((world, i, j, percentage / 100))
        _, values = self.evaluator.evaluate(leaves)
        for (world, i, j, probability), value in zip(pending, values):
            matrices[world][i][j] += probability * value
        solution = solve_worlds(matrices, [1.0] * self.worlds)
        prior = self.evaluator.evaluate([decision.observation])[0][0]
        policy = [(1 - self.mix) * p for p in prior]
        for index, probability in zip(decision.actions, solution.policy):
            policy[index] += self.mix * probability
        self.positions += len(leaves)
        self.search_seconds += time.perf_counter() - start
        return policy
