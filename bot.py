"""
Multi-language Text-to-Speech Telegram Bot
============================================
- Auto-detects the language of incoming text (langdetect)
- Converts text to speech using Microsoft Edge's free TTS engine (edge-tts)
- Lets the user pick a Male / Female voice via inline keyboard
- Sends the result back as a native Telegram voice message
- Runs a tiny Flask server in a background thread so Render's port-binding
  health check passes (required for "Web Service" deployments)

Environment variables required on Render:
    TOKEN   -> your Telegram Bot token from @BotFather
    PORT    -> automatically injected by Render (defaults to 8080 locally)
"""

import os
import logging
import tempfile
import threading
from typing import Dict

import edge_tts
from langdetect import detect, DetectorFactory
from flask import Flask

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# --------------------------------------------------------------------------- #
# Setup & configuration
# --------------------------------------------------------------------------- #

# Make langdetect deterministic (it's seeded randomly by default).
DetectorFactory.seed = 0

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
# Quiet down noisy third-party loggers.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logger = logging.getLogger("tts-bot")

TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    raise RuntimeError(
        "TOKEN environment variable is not set. "
        "Add it in Render's dashboard under Environment > Environment Variables."
    )

PORT = int(os.environ.get("PORT", 8080))

# In-memory store of each user's voice-gender preference.
# Key: telegram user_id -> "male" | "female"
# (Resets on restart. Swap for a real DB/Redis if you need persistence.)
user_voice_preference: Dict[int, str] = {}

DEFAULT_GENDER = "female"

# --------------------------------------------------------------------------- #
# Language -> Edge-TTS voice mapping
# Each language maps to a (female_voice, male_voice) pair.
# Voice names follow Microsoft's "xx-XX-NameNeural" convention.
# --------------------------------------------------------------------------- #
VOICE_MAP = {
    "en": ("en-US-JennyNeural", "en-US-GuyNeural"),
    "hi": ("hi-IN-SwaraNeural", "hi-IN-MadhurNeural"),
    "es": ("es-ES-ElviraNeural", "es-ES-AlvaroNeural"),
    "fr": ("fr-FR-DeniseNeural", "fr-FR-HenriNeural"),
    "de": ("de-DE-KatjaNeural", "de-DE-ConradNeural"),
    "it": ("it-IT-ElsaNeural", "it-IT-DiegoNeural"),
    "pt": ("pt-BR-FranciscaNeural", "pt-BR-AntonioNeural"),
    "ru": ("ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural"),
    "ja": ("ja-JP-NanamiNeural", "ja-JP-KeitaNeural"),
    "ko": ("ko-KR-SunHiNeural", "ko-KR-InJoonNeural"),
    "zh-cn": ("zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural"),
    "zh-tw": ("zh-TW-HsiaoChenNeural", "zh-TW-YunJheNeural"),
    "ar": ("ar-SA-ZariyahNeural", "ar-SA-HamedNeural"),
    "bn": ("bn-IN-TanishaaNeural", "bn-IN-BashkarNeural"),
    "ur": ("ur-PK-UzmaNeural", "ur-PK-AsadNeural"),
    "tr": ("tr-TR-EmelNeural", "tr-TR-AhmetNeural"),
    "vi": ("vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"),
    "th": ("th-TH-PremwadeeNeural", "th-TH-NiwatNeural"),
    "id": ("id-ID-GadisNeural", "id-ID-ArdiNeural"),
    "nl": ("nl-NL-FennaNeural", "nl-NL-MaartenNeural"),
    "pl": ("pl-PL-ZofiaNeural", "pl-PL-MarekNeural"),
    "uk": ("uk-UA-PolinaNeural", "uk-UA-OstapNeural"),
    "el": ("el-GR-AthinaNeural", "el-GR-NestorasNeural"),
    "he": ("he-IL-HilaNeural", "he-IL-AvriNeural"),
    "fa": ("fa-IR-DilaraNeural", "fa-IR-FaridNeural"),
    "sv": ("sv-SE-SofieNeural", "sv-SE-MattiasNeural"),
    "ta": ("ta-IN-PallaviNeural", "ta-IN-ValluvarNeural"),
    "te": ("te-IN-ShrutiNeural", "te-IN-MohanNeural"),
    "mr": ("mr-IN-AarohiNeural", "mr-IN-ManoharNeural"),
    "gu": ("gu-IN-DhwaniNeural", "gu-IN-NiranjanNeural"),
    "pa": ("pa-IN-OjasNeural", "pa-IN-VaaniNeural"),
}

