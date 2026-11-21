// Python 側（ui.py）から window.jarvis.* を呼んで画面を更新する
const STATES = {
  booting: ["BOOTING", "起動中"],
  idle: ["STANDBY", "待機中"],
  listening: ["LISTENING", "認識中"],
  thinking: ["PROCESSING", "思考中"],
  speaking: ["RESPONDING", "応答中"],
  checking: ["CHECKING", "確認中"],   // ファイルを見る・調べるなどの作業中（見た目は thinking と同じ）
  confirm: ["AWAITING CONFIRMATION", "確認待ち　はい／いいえ"],
  alarm: ["ALARM", "「ジャービス」と呼ぶか STOP で停止"],
  sleep: ["SLEEP", "スリープ中　「ジャービス、起きて」で解除"],
  enroll: ["VOICE REGISTRATION", "声の登録"],
};
const MODE_IDLE_LABEL = { normal: "待機中", always: "待機中　話しかけてください", sleep: "スリープ中" };
let currentMode = "normal";
let micMuted = false;
let devOn = false;          // 開発モード（画面と色が切り替わっている間 true）
let devSince = 0;           // 開発モードに入った時刻（ミリ秒）
let devSwitching = false;   // 切り替えアニメーションの間 true
let heldCount = 0;          // 保留リストの件数

const agoText = (tsSec) => {
  const m = Math.floor((Date.now() / 1000 - tsSec) / 60);
  return m < 1 ? "いま" : m < 60 ? `${m}分前` : m < 1440 ? `${Math.floor(m / 60)}時間前` : `${Math.floor(m / 1440)}日前`;
};

// 開発モードの経過時間（バッジ）。1 秒ごとに更新
setInterval(() => {
  if (!devOn || !devSince) return;
  const s = Math.max(0, Math.floor((Date.now() - devSince) / 1000));
  $("dev-elapsed").textContent = `${p2(Math.floor(s / 3600))}:${p2(Math.floor((s % 3600) / 60))}:${p2(s % 60)}`;
}, 1000);

// 画面の中身（色・欄）を開発モードに合わせて入れ替える
function applyDev(on) {
  devOn = on;
  if (on && !devSince) devSince = Date.now();
  if (!on) devSince = 0;
  document.body.classList.toggle("dev", on);
  $("dev-btn").classList.toggle("active", on);
  const sub = $("brand-sub");
  sub.textContent = on ? sub.dataset.dev : sub.dataset.normal;
}
const idleLabel = () => (micMuted ? "マイクミュート中　「ジャービス、マイクオン」で解除" : MODE_IDLE_LABEL[currentMode]);
let timerItems = [];

function remainingText(ms) {
  const s = Math.max(0, Math.round(ms / 1000));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const p = (n) => String(n).padStart(2, "0");
  return h ? `${h}:${p(m)}:${p(sec)}` : `${p(m)}:${p(sec)}`;
}

function renderTimers() {
  const ul = $("timers");
  ul.replaceChildren();
  const now = Date.now();
  const live = timerItems.filter((t) => t.at > now);
  $("timers-block").classList.toggle("is-empty", !live.length);  // 何もないときは欄ごと畳む
  if (!live.length) { ul.append(el("li", "empty", "セットされていません")); return; }
  for (const t of live) {
    const li = el("li");
    if (t.kind === "timer") {
      li.append(el("span", "time", remainingText(t.at - now)), el("span", "", `タイマー（残り）`));
    } else {
      const d = new Date(t.at);
      const tomorrow = d.toDateString() !== new Date().toDateString() ? "明日 " : "";
      li.append(el("span", "time", `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`),
                el("span", "", `${tomorrow}アラーム${t.title && t.title !== "アラーム" ? "：" + t.title : ""}`));
    }
    ul.append(li);
  }
}
setInterval(renderTimers, 1000);
const WEEK = ["日", "月", "火", "水", "木", "金", "土"];
const $ = (id) => document.getElementById(id);
const MAX_LOG = 300;
const p2 = (n) => String(n).padStart(2, "0");

function atBottom(box) {
  return box.scrollHeight - box.scrollTop - box.clientHeight < 40;
}

function scrollToLatest() {
  const box = $("log");
  box.scrollTop = box.scrollHeight;
  $("to-latest").classList.remove("show");
}

