import { CONTENT_MESSAGE } from '../shared/constants';

/**
 * Auto-Login：自动填充登录表单并提交。
 * 关键事实：密码框位于同源 `srcdoc` iframe 内（class="ant-input"），而 `<all_urls>` 的
 * content script 不会注入 `about:srcdoc` frame，因此不能用「iframe 各填各」模型——
 * 必须由**顶层 frame 直接访问 iframe.contentDocument 填密码**。
 * 顶层 frame：填用户名 + 访问 srcdoc iframe 填密码 + 勾选协议 + 提交登录。
 * 子 frame（若注入，如跨域外链 iframe）：仅兜底填自身 frame 内的密码。
 */
let credentials: { username: string; password: string } | null = null;

function isTopFrame(): boolean {
  return window === window.top;
}

/** 写入值并触发原生 setter + input/change 事件（兼容 React/Ant Design 受控组件）。
 * 密码框位于 iframe 内，需用该 iframe 自身 realm 的构造器触发，跨 realm 才可靠。 */
function setValue(el: HTMLInputElement, value: string, win: Window): void {
  const realm = win as unknown as {
    HTMLInputElement: typeof HTMLInputElement;
    Event: typeof Event;
  };
  const setter = Object.getOwnPropertyDescriptor(realm.HTMLInputElement.prototype, 'value')?.set;
  if (setter) {
    setter.call(el, value);
  } else {
    el.value = value;
  }
  el.dispatchEvent(new realm.Event('input', { bubbles: true }));
  el.dispatchEvent(new realm.Event('change', { bubbles: true }));
}

function fillUsername(): boolean {
  const field =
    document.querySelector<HTMLInputElement>('input[placeholder="请输入用户名"]') ||
    document.querySelector<HTMLInputElement>('input[type="text"]');
  if (field && credentials && field.value !== credentials.username) {
    setValue(field, credentials.username, window);
    return true;
  }
  return Boolean(field);
}

/** 顶层直接访问 srcdoc/同源 iframe，把密码填进 iframe 内的 password 输入框 */
function fillPasswordInIframes(): boolean {
  const iframes = document.querySelectorAll<HTMLIFrameElement>('iframe');
  for (const iframe of Array.from(iframes)) {
    try {
      const doc = iframe.contentDocument;
      const win = iframe.contentWindow;
      if (!doc || !win) {
        continue;
      }
      const field =
        doc.querySelector<HTMLInputElement>('input[type="password"]') ||
        doc.querySelector<HTMLInputElement>('input[placeholder*="密码"]') ||
        doc.querySelector<HTMLInputElement>('input[placeholder*="password"]');
      if (field && credentials && field.value !== credentials.password) {
        // 值为空/被框架重渲染清掉时才回填（防御 srcdoc 重渲染清值）
        setValue(field, credentials.password, win);
      }
      return Boolean(field);
    } catch {
      // 跨域 iframe 无法从顶层访问；由该 frame 自身注入的 content script 兜底填充
    }
  }
  return false;
}

function checkAgreement(): void {
  const box =
    (document.getElementById('privacyChecked') as HTMLInputElement | null) ||
    Array.from(document.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')).find((el) =>
      /同意|agree/i.test(`${el.name || ''} ${el.ariaLabel || ''} ${el.labels?.[0]?.textContent || ''}`),
    );
  if (box && !box.checked) {
    box.click();
  }
}

function findSubmit(): HTMLButtonElement | null {
  const buttons = Array.from(document.querySelectorAll<HTMLButtonElement>('button'));
  return buttons.find((b) => /登录|登\s*录|login|submit/i.test(b.textContent || '')) || null;
}

