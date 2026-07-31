from argparse import Namespace

from xdata.contracts import Post, ProviderResult, UserProfile, UserRef
from xdata.smoke import render_text, summarize_result


def test_summarize_result_handles_post_items():
    result = ProviderResult.ok(
        provider="socialdata",
        items=[Post(id="1", text="hello", author=UserRef(username="alice"))],
    )

    summary = summarize_result(result)

    assert summary["provider"] == "socialdata"
    assert summary["count"] == 1
    assert summary["sample"][0]["kind"] == "post"
    assert summary["sample"][0]["author"] == "alice"


def test_summarize_result_handles_user_items():
    result = ProviderResult.ok(
        provider="socialdata",
        items=[UserProfile(id="2", username="bob", name="Bob")],
    )

    summary = summarize_result(result)

    assert summary["sample"][0]["kind"] == "user"
    assert summary["sample"][0]["username"] == "bob"


def test_render_text_outputs_compact_summary():
    payload = {
        "config": {
            "user": "@OpenAI",
            "search_query": "from:OpenAI",
            "collect_query": "OpenAI",
            "graph": "followers",
            "limit": 3,
        },
        "status": {
            "socialdata": {"auth_required": True, "auth_present": True, "read_only": True},
            "syndication": {"auth_required": False, "configured": True},
            "official_x": {"auth_required": True, "auth_present": False, "read_only": True},
            "router": {},
        },
        "socialdata.search_posts": {
            "status": "ok",
            "provider": "socialdata",
            "count": 1,
            "reason": None,
            "warnings": [],
            "metadata": {"query": "from:OpenAI"},
            "sample": [{"kind": "post", "id": "1", "author": "OpenAI"}],
        },
    }

    text = render_text(payload)

    assert "Smoke config:" in text
    assert "socialdata.search_posts: status=ok provider=socialdata count=1" in text
    assert 'metadata={"query": "from:OpenAI"}' in text
