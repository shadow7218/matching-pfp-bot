import os
import logging
import asyncio
import json
import base64
import hashlib
from datetime import datetime
import pytz
from dotenv import load_dotenv
from PIL import Image
import io
import httpx
from telegram import Update, InputMediaPhoto
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

load_dotenv()

# ─── CONFIG ───────────────────────────────────────────────────────────────────
BOT_TOKEN = os.getenv("BOT1_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
CHANNEL_ID = os.getenv("CHANNEL_ID", "@Anime_wallpapers_EXT")
ADMIN_ID = int(os.getenv("ADMIN_ID", "8063827544"))
TIMEZONE = "Asia/Kolkata"
POST_HOUR = 9
POST_MINUTE = 0
LOW_QUEUE_ALERT = 8

QUEUE_FILE = "./queue.json"
SEEN_FILE = "./seen.json"
USERS_FILE = "./users.json"

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── STORAGE ──────────────────────────────────────────────────────────────────
def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def load_queue(): return load_json(QUEUE_FILE, [])
def save_queue(q): save_json(QUEUE_FILE, q)
def load_seen(): return set(load_json(SEEN_FILE, []))
def save_seen(s): save_json(SEEN_FILE, list(s))

def load_users():
    users = load_json(USERS_FILE, [ADMIN_ID])
    if ADMIN_ID not in users:
        users.append(ADMIN_ID)
    return users

def save_users(u): save_json(USERS_FILE, u)
def is_allowed(uid): return uid in load_users()

# ─── IMAGE HELPERS ────────────────────────────────────────────────────────────
def crop_to_square(image_bytes: bytes) -> bytes:
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    if w == h:
        return image_bytes
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=95)
    return out.getvalue()

def image_hash(image_bytes: bytes) -> str:
    return hashlib.md5(image_bytes).hexdigest()

# ─── GEMINI HASHTAGS ──────────────────────────────────────────────────────────
async def generate_hashtags(image_bytes: bytes) -> str:
    if not GEMINI_API_KEY:
        return "#matching #animematchingpfp #malexfemale #pfp #animepfp"
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img.thumbnail((512, 512))
        buffered = io.BytesIO()
        img.convert("RGB").save(buffered, format="JPEG", quality=70)
        base64_image = base64.b64encode(buffered.getvalue()).decode("utf-8")

        payload = {
            "contents": [{"parts": [
                {"text": "Look at this anime/manga profile picture. Return ONLY hashtags separated by spaces. Always include #pfp #animepfp #matching #animematchingpfp. Add character name tag if known, anime name tag, gender tags like #male #female #malexfemale. No explanation, just hashtags."},
                {"inline_data": {"mime_type": "image/jpeg", "data": base64_image}}
            ]}],
            "generationConfig": {"maxOutputTokens": 100}
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{GEMINI_API_URL}?key={GEMINI_API_KEY}",
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=30.0
            )
            response.raise_for_status()
            result = response.json()

        if "candidates" not in result or not result["candidates"]:
            return "#matching #animematchingpfp #malexfemale #pfp #animepfp"

        tags = result["candidates"][0]["content"]["parts"][0]["text"].strip()
        return tags
    except Exception as e:
        logger.warning(f"Gemini error: {e}")
        return "#matching #animematchingpfp #malexfemale #pfp #animepfp"

# ─── CAPTION ──────────────────────────────────────────────────────────────────
def build_caption(hashtags: str) -> str:
    return (
        "⛩✧🦋･ 𝐀𝖓𝖎𝖒𝖊 𝐏𝖋𝐏 & 𝐖𝖆𝖑𝖑𝖕𝖆𝖕𝖊𝖗 ･🦋✧⛩\n"
        "┈──────────┈🔻┈──────────┈\n\n"
        "𝐓𝐚𝐠𝐬  ~  " + hashtags + "\n\n"
        "𝐉ᴏɪɴ 𝐅ᴏʀ 𝐌ᴏʀᴇ....! ✨\n"
        "    ➥ " + CHANNEL_ID
    )

# ─── CORE POST FUNCTION ───────────────────────────────────────────────────────
async def do_post(app):
    queue = load_queue()
    if len(queue) < 2:
        await app.bot.send_message(
            chat_id=ADMIN_ID,
            text="⚠️ Queue has less than 2 images!\nPlease send more matching pairs."
        )
        return False

    pair = queue[:2]
    remaining = queue[2:]

    img1 = bytes(pair[0]["bytes"])
    img2 = bytes(pair[1]["bytes"])

    try:
        hashtags = await generate_hashtags(img1)
        caption = build_caption(hashtags)

        # Send as album - caption goes on FIRST image only
        media = [
            InputMediaPhoto(media=img1, caption=caption, parse_mode=None),
            InputMediaPhoto(media=img2),
        ]
        await app.bot.send_media_group(chat_id=CHANNEL_ID, media=media)
        save_queue(remaining)

        now = datetime.now(pytz.timezone(TIMEZONE)).strftime("%I:%M %p")
        await app.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"✅ Post sent!\n"
                f"📸 2 matching images posted\n"
                f"⏰ Time: {now}\n"
                f"📢 Channel: {CHANNEL_ID}\n"
                f"📦 Remaining: {len(remaining)} images ({len(remaining)//2} pairs)"
            )
        )

        if 0 < len(remaining) <= LOW_QUEUE_ALERT:
            await app.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"⚠️ Queue Alert!\n"
                    f"Only {len(remaining)} images left ({len(remaining)//2} pairs)!\n"
                    f"Please send more pfp pairs soon.\n"
                    f"Next post at {POST_HOUR:02d}:{POST_MINUTE:02d} IST"
                )
            )
        return True
    except Exception as e:
        logger.error(f"Post error: {e}")
        await app.bot.send_message(chat_id=ADMIN_ID, text=f"❌ Failed to post!\nError: {e}")
        return False