/** 顶层：填用户名 + 密码（iframe）+ 勾选协议；自愈式提交（v3.10.7 节奏门控）。
 *  提交五重门控：①字段齐备（任一填充失败绝不点击——密码 iframe 未就绪时空密码提交
 *  即「密码错误」报错的根因）；②提交前回读凭据与 DOM 值一致（受控组件状态落地）；
 *  ③按钮可用；④用户未接管（trusted 键入凭据框 / trusted 点击按钮都算接管）；
 *  ⑤服务端未连续拒绝（点击后观察期出现 antd 风格错误提示 → 容忍一次重填重试，
 *  再错即停止让位——盲目重试会与用户点击竞争并可能触发验证码/锁定）。
 *  节奏：MutationObserver + iframe load 即时触发 attempt（密码框挂载即填，压缩
 *  用户手动点击撞上未填充状态的窗口），800ms 轮询兜底。 */
function runTopFrameFlow(): void {
  const deadline = Date.now() + 30_000;
  let attempts = 0;
  let lastClickAt = 0;
  let userTouched = false;
  let absentStreak = 0;
  let errorSeen = 0; // 提交后观察期内的服务端拒绝次数（错误提示出现）
  let errorFlagged = false; // 本次点击观察期是否已标记过错误（防重复计数）
  let errorsAtLastClick = 0;

  /** 用户已改写字段值（trusted 键入或手动清空）= 接管；预期值非空才可比对 */
  const detectUserEdit = (el: HTMLInputElement | null, expected: string): void => {
    if (el && expected && el.value && el.value !== expected) {
      userTouched = true;
    }
  };

  // 用户手动键入凭据字段 = 接管（勾选框/协议等其它 input 不算）
  window.addEventListener(
    'input',
    (e) => {
      const t = e.target as HTMLInputElement | null;
      if (e.isTrusted && t instanceof HTMLInputElement && (t.type === 'text' || t.type === 'password' || t.type === '')) {
        userTouched = true;
        console.debug('[ql-auto] user edits credentials field, stand down');
      }
    },
    { capture: true },
  );

  // 用户点击任何按钮 = 接管意图：自动流程立即停止（自动点击自身的 click 事件
  // isTrusted=false 不会触发；勾选协议同理由 checkAgreement 内部处理）
  window.addEventListener(
    'click',
    (e) => {
      const t = e.target as HTMLElement | null;
      if (e.isTrusted && t?.closest?.('button')) {
        userTouched = true;
        console.debug('[ql-auto] user clicks a button, stand down');
      }
    },
    { capture: true },
  );

  /** 错误提示元素计数（antd 系登录平台的失败反馈：message/alert/表单校验错误） */
  const countErrors = (): number =>
    document.querySelectorAll(
      '.ant-message-error, .ant-message-notice-error, .ant-alert-error, .ant-form-item-explain-error',
    ).length;

  /** 读回 iframe 内密码框当前值（提交前回读用） */
  const readPasswordValue = (): string | null => {
    for (const iframe of document.querySelectorAll<HTMLIFrameElement>('iframe')) {
      try {
        const p = iframe.contentDocument?.querySelector<HTMLInputElement>('input[type="password"]');
        if (p) {
          return p.value;
        }
      } catch {
        // 跨域 iframe 读不到
      }
    }
    return null;
  };

  /** 填充并校验齐备：用户名或密码任一字段未就绪 → false（本轮不进点击流程） */
  const fillAll = (): boolean => {
    const uOk = fillUsername();
    const pOk = fillPasswordInIframes();
    checkAgreement();
    return uOk && pOk;
  };

  const timer = window.setInterval(attempt, 800);

  function attempt(): void {
    if (userTouched || !credentials || Date.now() > deadline || attempts >= 4 || errorSeen >= 2) {
      window.clearInterval(timer);
      return;
    }
    const submit = findSubmit();
    if (!submit) {
      // 登录成功跳转后按钮消失；点击过至少一次即认为流程已交付
      if (attempts > 0 && ++absentStreak >= 2) {
        window.clearInterval(timer);
      }
      return;
    }
    absentStreak = 0;
    // 先检测用户是否已改写凭据（覆盖 srcdoc iframe 密码框等无事件通道的场景），再决定是否回填
    detectUserEdit(
      document.querySelector<HTMLInputElement>('input[placeholder="请输入用户名"]') ||
        document.querySelector<HTMLInputElement>('input[type="text"]'),
      credentials.username,
    );
    if (userTouched) {
      window.clearInterval(timer);
      return;
    }
    const now = Date.now();
    if (lastClickAt) {
      if (now - lastClickAt < 3500) {
        // 观察期：检测服务端拒绝反馈（错误提示出现）
        if (!errorFlagged && countErrors() > errorsAtLastClick) {
          errorFlagged = true;
          errorSeen++;
          console.debug('[ql-auto] submit rejected by server', { errorSeen });
          if (errorSeen >= 2) {
            window.clearInterval(timer);
            return;
          }
        }
        return; // 点击观察期：给登录请求时间，避免连点
      }
      errorFlagged = false; // 观察期结束
    }
    if ((submit as HTMLButtonElement).disabled) {
      return; // 表单校验未过（按钮禁用），等待
    }
    // 齐备门槛：任一字段未就绪（iframe 未挂载等）绝不点击——空密码提交即「密码错误」
    if (!fillAll()) {
      return;
    }
    attempts++;
    lastClickAt = now;
    errorsAtLastClick = countErrors();
    window.setTimeout(() => {
      if (userTouched) {
        return; // 点击落地前用户已接管
      }
      if (!fillAll()) {
        return; // 点击落地前字段被重渲染清掉/移除 → 放弃本轮，等下轮重填后再点
      }
      const btn = findSubmit();
      // 提交前回读：受控组件的 DOM 值必须与凭证一致（状态未落地则推迟）
      const u = document.querySelector<HTMLInputElement>('input[placeholder="请输入用户名"]') ||
        document.querySelector<HTMLInputElement>('input[type="text"]');
      if (!btn || !u || u.value !== credentials!.username || readPasswordValue() !== credentials!.password) {
        return;
      }
      btn.click();
    }, 500);
  }

  attempt();

  // 节奏加速：密码 iframe 挂载（元素插入 / srcdoc 文档 load 完成）即时触发填充，
  // 不等 800ms 轮询——压缩「表单可见但未填完」的窗口
  let pending = 0;
  const scheduleAttempt = (): void => {
    if (pending) {
      return;
    }
    pending = window.setTimeout(() => {
      pending = 0;
      attempt();
    }, 100);
  };
  const observer = new MutationObserver(scheduleAttempt);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener('load', scheduleAttempt, true); // iframe srcdoc 文档加载完成
}

