from discord_openrouter.util import extract_reasoning_text, extract_usage


def test_extract_reasoning_text_deduplicates_repeated_blocks():
    repeated = (
        "The user is saying 'hi dere' which is a casual, informal greeting. "
        "I should respond in a friendly, casual way."
    )

    reasoning_text = extract_reasoning_text(
        {
            "reasoning": repeated,
            "reasoning_details": [
                {"type": "reasoning.summary", "summary": repeated},
                {"type": "reasoning.text", "text": "Second unique thought."},
                {"type": "reasoning.text", "text": "Second unique thought."},
            ],
        }
    )

    assert reasoning_text == repeated + "\n\nSecond unique thought."


def test_extract_usage_reads_openrouter_cost_cache_and_server_tools():
    usage = extract_usage(
        {
            "usage": {
                "prompt_tokens": 132,
                "completion_tokens": 263,
                "total_tokens": 395,
                "cost": 0.0032,
                "prompt_tokens_details": {
                    "cached_tokens": 64,
                },
                "completion_tokens_details": {
                    "reasoning_tokens": 239,
                },
                "server_tool_use": {
                    "web_search": 2,
                    "ignored_zero": 0,
                },
            }
        }
    )

    assert usage.prompt_tokens == 132
    assert usage.completion_tokens == 263
    assert usage.total_tokens == 395
    assert usage.cached_tokens == 64
    assert usage.reasoning_tokens == 239
    assert usage.cost == 0.0032
    assert usage.server_tool_use == {"web_search": 2}
