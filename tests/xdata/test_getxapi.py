import json

from xdata.providers.getxapi import GetXApiProvider, HttpResponse


def _article_payload() -> dict:
    return {
        "status": "success",
        "msg": "success",
        "article": {
            "id": "QXJ0aWNsZUVudGl0eTox",
            "author": {
                "id": "42",
                "userName": "alice",
                "name": "Alice",
            },
            "replyCount": 2,
            "likeCount": 3,
            "quoteCount": 4,
            "viewCount": 5,
            "createdAt": "Fri Mar 28 09:01:12 +0000 2025",
            "title": "When Tokens Burn",
            "preview_text": "preview",
            "cover_media_img_url": "https://pbs.twimg.com/media/cover.jpg",
            "contents": [
                {
                    "type": "unstyled",
                    "text": "Breaking down revenue.",
                    "inlineStyleRanges": [],
                },
                {
                    "type": "image",
                    "url": "https://pbs.twimg.com/media/article.png",
                    "width": 2040,
                    "height": 1372,
                },
                {
                    "type": "header-two",
                    "text": "Conclusion",
                    "inlineStyleRanges": [],
                },
            ],
        },
    }


def test_missing_auth_returns_unavailable(monkeypatch):
    monkeypatch.delenv("GETXAPI_API_KEY", raising=False)

    provider = GetXApiProvider(min_interval_seconds=0, jitter_seconds=0)
    result = provider.read_article("https://x.com/alice/status/1905545699552375179")

    assert result.status == "unavailable"
    assert result.reason == "auth_required"


def test_read_article_accepts_wrapper_tweet_url(monkeypatch):
    monkeypatch.setenv("GETXAPI_API_KEY", "token")

    def http_get(url, headers, timeout):
        del headers, timeout
        assert url.endswith("/article/get?id=1905545699552375179")
        return HttpResponse(status_code=200, text=json.dumps(_article_payload()))

    provider = GetXApiProvider(http_get=http_get, min_interval_seconds=0, jitter_seconds=0)
    result = provider.read_article("https://x.com/alice/status/1905545699552375179")

    assert result.status == "ok"
    assert result.items[0].title == "When Tokens Burn"
    assert result.items[0].author.username == "alice"
    assert result.items[0].images[0].url == "https://pbs.twimg.com/media/article.png"
    assert "[Image: https://pbs.twimg.com/media/article.png]" in result.items[0].body_text
    assert result.metadata["wrapper_tweet_id"] == "1905545699552375179"


def test_direct_article_url_requires_share_link(monkeypatch):
    monkeypatch.setenv("GETXAPI_API_KEY", "token")

    provider = GetXApiProvider(min_interval_seconds=0, jitter_seconds=0)
    result = provider.read_article("https://x.com/i/article/2074106701297360897")

    assert result.status == "error"
    assert result.reason == "article_url_requires_share_link"


def test_not_found_maps_to_unavailable(monkeypatch):
    monkeypatch.setenv("GETXAPI_API_KEY", "token")

    def http_get(url, headers, timeout):
        del url, headers, timeout
        return HttpResponse(status_code=404, text=json.dumps({"error": "No article found for tweet: 12345"}))

    provider = GetXApiProvider(http_get=http_get, min_interval_seconds=0, jitter_seconds=0)
    result = provider.read_article("12345")

    assert result.status == "unavailable"
    assert result.reason == "not_found"
