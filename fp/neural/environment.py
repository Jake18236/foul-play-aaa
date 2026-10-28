"""Seeded joint-action native environment; private truth stays in this module."""

from dataclasses import dataclass
import importlib.metadata
import math
import random

from poke_engine import State, root_info
from .observation import PublicLedger, action_map, observe
from .teams import native_team


def successors(jobs):
    import poke_engine

    if not hasattr(poke_engine, "neural_successors"):
        raise RuntimeError(
            "Offline self-play requires foulplay.6: run scripts/install_poke_engine.py"
        )
    normalized = [
        (
            state,
            "none" if a == "No Move" else a.removeprefix("switch "),
            "none" if b == "No Move" else b.removeprefix("switch "),
        )
        for state, a, b in jobs
    ]
    batches = poke_engine.neural_successors(normalized)
    for branches in batches:
        if (
            not branches
            or any(not math.isfinite(p) or p < 0 for _, p in branches)
            or not math.isclose(sum(p for _, p in branches), 100, abs_tol=0.01)
        ):
            raise ValueError("Invalid native chance distribution")
    return batches


def outcome(state):
    alive = [
        any(p.hp > 0 for p in side.pokemon) for side in (state.side_one, state.side_two)
    ]
    if not any(alive):
        # Native winner order cannot adjudicate simultaneous all-faints faithfully.
        return None, "ambiguous_double_ko"
    if not all(alive):
        return float(alive[0]), "terminal"
    return None, None


@dataclass
class Decision:
    observation: dict
    own: object
    actions: dict
    preview: bool


class NativeBattle:
    def __init__(self, first_team, second_team, seed=0, state=None):
        self.state = state or State(
            side_one=native_team(first_team),
            side_two=native_team(second_team),
            team_preview=True,
            terastallization_allowed=False,
        )
        self.random = random.Random(seed)
        self.step_number = 0
        self.ledgers = [
            PublicLedger.preview([p.id for p in side.pokemon])
            for side in (self.state.side_one, self.state.side_two)
        ]
        if not self.state.team_preview:
            for ledger, side in zip(
                self.ledgers, (self.state.side_one, self.state.side_two)
            ):
                ledger.see_active(side)

    def decisions(self):
        legal = root_info(self.state)[:2]
        sides = (self.state.side_one, self.state.side_two)
        return [
            Decision(
                observe(
                    sides[i],
                    sides[1 - i],
                    self.ledgers[1 - i],
                    self.state,
                    legal[i],
                    self.step_number,
                ),
                sides[i],
                action_map(sides[i], legal[i]),
                self.state.team_preview,
            )
            for i in range(2)
        ]

    def step(self, actions):
        legal = root_info(self.state)[:2]
        if any(a not in options for a, options in zip(actions, legal)):
            raise ValueError("Illegal action")
        branches = successors([(self.state, *actions)])[0]
        before = self.state
        self.state = self.random.choices(
            [s for s, _ in branches], weights=[p for _, p in branches]
        )[0]
        for ledger, old, new in zip(
            self.ledgers,
            (before.side_one, before.side_two),
            (self.state.side_one, self.state.side_two),
        ):
            # A fainted active's final public HP must persist after replacement.
            old_index = int(old.active_index)
            if new.pokemon[old_index].hp <= 0:
                ledger.team[old_index].update(hp=0.0, hp_known=1.0)
            ledger.see_active(new)
        self.step_number += 1


def play(first_team, second_team, agents, seed, max_steps=500):
    battle = NativeBattle(first_team, second_team, seed)
    records = []
    rngs = [random.Random(seed ^ 0x1731), random.Random(seed ^ 0x9919)]
    result, termination = None, None
    for step in range(max_steps):
        result, termination = outcome(battle.state)
        if termination:
            break
        decisions = battle.decisions()
        policies = [
            agent.policy(decision, rng)
            for agent, decision, rng in zip(agents, decisions, rngs)
        ]
        selected = []
        # Both policies have been computed before either action is selected/applied.
        for side, (decision, policy, rng) in enumerate(zip(decisions, policies, rngs)):
            if (
                len(policy) != 15
                or any(
                    not math.isfinite(p)
                    or p < 0
                    or (p > 0 and i not in decision.actions)
                    for i, p in enumerate(policy)
                )
                or not math.isclose(sum(policy), 1, abs_tol=1e-5)
            ):
                raise ValueError("Invalid masked policy")
            action = rng.choices(range(15), weights=policy)[0]
            selected.append(decision.actions[action])
            records.append(
                {
                    "step": step,
                    "side": side,
                    "observation": decision.observation,
                    "action": action,
                    "policy_target": policy,
                    "agent": agents[side].name,
                }
            )
        battle.step(selected)
    if termination is None:
        result, termination = outcome(battle.state)
    termination = termination or "step_cap"
    for record in records:
        record["outcome"] = (
            None if result is None else result if record["side"] == 0 else 1 - result
        )
    return {
        "seed": seed,
        "engine": importlib.metadata.version("poke-engine"),
        "backend": "native_search_model",
        "termination": termination,
        "outcome": result,
        "steps": battle.step_number,
        "records": records,
    }
