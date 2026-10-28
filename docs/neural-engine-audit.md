# Neural engine audit

Audit date: 2026-10-28. Target: Gen 9 Almost Any Ability, offline training.

## Reusable components

- `fp/battle/state.py` and `fp/search/poke_engine_helpers.py` construct native teams, stats, moves, side conditions and field state.
- Installed engine: `0.0.48+foulplay.5`. `scripts/install_poke_engine.py` pins and checksums the upstream source and applies local Rust patches.
- `root_info(State)` returns both legal action lists, a heuristic evaluation and terminal sign. `generate_instructions(State, a, b)` returns weighted chance branches; `State.apply_instructions(branch)` returns the successor. Actions must be chosen simultaneously, before either is applied. Pivot/forced replacement requests are additional decision steps.
- `seeded_search` exposes seeded MCTS visit counts and joint payoffs. Unfinished positions use a sigmoid of **heuristic change from the root**. These scores are not win probabilities or value training labels.
- `fp/search/main.py` is the original Foul Play agent, including opponent sampling and visit aggregation. Keep it unchanged as the production default.
- `fp/search/payoff.py` already solves simultaneous zero-sum root games.
- Cached AAA Showdown sets, full sets and usage data exist locally. They are candidate sources, not legality certificates or probability ground truth.

## Constraints and acceptance gates

- AAA's existing format patch disables Terastallization. The action representation must support Tera for other rulesets but never enable it in AAA.
- Native state transitions have no public battle-event stream. Private truth must remain in the environment; policy observations need a separate ledger. Unknown abilities/items remain unknown unless an explicit public event proves them. Native state diffs are not a license to reveal private sets or passive bench recovery.
- Native simulation is a search model, not a certified replacement for Showdown. Entry effects, pivot continuation, PP, damage distributions, trapping, simultaneous faints, Transform/Illusion and all supported AAA abilities require differential testing. Unsupported mechanics must not be silently advertised as legal, faithful training coverage.
- Use a pinned offline Showdown TeamValidator to validate entire teams (not just individual sets), record its source digest and reject teams with errors. A candidate dataset alone cannot establish legality.
- Split by canonical team/set identity before games; keep complete battles together. Hold out abilities explicitly for an ability-generalization suite. Do not label turn caps as draws or use censored games for terminal value supervision.
- Python callbacks at every native MCTS node would be prohibitively expensive. Start with a batch of native-generated root successors, evaluate them together, and solve a simultaneous root game. Keep native heuristic MCTS selectable. This is a shallow neural-guided search, not a claim that native internal MCTS leaves have been replaced.
- At audit time Python execution needed sandbox approval, PyTorch was absent (subsequently installed), and `/usr/bin/git` reported missing Xcode command-line tools. Rust rebuild availability must be checked independently.

## Implementation order

Observation boundary and leakage tests → float32 policy/value model → offline trajectories with explicit termination → supervised value fit → iterative self-play/checkpoints → batched neural root evaluation → paired benchmarks. Checkpoints and smoke metrics demonstrate execution only, never superiority.
