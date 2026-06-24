# ============================================================================
#  💎 VCF MANAGEMENT TOOL — Premium Telegram Bot (HTML + ANIMATED EMOJI)
#  Library : pyTelegramBotAPI (telebot)  +  Flask (keep-alive)
#  UI      : parse_mode="HTML" + <tg-emoji> Premium animated custom emojis
#  Core    : Thread-Safe (RLock/Lock) / Anti-Loop Gatekeeper / i18n / 24-7
# ----------------------------------------------------------------------------
#  pip install pyTelegramBotAPI flask openpyxl
# ============================================================================

import os
import re
import io
import time
import uuid
import random
import threading
import logging
from html import escape

import telebot
from telebot import types
from flask import Flask

# openpyxl is only needed for .xlsx parsing; import lazily-safe
try:
    import openpyxl
    OPENPYXL_OK = True
except Exception:
    OPENPYXL_OK = False

# ============================================================================
#  🔧 CONFIGURATION
# ============================================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN", "PUT-YOUR-BOT-TOKEN-HERE")
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "123456789").split(",") if x.strip()}

# Hidden global constant — Admin/Navy mode ALWAYS splits at exactly 200.
ADMIN_NAVY_SPLIT = 200

# Default split bounds for Normal mode (200–250 margin).
DEFAULT_SPLIT_MIN = 200
DEFAULT_SPLIT_MAX = 250

# Working directory for transient files (always cleaned up in finally blocks).
TMP_DIR = "tmp_vcf"
os.makedirs(TMP_DIR, exist_ok=True)

# Thread-locked user registry file (for /stats and /broadcast).
USERS_FILE = "users.txt"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("vcf-bot")

# GLOBAL HTML PARSE MODE — required for <tg-emoji> animated custom emojis.
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True)

# ============================================================================
#  ✨ PREMIUM ANIMATED CUSTOM EMOJI ENGINE
# ============================================================================
# Set to True since you have Telegram Premium and want live animated reactions!
USE_CUSTOM_EMOJI = bool(int(os.environ.get("USE_CUSTOM_EMOJI", "1")))

CUSTOM_EMOJI = {
    "diamond":   ("5377306342157541845", "💎"),
    "sparkle":   ("5377534935414753930", "✨"),
    "rocket":    ("5377498341074542641", "🚀"),
    "party":     ("5377434631194316169", "🎉"),
    "point":     ("5377331076020888459", "👇"),
    "warn":      ("5377299288301819298", "⚠️"),
    "stop":      ("5377268910860627479", "🛑"),
    "lock":      ("5377246106062577165", "🔒"),
    "check":     ("5377208966707527415", "✅"),
    "cross":     ("5377184978546879619", "❌"),
    "cross_mark":("5377184978546879619", "❌"),
    "gear":      ("5377158923245343476", "⚙️"),
    "fire":      ("5377134138959566379", "🔥"),
    "hourglass": ("5377104918607097490", "⏳"),
    "folder":    ("5377079275337052382", "📁"),
    "magnify":   ("5377055054925953019", "🔍"),
    "link":      ("5377027934447741383", "🔗"),
    "scissors":  ("5377003969515497632", "✂️"),
    "inbox":     ("5376982692879045308", "📥"),
    "outbox":    ("5376957494365685254", "📤"),
    "label":     ("5376931877657653766", "🏷️"),
    "person":    ("5376908359683988012", "👤"),
    "phone":     ("5376882959429877423", "📞"),
    "crown":     ("5376853657290797294", "👑"),
    "anchor":    ("5376826957243722385", "⚓"),
    "globe":     ("5376803085928570679", "🌐"),
    "chart":     ("5376775457271083190", "📊"),
    "mega":      ("5376749742456321653", "📢"),
    "pingpong":  ("5376723519564243966", "🏓"),
    "back":      ("5376698547437166897", "⏭️"),
    "card":      ("5376672819021922488", "📇"),
    "page":      ("5376646090605173489", "📄"),
    "home":      ("5376619362189424490", "🏠"),
    "bolt":      ("5376592633773675491", "⚡"),
    "boom":      ("5376565905357926492", "💥"),
    "blue":      ("5376539176942177493", "💙"),
    "green":     ("5376512448526428494", "🟢"),
    "red":       ("5376485720110679495", "🔴"),
    "star":      ("5376458991694930496", "⭐"),
    "number":    ("5376432263279181497", "🔢"),
    "company":   ("5376405534863432498", "🏢"),
    "plus":      ("5376378806447683499", "➕"),
    "mute":      ("5376352078031934500", "🤐"),
    "speech":    ("5376325349616185501", "💬"),
    "diamond2":  ("5376298621200000000", "💠"),
    "target":    ("5376271892773456789", "🎯"),
    "empty":     ("5376245164346913580", "📇"),
    "clip":      ("5376218435920370371", "📎"),
    "signal":    ("5376191707493827162", "📡"),
    "medal":     ("5376164979067283953", "🥇"),
    "flag":      ("5376138250640740744", "🏁"),
    "eyes":      ("5376111522214197535", "👀"),
    "bulb":      ("5376084793787654326", "💡"),
}

def ce(name):
    """Return a premium animated <tg-emoji> tag (or its safe fallback glyph)."""
    eid, fb = CUSTOM_EMOJI.get(name, ("", "✨"))
    if USE_CUSTOM_EMOJI and eid:
        return f'<tg-emoji emoji-id="{eid}">{fb}</tg-emoji>'
    return fb

# ============================================================================
#  🔐 THREAD-SAFETY PRIMITIVES
# ============================================================================
SESSION_LOCK = threading.RLock()   
USERS_LOCK = threading.Lock()      

SESSIONS = {}

def get_session(uid):
    with SESSION_LOCK:
        if uid not in SESSIONS:
            SESSIONS[uid] = {"data": {}, "lang": "en", "caption": True}
        return SESSIONS[uid]

def reset_user_data(uid):
    with SESSION_LOCK:
        s = SESSIONS.get(uid)
        if s:
            s["data"] = {}

def get_lang(uid):
    with SESSION_LOCK:
        return SESSIONS.get(uid, {}).get("lang", "en")

# ============================================================================
#  🌐 INTERNATIONALIZATION (en / hi / zh)  — labels and UI markup
# ============================================================================
BTN = {
    "text_to_vcf":  {"en": "📄 Text to VCF",   "hi": "📄 टेक्स्ट से VCF", "zh": "📄 文本转VCF"},
    "vcf_to_text":  {"en": "📇 VCF to Text",   "hi": "📇 VCF से टेक्स्ट", "zh": "📇 VCF转文本"},
    "admin_navy":   {"en": "👑 Admin/Navy VCF","hi": "👑 एडमिन/नेवी VCF","zh": "👑 管理/海军VCF"},
    "vcf_editor":   {"en": "✏️ VCF Editor",    "hi": "✏️ VCF एडिटर",      "zh": "✏️ VCF编辑器"},
    "merge_file":   {"en": "🔗 Merge File",    "hi": "🔗 फ़ाइल मर्ज",      "zh": "🔗 合并文件"},
    "split_file":   {"en": "✂️ Split File",    "hi": "✂️ फ़ाइल विभाजन",    "zh": "✂️ 拆分文件"},
    "rename_file":  {"en": "⚙️ Rename File",   "hi": "⚙️ नाम बदलें",       "zh": "⚙️ 重命名文件"},
    "get_details":  {"en": "🔍 Get VCF Details","hi": "🔍 VCF विवरण",      "zh": "🔍 VCF详情"},
    "language":     {"en": "🌐 Language",      "hi": "🌐 भाषा",            "zh": "🌐 语言"},
    "done":         {"en": "✅ Done & Merge",   "hi": "✅ पूर्ण और मर्ज",  "zh": "✅ 完成并合并"},
    "home":         {"en": "🏠 Main Menu",     "hi": "🏠 मुख्य मेनू",      "zh": "🏠 主菜单"},
}

