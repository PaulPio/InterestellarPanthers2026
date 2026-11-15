"""Gateway settings, read from ``CCM_*`` environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    db_path: Path
    mqtt_host: str
    mqtt_port: int
    api_host: str
    api_port: int
    dashboard_dist: Path
    raw_retention_days: int
    public_port: int
    public_key: str | None
    fallback_scenario: str = "quiet_day"
    fallback_stale_s: float = 60.0


def load_settings() -> Settings:
    env = os.environ.get
    return Settings(
        db_path=Path(env("CCM_DB", "data/ccm.sqlite3")),
        mqtt_host=env("CCM_MQTT_HOST", "127.0.0.1"),
        mqtt_port=int(env("CCM_MQTT_PORT", "1883")),
        api_host=env("CCM_API_HOST", "127.0.0.1"),
        api_port=int(env("CCM_API_PORT", "8000")),
        dashboard_dist=Path(env("CCM_DASHBOARD_DIST", "dashboard/dist")),
        raw_retention_days=int(env("CCM_RAW_RETENTION_DAYS", "7")),
        public_port=int(env("CCM_PUBLIC_PORT", "8001")),
        public_key=env("CCM_PUBLIC_KEY") or None,
        fallback_scenario=env("CCM_FALLBACK_SCENARIO", "quiet_day"),
        fallback_stale_s=float(env("CCM_FALLBACK_STALE_S", "60")),
    )
