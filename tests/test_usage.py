"""feed/usage.jsonl: what judging used and cost, read from Claude Code's replies and summed up."""

from ajh import usage

REPLY = {  # the shape of a real `claude -p --output-format json` reply; numbers from one triage stage
    "type": "result",
    "is_error": False,
    "duration_ms": 73200,
    "duration_api_ms": 625100,
    "num_turns": 52,
    "total_cost_usd": 0.2071,
    "modelUsage": {
        "claude-haiku-5-5": {
            "inputTokens": 300,
            "outputTokens": 137022,
            "thinkingTokens": 104889,
            "cacheReadInputTokens": 1199310,
            "cacheCreationInputTokens": 983247,
            "costUSD": 0.2034,
            "costBasis": "list",
        },
        "claude-sonnet-5-5": {"inputTokens": 12, "outputTokens": 90, "costUSD": 0.0037, "costBasis": "list"},
    },
}


def test_a_reply_is_read_per_model_and_several_calls_add_up():
    one = usage.from_reply(REPLY)
    assert one["models"]["claude-haiku-5-5"] == {
        "in": 300,
        "out": 137022,
        "thinking": 104889,
        "cache_read": 1199310,
        "cache_write": 983247,
        "cost_usd": 0.2034,
    }
    assert one["models"]["claude-sonnet-5-5"]["cache_read"] == 0  # a field the reply leaves out counts as zero
    assert (one["seconds"], one["api_seconds"], one["turns"]) == (73.2, 625.1, 52)
    both = usage.combine([one, one, {}])  # two batches, and a call that reported nothing
    assert (
        both["models"]["claude-haiku-5-5"]["out"] == 274044 and both["models"]["claude-haiku-5-5"]["cost_usd"] == 0.4068
    )
    assert (both["seconds"], both["turns"]) == (146.4, 104)
    junk = usage.from_reply(
        {"modelUsage": {"m": {"inputTokens": "many", "costUSD": None}, "n": "?"}, "num_turns": True}
    )
    assert junk["models"] == {
        "m": {"in": 0, "out": 0, "thinking": 0, "cache_read": 0, "cache_write": 0, "cost_usd": 0.0}
    }
    assert junk["turns"] == 0


def test_the_log_is_summed_for_the_last_update_week_month_and_all_time(tmp_path):
    path = tmp_path / "usage.jsonl"
    for at, cost in (
        ("2026-09-30T23:00:00Z", 2.0),
        ("2026-10-02T10:00:00Z", 1.0),
        ("2026-10-12T10:00:00Z", 0.5),
        ("2026-10-14T13:05:12Z", 0.25),
    ):
        tokens = {"in": 10, "out": 20, "thinking": 5, "cache_read": 100, "cache_write": 50}
        usage.append(path, {"at": at, "total": {**tokens, "cost_usd": cost}})
    path.write_text(path.read_text() + "not json\n[]\n")  # damaged lines are skipped, not fatal
    summary = usage.summary(usage.load(path), "2026-10-14")
    assert summary["last_update"] == {
        "at": "2026-10-14T13:05:12Z",
        "updates": 1,
        "in": 10,
        "out": 20,
        "thinking": 5,
        "cache_read": 100,
        "cache_write": 50,
        "cost_usd": 0.25,
    }
    assert (summary["last_7_days"]["updates"], summary["last_7_days"]["cost_usd"]) == (2, 0.75)  # 10-08 to 10-14
    assert (summary["month"]["name"], summary["month"]["updates"], summary["month"]["cost_usd"]) == ("2026-10", 3, 1.75)
    assert (summary["all_time"]["updates"], summary["all_time"]["cost_usd"]) == (4, 3.75)
    assert usage.summary([], "2026-10-14") is None


def test_the_kind_of_credential_is_recorded_never_its_value(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    assert usage.credential() == "login"  # the account Claude Code is logged in with
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "token")
    assert usage.credential() == "subscription"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    assert usage.credential() == "api_key"
