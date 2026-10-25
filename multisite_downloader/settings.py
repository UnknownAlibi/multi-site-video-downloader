"""settings.json 的读写。"""

import json
import os
from pathlib import Path

from .config import DEFAULT_SETTINGS, SETTINGS_PATH


def load_settings():
    try:
        if SETTINGS_PATH.exists():
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            merged = dict(DEFAULT_SETTINGS)
            merged.update(data)
            return merged, None
    except Exception as exc:
        return dict(DEFAULT_SETTINGS), str(exc)
    return dict(DEFAULT_SETTINGS), None


def save_settings(settings):
    """原子保存设置，返回 (True, None) 或 (False, error_msg)。"""
    target = Path(SETTINGS_PATH)
    temp = target.with_name(target.name + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, target)
        return True, None
    except Exception as exc:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass
        return False, str(exc)
