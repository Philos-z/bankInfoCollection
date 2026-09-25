import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

AI_BASE_URL = os.getenv("AI_BASE_URL", "http://127.0.0.1:8080/v1")
AI_API_KEY = os.getenv("AI_API_KEY", "dummy")
AI_MODEL = os.getenv("AI_MODEL", "gpt-5.6-sol")
AI_MAX_CONCURRENCY = int(os.getenv("AI_MAX_CONCURRENCY", "1"))
AI_TIMEOUT = float(os.getenv("AI_TIMEOUT", "180"))
AI_HTTP_RETRIES = int(os.getenv("AI_HTTP_RETRIES", "2"))
AI_RETRY_BASE_SECONDS = float(os.getenv("AI_RETRY_BASE_SECONDS", "2"))

DB_PATH = ROOT / os.getenv("DB_PATH", "data/bank_campaigns.db")
SNAPSHOT_DIR = ROOT / os.getenv("SNAPSHOT_DIR", "data/snapshots")
BANKS_CONFIG = ROOT / "config" / "banks.yaml"

USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
)
REQUEST_DELAY_SECONDS = float(os.getenv("REQUEST_DELAY_SECONDS", "3"))

# Source-health thresholds. A successful fetch always resets consecutive failures to zero.
SOURCE_HEALTH_WARNING_FAILURES = int(os.getenv("SOURCE_HEALTH_WARNING_FAILURES", "3"))
SOURCE_HEALTH_CRITICAL_FAILURES = int(os.getenv("SOURCE_HEALTH_CRITICAL_FAILURES", "5"))

# Days before end_date at which an active campaign is flagged as expiring.
EXPIRING_WINDOW_DAYS = 14
