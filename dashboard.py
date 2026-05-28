import html
from datetime import datetime

import streamlit as st

from storage import (
    ANNOUNCEMENT_MODES,
    REWARD_MODES,
    clean_command_name,
    delete_faq,
    delete_level_reward,
    get_command_usage,
    get_faq_revision,
    get_faqs,
    get_guild_level_settings,
    get_known_guild_ids,
    get_leaderboard,
    get_level_rewards,
    init_db,
    level_progress,
    migrate_json_files_if_needed,
    update_guild_level_settings,
    upsert_faq,
    upsert_level_reward,
)
from version import APP_RELEASE_NOTES, APP_RELEASE_TITLE, APP_VERSION


GENERAL_COMMANDS = ["help", "insights", "version"]
LEVEL_COMMANDS = ["rank", "level", "leaderboard"]
LEVEL_CONFIG_COMMANDS = [
    "levelconfig_status",
    "levelconfig_toggle",
    "levelconfig_xp",
    "levelconfig_cooldown",
    "levelconfig_min_message_length",
    "levelconfig_model",
    "levelconfig_announcements",
    "levelconfig_reward_mode",
    "levelconfig_reward_add",
    "levelconfig_reward_remove",
    "levelconfig_reward_list",
]
BUILT_IN_COMMANDS = [*GENERAL_COMMANDS, *LEVEL_COMMANDS, *LEVEL_CONFIG_COMMANDS]


def format_last_used(value):
    if not value:
        return "Never"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return value
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def categorize_command(command_name, faqs):
    if command_name in faqs:
        return "FAQ"
    if command_name in LEVEL_COMMANDS:
        return "Leveling"
    if command_name in LEVEL_CONFIG_COMMANDS:
        return "Level Admin"
    if command_name in GENERAL_COMMANDS:
        return "General"
    return "Deleted or Unknown"


def build_usage_rows(faqs, command_usage):
    usage_by_command = {row["command_name"]: row for row in command_usage}
    active_commands = [*BUILT_IN_COMMANDS, *faqs.keys()]
    all_commands = list(dict.fromkeys([*active_commands, *usage_by_command.keys()]))

    rows = []
    for command_name in all_commands:
        command_stats = usage_by_command.get(command_name, {})
        status = "Active" if command_name in active_commands else "Deleted FAQ"
        rows.append(
            {
                "Command": f"/{command_name}",
                "Category": categorize_command(command_name, faqs),
                "Uses": int(command_stats.get("count", 0) or 0),
                "Last Used": format_last_used(command_stats.get("last_used_at")),
                "Status": status,
                "_last_used_at": command_stats.get("last_used_at") or "",
            }
        )

    return sorted(rows, key=lambda row: (row["Uses"], row["_last_used_at"]), reverse=True)


def render_usage_table(rows, empty_message):
    if not rows:
        st.info(empty_message)
        return

    display_rows = [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in rows
    ]
    st.dataframe(display_rows, width="stretch", hide_index=True)


def render_insights(faq_snapshot):
    st.subheader("Command Insights")
    usage_rows = build_usage_rows(faq_snapshot, get_command_usage())
    total_uses = sum(row["Uses"] for row in usage_rows)
    used_rows = [row for row in usage_rows if row["Uses"] > 0]
    active_rows = [row for row in usage_rows if row["Status"] == "Active"]
    top_row = used_rows[0] if used_rows else None
    recent_row = max(used_rows, key=lambda row: row["_last_used_at"]) if used_rows else None

    metric_cols = st.columns(4)
    metric_cols[0].metric("Total Uses", total_uses)
    metric_cols[1].metric("Tracked Commands", len(active_rows))
    metric_cols[2].metric("Most Used", top_row["Command"] if top_row else "None")
    metric_cols[3].metric("Last Used", recent_row["Command"] if recent_row else "None")

    category_rows = []
    for category in ["FAQ", "Leveling", "Level Admin", "General", "Deleted or Unknown"]:
        category_commands = [row for row in usage_rows if row["Category"] == category]
        if category_commands:
            category_rows.append(
                {
                    "Category": category,
                    "Commands": len(category_commands),
                    "Uses": sum(row["Uses"] for row in category_commands),
                }
            )

    if category_rows:
        st.subheader("Usage by Category")
        st.dataframe(category_rows, width="stretch", hide_index=True)

    all_tab, faq_tab, leveling_tab, admin_tab, general_tab = st.tabs(
        ["All", "FAQs", "Leveling", "Level Admin", "General"]
    )
    with all_tab:
        render_usage_table(usage_rows, "No command usage recorded yet.")
    with faq_tab:
        render_usage_table(
            [row for row in usage_rows if row["Category"] == "FAQ"],
            "No FAQ command usage recorded yet.",
        )
    with leveling_tab:
        render_usage_table(
            [row for row in usage_rows if row["Category"] == "Leveling"],
            "No leveling command usage recorded yet.",
        )
    with admin_tab:
        render_usage_table(
            [row for row in usage_rows if row["Category"] == "Level Admin"],
            "No level admin command usage recorded yet.",
        )
    with general_tab:
        render_usage_table(
            [row for row in usage_rows if row["Category"] == "General"],
            "No general command usage recorded yet.",
        )


