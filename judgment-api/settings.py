"""Local settings only. Never print keys or corpus content."""
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parent
DEPENDENCIES = ROOT / '.deps'
if DEPENDENCIES.is_dir():
    sys.path.insert(0, str(DEPENDENCIES))


def load_settings(path=None):
    values = {}
    file = Path(path) if path else ROOT / '.env'
    if file.exists():
        for line in file.read_text(encoding='utf-8-sig').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    for key in ('JUDGE_API_KEY', 'JUDGMENTS_PATH'):
        if os.environ.get(key):
            values[key] = os.environ[key]
    if len(values.get('JUDGE_API_KEY', '')) < 20:
        raise ValueError('Set a dedicated random JUDGE_API_KEY of at least 20 characters in .env')
    source = Path(values.get('JUDGMENTS_PATH', ''))
    if not source.is_file():
        raise ValueError('JUDGMENTS_PATH must identify the local course dataset')
    return values

