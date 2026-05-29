import os
import re
import socket
import time
from datetime import datetime, timezone
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from typing import Optional

import truststore

# Prefer IPv4 to avoid macOS/VPN IPv6 blackhole timeouts, but keep DNS resilient.
_orig_getaddrinfo = socket.getaddrinfo


def ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    forced_family = socket.AF_INET if family in (0, socket.AF_UNSPEC) else family
    last_error = None

    for attempt in range(3):
        try:
            return _orig_getaddrinfo(host, port, forced_family, type, proto, flags)
        except socket.gaierror as exc:
            last_error = exc
            if attempt == 2:
                break
            time.sleep(0.5 * (attempt + 1))

    if forced_family != family:
        return _orig_getaddrinfo(host, port, family, type, proto, flags)

    raise last_error


socket.getaddrinfo = ipv4_getaddrinfo

# Inject native system certificates into ssl.
truststore.inject_into_ssl()

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

from storage import (
    ANNOUNCEMENT_MODES,
    REWARD_MODES,
    delete_level_reward,
    get_command_usage,
    get_earned_level_rewards,
    get_faq_revision,
    get_faqs,
    get_guild_level_summary,
    get_guild_level_settings,
    get_leaderboard,
    get_level_rewards,
    get_message_channel_leaderboard,
    get_message_leaderboard,
    get_user_daily_messages,
    get_user_daily_voice_hours,
    get_user_level,
    get_user_message_counts,
    get_user_message_rank,
    get_user_rank,
    get_user_top_channel,
    get_user_top_voice_channel,
    get_user_voice_counts,
    get_user_voice_rank,
    get_voice_channel_leaderboard,
    get_voice_leaderboard,
    init_db,
    is_guild_admin,
    level_progress,
    migrate_json_files_if_needed,
    record_command_usage,
    record_message_xp,
    record_voice_xp,
    replace_guild_channels,
    replace_guild_members,
    replace_guild_roles,
    update_guild_level_settings,
    upsert_guild,
    upsert_guild_member,
    upsert_level_reward,
)
from version import APP_RELEASE_NOTES, APP_RELEASE_TITLE, APP_VERSION

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
SYNC_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
SYNC_MODE = os.getenv("DISCORD_SYNC_MODE", "guild").strip().lower()
if SYNC_MODE not in {"guild", "guilds", "global", "all"}:
    SYNC_MODE = "guild"
PRESENCE_TYPE = os.getenv("DISCORD_PRESENCE_TYPE", "watching").strip().lower()
PRESENCE_TEXT = os.getenv(
    "DISCORD_PRESENCE_TEXT",
    f"/help | Conpass v{APP_VERSION}",
).strip()
PRESENCE_STATUS = os.getenv("DISCORD_STATUS", "online").strip().lower()
BASE_DIR = Path(__file__).resolve().parent
PROFILE_LOGO_FILE = BASE_DIR / "assets" / "conpass-logo.svg"
SVG_TOKEN_PATTERN = re.compile(r"[A-Za-z]|-?\d+(?:\.\d+)?")


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


CARD_BG = (31, 34, 37)
PANEL_BG = (47, 51, 57)
ROW_BG = (54, 58, 64)
BADGE_BG = (24, 26, 28)
TEXT = (220, 224, 229)
MUTED = (166, 170, 181)
ACCENT = (252, 186, 3)
GREEN = (49, 196, 82)
CHART_BLUE = (71, 145, 255)
CHART_WHITE = (238, 244, 255)
BASE_DIR = Path(__file__).resolve().parent
FONT_DIR = BASE_DIR / "assets" / "fonts"
INTER_FONT = FONT_DIR / "InterVariable.ttf"
ICON_FONT = FONT_DIR / "FontAwesome6Free-Solid-900.ttf"

PROFILE_ICONS = {
    "trophy": "\uf091",
    "hash": "\uf292",
    "speaker": "\uf028",
    "chart": "\uf201",
    "gamepad": "\uf11b",
}

TOP_PERIODS = {
    "7d": ("Last 7 days", 7),
    "14d": ("Last 14 days", 14),
    "30d": ("Last 30 days", 30),
    "all": ("All time", None),
}


