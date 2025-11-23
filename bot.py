"""
Safe Telegram bot to TRIGGER AUTHORIZED load-tests via k6.
USAGE: Only run this against servers you own or have written permission to test.
"""

import subprocess
import shlex
import logging
from functools import wraps
from telegram.ext import Updater, CommandHandler, CallbackContext
from telegram import Update

# ---- CONFIG ----
TELEGRAM_TOKEN = "7717703289:AAEGnC9CYZosIMkXZQ1yL5RfodmnX19gJZM"
# Telegram user IDs allowed to run tests (admins)
ADMIN_USER_IDS = {7615740556}  # replace with your Telegram numeric user id(s)
# Allowed targets mapping alias -> URL
WHITELIST = {
    "mystaging": "https://staging.example.com",
    "mylocal": "http://127.0.0.1:8000",
}
# Path to k6 script
K6_SCRIPT_PATH = "./simple_test.js"
# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
# ---- END CONFIG ----

def admin_only(func):
    @wraps(func)
    def wrapped(update: Update, context: CallbackContext, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in ADMIN_USER_IDS:
            update.message.reply_text("Unauthorized. You are not allowed to run tests.")
            logger.warning("Unauthorized attempt by user_id=%s", user_id)
            return
        return func(update, context, *args, **kwargs)
    return wrapped

def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "Safe Load-Test Bot.\n"
        "Commands:\n"
        "/list - show whitelisted targets\n"
        "/run <alias> - run a small, authorized k6 test against a whitelisted target\n"
        "/help - this message"
    )

def list_targets(update: Update, context: CallbackContext):
    lines = ["Whitelisted targets (aliases):"]
    for alias, url in WHITELIST.items():
        lines.append(f"{alias} -> {url}")
    update.message.reply_text("\n".join(lines))

@admin_only
def run_test(update: Update, context: CallbackContext):
    """
    Usage: /run <alias> [vus duration_seconds]
    Example: /run mystaging
    Example with params: /run mystaging 10 60
    """
    args = context.args
    if not args:
        update.message.reply_text("Usage: /run <alias> [vus duration_seconds]\nExample: /run mystaging 5 30")
        return

    alias = args[0]
    if alias not in WHITELIST:
        update.message.reply_text("Target not in whitelist.")
        return

    # safe defaults
    vus = 5
    duration = 30  # seconds

    try:
        if len(args) >= 2:
            vus = int(args[1])
        if len(args) >= 3:
            duration = int(args[2])
    except ValueError:
        update.message.reply_text("Invalid numeric parameters. Use integers for vus and duration_seconds.")
        return

    # safety checks
    if vus <= 0 or duration <= 0:
        update.message.reply_text("VUs and duration must be positive integers.")
        return
    if vus > 200:
        update.message.reply_text("Requested VUs too large. Max allowed = 200.")
        return
    if duration > 3600:
        update.message.reply_text("Requested duration too long. Max allowed = 3600 seconds (1 hour).")
        return

    target_url = WHITELIST[alias]

    # Confirmation message
    msg = (
        f"About to run k6 test against *{alias}* ({target_url})\n"
        f"VUs: {vus}\nDuration: {duration}s\n\n"
        "Reply with /confirm to proceed, or /cancel to abort. This request will time out in 60s."
    )
    update.message.reply_text(msg, parse_mode="Markdown")

    # store pending details in context.chat_data keyed by user id
    user_key = str(update.effective_user.id)
    context.chat_data[user_key] = {"alias": alias, "url": target_url, "vus": vus, "duration": duration}

@admin_only
def confirm(update: Update, context: CallbackContext):
    user_key = str(update.effective_user.id)
    pending = context.chat_data.get(user_key)
    if not pending:
        update.message.reply_text("Nothing to confirm.")
        return

    alias = pending["alias"]
    target_url = pending["url"]
    vus = pending["vus"]
    duration = pending["duration"]

    update.message.reply_text(f"Starting test against {alias} — VUs={vus}, duration={duration}s. Logging enabled.")

    # Build safe k6 command. We pass TARGET_URL via env to avoid shell interpolation.
    cmd = f"k6 run --vus {vus} --duration {duration}s {K6_SCRIPT_PATH}"
    logger.info("Running k6: %s against %s (requested by %s)", cmd, target_url, update.effective_user.id)

    try:
        # run as subprocess, pass TARGET_URL in env
        env = dict(**__import__("os").environ)
        env["TARGET_URL"] = target_url
        # We run subprocess synchronously to capture last lines of output (small tests only)
        proc = subprocess.run(shlex.split(cmd), capture_output=True, text=True, env=env, timeout=duration + 60)
        stdout = proc.stdout or "(no stdout)"
        stderr = proc.stderr or "(no stderr)"
        # Reply a short summary — avoid huge outputs in Telegram; attach first/last lines
        summary = stdout.strip().splitlines()
        trimmed = "\n".join(summary[:20])  # first 20 lines
        update.message.reply_text("k6 finished. Output (first 20 lines):\n" + (trimmed or "(no output)"))
        logger.info("k6 finished for %s; returncode=%s", alias, proc.returncode)
    except Exception as e:
        logger.exception("Error running k6: %s", e)
        update.message.reply_text(f"Error running k6: {e}")

    # clear pending
    context.chat_data.pop(user_key, None)

@admin_only
def cancel(update: Update, context: CallbackContext):
    user_key = str(update.effective_user.id)
    if user_key in context.chat_data:
        context.chat_data.pop(user_key, None)
        update.message.reply_text("Pending test canceled.")
    else:
        update.message.reply_text("No pending test to cancel.")

def help_cmd(update: Update, context: CallbackContext):
    start(update, context)

def main():
    updater = Updater(TELEGRAM_TOKEN, use_context=True)
    dp = updater.dispatcher

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("list", list_targets))
    dp.add_handler(CommandHandler("run", run_test))
    dp.add_handler(CommandHandler("confirm", confirm))
    dp.add_handler(CommandHandler("cancel", cancel))
    dp.add_handler(CommandHandler("help", help_cmd))

    updater.start_polling()
    logger.info("Bot started. Listening for commands.")
    updater.idle()

if __name__ == "__main__":
    main()