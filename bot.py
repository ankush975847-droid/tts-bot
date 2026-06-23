"""
Multi-language Text-to-Speech Telegram Bot (Realistic Voice Version)
===================================================================
- Auto-detects the language of incoming text (langdetect)
- Converts text to speech using Microsoft Edge's free TTS engine (edge-tts)
- Slows speech rate by 12% for more natural human pacing
- Lets the user pick a Male / Female voice via inline keyboard
- Sends the result back as a native Telegram voice message
- Runs a tiny Flask server in a background thread for Render compatibility
"""

import asyncio
import os
import logging
import tempfile
import threading
from typing import Dict

import edge_tts
from langdetect import detect, DetectorFactory
from flask import Flask
from waitress import serve as waitress_serve

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.request import HTTPXRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# Setup & configuration
DetectorFactory.seed = 0

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
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
user_voice_preference: Dict[int, str] = {}
DEFAULT_GENDER = "female"

# Speech rate adjustment for more natural pacing (edge_tts accepts this
# directly via the `rate` kwarg, so we no longer need to hand-build SSML).
SPEECH_RATE = "-12%"

# --------------------------------------------------------------------------- #
# HIGH QUALITY REALISTIC VOICES MAP
# --------------------------------------------------------------------------- #
VOICE_MAP = {
    "en": ("en-US-EmmaNeural", "en-US-BrianNeural"),       # Ultra-realistic English
    "hi": ("hi-IN-SwaraNeural", "hi-IN-MadhurNeural"),     # Best Indian Hindi voices
    "es": ("es-ES-ElviraNeural", "es-ES-AlvaroNeural"),
    "fr": ("fr-FR-DeniseNeural", "fr-FR-HenriNeural"),
    "de": ("de-DE-KatjaNeural", "de-DE-ConradNeural"),
    "it": ("it-IT-ElsaNeural", "it-IT-DiegoNeural"),
    "pt": ("pt-BR-FranciscaNeural", "pt-BR-AntonioNeural"),
    "ru": ("ru-RU-SvetlanaNeural", "ru-RU-DmitryNeural"),
    "ja": ("ja-JP-NanamiNeural", "ja-JP-KeitaNeural"),
    "ko": ("ko-KR-SunHiNeural", "ko-KR-InJoonNeural"),
    "zh-cn": ("zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural"),
    "ar": ("ar-SA-ZariyahNeural", "ar-SA-HamedNeural"),
    "bn": ("bn-IN-TanishaaNeural", "bn-IN-BashkarNeural"),
    "ur": ("ur-PK-UzmaNeural", "ur-PK-AsadNeural"),
}

FALLBACK_VOICES = ("en-US-EmmaNeural", "en-US-BrianNeural")


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
            InlineKeyboardButton("👨 Male Voice (Realistic)", callback_data="voice_male"),
            InlineKeyboardButton("👩 Female Voice (Realistic)", callback_data="voice_female"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "👋 **Welcome to the Real-Sounding TTS Bot!**\n\n"
        "आप मुझे किसी भी भाषा में टेक्स्ट भेजें, मैं उसे बिल्कुल **असली इंसानी आवाज़** में बदल दूँगा।\n\n"
        "कृपया अपनी पसंदीदा आवाज़ चुनें:",
        reply_markup=reply_markup,
        parse_mode="Markdown",
    )


