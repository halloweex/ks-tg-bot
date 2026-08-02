"""Configuration system: loads secrets from .env and settings from config.yaml."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic_settings import BaseSettings


class EnvSettings(BaseSettings):
    """Secrets and environment-specific settings loaded from .env file."""

    bot_token: str
    bot_username: str = "koreanstory_bot"
    keycrm_api_key: str
    shopify_api_token: str | None = None
    shopify_store_url: str | None = None
    admin_user_ids: str = ""
    # One key or several, comma-separated. Six legal entities ship orders, but
    # any one key tracks any parcel when the phone is supplied (measured), so one
    # is enough; more are accepted purely as failover. See NovaPoshtaClient.
    novaposhta_api_key: str | None = None
    # Accepted as well, because the plural is the natural thing to write once
    # there is more than one key — and an unknown variable name is silently
    # ignored, so the misspelling would have started the bot with no Nova Poshta
    # at all and said nothing.
    novaposhta_api_keys: str | None = None
    # INFO in production. DEBUG prints, among other things, the buyer-profile
    # sync failures — see bot/logs.py for what happens to numbers on the way out.
    # Path to the SQLite file. Read here rather than in bot/db.py, which took it
    # from os.getenv at import time — the one place outside this module that
    # touched the environment. The practical symptom was that BOT_DB_PATH set in
    # .env worked in Docker and did nothing locally: compose injects env_file as
    # real variables, while pydantic-settings reads .env and never exports it.
    bot_db_path: str = "bot_data.db"
    log_level: str = "INFO"
    # Key for the phone digests in the log. Without it the mask is reversible:
    # the Ukrainian mobile space is ~10^9 numbers and the digest is 24 bits, so
    # the whole space enumerates in seconds. Only matters once logs leave this
    # machine — which is what Sentry in the plan means. Any long random string;
    # changing it renumbers every digest, so set it once.
    log_phone_salt: str = ""

    @property
    def novaposhta_keys(self) -> list[str]:
        """Every configured Nova Poshta key, in the order they were given.

        Reads either spelling of the variable; duplicates are dropped so setting
        both does not make the client try the same key twice.
        """
        raw = ",".join(
            part for part in (self.novaposhta_api_key, self.novaposhta_api_keys) if part
        )
        keys: list[str] = []
        for key in (k.strip() for k in raw.split(",")):
            # A list is naturally written as [a,b,c], and left as-is the brackets
            # would silently corrupt the first and last key — which fail as
            # "no access to this parcel" rather than as a configuration error.
            key = key.strip("[]'\"").strip()
            if key and key not in keys:
                keys.append(key)
        return keys

    @property
    def admin_ids(self) -> list[int]:
        """Parse comma-separated admin IDs into a list of integers."""
        if not self.admin_user_ids.strip():
            return []
        return [int(uid.strip()) for uid in self.admin_user_ids.split(",") if uid.strip()]

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }


@dataclass
class AppConfig:
    """Combined configuration: secrets from .env + settings from config.yaml."""

    env: EnvSettings
    brand_name: str
    website_url: str
    support_chat_id: int
    about_text: str
    contacts_text: str
    payment_text: str
    delivery_text: str


def load_config(config_path: str | Path = "config.yaml") -> AppConfig:
    """Load and return the full application configuration.

    Secrets are loaded from .env via pydantic-settings.
    Non-secret settings are loaded from config.yaml.
    """
    env = EnvSettings()

    config_file = Path(config_path)
    with config_file.open("r", encoding="utf-8") as f:
        yaml_data = yaml.safe_load(f)

    return AppConfig(
        env=env,
        brand_name=yaml_data["brand_name"],
        website_url=yaml_data["website_url"],
        support_chat_id=yaml_data["support_chat_id"],
        about_text=yaml_data.get("about_text", ""),
        contacts_text=yaml_data.get("contacts_text", ""),
        payment_text=yaml_data.get("payment_text", ""),
        delivery_text=yaml_data.get("delivery_text", ""),
    )
