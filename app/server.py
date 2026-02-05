from fastapi import FastAPI, Response
from app.config import TICKERS, CACHE_TTL_SECONDS
from app.cache import get_cache, set_cache, with_lock
from app.stocks import fetch_snapshot
from app.exporters import snapshot_to_prometheus

app = FastAPI()

@with_lock
def _refresh_snapshot():
    snap = fetch_snapshot(TICKERS)
    set_cache(snap)
    return snap

@app.get("/metrics")
def metrics():
    cached = get_cache(CACHE_TTL_SECONDS)
    snapshot = cached if cached is not None else _refresh_snapshot()
    body, content_type = snapshot_to_prometheus(snapshot, TICKERS)
    return Response(content=body, media_type=content_type)