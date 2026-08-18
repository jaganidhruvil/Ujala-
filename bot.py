import asyncio
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
import os
from datetime import datetime
import httpx
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# ========== CONFIGURATION (Environment Variables) ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8620716243:AAEG20UBhlyfdGHmC32umyK9qeCx2AGIChg")
ADMIN_IDS = [int(os.environ.get("ADMIN_ID", "8739344756"))]

BASE_URL = "https://www.ujalahappiestonam.com/api/users"
MASTER_KEY = os.environ.get("UJALA_MASTER_KEY", "660395654")
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
    {"chat_id": "@KALUASC", "invite_url": "https://t.me/KALUASC"},
    {"chat_id": "@vishalxupdate", "invite_url": "https://t.me/vishalxupdate"},
    {"chat_id": "@X00MTSxKIDS", "invite_url": "https://t.me/X00MTSxKIDS"},
    {"chat_id": "@axxuloots", "invite_url": "https://t.me/axxuloots"}
]

# ========== LOGGING ==========
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ========== GLOBAL STATE ==========
user_sessions = {}

# ========== HTTPX CLIENT ==========
client = httpx.AsyncClient(
    timeout=httpx.Timeout(15.0, connect=10.0),
    limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
    headers={
        "User-Agent": "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.ujalahappiestonam.com",
        "Referer": "https://www.ujalahappiestonam.com/",
    }
)

# ========== CORE FUNCTIONS ==========
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

async def download_image_from_url_async(url: str) -> bytes:
    try:
        resp = await client.get(url, timeout=15.0)
        resp.raise_for_status()
        return resp.content
    except Exception as e:
        logger.error(f"Image download error: {e}")
        raise

async def create_user_async():
    try:
        resp = await client.post(f"{BASE_URL}", json={"masterKey": MASTER_KEY}, timeout=10.0)
        data = resp.json()
        decoded, ok = decrypt_resp(data.get("resp", ""))
        if not ok or decoded.get("statusCode") != 200:
            return None, None
        return str(decoded["userKey"]), decoded["dataKey"]
    except Exception as e:
        logger.error(f"Create user error: {e}")
        return None, None

async def send_otp_async(user_key, data_key, name, mobile, image_bytes, code=PRODUCT_CODE, city=CITY):
    try:
        t = get_timestamp()
        payload = {
            "name": name,
            "mobile": mobile,
            "email": "",
            "city": city,
            "code": code,
            "agreed1": "Yes",
            "agreed2": "Yes",
            "userKey": int(user_key),
            "t": t
        }
        data_value = generate_signature_data(payload, user_key, data_key)
        files = {"pack": ("pack.jpg", io.BytesIO(image_bytes), "image/jpeg")}
        form_data = {"t": str(t), "userKey": user_key, "data": data_value}
        resp = await client.post(
            f"{BASE_URL}/getOTP/{user_key}?t={t}",
            data=form_data,
            files=files,
            timeout=15.0
        )
        resp_json = resp.json()
        decoded, ok = decrypt_resp(resp_json.get("resp", ""))
        return ok and decoded.get("statusCode") == 200
    except Exception as e:
        logger.error(f"Send OTP error: {e}")
        return False

async def verify_otp_async(user_key, data_key, otp):
    try:
        t = get_timestamp()
        payload = {"otp": otp, "userKey": int(user_key), "t": t}
        data_value = generate_signature_data(payload, user_key, data_key)
        u, a, g = data_value.split(".", 2)
        body = f"userKey={user_key}&data={urllib.parse.quote_plus(u)}.{urllib.parse.quote_plus(a)}.{urllib.parse.quote_plus(g)}"
        headers = {"content-type": "application/x-www-form-urlencoded; charset=UTF-8"}
        resp = await client.post(
            f"{BASE_URL}/verifyOTP/{user_key}?t={t}",
            data=body,
            headers=headers,
            timeout=10.0
        )
        decoded, ok = decrypt_resp(resp.json().get("resp", ""))
        if ok and decoded.get("statusCode") == 200:
            return decoded.get("token")
        return None
    except Exception as e:
        logger.error(f"Verify OTP error: {e}")
        return None

