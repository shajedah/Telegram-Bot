import os
import requests
import logging
import asyncio
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- Logging ---
logging.basicConfig(level=logging.INFO)

# --- Config ---
TOKEN = os.getenv('BOT_TOKEN')
CHANNEL_ID = os.getenv('CHANNEL_ID')
FIREBASE_URL = os.getenv('FIREBASE_URL', '').rstrip('/')
BOT_PASSWORD = os.getenv('BOT_PASSWORD', 'Ahmed@11411')
DELETE_CONFIRM_TEXT = "Yes i am"
UPLOAD_DELAY = 1.0  # Delay between uploads to prevent rate limiting
DELETE_DELAY = 0.8  # Delay for deletions

# --- Firebase Functions ---
def save_to_firebase(msg_id, name):
    url = f"{FIREBASE_URL}/files/{msg_id}.json"
    try:
        requests.put(url, json={"name": name}, timeout=10)
        return 200
    except: return 500

def delete_all_firebase():
    url = f"{FIREBASE_URL}/files.json"
    try:
        requests.delete(url, timeout=10)
        return True
    except: return False

def fetch_from_firebase():
    url = f"{FIREBASE_URL}/files.json"
    try:
        res = requests.get(url, timeout=10)
        return res.json() if res.status_code == 200 else None
    except: return None

# --- Rate Limiter ---
class RateLimiter:
    def __init__(self, max_per_second=20, max_per_minute=20):
        self.max_per_second = max_per_second
        self.max_per_minute = max_per_minute
        self.second_requests = []
        self.minute_requests = []
    
    async def wait_if_needed(self):
        now = asyncio.get_event_loop().time()
        
        # Clean old entries
        self.second_requests = [t for t in self.second_requests if now - t < 1]
        self.minute_requests = [t for t in self.minute_requests if now - t < 60]
        
        # Check limits
        if len(self.second_requests) >= self.max_per_second:
            await asyncio.sleep(1.0)
            self.second_requests = []
        
        if len(self.minute_requests) >= self.max_per_minute:
            wait_time = 60 - (now - self.minute_requests[0])
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self.minute_requests = []
        
        # Record this request
        self.second_requests.append(now)
        self.minute_requests.append(now)

rate_limiter = RateLimiter()

# --- Helper: Check Auth ---
def is_authenticated(user_data):
    return user_data.get('auth') == True

