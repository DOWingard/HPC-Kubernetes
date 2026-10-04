"""Environment-driven settings. Secrets have no defaults: start-up fails fast if they are missing."""

import logging
import os
from dataclasses import dataclass, field

log = logging.getLogger("hpc_portal.config")


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        log.error("required environment variable %s is missing or empty", name)
        raise ConfigError(f"required environment variable {name} is missing or empty")
    return value


def parse_tokens(raw: str) -> dict[str, str]:
    """Parses "user:token,user:token" into {token: user}."""
    tokens: dict[str, str] = {}
    for pair in filter(None, (p.strip() for p in raw.split(","))):
        user, sep, token = pair.partition(":")
        if not sep or not user or len(token) < 16:
            raise ConfigError("PORTAL_API_TOKENS entries must look like user:token with tokens of 16+ chars")
        if token in tokens:
            raise ConfigError(f"duplicate API token configured for user {user}")
        tokens[token] = user
    if not tokens:
        raise ConfigError("PORTAL_API_TOKENS contains no user:token pairs")
    return tokens


@dataclass(frozen=True)
class Settings:
    api_tokens: dict[str, str] = field(repr=False)
    partitions: tuple[str, ...]
    shared_root: str
    command_timeout_s: float
    log_level: str

    @classmethod
    def from_env(cls) -> "Settings":
        settings = cls(
            api_tokens=parse_tokens(_require("PORTAL_API_TOKENS")),
            partitions=tuple(p for p in os.environ.get("ALLOWED_PARTITIONS", "local-node,k8s-burst").split(",") if p),
            shared_root=os.environ.get("SHARED_ROOT", "/shared"),
            command_timeout_s=float(os.environ.get("SLURM_COMMAND_TIMEOUT", "20")),
            log_level=os.environ.get("LOG_LEVEL", "info"),
        )
        log.info(
            "configuration loaded",
            extra={"users": sorted(set(settings.api_tokens.values())), "partitions": settings.partitions},
        )
        return settings
