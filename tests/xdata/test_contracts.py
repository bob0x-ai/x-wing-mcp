from xdata.contracts import Post, ProviderResult, UserProfile


def test_ok_with_empty_items_normalizes_to_empty():
    result = ProviderResult.ok(provider="test", items=[])

    assert result.status == "empty"
    assert result.reason == "no_results"


def test_result_to_dict_is_serializable_shape():
    result = ProviderResult.ok(
        provider="test",
        items=[Post(id="1", text="hello")],
        warnings=["note"],
    )

    payload = result.to_dict()

    assert payload["status"] == "ok"
    assert payload["provider"] == "test"
    assert payload["items"][0]["id"] == "1"
    assert payload["warnings"] == ["note"]


def test_result_to_dict_supports_user_profile_items():
    result = ProviderResult.ok(
        provider="test",
        items=[
            UserProfile(
                id="42",
                username="alice",
                public_metrics={"followers_count": 3},
            )
        ],
    )

    payload = result.to_dict()

    assert payload["items"][0]["username"] == "alice"
    assert payload["items"][0]["public_metrics"]["followers_count"] == 3
