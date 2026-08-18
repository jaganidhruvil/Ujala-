import os
import httpx
import json
import base64
import hmac
import hashlib
import random
import string
import time
import urllib.parse
import io
import logging
import re
from datetime import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# ========== CONFIGURATION ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [
    int(value.strip())
    for value in os.getenv("ADMIN_IDS", "").split(",")
    if value.strip()
]

BASE_URL = "https://www.ujalahappiestonam.com/api/users"
MASTER_KEY = os.getenv("UJALA_MASTER_KEY")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")
if not MASTER_KEY:
    raise RuntimeError("UJALA_MASTER_KEY environment variable is required")
if not ADMIN_IDS:
    raise RuntimeError("ADMIN_IDS environment variable is required")

# ========== FIXED VALUES ==========
PRODUCT_CODE = "8902102126232"
CITY = "Kerala"
IMAGE_URL = "https://i.ibb.co/pB9DQkrM/00.jpg"

# ========== BOT CONTROL ==========
BOT_STATUS = {
    "is_on": True,
    "schedule_enabled": False,
    "schedule_off": "20:00",
    "schedule_on": "01:00",
}

# ========== REQUIRED CHANNELS ==========
REQUIRED_CHANNELS = [
    {
        "chat_id": "@KALUASC",
        "invite_url": "https://t.me/KALUASC"
    },
    {
        "chat_id": "@vishalxupdate",
        "invite_url": "https://t.me/vishalxupdate"
    },
    {
        "chat_id": "@X00MTSxKIDS",
        "invite_url": "https://t.me/X00MTSxKIDS"
    },
    {
        "chat_id": "@axxuloots",
        "invite_url": "https://t.me/axxuloots"
    }
]

# ========== LOGGING ==========
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ========== GLOBAL STATE ==========
user_sessions = {}

HTTP_CLIENT = None
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.ujalahappiestonam.com",
    "Referer": "https://www.ujalahappiestonam.com/",
}
HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=15.0, write=15.0, pool=10.0)

# ========== CORE FUNCTIONS ==========
def _log_api_failure(stage: str, response=None, reason: str = ""):
    """Log only non-sensitive diagnostics; never log OTPs, tokens, or credentials."""
    status_code = getattr(response, "status_code", None)
    response_status = "unavailable"
    decoded_status = None
    if response is not None:
        try:
            body = response.json()
            response_status = "json" if isinstance(body, dict) else type(body).__name__
            encrypted = body.get("resp") if isinstance(body, dict) else None
            if encrypted:
                decoded, ok = decrypt_resp(encrypted)
                if ok and isinstance(decoded, dict):
                    decoded_status = decoded.get("statusCode")
        except (ValueError, json.JSONDecodeError, TypeError):
            response_status = "invalid_json"
        except Exception:
            response_status = "unreadable"
    logging.warning(
        "API failure stage=%s http_status=%s response_status=%s decoded_status=%s reason=%s",
        stage, status_code, response_status, decoded_status, reason or "unspecified"
    )

def generate_signature_data(payload: dict, user_key: str, data_key: str) -> str:
    payload_str = json.dumps(payload, separators=(',', ':'))
    a = base64.b64encode(payload_str.encode()).decode()
    ts = str(payload['t'])
    u = base64.b64encode(ts.encode()).decode()
    hmac_key = data_key[4:18].encode()
    message = f"{u}.{a}".encode()
    h = hmac.new(hmac_key, message, hashlib.sha256)
    hex_sig = h.hexdigest()
    f = base64.b64encode(hex_sig.encode()).decode()
    m = random.randint(1, 6)
    k = random.randint(2, 8)
    alphabet = string.ascii_letters + string.digits
    h_rand = "".join(random.choice(alphabet) for _ in range(k))
    g = f"{k}{m}{f[0:m]}{h_rand}{f[m:]}"
    return f"{u}.{a}.{g}"

