import os
from contextlib import contextmanager
from types import SimpleNamespace

from xdata.providers.official_x import OfficialXProvider
from xdata.providers.socialdata import HttpResponse as SocialDataHttpResponse
from xdata.providers.socialdata import SocialDataProvider
from xdata.providers.syndication import HttpResponse, SyndicationProvider

POST_PAYLOAD = {
    "id_str": "1234567890",
    "text": "hello from x",
    "created_at": "Fri Jul 03 00:00:00 +0000 2026",
    "user": {"id_str": "42", "screen_name": "alice", "name": "Alice"},
    "favorite_count": 5,
}


class _MockPosts:
    def get_by_id(self, **kwargs):
        assert kwargs["id"] == "1234567890"
        return SimpleNamespace(
            data={
                "id": "1234567890",
                "text": "hello from x",
                "created_at": "2026-07-03T00:00:00Z",
                "author_id": "42",
                "public_metrics": {"like_count": 5},
            }
        )


class _MockClient:
    posts = _MockPosts()
    users = SimpleNamespace(get_me=lambda: SimpleNamespace(data={"id": "99"}))


@contextmanager
def _env(**values):
    old = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _providers():
    def syndication_get(url, timeout):
        del timeout
        assert "1234567890" in url
        import json

        return HttpResponse(status_code=200, text=json.dumps(POST_PAYLOAD))

    yield SyndicationProvider(http_get=syndication_get, min_interval_seconds=0, jitter_seconds=0)

    with _env(X_OAUTH2_ACCESS_TOKEN="token"):
        yield OfficialXProvider(client_factory=lambda token: _MockClient())

    def socialdata_get(url, headers, timeout):
        del headers, timeout
        assert url.endswith("/tweets/1234567890")
        import json

        payload = dict(POST_PAYLOAD)
        payload["full_text"] = payload.pop("text")
        return SocialDataHttpResponse(status_code=200, text=json.dumps(payload))

    with _env(SOCIALDATA_API_KEY="token"):
        yield SocialDataProvider(http_get=socialdata_get, min_interval_seconds=0, jitter_seconds=0)


def test_exact_post_fetch_contract_across_providers():
    for provider in _providers():
        result = provider.fetch_urls(["https://x.com/alice/status/1234567890"])

        assert result.status == "ok"
        assert result.items
        assert result.items[0].id == "1234567890"
        assert result.items[0].text == "hello from x"
        assert result.provider == provider.name