# ─── SCHEDULER ────────────────────────────────────────────────────────────────
async def scheduler(app):
    logger.info(f"Scheduler started. Posting daily at {POST_HOUR:02d}:{POST_MINUTE:02d} IST")
    posted_today = False
    while True:
        now = datetime.now(pytz.timezone(TIMEZONE))
        if now.hour == POST_HOUR and now.minute == POST_MINUTE:
            if not posted_today:
                logger.info("⏰ Time to post!")
                await do_post(app)
                posted_today = True
        else:
            posted_today = False
        await asyncio.sleep(30)

# ─── COMMANDS ─────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text(
        "👋 Matching PfP Bot running!\n\n"
        "Send me 2 images to add a matching pair.\n\n"
        "/queue - Queue status\n"
        "/preview - Preview next pair\n"
        "/post - Post instantly to channel\n"
        "/clear - Clear queue\n"
        "/nextpost - Next post time\n"
        "/adduser [id] - Add user\n"
        "/removeuser [id] - Remove user\n"
        "/users - List users"
    )

async def cmd_queue(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    queue = load_queue()
    pairs = len(queue) // 2
    singles = len(queue) % 2
    msg = f"📦 Queue:\n🖼️ Images: {len(queue)}\n👫 Pairs: {pairs}"
    if singles: msg += f"\n⚠️ 1 image waiting for match!"
    await update.message.reply_text(msg)

async def cmd_preview(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    queue = load_queue()
    if len(queue) < 2:
        await update.message.reply_text("❌ No complete pairs in queue!")
        return
    pair = queue[:2]
    await update.message.reply_text("👀 Next pair:")
    media = [
        InputMediaPhoto(media=bytes(pair[0]["bytes"])),
        InputMediaPhoto(media=bytes(pair[1]["bytes"])),
    ]
    await update.message.reply_media_group(media=media)

async def cmd_post(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    await update.message.reply_text("📤 Posting to channel now...")
    success = await do_post(ctx.application)
    if success:
        await update.message.reply_text("✅ Posted successfully!")

async def cmd_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    save_queue([])
    await update.message.reply_text("🗑️ Queue cleared!")

async def cmd_nextpost(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return
    from datetime import timedelta
    now = datetime.now(pytz.timezone(TIMEZONE))
    next_post = now.replace(hour=POST_HOUR, minute=POST_MINUTE, second=0, microsecond=0)
    if now >= next_post:
        next_post += timedelta(days=1)
    diff = next_post - now
    hours, rem = divmod(int(diff.total_seconds()), 3600)
    minutes = rem // 60
    await update.message.reply_text(
        f"⏰ Next post: {next_post.strftime('%I:%M %p')} IST\n🕐 In: {hours}h {minutes}m"
    )

async def cmd_adduser(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        uid = int(ctx.args[0])
        users = load_users()
        if uid not in users:
            users.append(uid)
            save_users(users)
            await update.message.reply_text(f"✅ User {uid} added!")
        else:
            await update.message.reply_text("ℹ️ Already exists!")
    except:
        await update.message.reply_text("Usage: /adduser 123456789")

async def cmd_removeuser(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        uid = int(ctx.args[0])
        if uid == ADMIN_ID:
            await update.message.reply_text("❌ Cannot remove admin!")
            return
        users = load_users()
        if uid in users:
            users.remove(uid)
            save_users(users)
            await update.message.reply_text(f"✅ User {uid} removed!")
        else:
            await update.message.reply_text("ℹ️ Not found!")
    except:
        await update.message.reply_text("Usage: /removeuser 123456789")

async def cmd_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    users = load_users()
    await update.message.reply_text("👥 Allowed users:\n" + "\n".join(str(u) for u in users))

# ─── PHOTO HANDLER ────────────────────────────────────────────────────────────
async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id): return

    photo = update.message.photo[-1]
    file = await ctx.bot.get_file(photo.file_id)
    img_bytes = await file.download_as_bytearray()
    img_bytes = bytes(img_bytes)

    seen = load_seen()
    h = image_hash(img_bytes)
    if h in seen:
        await update.message.reply_text("⚠️ Duplicate image! Skipping.")
        return

    img_bytes = crop_to_square(img_bytes)
    h = image_hash(img_bytes)
    seen.add(h)
    save_seen(seen)

    queue = load_queue()
    queue.append({"bytes": list(img_bytes)})
    save_queue(queue)

    pairs = len(queue) // 2
    singles = len(queue) % 2
    msg = f"✅ Added!\n📦 Total: {len(queue)} images ({pairs} pairs)"
    if singles: msg += "\n⏳ Waiting for matching pair..."
    else: msg += "\n👫 Pair complete!"
    await update.message.reply_text(msg)

# ─── MAIN ─────────────────────────────────────────────────────────────────────
async def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("preview", cmd_preview))
    app.add_handler(CommandHandler("post", cmd_post))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("nextpost", cmd_nextpost))
    app.add_handler(CommandHandler("adduser", cmd_adduser))
    app.add_handler(CommandHandler("removeuser", cmd_removeuser))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    logger.info("🤖 Matching PfP Bot started!")
    await scheduler(app)

if __name__ == "__main__":
    asyncio.run(main())