def decrypt_resp(encrypted: str):
    try:
        return json.loads(base64.b64decode(encrypted).decode()), True
    except:
        return {"error": "decrypt_failed", "raw": encrypted}, False

def get_timestamp():
    return int(time.time() * 1000)

async def download_image_from_url(url: str) -> bytes:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    response = await HTTP_CLIENT.get(url, headers=headers)
    response.raise_for_status()
    return response.content

async def create_user():
    try:
        response = await HTTP_CLIENT.post(f"{BASE_URL}", json={"masterKey": MASTER_KEY})
        if response.status_code >= 400:
            _log_api_failure("create_user", response, "http_error")
            return None, None
        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError):
            _log_api_failure("create_user", response, "invalid_json")
            return None, None
        encrypted = data.get("resp") if isinstance(data, dict) else None
        if not encrypted:
            _log_api_failure("create_user", response, "missing_resp")
            return None, None
        decoded, ok = decrypt_resp(encrypted)
        if not ok or not isinstance(decoded, dict):
            _log_api_failure("create_user", response, "decrypt_failed")
            return None, None
        if decoded.get("statusCode") != 200 or not decoded.get("userKey") or not decoded.get("dataKey"):
            _log_api_failure("create_user", response, "unexpected_api_status")
            return None, None
        return str(decoded["userKey"]), decoded["dataKey"]
    except httpx.TimeoutException:
        logging.warning("API failure stage=create_user reason=timeout")
        return None, None
    except httpx.RequestError as exc:
        logging.warning("API failure stage=create_user reason=request_error type=%s", type(exc).__name__)
        return None, None
    except Exception:
        logging.exception("API failure stage=create_user reason=unexpected_exception")
        return None, None

async def send_otp(user_key, data_key, name, mobile, image_bytes, code=PRODUCT_CODE, city=CITY):
    t = get_timestamp()
    payload = {
        "name": name, "mobile": mobile, "email": "", "city": city, "code": code,
        "agreed1": "Yes", "agreed2": "Yes", "userKey": int(user_key), "t": t
    }
    data_value = generate_signature_data(payload, user_key, data_key)
    files = {"pack": ("pack.jpg", image_bytes, "image/jpeg")}
    form_data = {"t": str(t), "userKey": user_key, "data": data_value}
    try:
        response = await HTTP_CLIENT.post(f"{BASE_URL}/getOTP/{user_key}?t={t}", data=form_data, files=files)
        if response.status_code >= 400:
            _log_api_failure("send_otp", response, "http_error")
            return False
        try:
            resp_json = response.json()
        except (ValueError, json.JSONDecodeError):
            _log_api_failure("send_otp", response, "invalid_json")
            return False
        encrypted = resp_json.get("resp") if isinstance(resp_json, dict) else None
        if not encrypted:
            _log_api_failure("send_otp", response, "missing_resp")
            return False
        decoded, ok = decrypt_resp(encrypted)
        if not ok or not isinstance(decoded, dict):
            _log_api_failure("send_otp", response, "decrypt_failed")
            return False
        if decoded.get("statusCode") != 200:
            _log_api_failure("send_otp", response, "unexpected_api_status")
            return False
        return True
    except httpx.TimeoutException:
        logging.warning("API failure stage=send_otp reason=timeout")
        return False
    except httpx.RequestError as exc:
        logging.warning("API failure stage=send_otp reason=request_error type=%s", type(exc).__name__)
        return False
    except Exception:
        logging.exception("API failure stage=send_otp reason=unexpected_exception")
        return False

