import json
import os
import random
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_FILE = BASE_DIR / "conpass.sqlite3"
FAQ_JSON_FILE = BASE_DIR / "faq.json"
USAGE_JSON_FILE = BASE_DIR / "usage_stats.json"

RESERVED_COMMAND_NAMES = {
    "help",
    "insights",
    "me",
    "profile",
    "rank",
    "level",
    "leaderboard",
    "top",
    "stats",
    "levelconfig",
    "version",
}

ANNOUNCEMENT_MODES = {"current_channel", "configured_channel", "silent"}
REWARD_MODES = {"stack", "highest_only"}
LEVEL_MODELS = {"quadratic"}
CHANNEL_ID_SPLIT_RE = re.compile(r"[\s,;]+")

DEFAULT_LEVEL_SETTINGS = {
    "leveling_enabled": 1,
    "xp_min": 15,
    "xp_max": 25,
    "cooldown_seconds": 60,
    "min_message_length": 4,
    "level_model": "quadratic",
    "curve_quadratic": 5,
    "curve_linear": 50,
    "curve_base": 100,
    "announcement_mode": "current_channel",
    "announcement_channel_id": None,
    "reward_mode": "stack",
    "excluded_channel_ids": "",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def utc_today():
    return datetime.now(timezone.utc).date().isoformat()


def period_start_date(period_days):
    if not period_days:
        return None
    days = max(1, int(period_days))
    return (datetime.now(timezone.utc).date() - timedelta(days=days - 1)).isoformat()


def parse_timestamp(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def connect():
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def table_columns(conn, table_name):
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {row["name"] for row in rows}


def ensure_column(conn, table_name, column_name, ddl):
    if column_name not in table_columns(conn, table_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {ddl}")


def init_db():
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS faqs (
                command_name TEXT PRIMARY KEY,
                answer TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS command_usage (
                command_name TEXT PRIMARY KEY,
                count INTEGER NOT NULL DEFAULT 0,
                last_used_at TEXT
            );

            CREATE TABLE IF NOT EXISTS guilds (
                guild_id TEXT PRIMARY KEY,
                guild_name TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS guild_level_settings (
                guild_id TEXT PRIMARY KEY,
                leveling_enabled INTEGER NOT NULL DEFAULT 1,
                xp_min INTEGER NOT NULL DEFAULT 15,
                xp_max INTEGER NOT NULL DEFAULT 25,
                cooldown_seconds INTEGER NOT NULL DEFAULT 60,
                min_message_length INTEGER NOT NULL DEFAULT 4,
                level_model TEXT NOT NULL DEFAULT 'quadratic',
                curve_quadratic INTEGER NOT NULL DEFAULT 5,
                curve_linear INTEGER NOT NULL DEFAULT 50,
                curve_base INTEGER NOT NULL DEFAULT 100,
                announcement_mode TEXT NOT NULL DEFAULT 'current_channel',
                announcement_channel_id TEXT,
                reward_mode TEXT NOT NULL DEFAULT 'stack',
                excluded_channel_ids TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_levels (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                xp INTEGER NOT NULL DEFAULT 0,
                level INTEGER NOT NULL DEFAULT 0,
                messages INTEGER NOT NULL DEFAULT 0,
                voice_seconds INTEGER NOT NULL DEFAULT 0,
                voice_xp_remainder_seconds INTEGER NOT NULL DEFAULT 0,
                last_xp_at TEXT,
                last_voice_xp_at TEXT,
                created_at TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS level_role_rewards (
                guild_id TEXT NOT NULL,
                level INTEGER NOT NULL,
                role_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, level)
            );

            CREATE TABLE IF NOT EXISTS message_activity (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                activity_date TEXT NOT NULL,
                messages INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id, activity_date)
            );

            CREATE TABLE IF NOT EXISTS user_channel_activity (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                channel_name TEXT NOT NULL,
                activity_date TEXT NOT NULL,
                messages INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id, channel_id, activity_date)
            );

            CREATE TABLE IF NOT EXISTS voice_activity (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                activity_date TEXT NOT NULL,
                seconds INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id, activity_date)
            );

            CREATE TABLE IF NOT EXISTS user_voice_channel_activity (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                channel_name TEXT NOT NULL,
                activity_date TEXT NOT NULL,
                seconds INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id, channel_id, activity_date)
            );

            CREATE TABLE IF NOT EXISTS guild_admins (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );

            CREATE TABLE IF NOT EXISTS guild_channels (
                guild_id TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                channel_name TEXT NOT NULL,
                channel_type TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, channel_id)
            );

            CREATE TABLE IF NOT EXISTS guild_roles (
                guild_id TEXT NOT NULL,
                role_id TEXT NOT NULL,
                role_name TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, role_id)
            );

            CREATE TABLE IF NOT EXISTS guild_members (
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                username TEXT NOT NULL,
                display_name TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            );
            """
        )

        ensure_column(conn, "user_levels", "created_at", "created_at TEXT")
        ensure_column(
            conn,
            "user_levels",
            "voice_seconds",
            "voice_seconds INTEGER NOT NULL DEFAULT 0",
        )
        ensure_column(
            conn,
            "user_levels",
            "voice_xp_remainder_seconds",
            "voice_xp_remainder_seconds INTEGER NOT NULL DEFAULT 0",
        )
        ensure_column(
            conn,
            "user_levels",
            "last_voice_xp_at",
            "last_voice_xp_at TEXT",
        )
        ensure_column(
            conn,
            "guild_level_settings",
            "leveling_enabled",
            "leveling_enabled INTEGER NOT NULL DEFAULT 1",
        )
        ensure_column(
            conn,
            "guild_level_settings",
            "xp_min",
            "xp_min INTEGER NOT NULL DEFAULT 15",
        )
        ensure_column(
            conn,
            "guild_level_settings",
            "xp_max",
            "xp_max INTEGER NOT NULL DEFAULT 25",
        )
        ensure_column(
            conn,
            "guild_level_settings",
            "cooldown_seconds",
            "cooldown_seconds INTEGER NOT NULL DEFAULT 60",
        )
        ensure_column(
            conn,
            "guild_level_settings",
            "min_message_length",
            "min_message_length INTEGER NOT NULL DEFAULT 4",
        )
        ensure_column(
            conn,
            "guild_level_settings",
            "level_model",
            "level_model TEXT NOT NULL DEFAULT 'quadratic'",
        )
        ensure_column(
            conn,
            "guild_level_settings",
            "curve_quadratic",
            "curve_quadratic INTEGER NOT NULL DEFAULT 5",
        )
        ensure_column(
            conn,
            "guild_level_settings",
            "curve_linear",
            "curve_linear INTEGER NOT NULL DEFAULT 50",
        )
        ensure_column(
            conn,
            "guild_level_settings",
            "curve_base",
            "curve_base INTEGER NOT NULL DEFAULT 100",
        )
        ensure_column(
            conn,
            "guild_level_settings",
            "announcement_mode",
            "announcement_mode TEXT NOT NULL DEFAULT 'current_channel'",
        )
        ensure_column(
            conn,
            "guild_level_settings",
            "announcement_channel_id",
            "announcement_channel_id TEXT",
        )
        ensure_column(
            conn,
            "guild_level_settings",
            "reward_mode",
            "reward_mode TEXT NOT NULL DEFAULT 'stack'",
        )
        ensure_column(
            conn,
            "guild_level_settings",
            "excluded_channel_ids",
            "excluded_channel_ids TEXT NOT NULL DEFAULT ''",
        )

        conn.execute(
            "INSERT OR IGNORE INTO app_state (key, value) VALUES ('faq_revision', '0')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO app_state (key, value) VALUES ('json_migrated', '0')"
        )


def get_state(key, default=None):
    with connect() as conn:
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?",
            (key,),
        ).fetchone()
    return row["value"] if row else default


def set_state(key, value):
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO app_state (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, str(value)),
        )


def touch_faq_revision(conn):
    current = conn.execute(
        "SELECT value FROM app_state WHERE key = 'faq_revision'"
    ).fetchone()
    revision = int(current["value"]) if current else 0
    conn.execute(
        """
        INSERT INTO app_state (key, value)
        VALUES ('faq_revision', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (str(revision + 1),),
    )


