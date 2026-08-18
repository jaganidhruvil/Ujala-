import requests
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
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# ========== CONFIGURATION ==========
BOT_TOKEN = "8733508762:AAG8tJ_uxga6xlag34zGQ5fQH_NGrSqWprg"
ADMIN_IDS = [8739344756]

BASE_URL = "https://www.ujalahappiestonam.com/api/users"
MASTER_KEY = "660395654"
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

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.ujalahappiestonam.com",
    "Referer": "https://www.ujalahappiestonam.com/",
})

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

def download_image_from_url(url: str) -> bytes:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.content

def create_user():
    r = session.post(f"{BASE_URL}", json={"masterKey": MASTER_KEY}, timeout=10)
    data = r.json()
    decoded, ok = decrypt_resp(data.get("resp", ""))
    if not ok or decoded.get("statusCode") != 200:
        return None, None
    return str(decoded["userKey"]), decoded["dataKey"]

def send_otp(user_key, data_key, name, mobile, image_bytes, code=PRODUCT_CODE, city=CITY):
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
    
    r = session.post(
        f"{BASE_URL}/getOTP/{user_key}?t={t}",
        data=form_data,
        files=files,
        timeout=15
    )
    resp_json = r.json()
    decoded, ok = decrypt_resp(resp_json.get("resp", ""))
    return ok and decoded.get("statusCode") == 200

def verify_otp(user_key, data_key, otp):
    t = get_timestamp()
    payload = {"otp": otp, "userKey": int(user_key), "t": t}
    data_value = generate_signature_data(payload, user_key, data_key)
    u, a, g = data_value.split(".", 2)
    body = f"userKey={user_key}&data={urllib.parse.quote_plus(u)}.{urllib.parse.quote_plus(a)}.{urllib.parse.quote_plus(g)}"
    r = session.post(
        f"{BASE_URL}/verifyOTP/{user_key}?t={t}",
        data=body,
        headers={"content-type": "application/x-www-form-urlencoded; charset=UTF-8"},
        timeout=10
    )
    decoded, ok = decrypt_resp(r.json().get("resp", ""))
    if ok and decoded.get("statusCode") == 200:
        return decoded.get("token")
    return None

def spin_wheel(user_key, data_key, token):
    t = get_timestamp()
    payload = {"userKey": int(user_key), "t": t}
    data_value = generate_signature_data(payload, user_key, data_key)
    u, a, g = data_value.split(".", 2)
    body = f"userKey={user_key}&data={urllib.parse.quote_plus(u)}.{urllib.parse.quote_plus(a)}.{urllib.parse.quote_plus(g)}"
    headers = {
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "authorization": f"Bearer {token}"
    }
    r = session.post(
        f"{BASE_URL}/speenTheWheel/{user_key}?t={t}",
        data=body,
        headers=headers,
        timeout=10
    )
    decoded, ok = decrypt_resp(r.json().get("resp", ""))
    if ok and decoded.get("statusCode") == 200:
        return decoded.get('reward', 'Unknown')
    return None

def claim_reward(user_key, data_key, token):
    t = get_timestamp()
    payload = {"userKey": int(user_key), "t": t}
    data_value = generate_signature_data(payload, user_key, data_key)
    u, a, g = data_value.split(".", 2)
    body = f"userKey={user_key}&data={urllib.parse.quote_plus(u)}.{urllib.parse.quote_plus(a)}.{urllib.parse.quote_plus(g)}"
    headers = {
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "authorization": f"Bearer {token}"
    }
    r = session.post(
        f"{BASE_URL}/claimNow/{user_key}?t={t}",
        data=body,
        headers=headers,
        timeout=10
    )
    decoded, ok = decrypt_resp(r.json().get("resp", ""))
    if ok and decoded.get("statusCode") == 200:
        return True
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
async def check_membership(user_id, bot):
    try:
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

# ========== SIMPLE REPLY KEYBOARDS ==========
def get_main_keyboard():
    keyboard = [[KeyboardButton("🎡 Claim Reward")]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_otp_keyboard():
    keyboard = [[KeyboardButton("❌ Cancel")]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ========== TELEGRAM HANDLERS ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    first_name = update.effective_user.first_name or "User"
    
    if is_admin(user_id):
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"""🤖 UJALA CLAIM BOT

👋 Hello, {first_name}!

🟢 Bot Status: Online

🎁 Apna reward claim karein 👇

🛠 Admin panel: /admin""",
            reply_markup=get_main_keyboard()
        )
        user_sessions[user_id] = {"state": "idle"}
        return
    
    if not is_bot_on():
        await context.bot.send_message(
            chat_id=chat_id,
            text="🔴 Bot abhi band hai."
        )
        return
    
    if not await check_membership(user_id, context.bot):
        await context.bot.send_message(
            chat_id=chat_id,
            text=get_force_join_text(),
            reply_markup=get_force_join_keyboard()
        )
        return
    
    user_sessions[user_id] = {"state": "idle"}
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"""🤖 UJALA CLAIM BOT

👋 Hello, {first_name}!

🟢 Bot Status: Online

🎁 Apna reward claim karein 👇""",
        reply_markup=get_main_keyboard()
    )