async def _post_signed(path, user_key, data_key, token=None, timeout_stage="api"):
    t = get_timestamp()
    payload = {"userKey": int(user_key), "t": t}
    data_value = generate_signature_data(payload, user_key, data_key)
    u, a, g = data_value.split(".", 2)
    body = f"userKey={user_key}&data={urllib.parse.quote_plus(u)}.{urllib.parse.quote_plus(a)}.{urllib.parse.quote_plus(g)}"
    headers = {"content-type": "application/x-www-form-urlencoded; charset=UTF-8"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    return await HTTP_CLIENT.post(f"{BASE_URL}/{path}/{user_key}?t={t}", data=body, headers=headers)

async def verify_otp(user_key, data_key, otp):
    try:
        t = get_timestamp()
        payload = {"otp": otp, "userKey": int(user_key), "t": t}
        data_value = generate_signature_data(payload, user_key, data_key)
        u, a, g = data_value.split(".", 2)
        body = f"userKey={user_key}&data={urllib.parse.quote_plus(u)}.{urllib.parse.quote_plus(a)}.{urllib.parse.quote_plus(g)}"
        response = await HTTP_CLIENT.post(f"{BASE_URL}/verifyOTP/{user_key}?t={t}", data=body, headers={"content-type": "application/x-www-form-urlencoded; charset=UTF-8"})
        data = response.json()
        decoded, ok = decrypt_resp(data.get("resp", ""))
        if ok and isinstance(decoded, dict) and decoded.get("statusCode") == 200:
            return decoded.get("token")
        _log_api_failure("verify_otp", response, "unexpected_api_status")
    except httpx.TimeoutException:
        logging.warning("API failure stage=verify_otp reason=timeout")
    except (httpx.RequestError, ValueError, json.JSONDecodeError):
        logging.warning("API failure stage=verify_otp reason=request_or_json_error")
    except Exception:
        logging.exception("API failure stage=verify_otp reason=unexpected_exception")
    return None

async def spin_wheel(user_key, data_key, token):
    try:
        response = await _post_signed("speenTheWheel", user_key, data_key, token)
        data = response.json()
        decoded, ok = decrypt_resp(data.get("resp", ""))
        if ok and isinstance(decoded, dict) and decoded.get("statusCode") == 200:
            return decoded.get("reward", "Unknown")
        _log_api_failure("spin_wheel", response, "unexpected_api_status")
    except httpx.TimeoutException:
        logging.warning("API failure stage=spin_wheel reason=timeout")
    except (httpx.RequestError, ValueError, json.JSONDecodeError):
        logging.warning("API failure stage=spin_wheel reason=request_or_json_error")
    except Exception:
        logging.exception("API failure stage=spin_wheel reason=unexpected_exception")
    return None

async def claim_reward(user_key, data_key, token):
    try:
        response = await _post_signed("claimNow", user_key, data_key, token)
        data = response.json()
        decoded, ok = decrypt_resp(data.get("resp", ""))
        if ok and isinstance(decoded, dict) and decoded.get("statusCode") == 200:
            return True
        _log_api_failure("claim_reward", response, "unexpected_api_status")
    except httpx.TimeoutException:
        logging.warning("API failure stage=claim_reward reason=timeout")
    except (httpx.RequestError, ValueError, json.JSONDecodeError):
        logging.warning("API failure stage=claim_reward reason=request_or_json_error")
    except Exception:
        logging.exception("API failure stage=claim_reward reason=unexpected_exception")
    return False

def mask_mobile(mobile: str) -> str:
    if len(mobile) == 10:
        return f"{mobile[:4]}xxxx{mobile[8:]}"
    return mobile

# ========== NAME GENERATOR ==========
FIRST_NAMES = ["Aarav", "Vivaan", "Aditya", "Vihaan", "Arjun", "Sai", "Reyansh", "Ayaan", 
               "Ananya", "Aadhya", "Diya", "Myra", "Sara", "Anika", "Pari", "Aarohi", "Kiara",
               "Rahul", "Amit", "Priya", "Neha", "Raj", "Simran", "Karan", "Divya"]
LAST_NAMES = ["Nair", "Menon", "Pillai", "Kurup", "Nambiar", "Warrier", "Panicker", "Thampi", 
              "Varma", "Sharma", "Patel", "Singh", "Kumar", "Reddy", "Gupta", "Joshi"]

def generate_random_name():
    return f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"

# ========== IS ADMIN ==========
def is_admin(user_id):
    return user_id in ADMIN_IDS

# ========== FORCE JOIN FUNCTIONS ==========
async def check_membership(user_id):
    try:
        bot = application.bot
        for channel in REQUIRED_CHANNELS:
            chat_id = channel.get("chat_id")
            if not chat_id:
                continue
            try:
                member = await bot.get_chat_member(chat_id, user_id)
                if member.status not in ["member", "administrator", "creator"]:
                    return False
            except:
                return False
        return True
    except:
        return False

def get_force_join_keyboard():
    keyboard = []
    for channel in REQUIRED_CHANNELS:
        keyboard.append([InlineKeyboardButton(
            f"📢 Join {channel['chat_id']}", 
            url=channel['invite_url']
        )])
    keyboard.append([InlineKeyboardButton("✅ Check Membership", callback_data="check_membership")])
    return InlineKeyboardMarkup(keyboard)

def get_force_join_text():
    channels_list = "\n".join([f"• {ch['chat_id']}" for ch in REQUIRED_CHANNELS])
    return f"""🔒 *Access Restricted*

Bot use karne ke liye pehle ye channels join karo:

{channels_list}

👇 Neeche button dabao channel join karne ke liye"""

# ========== BOT CONTROL ==========
def is_bot_on():
    if not BOT_STATUS["schedule_enabled"]:
        return BOT_STATUS["is_on"]
    try:
        now = datetime.now()
        current = now.strftime("%H:%M")
        off = BOT_STATUS["schedule_off"]
        on = BOT_STATUS["schedule_on"]
        if off <= on:
            if current >= off or current < on:
                return False
            return True
        else:
            if off <= current < on:
                return False
            return True
    except:
        return BOT_STATUS["is_on"]

def get_bot_status_text():
    if not BOT_STATUS["schedule_enabled"]:
        status = "ON" if BOT_STATUS["is_on"] else "OFF"
        mode = "Manual"
    else:
        status = "ON" if is_bot_on() else "OFF"
        mode = "Scheduled"
    return f"Status: {status}\nMode: {mode}\nSchedule: {BOT_STATUS['schedule_on']} ON | {BOT_STATUS['schedule_off']} OFF"

# ========== CUSTOM REPLY KEYBOARD (Raw API) ==========
async def send_claim_keyboard(chat_id, text):
    """Only Claim button - Cyan (primary)"""
    keyboard_json = {
        "keyboard": [
            [
                {
                    "text": "🎡 Claim Reward",
                    "style": "primary",
                    "icon_custom_emoji_id": "5471984997361523302"
                }
            ]
        ],
        "resize_keyboard": True
    }
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": json.dumps(keyboard_json)
    }
    
    try:
        response = await HTTP_CLIENT.post(url, json=payload)
        return response.json()
    except Exception as e:
        logging.error(f"send_claim_keyboard error: {e}")
        return None

