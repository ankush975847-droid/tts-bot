import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton
import os
import re
import threading
import time
from flask import Flask
import logging
import random
import warnings
import uuid
import io
import copy

FILE_LOCK = threading.Lock()  # Prevents race conditions on users.txt
DATA_LOCK = threading.Lock()  # Protects all per-user session dictionaries

# ── Core Level Fixes & Requirements ──────────────────────────────────────────
os.environ['OPENPYXL_LXML'] = 'False'
warnings.filterwarnings("ignore", category=UserWarning)

try:
    import openpyxl
except ImportError:
    import sys
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "openpyxl"])
    import openpyxl

# ── Settings ──────────────────────────────────────────────────────────────────
TOKEN   = os.environ.get('TOKEN')
ADMIN_ID = int(os.environ.get('ADMIN_ID', 8632094892))
DEFAULT_SPLIT_LIMIT = 200   # Internal default for Admin/Navy module ONLY

bot = telebot.TeleBot(TOKEN, threaded=True)
user_data     = {}   # keyed by chat_id (int)
user_langs    = {}   # keyed by chat_id (int)
merge_storage = {}   # keyed by chat_id (int)
user_captions = {}   # keyed by chat_id (int) — unused externally; kept for /caption toggle

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask('')

@app.route('/')
def home():
    return "VCF Bot Continuous Full Framework Live!"

threading.Thread(
    target=lambda: app.run(
        host='0.0.0.0',
        port=int(os.environ.get('PORT', 8080)),
        debug=False,
        use_reloader=False
    )
).start()


# ── Utility helpers ───────────────────────────────────────────────────────────

def save_user(user_id):
    try:
        with FILE_LOCK:
            if not os.path.exists("users.txt"):
                open("users.txt", 'w').close()
            with open("users.txt", "r") as f:
                existing = f.read().splitlines()
            if str(user_id) not in existing:
                with open("users.txt", "a") as f:
                    f.write(f"{user_id}\n")
    except Exception as e:
        logging.error(f"Error saving user: {e}")


def escape_markdown(text):
    if not text:
        return ""
    return re.sub(r'([_*\[\`])', r'\\\1', str(text))


def safe_delete_file(path):
    """Safely delete a temp file, logging errors without raising."""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception as e:
        logging.error(f"Failed to delete temp file {path}: {e}")


# ── Language Database (EN, HI, ZH) ───────────────────────────────────────────
TEXTS = {
    'en': {
        'welcome':       "<tg-emoji emoji-id=\"6161188739969194553\">📍</tg-emoji> 👋 <b>Welcome!</b> Choose an option from the menu below:",
        'send_txt':      "<tg-emoji emoji-id=\"5357315181649076022\">📁</tg-emoji> <b>Text/Excel to VCF Module</b>\n\nPlease send your <code>.txt</code> or <code>.xlsx</code> file, or <b>paste numbers</b> directly.\n\nType /cancel anytime to return to main menu.",
        'scan_success':  "✅ **Collecting Contacts**\n🔍 Total Added: {count}\n\n1️⃣ Enter VCF file Name:",
        'enter_prefix':  "2️⃣ Enter Contact Prefix Name:",
        'enter_company': "3️⃣ Enter Company Name (or type 'skip'):",
        'enter_split':   "5️⃣ How many contacts per VCF file?\n💡 *(Safe Margin: 200 - 250)*:",
        'success':       "<tg-emoji emoji-id=\"5461151367559141950\">🎉</tg-emoji> <b>VCF Generation Completed Successfully!</b> 💯",
        'send_vcf':      "<tg-emoji emoji-id=\"5357315181649076022\">📁</tg-emoji> <b>VCF to Text Module</b>\n\nPlease send your <code>.vcf</code> file.",
        'invalid_file':  "<tg-emoji emoji-id=\"5765005318610228026\">❌</tg-emoji> Invalid input or file format. Please try again.",
        'invalid_number':"<tg-emoji emoji-id=\"5765005318610228026\">❌</tg-emoji> Please enter a valid positive number.",
        'send_split_vcf':"<tg-emoji emoji-id=\"5357315181649076022\">📁</tg-emoji> <b>Split File Module</b>\n\n📥 Upload ANY <code>.vcf</code> or <code>.txt</code> file to split into smaller parts...\n\n🔄 <b>Waiting for file...</b>",
        'ask_split_limit':"🔢 Contacts per split file?:",
        'send_merge_vcf':"<tg-emoji emoji-id=\"5357315181649076022\">📁</tg-emoji> <b>Merge File Module</b>\n\n📥 Upload multiple <code>.vcf</code> or <code>.txt</code> files to merge them together...",
        'merge_added':   "✅ File added! Queue: {count}\nSend next file or type /done to merge.",
        'no_merge_files':"❌ Empty queue. Please send at least one `.vcf` file before typing /done.",
        'merging':       "🔄 Merging...",
        'enter_editor_vcf': "<tg-emoji emoji-id=\"5357315181649076022\">📁</tg-emoji> <b>VCF Editor Module</b>\n\nPlease send the <code>.vcf</code> file:",
        'ask_new_prefix':"📝 Enter New Prefix Name:",
        'cancelled':     "❌ Cancelled.",
        'b_txt2vcf':     "📄 Text/Excel to VCF",
        'b_vcf2txt':     "📇 VCF to Text",
        'b_navy':        "👑 Admin/Navy VCF",
        'b_editor':      "✏️ VCF Editor",
        'b_merge':       "🔗 Merge File",
        'b_split':       "✂️ Split File",
        'b_rename':      "⚙️ Rename File",
        'b_details':     "🔍 Get VCF Details",
        'ask_rename':    "<tg-emoji emoji-id=\"5357315181649076022\">📁</tg-emoji> <b>Rename File Module</b>\n\n📥 Upload ANY file (<code>.txt</code>, <code>.vcf</code>, <code>.csv</code>, etc.) to change its name instantly.\n\n🔄 <b>Waiting for file...</b>",
        'ask_details':   "<tg-emoji emoji-id=\"5357315181649076022\">📁</tg-emoji> <b>VCF Details Scanner</b>\n\n📥 Upload a <code>.vcf</code> file to extract names and details...\n\n🔄 <b>Waiting for file...</b>"
    },
    'hi': {
        'welcome':       "<tg-emoji emoji-id=\"6161188739969194553\">📍</tg-emoji> 👋 <b>VCF Maker में स्वागत है!</b>\n\nनीचे दिए गए बटन्स में से एक option चुनें:",
        'send_txt':      "<tg-emoji emoji-id=\"5357315181649076022\">📁</tg-emoji> <b>Text/Excel to VCF Module</b>\n\nकृपया अपनी <code>.txt</code> या एक्सेल फ़ाइल भेजें, या सीधे चैट में नंबर पेस्ट करें।",
        'scan_success':  "✅ **Collecting Contacts**\n🔍 कुल नंबर्स मिले: {count}\n\n1️⃣ जनरेट होने वाली VCF फ़ाइल का **नाम** दर्ज करें:",
        'enter_prefix':  "2️⃣ कॉन्टैक्ट का **Prefix Name** दर्ज करें:",
        'enter_company': "3️⃣ **कंपनी का नाम** दर्ज करें (या 'skip' लिखें):",
        'enter_split':   "5️⃣ एक VCF फ़ाइल में **कितने कॉन्टैक्ट्स** रखने हैं?\n💡 *(व्हाट्सएप मार्केटिंग मार्जिन: 200 - 250)*:",
        'success':       "<tg-emoji emoji-id=\"5461151367559141950\">🎉</tg-emoji> <b>VCF जनरेशन सफलतापूर्वक पूरा हुआ!</b> 💯",
        'send_vcf':      "<tg-emoji emoji-id=\"5357315181649076022\">📁</tg-emoji> <b>VCF to Text Module</b>\n\nकृपया अपनी <code>.vcf</code> फ़ाइल भेजें।",
        'invalid_file':  "<tg-emoji emoji-id=\"5765005318610228026\">❌</tg-emoji> गलत इनपुट या फ़ाइल फॉर्मेट। कृपया पुनः प्रयास करें।",
        'invalid_number':"<tg-emoji emoji-id=\"5765005318610228026\">❌</tg-emoji> कृपया एक सही और सकारात्मक संख्या (नंबर) दर्ज करें।",
        'send_split_vcf':"<tg-emoji emoji-id=\"5357315181649076022\">📁</tg-emoji> <b>Split File Module</b>\n\n📥 स्प्लिट करने के लिए कोई भी <code>.vcf</code> या <code>.txt</code> फ़ाइल अपलोड करें...\n\n🔄 <b>Waiting for file...</b>",
        'ask_split_limit':"🔢 प्रत्येक स्प्लिट फ़ाइल में कितने कॉन्टैक्ट्स चाहिए?:",
        'send_merge_vcf':"<tg-emoji emoji-id=\"5357315181649076022\">📁</tg-emoji> <b>Merge File Module</b>\n\n📥 आपस में जोड़ने के लिए कई <code>.vcf</code> या <code>.txt</code> फ़ाइलें अपलोड करें...",
        'merge_added':   "✅ फ़ाइल जुड़ गई! कुल: {count}\nअगली फ़ाइल भेजें या मर्ज करने के लिए /done लिखें।",
        'no_merge_files':"❌ सूची खाली है। /done भेजने से पहले कृपया कम से कम एक `.vcf` फ़ाइल ज़रूर भेजें।",
        'merging':       "🔄 फ़ाइलों को जोड़ा जा रहा है...",
        'enter_editor_vcf': "<tg-emoji emoji-id=\"5357315181649076022\">📁</tg-emoji> <b>VCF Editor Module</b>\n\nकृपया <code>.vcf</code> फ़ाइल भेजें:",
        'ask_new_prefix':"📝 नया प्रीफिक्स नाम दर्ज करें:",
        'cancelled':     "❌ प्रक्रिया रद्द कर दी गई है।",
        'b_txt2vcf':     "📄 टेक्स्ट/एक्सेल को VCF",
        'b_vcf2txt':     "📇 VCF को टेक्स्ट बदलें",
        'b_navy':        "👑 Admin/Navy VCF",
        'b_editor':      "✏️ VCF एडिटर",
        'b_merge':       "🔗 फाइल मर्ज करें",
        'b_split':       "✂️ फाइल स्प्लिट करें",
        'b_rename':      "⚙️ नाम बदलें",
        'b_details':     "🔍 VCF विवरण प्राप्त करें",
        'ask_rename':    "<tg-emoji emoji-id=\"5357315181649076022\">📁</tg-emoji> <b>Rename File Module</b>\n\n📥 नाम बदलने के लिए कोई भी फ़ाइल (<code>.txt</code>, <code>.vcf</code>, <code>.csv</code>, आदि) अपलोड करें।\n\n🔄 <b>Waiting for file...</b>",
        'ask_details':   "<tg-emoji emoji-id=\"5357315181649076022\">📁</tg-emoji> <b>VCF Details Scanner</b>\n\n📥 विवरण निकालने के लिए कोई भी <code>.vcf</code> फ़ाइल अपलोड करें...\n\n🔄 <b>Waiting for file...</b>"
    },
    'zh': {
        'welcome':       "<tg-emoji emoji-id=\"6161188739969194553\">📍</tg-emoji> 👋 <b>欢迎使用 VCF 生成器！</b> 请从下方菜单选择一个选项：",
        'send_txt':      "<tg-emoji emoji-id=\"5357315181649076022\">📁</tg-emoji> <b>文本/Excel 转 VCF 模块</b>\n\n请发送您的 <code>.txt</code> 或 <code>.xlsx</code> 文件，或者直接<b>粘贴号码</b>。\n\n随时输入 /cancel 可返回主菜单。",
        'scan_success':  "✅ **正在收集联系人**\n🔍 已添加总数: {count}\n\n1️⃣ 输入 VCF 文件名称:",
        'enter_prefix':  "2️⃣ 输入联系人前缀名称:",
        'enter_company': "3️⃣ 输入公司名称 (或输入 'skip' 跳过):",
        'enter_split':   "5️⃣ 每个 VCF 文件包含多少个联系人？\n💡 *(安全范围: 200 - 250)*:",
        'success':       "<tg-emoji emoji-id=\"5461151367559141950\">🎉</tg-emoji> <b>VCF 文件成功生成！</b> 💯",
        'send_vcf':      "<tg-emoji emoji-id=\"5357315181649076022\">📁</tg-emoji> <b>VCF 转 文本模块</b>\n\n请发送您的 <code>.vcf</code> 文件。",
        'invalid_file':  "<tg-emoji emoji-id=\"5765005318610228026\">❌</tg-emoji> 输入或文件格式无效。请重试。",
        'invalid_number':"<tg-emoji emoji-id=\"5765005318610228026\">❌</tg-emoji> 请输入有效的正整数。",
        'send_split_vcf':"<tg-emoji emoji-id=\"5357315181649076022\">📁</tg-emoji> <b>拆分文件模块</b>\n\n📥 上传任意 <code>.vcf</code> 或 <code>.txt</code> 文件以拆分为较小的部分...\n\n🔄 <b>正在等待文件...</b>",
        'ask_split_limit':"🔢 每个拆分文件的联系人数量？:",
        'send_merge_vcf':"<tg-emoji emoji-id=\"5357315181649076022\">📁</tg-emoji> <b>合并文件模块</b>\n\n📥 上传多个 <code>.vcf</code> 或 <code>.txt</code> 文件以将它们合并在一起...",
        'merge_added':   "✅ 文件已添加！当前队列: {count}\n发送下一个文件或输入 /done 进行合并。",
        'no_merge_files':"❌ 队列为空。在输入 /done 之前，请至少发送一个 `.vcf` 文件。",
        'merging':       "🔄 正在合并...",
        'enter_editor_vcf': "<tg-emoji emoji-id=\"5357315181649076022\">📁</tg-emoji> <b>VCF 编辑器模块</b>\n\n请发送 <code>.vcf</code> 文件:",
        'ask_new_prefix':"📝 输入新的前缀名称:",
        'cancelled':     "❌ 已取消。",
        'b_txt2vcf':     "📄 文本/Excel 转 VCF",
        'b_vcf2txt':     "📇 VCF 转 文本",
        'b_navy':        "👑 高级 Admin/Navy VCF",
        'b_editor':      "✏️ VCF 编辑器",
        'b_merge':       "🔗 合并文件",
        'b_split':       "✂️ 拆分文件",
        'b_rename':      "⚙️ 重命名文件",
        'b_details':     "🔍 获取 VCF 详情",
        'ask_rename':    "<tg-emoji emoji-id=\"5357315181649076022\">📁</tg-emoji> <b>重命名文件模块</b>\n\n📥 上传任意文件 (<code>.txt</code>, <code>.vcf</code>, <code>.csv</code> 等) 立即修改名称。\n\n🔄 <b>正在等待文件...</b>",
        'ask_details':   "<tg-emoji emoji-id=\"5357315181649076022\">📁</tg-emoji> <b>VCF 详情扫描器</b>\n\n📥 上传 <code>.vcf</code> 文件以提取姓名和详细信息...\n\n🔄 <b>正在等待文件...</b>"
    }
}