@lru_cache(maxsize=128)
def font(size, bold=False):
    if INTER_FONT.exists():
        try:
            text_font = ImageFont.truetype(str(INTER_FONT), size)
            text_font.set_variation_by_name("Bold" if bold else "Regular")
            return text_font
        except (OSError, ValueError):
            pass

    candidates = [
        (
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
            if bold
            else "/System/Library/Fonts/Supplemental/Arial.ttf"
        ),
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
        (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        ),
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


@lru_cache(maxsize=64)
def icon_font(size):
    try:
        return ImageFont.truetype(str(ICON_FONT), size)
    except OSError:
        return font(size, True)


def text_width(draw, text, text_font):
    left, _, right, _ = draw.textbbox((0, 0), str(text), font=text_font)
    return right - left


def ellipsize(draw, value, text_font, max_width):
    text = str(value)
    if text_width(draw, text, text_font) <= max_width:
        return text
    while text and text_width(draw, f"{text}...", text_font) > max_width:
        text = text[:-1]
    return f"{text}..." if text else "..."


def format_number(value):
    return f"{int(value):,}"


def format_plain_number(value):
    return str(int(value))


def format_compact_number(value):
    number = int(value)
    if abs(number) < 1000:
        return str(number)
    compact = number / 1000
    text = f"{compact:.2f}".rstrip("0").rstrip(".")
    return f"{text}k"


def format_hours(value):
    number = float(value)
    if number == 0:
        return "0"
    return f"{number:.2f}".rstrip("0").rstrip(".")


def format_top_hours(value):
    return f"{float(value):,.2f} h"


def format_date(value):
    if not value:
        return "Unknown"
    return f"{value.strftime('%B')} {value.day}, {value.year}"


def image_file(image, filename):
    buffer = BytesIO()
    image.save(buffer, "PNG")
    buffer.seek(0)
    return discord.File(buffer, filename=filename)


def draw_vertical_gradient(image, top_color, bottom_color):
    draw = ImageDraw.Draw(image)
    width, height = image.size
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(
            int(top_color[index] + (bottom_color[index] - top_color[index]) * ratio)
            for index in range(3)
        )
        draw.line((0, y, width, y), fill=color)


def draw_shadowed_rounded_rectangle(
    draw,
    xy,
    radius,
    fill,
    shadow_offset=(4, 5),
    shadow_fill=(36, 39, 45),
):
    x1, y1, x2, y2 = xy
    dx, dy = shadow_offset
    draw.rounded_rectangle((x1 + dx, y1 + dy, x2 + dx, y2 + dy), radius=radius, fill=shadow_fill)
    draw.rounded_rectangle(xy, radius=radius, fill=fill)


def draw_badge(draw, xy, text, text_font, fill=BADGE_BG, text_fill=TEXT, shadow=True):
    x1, y1, x2, y2 = xy
    if shadow:
        draw_shadowed_rounded_rectangle(draw, xy, radius=8, fill=fill, shadow_offset=(4, 4))
    else:
        draw.rounded_rectangle(xy, radius=8, fill=fill)
    draw.text(
        (x1 + (x2 - x1 - text_width(draw, text, text_font)) / 2, y1 + 8),
        text,
        font=text_font,
        fill=text_fill,
    )


def draw_centered_text(draw, xy, text, text_font, fill=TEXT):
    x1, y1, x2, y2 = xy
    left, top, right, bottom = draw.textbbox((0, 0), str(text), font=text_font)
    draw.text(
        (
            x1 + (x2 - x1 - (right - left)) / 2,
            y1 + (y2 - y1 - (bottom - top)) / 2 - top,
        ),
        str(text),
        font=text_font,
        fill=fill,
    )


def draw_server_icon(image, draw, icon_bytes=None):
    icon_box = (20, 20, 116, 116)
    draw_shadowed_rounded_rectangle(
        draw,
        icon_box,
        radius=22,
        fill=(239, 242, 245),
        shadow_offset=(4, 5),
    )
    if icon_bytes:
        try:
            icon = Image.open(BytesIO(icon_bytes)).convert("RGB").resize((96, 96))
            mask = Image.new("L", (96, 96), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, 96, 96), radius=22, fill=255)
            image.paste(icon, (20, 20), mask)
            return
        except OSError:
            pass
    draw.text((46, 43), "CP", font=font(30, True), fill=CARD_BG)


async def read_guild_icon_bytes(guild):
    icon = getattr(guild, "icon", None)
    if not icon:
        return None
    try:
        return await icon.replace(size=128, static_format="png").read()
    except (discord.HTTPException, OSError):
        return None


async def read_member_avatar_bytes(member):
    avatar = getattr(member, "display_avatar", None)
    if not avatar:
        return None
    try:
        return await avatar.replace(size=128, static_format="png").read()
    except (discord.HTTPException, OSError):
        return None


def draw_header(image, draw, title, subtitle, width=1280, icon_bytes=None):
    draw_server_icon(image, draw, icon_bytes)
    draw.text(
        (132, 25),
        ellipsize(draw, title, font(42, True), width - 170),
        font=font(42, True),
        fill=TEXT,
    )
    draw.text((132, 74), subtitle, font=font(27), fill=MUTED)


def cubic_point(p0, p1, p2, p3, t):
    inverse = 1 - t
    return (
        inverse**3 * p0[0]
        + 3 * inverse**2 * t * p1[0]
        + 3 * inverse * t**2 * p2[0]
        + t**3 * p3[0],
        inverse**3 * p0[1]
        + 3 * inverse**2 * t * p1[1]
        + 3 * inverse * t**2 * p2[1]
        + t**3 * p3[1],
    )


def parse_svg_path(path_data):
    tokens = SVG_TOKEN_PATTERN.findall(path_data)
    paths = []
    path = []
    command = None
    current = (0.0, 0.0)
    start = (0.0, 0.0)
    index = 0

    while index < len(tokens):
        token = tokens[index]
        if token.isalpha():
            command = token.upper()
            index += 1
            if command == "Z":
                if path:
                    path.append(start)
                    paths.append(path)
                    path = []
                command = None
            continue

        if command == "M":
            current = (float(tokens[index]), float(tokens[index + 1]))
            start = current
            path = [current]
            index += 2
            command = "L"
        elif command == "L":
            current = (float(tokens[index]), float(tokens[index + 1]))
            path.append(current)
            index += 2
        elif command == "C":
            p1 = (float(tokens[index]), float(tokens[index + 1]))
            p2 = (float(tokens[index + 2]), float(tokens[index + 3]))
            p3 = (float(tokens[index + 4]), float(tokens[index + 5]))
            for step in range(1, 25):
                path.append(cubic_point(current, p1, p2, p3, step / 24))
            current = p3
            index += 6
        else:
            index += 1

    return paths


def render_profile_logo(size=96, color=(245, 247, 250)):
    try:
        svg = PROFILE_LOGO_FILE.read_text(encoding="utf-8")
    except OSError:
        return None

    match = re.search(r'<path[^>]+d="([^"]+)"', svg)
    if not match:
        return None

    render_size = 2000
    scale = render_size / 500
    mask = Image.new("L", (render_size, render_size), 0)
    mask_draw = ImageDraw.Draw(mask)
    for index, path in enumerate(parse_svg_path(match.group(1))):
        points = [(x * scale, y * scale) for x, y in path]
        if len(points) >= 3:
            mask_draw.polygon(points, fill=255 if index % 2 == 0 else 0)

    bounds = mask.getbbox()
    if not bounds:
        return None

    mask = mask.crop(bounds).resize((size, size), Image.Resampling.LANCZOS)
    logo = Image.new("RGBA", (size, size), (*color, 0))
    colored = Image.new("RGBA", (size, size), (*color, 255))
    logo.putalpha(mask)
    logo = Image.composite(colored, logo, mask)
    return logo