async def send_cancel_keyboard(chat_id, text):
    """Only Cancel button - Red (danger)"""
    keyboard_json = {
        "keyboard": [
            [
                {
                    "text": "❌ Cancel",
                    "style": "danger",
                    "icon_custom_emoji_id": "5382224089295365367"
                }
            ]
        ],
        "resize_keyboard": True
    }
    
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": json.dumps(keyboard_json)
    }
    
    try:
        response = await HTTP_CLIENT.post(url, json=payload)
        return response.json()
    except Exception as e:
        logging.error(f"send_cancel_keyboard error: {e}")
        return None

# ========== TELEGRAM HANDLERS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    first_name = update.effective_user.first_name or "User"
    
    if is_admin(user_id):
        await send_claim_keyboard(
            chat_id,
            f"""🤖 <b>UJALA CLAIM BOT</b>

👋 <b>Hello, {first_name}!</b>

🟢 <b>Bot Status:</b> Online

🎁 Apna reward claim karein 👇

🛠 Admin panel: /admin"""
        )
        user_sessions[user_id] = {"state": "idle"}
        return
    
    if not is_bot_on():
        await update.message.reply_text(
            "🔴 <b>Bot abhi band hai.</b>",
            parse_mode="HTML"
        )
        return
    
    if not await check_membership(user_id):
        text = get_force_join_text()
        reply_markup = get_force_join_keyboard()
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")
        return
    
    user_sessions[user_id] = {"state": "idle"}
    await send_claim_keyboard(
        chat_id,
        f"""🤖 <b>UJALA CLAIM BOT</b>

👋 <b>Hello, {first_name}!</b>

🟢 <b>Bot Status:</b> Online

🎁 Apna reward claim karein 👇"""
    )