TXT = {
    "welcome": {
        "en": f"{ce('sparkle')}{ce('diamond')} <b>Welcome to VCF Pro Manager!</b> {ce('diamond')}{ce('sparkle')}\n\n{ce('rocket')} Your all-in-one premium contact toolkit.\n{ce('point')} Tap an action from the dazzling menu below:",
        "hi": f"{ce('sparkle')}{ce('diamond')} <b>VCF प्रो मैनेजर में आपका स्वागत है!</b> {ce('diamond')}{ce('sparkle')}\n\n{ce('rocket')} आपका ऑल-इन-वन प्रीमियम कॉन्टैक्ट टूलकिट।\n{ce('point')} नीचे दिए गए मेनू से एक एक्शन चुनें:",
        "zh": f"{ce('sparkle')}{ce('diamond')} <b>欢迎使用 VCF 专业管理器！</b> {ce('diamond')}{ce('sparkle')}\n\n{ce('rocket')} 您的一站式高级通讯录工具箱。\n{ce('point')} 请从下方炫彩菜单轻点操作：",
    },
    "help": {
        "en": (f"📖 <b>VCF Pro — Help</b>\n\n"
               f"{ce('page')} <b>Text to VCF</b> — numbers/.txt/.xlsx to split VCF files\n"
               f"{ce('card')} <b>VCF to Text</b> — extract clean numbers\n"
               f"{ce('crown')} <b>Admin/Navy VCF</b> — dual-mode, auto-split @200\n"
               f"{ce('scissors')} <b>Split File</b> — break a big VCF into parts\n"
               f"{ce('link')} <b>Merge File</b> — combine many files into one VCF\n"
               f"{ce('gear')} <b>Rename File</b> — instant rename, keeps extension\n"
               f"{ce('magnify')} <b>Get VCF Details</b> — audit report + full log\n\n"
               f"🎛 <b>Commands:</b> /start /help /cancel /done /skip /caption /ping /stats /broadcast"),
        "hi": (f"📖 <b>VCF प्रो — मदद</b>\n\n"
               f"{ce('page')} <b>टेक्स्ट से VCF</b> — नंबर/.txt/.xlsx ➔ VCF फ़ाइलें\n"
               f"{ce('card')} <b>VCF से टेक्स्ट</b> — साफ़ नंबर निकालें\n"
               f"{ce('crown')} <b>एडमिन/नेवी VCF</b> — डुअल-मोड, ऑटो-स्प्लिट @200\n"
               f"{ce('scissors')} <b>फ़ाइल विभाजन</b> — बड़ी VCF को भागों में बाँटें\n"
               f"{ce('link')} <b>फ़ाइल मर्ज</b> — कई फ़ाइलें एक में जोड़ें\n"
               f"{ce('gear')} <b>नाम बदलें</b> — तुरंत रीनेम\n"
               f"{ce('magnify')} <b>VCF विवरण</b> — ऑडिट रिपोर्ट + लॉग\n\n"
               f"🎛 <b>कमांड:</b> /start /help /cancel /done /skip /caption /ping /stats /broadcast"),
        "zh": (f"📖 <b>VCF 专业版 — 帮助</b>\n\n"
               f"{ce('page')} <b>文本转VCF</b> — 号码/.txt/.xlsx ➔ VCF 文件\n"
               f"{ce('card')} <b>VCF转文本</b> — 提取纯净号码\n"
               f"{ce('crown')} <b>管理/海军VCF</b> — 双模式，自动按200拆分\n"
               f"{ce('scissors')} <b>拆分文件</b> — 将大 VCF 拆成多份\n"
               f"{ce('link')} <b>合并文件</b> — 多文件合并为一个 VCF\n"
               f"{ce('gear')} <b>重命名文件</b> — 即时改名\n"
               f"{ce('magnify')} <b>VCF详情</b> — 审计报告 + 日志\n\n"
               f"🎛 <b>命令:</b> /start /help /cancel /done /skip /caption /ping /stats /broadcast"),
    },
    "cancelled": {
        "en": f"{ce('stop')}{ce('sparkle')} <b>Operation cancelled.</b> Back to the main menu! {ce('home')}",
        "hi": f"{ce('stop')}{ce('sparkle')} <b>ऑपरेशन रद्द किया गया.</b> मुख्य मेनू पर वापस! {ce('home')}",
        "zh": f"{ce('stop')}{ce('sparkle')} <b>操作已取消。</b> 返回主菜单！{ce('home')}",
    },
    "choose_lang": {
        "en": f"{ce('globe')}{ce('sparkle')} <b>Choose your language:</b>",
        "hi": f"{ce('globe')}{ce('sparkle')} <b>अपनी भाषा चुनें:</b>",
        "zh": f"{ce('globe')}{ce('sparkle')} <b>请选择语言：</b>",
    },
    "lang_set": {
        "en": f"{ce('check')}🇬🇧 Language set to <b>English</b>!",
        "hi": f"{ce('check')}🇮🇳 भाषा <b>हिन्दी</b> पर सेट!",
        "zh": f"{ce('check')}🇨🇳 语言已设为 <b>简体中文</b>！",
    },
    "send_input_numbers": {
        "en": f"{ce('inbox')}{ce('sparkle')} <b>Send me your contacts!</b>\n\nPaste numbers directly, or upload a <code>.txt</code> / <code>.xlsx</code> file. {ce('page')}",
        "hi": f"{ce('inbox')}{ce('sparkle')} <b>अपने कॉन्टैक्ट भेजें!</b>\n\nनंबर पेस्ट करें, या <code>.txt</code> / <code>.xlsx</code> फ़ाइल अपलोड करें। {ce('page')}",
        "zh": f"{ce('inbox')}{ce('sparkle')} <b>发送您的联系人！</b>\n\n直接粘贴号码，或上传 <code>.txt</code> / <code>.xlsx</code> 文件。{ce('page')}",
    },
    "no_numbers": {
        "en": f"{ce('warn')} No valid numbers (min 7 digits) found. Try again!",
        "hi": f"{ce('warn')} कोई मान्य नंबर (न्यूनतम 7 अंक) नहीं मिला। पुनः प्रयास करें!",
        "zh": f"{ce('warn')} 未找到有效号码（至少7位）。请重试！",
    },
    "found_numbers": {
        "en": f"{ce('check')}{ce('party')} Found <b>{{" + "n" + "}}</b> unique numbers!",
        "hi": f"{ce('check')}{ce('party')} <b>{{" + "n" + "}}</b> अद्वितीय नंबर मिले!",
        "zh": f"{ce('check')}{ce('party')} 找到 <b>{{" + "n" + "}}</b> 个唯一号码！",
    },
    "ask_vcf_name": {
        "en": f"{ce('label')}{ce('sparkle')} <b>Step 1:</b> Enter the <b>VCF file name</b> (no extension):",
        "hi": f"{ce('label')}{ce('sparkle')} <b>चरण 1:</b> <b>VCF फ़ाइल नाम</b> दर्ज करें (बिना एक्सटेंशन):",
        "zh": f"{ce('label')}{ce('sparkle')} <b>第1步:</b> 输入 <b>VCF 文件名</b>（不含后缀）：",
    },
    "ask_prefix": {
        "en": f"{ce('person')}{ce('diamond2')} <b>Step 2:</b> Enter the <b>contact prefix name</b> (e.g. <code>Member</code>):",
        "hi": f"{ce('person')}{ce('diamond2')} <b>चरण 2:</b> <b>कॉन्टैक्ट प्रीफ़िक्स नाम</b> दर्ज करें (जैसे <code>Member</code>):",
        "zh": f"{ce('person')}{ce('diamond2')} <b>第2步:</b> 输入 <b>联系人前缀名</b>（如 <code>Member</code>）：",
    },
    "ask_company": {
        "en": f"{ce('company')}💼 <b>Step 3:</b> Enter a <b>company name</b>, or type /skip:",
        "hi": f"{ce('company')}💼 <b>चरण 3:</b> <b>कंपनी का नाम</b> दर्ज करें, या /skip टाइप करें:",
        "zh": f"{ce('company')}💼 <b>第3步:</b> 输入 <b>公司名称</b>，或输入 /skip：",
    },
    "ask_start_index": {
        "en": f"{ce('number')}{ce('star')} <b>Step 4:</b> Enter the <b>file starting index number</b> (e.g. <code>1</code>):",
        "hi": f"{ce('number')}{ce('star')} <b>चरण 4:</b> <b>फ़ाइल प्रारंभिक इंडेक्स नंबर</b> दर्ज करें (जैसे <code>1</code>):",
        "zh": f"{ce('number')}{ce('star')} <b>第4步:</b> 输入 <b>文件起始序号</b>（如 <code>1</code>）：",
    },
    "ask_split": {
        "en": f"{ce('scissors')}{ce('target')} <b>Step 5:</b> Enter <b>contacts per file</b> (<code>{{" + "mn" + "}}</code>–<code>{{" + "mx" + "}}</code>):",
        "hi": f"{ce('scissors')}{ce('target')} <b>चरण 5:</b> <b>प्रति फ़ाइल कॉन्टैक्ट</b> दर्ज करें (<code>{{" + "mn" + "}}</code>–<code>{{" + "mx" + "}}</code>):",
        "zh": f"{ce('scissors')}{ce('target')} <b>第5步:</b> 输入 <b>每个文件的联系人数</b>（<code>{{" + "mn" + "}}</code>–<code>{{" + "mx" + "}}</code>）：",
    },
    "bad_number": {
        "en": f"{ce('warn')} Please send a valid number. Try again!",
        "hi": f"{ce('warn')} कृपया मान्य संख्या भेजें। पुनः प्रयास करें!",
        "zh": f"{ce('warn')} 请输入有效数字。请重试！",
    },
    "building": {
        "en": f"{ce('gear')}{ce('fire')} <b>Building your VCF files...</b> Please wait! {ce('hourglass')}",
        "hi": f"{ce('gear')}{ce('fire')} <b>आपकी VCF फ़ाइलें बन रही हैं...</b> कृपया प्रतीक्षा करें! {ce('hourglass')}",
        "zh": f"{ce('gear')}{ce('fire')} <b>正在生成您的 VCF 文件...</b> 请稍候！{ce('hourglass')}",
    },
    "done_files": {
        "en": f"{ce('party')}{ce('diamond')} <b>All done!</b> Sent <b>{{" + "n" + "}}</b> file(s) with <b>{{" + "c" + "}}</b> contacts. {ce('rocket')}",
        "hi": f"{ce('party')}{ce('diamond')} <b>सब हो गया!</b> <b>{{" + "c" + "}}</b> कॉन्टैक्ट के साथ <b>{{" + "n" + "}}</b> फ़ाइल भेजी। {ce('rocket')}",
        "zh": f"{ce('party')}{ce('diamond')} <b>全部完成！</b> 已发送 <b>{{" + "n" + "}}</b> 个文件，共 <b>{{" + "c" + "}}</b> 个联系人。{ce('rocket')}",
    },
    "send_vcf": {
        "en": f"{ce('outbox')}📇 <b>Send me a <code>.vcf</code> file</b> to process. {ce('sparkle')}",
        "hi": f"{ce('outbox')}📇 प्रोसेस के लिए <b>एक <code>.vcf</code> फ़ाइल भेजें</b>। {ce('sparkle')}",
        "zh": f"{ce('outbox')}📇 <b>请发送一个 <code>.vcf</code> 文件</b> 进行处理。{ce('sparkle')}",
    },
    "not_vcf": {
        "en": f"{ce('warn')} That's not a <code>.vcf</code> file. Please send a valid VCF!",
        "hi": f"{ce('warn')} यह <code>.vcf</code> फ़ाइल नहीं है। कृपया मान्य VCF भेजें!",
        "zh": f"{ce('warn')} 这不是 <code>.vcf</code> 文件。请发送有效的 VCF！",
    },
    "ask_basename": {
        "en": f"{ce('label')}{ce('sparkle')} Enter a <b>new base name</b> for the output files:",
        "hi": f"{ce('label')}{ce('sparkle')} आउटपुट फ़ाइलों के लिए <b>नया बेस नाम</b> दर्ज करें:",
        "zh": f"{ce('label')}{ce('sparkle')} 为输出文件输入 <b>新的基础名称</b>：",
    },
    "merge_collect": {
        "en": f"{ce('link')}{ce('sparkle')} <b>Merge mode active!</b>\n\nSend <code>.vcf</code> / <code>.txt</code> files one-by-one. When finished, tap <b>✅ Done & Merge</b> below or send /done.",
        "hi": f"{ce('link')}{ce('sparkle')} <b>मर्ज मोड सक्रिय!</b>\n\n<code>.vcf</code> / <code>.txt</code> फ़ाइलें एक-एक भेजें। पूर्ण होने पर नीचे <b>✅ पूर्ण और मर्ज</b> दबाएँ या /done भेजें।",
        "zh": f"{ce('link')}{ce('sparkle')} <b>合并模式已开启！</b>\n\n逐个发送 <code>.vcf</code> / <code>.txt</code> 文件。完成后点击下方 <b>✅ 完成并合并</b> 或发送 /done。",
    },
    "merge_added": {
        "en": f"{ce('plus')}{ce('check')} Added to queue! Total files: <b>{{" + "n" + "}}</b>. Send more or tap <b>✅ Done & Merge</b>.",
        "hi": f"{ce('plus')}{ce('check')} कतार में जोड़ा गया! कुल फ़ाइलें: <b>{{" + "n" + "}}</b>. और भेजें या <b>✅ पूर्ण और मर्ज</b> दबाएँ।",
        "zh": f"{ce('plus')}{ce('check')} 已加入队列！文件总数：<b>{{" + "n" + "}}</b>。继续发送或点击 <b>✅ 完成并合并</b>。",
    },
    "merge_empty": {
        "en": f"{ce('warn')} Your merge queue is empty. Send some files first!",
        "hi": f"{ce('warn')} आपकी मर्ज कतार खाली है। पहले कुछ फ़ाइलें भेजें!",
        "zh": f"{ce('warn')} 合并队列为空。请先发送文件！",
    },
    "rename_send": {
        "en": f"{ce('gear')}{ce('clip')} <b>Send me ANY file</b> you want to rename. {ce('sparkle')}",
        "hi": f"{ce('gear')}{ce('clip')} जिस फ़ाइल का नाम बदलना है <b>वह भेजें</b>। {ce('sparkle')}",
        "zh": f"{ce('gear')}{ce('clip')} <b>发送任意文件</b> 以重命名。{ce('sparkle')}",
    },
    "rename_ask": {
        "en": f"{ce('label')}{ce('sparkle')} Enter the <b>new name</b> (extension is kept automatically):",
        "hi": f"{ce('label')}{ce('sparkle')} <b>नया नाम</b> दर्ज करें (एक्सटेंशन स्वतः रहेगा):",
        "zh": f"{ce('label')}{ce('sparkle')} 输入 <b>新名称</b>（后缀自动保留）：",
    },
    "caption_on":  {"en": f"{ce('green')} Captions <b>enabled</b>. {ce('speech')}", "hi": f"{ce('green')} कैप्शन <b>चालू</b>. {ce('speech')}", "zh": f"{ce('green')} 标题已 <b>开启</b>。{ce('speech')}"},
    "caption_off": {"en": f"{ce('red')} Captions <b>disabled</b>. {ce('mute')}", "hi": f"{ce('red')} कैप्शन <b>बंद</b>. {ce('mute')}", "zh": f"{ce('red')} 标题已 <b>关闭</b>。{ce('mute')}"},
    "admin_only":  {"en": f"{ce('cross')}{ce('lock')} Admins only!", "hi": f"{ce('cross')}{ce('lock')} केवल एडमिन!", "zh": f"{ce('cross')}{ce('lock')} 仅限管理员！"},
    "stats": {
        "en": f"{ce('chart')}{ce('diamond')} <b>Bot Stats</b>\n\n{ce('person')} Registered users: <b>{{" + "u" + "}}</b>\n{ce('green')} Active sessions: <b>{{" + "s" + "}}</b>",
        "hi": f"{ce('chart')}{ce('diamond')} <b>बॉट आँकड़े</b>\n\n{ce('person')} पंजीकृत उपयोगकर्ता: <b>{{" + "u" + "}}</b>\n{ce('green')} सक्रिय सत्र: <b>{{" + "s" + "}}</b>",
        "zh": f"{ce('chart')}{ce('diamond')} <b>机器人统计</b>\n\n{ce('person')} 注册用户：<b>{{" + "u" + "}}</b>\n{ce('green')} 活跃会话：<b>{{" + "s" + "}}</b>",
    },
    "broadcast_ask": {
        "en": f"{ce('mega')}{ce('sparkle')} Send me the <b>message to broadcast</b> to all users:",
        "hi": f"{ce('mega')}{ce('sparkle')} सभी उपयोगकर्ताओं को <b>प्रसारित करने का संदेश</b> भेजें:",
        "zh": f"{ce('mega')}{ce('sparkle')} 发送要 <b>广播</b> 给所有用户的消息：",
    },
    "broadcast_done": {
        "en": f"{ce('mega')}{ce('check')} <b>Broadcast complete!</b>\n\n{ce('check')} Sent: <b>{{" + "ok" + "}}</b>\n{ce('cross')} Failed: <b>{{" + "fail" + "}}</b>",
        "hi": f"{ce('mega')}{ce('check')} <b>प्रसारण पूर्ण!</b>\n\n{ce('check')} भेजा: <b>{{" + "ok" + "}}</b>\n{ce('cross')} विफल: <b>{{" + "fail" + "}}</b>",
        "zh": f"{ce('mega')}{ce('check')} <b>广播完成！</b>\n\n{ce('check')} 成功：<b>{{" + "ok" + "}}</b>\n{ce('cross')} 失败：<b>{{" + "fail" + "}}</b>",
    },
    "admin_collect_vip": {
        "en": f"{ce('crown')}{ce('diamond2')} <b>Admin/Navy Mode!</b>\n\n<b>Step 1:</b> Send <b>VIP/Admin numbers</b> (paste or file), or /skip:",
        "hi": f"{ce('crown')}{ce('diamond2')} <b>एडमिन/नेवी मोड!</b>\n\n<b>चरण 1:</b> <b>VIP/एडमिन नंबर</b> भेजें, या /skip:",
        "zh": f"{ce('crown')}{ce('diamond2')} <b>管理/海军模式！</b>\n\n<b>第1步:</b> 发送 <b>VIP/管理号码</b>，或 /skip：",
    },
    "admin_vip_prefix": {
        "en": f"{ce('person')}{ce('crown')} Enter the <b>VIP/Admin prefix name</b>:",
        "hi": f"{ce('person')}{ce('crown')} <b>VIP/एडमिन प्रीफ़िक्स नाम</b> दर्ज करें:",
        "zh": f"{ce('person')}{ce('crown')} 输入 <b>VIP/管理前缀名</b>：",
    },
    "admin_collect_navy": {
        "en": f"{ce('anchor')}{ce('blue')} <b>Step 2:</b> Now send the <b>Navy numbers</b> (paste or file):",
        "hi": f"{ce('anchor')}{ce('blue')} <b>चरण 2:</b> अब <b>नेवी नंबर</b> भेजें:",
        "zh": f"{ce('anchor')}{ce('blue')} <b>第2步:</b> 现在发送 <b>海军号码</b>：",
    },
    "admin_navy_prefix": {
        "en": f"{ce('person')}{ce('anchor')} Enter the <b>Navy prefix name</b>:",
        "hi": f"{ce('person')}{ce('anchor')} <b>नेवी प्रीफ़िक्स नाम</b> दर्ज करें:",
        "zh": f"{ce('person')}{ce('anchor')} 输入 <b>海军前缀名</b>：",
    },
    "ask_outfile": {
        "en": f"{ce('label')}{ce('sparkle')} Enter the <b>final output filename</b>:",
        "hi": f"{ce('label')}{ce('sparkle')} <b>अंतिम आउटपुट फ़ाइल नाम</b> दर्ज करें:",
        "zh": f"{ce('label')}{ce('sparkle')} 输入 <b>最终输出文件名</b>：",
    },
    "generic_error": {
        "en": f"{ce('boom')} Something went wrong: <code>{{" + "e" + "}}</code>\nPlease /start again.",
        "hi": f"{ce('boom')} कुछ गड़बड़ हो गई: <code>{{" + "e" + "}}</code>\nकृपया फिर से /start करें।",
        "zh": f"{ce('boom')} 出错了：<code>{{" + "e" + "}}</code>\n请重新 /start。",
    },
    "menu_hint": {
        "en": f"{ce('bulb')}{ce('sparkle')} Tap an option from the premium menu below {ce('point')}",
        "hi": f"{ce('bulb')}{ce('sparkle')} नीचे प्रीमियम मेनू से एक विकल्प चुनें {ce('point')}",
        "zh": f"{ce('bulb')}{ce('sparkle')} 请从下方高级菜单选择一项 {ce('point')}",
    },
    "idle_file": {
        "en": f"{ce('inbox')} Pick a module from the menu first, then send your file! {ce('point')}",
        "hi": f"{ce('inbox')} पहले मेनू से एक मॉड्यूल चुनें, फिर फ़ाइल भेजें! {ce('point')}",
        "zh": f"{ce('inbox')} 请先从菜单选择模块，然后发送文件！{ce('point')}",
    },
    "vcf_editor_welcome": {
        "en": f"{ce('gear')} <b>VCF Editor Active!</b>\n\n📥 Send me a <code>.vcf</code> file to edit:",
        "hi": f"{ce('gear')} <b>VCF एडिटर सक्रिय!</b>\n\n📥 एडिट करने के लिए <code>.vcf</code> फ़ाइल भेजें:",
        "zh": f"{ce('gear')} <b>VCF 编辑器已开启！</b>\n\n📥 请发送 <code>.vcf</code> 文件：",
    }
}