def get_faq_revision():
    return int(get_state("faq_revision", "0"))


def clean_command_name(value):
    name = value.strip().lower().replace(" ", "_")
    name = re.sub(r"[^a-z0-9_-]+", "", name)
    name = re.sub(r"_+", "_", name).strip("_-")
    name = name[:32].strip("_-")

    if not name:
        raise ValueError("Command name must include at least one letter or number.")
    if name in RESERVED_COMMAND_NAMES:
        raise ValueError(f"/{name} is a built-in command name.")

    return name


def get_faqs():
    with connect() as conn:
        rows = conn.execute(
            "SELECT command_name, answer FROM faqs ORDER BY command_name"
        ).fetchall()
    return {row["command_name"]: row["answer"] for row in rows}


def upsert_faq(command_name, answer):
    clean_name = clean_command_name(command_name)
    clean_answer = answer.strip()
    if not clean_answer:
        raise ValueError("FAQ answer cannot be empty.")

    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO faqs (command_name, answer, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(command_name) DO UPDATE SET
                answer = excluded.answer,
                updated_at = excluded.updated_at
            """,
            (clean_name, clean_answer, now, now),
        )
        touch_faq_revision(conn)
    return clean_name


def delete_faq(command_name):
    with connect() as conn:
        conn.execute("DELETE FROM faqs WHERE command_name = ?", (command_name,))
        touch_faq_revision(conn)


def record_command_usage(command_name):
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO command_usage (command_name, count, last_used_at)
            VALUES (?, 1, ?)
            ON CONFLICT(command_name) DO UPDATE SET
                count = command_usage.count + 1,
                last_used_at = excluded.last_used_at
            """,
            (command_name, now),
        )


def normalize_channel_ids(value):
    if value is None:
        return ""

    if isinstance(value, (list, tuple, set)):
        raw_ids = [str(item) for item in value]
    else:
        raw_ids = CHANNEL_ID_SPLIT_RE.split(str(value).strip())

    channel_ids = []
    seen = set()
    for raw_id in raw_ids:
        channel_id = str(raw_id).strip()
        if not channel_id:
            continue
        if not channel_id.isdigit():
            raise ValueError("Excluded channel IDs must be Discord numeric IDs.")
        if channel_id not in seen:
            channel_ids.append(channel_id)
            seen.add(channel_id)
    return ",".join(channel_ids)


def channel_is_excluded(settings, channel_id):
    if channel_id is None:
        return False
    excluded_channel_ids = {
        value
        for value in str(settings.get("excluded_channel_ids", "")).split(",")
        if value
    }
    return str(channel_id) in excluded_channel_ids


def get_command_usage():
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT command_name, count, last_used_at
            FROM command_usage
            ORDER BY count DESC, last_used_at DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def normalize_settings(row=None):
    settings = dict(DEFAULT_LEVEL_SETTINGS)
    if row:
        settings.update(dict(row))
    settings["leveling_enabled"] = int(settings["leveling_enabled"])
    settings["xp_min"] = int(settings["xp_min"])
    settings["xp_max"] = int(settings["xp_max"])
    settings["cooldown_seconds"] = int(settings["cooldown_seconds"])
    settings["min_message_length"] = int(settings["min_message_length"])
    settings["curve_quadratic"] = int(settings["curve_quadratic"])
    settings["curve_linear"] = int(settings["curve_linear"])
    settings["curve_base"] = int(settings["curve_base"])
    settings["excluded_channel_ids"] = normalize_channel_ids(
        settings.get("excluded_channel_ids")
    )
    return settings


