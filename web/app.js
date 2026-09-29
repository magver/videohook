/* VideoHook UI — один файл, без сборки. */
"use strict";

const S = {
  view: "home", state: null, clips: [], clip: null, momentsTab: "trends", trends: null, trendSort: "TRENDING_DESC",
  catalog: null, anime: null, search: "", pubTab: {}, knownTasks: {}, studioFilter: "",
};
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtT = (t) => { t = Math.max(0, +t || 0); const m = Math.floor(t / 60); return `${m}:${(t % 60).toFixed(1).padStart(4, "0")}`; };
const fmtN = (n) => (n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "K" : String(n || 0));
const media = (rel) => (rel ? "/media/" + rel.split("/").map(encodeURIComponent).join("/") : "");
const MOODS = { epic: "эпик", dark: "мрак", twist: "поворот", emotional: "эмоции", romantic: "романтика", funny: "юмор" };
const SRC = { base: "база", ai: "Gemini", youtube: "YouTube", template: "шаблон", manual: "вручную" };
const STAGE_VIEW = { found: "studio", downloaded: "studio", rendered: "studio", in_antigravity: "agent", ready: "publish", published: "publish" };

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({}));
  if (!r.ok || (data && data.error)) throw new Error(data.error || r.statusText);
  return data;
}
function toast(msg, kind = "") {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = msg;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), kind === "err" ? 8000 : 4000);
}
async function act(fn, okMsg) {
  try { const r = await fn(); if (okMsg) toast(okMsg, "ok"); return r; }
  catch (e) { toast(e.message, "err"); throw e; }
}
function modal(html) { $("#modal-body").innerHTML = html; $("#modal").classList.remove("hidden"); }
function closeModal() { $("#modal").classList.add("hidden"); }
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal" || e.target.dataset.close !== undefined) closeModal(); });
async function copy(text) { try { await navigator.clipboard.writeText(text); toast("Скопировано", "ok"); } catch { toast("Не удалось скопировать", "err"); } }

/* ---------------- навигация и опрос ---------------- */
function go(view, opts = {}) {
  S.view = view;
  Object.assign(S, opts);
  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === view));
  render();
}
$$("#nav a").forEach((a) => a.addEventListener("click", () => go(a.dataset.view)));
$("#btn-autopilot").addEventListener("click", openAutopilot);

