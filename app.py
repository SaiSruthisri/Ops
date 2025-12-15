# app.py
from flask import Flask, request, jsonify, render_template_string
import requests
import os
import json
import re
from google.cloud import firestore
from google.cloud.firestore import ArrayUnion
from datetime import datetime
from dotenv import load_dotenv
import google.auth
from google.oauth2 import service_account

# ================== ENV SETUP ==================
# load_dotenv()
# GOOGLE_CREDS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
# os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = GOOGLE_CREDS


# ================== CONFIG ==================
FIREBASE_PROJECT_ID = "ud-internal-ops"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")  # Put your key in .env
COLLECTION_NAME = "UD_internal_ops"

KB_OPTIONS = {
    "Master UD (Default)": "master_ud",
    "Master Onboarding": "master_onboarding",
    "Client – Blueflute": "client_blueflute",
    "Client – Vibrant Living": "client_vibrant_living",
}
DEFAULT_MASTER_KB = "master_onboarding"


creds_json = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_JSON")
creds_dict = json.loads(creds_json)
creds = service_account.Credentials.from_service_account_info(creds_dict)
db = firestore.Client(credentials=creds, project=FIREBASE_PROJECT_ID)
# ================== FIRESTORE ==================
# db = firestore.Client(project=FIREBASE_PROJECT_ID)

def format_user_updates(updates: list) -> str:
    if not updates:
        return ""
    updates = sorted(updates, key=lambda x: x.get("added_at", ""), reverse=True)
    return "\n".join([f"- {u.get('info')} (Added at: {u.get('added_at')}, Source: {u.get('source')})" for u in updates])

def get_doc_content(doc_id: str) -> str:
    doc = db.collection(COLLECTION_NAME).document(doc_id).get()
    if not doc.exists:
        return ""
    data = doc.to_dict()
    static = json.dumps(data.get("static_content", {}), indent=2)
    updates = format_user_updates(data.get("user_updates", []))
    return f"===== STATIC CONTENT =====\n{static}\n\n===== USER UPDATES =====\n{updates}"

def get_combined_knowledge(active_doc: str) -> str:
    if active_doc == DEFAULT_MASTER_KB:
        return get_doc_content(DEFAULT_MASTER_KB)
    master = get_doc_content(DEFAULT_MASTER_KB)
    client = get_doc_content(active_doc)
    return f"===== MASTER UD =====\n{master}\n\n===== CLIENT SPECIFIC =====\n{client}"

def append_user_update(doc_id: str, user_fact: str):
    doc_ref = db.collection(COLLECTION_NAME).document(doc_id)
    update_block = {
        "info": user_fact,
        "added_at": datetime.utcnow().isoformat(),
        "source": "user_chat"
    }
    doc_ref.set({"user_updates": ArrayUnion([update_block])}, merge=True)

def handle_kb_write(active_doc: str, fact: str):
    append_user_update(DEFAULT_MASTER_KB, fact)
    if active_doc != DEFAULT_MASTER_KB:
        append_user_update(active_doc, fact)

def ask_gemini(question, knowledge):
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    prompt = f"""
You are the official internal assistant for UrbanDart.
Be friendly, precise, and professional.

ROLE:
- Answer questions strictly using the provided knowledge
- Never invent facts
- Never assume missing information

====================
KNOWLEDGE BASE START
====================
{knowledge}
====================
KNOWLEDGE BASE END
====================

IMPORTANT:
- Read the ENTIRE knowledge base
- KB may contain old information
- User-added information is MOST RECENT and has priority
- Do NOT rely on headings alone
- Be case-insensitive and meaning-based

ANSWER RULES:
- If answer exists → answer confidently
- If conflicting info → prefer newest and say so
- If not found → say exactly:
  "I don’t have that information yet."

CRITICAL WRITE RULE:
- You NEVER add to the knowledge base
- You NEVER decide what is new information
- ONLY output NEW if user explicitly types it

ALLOWED WRITE FORMAT ONLY:
NEW: <exact user text>

If user does NOT explicitly write NEW:
- DO NOT output NEW
- DO NOT summarize as new info

User message:
{question}
"""
    payload = {"contents":[{"parts":[{"text":prompt}]}]}
    response = requests.post(url, json=payload)
    response.raise_for_status()
    result = response.json()
    return result["candidates"][0]["content"]["parts"][0]["text"]

# ================== FLASK APP ==================
app = Flask(__name__)

