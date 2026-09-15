import pytest

import sender


class NoNetwork:
    """Stands in for httpx.AsyncClient so a stray real request fails loudly.

    Sends that slip past a stub used to reach api.telegram.org with a dummy
    token: the suite still passed, just seconds slower. A hard error is easier
    to notice than a slow test.
    """

    def __init__(self, *args, **kwargs):
        raise RuntimeError(
            "test tried to open an HTTP client — stub the send it goes through"
        )


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Block real requests, and drop the shared Bot API client between tests.

    The client is module-level and bound to the event loop it was created in,
    so one left over from a previous asyncio.run would be unusable anyway.
    """
    sender._client = None
    monkeypatch.setattr(sender.httpx, "AsyncClient", NoNetwork)
    yield
    sender._client = None