async def check_membership_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    first_name = update.effective_user.first_name or "User"
    
    if not is_bot_on():
        await query.edit_message_text("🔴 <b>Bot abhi band hai.</b>", parse_mode="HTML")
        return
    
    if await check_membership(user_id):
        await query.edit_message_text(
            "✅ <b>Access Granted</b>\n\nAb bot use kar sakte ho.",
            parse_mode="HTML"
        )
        user_sessions[user_id] = {"state": "idle"}
        await send_claim_keyboard(
            chat_id,
            f"""🤖 <b>UJALA CLAIM BOT</b>

👋 <b>Hello, {first_name}!</b>

🟢 <b>Bot Status:</b> Online

🎁 Apna reward claim karein 👇"""
        )
    else:
        text = get_force_join_text()
        reply_markup = get_force_join_keyboard()
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode="Markdown")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    
    if not is_admin(user_id):
        if not is_bot_on():
            await update.message.reply_text("🔴 <b>Bot abhi band hai.</b>", parse_mode="HTML")
            return
        
        if not await check_membership(user_id):
            text_msg = get_force_join_text()
            reply_markup = get_force_join_keyboard()
            await update.message.reply_text(text_msg, reply_markup=reply_markup, parse_mode="Markdown")
            return
    
    if user_id not in user_sessions:
        user_sessions[user_id] = {"state": "idle"}
    
    state = user_sessions[user_id].get("state", "idle")
    
    # ===== MAIN MENU BUTTON =====
    if text == "🎡 Claim Reward" and state == "idle":
        await update.message.reply_text(
            "📱 <b>Apna 10-digit mobile number daalo</b>\n"
            "(without +91)\n\n"
            "Example: <code>9876543210</code>",
            parse_mode="HTML"
        )
        user_sessions[user_id]["state"] = "waiting_mobile"
        return
    
    # ===== CANCEL BUTTON =====
    if text == "❌ Cancel":
        if state in ["waiting_mobile", "waiting_otp", "processing_mobile"]:
            user_sessions[user_id] = {"state": "idle"}
            first_name = update.effective_user.first_name or "User"
            await send_claim_keyboard(
                chat_id,
                f"""🤖 <b>UJALA CLAIM BOT</b>

👋 <b>Hello, {first_name}!</b>

🟢 <b>Bot Status:</b> Online

🎁 Apna reward claim karein 👇"""
            )
        else:
            await update.message.reply_text("ℹ️ Cancel karne ke liye kuch nahi hai.")
        return

    # Avoid creating a duplicate welcome/menu message if another message arrives
    # while the mobile-number request is still being processed.
    if state == "processing_mobile":
        await update.message.reply_text("⏳ Your previous request is still processing. Please wait.")
        return
    
    # ===== DIRECT MOBILE INPUT =====
    if state == "waiting_mobile":
        mobile = re.sub(r'[\s\+]', '', text)
        if mobile.startswith("91"):
            mobile = mobile[2:]
        
        if len(mobile) != 10 or not mobile.isdigit():
            await update.message.reply_text(
                "❌ <b>Invalid mobile number</b>\n"
                "Sirf 10 digits daalo.\n"
                "Example: <code>9876543210</code>",
                parse_mode="HTML"
            )
            return
        
        user_sessions[user_id]["state"] = "processing_mobile"
        await update.message.reply_text("⏳ Processing...")
        
        name = generate_random_name()
        user_sessions[user_id]["mobile"] = mobile
        user_sessions[user_id]["name"] = name
        
        try:
            image_bytes = await download_image_from_url(IMAGE_URL)
            user_key, data_key = await create_user()
            if not user_key:
                user_sessions[user_id] = {"state": "idle"}
                await update.message.reply_text(
                    "❌ Processing failed.\n\nPlease try again after some time."
                )
                return
            
            user_sessions[user_id]["user_key"] = user_key
            user_sessions[user_id]["data_key"] = data_key
            
            if not await send_otp(user_key, data_key, name, mobile, image_bytes):
                user_sessions[user_id] = {"state": "idle"}
                await update.message.reply_text(
                    "❌ Ye number already registered hai.\n\nPlease koi dusra number try karein."
                )
                return
            
            user_sessions[user_id]["state"] = "waiting_otp"
            
            await send_cancel_keyboard(
                chat_id,
                f"✅ <b>OTP send kar diya {mobile} pe!</b>\n\n📩 <b>Ab 6-digit OTP daalo</b>\nExample: <code>123456</code>\n\n❌ Cancel karne ke liye red button dabao."
            )
            
        except httpx.TimeoutException:
            logging.warning("API failure stage=exception reason=timeout")
            user_sessions[user_id] = {"state": "idle"}
            await update.message.reply_text("⚠️ Temporary error occurred.\n\nPlease try again.")
        except httpx.RequestError as exc:
            logging.warning("API failure stage=exception reason=request_error type=%s", type(exc).__name__)
            user_sessions[user_id] = {"state": "idle"}
            await update.message.reply_text("⚠️ Temporary error occurred.\n\nPlease try again.")
        except Exception:
            logging.exception("API failure stage=exception reason=unexpected_exception")
            user_sessions[user_id] = {"state": "idle"}
            await update.message.reply_text("⚠️ Temporary error occurred.\n\nPlease try again.")
        
        return
    
    # ===== DIRECT OTP INPUT =====
    if state == "waiting_otp":
        otp = re.sub(r'[\s\+]', '', text)
        
        if len(otp) != 6 or not otp.isdigit():
            await update.message.reply_text(
                "❌ <b>Invalid OTP</b>\n"
                "Sirf 6 digits daalo.\n"
                "Example: <code>123456</code>",
                parse_mode="HTML"
            )
            return
        
        await update.message.reply_text("⏳ Verifying OTP...")
        
        user_key = user_sessions[user_id]["user_key"]
        data_key = user_sessions[user_id]["data_key"]
        mobile = user_sessions[user_id]["mobile"]
        
        user_sessions[user_id]["state"] = "verifying_otp"
        token = await verify_otp(user_key, data_key, otp)
        if not token:
            user_sessions[user_id] = {"state": "idle"}
            await update.message.reply_text("❌ <b>OTP verification failed.</b> Please try again.", parse_mode="HTML")
            return
        
        await update.message.reply_text("✅ <b>OTP Verified!</b>", parse_mode="HTML")
        
        await update.message.reply_text("🎡 Spinning wheel...")
        reward = await spin_wheel(user_key, data_key, token)
        if not reward:
            user_sessions[user_id] = {"state": "idle"}
            await update.message.reply_text("⚠️ Temporary error occurred.\n\nPlease try again.")
            return
        
        await update.message.reply_text("💰 Claiming reward...")
        if await claim_reward(user_key, data_key, token):
            masked = mask_mobile(mobile)
            
            # ===== NEW REWARD SUCCESS MESSAGE =====
            success_msg = f"""🎉 <b>Congratulations!</b> 🎉

📱 <b>Number:</b> {masked}
🎁 <b>Aapka Reward:</b> {reward}

✨ <i>Enjoy your reward! ❤️</i>"""
            await update.message.reply_text(success_msg, parse_mode="HTML")
        else:
            await update.message.reply_text(
                f"⚠️ <b>Spin me reward aaya but claim failed!</b>\n"
                f"Reward: {reward}",
                parse_mode="HTML"
            )
        
        user_sessions[user_id] = {"state": "idle"}
        
        first_name = update.effective_user.first_name or "User"
        await send_claim_keyboard(
            chat_id,
            f"""🤖 <b>UJALA CLAIM BOT</b>

👋 <b>Hello, {first_name}!</b>

🟢 <b>Bot Status:</b> Online

🎁 Apna reward claim karein 👇"""
        )
        
        return
    
    # ===== FALLBACK =====
    first_name = update.effective_user.first_name or "User"
    await send_claim_keyboard(
        chat_id,
        f"""🤖 <b>UJALA CLAIM BOT</b>

👋 <b>Hello, {first_name}!</b>

🟢 <b>Bot Status:</b> Online

🎁 Apna reward claim karein 👇"""
    )