# --- Bot Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['auth'] = True
    context.user_data['delete_auth'] = False
    welcome_text = (
        "💖 Welcome to Shajeda's Secure Gallery 📸\n\n"
        "/start - Menu\n"
        "/gallery - View Gallery\n"
        "/list - List files with ID\n"
        "/search - Search file by ID\n"
        "/clear - Delete all files\n\n"
        "You must use password to view gallery and clear database\n\n"
        "Please upload photos/videos/audios as file for best quality ensure"
    )
    keyboard = [
        [InlineKeyboardButton("View Gallery", callback_data="fetchall")],
        [
            InlineKeyboardButton("List Files", callback_data="list"),
            InlineKeyboardButton("Search", callback_data="ask_id")
        ],
        [InlineKeyboardButton("Clear All", callback_data="delete_step_1")]
    ]
    await update.message.reply_text(
        welcome_text, 
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def gallery_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['awaiting_gallery_password'] = True
    await update.message.reply_text("Enter password to view gallery")

async def list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = fetch_from_firebase()
    if not data:
        await update.message.reply_text("Gallery is empty")
        return
    
    text = "Your Files\n\n"
    for idx, (mid, info) in enumerate(data.items(), 1):
        text += f"{idx}. ID: {mid} - {info.get('name', 'File')}\n"
    
    text += f"\nTotal: {len(data)}"
    await update.message.reply_text(text[:4000])

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Enter file ID")

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("Confirm", callback_data="delete_step_2")],
        [InlineKeyboardButton("Cancel", callback_data="cancel_del")]
    ]
    await update.message.reply_text(
        "Clear database?\nFiles in channel will remain",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def show_main_menu(update, context):
    text = (
        "💖 Welcome to Shajeda's Secure Gallery 📸\n\n"
        "/start - Menu\n"
        "/gallery - View Gallery\n"
        "/list - List files with ID\n"
        "/search - Search file by ID\n"
        "/clear - Delete all files\n\n"
        "You must use password to view gallery and clear database\n\n"
        "Please upload photos/videos/audios as file for best quality ensure\n\n"
        "NOTE: Make all command real work"
    )
    keyboard = [
        [InlineKeyboardButton("View Gallery", callback_data="fetchall")],
        [
            InlineKeyboardButton("List Files", callback_data="list"),
            InlineKeyboardButton("Search", callback_data="ask_id")
        ],
        [InlineKeyboardButton("Clear All", callback_data="delete_step_1")]
    ]
    await context.bot.send_message(
        update.effective_chat.id, 
        text, 
        reply_markup=InlineKeyboardMarkup(keyboard), 
        parse_mode='Markdown'
    )

async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_data = context.user_data
    if not update.message: return
    
    msg_text = update.message.text.strip() if update.message.text else ""

    # 1. Delete Auth Check
    if user_data.get('awaiting_delete_password'):
        if msg_text == BOT_PASSWORD:
            user_data['delete_auth'] = True
            user_data['awaiting_delete_password'] = False
            user_data['awaiting_delete_confirm'] = True
            await update.message.reply_text(
                f"Type: **`{DELETE_CONFIRM_TEXT}`**\nTo confirm",
                parse_mode='Markdown'
            )
        else:
            await update.message.reply_text("Invalid password")
            user_data['awaiting_delete_password'] = False
        return

    # 2. Deletion Logic
    if user_data.get('awaiting_delete_confirm'):
        if msg_text == DELETE_CONFIRM_TEXT:
            data = fetch_from_firebase()
            if not data:
                await update.message.reply_text("Gallery is empty")
                user_data['awaiting_delete_confirm'] = False
                return
            
            total_files = len(data)
            status_msg = await update.message.reply_text(f"Clearing database...")
            
            # Only delete from Firebase, not from channel
            delete_all_firebase()
            
            await status_msg.edit_text(f"Done\nCleared: {total_files}")
            user_data['awaiting_delete_confirm'] = False
            user_data['delete_auth'] = False
        else:
            await update.message.reply_text("Cancelled")
            user_data['awaiting_delete_confirm'] = False
            user_data['delete_auth'] = False
        return

    # 3. Gallery Auth Check
    if user_data.get('awaiting_gallery_password'):
        if msg_text == BOT_PASSWORD:
            user_data['gallery_auth'] = True
            user_data['awaiting_gallery_password'] = False
            # Trigger fetchall
            data = fetch_from_firebase()
            if not data:
                await update.message.reply_text("Gallery is empty")
                return
            
            status_msg = await update.message.reply_text(f"Loading {len(data)} files...")
            
            for idx, mid in enumerate(data.keys(), 1):
                try:
                    await rate_limiter.wait_if_needed()
                    await context.bot.copy_message(update.effective_chat.id, CHANNEL_ID, int(mid))
                    await asyncio.sleep(UPLOAD_DELAY)
                    
                    if idx % 10 == 0:
                        try:
                            await status_msg.edit_text(f"Loading... {idx}/{len(data)}")
                        except:
                            pass
                except Exception as e:
                    logging.error(f"Fetch error for {mid}: {e}")
                    continue
            
            try:
                await status_msg.edit_text(f"Done\nTotal: {len(data)}")
            except:
                pass
        else:
            await update.message.reply_text("Invalid password")
            user_data['awaiting_gallery_password'] = False
        return

    # 4. ID Fetch
    if msg_text.isdigit():
        try:
            await context.bot.copy_message(update.effective_chat.id, CHANNEL_ID, int(msg_text))
        except:
            await update.message.reply_text("Not found")
        return

    # 5. File Upload with Rate Limiting
    if update.message.document or update.message.photo or update.message.video:
        name = "File"
        if update.message.document: name = update.message.document.file_name
        elif update.message.caption: name = update.message.caption
        
        try:
            # Apply rate limiting
            await rate_limiter.wait_if_needed()
            
            copied = await context.bot.copy_message(CHANNEL_ID, update.effective_chat.id, update.message.message_id)
            save_to_firebase(copied.message_id, name)
            
            # Send separate message for each upload with ID
            await update.message.reply_text(f"Done\nID: {copied.message_id}")
            
            # Additional delay between uploads
            await asyncio.sleep(UPLOAD_DELAY)
        except Exception as e:
            logging.error(f"Upload Error: {e}")
            await update.message.reply_text("Upload failed")
        return

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        data = fetch_from_firebase()

        if query.data == "list":
            if not data:
                await query.message.reply_text("Gallery is empty")
                return
            
            text = "Your Files\n\n"
            for idx, (mid, info) in enumerate(data.items(), 1):
                text += f"{idx}. ID: {mid} - {info.get('name', 'File')}\n"
            
            text += f"\nTotal: {len(data)}"
            await query.message.reply_text(text[:4000])

        elif query.data == "fetchall":
            # Ask for password
            context.user_data['awaiting_gallery_password'] = True
            await query.message.reply_text("Enter password to view gallery")

        elif query.data == "ask_id":
            await query.message.reply_text("Enter file ID")

        elif query.data == "delete_step_1":
            kb = [
                [InlineKeyboardButton("Confirm", callback_data="delete_step_2")],
                [InlineKeyboardButton("Cancel", callback_data="cancel_del")]
            ]
            await query.message.reply_text(
                "Clear database?\nFiles in channel will remain",
                reply_markup=InlineKeyboardMarkup(kb)
            )

        elif query.data == "delete_step_2":
            # Ask for password
            context.user_data['awaiting_delete_password'] = True
            await query.message.reply_text("Enter password to confirm deletion")

        elif query.data == "cancel_del":
            context.user_data['awaiting_delete_confirm'] = False
            context.user_data['awaiting_delete_password'] = False
            await query.message.reply_text("Cancelled")
    
    except Exception as e:
        logging.error(f"Button handler error: {e}")
        await query.message.reply_text("Something went wrong")

# --- Flask ---
app = Flask(__name__)
@app.route('/')
def home(): return "Online"

if __name__ == '__main__':
    Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080))), daemon=True).start()
    
    # Build bot with optional proxy support
    builder = ApplicationBuilder().token(TOKEN)
    
    # Add proxy if configured in .env
    proxy_url = os.getenv('PROXY_URL')
    if proxy_url:
        builder = builder.proxy_url(proxy_url)
        logging.info(f"Using proxy: {proxy_url}")
    
    # Increase timeouts for slow connections
    builder = builder.connect_timeout(30.0).read_timeout(30.0)
    
    bot_app = builder.build()
    bot_app.add_handler(CommandHandler('start', start))
    bot_app.add_handler(CommandHandler('gallery', gallery_command))
    bot_app.add_handler(CommandHandler('list', list_command))
    bot_app.add_handler(CommandHandler('search', search_command))
    bot_app.add_handler(CommandHandler('clear', clear_command))
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    bot_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_all_messages))
    bot_app.run_polling(drop_pending_updates=True)
