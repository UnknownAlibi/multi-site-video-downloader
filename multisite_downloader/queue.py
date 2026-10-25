"""download/queue.json 的原子读写。"""

import json
import os
from pathlib import Path

from .config import QUEUE_PATH


def load_queue(path=None):
    target = Path(path or QUEUE_PATH)
    try:
        if not target.exists():
            return []
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]
    except Exception:
        return []


def save_queue(tasks, path=None):
    """原子保存队列，返回 (ok, error)。"""
    target = Path(path or QUEUE_PATH)
    temp = target.with_name(target.name + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(list(tasks), handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
        return True, None
    except Exception as exc:
        try:
            if temp.exists():
                temp.unlink()
        except OSError:
            pass
        return False, str(exc)
