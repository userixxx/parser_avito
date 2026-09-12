import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from actualizator.fetcher import Fetcher
from actualizator.settings import Settings


class FakeSettings:
    cookie_slot = "act-msk"
    cookie_buy = False
    cookie_wait_seconds = 60.0
    cookie_daily_cap = 60
    cookie_min_interval = 1200.0
    avito_mobile = False
    avito_mobile_host = "m.avito.ru"

    def __init__(self, storage_dir):
        self.storage_dir = storage_dir


class FakeCore:
    def __init__(self):
        self.lease_calls = []

    def lease_cookie(self, city, exclude_id=None, allow_purchase=True):
        self.lease_calls.append(allow_purchase)
        return {"cookie_id": 1, "cookies": {"a": "b"}, "type": "pc"}


def build(tmp_path, buy):
    settings = FakeSettings(str(tmp_path))
    settings.cookie_buy = buy
    core = FakeCore()

    return Fetcher(settings, core, "act-msk"), core


def test_purchase_disabled_never_allows_budget(tmp_path):
    fetcher, _ = build(tmp_path, buy=False)

    assert fetcher._cookie_budget_allows() is False


def test_purchase_disabled_waits_fixed_pause_instead_of_budget_window(tmp_path):
    fetcher, _ = build(tmp_path, buy=False)

    assert fetcher._cookie_budget_wait() == 60.0


def test_purchase_disabled_leases_without_buying(tmp_path):
    fetcher, core = build(tmp_path, buy=False)

    fetcher._ensure_cookie()

    assert core.lease_calls == [False]


def test_purchase_enabled_keeps_previous_behaviour(tmp_path):
    fetcher, core = build(tmp_path, buy=True)

    fetcher._ensure_cookie()

    assert core.lease_calls == [True]


def test_only_sources_parsed_from_env(monkeypatch):
    monkeypatch.setenv("ACTUALIZER_ONLY_SOURCES", "avito, cian ")

    assert Settings().only_sources == ["avito", "cian"]


def test_only_sources_empty_by_default(monkeypatch):
    monkeypatch.delenv("ACTUALIZER_ONLY_SOURCES", raising=False)

    assert Settings().only_sources == []


def test_heartbeat_city_defaults_to_global(monkeypatch):
    monkeypatch.delenv("ACTUALIZER_HEARTBEAT_CITY", raising=False)

    assert Settings().heartbeat_city == "global"
