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

### [Gen 9] Bad 'n Boosted

Showdown's `gen9badnboosted` format is handled as a standard Gen 9 battle. Foul Play applies
the format's species modification before calculating stats: every base stat at or below 70,
including HP, is doubled and capped at 255. The resulting stats are passed to `poke-engine`,
which uses them for damage calculations and MCTS simulation; no native engine changes are
needed for this rule.

Opponent set inference uses Bad 'n Boosted usage statistics when available, searching the
requested month and up to 12 earlier months. If none are available, it falls back to Gen 9 OU
priors. This affects sampled hidden teams, not the stat calculation, and OU samples can include
sets banned by Bad 'n Boosted. Showdown still enforces its format bans and Sleep Moves Clause;
Foul Play does not independently validate those restrictions, so this is not full
format-specific opponent set modeling.

### Re-Installing the Engine

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
