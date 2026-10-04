import pytest

from hpc_portal.config import ConfigError, Settings, parse_tokens

TOKEN_A = "a" * 32
TOKEN_B = "b" * 32


def test_parse_tokens_maps_token_to_user():
    assert parse_tokens(f"alice:{TOKEN_A}, bob:{TOKEN_B}") == {TOKEN_A: "alice", TOKEN_B: "bob"}


@pytest.mark.parametrize("raw", ["", "alice", "alice:short", f":{TOKEN_A}", f"alice:{TOKEN_A},bob:{TOKEN_A}"])
def test_parse_tokens_rejects_bad_input(raw):
    with pytest.raises(ConfigError):
        parse_tokens(raw)


def test_settings_fail_fast_without_tokens(monkeypatch):
    monkeypatch.delenv("PORTAL_API_TOKENS", raising=False)
    with pytest.raises(ConfigError, match="PORTAL_API_TOKENS"):
        Settings.from_env()