def render_leaderboard(guild_id):
    rows = get_leaderboard(guild_id or None, limit=100)
    total_users = len(rows)
    total_messages = sum(int(row["messages"]) for row in rows)
    top_row = rows[0] if rows else None

    metric_cols = st.columns(4)
    metric_cols[0].metric("Tracked Members", total_users)
    metric_cols[1].metric("Total Messages", total_messages)
    metric_cols[2].metric("Top Member", top_row["username"] if top_row else "None")
    metric_cols[3].metric("Top Level", top_row["level"] if top_row else 0)

    if not rows:
        st.info("No member XP has been recorded yet.")
        return

    display_rows = []
    settings_cache = {}
    for index, row in enumerate(rows, start=1):
        row_guild_id = row["guild_id"]
        if row_guild_id not in settings_cache:
            settings_cache[row_guild_id] = get_guild_level_settings(row_guild_id)
        progress = level_progress(row["xp"], settings_cache[row_guild_id])
        display_rows.append(
            {
                "Rank": index,
                "Member": row["username"],
                "Guild ID": row_guild_id,
                "Level": progress["level"],
                "XP": row["xp"],
                "Progress": f"{progress['xp_into_level']} / {progress['xp_needed']}",
                "Messages": row["messages"],
                "Last XP": format_last_used(row["last_xp_at"]),
            }
        )

    st.dataframe(display_rows, width="stretch", hide_index=True)