async def spin_wheel_async(user_key, data_key, token):
    try:
        t = get_timestamp()
        payload = {"userKey": int(user_key), "t": t}
        data_value = generate_signature_data(payload, user_key, data_key)
        u, a, g = data_value.split(".", 2)
        body = f"userKey={user_key}&data={urllib.parse.quote_plus(u)}.{urllib.parse.quote_plus(a)}.{urllib.parse.quote_plus(g)}"
        headers = {
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "authorization": f"Bearer {token}"
        }
        resp = await client.post(
            f"{BASE_URL}/speenTheWheel/{user_key}?t={t}",
            data=body,
            headers=headers,
            timeout=10.0
        )
        decoded, ok = decrypt_resp(resp.json().get("resp", ""))
        if ok and decoded.get("statusCode") == 200:
            return decoded.get('reward', 'Unknown')
        return None
    except Exception as e:
        logger.error(f"Spin wheel error: {e}")
        return None

async def claim_reward_async(user_key, data_key, token):
    try:
        t = get_timestamp()
        payload = {"userKey": int(user_key), "t": t}
        data_value = generate_signature_data(payload, user_key, data_key)
        u, a, g = data_value.split(".", 2)
        body = f"userKey={user_key}&data={urllib.parse.quote_plus(u)}.{urllib.parse.quote_plus(a)}.{urllib.parse.quote_plus(g)}"
        headers = {
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "authorization": f"Bearer {token}"
        }
        resp = await client.post(
            f"{BASE_URL}/claimNow/{user_key}?t={t}",
            data=body,
            headers=headers,
            timeout=10.0
        )
        decoded, ok = decrypt_resp(resp.json().get("resp", ""))
        if ok and decoded.get("statusCode") == 200:
            return True
        return False
    except Exception as e:
        logger.error(f"Claim reward error: {e}")
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

