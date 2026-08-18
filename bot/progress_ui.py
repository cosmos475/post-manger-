"""
Shared progress-card text formatting for the single editable job message.

Pure formatting only -- no Telegram calls, no job/DB logic. Used by
bot/handlers/job_control.py (Caption Manager). Intended to be reused by
bot/handlers/post_manager_control.py in a later phase without duplicating
the bar-drawing/card logic.
"""

from __future__ import annotations


def render_bar(percent: int, width: int = 10) -> str:
    """Renders a simple block progress bar, e.g. '████████░░'."""
    percent = max(0, min(100, percent))
    filled = round(width * percent / 100)
    return "█" * filled + "░" * (width - filled)


def format_processing_card(
    title: str,
    current: int,
    total: int,
    edited_label: str,
    edited: int,
    skipped: int,
    failed: int,
) -> str:
    percent = int((current / total) * 100) if total else 0
    bar = render_bar(percent)
    return (
        f"✏️ {title}\n"
        f"Progress {current} / {total}\n"
        f"{bar} {percent}%\n\n"
        f"✅ {edited_label}    {edited}\n"
        f"⏭ Skipped    {skipped}\n"
        f"❌ Failed    {failed}\n\n"
        f"⚡ Processing..."
    )


def format_complete_card(
    title: str,
    total: int,
    edited_label: str,
    edited: int,
    skipped: int,
    failed: int,
) -> str:
    return (
        f"✅ {title}\n\n"
        f"Total    {total}\n"
        f"{edited_label}    {edited}\n"
        f"Skipped    {skipped}\n"
        f"Failed    {failed}\n\n"
        f"🏁 Completed"
    )


def format_failed_card(
    title: str,
    processed: int,
    total: int,
    edited_label: str,
    edited: int,
    skipped: int,
    failed: int,
) -> str:
    return (
        f"❌ {title}\n\n"
        f"Progress reached    {processed}/{total}\n"
        f"{edited_label}    {edited}\n"
        f"Skipped    {skipped}\n"
        f"Failed    {failed}\n\n"
        f"🏁 Stopped due to an error -- check Job Status for details"
    )


def _format_eta(seconds: int | None) -> str:
    if seconds is None or seconds <= 0:
        return "-"
    minutes, secs = divmod(seconds, 60)
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def format_status_card(
    current: int,
    total: int,
    edited: int,
    skipped: int,
    failed: int,
    status: str,
    percentage: int,
    eta_seconds: int | None,
    footer: str,
) -> str:
    """
    Bracket-style Caption Manager progress/status card:

      📍 Current/Total, ✅ Edited, 👥 Skipped, ❌ Failed,
      📊 Status (Editing / Sleeping Ns / Paused / Stopped / Completed / Failed),
      𖨠 Percentage, ⏱ ETA, footer (ᴘʀᴏɢʀᴇssɪɴɢ / ᴄᴏᴍᴘʟᴇᴛᴇᴅ).

    Pure formatting -- status/percentage/eta/footer are all supplied by the
    caller, this function does no state logic itself.
    """
    eta = _format_eta(eta_seconds)
    return (
        "╔════❰ Caption editing status ❱═❍⊱❁۪۪\n"
        "║╭━━━━━━━━━━━━━━━➣\n"
        f"║┣⪼📍 Current/Total : {current}/{total}\n"
        "║┃\n"
        f"║┣⪼✅ Edited : {edited}\n"
        "║┃\n"
        f"║┣⪼👥 Skipped : {skipped}\n"
        "║┃\n"
        f"║┣⪼❌ Failed : {failed}\n"
        "║┃\n"
        f"║┣⪼📊 Status: {status}\n"
        "║┃\n"
        f"║┣⪼𖨠 Percentage: {percentage} %\n"
        "║┃\n"
        f"║┣⪼⏱ ETA: {eta}\n"
        "║╰━━━━━━━━━━━━━━━➣\n"
        f"╚════❰ {footer} ❱══❍⊱❁۪۪"
    )
