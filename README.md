# Foul Play ![umbreon](https://play.pokemonshowdown.com/sprites/xyani/umbreon.gif)
A Pokémon battle-bot that can play battles on [Pokemon Showdown](https://pokemonshowdown.com/).

Foul Play can play single battles in all generations
though currently dynamax and z-moves are not supported.

![badge](https://github.com/pmariglia/foul-play/actions/workflows/ci.yml/badge.svg)

## Python version
Requires Python 3.11+.

## Getting Started

### Configuration

Command-line arguments are used to configure Foul Play

use `python run.py --help` to see all options.

### Running Locally

**1. Clone**

Clone the repository with `git clone https://github.com/pmariglia/foul-play.git`

**2. Install Requirements**

Install the requirements with `pip install -r requirements.txt`.

Then run `python scripts/install_poke_engine.py` to build the pinned native engine
with this project's mechanics fixes. Rust and a working C compiler are required.
For another generation, pass e.g. `--feature gen4`. The build verifies the upstream
source archive's SHA-256 before applying the patch. CI and Docker builds apply it
automatically.

The patched engine enforces AAA's Terastal Clause for both teams throughout
search, including after switches. Re-run the installer in the advisor's Python
environment after updating this checkout, then restart the advisor. Randbats
and other formats retain their existing Tera behavior.

Note: Requires Rust to be installed on your machine to build the engine.

**4. Run**

Run with `python run.py`

Here is a minimal example that plays a gen9randombattle on Pokemon Showdown:
```bash
python run.py \
--websocket-uri wss://sim3.psim.us/showdown/websocket \
--ps-username 'My Username' \
--ps-password sekret \
--bot-mode search_ladder \
--pokemon-format gen9randombattle
```

### Showdown browser advisor

Run `python advisor_server.py` and install [showdown-advisor.user.js](./showdown-advisor.user.js)
in a userscript manager. The advisor can rebuild its state from an open battle after a page
reload or when started mid-battle. It displays the engine's position estimate, ranked move
choices, likely opponent actions, and sampled opponent-team predictions. Hidden-team and
opponent-action details are estimates from the search, not confirmed information.
Use **Use recommended action** to send the current choice to Showdown, or enable **Auto**
to send each new choice after a randomized 3–5 second delay. Auto is off by default.
The team model can be edited to constrain later searches to your assumed roster, moves,
abilities, and items.

### Running with Docker

**1. Clone the repository**

`git clone https://github.com/pmariglia/foul-play.git`

**2. Build the Docker image**

Use the `Makefile` to build a Docker image
```shell
make docker
```

or for a specific generation:
```shell
make docker GEN=gen4
```

**3. Run the Docker Image**
```bash
docker run --rm --network host foul-play:latest \
--websocket-uri wss://sim3.psim.us/showdown/websocket \
--ps-username 'My Username' \
--ps-password sekret \
--bot-mode search_ladder \
--pokemon-format gen9randombattle
```

## Engine

This project uses [poke-engine](https://github.com/pmariglia/poke-engine) to search through battles.
See [the engine docs](https://poke-engine.readthedocs.io/en/latest/) for more information.

The engine must be built from source if installing locally so you must have rust installed on your machine.

### [Gen 9] Almost Any Ability

Showdown's `gen9almostanyability` format works with the advisor userscript and the
standard Gen 9 search engine. Opponent predictions use AAA-specific Showdown sets,
replay datasets when available, and Smogon AAA usage statistics (including up to
12 earlier months). AAA does not fall back to OU sets or OU ability statistics.
Missing Terastallization statistics are handled for formats where Tera is banned.

Ability inference allows abilities outside each species' normal pool. Unknown
abilities are treated conservatively when checking speed, priority, hazard immunity,
and Life Orb recoil. Revealed abilities and abilities entered in the advisor's
team model feed into search as usual. Species stats remain unchanged. Showdown
enforces AAA bans and clauses; sampled sets are not independently validated against
every format restriction.

### [Gen 9] Bad 'n Boosted

Showdown's `gen9badnboosted` format is handled as a standard Gen 9 battle. Foul Play applies
the format's species modification before calculating stats: every base stat at or below 70,
including HP, is doubled and capped at 255. The resulting stats are passed to `poke-engine`,
which uses them for damage calculations and MCTS simulation; no native engine changes are
needed for this rule.

Opponent set inference uses Bad 'n Boosted usage statistics when available, searching the
requested month and up to 12 earlier months. If none are available, it falls back to Gen 9 OU
usage. The bundled team-set source remains Gen 9 OU, so its samples can include sets banned by
Bad 'n Boosted. Search effort defaults to 500 ms per state and can be adjusted with
`--search-time-ms`. Move selection returns the highest-visit action from the sampled searches.
Showdown enforces format bans and Sleep Moves Clause, but Foul Play does not independently
validate every sampled set against those restrictions.

Search uses at least two opponent scenarios when more than 60 seconds remain on the
reported clock (or no clock is reported). If the scenarios prefer different actions,
or the top two actions are within 10 percentage points of weighted visits, it samples
another batch, up to three batches total. This can use up to three times the initial
search budget. Under clock pressure it keeps the mode's original sample count and budget.
Advisor analysis includes the total scenario count.

For Bad 'n Boosted, disagreement instead triggers a longer search of the same
sampled positions: each is searched again with twice the initial time, and those
results replace the initial results. This keeps the previous maximum compute
budget while spending more time looking ahead within each scenario. Clock-pressure
searches retain their original budget. The native API starts a fresh tree for each
search; it does not support resuming an existing tree.

The panel's search outlook is a centered heuristic search score, not a calibrated
win probability or an absolute position evaluation. Native MCTS evaluates unfinished
leaves relative to the starting position; it does not play every simulation to the
end. Scores are averaged using opponent scenario probabilities rather than each
scenario's iteration count. Full-game rollouts or a different leaf evaluator require
changes to the native engine.

The advisor saves directly revealed moves, items, and abilities after recommendations and at game end to
`fp/data/local_data/opponent_memory.json`. Profiles are keyed by format and normalized
opponent username, retain ten games per opponent, and expire after 90 days. In standard
battles, a profile is reused only when its roster contains the currently known roster.
Alternating sampled scenarios use that prior before sampling unknown set details, starting
with the first scenario so a small batch cannot miss it entirely; the rest
use the usual datasets. Current revelations and incompatible full movesets take precedence.
Games containing Illusion reveals or Transform are omitted, and sampled guesses and manual
team edits are never recorded as confirmed observations. Restart the advisor to load these
code changes; no userscript update is required. Delete the memory file to reset learning.
Revealed moves and their PP/disabled state survive dataset fallbacks and manual model edits.
Manual model hints are applied before filling unknown set details.

The advisor also records recommendations, per-scenario policies, and serialized engine states
in `logs/advisor-decisions.jsonl` for reproducing poor decisions. The trace rotates at 5 MB
and retains two backups. Search states stay on disk and are omitted from browser polling.

### Re-Installing the Engine

Use `python scripts/install_poke_engine.py --feature terastallization` for the
patched Gen 9 engine, or `make poke_engine GEN=gen4` for another generation.
The installed package version is `0.0.48+foulplay.3`. Installing the plain upstream
package replaces the patch.

This fixes an upstream Unaware bug: critical hits incorrectly used offensive boosts
or defensive drops that Unaware should ignore. The regression tests cover physical
and special damage, Psystrike, Body Press, Mold Breaker, and Stored Power. This is a
damage-mechanics fix; it does not replace the native leaf evaluator or implement
opponent action learning.

The patched engine also exposes seeded search and joint root-action statistics
for the [reproducible selector benchmark](./benchmarks/README.md). The experimental
`--search-selector matrix` computes a mixed payoff-matrix policy using the current
heuristic evaluator; it does not include a trained value model. The browser advisor
can opt in through `FOUL_PLAY_SEARCH_SELECTOR=matrix`. The default remains
visit-based MCTS selection. Run the benchmark before changing that default.

It is common to want to re-install the engine for different generations of Pokémon.

`pip` will used cached .whl artifacts when installing packages
and cannot detect the `--config-settings` flag that was used to build the engine.

The following command will ensure that the engine is re-installed properly:
```shell
pip uninstall -y poke-engine && pip install -v --force-reinstall --no-cache-dir poke-engine --config-settings="build-args=--features poke-engine/<GENERATION> --no-default-features"
```

Or using the Makefile:
```shell
make poke_engine GEN=<generation>
```

For example, to re-install the engine for generation 4:
```shell
make poke_engine GEN=gen4
```
