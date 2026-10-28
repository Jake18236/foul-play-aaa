# ruff: noqa: E402 -- optional torch dependency must be checked before imports
import pytest

torch = pytest.importorskip("torch")
from poke_engine import Move, Pokemon, Side, State, root_info, generate_instructions

from fp.neural.environment import NativeBattle, successors, outcome
from fp.neural.model import PolicyValueNet, Evaluator
from fp.neural.observation import PublicLedger, action_map, encode, observe
from fp.neural.training import check_separation


def state(
    ability="regenerator", item="leftovers", move="earthquake", hp=300, preview=False
):
    a = Pokemon(
        id="pikachu",
        hp=200,
        maxhp=200,
        types=("electric", "typeless"),
        ability="magicguard",
        moves=[Move(id="thunderbolt", pp=24)],
    )
    b = Pokemon(
        id="garchomp",
        hp=hp,
        maxhp=300,
        types=("dragon", "ground"),
        ability=ability,
        item=item,
        moves=[Move(id=move, pp=16)],
    )

    def side(p):
        return Side(
            pokemon=[
                p,
                Pokemon(
                    id="blissey", hp=300, maxhp=300, moves=[Move(id="seismictoss")]
                ),
            ]
            + [Pokemon.create_fainted() for _ in range(4)]
        )

    return State(
        side_one=side(a),
        side_two=side(b),
        terastallization_allowed=False,
        team_preview=preview,
    )


def observation(s, ledger=None):
    ledger = ledger or PublicLedger.preview([p.id for p in s.side_two.pokemon])
    ledger.see_active(s.side_two)
    return observe(s.side_one, s.side_two, ledger, s, root_info(s)[0])


def test_hidden_set_noninterference():
    # Hidden ability, item, moves, PP and stats must not alter the input.
    a = observation(state())
    b = observation(state("unaware", "choiceband", "dragonclaw"))
    assert a == b
    assert all(torch.equal(encode([a])[k], encode([b])[k]) for k in encode([a]))
    assert a["team"][6]["ability"] == "?"
    assert a["team"][6]["move0"] == "?"


def test_unknown_bench_hp_not_read():
    s = state()
    ledger = PublicLedger.preview([p.id for p in s.side_two.pokemon])
    result = observation(s, ledger)
    assert result["team"][7]["hp_known"] == 0
    assert result["team"][7]["hp"] == 0


def test_beliefs_never_certain():
    ledger = PublicLedger.preview(["pikachu"] * 6)
    with pytest.raises(ValueError):
        ledger.infer(0, "ability", {"regenerator": 1.0})
    ledger.infer(0, "ability", {"regenerator": 0.8})
    assert ledger.team[0]["ability"] == "?"
    ledger.reveal(0, "ability", "Regenerator")
    assert ledger.team[0]["ability"] == "regenerator"


def test_masked_float32_model_and_batch_equivalence():
    torch.manual_seed(42)
    obs = observation(state())
    model = PolicyValueNet()
    evaluator = Evaluator(model, "cpu", batch_size=4)
    p, v = evaluator.evaluate([obs, obs])
    assert p[0] == pytest.approx(p[1])
    assert all(p[0][i] == 0 for i, legal in enumerate(obs["legal"]) if not legal)
    assert sum(p[0]) == pytest.approx(1.0)
    assert 0 < v[0] < 1
    assert all(p.dtype == torch.float32 for p in model.parameters())
    logits, value = model(encode([obs]))
    (value.sum() + logits[0, 0]).backward()
    assert model.embedding.weight.grad.abs().sum() > 0


def test_native_offline_tracks_high_pp_without_changing_legacy_mode():
    s = state()
    legacy = s.apply_instructions(
        generate_instructions(s, "thunderbolt", "earthquake")[0]
    )
    assert legacy.side_one.pokemon[0].moves[0].pp == 24
    branches = successors([(s, "thunderbolt", "earthquake")])[0]
    assert sum(p for _, p in branches) == pytest.approx(100, abs=0.001)
    assert all(n.side_one.pokemon[0].moves[0].pp == 23 for n, _ in branches)
    assert s.side_one.pokemon[0].moves[0].pp == 24


def test_native_bulk_matches_individual_expansions():
    s = state()
    job = (s, "thunderbolt", "earthquake")
    a, b = successors([job, job])
    assert [(p.to_string(), w) for p, w in a] == [(p.to_string(), w) for p, w in b]


def test_aaa_masks_tera_and_preview_starts_without_residual_turn():
    s = state(preview=True)
    assert not any("tera" in a for a in root_info(s)[0])
    branches = successors([(s, "switch pikachu", "switch garchomp")])[0]
    assert all(not n.team_preview for n, _ in branches)
    assert all(n.side_one.pokemon[0].hp == 200 for n, _ in branches)
    assert all(n.side_two.pokemon[0].hp == 300 for n, _ in branches)


def test_joint_legality_and_terminal():
    s = state()
    with pytest.raises(ValueError):
        successors([(s, "thunderbolt-tera", "earthquake")])
    fainted = Side(pokemon=[Pokemon.create_fainted() for _ in range(6)])
    assert outcome(State(side_one=s.side_one, side_two=fainted)) == (1.0, "terminal")
    assert outcome(State(side_one=fainted, side_two=fainted)) == (
        None,
        "ambiguous_double_ko",
    )


def test_forced_switch_has_only_switch_actions():
    s = state(hp=0)
    legal = root_info(s)[1]
    mapping = action_map(s.side_two, legal)
    assert set(mapping) == {9}
    assert root_info(s)[0] == ["No Move"]


def test_seeded_environment_reproducible():
    a = NativeBattle(None, None, seed=23, state=state())
    b = NativeBattle(None, None, seed=23, state=state())
    a.step(["thunderbolt", "earthquake"])
    b.step(["thunderbolt", "earthquake"])
    assert a.state.to_string() == b.state.to_string()


def test_data_leakage_rejected():
    a = [{"id": "a", "teams": ["team1"], "sets": ["set1"], "manifest": "m"}]
    b = [{"id": "b", "teams": ["team2"], "sets": ["set1"], "manifest": "m"}]
    with pytest.raises(ValueError, match="sets leakage"):
        check_separation(a, b)
