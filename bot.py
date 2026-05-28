import os
import socket
from typing import Optional

import truststore

# Force IPv4 globally to fix macOS VPN IPv6 blackholing timeouts.
_orig_getaddrinfo = socket.getaddrinfo


def ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)


socket.getaddrinfo = ipv4_getaddrinfo

# Inject native system certificates into ssl.
truststore.inject_into_ssl()

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from storage import (
    ANNOUNCEMENT_MODES,
    REWARD_MODES,
    delete_level_reward,
    get_command_usage,
    get_earned_level_rewards,
    get_faq_revision,
    get_faqs,
    get_guild_level_settings,
    get_leaderboard,
    get_level_rewards,
    get_user_level,
    get_user_rank,
    init_db,
    level_progress,
    migrate_json_files_if_needed,
    record_command_usage,
    record_message_xp,
    update_guild_level_settings,
    upsert_level_reward,
)
from version import APP_RELEASE_NOTES, APP_RELEASE_TITLE, APP_VERSION

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
SYNC_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
SYNC_MODE = os.getenv("DISCORD_SYNC_MODE", "guild").strip().lower()
PRESENCE_TYPE = os.getenv("DISCORD_PRESENCE_TYPE", "watching").strip().lower()
PRESENCE_TEXT = os.getenv(
    "DISCORD_PRESENCE_TEXT",
    f"/help | ConPass v{APP_VERSION}",
).strip()
PRESENCE_STATUS = os.getenv("DISCORD_STATUS", "online").strip().lower()


def format_usage_count(count):
    return f"{count} {'use' if count == 1 else 'uses'}"


def format_level_progress(progress):
    return f"{progress['xp_into_level']} / {progress['xp_needed']} XP"


def build_presence_status():
    status_map = {
        "online": discord.Status.online,
        "idle": discord.Status.idle,
        "dnd": discord.Status.dnd,
        "do_not_disturb": discord.Status.dnd,
        "invisible": discord.Status.invisible,
    }
    return status_map.get(PRESENCE_STATUS, discord.Status.online)


def build_presence_activity():
    if not PRESENCE_TEXT:
        return None

    activity_map = {
        "playing": discord.ActivityType.playing,
        "watching": discord.ActivityType.watching,
        "listening": discord.ActivityType.listening,
        "competing": discord.ActivityType.competing,
    }
    activity_type = activity_map.get(PRESENCE_TYPE, discord.ActivityType.watching)
    return discord.Activity(type=activity_type, name=PRESENCE_TEXT)


def parse_positive_int(value, label):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number.")
    if parsed <= 0:
        raise ValueError(f"{label} must be greater than 0.")
    return parsed


class ConPassBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True

        super().__init__(command_prefix="!", intents=intents)
        self.faq_command_names = set()
        self.faq_revision = None
        self.sync_guild = (
            discord.Object(id=int(SYNC_GUILD_ID))
            if SYNC_GUILD_ID and SYNC_GUILD_ID.isdigit()
            else None
        )
        self.ready_sync_done = False

    async def setup_hook(self):
        init_db()
        migrate_json_files_if_needed()
        await self.refresh_faq_commands(sync=True)
        self.faq_sync_loop.start()
        print("ConPass is ready to connect.", flush=True)
        print("------", flush=True)

    async def on_ready(self):
        await self.change_presence(
            status=build_presence_status(),
            activity=build_presence_activity(),
        )
        if not self.ready_sync_done:
            await self.sync_ready_guild_commands()
            self.ready_sync_done = True
        print(f"Logged in as {self.user} (ID: {self.user.id})", flush=True)
        print(
            f"Presence: {PRESENCE_STATUS} / {PRESENCE_TYPE} {PRESENCE_TEXT}",
            flush=True,
        )

    async def sync_commands_to_guild(self, guild):
        guild_object = discord.Object(id=guild.id)
        self.tree.clear_commands(guild=guild_object)
        self.tree.copy_global_to(guild=guild_object)
        synced = await self.tree.sync(guild=guild_object)
        guild_label = getattr(guild, "name", None) or guild.id
        print(f"Synced {len(synced)} commands to guild {guild_label}.", flush=True)

    async def sync_ready_guild_commands(self):
        if self.sync_guild:
            await self.sync_commands_to_guild(self.sync_guild)
            return

        if SYNC_MODE in {"guild", "guilds", "all"}:
            for guild in self.guilds:
                await self.sync_commands_to_guild(guild)

    async def sync_command_tree(self):
        if self.sync_guild:
            await self.sync_commands_to_guild(self.sync_guild)
            return

        synced = await self.tree.sync()
        print(f"Synced {len(synced)} global commands.", flush=True)

        if SYNC_MODE in {"guild", "guilds", "all"}:
            for guild in self.guilds:
                await self.sync_commands_to_guild(guild)

    async def refresh_faq_commands(self, *, sync=False):
        faqs = get_faqs()

        for command_name in list(self.faq_command_names):
            self.tree.remove_command(command_name)
        self.faq_command_names.clear()

        for command_name, answer in faqs.items():
            self.tree.add_command(
                app_commands.Command(
                    name=command_name,
                    description=f"Information about {command_name}",
                    callback=self.make_faq_callback(command_name, answer),
                ),
                override=True,
            )
            self.faq_command_names.add(command_name)

        self.faq_revision = get_faq_revision()
        if sync:
            await self.sync_command_tree()

    def make_faq_callback(self, command_name, answer):
        async def command_callback(interaction: discord.Interaction):
            record_command_usage(command_name)
            embed = discord.Embed(
                title=f"// {command_name.upper()}",
                description=answer,
                color=0xFCBA03,
            )
            if self.user and self.user.avatar:
                embed.set_thumbnail(url=self.user.avatar.url)
            await interaction.response.send_message(embed=embed)

        return command_callback

    @tasks.loop(seconds=10)
    async def faq_sync_loop(self):
        current_revision = get_faq_revision()
        if self.faq_revision is None:
            self.faq_revision = current_revision
            return
        if current_revision != self.faq_revision:
            print(
                f"FAQ revision changed: {self.faq_revision} -> {current_revision}",
                flush=True,
            )
            await self.refresh_faq_commands(sync=True)

    @faq_sync_loop.before_loop
    async def before_faq_sync_loop(self):
        await self.wait_until_ready()

    async def on_message(self, message):
        if message.author.bot:
            return

        if message.guild:
            result = record_message_xp(
                guild_id=message.guild.id,
                user_id=message.author.id,
                username=message.author.display_name,
                message_content=message.content,
            )

            if result["leveled_up"] and isinstance(message.author, discord.Member):
                assigned_roles, reward_errors = await apply_level_rewards(
                    message.author,
                    result["level"],
                    result["settings"],
                )
                await announce_level_up(message, result, assigned_roles, reward_errors)

        await self.process_commands(message)


bot = ConPassBot()


async def require_manage_guild(interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            "Level configuration is only available inside servers.",
            ephemeral=True,
        )
        return False

    permissions = getattr(interaction.user, "guild_permissions", None)
    if not permissions or not permissions.manage_guild:
        await interaction.response.send_message(
            "You need the Manage Server permission to change leveling settings.",
            ephemeral=True,
        )
        return False

    return True


