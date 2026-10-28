# Foul Play strength plan: Gen 9 Bad ’n Boosted

Research date: 2026-10-22. This is a proposal, not a strength result. No search,
training, or UI changes are part of this planning step.

## Recommendation

Keep poke-engine's fast battle simulation. Build, in order: a reproducible strength
harness; explicit opponent-response comparisons; a coherent posterior over hidden
sets; and a trained, matchup-aware value function. Use small, controlled ablations
before changing defaults. Extra search time is a comparator, not the main strategy.

The first experiment should expose a root joint-action payoff table using the
existing evaluator. It tests whether explicitly considering staying in, attacking,
recovering, phazing, and switching improves decisions before a neural model exists.
Sampling instrumentation starts immediately; the reliable sampler lands before
joint-response search becomes a production default.

## What PokeCritic actually documents

The relevant project is MintOcha's Random Battles replay analyzer, rather than
similarly named team-rating applications. The author's [release discussion][release]
describes a compact learned evaluator, CPU inference, a depth-one equilibrium,
one sampled RNG outcome, and replay reconstruction using information unavailable
during live play. Its reported calibration error is an author claim without a
published evaluation protocol in the materials inspected; it is not our benchmark.

I inspected public repository snapshot
`14eccd10d7a4d3601d4028c0fea2e02238a5129a`:

- [Analysis worker][worker]: enumerates legal joint actions, simulates one turn,
  evaluates resulting positions, and handles terminal outcomes directly. Review
  uses one fixed RNG seed. Its displayed continuations repeatedly solve new local
  positions; that is not exhaustive search to the end or a backed-up deep tree.
- [Matrix solver][matrix]: solves mixed zero-sum strategies with linear programming
  and reports a best-response gap. It also calculates the safest *pure* action.
  The worker defaults to safety scoring. Those are different decision rules.
- [Export pipeline][export]: loads an outcome critic from an external PokemonBot
  project and exports batched ONNX inference. The inspected export uses a
  13-by-139 observation and distinguishes oracle/private-state encoding from
  masked observations. It does not publish a complete training recipe.

The repository lists deployed ONNX artifacts. This plan neither assumes reusable
weights nor depends on them: licensing, training provenance, information contract,
and BnB applicability would all need separate verification. We will train our own
model. I could not retrieve the linked Smogon thread (HTTP 403); the source and
author's accessible release discussion are the evidence here.

Transfer the idea of evaluating joint-action afterstates with a cheap learned
value function. Do not transfer hindsight knowledge, a known future RNG stream,
single-outcome rankings, or an untested Random Battles value model into live BnB.

## Comparison with this checkout

