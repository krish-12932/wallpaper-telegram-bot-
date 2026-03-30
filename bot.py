import os
import time
import logging
import threading
import urllib.request
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from supabase import create_client, Client
from ai_processor import setup_ai, generate_wallpaper_metadata
from flask import Flask

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load env variables (explicit path to help with deployment)
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(dotenv_path=_env_path, override=True)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not all([TELEGRAM_BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY]):
    logger.error("❌ Missing environment variables! Please check your .env file.")
    exit(1)

# Ensure AI is setup (throws error if no API key is found)
setup_ai()

# Supabase Client setup
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Flask Server setup for Render (Keep-alive)
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running perfectly on Render!"

def run_flask():
    # Render assigns a PORT dynamically, or defaults to 8080 locally
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

def keep_awake_pinger():
    """Automatically pings its own Render URL every 10 minutes so it never sleeps."""
    # Render automatically sets this environment variable for Web Services
    my_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not my_url:
        logger.warning("No RENDER_EXTERNAL_URL found. Auto-ping is disabled (you are probably running locally).")
        return
        
    while True:
        try:
            time.sleep(10 * 60) # Wait 10 minutes
            logger.info(f"🔄 Auto-Knock: Pinging myself at {my_url} to stay awake...")
            urllib.request.urlopen(my_url)
        except Exception as e:
            logger.error(f"❌ Auto-Knock Failed: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **Hello!**\n\n"
        "Send me a wallpaper (as a Photo or Document) and I will:\n"
        "1. Auto-tag it using Gemini AI.\n"
        "2. Upload to Supabase Storage.\n"
        "3. Save to Database.",
        parse_mode="Markdown"
    )

async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles incoming photos and documents (uncompressed images)."""
    
    # Send early visual feedback
    msg = await update.message.reply_text("⚙️ Receiving image... Please wait.")
    
    temp_file_path = None
    try:
        # Determine if it's a photo (compressed) or document (original)
        file_id = None
        extension = "jpg"
        
        if update.message.photo:
            # Get the highest resolution photo (the final one in the array)
            photo = update.message.photo[-1]
            file_id = photo.file_id
        elif update.message.document:
            doc = update.message.document
            mime = doc.mime_type
            if not mime or not mime.startswith("image/"):
                await msg.edit_text("❌ Please send an IMAGE file.")
                return
            file_id = doc.file_id
            if "." in doc.file_name:
                extension = doc.file_name.split(".")[-1]
                
        if not file_id:
            await msg.edit_text("❌ Unknown media format.")
            return
            
        temp_file_path = f"temp_{file_id}.{extension}"
            
        # 1. Download file content from Telegram backend to disk
        await msg.edit_text("⏳ Downloading image from Telegram servers...")
        telegram_file = await context.bot.get_file(file_id)
        await telegram_file.download_to_drive(temp_file_path)
        
        # 2. Process image with Google Gemini API
        await msg.edit_text("🧠 Analyzing image using AI for Title, Category, and Description...")
        metadata = generate_wallpaper_metadata(temp_file_path)
        
        title = metadata.get("title", "Premium Wallpaper").strip()
        category = metadata.get("category", "Aesthetic").strip()
        description = metadata.get("description", "").strip()
        tags = metadata.get("tags", [])
        
        # 3. Upload to Supabase Storage
        await msg.edit_text("☁️ Uploading to Supabase Cloud Storage bucket 'image'...")
        
        # Create a unique SEO-friendly filename
        clean_title = title.lower().replace(" ", "-")
        safe_title = "".join(c for c in clean_title if c.isalnum() or c == "-")
        # Appending timestamp prevents accidental overwrites
        unique_filename = f"{int(time.time())}-{safe_title}.{extension}"
        
        supabase.storage.from_("image").upload(
            path=unique_filename, 
            file=temp_file_path,
            file_options={"content-type": f"image/{extension}"}
        )
        
        # Construct public URL
        file_url = supabase.storage.from_("image").get_public_url(unique_filename)
        
        # 4. Insert Metadata into Database 'photos' table
        await msg.edit_text("📝 Saving record to Supabase Database...")
        
        current_time = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        db_payload = {
            "title": title,
            "category": category,
            "description": description,
            "file_url": file_url,
            "created_at": current_time
        }
        
        supabase.table("photos").insert(db_payload).execute()
        
        # 5. Success Message Summary
        success_text = (
            f"✅ **Wallpaper Successfully Uploaded!**\n\n"
            f"📌 **Title:** {title}\n"
            f"📂 **Category:** {category}\n"
            f"📝 **Description:** {description}\n"
            f"🏷️ **Tags:** {', '.join(tags)}\n\n"
            f"🔗 [View Source Image]({file_url})"
        )
        await msg.edit_text(success_text, parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Error handling media: {e}")
        await msg.edit_text(f"❌ An error occurred during upload:\n`{e}`", parse_mode="Markdown")
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception as cleanup_error:
                logger.warning(f"Failed to clean up temp file: {cleanup_error}")

def main():
    logger.info("🚀 Starting Wallpaper AI Auto-Uploader Bot alongside Flask...")
    
    # IMPORTANT: Run Flask Server in the background for Render Web Service binding
    server_thread = threading.Thread(target=run_flask)
    # daemon=True makes sure the thread exits when the main bot crashes/exits
    server_thread.daemon = True
    server_thread.start()
    
    # Run Automatic Self-Pinger
    pinger_thread = threading.Thread(target=keep_awake_pinger)
    pinger_thread.daemon = True
    pinger_thread.start()
    
    # Increase network timeouts because uploading 4k images uses a lot of IO bandwidth
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).read_timeout(60).write_timeout(60).connect_timeout(60).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_media))
    
    # Run the bot and listen for events
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