def validate_level_settings(settings):
    if settings["xp_min"] <= 0:
        raise ValueError("Minimum XP must be greater than 0.")
    if settings["xp_max"] < settings["xp_min"]:
        raise ValueError("Maximum XP must be greater than or equal to minimum XP.")
    if settings["cooldown_seconds"] < 0:
        raise ValueError("Cooldown cannot be negative.")
    if settings["min_message_length"] < 0:
        raise ValueError("Minimum message length cannot be negative.")
    if settings["level_model"] not in LEVEL_MODELS:
        raise ValueError("Only the quadratic level model is supported in v1.")
    if settings["curve_quadratic"] < 0 or settings["curve_linear"] < 0:
        raise ValueError("Curve quadratic and linear values cannot be negative.")
    if settings["curve_base"] <= 0:
        raise ValueError("Curve base must be greater than 0.")
    if settings["announcement_mode"] not in ANNOUNCEMENT_MODES:
        raise ValueError("Announcement mode must be current_channel, configured_channel, or silent.")
    if settings["reward_mode"] not in REWARD_MODES:
        raise ValueError("Reward mode must be stack or highest_only.")
    if xp_needed_for_next_level(0, settings) <= 0:
        raise ValueError("Level model must produce positive XP requirements.")


def get_guild_level_settings(guild_id):
    guild_key = str(guild_id)
    with connect() as conn:
        row = conn.execute(
            """
            SELECT *
            FROM guild_level_settings
            WHERE guild_id = ?
            """,
            (guild_key,),
        ).fetchone()
    return normalize_settings(row)


def ensure_guild_level_settings(conn, guild_id):
    guild_key = str(guild_id)
    row = conn.execute(
        "SELECT * FROM guild_level_settings WHERE guild_id = ?",
        (guild_key,),
    ).fetchone()
    if row:
        return normalize_settings(row)

    now = utc_now()
    settings = dict(DEFAULT_LEVEL_SETTINGS)
    conn.execute(
        """
        INSERT INTO guild_level_settings (
            guild_id, leveling_enabled, xp_min, xp_max, cooldown_seconds,
            min_message_length, level_model, curve_quadratic, curve_linear,
            curve_base, announcement_mode, announcement_channel_id, reward_mode,
            excluded_channel_ids, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guild_key,
            settings["leveling_enabled"],
            settings["xp_min"],
            settings["xp_max"],
            settings["cooldown_seconds"],
            settings["min_message_length"],
            settings["level_model"],
            settings["curve_quadratic"],
            settings["curve_linear"],
            settings["curve_base"],
            settings["announcement_mode"],
            settings["announcement_channel_id"],
            settings["reward_mode"],
            settings["excluded_channel_ids"],
            now,
            now,
        ),
    )
    return settings


def recalculate_guild_levels(conn, guild_id, settings):
    rows = conn.execute(
        "SELECT user_id, xp FROM user_levels WHERE guild_id = ?",
        (str(guild_id),),
    ).fetchall()
    now = utc_now()
    for row in rows:
        conn.execute(
            """
            UPDATE user_levels
            SET level = ?, updated_at = ?
            WHERE guild_id = ? AND user_id = ?
            """,
            (
                level_from_xp(row["xp"], settings),
                now,
                str(guild_id),
                row["user_id"],
            ),
        )


def update_guild_level_settings(guild_id, **updates):
    allowed = set(DEFAULT_LEVEL_SETTINGS)
    unknown = set(updates) - allowed
    if unknown:
        raise ValueError(f"Unknown setting: {', '.join(sorted(unknown))}")

    with connect() as conn:
        current = ensure_guild_level_settings(conn, guild_id)
        current.update(updates)
        current = normalize_settings(current)
        validate_level_settings(current)
        should_recalculate = bool(
            {"curve_quadratic", "curve_linear", "curve_base"} & set(updates)
        )

        assignments = ", ".join(f"{key} = ?" for key in updates)
        values = [current[key] for key in updates]
        values.extend([utc_now(), str(guild_id)])
        conn.execute(
            f"""
            UPDATE guild_level_settings
            SET {assignments}, updated_at = ?
            WHERE guild_id = ?
            """,
            values,
        )
        if should_recalculate:
            recalculate_guild_levels(conn, guild_id, current)
    return get_guild_level_settings(guild_id)


def upsert_guild(guild_id, guild_name):
    clean_name = str(guild_name).strip()
    if not clean_name:
        return

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO guilds (guild_id, guild_name, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET
                guild_name = excluded.guild_name,
                updated_at = excluded.updated_at
            """,
            (str(guild_id), clean_name, utc_now()),
        )


def get_guild_name(guild_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT guild_name FROM guilds WHERE guild_id = ?",
            (str(guild_id),),
        ).fetchone()
    return row["guild_name"] if row else None


