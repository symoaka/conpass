# Changelog

## v0.1.6 - Guild Names in Leveling
- Stored connected Discord guild names in SQLite when the bot starts.
- Added dashboard guild-name lookup beside the Guild ID field.
- Added Discord API fallback lookup for manually entered guild IDs.
- Updated leaderboard guild display to show server names when known.

## v0.1.5 - Insights and Live Command Sync
- Split dashboard Insights into FAQ, Leveling, Level Admin, and General command sections.
- Added a usage-by-category summary table.
- Added fast guild command syncing so dashboard-added FAQ commands appear faster in Discord.
- Added `DISCORD_SYNC_MODE` for controlling command sync behavior.

## v0.1.4 - GitHub README Docs
- Expanded `README.md` into a complete GitHub setup and usage guide.
- Documented how to launch the bot and dashboard.
- Documented commands, dashboard tabs, runtime data, and versioning.
- Fixed the launcher message to point at `.env.example`.

## v0.1.3 - GitHub Repo Setup
- Added `.gitignore` to keep secrets, virtualenvs, logs, and runtime databases out of git.
- Added `.env.example` for safe configuration sharing.
- Added `README.md` for the GitHub project page.
- Prepared the project for its first git commit and GitHub push.

## v0.1.2 - Bot Rich Presence
- Added configurable Discord bot presence.
- Added `.env` options for activity type, activity text, and online status.
- Updated `/version` and dashboard version text for this release.

## v0.1.1 - Version Tracking
- Added visible version text to the dashboard.
- Added a `/version` Discord command.
- Added this changelog so future updates include a version bump and update note.

## v0.1.0 - SQLite Leveling
- Moved FAQs/questions and command insights into SQLite.
- Added configurable per-server leveling, rank, leaderboard, announcements, and role rewards.
- Added dashboard controls for leveling settings, model values, leaderboards, and rewards.
