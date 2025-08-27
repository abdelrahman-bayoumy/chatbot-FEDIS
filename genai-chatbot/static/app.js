const chat = document.getElementById("chat");
const form = document.getElementById("composer");
const input = document.getElementById("input");
const historyList = document.getElementById("historyList");
const exportBtn = document.getElementById("exportBtn");
const clearBtn = document.getElementById("clearBtn");

let msgCounter = 0;

/* Create a chat bubble and return its index */
function addMessage(text, who = "bot") {
  const div = document.createElement("div");
  div.className = `msg ${who}`;
  div.textContent = text;
  div.dataset.idx = msgCounter++;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return Number(div.dataset.idx);
}

function summarize(text, n = 60) {
  text = (text || "").replace(/\s+/g, " ").trim();
  return text.length > n ? text.slice(0, n - 1) + "…" : text || "(empty)";
}

function fmtTime(ts) {
  try { return new Date(ts).toLocaleString(); } catch { return ""; }
}

function scrollToIdx(idx) {
  const el = chat.querySelector(`.msg[data-idx='${idx}']`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.add("highlight");
  setTimeout(() => el.classList.remove("highlight"), 1200);
}

/* Build the sidebar list from user messages */
function renderSidebar(userItems, userIdx) {
  historyList.innerHTML = "";
  userItems.forEach((m, i) => {
    const li = document.createElement("li");
    li.className = "hitem";
    li.innerHTML = `${summarize(m.message)}<span class="htime">${fmtTime(m.ts)}</span>`;
    li.addEventListener("click", () => scrollToIdx(userIdx[i]));
    historyList.appendChild(li);
  });
}

/* Load history: populate chat + sidebar */
async function loadHistory(limit = 100) {
  try {
    const res = await fetch(`/history?limit=${limit}`);
    const data = await res.json();
    const items = data.items || [];
    if (!items.length) {
      addMessage('Hi! I can remember facts. Try: “remember my birthday is Nov 9”, then ask “what is my birthday?”');
      return;
    }
    const userItems = [];
    const userIdx = [];
    items.forEach((m) => {
      const idx = addMessage(m.message, m.role === "user" ? "user" : "bot");
      if (m.role === "user") { userItems.push(m); userIdx.push(idx); }
    });
    renderSidebar(userItems, userIdx);
  } catch (e) {
    addMessage("Could not load history. Try again.");
  }
}

/* Composer */
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;

  const idx = addMessage(text, "user");
  input.value = ""; input.focus();

  // also append to sidebar immediately
  const li = document.createElement("li");
  li.className = "hitem";
  li.innerHTML = `${summarize(text)}<span class="htime">${fmtTime(new Date().toISOString())}</span>`;
  li.addEventListener("click", () => scrollToIdx(idx));
  historyList.prepend(li); // newest on top

  try {
    const res = await fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text })
    });
    const data = await res.json();
    addMessage(data.reply || "…");
  } catch {
    addMessage("Network error. Please try again.");
  }
});

/* Export & Clear */
exportBtn?.addEventListener("click", () => { window.location.href = "/export"; });

clearBtn?.addEventListener("click", async () => {
  if (!confirm("Clear your chat history? This cannot be undone.")) return;
  try {
    await fetch("/clear", { method: "POST" });
    chat.innerHTML = "";
    historyList.innerHTML = "";
    addMessage("History cleared. Start a new chat!");
  } catch {
    addMessage("Could not clear history.");
  }
});

/* boot */
loadHistory(200);