def draw_profile_mark(image, draw, width):
    logo = render_profile_logo(96)
    if logo:
        image.paste(logo, (width - 118, 22), logo)
        return

    center_x, center_y = width - 70, 70
    outer_box = (center_x - 48, center_y - 48, center_x + 48, center_y + 48)
    inner_box = (center_x - 28, center_y - 28, center_x + 28, center_y + 28)
    draw.arc(outer_box, start=110, end=250, fill=(245, 247, 250), width=11)
    draw.arc(outer_box, start=290, end=70, fill=(245, 247, 250), width=11)
    draw.ellipse(inner_box, outline=(245, 247, 250), width=11)
    draw.rectangle((center_x - 8, center_y - 51, center_x + 8, center_y - 31), fill=CARD_BG)


def draw_top_header(image, draw, guild_name, icon_bytes=None):
    draw_server_icon(image, draw, icon_bytes)
    draw.text((130, 13), ellipsize(draw, guild_name, font(49, True), 1080), font=font(49, True), fill=TEXT)
    draw_profile_icon(draw, "trophy", 145, 94, 36, TEXT)
    draw.text((174, 76), "Top Statistics", font=font(33), fill=MUTED)


def draw_top_list_row(draw, x, y, w, rank, label, value):
    row_h = 68
    rank_w = 78
    row_fill = (48, 51, 57)
    rank_fill = (23, 26, 28)
    value_fill = (31, 34, 39)

    draw.rounded_rectangle((x + 2, y + 5, x + w + 2, y + row_h + 5), radius=8, fill=(25, 27, 31))
    draw.rounded_rectangle((x, y, x + w, y + row_h), radius=8, fill=row_fill)
    draw.rounded_rectangle((x, y, x + rank_w, y + row_h), radius=8, fill=rank_fill)
    draw_centered_text(draw, (x, y, x + rank_w, y + row_h), str(rank), font(31, True), TEXT)

    value_text = str(value)
    value_font = font(31, True)
    badge_w = max(122, text_width(draw, value_text, value_font) + 34)
    badge_x2 = x + w - 10
    badge_x1 = badge_x2 - badge_w
    badge_y1 = y + 10
    badge_y2 = y + row_h - 10
    draw.rounded_rectangle((badge_x1, badge_y1, badge_x2, badge_y2), radius=7, fill=value_fill)
    draw_centered_text(draw, (badge_x1, badge_y1, badge_x2, badge_y2), value_text, value_font, TEXT)

    label_font = font(34)
    label_x = x + rank_w + 20
    label_max = badge_x1 - label_x - 22
    draw.text((label_x, y + 15), ellipsize(draw, label, label_font, label_max), font=label_font, fill=TEXT)


def draw_top_section(draw, title, icon, y, left_rows, right_rows):
    if icon == "hash":
        draw_hash_icon(draw, 20, y - 8, TEXT)
        draw.text((64, y - 3), "Messages", font=font(39, True), fill=TEXT)
        empty_value = "0"
    else:
        draw_speaker_icon(draw, 20, y - 8, TEXT)
        draw.text((64, y - 3), "Voice Activity", font=font(37, True), fill=TEXT)
        empty_value = "0.00 h"

    left_x = 20
    right_x = 650
    row_w = 610
    first_y = y + 54
    for index in range(3):
        left = left_rows[index] if index < len(left_rows) else ("-", empty_value)
        right = right_rows[index] if index < len(right_rows) else ("-", empty_value)
        row_y = first_y + index * 80
        draw_top_list_row(draw, left_x, row_y, row_w, index + 1, left[0], left[1])
        draw_top_list_row(draw, right_x, row_y, row_w, index + 1, right[0], right[1])


def draw_top_footer(image, draw, lookback_label):
    footer_x = 25
    footer_y = 774
    footer_gap = 8
    footer_parts = [
        ("Server Lookback:", font(24, True)),
        (f"{lookback_label} -", font(24)),
        ("Timezone:", font(24, True)),
        ("UTC", font(24)),
    ]
    for text, text_font in footer_parts:
        draw.text((footer_x, footer_y), text, font=text_font, fill=TEXT)
        footer_x += text_width(draw, text, text_font) + footer_gap

    logo_size = 30
    logo_gap = 10
    logo = render_profile_logo(logo_size, color=TEXT)
    power_text = "Conpass"
    power_font = font(27)
    power_w = text_width(draw, power_text, power_font)
    group_w = power_w + logo_gap + logo_size
    power_x = 1254 - group_w
    if logo:
        image.paste(logo, (power_x + power_w + logo_gap, 764), logo)
    draw.text((power_x, 769), power_text, font=power_font, fill=TEXT)


def render_top_messages_card(
    guild_name,
    message_users,
    message_channels,
    voice_users,
    voice_channels,
    lookback_label,
    icon_bytes=None,
):
    width, height = 1280, 819
    image = Image.new("RGB", (width, height), CARD_BG)
    draw = ImageDraw.Draw(image)
    draw_top_header(image, draw, guild_name, icon_bytes)

    message_user_rows = [
        (row["username"], format_number(row["messages"]))
        for row in message_users[:3]
    ]
    message_channel_rows = [
        (f"#{row['channel_name']}", format_number(row["messages"]))
        for row in message_channels[:3]
    ]
    voice_user_rows = [
        (row["username"], format_top_hours(row["hours"]))
        for row in voice_users[:3]
    ]
    voice_channel_rows = [
        (row["channel_name"], format_top_hours(row["hours"]))
        for row in voice_channels[:3]
    ]

    draw_top_section(draw, "Messages", "hash", 145, message_user_rows, message_channel_rows)
    draw_top_section(draw, "Voice Activity", "speaker", 470, voice_user_rows, voice_channel_rows)
    draw_top_footer(image, draw, lookback_label)
    return image


def draw_profile_avatar(image, draw, avatar_bytes):
    box = (20, 20, 110, 110)
    mask = Image.new("L", (90, 90), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 90, 90), radius=22, fill=255)
    if avatar_bytes:
        try:
            avatar = Image.open(BytesIO(avatar_bytes)).convert("RGB").resize((90, 90))
            image.paste(avatar, (20, 20), mask)
            return
        except OSError:
            pass
    draw_shadowed_rounded_rectangle(draw, box, radius=22, fill=(239, 242, 245))
    logo = render_profile_logo(62, color=CARD_BG)
    if logo:
        image.paste(logo, (34, 34), logo)
    else:
        draw_centered_text(draw, box, "CP", font(28, True), CARD_BG)


