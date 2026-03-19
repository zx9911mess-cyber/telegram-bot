import re
import os
import yt_dlp
import logging
from telegram import Update, Message, InputMediaPhoto
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from telegram.error import BadRequest
from PIL import Image
import io

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN")
COOKIES_FILE = "twitter_cookies.txt"

if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set.")

# إصلاح ملفات الكوكيز إذا موجودة
def fix_cookies_file():
    if not os.path.exists(COOKIES_FILE):
        return
    with open(COOKIES_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    fixed = []
    changed = False
    for line in lines:
        if line.startswith('#') and not line.startswith('# '):
            line = re.sub(r'^#HttpOnly_', '', line)
            changed = True
        parts = line.rstrip('\n').split('\t')
        if len(parts) >= 2 and parts[0].startswith('.') and parts[1] == 'FALSE':
            parts[1] = 'TRUE'
            line = '\t'.join(parts) + '\n'
            changed = True
        fixed.append(line)
    if changed:
        with open(COOKIES_FILE, 'w', encoding='utf-8') as f:
            f.writelines(fixed)
        logging.info("Cookies file auto-fixed")

fix_cookies_file()

TWITTER_PATTERN = re.compile(
    r'https?://(?:www\.)?(?:twitter\.com|x\.com|t\.co)/\S+'
)

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
VIDEO_EXTS = {'.mp4', '.webm', '.mkv', '.mov'}

def get_twitter_url(text):
    if not text:
        return None
    match = TWITTER_PATTERN.search(text)
    return match.group(0).rstrip('.,)') if match else None

def to_mirror(url, mirror):
    url = re.sub(r'https?://(?:www\.)?twitter\.com', f'https://{mirror}', url)
    url = re.sub(r'https?://(?:www\.)?x\.com', f'https://{mirror}', url)
    return url

def find_media_files():
    videos = []
    images = []
    for f in sorted(os.listdir()):
        if f.startswith("media."):
            ext = os.path.splitext(f)[1].lower()
            if ext in VIDEO_EXTS:
                videos.append(f)
            elif ext in IMAGE_EXTS:
                images.append(f)
    return videos, images

def cleanup_media():
    for f in os.listdir():
        if f.startswith("media."):
            try:
                os.remove(f)
            except Exception:
                pass

def build_ydl_opts(api_mode=None, for_images=False):
    opts = {
        'outtmpl': 'media.%(autonumber)s.%(ext)s',
        'quiet': True,
    }
    if for_images:
        opts['format'] = 'best'
        opts['write_all_thumbnails'] = False
    else:
        opts['format'] = 'bestvideo+bestaudio/best'
        opts['merge_output_format'] = 'mp4'
    if api_mode:
        opts['extractor_args'] = {'twitter': {'api': [api_mode]}}
    if os.path.exists(COOKIES_FILE):
        opts['cookiefile'] = COOKIES_FILE
    return opts

def safe_reply_text(msg: Message, chat_id: int, bot, text: str):
    try:
        msg.reply_text(text)
    except BadRequest:
        bot.send_message(chat_id=chat_id, text=text)

def download_media(url):
    has_cookies = os.path.exists(COOKIES_FILE)
    strategies = []
    if has_cookies:
        strategies.append((url, 'graphql', 'authenticated graphql'))
        strategies.append((url, None,       'authenticated default'))
    strategies.append((url, 'syndication', 'syndication API'))
    strategies.append((url, 'graphql',     'graphql API'))
    strategies.append((url, None,          'default API'))
    if 'twitter.com' in url or 'x.com' in url:
        strategies.append((to_mirror(url, 'fxtwitter.com'), None, 'fxtwitter mirror'))
        strategies.append((to_mirror(url, 'vxtwitter.com'), None, 'vxtwitter mirror'))

    for try_url, api_mode, label in strategies:
        cleanup_media()
        logging.info(f"Trying [{label}]: {try_url}")
        try:
            with yt_dlp.YoutubeDL(build_ydl_opts(api_mode)) as ydl:
                ydl.download([try_url])
            videos, images = find_media_files()
            if videos or images:
                logging.info(f"Success with [{label}]: {len(videos)} video(s), {len(images)} image(s)")
                return videos, images
        except Exception as e:
            logging.warning(f"[{label}] failed: {e}")
            continue

    return [], []

def process_message(msg, bot):
    if not msg:
        return
    chat_id = msg.chat_id
    text = msg.text or msg.caption or ""
    url = get_twitter_url(text)
    if not url:
        return
    if re.search(r'(?:twitter\.com|x\.com)/\w+$', url):
        return

    logging.info(f"Found Twitter URL: {url}")
    safe_reply_text(msg, chat_id, bot, "📥 جاري تحميل الميديا...")

    videos, images = download_media(url)

    if not videos and not images:
        safe_reply_text(msg, chat_id, bot, "⚠️ لم يتم العثور على فيديو أو صورة في هذه التغريدة")
        cleanup_media()
        return

    try:
        for vf in videos:
            try:
                msg.reply_video(video=open(vf, 'rb'))
            except BadRequest:
                bot.send_video(chat_id=chat_id, video=open(vf, 'rb'))

        if len(images) == 1:
            try:
                msg.reply_photo(photo=open(images[0], 'rb'))
            except BadRequest:
                bot.send_photo(chat_id=chat_id, photo=open(images[0], 'rb'))
        elif len(images) > 1:
            media_group = [InputMediaPhoto(open(f, 'rb')) for f in images[:10]]
            try:
                msg.reply_media_group(media=media_group)
            except BadRequest:
                bot.send_media_group(chat_id=chat_id, media=media_group)
    except Exception as e:
        logging.error(f"Failed to send media: {e}")
        safe_reply_text(msg, chat_id, bot, "❌ فشل إرسال الميديا")
    finally:
        cleanup_media()

def handler(update, context):
    msg = update.message or update.channel_post
    process_message(msg, context.bot)

updater = Updater(TOKEN, use_context=True)
dp = updater.dispatcher

dp.add_handler(MessageHandler(Filters.all, handler))

print("BOT WORKING 🔥")
if os.path.exists(COOKIES_FILE):
    print("✅ Cookies file found — authenticated downloads enabled")
else:
    print("⚠️  No cookies file — sensitive tweets may not download")

updater.start_polling()
updater.idle()