async def check_membership_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    first_name = update.effective_user.first_name or "User"
    
    if not is_bot_on():
        await context.bot.send_message(
            chat_id=chat_id,
            text="🔴 Bot abhi band hai."
        )
        return
    
    if await check_membership(user_id, context.bot):
        await context.bot.send_message(
            chat_id=chat_id,
            text="✅ Access Granted\n\nAb bot use kar sakte ho."
        )
        user_sessions[user_id] = {"state": "idle"}
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"""🤖 UJALA CLAIM BOT

👋 Hello, {first_name}!

🟢 Bot Status: Online

🎁 Apna reward claim karein 👇""",
            reply_markup=get_main_keyboard()
        )
    else:
        await context.bot.send_message(
            chat_id=chat_id,
            text=get_force_join_text(),
            reply_markup=get_force_join_keyboard()
        )

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    text = update.message.text.strip()
    
    if not is_admin(user_id):
        if not is_bot_on():
            await context.bot.send_message(
                chat_id=chat_id,
                text="🔴 Bot abhi band hai."
            )
            return
        if not await check_membership(user_id, context.bot):
            await context.bot.send_message(
                chat_id=chat_id,
                text=get_force_join_text(),
                reply_markup=get_force_join_keyboard()
            )
            return
    
    if user_id not in user_sessions:
        user_sessions[user_id] = {"state": "idle"}
    
    state = user_sessions[user_id].get("state", "idle")
    
    if text == "🎡 Claim Reward" and state == "idle":
        await context.bot.send_message(
            chat_id=chat_id,
            text="📱 Apna 10-digit mobile number daalo\n(without +91)\n\nExample: 9876543210"
        )
        user_sessions[user_id]["state"] = "waiting_mobile"
        return
    
    if text == "❌ Cancel":
        if state in ["waiting_mobile", "waiting_otp"]:
            user_sessions[user_id] = {"state": "idle"}
            first_name = update.effective_user.first_name or "User"
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"""🤖 UJALA CLAIM BOT

👋 Hello, {first_name}!

🟢 Bot Status: Online

🎁 Apna reward claim karein 👇""",
                reply_markup=get_main_keyboard()
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text="ℹ️ Cancel karne ke liye kuch nahi hai."
            )
        return
    
    if state == "waiting_mobile":
        mobile = re.sub(r'[\s\+]', '', text)
        if mobile.startswith("91"):
            mobile = mobile[2:]
        
        if len(mobile) != 10 or not mobile.isdigit():
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Invalid mobile number\nSirf 10 digits daalo.\nExample: 9876543210"
            )
            return
        
        await context.bot.send_message(
            chat_id=chat_id,
            text="⏳ Processing..."
        )
        
        name = generate_random_name()
        user_sessions[user_id]["mobile"] = mobile
        user_sessions[user_id]["name"] = name
        
        try:
            image_bytes = download_image_from_url(IMAGE_URL)
            user_key, data_key = create_user()
            if not user_key:
                first_name = update.effective_user.first_name or "User"
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"""🤖 UJALA CLAIM BOT

👋 Hello, {first_name}!

🟢 Bot Status: Online

🎁 Apna reward claim karein 👇""",
                    reply_markup=get_main_keyboard()
                )
                user_sessions[user_id] = {"state": "idle"}
                return
            
            user_sessions[user_id]["user_key"] = user_key
            user_sessions[user_id]["data_key"] = data_key
            
            if not send_otp(user_key, data_key, name, mobile, image_bytes):
                first_name = update.effective_user.first_name or "User"
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"""🤖 UJALA CLAIM BOT

👋 Hello, {first_name}!

🟢 Bot Status: Online

🎁 Apna reward claim karein 👇""",
                    reply_markup=get_main_keyboard()
                )
                user_sessions[user_id] = {"state": "idle"}
                return
            
            user_sessions[user_id]["state"] = "waiting_otp"
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"✅ OTP send kar diya {mobile} pe!\n\n📩 Ab 6-digit OTP daalo\nExample: 123456",
                reply_markup=get_otp_keyboard()
            )
            
        except Exception as e:
            logger.error(f"Mobile flow error: {e}")
            first_name = update.effective_user.first_name or "User"
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"""🤖 UJALA CLAIM BOT

👋 Hello, {first_name}!

🟢 Bot Status: Online

🎁 Apna reward claim karein 👇""",
                reply_markup=get_main_keyboard()
            )
            user_sessions[user_id] = {"state": "idle"}
        
        return
    
    if state == "waiting_otp":
        otp = re.sub(r'[\s\+]', '', text)
        
        if len(otp) != 6 or not otp.isdigit():
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Invalid OTP\nSirf 6 digits daalo.\nExample: 123456"
            )
            return
        
        await context.bot.send_message(
            chat_id=chat_id,
            text="⏳ Verifying OTP..."
        )
        
        user_key = user_sessions[user_id]["user_key"]
        data_key = user_sessions[user_id]["data_key"]
        mobile = user_sessions[user_id]["mobile"]
        
        token = verify_otp(user_key, data_key, otp)
        if not token:
            await context.bot.send_message(
                chat_id=chat_id,
                text="❌ Invalid OTP! Dobara try karo."
            )
            return
        
        await context.bot.send_message(
            chat_id=chat_id,
            text="✅ OTP Verified!"
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text="🎡 Spinning wheel..."
        )
        
        reward = spin_wheel(user_key, data_key, token)
        if not reward:
            first_name = update.effective_user.first_name or "User"
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"""🤖 UJALA CLAIM BOT

👋 Hello, {first_name}!

🟢 Bot Status: Online

🎁 Apna reward claim karein 👇""",
                reply_markup=get_main_keyboard()
            )
            user_sessions[user_id] = {"state": "idle"}
            return
        
        await context.bot.send_message(
            chat_id=chat_id,
            text="💰 Claiming reward..."
        )
        if claim_reward(user_key, data_key, token):
            masked = mask_mobile(mobile)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"""🎉 Congratulations! 🎉

📱 Number: {masked}
🎁 Aapka Reward: {reward}

✨ Enjoy your reward! ❤️"""
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"⚠️ Spin me reward aaya but claim failed!\nReward: {reward}"
            )
        
        user_sessions[user_id] = {"state": "idle"}
        first_name = update.effective_user.first_name or "User"
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"""🤖 UJALA CLAIM BOT

👋 Hello, {first_name}!

🟢 Bot Status: Online

🎁 Apna reward claim karein 👇""",
            reply_markup=get_main_keyboard()
        )
        return
    
    first_name = update.effective_user.first_name or "User"
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"""🤖 UJALA CLAIM BOT

👋 Hello, {first_name}!

🟢 Bot Status: Online

🎁 Apna reward claim karein 👇""",
        reply_markup=get_main_keyboard()
    )