def draw_profile_guild_badge(image, draw, x, y, guild_name, icon_bytes=None):
    if icon_bytes:
        try:
            icon = Image.open(BytesIO(icon_bytes)).convert("RGB").resize((28, 28))
            mask = Image.new("L", (28, 28), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, 28, 28), radius=7, fill=255)
            image.paste(icon, (x + 3, y), mask)
        except OSError:
            icon_bytes = None
    if not icon_bytes:
        draw.rounded_rectangle((x, y, x + 34, y + 28), radius=7, fill=(245, 247, 250))
        logo = render_profile_logo(25, color=(16, 18, 20))
        if logo:
            image.paste(logo, (x + 4, y + 2), logo)
    draw.text((x + 45, y - 3), guild_name.upper(), font=font(31), fill=MUTED)


def draw_date_stat(draw, x, label, value):
    label_font = font(24, True)
    value_font = font(30)
    box_w = 320
    box_h = 90
    box_top = 20
    draw_shadowed_rounded_rectangle(
        draw,
        (x, box_top, x + box_w, box_top + box_h),
        radius=8,
        fill=(61, 66, 74),
        shadow_offset=(4, 5),
        shadow_fill=(29, 32, 37),
    )
    draw_centered_text(draw, (x, box_top + 8, x + box_w, box_top + 42), label, label_font)
    draw_centered_text(draw, (x, box_top + 40, x + box_w, box_top + box_h - 5), value, value_font)


def draw_font_icon(draw, glyph, x, y, size=40, color=TEXT):
    text_font = icon_font(size)
    left, top, right, bottom = draw.textbbox((0, 0), glyph, font=text_font)
    draw.text(
        (x - (right - left) / 2 - left, y - (bottom - top) / 2 - top),
        glyph,
        font=text_font,
        fill=color,
    )


def draw_profile_icon(draw, icon, x, y, size=40, color=TEXT):
    glyph = PROFILE_ICONS.get(icon)
    if glyph:
        draw_font_icon(draw, glyph, x, y, size, color)


def draw_trophy_icon(draw, x, y, color=TEXT):
    draw_profile_icon(draw, "trophy", x + 25, y + 25, 30, color)


def draw_hash_icon(draw, x, y, color=TEXT):
    draw_profile_icon(draw, "hash", x + 25, y + 26, 30, color)


def draw_speaker_icon(draw, x, y, color=TEXT):
    draw_profile_icon(draw, "speaker", x + 25, y + 26, 30, color)


def draw_chart_icon(draw, x, y, color=TEXT):
    draw_profile_icon(draw, "chart", x + 25, y + 26, 29, color)


def draw_gamepad_icon(draw, x, y, color=TEXT):
    draw_profile_icon(draw, "gamepad", x + 25, y + 26, 32, color)


def draw_profile_panel_icon(draw, icon, x, y):
    draw_profile_icon(draw, icon, x + 25, y + 26, 30)


def draw_panel(draw, xy, title, icon=None):
    draw_shadowed_rounded_rectangle(
        draw,
        xy,
        radius=16,
        fill=PANEL_BG,
        shadow_offset=(5, 7),
        shadow_fill=(28, 31, 36),
    )
    x1, y1, x2, _ = xy
    draw.text((x1 + 15, y1 + 10), title, font=font(31, True), fill=TEXT)
    if icon:
        draw_profile_panel_icon(draw, icon, x2 - 58, y1 + 7)


def draw_stat_row(
    draw,
    x,
    y,
    w,
    label,
    value,
    unit="",
    label_w=115,
    row_h=50,
    label_size=32,
    value_size=31,
    unit_size=24,
):
    draw.rounded_rectangle((x, y, x + w, y + row_h), radius=7, fill=(32, 35, 39))
    draw.rounded_rectangle((x, y, x + label_w, y + row_h), radius=7, fill=(24, 27, 29))
    label_font = font(label_size, True)
    value_font = font(value_size)
    unit_font = font(unit_size)
    draw_centered_text(draw, (x, y, x + label_w, y + row_h), label, label_font)
    value_left, value_top, value_right, value_bottom = draw.textbbox((0, 0), value, font=value_font)
    value_x = x + label_w + 15
    value_y = y + (row_h - (value_bottom - value_top)) / 2 - value_top
    draw.text((value_x, value_y), value, font=value_font, fill=TEXT)
    if unit:
        unit_x = value_x + text_width(draw, value, value_font) + 8
        _, unit_top, _, unit_bottom = draw.textbbox((0, 0), unit, font=unit_font)
        unit_y = y + (row_h - (unit_bottom - unit_top)) / 2 - unit_top + 3
        draw.text((unit_x, unit_y), unit, font=unit_font, fill=TEXT)


def draw_rank_panel(draw, xy, message_rank, voice_rank):
    draw_panel(draw, xy, "Server Ranks", "trophy")
    x1, y1, x2, _ = xy
    row_w = x2 - x1 - 30
    draw_stat_row(
        draw,
        x1 + 15,
        y1 + 64,
        row_w,
        "Message",
        f"#{message_rank}" if message_rank else "-",
        "",
        196,
        74,
        36,
        35,
    )
    draw_stat_row(
        draw,
        x1 + 15,
        y1 + 153,
        row_w,
        "Voice",
        f"#{voice_rank}" if voice_rank else "-",
        "",
        196,
        74,
        36,
        35,
    )


def draw_counts_panel(draw, xy, title, icon, counts, unit):
    draw_panel(draw, xy, title, icon)
    x1, y1, x2, _ = xy
    row_w = x2 - x1 - 30
    for index, days in enumerate([1, 7, 14]):
        value = counts.get(days, 0)
        text = format_compact_number(value) if unit == "messages" else format_hours(value)
        draw_stat_row(draw, x1 + 15, y1 + 57 + index * 60, row_w, f"{days}d", text, unit, 96)