# ── Build the complete set of known menu-button texts (all 3 languages) ───────
ALL_BUTTON_TEXTS = set()
for _lang in TEXTS:
    for _key, _val in TEXTS[_lang].items():
        if _key.startswith('b_'):
            ALL_BUTTON_TEXTS.add(_val)

# Language-selector button texts
LANG_BUTTON_TEXTS = {"🇬🇧 English", "🇮🇳 हिन्दी", "🇨🇳 简体中文"}

# Combined set: every text that the catch-all handler must treat as a menu event
ALL_MENU_TEXTS = ALL_BUTTON_TEXTS | LANG_BUTTON_TEXTS


# ════════════════════════════════════════════════════════════════════════════════
#  CORE ANTI-LOOP GATE-KEEPER
#
#  KEY FIX — this function is the ONLY place that routes menu buttons.
#  The catch-all handler (handle_menu_and_languages) now ONLY calls this
#  function and does NOTHING ELSE.  _dispatch_menu_button is called
#  exclusively from here, never from handle_menu_and_languages directly.
#  This collapses the execution graph to a single path and eliminates every
#  possible re-entrancy scenario.
# ════════════════════════════════════════════════════════════════════════════════

def check_menu_or_commands(message):
    """Gate-keeper called at the top of every step-handler AND by the catch-all.

    Returns True  → caller must stop immediately (message was handled here).
    Returns False → normal step input; caller should proceed normally.

    Thread-safety contract
    ──────────────────────
    State is cleared under DATA_LOCK BEFORE any routing logic executes, so
    a concurrent thread that also enters here for the same chat_id will find
    an already-clean state and will not register duplicate step-handlers.
    """
    if not message or not message.text:
        return False

    text     = message.text.strip()
    chat_id  = message.chat.id
    base_cmd = text.split()[0].lower() if text.startswith('/') else None

    # /done and /skip are step-handler signals — let them fall through
    if base_cmd in ('/done', '/skip') or text == "✅ Done":
        return False

    is_global_cmd = base_cmd in (
        '/start', '/language', '/help', '/cancel',
        '/caption', '/ping', '/stats', '/broadcast'
    )
    is_menu_btn = text in ALL_MENU_TEXTS

    if not is_global_cmd and not is_menu_btn:
        return False

    # ── CRITICAL: atomically wipe step handler + session state FIRST ──────────
    # clear_step_handler_by_chat_id is telebot's own thread-safe method;
    # we call it before acquiring DATA_LOCK to avoid a potential deadlock
    # with telebot's internal lock inside that method.
    bot.clear_step_handler_by_chat_id(chat_id=chat_id)
    with DATA_LOCK:
        user_data.pop(chat_id, None)
        merge_storage.pop(chat_id, None)
    # ──────────────────────────────────────────────────────�����──────────────────

    if is_global_cmd:
        cmd_map = {
            '/start':     send_welcome,
            '/language':  send_welcome,
            '/help':      help_command,
            '/cancel':    cancel_command,
            '/caption':   caption_toggle,
            '/ping':      ping_speed_test,
            '/stats':     bot_statistics_check,
            '/broadcast': broadcast_message,
        }
        cmd_map[base_cmd](message)
    else:
        # is_menu_btn — route through the single dispatcher; NEVER call
        # handle_menu_and_languages from here (that would be circular).
        _dispatch_menu_button(message)

    return True   # ← tells every caller: "I handled it, you must return now"


