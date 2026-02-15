import json, time
from app.config import TICKERS
from app.stocks import fetch_snapshot

def main():
    snap = fetch_snapshot(TICKERS)
    snap["generated_at"] = int(time.time())
    with open("snapshot.json", "w") as f:
        json.dump(snap, f, indent=2)
    print("Write to snapshot.json")

if __name__ == "__main__":
    main()
