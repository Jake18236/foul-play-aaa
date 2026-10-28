# Reproducible selector benchmark

This is an offline development benchmark of the existing native engine. It does
not contact a ladder, use a replay's future RNG stream, train a model, or change
the userscript. Current production selection remains `visits`.

## Baseline and inputs

`baseline/source.tar.gz` and `baseline/manifest.json` freeze the dirty source tree
and native package identity **before** this implementation. The baseline engine
was `0.0.48+foulplay.1`. The archive includes the build script needed to reconstruct
that package. Do not overwrite this snapshot when rerunning a benchmark.

The comparison uses `0.0.48+foulplay.3`: the same simulator, heuristic evaluator,
UCB selection, and backups, with seeded RNG, exact iteration counts, joint root
statistics, and more frequent timed stopping checks. The baseline action rule is
the existing most-visited action. Both rules consume the **same seeded tree**,
not separate searches with accidentally different RNG or sampling.

`states.json` contains ten constructed full-information BnB positions covering
Unaware, setup, phazing, hazards, and short endgames. These are explicit assumed
sets, not reconstructed private sets from the supplied replay. Every fixture move
has a Gen 9 source recorded in `learnset-contract.json`, including pre-evolution
inheritance. That contract is a move-availability check, not a complete Showdown
format validator. Initial exploratory fixtures are retained as `states-v1.json`;
they included an unsupported Ribombee Roost and are superseded by `states.json`.

The live sampler is deliberately excluded from selector comparisons so changes to
beliefs cannot masquerade as improvements to action selection. Its failures have
a separate isolated baseline comparison in `sampling.py`.

## Run

From the repository root, using the configured virtual environment:

```sh
venv/bin/python scripts/install_poke_engine.py
venv/bin/python -m benchmarks.compare --iterations 20000 --seeds 8 --games --max-turns 80 --output benchmarks/results/paired-v2.json
venv/bin/python -m benchmarks.compare --iterations 10000 --seeds 3 --games --opponent attack --max-turns 60 --output benchmarks/results/attack-v2.json
venv/bin/python -m benchmarks.compare --iterations 0 --budget-ms 200 --seeds 4 --output benchmarks/results/timed-200ms.json
venv/bin/python -m benchmarks.sampling
venv/bin/python -m benchmarks.summarize benchmarks/results/paired-v2.json
```

Fixed-iteration decisions, policies, and game histories reproduce for a given
fixture, seed, package build, and platform. Timings do not. Reports record source
hashes, fixture hash, native binary hash, Python/platform, seeds, requested and
actual iterations, joint visits, and a decision-content hash that excludes timing.
Tests repeat both decisions and short games to verify deterministic execution.

In timed mode, both selectors get the same allocated decision cap. The harness
reserves 20% for tree destruction, native/Python conversion, and selection. Actual
latency and overruns are recorded rather than assuming the native duration
includes cleanup. There is no hard real-time guarantee under arbitrary load.
Run timed measurements without simultaneous benchmark/test jobs.

## Experimental action rule

The native API enumerates each legal root action pair and computes a one-turn
chance-weighted heuristic payoff. Joint tree returns then update these cells;
one prior observation regularizes each empirical mean. An unvisited pair retains
the explicitly computed shallow payoff, and reports expose that fraction. This
does not guarantee deep coverage or calibrated cell values.

A dependency-free primal/dual simplex solver computes mixed zero-sum strategies
and checks the best-response gap. For multiple sampled worlds, a response-profile
constraint generator finds **one own policy** shared across the worlds; the
opponent can respond according to its own hypothesized set. It does not take a
different best action in every unknowable world, nor let the opponent see our
sampled simultaneous action. Deeper information-set leakage/strategy fusion in
the underlying full-state searches is not solved here.

Autonomous experimental play samples from the mixed policy. Picking its largest
component would lose its equilibrium protection. The policy gap applies to the
estimated root matrix, not to the true partially observed Pokémon game. The
original heuristic remains in use: no learned critic or opponent policy is
claimed. This experiment isolates the final action rule.

Enable explicitly with `--search-selector matrix` for the bot or:

```sh
FOUL_PLAY_SEARCH_SELECTOR=matrix venv/bin/python advisor_server.py
```

This requires the patched engine and one search thread. Team preview retains
legacy selection. Solver failures retain the visit-based choice. Production
defaults remain unchanged until a candidate demonstrates a strength gain.

## Arena interpretation

The head-to-head arena uses native legal actions and chance branches. Each initial
state and seed is played with both selector/side assignments. Chance and action
RNGs are separate. A seed does not force identical events after policies diverge.
Forced replacement/pivot stages are resolved through native action generation;
the reported step count includes those stages, not just ordinary full turns.

The attack opponent chooses a damaging non-pivot move by worst-response
one-turn value whenever one is available. It switches only if forced or no such
move exists. Both candidates face the same fixed opponent and paired starts.

Terminal native outcomes determine wins. Turn-capped games receive half a point
and are reported separately; they are never adjudicated using a candidate's own
heuristic. Errors abort a run rather than silently dropping losing games. Full
histories are retained for inspection. The bootstrap clusters by constructed
case; the cases are related development examples, so its interval is **not** a
ladder-strength confidence interval or a substitute for a sealed team pool.

This first benchmark does not yet provide a pinned local Showdown cross-check,
hidden-information full games, or a representative held-out BnB team population.
Those remain prerequisites for claims about live playing strength. A smaller
estimated matrix exploitability alone is not a strength result.

## Sampling repairs measured separately

`sampling.py` extracts the frozen source to a temporary directory and executes
the same six cases against that source and current source in separate processes.
It injects identical datasets and uses a two-second timeout to expose a genuine
zero-rate loop. No network data is used.

The baseline fails all six cases: confirmed stats/HP overwritten, contradicted
traits restored, missing-usage crash, zero-rate hang, more than four moves, and
fallback trait combinations that contradict revelations. The current source
passes all six. Additional tests cover consumed items, known moves and PP/disabled
flags, Tera, transformed stats, inferred speed constraints, missing teammate
counts, and explicit rejection of an opponent with zero usable moves.

These are safety/correctness improvements, not a measured win-rate improvement
from sampling. Marginal priors remain approximate and OU fallback remains a
format-mismatch risk; a persistent calibrated BnB posterior is future work.