async def voice_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the inline keyboard button press for voice gender."""
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    gender = "male" if query.data == "voice_male" else "female"
    user_voice_preference[user_id] = gender

    await query.edit_message_text(
        f"✅ आवाज़ को *{gender.capitalize()}* पर सेट कर दिया गया है।\n\n"
        "अब मुझे कोई भी टेक्स्ट मैसेज भेजें, मैं उसे पढ़कर सुनाऊँगा!\n\n"
        "_अगर फिर से आवाज़ बदलनी हो, तो दोबारा /start भेजें।_",
        parse_mode="Markdown",
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Detect language, synthesize speech, and reply with a voice note."""
    text = update.message.text.strip()
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if not text:
        return

    if len(text) > 2000:
        await update.message.reply_text("मैसेज बहुत लंबा है (अधिकतम 2000 अक्षर)।")
        return

    gender = user_voice_preference.get(user_id, DEFAULT_GENDER)

    try:
        lang_code = detect(text)
    except Exception:
        lang_code = "en"

    voice = get_voice_for_text(lang_code, gender)

    # Show "recording voice" indicator
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.RECORD_VOICE)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp_file:
            tmp_path = tmp_file.name

        # FIX: Pass plain text straight to edge_tts and let it build the
        # SSML internally via the `rate` parameter. The previous version
        # hand-built an SSML string and interpolated the raw user text into
        # it unescaped — any "&", "<", ">" or '"' in the message produced
        # invalid XML and made every such request fail. It also passed
        # `is_ssml=True` to Communicate, which isn't part of its
        # constructor signature. Letting edge_tts handle this internally
        # avoids both problems and is the documented way to set speech
        # rate.
        communicate = edge_tts.Communicate(text, voice, rate=SPEECH_RATE)
        await communicate.save(tmp_path)

        # Guard against the free endpoint occasionally returning an empty
        # file on a transient hiccup -- sending a 0-byte voice note looks
        # like a successful reply but produces unplayable audio.
        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            raise RuntimeError("edge_tts produced an empty audio file")

        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_VOICE)

        with open(tmp_path, "rb") as audio_file:
            await update.message.reply_voice(
                voice=audio_file,
                caption=f"🌐 Language: {lang_code.upper()} | 🎙️ Voice: {gender.capitalize()} (Real)",
            )

    except Exception as exc:
        logger.exception("Failed to generate speech: %s", exc)
        await update.message.reply_text("⚠️ माफ़ कीजिएगा, आवाज़ बनाने में कोई दिक्कत आई।")

    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("Use /start to configure the bot and send any text.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # FIX: telegram.error.TimedOut / NetworkError during long-polling are
    # transient and PTB's polling loop already retries them on its own —
    # logging the full traceback as an `error` for every blip is noisy and
    # makes a harmless network hiccup look like a crash. Anything else is
    # still logged at full severity.
    from telegram.error import TimedOut, NetworkError

    if isinstance(context.error, (TimedOut, NetworkError)):
        logger.warning("Transient network error (will retry automatically): %s", context.error)
        return

    logger.error("Update %s caused error: %s", update, context.error, exc_info=context.error)


# --------------------------------------------------------------------------- #
# Flask keep-alive server for Render
# --------------------------------------------------------------------------- #
flask_app = Flask(__name__)


@flask_app.route("/")
def home():
    return "Bot is running with Realistic Voice Mode!", 200


def run_flask():
    # FIX: Flask's built-in `app.run()` is a development server -- it
    # warns against this explicitly and isn't designed to run reliably,
    # indefinitely, in a background thread alongside a competing asyncio
    # event loop (the bot's polling loop). Under Render's container this
    # combination has been seen to make the keep-alive port flaky or slow
    # to open, which is consistent with Render's port scanner timing out
    # even though the thread technically started. `waitress` is a small,
    # pure-Python production WSGI server with none of those caveats.
    try:
        logger.info("Flask server attempting to bind 0.0.0.0:%s", PORT)
        waitress_serve(flask_app, host="0.0.0.0", port=PORT)
    except Exception:
        logger.exception("Flask server failed to start -- Render will not detect an open port")


# --------------------------------------------------------------------------- #
# Application bootstrap
# --------------------------------------------------------------------------- #
def main() -> None:
    # FIX: removed the manual `asyncio.new_event_loop()` /
    # `asyncio.set_event_loop()` calls that used to live here. PTB v20+'s
    # `run_polling()` creates and manages its own event loop internally;
    # pre-creating one and setting it as current does nothing useful and
    # can raise a RuntimeError or warning on some PTB/Python versions when
    # `run_polling()` tries to set up its own loop afterward.
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask keep-alive server started on port %s", PORT)

    # FIX: telegram.error.TimedOut in the Render logs comes from PTB's
    # default HTTPXRequest timeouts being too tight for the network path
    # between Render and Telegram's servers — a brief latency spike is
    # enough to trip it even though nothing is actually broken. We build
    # a custom HTTPXRequest with longer connect/read/write/pool timeouts
    # and a bigger connection pool, and pass get_updates-specific timeouts
    # (used for the long-polling call itself) so polling tolerates slow
    # responses instead of erroring out.
    request = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=20.0,
        read_timeout=20.0,
        write_timeout=20.0,
        pool_timeout=20.0,
    )

    application = (
        ApplicationBuilder()
        .token(TOKEN)
        .request(request)
        .get_updates_connect_timeout(30.0)
        .get_updates_read_timeout(30.0)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CallbackQueryHandler(voice_choice_callback, pattern="^voice_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_error_handler(error_handler)

    logger.info("Starting Telegram bot polling...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