// 一覧を差分で更新する。変わっていない行はそのまま残し、新しく増えた行だけ光らせて追加する
function syncList(ul, items, keyOf, build, emptyText) {
  if (items === null) {  // 取得に失敗したときは、前に出ていた内容を残す
    if (!ul.dataset.loaded) ul.replaceChildren(el("li", "empty", "取得できません"));
    return;
  }
  if (!items.length) {
    ul.replaceChildren(el("li", "empty", emptyText));
    ul.dataset.loaded = "1";
    return;
  }
  const old = new Map([...ul.children].filter((li) => li.dataset.key).map((li) => [li.dataset.key, li]));
  const firstLoad = !ul.dataset.loaded;
  const next = items.map((it) => {
    const key = keyOf(it), sig = JSON.stringify(it);
    const prev = old.get(key);
    if (prev && prev.dataset.sig === sig) return prev;  // 変化なし
    const li = build(it);
    li.dataset.key = key;
    li.dataset.sig = sig;
    if (!firstLoad) {
      li.classList.add(prev ? "changed" : "added");
      // 光る演出が終わったら印を外す（付けっぱなしだと、並べ直すたびに演出がやり直されてチラつく）
      li.addEventListener("animationend", () => li.classList.remove("added", "changed"), { once: true });
    }
    return li;
  });
  ul.replaceChildren(...next);
  ul.dataset.loaded = "1";
}

// ---- 入力欄・添付ファイル ----
const MAX_UPLOAD_MB = 25;
let pendingFiles = [];
const sizeText = (b) => (b >= 1048576 ? `${(b / 1048576).toFixed(1)}MB` : `${Math.max(1, Math.round(b / 1024))}KB`);
function renderAttachments() {
  const box = $("attachments");
  box.replaceChildren(...pendingFiles.map((f, i) => {
    const chip = el("span", "chip" + (f.error ? " error" : f.data === null ? " loading" : ""));
    chip.append(el("span", "chip-name", f.name), el("span", "chip-size", f.error || sizeText(f.size)));
    const x = el("button", "chip-x", "×");
    x.type = "button";
    x.title = "外す";
    x.onclick = () => jarvis.removeFile(i);
    chip.append(x);
    return chip;
  }));
  $("composer").classList.toggle("has-files", pendingFiles.length > 0);
}
function autosize() {  // 入力欄は 4 行まで自動で広げる
  const t = $("text-input");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 4 * 24 + 16) + "px";
}
document.addEventListener("DOMContentLoaded", () => {
  const t = $("text-input");
  t.addEventListener("input", autosize);
  t.addEventListener("keydown", (e) => {  // Enter で送信（日本語の変換中は除く）、Shift+Enter で改行
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); jarvis.sendText(); }
  });
  t.addEventListener("paste", (e) => {  // 画像などを Ctrl+V で貼り付けたら添付にする
    const files = [...(e.clipboardData?.files || [])];
    if (files.length) { e.preventDefault(); jarvis.addFiles(files); }
  });
  let depth = 0;  // 画面のどこにドロップしても添付できる
  const overlay = $("drop-overlay");
  const hasFiles = (e) => [...(e.dataTransfer?.types || [])].includes("Files");
  window.addEventListener("dragenter", (e) => { if (hasFiles(e)) { depth++; overlay.classList.add("show"); } });
  window.addEventListener("dragleave", () => { if (--depth <= 0) { depth = 0; overlay.classList.remove("show"); } });
  window.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    depth = 0;
    overlay.classList.remove("show");
    if (e.dataTransfer?.files?.length) { jarvis.addFiles(e.dataTransfer.files); $("text-input").focus(); }
  });
});