def t(uid, key, **kw):
    lang = get_lang(uid)
    template = TXT.get(key, {}).get(lang) or TXT.get(key, {}).get("en", key)
    return template.format(**kw) if kw else template

# ============================================================================
#  ⌨️ DYNAMIC INLINE KEYBOARDS
# ============================================================================
def _ib(uid, key):
    lang = get_lang(uid)
    return types.InlineKeyboardButton(BTN[key][lang], callback_data=f"menu:{key}")

def main_menu(uid):
    kb = types.InlineKeyboardMarkup()
    kb.row(_ib(uid, "text_to_vcf"), _ib(uid, "vcf_to_text"))
    kb.row(_ib(uid, "admin_navy"),  _ib(uid, "vcf_editor"))
    kb.row(_ib(uid, "merge_file"),  _ib(uid, "split_file"))
    kb.row(_ib(uid, "rename_file"), _ib(uid, "get_details"))
    kb.row(_ib(uid, "language"))
    return kb

def merge_menu(uid):
    lang = get_lang(uid)
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton(BTN["done"][lang], callback_data="merge:done"))
    kb.row(types.InlineKeyboardButton(BTN["home"][lang], callback_data="menu:home"))
    return kb

def home_menu(uid):
    lang = get_lang(uid)
    kb = types.InlineKeyboardMarkup()
    kb.row(types.InlineKeyboardButton(BTN["home"][lang], callback_data="menu:home"))
    return kb

