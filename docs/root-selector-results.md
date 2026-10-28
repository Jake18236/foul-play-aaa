# Root payoff selector implementation and benchmark results

Date: 2026-10-22. Production default: **visit-based MCTS remains enabled**.

## Implemented

- Frozen source baseline and engine identity in [the baseline manifest](../benchmarks/baseline/manifest.json), with a source archive that includes uncommitted work.
- Patched engine `0.0.48+foulplay.3`: seeded search, exact iteration budgets, joint root visits/returns, one-turn chance-weighted payoff priors, and more frequent timed stopping checks. The UCB and value-backup rules are retained.
- [Experimental matrix selection](../fp/search/payoff.py): primal/dual simplex with a best-response-gap check; one policy shared across sampled worlds; mixed-policy action sampling. Unvisited cells use explicit shallow priors. Current heuristic evaluation is retained to isolate final selection.
- An offline [paired comparison and native arena](../benchmarks/compare.py), with source/input/native hashes, separate simulation and action seeds, histories, CPU/wall timings, and deterministic decision hashes.
- Sampling repairs: bounded finite-weight move completion, four-move limit, missing-usage handling, observed-trait repair, preserved known stats/HP/moves/PP/Tera/items, robust missing teammate counts, and rejection of zero-move opponent simulations.

The userscript is unchanged from the frozen baseline. Matrix selection is opt-in
through `--search-selector matrix` or `FOUL_PLAY_SEARCH_SELECTOR=matrix` for the
advisor. It requires one search thread. Preview and solver-failure paths retain
visit selection. Restart existing advisor processes to load engine/sampler changes.

## Measured results

Final comparisons use ten constructed Gen 9 BnB fixtures in [states.json](../benchmarks/states.json).
All moves have recorded Gen 9 learnset sources. These are full-information assumed
sets, not recovered private replay sets or a held-out ladder team population.

| Experiment | Visit selection | Matrix selection | Interpretation |
| --- | --- | --- | --- |
| 160 paired head-to-head games; 20,000 simulations per decision; 8 seeds and both side assignments | 48.75% | 51.25% | No demonstrated substantial gain. All 160 games reached native terminal outcomes. |
| Fixed stay-and-attack stress opponent; 10,000 simulations; 60 games per selector | 78.33% | 75.00% | Matrix did not improve this stress slice. Six of the combined 120 games reached the step cap and were scored as draws. |
| 40 paired decisions, 200 ms allocated total cap | Same tree and allocated cap | p95 191.45 ms; 0 observed overruns | Headroom includes native cleanup and selector overhead. |
| 40 paired decisions, 50 ms allocated total cap | Same tree and allocated cap | p95 43.75 ms; 0 observed overruns | Machine-specific latency result, not a hard real-time guarantee. |

The case-cluster bootstrap interval for matrix's head-to-head score is
**47.5%–55.0%**. Related development fixtures are not independent ladder samples;
this interval describes the development collection only. The planned promotion
gate (at least 55%, lower confidence bound above 50%, and no stress regression)
was not met. We therefore do not change the default selector.

Both final selectors consume the identical seeded MCTS tree. Visit selection
chooses the existing most-visited action; matrix selection uses the joint returns
and explicit one-turn priors from that same computation. Fixed-iteration results
isolate selector behavior; separate timed runs measure its real overhead.

Raw evidence:

- [Head-to-head positions and complete game histories](../benchmarks/results/paired-v2.json)
- [Attack-opponent comparison](../benchmarks/results/attack-v2.json)
- [200 ms timing](../benchmarks/results/timed-v2-200ms.json)
- [50 ms timing](../benchmarks/results/timed-v2-50ms.json)
- [Frozen-source versus repaired sampling cases](../benchmarks/results/sampling.json)

## Safety and reproducibility checks

All six isolated sampling regressions fail against the original frozen source and
pass against current source. The original cases include an actual zero-rate hang
and missing-usage exception, in addition to contradictory or oversized guesses.
They use identical injected datasets and no live network sources.

Automated tests repeat seeded native results, verify exact simulation counts and
joint/marginal accounting, preserve input states, replay identical game histories,
verify known matrix games and best-response bounds, prevent hidden-world-specific
own choices, check the real process-pool integration, and check fixture move sources.
The full suite passes **824 tests**. Lint and whitespace checks pass.

An initial fixture incorrectly included Ribombee Roost, a past-generation move.
It was corrected to Substitute before final comparisons. Earlier v1 exploratory
scores are superseded by the v2 reports above. Timing tests also exposed that
native duration excludes tree destruction; the harness now reserves 20% for
cleanup/conversion/selection and reports actual overruns. These changes were not
chosen to increase the matrix selector's game score.

## What the results justify next

The benchmark and sampler safety repairs are useful now. Matrix action selection
is a functioning experiment, not a proven playing-strength upgrade. Its values
remain noisy, root-relative heuristic returns, and shallow priors can determine
poorly explored replies. It does not learn this opponent's behavior or replace the
native evaluator.

The next strength experiments should improve the values and beliefs under the
same frozen selector comparison: matchup-aware evaluation, a coherent BnB set
posterior, and a calibrated opponent action model. A local Showdown differential
oracle, hidden-information arena, and broader sealed team pool are still required
before claiming live BnB gains. No model weights or UI work were added here.

See [benchmark instructions and limits](../benchmarks/README.md) for exact commands.