def _dispatch_menu_button(message):
    """Route a confirmed menu-button press.

    Called ONLY from check_menu_or_commands.
    NEVER called directly from handle_menu_and_languages.
    This keeps the call graph strictly acyclic:

        handle_menu_and_languages
               ↓
        check_menu_or_commands  (checks + clears state)
               ↓
        _dispatch_menu_button   (registers fresh step-handler)

    No function in _dispatch_menu_button ever calls back into
    check_menu_or_commands or handle_menu_and_languages.
    """
    chat_id = message.chat.id
    text    = message.text.strip()
    lang    = user_langs.get(chat_id, 'en')
    t       = TEXTS[lang]

    # ── Language-selector buttons ─────────────────────────────────────────────
    if text in LANG_BUTTON_TEXTS:
        if "English" in text:
            new_lang = 'en'
        elif "हिन्दी" in text:
            new_lang = 'hi'
        else:
            new_lang = 'zh'
        with DATA_LOCK:
            user_langs[chat_id] = new_lang
        bot.send_message(
            chat_id,
            TEXTS[new_lang]['welcome'],
            reply_markup=get_main_menu_keyboard(new_lang, chat_id),
            parse_mode="HTML"
        )
        return

    # ── Module routing ────────────────────────────────────────────────────────
    if text in [TEXTS['en']['b_txt2vcf'], TEXTS['hi']['b_txt2vcf'], TEXTS['zh']['b_txt2vcf']]:
        markup = InlineKeyboardMarkup().add(
            InlineKeyboardButton("✅ Done", callback_data="action_done")
        )
        bot.send_message(chat_id, t['send_txt'], reply_markup=markup, parse_mode="HTML")
        with DATA_LOCK:
            user_data[chat_id] = {'numbers': [], 'mode': 'normal', 'origin_chat_id': chat_id}
        bot.register_next_step_handler_by_chat_id(chat_id, process_inputs)

    elif text in [TEXTS['en']['b_navy'], TEXTS['hi']['b_navy'], TEXTS['zh']['b_navy']]:
        with DATA_LOCK:
            user_data[chat_id] = {
                'mode': 'navy_dual',
                'admin_numbers': [],
                'navy_numbers': [],
                'admin_prefix': '',
                'navy_prefix': '',
                'filename': '',
                'origin_chat_id': chat_id
            }
        markup = InlineKeyboardMarkup().add(
            InlineKeyboardButton("⏭️ Skip Admin", callback_data="skip_admin")
        )
        bot.send_message(
            chat_id,
            "👑 **Step 1 • Admin Contacts**\n\n📥 Send Admin numbers or files...",
            reply_markup=markup,
            parse_mode="Markdown"
        )
        bot.register_next_step_handler_by_chat_id(chat_id, process_navy_admin_inputs)

    elif text in [TEXTS['en']['b_vcf2txt'], TEXTS['hi']['b_vcf2txt'], TEXTS['zh']['b_vcf2txt']]:
        bot.send_message(chat_id, t['send_vcf'], parse_mode="HTML")
        bot.register_next_step_handler_by_chat_id(chat_id, process_vcf_to_txt)

    elif text in [TEXTS['en']['b_split'], TEXTS['hi']['b_split'], TEXTS['zh']['b_split']]:
        bot.send_message(chat_id, t['send_split_vcf'], parse_mode="HTML")
        bot.register_next_step_handler_by_chat_id(chat_id, process_split_vcf)

    elif text in [TEXTS['en']['b_merge'], TEXTS['hi']['b_merge'], TEXTS['zh']['b_merge']]:
        with DATA_LOCK:
            merge_storage[chat_id] = []
        bot.send_message(chat_id, t['send_merge_vcf'], parse_mode="HTML")
        bot.register_next_step_handler_by_chat_id(chat_id, process_merge_vcf)

    elif text in [TEXTS['en']['b_editor'], TEXTS['hi']['b_editor'], TEXTS['zh']['b_editor']]:
        bot.send_message(chat_id, t['enter_editor_vcf'], parse_mode="HTML")
        bot.register_next_step_handler_by_chat_id(chat_id, process_editor_vcf)

    elif text in [TEXTS['en']['b_rename'], TEXTS['hi']['b_rename'], TEXTS['zh']['b_rename']]:
        bot.send_message(chat_id, t['ask_rename'], parse_mode="HTML")
        bot.register_next_step_handler_by_chat_id(chat_id, process_rename_vcf)

    elif text in [TEXTS['en']['b_details'], TEXTS['hi']['b_details'], TEXTS['zh']['b_details']]:
        bot.send_message(chat_id, t['ask_details'], parse_mode="HTML")
        bot.register_next_step_handler_by_chat_id(chat_id, process_details_vcf)


# ── Main-menu keyboard builder ────────────────────────────────────────────────

def get_main_menu_keyboard(lang, user_id):
    from telebot import types
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)

    kb.row(
        types.KeyboardButton(TEXTS[lang]['b_txt2vcf'], icon_custom_emoji_id="5433653135799228968"),
        types.KeyboardButton(TEXTS[lang]['b_vcf2txt'], icon_custom_emoji_id="5431736674147114227")
    )
    kb.row(
        types.KeyboardButton(TEXTS[lang]['b_navy'], icon_custom_emoji_id="6266995104687330978"),
        types.KeyboardButton(TEXTS[lang]['b_editor'], icon_custom_emoji_id="5334673106202010226")
    )
    kb.row(
        types.KeyboardButton(TEXTS[lang]['b_merge'], icon_custom_emoji_id="5264727218734524899"),
        types.KeyboardButton(TEXTS[lang]['b_split'], icon_custom_emoji_id="5237808360882977239")
    )
    kb.row(
        types.KeyboardButton(TEXTS[lang]['b_rename'], icon_custom_emoji_id="4920442992474456685"),
        types.KeyboardButton(TEXTS[lang]['b_details'], icon_custom_emoji_id="5893382531037794941")
    )
    return kb


# ════════════════════════════════════════════════════════════════════════════════
#  COMMAND HANDLERS  (telebot always prefers these over func=True handlers)
# ════════════════════════════════════════════════════════════════════════════════

@bot.message_handler(commands=['help'])
def help_command(message):
    bot.send_message(
        message.chat.id,
        "⁉️ **How To Use? / 使用说明**\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "• Pick a module from the menu below.\n"
        "• Follow the step-by-step prompts.\n"
        "• `/cancel` — abort the current process anytime.\n"
        "• `/done` — finish collecting/merging files when prompted.\n"
        "• `/skip` — skip an optional step.\n"
        "• `/caption` — toggle file captions ON/OFF.\n"
        "• `/ping` — check bot status & latency.\n"
        "• `/stats` — view bot statistics.",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=['caption'])
def caption_toggle(message):
    chat_id = message.chat.id
    current = user_captions.get(chat_id, True)
    user_captions[chat_id] = not current
    status_str = "🟢 **ON**" if user_captions[chat_id] else "🔴 **OFF**"
    bot.send_message(
        chat_id,
        f"⚠️ **Caption Configuration:** File block captions are now {status_str}.",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=['ping'])
def ping_speed_test(message):
    start_time = time.time()
    msg = bot.send_message(message.chat.id, "⚡ <i>Checking Bot Response Velocity...</i>", parse_mode="HTML")
    end_time   = time.time()
    ping_ms    = round((end_time - start_time) * 1000)
    response_text = (
        "<tg-emoji emoji-id=\"6161188739969194553\">📍</tg-emoji> 🏓 <b>PONG! SYSTEM STATUS</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "📡 <b>Latency:</b> {} ms\n"
        "⚡ <b>Speed:</b> 🚀 Good\n"
        "❓ <b>Status:</b> Online\n"
        "🔒 <b>Server:</b> Operational\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "👤 <i>Owner:</i> @MR_MUKUL4"
    ).format(ping_ms)
    bot.edit_message_text(response_text, message.chat.id, msg.message_id, parse_mode="HTML")


@bot.message_handler(commands=['stats'])
def bot_statistics_check(message):
    # Strictly restricted to ADMIN_ID
    if message.from_user.id != ADMIN_ID:
        bot.send_message(
            message.chat.id,
            "⚠️ This information is restricted to the Bot Administrator only."
        )
        return
    total = 0
    if os.path.exists("users.txt"):
        with FILE_LOCK:
            with open("users.txt", "r") as f:
                total = len([l for l in f.read().splitlines() if l.strip()])
    bot.send_message(
        message.chat.id,
        f"<tg-emoji emoji-id=\"5800812959173187710\">👑</tg-emoji> <b>Bot Operational Statistics:</b>\n\n"
        f"👥 Registered Users: <code>{total}</code>\n"
        f"👑 Channels status: <code>Operational</code>🛡️\n"
        f"<tg-emoji emoji-id=\"6109340839664686978\">🌟</tg-emoji> ⚙️ Engine Build: <code>v4.5 Live Framework</code>",
        parse_mode="HTML"
    )