def lang_menu():
    kb = types.InlineKeyboardMarkup()
    kb.row(
        types.InlineKeyboardButton("🇬🇧 English", callback_data="lang:en"),
        types.InlineKeyboardButton("🇮🇳 हिन्दी", callback_data="lang:hi"),
        types.InlineKeyboardButton("🇨🇳 简体中文", callback_data="lang:zh"),
    )
    return kb

# ============================================================================
#  🧰 CORE HELPERS  (parsing, VCF building, file I/O)
# ============================================================================
NUMBER_RE = re.compile(r'\+?\d[\d\-\s().]{5,}\d')   
VCARD_RE = re.compile(r'BEGIN:VCARD.*?END:VCARD', re.DOTALL | re.IGNORECASE)
TEL_RE = re.compile(r'TEL[^:]*:([+\d\-\s().]+)', re.IGNORECASE)
FN_RE = re.compile(r'^FN:(.*)$', re.IGNORECASE | re.MULTILINE)

def extract_numbers(text):
    found, seen = [], set()
    for raw in NUMBER_RE.findall(text or ""):
        digits = re.sub(r'\D', '', raw)
        plus = raw.strip().startswith('+')
        if len(digits) < 7:
            continue
        norm = ('+' + digits) if plus else digits
        if norm not in seen:
            seen.add(norm)
            found.append(norm)
    return found

def numbers_from_xlsx(path):
    if not OPENPYXL_OK:
        return []
    chunks = []
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            for row in ws.iter_rows(values_only=True):
                for cell in row:
                    if cell is not None:
                        chunks.append(str(cell))
    finally:
        wb.close()
    return extract_numbers(" ".join(chunks))

def build_vcards(numbers, prefix, company, start_index):
    cards = []
    idx = start_index
    for num in numbers:
        name = f"{prefix} {idx}"
        org = f"ORG:{company}\n" if company else ""
        card = (
            "BEGIN:VCARD\n"
            "VERSION:3.0\n"
            f"FN:{name}\n"
            f"N:{name};;;;\n"
            f"{org}"
            f"TEL;TYPE=CELL:{num}\n"
            "END:VCARD"
        )
        cards.append(card)
        idx += 1
    return cards