async def apply_level_rewards(member, new_level, settings):
    rewards = get_earned_level_rewards(member.guild.id, new_level)
    if not rewards:
        return [], []

    assigned = []
    errors = []
    reward_mode = settings["reward_mode"]

    if reward_mode == "highest_only":
        highest_reward = max(rewards, key=lambda reward: int(reward["level"]))
        rewards_to_add = [highest_reward]
        rewards_to_remove = [
            reward for reward in rewards if reward["role_id"] != highest_reward["role_id"]
        ]
    else:
        rewards_to_add = rewards
        rewards_to_remove = []

    for reward in rewards_to_add:
        role = member.guild.get_role(int(reward["role_id"]))
        if not role:
            errors.append(f"Missing role ID {reward['role_id']}")
            continue
        if role in member.roles:
            continue
        try:
            await member.add_roles(role, reason=f"Reached level {new_level}")
            assigned.append(role.mention)
        except discord.Forbidden:
            errors.append(f"Missing permission or role hierarchy for {role.name}")
        except discord.HTTPException:
            errors.append(f"Could not assign {role.name}")

    for reward in rewards_to_remove:
        role = member.guild.get_role(int(reward["role_id"]))
        if not role or role not in member.roles:
            continue
        try:
            await member.remove_roles(role, reason=f"Reached level {new_level}")
        except discord.HTTPException:
            errors.append(f"Could not remove {role.name}")

    return assigned, errors


async def announce_level_up(message, result, assigned_roles, reward_errors):
    settings = result["settings"]
    if settings["announcement_mode"] == "silent":
        return

    target_channel = message.channel
    if settings["announcement_mode"] == "configured_channel":
        channel_id = settings.get("announcement_channel_id")
        configured_channel = None
        if channel_id:
            try:
                configured_channel = message.guild.get_channel(int(channel_id))
            except (TypeError, ValueError):
                configured_channel = None
        target_channel = configured_channel or message.channel

    content = (
        f"{message.author.mention} reached level {result['level']} "
        f"and earned {result['awarded_xp']} XP."
    )
    if assigned_roles:
        content += f" Reward: {', '.join(assigned_roles)}."
    if reward_errors:
        content += " Some role rewards need admin attention."

    try:
        await target_channel.send(content)
    except discord.HTTPException:
        pass


async def send_rank_response(interaction, member=None):
    if not interaction.guild:
        await interaction.response.send_message(
            "Levels are only tracked inside servers.",
            ephemeral=True,
        )
        return

    target = member or interaction.user
    settings = get_guild_level_settings(interaction.guild.id)
    row = get_user_level(interaction.guild.id, target.id)
    xp = int(row["xp"]) if row else 0
    messages = int(row["messages"]) if row else 0
    progress = level_progress(xp, settings)
    rank = get_user_rank(interaction.guild.id, target.id)

    embed = discord.Embed(
        title=f"// RANK {rank if rank else '-'}",
        description=f"{target.mention}'s server progress",
        color=0xFCBA03,
    )
    embed.add_field(name="Level", value=str(progress["level"]), inline=True)
    embed.add_field(name="XP", value=str(xp), inline=True)
    embed.add_field(name="Progress", value=format_level_progress(progress), inline=True)
    embed.add_field(name="Messages", value=str(messages), inline=True)

    avatar = getattr(target, "display_avatar", None)
    if avatar:
        embed.set_thumbnail(url=avatar.url)

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="help", description="Show all available FAQ commands")
async def help_command(interaction: discord.Interaction):
    record_command_usage("help")
    faqs = get_faqs()

    embed = discord.Embed(
        title="// COMMANDS",
        description="Welcome to the ConPass Help Menu! Here is a list of all available commands.",
        color=0xFCBA03,
    )

    if faqs:
        command_list = "\n".join([f"• `/{key}`" for key in faqs.keys()])
        embed.add_field(name="Available Topics", value=command_list, inline=False)
    else:
        embed.add_field(name="Available Topics", value="No topics found.", inline=False)

    embed.add_field(
        name="Community",
        value="• `/rank`\n• `/leaderboard`\n• `/insights`\n• `/version`",
        inline=False,
    )
    embed.add_field(
        name="Admin",
        value="• `/levelconfig status`\n• `/levelconfig reward list`",
        inline=False,
    )

    if bot.user and bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)
    embed.set_footer(text=f"ConPass v{APP_VERSION}")

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="insights", description="Show command usage insights")
async def insights_command(interaction: discord.Interaction):
    record_command_usage("insights")
    command_rows = get_command_usage()
    total_uses = sum(int(row["count"]) for row in command_rows)

    embed = discord.Embed(title="// INSIGHTS", color=0xFCBA03)
    embed.add_field(name="Total Command Uses", value=str(total_uses), inline=True)

    if command_rows:
        top_row = command_rows[0]
        embed.add_field(
            name="Most Used",
            value=f"`/{top_row['command_name']}` ({format_usage_count(top_row['count'])})",
            inline=True,
        )
        top_commands = "\n".join(
            f"• `/{row['command_name']}` - {format_usage_count(row['count'])}"
            for row in command_rows[:5]
        )
        embed.add_field(name="Top Commands", value=top_commands, inline=False)
    else:
        embed.add_field(name="Most Used", value="No command usage yet.", inline=True)

    if bot.user and bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="version", description="Show the current ConPass version")