@bot.message_handler(commands=['broadcast'])
def broadcast_message(message):
    # Strictly restricted to ADMIN_ID — silently ignore everyone else
    if message.from_user.id != ADMIN_ID:
        return
    parts = message.text.split(None, 1)
    if len(parts) < 2 or not parts[1].strip():
        bot.send_message(message.chat.id, "❌ Please type your message after the command.")
        return
    broadcast_text = parts[1].strip()
    if not os.path.exists("users.txt"):
        bot.send_message(message.chat.id, "❌ No registered users found (users.txt missing).")
        return
    with FILE_LOCK:
        with open("users.txt", "r") as f:
            user_ids = [l.strip() for l in f.read().splitlines() if l.strip()]
    sent_count   = 0
    failed_count = 0
    for uid in user_ids:
        try:
            bot.send_message(int(uid), broadcast_text)
            sent_count += 1
        except Exception:
            failed_count += 1
    bot.send_message(
        message.chat.id,
        f"✅ Broadcast completed!\n\n📤 Sent: `{sent_count}`\n❌ Failed/Blocked: `{failed_count}`",
        parse_mode="Markdown"
    )


@bot.message_handler(commands=['start', 'language'])
def send_welcome(message):
    save_user(message.from_user.id)
    markup = ReplyKeyboardMarkup(row_width=3, resize_keyboard=True)
    markup.add(
        KeyboardButton("🇬🇧 English"),
        KeyboardButton("🇮🇳 हिन्दी"),
        KeyboardButton("🇨🇳 简体中文")
    )
    bot.send_message(
        message.chat.id,
        "🌐 **Select Language / भाषा चुनें / 选择语言:**",
        reply_markup=markup,
        parse_mode="Markdown"
    )


@bot.message_handler(commands=['cancel'])
def cancel_command(message):
    chat_id = message.chat.id
    lang    = user_langs.get(chat_id, 'en')
    bot.clear_step_handler_by_chat_id(chat_id=chat_id)
    with DATA_LOCK:
        user_data.pop(chat_id, None)
        merge_storage.pop(chat_id, None)
    bot.send_message(
        chat_id,
        TEXTS[lang]['cancelled'],
        reply_markup=get_main_menu_keyboard(lang, chat_id)
    )


# ════════════════════════════════════════════════════════════════════════════════
#  CATCH-ALL TEXT HANDLER
#
#  KEY FIX: This handler now calls check_menu_or_commands() and STOPS.
#  It does NOT independently call _dispatch_menu_button.
#  This means every message that passes through here goes through a single,
#  locked gate that serialises state-clearing before routing — no race.
#
#  If the message is NOT a menu button or global command,
#  check_menu_or_commands returns False and we return silently.
#  That means random text messages while no step-handler is active are
#  simply ignored (no error spam, no loop), which is correct behavior.
# ════════════════════════════════════════════════════════════════════════════════

@bot.message_handler(func=lambda message: True)
def handle_menu_and_languages(message):
    """Catch-all for menu buttons and language selectors.

    ARCHITECTURE NOTE:
    ──────────────────
    This handler is intentionally thin.  It delegates entirely to
    check_menu_or_commands, which handles both routing AND state-clearing
    atomically.  We never call _dispatch_menu_button directly from here —
    that would create a second routing path and re-introduce the re-entrancy
    bug.

    Messages that are not recognised as menu buttons are silently dropped.
    This is safe because any legitimate step-input arrives through a
    register_next_step_handler_by_chat_id chain that bypasses this handler
    entirely (telebot's step-handler queue runs before the @message_handler
    func=True matcher).
    """
    if not message.text:
        return
    save_user(message.from_user.id)
    # check_menu_or_commands handles EVERYTHING: state-clear + routing.
    # If it returns False the message was not a menu button — just drop it.
    check_menu_or_commands(message)


# ════════════════════════════════════════════════════════════════���═══════════════
#  CALLBACK QUERY HANDLER
# ════════════════════════════════════════════════════════════════════════════════

@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    chat_id = call.message.chat.id
    lang    = user_langs.get(chat_id, 'en')

    try:
        bot.answer_callback_query(call.id)
    except Exception:
        pass

    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception:
        pass

    # Clear any pending step-handler to prevent double-fires
    bot.clear_step_handler_by_chat_id(chat_id=chat_id)

    if call.data == "action_done":
        with DATA_LOCK:
            numbers = list(user_data.get(chat_id, {}).get('numbers', []))
        if not numbers:
            bot.send_message(chat_id, TEXTS[lang]['invalid_file'], parse_mode="HTML")
            bot.register_next_step_handler_by_chat_id(chat_id, process_inputs)
            return
        bot.send_message(chat_id, "1️⃣ **Enter Final VCF file Name:**", parse_mode="Markdown")
        bot.register_next_step_handler_by_chat_id(chat_id, get_file_name)

    elif call.data in ("skip_admin", "done_admin"):
        with DATA_LOCK:
            if chat_id not in user_data:
                bot.send_message(chat_id, "❌ Session expired. Please start again.")
                return
            if call.data == "skip_admin":
                user_data[chat_id]['admin_numbers'] = []
        trigger_navy_step_2(chat_id)

    elif call.data in ("skip_navy", "done_navy"):
        with DATA_LOCK:
            if chat_id not in user_data:
                bot.send_message(chat_id, "❌ Session expired. Please start again.")
                return
            if call.data == "skip_navy":
                user_data[chat_id]['navy_numbers'] = []
        proceed_after_collection(chat_id)


# ════════════════════════════════════════════════════════════════════════════════
#  TEXT/EXCEL → VCF NORMAL MODE FLOW
#  Steps: process_inputs → get_file_name → get_prefix → get_company
#         → get_start_number → generate_vcf_router
# ════════════════════════════════════════════════════════════════════════════════