// 今日の予定: 終わったものは欄から消す（終了時刻がなければ開始の 1 時間後に終わったとみなす。終日はその日のうちは残す）
let scheduleItems = null;
const toMin = (hhmm) => { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };
function isOver(it, nowMin) {
  if (!/^\d{1,2}:\d{2}$/.test(it.start || "")) return false;  // 終日など
  const start = toMin(it.start);
  let end = /^\d{1,2}:\d{2}$/.test(it.end || "") ? toMin(it.end) : start + 60;
  if (end < start) end += 24 * 60;  // 日付をまたぐ予定（22:00〜01:00 など）
  return nowMin >= end;
}
function renderSchedule() {
  const d = new Date(), nowMin = d.getHours() * 60 + d.getMinutes();
  const items = scheduleItems === null ? null : scheduleItems;
  // 薄くする状態は並べ直しの判定に入れない（入れると終了時に行が作り直されてチラつく）
  syncList($("schedule"), items, (it) => `${it.start}|${it.title}`, (it) => {
    const li = el("li", "");
    const time = /^\d/.test(it.start) && /^\d/.test(it.end || "") ? `${it.start}–${it.end}` : it.start;
    li.append(el("span", "time", time), el("span", "", it.title));
    return li;
  }, "本日の予定はありません");
  // 終わった予定は、行を作り直さずに印だけ付け替える
  if (items) [...$("schedule").children].forEach((li, i) => {
    if (items[i]) li.classList.toggle("over", isOver(items[i], nowMin));
  });
}
setInterval(renderSchedule, 30000);  // 30 秒ごとに、終わった予定を薄い表示にする

// 字幕: 考え中はあなたの依頼、話し中はジャービスの返事を、中央の円の下に出す
let currentState = "booting", lastUser = "", lastJarvis = "";
const clip = (s, n) => (s.length > n ? s.slice(0, n) + "…" : s);
function updateCaption() {
  const cap = $("caption");
  let text = "", cls = "";
  const busy = ["thinking", "checking", "speaking"].includes(currentState);
  if (busy && lastJarvis) {
    text = clip(lastJarvis.replace(/\s+/g, " "), 90);
    cls = "jarvis";
  } else if (busy && lastUser) {
    text = `「${clip(lastUser, 40)}」`;
    cls = "user";
  }
  if (text) cap.textContent = text;
  cap.className = `caption ${cls}` + (text ? " show" : "");
}

// 天気のアイコン（線画）。天気の文字（快晴・曇り・雨…）から選ぶ
const SUN = '<circle cx="24" cy="24" r="8"/><path d="M24 6v5M24 37v5M6 24h5M37 24h5M11.3 11.3l3.5 3.5M33.2 33.2l3.5 3.5M11.3 36.7l3.5-3.5M33.2 14.8l3.5-3.5"/>';
const CLOUD = '<path d="M14 36h21a8 8 0 0 0 0-16 11 11 0 0 0-21.2 3.3A6.5 6.5 0 0 0 14 36z"/>';
const SMALL_SUN = '<circle cx="17" cy="16" r="5"/><path d="M17 5v3M6 16h3M9.2 8.2l2.1 2.1M24.8 8.2l-2.1 2.1"/>';
function weatherIcon(text) {
  if (/雷/.test(text)) return CLOUD + '<path d="M25 37l-4 7h6l-4 6" />';
  if (/雪/.test(text)) return CLOUD + '<path d="M18 42h.1M24 45h.1M30 42h.1" stroke-width="3.5"/>';
  if (/雨/.test(text)) return CLOUD + '<path d="M18 40l-2 5M25 40l-2 5M32 40l-2 5"/>';
  if (/霧/.test(text)) return '<path d="M8 18h32M5 25h38M8 32h32M12 39h24"/>';
  if (/時々|のち/.test(text)) return SMALL_SUN + CLOUD;
  if (/曇/.test(text)) return CLOUD;
  return SUN;
}

let streamEl = null;    // いま文ごとに伸ばしている返事の吹き出し
let streamText = "";    // その全文（これまでに出した分）

// 吹き出しの本文だけを書き換える（右端の時刻の印は残す）
function setBodyText(body, text) {
  if (body.firstChild && body.firstChild.nodeType === Node.TEXT_NODE) body.firstChild.textContent = text;
  else body.prepend(document.createTextNode(text));
}

function buildMessage(role, text, time) {
  const m = el("div", `msg ${role}`);
  m.append(el("div", "who", role === "user" ? "YOU" : "JARVIS"));
  const body = el("div", "body", text);
  body.append(el("span", "t", time));
  m.append(body);
  return m;
}

