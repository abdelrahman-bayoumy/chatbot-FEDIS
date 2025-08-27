import os, uuid, json, datetime, re, threading
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, make_response
from collections import deque

load_dotenv()

# ---------- Memory (very small JSON store) ----------
DATA_PATH = Path("data/memory.json")
DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
_LOCK = threading.Lock()

def _load_all():
    if not DATA_PATH.exists(): return {}
    try:
        return json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

def _save_all(obj):
    DATA_PATH.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def normalize_key(k: str) -> str:
    return re.sub(r"[^\w\s-]", "", (k or "").strip().lower())

class MemoryStore:
    def __init__(self): self._cache = _load_all()
    def remember(self, user_id: str, key: str, value: str):
        key_n = normalize_key(key)
        with _LOCK:
            user_mem = self._cache.setdefault(user_id, {})
            user_mem[key_n] = (value or "").strip()
            _save_all(self._cache)
    def recall(self, user_id: str, key: str):
        key_n = normalize_key(key)
        with _LOCK:
            return self._cache.get(user_id, {}).get(key_n)
    def list_facts(self, user_id: str):
        with _LOCK:
            return dict(self._cache.get(user_id, {}))

REMEMBER_RE = re.compile(r"""^\s*(?:please\s+)?(?:remember\s+(?:that\s+)?)?my\s+(.+?)\s+(?:is|=)\s+(.+?)\s*$""", re.I)
RECALL_RE   = re.compile(r"""^\s*(?:what|when)(?:\s+is|'s)\s+my\s+(.+?)\s*\??\s*$""", re.I)

def try_parse_remember(text: str):
    m = REMEMBER_RE.match(text or "")
    return (m.group(1), m.group(2)) if m else None

def try_parse_recall(text: str):
    m = RECALL_RE.match(text or "")
    return m.group(1) if m else None

# ---------- LLM helpers (Groq primary, OpenAI optional fallback) ----------
def chat_with_groq(prompt: str):
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key: return None
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        model = os.getenv("GROQ_MODEL", "llama3-8b-8192")
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a helpful, concise assistant."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )
        return resp.choices[0].message.content
    except Exception:
        return None

def chat_with_openai(prompt: str):
    key = os.getenv("OPENAI_API_KEY")
    if not key: return None
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key)
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a helpful, concise assistant."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.4,
        )
        return resp.choices[0].message.content
    except Exception:
        return None

def llm_chat(prompt: str):
    return chat_with_groq(prompt) or chat_with_openai(prompt)

# ---------- Flask app ----------
app = Flask(__name__)
mem = MemoryStore()

LOGS_DIR = Path("logs"); LOGS_DIR.mkdir(parents=True, exist_ok=True)
STRUCT_LOG = LOGS_DIR / "conversations.jsonl"

