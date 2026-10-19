import os
from pathlib import Path

DATABASE_URL = os.environ.get('DATABASE_URL', 'sqlite:////tmp/global_quant_test.db')
REDIS_URL = os.environ.get('REDIS_URL', 'redis://redis:6379/0')
DATA_ROOT = Path(os.environ.get('DATA_ROOT', '/market-data'))
RESEARCH_ROOT = Path(os.environ.get('RESEARCH_ROOT', '/research'))
RESEARCH_RUNS_ROOT = Path(os.environ.get('RESEARCH_RUNS_ROOT', str(RESEARCH_ROOT / 'research-runs')))
RESEARCH_WORKER_TOKEN = os.environ.get('RESEARCH_WORKER_TOKEN', '')
CONFIG_ROOT = Path(os.environ.get('CONFIG_ROOT', '/app/config'))
COOKIE_SECURE = os.environ.get('COOKIE_SECURE', 'false').lower() == 'true'
ALLOWED_ORIGINS = set(os.environ.get('ALLOWED_ORIGINS', 'http://localhost:8080,http://127.0.0.1:8080').split(','))
SESSION_HOURS = 12
# Phase 1 is incapable of sending orders. No environment flag can enable live execution.
LIVE_EXECUTION_IMPLEMENTED = False
