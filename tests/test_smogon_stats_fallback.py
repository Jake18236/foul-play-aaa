import logging

from fp.data.sets import smogon as smogon_module
from fp.data.sets.smogon import SmogonSets


class FakeResponse:
    def __init__(self, status_code, data=None):
        self.status_code = status_code
        self.data = data or {}

    def raise_for_status(self):
        if self.status_code != 200:
            raise AssertionError(f"Unexpected HTTP status {self.status_code}")

    def json(self):
        return {"data": self.data}


def stats_url(game_mode, month_delta=1):
    return f"https://stats.example/{month_delta:02d}/chaos/{game_mode}-0.json"


def test_smogon_stats_search_caches_historical_dataset(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(smogon_module, "SMOGON_CACHE_DIR", str(tmp_path))
    sets = SmogonSets()
    requested_urls = []

    def get(url):
        requested_urls.append(url)
        month_delta = int(url.split("/")[-3])
        if month_delta == 3:
            return FakeResponse(200, {"pikachu": {"Raw count": 1}})
        return FakeResponse(404)

    monkeypatch.setattr(smogon_module.requests, "get", get)
    monkeypatch.setattr(
        sets,
        "_get_smogon_stats_file_name",
        lambda game_mode, month_delta=1: stats_url(game_mode, month_delta),
    )

    with caplog.at_level(logging.INFO):
        stats = sets._get_smogon_stats_json(stats_url("gen9badnboosted"))

    assert stats == {"pikachu": {"Raw count": 1}}
    assert requested_urls == [
        stats_url("gen9badnboosted", 1),
        stats_url("gen9badnboosted", 2),
        stats_url("gen9badnboosted", 3),
    ]
    assert (tmp_path / "03-gen9badnboosted-0.json").exists()
    assert "historical Smogon stats for 03" in caplog.text

    requested_urls.clear()
    cached_stats = sets._get_smogon_stats_json(stats_url("gen9badnboosted"))
    assert cached_stats == stats
    assert requested_urls == [
        stats_url("gen9badnboosted", 1),
        stats_url("gen9badnboosted", 2),
    ]


def test_badnboosted_stats_fall_back_to_ou_when_history_is_missing(
    monkeypatch, tmp_path, caplog
):
    monkeypatch.setattr(smogon_module, "SMOGON_CACHE_DIR", str(tmp_path))
    sets = SmogonSets()
    requested_urls = []

    def get(url):
        requested_urls.append(url)
        if "gen9ou" in url:
            return FakeResponse(200, {"ou-pokemon": {"Raw count": 1}})
        return FakeResponse(404)

    monkeypatch.setattr(smogon_module.requests, "get", get)
    monkeypatch.setattr(
        sets,
        "_get_smogon_stats_file_name",
        lambda game_mode, month_delta=1: stats_url(game_mode, month_delta),
    )

    with caplog.at_level(logging.WARNING):
        stats = sets._get_smogon_stats_json(stats_url("gen9badnboosted"))

    assert stats == {"ou-pokemon": {"Raw count": 1}}
    assert requested_urls[-1] == stats_url("gen9ou")
    assert "falling back to Gen 9 OU stats" in caplog.text