def draw_top_channels_panel(draw, xy, top_channel, top_voice_channel):
    draw_panel(draw, xy, "Top Channels & Applications", "chart")
    x1, y1, x2, _ = xy
    channel_name = f"#{top_channel['channel_name']}" if top_channel else "#-"
    channel_messages = int(top_channel["messages"]) if top_channel else 0
    voice_channel_name = f"#{top_voice_channel['channel_name']}" if top_voice_channel else "#-"
    voice_hours = float(top_voice_channel["hours"]) if top_voice_channel else 0
    first_row_y = y1 + 57
    second_row_y = y1 + 150
    row_h = 74
    row_right = x2 - 15
    label_right = x1 + 258

    draw_hash_icon(draw, x1 + 25, first_row_y + 11)
    draw.rounded_rectangle((x1 + 80, first_row_y, row_right, first_row_y + row_h), radius=7, fill=(32, 35, 39))
    draw.rounded_rectangle((x1 + 80, first_row_y, label_right, first_row_y + row_h), radius=7, fill=(24, 27, 29))
    channel_font = font(33, True)
    draw_centered_text(
        draw,
        (x1 + 80, first_row_y, label_right, first_row_y + row_h),
        ellipsize(draw, channel_name, channel_font, 162),
        channel_font,
    )
    value = format_compact_number(channel_messages)
    draw.text((x1 + 276, first_row_y + 20), value, font=font(34), fill=TEXT)
    draw.text((x1 + 276 + text_width(draw, value, font(34)) + 8, first_row_y + 27), "messages", font=font(25), fill=TEXT)

    draw_speaker_icon(draw, x1 + 25, second_row_y + 11)
    draw.rounded_rectangle((x1 + 80, second_row_y, row_right, second_row_y + row_h), radius=7, fill=(32, 35, 39))
    draw.rounded_rectangle((x1 + 80, second_row_y, label_right, second_row_y + row_h), radius=7, fill=(24, 27, 29))
    draw_centered_text(
        draw,
        (x1 + 80, second_row_y, label_right, second_row_y + row_h),
        ellipsize(draw, voice_channel_name, channel_font, 162),
        channel_font,
    )
    voice_text = format_hours(voice_hours)
    draw.text((x1 + 276, second_row_y + 20), voice_text, font=font(34), fill=TEXT)
    draw.text(
        (x1 + 276 + text_width(draw, voice_text, font(34)) + 8, second_row_y + 27),
        "hours",
        font=font(25),
        fill=TEXT,
    )


def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def smooth_chart_points(points, samples=16):
    if len(points) < 3:
        return points

    extended = [points[0], *points, points[-1]]
    smoothed = []
    for index in range(1, len(extended) - 2):
        p0, p1, p2, p3 = extended[index - 1], extended[index], extended[index + 1], extended[index + 2]
        for step in range(samples):
            t = step / samples
            t2 = t * t
            t3 = t2 * t
            x = 0.5 * (
                (2 * p1[0])
                + (-p0[0] + p2[0]) * t
                + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
            )
            y = 0.5 * (
                (2 * p1[1])
                + (-p0[1] + p2[1]) * t
                + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
            )
            smoothed.append((x, y))
    smoothed.append(points[-1])
    return smoothed


def draw_line_chart(draw, xy, message_values, voice_values=None):
    draw_panel(draw, xy, "Charts")
    x1, y1, x2, y2 = xy
    draw.ellipse((x2 - 312, y1 + 15, x2 - 289, y1 + 38), fill=CHART_BLUE)
    draw.text((x2 - 275, y1 + 6), "Message", font=font(31, True), fill=TEXT)
    draw.ellipse((x2 - 126, y1 + 15, x2 - 103, y1 + 38), fill=CHART_WHITE)
    draw.text((x2 - 89, y1 + 6), "Voice", font=font(31, True), fill=TEXT)

    chart_left, chart_top = x1 + 10, y1 + 95
    chart_right, chart_bottom = x2 - 10, y2 - 8
    voice_values = voice_values or []
    max_value = max(1, max(message_values or [0]), max(voice_values or [0]))
    if len(message_values) < 2:
        message_values = [0, *message_values]
    if len(voice_values) < 2:
        voice_values = [0, *voice_values]

    def chart_points(values):
        points = []
        for index, value in enumerate(values):
            x = chart_left + (chart_right - chart_left) * index / max(1, len(values) - 1)
            y = chart_bottom - (chart_bottom - chart_top) * (value / max_value)
            points.append((x, y))
        return [(x, clamp(y, chart_top, chart_bottom)) for x, y in smooth_chart_points(points)]

    message_points = chart_points(message_values)
    voice_points = chart_points(voice_values)
    if len(message_points) >= 2:
        fill_points = [(chart_left, chart_bottom), *message_points, (chart_right, chart_bottom)]
        draw.polygon(fill_points, fill=(40, 55, 78))
        draw.line(message_points, fill=CHART_BLUE, width=5, joint="curve")
    if len(voice_points) >= 2:
        draw.line(voice_points, fill=CHART_WHITE, width=4, joint="curve")
    draw.line((chart_left, chart_bottom, chart_right, chart_bottom), fill=CHART_WHITE, width=3)