# Fallback voice pair if langdetect returns a language we haven't mapped.
FALLBACK_VOICES = ("en-US-JennyNeural", "en-US-GuyNeural")


def get_voice_for_text(lang_code: str, gender: str) -> str:
    """Pick the right Edge-TTS voice for a detected language + gender."""
    lang_code = lang_code.lower()
    female_voice, male_voice = VOICE_MAP.get(lang_code, FALLBACK_VOICES)
    return male_voice if gender == "male" else female_voice


# --------------------------------------------------------------------------- #
# Telegram handlers
# --------------------------------------------------------------------------- #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Greet the user and let them pick a voice gender."""
    keyboard = [
        [
            InlineKeyboardButton("🔊 Male Voice", callback_data="voice_male"),
            InlineKeyboardButton("🔊 Female Voice", callback_data="voice_female"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "👋 Welcome to the Text-to-Speech Bot!\n\n"
        "Send me any text in *almost any language* and I'll reply with a "
        "natural-sounding voice message.\n\n"
        "First, choose your preferred voice:",
        reply_markup=reply_markup,
        parse_mode="Markdown",
    )


async def voice_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the inline keyboard button press for voice gender."""
    query = update.callback_query
    await query.answer()  # stop the loading spinner on the button

    user_id = query.from_user.id
    gender = "male" if query.data == "voice_male" else "female"
    user_voice_preference[user_id] = gender

    await query.edit_message_text(
        f"✅ Voice set to *{gender.capitalize()}*.\n\n"
        "Now just send me a text message in any language and I'll read it out loud!\n\n"
        "_You can change your voice anytime by sending /start again._",
        parse_mode="Markdown",
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Detect language, synthesize speech, and reply with a voice message."""
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not text:
        await update.message.reply_text("Please send some text for me to read out loud.")
        return

    if len(text) > 2000:
        await update.message.reply_text(
            "That message is a bit too long for me (max 2000 characters). "
            "Please send a shorter text."
        )
        return

    gender = user_voice_preference.get(user_id, DEFAULT_GENDER)

    # Detect language; fall back to English if detection fails (e.g. emoji-only text).
    try:
        lang_code = detect(text)
    except Exception:
        lang_code = "en"

    voice = get_voice_for_text(lang_code, gender)

    # Show a "recording voice" indicator while we generate the audio.
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)

    tmp_path = None
    try:
        # Generate a unique temp file path (.ogg works as a native voice note).
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_file:
            tmp_path = tmp_file.name

        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(tmp_path)

        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VOICE)

        with open(tmp_path, "rb") as audio_file:
            await update.message.reply_voice(
                voice=audio_file,
                caption=f"🌐 Detected: {lang_code} | 🎙️ Voice: {gender.capitalize()}",
            )

    except Exception as exc:
        logger.exception("Failed to generate/send speech: %s", exc)
        await update.message.reply_text(
            "⚠️ Sorry, I couldn't generate the audio for that message. Please try again."
        )

    finally:
        # Always clean up the temp file from disk, success or failure.
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError as exc:
                logger.warning("Could not delete temp file %s: %s", tmp_path, exc)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "ℹ️ *How to use this bot:*\n\n"
        "1. /start - choose Male or Female voice\n"
        "2. Send any text message in any language\n"
        "3. Get back a voice message read in that language\n\n"
        "Commands:\n"
        "/start - choose your voice\n"
        "/help - show this message",
        parse_mode="Markdown",
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors caused by updates so the bot doesn't crash silently."""
    logger.error("Update %s caused error: %s", update, context.error, exc_info=context.error)


# --------------------------------------------------------------------------- #
# Flask keep-alive server (required so Render's Web Service detects an open
# port and doesn't kill the deployment with a timeout).
# --------------------------------------------------------------------------- #

flask_app = Flask(__name__)


@flask_app.route("/")
def home():
    return "Bot is running", 200


@flask_app.route("/health")
def health():
    return {"status": "ok"}, 200


def run_flask():
    # use_reloader/debug must be off — this runs in a background thread.
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)


# --------------------------------------------------------------------------- #
# Application bootstrap
# --------------------------------------------------------------------------- #

def main() -> None:
    # Start the Flask server on a daemon thread so it doesn't block the
    # asyncio event loop that python-telegram-bot needs, and so it shuts
    # down automatically if the main process exits.
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask keep-alive server started on port %s", PORT)

    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(voice_choice_callback, pattern="^voice_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_error_handler(error_handler)

    logger.info("Starting Telegram bot polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
  