/** 子 frame：仅兜底填自身 frame 内的密码（srcdoc iframe 不注入，此分支主要服务跨域外链 iframe） */
function runIframeFlow(): void {
  if (!credentials) {
    return;
  }
  const field =
    document.querySelector<HTMLInputElement>('input[type="password"]') ||
    document.querySelector<HTMLInputElement>('input[placeholder*="密码"]') ||
    document.querySelector<HTMLInputElement>('input[placeholder*="password"]');
  if (field) {
    setValue(field, credentials.password, window);
  }
}

function start(): void {
  if (!credentials) {
    return;
  }
  if (isTopFrame()) {
    runTopFrameFlow();
  } else {
    runIframeFlow();
  }
}

/** 就绪（document_idle）后主动向 background 索取本次自动登录凭证；失败则轻量重试 */
async function requestCredentials(attempt = 0): Promise<void> {
  if (credentials) {
    return;
  }
  try {
    const res = await chrome.runtime.sendMessage({ type: CONTENT_MESSAGE.autoLoginRequest });
    if (res && typeof res === 'object' && typeof res.username === 'string' && typeof res.password === 'string') {
      credentials = { username: res.username, password: res.password };
      start();
    }
  } catch {
    if (attempt < 3) {
      window.setTimeout(() => void requestCredentials(attempt + 1), 500);
    }
  }
}

chrome.runtime.onMessage.addListener((msg: unknown) => {
  if (
    msg &&
    typeof msg === 'object' &&
    (msg as { type?: string }).type === CONTENT_MESSAGE.autoLogin &&
    typeof (msg as { username?: string }).username === 'string' &&
    typeof (msg as { password?: string }).password === 'string'
  ) {
    credentials = {
      username: (msg as { username: string }).username,
      password: (msg as { password: string }).password,
    };
    start();
  }
});

void requestCredentials();