def render_level_settings(guild_id):
    settings = get_guild_level_settings(guild_id)

    st.subheader("Level Settings")
    with st.form("level_settings_form"):
        col1, col2, col3 = st.columns(3)
        enabled = col1.checkbox(
            "Leveling Enabled",
            value=bool(settings["leveling_enabled"]),
        )
        xp_min = col2.number_input(
            "Minimum XP",
            min_value=1,
            value=int(settings["xp_min"]),
            step=1,
        )
        xp_max = col3.number_input(
            "Maximum XP",
            min_value=1,
            value=int(settings["xp_max"]),
            step=1,
        )

        col4, col5, col6 = st.columns(3)
        cooldown = col4.number_input(
            "Cooldown Seconds",
            min_value=0,
            value=int(settings["cooldown_seconds"]),
            step=1,
        )
        min_length = col5.number_input(
            "Minimum Message Length",
            min_value=0,
            value=int(settings["min_message_length"]),
            step=1,
        )
        reward_mode = col6.selectbox(
            "Reward Mode",
            options=sorted(REWARD_MODES),
            index=sorted(REWARD_MODES).index(settings["reward_mode"]),
        )

        st.subheader("Level Model")
        model_cols = st.columns(3)
        curve_quadratic = model_cols[0].number_input(
            "Quadratic",
            min_value=0,
            value=int(settings["curve_quadratic"]),
            step=1,
        )
        curve_linear = model_cols[1].number_input(
            "Linear",
            min_value=0,
            value=int(settings["curve_linear"]),
            step=1,
        )
        curve_base = model_cols[2].number_input(
            "Base",
            min_value=1,
            value=int(settings["curve_base"]),
            step=1,
        )

        st.subheader("Announcements")
        announcement_options = ["current_channel", "configured_channel", "silent"]
        announcement_mode = st.selectbox(
            "Announcement Mode",
            options=announcement_options,
            index=announcement_options.index(settings["announcement_mode"]),
        )
        announcement_channel_id = st.text_input(
            "Announcement Channel ID",
            value=settings["announcement_channel_id"] or "",
            disabled=announcement_mode != "configured_channel",
        )

        submitted = st.form_submit_button("Save Level Settings")
        if submitted:
            try:
                channel_id = (
                    announcement_channel_id.strip()
                    if announcement_mode == "configured_channel"
                    else None
                )
                update_guild_level_settings(
                    guild_id,
                    leveling_enabled=1 if enabled else 0,
                    xp_min=int(xp_min),
                    xp_max=int(xp_max),
                    cooldown_seconds=int(cooldown),
                    min_message_length=int(min_length),
                    reward_mode=reward_mode,
                    curve_quadratic=int(curve_quadratic),
                    curve_linear=int(curve_linear),
                    curve_base=int(curve_base),
                    announcement_mode=announcement_mode,
                    announcement_channel_id=channel_id,
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.success("Level settings saved.")
                st.rerun()


def render_reward_settings(guild_id):
    st.subheader("Role Rewards")
    rewards = get_level_rewards(guild_id)

    if rewards:
        reward_rows = [
            {
                "Level": reward["level"],
                "Role ID": reward["role_id"],
                "Updated": format_last_used(reward["updated_at"]),
            }
            for reward in rewards
        ]
        st.dataframe(reward_rows, width="stretch", hide_index=True)
    else:
        st.info("No role rewards configured for this guild.")

    add_col, remove_col = st.columns(2)
    with add_col:
        with st.form("add_reward_form", clear_on_submit=True):
            reward_level = st.number_input("Reward Level", min_value=1, value=1, step=1)
            role_id = st.text_input("Role ID")
            submitted = st.form_submit_button("Add or Replace Reward")
            if submitted:
                try:
                    if not role_id.strip().isdigit():
                        raise ValueError("Role ID must be a numeric Discord role ID.")
                    upsert_level_reward(guild_id, int(reward_level), role_id.strip())
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.success("Reward saved.")
                    st.rerun()

    with remove_col:
        with st.form("remove_reward_form"):
            existing_levels = [int(reward["level"]) for reward in rewards]
            if existing_levels:
                remove_level = st.selectbox("Reward Level", options=existing_levels)
            else:
                remove_level = st.number_input("Reward Level", min_value=1, value=1, step=1)
            submitted = st.form_submit_button("Remove Reward")
            if submitted:
                delete_level_reward(guild_id, int(remove_level))
                st.success("Reward removed.")
                st.rerun()


def render_levels():
    st.subheader("Member Levels")
    known_guild_ids = get_known_guild_ids()
    current_guild_id = st.session_state.get("level_guild_id", "")

    with st.form("guild_loader_form"):
        selected = ""
        if known_guild_ids:
            options = ["", *known_guild_ids]
            selected_index = options.index(current_guild_id) if current_guild_id in options else 0
            selected = st.selectbox(
                "Known Guild",
                options=options,
                index=selected_index,
                format_func=lambda value: "All guilds" if not value else value,
            )
        manual_default = current_guild_id if current_guild_id and current_guild_id not in known_guild_ids else selected
        manual_guild_id = st.text_input("Guild ID", value=manual_default)
        submitted = st.form_submit_button("Load Guild")
        if submitted:
            st.session_state["level_guild_id"] = manual_guild_id.strip() or selected
            st.rerun()

    guild_id = st.session_state.get("level_guild_id", "").strip()
    if guild_id:
        st.caption(f"Loaded guild: `{guild_id}`")

    render_leaderboard(guild_id)

    if not guild_id:
        st.info("Enter a guild ID to edit leveling settings and role rewards.")
        return
    if not guild_id.isdigit():
        st.error("Guild ID must be numeric.")
        return

    render_level_settings(guild_id)
    render_reward_settings(guild_id)


st.set_page_config(page_title="ConPass Dashboard", page_icon="⚡", layout="wide")

st.markdown(
    """
<style>
    .stApp {
        background-color: #0d1117;
        color: #e6edf3;
        font-family: 'Inter', 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    }

    .faq-card {
        background-color: #161b22;
        padding: 20px;
        border-radius: 8px;
        margin-bottom: 16px;
        border-left: 5px solid #fcba03;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    .faq-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 12px rgba(0, 0, 0, 0.5);
    }

    .faq-title {
        color: #fcba03;
        font-size: 1.25rem;
        font-weight: 700;
        margin-bottom: 10px;
        text-transform: uppercase;
        letter-spacing: 0;
    }
    .faq-content {
        color: #8b949e;
        font-size: 1rem;
        line-height: 1.6;
    }

    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
</style>
""",
    unsafe_allow_html=True,
)

init_db()
migrate_json_files_if_needed()

st.title("⚡ ConPass Dashboard")
st.caption(f"ConPass v{APP_VERSION} - {APP_RELEASE_TITLE}: {APP_RELEASE_NOTES}")
st.markdown("Manage the Discord bot knowledge base, command usage, and member levels.")

faqs = get_faqs()
tab1, tab2, tab3, tab4 = st.tabs(
    ["📋 FAQs", "➕ Add or Update", "📊 Insights", "🏆 Leveling"]
)

with tab1:
    st.subheader("Current Knowledge Base")
    st.caption(f"FAQ revision: {get_faq_revision()}")
    if not faqs:
        st.info("No FAQs found. Go to the next tab to add some.")
    else:
        for key, value in list(faqs.items()):
            col1, col2 = st.columns([5, 1])
            with col1:
                st.markdown(
                    f"""
                    <div class="faq-card">
                        <div class="faq-title">// {html.escape(key)}</div>
                        <div class="faq-content">{html.escape(value)}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
            with col2:
                st.write("")
                st.write("")
                if st.button("Delete 🗑️", key=f"del_{key}", help="Remove this FAQ"):
                    delete_faq(key)
                    st.rerun()

with tab2:
    st.subheader("Add or Update an FAQ")

    with st.form("faq_form", clear_on_submit=True):
        new_key = st.text_input("Command Keyword", placeholder="angle snapping")
        new_value = st.text_area("Response Content", height=150)
        submitted = st.form_submit_button("Save")

        if submitted:
            try:
                clean_name = clean_command_name(new_key)
                saved_name = upsert_faq(clean_name, new_value)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.success(f"Saved `/{saved_name}`. The bot will sync it shortly.")
                st.rerun()

with tab3:
    render_insights(faqs)

with tab4:
    render_levels()