def read_history(user_id: str, limit: int = 100):
    """
    Return the last `limit` chat messages for this user from conversations.jsonl.
    Each line in the file is a JSON object with {user_id, role, message, ts}.
    """
    if not STRUCT_LOG.exists():
        return []
    items = deque(maxlen=limit)
    with open(STRUCT_LOG, "r", encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("user_id") == user_id and rec.get("role") in ("user", "assistant"):
                items.append({
                    "role": rec.get("role"),
                    "message": rec.get("message", ""),
                    "ts": rec.get("ts")
                })
    return list(items)

def log_event(event: dict):
    event = dict(event)
    event["ts"] = datetime.datetime.utcnow().isoformat() + "Z"
    with open(STRUCT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")

def get_or_set_user(resp):
    from flask import request as _req
    uid = _req.cookies.get("uid")
    if not uid:
        uid = str(uuid.uuid4())
        resp.set_cookie("uid", uid, max_age=60*60*24*365*2, samesite="Lax")
    return uid

@app.get("/")
def index():
    resp = make_response(render_template("index.html"))
    get_or_set_user(resp)
    return resp




@app.get("/history")
def history():
    # build a Response so we can set uid cookie if missing
    payload = {"items": []}
    response = make_response(jsonify(payload))
    user_id = get_or_set_user(response)

    # parse limit (default 100)
    try:
        limit = int(request.args.get("limit", "100"))
    except ValueError:
        limit = 100

    payload["items"] = read_history(user_id, limit)
    response.set_data(json.dumps(payload))
    response.mimetype = "application/json"
    return response

@app.get("/export")
def export_history():
    resp = make_response()
    user_id = get_or_set_user(resp)
    items = read_history(user_id, 10000)
    resp.headers["Content-Type"] = "application/json"
    resp.headers["Content-Disposition"] = "attachment; filename=history.json"
    resp.set_data(json.dumps({"user_id": user_id, "items": items}, ensure_ascii=False, indent=2))
    return resp

@app.post("/clear")
def clear_history():
    resp = make_response(jsonify({"ok": True}))
    user_id = get_or_set_user(resp)
    if STRUCT_LOG.exists():
        kept = []
        with open(STRUCT_LOG, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    if rec.get("user_id") != user_id:
                        kept.append(line)
                except json.JSONDecodeError:
                    kept.append(line)
        with open(STRUCT_LOG, "w", encoding="utf-8") as f:
            f.writelines(kept)
    return resp



@app.post("/chat")
def chat():
    data = request.get_json(force=True, silent=True) or {}
    user_msg = (data.get("message") or "").strip()

    # build a response so we can attach Set-Cookie if uid is missing
    response = make_response()
    user_id = get_or_set_user(response)

    if not user_msg:
        response.set_data(json.dumps({"reply": "Tell me something and I’ll try to help."}))
        response.mimetype = "application/json"
        return response

    log_event({"user_id": user_id, "role": "user", "message": user_msg})

    # write memory
    kv = try_parse_remember(user_msg)
    if kv:
        k, v = kv
        mem.remember(user_id, k, v)
        bot = f"I’ll remember your {k.strip()} is {v.strip()}."
        log_event({"user_id": user_id, "role": "assistant", "message": bot})
        response.set_data(json.dumps({"reply": bot}))
        response.mimetype = "application/json"
        return response

    # read memory
    rk = try_parse_recall(user_msg)
    if rk:
        v = mem.recall(user_id, rk)
        bot = f"You told me your {rk.strip()} is {v}." if v else \
              f"I don’t have your {rk.strip()} yet. You can say: “remember my {rk.strip()} is …”."
        log_event({"user_id": user_id, "role": "assistant", "message": bot})
        response.set_data(json.dumps({"reply": bot}))
        response.mimetype = "application/json"
        return response

    # general chat
    facts = mem.list_facts(user_id)
    context = ""
    if facts:
        facts_str = "; ".join([f"{k}={v}" for k, v in facts.items()])
        context = f"(User facts: {facts_str})\n"

    prompt = f"{context}User said: {user_msg}\nRespond helpfully and briefly."
    bot = llm_chat(prompt) or ("Got it. " + user_msg)

    log_event({"user_id": user_id, "role": "assistant", "message": bot})
    response.set_data(json.dumps({"reply": bot}))
    response.mimetype = "application/json"
    return response


    facts = mem.list_facts(user_id)
    context = ""
    if facts:
        facts_str = "; ".join([f"{k}={v}" for k, v in facts.items()])
        context = f"(User facts: {facts_str})\n"

    prompt = f"{context}User said: {user_msg}\nRespond helpfully and briefly."
    bot = llm_chat(prompt) or ("Got it. " + user_msg)

    log_event({"user_id": user_id, "role": "assistant", "message": bot})
    return jsonify({"reply": bot})

if __name__ == "__main__":
    import os, argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", 5000)))
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    args = parser.parse_args()

    # create folders if missing
    from pathlib import Path
    Path("templates").mkdir(exist_ok=True)
    Path("static").mkdir(exist_ok=True)
    Path("data").mkdir(exist_ok=True)

    app.run(debug=True, host=args.host, port=args.port)