function tick() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  $("clock").textContent = `${p(d.getHours())}:${p(d.getMinutes())}`;
  $("seconds").textContent = p(d.getSeconds());
  $("date").textContent = `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日（${WEEK[d.getDay()]}）`;
}
tick();
setInterval(tick, 1000);
// pywebview の準備ができたら、保存されているミュート状態をボタンに反映
window.addEventListener("pywebviewready", () => window.pywebview.api.get_mutes().then((m) => jarvis.setMutes(m)));
document.addEventListener("DOMContentLoaded", () => {
  $("log").addEventListener("scroll", (e) => {
    if (atBottom(e.target)) $("to-latest").classList.remove("show");
  });
});

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

window.jarvis = {
  showPopup(title, src, original) {  // サイト・画像・動画を、ダッシュボードの中の窓に出す
    $("popup-title").textContent = title || original;
    $("popup-frame").src = src;
    const pop = $("popup");
    pop.hidden = false;
    // 会話ログに重ならないよう、見出しの下から会話ログの上までの、中央〜右の範囲に収める
    const top = document.querySelector(".top").getBoundingClientRect();
    const log = document.querySelector(".log").getBoundingClientRect();
    const core = document.querySelector(".core").getBoundingClientRect();
    const right = document.querySelector(".right").getBoundingClientRect();
    if (log.top - top.bottom > 120) {
      const s = pop.style;
      s.transform = "none";
      s.left = `${core.left}px`;
      s.top = `${top.bottom + 8}px`;
      s.width = `${right.right - core.left}px`;
      s.height = `${log.top - top.bottom - 16}px`;
    }
  },
  closePopup() {
    $("popup").hidden = true;
    $("popup-frame").src = "about:blank";  // 動画は閉じたら止める
  },
  setState(state) {
    currentState = state;
    document.body.className = `state-${state}${state === "checking" ? " state-thinking" : ""} mode-${currentMode}` + (micMuted ? " mic-muted" : "")
      + (devOn ? " dev" : "") + (devSwitching ? " dev-switching" : "") + (heldCount ? " has-held" : "");
    const [en, ja] = STATES[state] || [state.toUpperCase(), ""];
    $("state-en").textContent = en;
    $("state-ja").textContent = state === "idle" ? idleLabel() : ja;
    if (!["listening", "confirm", "enroll"].includes(state)) this.setLevel(0);
    updateCaption();
  },

  setLevel(v) {
    document.documentElement.style.setProperty("--level", Math.min(1, v * 8).toFixed(3));
  },

  // ---- 返事を文ごとに出す（話す内容は、声より先に・全文を待たずに画面へ） ----
  // 1 文目で吹き出しを作り、次の文が来るたびに足す。字幕（円の下）は、いま話している最新の 1 文
  beginReply(text) {
    streamEl = null;
    lastJarvis = text;
    streamText = text;
    updateCaption();
    const d = new Date();
    const box = $("log");
    const follow = atBottom(box);
    streamEl = buildMessage("jarvis", text, `${p2(d.getHours())}:${p2(d.getMinutes())}`);
    box.append(streamEl);
    while (box.children.length > MAX_LOG) box.firstChild.remove();
    if (follow) scrollToLatest(); else $("to-latest").classList.add("show");
  },

  appendReply(text) {
    if (!streamEl) { this.beginReply(text); return; }
    streamText += text;
    lastJarvis = text;
    updateCaption();
    const box = $("log");
    const follow = atBottom(box);
    setBodyText(streamEl.querySelector(".body"), streamText);
    if (follow) scrollToLatest();
  },

  endReply(full) {  // 返事が終わった。整えた全文に置き換える
    if (streamEl) {
      setBodyText(streamEl.querySelector(".body"), full);
      lastJarvis = full;
      streamEl = null;
      updateCaption();
      if (atBottom($("log"))) scrollToLatest();
    } else {
      this.addMessage("jarvis", full);
    }
  },

  discardReply() {  // 出していた返事を取り消す（PC 内の AI の返事を捨てて、Claude に任せるとき）
    if (streamEl) streamEl.remove();
    streamEl = null;
    lastJarvis = "";
    updateCaption();
  },

  addMessage(role, text) {
    streamEl = null;
    if (role === "user") { lastUser = text; lastJarvis = ""; } else { lastJarvis = text; }
    updateCaption();
    const d = new Date();
    const box = $("log");
    const follow = atBottom(box);  // 過去を読んでいる途中なら、勝手に最新へ飛ばさない
    box.append(buildMessage(role, text, `${p2(d.getHours())}:${p2(d.getMinutes())}`));
    while (box.children.length > MAX_LOG) box.firstChild.remove();
    if (follow) scrollToLatest();
    else $("to-latest").classList.add("show");
  },

  // 起動時に、ログファイルから過去の会話を読み込む。items: [{role, text, time}]
  setHistory(items) {
    const box = $("log");
    box.replaceChildren(...items.slice(-MAX_LOG).map((i) => buildMessage(i.role, i.text, i.time)));
    if (items.length) {
      const sep = el("div", "log-sep", "ここまでが前回までの会話");
      box.append(sep);
    }
    scrollToLatest();
  },

  setWeather(w) {
    if (!w) { $("w-text").textContent = "取得できません"; return; }
    $("w-icon").innerHTML = weatherIcon(w.text);
    $("w-temp").innerHTML = `${Math.round(w.temp)}<small>°C</small>`;
    $("w-text").textContent = w.text;
    $("w-max").textContent = `${Math.round(w.max)}°`;
    $("w-min").textContent = `${Math.round(w.min)}°`;
    $("w-pop").textContent = `${w.pop}%`;
    $("w-hum").textContent = `${w.humidity}%`;
  },

  setSchedule(items) {
    scheduleItems = items;
    renderSchedule();
  },

  setTasks(items) {
    const open = items.filter((t) => !t.done).length;
    $("task-count").textContent = items.length ? `残り ${open} 件` : "";
    const today = `${new Date().getFullYear()}-${p2(new Date().getMonth() + 1)}-${p2(new Date().getDate())}`;
    // 未完了を先に、その中は締切が近い順（締切のないものは最後）。完了済みは後ろに
    const dueKey = (t) => t.due_at || (t.due ? t.due + "T23:59" : "9999");
    const sorted = [...items].sort((a, b) => (a.done ? 1 : 0) - (b.done ? 1 : 0) ||
      (a.done ? 0 : dueKey(a) < dueKey(b) ? -1 : dueKey(a) > dueKey(b) ? 1 : 0));
    syncList($("tasks"), sorted, (t) => String(t.id ?? t.title), (t) => {
      // 締切まで 24 時間以内（過ぎていないもの）は強調する
      const left = t.due_at ? new Date(t.due_at).getTime() - Date.now() : Infinity;
      const soon = !t.done && left > 0 && left <= 24 * 3600 * 1000;
      const li = el("li", (t.done ? "done" : "") + (soon ? " soon" : ""));
      li.append(el("span", "box", t.done ? "✓" : ""), el("span", "title", t.title));
      if (t.due) {
        const state = t.done ? "" : t.due < today ? " over" : t.due === today ? " today" : "";
        const label = state === " today" ? "今日" : t.due.slice(5).replace("-", "/");
        li.append(el("span", "due" + state, label));
      }
      li.onclick = () => { if (window.pywebview) window.pywebview.api.toggle_task(t.id); };
      return li;
    }, "タスクはありません");
  },

  setWeek(items) {
    syncList($("week"), items, (it) => `${it.iso || it.date}|${it.start}|${it.title}`, (it) => {
      const li = el("li");
      const time = el("span", "time", it.start === "終日" ? "終日" : it.start);
      time.prepend(el("em", "", `${it.date}（${it.wd}）`));
      li.append(time, el("span", "", it.title));
      return li;
    }, "今週の予定はありません");
  },

  setNews(items) {
    syncList($("news"), items && items.length ? items : null, (it) => it.title, (it) => {
      const li = el("li");
      li.append(el("span", "t", it.time), el("span", "", it.title));
      return li;
    }, "ニュースはありません");
  },

  setStats(s) {
    for (const k of ["cpu", "mem", "disk"]) {
      $(`s-${k}`).textContent = `${s[k]}%`;
      $(`s-${k}-bar`).style.width = `${s[k]}%`;
    }
  },

  // 声の登録: 何回目か（●○○）と、今どうしてほしいかを出す
  setEnroll(step, total, phase) {
    const msg = { intro: "ピッという合図のあとに、一言お話しください",
                  listen: "どうぞ、お話しください", ok: "覚えました", retry: "もう少し長めにお話しください" }[phase] || "";
    const done = phase === "ok" ? step : step - 1;
    $("state-ja").textContent = `声の登録 ${Math.min(step, total) || 1} / ${total}　${msg}`;
    const dots = $("enroll-dots");
    dots.replaceChildren(...Array.from({ length: total }, (_, i) =>
      el("span", i < done ? "done" : i === done && phase === "listen" ? "now" : "")));
    document.body.classList.toggle("enroll-listening", phase === "listen");
  },

  // ---- 入力欄と添付ファイル ----
  addFiles(list) {
    for (const f of list) {
      if (f.size > MAX_UPLOAD_MB * 1024 * 1024) {
        pendingFiles.push({ name: f.name, size: f.size, error: `${MAX_UPLOAD_MB}MB を超えています` });
        continue;
      }
      const item = { name: f.name, size: f.size, data: null };
      pendingFiles.push(item);
      const reader = new FileReader();
      reader.onload = () => { item.data = String(reader.result).split(",")[1] || ""; renderAttachments(); };
      reader.readAsDataURL(f);
    }
    renderAttachments();
  },

  removeFile(i) { pendingFiles.splice(i, 1); renderAttachments(); },

  sendText(e) {
    if (e) e.preventDefault();
    const box = $("text-input");
    const text = box.value.trim();
    const files = pendingFiles.filter((f) => f.data !== null && !f.error);
    if (pendingFiles.some((f) => f.data === null && !f.error)) return false;  // 読み込み中は待つ
    if (!text && !files.length) return false;
    if (window.pywebview) window.pywebview.api.send_text(text, files.map((f) => ({ name: f.name, data: f.data })));
    box.value = "";
    autosize();
    pendingFiles = [];
    renderAttachments();
    return false;
  },

  setTimers(items) { timerItems = items || []; renderTimers(); },

  setMode(mode) {
    currentMode = mode;
    document.querySelectorAll(".modes button").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
    document.body.classList.remove("mode-normal", "mode-always", "mode-sleep");
    document.body.classList.add(`mode-${mode}`);
    if (document.body.classList.contains("state-idle")) $("state-ja").textContent = idleLabel();
  },

  setMutes(m) {
    for (const kind of ["mic", "speaker", "voiceprint"]) {
      const b = $(`mute-${kind}`);
      if (!b) continue;
      b.classList.toggle("muted", !!m[kind]);
      const name = { mic: "マイク", speaker: "スピーカー", voiceprint: "声紋認証" }[kind];
      const off = kind === "voiceprint" ? "オフ（誰の声にも反応）" : "ミュート中";
      b.title = m[kind] ? `${name}：${off}（押すとオン）` : `${name}：オン（押すと${kind === "voiceprint" ? "オフ" : "ミュート"}）`;
    }
    document.body.classList.toggle("mic-muted", !!m.mic);
    micMuted = !!m.mic;
    if (document.body.classList.contains("state-idle")) $("state-ja").textContent = idleLabel();
  },

  toggleMute(kind) {
    if (window.pywebview) window.pywebview.api.toggle_mute(kind).then((m) => this.setMutes(m));
  },

  requestMode(mode) {
    if (window.pywebview) window.pywebview.api.set_mode(mode);
  },

  setSystem(text) { $("sys-text").textContent = text; },

  // ---- 開発モード ----
  requestDev() {
    if (window.pywebview) window.pywebview.api.set_dev();
  },

  // 切り替え: 光の帯が左から右へ走り、通り過ぎたところから色と欄が入れ替わる（中身の入れ替えは帯が画面の中ほどに来たとき）
  setDev(on, quiet) {
    if (on === devOn) return;
    const flash = $("mode-flash");
    if (quiet || matchMedia("(prefers-reduced-motion: reduce)").matches) { applyDev(on); return; }
    $("mf-title").textContent = on ? "DEVELOPMENT MODE" : "STANDARD MODE";
    $("mf-sub").textContent = on ? "GUARD RELAXED　·　FOCUS ENGAGED" : "ALL SYSTEMS RESTORED";
    flash.className = `mode-flash ${on ? "to-dev" : "to-normal"}`;
    void flash.offsetWidth;  // アニメーションを最初からやり直すための再描画
    flash.classList.add("run");
    devSwitching = true;
    document.body.classList.add("dev-switching");
    setTimeout(() => applyDev(on), 600);
    setTimeout(() => {
      devSwitching = false;
      document.body.classList.remove("dev-switching");
      flash.className = "mode-flash";
    }, 2400);
  },

  heldAction(action, fp) {
    if (window.pywebview) window.pywebview.api.held_action(action, fp);
  },

  setDevInfo(d) {
    const m = d && d.main;
    $("dev-proj-hint").textContent = d && d.others.length ? `他 ${d.others.join(" / ")}` : "";
    const body = $("dev-proj-body");
    if (!m) { body.replaceChildren(el("div", "empty", "プロジェクトが見つかりません")); }
    else {
      const meta = m.git ? `⎇ ${m.branch}　${m.dirty ? `● 未コミット ${m.dirty} 件` : "✓ クリーン"}` : "git 管理なし";
      const line = el("div", "proj-meta", meta);
      if (m.git && m.dirty) line.classList.add("dirty");
      body.replaceChildren(el("div", "proj-name", m.name), line);
    }
    const rows = m ? [
      ...m.commits.map((c) => ({ key: "c" + c.hash, tag: c.hash, text: c.msg, ts: c.ts, cls: "commit" })),
      ...m.recent.map((f) => ({ key: "f" + f.name, tag: "✎", text: f.name.replace(/\\/g, "/"), ts: f.ts, cls: "file" })),
    ] : [];
    syncList($("dev-recent"), rows, (r) => r.key, (r) => {
      const li = el("li", r.cls);
      li.append(el("span", "tag", r.tag), el("span", "txt", r.text), el("span", "ago", agoText(r.ts)));
      return li;
    }, "最近の変更はありません");
    syncList($("dev-errors"), (d && d.errors) || [], (e) => e.t + e.msg, (e) => {
      const li = el("li", "err");
      li.append(el("span", "tag", e.t), el("span", "txt", e.msg));
      return li;
    }, "エラーはありません");
  },

  setDevActivity(a) {
    if (a.since) devSince = a.since * 1000;
    if (a.until) {  // 時間指定（「7時まで」）があれば、バッジに終わる時刻を出す
      const d = new Date(a.until * 1000);
      const tomorrow = d.toDateString() !== new Date().toDateString() ? "明日 " : "";
      $("dev-until").textContent = `〜 ${tomorrow}${p2(d.getHours())}:${p2(d.getMinutes())}`;
    } else {
      $("dev-until").textContent = "";
    }
    heldCount = a.held.length;
    document.body.classList.toggle("has-held", heldCount > 0);
    $("held-count").textContent = heldCount ? `${heldCount} 件　承認待ち` : "";
    $("held-all").hidden = heldCount < 2;
    syncList($("held"), a.held, (h) => h.fp, (h) => {
      const li = el("li", "held-item");
      const ok = el("button", "mini-btn ok", "承認");
      ok.onclick = () => this.heldAction("approve", h.fp);
      const no = el("button", "mini-btn no", "却下");
      no.onclick = () => this.heldAction("reject", h.fp);
      li.append(el("span", "txt", h.desc), ok, no);
      return li;
    }, "保留はありません");
    syncList($("dev-passed"), [...a.events].reverse(), (e) => e.t + e.desc, (e) => {
      const li = el("li");
      li.append(el("span", "tag", e.t), el("span", "txt", e.desc));
      return li;
    }, "まだありません");
  },

  setDevTasks(items) {
    const open = items.filter((t) => !t.done).length;
    $("dev-task-count").textContent = items.length ? `残り ${open} 件` : "";
    const sorted = [...items].sort((a, b) => (a.done ? 1 : 0) - (b.done ? 1 : 0));
    syncList($("dev-tasks"), sorted, (t) => String(t.id ?? t.title), (t) => {
      const li = el("li", t.done ? "done" : "");
      li.append(el("span", "box", t.done ? "✓" : ""), el("span", "title", t.title));
      if (t.due) li.append(el("span", "due", t.due.slice(5).replace("-", "/")));
      li.onclick = () => { if (window.pywebview) window.pywebview.api.toggle_dev_task(t.id); };
      return li;
    }, "開発のやることはありません");
  },
};
