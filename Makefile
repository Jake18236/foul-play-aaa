docker:
	docker build . -t foul-play:latest --build-arg GEN=$(GEN)

clean_logs:
	rm logs/*

clean_data_cache:
	rm -rf fp/data/pkmn_sets_cache/*
	rm -rf fp/data/smogon_stats_cache/*

test:
	ruff check
	pytest tests

fmt:
	ruff format

lint:
	ruff check --fix

poke_engine:
	python scripts/install_poke_engine.py --feature $(if $(GEN),$(GEN),terastallization)

# This assumes that the pmariglia/poke-engine repository is in the same directory as foul-play
poke_engine_local:
	pip uninstall -y poke-engine && pip install -v --force-reinstall --no-cache-dir ../poke-engine/poke-engine-py --config-settings="build-args=--features poke-engine/$(GEN) --no-default-features"