def write_temp(content, suffix):
    path = os.path.join(TMP_DIR, f"{uuid.uuid4().hex}{suffix}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path

def safe_remove(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception as e:
        log.warning("Could not remove %s: %s", path, e)

def caption_for(uid, text):
    with SESSION_LOCK:
        enabled = SESSIONS.get(uid, {}).get("caption", True)
    return text if enabled else None

def send_vcf_chunks(uid, chat_id, cards, base_name, per_file, start_part=1):
    total_files = 0
    temp_paths = []
    try:
        part = start_part
        for i in range(0, len(cards), per_file):
            chunk = cards[i:i + per_file]
            content = "\n".join(chunk) + "\n"
            fname = f"{base_name}_{part}.vcf" if len(cards) > per_file else f"{base_name}.vcf"
            path = os.path.join(TMP_DIR, f"{uuid.uuid4().hex}.vcf")
            temp_paths.append(path)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            with open(path, "rb") as f:
                bot.send_document(
                    chat_id, f, visible_file_name=fname,
                    caption=caption_for(uid, f"{ce('card')}{ce('sparkle')} <b>{escape(fname)}</b> — {len(chunk)} contacts {ce('diamond')}"),
                )
            total_files += 1
            part += 1
    finally:
        for p in temp_paths:
            safe_remove(p)
    return total_files

def download_to_temp(message):
    file_info = bot.get_file(message.document.file_id)
    data = bot.download_file(file_info.file_path)
    orig = message.document.file_name or "file"
    ext = os.path.splitext(orig)[1] or ".bin"
    path = os.path.join(TMP_DIR, f"{uuid.uuid4().hex}{ext}")
    with open(path, "wb") as f:
        f.write(data)
    return path, orig

# ============================================================================
#  👥 USER REGISTRY
# ============================================================================
def register_user(uid):
    with USERS_LOCK:
        existing = set()
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                existing = {line.strip() for line in f if line.strip()}
        if str(uid) not in existing:
            with open(USERS_FILE, "a", encoding="utf-8") as f:
                f.write(f"{uid}\n")

def all_users():
    with USERS_LOCK:
        if not os.path.exists(USERS_FILE):
            return []
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return [line.strip() for line in f if line.strip()]

# ============================================================================
#  🛡️ ANTI-LOOP GATEKEEPER (RE-ENTRANCY SAFE)
# ============================================================================
GLOBAL_COMMANDS = {"/start", "/help", "/cancel", "/ping", "/stats", "/broadcast", "/caption"}

def check_menu_or_commands(message):
    uid = message.from_user.id
    text = (message.text or "").strip()

    cmd = text.split()[0] if text else ""
    if cmd in GLOBAL_COMMANDS:
        bot.clear_step_handler_by_chat_id(message.chat.id)
        reset_user_data(uid)
        _dispatch_command(message, cmd)
        return True
    return False

def _dispatch_command(message, cmd):
    if cmd == "/start":
        cmd_start(message)
    elif cmd == "/help":
        cmd_help(message)
    elif cmd == "/cancel":
        cmd_cancel(message)
    elif cmd == "/ping":
        cmd_ping(message)
    elif cmd == "/stats":
        cmd_stats(message)
    elif cmd == "/broadcast":
        cmd_broadcast(message)
    elif cmd == "/caption":
        cmd_caption(message)

# ============================================================================
#  🎬 COMMAND HANDLERS
# ============================================================================
@bot.message_handler(commands=["start"])
def cmd_start(message):
    uid = message.from_user.id
    register_user(uid)
    bot.clear_step_handler_by_chat_id(message.chat.id)
    reset_user_data(uid)
    bot.send_message(message.chat.id, t(uid, "welcome"), reply_markup=main_menu(uid))

@bot.message_handler(commands=["help"])
def cmd_help(message):
    uid = message.from_user.id
    bot.send_message(message.chat.id, t(uid, "help"), reply_markup=main_menu(uid))

@bot.message_handler(commands=["cancel"])
def cmd_cancel(message):
    uid = message.from_user.id
    bot.clear_step_handler_by_chat_id(message.chat.id)
    reset_user_data(uid)
    bot.send_message(message.chat.id, t(uid, "cancelled"), reply_markup=main_menu(uid))

@bot.message_handler(commands=["ping"])
def cmd_ping(message):
    uid = message.from_user.id
    start = time.time()
    sent = bot.send_message(message.chat.id, f"{ce('pingpong')} Pinging...")
    latency = int((time.time() - start) * 1000)
    bot.edit_message_text(
        f"{ce('pingpong')}{ce('bolt')} <b>Pong!</b>\n\n{ce('signal')} Response velocity: <b>{latency} ms</b> {ce('rocket')}",
        message.chat.id, sent.message_id,
    )

@bot.message_handler(commands=["caption"])
def cmd_caption(message):
    uid = message.from_user.id
    s = get_session(uid)
    with SESSION_LOCK:
        s["caption"] = not s.get("caption", True)
        now = s["caption"]
    bot.send_message(message.chat.id, t(uid, "caption_on" if now else "caption_off"))

@bot.message_handler(commands=["stats"])
def cmd_stats(message):
    uid = message.from_user.id
    if uid not in ADMIN_IDS:
        bot.send_message(message.chat.id, t(uid, "admin_only"))
        return
    with SESSION_LOCK:
        active = len(SESSIONS)
    bot.send_message(message.chat.id, t(uid, "stats", u=len(all_users()), s=active))

@bot.message_handler(commands=["broadcast"])
def cmd_broadcast(message):
    uid = message.from_user.id
    if uid not in ADMIN_IDS:
        bot.send_message(message.chat.id, t(uid, "admin_only"))
        return
    msg = bot.send_message(message.chat.id, t(uid, "broadcast_ask"), reply_markup=home_menu(uid))
    bot.register_next_step_handler(msg, step_broadcast_send)

def step_broadcast_send(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    payload = message.text or ""
    ok = fail = 0
    for u in all_users():
        try:
            bot.send_message(int(u), f"{ce('mega')}{ce('sparkle')} <b>Broadcast</b>\n\n{payload}")
            ok += 1
            time.sleep(0.05)  
        except Exception:
            fail += 1
    bot.send_message(message.chat.id, t(uid, "broadcast_done", ok=ok, fail=fail), reply_markup=main_menu(uid))

@bot.message_handler(commands=["done"])
def cmd_done(message):
    uid = message.from_user.id
    with SESSION_LOCK:
        mode = SESSIONS.get(uid, {}).get("data", {}).get("mode")
    if mode == "merge":
        finalize_merge_prompt(message)
    else:
        cmd_start(message)

@bot.message_handler(commands=["skip"])
def cmd_skip(message):
    bot.send_message(message.chat.id, f"{ce('back')} Nothing to skip right now. 😊")

# ============================================================================
#  🎛️ CALLBACK ROUTING PANELS
# ============================================================================
@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("lang:"))
def on_lang(call):
    uid = call.from_user.id
    lang = call.data.split(":", 1)[1]
    if lang in ("en", "hi", "zh"):
        s = get_session(uid)
        with SESSION_LOCK:
            s["lang"] = lang
        bot.answer_callback_query(call.id, "✅")
        bot.send_message(call.message.chat.id, t(uid, "lang_set"), reply_markup=main_menu(uid))

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("menu:"))
def on_menu(call):
    uid = call.from_user.id
    key = call.data.split(":", 1)[1]
    bot.answer_callback_query(call.id)
    
    register_user(uid)
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    reset_user_data(uid)

    if key == "home":
        bot.send_message(call.message.chat.id, t(uid, "welcome"), reply_markup=main_menu(uid))
    elif key == "language":
        bot.send_message(call.message.chat.id, t(uid, "choose_lang"), reply_markup=lang_menu())
    elif key == "text_to_vcf":
        start_text_to_vcf(call.message)
    elif key == "vcf_to_text":
        start_vcf_to_text(call.message)
    elif key == "admin_navy":
        start_admin_navy(call.message)
    elif key == "vcf_editor":
        start_vcf_editor(call.message)
    elif key == "merge_file":
        start_merge(call.message)
    elif key == "split_file":
        start_split(call.message)
    elif key == "rename_file":
        start_rename(call.message)
    elif key == "get_details":
        start_get_details(call.message)

@bot.callback_query_handler(func=lambda c: c.data == "merge:done")
def on_merge_done_callback(call):
    bot.answer_callback_query(call.id)
    finalize_merge_prompt(call.message)

# ============================================================================
#  📄 MODULE 1 — TEXT/EXCEL TO VCF  (Normal Mode)
# ============================================================================
def start_text_to_vcf(message):
    uid = message.chat.id  
    with SESSION_LOCK:
        get_session(uid)["data"] = {"mode": "text2vcf"}
    msg = bot.send_message(message.chat.id, t(uid, "send_input_numbers"), reply_markup=home_menu(uid))
    bot.register_next_step_handler(msg, step_t2v_input)

def step_t2v_input(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    numbers = []
    try:
        if message.content_type == "document":
            path, orig = download_to_temp(message)
            try:
                if orig.lower().endswith(".xlsx"):
                    numbers = numbers_from_xlsx(path)
                else:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        numbers = extract_numbers(f.read())
            finally:
                safe_remove(path)
        else:
            numbers = extract_numbers(message.text)
    except Exception as e:
        bot.send_message(message.chat.id, t(uid, "generic_error", e=escape(str(e))), reply_markup=main_menu(uid))
        reset_user_data(uid)
        return

    if not numbers:
        msg = bot.send_message(message.chat.id, t(uid, "no_numbers"), reply_markup=home_menu(uid))
        bot.register_next_step_handler(msg, step_t2v_input)
        return

    random.shuffle(numbers)  
    with SESSION_LOCK:
        get_session(uid)["data"]["numbers"] = numbers
    bot.send_message(message.chat.id, t(uid, "found_numbers", n=len(numbers)))
    msg = bot.send_message(message.chat.id, t(uid, "ask_vcf_name"), reply_markup=home_menu(uid))
    bot.register_next_step_handler(msg, step_t2v_name)

def step_t2v_name(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    with SESSION_LOCK:
        get_session(uid)["data"]["filename"] = (message.text or "contacts").strip()
    msg = bot.send_message(message.chat.id, t(uid, "ask_prefix"), reply_markup=home_menu(uid))
    bot.register_next_step_handler(msg, step_t2v_prefix)

def step_t2v_prefix(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    with SESSION_LOCK:
        get_session(uid)["data"]["prefix"] = (message.text or "Contact").strip()
    msg = bot.send_message(message.chat.id, t(uid, "ask_company"), reply_markup=home_menu(uid))
    bot.register_next_step_handler(msg, step_t2v_company)

def step_t2v_company(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    text = (message.text or "").strip()
    company = "" if text.lower() in ("/skip", "skip") else text
    with SESSION_LOCK:
        get_session(uid)["data"]["company"] = company
    msg = bot.send_message(message.chat.id, t(uid, "ask_start_index"), reply_markup=home_menu(uid))
    bot.register_next_step_handler(msg, step_t2v_start)

def step_t2v_start(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    try:
        start_idx = int(re.sub(r'\D', '', (message.text or "1")) or "1")
    except ValueError:
        msg = bot.send_message(message.chat.id, t(uid, "bad_number"), reply_markup=home_menu(uid))
        bot.register_next_step_handler(msg, step_t2v_start)
        return
    with SESSION_LOCK:
        get_session(uid)["data"]["start_index"] = start_idx
    msg = bot.send_message(message.chat.id, t(uid, "ask_split", mn=DEFAULT_SPLIT_MIN, mx=DEFAULT_SPLIT_MAX), reply_markup=home_menu(uid))
    bot.register_next_step_handler(msg, step_t2v_split)

def step_t2v_split(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    try:
        split = int(re.sub(r'\D', '', (message.text or "")) or "0")
    except ValueError:
        split = 0
    if split <= 0:
        msg = bot.send_message(message.chat.id, t(uid, "bad_number"), reply_markup=home_menu(uid))
        bot.register_next_step_handler(msg, step_t2v_split)
        return
    split = max(DEFAULT_SPLIT_MIN, min(DEFAULT_SPLIT_MAX, split))

    with SESSION_LOCK:
        data = get_session(uid)["data"]
        numbers = data.get("numbers", [])
        prefix = data.get("prefix", "Contact")
        company = data.get("company", "")
        start_idx = data.get("start_index", 1)
        fname = data.get("filename", "contacts")

    bot.send_message(message.chat.id, t(uid, "building"))
    try:
        cards = build_vcards(numbers, prefix, company, start_idx)
        n_files = send_vcf_chunks(uid, message.chat.id, cards, fname, split)
        bot.send_message(message.chat.id, t(uid, "done_files", n=n_files, c=len(cards)), reply_markup=main_menu(uid))
    except Exception as e:
        bot.send_message(message.chat.id, t(uid, "generic_error", e=escape(str(e))), reply_markup=main_menu(uid))
    finally:
        reset_user_data(uid)

# ============================================================================
#  👑 MODULE 2 — ADMIN/NAVY DUAL-MODE  (auto-split @ 200)
# ============================================================================
def start_admin_navy(message):
    uid = message.chat.id  
    with SESSION_LOCK:
        get_session(uid)["data"] = {"mode": "admin_navy"}
    msg = bot.send_message(message.chat.id, t(uid, "admin_collect_vip"), reply_markup=home_menu(uid))
    bot.register_next_step_handler(msg, step_an_vip)

def _grab_numbers(message):
    if message.content_type == "document":
        path, orig = download_to_temp(message)
        try:
            if orig.lower().endswith(".xlsx"):
                return numbers_from_xlsx(path)
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                return extract_numbers(f.read())
        finally:
            safe_remove(path)
    return extract_numbers(message.text)

def step_an_vip(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    text = (message.text or "").strip().lower()
    if text in ("/skip", "skip"):
        vips = []
    else:
        vips = _grab_numbers(message)
    with SESSION_LOCK:
        get_session(uid)["data"]["vips"] = vips
    if vips:
        msg = bot.send_message(message.chat.id, t(uid, "admin_vip_prefix"), reply_markup=home_menu(uid))
        bot.register_next_step_handler(msg, step_an_vip_prefix)
    else:
        with SESSION_LOCK:
            get_session(uid)["data"]["vip_prefix"] = "Admin"
        msg = bot.send_message(message.chat.id, t(uid, "admin_collect_navy"), reply_markup=home_menu(uid))
        bot.register_next_step_handler(msg, step_an_navy)

def step_an_vip_prefix(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    with SESSION_LOCK:
        get_session(uid)["data"]["vip_prefix"] = (message.text or "Admin").strip()
    msg = bot.send_message(message.chat.id, t(uid, "admin_collect_navy"), reply_markup=home_menu(uid))
    bot.register_next_step_handler(msg, step_an_navy)

def step_an_navy(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    navy = _grab_numbers(message)
    if not navy:
        msg = bot.send_message(message.chat.id, t(uid, "no_numbers"), reply_markup=home_menu(uid))
        bot.register_next_step_handler(msg, step_an_navy)
        return
    with SESSION_LOCK:
        get_session(uid)["data"]["navy"] = navy
    msg = bot.send_message(message.chat.id, t(uid, "admin_navy_prefix"), reply_markup=home_menu(uid))
    bot.register_next_step_handler(msg, step_an_navy_prefix)

def step_an_navy_prefix(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    with SESSION_LOCK:
        get_session(uid)["data"]["navy_prefix"] = (message.text or "Navy").strip()
    msg = bot.send_message(message.chat.id, t(uid, "ask_outfile"), reply_markup=home_menu(uid))
    bot.register_next_step_handler(msg, step_an_outfile)

def step_an_outfile(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    fname = (message.text or "AdminNavy").strip()
    with SESSION_LOCK:
        data = get_session(uid)["data"]
        vips = data.get("vips", [])
        navy = data.get("navy", [])
        vip_prefix = data.get("vip_prefix", "Admin")
        navy_prefix = data.get("navy_prefix", "Navy")

    bot.send_message(message.chat.id, t(uid, "building"))
    try:
        cards = build_vcards(vips, vip_prefix, "", 1) + build_vcards(navy, navy_prefix, "", 1)
        n_files = send_vcf_chunks(uid, message.chat.id, cards, fname, ADMIN_NAVY_SPLIT)
        bot.send_message(message.chat.id, t(uid, "done_files", n=n_files, c=len(cards)), reply_markup=main_menu(uid))
    except Exception as e:
        bot.send_message(message.chat.id, t(uid, "generic_error", e=escape(str(e))), reply_markup=main_menu(uid))
    finally:
        reset_user_data(uid)

# ============================================================================
#  📇 MODULE 3 — VCF TO TEXT
# ============================================================================
def start_vcf_to_text(message):
    uid = message.chat.id  
    with SESSION_LOCK:
        get_session(uid)["data"] = {"mode": "vcf2text"}
    msg = bot.send_message(message.chat.id, t(uid, "send_vcf"), reply_markup=home_menu(uid))
    bot.register_next_step_handler(msg, step_vcf2text_file)

def step_vcf2text_file(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    if message.content_type != "document" or not (message.document.file_name or "").lower().endswith(".vcf"):
        msg = bot.send_message(message.chat.id, t(uid, "not_vcf"), reply_markup=home_menu(uid))
        bot.register_next_step_handler(msg, step_vcf2text_file)
        return

    in_path = out_path = None
    try:
        in_path, _ = download_to_temp(message)
        with open(in_path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
        numbers = []
        seen = set()
        for tel in TEL_RE.findall(raw):
            for num in extract_numbers(tel):
                if num not in seen:
                    seen.add(num)
                    numbers.append(num)
        out_path = write_temp("\n".join(numbers) + "\n", ".txt")
        with open(out_path, "rb") as f:
            bot.send_document(
                message.chat.id, f, visible_file_name="numbers.txt",
                caption=caption_for(uid, f"{ce('check')}{ce('sparkle')} Extracted <b>{len(numbers)}</b> unique numbers! {ce('diamond')}"),
            )
        bot.send_message(message.chat.id, f"{ce('party')} Done!", reply_markup=main_menu(uid))
    except Exception as e:
        bot.send_message(message.chat.id, t(uid, "generic_error", e=escape(str(e))), reply_markup=main_menu(uid))
    finally:
        safe_remove(in_path)
        safe_remove(out_path)
        reset_user_data(uid)

# ============================================================================
#  ✏️ MODULE 4 — VCF EDITOR
# ============================================================================
def start_vcf_editor(message):
    uid = message.chat.id  
    with SESSION_LOCK:
        get_session(uid)["data"] = {"mode": "vcf_editor"}
    msg = bot.send_message(message.chat.id, t(uid, "vcf_editor_welcome"), reply_markup=home_menu(uid))
    bot.register_next_step_handler(msg, step_editor_file)

def step_editor_file(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    if message.content_type != "document" or not (message.document.file_name or "").lower().endswith(".vcf"):
        msg = bot.send_message(message.chat.id, t(uid, "not_vcf"), reply_markup=home_menu(uid))
        bot.register_next_step_handler(msg, step_editor_file)
        return
    
    in_path = None
    try:
        in_path, orig = download_to_temp(message)
        with open(in_path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
        
        cards = [c.strip() for c in VCARD_RE.findall(raw)]
        if not cards:
            bot.send_message(message.chat.id, t(uid, "not_vcf"), reply_markup=main_menu(uid))
            reset_user_data(uid)
            return
            
        with SESSION_LOCK:
            get_session(uid)["data"]["cards"] = cards
            get_session(uid)["data"]["orig_name"] = orig
            
        bot.send_message(message.chat.id, t(uid, "found_numbers", n=len(cards)))
        msg = bot.send_message(message.chat.id, t(uid, "ask_prefix"), reply_markup=home_menu(uid))
        bot.register_next_step_handler(msg, step_editor_prefix)
    except Exception as e:
        bot.send_message(message.chat.id, t(uid, "generic_error", e=escape(str(e))), reply_markup=main_menu(uid))
        reset_user_data(uid)
    finally:
        safe_remove(in_path)

def step_editor_prefix(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    prefix = (message.text or "Contact").strip()
    with SESSION_LOCK:
        get_session(uid)["data"]["prefix"] = prefix
    msg = bot.send_message(message.chat.id, t(uid, "ask_basename"), reply_markup=home_menu(uid))
    bot.register_next_step_handler(msg, step_editor_finalize)

def step_editor_finalize(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    base = (message.text or "Edited").strip()
    
    with SESSION_LOCK:
        data = get_session(uid)["data"]
        cards = data.get("cards", [])
        prefix = data.get("prefix", "Contact")
        
    bot.send_message(message.chat.id, t(uid, "building"))
    out_path = None
    try:
        edited_cards = []
        for idx, card in enumerate(cards, start=1):
            name = f"{prefix} {idx}"
            card = re.sub(r'FN:[^\n\r]*', f'FN:{name}', card)
            card = re.sub(r'N:[^\n\r]*', f'N:{name};;;;', card)
            edited_cards.append(card)
            
        merged = "\n".join(edited_cards) + "\n"
        out_path = write_temp(merged, ".vcf")
        
        with open(out_path, "rb") as f:
            bot.send_document(
                message.chat.id, f, visible_file_name=f"{base}.vcf",
                caption=caption_for(uid, f"{ce('gear')}{ce('check')} Edited <b>{len(edited_cards)}</b> contacts successfully!"),
            )
        bot.send_message(message.chat.id, f"{ce('party')} Done!", reply_markup=main_menu(uid))
    except Exception as e:
        bot.send_message(message.chat.id, t(uid, "generic_error", e=escape(str(e))), reply_markup=main_menu(uid))
    finally:
        safe_remove(out_path)
        reset_user_data(uid)

# ============================================================================
#  ✂️ MODULE 5 — VCF SPLITTER
# ============================================================================
def start_split(message):
    uid = message.chat.id  
    with SESSION_LOCK:
        get_session(uid)["data"] = {"mode": "split"}
    msg = bot.send_message(message.chat.id, t(uid, "send_vcf"), reply_markup=home_menu(uid))
    bot.register_next_step_handler(msg, step_split_file)

def step_split_file(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    if message.content_type != "document" or not (message.document.file_name or "").lower().endswith(".vcf"):
        msg = bot.send_message(message.chat.id, t(uid, "not_vcf"), reply_markup=home_menu(uid))
        bot.register_next_step_handler(msg, step_split_file)
        return
    in_path = None
    try:
        in_path, _ = download_to_temp(message)
        with open(in_path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
        cards = [c.strip() for c in VCARD_RE.findall(raw)]
        if not cards:
            bot.send_message(message.chat.id, t(uid, "not_vcf"), reply_markup=main_menu(uid))
            reset_user_data(uid)
            return
        with SESSION_LOCK:
            get_session(uid)["data"]["cards"] = cards
        bot.send_message(message.chat.id, t(uid, "found_numbers", n=len(cards)))
        msg = bot.send_message(message.chat.id, t(uid, "ask_split", mn=1, mx=1000), reply_markup=home_menu(uid))
        bot.register_next_step_handler(msg, step_split_limit)
    except Exception as e:
        bot.send_message(message.chat.id, t(uid, "generic_error", e=escape(str(e))), reply_markup=main_menu(uid))
        reset_user_data(uid)
    finally:
        safe_remove(in_path)

def step_split_limit(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    try:
        limit = int(re.sub(r'\D', '', (message.text or "")) or "0")
    except ValueError:
        limit = 0
    if limit <= 0:
        msg = bot.send_message(message.chat.id, t(uid, "bad_number"), reply_markup=home_menu(uid))
        bot.register_next_step_handler(msg, step_split_limit)
        return
    with SESSION_LOCK:
        get_session(uid)["data"]["limit"] = limit
    msg = bot.send_message(message.chat.id, t(uid, "ask_basename"), reply_markup=home_menu(uid))
    bot.register_next_step_handler(msg, step_split_basename)

def step_split_basename(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    base = (message.text or "Split").strip()
    with SESSION_LOCK:
        data = get_session(uid)["data"]
        cards = data.get("cards", [])
        limit = data.get("limit", 200)
    bot.send_message(message.chat.id, t(uid, "building"))
    try:
        n_files = send_vcf_chunks(uid, message.chat.id, cards, f"{base}_Part", limit)
        bot.send_message(message.chat.id, t(uid, "done_files", n=n_files, c=len(cards)), reply_markup=main_menu(uid))
    except Exception as e:
        bot.send_message(message.chat.id, t(uid, "generic_error", e=escape(str(e))), reply_markup=main_menu(uid))
    finally:
        reset_user_data(uid)

# ============================================================================
#  🔗 MODULE 6 — MERGE
# ============================================================================
def start_merge(message):
    uid = message.chat.id  
    with SESSION_LOCK:
        get_session(uid)["data"] = {"mode": "merge", "queue": []}
    msg = bot.send_message(message.chat.id, t(uid, "merge_collect"), reply_markup=merge_menu(uid))
    bot.register_next_step_handler(msg, step_merge_collect)

def step_merge_collect(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    text = (message.text or "").strip()

    if text == "/done":
        finalize_merge_prompt(message)
        return

    if message.content_type == "document":
        try:
            path, _ = download_to_temp(message)
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            safe_remove(path)
            with SESSION_LOCK:
                get_session(uid)["data"]["queue"].append(content)
                qlen = len(get_session(uid)["data"]["queue"])
            bot.send_message(message.chat.id, t(uid, "merge_added", n=qlen), reply_markup=merge_menu(uid))
        except Exception as e:
            bot.send_message(message.chat.id, t(uid, "generic_error", e=escape(str(e))), reply_markup=merge_menu(uid))
    else:
        bot.send_message(message.chat.id, t(uid, "merge_collect"), reply_markup=merge_menu(uid))

    bot.register_next_step_handler_by_chat_id(message.chat.id, step_merge_collect)

def finalize_merge_prompt(message):
    uid = message.chat.id if message.from_user.id == bot.get_me().id else message.from_user.id
    with SESSION_LOCK:
        queue = get_session(uid)["data"].get("queue", [])
    if not queue:
        msg = bot.send_message(message.chat.id, t(uid, "merge_empty"), reply_markup=merge_menu(uid))
        bot.register_next_step_handler(msg, step_merge_collect)
        return
    bot.clear_step_handler_by_chat_id(message.chat.id)
    msg = bot.send_message(message.chat.id, t(uid, "ask_outfile"), reply_markup=home_menu(uid))
    bot.register_next_step_handler(msg, step_merge_finalize)

def step_merge_finalize(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    fname = (message.text or "Merged").strip()
    with SESSION_LOCK:
        queue = get_session(uid)["data"].get("queue", [])
    bot.send_message(message.chat.id, t(uid, "building"))
    out_path = None
    try:
        all_cards = []
        for content in queue:
            cards = VCARD_RE.findall(content)
            if cards:
                all_cards.extend(c.strip() for c in cards)
            else:
                nums = extract_numbers(content)
                all_cards.extend(build_vcards(nums, "Contact", "", 1))
        merged = "\n".join(all_cards) + "\n"
        out_path = write_temp(merged, ".vcf")
        with open(out_path, "rb") as f:
            bot.send_document(
                message.chat.id, f, visible_file_name=f"{fname}.vcf",
                caption=caption_for(uid, f"{ce('link')}{ce('diamond')} Merged <b>{len(all_cards)}</b> contacts from <b>{len(queue)}</b> files! {ce('rocket')}"),
            )
        bot.send_message(message.chat.id, f"{ce('party')} Done!", reply_markup=main_menu(uid))
    except Exception as e:
        bot.send_message(message.chat.id, t(uid, "generic_error", e=escape(str(e))), reply_markup=main_menu(uid))
    finally:
        safe_remove(out_path)
        reset_user_data(uid)

# ============================================================================
#  ⚙️ MODULE 7 — QUICK RENAME
# ============================================================================
def start_rename(message):
    uid = message.chat.id  
    with SESSION_LOCK:
        get_session(uid)["data"] = {"mode": "rename"}
    msg = bot.send_message(message.chat.id, t(uid, "rename_send"), reply_markup=home_menu(uid))
    bot.register_next_step_handler(msg, step_rename_file)

def step_rename_file(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    if message.content_type != "document":
        msg = bot.send_message(message.chat.id, t(uid, "rename_send"), reply_markup=home_menu(uid))
        bot.register_next_step_handler(msg, step_rename_file)
        return
    try:
        path, orig = download_to_temp(message)
        ext = os.path.splitext(orig)[1]
        with SESSION_LOCK:
            get_session(uid)["data"]["path"] = path
            get_session(uid)["data"]["ext"] = ext
        msg = bot.send_message(message.chat.id, t(uid, "rename_ask"), reply_markup=home_menu(uid))
        bot.register_next_step_handler(msg, step_rename_name)
    except Exception as e:
        bot.send_message(message.chat.id, t(uid, "generic_error", e=escape(str(e))), reply_markup=main_menu(uid))
        reset_user_data(uid)

def step_rename_name(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    new_name = (message.text or "renamed").strip()
    with SESSION_LOCK:
        data = get_session(uid)["data"]
        path = data.get("path")
        ext = data.get("ext", "")
    if not new_name.lower().endswith(ext.lower()):
        new_name = new_name + ext
    try:
        with open(path, "rb") as f:
            bot.send_document(
                message.chat.id, f, visible_file_name=new_name,
                caption=caption_for(uid, f"{ce('gear')}{ce('check')} Renamed to <b>{escape(new_name)}</b> {ce('diamond')}"),
            )
        bot.send_message(message.chat.id, f"{ce('party')} Done!", reply_markup=main_menu(uid))
    except Exception as e:
        bot.send_message(message.chat.id, t(uid, "generic_error", e=escape(str(e))), reply_markup=main_menu(uid))
    finally:
        safe_remove(path)
        reset_user_data(uid)

# ============================================================================
#  🔍 MODULE 8 — ADVANCED VCF DETAILS SCANNER
# ============================================================================
def start_get_details(message):
    uid = message.chat.id  
    with SESSION_LOCK:
        get_session(uid)["data"] = {"mode": "details"}
    msg = bot.send_message(message.chat.id, t(uid, "send_vcf"), reply_markup=home_menu(uid))
    bot.register_next_step_handler(msg, step_details_file)

def step_details_file(message):
    if check_menu_or_commands(message):
        return
    uid = message.from_user.id
    if message.content_type != "document" or not (message.document.file_name or "").lower().endswith(".vcf"):
        msg = bot.send_message(message.chat.id, t(uid, "not_vcf"), reply_markup=home_menu(uid))
        bot.register_next_step_handler(msg, step_details_file)
        return

    in_path = log_path = None
    try:
        in_path, orig = download_to_temp(message)
        with open(in_path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
        cards = VCARD_RE.findall(raw)

        parsed = []   
        for card in cards:
            fn = FN_RE.search(card)
            name = fn.group(1).strip() if fn else "Unknown"
            tels = TEL_RE.findall(card)
            num = re.sub(r'[^\d+]', '', tels[0]) if tels else "N/A"
            parsed.append((name, num))

        total = len(parsed)
        first_name = parsed[0][0] if parsed else "—"
        last_name = parsed[-1][0] if parsed else "—"

        preview = parsed[:50]
        lines = [f"{ce('magnify')}{ce('diamond')} <b>VCF Audit Report</b> {ce('diamond')}\n",
                 f"{ce('folder')} <b>File:</b> <code>{escape(orig)}</code>",
                 f"{ce('number')} <b>Total Contacts:</b> <b>{total}</b>",
                 f"{ce('medal')} <b>First Contact:</b> {escape(first_name)}",
                 f"{ce('flag')} <b>Last Contact:</b> {escape(last_name)}\n",
                 f"{ce('eyes')} <b>Preview (first 50):</b>"]
        for i, (nm, nb) in enumerate(preview, 1):
            lines.append(f"<code>{i:>3}.</code> {ce('person')} {escape(nm)} — {ce('phone')} <code>{escape(nb)}</code>")
        report = "\n".join(lines)
        if len(report) > 3900:
            report = report[:3900] + f"\n…{ce('scissors')} <i>(truncated — see attached log)</i>"
        bot.send_message(message.chat.id, report)

        log_lines = [f"{i+1}. {nm} -> {nb}" for i, (nm, nb) in enumerate(parsed)]
        log_path = write_temp("VCF DETAILED LOG\n=================\n" + "\n".join(log_lines) + "\n", ".txt")
        with open(log_path, "rb") as f:
            bot.send_document(
                message.chat.id, f, visible_file_name=f"{os.path.splitext(orig)[0]}_log.txt",
                caption=caption_for(uid, f"{ce('page')}{ce('check')} Full log of <b>{total}</b> contacts. {ce('diamond')}"),
            )
        bot.send_message(message.chat.id, f"{ce('party')} Done!", reply_markup=main_menu(uid))
    except Exception as e:
        bot.send_message(message.chat.id, t(uid, "generic_error", e=escape(str(e))), reply_markup=main_menu(uid))
    finally:
        safe_remove(in_path)
        safe_remove(log_path)
        reset_user_data(uid)

# ============================================================================
#  🗂️ CATCH-ALL HANDLERS
# ============================================================================
@bot.message_handler(content_types=["text"])
def catch_all_text(message):
    uid = message.from_user.id
    register_user(uid)
    text = (message.text or "").strip()

    cmd = text.split()[0] if text else ""
    if cmd in GLOBAL_COMMANDS:
        _dispatch_command(message, cmd)
        return

    bot.send_message(uid, t(uid, "menu_hint"), reply_markup=main_menu(uid))

@bot.message_handler(content_types=["document", "photo", "audio", "video"])
def catch_all_files(message):
    uid = message.from_user.id
    register_user(uid)
    bot.send_message(uid, t(uid, "idle_file"), reply_markup=main_menu(uid))

# ============================================================================
#  🌐 FLASK KEEP-ALIVE WEB SERVER
# ============================================================================
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "💎✨ VCF Pro Bot is alive and running 24/7 with Premium HTML Custom Emojis! 🚀"

@flask_app.route("/health")
def health():
    return {"status": "ok", "time": time.time()}

def run_flask():
    flask_app.run(host="0.0.0.0", port=8080)

def keep_alive():
    thread = threading.Thread(target=run_flask, daemon=True)
    thread.start()

# ============================================================================
#  🚀 BOOT
# ============================================================================
def set_default_commands():
    cmds = [
        types.BotCommand("start", "🏠 Open main menu"),
        types.BotCommand("help", "📖 How to use the bot"),
        types.BotCommand("cancel", "🛑 Cancel current operation"),
        types.BotCommand("done", "✅ Finish merge queue"),
        types.BotCommand("skip", "⏭️ Skip an optional step"),
        types.BotCommand("caption", "💬 Toggle file captions"),
        types.BotCommand("ping", "🏓 Latency test"),
        types.BotCommand("stats", "📊 Bot stats (admin)"),
        types.BotCommand("broadcast", "📢 Broadcast (admin)"),
    ]
    try:
        bot.set_my_commands(cmds)
    except Exception as e:
        log.warning("Could not set commands: %s", e)

if __name__ == "__main__":
    keep_alive()
    set_default_commands()
    log.info("💎 VCF Pro Bot starting (polling)...")
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)
