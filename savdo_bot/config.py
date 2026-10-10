import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
try:
    OWNER_ID = int(os.getenv("OWNER_ID", "0"))
except ValueError:
    OWNER_ID = 0

if not BOT_TOKEN:
    raise RuntimeError(".env faylida BOT_TOKEN kiritilmagan.")
if OWNER_ID <= 0:
    raise RuntimeError(".env faylida OWNER_ID raqamli Telegram ID bo'lishi kerak.")
