import os
import json
import logging
import asyncio
from datetime import datetime, time
import pytz
from PIL import Image
import io
import google.generativeai as genai
from telegram import Update, InputMediaPhoto
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

# ─── CONFIG ───────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ["BOT1_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
ADMIN_ID = int(os.environ.get("ADMIN_ID", "8063827544"))
CHANNEL = os.environ.get("CHANNEL", "@Anime_wallpapers_EXT")
TIMEZONE = pytz.timezone("Asia/Kolkata")  # UTC+05:30

# Post times (24hr)
POST_HOUR = 9
POST_MINUTE = 0

# Low queue alert threshold
LOW_QUEUE_ALERT = 8

# Queue & seen hashes storage
QUEUE_FILE = "queue.json"
SEEN_FILE = "seen.json"
USERS_FILE = "users.json"

# ─── LOGGING ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── GEMINI SETUP ─────────────────────────────────────────────────────────────
genai.configure(api_key=GEMINI_API_KEY)
gemini = genai.GenerativeModel("gemini-1.5-flash")

# ─── STORAGE HELPERS ──────────────────────────────────────────────────────────
def load_json(path, default):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return default

def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def load_queue():
    return load_json(QUEUE_FILE, [])

def save_queue(q):
    save_json(QUEUE_FILE, q)

def load_seen():
    return set(load_json(SEEN_FILE, []))

def save_seen(s):
    save_json(SEEN_FILE, list(s))

def load_users():
    users = load_json(USERS_FILE, [ADMIN_ID])
    if ADMIN_ID not in users:
        users.append(ADMIN_ID)
    return users

def save_users(u):
    save_json(USERS_FILE, u)

# ─── AUTH ─────────────────────────────────────────────────────────────────────
def is_allowed(user_id):
    return user_id in load_users()

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
    import hashlib
    return hashlib.md5(image_bytes).hexdigest()

# ─── GEMINI HASHTAG GENERATION ────────────────────────────────────────────────
async def generate_hashtags(image_bytes: bytes) -> str:
    try:
        img = Image.open(io.BytesIO(image_bytes))
        prompt = (
            "Look at this anime/manga profile picture. "
            "Identify: 1) character name if known, 2) anime/manga name, 3) gender, "
            "4) art style. "
            "Return ONLY hashtags separated by spaces. "
            "Always include #pfp #animepfp #matching #animematchingpfp. "
            "Add character name tag, anime name tag, gender tags like #male #female #malexfemale. "
            "No explanation, just hashtags."
        )
        response = gemini.generate_content([prompt, img])
        tags = response.text.strip()
        return tags
    except Exception as e:
        logger.error(f"Gemini error: {e}")
        return "#pfp #animepfp #matching #animematchingpfp #malexfemale"

# ─── CAPTION BUILDER ──────────────────────────────────────────────────────────
def build_caption(hashtags: str) -> str:
    return (
        "⛩✧🦋･ 𝐀𝖓𝖎𝖒𝖊 𝐏𝖋𝐏 & 𝐖𝖆𝖑𝖑𝖕𝖆𝖕𝖊𝖗 ･🦋✧⛩\n"
        "┈──────────┈🔻┈──────────┈\n\n"
        f"𝐓𝐚𝐠𝐬  ~  {hashtags}\n\n"
        "𝐉ᴏɪɴ 𝐅ᴏʀ 𝐌ᴏʀᴇ....! ✨\n"
        f"    ➥ {CHANNEL}"
    )

# ─── POST LOGIC ───────────────────────────────────────────────────────────────
async def post_pair(app, pair: list):
    """Post a pair of 2 matching images as album to channel."""
    img1_bytes = bytes(pair[0]["bytes"])
    img2_bytes = bytes(pair[1]["bytes"])

    hashtags = await generate_hashtags(img1_bytes)
    caption = build_caption(hashtags)

    media = [
        InputMediaPhoto(media=img1_bytes, caption=caption),
        InputMediaPhoto(media=img2_bytes),
    ]

    await app.bot.send_media_group(chat_id=CHANNEL, media=media)
    logger.info("✅ Pair posted to channel!")

async def scheduled_post(app):
    """Load queue, post first pair, notify admin."""
    queue = load_queue()

    if len(queue) < 2:
        await app.bot.send_message(
            chat_id=ADMIN_ID,
            text="⚠️ Queue is empty or has less than 2 images!\nPlease send more matching pairs."
        )
        return

    pair = queue[:2]
    remaining = queue[2:]

    try:
        await post_pair(app, pair)
        save_queue(remaining)

        # Log to admin
        now = datetime.now(TIMEZONE).strftime("%I:%M %p")
        await app.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"✅ Post sent!\n"
                f"📸 2 matching images posted\n"
                f"⏰ Time: {now}\n"
                f"📢 Channel: {CHANNEL}\n"
                f"📦 Remaining in queue: {len(remaining)} images ({len(remaining)//2} pairs)"
            )
        )

        # Low queue alert
        if len(remaining) <= LOW_QUEUE_ALERT:
            await app.bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"⚠️ Queue Alert!\n"
                    f"Only {len(remaining)} matching images left ({len(remaining)//2} pairs)!\n"
                    f"Please send more pfp pairs soon.\n"
                    f"Next post is at {POST_HOUR:02d}:{POST_MINUTE:02d} AM"
                )
            )

    except Exception as e:
        logger.error(f"Post error: {e}")
        await app.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"❌ Failed to post!\nError: {e}"
        )