# ========== CUSTOM REPLY KEYBOARD ==========
async def send_claim_keyboard(chat_id, text):
    keyboard_json = {
        "keyboard": [
            [{"text": "🎡 Claim Reward", "style": "primary", "icon_custom_emoji_id": "5471984997361523302"}]
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
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            await http_client.post(url, json=payload)
    except Exception as e:
        logger.error(f"send_claim_keyboard error: {e}")

async def send_cancel_keyboard(chat_id, text):
    keyboard_json = {
        "keyboard": [
            [{"text": "❌ Cancel", "style": "danger", "icon_custom_emoji_id": "5382224089295365367"}]
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
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            await http_client.post(url, json=payload)
    except Exception as e:
        logger.error(f"send_cancel_keyboard error: {e}")

# ========== TELEGRAM HANDLERS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    first_name = update.effective_user.first_name or "User"
    
    if is_admin(user_id):
        await send_claim_keyboard(
            chat_id,
            f"""🤖 <b>UJALA CLAIM BOT</b>\n\n👋 <b>Hello, {first_name}!</b>\n\n🟢 <b>Bot Status:</b> Online\n\n🎁 Apna reward claim karein 👇\n\n🛠 Admin panel: /admin"""
        )
        user_sessions[user_id] = {"state": "idle"}
        return
    
    if not is_bot_on():
        await update.message.reply_text("🔴 <b>Bot abhi band hai.</b>", parse_mode="HTML")
        return
    
    if not await check_membership(user_id):
        await update.message.reply_text(get_force_join_text(), reply_markup=get_force_join_keyboard(), parse_mode="Markdown")
        return
    
    user_sessions[user_id] = {"state": "idle"}
    await send_claim_keyboard(
        chat_id,
        f"""🤖 <b>UJALA CLAIM BOT</b>\n\n👋 <b>Hello, {first_name}!</b>\n\n🟢 <b>Bot Status:</b> Online\n\n🎁 Apna reward claim karein 👇"""
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
        await query.edit_message_text("✅ <b>Access Granted</b>\n\nAb bot use kar sakte ho.", parse_mode="HTML")
        user_sessions[user_id] = {"state": "idle"}
        await send_claim_keyboard(
            chat_id,
            f"""🤖 <b>UJALA CLAIM BOT</b>\n\n👋 <b>Hello, {first_name}!</b>\n\n🟢 <b>Bot Status:</b> Online\n\n🎁 Apna reward claim karein 👇"""
        )
    else:
        await query.edit_message_text(get_force_join_text(), reply_markup=get_force_join_keyboard(), parse_mode="Markdown")

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    
    if not is_admin(user_id):
        if not is_bot_on():
            await update.message.reply_text("🔴 <b>Bot abhi band hai.</b>", parse_mode="HTML")
            return
        if not await check_membership(user_id):
            await update.message.reply_text(get_force_join_text(), reply_markup=get_force_join_keyboard(), parse_mode="Markdown")
            return
    
    if user_id not in user_sessions:
        user_sessions[user_id] = {"state": "idle"}
    
    state = user_sessions[user_id].get("state", "idle")
    
    if text == "🎡 Claim Reward" and state == "idle":
        await update.message.reply_text("📱 <b>Apna 10-digit mobile number daalo</b>\n(without +91)\n\nExample: <code>9876543210</code>", parse_mode="HTML")
        user_sessions[user_id]["state"] = "waiting_mobile"
        return
    
    if text == "❌ Cancel":
        if state in ["waiting_mobile", "waiting_otp"]:
            user_sessions[user_id] = {"state": "idle"}
            first_name = update.effective_user.first_name or "User"
            await send_claim_keyboard(
                chat_id,
                f"""🤖 <b>UJALA CLAIM BOT</b>\n\n👋 <b>Hello, {first_name}!</b>\n\n🟢 <b>Bot Status:</b> Online\n\n🎁 Apna reward claim karein 👇"""
            )
        else:
            await update.message.reply_text("ℹ️ Cancel karne ke liye kuch nahi hai.")
        return
    
    if state == "waiting_mobile":
        mobile = re.sub(r'[\s\+]', '', text)
        if mobile.startswith("91"):
            mobile = mobile[2:]
        
        if len(mobile) != 10 or not mobile.isdigit():
            await update.message.reply_text("❌ <b>Invalid mobile number</b>\nSirf 10 digits daalo.\nExample: <code>9876543210</code>", parse_mode="HTML")
            return
        
        await update.message.reply_text("⏳ Processing...")
        
        name = generate_random_name()
        user_sessions[user_id]["mobile"] = mobile
        user_sessions[user_id]["name"] = name
        
        try:
            image_bytes, (user_key, data_key) = await asyncio.gather(
                download_image_from_url_async(IMAGE_URL),
                create_user_async()
            )
            
            if not user_key:
                first_name = update.effective_user.first_name or "User"
                await send_claim_keyboard(
                    chat_id,
                    f"""🤖 <b>UJALA CLAIM BOT</b>\n\n👋 <b>Hello, {first_name}!</b>\n\n🟢 <b>Bot Status:</b> Online\n\n🎁 Apna reward claim karein 👇"""
                )
                user_sessions[user_id] = {"state": "idle"}
                return
            
            user_sessions[user_id]["user_key"] = user_key
            user_sessions[user_id]["data_key"] = data_key
            
            if not await send_otp_async(user_key, data_key, name, mobile, image_bytes):
                first_name = update.effective_user.first_name or "User"
                await send_claim_keyboard(
                    chat_id,
                    f"""🤖 <b>UJALA CLAIM BOT</b>\n\n👋 <b>Hello, {first_name}!</b>\n\n🟢 <b>Bot Status:</b> Online\n\n🎁 Apna reward claim karein 👇"""
                )
                user_sessions[user_id] = {"state": "idle"}
                return
            
            user_sessions[user_id]["state"] = "waiting_otp"
            await send_cancel_keyboard(
                chat_id,
                f"✅ <b>OTP send kar diya {mobile} pe!</b>\n\n📩 <b>Ab 6-digit OTP daalo</b>\nExample: <code>123456</code>\n\n❌ Cancel karne ke liye red button dabao."
            )
            
        except Exception as e:
            logger.error(f"Mobile flow error: {e}")
            first_name = update.effective_user.first_name or "User"
            await send_claim_keyboard(
                chat_id,
                f"""🤖 <b>UJALA CLAIM BOT</b>\n\n👋 <b>Hello, {first_name}!</b>\n\n🟢 <b>Bot Status:</b> Online\n\n🎁 Apna reward claim karein 👇"""
            )
            user_sessions[user_id] = {"state": "idle"}
        
        return
    
    if state == "waiting_otp":
        otp = re.sub(r'[\s\+]', '', text)
        
        if len(otp) != 6 or not otp.isdigit():
            await update.message.reply_text("❌ <b>Invalid OTP</b>\nSirf 6 digits daalo.\nExample: <code>123456</code>", parse_mode="HTML")
            return
        
        await update.message.reply_text("⏳ Verifying OTP...")
        
        user_key = user_sessions[user_id]["user_key"]
        data_key = user_sessions[user_id]["data_key"]
        mobile = user_sessions[user_id]["mobile"]
        
        token = await verify_otp_async(user_key, data_key, otp)
        if not token:
            await update.message.reply_text("❌ <b>Invalid OTP!</b> Dobara try karo.", parse_mode="HTML")
            return
        
        await update.message.reply_text("✅ <b>OTP Verified!</b>", parse_mode="HTML")
        await update.message.reply_text("🎡 Spinning wheel...")
        
        reward = await spin_wheel_async(user_key, data_key, token)
        if not reward:
            first_name = update.effective_user.first_name or "User"
            await send_claim_keyboard(
                chat_id,
                f"""🤖 <b>UJALA CLAIM BOT</b>\n\n👋 <b>Hello, {first_name}!</b>\n\n🟢 <b>Bot Status:</b> Online\n\n🎁 Apna reward claim karein 👇"""
            )
            user_sessions[user_id] = {"state": "idle"}
            return
        
        await update.message.reply_text("💰 Claiming reward...")
        if await claim_reward_async(user_key, data_key, token):
            masked = mask_mobile(mobile)
            await update.message.reply_text(
                f"""🎉 <b>Congratulations!</b> 🎉\n\n📱 <b>Number:</b> {masked}\n🎁 <b>Aapka Reward:</b> {reward}\n\n✨ <i>Enjoy your reward! ❤️</i>""",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(f"⚠️ <b>Spin me reward aaya but claim failed!</b>\nReward: {reward}", parse_mode="HTML")
        
        user_sessions[user_id] = {"state": "idle"}
        first_name = update.effective_user.first_name or "User"
        await send_claim_keyboard(
            chat_id,
            f"""🤖 <b>UJALA CLAIM BOT</b>\n\n👋 <b>Hello, {first_name}!</b>\n\n🟢 <b>Bot Status:</b> Online\n\n🎁 Apna reward claim karein 👇"""
        )
        return
    
    first_name = update.effective_user.first_name or "User"
    await send_claim_keyboard(
        chat_id,
        f"""🤖 <b>UJALA CLAIM BOT</b>\n\n👋 <b>Hello, {first_name}!</b>\n\n🟢 <b>Bot Status:</b> Online\n\n🎁 Apna reward claim karein 👇"""
    )

# ========== ADMIN PANEL ==========
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await update.message.reply_text("❌ <b>Unauthorized.</b>", parse_mode="HTML")
        return
    
    status_text = get_bot_status_text()
    text = f"""🛠 <b>ADMIN DASHBOARD</b>\n\n{status_text}\n\n👤 Admin ID: {user_id}\n\n📢 Required Channels:\n"""
    for ch in REQUIRED_CHANNELS:
        text += f"• {ch['chat_id']}\n"
    text += "\n⚡ Ujala Claim Bot"
    
    keyboard = [
        [InlineKeyboardButton("🟢 Bot ON", callback_data="bot_on"), InlineKeyboardButton("🔴 Bot OFF", callback_data="bot_off")],
        [InlineKeyboardButton("⏰ Schedule", callback_data="bot_schedule")],
        [InlineKeyboardButton("🔄 Refresh", callback_data="admin_refresh")]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

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

# ========== MAIN ==========
async def main():
    global application
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("admin", admin_panel))
        application.add_handler(CallbackQueryHandler(check_membership_callback, pattern="check_membership"))
        application.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^admin_"))
        application.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^bot_"))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
        
        print("="*60)
        print("🚀 UJALA CLAIM BOT STARTED (Async + HTTPX)")
        print(f"👤 Admin IDs: {ADMIN_IDS}")
        print("📢 Required Channels:")
        for ch in REQUIRED_CHANNELS:
            print(f"   • {ch['chat_id']}")
        print("="*60)
        
        await application.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        await client.aclose()

# ========== ENTRY POINT ==========
if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
