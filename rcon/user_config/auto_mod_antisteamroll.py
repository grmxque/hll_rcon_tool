from string import Formatter

from pydantic import ConfigDict, Field, HttpUrl, field_serializer, field_validator

from rcon.user_config.utils import BaseUserConfig, set_user_config


class AutoModAntiSteamrollUserConfig(BaseUserConfig):
    """Live settings for protecting the losing team's final Warfare sector."""

    NAME = "AutoModAntiSteamrollUserConfig"
    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(default=False, title="Enable Anti-Steamroll")
    dry_run: bool = Field(
        default=False,
        title="Dry-Run",
        description="Record proposed actions in audit logs without messaging or sanctioning players.",
    )
    discord_webhook_url: HttpUrl | None = Field(
        default=None, title="Discord Webhook URL"
    )
    caps_threshold: int = Field(
        default=4,
        ge=3,
        le=4,
        title="Caps Threshold",
        description="Protect the final enemy capture sector when the leading team owns at least this many caps. Warfare only.",
    )
    allow_final_attack_remaining_minutes: int = Field(
        default=30,
        ge=0,
        title="Allow Attack: Remaining Minutes",
        description="Release protection when remaining match time is at or below this value.",
    )
    protection_duration_minutes: int = Field(
        default=0,
        ge=0,
        title="Maximum Protection Duration (Minutes)",
        description="Release after this many minutes of continuous dominance; zero protects until the remaining-time threshold. Score changes reset the duration.",
    )
    min_players: int = Field(default=0, ge=0, le=100, title="Minimum Players")
    boundary_margin_units: float = Field(
        default=1000,
        ge=0,
        le=10000,
        title="Boundary Margin (World Units)",
        description="Inset on all edges of the final capture sector to avoid border false positives. Standard grid cells are 20000 world units wide.",
    )
    entry_grace_seconds: int = Field(
        default=10,
        ge=5,
        title="Confirmed Presence (Seconds)",
        description="Continuous observed presence required before warnings or sanctions; checks run approximately every five seconds.",
    )
    violation_reset_seconds: int = Field(
        default=120,
        ge=15,
        title="Escalation Reset (Seconds)",
        description="Forget prior warnings and punishments after this long outside the protected interior. Re-entry always requires a new presence confirmation.",
    )
    number_of_warnings: int = Field(
        default=2,
        ge=-1,
        title="Number of Warnings",
        description="Zero skips warnings; -1 warns indefinitely.",
    )
    warning_interval_seconds: int = Field(
        default=15, ge=5, title="Warning Interval (Seconds)"
    )
    warning_message: str = Field(
        default="Anti-Steamroll: {player_name}, leave the protected final enemy sector. Final attacks are allowed with {remaining_minutes} minutes remaining.",
        title="Warning Message",
    )
    number_of_punishments: int = Field(
        default=2,
        ge=-1,
        title="Number of Punishments",
        description="Zero skips punishments; -1 punishes indefinitely.",
    )
    punish_interval_seconds: int = Field(
        default=15, ge=5, title="Punish Interval (Seconds)"
    )
    punish_message: str = Field(
        default="Anti-Steamroll: you entered the protected final enemy sector ({received_punishes}/{max_punishes}).",
        title="Punish Message",
    )
    kick_after_max_punish: bool = Field(default=True, title="Enable Kick")
    kick_grace_period_seconds: int = Field(
        default=30, ge=5, title="Kick Grace Period (Seconds)"
    )
    kick_message: str = Field(
        default="Anti-Steamroll: you remained in the protected final enemy sector after warnings and punishments.",
        title="Kick Message",
    )

    @field_serializer("discord_webhook_url")
    def serialize_webhook(self, value: HttpUrl | None):
        return str(value) if value is not None else None

    @field_validator("warning_message", "punish_message", "kick_message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        allowed = {
            "player_name",
            "team",
            "remaining_minutes",
            "received_warnings",
            "max_warnings",
            "received_punishes",
            "max_punishes",
        }
        for _, name, spec, conversion in Formatter().parse(value):
            if name is not None and (name not in allowed or spec or conversion):
                raise ValueError("Unsupported message placeholder or format")
        return value

    @staticmethod
    def save_to_db(values: dict, dry_run=False):
        validated = AutoModAntiSteamrollUserConfig.model_validate(values)
        if not dry_run:
            set_user_config(validated.NAME, validated)
