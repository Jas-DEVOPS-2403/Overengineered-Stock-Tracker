import time
import threading 
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

class CacheState:
    ts: float = 0.0
    data: Optional[Dict[str, Any]] = None

_lock = threading.Lock()
_state = CacheState()  

def get_cache(ttl_seconds: int) -> Optional[Dict[str, Any]]:
    now = time.time()
    if _state.data is not None and (now - _state.ts) < ttl_seconds:
        return _state.data
    return None

def set_cache(data: Dict[str, Any]) -> None:
    _state.data = data 
    _state.ts = time.time()

def with_lock(fn):
    #decorator helper to avoid concurrent refreshes
    def wrapper(*args, **kwargs):
        with _lock:
            return fn(*args, **kwargs)
    return wrapper