def render_profile_card(
    guild_name,
    display_name,
    username,
    message_rank,
    voice_rank,
    message_counts,
    voice_counts,
    daily_messages,
    daily_voice_hours,
    top_channel,
    top_voice_channel,
    created_at,
    joined_at,
    avatar_bytes=None,
    guild_icon_bytes=None,
):
    width, height = 1280, 708
    image = Image.new("RGB", (width, height), CARD_BG)
    draw = ImageDraw.Draw(image)

    draw_profile_avatar(image, draw, avatar_bytes)
    draw.text((124, 18), ellipsize(draw, display_name, font(49, True), 520), font=font(49, True), fill=TEXT)
    username_x = 124 + min(text_width(draw, display_name, font(49, True)), 520) + 14
    username_width = max(0, 602 - username_x - 16)
    if username_width >= text_width(draw, "...", font(31)):
        draw.text((username_x, 35), ellipsize(draw, username, font(31), username_width), font=font(31), fill=MUTED)
    draw_profile_guild_badge(image, draw, 124, 74, guild_name, guild_icon_bytes)

    draw_date_stat(draw, 602, "Created On", format_date(created_at))
    draw_date_stat(draw, 942, "Joined On", format_date(joined_at))

    draw_rank_panel(draw, (20, 130, 420, 372), message_rank, voice_rank)
    draw_counts_panel(draw, (440, 130, 840, 372), "Messages", "hash", message_counts, "messages")
    draw_counts_panel(draw, (860, 130, 1260, 372), "Voice Activity", "speaker", voice_counts, "hours")
    draw_top_channels_panel(draw, (20, 393, 630, 635), top_channel, top_voice_channel)
    draw_line_chart(draw, (650, 393, 1260, 635), daily_messages, daily_voice_hours)

    footer_x = 25
    footer_y = 660
    footer_gap = 10
    footer_parts = [
        ("Server Lookback:", font(24, True)),
        ("Last 14 days -", font(24)),
        ("Timezone:", font(24, True)),
        ("UTC", font(24)),
    ]
    for text, text_font in footer_parts:
        draw.text((footer_x, footer_y), text, font=text_font, fill=TEXT)
        footer_x += text_width(draw, text, text_font) + footer_gap

    logo_size = 28
    logo_gap = 10
    logo = render_profile_logo(logo_size, color=TEXT)
    power_text = "Conpass"
    power_font = font(25)
    power_w = text_width(draw, power_text, power_font)
    group_w = power_w + logo_gap + logo_size
    power_x = width - 20 - group_w
    if logo:
        image.paste(logo, (power_x + power_w + logo_gap, 650), logo)
    draw.text((power_x, 655), power_text, font=power_font, fill=TEXT)
    return image


def render_server_stats_card(guild_name, summary, top_rows, icon_bytes=None):
    width, height = 1100, 620
    image = Image.new("RGB", (width, height), CARD_BG)
    draw = ImageDraw.Draw(image)
    draw_header(image, draw, guild_name, "Server Statistics", width, icon_bytes)

    stat_cards = [
        ("Members", format_number(summary["tracked_members"])),
        ("Messages", format_number(summary["total_messages"])),
        ("Total XP", format_number(summary["total_xp"])),
        ("Top Level", format_number(summary["top_level"])),
    ]
    for idx, (label, value) in enumerate(stat_cards):
        x = 20 + idx * 265
        draw.rounded_rectangle((x, 145, x + 245, 255), radius=12, fill=PANEL_BG)
        draw.text((x + 16, 162), label, font=font(24, True), fill=TEXT)
        draw_badge(draw, (x + 16, 205, x + 229, 242), value, font(24, True))

    draw.text((20, 300), "Top Message Users", font=font(32, True), fill=TEXT)
    if not top_rows:
        draw.rounded_rectangle((20, 350, width - 20, 410), radius=8, fill=ROW_BG)
        draw.text(
            (42, 367),
            "No server activity has been recorded yet.",
            font=font(25),
            fill=MUTED,
        )
        return image

    max_messages = max(1, max(int(row["messages"]) for row in top_rows))
    for index, row in enumerate(top_rows[:5], start=1):
        y = 350 + (index - 1) * 48
        draw.rounded_rectangle((20, y, width - 20, y + 39), radius=8, fill=ROW_BG)
        draw.rounded_rectangle((20, y, 82, y + 39), radius=8, fill=BADGE_BG)
        draw.text(
            (50 - text_width(draw, str(index), font(22, True)) / 2, y + 7),
            str(index),
            font=font(22, True),
            fill=TEXT,
        )
        draw.text(
            (100, y + 6),
            ellipsize(draw, row["username"], font(23), 220),
            font=font(23),
            fill=TEXT,
        )
        bar_left, bar_right = 360, width - 220
        bar_width = int((bar_right - bar_left) * (int(row["messages"]) / max_messages))
        draw.rounded_rectangle((bar_left, y + 11, bar_right, y + 28), radius=7, fill=BADGE_BG)
        draw.rounded_rectangle(
            (bar_left, y + 11, bar_left + max(14, bar_width), y + 28),
            radius=7,
            fill=ACCENT,
        )
        draw_badge(
            draw,
            (width - 172, y + 4, width - 40, y + 34),
            format_number(row["messages"]),
            font(22, True),
        )

    draw.text((25, height - 42), "Powered by ConPass", font=font(23, True), fill=MUTED)
    return image


class ConPassBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        intents.voice_states = True

        super().__init__(command_prefix="!", intents=intents)
        self.faq_command_names = set()
        self.faq_revision = None
        self.sync_guild = (
            discord.Object(id=int(SYNC_GUILD_ID))
            if SYNC_GUILD_ID and SYNC_GUILD_ID.isdigit()
            else None
        )
        self.ready_sync_done = False
        self.global_commands_cleared = False
        self.active_voice_sessions = {}

    async def setup_hook(self):
        init_db()
        migrate_json_files_if_needed()
        await self.refresh_faq_commands(sync=True)
        self.faq_sync_loop.start()
        self.voice_xp_loop.start()
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
        self.remember_guild_names()
        print(f"Logged in as {self.user} (ID: {self.user.id})", flush=True)
        print(
            f"Presence: {PRESENCE_STATUS} / {PRESENCE_TYPE} {PRESENCE_TEXT}",
            flush=True,
        )

    def remember_guild_names(self):
        for guild in self.guilds:
            upsert_guild(guild.id, guild.name)
            self.remember_guild_metadata(guild)
            self.remember_active_voice_members(guild)

    def remember_active_voice_members(self, guild):
        for channel in [*getattr(guild, "voice_channels", []), *getattr(guild, "stage_channels", [])]:
            for member in getattr(channel, "members", []):
                if not member.bot:
                    self.start_voice_session(member, channel)

    def remember_guild_metadata(self, guild):
        text_channel_types = (
            discord.ChannelType.text,
            discord.ChannelType.news,
            discord.ChannelType.forum,
            discord.ChannelType.public_thread,
            discord.ChannelType.private_thread,
            discord.ChannelType.news_thread,
        )
        channels = [
            {
                "channel_id": channel.id,
                "channel_name": getattr(channel, "name", str(channel.id)),
                "channel_type": str(getattr(channel, "type", "unknown")),
                "position": getattr(channel, "position", 0),
            }
            for channel in guild.channels
            if getattr(channel, "type", None) in text_channel_types
        ]
        replace_guild_channels(guild.id, channels)

        roles = [
            {
                "role_id": role.id,
                "role_name": role.name,
                "position": role.position,
            }
            for role in guild.roles
            if not role.is_default()
        ]
        replace_guild_roles(guild.id, roles)

        members = [
            {
                "user_id": member.id,
                "username": member.name,
                "display_name": member.display_name,
            }
            for member in guild.members
            if not member.bot
        ]
        if members:
            replace_guild_members(guild.id, members)

    async def sync_commands_to_guild(self, guild):
        guild_object = discord.Object(id=guild.id)
        self.tree.clear_commands(guild=guild_object)
        self.tree.copy_global_to(guild=guild_object)
        synced = await self.tree.sync(guild=guild_object)
        guild_label = getattr(guild, "name", None) or guild.id
        print(f"Synced {len(synced)} commands to guild {guild_label}.", flush=True)

    async def clear_global_commands(self):
        if self.global_commands_cleared:
            return

        commands = list(self.tree.get_commands())
        self.tree.clear_commands(guild=None)
        await self.tree.sync()
        for command in commands:
            self.tree.add_command(command, override=True)

        self.global_commands_cleared = True
        print("Cleared global commands to prevent duplicates.", flush=True)

    async def sync_ready_guild_commands(self):
        if self.sync_guild:
            await self.clear_global_commands()
            await self.sync_commands_to_guild(self.sync_guild)
            return

        if SYNC_MODE in {"guild", "guilds", "all"}:
            if SYNC_MODE in {"guild", "guilds"}:
                await self.clear_global_commands()
            for guild in self.guilds:
                await self.sync_commands_to_guild(guild)

    async def sync_command_tree(self):
        if self.sync_guild:
            await self.clear_global_commands()
            await self.sync_commands_to_guild(self.sync_guild)
            return

        if SYNC_MODE in {"global", "all"}:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} global commands.", flush=True)
        else:
            await self.clear_global_commands()

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

    def voice_session_key(self, member):
        return (str(member.guild.id), str(member.id))

    def start_voice_session(self, member, channel):
        upsert_guild_member(
            member.guild.id,
            member.id,
            member.name,
            member.display_name,
        )
        self.active_voice_sessions[self.voice_session_key(member)] = {
            "member": member,
            "started_at": datetime.now(timezone.utc),
            "channel_id": channel.id,
            "channel_name": getattr(channel, "name", str(channel.id)),
        }

    async def flush_voice_session(self, member, *, close=False):
        key = self.voice_session_key(member)
        session = self.active_voice_sessions.get(key)
        if not session:
            return

        now = datetime.now(timezone.utc)
        duration_seconds = int((now - session["started_at"]).total_seconds())
        if duration_seconds <= 0:
            if close:
                self.active_voice_sessions.pop(key, None)
            return

        result = record_voice_xp(
            guild_id=member.guild.id,
            user_id=member.id,
            username=member.display_name,
            duration_seconds=duration_seconds,
            channel_id=session["channel_id"],
            channel_name=session["channel_name"],
        )
        if close:
            self.active_voice_sessions.pop(key, None)
        else:
            session["started_at"] = now

        if result["leveled_up"]:
            assigned_roles, reward_errors = await apply_level_rewards(
                member,
                result["level"],
                result["settings"],
            )
            await announce_member_level_up(member, result, assigned_roles, reward_errors)

    @tasks.loop(seconds=60)
    async def voice_xp_loop(self):
        for session in list(self.active_voice_sessions.values()):
            member = session.get("member")
            if member and member.voice and member.voice.channel:
                await self.flush_voice_session(member)

    @voice_xp_loop.before_loop
    async def before_voice_xp_loop(self):
        await self.wait_until_ready()

    async def on_voice_state_update(self, member, before, after):
        if member.bot or not member.guild:
            return

        before_channel = before.channel
        after_channel = after.channel
        if before_channel == after_channel:
            return

        if before_channel:
            await self.flush_voice_session(member, close=True)
        if after_channel:
            self.start_voice_session(member, after_channel)

    async def on_message(self, message):
        if message.author.bot:
            return

        if message.guild:
            upsert_guild_member(
                message.guild.id,
                message.author.id,
                message.author.name,
                message.author.display_name,
            )
            result = record_message_xp(
                guild_id=message.guild.id,
                user_id=message.author.id,
                username=message.author.display_name,
                message_content=message.content,
                channel_id=message.channel.id,
                channel_name=getattr(message.channel, "name", str(message.channel.id)),
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


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError,
):
    command_name = interaction.command.qualified_name if interaction.command else "unknown"
    print(f"Error in /{command_name}: {error!r}", flush=True)

    message = "ConPass hit an error while handling that command. Check the bot terminal for details."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        pass