# ========== ADMIN PANEL ==========
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ <b>Unauthorized.</b>", parse_mode="HTML")
        return
    
    status_text = get_bot_status_text()
    
    text = f"""🛠 <b>ADMIN DASHBOARD</b>

{status_text}

👤 Admin ID: {user_id}

📢 Required Channels:
"""
    for ch in REQUIRED_CHANNELS:
        text += f"• {ch['chat_id']}\n"

    text += "\n⚡ Ujala Claim Bot"

    keyboard = [
        [InlineKeyboardButton("🟢 Bot ON", callback_data="bot_on"),
         InlineKeyboardButton("🔴 Bot OFF", callback_data="bot_off")],
        [InlineKeyboardButton("⏰ Schedule", callback_data="bot_schedule")],
        [InlineKeyboardButton("🔄 Refresh", callback_data="admin_refresh")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await query.edit_message_text("❌ <b>Unauthorized.</b>", parse_mode="HTML")
        return
    
    data = query.data
    
    if data == "admin_refresh":
        await admin_panel(update, context)
    
    elif data == "bot_on":
        BOT_STATUS["is_on"] = True
        BOT_STATUS["schedule_enabled"] = False
        await query.edit_message_text("🟢 <b>Bot manually ON kar diya.</b>", parse_mode="HTML")
        await admin_panel(update, context)
    
    elif data == "bot_off":
        BOT_STATUS["is_on"] = False
        BOT_STATUS["schedule_enabled"] = False
        await query.edit_message_text("🔴 <b>Bot manually OFF kar diya.</b>", parse_mode="HTML")
        await admin_panel(update, context)
    
    elif data == "bot_schedule":
        BOT_STATUS["schedule_enabled"] = not BOT_STATUS["schedule_enabled"]
        status = "ON" if BOT_STATUS["schedule_enabled"] else "OFF"
        await query.edit_message_text(f"⏰ <b>Schedule {status} kar diya.</b>", parse_mode="HTML")
        await admin_panel(update, context)

async def post_init(application: Application):
    global HTTP_CLIENT
    HTTP_CLIENT = httpx.AsyncClient(headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT, follow_redirects=True)
    logging.info("Shared HTTP client initialized")

async def post_shutdown(application: Application):
    global HTTP_CLIENT
    if HTTP_CLIENT is not None:
        await HTTP_CLIENT.aclose()
        HTTP_CLIENT = None
        logging.info("Shared HTTP client closed")

# ========== MAIN ==========
def main():
    global application
    application = (Application.builder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build())
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_panel))
    application.add_handler(CallbackQueryHandler(check_membership_callback, pattern="check_membership"))
    application.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^admin_"))
    application.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^bot_"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    print("="*60)
    print("🎡 UJALA CLAIM BOT STARTED")
    print(f"👤 Admin IDs: {ADMIN_IDS}")
    print("📢 Required Channels:")
    for ch in REQUIRED_CHANNELS:
        print(f"   • {ch['chat_id']}")
    print("="*60)
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()