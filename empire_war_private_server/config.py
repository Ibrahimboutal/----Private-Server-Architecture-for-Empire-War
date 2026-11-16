"""Strict local-only configuration; relative paths resolve beside config.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 1024
    database: Path = Path("runtime/state.db")
    audit_database: Path = Path("runtime/audit.db")
    max_connections: int = 32
    max_frame_bytes: int = 1_048_576
    max_decoded_bytes: int = 8_388_608
    read_timeout_seconds: int = 30
    requests_per_second: int = 20
    session_ttl_seconds: int = 3600
    synthetic_passport: str = "research-local"
    synthetic_password: str = "local-test-only"
    synthetic_client_login: bool = False
    gameplay_scenario: str = "captured-phase2-v1"
    market_scenario: str = "captured-market-food-v1"
    simulate_int32_crash: bool = False
    overflow_breaker_threshold: int = 3
    simulation_delay_ms: int = 100

    def __post_init__(self) -> None:
        if self.host != "127.0.0.1":
            raise ValueError("the server must bind only to 127.0.0.1")
        if self.gameplay_scenario not in ("disabled", "captured-phase2-v1", "captured-quarry-shortage-v1"):
            raise ValueError("unknown gameplay_scenario")
        if self.market_scenario not in ("disabled", "captured-market-food-v1"):
            raise ValueError("unknown market_scenario")
        if type(self.simulate_int32_crash) is not bool:
            raise ValueError("simulate_int32_crash must be boolean")
        if type(self.synthetic_client_login) is not bool:
            raise ValueError("synthetic_client_login must be boolean")
        if self.simulate_int32_crash and self.market_scenario == "disabled":
            raise ValueError("simulation requires the market scenario")
        bounds = {"port": (1, 65535), "max_connections": (1, 128),
                  "max_frame_bytes": (64, 5_000_000), "max_decoded_bytes": (64, 8_388_608),
                  "read_timeout_seconds": (1, 300), "requests_per_second": (1, 100),
                  "session_ttl_seconds": (1, 3600), "overflow_breaker_threshold": (1, 100),
                  "simulation_delay_ms": (0, 1000)}
        for name, (lower, upper) in bounds.items():
            value = getattr(self, name)
            if type(value) is not int or not lower <= value <= upper:
                raise ValueError(f"{name} must be an integer in {lower}..{upper}")
        for name in ("synthetic_passport", "synthetic_password"):
            value = getattr(self, name)
            if not isinstance(value, str) or not 1 <= len(value) <= 128:
                raise ValueError(f"invalid {name}")

    @classmethod
    def load(cls, path: Path) -> ServerConfig:
        data: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("configuration must be a mapping")
        for name in ("database", "audit_database"):
            value = data.get(name, str(getattr(cls(), name)))
            if not isinstance(value, str) or not value:
                raise ValueError(f"invalid {name}")
            data[name] = (path.resolve().parent / value).resolve()
        try:
            return cls(**data)
        except TypeError as exc:
            raise ValueError("unknown configuration field") from exc
