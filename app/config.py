import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

# Base paths
BASE_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

# API Keys
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")

# Mode
DEFAULT_MODE = "normie"

# Local model
LOCAL_MODEL = "gemma4:e2b"
OLLAMA_BASE_URL = "http://localhost:11434"

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# Cost limits
DAILY_COST_LIMIT = 0.50  # dollars — normie mode ceiling

# Logging
LOG_PATH = LOGS_DIR / "pi.log"

# Tool config
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-2.0-flash"

CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "")
CEREBRAS_MODEL = "gpt-oss-120b"

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct:free"

Z_AI_API_KEY = os.getenv("Z_AI_API_KEY", "")
Z_AI_MODEL = os.getenv("Z_AI_MODEL", "glm-4.7-flash")

# Qwen via Alibaba Cloud Model Studio (DashScope) — hackathon primary provider
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen3.7-max")


SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")

# Obsidian Local REST API (plugin: obsidian-local-rest-api)
OBSIDIAN_HOST = os.getenv("OBSIDIAN_HOST", "http://127.0.0.1:27123")
OBSIDIAN_API_KEY = os.getenv("OBSIDIAN_API_KEY", "")

# Optional awareness backup APIs
ALPHA_VANTAGE_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")
NEWS_API_KEY = os.getenv("NEWS_API_KEY", "")
