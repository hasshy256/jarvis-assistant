// ジャービスのリモコン（最小版）。できるのは会話だけ。
// 文字で送るか、🎙 か円を押している間に話して離すと送る（文字起こしは iPhone がする）。返事は PC のジャービスと同じ声で読み上げる（ミュートできる）。
// 合言葉（トークン）はこの iPhone の中（localStorage）にだけ保存する。
const BUILD = "3.2";
const $ = (id) => document.getElementById(id);
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch {} },
  del(k) { try { localStorage.removeItem(k); } catch {} },
};
let token = store.get("jarvisToken") || "";
let muted = store.get("jarvisMuted") === "1";
let waiting = 0;   // 返事を待っている依頼の数（待っている間も続けて送れる。PC は届いた順に 1 つずつ処理する）
const HINT = "🎙 か円を押している間だけ聞き取ります。離すと送ります";
const TAP_HINT = "🎙 か円をタップして話し、もう一度タップで送ります";
const WD = "日月火水木金土";
const root = document.documentElement.classList;  // 録音の状態（holding / recording）を付ける

// ---- 時計 ----
function tick() {
  const d = new Date();
  $("time").innerHTML = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}` +
    `<small>${String(d.getSeconds()).padStart(2, "0")}</small>`;
  $("date").textContent = `${d.getFullYear()}.${d.getMonth() + 1}.${d.getDate()}（${WD[d.getDay()]}）`;
}
tick();
setInterval(tick, 1000);

// ---- PC とのやりとり ----
async function request(path, body, type) {
  const headers = { "Authorization": "Bearer " + token };
  if (body !== undefined) headers["Content-Type"] = type || "application/json";
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers,
    body: body === undefined ? undefined : (type ? body : JSON.stringify(body)),
  });
  if (res.status === 401) { askToken("トークンが違います"); throw new Error("401"); }
  if (!res.ok) throw new Error("HTTP " + res.status);
  return res;
}
const api = async (path, body, type) => (await request(path, body, type)).json();

// うまく動かないときに原因を調べられるよう、iPhone 側の様子を PC の記録（logs/remote）に送る
function clientLog(msg) {
  request("/api/client-log", { text: String(msg).slice(0, 500) }).catch(() => {});
}

function setLink(ok) {
  $("link").textContent = ok ? "ONLINE" : "OFFLINE";
  $("link").classList.toggle("off", !ok);
}

function hint(text, rec) {
  $("hint").textContent = text || (outdated ? "新しい版があります。アプリを閉じて開き直してください" :
    micBroken ? KEYBOARD_HINT : tapMode ? TAP_HINT : HINT);
  $("hint").classList.toggle("rec", !!rec);
}

// ---- 会話ログ ----
function addMessage(role, text, time) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  const t = document.createElement("time");
  t.textContent = time || new Date().toTimeString().slice(0, 5);
  const body = document.createElement("span");
  body.textContent = text;
  div.append(t, body);
  $("log").append(div);
  $("log").scrollTop = $("log").scrollHeight;
  return div;
}

// ジャービスが PC から送ってきたファイル。タップで受け取る（共有シートが開ければ「ファイルに保存」などが選べる）
function addFiles(div, files) {
  for (const f of files || []) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "filebtn";
    btn.textContent = "⬇ " + f.name;
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const blob = await (await request("/api/file/" + encodeURIComponent(f.id))).blob();
        const file = new File([blob], f.name, { type: blob.type });
        if (navigator.canShare && navigator.canShare({ files: [file] })) {
          await navigator.share({ files: [file] });
        } else {
          const a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = f.name;
          a.click();
          setTimeout(() => URL.revokeObjectURL(a.href), 60000);
        }
      } catch (e) {
        if (e.name !== "AbortError") btn.textContent = "⚠ 受け取れませんでした（PC を再起動した後は、もう一度頼んでください）";
      } finally { btn.disabled = false; }
    });
    div.append(btn);
  }
}

async function loadHistory() {
  try {
    const items = await api("/api/history");
    setLink(true);
    $("log").innerHTML = "";
    for (const m of items.slice(-50)) addFiles(addMessage(m.role, m.text, m.time), m.files);
  } catch {}
}

// ---- 読み上げ（PC のジャービスと同じ声） ----
let ctx = null;     // AudioContext。ボタンを押したときに作っておくと、あとで返事の音を鳴らせる
let player = null;

function ensureAudio() {
  try {
    ctx = ctx || new (window.AudioContext || window.webkitAudioContext)();
    if (ctx.state !== "running") ctx.resume().catch(() => {});  // 待たない（iPhone では返ってこないことがある）
  } catch {}
}

let voiceQueue = Promise.resolve();  // 読み上げの順番待ち
let voiceGen = 0;                    // 止めたら増やす。順番待ちしていた読み上げも取りやめる

function stopVoice() {
  voiceGen++;
  if (player) { try { player.stop(); } catch {} player = null; }
}

// 返事を読み上げる。前の返事を読んでいる途中なら、読み終えてから読む
function speak(text) {
  if (muted || !text) return;
  const gen = voiceGen;
  const audio = request("/api/tts", { text })  // 音声は先に作り始めておく
    .then((res) => res.arrayBuffer())
    .then((buf) => { ensureAudio(); return ctx.decodeAudioData(buf); });
  audio.catch(() => {});
  voiceQueue = voiceQueue.then(async () => {
    try {
      const data = await audio;
      if (muted || gen !== voiceGen) return;
      player = ctx.createBufferSource();
      player.buffer = data;
      player.connect(ctx.destination);
      const ended = new Promise((r) => { player.onended = r; });
      player.start();
      await ended;
    } catch (e) {
      clientLog("読み上げに失敗: " + e);
    } finally {
      player = null;
    }
  });
}

function showMute() {
  $("muteBtn").textContent = muted ? "🔇 ミュート" : "🔊 声あり";
  $("muteBtn").classList.toggle("off", muted);
}
$("muteBtn").addEventListener("click", () => {
  muted = !muted;
  store.set("jarvisMuted", muted ? "1" : "0");
  showMute();
  if (muted) stopVoice(); else ensureAudio();
});
showMute();

// ---- 依頼を送る（文字でも声でも同じ流れ） ----
// 返事は、その依頼の「…」の吹き出しに入れる（続けて送っても、依頼と返事の並びがずれない）
async function submit(text, files) {
  addMessage("user", (text ? text + "\n" : "") + (files && files.length ? "📎 " + files.map((f) => f.name).join("、") : ""));
  const div = addMessage("jarvis pending", "…");
  waiting++;
  document.body.className = "thinking";
  let awaiting = false;
  try {
    const r = files && files.length
      ? await api("/api/command-files", { text, files: await Promise.all(files.map(encodeFile)) })
      : await api("/command", { text });
    div.classList.remove("pending");
    div.querySelector("span").textContent = r.reply;
    addFiles(div, r.files);
    if (r.action) {  // アラームなど iPhone 本体の操作は、最小版ではまだ使えない
      const note = document.createElement("span");
      note.className = "note";
      note.textContent = "※ iPhone の操作（アラームなど）はまだ使えません";
      div.append(note);
    }
    awaiting = !!r.awaiting;
    setLink(true);
    speak(r.reply);
  } catch (e) {
    div.remove();
    if (e.message !== "401") {
      clientLog("送信に失敗: " + e);
      addMessage("jarvis error", "PC に届きませんでした。PC のジャービスと Tailscale が動いているか確かめてください。");
      setLink(false);
    }
  } finally {
    waiting--;
    document.body.className = waiting ? "thinking" : awaiting ? "confirm" : "";
    $("log").scrollTop = $("log").scrollHeight;
  }
}

// 文字で送る
const box = $("text");
function updateFilled() { $("form").classList.toggle("filled", !!box.value.trim() || attached.length > 0); }
box.addEventListener("input", () => { box.style.height = "auto"; box.style.height = box.scrollHeight + "px"; updateFilled(); });
box.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("form").requestSubmit(); }
});
$("form").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = box.value.trim();
  if (!text && !attached.length) return;
  box.value = "";
  box.style.height = "auto";
  const files = attached;
  attached = [];
  showAttached();
  ensureAudio();
  submit(text, files);
});

// ---- ファイル・写真の添付（📎） ----
let attached = [];
const MAX_MB = 25;
$("attach").addEventListener("click", () => $("file").click());
$("file").addEventListener("change", () => {
  for (const f of $("file").files) {
    if (f.size > MAX_MB * 1024 * 1024) { addMessage("jarvis error", f.name + " は " + MAX_MB + "MB を超えているので添付できません。"); continue; }
    attached.push(f);
  }
  $("file").value = "";
  showAttached();
});
function showAttached() {
  $("attach").classList.toggle("has", attached.length > 0);
  $("attach").textContent = attached.length ? "📎" + attached.length : "📎";
  updateFilled();
  if (attached.length) hint("添付: " + attached.map((f) => f.name).join("、") + "（送信で送ります）");
  else hint();
}
// 大きい写真は縮めてから送る（通信が速くなり、読み取りには十分）
async function encodeFile(f) {
  let blob = f, name = f.name;
  if (/^image\/(jpeg|png|heic|heif|webp)/.test(f.type) && f.size > 1.5 * 1024 * 1024) {
    try {
      const bmp = await createImageBitmap(f);
      const scale = Math.min(1, 2000 / Math.max(bmp.width, bmp.height));
      const cv = document.createElement("canvas");
      cv.width = Math.round(bmp.width * scale); cv.height = Math.round(bmp.height * scale);
      cv.getContext("2d").drawImage(bmp, 0, 0, cv.width, cv.height);
      blob = await new Promise((ok) => cv.toBlob(ok, "image/jpeg", 0.85));
      if (blob) name = name.replace(/\.[^.]+$/, "") + ".jpg"; else blob = f;
    } catch { blob = f; }
  }
  const data = await new Promise((ok, ng) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result).split(",")[1] || "");
    r.onerror = ng;
    r.readAsDataURL(blob);
  });
  return { name, data };
}

// ---- 声で送る（🎙 か円を押している間だけ聞き取る） ----
// 文字起こしは iPhone（Safari の音声認識）がして、できた文を文字の依頼として PC に送る。
// 押している間の聞き取りを iPhone が許さないときは、タップで始めてもう一度タップで送る形に切り替える
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recog = null;       // 聞き取り中のもの
let tapMode = false;
// iPhone がこの画面にマイクを渡してくれない（audio-capture）ときは、🎙 でキーボードを開き、キーボードのマイクで話してもらう。
// アプリを開き直すたびに、もう一度画面のマイクを試す
let micBroken = false;
const KEYBOARD_HINT = "🎙 を押すとキーボードが開きます。キーボードのマイクで話してください";

function startListening() {
  if (recog) return;
  if (!Recognition) {
    addMessage("jarvis error", "この iPhone では文字起こしを使えません。キーボードのマイクで入力してください。");
    return;
  }
  stopVoice();
  const r = new Recognition();
  r.lang = "ja-JP";
  r.continuous = true;
  r.interimResults = true;
  let final = "", interim = "", failed = "";
  r.sendOnEnd = false;
  r.onresult = (ev) => {
    final = ""; interim = "";
    for (const res of ev.results) {
      if (res.isFinal) final += res[0].transcript; else interim += res[0].transcript;
    }
    hint("● " + (final + interim || "聞いています…"), true);
  };
  r.onerror = (ev) => {
    failed = ev.error;
    clientLog("文字起こしのエラー: " + ev.error + (tapMode ? "（タップ）" : "（長押し）"));
    if (ev.error === "not-allowed" && !tapMode) {
      tapMode = true;  // 押した瞬間には始められない。次からタップで
    } else if (ev.error === "audio-capture") {
      if (!micBroken) addMessage("jarvis error", "いまは iPhone がこの画面にマイクを使わせてくれません。" +
        "🎙 か円を押すとキーボードが開くので、キーボードのマイクで話して ➤ で送ってください。");
      micBroken = true;
    } else if (!["no-speech", "aborted"].includes(ev.error)) {
      addMessage("jarvis error", "文字起こしできませんでした（" + ev.error + "）。" +
        (ev.error === "not-allowed" ? "iPhone の設定で、マイクと音声認識を許可してください。" : ""));
    }
  };
  r.onend = () => {
    if (recog === r) recog = null;
    root.remove("holding", "recording");
    const text = (final + interim).trim();
    if (r.sendOnEnd && text) {
      hint();
      submit(text);
    } else if (r.sendOnEnd && !failed) {
      hint("聞き取れませんでした。もう一度どうぞ");
    } else {
      hint();
    }
  };
  try {
    r.start();
  } catch (e) {
    clientLog("文字起こしを始められません: " + e);
    return;
  }
  recog = r;
  root.add("recording");
  hint(tapMode ? "● 聞いています… もう一度タップで送ります" : "● 聞いています… 離すと送ります", true);
}

function finishListening() {
  if (!recog) return;
  recog.sendOnEnd = true;
  hint("文字にしています…");
  recog.stop();  // 最後の結果が届いてから onend が呼ばれる
}

function cancelListening() {
  if (recog) recog.abort();
}

for (const el of [$("mic"), $("reactor")]) {
  el.addEventListener("pointerdown", (e) => {
    if (tapMode || micBroken) return;
    e.preventDefault();
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch {}
    root.add("holding");
    startListening();
  });
  el.addEventListener("pointerup", () => {
    ensureAudio();  // 指を離したときなら、iPhone が音の準備を許してくれる
    root.remove("holding");
    if (!tapMode && !micBroken) finishListening();
  });
  el.addEventListener("pointercancel", () => { if (!tapMode && !micBroken) finishListening(); });
  el.addEventListener("click", () => {
    if (micBroken) {  // タップの中でなら、キーボードを開ける
      $("text").focus();
      hint("キーボードの右下のマイクを押して話してください");
      return;
    }
    if (!tapMode) return;
    ensureAudio();
    if (recog) finishListening(); else startListening();
  });
  el.addEventListener("contextmenu", (e) => e.preventDefault());
}

// ---- 合言葉 ----
function askToken(message) {
  store.del("jarvisToken");
  token = "";
  $("setupError").textContent = message || "";
  $("setup").hidden = false;
  $("tokenInput").focus();
}
$("setupForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  token = $("tokenInput").value.trim();
  if (!token) return;
  store.set("jarvisToken", token);
  $("setup").hidden = true;
  await loadHistory();
});

// ---- 新しい版が出たら知らせる（PC 側で画面のファイルを直したとき） ----
// 自動で読み込み直すと、iPhone ではアプリを閉じるまでマイクが使えなくなるので、開き直してもらう
let version = null;
let outdated = false;
async function checkVersion() {
  if (!token || waiting || recog) return;
  try {
    const v = (await api("/api/version")).version;
    setLink(true);
    if (version && v !== version) outdated = true;
    if (!version) version = v;
    if (outdated) hint("新しい版があります。アプリを閉じて開き直してください");
  } catch (e) {
    if (e.message !== "401") setLink(false);
  }
}
setInterval(checkVersion, 60 * 1000);

// 画面を開いている間は PC に合図を送る。閉じている間に返事ができたら、PC が iPhone に通知する（開いていれば通知しない）
function beat(open) {
  if (!token) return;
  fetch("/api/alive", {
    method: "POST",
    headers: { "Authorization": "Bearer " + token, "Content-Type": "application/json" },
    body: JSON.stringify({ open }),
    keepalive: true,
  }).catch(() => {});
}
setInterval(() => { if (!document.hidden) beat(true); }, 10 * 1000);
beat(true);

document.addEventListener("visibilitychange", () => {
  if (document.hidden) { beat(false); cancelListening(); return; }
  beat(true);
  if (token) { checkVersion(); loadHistory(); loadAgenda(); }
});

// 以前の版が入れた「画面の保存（サービスワーカー）」は使わないので外す
if ("serviceWorker" in navigator) {
  navigator.serviceWorker.getRegistrations().then((rs) => rs.forEach((r) => r.unregister())).catch(() => {});
}

// ---- 予定・やること（時計の下の折りたたみ） ----
function agendaRow(label, text, cls) {
  const row = document.createElement("div");
  row.className = "row" + (cls ? " " + cls : "");
  const b = document.createElement("b"), s = document.createElement("span");
  b.textContent = label; s.textContent = text;
  row.append(b, s);
  return row;
}
function agendaHead(text) {
  const h = document.createElement("h3");
  h.textContent = text;
  return h;
}
async function loadAgenda() {
  if (!token) return;
  try {
    const d = await api("/api/dashboard");
    const now = new Date(), pad = (n) => String(n).padStart(2, "0");
    const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
    const hm = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
    const events = d.events || [], tasks = d.tasks || [];
    const next = events.find((e) => e.iso > today || (e.iso === today && (e.start === "終日" || e.start >= hm)));
    const parts = [];
    if (next) parts.push("次: " + (next.iso === today ? "" : next.date + " ") + (next.start === "終日" ? "" : next.start + " ") + next.title);
    else if (d.events) parts.push("予定なし");
    parts.push(tasks.length ? `やること${tasks.length}件` : "やることなし");
    $("agendaSummary").textContent = parts.join(" ・ ");
    const body = $("agendaBody");
    body.replaceChildren();
    if (d.weather) body.append(agendaHead("WEATHER"), agendaRow(d.weather.text, `${d.weather.temp}℃（${d.weather.min}〜${d.weather.max}℃・降水${d.weather.pop}%）`));
    body.append(agendaHead("SCHEDULE"));
    if (!events.length) body.append(agendaRow("", "今週の予定はありません"));
    for (const e of events) body.append(agendaRow(`${e.date}(${e.wd}) ${e.start === "終日" ? "終日" : e.start}`, e.title, e.iso === today ? "soon" : ""));
    body.append(agendaHead("TASKS"));
    if (!tasks.length) body.append(agendaRow("", "未完了のやることはありません"));
    for (const t of tasks) body.append(agendaRow(t.due ? t.due.slice(5).replace("-", "/") : "", t.title));
  } catch (e) { /* 取れなかったら前の表示のまま */ }
}
setInterval(loadAgenda, 5 * 60 * 1000);

// ---- 議事録（iPhone のマイクで会議を録る。止めたら PC に送って、文字起こしと議事録づくりは PC が行う） ----
let mrec = null, mchunks = [], mtimer = null, mstart = 0, mwake = null;

function mtime() {
  const s = Math.floor((Date.now() - mstart) / 1000);
  return String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
}

async function minutesStart() {
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    clientLog("議事録のマイクを使えません: " + e);
    addMessage("jarvis error", "マイクを使えませんでした。iPhone の設定で、このアプリのマイクを許可してください。");
    return;
  }
  stopVoice();
  mchunks = [];
  mrec = new MediaRecorder(stream);
  mrec.ondataavailable = (ev) => { if (ev.data && ev.data.size) mchunks.push(ev.data); };
  mrec.onstop = () => {
    stream.getTracks().forEach((t) => t.stop());
    if (mwake) { mwake.release().catch(() => {}); mwake = null; }
  };
  mrec.start(10000);
  mstart = Date.now();
  try { mwake = await navigator.wakeLock.request("screen"); } catch {}  // 画面が消えると録音が止まるので、点けたままにする
  const b = $("minutesBtn");
  b.classList.remove("off");
  b.textContent = "⏹ 00:00";
  mtimer = setInterval(() => { b.textContent = "⏹ " + mtime(); }, 1000);
  hint("● 議事録を録音中です。画面を点けたままにしてください。もう一度 ⏹ を押すと終わります", true);
}

async function minutesStop() {
  clearInterval(mtimer);
  const rec = mrec;
  mrec = null;
  const b = $("minutesBtn");
  b.textContent = "📝 送信中…";
  await new Promise((res) => { rec.addEventListener("stop", res, { once: true }); rec.stop(); });
  const blob = new Blob(mchunks, { type: rec.mimeType || "audio/mp4" });
  mchunks = [];
  hint();
  const div = addMessage("jarvis pending", "録音を PC に送っています…");
  try {
    await request("/api/minutes", blob, blob.type || "audio/mp4");
  } catch (e) {
    clientLog("議事録の送信に失敗: " + e);
    div.remove();
    addMessage("jarvis error", "録音を PC に送れませんでした。PC のジャービスと Tailscale が動いているか確かめてください。");
    b.classList.add("off"); b.textContent = "📝 議事録";
    return;
  }
  div.querySelector("span").textContent = "録音を受け取りました。文字起こしをしています。終わるまで画面を開いたままお待ちください。";
  for (;;) {
    await new Promise((r) => setTimeout(r, 5000));
    let st;
    try { st = await api("/api/minutes/status"); } catch (e) { continue; }
    if (st.state === "working") {
      b.textContent = "📝 " + (st.step || "作成中");
      div.querySelector("span").textContent = (st.step || "作成中") + "…";
    } else {
      div.classList.remove("pending");
      div.querySelector("span").textContent = st.reply || "議事録ができました。";
      speak(st.reply || "");
      break;
    }
  }
  b.classList.add("off");
  b.textContent = "📝 議事録";
}

$("minutesBtn").addEventListener("click", () => {
  const b = $("minutesBtn");
  if (b.dataset.busy) return;
  b.dataset.busy = "1";
  (mrec ? minutesStop() : minutesStart()).finally(() => { delete b.dataset.busy; });
});

$("build").textContent = "REMOTE " + BUILD;
if (token) { loadHistory(); checkVersion(); loadAgenda(); } else askToken();