async def require_manage_guild(interaction):
    if not interaction.guild:
        await interaction.response.send_message(
            "Level configuration is only available inside servers.",
            ephemeral=True,
        )
        return False

    permissions = getattr(interaction.user, "guild_permissions", None)
    if permissions and permissions.manage_guild:
        return True

    if is_guild_admin(interaction.guild.id, interaction.user.id):
        return True

    if not permissions or not permissions.manage_guild:
        await interaction.response.send_message(
            "You need the Manage Server permission or ConPass admin access to change leveling settings.",
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


def find_level_announcement_channel(guild, settings, fallback_channel=None):
    if settings["announcement_mode"] == "configured_channel":
        channel_id = settings.get("announcement_channel_id")
        if channel_id:
            try:
                configured_channel = guild.get_channel(int(channel_id))
            except (TypeError, ValueError):
                configured_channel = None
            if configured_channel:
                return configured_channel

    if fallback_channel:
        return fallback_channel

    candidates = [
        getattr(guild, "system_channel", None),
        getattr(guild, "rules_channel", None),
        getattr(guild, "public_updates_channel", None),
    ]
    candidates.extend(getattr(guild, "text_channels", []))
    me = getattr(guild, "me", None)
    for channel in candidates:
        if not channel:
            continue
        permissions = channel.permissions_for(me) if me else None
        if permissions is None or permissions.send_messages:
            return channel
    return None


async def announce_member_level_up(member, result, assigned_roles, reward_errors, fallback_channel=None):
    settings = result["settings"]
    if settings["announcement_mode"] == "silent":
        return

    target_channel = find_level_announcement_channel(member.guild, settings, fallback_channel)
    if not target_channel:
        return

    content = (
        f"{member.mention} reached level {result['level']} "
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


async def announce_level_up(message, result, assigned_roles, reward_errors):
    await announce_member_level_up(
        message.author,
        result,
        assigned_roles,
        reward_errors,
        fallback_channel=message.channel,
    )


def voice_hours_from_seconds(seconds):
    return format_hours(int(seconds or 0) / 3600)


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
    voice_seconds = int(row["voice_seconds"]) if row else 0
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
    embed.add_field(name="Voice", value=f"{voice_hours_from_seconds(voice_seconds)} hours", inline=True)

    avatar = getattr(target, "display_avatar", None)
    if avatar:
        embed.set_thumbnail(url=avatar.url)

    await interaction.response.send_message(embed=embed)


async def send_profile_response(interaction, member=None):
    if not interaction.guild:
        await interaction.response.send_message(
            "Profiles are only available inside servers.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)
    target = member or interaction.user
    guild_name = interaction.guild.name if interaction.guild else "Server"
    image = render_profile_card(
        guild_name,
        getattr(target, "display_name", None) or target.name,
        getattr(target, "name", None) or str(target),
        get_user_message_rank(interaction.guild.id, target.id),
        get_user_voice_rank(interaction.guild.id, target.id),
        get_user_message_counts(interaction.guild.id, target.id),
        get_user_voice_counts(interaction.guild.id, target.id),
        get_user_daily_messages(interaction.guild.id, target.id, days=14),
        get_user_daily_voice_hours(interaction.guild.id, target.id, days=14),
        get_user_top_channel(interaction.guild.id, target.id, period_days=14),
        get_user_top_voice_channel(interaction.guild.id, target.id, period_days=14),
        getattr(target, "created_at", None),
        getattr(target, "joined_at", None),
        await read_member_avatar_bytes(target),
        await read_guild_icon_bytes(interaction.guild),
    )
    await interaction.followup.send(file=image_file(image, "conpass-profile.png"))


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
        value=(
            "• `/profile`\n• `/me`\n• `/rank`\n• `/leaderboard`\n"
            "• `/top`\n• `/stats`\n• `/insights`\n• `/version`"
        ),
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


@bot.tree.command(name="profile", description="Show a member's profile stats card")
@app_commands.describe(member="Choose a server member")
async def profile_command(
    interaction: discord.Interaction,
    member: Optional[discord.Member] = None,
):
    record_command_usage("profile")
    await send_profile_response(interaction, member)


@bot.tree.command(name="me", description="Show your profile stats card")
async def me_command(interaction: discord.Interaction):
    record_command_usage("me")
    await send_profile_response(interaction)


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


@bot.tree.command(name="top", description="Show the server's top statistics")
@app_commands.describe(
    period="Statistics lookback window",
)
@app_commands.choices(
    period=[
        app_commands.Choice(name="Last 7 days", value="7d"),
        app_commands.Choice(name="Last 14 days", value="14d"),
        app_commands.Choice(name="Last 30 days", value="30d"),
        app_commands.Choice(name="All time", value="all"),
    ]
)
async def top_command(
    interaction: discord.Interaction,
    period: str = "14d",
):
    if not interaction.guild:
        await interaction.response.send_message(
            "Top message users are only available inside servers.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)
    record_command_usage("top")
    lookback_label, period_days = TOP_PERIODS.get(period, TOP_PERIODS["14d"])
    message_users = get_message_leaderboard(
        interaction.guild.id,
        limit=3,
        offset=0,
        period_days=period_days,
    )
    message_channels = get_message_channel_leaderboard(
        interaction.guild.id,
        limit=3,
        offset=0,
        period_days=period_days,
    )
    voice_users = get_voice_leaderboard(
        interaction.guild.id,
        limit=3,
        offset=0,
        period_days=period_days,
    )
    voice_channels = get_voice_channel_leaderboard(
        interaction.guild.id,
        limit=3,
        offset=0,
        period_days=period_days,
    )
    image = render_top_messages_card(
        interaction.guild.name,
        message_users,
        message_channels,
        voice_users,
        voice_channels,
        lookback_label,
        await read_guild_icon_bytes(interaction.guild),
    )
    await interaction.followup.send(file=image_file(image, "conpass-top.png"))


@bot.tree.command(name="stats", description="Show this server's leveling stats card")
async def stats_command(interaction: discord.Interaction):
    record_command_usage("stats")
    if not interaction.guild:
        await interaction.response.send_message(
            "Server stats are only available inside servers.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)
    summary = get_guild_level_summary(interaction.guild.id)
    top_rows = get_message_leaderboard(interaction.guild.id, limit=5)
    image = render_server_stats_card(
        interaction.guild.name,
        summary,
        top_rows,
        await read_guild_icon_bytes(interaction.guild),
    )
    await interaction.followup.send(file=image_file(image, "conpass-stats.png"))


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
    excluded_channel_lines = [
        f"<#{channel_id}>"
        for channel_id in str(settings.get("excluded_channel_ids", "")).split(",")
        if channel_id
    ]

    embed = discord.Embed(title="// LEVEL CONFIG", color=0xFCBA03)
    embed.add_field(
        name="XP",
        value=(
            f"Enabled: `{bool(settings['leveling_enabled'])}`\n"
            f"Range: `{settings['xp_min']}-{settings['xp_max']}`\n"
            f"Cooldown: `{settings['cooldown_seconds']}s`\n"
            f"Min Length: `{settings['min_message_length']}`\n"
            f"Excluded Channels: {', '.join(excluded_channel_lines) if excluded_channel_lines else '`none`'}"
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