| Area | Existing implementation | Consequence and proposed experiment |
| --- | --- | --- |
| Simultaneous moves | Native MCTS selects both sides' actions using separate UCB statistics, then resolves their joint transition. | Simultaneity already exists. Test explicit root matrices and regret-based selection rather than describing this as adding opponent turns. |
| Leaf value | The installed 0.0.48 source uses fixed material/status/boost/hazard terms; unfinished leaves return a sigmoid of change from the root evaluation. | The return is not an absolute calibrated win probability. A learned terminal-outcome critic must replace that leaf contract, not be inserted inside a second sigmoid of root-relative differences. |
| Final choice | [Search aggregation](../fp/search/main.py#L19) averages per-world visit distributions and selects their largest component. | This is not a certified equilibrium or an explicit best response to a learned opponent policy. Measure exploitability on small games and test sampling from a mixed policy. |
| Opponent modeling | [Memory](../fp/opponent_memory.py#L160) retains revealed sets from compatible rematches; [sampling](../fp/search/standard_battles.py#L304) stratifies prior-informed and general worlds. | It does not learn action tendencies. Native opponent search frequencies are not a calibrated forecast of what this person will do. |
| Full-set priors | [BnB format routing](../fp/format_spec.py#L45) uses OU full sets. [Full-set sampling](../fp/search/standard_battles.py#L170) is uniform within a source, with a hand-set 75% preference when moves are known. | Source counts are explicitly not treated as representative frequencies. Use labeled, format-specific priors and measure their reliability. |
| Marginal fallback | [Smogon traits](../fp/data/sets/smogon.py#L279) use products of independent ability/item/spread/Tera frequencies; moves are filled separately. | Correlations and team structure can be lost. Prefer joint sets; mark marginal repairs as uncertain. |
| Contradictory evidence | [Trait fallback](../fp/data/sets/smogon.py#L322) can relax ability/item/Tera/speed checks. [Damage filtering](../fp/battle/inference.py#L425) can retain every hypothesis rather than empty a source. | Avoid an empty posterior by repairing unknown details, not silently restoring inconsistent candidates. Confirmed facts and uncertain deductions need different treatment. |
| Search budget | [Advisor configuration](../advisor_server.py#L181) uses 6,000 ms per state. BnB disagreement now repeats the same sampled states at double duration. | Compare actual total decision budgets, not per-state settings. The tree is restarted rather than resumed. |

The native comparison was checked against the pinned 0.0.48 source archive used by
[our installer](../scripts/install_poke_engine.py), not inferred from upstream main.
Upstream [MCTS][mcts] and [evaluation][evaluate] are useful references but may drift.
The installed Unaware critical-hit fix changes mechanics, not these search/value
limitations. Existing correctness tests do not establish stronger play.

## Phase 0 — Freeze the baseline and build the measuring tools

**Deliverables:** a headless local Showdown arena, a tactical-state suite, complete
decision traces, and reproducible experiment manifests. Suggested locations:
`benchmarks/`, `fp/search/response_model.py`, `fp/data/sets/belief.py`, and later
`fp/evaluation/`. These are proposed new modules.

Freeze the entire current dirty checkout, not just HEAD
`703ca5c07312b4b3eb1ecdd9889559d665a48764`. Store a patch/content manifest, engine
version and source hash, Showdown revision and BnB rules, dataset bytes/dates/hashes,
configuration, machine, and worker/thread counts. Primary baseline B0 is current
Foul Play plus `0.0.48+foulplay.1`; upstream 0.0.48 is an optional diagnostic
ablation, never the sole opponent used to claim improvement.

Extend traces with observation history, own private request, sampled sets and
weights, source and fallback reasons, joint-action statistics, chosen distribution,
RNG seeds, CPU time, latency, transitions, and evaluation revision. Keep arena
ground truth in a separate evaluator-only record. Current traces preserve states
and per-world marginal policies but lack several of these fields.

Add a native seeded-search API. A Python random seed alone cannot reproduce the
Rust search or multithread scheduling. Use one worker/thread and fixed iterations
for diagnostic reproducibility; use timed runs and repeated independent seeds for
real performance comparisons. Couple simulator randomness where useful, without
assuming the same seed produces identical events after actions alter RNG consumption.

Start with 50 explicit tactical positions and 10,000 joint transitions checked
against pinned Showdown, conditional on matching chance outcomes. Cover BnB stat
boundaries, Unaware/criticals/Stored Power, forced and pivot switches, phazing,
hazard-entry deaths, item loss, healing, weather, and Tera. Require no unexplained
state or legal-action mismatches before strength testing.

Replay 2703189072 is a development case. Its private EVs and the original advisor
recommendations are unavailable. Use labeled constructed variants of its pivotal
positions; do not call those exact reconstructions or put them in a sealed test set.

## Phase 1 — Explicit opponent responses, with current evaluation held fixed

**Deliverables:** a root-action enumerator, stochastic afterstate matrix, opponent
action predictor, and a configurable response solver. Extend native bindings to
expose legal actions, joint root statistics, branch probabilities, and seeded search.
Resolve forced replacement and pivot choices as separate decision stages.

For each own action, evaluate legal opposing attacks, setup, recovery, disruption,
phazing, and switches. Use native transitions rather than duplicating move logic.
Ensure every legal root action pair gets initial coverage in diagnostic matrices;
under a deadline, mark missing cells as unknown and fall back rather than treating
unvisited replies as safe. Allocate extra samples to uncertain, decision-relevant
cells. Enumerate chance outcomes where affordable; otherwise use multiple sampled
outcomes and uncertainty intervals. Include critical and secondary-effect tails.

Train a lightweight opponent-policy baseline from prefix-only observations, then
add a small, context-dependent online update. Separate voluntary switches from
forced replacements and pivots. Features include matchup, health, boosts, hazards,
revealed recovery/phazing, past behavior, and feasible sets. Back off to a population
prior when evidence is thin; do not conclude that one stay means “never switches.”
An opponent predictor must never see our currently selected simultaneous action
or our unrevealed private information.

Evaluate three decision rules independently:

1. Mixed zero-sum equilibrium as a robust reference.
2. Best response to the learned action distribution.
3. A tunable combination of predicted-opponent value and worst-response value,
   reducing exploitative weight when prediction uncertainty is high.

Optimize a *single* own root policy across hidden-set worlds. Opposing policies
can depend on their own private set and observations, but not on our hidden data.
Do not solve each world independently and assume we can choose different actions
without knowing which world is real. A shared root policy only partly mitigates
this “strategy fusion”; deeper information-set handling remains future work.

Do not execute an equilibrium by always taking its largest-probability action:
that discards its protection. In autonomous play, test sampling its mixed strategy.
In manual-advisor evaluation, explicitly score the deterministic recommendation
rule separately. No new UI is required.

Start with root afterstate evaluation and bounded continuations. Later compare
regret matching/Exp3 with native decoupled UCB under the same budget. [Simultaneous
MCTS research][sm-mcts] supports testing regret-based methods under specific
assumptions; it does not prove superiority or equilibrium convergence for our
partially observed Pokémon implementation.

**Gate:** exact small matrix games have normalized strategies and best-response
gap below 1e-6; held-out opponent action log loss improves by at least 10% over a
context-free prior; no more than a 3-percentage-point score regression against the
switch-punishing opponent slice. Confirm gains in games before promotion.

## Phase 2 — Reliable hidden-set beliefs

**Deliverables:** a weighted persistent particle belief updated after observations,
with coherent full sets and explicit source confidence.

- Separate confirmed observations from inferred restrictions. Preserve revealed
  moves, consumed items, current abilities, Tera, HP intervals, and PP exactly.
  Account for ability changes, Transform, and Illusion rather than assuming a
  revealed trait is always the original set trait.
- Give damage, speed, item, and ability evidence appropriate likelihoods. Damage
  percentages are rounded; unknown rolls and missing contexts must be marginalized,
  not treated as exact truth. Old heuristic deductions remain reversible.
- Prefer joint BnB sets and team correlations. Use OU only as an explicitly
  discounted, legal, broad fallback; audit data staleness and changed format rules.
  Marginal statistics alone cannot recover missing joint correlations.
- If every candidate contradicts hard evidence, synthesize compatible unknown
  components around the observations. Log the repair and uncertainty. Never
  silently overwrite a known fact to fit the dataset.
- Sample adaptively from 8/16/32/64-particle experiments using effective sample
  size and decision sensitivity. More worlds need not each run a full 6-second
  search. Carry posterior weights into the common root decision.

**Gate:** zero confirmed-fact or legality violations in controlled tests; at least
15% lower held-out predictive log loss for subsequent revelations than the current
sampler; stable decisions as particle count rises. Measure full-set top-k coverage
only in controlled games with known hidden teams. In public replays, unrevealed
moves are censored, not evidence that a Pokémon lacks them. Freeze source snapshots
by date so test information cannot enter priors.

Phase 1 and Phase 2 form a joint belief/action model: whether a player stays depends
on its set, and its actions provide noisy evidence about that set. Start with
separate models for attribution, then test that coupling explicitly.

## Phase 3 — Matchup-aware value, then our own learned critic

**Deliverables:** an evaluator interface, a transparent feature baseline, a training
dataset, and a compact trained critic with an explicit information contract.

First test features tied to the observed failures: actual damage and speed
matchups; reachable KOs; surviving safe entries; boots and remaining hazard targets;
recovery and phazing denial; status susceptibility; and preservation of unique
checks, removal, Taunt, Knock Off, or setup opportunities. A boost is valuable for
what it enables. Avoid arbitrary permanent “utility Pokémon bonuses” and blanket
setup bans: Stored Power and useful speed boosts can matter into Unaware.

Fit a small linear/MLP model before considering a transformer. Encode actual BnB
stats, moves, types, abilities, items, field effects, health, and matchup interactions;
species identity alone will not represent modified formats reliably. Use a
perspective-symmetric encoder and explicit terminal win/draw/loss values.

Generate an initial 10,000 complete local games using diverse BnB teams and a frozen
mixture of policies: B0, stay-and-attack, switch-heavy, stall/phazing, and randomized
opponents. Track learning curves before scaling data generation or model size.
Use real terminal outcomes and tested TD/bootstrapped targets; avoid training a
model solely to copy the flawed current heuristic.

A full-state critic can train on controlled self-play states and evaluate sampled
worlds during search. A public-information critic must train on prefix-only
observations. Keep those datasets/contracts separate. Public replays provide
supplementary public-information outcome/action labels, not complete private sets
recovered from future revelations. Split by game, team/archetype, player where
possible, and time; never randomly split turns from the same match across sets.

Initialize integration as batched root/afterstate reranking with an existing-engine
fallback. Only move inference into native leaf evaluation after measured gains.
Avoid a Python/ONNX call at every Rust node: prototype batching and caching, then
use native inference or an efficient exported evaluator if throughput warrants it.
For learned leaves, use the model's calibrated terminal-outcome value directly.
Retain the old root-relative heuristic as a separate ablation, not an extra sigmoid.

**Gate:** at least 10% relative Brier improvement over a separately calibrated
heuristic baseline, expected calibration error <=0.05 on a sufficiently populated
held-out set, and positive closed-loop game results. Report reliability plots and
confidence intervals by archetype; a critic predicts outcomes under its data's
continuation policies, not an intrinsic probability of victory under perfect play.

## Benchmark contract and promotion gates

All thresholds below are proposed acceptance criteria, not measured improvements.

| Test | Design | Measurements / acceptance |
| --- | --- | --- |
| Mechanics and observations | 10,000 conditional joint transitions plus focused cases | Zero unexplained divergences and zero private-information leaks. |
| Tactical suite | 50 development cases initially; independently author at least 100 sealed cases | Continuation regret and failure rates; target >=50% reduction in specified failure modes, without requiring one human-preferred move when alternatives are equivalent. |
| Opponent prediction | Game/player/time-separated prefix data; attack/stay/switch/recover/phaze slices | Legal-action NLL, switch Brier/calibration, category coverage, adaptation speed and response to a behavior change. |
| Set belief | Known-team arena games plus censored public replay prefixes | Prediction NLL, hard violations, source/repair rates, effective sample size, full-set coverage where ground truth exists. |
| Value | Separate training/calibration/test games, clustered by game/team | Brier, log loss, calibration, perspective symmetry, terminal correctness, and downstream strength. |
| Full games | 200-game smoke test; then 1,000 paired team/side/seed matchups = 2,000 games per finalist comparison | Target >=55% score vs B0, with cluster-aware 95% lower confidence bound >50%; report draws as half a point and all failures/timeouts as losses. |
| Opponent pool | Fixed B0 snapshots, greedy attacker, stay-in punisher, switch-heavy player, stall/phazing, and adaptive exploiter | Target +10 percentage points vs the frozen pool; no slice >3 points worse; uncertainty may require more games. |
| Efficiency | Same hardware, workers and total decision deadline: 2, 6 and 12 seconds | Median/p95/p99 latency, CPU-seconds, transitions, model throughput, memory, timeout rate; p99 within cap and timeout rate <0.1%. |

Use team/side swaps, multiple search seeds, and independent matchup seeds. Paired
seeds reduce variance but do not force identical RNG events after divergent moves.
Bootstrap at the matchup/team cluster level; do not count correlated turns or
paired games as independent observations. Fix promotion metrics and the opponent
pool before the sealed run, and do not repeatedly tune against its results.

For tactical regret, use exact small endgames where feasible and independently
validated long-budget continuation references elsewhere. The current evaluator or
a critic's own score is not ground truth for its improvement. Public replays can
show bad outcomes but cannot assign counterfactual wins to every unplayed move.

Matched budgets include sampling, IPC, workers, evaluation, and refinement. The
present 6-second-per-world configuration can take roughly 12–36 seconds with two
worlds and one worker; benchmark it as a separate legacy configuration, alongside
deadline-limited B0 and new variants. Also report a compute-matched ablation.

The confirmatory arena is a material compute task: at 60 turns per game and six
CPU-seconds for each player's decision, 2,000 games cost roughly 400 core-hours
before overhead. Use smoke tests to eliminate weak candidates before confirming
finalists. These are planning quantities, not permission to launch training or
purchase compute.

## Ablation order and first implementation milestone

Run B0; B0 with more time; root mixed-response search alone; opponent adaptation
alone; coherent beliefs alone; improved feature value alone; learned value alone;
then combinations and the complete candidate. Use the same frozen mechanics and
data conditions for each. Keep a controlled full-information diagnostic to separate
belief failures from response/search failures, but use hidden-information games for
the primary strength claim. Recheck OU/AAA only as separate regression slices; do
not presume BnB gains transfer.

The first implementation milestone is Phase 0 plus a root response-matrix experiment
on constructed variants of the supplied replay. It should produce a reproducible
comparison of “opponent stays and attacks/phazes” versus “opponent switches,” with
RNG uncertainty and all model assumptions recorded. That is enough to evaluate the
first hypothesis before collecting a large training corpus.

No UI work is planned. The current userscript remains the client. Do not change
production defaults until a candidate clears the correctness, budget, and strength
gates. Expected win-rate gains remain hypotheses until those measurements exist.

[release]: https://www.reddit.com/r/CompetitivePokemon/comments/1wn7uxo/demo_release_pokecritic_pokemon_game_analysis/
[worker]: https://github.com/MintOcha/MintOcha.github.io/blob/14eccd10d7a4d3601d4028c0fea2e02238a5129a/src/analysis.worker.ts
[matrix]: https://github.com/MintOcha/MintOcha.github.io/blob/14eccd10d7a4d3601d4028c0fea2e02238a5129a/src/matrix.ts
[export]: https://github.com/MintOcha/MintOcha.github.io/blob/14eccd10d7a4d3601d4028c0fea2e02238a5129a/tools/export_critic.py
[mcts]: https://github.com/pmariglia/poke-engine/blob/main/src/mcts.rs
[evaluate]: https://github.com/pmariglia/poke-engine/blob/main/src/genx/evaluate.rs
[sm-mcts]: https://papers.nips.cc/paper_files/paper/2013/hash/1579779b98ce9edb98dd85606f2c119d-Abstract.html
