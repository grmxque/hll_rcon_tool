from string import Formatter

from pydantic import (
    Field,
    HttpUrl,
    ValidationInfo,
    field_serializer,
    field_validator,
)

from rcon.user_config.utils import (
    BaseUserConfig,
    key_check,
    set_user_config,
    validate_game_roles,
)

ANNOUNCEMENT_PLACEHOLDERS = frozenset({"remaining_minutes", "attack_allowed_minutes"})
SANCTION_PLACEHOLDERS = ANNOUNCEMENT_PLACEHOLDERS | {
    "player_name",
    "team",
    "received_warnings",
    "max_warnings",
    "received_punishes",
    "max_punishes",
}
# Recon squads (sniper + its spotter) and tank crews legitimately operate deep
# behind enemy lines.
DEFAULT_IMMUNE_ROLES = ["spotter", "sniper", "tankcommander", "crewman"]


def _validate_placeholders(value: str, allowed: frozenset[str]) -> str:
    for _, name, spec, conversion in Formatter().parse(value):
        if name is not None and (name not in allowed or spec or conversion):
            raise ValueError("Unsupported message placeholder or format")
    return value


class AutoModAntiSteamrollUserConfig(BaseUserConfig):
    """Protects the losing team's last Warfare sector while the score is 4-1."""

    # This docstring is published in the JSON schema shown by the settings UI.
    # Unknown keys are ignored when loading (pydantic default), so a stored config
    # that still holds a removed or renamed field keeps its other values instead
    # of failing validation and silently falling back to the defaults (disabled).
    # API writes reject unknown keys in ``save_to_db``.

    NAME = "AutoModAntiSteamrollUserConfig"

    enabled: bool = Field(default=False, title="Enable Anti-Steamroll")
    dry_run: bool = Field(
        default=False,
        title="Dry-Run",
        description="Log and audit what would happen without messaging or sanctioning players.",
    )
    discord_webhook_url: HttpUrl | None = Field(
        default=None,
        title="Discord Webhook URL",
        description="Optional: posts protection announcements and every warning, punish and kick to this Discord channel.",
    )
    allow_final_attack_remaining_minutes: int = Field(
        default=45,
        ge=0,
        title="Allow Final Attack: Remaining Minutes",
        description="The last point is protected until the remaining match time reaches this value.",
    )
    min_players: int = Field(
        default=0,
        ge=0,
        le=100,
        title="Minimum Players",
        description="No protection below this number of connected players.",
    )
    immune_roles: list[str] = Field(
        default_factory=lambda: list(DEFAULT_IMMUNE_ROLES),
        title="Immune Roles",
        description="Players in these roles are never checked by Anti-Steamroll.",
    )
    winning_team_message: str = Field(
        default="Anti-Steamroll: the enemy's last point is protected until {attack_allowed_minutes} minutes remain. Do not enter their last sector, and please balance the teams if needed.",
        title="Winning Team Message",
        description="Sent once to the team holding 4 points when protection starts. Leave empty to disable.",
    )
    losing_team_message: str = Field(
        default="Anti-Steamroll: your last point is protected until {attack_allowed_minutes} minutes remain. Regroup, build garrisons behind your lines and push back.",
        title="Losing Team Message",
        description="Sent once to the team holding 1 point when protection starts. Leave empty to disable.",
    )
    protection_lifted_message: str = Field(
        default="Anti-Steamroll: {attack_allowed_minutes} minutes remain, the last point can now be attacked.",
        title="Protection Lifted Message",
        description="Sent once to all players when the remaining time is reached during a protection. Leave empty to disable.",
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
        default="Anti-Steamroll: {player_name}, leave the protected final enemy sector. Final attacks are allowed with {attack_allowed_minutes} minutes remaining.",
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

    @field_validator("immune_roles")
    @classmethod
    def validate_roles(cls, values, info: ValidationInfo):
        return validate_game_roles(values, info)

    @field_validator("warning_message", "punish_message", "kick_message")
    @classmethod
    def validate_sanction_message(cls, value: str) -> str:
        return _validate_placeholders(value, SANCTION_PLACEHOLDERS)

    @field_validator(
        "winning_team_message", "losing_team_message", "protection_lifted_message"
    )
    @classmethod
    def validate_announcement(cls, value: str) -> str:
        return _validate_placeholders(value, ANNOUNCEMENT_PLACEHOLDERS)

    @staticmethod
    def save_to_db(values: dict, dry_run=False):
        if isinstance(values, dict):
            key_check(
                frozenset(),
                frozenset(AutoModAntiSteamrollUserConfig.model_fields),
                values.keys(),
            )
        validated = AutoModAntiSteamrollUserConfig.model_validate(values)
        if not dry_run:
            set_user_config(validated.NAME, validated)