# ========== ADMIN PANEL ==========
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Unauthorized."
        )
        return
    
    status_text = get_bot_status_text()
    text = f"""🛠 ADMIN DASHBOARD

{status_text}

👤 Admin ID: {user_id}

📢 Required Channels:
"""
    for ch in REQUIRED_CHANNELS:
        text += f"• {ch['chat_id']}\n"
    text += "\n⚡ Ujala Claim Bot"
    
    keyboard = [
        [InlineKeyboardButton("🟢 Bot ON", callback_data="bot_on"), InlineKeyboardButton("🔴 Bot OFF", callback_data="bot_off")],
        [InlineKeyboardButton("⏰ Schedule", callback_data="bot_schedule")],
        [InlineKeyboardButton("🔄 Refresh", callback_data="admin_refresh")]
    ]
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def admin_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await query.edit_message_text("❌ Unauthorized.")
        return
    
    data = query.data
    if data == "admin_refresh":
        await admin_panel(update, context)
    elif data == "bot_on":
        BOT_STATUS["is_on"] = True
        BOT_STATUS["schedule_enabled"] = False
        await query.edit_message_text("🟢 Bot manually ON kar diya.")
        await admin_panel(update, context)
    elif data == "bot_off":
        BOT_STATUS["is_on"] = False
        BOT_STATUS["schedule_enabled"] = False
        await query.edit_message_text("🔴 Bot manually OFF kar diya.")
        await admin_panel(update, context)
    elif data == "bot_schedule":
        BOT_STATUS["schedule_enabled"] = not BOT_STATUS["schedule_enabled"]
        status = "ON" if BOT_STATUS["schedule_enabled"] else "OFF"
        await query.edit_message_text(f"⏰ Schedule {status} kar diya.")
        await admin_panel(update, context)

# ========== MAIN ==========
def main():
    # Create application
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CallbackQueryHandler(check_membership_callback, pattern="check_membership"))
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^admin_"))
    app.add_handler(CallbackQueryHandler(admin_callback_handler, pattern="^bot_"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    print("="*60)
    print("🎡 UJALA CLAIM BOT STARTED")
    print(f"👤 Admin ID: {ADMIN_IDS[0]}")
    print("📢 Required Channels:")
    for ch in REQUIRED_CHANNELS:
        print(f"   • {ch['chat_id']}")
    print("="*60)
    
    # Run bot
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