def process_inputs(message):
    if check_menu_or_commands(message):
        return
    chat_id = message.chat.id
    with DATA_LOCK:
        if chat_id not in user_data:
            return
        current_list = list(user_data[chat_id]['numbers'])

    new_entries = []

    if message.document:
        fname = (message.document.file_name or "").lower()
        try:
            file_info       = bot.get_file(message.document.file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            if fname.endswith('.txt'):
                lines       = downloaded_file.decode('utf-8', errors='ignore').splitlines()
                new_entries = [
                    re.sub(r'[^\d\+]', '', l)
                    for l in lines
                    if len(re.sub(r'[^\d\+]', '', l)) >= 7
                ]
            elif fname.endswith('.xlsx'):
                wb = openpyxl.load_workbook(io.BytesIO(downloaded_file), data_only=True)
                for sheet in wb.worksheets:
                    for row in sheet.iter_rows(values_only=True):
                        for cell in row:
                            val     = str(cell).strip() if cell is not None else ''
                            cleaned = re.sub(r'[^\d\+]', '', val)
                            if len(cleaned) >= 7:
                                new_entries.append(cleaned)
            else:
                bot.send_message(chat_id, "❌ Unsupported file type. Please send a `.txt` or `.xlsx` file.")
                bot.register_next_step_handler_by_chat_id(chat_id, process_inputs)
                return
        except Exception as e:
            logging.error(f"process_inputs file error: {e}")
            bot.send_message(chat_id, f"❌ Could not read file: {e}")
            bot.register_next_step_handler_by_chat_id(chat_id, process_inputs)
            return
    elif message.text:
        found       = re.findall(r'\+?\d[\d\-\s]{5,14}\d', message.text)
        new_entries = [
            re.sub(r'[^\d\+]', '', n)
            for n in found
            if len(re.sub(r'[^\d\+]', '', n)) >= 7
        ]
    else:
        bot.register_next_step_handler_by_chat_id(chat_id, process_inputs)
        return

    with DATA_LOCK:
        if chat_id not in user_data:
            return
        merged                      = list(dict.fromkeys(current_list + new_entries))
        user_data[chat_id]['numbers'] = merged
        count                       = len(merged)

    markup = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅ Done", callback_data="action_done")
    )
    bot.send_message(
        chat_id,
        f"📥 Collected: `{count}` numbers. Send more or click Done:",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    bot.register_next_step_handler_by_chat_id(chat_id, process_inputs)


def get_file_name(message):
    if check_menu_or_commands(message):
        return
    if not message.text:
        bot.send_message(message.chat.id, "❌ Please type the file name.")
        bot.register_next_step_handler_by_chat_id(message.chat.id, get_file_name)
        return
    with DATA_LOCK:
        if message.chat.id in user_data:
            user_data[message.chat.id]['filename'] = message.text.strip() or "Contacts"
    bot.send_message(message.chat.id, "2️⃣ **Enter Contact Prefix Name:**", parse_mode="Markdown")
    bot.register_next_step_handler_by_chat_id(message.chat.id, get_prefix)


def get_prefix(message):
    if check_menu_or_commands(message):
        return
    if not message.text:
        bot.send_message(message.chat.id, "❌ Please type the prefix name.")
        bot.register_next_step_handler_by_chat_id(message.chat.id, get_prefix)
        return
    with DATA_LOCK:
        if message.chat.id in user_data:
            user_data[message.chat.id]['prefix'] = message.text.strip() or "Contact"
    bot.send_message(
        message.chat.id,
        "3️⃣ **Enter Company Name (or type 'skip'):**",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler_by_chat_id(message.chat.id, get_company)


def get_company(message):
    if check_menu_or_commands(message):
        return
    if not message.text:
        bot.send_message(message.chat.id, "❌ Please type the company name or 'skip'.")
        bot.register_next_step_handler_by_chat_id(message.chat.id, get_company)
        return
    company_val = (
        message.text.strip()
        if message.text.strip().lower() not in ['skip', '/skip']
        else ""
    )
    with DATA_LOCK:
        if message.chat.id in user_data:
            user_data[message.chat.id]['company'] = company_val
    bot.send_message(
        message.chat.id,
        "4️⃣ **VCF File Starting Number?**\n*(Example: 1)*",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler_by_chat_id(message.chat.id, get_start_number)


def get_start_number(message):
    """Step 4 of Normal Mode: capture the file-index starting number.
    Validates as a positive integer, defaults to 1 on bad/empty input.
    Then asks for the split count (Step 5) and hands off to generate_vcf_router."""
    if check_menu_or_commands(message):
        return
    chat_id   = message.chat.id
    start_num = 1  # safe default
    if message.text and message.text.strip():
        try:
            parsed = int(message.text.strip())
            if parsed > 0:
                start_num = parsed
        except (ValueError, TypeError):
            pass  # default remains 1
    with DATA_LOCK:
        if chat_id in user_data:
            user_data[chat_id]['file_start_idx'] = start_num
    bot.send_message(
        chat_id,
        "5️⃣ **How many contacts per VCF file?**\n*(Safe Margin: 200 - 250)*:",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler_by_chat_id(chat_id, generate_vcf_router)


def generate_vcf_router(message):
    """Step 5 (final) of Normal Mode: read split count and generate files."""
    if check_menu_or_commands(message):
        return
    chat_id = message.chat.id

    if not message.text:
        bot.send_message(chat_id, "❌ Please enter a valid number.")
        bot.register_next_step_handler_by_chat_id(chat_id, generate_vcf_router)
        return

    try:
        split_count = int(message.text.strip())
        if split_count <= 0:
            raise ValueError("Must be positive")
    except (ValueError, TypeError):
        bot.send_message(chat_id, "❌ Please enter a valid positive number.")
        bot.register_next_step_handler_by_chat_id(chat_id, generate_vcf_router)
        return

    with DATA_LOCK:
        data = user_data.get(chat_id)
        if not data:
            bot.send_message(chat_id, "❌ Session expired. Please start again from the menu.")
            return
        data_snapshot = copy.deepcopy(data)
        user_data.pop(chat_id, None)

    bot.send_message(chat_id, "<tg-emoji emoji-id=\"5375338737028841420\">🔄</tg-emoji> 🚀 <b>Executing System Generation Block...</b>", parse_mode="HTML")

    # File index starts from the user-supplied value (Step 4); contact index always starts at 1
    file_idx    = data_snapshot.get('file_start_idx', 1)
    contact_idx = 1
    filename    = data_snapshot.get('filename', 'Output')
    company     = data_snapshot.get('company', '')

    try:
        numbers = data_snapshot.get('numbers', [])
        if not numbers:
            bot.send_message(chat_id, "❌ No contacts collected. Please start again.")
            return
        random.shuffle(numbers)
        prefix = data_snapshot.get('prefix', 'Contact')
        chunks = [numbers[i:i + split_count] for i in range(0, len(numbers), split_count)]

        for chunk in chunks:
            vcf_content = ""
            for num in chunk:
                c_name       = f"{prefix} {contact_idx}"
                vcf_content += f"BEGIN:VCARD\nVERSION:3.0\nFN:{c_name}\nN:;{c_name};;;\n"
                if company:
                    vcf_content += f"ORG:{company}\n"
                vcf_content += f"TEL;TYPE=CELL:{num}\nEND:VCARD\n"
                contact_idx += 1

            vcf_file_path = f"tmp_{chat_id}_{uuid.uuid4().hex}.vcf"
            try:
                with open(vcf_file_path, "w", encoding="utf-8") as f:
                    f.write(vcf_content)
                with open(vcf_file_path, "rb") as f:
                    bot.send_document(
                        chat_id, f,
                        caption=None,
                        visible_file_name=f"{filename}_{file_idx}.vcf"
                    )
            finally:
                safe_delete_file(vcf_file_path)
            file_idx += 1

    except Exception as e:
        logging.error(f"generate_vcf_router error: {e}")
        bot.send_message(chat_id, f"❌ Generation failed: {e}")
        return

    lang = user_langs.get(chat_id, 'en')
    bot.send_message(
        chat_id,
        TEXTS[lang]['success'],
        reply_markup=get_main_menu_keyboard(lang, chat_id),
        parse_mode="HTML"
    )


# ════════════════════════════════════════════════════════════════════════════════
#  ADMIN / NAVY DUAL-MODE FLOW
#  Steps: process_navy_admin_inputs → trigger_navy_step_2
#         → process_navy_numbers_inputs → proceed_after_collection
#         → [ask_admin_prefix] → [ask_navy_prefix] → ask_navy_filename
#         → generate_navy_vcf (internal split limit = DEFAULT_SPLIT_LIMIT)
# ════════════════════════════════════════════════════════════════════════════════

def process_navy_admin_inputs(message):
    if check_menu_or_commands(message):
        return
    chat_id = message.chat.id
    with DATA_LOCK:
        data = user_data.get(chat_id)
    if not data:
        return

    extracted = []
    if message.document:
        fname = (message.document.file_name or "").lower()
        if fname.endswith('.txt'):
            try:
                file_info = bot.get_file(message.document.file_id)
                lines     = bot.download_file(file_info.file_path).decode('utf-8', errors='ignore').splitlines()
                extracted = [
                    re.sub(r'[^\d\+]', '', l)
                    for l in lines
                    if len(re.sub(r'[^\d\+]', '', l)) >= 7
                ]
            except Exception as e:
                logging.error(f"process_navy_admin_inputs file error: {e}")
        else:
            bot.send_message(chat_id, "❌ Please send a `.txt` file for admin numbers.")
            bot.register_next_step_handler_by_chat_id(chat_id, process_navy_admin_inputs)
            return
    elif message.text:
        if message.text.strip().lower() in ('/skip', '⏭️ skip admin'):
            with DATA_LOCK:
                if chat_id in user_data:
                    user_data[chat_id]['admin_numbers'] = []
            trigger_navy_step_2(chat_id)
            return
        found     = re.findall(r'\+?\d[\d\-\s]{5,14}\d', message.text)
        extracted = [
            re.sub(r'[^\d\+]', '', n)
            for n in found
            if len(re.sub(r'[^\d\+]', '', n)) >= 7
        ]
    else:
        bot.register_next_step_handler_by_chat_id(chat_id, process_navy_admin_inputs)
        return

    with DATA_LOCK:
        if chat_id in user_data:
            user_data[chat_id]['admin_numbers'].extend(extracted)
            user_data[chat_id]['admin_numbers'] = list(dict.fromkeys(user_data[chat_id]['admin_numbers']))
            count = len(user_data[chat_id]['admin_numbers'])
        else:
            return

    markup = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅ Done Admin", callback_data="done_admin")
    )
    bot.send_message(
        chat_id,
        f"<tg-emoji emoji-id=\"6026218958900695642\">💎</tg-emoji> <b>Final Admin:</b> {count}\n✨ Saved!\n\nSend more or click Done Admin:",
        reply_markup=markup,
        parse_mode="HTML"
    )
    bot.register_next_step_handler_by_chat_id(chat_id, process_navy_admin_inputs)


def process_navy_numbers_inputs(message):
    if check_menu_or_commands(message):
        return
    chat_id = message.chat.id
    with DATA_LOCK:
        data = user_data.get(chat_id)
    if not data:
        return

    extracted = []
    if message.document:
        fname = (message.document.file_name or "").lower()
        if fname.endswith('.txt'):
            try:
                file_info = bot.get_file(message.document.file_id)
                lines     = bot.download_file(file_info.file_path).decode('utf-8', errors='ignore').splitlines()
                extracted = [
                    re.sub(r'[^\d\+]', '', l)
                    for l in lines
                    if len(re.sub(r'[^\d\+]', '', l)) >= 7
                ]
            except Exception as e:
                logging.error(f"process_navy_numbers_inputs file error: {e}")
        else:
            bot.send_message(chat_id, "❌ Please send a `.txt` file for navy numbers.")
            bot.register_next_step_handler_by_chat_id(chat_id, process_navy_numbers_inputs)
            return
    elif message.text:
        if message.text.strip().lower() in ('/skip', '⏭️ skip navy'):
            with DATA_LOCK:
                if chat_id in user_data:
                    user_data[chat_id]['navy_numbers'] = []
            proceed_after_collection(chat_id)
            return
        found     = re.findall(r'\+?\d[\d\-\s]{5,14}\d', message.text)
        extracted = [
            re.sub(r'[^\d\+]', '', n)
            for n in found
            if len(re.sub(r'[^\d\+]', '', n)) >= 7
        ]
    else:
        bot.register_next_step_handler_by_chat_id(chat_id, process_navy_numbers_inputs)
        return

    with DATA_LOCK:
        if chat_id in user_data:
            user_data[chat_id]['navy_numbers'].extend(extracted)
            user_data[chat_id]['navy_numbers'] = list(dict.fromkeys(user_data[chat_id]['navy_numbers']))
            count = len(user_data[chat_id]['navy_numbers'])
        else:
            return

    markup = InlineKeyboardMarkup().add(
        InlineKeyboardButton("✅ Done Navy", callback_data="done_navy")
    )
    bot.send_message(
        chat_id,
        f"<tg-emoji emoji-id=\"6026218958900695642\">💎</tg-emoji> <b>Final Navy:</b> {count}\n✨ Saved!\n\nSend more or click Done Navy:",
        reply_markup=markup,
        parse_mode="HTML"
    )
    bot.register_next_step_handler_by_chat_id(chat_id, process_navy_numbers_inputs)


def trigger_navy_step_2(chat_id):
    markup = InlineKeyboardMarkup().add(
        InlineKeyboardButton("⏭️ Skip Navy", callback_data="skip_navy")
    )
    bot.send_message(
        chat_id,
        "⚙️ **Step 2 • Navy Contacts**\n\n📂 Send Navy numbers or files.",
        reply_markup=markup,
        parse_mode="Markdown"
    )
    bot.register_next_step_handler_by_chat_id(chat_id, process_navy_numbers_inputs)


def proceed_after_collection(chat_id):
    """Called once both Admin and Navy collection (or skips) are finished.
    Only prompts for a prefix when the respective category actually has numbers.
    Never asks for split count — DEFAULT_SPLIT_LIMIT is applied internally."""
    with DATA_LOCK:
        data      = user_data.get(chat_id, {})
        has_admin = bool(data.get('admin_numbers'))
        if not has_admin:
            # No admin numbers — silently default prefix; no prompt
            if chat_id in user_data:
                user_data[chat_id]['admin_prefix'] = 'Admin'

    if has_admin:
        ask_admin_prefix(chat_id)
    else:
        proceed_after_admin_prefix(chat_id)


def ask_admin_prefix(chat_id):
    bot.send_message(
        chat_id,
        "<tg-emoji emoji-id=\"6026218958900695642\">💎</tg-emoji> <b>Step 3 • Admin Name Prefix</b>\n\nWhat should be the name for Admin contacts?\n<i>Example: Admin Target</i>",
        parse_mode="HTML"
    )
    bot.register_next_step_handler_by_chat_id(chat_id, get_admin_prefix)


def proceed_after_admin_prefix(chat_id):
    with DATA_LOCK:
        data      = user_data.get(chat_id, {})
        has_navy  = bool(data.get('navy_numbers'))
        if not has_navy:
            if chat_id in user_data:
                user_data[chat_id]['navy_prefix'] = 'Navy'

    if has_navy:
        ask_navy_prefix(chat_id)
    else:
        ask_navy_filename(chat_id)


def ask_navy_prefix(chat_id):
    bot.send_message(
        chat_id,
        "<tg-emoji emoji-id=\"6026218958900695642\">💎</tg-emoji> <b>Step 4 • Navy Name Prefix</b>\n\nEnter the name for Navy contacts.\n<i>Example: Navy Target</i>",
        parse_mode="HTML"
    )
    bot.register_next_step_handler_by_chat_id(chat_id, get_navy_prefix)


def ask_navy_filename(chat_id):
    bot.send_message(
        chat_id,
        "🔄 **Step 5 • Final VCF Filename**\n\nEnter the name for your generated VCF file.",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler_by_chat_id(chat_id, get_navy_filename)


def get_admin_prefix(message):
    if check_menu_or_commands(message):
        return
    chat_id = message.chat.id
    if not message.text:
        bot.send_message(chat_id, "❌ Please type the admin prefix name.")
        bot.register_next_step_handler_by_chat_id(chat_id, get_admin_prefix)
        return
    typed      = message.text.strip()
    prefix_val = "Admin" if typed.lower() in ('/skip', 'skip', '') else (typed or "Admin")
    with DATA_LOCK:
        if chat_id in user_data:
            user_data[chat_id]['admin_prefix'] = prefix_val
    proceed_after_admin_prefix(chat_id)


def get_navy_prefix(message):
    if check_menu_or_commands(message):
        return
    chat_id = message.chat.id
    if not message.text:
        bot.send_message(chat_id, "❌ Please type the navy prefix name.")
        bot.register_next_step_handler_by_chat_id(chat_id, get_navy_prefix)
        return
    typed      = message.text.strip()
    prefix_val = "Navy" if typed.lower() in ('/skip', 'skip', '') else (typed or "Navy")
    with DATA_LOCK:
        if chat_id in user_data:
            user_data[chat_id]['navy_prefix'] = prefix_val
    ask_navy_filename(chat_id)


def get_navy_filename(message):
    if check_menu_or_commands(message):
        return
    chat_id = message.chat.id
    if not message.text:
        bot.send_message(chat_id, "❌ Please type the file name.")
        bot.register_next_step_handler_by_chat_id(chat_id, get_navy_filename)
        return
    fname = message.text.strip() or "Target_File"
    with DATA_LOCK:
        if chat_id not in user_data:
            bot.send_message(chat_id, "❌ Session expired. Please start again.")
            return
        user_data[chat_id]['filename'] = fname
    bot.send_message(chat_id, "<tg-emoji emoji-id=\"5375338737028841420\">🔄</tg-emoji> 🚀 <b>Executing System Generation Block...</b>", parse_mode="HTML")
    generate_navy_vcf(chat_id, DEFAULT_SPLIT_LIMIT)


def generate_navy_vcf(chat_id, split_count=DEFAULT_SPLIT_LIMIT):
    """Generate Admin/Navy VCF files using an internal fixed split limit.
    No split-count prompt is ever shown to the user for this module."""
    with DATA_LOCK:
        data = user_data.get(chat_id)
        if not data:
            bot.send_message(chat_id, "❌ Session expired. Please start again from the menu.")
            return
        data_snapshot = copy.deepcopy(data)
        user_data.pop(chat_id, None)

    file_idx    = 1
    contact_idx = 1
    filename    = data_snapshot.get('filename', 'Output')

    try:
        combined_package = []
        for n in data_snapshot.get('admin_numbers', []):
            combined_package.append(('admin', n))
        for n in data_snapshot.get('navy_numbers', []):
            combined_package.append(('navy', n))

        if not combined_package:
            bot.send_message(chat_id, "❌ No contacts collected. Please start again.")
            return

        chunks   = [combined_package[i:i + split_count] for i in range(0, len(combined_package), split_count)]
        a_prefix = data_snapshot.get('admin_prefix', 'Admin')
        n_prefix = data_snapshot.get('navy_prefix', 'Navy')

        for chunk in chunks:
            vcf_content = ""
            for tag, num in chunk:
                prefix_to_use = a_prefix if tag == 'admin' else n_prefix
                c_name        = f"{prefix_to_use} {contact_idx}"
                vcf_content  += f"BEGIN:VCARD\nVERSION:3.0\nFN:{c_name}\nN:;{c_name};;;\n"
                if tag == 'admin':
                    vcf_content += "NOTE:Admin VIP Contact\n"
                vcf_content  += f"TEL;TYPE=CELL:{num}\nEND:VCARD\n"
                contact_idx  += 1

            vcf_file_path = f"tmp_{chat_id}_{uuid.uuid4().hex}.vcf"
            try:
                with open(vcf_file_path, "w", encoding="utf-8") as f:
                    f.write(vcf_content)
                with open(vcf_file_path, "rb") as f:
                    bot.send_document(
                        chat_id, f,
                        caption=None,
                        visible_file_name=f"{filename}_{file_idx}.vcf"
                    )
            finally:
                safe_delete_file(vcf_file_path)
            file_idx += 1

    except Exception as e:
        logging.error(f"generate_navy_vcf error: {e}")
        bot.send_message(chat_id, f"❌ Generation failed: {e}")
        return

    lang = user_langs.get(chat_id, 'en')
    bot.send_message(
        chat_id,
        TEXTS[lang]['success'],
        reply_markup=get_main_menu_keyboard(lang, chat_id),
        parse_mode="HTML"
    )


# ════════════════════════════════════════════════════════════════════════════════
#  VCF → TEXT MODULE
# ════════════════════════════════════════════════════════════════════════════════

def process_vcf_to_txt(message):
    if check_menu_or_commands(message):
        return
    if not message.document or not (message.document.file_name or "").lower().endswith('.vcf'):
        bot.send_message(message.chat.id, "❌ Please send a valid `.vcf` file.")
        bot.register_next_step_handler_by_chat_id(message.chat.id, process_vcf_to_txt)
        return

    txt_path = f"tmp_{message.chat.id}_{uuid.uuid4().hex}.txt"
    try:
        file_info   = bot.get_file(message.document.file_id)
        vcf_data    = bot.download_file(file_info.file_path).decode('utf-8', errors='ignore')
        raw_numbers = re.findall(r'TEL[^\:]*\:([^\n\r]+)', vcf_data)
        cleaned     = list(dict.fromkeys([
            re.sub(r'[^\d\+]', '', n) for n in raw_numbers if n
        ]))
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(cleaned))
        with open(txt_path, "rb") as f:
            bot.send_document(
                message.chat.id, f,
                caption=None,
                visible_file_name=f"Extracted_{message.chat.id}.txt"
            )
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error: {e}")
    finally:
        safe_delete_file(txt_path)


# ════════════════════════════════════════════════════════════════════════════════
#  SPLIT FILE MODULE
# ════════════════════════════════════════════════════════════════════════════════

def process_split_vcf(message):
    if check_menu_or_commands(message):
        return
    if not message.document:
        bot.send_message(message.chat.id, "❌ Please send a `.vcf` or `.txt` file.")
        bot.register_next_step_handler_by_chat_id(message.chat.id, process_split_vcf)
        return

    fname = (message.document.file_name or "").lower()
    if not (fname.endswith('.vcf') or fname.endswith('.txt')):
        bot.send_message(message.chat.id, "❌ Please send a `.vcf` or `.txt` file.")
        bot.register_next_step_handler_by_chat_id(message.chat.id, process_split_vcf)
        return

    try:
        file_info = bot.get_file(message.document.file_id)
        vcf_data  = bot.download_file(file_info.file_path).decode('utf-8', errors='ignore')
        cards     = re.findall(r'BEGIN:VCARD.*?END:VCARD', vcf_data, re.DOTALL)

        if not cards:
            bot.send_message(message.chat.id, "❌ No valid vCard entries found in the file. Please check and try again.")
            with DATA_LOCK:
                user_data.pop(message.chat.id, None)
            return

        with DATA_LOCK:
            user_data[message.chat.id] = {
                'split_cards':     cards,
                'split_file_name': message.document.file_name or "file.vcf",
                'total_contacts':  len(cards)
            }

        safe_name   = escape_markdown(message.document.file_name or "file.vcf")
        response_ui = (
            "🎉 *File Loaded! (.VCF)*\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "📂 *File:* {}\n"
            "👥 *Total Contacts:* {}\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "🔢 *How many contacts do you want per file?* (e.g., 50, 100)"
        ).format(safe_name, len(cards))

        bot.send_message(message.chat.id, response_ui, parse_mode="Markdown")
        bot.register_next_step_handler_by_chat_id(message.chat.id, ask_split_new_name_step)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Load Error: {e}")


def ask_split_new_name_step(message):
    if check_menu_or_commands(message):
        return
    chat_id = message.chat.id
    if not message.text:
        bot.send_message(chat_id, "❌ Please enter a valid number.")
        bot.register_next_step_handler_by_chat_id(chat_id, ask_split_new_name_step)
        return
    try:
        limit = int(message.text.strip())
        if limit <= 0:
            raise ValueError
        with DATA_LOCK:
            if chat_id in user_data:
                user_data[chat_id]['split_limit'] = limit
    except (ValueError, TypeError):
        bot.send_message(chat_id, "❌ Enter a valid positive number.")
        bot.register_next_step_handler_by_chat_id(chat_id, ask_split_new_name_step)
        return

    with DATA_LOCK:
        base_name = user_data.get(chat_id, {}).get('split_file_name', 'file.vcf').rsplit('.', 1)[0]
    safe_base = escape_markdown(base_name)
    bot.send_message(
        chat_id,
        f"🔄 *New File Name?*\n\nType a new name OR send anything to keep old name: `{safe_base}`",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler_by_chat_id(chat_id, execute_split_vcf)


def execute_split_vcf(message):
    if check_menu_or_commands(message):
        return
    chat_id = message.chat.id
    with DATA_LOCK:
        data = user_data.get(chat_id)
    if not data:
        bot.send_message(chat_id, "❌ Session expired. Please start again.")
        return

    typed_text     = message.text.strip() if message.text else ""
    old_base       = data['split_file_name'].rsplit('.', 1)[0]
    final_base_name = typed_text if typed_text else old_base

    bot.send_message(chat_id, "<tg-emoji emoji-id=\"5375338737028841420\">🔄</tg-emoji> 🎬 <i>Splitting File...</i>\n📶 <i>Status: Processing...</i>", parse_mode="HTML")

    cards  = data['split_cards']
    limit  = data['split_limit']
    chunks = [cards[i:i + limit] for i in range(0, len(cards), limit)]

    try:
        for idx, chunk in enumerate(chunks, start=1):
            chunk_path = f"tmp_{chat_id}_{uuid.uuid4().hex}.vcf"
            try:
                with open(chunk_path, 'w', encoding='utf-8') as f:
                    f.write("\n".join(chunk))
                with open(chunk_path, 'rb') as f:
                    bot.send_document(
                        chat_id, f,
                        caption=None,
                        visible_file_name=f"{final_base_name}_Part_{idx}.vcf"
                    )
            finally:
                safe_delete_file(chunk_path)
        bot.send_message(chat_id, "🎉 **File Splitting Completed!** 🥳", parse_mode="Markdown")
    except Exception as e:
        bot.send_message(chat_id, f"❌ Failed: {e}")
    finally:
        with DATA_LOCK:
            user_data.pop(chat_id, None)


# ════════════════════════════════════════════════════════════════════════════════
#  MERGE FILE MODULE
# ════════════════════════════════════════════════════════════════════════════════

def process_merge_vcf(message):
    if check_menu_or_commands(message):
        return
    chat_id = message.chat.id

    if message.text and message.text.strip() in ["/done", "✅ Done"]:
        with DATA_LOCK:
            files = list(merge_storage.get(chat_id, []))
        if not files:
            bot.send_message(chat_id, "❌ Queue Empty. Please send at least 1 file.")
            bot.register_next_step_handler_by_chat_id(chat_id, process_merge_vcf)
            return
        bot.send_message(
            chat_id,
            "<tg-emoji emoji-id=\"5375338737028841420\">🔄</tg-emoji> 🎬 <i>Merging Files</i>\n\n📁 Total Files: {}\n📶 <i>Status: Processing...</i>".format(len(files)),
            parse_mode="HTML"
        )
        bot.send_message(
            chat_id,
            "📝 **Send Merged File Name:**\n(Example: All_Contacts_Merged)\n*Note: .vcf extension added automatically.*",
            parse_mode="Markdown"
        )
        bot.register_next_step_handler_by_chat_id(chat_id, execute_merge_vcf)
        return

    if message.document:
        fname = (message.document.file_name or "").lower()
        if fname.endswith('.vcf') or fname.endswith('.txt'):
            with DATA_LOCK:
                if chat_id not in merge_storage:
                    merge_storage[chat_id] = []
                merge_storage[chat_id].append(message.document.file_id)
                count = len(merge_storage[chat_id])
            bot.send_message(
                chat_id,
                f"✅ File added! Queue: {count}\nSend next or type /done."
            )
            bot.register_next_step_handler_by_chat_id(chat_id, process_merge_vcf)
        else:
            bot.send_message(chat_id, "❌ Please send a `.vcf` or `.txt` file, or type `/done`.")
            bot.register_next_step_handler_by_chat_id(chat_id, process_merge_vcf)
    else:
        bot.send_message(chat_id, "❌ Please send a `.vcf` or `.txt` file, or type `/done`.")
        bot.register_next_step_handler_by_chat_id(chat_id, process_merge_vcf)


def execute_merge_vcf(message):
    if check_menu_or_commands(message):
        return
    chat_id = message.chat.id
    with DATA_LOCK:
        files = list(merge_storage.get(chat_id, []))
    if not files:
        bot.send_message(chat_id, "❌ No files in queue. Please start again.")
        return

    target_name = ((message.text.strip() if message.text else "") or "Merged_Output") + ".vcf"
    disk_path   = f"tmp_{chat_id}_{uuid.uuid4().hex}.vcf"
    try:
        merged_content = ""
        for f_id in files:
            f_info          = bot.get_file(f_id)
            merged_content += (
                bot.download_file(f_info.file_path).decode('utf-8', errors='ignore').strip() + "\n"
            )
        with open(disk_path, 'w', encoding='utf-8') as f:
            f.write(merged_content)
        with open(disk_path, 'rb') as f:
            bot.send_document(
                chat_id, f,
                caption=None,
                visible_file_name=target_name
            )
    except Exception as e:
        bot.send_message(chat_id, f"❌ Error: {e}")
    finally:
        safe_delete_file(disk_path)
        with DATA_LOCK:
            merge_storage.pop(chat_id, None)


# ════════════════════════════════════════════════════════════════════════════════
#  VCF EDITOR MODULE
# ════════════════════════════════════════════════════════════════════════════════

def process_editor_vcf(message):
    if check_menu_or_commands(message):
        return
    if not message.document or not (message.document.file_name or "").lower().endswith('.vcf'):
        bot.send_message(message.chat.id, "❌ Please send a valid `.vcf` file.")
        bot.register_next_step_handler_by_chat_id(message.chat.id, process_editor_vcf)
        return
    with DATA_LOCK:
        user_data[message.chat.id] = {
            'edit_file_id':   message.document.file_id,
            'edit_file_name': message.document.file_name
        }
    lang = user_langs.get(message.chat.id, 'en')
    bot.send_message(message.chat.id, TEXTS[lang]['ask_new_prefix'])
    bot.register_next_step_handler_by_chat_id(message.chat.id, execute_editor_vcf)


def execute_editor_vcf(message):
    if check_menu_or_commands(message):
        return
    chat_id = message.chat.id
    if not message.text:
        bot.send_message(chat_id, "❌ Please type the new prefix name.")
        bot.register_next_step_handler_by_chat_id(chat_id, execute_editor_vcf)
        return

    new_prefix  = message.text.strip() or "Contact"
    with DATA_LOCK:
        data = user_data.get(chat_id)
    if not data:
        bot.send_message(chat_id, "❌ Session expired. Please start again.")
        return

    edited_path = f"tmp_{chat_id}_{uuid.uuid4().hex}.vcf"
    try:
        file_info = bot.get_file(data['edit_file_id'])
        vcf_data  = bot.download_file(file_info.file_path).decode('utf-8', errors='ignore')
        cards     = re.findall(r'BEGIN:VCARD.*?END:VCARD', vcf_data, re.DOTALL)
        edited    = ""
        for idx, card in enumerate(cards, start=1):
            card    = re.sub(r'FN:[^\n\r]*',  f'FN:{new_prefix} {idx}',      card)
            card    = re.sub(r'N:[^\n\r]*',   f'N:;{new_prefix} {idx};;;',   card)
            edited += card + "\n"
        with open(edited_path, 'w', encoding='utf-8') as f:
            f.write(edited)
        with open(edited_path, 'rb') as f:
            bot.send_document(
                chat_id, f,
                caption=None,
                visible_file_name=f"Edited_{data['edit_file_name']}"
            )
    except Exception as e:
        bot.send_message(chat_id, f"❌ Failed: {e}")
    finally:
        safe_delete_file(edited_path)
        with DATA_LOCK:
            user_data.pop(chat_id, None)


# ══════════════════════════════════════════════════════════��═════════════════════
#  RENAME FILE MODULE
# ═══════════════════════════════════════════���════════════════════════════════════

def process_rename_vcf(message):
    if check_menu_or_commands(message):
        return
    if not message.document or not message.document.file_name:
        bot.send_message(message.chat.id, "❌ Please upload ANY file to change its name.")
        bot.register_next_step_handler_by_chat_id(message.chat.id, process_rename_vcf)
        return
    with DATA_LOCK:
        user_data[message.chat.id] = {
            'rename_file_id': message.document.file_id,
            'orig_ext':       message.document.file_name.rsplit('.', 1)[-1]
        }
    bot.send_message(message.chat.id, "📝 **Enter Target Name:**", parse_mode="Markdown")
    bot.register_next_step_handler_by_chat_id(message.chat.id, execute_rename_vcf)


def execute_rename_vcf(message):
    if check_menu_or_commands(message):
        return
    chat_id = message.chat.id
    with DATA_LOCK:
        data = user_data.get(chat_id)
    if not data:
        return

    if not message.text:
        bot.send_message(chat_id, "❌ Please type the new file name.")
        bot.register_next_step_handler_by_chat_id(chat_id, execute_rename_vcf)
        return

    new_name  = (message.text.strip() or "Renamed_File") + f".{data['orig_ext']}"
    disk_path = f"tmp_{chat_id}_{uuid.uuid4().hex}.{data['orig_ext']}"
    try:
        file_info = bot.get_file(data['rename_file_id'])
        with open(disk_path, 'wb') as f:
            f.write(bot.download_file(file_info.file_path))
        with open(disk_path, 'rb') as f:
            bot.send_document(
                chat_id, f,
                caption=None,
                visible_file_name=new_name
            )
    except Exception as e:
        bot.send_message(chat_id, f"❌ Failed: {e}")
    finally:
        safe_delete_file(disk_path)
        with DATA_LOCK:
            user_data.pop(chat_id, None)


# ════════════════════════════════════════════════════════════════════════════════
#  VCF DETAILS SCANNER MODULE
# ════════════════════════════════════════════════════════════════════════════════

def process_details_vcf(message):
    if check_menu_or_commands(message):
        return
    if not message.document or not (message.document.file_name or "").lower().endswith('.vcf'):
        bot.send_message(message.chat.id, "❌ Please send a valid `.vcf` file.")
        bot.register_next_step_handler_by_chat_id(message.chat.id, process_details_vcf)
        return

    chat_id = message.chat.id
    bot.send_message(
        chat_id,
        "<tg-emoji emoji-id=\"5375338737028841420\">🔄</tg-emoji> 📶 <i>Analyzing {}... Please wait!</i>".format(escape_markdown(message.document.file_name or "")),
        parse_mode="HTML"
    )

    disk_path = f"tmp_{chat_id}_{uuid.uuid4().hex}.txt"
    try:
        file_info = bot.get_file(message.document.file_id)
        vcf_data  = bot.download_file(file_info.file_path).decode('utf-8', errors='ignore')

        vcards         = re.findall(r'BEGIN:VCARD.*?END:VCARD', vcf_data, re.DOTALL)
        total_contacts = len(vcards)

        extracted_list = []
        for idx, card in enumerate(vcards, start=1):
            fn_match  = re.search(r'FN:(.*)',        card)
            tel_match = re.search(r'TEL[^\:]*\:(.*)', card)
            name      = fn_match.group(1).strip()  if fn_match  else f"Contact {idx}"
            phone     = tel_match.group(1).strip() if tel_match else "No Number"
            phone     = re.sub(r'[^\d\+]', '', phone)
            extracted_list.append(f"{idx}. 👤 {name}\n   📞 {phone}")

        items_to_show = extracted_list[:50]
        total_pages   = max(1, -(-total_contacts // 50))
        shown_count   = len(items_to_show)

        start_name = end_name = "None"
        if vcards:
            m          = re.search(r'FN:(.*)', vcards[0])
            start_name = m.group(1).strip() if m else "None"
            m          = re.search(r'FN:(.*)', vcards[-1])
            end_name   = m.group(1).strip() if m else "None"

        safe_fname      = escape_markdown(message.document.file_name or "")
        safe_start_name = escape_markdown(start_name)
        safe_end_name   = escape_markdown(end_name)

        report_header = (
            "🔍 *VCF ANALYSIS • PAGE 1/{}*\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            "📂 *File:* {}\n"
            "👥 *Total:* {} Contacts\n"
            "📊 *Range:* Contact 1 to {}\n"
            "🏁 *Start:* {}\n"
            "🛑 *End:* {}\n"
            "━━━━━━━━━━━━━━━━━━━\n\n"
        ).format(total_pages, safe_fname, total_contacts, shown_count, safe_start_name, safe_end_name)

        escaped_items    = [escape_markdown(item) for item in items_to_show]
        full_report_msg  = report_header + "\n".join(escaped_items)

        if len(full_report_msg) > 4000:
            full_report_msg = full_report_msg[:3900] + "\n\n...[List truncated for chat view]..."

        bot.send_message(chat_id, full_report_msg, parse_mode="Markdown")

        txt_filename = (message.document.file_name or "details").rsplit('.', 1)[0] + "_details.txt"
        with open(disk_path, "w", encoding="utf-8") as f:
            f.write(
                f"Full Report for VCF: {message.document.file_name}\n"
                f"Total Contacts: {total_contacts}\n\n"
            )
            f.write("\n".join(extracted_list))

        with open(disk_path, "rb") as f:
            bot.send_document(
                chat_id, f,
                caption=None,
                visible_file_name=txt_filename
            )
    except Exception as e:
        bot.send_message(chat_id, f"❌ Analysis Error: {e}")
    finally:
        safe_delete_file(disk_path)
        with DATA_LOCK:
            user_data.pop(chat_id, None)


# ════════════════════════════════════════════════════════════════════════════════
#  BOT COMMANDS REGISTRATION & POLLING
# ════════════════════════════════════════════════════════════════════════════════

bot.set_my_commands([
    BotCommand("start",     "🚀 Restart Bot"),
    BotCommand("help",      "ℹ️ How To Use?"),
    BotCommand("done",      "✅ After Upload File"),
    BotCommand("skip",      "⏭️ Skip For Admin/Navy"),
    BotCommand("caption",   "⚠️ Caption ON/OFF"),
    BotCommand("cancel",    "🚫 Cancel Process"),
    BotCommand("ping",      "🏓 Ping & Bot Speed"),
    BotCommand("stats",     "📊 Bot Statistics"),
    BotCommand("broadcast", "📢 Broadcast Message (Admin Only)")
])

bot.infinity_polling(skip_pending=True)
