from string import Formatter
from typing import Literal

from pydantic import Field, field_validator

from rcon.user_config.utils import BaseUserConfig, key_check, set_user_config

NEW_PLAYER_PLACEHOLDERS = frozenset({"player_name"})


class WelcomeStatsUserConfig(BaseUserConfig):
    """Sends each player their all-time stats privately when they connect."""

    # This docstring is published in the JSON schema shown by the settings UI.

    NAME = "WelcomeStatsUserConfig"

    enabled: bool = Field(default=False, title="Enable Welcome Stats")
    language: Literal["en", "fr"] = Field(
        default="en", title="Language", description="Language of the message labels."
    )
    delay_seconds: int = Field(
        default=20,
        ge=0,
        le=120,
        title="Delay (Seconds)",
        description="Wait after the connection so the player has loaded in and can see the message.",
    )
    min_games: int = Field(
        default=1,
        ge=1,
        title="Minimum Played Games",
        description="Players with fewer recorded games do not receive their stats.",
    )
    new_player_message: str = Field(
        default="",
        title="New Player Message",
        description="Sent instead of the stats to players below the minimum played games. Leave empty to disable. Placeholder: {player_name}.",
    )
    top_count: int = Field(
        default=3,
        ge=0,
        le=5,
        title="Victims / Nemesis Count",
        description="Number of most killed players and nemesis shown; 0 hides both sections.",
    )

    @field_validator("new_player_message")
    @classmethod
    def validate_new_player_message(cls, value: str) -> str:
        for _, name, spec, conversion in Formatter().parse(value):
            if name is not None and (
                name not in NEW_PLAYER_PLACEHOLDERS or spec or conversion
            ):
                raise ValueError("Unsupported message placeholder or format")
        return value

    @staticmethod
    def save_to_db(values: dict, dry_run=False):
        if isinstance(values, dict):
            key_check(
                frozenset(),
                frozenset(WelcomeStatsUserConfig.model_fields),
                values.keys(),
            )
        validated = WelcomeStatsUserConfig.model_validate(values)
        if not dry_run:
            set_user_config(validated.NAME, validated)