async def version_command(interaction: discord.Interaction):
    record_command_usage("version")
    embed = discord.Embed(
        title=f"// CONPASS v{APP_VERSION}",
        description=APP_RELEASE_TITLE,
        color=0xFCBA03,
    )
    embed.add_field(name="Update Notes", value=APP_RELEASE_NOTES, inline=False)
    embed.add_field(
        name="Presence",
        value=f"`{PRESENCE_STATUS}` / `{PRESENCE_TYPE}` `{PRESENCE_TEXT}`",
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="rank", description="Show a member's current server rank")
@app_commands.describe(member="Choose a server member")
async def rank_command(
    interaction: discord.Interaction,
    member: Optional[discord.Member] = None,
):
    record_command_usage("rank")
    await send_rank_response(interaction, member)


@bot.tree.command(name="level", description="Alias for /rank")
@app_commands.describe(member="Choose a server member")
async def level_command(
    interaction: discord.Interaction,
    member: Optional[discord.Member] = None,
):
    record_command_usage("level")
    await send_rank_response(interaction, member)


@bot.tree.command(name="leaderboard", description="Show the server XP leaderboard")
@app_commands.describe(page="Leaderboard page number")
async def leaderboard_command(interaction: discord.Interaction, page: int = 1):
    record_command_usage("leaderboard")
    if not interaction.guild:
        await interaction.response.send_message(
            "Leaderboards are only available inside servers.",
            ephemeral=True,
        )
        return

    page = max(1, min(int(page), 100))
    per_page = 10
    offset = (page - 1) * per_page
    rows = get_leaderboard(interaction.guild.id, limit=per_page, offset=offset)
    embed = discord.Embed(title=f"// LEADERBOARD PAGE {page}", color=0xFCBA03)

    if rows:
        lines = []
        for index, row in enumerate(rows, start=offset + 1):
            lines.append(
                f"{index}. **{row['username']}** - Level {row['level']} ({row['xp']} XP)"
            )
        embed.description = "\n".join(lines)
    else:
        embed.description = "No XP has been recorded yet."

    await interaction.response.send_message(embed=embed)


levelconfig = app_commands.Group(
    name="levelconfig",
    description="Configure ConPass leveling",
    default_permissions=discord.Permissions(manage_guild=True),
)


