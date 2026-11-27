"""Gui tin nhan len Discord qua webhook.

Dung:  python notify_discord.py "noi dung tin nhan"
URL webhook lay tu DISCORD_WEBHOOK_URL (bien moi truong hoac file .env canh script).
Tin dai hon 2000 ky tu se tu cat thanh nhieu tin. Khong ping @everyone/role/user.
Exit code: 0 = xong, 1 = loi.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

LIMIT = 2000
USERNAME = "Hoshino Update"
ENV_NAME = "DISCORD_WEBHOOK_URL"
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def get_webhook_url():
    url = (os.environ.get(ENV_NAME) or "").strip()
    if not url:
        try:
            with open(ENV_PATH, encoding="utf-8") as f:
                for line in f:
                    key, sep, val = line.strip().partition("=")
                    if sep and key.strip() == ENV_NAME:
                        url = val.strip().strip('"').strip("'")
                        break
        except OSError:
            pass
    return url


def valid_webhook_url(url):
    u = urllib.parse.urlparse(url)
    host = (u.hostname or "").lower()
    return (u.scheme == "https"
            and host in ("discord.com", "discordapp.com")
            or host.endswith((".discord.com", ".discordapp.com"))) and u.path.startswith("/api/webhooks/")


def split_message(text, limit=LIMIT):
    chunks = []
    text = text.strip()
    while len(text) > limit:
        cut = max(text.rfind("\n", 0, limit), text.rfind(" ", 0, limit))
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks


def post(url, content, retries=3):
    body = json.dumps({"content": content, "username": USERNAME,
                       "allowed_mentions": {"parse": []}}).encode("utf-8")
    for _ in range(retries):
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "User-Agent": "HoshinoUpdate",   # Discord/Cloudflare chan User-Agent mac dinh cua urllib
        })
        try:
            with urllib.request.urlopen(req, timeout=15):
                return True
        except urllib.error.HTTPError as e:
            if e.code != 429:
                print(f"[discord] HTTP {e.code}", file=sys.stderr)
                return False
            try:
                wait = float(json.loads(e.read().decode()).get("retry_after", 1))
            except Exception:
                wait = 1.0
            time.sleep(min(wait, 30) + 0.2)
        except Exception as e:
            print(f"[discord] loi: {e}", file=sys.stderr)
            time.sleep(1)
    return False


def main():
    text = " ".join(sys.argv[1:]).strip().replace("\\n", "\n").replace("\\t", "\t")
    if not text:
        print('Dung: python notify_discord.py "noi dung"', file=sys.stderr)
        return 1
    url = get_webhook_url()
    if not url or not valid_webhook_url(url):
        print(f"[discord] {ENV_NAME} chua co hoac khong phai webhook Discord", file=sys.stderr)
        return 1
    for i, chunk in enumerate(split_message(text)):
        if i:
            time.sleep(0.6)
        if not post(url, chunk):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
