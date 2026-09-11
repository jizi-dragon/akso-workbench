"""自动登录引擎（quick-login content/auto-login.ts 全量知识迁移 → Playwright）。

节奏门控五重门（参数与原实现一致，完整清单见 docs/CONFIG.md §4.1）：
  ① 字段齐备门槛    用户名/密码（srcdoc iframe 内）任一未就绪 → 绝不提交
  ② 提交前回读      点击落地前 500ms 复核两字段值，受控组件状态未落地则推迟
  ③ 即时填充        MutationObserver + window load → 100ms 去抖 attempt；800ms 轮询兜底
  ④ 用户点击接管    trusted input/click → userTouched 永久让位
  ⑤ 失败感知让位    antd 错误元素计数，观察期 3500ms 内新增 ≥1 记一次；累计 2 次停手

关键迁移点：
- 密码框在同源 srcdoc iframe 内 → 顶层 frame 直接 iframe.contentDocument 探测
  （fillPasswordInIframes），跨 realm setValue（iframe 自身 realm 的原生 setter +
  input/change 事件）兼容 React 受控组件。
- 引擎以 MAIN world 注入（page.add_init_script），显式 start 后才启动状态机；
  状态暴露在 window.__aksoAutoLoginState 供 Python 轮询。
- token 捕获：captureToken 思路简化版 —— context.on("response") 抓
  Authorization: Bearer <JWT>（形状校验 ^\\w+.\\w+.[\\w-]*$），按账号存内存快照。
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------- 引擎 JS（MAIN world）

AUTOLOGIN_JS = r"""
(() => {
  if (window.__aksoAutoLoginLoaded) return;
  window.__aksoAutoLoginLoaded = true;

  const state = {
    phase: 'idle',      // idle | running | success | stopped | gave_up
    reason: '',
    attempts: 0,
    errors: 0,
    userTouched: false,
    startedAt: 0,
    updatedAt: 0,
  };
  const touch = () => { state.updatedAt = Date.now(); };
  const setPhase = (phase, reason) => { state.phase = phase; if (reason) state.reason = reason; touch(); };

  // ---- 跨 realm setValue（React/AntD 受控组件兼容）----
  function setValue(doc, el, value) {
    const win = doc.defaultView;
    try {
      const setter = Object.getOwnPropertyDescriptor(win.HTMLInputElement.prototype, 'value').set;
      setter.call(el, value);
      el.dispatchEvent(new win.Event('input', { bubbles: true }));
      el.dispatchEvent(new win.Event('change', { bubbles: true }));
    } catch (e) {
      el.value = value;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      el.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  // ---- ① 字段齐备门槛 ----
  function findUsername(doc) {
    return doc.querySelector('input[placeholder="请输入用户名"]')
      || doc.querySelector('input[type="text"]');
  }

  function fillUsername(doc, username) {
    const field = findUsername(doc);
    if (!field) return false;
    if (field.value !== username) setValue(doc, field, username);
    return true;
  }

  // srcdoc iframe 密码框探测（顶层 frame 直访 contentDocument）
  function fillPasswordInIframes(doc, password) {
    for (const iframe of doc.querySelectorAll('iframe')) {
      try {
        const inner = iframe.contentDocument;
        const iwin = iframe.contentWindow;
        if (!inner || !iwin) continue;
        const field = inner.querySelector('input[type="password"]')
          || inner.querySelector('input[placeholder*="密码"]')
          || inner.querySelector('input[placeholder*="password"]');
        if (field && field.value !== password) setValue(inner, field, password);
        return Boolean(field);
      } catch (e) { /* 跨域 iframe 读不到，跳过 */ }
    }
    const own = doc.querySelector('input[type="password"]')
      || doc.querySelector('input[placeholder*="密码"]')
      || doc.querySelector('input[placeholder*="password"]');
    if (own && own.value !== password) setValue(doc, own, password);
    return Boolean(own);
  }

  function checkAgreement(doc) {
    const box = doc.querySelector('#privacyChecked')
      || Array.from(doc.querySelectorAll('input[type="checkbox"]')).find((el) => {
        const label = el.labels && el.labels[0];
        const text = [el.name, el.getAttribute('aria-label') || '', label ? label.textContent : ''].join(' ');
        return /同意|agree/i.test(text);
      });
    if (box && !box.checked) box.click();
    return true;
  }

  function fillAll(doc, credentials) {
    const uOk = fillUsername(doc, credentials.username);
    const pOk = fillPasswordInIframes(doc, credentials.password);
    checkAgreement(doc);
    return uOk && pOk;   // 任一未齐备 → 本轮绝不进入点击流程
  }

  // 提交前回读：遍历 iframe 取密码现值（跨域读不到返回 null）
  function readPasswordValue(doc) {
    for (const iframe of doc.querySelectorAll('iframe')) {
      try {
        const inner = iframe.contentDocument;
        if (!inner) continue;
        const f = inner.querySelector('input[type="password"]');
        if (f) return f.value;
      } catch (e) { /* cross-origin */ }
    }
    const own = doc.querySelector('input[type="password"]');
    return own ? own.value : null;
  }

  function findSubmit(doc) {
    const candidates = Array.from(
      doc.querySelectorAll('button:not([disabled]), input[type="submit"]:not([disabled])')
    );
    const byText = candidates.find((b) =>
      /登\s*录|login|sign\s*in/i.test((b.textContent || b.value || '').trim()));
    if (byText) return byText;
    return doc.querySelector('button[type="submit"]:not([disabled]), input[type="submit"]:not([disabled])');
  }

  // ⑤ 失败感知：antd 错误元素计数
  function countErrors(doc) {
    return doc.querySelectorAll(
      '.ant-message-error, .ant-message-notice-error, .ant-alert-error, .ant-form-item-explain-error'
    ).length;
  }

  // ---- 状态机（runTopFrameFlow 全量参数迁移）----
  function runTopFrameFlow(credentials) {
    const deadline = Date.now() + 30000;   // 总截止 30s
    const MAX_ATTEMPTS = 4;                // 提交次数上限（含被拒）
    const CLICK_DELAY = 500;               // 填完 → 500ms → 复核 → click
    const OBSERVE_WINDOW = 3500;           // 点击后观察期
    let attempts = 0;
    let errorSeen = 0;
    let errorFlagged = false;
    let lastClickAt = 0;
    let errorsAtLastClick = 0;
    let absentStreak = 0;
    let userTouched = false;
    let timer = null;
    let observer = null;
    let pending = false;

    const stop = (phase, reason) => {
      setPhase(phase, reason);
      state.attempts = attempts;
      state.errors = errorSeen;
      state.userTouched = userTouched;
      if (timer) { clearInterval(timer); timer = null; }
      if (observer) { observer.disconnect(); observer = null; }
      window.removeEventListener('input', onTrustedInput, true);
      window.removeEventListener('click', onTrustedClick, true);
    };

    // ④ 用户点击接管：trusted 事件即让位（自动 click 的 isTrusted=false 不触发自己）
    const onTrustedInput = (e) => {
      if (!e.isTrusted) return;
      const t = e.target;
      if (t instanceof HTMLInputElement && ['text', 'password', ''].includes(t.type)) {
        userTouched = true; stop('stopped', 'user_input');
      }
    };
    const onTrustedClick = (e) => {
      if (!e.isTrusted) return;
      if (e.target && e.target.closest && e.target.closest('button')) {
        userTouched = true; stop('stopped', 'user_click');
      }
    };
    window.addEventListener('input', onTrustedInput, { capture: true });
    window.addEventListener('click', onTrustedClick, { capture: true });

    const attempt = () => {
      if (userTouched) { stop('stopped', state.reason || 'user_takeover'); return; }
      if (Date.now() > deadline) { stop('gave_up', 'deadline'); return; }
      if (attempts >= MAX_ATTEMPTS) { stop('gave_up', 'attempts_limit'); return; }
      if (errorSeen >= 2) { stop('gave_up', 'errors_limit'); return; }

      // 用户编辑检测补充通道（覆盖 srcdoc iframe 内无事件通道的场景）
      const uField = findUsername(document);
      if (uField && uField.value && uField.value !== credentials.username) {
        userTouched = true; stop('stopped', 'user_edit'); return;
      }

      const btn = findSubmit(document);
      if (!btn) {
        // 登录成功跳转：提交按钮消失（连续两轮 800ms 检测不到）
        if (attempts > 0 && ++absentStreak >= 2) { stop('success', 'submit_gone'); return; }
        return;  // attempts === 0：页面未就绪，只等待不计数
      }
      if (btn.disabled) return;  // 表单校验未过

      // ⑤ 点击后观察期内不点击，只监测错误元素
      if (attempts > 0 && Date.now() - lastClickAt < OBSERVE_WINDOW) {
        const errs = countErrors(document);
        if (!errorFlagged && errs > errorsAtLastClick) {
          errorSeen += 1; errorFlagged = true;
          state.errors = errorSeen; touch();
          if (errorSeen >= 2) { stop('gave_up', 'errors_limit'); return; }
        }
        return;
      }
      errorFlagged = false;

      // ① 齐备门槛：不齐备绝不提交
      if (!fillAll(document, credentials)) return;

      // ② 提交前回读：500ms 后复核，受控状态未落地则本轮推迟
      attempts += 1;
      state.attempts = attempts; touch();
      lastClickAt = Date.now();
      errorsAtLastClick = countErrors(document);
      const btnAtClick = btn;
      setTimeout(() => {
        if (userTouched) return;
        if (!fillAll(document, credentials)) return;  // 重渲染清值 → 等下轮重填
        const btn2 = findSubmit(document) || btnAtClick;
        const u2 = findUsername(document);
        const pVal = readPasswordValue(document);
        if (u2 && u2.value === credentials.username && pVal === credentials.password) {
          btn2.click();
        }
      }, CLICK_DELAY);
    };

    // ③ 即时填充：MutationObserver + load → 100ms 去抖；800ms 轮询兜底
    const scheduleAttempt = () => {
      if (pending) return;
      pending = true;
      setTimeout(() => { pending = false; attempt(); }, 100);
    };
    observer = new MutationObserver(scheduleAttempt);
    observer.observe(document.documentElement, { childList: true, subtree: true });
    window.addEventListener('load', scheduleAttempt, true);
    timer = setInterval(attempt, 800);
    setPhase('running', '');
    attempt();
    return true;
  }

  window.__aksoStartAutoLogin = (username, password) => {
    if (state.phase === 'running') return false;
    if (!username || !password) return false;
    state.phase = 'running';
    state.reason = '';
    state.attempts = 0;
    state.errors = 0;
    state.userTouched = false;
    touch();
    return runTopFrameFlow({ username, password });
  };
  window.__aksoAutoLoginState = state;
})();
"""


# ---------------------------------------------------------------- Python 驱动层


def install(page: Any) -> None:
    """把引擎注入页面（MAIN world，含子 frame 场景由 init script 覆盖）。"""
    page.add_init_script(AUTOLOGIN_JS)


def start(page: Any, username: str, password: str) -> bool:
    """在主 frame 启动自动登录状态机。"""
    return bool(page.evaluate("([u, p]) => window.__aksoStartAutoLogin(u, p)", [username, password]))


def status(page: Any) -> dict[str, Any] | None:
    """读取引擎状态（页面跳转中/已销毁返回 None）。"""
    try:
        raw = page.evaluate(
            "() => window.__aksoAutoLoginState"
            " ? JSON.parse(JSON.stringify(window.__aksoAutoLoginState)) : null"
        )
        return raw if isinstance(raw, dict) else None
    except Exception:  # noqa: BLE001 —— 导航/关闭期间的求值失败一律视为不可读
        return None


def wait_terminal(
    page: Any,
    timeout_s: float = 45.0,
    poll_s: float = 0.3,
    on_tick: Callable[[dict[str, Any] | None], None] | None = None,
) -> dict[str, Any]:
    """轮询直到引擎到达终态（success/stopped/gave_up）或超时。

    返回最终状态 dict；超时抛 TimeoutError。页面导航期间的求值失败按"未终态"继续等。
    """
    terminal = {"success", "stopped", "gave_up"}
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        last = status(page)
        if on_tick is not None:
            try:
                on_tick(last)
            except Exception:  # noqa: BLE001 —— 观察回调永不拖垮等待
                pass
        if last and last.get("phase") in terminal:
            return last
        time.sleep(poll_s)
    raise TimeoutError(f"自动登录 {timeout_s:.0f}s 未达终态（最后状态：{last}）")


# ---------------------------------------------------------------- token 捕获

JWT_SHAPE = re.compile(r"^[\w-]+\.[\w-]+\.[\w-]*$")


@dataclass
class TokenCapture:
    """captureToken 简化版：按账号捕获 JWT 形状的 Bearer token。

    原实现的异账号护栏/身份叛逃处置/Cookie 袋/DNR 回放均为扩展专属，不迁
    （Playwright context 原生隔离）。捕获结果仅存内存，进程结束即失。
    """

    account_id: str
    tokens: dict[str, dict[str, Any]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def attach(self, context: Any) -> None:
        context.on("response", self._on_response)

    def _on_response(self, response: Any) -> None:
        try:
            auth = (response.request.headers or {}).get("authorization", "")
        except Exception:  # noqa: BLE001
            return
        value = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if value and JWT_SHAPE.match(value):
            with self._lock:
                self.tokens[value] = {
                    "url": getattr(response, "url", ""),
                    "captured_at": now_ms_s(),
                }
                # 只保留最近 8 个，防内存膨胀
                if len(self.tokens) > 8:
                    for key in sorted(self.tokens, key=lambda k: self.tokens[k]["captured_at"])[:-8]:
                        self.tokens.pop(key, None)

    def latest(self) -> str | None:
        with self._lock:
            if not self.tokens:
                return None
            return max(self.tokens, key=lambda k: self.tokens[k]["captured_at"])


def now_ms_s() -> int:
    return int(time.time() * 1000)