async function poll() {
  try {
    const st = await api("/api/state");
    S.state = st;
    $("#ver").textContent = "v" + st.version;
    const c = st.counts;
    $("#nb-moments").textContent = st.moments.refilling.length ? "↻" : "";
    $("#nb-studio").textContent = (c.found + c.downloaded + c.rendered) || "";
    $("#nb-agent").textContent = c.in_antigravity || "";
    $("#nb-publish").textContent = c.ready || "";
    const h = st.health;
    $("#nb-settings").classList.toggle("on", !h.ffmpeg.ok || !(h.gemini.api || h.gemini.antigravity));
    renderTasks(st.tasks);
    renderActivity(st.tasks);
    updateClipTask();
    let changed = false;
    for (const t of st.tasks) {
      const prev = S.knownTasks[t.id];
      if (prev && prev !== t.status && (t.status === "done" || t.status === "error")) {
        changed = true;
        toast(t.status === "done" ? `✓ ${t.title}: ${t.message}` : `✕ ${t.title}: ${t.error}`, t.status === "done" ? "ok" : "err");
      }
      S.knownTasks[t.id] = t.status;
    }
    if (changed) refreshData();
    else if (S.view === "home" || S.view === "agent") softRefresh();
  } catch (e) { /* сервер перезапускается */ }
  const busy = (S.state?.tasks || []).some((t) => t.status === "running" || t.status === "queued");
  setTimeout(poll, busy ? 1000 : 2500);
}
/* ---------------- прогресс ---------------- */
const fmtDur = (s) => { s = Math.max(0, Math.round(s)); return s >= 60 ? `${Math.floor(s / 60)} мин ${s % 60} c` : `${s} c`; };
const TPROG = {};   // история прогресса задач — для «осталось ~»
function taskInfo(t) {
  const now = Date.now() / 1000;
  const el = t.started ? now - t.started : 0;
  const h = (TPROG[t.id] = TPROG[t.id] || { p: t.progress, at: now, still: now });
  if (t.progress !== h.p) { h.p = t.progress; h.still = now; }
  const busy = now - h.still > 4;            // прогресс давно не менялся — показываем «идёт работа»
  let eta = "";
  if (t.status === "running" && t.progress > 0.05 && t.progress < 0.99 && el > 3) eta = "осталось ~" + fmtDur(el / t.progress - el);
  return { el, eta, busy, pct: Math.round(t.progress * 100) };
}
function taskHtml(t, big) {
  const i = taskInfo(t);
  const queued = t.status === "queued";
  return `<div class="act"><span class="spin"></span><b>${esc(t.title)}</b><span class="pct">${queued ? "в очереди" : i.pct + "%"}</span>
    <div class="msg" title="${esc(t.message)}">${esc(t.message)}${i.el ? " · " + fmtDur(i.el) : ""}${i.eta ? " · " + i.eta : ""}</div>
    <div class="bigbar ${i.busy || queued ? "busy" : ""}"><span style="width:${Math.max(2, i.pct)}%"></span></div></div>`;
}
function renderActivity(list) {
  const act = list.filter((t) => t.status === "running" || t.status === "queued");
  $("#activity").innerHTML = act.slice(0, 3).map((t) => taskHtml(t, true)).join("")
    + (act.length > 3 ? `<div class="act"><span></span><small class="muted">и ещё задач: ${act.length - 3}</small></div>` : "");
}
function updateClipTask() {
  const box = $("#clip-task");
  if (!box || !S.clip) return;
  const t = (S.state?.tasks || []).find((x) => x.clip_id === S.clip.id && (x.status === "running" || x.status === "queued"));
  box.innerHTML = t ? `<div class="row between"><b><span class="spin"></span> ${esc(t.title)}</b><span class="pct">${Math.round(t.progress * 100)}%</span></div>
    <div class="bigbar ${taskInfo(t).busy ? "busy" : ""}"><span style="width:${Math.max(2, Math.round(t.progress * 100))}%"></span></div>
    <small class="muted">${esc(t.message)}${taskInfo(t).eta ? " · " + taskInfo(t).eta : ""}</small>` : "";
}
function renderTasks(list) {
  const now = Date.now() / 1000;
  list = list.filter((t) => (t.status === "error" && now - t.updated < 60) || (t.status === "done" && now - t.updated < 8));
  $("#tasks").innerHTML = list.slice(0, 6).map((t) => `
    <div class="task ${t.status}"><b>${t.status === "running" ? '<span class="spin"></span> ' : ""}${esc(t.title)}</b>
    <div class="msg" title="${esc(t.error || t.message)}">${esc(t.error || t.message)}</div>
    ${t.status === "running" || t.status === "queued" ? `<div class="bar"><span style="width:${Math.round(t.progress * 100)}%"></span></div>` : ""}</div>`).join("");
}
let softTimer = 0;
function softRefresh() {
  if (Date.now() - softTimer < 5000) return;
  softTimer = Date.now();
  refreshData(true);
}
async function refreshData(soft = false) {
  S.clips = await api("/api/clips").catch(() => S.clips);
  if (S.clip) S.clip = S.clips.find((c) => c.id === S.clip.id) || null;
  if (S.view === "moments" && S.anime) S.catalog = null;
  const focused = document.activeElement && ["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName);
  if (focused && soft) return;
  if (S.view === "settings") return;   // настройки не зависят от клипов — не стираем то, что вводится
  if (S.view === "studio" && focused) { renderClipList(); return; }
  render();
}

function render() {
  const v = { home: viewHome, moments: viewMoments, studio: viewStudio, agent: viewAgent, publish: viewPublish, settings: viewSettings }[S.view];
  v();
}

/* ---------------- Сводка ---------------- */
function viewHome() {
  const st = S.state;
  if (!st) { $("#main").innerHTML = '<div class="empty"><span class="spin"></span></div>'; return; }
  const c = st.counts, m = st.moments, h = st.health;
  const byStage = (s) => S.clips.filter((x) => x.stage === s);
  const todos = [];
  byStage("in_antigravity").filter((x) => x.ag?.status === "manual").forEach((x) => todos.push({ clip: x, t: "Вставьте промпт в Antigravity", btn: "Промпт", fn: `copyPrompt('${x.id}')` }));
  byStage("ready").forEach((x) => todos.push({ clip: x, t: "Готов — опубликуйте", btn: "Публиковать", fn: `go('publish')` }));
  byStage("rendered").forEach((x) => todos.push({ clip: x, t: "Смонтирован — отправьте агенту", btn: "В Antigravity", fn: `sendAG('${x.id}')` }));
  byStage("downloaded").forEach((x) => todos.push({ clip: x, t: "Источник готов — смонтируйте", btn: "Открыть", fn: `openClip('${x.id}')` }));
  byStage("found").forEach((x) => todos.push({ clip: x, t: "Ждёт источник", btn: "Открыть", fn: `openClip('${x.id}')` }));
  const hl = [
    [h.ffmpeg.ok, "FFmpeg", h.ffmpeg.ok ? "готов" : h.ffmpeg.error],
    [h.ytdlp.ok, "yt-dlp", h.ytdlp.ok ? h.ytdlp.version : "не установлен"],
    [h.gemini.api || h.gemini.antigravity, "ИИ (Google AI Pro)", h.gemini.antigravity ? "через Antigravity" + (h.gemini.api ? " · запасной: API-ключ" : "") : h.gemini.api ? "Gemini API-ключ" : "не подключён — запустите Antigravity"],
    [h.antigravity.agentapi, "Antigravity", h.antigravity.running ? "запущен — агент стартует автоматически" : h.antigravity.installed ? "установлен, запустится автоматически" : "не найден — промпт вставляется вручную"],
    [h.youtube.connected, "YouTube API", h.youtube.connected ? "подключён" : "ассистированная загрузка"],
    [h.tiktok.configured, "TikTok API", h.tiktok.configured ? "подключён" : "ассистированная загрузка"],
  ];
  $("#main").innerHTML = `
    <div class="head"><h1>Сводка</h1><span class="sub">конвейер вирусных аниме-шортсов</span><div class="spacer"></div>
      <button class="btn" onclick="go('moments')">✦ Найти моменты</button><button class="btn primary" onclick="openAutopilot()">⚡ Автопилот</button></div>
    <div class="funnel">${st.stages.map(([k, name]) => `<div class="stage" onclick="go('${STAGE_VIEW[k]}',{studioFilter:'${k}'})"><small>${name}</small><b>${c[k] || 0}</b></div>`).join("")}</div>
    <div class="grid g3" style="margin-top:16px">
      <div class="card" style="grid-column: span 2"><h2>Что сделать дальше</h2>
        ${todos.length ? todos.slice(0, 8).map((d) => `<div class="todo"><div class="th" style="width:36px;height:48px;border-radius:5px;background:var(--panel2) url('${media(d.clip.cover || d.clip.render?.thumb || d.clip.thumb)}') center/cover"></div>
          <div class="t"><b>${esc(d.clip.anime)} — ${esc(d.clip.title)}</b><small>${d.t}</small></div><button class="btn sm" onclick="${d.fn}">${d.btn}</button></div>`).join("")
          : `<div class="empty"><b>Очередь пуста</b>Запустите автопилот или выберите момент в разделе «Моменты».</div>`}
      </div>
      <div class="col" style="gap:16px">
        <div class="card"><h2>База моментов</h2>
          <div class="grid g2"><div class="kpi"><b>${m.fresh}</b><small>свежих</small></div><div class="kpi"><b>${m.used}</b><small>использовано</small></div>
          <div class="kpi"><b>${m.anime}</b><small>тайтлов</small></div><div class="kpi"><b>${m.moments}</b><small>всего</small></div></div>
          <p class="muted" style="font-size:12px;margin:10px 0 0">Использованный момент уходит из выдачи; когда у тайтла &lt; 3 свежих — база пополняется сама (Gemini → YouTube → шаблоны).
          ${m.refilling.length ? `<br><span class="spin"></span> Пополняется: ${m.refilling.map(esc).join(", ")}` : ""}</p>
        </div>
        <div class="card health"><h2>Подключения</h2><ul>${hl.map(([ok, n, d]) => `<li><span class="${ok ? "ok-t" : "warn-t"}">${ok ? "●" : "○"}</span><div><b>${n}</b><br><small>${esc(d)}</small></div></li>`).join("")}</ul>
          <button class="btn sm" style="margin-top:8px" onclick="go('settings')">Настроить</button></div>
      </div>
    </div>`;
}

function openAutopilot() {
  const s = S.state?.settings || {};
  modal(`<h2>⚡ Автопилот</h2>
    <p class="muted">Берёт свежие моменты из трендовых тайтлов (по одному на тайтл), находит лучший источник и отрезок,
    монтирует 9:16 и ${s.use_antigravity ? "отправляет в Antigravity" : "сразу готовит к публикации"}. Использованные моменты автоматически заменяются новыми.</p>
    <label class="f">Сколько роликов<input id="ap-count" type="number" min="1" max="10" value="3"></label>
    <label class="chk" style="margin-top:10px"><input type="checkbox" id="ap-ag" ${s.use_antigravity ? "checked" : ""}> Доработка в Antigravity (${esc(s.antigravity_model || "")})</label>
    <label class="chk" style="margin-top:6px"><input type="checkbox" id="ap-pub" ${s.auto_publish ? "checked" : ""}> Автопубликация через подключённые API</label>
    <div class="row" style="margin-top:16px;justify-content:flex-end"><button class="btn" data-close>Отмена</button><button class="btn primary" id="ap-go">Запустить</button></div>`);
  $("#ap-go").onclick = async () => {
    await act(() => api("/api/settings", { use_antigravity: $("#ap-ag").checked, auto_publish: $("#ap-pub").checked }));
    await act(() => api("/api/autopilot", { count: +$("#ap-count").value || 3 }), "Автопилот запущен");
    closeModal();
  };
}

/* ---------------- Моменты ---------------- */
async function viewMoments() {
  const tab = S.momentsTab;
  $("#main").innerHTML = `
    <div class="head"><h1>Моменты</h1><span class="sub">тренды AniList · база, пополняемая ИИ · ваши ссылки</span><div class="spacer"></div>
      <div class="seg"><button data-t="trends" class="${tab === "trends" ? "on" : ""}">🔥 Тренды</button><button data-t="base" class="${tab === "base" ? "on" : ""}">✦ База</button><button data-t="url" class="${tab === "url" ? "on" : ""}">🔗 По ссылке</button></div></div>
    <div id="mbody"></div>`;
  $$(".seg button").forEach((b) => (b.onclick = () => { S.momentsTab = b.dataset.t; S.anime = null; viewMoments(); }));
  if (S.anime) return renderAnime();
  if (tab === "trends") return renderTrends();
  if (tab === "base") return renderBase();
  renderUrl();
}

async function renderTrends() {
  const box = $("#mbody");
  box.innerHTML = `<div class="row" style="margin-bottom:14px">
      <input id="asearch" placeholder="Найти аниме (RU/EN, имя персонажа)…" style="max-width:380px" value="${esc(S.search)}">
      <div class="seg" id="tsort">${[["TRENDING_DESC", "В тренде"], ["POPULARITY_DESC", "Популярные"], ["SCORE_DESC", "Топ рейтинга"]].map(([k, n]) => `<button data-s="${k}" class="${S.trendSort === k ? "on" : ""}">${n}</button>`).join("")}</div>
      <div class="spacer" style="flex:1"></div><button class="btn sm" id="tref">↻ Обновить</button></div>
    <div id="covers"><div class="empty"><span class="spin"></span> Загружаю тренды AniList…</div></div>`;
  $("#asearch").onkeydown = async (e) => { if (e.key === "Enter") { S.search = e.target.value.trim(); await loadTrends(); } };
  $$("#tsort button").forEach((b) => (b.onclick = () => { S.trendSort = b.dataset.s; S.search = ""; S.trends = null; renderTrends(); }));
  $("#tref").onclick = () => loadTrends(true);
  loadTrends();
}
async function loadTrends(fresh = false) {
  try {
    const list = S.search ? await api("/api/anime/search?q=" + encodeURIComponent(S.search))
      : (S.trends && !fresh ? S.trends : (S.trends = await api(`/api/trending?sort=${S.trendSort}&fresh=${fresh ? 1 : 0}`)));
    const box = $("#covers");
    if (!box) return;
    box.innerHTML = list.length ? `<div class="covers">${list.map((a, i) => `
      <div class="cover" data-i="${i}"><div class="img" style="${a.cover ? `background-image:url('${esc(a.cover)}')` : ""}">${a.cover ? "" : esc(a.en)}</div>
        ${a.score ? `<span class="score">★ ${a.score}</span>` : ""}
        <span class="tag chip ${a.fresh ? "ok" : "warn"}">${a.fresh ? a.fresh + " свежих" : a.in_base ? "пополнить" : "новый"}</span>
        <div class="meta"><b>${esc(a.ru || a.en)}</b><small>${esc((a.genres || []).slice(0, 2).join(" · "))}${a.airing ? " · 🟢 онгоинг" : ""}</small></div></div>`).join("")}</div>`
      : `<div class="empty"><b>Ничего не найдено</b>AniList недоступен или запрос слишком узкий.</div>`;
    $$(".cover", box).forEach((el) => (el.onclick = () => openAnimeFromTrend(list[+el.dataset.i])));
  } catch (e) { toast(e.message, "err"); }
}
async function openAnimeFromTrend(a) {
  if (!a.in_base || !a.fresh) {
    const r = await act(() => api("/api/anime/register", a));
    a.key = r.key;
    toast("Тайтл добавлен — Gemini/YouTube подбирают моменты…");
  }
  S.anime = a.key;
  viewMoments();
}

async function renderBase() {
  const box = $("#mbody");
  box.innerHTML = `<div class="row" style="margin-bottom:14px"><input id="bsearch" placeholder="Фильтр по тайтлу или сцене…" style="max-width:380px" value="${esc(S.search)}">
    <label class="chk"><input type="checkbox" id="bused"> показывать использованные</label><div style="flex:1"></div>
    <button class="btn sm" id="bmaint">↻ Пополнить всё, где мало</button></div><div id="blist"><div class="empty"><span class="spin"></span></div></div>`;
  const load = async () => {
    S.catalog = await api(`/api/catalog?q=${encodeURIComponent($("#bsearch").value)}&used=${$("#bused").checked ? 1 : 0}`);
    $("#blist").innerHTML = `<div class="grid g3">${S.catalog.map((a) => `
      <div class="card tight" style="cursor:pointer" data-k="${esc(a.key)}"><div class="row between"><b>${esc(a.name)}</b>
        <span class="chip ${a.fresh >= 3 ? "ok" : a.fresh ? "warn" : "err"}">${a.refilling ? "↻ " : ""}${a.fresh}/${a.total}</span></div>
        <small>${esc(a.studio)}${a.genres.length ? " · " + esc(a.genres.slice(0, 2).join(", ")) : ""}</small>
        <div class="muted" style="font-size:12px;margin-top:6px">${topFresh(a).slice(0, 2).map((m) => (m.score != null ? `<span class="score ${scoreCls(m.score)}">${m.score}%</span> ` : "• ") + esc(m.title)).join("<br>") || "нет свежих — будет пополнено"}</div></div>`).join("")}</div>`;
    $$("[data-k]", $("#blist")).forEach((el) => (el.onclick = () => { S.anime = el.dataset.k; viewMoments(); }));
  };
  $("#bsearch").oninput = debounce(load, 250);
  $("#bused").onchange = load;
  $("#bmaint").onclick = () => act(() => api("/api/moments/maintain", {}), "Пополнение запущено");
  load();
}

async function renderAnime() {
  const box = $("#mbody");
  const cat = await api("/api/catalog?used=1");
  const a = cat.find((x) => x.key === S.anime);
  if (!a) { box.innerHTML = `<div class="empty"><b>Моменты подбираются…</b><span class="spin"></span> Обновится автоматически.</div><button class="btn" onclick="S.anime=null;viewMoments()">← Назад</button>`; setTimeout(() => S.view === "moments" && S.anime && renderAnime(), 4000); return; }
  box.innerHTML = `
    <div class="row" style="margin-bottom:14px"><button class="btn sm" onclick="S.anime=null;viewMoments()">← Назад</button>
      <h2 style="margin:0">${esc(a.name)}</h2><span class="chip">${esc(a.studio || "студия н/д")}</span>
      <span class="chip ${a.fresh >= 3 ? "ok" : "warn"}">свежих ${a.fresh} из ${a.total}</span>${a.refilling ? '<span class="chip"><span class="spin"></span> пополняется</span>' : ""}
      <div style="flex:1"></div><button class="btn sm" id="rate">📈 Оценить шансы (Gemini)</button><button class="btn sm" id="refill">✨ Подобрать ещё (Gemini/YouTube)</button><button class="btn sm" id="addm">＋ Свой момент</button></div>
    <div class="col">${sortMoments(a.moments).map((m) => `
      <div class="moment ${m.used ? "used" : ""}"><div>
        <div class="row">${m.score != null ? `<span class="score ${scoreCls(m.score)}" title="${esc(m.score_why || "Шанс успеха по оценке Gemini")}">${m.score}%</span>` : ""}<span class="h">${esc(m.title)}</span>${m.episode ? `<small>${esc(m.episode)}</small>` : ""}
          <span class="chip ${m.mood}">${MOODS[m.mood] || m.mood}</span><span class="chip src-${m.source}">${SRC[m.source] || m.source}</span>
          ${m.views ? `<span class="chip">👁 ${fmtN(m.views)}</span>` : ""}${m.used ? `<span class="chip">использован ×${m.used_count}</span>` : ""}</div>
        <div class="hook">«${esc(m.hook)}»</div>${m.score_why ? `<div class="why">📈 ${esc(m.score_why)}</div>` : m.why ? `<div class="why">${esc(m.why)}</div>` : ""}</div>
        <div class="row">${m.used ? `<button class="btn sm ghost" onclick="resetMoment('${m.id}')">вернуть</button>` : ""}
          <button class="btn sm" onclick="takeMoment('${esc(a.key)}','${m.id}','source')">В студию</button>
          <button class="btn sm primary" onclick="takeMoment('${esc(a.key)}','${m.id}','full')">⚡ Конвейер</button></div></div>`).join("") || '<div class="empty">Моменты подбираются…</div>'}</div>`;
  $("#refill").onclick = () => act(() => api("/api/moments/refill", { anime_key: a.key }), "Подбираю новые моменты…");
  $("#rate").onclick = () => act(() => api("/api/moments/rate", { anime_key: a.key }), "Gemini оценивает шансы — прогресс вверху");
  $("#addm").onclick = () => addMomentModal(a.key);
}
const clipBusy = (id) => (S.state?.tasks || []).some((t) => t.clip_id === id && (t.status === "running" || t.status === "queued"));
const scoreCls = (v) => (v >= 70 ? "hi" : v >= 45 ? "mid" : "lo");
const sortMoments = (list) => list.slice().sort((x, y) => (x.used - y.used) || ((y.score ?? -1) - (x.score ?? -1)));
const topFresh = (a) => sortMoments(a.moments.filter((m) => !m.used));
async function takeMoment(key, id, run) {
  const r = await act(() => api("/api/clip/from_moment", { anime_key: key, moment_id: id, run }), run === "full" ? "Запущен полный конвейер" : "Ищу источник и лучший отрезок…");
  S.clips = await api("/api/clips");
  if (run !== "full") openClip(r.clip.id); else renderAnime();
}
async function resetMoment(id) { await act(() => api("/api/moments/reset", { moment_id: id })); renderAnime(); }
function addMomentModal(key) {
  modal(`<h2>Свой момент</h2><div class="col">
    <label class="f">Название сцены<input id="nm-t"></label><label class="f">Серия / арка<input id="nm-e"></label>
    <label class="f">Хук (заголовок на экране)<input id="nm-h"></label>
    <label class="f">Поисковый запрос или ссылка на YouTube<input id="nm-q"></label>
    <label class="f">Настроение<select id="nm-m">${Object.entries(MOODS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></label></div>
    <div class="row" style="margin-top:14px;justify-content:flex-end"><button class="btn" data-close>Отмена</button><button class="btn primary" id="nm-go">Добавить</button></div>`);
  $("#nm-go").onclick = async () => {
    const q = $("#nm-q").value.trim();
    await act(() => api("/api/moments/add", { anime_key: key, moment: { title: $("#nm-t").value, episode: $("#nm-e").value, hook: $("#nm-h").value || $("#nm-t").value, query: q.startsWith("http") ? "" : q, url: q.startsWith("http") ? q : "", mood: $("#nm-m").value } }), "Момент добавлен");
    closeModal(); renderAnime();
  };
}
function renderUrl() {
  $("#mbody").innerHTML = `<div class="card" style="max-width:680px"><h2>Ролик по ссылке</h2>
    <p class="muted">YouTube, VK Видео, RuTube и др. (всё, что поддерживает yt-dlp). Название аниме определится автоматически, лучший отрезок найдут «Самые пересматриваемые», громкость, динамика и Gemini.</p>
    <div class="col"><input id="u-url" placeholder="https://www.youtube.com/watch?v=…"><input id="u-title" placeholder="Название сцены (необязательно)">
    <div class="row"><button class="btn" id="u-src">В студию</button><button class="btn primary" id="u-full">⚡ Полный конвейер</button></div></div></div>`;
  const go_ = async (run) => {
    const url = $("#u-url").value.trim();
    if (!url) return toast("Вставьте ссылку", "err");
    const r = await act(() => api("/api/clip/from_url", { url, title: $("#u-title").value, run }), "Принято в работу");
    S.clips = await api("/api/clips");
    if (run !== "full") openClip(r.clip.id);
  };
  $("#u-src").onclick = () => go_("source");
  $("#u-full").onclick = () => go_("full");
}

/* ---------------- Студия ---------------- */
function openClip(id) { S.clip = S.clips.find((c) => c.id === id) || { id }; go("studio"); }
async function viewStudio() {
  if (!S.clips.length) S.clips = await api("/api/clips");
  if (S.clip && !S.clip.stage) S.clip = S.clips.find((c) => c.id === S.clip.id) || null;
  const f = S.studioFilter;
  $("#main").innerHTML = `
    <div class="head"><h1>Студия</h1><span class="sub">отрезок · подложка · тексты · музыка → рендер 9:16</span><div class="spacer"></div>
      <select id="sfilter" style="width:auto"><option value="">Все в работе</option>${(S.state?.stages || []).map(([k, n]) => `<option value="${k}" ${f === k ? "selected" : ""}>${n}</option>`).join("")}</select></div>
    <div class="studio"><div class="card tight"><div class="cliplist" id="cliplist"></div></div><div id="editor"></div></div>`;
  $("#sfilter").onchange = (e) => { S.studioFilter = e.target.value; renderClipList(); };
  renderClipList();
  renderEditor();
}
function renderClipList() {
  const box = $("#cliplist");
  if (!box) return;
  const f = S.studioFilter;
  const list = S.clips.filter((c) => (f ? c.stage === f : !["ready", "published"].includes(c.stage)));
  const stageName = Object.fromEntries(S.state?.stages || []);
  box.innerHTML = list.length ? list.map((c) => `<div class="clipitem ${S.clip?.id === c.id ? "on" : ""}" data-id="${c.id}">
      <div class="th" style="background-image:url('${media(c.render?.thumb || c.thumb || c.cover)}')"></div>
      <div style="min-width:0"><b>${esc(c.title)}</b><small>${esc(c.anime)}</small><br><span class="chip">${stageName[c.stage] || c.stage}</span></div></div>`).join("")
    : `<div class="empty" style="padding:20px"><b>Пусто</b>Возьмите момент в разделе «Моменты».</div>`;
  $$(".clipitem", box).forEach((el) => (el.onclick = () => { S.clip = S.clips.find((c) => c.id === el.dataset.id); renderClipList(); renderEditor(); }));
}

function renderEditor() {
  const box = $("#editor");
  const c = S.clip;
  if (!box) return;
  if (!c || !c.stage) { box.innerHTML = `<div class="empty"><b>Выберите клип слева</b>или возьмите новый момент.</div>`; return; }
  const st = S.state || {};
  const p = c.render?.params || {};
  const tpl = p.template || st.settings?.default_template || "cinema";
  const eff = { color_pop: true, zoom_punch: true, accent_zoom: true, flash: true, slowmo: true, impact_sfx: true, whoosh_sfx: true, punchy_audio: true, loudnorm: true, ...(p.effects || {}) };
  const parts = (c.parts || []).length > 1 ? c.parts : null;
  const subs = c.subtitles || [];
  const subsSrc = { gemini: "Gemini", "youtube-manual": "YouTube", "youtube-auto": "автоперевод YouTube", "gemini-translate": "перевод Gemini" }[c.subtitles_source] || "";
  const seg = c.segment || { start: 0, end: 30 };
  const order = (st.stages || []).map((s) => s[0]);
  const si = order.indexOf(c.stage);
  const hasSrc = !!c.source_file;
  const music = p.music || c.music || "none";
  box.innerHTML = `
    <div class="card stack">
      <div class="row between"><div><h2 style="margin:0">${esc(c.title)}</h2><small>${esc(c.anime_name || c.anime)}${c.episode ? " · " + esc(c.episode) : ""}${c.source_views ? " · источник 👁 " + fmtN(c.source_views) : ""}</small></div>
        <div class="row"><button class="btn sm ghost danger" id="e-del">Удалить</button></div></div>
      <div class="steps">${order.map((_, i) => `<span class="${i <= si ? "on" : ""}"></span>`).join("")}</div>
      <div id="clip-task" class="clip-task"></div>
      ${!hasSrc && clipBusy(c.id) ? `<div class="empty"><b>Ищу источник и скачиваю…</b>Прогресс — в рамке выше. Можно переключаться между разделами, работа продолжится.</div>` : !hasSrc ? `<div class="empty"><b>Источник ещё не скачан</b>Найдём лучший ролик по запросу «${esc(c.query || c.title)}» и выберем отрезок автоматически.<br><br>
        <div class="row" style="justify-content:center"><input id="e-url" placeholder="или вставьте свою ссылку" style="max-width:360px"><button class="btn primary" id="e-src">Найти и скачать</button></div></div>` : `
      <div class="editor">
        <div>
          <div class="seg" style="margin-bottom:8px"><button class="on" data-pv="src">Исходник</button><button data-pv="render" ${c.render?.file ? "" : "disabled"}>Рендер 9:16</button></div>
          <div class="player" id="pl"><video id="vid" controls preload="metadata" src="${media(c.source_file)}"></video></div>
          <div class="timeline" id="tl"></div>
          <div class="row" style="margin-top:8px"><button class="btn sm" id="in">[ Начало</button><input id="s0" type="number" step="0.1" value="${(+seg.start).toFixed(1)}" style="width:80px">
            <input id="s1" type="number" step="0.1" value="${(+seg.end).toFixed(1)}" style="width:80px"><button class="btn sm" id="out">Конец ]</button>
            <small id="slen">${(seg.end - seg.start).toFixed(1)} c</small><button class="btn sm ghost" id="play-sel">▶ отрезок</button></div>
          <h3 style="margin-top:14px">Лучшие отрезки</h3>
          <div class="sugs">${(c.suggestions || []).map((s, i) => `<div class="sugg" data-i="${i}"><span class="chip ${s.source === "gemini" ? "src-ai" : s.source === "heatmap" ? "src-youtube" : "src-manual"}">${{ gemini: "Gemini", heatmap: "Пересмотры", local: "Звук+динамика" }[s.source] || s.source}</span>
            <b>${fmtT(s.start)}–${fmtT(s.end)}</b><span class="muted" style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(s.reason || "")}</span>${(s.confirmed_by || []).length ? `<span class="chip ok">✓ ${s.confirmed_by.length + 1} сигнала</span>` : ""}</div>`).join("") || '<small class="muted">нет данных</small>'}</div>
          ${c.ai?.story ? `<div class="log" style="margin-top:8px"><div>📖 ${esc(c.ai.story)}</div></div>` : ""}
          ${parts ? `<small class="muted" style="display:block;margin-top:6px">Сцена собрана из ${parts.length} частей: ${parts.map((x) => fmtT(x[0]) + "–" + fmtT(x[1])).join(", ")} — затянутая середина вырезана</small>` : ""}
          ${c.ru_dub ? `<span class="chip ok" style="margin-top:6px">🇷🇺 русская озвучка</span>` : ""}
          <button class="btn sm" style="margin-top:8px" id="e-ai" ${st.health?.gemini?.api || st.health?.gemini?.antigravity ? "" : "disabled title='Подключите Gemini в настройках'"}>✨ Gemini-анализ видео</button><small class="muted" style="display:block;margin-top:4px">Gemini смотрит и слушает исходник: цельная сцена с завязкой и финалом, ударные моменты, замедление, русские субтитры</small>
        </div>
        <div class="stack">
          <label class="f">Хук (сверху, первые секунды решают всё)<input id="e-hook" value="${esc(c.hook)}" maxlength="70"></label>
          <label class="f">Подпись / цитата${(c.key_lines || []).length ? ` <span class="chip src-ai">+ ${c.key_lines.length} реплик по таймингу от Gemini</span>` : ""}<input id="e-cap" value="${esc(c.caption)}" maxlength="100"></label>
          <div><h3>Подложка</h3><div class="tpls">${Object.entries(st.templates || {}).map(([k, n]) => `<div class="tpl ${k === tpl ? "on" : ""}" data-t="${k}" title="${esc(n)}"><div class="mini"></div>${esc(n.split(":")[0].split("(")[0])}</div>`).join("")}</div></div>
          <label class="f" id="e-comm-wrap" style="${tpl === "commentary" ? "" : "display:none"}">Авторский комментарий (для подложки «комментарий»)<textarea id="e-comm">${esc(c.commentary || "")}</textarea></label>
          <div class="grid g2"><label class="f">Музыка<select id="e-music"><option value="none">Без музыки (оригинал)</option>${Object.entries(st.music || {}).map(([k, n]) => `<option value="${k}" ${k === music ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></label>
            <label class="f">Громкость музыки: <span id="mv">${Math.round((p.music_volume ?? st.settings?.music_volume ?? .22) * 100)}%</span><input type="range" id="e-mvol" min="0" max="0.6" step="0.02" value="${p.music_volume ?? st.settings?.music_volume ?? .22}"></label></div>
          <div class="grid g2"><label class="f">Переход между частями<select id="e-trans">${Object.entries(st.transitions || { auto: "Авто" }).map(([k, n]) => `<option value="${k}" ${k === (p.transition || c.transition || "auto") ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></label>
            <label class="f">Субтитры<label class="chk" style="margin-top:6px"><input type="checkbox" id="e-subs" ${subs.length && (p.use_subtitles ?? st.settings?.subtitles_mode !== "off") ? "checked" : ""} ${subs.length ? "" : "disabled"}> ${subs.length ? `русские: ${subs.length} реплик${subsSrc ? " (" + subsSrc + ")" : ""}` : "нет реплик — запустите Gemini-анализ"}</label></label></div>
          <div class="row">${[["zoom_punch", "Панч-зум в начале"], ["accent_zoom", "Зум на ударах"], ["flash", "Вспышка на ударах"], ["slowmo", "Замедление кульминации"], ["color_pop", "Сочные цвета"]].map(([k, n]) => `<label class="chk"><input type="checkbox" data-eff="${k}" ${eff[k] ? "checked" : ""}> ${n}</label>`).join("")}</div>
          <div class="row">${[["punchy_audio", "Плотный родной звук"], ["impact_sfx", "Удар по звуку"], ["whoosh_sfx", "«Вжух» на переходах"], ["loudnorm", "Громкость −14 LUFS"]].map(([k, n]) => `<label class="chk"><input type="checkbox" data-eff="${k}" ${eff[k] ? "checked" : ""}> ${n}</label>`).join("")}
            <label class="chk"><input type="checkbox" id="e-loop" ${(p.loop_friendly ?? st.settings?.loop_friendly ?? false) ? "checked" : ""}> Мягкий луп</label>
            <label class="chk"><input type="checkbox" id="e-bar" ${(p.progress_bar ?? true) ? "checked" : ""}> Прогресс-бар</label></div>
          <small class="muted">Внизу всегда кредит: «${esc(c.credit)}» — ролик оформляется как фан-обзор, а не перезалив.</small>
          <div class="row"><button class="btn primary" id="e-render">🎬 Смонтировать 9:16</button>
            <button class="btn" id="e-ag" ${c.render?.file ? "" : "disabled"}>◈ Доработать в Antigravity</button>
            <button class="btn ghost" id="e-skip" ${c.render?.file ? "" : "disabled"}>Сразу в публикацию</button></div>
          ${(c.edit_ideas || []).length ? `<div><h3>Идеи монтажа от Gemini</h3><div class="log">${c.edit_ideas.map((i) => `<div>• ${esc(i)}</div>`).join("")}</div></div>` : ""}
          <div><h3>История</h3><div class="log">${(c.log || []).slice().reverse().map((l) => `<div>${new Date(l.t * 1000).toLocaleTimeString()} — ${esc(l.msg)}</div>`).join("")}</div></div>
        </div>
      </div>`}
    </div>`;
  updateClipTask();
  $("#e-del").onclick = async () => { if (confirm("Удалить клип из библиотеки?")) { await act(() => api(`/api/clip/${c.id}/delete`, {})); S.clip = null; refreshData(); } };
  if (!hasSrc && !$("#e-src")) return;
  if (!hasSrc) {
    $("#e-src").onclick = () => act(() => api(`/api/clip/${c.id}/source`, { url: $("#e-url").value.trim() }), "Ищу и скачиваю…");
    return;
  }
  const vid = $("#vid");
  const s0 = $("#s0"), s1 = $("#s1");
  const drawTl = () => {
    const d = vid.duration || c.source_duration || 1;
    const a = +s0.value, b = +s1.value;
    $("#tl").innerHTML = (c.suggestions || []).map((s) => `<div class="sug ${s.source}" style="left:${(s.start / d) * 100}%;width:${((s.end - s.start) / d) * 100}%"></div>`).join("")
      + `<div class="sel" style="left:${(a / d) * 100}%;width:${((b - a) / d) * 100}%"></div><div class="ph" style="left:${((vid.currentTime || 0) / d) * 100}%"></div>`;
    $("#slen").textContent = (b - a).toFixed(1) + " c";
  };
  vid.onloadedmetadata = () => { drawTl(); vid.currentTime = +s0.value; };
  drawTl();
  vid.ontimeupdate = () => { drawTl(); if (vid.dataset.sel && vid.currentTime >= +s1.value) { vid.pause(); delete vid.dataset.sel; } };
  $("#tl").onclick = (e) => { const r = e.currentTarget.getBoundingClientRect(); vid.currentTime = ((e.clientX - r.left) / r.width) * (vid.duration || 1); };
  $("#in").onclick = () => { s0.value = vid.currentTime.toFixed(1); saveSeg(); };
  $("#out").onclick = () => { s1.value = vid.currentTime.toFixed(1); saveSeg(); };
  s0.onchange = s1.onchange = saveSeg;
  $("#play-sel").onclick = () => { vid.currentTime = +s0.value; vid.dataset.sel = 1; vid.play(); };
  function saveSeg() {
    if (+s1.value <= +s0.value) s1.value = (+s0.value + 5).toFixed(1);
    drawTl();
    api(`/api/clip/${c.id}/update`, { segment: { start: +s0.value, end: +s1.value } }).then((u) => (S.clip = u)).catch((e) => toast(e.message, "err"));
  }
  $$(".sugg").forEach((el) => (el.onclick = () => { const s = c.suggestions[+el.dataset.i]; s0.value = s.start.toFixed(1); s1.value = s.end.toFixed(1); saveSeg(); vid.currentTime = s.start; vid.dataset.sel = 1; vid.play(); }));
  $$("[data-pv]").forEach((b) => (b.onclick = () => {
    $$("[data-pv]").forEach((x) => x.classList.toggle("on", x === b));
    const r = b.dataset.pv === "render";
    $("#pl").classList.toggle("v", r);
    vid.src = r ? media(c.render.file) + "?t=" + (c.render.t || 0) : media(c.source_file);
  }));
  $$(".tpl").forEach((el) => (el.onclick = () => { $$(".tpl").forEach((x) => x.classList.toggle("on", x === el)); $("#e-comm-wrap").style.display = el.dataset.t === "commentary" ? "" : "none"; }));
  $("#e-mvol").oninput = (e) => ($("#mv").textContent = Math.round(e.target.value * 100) + "%");
  $("#e-ai").onclick = () => act(() => api(`/api/clip/${c.id}/analyze`, {}), "Gemini смотрит видео…");
  const collect = () => ({
    template: $(".tpl.on")?.dataset.t || "cinema",
    segments: parts && Math.abs(parts[0][0] - +s0.value) < 0.6 && Math.abs(parts[parts.length - 1][1] - +s1.value) < 0.6 ? parts : [[+s0.value, +s1.value]],
    transition: $("#e-trans").value, use_subtitles: $("#e-subs").checked,
    hook: $("#e-hook").value, caption: $("#e-cap").value, commentary: $("#e-comm").value,
    music: $("#e-music").value, music_volume: +$("#e-mvol").value,
    effects: Object.fromEntries($$("[data-eff]").map((x) => [x.dataset.eff, x.checked])),
    loop_friendly: $("#e-loop").checked, progress_bar: $("#e-bar").checked,
  });
  $("#e-render").onclick = () => act(() => api(`/api/clip/${c.id}/render`, collect()), "Монтаж запущен");
  $("#e-ag").onclick = () => sendAG(c.id);
  $("#e-skip").onclick = async () => { await act(() => api(`/api/clip/${c.id}/skip_ag`, {}), "Отправлено в публикацию"); refreshData(); };
}
async function sendAG(id) { await act(() => api(`/api/clip/${id}/antigravity`, {}), "Готовлю задачу для Antigravity…"); }

/* ---------------- Antigravity ---------------- */
async function viewAgent() {
  const list = S.clips.filter((c) => c.ag && c.ag.dir);
  const h = S.state?.health?.antigravity || {};
  const stCls = { running: "warn", manual: "warn", prepared: "", done: "ok", error: "err" };
  const stName = { running: "работает", manual: "ждёт вставки промпта", prepared: "подготовлено", done: "готово", error: "ошибка" };
  $("#main").innerHTML = `
    <div class="head"><h1>Antigravity</h1><span class="sub">агент ${esc(h.model || "")} монтирует финальную версию</span><div class="spacer"></div>
      <span class="chip ${h.agentapi ? "ok" : "warn"}">${h.agentapi ? "agentapi найден — автозапуск" : "ручной режим — промпт в буфер"}</span></div>
    <div class="card" style="margin-bottom:16px"><div class="grid g3" style="font-size:13px">
      <div><b>1. Отдаём</b><br><small>Папка задачи: чистый фрагмент, черновик 9:16, раскладка, музыка, шрифт, <code>BRIEF.md</code> с заданием и <code>job.json</code>.</small></div>
      <div><b>2. Агент работает</b><br><small>Смотрит видео, делает хук, зумы, ритм под бит, субтитры, луп. Пишет статус в <code>output/status.txt</code>.</small></div>
      <div><b>3. Забираем</b><br><small>Когда появляются <code>output/final.mp4</code> и <code>result.json</code>, VideoHook сам импортирует ролик и описания.</small></div></div></div>
    ${list.length ? `<div class="col">${list.map((c) => `<div class="card tight"><div class="row">
        <div class="th" style="width:48px;height:64px;border-radius:6px;background:var(--panel2) url('${media(c.render?.thumb || c.thumb)}') center/cover"></div>
        <div style="flex:1;min-width:0"><b>${esc(c.anime)} — ${esc(c.title)}</b><br>
          <span class="chip ${stCls[c.ag.status] || ""}">${c.ag.status === "running" ? '<span class="spin"></span> ' : ""}${stName[c.ag.status] || c.ag.status}</span>
          <small> ${esc(c.ag.message || "")}</small>${c.ag.conversation_id ? `<br><small class="mono">диалог ${esc(c.ag.conversation_id)}</small>` : ""}</div>
        <div class="row">${c.ag.prompt ? `<button class="btn sm ${c.ag.status === "manual" ? "primary" : ""}" onclick="copyPrompt('${c.id}')">Копировать промпт</button>` : ""}
          <button class="btn sm" onclick="openFile('${c.id}','job')">Папка задачи</button>
          <button class="btn sm" onclick="checkAG('${c.id}')">Проверить</button>
          ${c.ag.status !== "done" ? `<button class="btn sm ghost" onclick="skipAG('${c.id}')">Взять черновик</button>` : ""}
          ${c.ag.status === "done" || c.ag.status === "error" ? `<button class="btn sm ghost" onclick="sendAG('${c.id}')">Повторить</button>` : ""}</div></div>
        ${(c.ag.edits || []).length ? `<div class="log" style="margin-top:8px">${c.ag.edits.map((e) => "• " + esc(e)).join("<br>")}</div>` : ""}</div>`).join("")}</div>`
      : `<div class="empty"><b>Задач пока нет</b>Смонтируйте клип в студии и нажмите «Доработать в Antigravity».</div>`}`;
}
function copyPrompt(id) { const c = S.clips.find((x) => x.id === id); if (c?.ag?.prompt) copy(c.ag.prompt); }
async function checkAG(id) { await act(() => api(`/api/clip/${id}/antigravity/check`, {})); refreshData(); }
async function skipAG(id) { await act(() => api(`/api/clip/${id}/skip_ag`, {}), "Черновик отправлен в публикацию"); refreshData(); }
function openFile(id, what) { act(() => api(`/api/clip/${id}/open`, { what })); }

/* ---------------- Публикация ---------------- */
const PLAT = { youtube: "YouTube Shorts", tiktok: "TikTok", instagram: "Instagram Reels" };
function viewPublish() {
  const ready = S.clips.filter((c) => c.stage === "ready");
  const done = S.clips.filter((c) => c.stage === "published");
  const h = S.state?.health || {};
  $("#main").innerHTML = `
    <div class="head"><h1>Публикация</h1><span class="sub">тексты под каждую площадку · загрузка в один клик</span><div class="spacer"></div>
      <span class="chip ${h.youtube?.connected ? "ok" : ""}">YouTube: ${h.youtube?.connected ? "API" : "вручную"}</span>
      <span class="chip ${h.tiktok?.configured ? "ok" : ""}">TikTok: ${h.tiktok?.configured ? "API" : "вручную"}</span><span class="chip">Instagram: ассистент</span></div>
    ${ready.length ? `<div class="col" style="gap:16px">${ready.map(pubCard).join("")}</div>` : `<div class="empty"><b>Нет готовых роликов</b>Готовые ролики из Antigravity или студии появятся здесь.</div>`}
    ${done.length ? `<h2 style="margin-top:28px">Опубликовано</h2><div class="col">${done.map((c) => `<div class="card tight row"><b style="flex:1">${esc(c.anime)} — ${esc(c.title)}</b>
      ${Object.entries(c.publish?.status || {}).map(([k, v]) => `<span class="chip ${v.state === "published" ? "ok" : ""}">${PLAT[k]}: ${v.url ? `<a href="${esc(v.url)}" target="_blank">${v.state}</a>` : v.state}</span>`).join("")}</div>`).join("")}</div>` : ""}`;
  ready.forEach(bindPubCard);
}
function pubCard(c) {
  const tab = S.pubTab[c.id] || "youtube";
  const caps = c.publish?.captions || {};
  const cp = caps[tab] || {};
  const status = c.publish?.status || {};
  const lim = { youtube: 5000, tiktok: 2200, instagram: 2200 }[tab];
  const text = tab === "youtube" ? cp.description || "" : cp.caption || "";
  const h = S.state?.health || {};
  const apiOk = (tab === "youtube" && h.youtube?.connected) || (tab === "tiktok" && h.tiktok?.configured);
  return `<div class="card pubcard" data-id="${c.id}">
    <div><video src="${media(c.final_file)}" poster="${media(c.cover)}" controls preload="none"></video>
      <div class="row" style="margin-top:6px"><button class="btn sm" data-a="reveal">Файл</button><a class="btn sm" href="${media(c.final_file)}" download>Скачать</a></div></div>
    <div class="stack">
      <div class="row between"><div><b>${esc(c.anime)} — ${esc(c.title)}</b><br><small>${esc(c.credit)}</small></div>
        <button class="btn sm" data-a="gen">✨ ${caps.youtube ? "Переписать" : "Написать"} описания${S.state?.health?.gemini?.api || S.state?.health?.gemini?.antigravity ? " (Gemini)" : ""}</button></div>
      <div class="seg">${Object.entries(PLAT).map(([k, n]) => `<button data-tab="${k}" class="${k === tab ? "on" : ""}">${n}${status[k] ? " ✓" : ""}</button>`).join("")}</div>
      ${!caps.youtube ? `<div class="empty" style="padding:16px">Описания ещё не готовы — нажмите «Написать описания».</div>` : `
      ${tab === "youtube" ? `<label class="f">Заголовок<input data-f="title" value="${esc(cp.title || "")}" maxlength="100"></label>` : ""}
      <label class="f">${tab === "youtube" ? "Описание" : "Подпись"}<textarea data-f="${tab === "youtube" ? "description" : "caption"}" rows="5">${esc(text)}</textarea></label>
      <div class="counter" data-cnt>${text.length} / ${lim}</div>
      ${tab === "youtube" ? `<label class="f">Теги<input data-f="tags" value="${esc((cp.tags || []).join(", "))}"></label>` : ""}
      ${caps.pinned_comment ? `<small class="muted">Закреп: ${esc(caps.pinned_comment)}</small>` : ""}
      <div class="row">
        ${apiOk ? `<button class="btn primary" data-a="api">⬆ Загрузить в ${PLAT[tab]}</button>` : ""}
        <button class="btn ${apiOk ? "" : "primary"}" data-a="assist">📋 Копировать и открыть ${PLAT[tab]}</button>
        <input data-f="url" placeholder="ссылка на опубликованный ролик" style="max-width:260px"><button class="btn sm" data-a="mark">Отметить опубликованным</button></div>
      ${status[tab] ? `<small class="pstat">Статус: ${esc(status[tab].state)} ${status[tab].url ? `<a href="${esc(status[tab].url)}" target="_blank">${esc(status[tab].url)}</a>` : ""}</small>` : ""}`}
    </div></div>`;
}
function bindPubCard(c) {
  const el = $(`.pubcard[data-id="${c.id}"]`);
  if (!el) return;
  const tab = S.pubTab[c.id] || "youtube";
  $$("[data-tab]", el).forEach((b) => (b.onclick = () => { saveCaps(c, el, tab); S.pubTab[c.id] = b.dataset.tab; viewPublish(); }));
  const ta = $("textarea", el);
  if (ta) ta.oninput = () => { const lim = { youtube: 5000, tiktok: 2200, instagram: 2200 }[tab]; const cnt = $("[data-cnt]", el); cnt.textContent = `${ta.value.length} / ${lim}`; cnt.classList.toggle("over", ta.value.length > lim); };
  $$("input[data-f],textarea[data-f]", el).forEach((i) => i.dataset.f !== "url" && (i.onchange = () => saveCaps(c, el, tab)));
  const on = (a, fn) => { const b = $(`[data-a="${a}"]`, el); if (b) b.onclick = fn; };
  on("reveal", () => openFile(c.id, "final"));
  on("gen", async () => { await act(() => api(`/api/clip/${c.id}/captions`, { use_ai: true }), "Описания готовы"); refreshData(); });
  on("assist", async () => {
    await saveCaps(c, el, tab);
    const cp = (S.clips.find((x) => x.id === c.id)?.publish?.captions || {})[tab] || {};
    await copy(tab === "youtube" ? `${cp.title}\n\n${cp.description}` : cp.caption);
    await act(() => api(`/api/clip/${c.id}/publish`, { platform: tab, mode: "assisted" }), "Текст в буфере, файл выделен, загрузчик открыт");
  });
  on("api", async () => {
    await saveCaps(c, el, tab);
    if (tab === "tiktok") { const cp = (c.publish?.captions || {}).tiktok || {}; await copy(cp.caption || ""); }
    await act(() => api(`/api/clip/${c.id}/publish`, { platform: tab }), `Загрузка в ${PLAT[tab]} запущена`);
  });
  on("mark", async () => { await act(() => api(`/api/clip/${c.id}/mark_published`, { platform: tab, url: $('[data-f="url"]', el).value.trim() }), "Отмечено"); refreshData(); });
}
async function saveCaps(c, el, tab) {
  const caps = JSON.parse(JSON.stringify(c.publish?.captions || {}));
  if (!caps.youtube) return;
  const cp = (caps[tab] = caps[tab] || {});
  $$("[data-f]", el).forEach((i) => {
    if (i.dataset.f === "url") return;
    cp[i.dataset.f] = i.dataset.f === "tags" ? i.value.split(",").map((x) => x.trim()).filter(Boolean) : i.value;
  });
  const u = await api(`/api/clip/${c.id}/captions`, { captions: caps }).catch((e) => toast(e.message, "err"));
  if (u) { const i = S.clips.findIndex((x) => x.id === c.id); if (i >= 0) S.clips[i] = u; }
}

/* ---------------- Настройки ---------------- */
function viewSettings() {
  const s = S.state?.settings || {};
  const h = S.state?.health || {};
  const tpl = S.state?.templates || {};
  const f = (k, label, type = "text", hint = "") => `<label class="f">${label}<input data-k="${k}" type="${type}" value="${esc(s[k] ?? "")}" ${type === "password" ? `placeholder="${s[k + "_set"] ? "сохранён — введите новый, чтобы заменить" : ""}"` : ""}>${hint ? `<small>${hint}</small>` : ""}</label>`;
  const cb = (k, label) => `<label class="chk"><input type="checkbox" data-k="${k}" ${s[k] ? "checked" : ""}> ${label}</label>`;
  $("#main").innerHTML = `
    <div class="head"><h1>Настройки</h1><span class="sub">рабочая папка: <code>${esc(S.state?.work_dir || "")}</code></span><div class="spacer"></div>
      <button class="btn" id="hc">↻ Проверить подключения</button></div>
    <div class="grid g2">
      <div class="card stack"><h2>Канал</h2>
        ${f("brand_handle", "Подпись канала на ролике", "text", "показывается внизу каждого ролика, например @anime_hook")}
        ${f("channel_name", "Название канала", "text", "используется в описаниях и подсказках ИИ")}
        <label class="f">Подпись в описаниях<textarea data-k="caption_signature" rows="3" placeholder="Например: Подписывайся — каждый день лучшие моменты аниме 🔥">${esc(s.caption_signature || "")}</textarea><small>Добавляется в конец описания каждого ролика на всех площадках.</small></label>
        ${f("channel_hashtags", "Постоянные хэштеги канала", "text", "через пробел: anime_hook аниме_моменты — добавятся к каждому описанию и тегам YouTube")}
        <h3 style="margin:6px 0 0">Ресурсы канала</h3>
        <div class="grid g2">${f("telegram", "Telegram", "text", "https://t.me/…")}${f("link_youtube", "YouTube", "text", "https://youtube.com/@…")}
          ${f("link_tiktok", "TikTok", "text", "https://tiktok.com/@…")}${f("link_instagram", "Instagram", "text", "https://instagram.com/…")}
          ${f("link_vk", "VK", "text", "https://vk.com/…")}${f("link_donate", "Поддержка (Boosty, донаты)", "text", "https://boosty.to/…")}</div>
        ${cb("links_in_short_captions", "Добавлять ссылки и в TikTok/Instagram (там они не кликабельны)")}
        <small class="muted">Ссылки и подпись вставляются в описания автоматически — вручную править каждый ролик не нужно.</small></div>
      <div class="card stack"><h2>Google AI Pro — Gemini</h2>${cb("ai_via_antigravity", "ИИ-задачи через Antigravity (приоритет: мощнее модель, больше лимитов)")}
        <label class="f">API-ключ Gemini<input data-k="gemini_api_key" type="password" placeholder="${s.gemini_api_key_set ? "сохранён " + esc(s.gemini_api_key) : "AIza…"}"><small>Ключ бесплатно: <a href="https://aistudio.google.com/apikey" target="_blank">aistudio.google.com/apikey</a>. Запасной канал, если Antigravity недоступен.</small></label>
        ${f("gemini_model", "Модель Gemini", "text", "например gemini-flash-latest или gemini-pro-latest")}
                <small class="${h.gemini?.api || h.gemini?.antigravity ? "ok-t" : "warn-t"}">${h.gemini?.antigravity ? "● работает через Antigravity" : h.gemini?.api ? "● API подключён" : "○ не подключено"}</small></div>
      <div class="card stack"><h2>Antigravity</h2>${cb("use_antigravity", "Отправлять ролики на доработку агенту")}
        ${f("antigravity_model", "Модель агента (для задания)")}${f("antigravity_model_flag", "Значение --model для agentapi")}
        ${cb("antigravity_auto_launch", "Запускать Antigravity автоматически, если он закрыт")}${f("antigravity_cmd", "Путь к Antigravity.exe", "text", "пусто — автопоиск")}
        ${f("antigravity_brain_dir", "Папка brain (статус диалогов)", "text", "пусто — ~/.gemini/antigravity/brain")}
        <small class="${h.antigravity?.agentapi ? "ok-t" : "warn-t"}">${h.antigravity?.running ? "● подключено: " + esc(h.antigravity.cmd) : h.antigravity?.installed ? "● установлен — запустится при первой задаче" : "○ Antigravity не найден — будет ручная вставка промпта"}</small></div>
      <div class="card stack"><h2>Монтаж</h2>
        <label class="f">Подложка по умолчанию<select data-k="default_template">${Object.entries(tpl).map(([k, n]) => `<option value="${k}" ${s.default_template === k ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></label>
        <label class="f">Музыка по умолчанию<select data-k="default_music"><option value="none" ${s.default_music === "none" ? "selected" : ""}>Родной звук сцены (рекомендуется)</option><option value="auto" ${s.default_music === "auto" ? "selected" : ""}>Музыка по настроению сцены</option>${Object.entries(S.state?.music || {}).map(([k, n]) => `<option value="${k}" ${s.default_music === k ? "selected" : ""}>${esc(n)}</option>`).join("")}</select></label>
        ${f("music_volume", "Громкость музыки (0–0.6)", "number")}${f("clip_min_seconds", "Минимальная длина ролика, c (если исходник позволяет)", "number")}${f("clip_max_seconds", "Максимальная длина ролика, c", "number")}
        ${cb("auto_rate_moments", "ИИ оценивает шанс успеха новых моментов")}${cb("loop_friendly", "Мягкий луп по умолчанию (кроссфейд звука в конце)")}
        <label class="f">Русские субтитры<select data-k="subtitles_mode"><option value="auto" ${s.subtitles_mode !== "off" ? "selected" : ""}>Добавлять, если есть реплики</option><option value="off" ${s.subtitles_mode === "off" ? "selected" : ""}>Не добавлять</option></select></label>
        ${cb("prefer_ru_dub", "Искать источник с русской озвучкой в первую очередь")}</div>
      <div class="card stack"><h2>YouTube Shorts</h2>
        ${f("youtube_client_secret", "Путь к client_secret.json", "text", "Google Cloud Console → APIs → YouTube Data API v3 → OAuth-клиент «Приложение для ПК»")}
        <label class="f">Приватность при загрузке<select data-k="youtube_privacy">${["private", "unlisted", "public"].map((v) => `<option ${s.youtube_privacy === v ? "selected" : ""}>${v}</option>`).join("")}</select></label>
        <div class="row"><a class="btn sm" href="/api/youtube/connect" target="_blank">Подключить YouTube</a><small class="${h.youtube?.connected ? "ok-t" : "muted"}">${h.youtube?.connected ? "● подключён" : "не подключён"}</small></div></div>
      <div class="card stack"><h2>TikTok и автопубликация</h2>
        <label class="f">TikTok access token (Content Posting API, scope video.upload)<input data-k="tiktok_access_token" type="password" placeholder="${s.tiktok_access_token_set ? "сохранён " + esc(s.tiktok_access_token) : ""}"><small>Ролик уходит во «Входящие» TikTok — подпись вставляете в приложении (она копируется автоматически).</small></label>
        ${cb("auto_publish", "Автоматически загружать готовые ролики через подключённые API")}
        <small class="muted">Instagram Reels публикуется ассистентом: подпись в буфер, файл выделен, открыт instagram.com.</small></div>
    </div>
    <div class="savebar"><small class="muted" id="save-state">Изменения сохраняются автоматически</small>
      <button class="btn primary" id="save">Сохранить</button></div>`;
  $("#hc").onclick = async () => { await act(() => api("/api/health"), "Проверено"); await poll1(); viewSettings(); };
  const readInput = (i) => {
    if (i.type === "checkbox") return i.checked;
    if (i.type === "number") return +i.value;
    return i.value.trim();
  };
  const collectSettings = () => {
    const patch = {};
    $$("[data-k]", $("#main")).forEach((i) => {
      if (i.type === "password" && !i.value.trim()) return;   // пустое поле ключа — не затираем сохранённый
      patch[i.dataset.k] = readInput(i);
    });
    return patch;
  };
  const pending = {};
  const flush = debounce(async () => {
    const patch = { ...pending };
    Object.keys(pending).forEach((k) => delete pending[k]);
    if (!Object.keys(patch).length) return;
    $("#save-state").textContent = "Сохраняю…";
    try {
      S.state.settings = await api("/api/settings", patch);
      $("#save-state").textContent = "✓ Сохранено";
    } catch (e) { $("#save-state").textContent = "Не сохранено: " + e.message; toast(e.message, "err"); }
  }, 600);
  $$("[data-k]", $("#main")).forEach((i) => {
    const ev = i.type === "checkbox" || i.tagName === "SELECT" ? "change" : "input";
    i.addEventListener(ev, () => {
      if (i.type === "password" && !i.value.trim()) return;
      pending[i.dataset.k] = readInput(i);
      $("#save-state").textContent = "Есть изменения…";
      flush();
    });
  });
  $("#save").onclick = async () => {
    S.state.settings = await act(() => api("/api/settings", collectSettings()), "Настройки сохранены");
    $("#save-state").textContent = "✓ Сохранено";
    await api("/api/health");
    await poll1(); viewSettings();
  };
}
async function poll1() { S.state = await api("/api/state"); }

function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

/* старт */
(async () => {
  try { S.state = await api("/api/state"); S.clips = await api("/api/clips"); } catch (e) { toast("Сервер недоступен: " + e.message, "err"); }
  render();
  poll();
})();