# Frontend template (all-in-one)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>UD Internal Ops Assistant</title>
<style>
body {
    background-color: #111;
    color: white;
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
    display: flex;
    flex-direction: column;
    align-items: center;
    min-height: 100vh;
    margin: 0;
    padding: 0;
}
h1 {
    text-align: center;
    margin-top: 1rem;
}
#chat-container {
    max-width: 800px;
    width: 90%;
    flex: 1;
    overflow-y: auto;
    padding: 1rem;
    margin: 1rem 0;
    border: 1px solid #333;
    border-radius: 10px;
    background-color: #1a1a1a;
    display: flex;
    flex-direction: column;
}
.message-wrapper {
    display: flex;
    margin: 0.5rem 0;
    align-items: flex-end;
}
.message-wrapper.user {
    justify-content: flex-end;
}
.message-wrapper.assistant {
    justify-content: flex-start;
}
.avatar {
    width: 40px;
    height: 40px;
    border-radius: 50%;
    margin: 0 0.5rem;
    object-fit: cover;
}
.message {
    padding: 0.75rem 1rem;
    border-radius: 15px;
    max-width: 70%;
    word-wrap: break-word;
    display: inline-block;
}
.user .message {
    background-color: #333;
    color: #fff;
    text-align: right;
}
.assistant .message {
    background-color: #222;
    color: #fff;
    text-align: left;
}
#input-container {
    display: flex;
    width: 90%;
    max-width: 800px;
    margin-bottom: 1rem;
}
#userInput {
    flex: 1;
    padding: 0.75rem 1rem;
    border-radius: 10px 0 0 10px;
    border: none;
    outline: none;
    font-size: 1rem;
}
#sendBtn {
    padding: 0.75rem 1rem;
    border: none;
    background-color: #4caf50;
    color: white;
    font-weight: bold;
    cursor: pointer;
    border-radius: 0 10px 10px 0;
    transition: background-color 0.3s;
}
#sendBtn:hover {
    background-color: #45a049;
}
#kbSelector {
    margin-bottom: 1rem;
    padding: 0.5rem 1rem;
    border-radius: 10px;
    border: none;
    font-size: 1rem;
}
</style>
</head>
<body>
<h1>💬 UD Internal Ops Assistant</h1>

<select id="kbSelector">
{% for label, doc in kb_options.items() %}
<option value="{{doc}}">{{label}}</option>
{% endfor %}
</select>

<div id="chat-container"></div>

<div id="input-container">
<input type="text" id="userInput" placeholder="Ask a question or add knowledge (NEW: ...)">
<button id="sendBtn">Send</button>
</div>

<script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
<script>
let messages = [];
const chatEl = document.getElementById("chat-container");
const inputEl = document.getElementById("userInput");
const sendBtn = document.getElementById("sendBtn");
const kbSelector = document.getElementById("kbSelector");

const userAvatar = "/mnt/data/4b74f16e-7553-4b8f-acb6-59295e456d1f.png"; // Your uploaded image
const botAvatar = "https://cdn-icons-png.flaticon.com/512/4712/4712027.png"; // Bot face placeholder

function addMessage(role, content){
  messages.push({role, content});
  const wrapper = document.createElement("div");
  wrapper.className = 'message-wrapper ' + role;

  const msgDiv = document.createElement("div");
  msgDiv.className = 'message';
  msgDiv.innerHTML = marked.parse(content);

  const avatarImg = document.createElement("img");
  avatarImg.className = "avatar";
  avatarImg.src = role === "user" ? userAvatar : botAvatar;

  if(role === "user"){
    wrapper.appendChild(msgDiv);
    wrapper.appendChild(avatarImg);
  } else {
    wrapper.appendChild(avatarImg);
    wrapper.appendChild(msgDiv);
  }

  chatEl.appendChild(wrapper);
  chatEl.scrollTop = chatEl.scrollHeight;
}

sendBtn.addEventListener("click", async () => {
  const text = inputEl.value.trim();
  if(!text) return;
  addMessage("user", text);
  inputEl.value = "";
  const activeKB = kbSelector.value;

  try {
    const resp = await fetch("/ask", {
      method:"POST",
      headers:{"Content-Type":"application/json"},
      body: JSON.stringify({question:text, active_kb:activeKB})
    });
    const data = await resp.json();
    addMessage("assistant", data.answer);
  } catch(err){
    addMessage("assistant", "⚠️ Error: Could not get a response.");
    console.error(err);
  }
});

inputEl.addEventListener("keypress", function(e) {
  if (e.key === "Enter") {
    sendBtn.click();
  }
});
</script>
</body>
</html>

"""


@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE, kb_options=KB_OPTIONS)

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json()
    question = data.get("question")
    active_kb = data.get("active_kb", DEFAULT_MASTER_KB)
    
    if re.match(r"(?i)^new\s*:", question):
        fact = re.sub(r"(?i)^new\s*:", "", question).strip()
        handle_kb_write(active_kb, fact)
        return jsonify({"answer":"✅ Saved. Information has been added to the knowledge base."})
    
    knowledge = get_combined_knowledge(active_kb)
    answer = ask_gemini(question, knowledge)
    return jsonify({"answer": answer})

# ================== RUN ==================
if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5001, debug=True)
