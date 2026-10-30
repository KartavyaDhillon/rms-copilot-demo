"""Central configuration. Everything reads settings from HERE, nowhere else."""
import os
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()  # reads the .env file into the environment

# --- Dates (replaces the line you copy-pasted into 39 files) ---
TODAY_DDMMYYYY = datetime.now().strftime("%d%m%Y")
TODAY_YYYYMMDD = datetime.now().strftime("%Y%m%d")

# --- AI providers ---
GROQ_API_KEY = os.getenv("GROQ_API_KEY")          # free LLM provider (used now)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")  # Claude (used later, when funded)

# --- Email (Netcore) ---
SMTP_SERVER = os.getenv("SMTP_SERVER")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")

# --- FTP ---
FTP_HOST = os.getenv("FTP_HOST")
FTP_USER = os.getenv("FTP_USER")
FTP_PASS = os.getenv("FTP_PASS")

# --- WhatsApp (Gupshup) ---
GUPSHUP_API_KEY = os.getenv("GUPSHUP_API_KEY")