# ─── SCHEDULER ────────────────────────────────────────────────────────────────
async def scheduler(app):
    """Run forever, post at scheduled time daily."""
    logger.info(f"Scheduler started. Will post daily at {POST_HOUR:02d}:{POST_MINUTE:02d} IST")
    posted_today = False

    while True:
        now = datetime.now(TIMEZONE)
        if now.hour == POST_HOUR and now.minute == POST_MINUTE:
            if not posted_today:
                logger.info("⏰ Scheduled post time!")
                await scheduled_post(app)
                posted_today = True
        else:
            posted_today = False
        await asyncio.sleep(30)

# ─── COMMAND HANDLERS ─────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    await update.message.reply_text(
        "👋 Matching PfP Bot is running!\n\n"
        "📤 Send me 2 images to add a matching pair to the queue.\n\n"
        "Commands:\n"
        "/queue - See queue status\n"
        "/preview - Preview next pair\n"
        "/clear - Clear the queue\n"
        "/nextpost - Next post time\n"
        "/adduser [id] - Add allowed user\n"
        "/removeuser [id] - Remove user\n"
        "/users - List allowed users"
    )

async def cmd_queue(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    queue = load_queue()
    pairs = len(queue) // 2
    singles = len(queue) % 2
    msg = (
        f"📦 Queue Status:\n"
        f"🖼️ Total images: {len(queue)}\n"
        f"👫 Complete pairs: {pairs}\n"
    )
    if singles:
        msg += f"⚠️ {singles} unpaired image waiting for its match!\n"
    await update.message.reply_text(msg)

async def cmd_preview(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    queue = load_queue()
    if len(queue) < 2:
        await update.message.reply_text("❌ No complete pairs in queue!")
        return
    pair = queue[:2]
    await update.message.reply_text("👀 Next pair to be posted:")
    media = [
        InputMediaPhoto(media=bytes(pair[0]["bytes"])),
        InputMediaPhoto(media=bytes(pair[1]["bytes"])),
    ]
    await update.message.reply_media_group(media=media)

async def cmd_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    save_queue([])
    await update.message.reply_text("🗑️ Queue cleared!")

async def cmd_nextpost(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return
    now = datetime.now(TIMEZONE)
    next_post = now.replace(hour=POST_HOUR, minute=POST_MINUTE, second=0, microsecond=0)
    if now >= next_post:
        from datetime import timedelta
        next_post += timedelta(days=1)
    diff = next_post - now
    hours, rem = divmod(int(diff.total_seconds()), 3600)
    minutes = rem // 60
    await update.message.reply_text(
        f"⏰ Next post: {next_post.strftime('%I:%M %p')} IST\n"
        f"🕐 In: {hours}h {minutes}m"
    )

async def cmd_adduser(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        uid = int(ctx.args[0])
        users = load_users()
        if uid not in users:
            users.append(uid)
            save_users(users)
            await update.message.reply_text(f"✅ User {uid} added!")
        else:
            await update.message.reply_text("ℹ️ User already exists!")
    except:
        await update.message.reply_text("Usage: /adduser 123456789")

async def cmd_removeuser(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
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
            await update.message.reply_text("ℹ️ User not found!")
    except:
        await update.message.reply_text("Usage: /removeuser 123456789")

async def cmd_users(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    users = load_users()
    await update.message.reply_text(f"👥 Allowed users:\n" + "\n".join(str(u) for u in users))

# ─── PHOTO HANDLER ────────────────────────────────────────────────────────────
async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update.effective_user.id):
        return

    photo = update.message.photo[-1]
    file = await ctx.bot.get_file(photo.file_id)
    img_bytes = await file.download_as_bytearray()
    img_bytes = bytes(img_bytes)

    # Duplicate check
    seen = load_seen()
    h = image_hash(img_bytes)
    if h in seen:
        await update.message.reply_text("⚠️ This image is already in the system! Skipping duplicate.")
        return

    # Auto crop to 1:1
    img_bytes = crop_to_square(img_bytes)
    h = image_hash(img_bytes)
    seen.add(h)
    save_seen(seen)

    # Add to queue
    queue = load_queue()
    queue.append({"bytes": list(img_bytes)})
    save_queue(queue)

    pairs = len(queue) // 2
    singles = len(queue) % 2

    msg = f"✅ Image added to queue!\n📦 Total: {len(queue)} images ({pairs} pairs)"
    if singles:
        msg += "\n⏳ Waiting for matching pair..."
    else:
        msg += "\n👫 Pair complete!"
    await update.message.reply_text(msg)

# ─── MAIN ─────────────────────────────────────────────────────────────────────
async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("queue", cmd_queue))
    app.add_handler(CommandHandler("preview", cmd_preview))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("nextpost", cmd_nextpost))
    app.add_handler(CommandHandler("adduser", cmd_adduser))
    app.add_handler(CommandHandler("removeuser", cmd_removeuser))
    app.add_handler(CommandHandler("users", cmd_users))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    await app.initialize()
    await app.start()
    await app.updater.start_polling(allowed_updates=Update.ALL_TYPES)

    logger.info("🤖 Matching PfP Bot started!")
    await scheduler(app)

if __name__ == "__main__":
    asyncio.run(main())
