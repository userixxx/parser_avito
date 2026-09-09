import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from actualizator.pool_fetcher import PoolFetcher
from actualizator.pool_worker import PoolCycle


class FakeSettings:
    cookie_slot = "act-msk"
    pause_min = 0.0
    pause_max = 0.0
    idle_sleep = 0
    busy_sleep = 0
    disabled_sleep = 0
    block_limit = 2
    block_retries = 1


class FakeProxy:
    def __init__(self, results):
        self._results = list(results)
        self.acquired = 0
        self.released = 0
        self.proxy_url = "http://p:1"
        self.proxy_id = 4

    def acquire(self):
        self.acquired += 1
        return self._results.pop(0) if self._results else False

    def release(self):
        self.released += 1


class FakeCore:
    def __init__(self, tasks=None):
        self.lease_calls = []
        self.reported = []
        self._tasks = tasks if tasks is not None else []

    def lease(self, limit, source=None):
        self.lease_calls.append((limit, source))
        return self._tasks

    def report(self, results):
        self.reported.append(results)
        return True


class FakeFetcher:
    def __init__(self, starved=False):
        self.checked = []
        self.proxy_string = None
        self.cookie_starved = starved

    def apply_config(self, proxy_string, change_url):
        self.proxy_string = proxy_string

    def check(self, url, source):
        self.checked.append((url, source))
        return "alive", 200


def test_no_channel_means_no_lease():
    core = FakeCore()
    proxy = FakeProxy([False])
    cycle = PoolCycle(FakeSettings(), core, FakeFetcher(), proxy)

    assert cycle.tick() == "busy"
    assert core.lease_calls == []
    assert proxy.released == 0


def test_channel_first_then_lease_then_release():
    core = FakeCore([{"listing_id": 1, "source": "avito", "city": "omsk",
                      "source_url": "https://avito.test/1"}])
    proxy = FakeProxy([True])
    fetcher = FakeFetcher()
    cycle = PoolCycle(FakeSettings(), core, fetcher, proxy)

    assert cycle.tick() == "done"
    assert core.lease_calls == [(1, "avito")]
    assert fetcher.checked == [("https://avito.test/1", "avito")]
    assert proxy.released == 1
    assert core.reported == [[{"listing_id": 1, "source": "avito", "result": "alive", "http_code": 200}]]


def test_channel_released_even_when_queue_is_empty():
    core = FakeCore([])
    proxy = FakeProxy([True])
    cycle = PoolCycle(FakeSettings(), core, FakeFetcher(), proxy)

    assert cycle.tick() == "empty"
    assert core.lease_calls == [(1, "avito")]
    assert proxy.released == 1
    assert core.reported == []


def test_starved_cookie_is_not_reported_as_a_verdict():
    core = FakeCore([{"listing_id": 1, "source": "avito", "city": "omsk",
                      "source_url": "https://avito.test/1"}])
    proxy = FakeProxy([True])
    cycle = PoolCycle(FakeSettings(), core, FakeFetcher(starved=True), proxy)

    assert cycle.tick() == "starved"
    assert core.reported == []
    assert proxy.released == 1


def test_pool_fetcher_has_no_escalation():
    assert PoolFetcher._escalation_plan(None) == []


def test_pool_fetcher_never_rotates_ip():
    assert PoolFetcher.rotate_ip(None) is False


def test_pool_fetcher_drops_the_cookie_after_block_limit():
    fetcher = PoolFetcher.__new__(PoolFetcher)
    fetcher.settings = FakeSettings()
    fetcher.settings.block_limit = 2
    fetcher.cookie = {"cookie_id": 7}
    fetcher.consecutive_blocks = 1
    fetcher.batch_interrupted = False

    PoolFetcher._on_block(fetcher)
    assert fetcher.cookie == {"cookie_id": 7}

    fetcher.consecutive_blocks = 2
    PoolFetcher._on_block(fetcher)
    assert fetcher.cookie is None
    assert fetcher.batch_interrupted is True


def test_pool_fetcher_disables_block_retries(monkeypatch):
    from actualizator import fetcher as fetcher_module

    def fake_init(self, settings, core, cookie_slot):
        self.settings = settings
        self.core = core
        self.cookie_slot = cookie_slot

    monkeypatch.setattr(fetcher_module.Fetcher, "__init__", fake_init)

    settings = FakeSettings()
    settings.block_retries = 5

    PoolFetcher(settings, FakeCore(), "act-msk")

    assert settings.block_retries == 1
