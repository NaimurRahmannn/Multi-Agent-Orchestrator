import pytest
from pydantic import ValidationError

from agentorchestra.agents.manager import normalize_token_usage


class AttributeUsage:
    prompt_tokens = 2
    completion_tokens = 3
    total_tokens = 5


class UsageSource:
    token_usage = AttributeUsage()


class DumpableUsage:
    def model_dump(self, mode):
        assert mode == "json"
        return {"prompt_tokens": 4, "completion_tokens": 5, "total_tokens": 9}


def test_no_usage_object_returns_unknown_usage():
    assert normalize_token_usage(object()).model_dump(mode="json") == {
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
    }


def test_mapping_style_usage_is_supported():
    usage = normalize_token_usage(
        {"usage_metrics": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}
    )

    assert usage.total_tokens == 3


def test_attribute_style_usage_is_supported():
    usage = normalize_token_usage(UsageSource())

    assert usage.prompt_tokens == 2
    assert usage.completion_tokens == 3
    assert usage.total_tokens == 5


def test_model_dump_usage_is_supported():
    usage = normalize_token_usage({"token_usage": DumpableUsage()})

    assert usage.total_tokens == 9


def test_zero_tokens_are_preserved():
    usage = normalize_token_usage({"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})

    assert usage.total_tokens == 0


def test_inconsistent_totals_are_rejected():
    with pytest.raises(ValidationError):
        normalize_token_usage({"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 6})


def test_negative_values_are_rejected():
    with pytest.raises(ValidationError):
        normalize_token_usage({"prompt_tokens": -1})


def test_unrelated_metadata_is_ignored():
    usage = normalize_token_usage({"irrelevant": "metadata"})

    assert usage.total_tokens is None
