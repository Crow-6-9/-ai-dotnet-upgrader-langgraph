import os, re, requests, secrets, hashlib, time
import jwt  # PyJWT

# helper to read files
def read_text(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        return f.read()

def collect_csproj_files(root_dir):
    out = []
    for r, _, files in os.walk(root_dir):
        for f in files:
            if f.endswith(".csproj"):
                out.append(os.path.join(r, f))
    return out

def detect_private_feeds(project_root):
    feeds = []
    for r, _, files in os.walk(project_root):
        for f in files:
            if f.lower() == "nuget.config":
                txt = read_text(os.path.join(r, f))
                feeds += re.findall(r'<add key=".*?" value="(.*?)"', txt)
    return list(set(feeds))

# JWT generator for feed auth (ephemeral)
def generate_jwt_token_for_feed(feed_url: str, subject: str = "ai-upgrader", ttl_seconds: int = 300, secret: str = None):
    """
    Generate an HS256 JWT token for use in Authorization: Bearer <token>.
    If secret is None, we derive a random secret per feed (non-persisted).
    In production, replace with your real token issuance (Azure AD, etc.).
    """
    if not secret:
        # derive deterministic-ish secret from feed_url + random salt
        salt = secrets.token_hex(8)
        secret = hashlib.sha256((feed_url + salt).encode()).hexdigest()
    now = int(time.time())
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + ttl_seconds,
        "feed": feed_url
    }
    token = jwt.encode(payload, secret, algorithm="HS256")
    # return the token (and optionally the secret if you want to verify later)
    return token

def get_latest_nuget_version_for_feed(package_name: str, feed_url: str = None, token: str = None):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    # try private feed first
    if feed_url:
        try:
            url = f"{feed_url.rstrip('/')}/v3-flatcontainer/{package_name.lower()}/index.json"
            r = requests.get(url, headers=headers, timeout=6)
            if r.status_code == 200:
                versions = r.json().get("versions", [])
                stable = [v for v in versions if "-" not in v]
                return stable[-1] if stable else (versions[-1] if versions else None)
        except Exception:
            pass
    # fallback to public
    try:
        url = f"https://api.nuget.org/v3-flatcontainer/{package_name.lower()}/index.json"
        r = requests.get(url, timeout=6)
        if r.status_code == 200:
            versions = r.json().get("versions", [])
            stable = [v for v in versions if "-" not in v]
            return stable[-1] if stable else (versions[-1] if versions else None)
    except Exception:
        pass
    return None
