from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("KSEO_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("KSEO_DB", DATA_DIR / "kseo.db"))
STATIC_DIR = ROOT / "static"

HOST = os.environ.get("KSEO_HOST", "127.0.0.1")
PORT = int(os.environ.get("KSEO_PORT", "18345"))
INTERNAL_TOKEN = os.environ.get("KSEO_INTERNAL_TOKEN", "")
DEV_USER = os.environ.get("KSEO_DEV_USER", "local")
_admin_users = os.environ.get("KSEO_ADMIN_USERS", "").strip() or "xb_bittensor"
ADMIN_USERS = {
    value.strip().lstrip("@").lower() for value in _admin_users.split(",") if value.strip()
}

# クロール設定。他社サイトを叩くので、既定は控えめにする。
USER_AGENT = os.environ.get(
    "KSEO_USER_AGENT",
    "KurageSEO/1.0 (+https://kurage.exbridge.jp/kseo.php)",
)
CRAWL_TIMEOUT = float(os.environ.get("KSEO_CRAWL_TIMEOUT", "20"))
CRAWL_DELAY = float(os.environ.get("KSEO_CRAWL_DELAY", "1.0"))
FREE_PAGES_PER_AUDIT = int(os.environ.get("KSEO_FREE_PAGES_PER_AUDIT", "5"))
PAID_PAGES_PER_AUDIT = int(os.environ.get("KSEO_PAID_PAGES_PER_AUDIT", "50"))
MAX_HTML_BYTES = int(os.environ.get("KSEO_MAX_HTML_BYTES", str(3 * 1024 * 1024)))

# LLM。無料枠と管理者は自社GPUのGemma、課金された実行だけDeepSeek。
# 監査そのものはLLMを使わない(判定が揺れると不良品になる)。LLMは改善案の文章だけ。
RQDB4AI_URL = os.environ.get("KSEO_RQDB4AI_URL", "").strip().rstrip("/")
RQDB4AI_TOKEN = os.environ.get("KSEO_RQDB4AI_TOKEN", "").strip()
RQDB4AI_FUNCTION = (
    os.environ.get("KSEO_RQDB4AI_FUNCTION", "").strip() or "kseo.jobs.ollama_chat_job"
)
RQDB4AI_POLL_INTERVAL = max(0.5, float(os.environ.get("KSEO_RQDB4AI_POLL_INTERVAL", "2")))
RQDB4AI_WAIT_TIMEOUT = max(30.0, float(os.environ.get("KSEO_RQDB4AI_WAIT_TIMEOUT", "300")))
# 直叩きの既定は 192.168.0.3。192.168.0.14 は rqdb4ai がホスト別キューで
# 直列化してGPU競合を防ぐ経路専用なので、直接叩いてはいけない。
# rqdb4ai を設定した場合だけそちら経由になる(下の RQDB4AI_URL)。
OLLAMA_BASE_URL = (
    os.environ.get("KSEO_OLLAMA_BASE_URL", "").strip() or "http://192.168.0.3:11434"
).rstrip("/")
OLLAMA_MODEL = os.environ.get("KSEO_OLLAMA_MODEL", "").strip() or "gemma4:12b-it-qat"
OLLAMA_TIMEOUT = float(os.environ.get("KSEO_OLLAMA_TIMEOUT", "180"))

DEEPSEEK_BASE_URL = (
    os.environ.get("KSEO_DEEPSEEK_BASE_URL", "").strip() or "https://api.deepseek.com"
).rstrip("/")
DEEPSEEK_API_KEY = os.environ.get("KSEO_DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_API_KEY_FILE = os.environ.get("KSEO_DEEPSEEK_API_KEY_FILE", "").strip()
DEEPSEEK_API_KEY_NAME = (
    os.environ.get("KSEO_DEEPSEEK_API_KEY_NAME", "").strip() or "DEEPSEEK_API_KEY"
)
DEEPSEEK_MODEL = os.environ.get("KSEO_DEEPSEEK_MODEL", "").strip() or "deepseek-v4-flash"
DEEPSEEK_TIMEOUT = float(os.environ.get("KSEO_DEEPSEEK_TIMEOUT", "180"))

FREE_AUDITS_PER_MONTH = int(os.environ.get("KSEO_FREE_AUDITS_PER_MONTH", "3"))
FREE_ADVICE_PER_MONTH = int(os.environ.get("KSEO_FREE_ADVICE_PER_MONTH", "5"))
MAX_SITES_PER_USER = int(os.environ.get("KSEO_MAX_SITES_PER_USER", "20"))

PUBLIC_APP_URL = (
    os.environ.get("KSEO_PUBLIC_APP_URL", "").strip()
    or "https://kurage.exbridge.jp/kseo.php"
)
