/* HARD_GATE#845 CARD_H_LOG_CLEAR_GLOBAL */
/* HARD_GATE#834 PAIN_FIX_BATCH */
/* CMCC Alive WebUI — multi-account console */
(function () {
  "use strict";

  const TOKEN_KEY = "cmcc_webui_token";

  const state = {
    profiles: [],
    configPid: null,
    drafts: Object.create(null),
    logs: Object.create(null),
    globalLog: [],
    busy: Object.create(null),
    cardMsg: Object.create(null),
    desktops: Object.create(null),
    jobsById: Object.create(null),
    jobsByProfile: Object.create(null),
    tokenRequired: false,
    es: null,
    sseNeedTokenLogged: false,
    logModalPid: null,
    logModalReturnFocus: null,
    composer: {
      /* HARD_GATE#871c: composer 初始占位；卡片以用户/档案选择为准，禁止全局强制 */
      protocol: "ZTE",
      clientProfile: "linux",
      mode: "live",
      userServiceId: "",
      desktopLabel: "",
      profileId: "",
    },
    /* HARD_GATE#871: 从日志/桌面列表缓存的云桌面状态文案 */
    desktopStatusByPid: Object.create(null),
  };

  function $(sel, root) {
    return (root || document).querySelector(sel);
  }

  function $$(sel, root) {
    return Array.prototype.slice.call((root || document).querySelectorAll(sel));
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function toast(msg, isError) {
    const el = $("#toast");
    if (!el) return;
    el.textContent = msg;
    el.classList.toggle("error", !!isError);
    el.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(function () {
      el.classList.add("hidden");
    }, 2800);
  }

  function getToken() {
    try {
      return localStorage.getItem(TOKEN_KEY) || "";
    } catch (_) {
      return "";
    }
  }

  function setToken(v) {
    try {
      if (v) localStorage.setItem(TOKEN_KEY, v);
      else localStorage.removeItem(TOKEN_KEY);
    } catch (_) {}
  }

  function humanError(err, fallback) {
    if (!err) return fallback || "操作失败";
    if (typeof err === "string") return err;
    const code = err.code || err.error || err.error_code || "";
    const msg = err.message || err.detail || err.error_message || "";
    const next =
      err.nextStep ||
      err.next_step ||
      (err.payload && (err.payload.nextStep || err.payload.next_step)) ||
      (err.data && (err.data.nextStep || err.data.next_step)) ||
      "";
    const map = {
      PROFILE_IN_USE: "该账号已在保活中，请先停止再启动",
      VALIDATION: "填写有误，请检查账号、密码或配置",
      NOT_FOUND: "账号不存在或已删除",
      UNAUTHORIZED: "未授权，请检查访问令牌",
      FORBIDDEN: "没有权限执行此操作",
      LIVE_DISABLED: "当前环境未开启长期保活，请改用「单轮」或联系管理员",
      LOGIN_FAILED: "登录失败，请检查账号密码",
      AUTH_FAILED: "账号或密码错误",
      AUTH_EXPIRED: "登录会话失效，请重新登录",
      HTTP_401: "登录失败（401）：账号密码错误或会话失效",
      401: "登录失败（401）：账号密码错误或会话失效",
      AUTH_REQUIRED: "需要先登录账号",
      LOGIN_REQUIRED: "请先登录账号",
      DESKTOP_REQUIRED: "请先选择云桌面再启动",
      NETWORK: "网络异常，请稍后重试",
    };
    let base = "";
    if (code && map[code]) {
      base = map[code];
    } else if (msg && typeof msg === "string") {
      if (/PROFILE_IN_USE/i.test(msg)) base = map.PROFILE_IN_USE;
      else if (/LIVE_DISABLED/i.test(msg)) base = map.LIVE_DISABLED;
      else if (/AUTH_REQUIRED/i.test(msg)) base = map.AUTH_REQUIRED;
      else if (/LOGIN_REQUIRED/i.test(msg)) base = map.LOGIN_REQUIRED;
      else if (/JSON|\{|\}|\[|\]/.test(msg) && msg.length > 120) {
        base = fallback || "服务返回异常，请稍后重试";
      } else base = msg;
    } else {
      base = fallback || "操作失败，请稍后重试";
    }
    if (next) {
      const n = String(next);
      if (base.indexOf(n) < 0) base = base + " · 下一步：" + n;
    }
    return base;
  }

  async function api(path, opts) {
    opts = opts || {};
    const headers = Object.assign(
      { Accept: "application/json" },
      opts.headers || {}
    );
    const token = getToken();
    if (token) headers.Authorization = "Bearer " + token;
    let body = opts.body;
    if (body != null && typeof body !== "string") {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(body);
    }
    let res;
    try {
      res = await fetch(path, {
        method: opts.method || "GET",
        headers: headers,
        body: body,
      });
    } catch (e) {
      const err = new Error("网络异常，请稍后重试");
      err.code = "NETWORK";
      throw err;
    }
    const text = await res.text();
    let data = null;
    if (text) {
      try {
        data = JSON.parse(text);
      } catch (_) {
        data = { raw: text };
      }
    }
    if (!res.ok) {
      const err = new Error(
        humanError(
          data || {},
          "请求失败（" + res.status + "）"
        )
      );
      err.status = res.status;
      err.code = (data && (data.code || data.error || data.error_code)) || "";
      err.nextStep =
        data &&
        (data.nextStep ||
          data.next_step ||
          (data.data && (data.data.nextStep || data.data.next_step)));
      err.payload = data;
      err.data = data;
      err.message = humanError(err, err.message);
      throw err;
    }
    return data;
  }

  function statusOf(p) {
    const s = String(
      (p && (p.status || (p.job && p.job.status) || p.jobStatus)) || "idle"
    ).toLowerCase();
    if (s === "running" || s === "alive" || s === "starting") return "running";
    if (s === "error" || s === "failed" || s === "fail") return "error";
    if (s === "stopped" || s === "stop" || s === "exited") return "stopped";
    return "idle";
  }

  function statusLabel(st) {
    if (st === "running") return "保活中";
    if (st === "error") return "异常";
    if (st === "stopped") return "已停止";
    return "空闲";
  }

  function protocolLabel(v) {
    /* HARD_GATE#871c: label from value; empty → 未选 */
    const raw = String(v || "").toUpperCase();
    if (!raw) return "未选";
    const u = raw;
    if (u === "ZTE" || u === "ZX" || u === "ZHONGXING") return "中兴";
    return "深信服";
  }

  function clientLabel(v) {
    const c = String(v || "linux").toLowerCase();
    if (c === "windows") return "Windows";
    if (c === "mac") return "Mac";
    return "Linux";
  }

  function modeLabel(v) {
    /* HARD_GATE#718: button/label text forever = 永久 / 单轮 only */
    const m = String(v || "live").toLowerCase();
    if (m === "dry-run" || m === "dryrun" || m === "once" || m === "single") return "单轮";
    return "永久";
  }

  function modeIsOnce(v) {
    const m = String(v || "live").toLowerCase();
    return m === "dry-run" || m === "dryrun" || m === "once" || m === "single";
  }

  /* #848: 永久/单轮都走 LIVE 真子进程；单轮用 once，不再映射 dry-run(FakeBackend) */
  function modeApi(v) {
    return modeIsOnce(v) ? "once" : "live";
  }

  function durationForMode(mode, trafficSec) {
    if (modeIsOnce(mode)) {
      const t = Number(trafficSec || 60);
      return t > 0 ? t : 60;
    }
    return 0;
  }

  function jobOf(p) {
    if (!p) return null;
    if (p.job && typeof p.job === "object") return p.job;
    if (p.jobId && state.jobsById[p.jobId]) return state.jobsById[p.jobId];
    if (p.id && state.jobsByProfile[p.id]) return state.jobsByProfile[p.id];
    return null;
  }

  function resolveUserProtocol() {
    /* HARD_GATE#871c: user choice only — never force SCG globally */
    for (var i = 0; i < arguments.length; i++) {
      var v = arguments[i];
      if (v == null || v === "") continue;
      var u = String(v).toUpperCase();
      if (u === "ZX" || u === "ZHONGXING") u = "ZTE";
      if (u === "SANGFOR") u = "SCG";
      if (u === "ZTE" || u === "SCG") return u;
    }
    return "ZTE"; /* historical empty-only fallback, not product default force */
  }

  function ensureDraft(pid, p) {
    const job = jobOf(p);
    const protocol =
      resolveUserProtocol(p && p.protocol, p && p.lastOfficialProtocol, p && p.protocolHint, job && job.protocol);
    const mode =
      (p && p.mode) ||
      (job && job.mode) ||
      "live";
    if (!state.drafts[pid]) {
      state.drafts[pid] = {
        displayName: (p && p.displayName) || "",
        username: "",
        password: "",
        protocol: protocol,
        lastOfficialProtocol: protocol,
        clientProfile: (p && p.clientProfile) || "linux",
        mode: mode,
        intervalMin: 5,
        trafficSec: 60,
        durationSec: durationForMode(mode, 60),
        userServiceId: (p && p.userServiceId) || "",
        desktopLabel: (p && p.desktopLabel) || "",
        spuCode: (p && (p.spuCode || p.spu_code)) || "",
      };
    } else if (p) {
      const d = state.drafts[pid];
      if (!d.displayName && p.displayName) d.displayName = p.displayName;
      if (!d.userServiceId && p.userServiceId) d.userServiceId = p.userServiceId;
      if (!d.desktopLabel && p.desktopLabel) d.desktopLabel = p.desktopLabel;
      if (!d.spuCode && p && (p.spuCode || p.spu_code)) {
        d.spuCode = p.spuCode || p.spu_code;
      }
      if (!d.clientProfile && p.clientProfile) d.clientProfile = p.clientProfile;
      if (p.protocol) {
        d.protocol = p.protocol;
        d.lastOfficialProtocol = p.protocol;
      } else if (job && job.protocol) {
        d.protocol = job.protocol;
        if (!d.lastOfficialProtocol) d.lastOfficialProtocol = job.protocol;
      }
      if (!d.lastOfficialProtocol) d.lastOfficialProtocol = d.protocol || "ZTE";
      if (p.mode) d.mode = p.mode;
      else if (job && job.mode) d.mode = job.mode;
    }
    if (!state.drafts[pid].lastOfficialProtocol) {
      state.drafts[pid].lastOfficialProtocol = resolveUserProtocol(state.drafts[pid].protocol, state.drafts[pid].lastOfficialProtocol);
    }
    return state.drafts[pid];
  }

  function pushGlobal(line, level) {
    state.globalLog.push({
      at: new Date().toISOString(),
      line: String(line || ""),
      level: level || "info",
    });
    if (state.globalLog.length > 300) {
      state.globalLog = state.globalLog.slice(-300);
    }
    renderGlobalLog();
  }

  /* HARD_GATE#768-B: card-only keepalive/job log sink (never global) */
  function patchCardStatus(pid) {
    // HARD_GATE#784: update status chrome only; leave log DOM untouched
    if (!pid) return;
    const p = state.profiles.find(function (x) {
      return x.id === pid;
    });
    if (!p) return;
    const st = statusOf(p);
    const card = document.querySelector('article.card[data-id="' + pid + '"]');
    if (!card) return;
    card.className = card.className
      .split(/\s+/)
      .filter(function (c) {
        return c && c.indexOf("status-") !== 0;
      })
      .concat(["status-" + st])
      .join(" ");
    if (card.className.indexOf("card") < 0) card.className = "card " + card.className;
    // ensure base card class retained
    if (!/\bcard\b/.test(card.className)) card.className = "card " + card.className;
    const badge = card.querySelector(".status-badge, .card-status, [data-status-label]");
    if (badge) badge.textContent = statusLabel(st);
    const open = state.configPid === pid;
    if (open) card.classList.add("is-configuring");
    else card.classList.remove("is-configuring");
    // busy buttons
    const busy = !!state.busy[pid];
    const acts = card.querySelectorAll("[data-act]");
    for (let i = 0; i < acts.length; i++) {
      if (busy) acts[i].setAttribute("disabled", "disabled");
      else acts[i].removeAttribute("disabled");
    }
    // start/stop visibility if present
    const startBtn = card.querySelector('[data-act="start"]');
    const stopBtn = card.querySelector('[data-act="stop"]');
    if (startBtn && stopBtn) {
      if (st === "running") {
        startBtn.hidden = true;
        stopBtn.hidden = false;
      } else {
        startBtn.hidden = false;
        stopBtn.hidden = true;
      }
    }
  }

  function pushCard(pid, line, at) {
    // HARD_GATE#854: buffer + immediate paint (SSE must reappear after clear; 6s poll is backup)
    if (!pid || !line) return;
    const arr = state.logs[pid] || (state.logs[pid] = []);
    try { patchCardDeskStatus(pid); } catch (_e) {}
    const entry = { at: at || new Date().toISOString(), line: String(line) };
    arr.push(entry);
    if (arr.length > 300) state.logs[pid] = arr.slice(-300);
    try { patchCardDeskStatus(pid); } catch (_e) {}
    applyLogsToDom(pid, false);
  }

  function shanghaiHms(isoOrDate) {
    /* HARD_GATE#871c: full Asia/Shanghai [YYYY-MM-DD HH:mm:ss] like CLI */
    try {
      const d = isoOrDate instanceof Date ? isoOrDate : new Date(isoOrDate || Date.now());
      if (isNaN(d.getTime())) return "";
      const parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: "Asia/Shanghai",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      }).formatToParts(d);
      const get = (t) => (parts.find((p) => p.type === t) || {}).value || "";
      return get("year") + "-" + get("month") + "-" + get("day") + " " + get("hour") + ":" + get("minute") + ":" + get("second");
    } catch (e) {
      try {
        const d2 = new Date(isoOrDate || Date.now());
        const t = d2.getTime() + 8 * 3600 * 1000;
        const x = new Date(t);
        const y = x.getUTCFullYear();
        const mo = String(x.getUTCMonth() + 1).padStart(2, "0");
        const da = String(x.getUTCDate()).padStart(2, "0");
        const hh = String(x.getUTCHours()).padStart(2, "0");
        const mm = String(x.getUTCMinutes()).padStart(2, "0");
        const ss = String(x.getUTCSeconds()).padStart(2, "0");
        return y + "-" + mo + "-" + da + " " + hh + ":" + mm + ":" + ss;
      } catch (e2) {
        return "";
      }
    }
  }

  function extractDesktopStatusFromLogs(pid) {
    /* HARD_GATE#871: parse "云桌面状态：xxx" from recent log lines */
    const arr = state.logs[pid] || [];
    for (let i = arr.length - 1; i >= 0; i--) {
      const raw = String((arr[i] && (arr[i].line || arr[i].text)) || arr[i] || "");
      const m = raw.match(/云桌面状态[：:]\s*([^\s\|\[\]，,；;]+(?:\s*[^\s\|\[\]，,；;]+){0,4})/);
      if (m && m[1]) {
        const v = m[1].trim();
        if (v && v !== "—" && v !== "-") {
          state.desktopStatusByPid[pid] = v;
          return v;
        }
      }
      if (/开机运行中|运行中|已开机|关机|已关机|休眠|启动中/.test(raw)) {
        if (/开机运行中/.test(raw)) {
          state.desktopStatusByPid[pid] = "开机运行中";
          return "开机运行中";
        }
        if (/已关机|关机/.test(raw) && !/开机/.test(raw)) {
          state.desktopStatusByPid[pid] = "已关机";
          return "已关机";
        }
      }
    }
    return state.desktopStatusByPid[pid] || "";
  }

  function patchCardDeskStatus(pid) {
    /* HARD_GATE#871_PATCH_LOG_STATUS: never leave em-dash while keepalive running */
    const card = document.querySelector('.card[data-id="' + pid + '"]');
    if (!card) return;
    const el = card.querySelector("[data-desk-status]");
    if (!el) return;
    let v = extractDesktopStatusFromLogs(pid);
    if (!v) {
      const st = (state.profiles && state.profiles[pid] && state.profiles[pid].status) || "";
      const job = (state.jobs && state.jobs[pid]) || {};
      const running = /run|alive|keep|active|ing/i.test(String(st)) ||
        /run|alive|active/i.test(String(job.status || job.state || ""));
      const cardRun = card.classList.contains("is-running") || card.getAttribute("data-running") === "1";
      if (running || cardRun) v = "保活中";
    }
    if (!v) return;
    el.textContent = v;
    el.setAttribute("title", v);
    state.desktopStatusByPid = state.desktopStatusByPid || {};
    state.desktopStatusByPid[pid] = v;
  }


  function renderGlobalLog() {
    const box = $("#global-log");
    if (!box) return;
    const lines = state.globalLog.slice(-200);
    if (!lines.length) {
      box.innerHTML = '<div class="log-empty">暂无日志</div>';
      return;
    }
    box.innerHTML = lines
      .map(function (x) {
        const t = shanghaiHms(x.at) || shanghaiHms(Date.now()) || "";
        return (
          '<div class="log-line ' +
          esc(x.level || "") +
          '"><time>' +
          esc(t) +
          "</time><span>" +
          esc(x.line) +
          "</span></div>"
        );
      })
      .join("");
    box.scrollTop = box.scrollHeight;
  }

  function renderStats() {
    const counts = { total: 0, running: 0, idle: 0, error: 0 };
    for (let i = 0; i < state.profiles.length; i++) {
      const p = state.profiles[i];
      counts.total += 1;
      const st = statusOf(p);
      if (st === "running") counts.running += 1;
      else if (st === "error") counts.error += 1;
      else counts.idle += 1;
    }
    const root = $("#top-stats");
    if (!root) return;
    const map = {
      total: "账号 " + counts.total,
      running: "保活 " + counts.running,
      idle: "空闲 " + counts.idle,
      error: "异常 " + counts.error,
    };
    $$("[data-k]", root).forEach(function (el) {
      const k = el.getAttribute("data-k");
      if (map[k] != null) el.textContent = map[k];
    });
  }

  function classifyLogLine(line) {
    const s = String(line || "").toLowerCase();
    if (
      s.indexOf("token") >= 0 ||
      s.indexOf("refreshtoken") >= 0 ||
      s.indexOf("refresh token") >= 0 ||
      s.indexOf("刷新令牌") >= 0 ||
      s.indexOf("令牌刷新") >= 0
    ) {
      return "token";
    }
    if (
      s.indexOf("5xx") >= 0 ||
      s.indexOf(" http 5") >= 0 ||
      s.indexOf("status=5") >= 0 ||
      s.indexOf("soft recover") >= 0 ||
      s.indexOf("soft-recover") >= 0 ||
      s.indexOf("软恢复") >= 0 ||
      /\b5\d\d\b/.test(s)
    ) {
      return "warn";
    }
    if (
      s.indexOf("error") >= 0 ||
      s.indexOf("fail") >= 0 ||
      s.indexOf("exception") >= 0 ||
      s.indexOf("失败") >= 0 ||
      s.indexOf("异常") >= 0
    ) {
      return "error";
    }
    return "";
  }

  function formatLogDisplayLine(x) {
    // Backend product lines already embed [YYYY-MM-DD HH:MM:SS]; keep exact Python style.
    // For raw/orch lines without stamp, synthesize Shanghai wall stamp from entry.at.
    // HARD_GATE#861: parse ISO/Z/offset via Date so UTC "...Z" shows as Asia/Shanghai.
    const raw = String((x && x.line) || "");
    if (!raw) return "";
    if (/^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]/.test(raw)) return raw;
    const at = String((x && x.at) || "");
    let stamp = "";
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(at)) {
      try {
        const d = new Date(at);
        if (!isNaN(d.getTime())) {
          stamp = d.toLocaleString("sv-SE", {
            timeZone: "Asia/Shanghai",
            hour12: false,
          }).replace("T", " ").slice(0, 19);
        }
      } catch (e) {
        stamp = "";
      }
      if (!stamp) stamp = at.slice(0, 19).replace("T", " ");
    } else if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/.test(at)) {
      stamp = at.slice(0, 19);
    }
    return stamp ? "[" + stamp + "] " + raw : raw;
  }

  function profileLogsHtml(pid, opts) {
    // HARD_GATE#841: card = last 6 complete entries only (no empty slots);
    // modal (full) keeps entire history. Height follows content.
    opts = opts || {};
    const full = !!opts.full;
    const all = state.logs[pid] || [];
    const lines = full ? all : all.slice(-6);
    if (!lines.length) {
      if (full) {
        return '<div class="log-empty log-empty-fill">暂无日志。启动保活后这里会实时滚动。</div>';
      }
      // HARD_GATE#853: empty placeholder fills fixed viewport (no card shrink)
      return (
        '<div class="log-line log-line-py log-line-card log-line-empty log-empty-fill">' +
        '<span class="log-text">暂无日志。启动保活后这里会实时滚动。</span></div>'
      );
    }
    return lines
      .map(function (x) {
        const raw = formatLogDisplayLine(x);
        const level = classifyLogLine(raw);
        const rowCls = full
          ? 'log-line log-line-py log-line-full ' + level
          : 'log-line log-line-py log-line-card ' + level;
        const titleAttr = full ? '' : ' title="' + esc(raw) + '"';
        return (
          '<div class="' +
          rowCls +
          '"' +
          titleAttr +
          '><span class="log-text">' +
          esc(raw) +
          '</span></div>'
        );
      })
      .join('');
  }

  
  function ensureLogModal() {
    // HARD_GATE#768-C: log-modal alias + CSS shell .log-full-modal / .log-full-dialog
    // HARD_GATE#827: static #log-full-modal must still bind close/backdrop once
    let el = $("#log-modal") || $("#log-full-modal");
    if (!el) {
      el = document.createElement("div");
      el.id = "log-modal";
      el.className = "log-modal log-full-modal modal hidden";
      el.setAttribute("aria-hidden", "true");
      el.setAttribute("role", "dialog");
      el.setAttribute("aria-modal", "true");
      el.setAttribute("aria-labelledby", "log-full-title");
      el.innerHTML =
        '<div class="log-full-dialog modal-card log-modal-card">' +
        '<div class="log-full-head modal-head">' +
        '<h3 id="log-full-title" class="log-full-title">完整日志</h3>' +
        '<button type="button" class="btn btn-ghost modal-x" id="log-full-close" aria-label="关闭">×</button>' +
        "</div>" +
        '<div class="log-box log-full-body log-full-box card-log" id="log-full-body" data-log-modal-body="1"></div>' +
        "</div>";
      document.body.appendChild(el);
    }
    // HARD_GATE#833: bind close once; also re-hook static close button if recreated
    if (!el.dataset.closeBound) {
      el.dataset.closeBound = "1";
      el.addEventListener("click", function (ev) {
        if (ev.target === el || (ev.target && ev.target.getAttribute && ev.target.getAttribute("data-close-log-modal") != null)) {
          closeLogModal();
        }
      });
    }
    const closeBtn =
      el.querySelector("#log-full-close") ||
      el.querySelector("[data-close-log-modal], .modal-x, .log-full-close, .btn-log-close");
    if (closeBtn && closeBtn.dataset.boundClose !== "1") {
      closeBtn.dataset.boundClose = "1";
      closeBtn.addEventListener("click", function (ev) {
        ev.preventDefault();
        ev.stopPropagation();
        closeLogModal();
      });
    }
    return el;
  }

  function openLogModal(pid) {
    if (!pid) return;
    const el = ensureLogModal();
    const p = (state.profiles || []).find(function (x) {
      return x && x.id === pid;
    });
    const d = ensureDraft(pid, p || {});
    const name =
      (p && (p.displayName || p.usernameMasked || p.username)) || pid;
    const usid = (d && d.userServiceId) || (p && p.userServiceId) || "";
    const title = el.querySelector("#log-full-title");
    if (title) {
      title.textContent =
        "完整日志 · " + name + (usid ? " · 桌面 " + usid : "");
    }
    const body = el.querySelector("#log-full-body");
    if (body) body.innerHTML = profileLogsHtml(pid, { full: true });
    // HARD_GATE#810: force visible vs CSS .log-full-modal / [hidden] / .is-hidden
    // HARD_GATE#827: scroll lock + return focus; close must reverse cleanly
    state.logModalReturnFocus =
      document.activeElement && document.activeElement !== document.body
        ? document.activeElement
        : document.querySelector('.log-panel[data-pid="' + pid + '"]') ||
          document.querySelector('.card[data-pid="' + pid + '"] .log-panel') ||
          null;
    el.classList.remove("hidden", "is-hidden");
    el.classList.add("open", "is-open");
    el.removeAttribute("hidden");
    el.setAttribute("aria-hidden", "false");
    el.style.display = "flex";
    el.style.visibility = "visible";
    el.style.opacity = "1";
    el.style.pointerEvents = "auto";
    el.style.zIndex = "1200";
    el.setAttribute("data-pid", String(pid));
    document.body.classList.add("log-modal-open", "modal-open");
    document.body.style.overflow = "hidden";
    document.body.style.pointerEvents = "";
    state.logModalPid = pid;
    const closeBtn = el.querySelector("#log-full-close");
    if (closeBtn && typeof closeBtn.focus === "function") {
      try {
        closeBtn.focus();
      } catch (_) {}
    }
  }

  function closeLogModal() {
    // HARD_GATE#831 CARD_LOG_MODAL_CLOSE: X/backdrop/Esc clean close, no residual mask
    const el = $("#log-modal") || $("#log-full-modal");
    if (!el) return;
    el.classList.add("hidden", "is-hidden");
    el.classList.remove("open", "is-open", "show");
    el.setAttribute("hidden", "");
    el.setAttribute("aria-hidden", "true");
    el.style.display = "none";
    el.style.visibility = "hidden";
    el.style.opacity = "0";
    el.style.pointerEvents = "none";
    el.style.zIndex = "";
    document.body.classList.remove("log-modal-open", "modal-open");
    document.body.style.overflow = "";
    document.body.style.pointerEvents = "";
    el.removeAttribute("data-pid");
    state.logModalPid = null;
  }


  function desktopRowText(d) {
    const id = (d && (d.userServiceId || d.id)) || "";
    const label = (d && (d.desktopLabel || d.skuName || d.sku || d.name || d.label || d.vmName)) || id || "未命名";
    const spu = (d && (d.spuCode || d.spu_code)) || "—";
    return label + " / " + id + " | spuCode：" + spu;
  }

  function deskRefreshCtaHtml(pid, composer, loading) {
    /* HARD_GATE#781: centered text-link CTA (CLI Proxy style), not thick bordered btn.
       Do NOT use class desk-refresh-cta alone under old CSS (thick secondary btn).
       Keep data-act + desk-refresh-link; inline style guarantees text-link look without CSS ownership. */
    const act = composer ? "composer-desktops" : "desktops";
    const pidAttr = composer
      ? ""
      : ' data-pid="' + esc(pid || "") + '"';
    const label = loading ? "刷新中…" : "点击此处刷新云桌面列表";
    const disabled = loading ? " disabled aria-busy=\"true\"" : "";
    const busyCls = loading ? " is-loading" : "";
    const color = loading ? "var(--muted, #8b90a5)" : "var(--accent, #625fff)";
    const style =
      "display:block;width:100%;margin:6px 0 0;padding:4px 0;border:0;background:transparent;" +
      "box-shadow:none;border-radius:0;min-height:auto;height:auto;font:inherit;font-size:13px;" +
      "font-weight:500;letter-spacing:0;text-align:center;text-decoration:underline;" +
      "text-underline-offset:3px;cursor:" +
      (loading ? "wait" : "pointer") +
      ";color:" +
      color +
      ";";
    return (
      '<div class="desk-refresh-wrap" style="width:100%;text-align:center;">' +
      '<button type="button" class="desk-refresh-link desk-refresh-cta' +
      busyCls +
      '" data-act="' +
      act +
      '"' +
      pidAttr +
      ' title="刷新云桌面列表" aria-label="刷新云桌面列表" style="' +
      style +
      '"' +
      disabled +
      ">" +
      label +
      "</button></div>"
    );
  }

  function desktopSegmentedHtml(pid, selected, surface) {
    const list = state.desktops[pid] || [];
    const surfaceAttr = surface ? ' data-surface="1"' : "";
    const name = "desktop-" + pid + (surface ? "-surface" : "-modal");
    if (!list.length) {
      /* HARD_GATE#747: empty = real CTA button; keep selected id chip if any */
      const chip = selected
        ? '<span class="desk-selected-chip">' + esc(String(selected)) + "</span>"
        : "";
      return (
        '<div class="desk-seg is-empty desk-seg-refresh" role="group" aria-label="云桌面刷新">' +
        chip +
        deskRefreshCtaHtml(pid, false, !!state.busy[pid]) +
        "</div>"
      );
    }
    let html =
      '<div class="desk-seg" role="radiogroup" aria-label="云桌面">';
    for (let i = 0; i < list.length; i++) {
      const d = list[i] || {};
      const id = d.userServiceId || d.id || "";
      const label = d.desktopLabel || d.skuName || d.sku || d.name || d.label || d.vmName || id;
      const val = id + "||" + label;
      const checked = id === selected || label === selected;
      const text = desktopRowText(d);
      html +=
        '<label class="desk-seg-item' +
        (checked ? " is-active" : "") +
        '">' +
        '<input type="radio" name="' +
        esc(name) +
        '" data-pid="' +
        esc(pid) +
        '" data-key="desktop"' +
        surfaceAttr +
        ' value="' +
        esc(val) +
        '"' +
        (checked ? " checked" : "") +
        " />" +
        '<span class="desk-dot" aria-hidden="true"></span>' +
        '<span class="desk-seg-text">' +
        esc(text) +
        "</span></label>";
    }
    /* HARD_GATE#747: keep refresh after selected / list loaded */
    return html + "</div>" + deskRefreshCtaHtml(pid, false, !!state.busy[pid]);
  }

  /* compat alias — no native select options */
  function desktopOptionsHtml(pid, selected) {
    return desktopSegmentedHtml(pid, selected, false);
  }

  function cardHtml(p) {
    const pid = p.id;
    const st = statusOf(p);
    const open = state.configPid === pid;
    const d = ensureDraft(pid, p);
    const busy = !!state.busy[pid];
    const job = jobOf(p);
    const name = p.displayName || pid;
    const user = p.usernameMasked || "未设置账号";
    const usid = d.userServiceId || p.userServiceId || "";
    let deskLabel = d.desktopLabel || p.desktopLabel || "";
    /* resolve label from cached list for card-meta only; never spu on surface */
    if (usid && !deskLabel) {
      const dlist = state.desktops[pid] || [];
      for (let i = 0; i < dlist.length; i++) {
        const x = dlist[i];
        const xid = x.userServiceId || x.id || "";
        if (xid === usid) {
          deskLabel = x.desktopLabel || x.name || x.label || "";
          break;
        }
      }
    }
    /* HARD_GATE#736: surface id-only; no long desk/spu string */
    const deskIdText = usid || "未选";
    const deskShort = deskLabel || usid || "未选桌面";
    const client = p.clientProfile || d.clientProfile || "linux";
    const protocol =
      resolveUserProtocol(d.protocol, p && p.protocol, p && p.lastOfficialProtocol, p && p.protocolHint, job && job.protocol);
    const mode = d.mode || (p && p.mode) || (job && job.mode) || "live";
    /* HARD_GATE#871: 云桌面状态 = 桌面列表 / job / 日志「云桌面状态：xxx」缓存 */
    let deskStatus = "—";
    if (usid) {
      const dlist = state.desktops[pid] || [];
      for (let i = 0; i < dlist.length; i++) {
        const x = dlist[i];
        const xid = x.userServiceId || x.id || "";
        if (String(xid) === String(usid)) {
          deskStatus = desktopStatusText(x);
          break;
        }
      }
    }
    if (deskStatus === "—" || !deskStatus) {
      const jst =
        (job &&
          (job.desktopStatus ||
            job.vmStatusShow ||
            job.statusText ||
            job.cloudStatus)) ||
        "";
      if (jst) deskStatus = String(jst);
    }
    if ((deskStatus === "—" || !deskStatus) && state.desktopStatusByPid[pid]) {
      deskStatus = String(state.desktopStatusByPid[pid]);
    }
    if (deskStatus === "—" || !deskStatus) {
      const fromLog = extractDesktopStatusFromLogs(pid);
      if (fromLog) deskStatus = fromLog;
    }
    if ((deskStatus === "—" || !deskStatus) && (st === "running" || (job && job.status === "running"))) {
      deskStatus = "查询中…";
    }
    const errLine = String(state.cardMsg[pid] || "").trim();
    const running = st === "running";

    return (
      '<article class="card status-' +
      esc(st) +
      (open ? " is-configuring" : "") +
      '" data-id="' +
      esc(pid) +
      '">' +
      '<header class="card-head">' +
      '<div class="card-title">' +
      '<span class="status-dot" aria-hidden="true"></span>' +
      "<div>" +
      '<p class="card-name">' +
      esc(name) +
      "</p>" +
      '<p class="card-meta">' +
      esc(user) +
      " · " +
      esc(protocolLabel(protocol)) +
      " · " +
      esc(deskShort) +
      "</p>" +
      "</div></div>" +
      '<span class="badge badge-' +
      esc(st) +
      '">' +
      esc(statusLabel(st)) +
      "</span>" +
      "</header>" +
      '<div class="card-summary">' +
      /* HARD_GATE#869: 3×2 left-aligned columns
         云桌面↔模式 | 用户协议↔间隔 | 客户端↔云桌面状态 */
      "<div>云桌面<strong title=\"" +
      esc(deskIdText) +
      '">' +
      esc(deskIdText) +
      "</strong></div>" +
      "<div>用户协议<strong>" +
      esc(protocolLabel(protocol)) +
      "</strong></div>" +
      "<div>客户端<strong>" +
      esc(clientLabel(client)) +
      "</strong></div>" +
      "<div>模式<strong>" +
      esc(modeLabel(mode)) +
      "</strong></div>" +
      "<div>间隔<strong>" +
      esc(String(d.intervalMin || 5)) +
      " 分钟</strong></div>" +
      "<div>云桌面状态<strong data-desk-status title=\"" +
      esc(deskStatus) +
      "\">" +
      esc(deskStatus) +
      "</strong></div>" +
      "</div>" +
      '<div class="card-surface">' +
      (errLine
        ? '<p class="card-error">' + esc(errLine) + "</p>"
        : "") +
      '<div class="card-actions">' +
      (running
        ? '<button type="button" class="btn btn-stop" data-act="stop" ' +
          (busy ? "disabled" : "") +
          ">停止保活</button>"
        : '<button type="button" class="btn btn-primary" data-act="start" ' +
          (busy ? "disabled" : "") +
          ">开始保活</button>") +
      '<button type="button" class="btn btn-ghost" data-act="config" ' +
      (busy ? "disabled" : "") +
      (open ? ' aria-expanded="true"' : ' aria-expanded="false"') +
      ">配置</button>" +
      '<button type="button" class="btn btn-ghost" data-act="clear-thread" ' +
      (busy ? "disabled" : "") +
      ' title="停止并清除该账号当前保活线程（本地编排，不调用上游桌面登出）">清除线程</button>' +
      '<button type="button" class="btn btn-ghost" data-act="clear-logs" ' +
      (busy ? "disabled" : "") +
      ">清空日志</button>" +
      "</div>" +
      /* HARD_GATE#736: logs-only dual surface; desktop box removed */
      /* HARD_GATE#810: dblclick whole log panel (head+box) → full modal */
      '<div class="card-surface-dual card-surface-log-only">' +
      '<div class="log-panel surface-log card-log-expanded" title="双击日志查看完整记录">' +
      '<div class="log-panel-head"><span>日志（常显最近 6 条；双击看全部）</span></div>' +
      '<div class="log-box log-viewport" data-log="' +
      esc(pid) +
      '" title="双击查看完整日志">' +
      profileLogsHtml(pid) +
      "</div></div>" +
      "</div>" +
      "</div></article>"
    );
  }

  function configFormHtml(p) {
    const pid = p.id;
    const d = ensureDraft(pid, p);
    const busy = !!state.busy[pid];
    const job = jobOf(p);
    const user = p.usernameMasked || "未设置账号";
    const client = p.clientProfile || d.clientProfile || "linux";
    const protocol =
      resolveUserProtocol(d.protocol, p && p.protocol, p && p.lastOfficialProtocol, p && p.protocolHint, job && job.protocol);
    const mode = d.mode || (p && p.mode) || (job && job.mode) || "live";
    const errLine = String(state.cardMsg[pid] || "").trim();
    const usid = d.userServiceId || (p && p.userServiceId) || "";
    const selectedDesk = usid || d.desktopLabel || "";
    const spu =
      d.spuCode ||
      (p && (p.spuCode || p.spu_code)) ||
      "";
    return (
      (errLine
        ? '<p class="card-error" id="config-modal-error" role="alert">' + esc(errLine) + "</p>"
        : "") +
      '<div class="card-fields config-modal-fields">' +
      '<label class="field span-2"><span>显示名</span>' +
      '<input type="text" data-pid="' +
      esc(pid) +
      '" data-key="displayName" value="' +
      esc(d.displayName || "") +
      '" /></label>' +
      '<label class="field"><span>账号</span>' +
      '<input type="text" data-pid="' +
      esc(pid) +
      '" data-key="username" placeholder="' +
      esc(user) +
      '" value="' +
      esc(d.username || "") +
      '" /></label>' +
      '<label class="field"><span>密码</span>' +
      '<input type="text" autocomplete="new-password" data-pid="' +
      esc(pid) +
      '" data-key="password" placeholder="' +
      (p.hasPassword ? "已保存，不改请留空" : "请输入密码") +
      '" value="" /></label>' +
      /* HARD_GATE#784 LOGIN_AFTER_PWD: 登录紧贴密码后、云桌面前 */
      '<div class="field span-2 login-after-pwd">' +
      '<button type="button" class="btn btn-secondary btn-login-inline" data-act="login" data-id="' +
      esc(pid) +
      '"' +
      (busy ? " disabled" : "") +
      ' title="登录并加载官方云桌面列表（不启动保活）">登录</button>' +
      '<span class="field-hint">登录后加载云桌面列表，不会启动保活</span>' +
      "</div>" +
      '<div class="field span-2 desktop-field config-desktop-field">' +
      "<span>云桌面</span>" +
      '<div class="desk-seg-wrap">' +
      /* HARD_GATE#747: CTA button inside segmented html (empty + after list) */
      desktopSegmentedHtml(pid, selectedDesk, false) +
      "</div>" +
      "</div>" +
      /* HARD_GATE#729: form-pair only 保活间隔 || 单次流量持续; duration field removed */
      '<div class="form-pair span-2" role="group" aria-label="保活间隔 / 单次流量持续">' +
      '<label class="field"><span>保活间隔（分钟）</span>' +
      '<input type="number" min="1" max="1440" data-pid="' +
      esc(pid) +
      '" data-key="intervalMin" value="' +
      esc(String(d.intervalMin || 5)) +
      '" /></label>' +
      '<label class="field"><span>单次流量持续（秒）</span>' +
      '<input type="number" min="5" max="3600" data-pid="' +
      esc(pid) +
      '" data-key="trafficSec" value="' +
      esc(String(d.trafficSec || 60)) +
      '" /></label>' +
      "</div>" +
      '<div class="form-bottom-3 span-2" role="group" aria-label="客户端 / 模式 / 用户协议">' +
      '<div class="field"><span>客户端类型</span>' +
      '<div class="seg" role="group" aria-label="客户端类型">' +
      '<button type="button" class="seg-btn' +
      (client === "linux" ? " active" : "") +
      '" data-pid="' +
      esc(pid) +
      '" data-key="clientProfile" data-val="linux">Linux</button>' +
      '<button type="button" class="seg-btn' +
      (client === "windows" ? " active" : "") +
      '" data-pid="' +
      esc(pid) +
      '" data-key="clientProfile" data-val="windows">Windows</button>' +
      '<button type="button" class="seg-btn' +
      (client === "mac" ? " active" : "") +
      '" data-pid="' +
      esc(pid) +
      '" data-key="clientProfile" data-val="mac">Mac</button>' +
      "</div></div>" +
      '<div class="field"><span>模式</span>' +
      '<div class="seg" role="group" aria-label="保活模式">' +
      '<button type="button" class="seg-btn' +
      (!modeIsOnce(d.mode) ? " active" : "") +
      '" data-pid="' +
      esc(pid) +
      '" data-key="mode" data-val="live">永久</button>' +
      '<button type="button" class="seg-btn' +
      (modeIsOnce(d.mode) ? " active" : "") +
      '" data-pid="' +
      esc(pid) +
      '" data-key="mode" data-val="once">单轮</button>' +
      "</div></div>" +
      '<div class="field"><span>用户协议</span>' +
      '<div class="seg" role="group" aria-label="用户协议">' +
      '<button type="button" class="seg-btn' +
      (String(protocol).toUpperCase() === "ZTE" ? " active" : "") +
      '" data-pid="' +
      esc(pid) +
      '" data-key="protocol" data-val="ZTE">ZTE</button>' +
      '<button type="button" class="seg-btn' +
      (String(protocol).toUpperCase() === "SCG" || String(protocol).toUpperCase() === "SANGFOR" ? " active" : "") +
      '" data-pid="' +
      esc(pid) +
      '" data-key="protocol" data-val="SCG">SCG</button>' +
      "</div></div>" +
      "</div>" +
      "</div>" +
      '<div class="card-config-actions config-modal-actions">' +
      '<button type="button" class="btn btn-primary" data-act="save-start" data-pid="' +
      esc(pid) +
      '" ' +
      (busy || !usid ? "disabled" : "") +
      (usid ? "" : ' title="请先选择云桌面"') +
      ">保存并保活</button>" +
      '<button type="button" class="btn btn-ghost" data-act="save" data-pid="' +
      esc(pid) +
      '" ' +
      (busy ? "disabled" : "") +
      ">保存配置</button>" +
      '<button type="button" class="btn btn-danger" data-act="delete" data-pid="' +
      esc(pid) +
      '" ' +
      (busy ? "disabled" : "") +
      ">删除账号</button>" +
      '<button type="button" class="btn btn-ghost" data-act="config-close">取消</button>' +
      "</div>"
    );
  }

  function openConfigModal(pid) {
    const modal = $("#config-modal");
    const body = $("#config-modal-body");
    const title = $("#config-modal-title");
    if (!modal || !body) return;
    const p = state.profiles.find(function (x) {
      return x.id === pid;
    });
    if (!p) return;
    state.configPid = pid;
    ensureDraft(pid, p);
    const name = p.displayName || pid;
    if (title) title.textContent = "配置 · " + name;
    body.innerHTML = configFormHtml(p);
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
    renderCards();
    setTimeout(function () {
      const first = body.querySelector('input:not([type="radio"]), select, input[type="radio"]');
      if (first) {
        try {
          first.focus();
        } catch (_) {}
      }
    }, 0);
  }

  function refreshConfigModal() {
    const pid = state.configPid;
    if (!pid) return;
    const modal = $("#config-modal");
    const body = $("#config-modal-body");
    if (!modal || !body || modal.classList.contains("hidden")) return;
    const p = state.profiles.find(function (x) {
      return x.id === pid;
    });
    if (!p) return;
    const active = document.activeElement;
    const keepKey =
      active && active.getAttribute ? active.getAttribute("data-key") : null;
    const keepVal = active && "value" in active ? active.value : null;
    body.innerHTML = configFormHtml(p);
    if (keepKey) {
      const el = body.querySelector('[data-key="' + keepKey + '"]');
      if (el) {
        if (keepVal != null && el.type !== "password") {
          try {
            el.value = keepVal;
          } catch (_) {}
        }
        try {
          el.focus();
        } catch (_) {}
      }
    }
  }

  function closeConfigModal() {
    const modal = $("#config-modal");
    state.configPid = null;
    if (modal) {
      modal.classList.add("hidden");
      modal.setAttribute("aria-hidden", "true");
    }
    const body = $("#config-modal-body");
    if (body) body.innerHTML = "";
    renderCards();
  }

  function renderCards() {
    const root = $("#timeline");
    const empty = $("#empty-state");
    if (!root) return;
    renderStats();
    /* HARD_GATE#851: belt-and-suspenders draft hide */
    const visible = (state.profiles || []).filter(function (p) {
      return p && !p.draft && p.draft !== true && p.draft !== 1 && p.draft !== "1";
    });
    if (!visible.length) {
      root.innerHTML = "";
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;
    root.innerHTML = visible.map(cardHtml).join("");
    // HARD_GATE#838: after rebuild, pin each card log to latest 6
    state.profiles.forEach(function (p) {
      if (!p || !p.id) return;
      const panel =
        $('.log-viewport[data-log="' + p.id + '"]') ||
        $('.log-box[data-log="' + p.id + '"]');
      if (panel) panel.scrollTop = panel.scrollHeight;
      if (!state.logs[p.id] || !state.logs[p.id].length) {
        loadLogs(p.id).catch(function () {});
      } else {
        applyLogsToDom(p.id, true);
      }
    });
    refreshConfigModal();
  }

  
  function setConfigError(pid, msg) {
    const text = String(msg || "").trim();
    if (text) state.cardMsg[pid] = text;
    else delete state.cardMsg[pid];
    const el = document.getElementById("config-modal-error");
    if (!el) return;
    if (!text) {
      el.remove();
      return;
    }
    el.classList.remove("hidden");
    el.textContent = text;
    el.setAttribute("role", "alert");
  }

function setComposerMsg(text, kind) {
    const el = $("#composer-msg");
    if (!el) return;
    el.textContent = text || "";
    el.classList.remove("error", "ok");
    if (kind) el.classList.add(kind);
  }

  function readComposer() {
    return {
      displayName: ($("#c-displayName") && $("#c-displayName").value.trim()) || "",
      username: ($("#c-username") && $("#c-username").value.trim()) || "",
      password: ($("#c-password") && $("#c-password").value) || "",
      protocol: resolveUserProtocol(state.composer.protocol, state.composer.lastOfficialProtocol),
      clientProfile: state.composer.clientProfile || "linux",
      mode: modeApi(state.composer.mode || "live"),
      intervalMin: Number(($("#c-intervalMin") && $("#c-intervalMin").value) || 5),
      trafficSec: Number(($("#c-trafficSec") && $("#c-trafficSec").value) || 60),
      /* #848: once -> trafficSec; live forever -> 0 */
      durationSec: durationForMode(
        state.composer.mode || "live",
        Number(($("#c-trafficSec") && $("#c-trafficSec").value) || 60)
      ),
      userServiceId:
        state.composer.userServiceId ||
        ($("#c-userServiceId") && $("#c-userServiceId").value) ||
        "",
      desktopLabel:
        state.composer.desktopLabel ||
        ($("#c-desktopLabel") && $("#c-desktopLabel").value) ||
        "",
    };
  }

  function clearComposer() {
    ["c-displayName", "c-username", "c-password"].forEach(function (id) {
      const el = $("#" + id);
      if (el) el.value = "";
    });
    if ($("#c-intervalMin")) $("#c-intervalMin").value = "5";
    if ($("#c-trafficSec")) $("#c-trafficSec").value = "60";
    if ($("#c-userServiceId")) $("#c-userServiceId").value = "";
    if ($("#c-desktopLabel")) $("#c-desktopLabel").value = "";
    if ($("#c-desktop")) {
      /* HARD_GATE#842: empty table state */
      $("#c-desktop").innerHTML = composerDeskEmptyHtml();
      $("#c-desktop").classList.add("is-locked");
      $("#c-desktop").setAttribute("aria-disabled", "true");
    }
    state.composer = {
      protocol: "ZTE",
      clientProfile: "linux",
      mode: "live",
      userServiceId: "",
      desktopLabel: "",
      profileId: "",
    };
    $$(".composer .seg-btn").forEach(function (btn) {
      const p = btn.getAttribute("data-protocol");
      const c = btn.getAttribute("data-client");
      const m = btn.getAttribute("data-mode");
      if (p) btn.classList.toggle("active", p === "ZTE");
      if (c) btn.classList.toggle("active", c === "linux");
      if (m) btn.classList.toggle("active", m === "live");
    });
    setComposerMsg("");
    setComposerDesktopLock(false);
    setComposerOfficial("未登录");
  }


  async function loadJobs() {
    try {
      const data = await api("/api/jobs");
      const jobs = (data && data.jobs) || data || [];
      const list = Array.isArray(jobs) ? jobs : [];
      state.jobsById = Object.create(null);
      state.jobsByProfile = Object.create(null);
      for (let i = 0; i < list.length; i++) {
        const j = list[i] || {};
        const jid = j.id || j.jobId || j.job_id;
        if (jid) state.jobsById[jid] = j;
        const pid = j.profileId || j.profile_id || j.accountId || j.account_id;
        if (pid) state.jobsByProfile[pid] = j;
      }
    } catch (_) {
      /* jobs optional; card falls back to profile fields */
    }
  }

  async function loadProfiles(forceExpandNone) {
    try {
      await loadJobs();
      const data = await api("/api/profiles");
      /* HARD_GATE#851: never show draft profiles on timeline */
      state.profiles = ((data && data.profiles) || []).filter(function (p) {
        return p && !p.draft && p.draft !== true && p.draft !== 1 && p.draft !== "1";
      });
      for (let i = 0; i < state.profiles.length; i++) {
        ensureDraft(state.profiles[i].id, state.profiles[i]);
      }
      if (forceExpandNone) {
        /* config modal pid kept independently */
      }
      renderCards();
    } catch (e) {
      toast(humanError(e, "列表加载失败"), true);
      pushGlobal("列表加载失败: " + humanError(e), "error");
    }
  }

  async function loadLogs(pid, toastOk) {
    // HARD_GATE#855/#854 LOG_POLL_6S: pull + fingerprint paint; empty API must not erase fresher local SSE buffer
    try {
      const data = await api(
        "/api/profiles/" + encodeURIComponent(pid) + "/logs"
      );
      const lines = (data && data.lines) || [];
      const prev = state.logs[pid] || [];
      const prevFp = logsFingerprint(prev);
      // If backend returns empty but local still has lines and this is not a forced refresh
      // after clear, keep local until next non-empty pull (SSE may be ahead of poll race).
      // Forced toastOk / explicit clear path already zeroed state.logs.
      if (lines.length === 0 && prev.length > 0 && !toastOk) {
        // keep prev; still ensure DOM painted
        applyLogsToDom(pid, false);
        return;
      }
      state.logs[pid] = lines;
    try { patchCardDeskStatus(pid); } catch (_e) {}
      const nextFp = logsFingerprint(lines);
      if (toastOk || prevFp !== nextFp) applyLogsToDom(pid, !!toastOk);
      if (toastOk) toast("日志已刷新");
    } catch (e) {
      pushGlobal("[" + pid + "] 日志读取失败: " + humanError(e), "error");
    }
  }

  function logsFingerprint(lines) {
    const arr = lines || [];
    if (!arr.length) return "0";
    const last = arr[arr.length - 1] || {};
    return String(arr.length) + "|" + String(last.at || "") + "|" + String(last.line || "");
  }

  function applyLogsToDom(pid, force) {
    // HARD_GATE#841: paint last-6 (or full modal); never invent blank rows
    if (!pid) return;
    /* HARD_GATE#871: keep 云桌面状态 fresh from log lines */
    try {
      const st = extractDesktopStatusFromLogs(pid);
      if (st) {
        const chip = document.querySelector(
          '.card[data-id="' + pid + '"] [data-desk-status]'
        );
        if (chip && chip.textContent !== st) {
          chip.textContent = st;
          chip.setAttribute("title", st);
        }
      }
    } catch (e) {}
    const fp = logsFingerprint(state.logs[pid]);
    const panel =
      $('.log-viewport[data-log="' + pid + '"]') ||
      $('.log-box[data-log="' + pid + '"]') ||
      $('[data-log="' + pid + '"]');
    if (panel) {
      if (force || panel.getAttribute('data-log-fp') !== fp) {
        panel.innerHTML = profileLogsHtml(pid);
        panel.setAttribute('data-log-fp', fp);
        // pin latest (defensive even with only 6 rows)
        const pin = function () {
          panel.scrollTop = panel.scrollHeight;
        };
        pin();
        requestAnimationFrame(function () {
          pin();
          requestAnimationFrame(pin);
        });
      }
    }
    const body = $('#log-full-body');
    const modal = $('#log-modal') || $('#log-full-modal');
    const modalPid = modal
      ? String(modal.getAttribute('data-pid') || state.logModalPid || '')
      : '';
    if (
      body &&
      modal &&
      !modal.classList.contains('hidden') &&
      modal.getAttribute('aria-hidden') !== 'true' &&
      modalPid === String(pid)
    ) {
      const mfp = 'full:' + fp;
      if (force || body.getAttribute('data-log-fp') !== mfp) {
        body.innerHTML = profileLogsHtml(pid, { full: true });
        body.setAttribute('data-log-fp', mfp);
        body.scrollTop = body.scrollHeight;
      }
    }
  }

  
  function clearSavedToken(opts) {
    opts = opts || {};
    setToken("");
    state.sseNeedTokenLogged = false;
    if (state.es) {
      try {
        state.es.close();
      } catch (_) {}
      state.es = null;
    }
    loadSys().then(function () {
      if (state.tokenRequired) {
        pushGlobal("已清除本机令牌 · 需重新填写后才能连接事件流", "error");
      } else {
        connectSSE();
      }
    });
    if (opts.toast !== false) toast("已清除本机令牌");
  }

  async function loadSys() {
    try {
      const info = await api("/api/system/info");
      state.tokenRequired = !!(info && info.tokenRequired);
      const el = $("#sys-info");
      if (el) {
        el.textContent =
          "服务 " +
          (info.service || "cmcc-cloud-alive") +
          " · v" +
          (info.version || "?") +
          (state.tokenRequired ? " · 需令牌" : "");
      }
    } catch (e) {
      // If token gate is on and info is still protected, 401 implies need-token.
      if (e && (e.status === 401 || e.code === "AUTH_FAILED")) {
        state.tokenRequired = true;
      }
      const el = $("#sys-info");
      if (el) {
        el.textContent = state.tokenRequired ? "服务 · 需令牌" : "";
      }
    }
  }

  function confirmModal(title, body, okText) {
    return new Promise(function (resolve) {
      const modal = $("#modal");
      const t = $("#modal-title");
      const b = $("#modal-body");
      const ok = $("#modal-ok");
      const cancel = $("#modal-cancel");
      if (!modal || !ok || !cancel) {
        resolve(window.confirm(body || title));
        return;
      }
      t.textContent = title || "确认";
      b.textContent = body || "";
      ok.textContent = okText || "确定删除";
      // HARD_GATE#843: tertiary confirm above config modal
      modal.style.zIndex = "1300";
      modal.classList.remove("hidden");
      modal.setAttribute("aria-hidden", "false");
      setTimeout(function () {
        try {
          cancel.focus();
        } catch (_) {}
      }, 0);
      const done = function (v) {
        modal.classList.add("hidden");
        modal.setAttribute("aria-hidden", "true");
        ok.onclick = null;
        cancel.onclick = null;
        resolve(v);
      };
      ok.onclick = function () {
        done(true);
      };
      cancel.onclick = function () {
        done(false);
      };
    });
  }

  async function onSave(pid) {
    const d = ensureDraft(pid);
    state.busy[pid] = true;
    renderCards();
    try {
      if (d.username || d.password) {
        await api("/api/profiles/" + encodeURIComponent(pid) + "/login", {
          method: "POST",
          body: {
            username: d.username || undefined,
            password: d.password || undefined,
          },
        });
      }
      if (d.userServiceId || d.desktopLabel) {
        await api(
          "/api/profiles/" + encodeURIComponent(pid) + "/select-desktop",
          {
            method: "POST",
            body: {
              userServiceId: d.userServiceId || undefined,
              desktopLabel: d.desktopLabel || undefined,
              protocol: resolveUserProtocol(d.protocol, d.lastOfficialProtocol),
              protocolHint: (d.protocol || "").toUpperCase() || undefined,
              spuCode: d.spuCode || undefined,
            },
          }
        );
      }
      d.password = "";
      state.cardMsg[pid] = "";
      toast("配置已保存");
      pushGlobal("[" + pid + "] 配置已保存");
      closeConfigModal();
      await loadProfiles();
    } catch (e) {
      const msg = humanError(e, "保存失败");
      state.cardMsg[pid] = msg;
      toast(msg, true);
      pushGlobal("[" + pid + "] 保存失败: " + msg, "error");
      renderCards();
    } finally {
      state.busy[pid] = false;
      renderCards();
    }
  }

  async function onStart(pid) {
    const d = ensureDraft(pid);
    const p = state.profiles.find(function (x) {
      return x.id === pid;
    });
    if (!(d.userServiceId || d.desktopLabel || (p && (p.userServiceId || p.desktopLabel)))) {
      const msg = "请先选择云桌面";
      state.cardMsg[pid] = msg;
      toast(msg, true);
      renderCards();
      return;
    }
    state.busy[pid] = true;
    state.cardMsg[pid] = "";
    renderCards();
    try {
      if (d.username || d.password) {
        await api("/api/profiles/" + encodeURIComponent(pid) + "/login", {
          method: "POST",
          body: {
            username: d.username || undefined,
            password: d.password || undefined,
          },
        });
      }
      // 登录后尽量刷新桌面列表 / 协议提示
      try {
        const deskData = await api(
          "/api/profiles/" + encodeURIComponent(pid) + "/desktops"
        );
        const list =
          (deskData && (deskData.desktops || deskData.items || deskData.list)) ||
          (Array.isArray(deskData) ? deskData : []) ||
          [];
        state.desktops[pid] = list;
        if (d.userServiceId) {
          for (let i = 0; i < list.length; i++) {
            const x = list[i] || {};
            const xid = x.userServiceId || x.id || "";
            if (xid === d.userServiceId) {
              applyOfficialFromDesktop(d, x);
              break;
            }
          }
        } else if (list.length === 1) {
          const only = list[0] || {};
          d.userServiceId = only.userServiceId || only.id || "";
          d.desktopLabel =
            only.desktopLabel || only.name || only.label || d.userServiceId;
          applyOfficialFromDesktop(d, only);
        }
      } catch (_) {
        /* 桌面刷新失败不阻断启动；AUTH 等由后续 select/jobs 暴露 */
      }
      if (d.userServiceId || d.desktopLabel) {
        await api(
          "/api/profiles/" + encodeURIComponent(pid) + "/select-desktop",
          {
            method: "POST",
            body: {
              userServiceId: d.userServiceId || undefined,
              desktopLabel: d.desktopLabel || undefined,
              protocol: resolveUserProtocol(d.protocol, d.lastOfficialProtocol),
              protocolHint: (d.protocol || "").toUpperCase() || undefined,
              spuCode: d.spuCode || undefined,
            },
          }
        );
      }
      const mode = modeApi(d.mode);
      const trafficSec = Number(d.trafficSec || 60);
      const durationSec = durationForMode(mode, trafficSec);
      const data = await api(
        "/api/profiles/" + encodeURIComponent(pid) + "/jobs",
        {
          method: "POST",
          body: {
            protocol: resolveUserProtocol(d.protocol, d.lastOfficialProtocol),
            mode: mode,
            clientProfile: d.clientProfile || "linux",
            intervalSec: Math.max(60, Number(d.intervalMin || 5) * 60),
            trafficSec: trafficSec,
            /* #848: once uses trafficSec; live forever uses 0 */
            durationSec: durationSec,
          },
        }
      );
      toast(modeIsOnce(mode) ? "已启动单轮保活" : "已开始保活");
      pushGlobal(
        "[" +
          ((p && p.displayName) || pid) +
          "] 开始保活 · " +
          protocolLabel(d.protocol) +
          " · " +
          modeLabel(mode)
      );
      d.password = "";
      /* no card expand */
      closeConfigModal();
      await loadProfiles();
      await loadLogs(pid);
      return data;
    } catch (e) {
      const msg = humanError(e, "启动失败");
      state.cardMsg[pid] = msg;
      toast(msg, true);
      pushGlobal("[" + pid + "] 启动失败: " + msg, "error");
      renderCards();
    } finally {
      state.busy[pid] = false;
      renderCards();
    }
  }

  async function onStop(pid) {
    state.busy[pid] = true;
    renderCards();
    try {
      await api(
        "/api/profiles/" + encodeURIComponent(pid) + "/jobs/current",
        { method: "DELETE" }
      );
      toast("已停止保活");
      pushGlobal("[" + pid + "] 已停止保活");
      state.cardMsg[pid] = "";
      await loadProfiles();
      await loadLogs(pid);
    } catch (e) {
      const msg = humanError(e, "停止失败");
      state.cardMsg[pid] = msg;
      toast(msg, true);
      pushGlobal("[" + pid + "] 停止失败: " + msg, "error");
      renderCards();
    } finally {
      state.busy[pid] = false;
      renderCards();
    }
  }

  async function onClearThread(pid) {
    // Card button: stop/clear local keepalive worker for this profile.
    // Replaces upstream desktop-logout; user evidence shows orphan local
    // threads (not SOHO logout) were what blocked restart after fail.
    if (!pid) return;
    const p = state.profiles.find(function (x) {
      return x.id === pid;
    });
    const name = (p && p.displayName) || pid;
    const ok = await confirmModal(
      "清除线程",
      "确定清除「" +
        name +
        "」的当前保活线程？仅停止本机编排任务，不会调用上游桌面登出，账号登录态保留。",
      "确定清除"
    );
    if (!ok) return;
    state.busy[pid] = true;
    renderCards();
    try {
      await api(
        "/api/profiles/" + encodeURIComponent(pid) + "/jobs/current",
        { method: "DELETE" }
      );
      toast("已清除保活线程");
      pushGlobal("[" + pid + "] 已清除保活线程");
      state.cardMsg[pid] = "";
      await loadProfiles();
      await loadLogs(pid).catch(function () {});
    } catch (e) {
      // No running job is still a successful "clear" from user POV.
      const code = e && (e.code || e.status);
      const msgRaw = String((e && (e.message || e.detail)) || "");
      if (
        code === "NOT_FOUND" ||
        code === 404 ||
        /not.?found|no.+job|无.+任务|没有.+任务/i.test(msgRaw)
      ) {
        toast("当前无运行中的保活线程");
        pushGlobal("[" + pid + "] 清除线程：当前无运行任务");
        state.cardMsg[pid] = "";
        await loadProfiles().catch(function () {});
      } else {
        const msg = humanError(e, "清除线程失败");
        state.cardMsg[pid] = msg;
        toast(msg, true);
        pushGlobal("[" + pid + "] 清除线程失败: " + msg, "error");
        renderCards();
      }
    } finally {
      state.busy[pid] = false;
      renderCards();
    }
  }

  // Backward-compat alias (old handlers / residual callers).
  async function onDesktopLogout(pid) {
    return onClearThread(pid);
  }

  async function onDelete(pid) {
    const p = state.profiles.find(function (x) {
      return x.id === pid;
    });
    const name = (p && p.displayName) || pid;
    const ok = await confirmModal(
      "删除账号",
      "确定删除该账号？删除后无法恢复",
      "确定删除"
    );
    if (!ok) return;
    state.busy[pid] = true;
    renderCards();
    try {
      await api("/api/profiles/" + encodeURIComponent(pid), {
        method: "DELETE",
      });
      delete state.drafts[pid];
      delete state.logs[pid];
      delete state.cardMsg[pid];
      delete state.desktops[pid];
      if (state.configPid === pid) closeConfigModal();
      toast("已删除 " + name);
      pushGlobal("已删除账号 " + name);
      await loadProfiles();
    } catch (e) {
      const msg = humanError(e, "删除失败");
      state.cardMsg[pid] = msg;
      toast(msg, true);
      pushGlobal("[" + pid + "] 删除失败: " + msg, "error");
      renderCards();
    } finally {
      state.busy[pid] = false;
      renderCards();
    }
  }


  function applyOfficialFromDesktop(target, desk) {
    if (!target || !desk) return target;
    const hint = desk.protocolHint || desk.protocol_hint || desk.protocol || "";
    const spu = desk.spuCode || desk.spu_code || "";
    if (hint) {
      const hp = String(hint).toUpperCase();
      if (hp === "ZTE" || hp === "SCG" || hp === "SANGFOR") {
        target.protocol = hp === "SANGFOR" ? "SCG" : hp;
        target.lastOfficialProtocol = target.protocol;
      }
    }
    if (spu) target.spuCode = spu;
    if (!target.desktopLabel) {
      target.desktopLabel =
        desk.desktopLabel || desk.name || desk.label || target.userServiceId || "";
    }
    return target;
  }

  async function onDesktops(pid) {
    state.busy[pid] = true;
    renderCards();
    try {
      const data = await api(
        "/api/profiles/" + encodeURIComponent(pid) + "/desktops"
      );
      const list =
        (data && (data.desktops || data.items || data.list)) ||
        (Array.isArray(data) ? data : []) ||
        [];
      state.desktops[pid] = list;
      const d = ensureDraft(pid);
      if (d.userServiceId) {
        for (let i = 0; i < list.length; i++) {
          const x = list[i] || {};
          const xid = x.userServiceId || x.id || "";
          if (xid === d.userServiceId) {
            applyOfficialFromDesktop(d, x);
            break;
          }
        }
      } else if (list.length === 1) {
        const only = list[0] || {};
        d.userServiceId = only.userServiceId || only.id || "";
        d.desktopLabel =
          only.desktopLabel || only.name || only.label || d.userServiceId;
        applyOfficialFromDesktop(d, only);
      }
      // A12: success/info stays in toast+global log; cardMsg is error-only (red)
      state.cardMsg[pid] = "";
      const info = list.length
        ? "已加载 " + list.length + " 个云桌面"
        : "未返回云桌面，请确认已登录";
      toast(info);
      pushGlobal("[" + pid + "] " + info);
      renderCards();
    } catch (e) {
      const msg = humanError(e, "刷新桌面失败");
      state.cardMsg[pid] = msg;
      toast(msg, true);
      pushGlobal("[" + pid + "] 刷新桌面失败: " + msg, "error");
      renderCards();
    } finally {
      state.busy[pid] = false;
      renderCards();
    }
  }

  
  async function onConfigLogin(pid) {
    // HARD_GATE#784: modal 登录 = save draft creds then refresh official desktops (no keepalive)
    if (!pid) return;
    state.busy[pid] = true;
    patchCardStatus(pid);
    // if config form open, keep form; avoid full card wipe of inputs
    try {
      // pull latest draft from open form fields
      const form = $("#config-form") || document.querySelector('[data-id="' + pid + '"]');
      if (form) {
        const inputs = form.querySelectorAll("[data-key]");
        for (let i = 0; i < inputs.length; i++) {
          applyDraftFromEl(inputs[i]);
        }
      }
      const d = ensureDraft(pid);
      const body = {
        username: d.username || undefined,
        password: d.password || undefined,
        clientProfile: d.clientProfile || undefined,
        protocol: d.protocol || undefined,
        mode: d.mode || undefined,
        displayName: d.displayName || undefined,
      };
      // best-effort save so /desktops uses fresh creds
      try {
        await api("/api/profiles/" + encodeURIComponent(pid), {
          method: "PUT",
          body: JSON.stringify(body),
        });
      } catch (_) {
        /* create path may differ; still try desktops */
      }
      await onDesktops(pid);
    } catch (e) {
      const msg = humanError(e, "登录失败");
      state.cardMsg[pid] = msg;
      toast(msg, true);
      pushGlobal("[" + pid + "] " + msg, "error");
    } finally {
      state.busy[pid] = false;
      // refresh modal desktop list without nuking logs if possible
      if (state.configPid === pid) {
        try {
          refreshConfigModal();
        } catch (_) {
          renderCards();
        }
      } else {
        patchCardStatus(pid);
      }
    }
  }

  function setComposerOfficial(text) {
    /* HARD_GATE#707-3/4: drop 协议提示 / 官方协议 independent UI; keep data-only no-op */
    const el = $("#c-official-protocol");
    if (el) {
      el.textContent = text || "未登录";
      const wrap = el.closest(".official-protocol-field") || el.parentElement;
      if (wrap && wrap.style) wrap.style.display = "none";
      el.style.display = "none";
    }
  }

  function composerDeskEmptyHtml() {
    /* HARD_GATE#842: table empty state matching reference layout */
    return (
      '<div class="desk-table-wrap"><table class="desk-table" aria-label="云桌面">' +
      "<thead><tr>" +
      '<th class="col-idx">序号</th>' +
      '<th class="col-name">名称</th>' +
      '<th class="col-id">ID</th>' +
      '<th class="col-proto">协议</th>' +
      '<th class="col-act">操作</th>' +
      "</tr></thead>" +
      '<tbody id="c-desktop-tbody">' +
      '<tr class="desk-empty-row"><td colspan="5">' +
      '<div class="desk-empty"><div class="desk-empty-title">暂无数据</div>' +
      '<div class="desk-empty-hint">登录后自动获取云桌面列表</div></div>' +
      "</td></tr></tbody></table></div>"
    );
  }

  function desktopSpecText(d) {
    d = d || {};
    const spu = d.spuCode || d.spu_code || "";
    if (spu) return String(spu);
    const cpu = d.cpu || d.cpuNum || d.cpuCore || "";
    const mem = d.memory || d.mem || d.ram || "";
    if (cpu || mem) return String(cpu || "—") + "C / " + String(mem || "—") + "G";
    const spec = d.spec || d.productName || d.packageName || d.resourceName || "";
    return spec ? String(spec) : "—";
  }

  function desktopStatusText(d) {
    d = d || {};
    const st =
      d.vmStatusShow ||
      d.statusName ||
      d.statusText ||
      d.desktopStatusName ||
      d.pcStatusName ||
      d.runStatusName ||
      d.status ||
      d.desktopStatus ||
      d.pcStatus ||
      d.runStatus ||
      "";
    if (st === 0 || st === "0") return "未知";
    return st ? String(st) : "—";
  }

  function desktopProtocolText(d) {
    /* HARD_GATE#846: protocol col = spuCode (python CLI: | spuCode：xxx) */
    d = d || {};
    const spu = d.spuCode || d.spu_code || d.spu || "";
    if (spu) return String(spu);
    const hint = d.protocolHint || d.protocol || d.clientProtocol || "";
    return hint ? String(hint) : "—";
  }


  function setComposerDeskRefreshEnabled(unlocked) {
    const btn = $("#c-desk-refresh");
    if (!btn) return;
    const pid = state.composer && state.composer.profileId;
    const busy = !!(pid && state.busy[pid]);
    btn.disabled = !unlocked || busy;
    btn.textContent = busy ? "刷新中…" : "刷新列表";
    btn.classList.toggle("is-loading", busy);
  }

  function setComposerDesktopLock(unlocked) {
    const box = $("#c-desktop");
    if (box) {
      box.classList.toggle("is-locked", !unlocked);
      box.setAttribute("aria-disabled", unlocked ? "false" : "true");
      const radios = box.querySelectorAll('input[type="radio"]');
      for (let i = 0; i < radios.length; i++) {
        radios[i].disabled = !unlocked;
      }
      const acts = box.querySelectorAll(".desk-select-btn");
      for (let i = 0; i < acts.length; i++) {
        acts[i].disabled = !unlocked;
      }
    }
    setComposerDeskRefreshEnabled(unlocked);
    const sub = $("#c-desktop-sub");
    if (sub) {
      sub.textContent = unlocked ? "已加载，可选择云桌面" : "登录后自动获取";
    }
    const note = $("#c-desktop-note");
    if (note) {
      note.textContent = unlocked
        ? "官方 list_clouds 已加载；在操作列点击「选择」"
        : "登录成功后展示官方 list_clouds（名称 / id | spuCode：xxx）";
    }
  }

  function desktopOptionLabel(d) {
    /* HARD_GATE#781: 名称 / id | spuCode：xxx */
    return desktopRowText(d || {});
  }

  function fillComposerDesktopSelect(list, selectedId) {
    /* HARD_GATE#842: composer desktop = full-width table */
    const box = $("#c-desktop");
    if (!box) return;
    list = Array.isArray(list) ? list : [];
    if (!list.length) {
      box.innerHTML = composerDeskEmptyHtml();
      state.composer.userServiceId = "";
      state.composer.desktopLabel = "";
      if ($("#c-userServiceId")) $("#c-userServiceId").value = "";
      if ($("#c-desktopLabel")) $("#c-desktopLabel").value = "";
      setComposerDesktopLock(!!(state.composer && state.composer.profileId));
      return;
    }
    let matched = false;
    let rows = "";
    for (let i = 0; i < list.length; i++) {
      const d = list[i] || {};
      const id = String(d.userServiceId || d.id || "");
      const spu = String(d.spuCode || d.spu || "");
      const label = d.desktopLabel || d.skuName || d.sku || d.vmName || d.name || d.labelName || id || "未命名";
      const active =
        selectedId && String(selectedId) === id
          ? true
          : !selectedId && list.length === 1;
      if (active) matched = true;
      const rid = "c-desk-" + i + "-" + id.replace(/[^a-zA-Z0-9_-]/g, "_");
      const proto = desktopProtocolText(d);
      const seq = String(i + 1);
      rows +=
        '<tr class="' +
        (active ? "is-selected" : "") +
        '">' +
        '<td class="col-idx">' +
        esc(seq) +
        "</td>" +
        '<td class="col-name" title="' +
        esc(label) +
        '">' +
        esc(label) +
        "</td>" +
        '<td class="col-id" title="' +
        esc(id) +
        '">' +
        esc(id || "—") +
        "</td>" +
        '<td class="col-proto" title="' +
        esc(proto) +
        '">' +
        esc(proto) +
        "</td>" +
        '<td class="col-act">' +
        '<label class="desk-select-wrap" for="' +
        rid +
        '">' +
        '<input type="radio" class="sr-only" name="c-desktop" id="' +
        rid +
        '" value="' +
        esc(id) +
        '" data-label="' +
        esc(label) +
        '" data-spu="' +
        esc(proto) +
        '"' +
        (active ? " checked" : "") +
        " />" +
        '<span class="btn btn-secondary desk-select-btn' +
        (active ? " is-active" : "") +
        '" data-desk-select="1">' +
        (active ? "已选" : "选择") +
        "</span></label></td></tr>";
    }
    box.innerHTML =
      '<div class="desk-table-wrap"><table class="desk-table" aria-label="云桌面">' +
      "<thead><tr>" +
      '<th class="col-idx">序号</th>' +
      '<th class="col-name">名称</th>' +
      '<th class="col-id">ID</th>' +
      '<th class="col-proto">协议</th>' +
      '<th class="col-act">操作</th>' +
      "</tr></thead>" +
      '<tbody id="c-desktop-tbody">' +
      rows +
      "</tbody></table></div>";
    if (matched) {
      const act = box.querySelector('input[name="c-desktop"]:checked');
      if (act) {
        state.composer.userServiceId = act.value || "";
        state.composer.desktopLabel =
          act.getAttribute("data-label") || act.value || "";
        if ($("#c-userServiceId")) $("#c-userServiceId").value = act.value || "";
        if ($("#c-desktopLabel"))
          $("#c-desktopLabel").value =
            act.getAttribute("data-label") || act.value || "";
      }
    }
    setComposerDesktopLock(true);
  }

  function applyOfficialFromDesktop(target, d) {
    if (!target || !d) return;
    const hint = (
      d.protocolHint ||
      d.protocol_hint ||
      d.protocol ||
      ""
    )
      .toString()
      .toUpperCase();
    const spu = d.spuCode || d.spu_code || "";
    if (hint) {
      target.protocol = hint;
      target.protocolHint = hint;
      target.lastOfficialProtocol = hint;
    }
    if (spu) target.spuCode = spu;
    if (hint || spu) {
      setComposerOfficial(
        (hint || "未知") + (spu ? " · spu " + spu : "")
      );
    }
  }

  function ensureComposerLoginBtn() {
    // HTML already has dual login buttons; keep as no-op fallback.
    if ($("#c-login") && $("#c-login-sub")) return;
    const actions = $(".composer-actions") || $(".field-login-cta");
    if (!actions) return;
    if (!$("#c-login")) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "btn btn-primary btn-login-cta";
      btn.id = "c-login";
      btn.textContent = "主帐号获取云桌面";
      btn.title = "主帐号登录并加载官方云桌面列表（不启动保活）";
      actions.appendChild(btn);
    }
    if (!$("#c-login-sub")) {
      const btn2 = document.createElement("button");
      btn2.type = "button";
      btn2.className = "btn btn-secondary btn-login-cta";
      btn2.id = "c-login-sub";
      btn2.textContent = "子帐号获取云桌面";
      btn2.title = "子帐号登录并加载官方云桌面列表（不启动保活）";
      actions.appendChild(btn2);
    }
  }

  async function composerLoginOnly(ev, modeOpt) {
    if (ev) ev.preventDefault();
    const mode = modeOpt === "sub" ? "sub" : "main";
    const isSub = mode === "sub";
    const c = readComposer();
    if (!c.username) {
      setComposerMsg("请填写账号", "error");
      return;
    }
    if (!c.password) {
      setComposerMsg("请填写密码", "error");
      return;
    }
    const loginBtn = $("#c-login");
    const loginSubBtn = $("#c-login-sub");
    const submitBtn = $("#c-submit");
    if (loginBtn) loginBtn.disabled = true;
    if (loginSubBtn) loginSubBtn.disabled = true;
    if (submitBtn) submitBtn.disabled = true;
    setComposerMsg("正在登录…");
    try {
      let pid = state.composer.profileId || "";
      if (!pid) {
        const created = await api("/api/profiles", {
          method: "POST",
          body: {
            displayName: c.displayName || undefined,
            username: c.username,
            password: c.password,
            clientProfile: c.clientProfile || "linux",
            protocol: resolveUserProtocol(c.protocol),
            draft: true,
          },
        });
        const p = created && created.profile;
        if (!p || !p.id) throw new Error("创建账号失败");
        pid = p.id;
        state.composer.profileId = pid;
        ensureDraft(pid, p);
      } else {
        ensureDraft(pid);
      }
      state.drafts[pid].username = c.username;
      state.drafts[pid].password = c.password;
      state.drafts[pid].protocol = resolveUserProtocol(c.protocol);
      state.drafts[pid].lastOfficialProtocol = state.drafts[pid].protocol;
      state.drafts[pid].clientProfile = c.clientProfile;
      state.drafts[pid].mode = c.mode;
      state.drafts[pid].intervalMin = c.intervalMin;
      state.drafts[pid].trafficSec = c.trafficSec;
      state.drafts[pid].durationSec = 0;
      state.drafts[pid].loginMode = mode;
      state.drafts[pid].isSubAccount = isSub;
      state.composer.loginMode = mode;
      state.composer.isSubAccount = isSub;

      await api("/api/profiles/" + encodeURIComponent(pid) + "/login", {
        method: "POST",
        body: {
          username: c.username,
          password: c.password,
          mode: mode,
          isSubAccount: isSub,
        },
      });
      setComposerMsg("登录成功，正在加载官方云桌面列表…", "ok");
      setComposerDesktopLock(true);
      pushGlobal(
        "[" + (c.displayName || c.username) + "] 登录成功，加载云桌面列表"
      );

      let list = [];
      try {
        const deskData = await api(
          "/api/profiles/" + encodeURIComponent(pid) + "/desktops"
        );
        list =
          (deskData && (deskData.desktops || deskData.items || deskData.list)) ||
          (Array.isArray(deskData) ? deskData : []) ||
          [];
        state.desktops[pid] = list;
        fillComposerDesktopSelect(list, c.userServiceId || "");
        if (!c.userServiceId && list.length === 1) {
          const only = list[0] || {};
          c.userServiceId = only.userServiceId || only.id || "";
          c.desktopLabel =
            only.desktopLabel || only.name || only.label || c.userServiceId;
          applyOfficialFromDesktop(c, only);
          applyOfficialFromDesktop(state.drafts[pid], only);
          fillComposerDesktopSelect(list, c.userServiceId);
        } else if (c.userServiceId) {
          const hit = list.find(function (d) {
            const id = d.userServiceId || d.id || "";
            return id === c.userServiceId;
          });
          if (hit) {
            applyOfficialFromDesktop(c, hit);
            applyOfficialFromDesktop(state.drafts[pid], hit);
          }
        }
        if (list.length) {
          setComposerMsg(
            "登录成功 · 已加载 " + list.length + " 台云桌面，请选择后点「保存并保活」",
            "ok"
          );
        } else {
          setComposerMsg("登录成功，但官方云桌面列表为空", "error");
        }
      } catch (de) {
        const dmsg = humanError(de, "云桌面列表加载失败");
        pushGlobal(
          "[" + (c.displayName || c.username) + "] 刷新桌面: " + dmsg,
          "error"
        );
        setComposerMsg("登录成功，但桌面列表失败: " + dmsg, "error");
      }
      /* HARD_GATE#850: login-only must not push draft into timeline */
        } catch (e) {
      const msg = humanError(e, "登录失败");
      setComposerMsg(msg, "error");
      toast(msg, true);
      pushGlobal("Composer 登录失败: " + msg, "error");
      /* HARD_GATE#850: login-only must not push draft into timeline */
        } finally {
      if (loginBtn) loginBtn.disabled = false;
      if (loginSubBtn) loginSubBtn.disabled = false;
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  async function composerSaveAndStart(ev) {
    if (ev) ev.preventDefault();
    const c = readComposer();
    if (!c.username) {
      setComposerMsg("请填写账号", "error");
      return;
    }
    if (!c.password) {
      setComposerMsg("请填写密码", "error");
      return;
    }
    const btn = $("#c-submit");
    const loginBtn = $("#c-login");
    if (btn) btn.disabled = true;
    if (loginBtn) loginBtn.disabled = true;
    setComposerMsg("正在保存并启动保活…");
    try {
      let pid = state.composer.profileId || "";
      if (!pid) {
        // Not yet logged via 登录: create profile first (still require desktop)
        const created = await api("/api/profiles", {
          method: "POST",
          body: {
            displayName: c.displayName || undefined,
            username: c.username,
            password: c.password,
            clientProfile: c.clientProfile || "linux",
            protocol: resolveUserProtocol(c.protocol),
          },
        });
        const p = created && created.profile;
        if (!p || !p.id) throw new Error("创建账号失败");
        pid = p.id;
        state.composer.profileId = pid;
        ensureDraft(pid, p);
        await api("/api/profiles/" + encodeURIComponent(pid) + "/login", {
          method: "POST",
          body: { username: c.username, password: c.password },
        });
        setComposerDesktopLock(true);
        try {
          const deskData = await api(
            "/api/profiles/" + encodeURIComponent(pid) + "/desktops"
          );
          const list =
            (deskData &&
              (deskData.desktops || deskData.items || deskData.list)) ||
            (Array.isArray(deskData) ? deskData : []) ||
            [];
          state.desktops[pid] = list;
          fillComposerDesktopSelect(list, c.userServiceId || "");
          if (!c.userServiceId && list.length === 1) {
            const only = list[0] || {};
            c.userServiceId = only.userServiceId || only.id || "";
            c.desktopLabel =
              only.desktopLabel || only.name || only.label || c.userServiceId;
            applyOfficialFromDesktop(c, only);
            applyOfficialFromDesktop(state.drafts[pid], only);
            fillComposerDesktopSelect(list, c.userServiceId);
          }
        } catch (_) {}
      }
      ensureDraft(pid);
      state.drafts[pid].username = c.username;
      state.drafts[pid].password = c.password;
      state.drafts[pid].protocol = resolveUserProtocol(c.protocol);
      state.drafts[pid].lastOfficialProtocol = state.drafts[pid].protocol;
      state.drafts[pid].clientProfile = c.clientProfile;
      state.drafts[pid].mode = c.mode;
      state.drafts[pid].intervalMin = c.intervalMin;
      state.drafts[pid].trafficSec = c.trafficSec;
      state.drafts[pid].durationSec = 0;

      // re-read desktop selection from DOM after possible fill
      const c2 = readComposer();
      c.userServiceId = c2.userServiceId || c.userServiceId;
      c.desktopLabel = c2.desktopLabel || c.desktopLabel;
      c.protocol = c2.protocol || c.protocol;

      const list = state.desktops[pid] || [];
      if (!c.userServiceId) {
        if (list.length > 1) {
          setComposerMsg("请选择云桌面后再点「保存并保活」", "error");
          toast("请先选择云桌面", true);
          await loadProfiles();
          return;
        }
        if (!list.length) {
          setComposerMsg(
            "请先点「登录」加载官方云桌面列表，再选择桌面后保存并保活",
            "error"
          );
          toast("请先登录并选择云桌面", true);
          await loadProfiles();
          return;
        }
        if (list.length === 1) {
          const only = list[0] || {};
          c.userServiceId = only.userServiceId || only.id || "";
          c.desktopLabel =
            only.desktopLabel || only.name || only.label || c.userServiceId;
          applyOfficialFromDesktop(c, only);
          applyOfficialFromDesktop(state.drafts[pid], only);
        }
      }

      if (c.userServiceId || c.desktopLabel) {
        await api(
          "/api/profiles/" + encodeURIComponent(pid) + "/select-desktop",
          {
            method: "POST",
            body: {
              userServiceId: c.userServiceId || undefined,
              desktopLabel: c.desktopLabel || undefined,
              protocol: (
                resolveUserProtocol(c.protocol, state.drafts[pid] && state.drafts[pid].protocol, state.drafts[pid] && state.drafts[pid].lastOfficialProtocol)
              ).toUpperCase(),
              protocolHint:
                (c.protocol || state.drafts[pid].protocol || "").toUpperCase() ||
                undefined,
              spuCode: state.drafts[pid].spuCode || undefined,
            },
          }
        );
        state.drafts[pid].userServiceId = c.userServiceId || "";
        state.drafts[pid].desktopLabel = c.desktopLabel || "";
      }

      setComposerMsg("正在启动保活…", "ok");
      const mode = modeApi(c.mode);
      const trafficSec = Number(c.trafficSec || 60);
      await api("/api/profiles/" + encodeURIComponent(pid) + "/jobs", {
        method: "POST",
        body: {
          protocol: (
            resolveUserProtocol(c.protocol, state.drafts[pid] && state.drafts[pid].protocol, state.drafts[pid] && state.drafts[pid].lastOfficialProtocol)
          ).toUpperCase(),
          mode: mode,
          clientProfile: c.clientProfile || "linux",
          intervalSec: Math.max(60, Number(c.intervalMin || 5) * 60),
          trafficSec: trafficSec,
          durationSec: durationForMode(mode, trafficSec),
        },
      });
      toast("保存并保活成功");
      setComposerMsg("保存并保活成功", "ok");
      pushGlobal(
        "[" +
          (c.displayName || c.username) +
          "] 保存并保活 · " +
          protocolLabel(c.protocol || state.drafts[pid].protocol) +
          " · " +
          modeLabel(mode)
      );
      clearComposer();
      await loadProfiles();
      await loadLogs(pid);
    } catch (e) {
      const msg = humanError(e, "保存并保活失败");
      setComposerMsg(msg, "error");
      toast(msg, true);
      pushGlobal("Composer 失败: " + msg, "error");
      await loadProfiles();
    } finally {
      if (btn) btn.disabled = false;
      if (loginBtn) loginBtn.disabled = false;
    }
  }

  // legacy alias (submit handler name used in older wires)
  async function composerLoginAndStart(ev) {
    return composerSaveAndStart(ev);
  }


  function applyDraftFromEl(el) {
    const pid = el.getAttribute("data-pid");
    const key = el.getAttribute("data-key");
    if (!pid || !key) return;
    const d = ensureDraft(pid);
    if (key === "desktop") {
      const parts = String(el.value || "").split("||");
      d.userServiceId = parts[0] || "";
      d.desktopLabel = parts[1] || parts[0] || "";
      const list = state.desktops[pid] || [];
      let matched = null;
      for (let i = 0; i < list.length; i++) {
        const x = list[i];
        const xid = x.userServiceId || x.id || "";
        if (xid === d.userServiceId) {
          matched = x;
          break;
        }
      }
      if (matched) {
        applyOfficialFromDesktop(d, matched);
      }
      const root = el.closest(".desk-seg");
      if (root) {
        const items = root.querySelectorAll(".desk-seg-item");
        for (let i = 0; i < items.length; i++) {
          items[i].classList.toggle(
            "is-active",
            items[i].contains(el) || (items[i].querySelector("input") || {}).checked
          );
        }
      }
      if (el.getAttribute("data-surface") === "1") {
        renderCards();
      } else if (state.configPid === pid) {
        refreshConfigModal();
      }
      return;
    }
    if (key === "intervalMin" || key === "trafficSec") {
      const raw = el.getAttribute("data-val");
      d[key] = Number(raw != null ? raw : el.value || 0);
    } else if (key === "durationSec") {
      /* HARD_GATE#729: ignore duration UI if residual HTML still present */
      d.durationSec = 0;
    } else {
      const raw = el.getAttribute("data-val");
      const val = raw != null ? raw : el.value;
      d[key] = val;
      if (key === "protocol" && val) {
        d.lastOfficialProtocol = val;
      }
    }
    // seg-btn active state in config modal / cards
    if (el.classList && el.classList.contains("seg-btn")) {
      const group = el.closest(".seg");
      if (group) {
        const btns = group.querySelectorAll(".seg-btn");
        for (let i = 0; i < btns.length; i++) {
          btns[i].classList.toggle("active", btns[i] === el);
        }
      }
    }
  }

  function bindCardEvents() {
    const root = $("#timeline");
    if (!root || root._bound) return;
    root._bound = true;

    root.addEventListener("click", function (ev) {
      const segBtn = ev.target.closest(".seg-btn[data-key]");
      if (segBtn) {
        applyDraftFromEl(segBtn);
        return;
      }
      const actEl = ev.target.closest("[data-act]");
      const card = ev.target.closest(".card");
      if (!card) return;
      const pid = card.getAttribute("data-id");
      if (!pid) return;
      const act = actEl ? actEl.getAttribute("data-act") : "";
      // 配置入口：居中 Modal（OPS#337）；卡面保持紧凑不展开
      if (act === "config" || act === "config-close") {
        ev.preventDefault();
        if (act === "config-close") {
          closeConfigModal();
        } else if (state.configPid === pid) {
          closeConfigModal();
        } else {
          openConfigModal(pid);
          loadLogs(pid).catch(function () {});
        }
        return;
      }
      if (!act) return;
      ev.preventDefault();
      if (act === "start") onStart(pid);
      else if (act === "stop") onStop(pid);
      else if (act === "save") onSave(pid);
      else if (act === "delete") onDelete(pid);
      else if (act === "desktops") onDesktops(pid);
      else if (act === "login") onConfigLogin(pid);
      else if (act === "desktop-logout" || act === "clear-thread") onClearThread(pid);
      else if (act === "clear-logs") {
        // HARD_GATE#853: real backend clear (not FE-only fake clear)
        if (!pid) return;
        const btn = ev.target.closest("[data-act]");
        if (btn) btn.disabled = true;
        api("/api/profiles/" + encodeURIComponent(pid) + "/logs", { method: "DELETE" })
          .then(function (data) {
            state.logs[pid] = [];
    try { patchCardDeskStatus(pid); } catch (_e) {}
            state._logClearedAt = state._logClearedAt || {};
            state._logClearedAt[pid] = Date.now();
            applyLogsToDom(pid, true);
            if (state.logModalPid === pid) {
              const full =
                $("#log-full-body") ||
                $("#log-full .log-box") ||
                $(".log-full .log-box");
              if (full) full.innerHTML = profileLogsHtml(pid, { full: true });
            }
            const n = data && data.cleared != null ? data.cleared : 0;
            toast("已清空该账号日志" + (n ? "（" + n + "）" : ""));
            pushGlobal("[" + pid + "] 卡片日志已清空（后端缓冲 " + n + "）");
          })
          .catch(function (err) {
            toast((err && err.message) || "清空日志失败", "error");
            pushGlobal("[" + pid + "] 清空日志失败: " + ((err && err.message) || err), "error");
          })
          .finally(function () {
            if (btn) btn.disabled = false;
          });
      }
    });

    root.addEventListener("input", function (ev) {
      applyDraftFromEl(ev.target);
    });

    root.addEventListener("change", function (ev) {
      applyDraftFromEl(ev.target);
    });

    // HARD_GATE#768-C / HARD_GATE#810: double-click card log panel → full history modal
    root.addEventListener("dblclick", function (ev) {
      const t = ev.target;
      if (!t || !t.closest) return;
      // hit head / empty / line / box / whole panel (not only .log-box)
      const hit = t.closest(
        ".log-panel, .log-panel-head, .log-box, .log-viewport, .log-line, .log-empty, [data-log]"
      );
      if (!hit) return;
      const card = hit.closest(".card");
      const holder =
        (hit.getAttribute && hit.getAttribute("data-log") && hit) ||
        hit.closest("[data-log]") ||
        card;
      const pid =
        (holder && holder.getAttribute && holder.getAttribute("data-log")) ||
        (holder && holder.getAttribute && holder.getAttribute("data-id")) ||
        (card && card.getAttribute("data-id")) ||
        "";
      if (!pid) return;
      ev.preventDefault();
      if (ev.stopPropagation) ev.stopPropagation();
      openLogModal(pid);
      loadLogs(pid).catch(function () {});
    });

    // Modal is outside #timeline — bind separately (OPS#337)
    const modal = $("#config-modal");
    if (modal && !modal._bound) {
      modal._bound = true;
      modal.addEventListener("click", function (ev) {
        if (ev.target === modal) {
          closeConfigModal();
          return;
        }
        const segBtn = ev.target.closest(".seg-btn[data-key]");
        if (segBtn) {
          applyDraftFromEl(segBtn);
          return;
        }
        const actEl = ev.target.closest("[data-act]");
        if (!actEl) return;
        const act = actEl.getAttribute("data-act");
        const pid = actEl.getAttribute("data-pid") || state.configPid || "";
        if (act === "config-close") {
          ev.preventDefault();
          closeConfigModal();
          return;
        }
        if (act === "save" && pid) {
          ev.preventDefault();
          onSave(pid);
          return;
        }
        if (act === "save-start" && pid) {
          ev.preventDefault();
          onStart(pid);
          return;
        }
        if (act === "desktops" && pid) {
          ev.preventDefault();
          onDesktops(pid);
          return;
        }
        // HARD_GATE#665 D: delete account from config modal (modal is outside #timeline)
        if (act === "delete" && pid) {
          ev.preventDefault();
          onDelete(pid);
          return;
        }
      });
      modal.addEventListener("input", function (ev) {
        applyDraftFromEl(ev.target);
      });
      modal.addEventListener("change", function (ev) {
        applyDraftFromEl(ev.target);
      });
    }
  }


  function applyJobEvent(data) {
    if (!data || typeof data !== "object") return;
    const jid = data.jobId || data.job_id || data.id || null;
    const pid = data.profileId || data.profile_id || null;
    if (!jid && !pid) {
      if (data.detail && data.detail !== "global-sse" && data.detail !== "snapshot") {
        pushGlobal(String(data.detail), data.status === "error" ? "error" : "info");
      }
      return;
    }
    const prev =
      (jid && state.jobsById[jid]) ||
      (pid && state.jobsByProfile[pid]) ||
      null;
    const merged = Object.assign({}, prev || {}, data);
    if (jid) {
      merged.id = merged.id || jid;
      merged.jobId = merged.jobId || jid;
      state.jobsById[jid] = merged;
    }
    if (pid) {
      merged.profileId = merged.profileId || pid;
      state.jobsByProfile[pid] = merged;
    }
    const status = merged.status || data.status || "";
    const label = pid || jid || "?";
    // HARD_GATE#768-B: job status meta may hit global; keepalive round/detail stays card-only via pushCard
    const detail = data.detail ? String(data.detail) : "";
    const looksKeepalive =
      /保活|keepalive|SCG|第\s*\d+\s*轮|round/i.test(detail) ||
      /保活|keepalive|SCG|第\s*\d+\s*轮|round/i.test(status);
    if (looksKeepalive && pid) {
      if (detail) pushCard(pid, detail, data.at || new Date().toISOString());
    } else if (status && (!prev || prev.status !== status)) {
      pushGlobal(
        "[" + label + "] job " + status + (detail && !looksKeepalive ? " — " + detail : ""),
        status === "error" ? "error" : "info"
      );
    } else if (detail && detail !== "snapshot" && !looksKeepalive) {
      pushGlobal("[" + label + "] " + detail, status === "error" ? "error" : "info");
    }
    try {
      // HARD_GATE#784: status-only patch; do not rebuild log panels
      if (pid) patchCardStatus(pid);
      else if (jid) {
        const p = state.profiles.find(function (x) {
          const j = jobOf(x);
          return j && String(j.id || j.jobId || "") === String(jid);
        });
        if (p) patchCardStatus(p.id);
      }
    } catch (_) {}
  }

  function applyJobLogEvent(data) {
    if (!data || typeof data !== "object") return;
    const line = data.line || data.message || "";
    if (!line) return;
    const pid = data.profileId || data.profile_id || "";
    // HARD_GATE#854: SSE buffers + paints via pushCard→applyLogsToDom (clear后新日志立即可见)
    if (pid) pushCard(pid, line, data.at || new Date().toISOString());
  }

  function connectSSE() {
    if (typeof EventSource === "undefined") return;
    try {
      if (state.es) {
        try {
          state.es.close();
        } catch (_) {}
        state.es = null;
      }
      // EventSource cannot set Authorization headers; BE accepts ?token= (and Bearer on fetch).
      const token = getToken();
      if (state.tokenRequired && !token) {
        if (!state.sseNeedTokenLogged) {
          pushGlobal(
            "需要访问令牌才能连接事件流 · 请在顶部填写并保存，或使用 ?token=…",
            "error"
          );
          state.sseNeedTokenLogged = true;
        }
        return;
      }
      state.sseNeedTokenLogged = false;
      let url = "/api/events";
      if (token) {
        url +=
          (url.indexOf("?") >= 0 ? "&" : "?") +
          "token=" +
          encodeURIComponent(token);
      }
      const es = new EventSource(url);
      state.es = es;
      // BE emits named events (event: job_status / job_log); onmessage only gets unnamed.
      es.addEventListener("job_status", function (ev) {
        try {
          applyJobEvent(JSON.parse(ev.data));
        } catch (_) {}
      });
      es.addEventListener("job_log", function (ev) {
        try {
          applyJobLogEvent(JSON.parse(ev.data));
        } catch (_) {}
      });
      es.addEventListener("job_log_cleared", function (ev) {
        try {
          const d = JSON.parse(ev.data) || {};
          const pid = d.profileId || d.profile_id || "";
          if (!pid) return;
          state.logs[pid] = [];
    try { patchCardDeskStatus(pid); } catch (_e) {}
          applyLogsToDom(pid, true);
        } catch (_) {}
      });
      es.onmessage = function (ev) {
        try {
          const data = JSON.parse(ev.data);
          if (data && data.line) {
            applyJobLogEvent(data);
          } else if (data && (data.status || data.jobId || data.profileId)) {
            applyJobEvent(data);
          } else if (data && data.detail) {
            pushGlobal(String(data.detail), data.level || "info");
          }
        } catch (_) {}
      };
      es.onerror = function () {
        /* quiet reconnect by browser */
      };
    } catch (_) {}
  }


  // HARD_GATE#831 CARD_LOG_DBLCLICK: double-click card log viewport opens full modal
  document.addEventListener(
    "dblclick",
    function (ev) {
      const t = ev.target;
      if (!t || !t.closest) return;
      const box = t.closest('[data-log], .log-panel, .card-surface-log-only, .log-viewport');
      if (!box) return;
      let pid = box.getAttribute("data-log");
      if (!pid) {
        const card = box.closest("[data-pid], .account-card, .card");
        if (card) pid = card.getAttribute("data-pid") || (card.dataset && card.dataset.pid);
      }
      if (!pid && box.querySelector) {
        const inner = box.querySelector("[data-log]");
        if (inner) pid = inner.getAttribute("data-log");
      }
      if (!pid) return;
      ev.preventDefault();
      openLogModal(pid);
      loadLogs(pid).catch(function () {});
    },
    false
  );

  function startPolling() {
    // HARD_GATE#831: profile/status poll 4s; card logs poll 6s (HARD_GATE#852)
    setInterval(async function () {
      try {
        await loadJobs();
        const data = await api("/api/profiles");
        const next = ((data && data.profiles) || []).filter(function (p) {
          return p && !p.draft && p.draft !== true && p.draft !== 1 && p.draft !== "1";
        });
        const prevMap = Object.create(null);
        for (let i = 0; i < state.profiles.length; i++) {
          prevMap[state.profiles[i].id] = statusOf(state.profiles[i]);
        }
        // HARD_GATE#784: only full-render when membership/status set changes
        let needFull = next.length !== state.profiles.length;
        if (!needFull) {
          for (let i = 0; i < next.length; i++) {
            const id = next[i].id;
            if (!prevMap[id]) {
              needFull = true;
              break;
            }
            if (prevMap[id] !== statusOf(next[i])) {
              // status change handled below via patch; still need profile data swap
            }
          }
        }
        const idSetPrev = state.profiles
          .map(function (x) {
            return x.id;
          })
          .join("\0");
        const idSetNext = next
          .map(function (x) {
            return x.id;
          })
          .join("\0");
        if (idSetPrev !== idSetNext) needFull = true;
        state.profiles = next;
        const active = document.activeElement;
        const keepPid =
          active && active.getAttribute ? active.getAttribute("data-pid") : null;
        const keepKey =
          active && active.getAttribute ? active.getAttribute("data-key") : null;
        const selStart = active && active.selectionStart;
        const selEnd = active && active.selectionEnd;
        /* HARD_GATE#851: NEVER full-render while config panel open (kills flicker) */
        if (state.configPid) {
          for (let i = 0; i < next.length; i++) {
            patchCardStatus(next[i].id);
          }
        } else if (needFull) {
          renderCards();
        } else {
          for (let i = 0; i < next.length; i++) {
            patchCardStatus(next[i].id);
          }
        }
        if (keepPid && keepKey) {
          const el = $(
            'input[data-pid="' +
              keepPid +
              '"][data-key="' +
              keepKey +
              '"], select[data-pid="' +
              keepPid +
              '"][data-key="' +
              keepKey +
              '"]'
          );
          if (el) {
            el.focus();
            if (typeof selStart === "number" && el.setSelectionRange) {
              try {
                el.setSelectionRange(selStart, selEnd);
              } catch (_) {}
            }
          }
        }
        next.forEach(function (p) {
          if (!p || !p.id) return;
          const pid = p.id;
          const prev = prevMap[pid];
          const now = statusOf(p);
          if (prev && now && prev !== now) {
            pushGlobal(
              "[" +
                (p.displayName || pid) +
                "] 状态 " +
                statusLabel(prev) +
                " → " +
                statusLabel(now)
            );
          }
        });
      } catch (_) {}
    }, 4000);

    setInterval(async function () {
      try {
        // HARD_GATE#855 / #852: 6s log poll — applyLogsToDom only (fingerprint + modal), never full-render
        const list = state.profiles || [];
        for (let i = 0; i < list.length; i++) {
          const p = list[i];
          if (!p || !p.id) continue;
          await loadLogs(p.id).catch(function () {});
        }
        // open modal kept in sync via applyLogsToDom(state.logModalPid)
      } catch (_) {}
    }, 6000); /* HARD_GATE#856/#855/#852: card log poll 6s */
  }

  function wireChrome() {
    $("#btn-refresh") &&
      $("#btn-refresh").addEventListener("click", async function () {
        // HARD_GATE#843: top refresh reloads profiles + jobs + all card logs (minute paint path)
        const btn = $("#btn-refresh");
        if (btn) btn.disabled = true;
        try {
          await loadJobs();
          await loadProfiles(false);
          const ids = state.profiles.map(function (p) { return p.id; });
          await Promise.all(
            ids.map(function (pid) {
              return loadLogs(pid, false).catch(function () {});
            })
          );
          toast("已刷新账号与日志");
          pushGlobal("整页刷新完成 · " + ids.length + " 个账号");
        } catch (e) {
          toast(humanError(e, "刷新失败"), true);
        } finally {
          if (btn) btn.disabled = false;
        }
      });
    $("#btn-clear-log") &&
      $("#btn-clear-log").addEventListener("click", function () {
        state.globalLog = [];
        renderGlobalLog();
      });
    $("#c-clear") &&
      $("#c-clear").addEventListener("click", function () {
        clearComposer();
      });
    ensureComposerLoginBtn();
    $("#c-login") &&
      $("#c-login").addEventListener("click", function (ev) {
        composerLoginOnly(ev, "main");
      });
    $("#c-login-sub") &&
      $("#c-login-sub").addEventListener("click", function (ev) {
        composerLoginOnly(ev, "sub");
      });
    $("#composer-form") &&
      $("#composer-form").addEventListener("submit", composerSaveAndStart);

    $$(".composer .seg-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        const protocol = btn.getAttribute("data-protocol");
        const client = btn.getAttribute("data-client");
        const mode = btn.getAttribute("data-mode");
        if (protocol) {
          state.composer.protocol = protocol;
          $$('.composer .seg-btn[data-protocol]').forEach(function (b) {
            b.classList.toggle(
              "active",
              b.getAttribute("data-protocol") === protocol
            );
          });
        }
        if (client) {
          state.composer.clientProfile = client;
          $$('.composer .seg-btn[data-client]').forEach(function (b) {
            b.classList.toggle(
              "active",
              b.getAttribute("data-client") === client
            );
          });
        }
        if (mode) {
          state.composer.mode = mode;
          $$('.composer .seg-btn[data-mode]').forEach(function (b) {
            b.classList.toggle("active", b.getAttribute("data-mode") === mode);
          });
        }
      });
    });

    $("#c-desk-refresh") &&
      $("#c-desk-refresh").addEventListener("click", function (ev) {
        ev.preventDefault();
        const fake = { target: ev.currentTarget, preventDefault: function () {} };
        const box = $("#c-desktop");
        if (box) {
          // reuse same path as in-panel refresh by synthesizing event on box listener
        }
        const pid = state.composer.profileId;
        if (!pid) {
          setComposerMsg("请先登录以加载官方云桌面列表", "error");
          return;
        }
        (async function () {
          const hit = $("#c-desk-refresh");
          try {
            state.busy[pid] = true;
            setComposerMsg("正在刷新官方云桌面列表…");
            setComposerDeskRefreshEnabled(true);
            const deskData = await api(
              "/api/profiles/" + encodeURIComponent(pid) + "/desktops"
            );
            const list =
              (deskData && (deskData.desktops || deskData.items || deskData.list)) ||
              (Array.isArray(deskData) ? deskData : []) ||
              [];
            state.desktops[pid] = list;
            fillComposerDesktopSelect(list, state.composer.userServiceId || "");
            setComposerMsg(
              list.length
                ? "已刷新官方云桌面 " + list.length + " 台"
                : "官方列表为空",
              list.length ? "ok" : "warn"
            );
          } catch (err) {
            setComposerMsg((err && err.message) || "刷新云桌面失败", "error");
          } finally {
            state.busy[pid] = false;
            fillComposerDesktopSelect(
              state.desktops[pid] || [],
              state.composer.userServiceId || ""
            );
          }
        })();
      });

    $("#c-desktop") &&
      $("#c-desktop").addEventListener("click", function (ev) {
        /* HARD_GATE#842: row 操作「选择」*/
        const sel = ev.target && ev.target.closest
          ? ev.target.closest("[data-desk-select]")
          : null;
        if (sel) {
          const lab = sel.closest("label");
          const input = lab && lab.querySelector('input[type="radio"]');
          if (input && !input.disabled) {
            input.checked = true;
            input.dispatchEvent(new Event("change", { bubbles: true }));
          }
          return;
        }
        /* HARD_GATE#707-2: legacy in-panel refresh control */
        const hit = ev.target && ev.target.closest
          ? ev.target.closest('[data-act="composer-desktops"]')
          : null;
        if (!hit) return;
        ev.preventDefault();
        const pid = state.composer.profileId;
        if (!pid) {
          setComposerMsg("请先登录以加载官方云桌面列表", "error");
          return;
        }
        (async function () {
          try {
            state.busy[pid] = true;
            setComposerMsg("正在刷新官方云桌面列表…");
            if (hit) {
              hit.disabled = true;
              hit.classList.add("is-loading");
              hit.textContent = "刷新中…";
            }
            const deskData = await api(
              "/api/profiles/" + encodeURIComponent(pid) + "/desktops"
            );
            const list =
              (deskData && (deskData.desktops || deskData.items || deskData.list)) ||
              (Array.isArray(deskData) ? deskData : []) ||
              [];
            state.desktops[pid] = list;
            fillComposerDesktopSelect(
              list,
              state.composer.userServiceId || ""
            );
            setComposerMsg(
              list.length
                ? "已刷新官方云桌面 " + list.length + " 台"
                : "官方列表为空",
              list.length ? "ok" : "warn"
            );
          } catch (err) {
            setComposerMsg(
              (err && err.message) || "刷新云桌面失败",
              "error"
            );
          } finally {
            state.busy[pid] = false;
            // rebuild CTA if list still empty / restore label
            fillComposerDesktopSelect(
              state.desktops[pid] || [],
              state.composer.userServiceId || ""
            );
          }
        })();
      });

    $("#c-desktop") &&
      $("#c-desktop").addEventListener("change", function (ev) {
        const t = ev.target;
        if (!t || t.name !== "c-desktop") return;
        const id = t.value || "";
        const label = t.getAttribute("data-label") || id;
        state.composer.userServiceId = id;
        state.composer.desktopLabel = label;
        var spuPick = t.getAttribute("data-spu") || "";
        if (!spuPick) {
          var pid0 = state.composer.profileId;
          var list0 = (pid0 && state.desktops[pid0]) || [];
          for (var si = 0; si < list0.length; si++) {
            var dd = list0[si] || {};
            if (String(dd.userServiceId || dd.id || "") === String(id)) {
              spuPick = String(dd.spuCode || dd.spu || "");
              break;
            }
          }
        }
        state.composer.spuCode = spuPick;
        if ($("#c-userServiceId")) $("#c-userServiceId").value = id;
        if ($("#c-desktopLabel")) $("#c-desktopLabel").value = label;
        if ($("#c-spuCode")) $("#c-spuCode").value = spuPick;
        $$("#c-desktop tbody tr").forEach(function (tr) {
          tr.classList.toggle("is-selected", tr.contains(t));
        });
        $$("#c-desktop .desk-select-btn").forEach(function (btn) {
          const on = btn.closest("label") && btn.closest("label").contains(t);
          btn.classList.toggle("is-active", !!on);
          btn.textContent = on ? "已选" : "选择";
        });
        const pid = state.composer.profileId;
        const list = (pid && state.desktops[pid]) || [];
        for (let i = 0; i < list.length; i++) {
          const d = list[i] || {};
          if (String(d.userServiceId || d.id || "") === String(id)) {
            applyOfficialFromDesktop(state.composer, d);
            break;
          }
        }
      });

    const help = $("#help-modal");
    $("#btn-help") &&
      $("#btn-help").addEventListener("click", function () {
        if (!help) return;
        help.classList.remove("hidden");
        help.setAttribute("aria-hidden", "false");
      });
    $("#help-close") &&
      $("#help-close").addEventListener("click", function () {
        if (!help) return;
        help.classList.add("hidden");
        help.setAttribute("aria-hidden", "true");
      });


    $("#config-modal-close") &&
      $("#config-modal-close").addEventListener("click", function () {
        closeConfigModal();
      });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" || ev.key === "Esc") {
        // HARD_GATE#827: Esc closes full-log modal before config/help
        const lm = $("#log-modal") || $("#log-full-modal");
        if (
          lm &&
          state.logModalPid &&
          !lm.classList.contains("hidden") &&
          lm.getAttribute("aria-hidden") !== "true"
        ) {
          closeLogModal();
          return;
        }
        const cm = $("#config-modal");
        if (cm && !cm.classList.contains("hidden")) {
          closeConfigModal();
          return;
        }
        const help = $("#help-modal");
        if (help && !help.classList.contains("hidden")) {
          help.classList.add("hidden");
          help.setAttribute("aria-hidden", "true");
        }
      }
    });

    try {
      const u = new URL(location.href);
      const t = u.searchParams.get("token");
      if (t) {
        setToken(t);
        u.searchParams.delete("token");
        history.replaceState({}, "", u.pathname + u.search + u.hash);
        // Token arrived after boot path may have skipped SSE; reconnect with ?token=.
        state.sseNeedTokenLogged = false;
        connectSSE();
      }
    } catch (_) {}
  }

  async function boot() {
    bindCardEvents();
    wireChrome();
    pushGlobal("爱家移动云电脑就绪 · 多账户保活控制台");
    await loadSys();
    await loadProfiles(true);
    connectSSE();
    startPolling();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