@levelconfig.command(name="status", description="Show server leveling settings")
async def levelconfig_status(interaction: discord.Interaction):
    record_command_usage("levelconfig_status")
    if not await require_manage_guild(interaction):
        return

    settings = get_guild_level_settings(interaction.guild.id)
    rewards = get_level_rewards(interaction.guild.id)
    reward_lines = [
        f"Level {reward['level']}: <@&{reward['role_id']}>" for reward in rewards[:10]
    ]

    embed = discord.Embed(title="// LEVEL CONFIG", color=0xFCBA03)
    embed.add_field(
        name="XP",
        value=(
            f"Enabled: `{bool(settings['leveling_enabled'])}`\n"
            f"Range: `{settings['xp_min']}-{settings['xp_max']}`\n"
            f"Cooldown: `{settings['cooldown_seconds']}s`\n"
            f"Min Length: `{settings['min_message_length']}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Model",
        value=(
            f"`{settings['level_model']}` "
            f"q=`{settings['curve_quadratic']}`, "
            f"l=`{settings['curve_linear']}`, "
            f"base=`{settings['curve_base']}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Announcements",
        value=(
            f"Mode: `{settings['announcement_mode']}`\n"
            f"Channel: `{settings['announcement_channel_id'] or 'none'}`"
        ),
        inline=False,
    )
    embed.add_field(
        name="Rewards",
        value=(
            f"Mode: `{settings['reward_mode']}`\n"
            + ("\n".join(reward_lines) if reward_lines else "No role rewards configured.")
        ),
        inline=False,
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@levelconfig.command(name="toggle", description="Enable or disable leveling")
async def levelconfig_toggle(interaction: discord.Interaction, enabled: bool):
    record_command_usage("levelconfig_toggle")
    if not await require_manage_guild(interaction):
        return

    settings = update_guild_level_settings(
        interaction.guild.id,
        leveling_enabled=1 if enabled else 0,
    )
    await interaction.response.send_message(
        f"Leveling enabled: `{bool(settings['leveling_enabled'])}`",
        ephemeral=True,
    )


@levelconfig.command(name="xp", description="Set XP awarded per eligible message")
@app_commands.rename(minimum="min", maximum="max")
async def levelconfig_xp(interaction: discord.Interaction, minimum: int, maximum: int):
    record_command_usage("levelconfig_xp")
    if not await require_manage_guild(interaction):
        return

    try:
        settings = update_guild_level_settings(
            interaction.guild.id,
            xp_min=minimum,
            xp_max=maximum,
        )
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    await interaction.response.send_message(
        f"XP range set to `{settings['xp_min']}-{settings['xp_max']}`.",
        ephemeral=True,
    )


@levelconfig.command(name="cooldown", description="Set the XP cooldown in seconds")
async def levelconfig_cooldown(interaction: discord.Interaction, seconds: int):
    record_command_usage("levelconfig_cooldown")
    if not await require_manage_guild(interaction):
        return

    try:
        settings = update_guild_level_settings(
            interaction.guild.id,
            cooldown_seconds=seconds,
        )
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    await interaction.response.send_message(
        f"XP cooldown set to `{settings['cooldown_seconds']}s`.",
        ephemeral=True,
    )


@levelconfig.command(
    name="min-message-length",
    description="Set the minimum message length that can earn XP",
)
async def levelconfig_min_message_length(
    interaction: discord.Interaction,
    characters: int,
):
    record_command_usage("levelconfig_min_message_length")
    if not await require_manage_guild(interaction):
        return

    try:
        settings = update_guild_level_settings(
            interaction.guild.id,
            min_message_length=characters,
        )
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    await interaction.response.send_message(
        f"Minimum message length set to `{settings['min_message_length']}`.",
        ephemeral=True,
    )


@levelconfig.command(name="model", description="Set the quadratic leveling model")
async def levelconfig_model(
    interaction: discord.Interaction,
    quadratic: int,
    linear: int,
    base: int,
):
    record_command_usage("levelconfig_model")
    if not await require_manage_guild(interaction):
        return

    try:
        settings = update_guild_level_settings(
            interaction.guild.id,
            curve_quadratic=quadratic,
            curve_linear=linear,
            curve_base=base,
        )
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    await interaction.response.send_message(
        (
            "Level model updated: "
            f"`{settings['curve_quadratic']}*level^2 + "
            f"{settings['curve_linear']}*level + {settings['curve_base']}`."
        ),
        ephemeral=True,
    )


@levelconfig.command(name="announcements", description="Configure level-up announcements")
@app_commands.choices(
    mode=[
        app_commands.Choice(name="current_channel", value="current_channel"),
        app_commands.Choice(name="configured_channel", value="configured_channel"),
        app_commands.Choice(name="silent", value="silent"),
    ]
)
async def levelconfig_announcements(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str],
    channel: Optional[discord.TextChannel] = None,
):
    record_command_usage("levelconfig_announcements")
    if not await require_manage_guild(interaction):
        return

    selected_mode = mode.value
    if selected_mode not in ANNOUNCEMENT_MODES:
        await interaction.response.send_message("Invalid announcement mode.", ephemeral=True)
        return
    if selected_mode == "configured_channel" and channel is None:
        await interaction.response.send_message(
            "Choose a channel when using configured_channel mode.",
            ephemeral=True,
        )
        return

    channel_id = str(channel.id) if selected_mode == "configured_channel" and channel else None
    settings = update_guild_level_settings(
        interaction.guild.id,
        announcement_mode=selected_mode,
        announcement_channel_id=channel_id,
    )
    channel_suffix = (
        f" in <#{settings['announcement_channel_id']}>"
        if settings["announcement_channel_id"]
        else ""
    )
    await interaction.response.send_message(
        f"Announcement mode set to `{settings['announcement_mode']}`{channel_suffix}.",
        ephemeral=True,
    )


@levelconfig.command(name="reward-mode", description="Set how level reward roles behave")
@app_commands.choices(
    mode=[
        app_commands.Choice(name="stack", value="stack"),
        app_commands.Choice(name="highest_only", value="highest_only"),
    ]
)
async def levelconfig_reward_mode(
    interaction: discord.Interaction,
    mode: app_commands.Choice[str],
):
    record_command_usage("levelconfig_reward_mode")
    if not await require_manage_guild(interaction):
        return

    selected_mode = mode.value
    if selected_mode not in REWARD_MODES:
        await interaction.response.send_message("Invalid reward mode.", ephemeral=True)
        return

    settings = update_guild_level_settings(
        interaction.guild.id,
        reward_mode=selected_mode,
    )
    await interaction.response.send_message(
        f"Reward mode set to `{settings['reward_mode']}`.",
        ephemeral=True,
    )


reward_group = app_commands.Group(name="reward", description="Manage level role rewards")


@reward_group.command(name="add", description="Add or replace a level role reward")
async def levelconfig_reward_add(
    interaction: discord.Interaction,
    level: int,
    role: discord.Role,
):
    record_command_usage("levelconfig_reward_add")
    if not await require_manage_guild(interaction):
        return

    try:
        upsert_level_reward(interaction.guild.id, level, role.id)
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    await interaction.response.send_message(
        f"Level `{level}` reward set to {role.mention}.",
        ephemeral=True,
    )


@reward_group.command(name="remove", description="Remove a level role reward")
async def levelconfig_reward_remove(interaction: discord.Interaction, level: int):
    record_command_usage("levelconfig_reward_remove")
    if not await require_manage_guild(interaction):
        return

    try:
        parse_positive_int(level, "Reward level")
    except ValueError as exc:
        await interaction.response.send_message(str(exc), ephemeral=True)
        return

    delete_level_reward(interaction.guild.id, level)
    await interaction.response.send_message(
        f"Removed the level `{level}` reward mapping.",
        ephemeral=True,
    )


@reward_group.command(name="list", description="List level role rewards")
async def levelconfig_reward_list(interaction: discord.Interaction):
    record_command_usage("levelconfig_reward_list")
    if not await require_manage_guild(interaction):
        return

    rewards = get_level_rewards(interaction.guild.id)
    if rewards:
        lines = [
            f"• Level `{reward['level']}`: <@&{reward['role_id']}>"
            for reward in rewards
        ]
        message = "\n".join(lines)
    else:
        message = "No level role rewards configured."

    await interaction.response.send_message(message, ephemeral=True)


levelconfig.add_command(reward_group)
bot.tree.add_command(levelconfig)


if __name__ == "__main__":
    if not TOKEN or TOKEN == "your_discord_bot_token_here":
        print("Error: DISCORD_TOKEN not properly configured in .env file.")
    else:
        bot.run(TOKEN)
