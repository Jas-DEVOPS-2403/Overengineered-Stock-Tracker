from app.config import TICKERS, CACHE_TTL_SECONDS
from app.cache import get_cached, set_cached
from app.stocks import fetch_snapshot
from app.exporters import snapshot_to_prometheus

# module level cache for metrics/ prometheus endpoint
def handler(request):
    cached = get_cached(CACHE_TTL_SECONDS)
    snapshot = cached if cached is not None else fetch_snapshot(TICKERS)
    if cached is None:
        set_cached(snapshot)

    body, content_type = snapshot_to_prometheus(snapshot, TICKERS)

    return {
        "statusCode": 200,
        "headers": {"Content-Type": content_type},
        "body": body.decode("utf-8"),
    }
