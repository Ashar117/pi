"""testing/conftest.py — suite-wide hygiene (T-280).

Tests must never touch Ash's real Telegram. tools_telegram reads its token
from the environment at import time, and several runtime paths send
"best-effort" messages (guest-approval notify in agent/tools.py, watcher
alerts, P1 alerts) — one test driving those paths unmocked means real
messages on Ash's phone at 3am, which is exactly what happened on
2026-07-07 (the "[Approval needed] alice wants to run run_script" ping).

pytest imports conftest before any test module, and app/config.py's
load_dotenv() never overrides existing env vars, so blanking here wins
over .env for every test in the suite (verify.py runs each file as its
own pytest subprocess — this applies to all of them).
"""
import os

os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["TELEGRAM_CHAT_ID"] = ""