def replace_guild_channels(guild_id, channels):
    now = utc_now()
    rows = [
        (
            str(guild_id),
            str(channel["channel_id"]),
            str(channel["channel_name"]).strip() or str(channel["channel_id"]),
            str(channel.get("channel_type") or "unknown"),
            int(channel.get("position") or 0),
            now,
        )
        for channel in channels
    ]
    with connect() as conn:
        conn.execute("DELETE FROM guild_channels WHERE guild_id = ?", (str(guild_id),))
        conn.executemany(
            """
            INSERT INTO guild_channels (
                guild_id, channel_id, channel_name, channel_type, position, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def get_guild_channels(guild_id):
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT guild_id, channel_id, channel_name, channel_type, position, updated_at
            FROM guild_channels
            WHERE guild_id = ?
            ORDER BY position ASC, channel_name COLLATE NOCASE ASC
            """,
            (str(guild_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def replace_guild_roles(guild_id, roles):
    now = utc_now()
    rows = [
        (
            str(guild_id),
            str(role["role_id"]),
            str(role["role_name"]).strip() or str(role["role_id"]),
            int(role.get("position") or 0),
            now,
        )
        for role in roles
    ]
    with connect() as conn:
        conn.execute("DELETE FROM guild_roles WHERE guild_id = ?", (str(guild_id),))
        conn.executemany(
            """
            INSERT INTO guild_roles (guild_id, role_id, role_name, position, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )


def get_guild_roles(guild_id):
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT guild_id, role_id, role_name, position, updated_at
            FROM guild_roles
            WHERE guild_id = ?
            ORDER BY position DESC, role_name COLLATE NOCASE ASC
            """,
            (str(guild_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_guild_member(guild_id, user_id, username, display_name=None):
    clean_username = str(username).strip() or f"User {user_id}"
    clean_display = str(display_name or clean_username).strip() or clean_username
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO guild_members (
                guild_id, user_id, username, display_name, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                username = excluded.username,
                display_name = excluded.display_name,
                updated_at = excluded.updated_at
            """,
            (str(guild_id), str(user_id), clean_username, clean_display, utc_now()),
        )


def replace_guild_members(guild_id, members):
    now = utc_now()
    rows = [
        (
            str(guild_id),
            str(member["user_id"]),
            str(member["username"]).strip() or f"User {member['user_id']}",
            str(member.get("display_name") or member.get("username") or member["user_id"]).strip(),
            now,
        )
        for member in members
    ]
    with connect() as conn:
        conn.execute("DELETE FROM guild_members WHERE guild_id = ?", (str(guild_id),))
        conn.executemany(
            """
            INSERT INTO guild_members (
                guild_id, user_id, username, display_name, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )


def get_guild_members(guild_id, limit=1000):
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT guild_id, user_id, username, display_name, updated_at
            FROM guild_members
            WHERE guild_id = ?
            ORDER BY display_name COLLATE NOCASE ASC
            LIMIT ?
            """,
            (str(guild_id), int(limit)),
        ).fetchall()
    return [dict(row) for row in rows]


def get_known_guilds():
    guild_ids = get_known_guild_ids()
    if not guild_ids:
        return []

    with connect() as conn:
        name_rows = conn.execute(
            f"""
            SELECT guild_id, guild_name
            FROM guilds
            WHERE guild_id IN ({",".join("?" for _ in guild_ids)})
            """,
            guild_ids,
        ).fetchall()
    names = {row["guild_id"]: row["guild_name"] for row in name_rows}
    return [
        {"guild_id": guild_id, "guild_name": names.get(guild_id)}
        for guild_id in guild_ids
    ]


def get_known_guild_ids():
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT guild_id FROM guilds
            UNION
            SELECT guild_id FROM guild_level_settings
            UNION
            SELECT guild_id FROM user_levels
            UNION
            SELECT guild_id FROM voice_activity
            UNION
            SELECT guild_id FROM level_role_rewards
            UNION
            SELECT guild_id FROM guild_admins
            UNION
            SELECT guild_id FROM guild_channels
            UNION
            SELECT guild_id FROM guild_roles
            UNION
            SELECT guild_id FROM guild_members
            ORDER BY guild_id
            """
        ).fetchall()
    return [row["guild_id"] for row in rows]


def get_guild_admins(guild_id):
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT guild_id, user_id, username, created_at, updated_at
            FROM guild_admins
            WHERE guild_id = ?
            ORDER BY username COLLATE NOCASE, user_id
            """,
            (str(guild_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_guild_admin(guild_id, user_id, username):
    user_key = str(user_id).strip()
    if not user_key.isdigit():
        raise ValueError("Admin user ID must be a numeric Discord user ID.")

    clean_name = str(username).strip() or f"User {user_key}"
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO guild_admins (guild_id, user_id, username, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                username = excluded.username,
                updated_at = excluded.updated_at
            """,
            (str(guild_id), user_key, clean_name, now, now),
        )


def delete_guild_admin(guild_id, user_id):
    with connect() as conn:
        conn.execute(
            "DELETE FROM guild_admins WHERE guild_id = ? AND user_id = ?",
            (str(guild_id), str(user_id)),
        )


def is_guild_admin(guild_id, user_id):
    with connect() as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM guild_admins
            WHERE guild_id = ? AND user_id = ?
            """,
            (str(guild_id), str(user_id)),
        ).fetchone()
    return row is not None


def xp_needed_for_next_level(level, settings=None):
    settings = normalize_settings(settings)
    level = max(0, int(level))
    return (
        settings["curve_quadratic"] * (level**2)
        + settings["curve_linear"] * level
        + settings["curve_base"]
    )


def level_from_xp(total_xp, settings=None):
    settings = normalize_settings(settings)
    level = 0
    remaining = max(0, int(total_xp))
    while remaining >= xp_needed_for_next_level(level, settings):
        remaining -= xp_needed_for_next_level(level, settings)
        level += 1
    return level


def level_progress(total_xp, settings=None):
    settings = normalize_settings(settings)
    level = 0
    remaining = max(0, int(total_xp))
    while remaining >= xp_needed_for_next_level(level, settings):
        remaining -= xp_needed_for_next_level(level, settings)
        level += 1
    return {
        "level": level,
        "xp_into_level": remaining,
        "xp_needed": xp_needed_for_next_level(level, settings),
    }


def get_user_level(guild_id, user_id):
    with connect() as conn:
        row = conn.execute(
            """
            SELECT guild_id, user_id, username, xp, level, messages,
                   voice_seconds, voice_xp_remainder_seconds, last_xp_at,
                   last_voice_xp_at, created_at, updated_at
            FROM user_levels
            WHERE guild_id = ? AND user_id = ?
            """,
            (str(guild_id), str(user_id)),
        ).fetchone()
    return dict(row) if row else None


def get_user_rank(guild_id, user_id):
    row = get_user_level(guild_id, user_id)
    if not row:
        return None

    with connect() as conn:
        rank_row = conn.execute(
            """
            SELECT COUNT(*) + 1 AS rank
            FROM user_levels
            WHERE guild_id = ?
              AND (
                level > ?
                OR (level = ? AND xp > ?)
                OR (level = ? AND xp = ? AND messages > ?)
              )
            """,
            (
                str(guild_id),
                int(row["level"]),
                int(row["level"]),
                int(row["xp"]),
                int(row["level"]),
                int(row["xp"]),
                int(row["messages"]),
            ),
        ).fetchone()
    return int(rank_row["rank"]) if rank_row else None


def get_user_message_rank(guild_id, user_id):
    row = get_user_level(guild_id, user_id)
    if not row:
        return None

    with connect() as conn:
        rank_row = conn.execute(
            """
            SELECT COUNT(*) + 1 AS rank
            FROM user_levels
            WHERE guild_id = ?
              AND (
                messages > ?
                OR (messages = ? AND level > ?)
                OR (messages = ? AND level = ? AND xp > ?)
              )
            """,
            (
                str(guild_id),
                int(row["messages"]),
                int(row["messages"]),
                int(row["level"]),
                int(row["messages"]),
                int(row["level"]),
                int(row["xp"]),
            ),
        ).fetchone()
    return int(rank_row["rank"]) if rank_row else None


def get_user_voice_rank(guild_id, user_id):
    row = get_user_level(guild_id, user_id)
    if not row or int(row.get("voice_seconds") or 0) <= 0:
        return None

    with connect() as conn:
        rank_row = conn.execute(
            """
            SELECT COUNT(*) + 1 AS rank
            FROM user_levels
            WHERE guild_id = ?
              AND (
                voice_seconds > ?
                OR (voice_seconds = ? AND level > ?)
                OR (voice_seconds = ? AND level = ? AND xp > ?)
              )
            """,
            (
                str(guild_id),
                int(row["voice_seconds"]),
                int(row["voice_seconds"]),
                int(row["level"]),
                int(row["voice_seconds"]),
                int(row["level"]),
                int(row["xp"]),
            ),
        ).fetchone()
    return int(rank_row["rank"]) if rank_row else None


def record_message_xp(
    guild_id,
    user_id,
    username,
    message_content,
    channel_id=None,
    channel_name=None,
):
    now = utc_now()
    now_dt = parse_timestamp(now)
    guild_key = str(guild_id)
    user_key = str(user_id)
    content = message_content or ""

    with connect() as conn:
        settings = ensure_guild_level_settings(conn, guild_key)
        if not settings["leveling_enabled"]:
            return {
                "tracked": False,
                "reason": "disabled",
                "settings": settings,
                "awarded_xp": 0,
                "level": 0,
                "previous_level": 0,
                "leveled_up": False,
            }

        if channel_is_excluded(settings, channel_id):
            return {
                "tracked": False,
                "reason": "excluded_channel",
                "settings": settings,
                "awarded_xp": 0,
                "level": 0,
                "previous_level": 0,
                "leveled_up": False,
            }

        if len(content.strip()) < settings["min_message_length"]:
            return {
                "tracked": False,
                "reason": "too_short",
                "settings": settings,
                "awarded_xp": 0,
                "level": 0,
                "previous_level": 0,
                "leveled_up": False,
            }

        row = conn.execute(
            """
            SELECT xp, level, messages, last_xp_at, created_at
            FROM user_levels
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_key, user_key),
        ).fetchone()

        if row:
            xp = int(row["xp"])
            messages = int(row["messages"]) + 1
            last_xp_at = row["last_xp_at"]
            created_at = row["created_at"] or now
        else:
            xp = 0
            messages = 1
            last_xp_at = None
            created_at = now

        should_award = True
        last_award = parse_timestamp(last_xp_at)
        if last_award and now_dt:
            elapsed = (now_dt - last_award).total_seconds()
            should_award = elapsed >= settings["cooldown_seconds"]

        previous_level = level_from_xp(xp, settings)
        awarded_xp = random.randint(settings["xp_min"], settings["xp_max"]) if should_award else 0
        if awarded_xp:
            xp += awarded_xp
            last_xp_at = now

        new_level = level_from_xp(xp, settings)
        conn.execute(
            """
            INSERT INTO user_levels (
                guild_id, user_id, username, xp, level, messages, last_xp_at,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                username = excluded.username,
                xp = excluded.xp,
                level = excluded.level,
                messages = excluded.messages,
                last_xp_at = excluded.last_xp_at,
                created_at = COALESCE(user_levels.created_at, excluded.created_at),
                updated_at = excluded.updated_at
            """,
            (
                guild_key,
                user_key,
                username,
                xp,
                new_level,
                messages,
                last_xp_at,
                created_at,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO message_activity (
                guild_id, user_id, username, activity_date, messages,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(guild_id, user_id, activity_date) DO UPDATE SET
                username = excluded.username,
                messages = message_activity.messages + 1,
                updated_at = excluded.updated_at
            """,
            (
                guild_key,
                user_key,
                username,
                utc_today(),
                now,
                now,
            ),
        )
        if channel_id is not None:
            conn.execute(
                """
                INSERT INTO user_channel_activity (
                    guild_id, user_id, channel_id, channel_name, activity_date,
                    messages, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(guild_id, user_id, channel_id, activity_date) DO UPDATE SET
                    channel_name = excluded.channel_name,
                    messages = user_channel_activity.messages + 1,
                    updated_at = excluded.updated_at
                """,
                (
                    guild_key,
                    user_key,
                    str(channel_id),
                    str(channel_name or channel_id),
                    utc_today(),
                    now,
                    now,
                ),
            )

    return {
        "tracked": True,
        "reason": "awarded" if awarded_xp else "cooldown",
        "settings": settings,
        "awarded_xp": awarded_xp,
        "level": new_level,
        "previous_level": previous_level,
        "leveled_up": new_level > previous_level,
    }


def record_voice_xp(
    guild_id,
    user_id,
    username,
    duration_seconds,
    channel_id=None,
    channel_name=None,
):
    now = utc_now()
    guild_key = str(guild_id)
    user_key = str(user_id)
    seconds = max(0, int(duration_seconds))

    with connect() as conn:
        settings = ensure_guild_level_settings(conn, guild_key)
        if not settings["leveling_enabled"]:
            return {
                "tracked": False,
                "reason": "disabled",
                "settings": settings,
                "awarded_xp": 0,
                "level": 0,
                "previous_level": 0,
                "leveled_up": False,
            }

        if channel_is_excluded(settings, channel_id):
            return {
                "tracked": False,
                "reason": "excluded_channel",
                "settings": settings,
                "awarded_xp": 0,
                "level": 0,
                "previous_level": 0,
                "leveled_up": False,
            }

        if seconds <= 0:
            return {
                "tracked": False,
                "reason": "no_duration",
                "settings": settings,
                "awarded_xp": 0,
                "level": 0,
                "previous_level": 0,
                "leveled_up": False,
            }

        row = conn.execute(
            """
            SELECT xp, level, messages, voice_seconds, voice_xp_remainder_seconds,
                   created_at
            FROM user_levels
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_key, user_key),
        ).fetchone()

        if row:
            xp = int(row["xp"])
            level = int(row["level"])
            messages = int(row["messages"])
            voice_seconds = int(row["voice_seconds"]) + seconds
            remainder_seconds = int(row["voice_xp_remainder_seconds"]) + seconds
            created_at = row["created_at"] or now
        else:
            xp = 0
            level = 0
            messages = 0
            voice_seconds = seconds
            remainder_seconds = seconds
            created_at = now

        interval_seconds = max(1, int(settings["cooldown_seconds"]) or 60)
        award_count = remainder_seconds // interval_seconds
        remainder_seconds = remainder_seconds % interval_seconds
        previous_level = level_from_xp(xp, settings)
        awarded_xp = sum(
            random.randint(settings["xp_min"], settings["xp_max"])
            for _ in range(award_count)
        )
        if awarded_xp:
            xp += awarded_xp

        new_level = level_from_xp(xp, settings)
        conn.execute(
            """
            INSERT INTO user_levels (
                guild_id, user_id, username, xp, level, messages,
                voice_seconds, voice_xp_remainder_seconds, last_voice_xp_at,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                username = excluded.username,
                xp = excluded.xp,
                level = excluded.level,
                messages = excluded.messages,
                voice_seconds = excluded.voice_seconds,
                voice_xp_remainder_seconds = excluded.voice_xp_remainder_seconds,
                last_voice_xp_at = COALESCE(excluded.last_voice_xp_at, user_levels.last_voice_xp_at),
                created_at = COALESCE(user_levels.created_at, excluded.created_at),
                updated_at = excluded.updated_at
            """,
            (
                guild_key,
                user_key,
                username,
                xp,
                new_level,
                messages,
                voice_seconds,
                remainder_seconds,
                now if awarded_xp else None,
                created_at,
                now,
            ),
        )
        conn.execute(
            """
            INSERT INTO voice_activity (
                guild_id, user_id, username, activity_date, seconds,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id, activity_date) DO UPDATE SET
                username = excluded.username,
                seconds = voice_activity.seconds + excluded.seconds,
                updated_at = excluded.updated_at
            """,
            (
                guild_key,
                user_key,
                username,
                utc_today(),
                seconds,
                now,
                now,
            ),
        )
        if channel_id is not None:
            conn.execute(
                """
                INSERT INTO user_voice_channel_activity (
                    guild_id, user_id, channel_id, channel_name, activity_date,
                    seconds, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id, channel_id, activity_date) DO UPDATE SET
                    channel_name = excluded.channel_name,
                    seconds = user_voice_channel_activity.seconds + excluded.seconds,
                    updated_at = excluded.updated_at
                """,
                (
                    guild_key,
                    user_key,
                    str(channel_id),
                    str(channel_name or channel_id),
                    utc_today(),
                    seconds,
                    now,
                    now,
                ),
            )

    return {
        "tracked": True,
        "reason": "awarded" if awarded_xp else "cooldown",
        "settings": settings,
        "awarded_xp": awarded_xp,
        "award_count": int(award_count),
        "duration_seconds": seconds,
        "level": new_level,
        "previous_level": previous_level,
        "leveled_up": new_level > previous_level,
    }


def get_leaderboard(guild_id=None, limit=10, offset=0):
    query = """
        SELECT guild_id, user_id, username, xp, level, messages, voice_seconds,
               last_xp_at, last_voice_xp_at, created_at, updated_at
        FROM user_levels
    """
    params = []
    if guild_id is not None:
        query += " WHERE guild_id = ?"
        params.append(str(guild_id))
    query += " ORDER BY level DESC, xp DESC, messages DESC LIMIT ? OFFSET ?"
    params.extend([int(limit), int(offset)])

    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def get_message_leaderboard(guild_id=None, limit=10, offset=0, period_days=None):
    if period_days:
        query = """
            SELECT
                activity.guild_id,
                activity.user_id,
                MAX(activity.username) AS username,
                COALESCE(levels.xp, 0) AS xp,
                COALESCE(levels.level, 0) AS level,
                SUM(activity.messages) AS messages,
                levels.last_xp_at,
                MIN(activity.created_at) AS created_at,
                MAX(activity.updated_at) AS updated_at
            FROM message_activity AS activity
            LEFT JOIN user_levels AS levels
              ON levels.guild_id = activity.guild_id
             AND levels.user_id = activity.user_id
        """
        params = []
        filters = ["activity.activity_date >= ?"]
        params.append(period_start_date(period_days))
        if guild_id is not None:
            filters.append("activity.guild_id = ?")
            params.append(str(guild_id))
        query += " WHERE " + " AND ".join(filters)
        query += """
            GROUP BY activity.guild_id, activity.user_id
            ORDER BY messages DESC, level DESC, xp DESC
            LIMIT ? OFFSET ?
        """
        params.extend([int(limit), int(offset)])

        with connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    query = """
        SELECT guild_id, user_id, username, xp, level, messages, voice_seconds,
               last_xp_at, last_voice_xp_at, created_at, updated_at
        FROM user_levels
    """
    params = []
    if guild_id is not None:
        query += " WHERE guild_id = ?"
        params.append(str(guild_id))
    query += " ORDER BY messages DESC, level DESC, xp DESC LIMIT ? OFFSET ?"
    params.extend([int(limit), int(offset)])

    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def get_message_channel_leaderboard(guild_id=None, limit=3, offset=0, period_days=None):
    filters = []
    params = []
    if period_days:
        filters.append("activity_date >= ?")
        params.append(period_start_date(period_days))
    if guild_id is not None:
        filters.append("guild_id = ?")
        params.append(str(guild_id))

    query = """
        SELECT
            guild_id,
            channel_id,
            MAX(channel_name) AS channel_name,
            SUM(messages) AS messages
        FROM user_channel_activity
    """
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += """
        GROUP BY guild_id, channel_id
        ORDER BY messages DESC, channel_name COLLATE NOCASE ASC
        LIMIT ? OFFSET ?
    """
    params.extend([int(limit), int(offset)])

    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


def get_voice_leaderboard(guild_id=None, limit=3, offset=0, period_days=None):
    if period_days:
        query = """
            SELECT
                activity.guild_id,
                activity.user_id,
                MAX(activity.username) AS username,
                SUM(activity.seconds) AS seconds
            FROM voice_activity AS activity
        """
        params = []
        filters = ["activity.activity_date >= ?"]
        params.append(period_start_date(period_days))
        if guild_id is not None:
            filters.append("activity.guild_id = ?")
            params.append(str(guild_id))
        query += " WHERE " + " AND ".join(filters)
        query += """
            GROUP BY activity.guild_id, activity.user_id
            ORDER BY seconds DESC, username COLLATE NOCASE ASC
            LIMIT ? OFFSET ?
        """
        params.extend([int(limit), int(offset)])

        with connect() as conn:
            rows = conn.execute(query, params).fetchall()
        results = [dict(row) for row in rows]
    else:
        query = """
            SELECT guild_id, user_id, username, voice_seconds AS seconds
            FROM user_levels
        """
        params = []
        if guild_id is not None:
            query += " WHERE guild_id = ?"
            params.append(str(guild_id))
        query += " ORDER BY voice_seconds DESC, username COLLATE NOCASE ASC LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])

        with connect() as conn:
            rows = conn.execute(query, params).fetchall()
        results = [dict(row) for row in rows]

    for row in results:
        row["hours"] = seconds_to_hours(row["seconds"])
    return results


def get_voice_channel_leaderboard(guild_id=None, limit=3, offset=0, period_days=None):
    filters = []
    params = []
    if period_days:
        filters.append("activity_date >= ?")
        params.append(period_start_date(period_days))
    if guild_id is not None:
        filters.append("guild_id = ?")
        params.append(str(guild_id))

    query = """
        SELECT
            guild_id,
            channel_id,
            MAX(channel_name) AS channel_name,
            SUM(seconds) AS seconds
        FROM user_voice_channel_activity
    """
    if filters:
        query += " WHERE " + " AND ".join(filters)
    query += """
        GROUP BY guild_id, channel_id
        ORDER BY seconds DESC, channel_name COLLATE NOCASE ASC
        LIMIT ? OFFSET ?
    """
    params.extend([int(limit), int(offset)])

    with connect() as conn:
        rows = conn.execute(query, params).fetchall()

    results = [dict(row) for row in rows]
    for row in results:
        row["hours"] = seconds_to_hours(row["seconds"])
    return results


def get_guild_level_summary(guild_id, period_days=None):
    if period_days:
        with connect() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(DISTINCT user_id) AS tracked_members,
                    COALESCE(SUM(messages), 0) AS total_messages
                FROM message_activity
                WHERE guild_id = ? AND activity_date >= ?
                """,
                (str(guild_id), period_start_date(period_days)),
            ).fetchone()
            voice_row = conn.execute(
                """
                SELECT COALESCE(SUM(seconds), 0) AS total_voice_seconds
                FROM voice_activity
                WHERE guild_id = ? AND activity_date >= ?
                """,
                (str(guild_id), period_start_date(period_days)),
            ).fetchone()
            all_time = conn.execute(
                """
                SELECT
                    COALESCE(SUM(xp), 0) AS total_xp,
                    COALESCE(MAX(level), 0) AS top_level,
                    COALESCE(AVG(level), 0) AS average_level
                FROM user_levels
                WHERE guild_id = ?
                """,
                (str(guild_id),),
            ).fetchone()
        summary = dict(row)
        summary.update(dict(voice_row))
        summary.update(dict(all_time))
        return summary

    with connect() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*) AS tracked_members,
                COALESCE(SUM(messages), 0) AS total_messages,
                COALESCE(SUM(voice_seconds), 0) AS total_voice_seconds,
                COALESCE(SUM(xp), 0) AS total_xp,
                COALESCE(MAX(level), 0) AS top_level,
                COALESCE(AVG(level), 0) AS average_level
            FROM user_levels
            WHERE guild_id = ?
            """,
            (str(guild_id),),
        ).fetchone()
    return dict(row)


def get_user_message_counts(guild_id, user_id):
    counts = {1: 0, 7: 0, 14: 0}
    with connect() as conn:
        for days in counts:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(messages), 0) AS messages
                FROM message_activity
                WHERE guild_id = ?
                  AND user_id = ?
                  AND activity_date >= ?
                """,
                (str(guild_id), str(user_id), period_start_date(days)),
            ).fetchone()
            counts[days] = int(row["messages"] if row else 0)
    return counts


def seconds_to_hours(seconds):
    return round(int(seconds or 0) / 3600, 2)


def get_user_voice_counts(guild_id, user_id):
    counts = {1: 0, 7: 0, 14: 0}
    with connect() as conn:
        for days in counts:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(seconds), 0) AS seconds
                FROM voice_activity
                WHERE guild_id = ?
                  AND user_id = ?
                  AND activity_date >= ?
                """,
                (str(guild_id), str(user_id), period_start_date(days)),
            ).fetchone()
            counts[days] = seconds_to_hours(row["seconds"] if row else 0)
    return counts


def get_user_daily_messages(guild_id, user_id, days=14):
    start_date = datetime.now(timezone.utc).date() - timedelta(days=int(days) - 1)
    labels = [
        (start_date + timedelta(days=offset)).isoformat()
        for offset in range(int(days))
    ]

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT activity_date, messages
            FROM message_activity
            WHERE guild_id = ?
              AND user_id = ?
              AND activity_date >= ?
            ORDER BY activity_date ASC
            """,
            (str(guild_id), str(user_id), labels[0]),
        ).fetchall()

    by_date = {row["activity_date"]: int(row["messages"]) for row in rows}
    return [by_date.get(label, 0) for label in labels]


def get_user_daily_voice_hours(guild_id, user_id, days=14):
    start_date = datetime.now(timezone.utc).date() - timedelta(days=int(days) - 1)
    labels = [
        (start_date + timedelta(days=offset)).isoformat()
        for offset in range(int(days))
    ]

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT activity_date, seconds
            FROM voice_activity
            WHERE guild_id = ?
              AND user_id = ?
              AND activity_date >= ?
            ORDER BY activity_date ASC
            """,
            (str(guild_id), str(user_id), labels[0]),
        ).fetchall()

    by_date = {row["activity_date"]: seconds_to_hours(row["seconds"]) for row in rows}
    return [by_date.get(label, 0) for label in labels]


def get_user_top_channel(guild_id, user_id, period_days=14):
    date_filter = ""
    params = [str(guild_id), str(user_id)]
    if period_days:
        date_filter = "AND activity_date >= ?"
        params.append(period_start_date(period_days))

    with connect() as conn:
        row = conn.execute(
            f"""
            SELECT channel_id, MAX(channel_name) AS channel_name, SUM(messages) AS messages
            FROM user_channel_activity
            WHERE guild_id = ?
              AND user_id = ?
              {date_filter}
            GROUP BY channel_id
            ORDER BY messages DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
    return dict(row) if row else None


def get_user_top_voice_channel(guild_id, user_id, period_days=14):
    date_filter = ""
    params = [str(guild_id), str(user_id)]
    if period_days:
        date_filter = "AND activity_date >= ?"
        params.append(period_start_date(period_days))

    with connect() as conn:
        row = conn.execute(
            f"""
            SELECT channel_id, MAX(channel_name) AS channel_name, SUM(seconds) AS seconds
            FROM user_voice_channel_activity
            WHERE guild_id = ?
              AND user_id = ?
              {date_filter}
            GROUP BY channel_id
            ORDER BY seconds DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["hours"] = seconds_to_hours(result["seconds"])
    return result


def get_level_rewards(guild_id):
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT guild_id, level, role_id, created_at, updated_at
            FROM level_role_rewards
            WHERE guild_id = ?
            ORDER BY level ASC
            """,
            (str(guild_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def get_earned_level_rewards(guild_id, level):
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT guild_id, level, role_id, created_at, updated_at
            FROM level_role_rewards
            WHERE guild_id = ? AND level <= ?
            ORDER BY level ASC
            """,
            (str(guild_id), int(level)),
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_level_reward(guild_id, level, role_id):
    level = int(level)
    if level <= 0:
        raise ValueError("Reward level must be greater than 0.")

    now = utc_now()
    with connect() as conn:
        ensure_guild_level_settings(conn, guild_id)
        conn.execute(
            """
            INSERT INTO level_role_rewards (guild_id, level, role_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, level) DO UPDATE SET
                role_id = excluded.role_id,
                updated_at = excluded.updated_at
            """,
            (str(guild_id), level, str(role_id), now, now),
        )


def delete_level_reward(guild_id, level):
    with connect() as conn:
        conn.execute(
            "DELETE FROM level_role_rewards WHERE guild_id = ? AND level = ?",
            (str(guild_id), int(level)),
        )


def migrate_json_files_if_needed():
    init_db()
    if get_state("json_migrated", "0") == "1":
        return

    now = utc_now()
    with connect() as conn:
        imported_faqs = 0
        existing_faq_count = conn.execute("SELECT COUNT(*) AS count FROM faqs").fetchone()[
            "count"
        ]
        if existing_faq_count == 0 and FAQ_JSON_FILE.exists():
            try:
                faqs = json.loads(FAQ_JSON_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                faqs = {}

            if isinstance(faqs, dict):
                for name, answer in faqs.items():
                    try:
                        clean_name = clean_command_name(str(name))
                    except ValueError:
                        continue
                    if not str(answer).strip():
                        continue
                    conn.execute(
                        """
                        INSERT INTO faqs (command_name, answer, created_at, updated_at)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(command_name) DO UPDATE SET
                            answer = excluded.answer,
                            updated_at = excluded.updated_at
                        """,
                        (clean_name, str(answer).strip(), now, now),
                    )
                    imported_faqs += 1

        existing_usage_count = conn.execute(
            "SELECT COUNT(*) AS count FROM command_usage"
        ).fetchone()["count"]
        if existing_usage_count == 0 and USAGE_JSON_FILE.exists():
            try:
                usage = json.loads(USAGE_JSON_FILE.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                usage = {}

            commands = usage.get("commands", {}) if isinstance(usage, dict) else {}
            if isinstance(commands, dict):
                for name, data in commands.items():
                    if not isinstance(data, dict):
                        continue
                    try:
                        count = int(data.get("count", 0))
                    except (TypeError, ValueError):
                        count = 0
                    conn.execute(
                        """
                        INSERT INTO command_usage (command_name, count, last_used_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(command_name) DO UPDATE SET
                            count = excluded.count,
                            last_used_at = excluded.last_used_at
                        """,
                        (str(name), max(0, count), data.get("last_used_at")),
                    )

        if imported_faqs:
            touch_faq_revision(conn)
        conn.execute(
            """
            INSERT INTO app_state (key, value)
            VALUES ('json_migrated', '1')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """
        )
