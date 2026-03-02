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
