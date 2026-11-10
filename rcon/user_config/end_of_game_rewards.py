from typing import Literal

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

TopCategory = Literal["combat", "offense", "defense", "support"]


class EndOfGameRewardsUserConfig(BaseUserConfig):
    """Sends end-of-game stats to connected players and rewards the best ones with VIP days."""

    # This docstring is published in the JSON schema shown by the settings UI.

    NAME = "EndOfGameRewardsUserConfig"

    enabled: bool = Field(default=False, title="Enable End of Game Rewards")
    dry_run: bool = Field(
        default=False,
        title="Dry-Run",
        description="Log, audit and post to Discord what would happen, without messaging players or granting VIP.",
    )
    discord_webhook_url: HttpUrl | None = Field(
        default=None,
        title="Discord Webhook URL",
        description="Optional: posts every VIP reward to this Discord channel.",
    )
    language: Literal["en", "fr"] = Field(
        default="en", title="Language", description="Language of the message labels."
    )
    send_stats_message: bool = Field(
        default=True,
        title="Send Stats Message",
        description="Send each connected player the best scores, their own stats and their VIP reward.",
    )
    vip_rewards_enabled: bool = Field(default=True, title="Enable VIP Rewards")
    min_players_for_rewards: int = Field(
        default=50,
        ge=0,
        le=100,
        title="Minimum Players for VIP Rewards",
        description="No VIP reward when fewer players are connected at the end of the game.",
    )
    top_categories: list[TopCategory] = Field(
        default_factory=lambda: ["offense", "defense", "support", "combat"],
        title="Best Score Categories",
        description="The best player of each category is announced and rewarded. Allowed: combat, offense, defense, support.",
    )
    top_category_reward_days: int = Field(
        default=4, ge=0, title="Best Score Reward (VIP Days)"
    )
    individual_reward_days: int = Field(
        default=2,
        ge=0,
        title="Individual Threshold Reward (VIP Days)",
        description="Granted for each individual threshold reached; rewards add up.",
    )
    kills_threshold: int = Field(
        default=70, ge=0, title="Kills Threshold", description="0 disables this rule."
    )
    kills_streak_threshold: int = Field(
        default=10,
        ge=0,
        title="Kill Streak Threshold",
        description="0 disables this rule.",
    )
    kills_per_minute_threshold: float = Field(
        default=0.75,
        ge=0,
        title="Kills per Minute Threshold",
        description="0 disables this rule.",
    )
    kill_death_ratio_threshold: float = Field(
        default=1.5,
        ge=0,
        title="Kill/Death Ratio Threshold",
        description="0 disables this rule.",
    )
    kill_death_ratio_min_kills: int = Field(
        default=30,
        ge=0,
        title="Kill/Death Ratio: Minimum Kills",
        description="The kill/death ratio rule only applies from this number of kills.",
    )
    fidelity_reward_days: int = Field(
        default=0,
        ge=0,
        title="Fidelity Reward (VIP Days)",
        description="Granted to players who are already VIP (not unlimited).",
    )
    garrison_rewards_enabled: bool = Field(
        default=False,
        title="Top 3 Garrison Builders",
        description="Reward the 3 players who built the most garrisons (detected from the support score, see docs/actions-tracking.md). Ties: the first to reach the count wins.",
    )
    garrison_reward_days: int = Field(
        default=1, ge=0, title="Top 3 Garrison Builders Reward (VIP Days)"
    )
    node_rewards_enabled: bool = Field(
        default=False,
        title="Thank the Node Builders",
        description="Reward every player who built at least one resource node.",
    )
    node_reward_days: int = Field(
        default=1, ge=0, title="Node Builders Reward (VIP Days)"
    )
    garrison_builder_roles: list[str] = Field(
        default_factory=lambda: ["officer", "armycommander"],
        title="Garrison Builder Roles",
        description="Roles able to build a garrison (officer = squad leader). Several commander abilities give the same +50 support as a garrison: a commander's garrison only counts once teammates spawn on it.",
    )
    node_builder_roles: list[str] = Field(
        default_factory=lambda: ["engineer"],
        title="Node Builder Roles",
        description="Roles able to build a resource node.",
    )
    thanks_banner_enabled: bool = Field(
        default=False,
        title="Construction Thanks Banner",
        description="During the match, after a new detected construction, broadcast the builders with their count (garrisons, then nodes), then restore the previous broadcast.",
    )
    thanks_banner_interval_minutes: int = Field(
        default=5,
        ge=1,
        le=60,
        title="Thanks Banner: Minimum Minutes Between Two Banners",
    )
    thanks_banner_seconds: int = Field(
        default=30,
        ge=5,
        le=120,
        title="Thanks Banner: Seconds per Message",
    )
    record_actions_snapshots: bool = Field(
        default=False,
        title="Record Actions Snapshots",
        description="Write the polled player data of each match to a JSONL file in the logs folder, to replay and check the detection (python -m rcon.actions_tracking replay).",
    )
    vip_description: str = Field(
        default="Top stats VIP",
        min_length=1,
        title="VIP Description",
        description='Stored with the VIP as "<player name> - <description>".',
    )

    @property
    def actions_tracking_enabled(self) -> bool:
        return self.enabled and (
            self.garrison_rewards_enabled
            or self.node_rewards_enabled
            or self.thanks_banner_enabled
            or self.record_actions_snapshots
        )

    @field_validator("garrison_builder_roles", "node_builder_roles")
    @classmethod
    def validate_builder_roles(cls, values, info: ValidationInfo):
        return validate_game_roles(values, info)

    @field_serializer("discord_webhook_url")
    def serialize_webhook(self, value: HttpUrl | None):
        return str(value) if value is not None else None

    @staticmethod
    def save_to_db(values: dict, dry_run=False):
        if isinstance(values, dict):
            key_check(
                frozenset(),
                frozenset(EndOfGameRewardsUserConfig.model_fields),
                values.keys(),
            )
        validated = EndOfGameRewardsUserConfig.model_validate(values)
        if not dry_run:
            set_user_config(validated.NAME, validated)
