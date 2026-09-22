"""路径与运行配置（fork 自 mogul.config，改名 + 兼容接管）。

数据目录：优先环境变量 WORKBENCH_DATA（兼容 MOGUL_DATA）；
默认 %APPDATA%/Workbench（Windows）或 ~/.workbench。
首次运行时若本机存在**更名前**的数据目录（%APPDATA%/AksoWorkbench，2026-09-16 之前）
或旧版 mogul（Tauri / Python 版）的库，自动接管（复制）为初始数据库——
历史会话 / 账号 / 密钥全部保留。**接管只在目标库不存在时发生，绝不覆盖现有数据。**
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "Workbench"
LEGACY_APP_NAME = "AksoWorkbench"  # 2026-09-16 更名前的自有数据目录（自动接管，见 docs/adr/0013）
LEGACY_IDENTIFIER = "com.example.mogulsimulator"  # 旧 Tauri 版
LEGACY_PY_NAME = "MogulWorkbench"  # 旧 Python 版（mogul_simulator）


def app_data_dir() -> Path:
    env = os.environ.get("WORKBENCH_DATA") or os.environ.get("MOGUL_DATA")
    if env:
        return Path(env)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_NAME
    return Path.home() / ".workbench"


DATA_DIR = app_data_dir()
DB_PATH = Path(
    os.environ.get("WORKBENCH_DB")
    or os.environ.get("MOGUL_DB")
    or (DATA_DIR / "workbench.db")
)


def legacy_db_candidates() -> list[Path]:
    """旧数据库的可能位置，**按「越可能是真数据」排序**（先命中者胜）。

    1. `%APPDATA%/AksoWorkbench/workbench.db` —— **本工作台更名前**（2026-09-16 之前）的数据目录。
       里面有账号库 + Fernet 密钥，**不接管就等于让用户看到「账号全没了」**（见 docs/adr/0013）。
    2. 更早的两版 mogul（Python 版 / Tauri 版）。
    """
    candidates: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / LEGACY_APP_NAME / "workbench.db")
        candidates.append(Path(appdata) / LEGACY_PY_NAME / "mogul.db")
        candidates.append(Path(appdata) / LEGACY_IDENTIFIER / "mogul.db")
    candidates.append(Path.home() / LEGACY_IDENTIFIER / "mogul.db")
    return candidates


def bootstrap_database() -> Path:
    """确保数据库就绪：优先复用旧库数据。返回最终 DB 路径。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        for legacy in legacy_db_candidates():
            if legacy.exists():
                shutil.copy2(legacy, DB_PATH)
                for suffix in ("-wal", "-shm"):
                    side = legacy.with_name(legacy.name + suffix)
                    if side.exists():
                        shutil.copy2(side, DB_PATH.with_name(DB_PATH.name + suffix))
                break
    return DB_PATH


HOST = os.environ.get("WORKBENCH_HOST") or os.environ.get("MOGUL_HOST", "127.0.0.1")
PORT = int(os.environ.get("WORKBENCH_PORT") or os.environ.get("MOGUL_PORT", "18765"))
# 壳的另一半端口（见 docs/adr/0011）：browser_pool 也要用，故在此单点声明，
# 免得再出现「同一个数字在两个模块各写一遍」——这正是 ADR-0011 要消灭的东西。
CDP_PORT = int(os.environ.get("WORKBENCH_CDP_PORT", "18766"))
CONTROL_PORT = int(os.environ.get("WORKBENCH_CONTROL_PORT", "18767"))

# 壳公布的端口文件（ADR-0011 §D-3）。壳是仲裁者，它把**实际选中**的端口写在这里；
# 服务端只读它用于自证身份（/api/health），不拿它当监听依据（监听端口由壳下发）。
# 路径规则必须与 desktop/shell-state.js 的 DATA_DIR 完全一致，否则会读到另一个目录。
# 注意：具体的 DESKTOP_PORTS_PATH 在下方 RUNTIME_DIR 定义之后才算出来。
DESKTOP_PORTS_NAME = "desktop-ports.json"

# 打包态（PyInstaller）：资源解包到 sys._MEIPASS
FROZEN = getattr(sys, "frozen", False)
_BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))

# 项目根（workbench/ 的上一级）：adapters/、tools/、临时产物目录的锚点
# 打包态下 adapters 作为数据文件打进 _MEIPASS/adapters
PROJECT_ROOT = _BUNDLE_DIR if FROZEN else Path(__file__).resolve().parent.parent
ADAPTERS_DIR = Path(os.environ.get("WORKBENCH_ADAPTERS") or (PROJECT_ROOT / "adapters"))
RUNTIME_DIR = DATA_DIR / "runtime"  # 子进程日志 / 蓝图暂存 / env 导出
DESKTOP_PORTS_PATH = RUNTIME_DIR / DESKTOP_PORTS_NAME  # 壳公布的实际端口（ADR-0011 §D-3）

# ---------------------------------------------------------------- 附件（用户交给 agent 的文件）
#
# 2026-09-22，用户下达「核心 agent 能力增强：用户会直接传图片或文件（word/pdf/markdown/excel）」。
#
# 为什么落在 runtime/ 而不是让用户挑目录：**用户不提供路径**。上传接口只收字节流，
# 落盘名由服务端生成（`<id>__<slug(原名)>`），路径永远在 ATTACHMENTS_DIR 之内——
# 这条与沙箱、只读工具用的是同一套"路径不信任"口径（config.is_sensitive_path 的同族）。
ATTACHMENTS_DIR = RUNTIME_DIR / "attachments"
ATTACHMENTS_MAX_BYTES = 20 * 1024 * 1024
"""单个文件上限 20 MB：上传体验与解析耗时的折中（更大的建议切分或转 CSV）。"""
ATTACHMENTS_MAX_PER_TURN = 5
"""一轮最多带几个：再多模型也读不过来，而每个都要占上下文。"""
ATTACHMENTS_MAX_TEXT_CHARS = 200_000
"""解析出的正文硬上限（防"一份 500 页 PDF 把内存和上下文一起吃掉"）。"""
ATTACHMENTS_INLINE_CHARS = 12_000
"""进上下文的**内联**预算：超过它的部分不塞，改成"摘要 + 定位目录 + 用工具按需取"。"""

STATIC_DIR = Path(__file__).parent / "static"


# ---------------------------------------------------------------- 端口公布文件


def read_desktop_ports() -> dict | None:
    """读壳公布的端口文件；不存在或不可解析返回 None。

    为什么服务端也要读它：`/api/health` 要回答「我是谁、我在哪个口、同一批的另两个口在哪」，
    这样壳的就绪探测才能从「有人应答」升级为「是我们的服务在应答」（ADR-0011 §D-4）。
    读不到不算错误——开发者手起 `python -m workbench.main` 时本来就没有壳。
    """
    import json

    try:
        data = json.loads(DESKTOP_PORTS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def describe_service() -> dict:
    """本服务端的身份描述（`/api/health` 与 `/whoami` 共用的同一份口径）。

    `ports` 以壳公布的**实际值**为准、缺失时回落到本进程的环境变量/默认值——
    两者不一致本身就是个 bug（壳必须下发端口，见 ADR-0011 §D-2），故把出处一并报出来。
    """
    ports = {"server": PORT, "cdp": CDP_PORT, "control": CONTROL_PORT}
    published = read_desktop_ports() or {}
    published_ports = published.get("ports") or {}
    source = "default"
    if isinstance(published_ports, dict) and published_ports:
        source = "desktop-ports.json"
        for key in ports:
            value = published_ports.get(key)
            if isinstance(value, int) and value > 0:
                ports[key] = value
    if os.environ.get("WORKBENCH_PORT"):
        source = "env"
        ports["server"] = PORT
    return {
        "ports": ports,
        "portsSource": source,
        "shell": {
            "pid": published.get("pid"),
            "version": published.get("version"),
            "startedAt": published.get("startedAt"),
        } if published else None,
    }


# ---------------------------------------------------------------- 敏感路径（唯一事实源）


#: 命中即拒的路径片段（大小写不敏感、按**成分包含**判定，因此子路径同样敏感）。
#:
#: ## 为什么必须是**一张**表
#:
#: 2026-09-20 实测发现**两张表并存**：`sandbox/policy.py` 有一张（含 `browser-states` /
#: `egmp-tokens` / `workbench.db` / `accounts.json`），`plugins/builtins.py` 另有一张
#: （只有 Chrome/Edge/Firefox profile 那类**应用之外**的路径）。
#:
#: 后果是可复现的：只读工具 `read_local_file`（`risk=read`，**不过确认门**）能**完整读出**
#: `runtime/browser-states/*.json`（cookie + 登录态）、`runtime/egmp-tokens/*.json`（平台 JWT）、
#: 以及账号备份（**含 Fernet 密钥与全部密文口令**）——而**同一个路径沙箱是拦的**。
#:
#: 这与 2026-09-15「两套工具表并存」是**同一类错误**：两个会各自漂移的事实源。
#: ⇒ 收成这一张，两个消费者都从这里读；`tests/test_sensitive_paths.py` 有一条断言钉住
#: "两边拿到的是**同一个对象**"（不是"内容相等"，那样还是会漂）。
#:
#: ## 为什么是并集（而不是各留一份）
#:
#: 读工具与沙箱 `cwd` 的威胁模型不同，但**并集对两者都安全**：
#: 沙箱只是不能用这些路径当工作目录——而那本来就该拒。
#:
#: ## 判定是"宁可误拒"
#:
#: 这些是"一句话就能读走凭据"的位置，**没有"用户其实想看它"的合理场景**；
#: 真要看请用户自己打开。误拒的代价是让用户换个文件，漏放的代价是全量凭据外泄。
SENSITIVE_PATH_PARTS: tuple[str, ...] = (
    # 本应用自己的凭据产物（**最关键的一批**：它们就在 DATA_DIR 里）
    "workbench.db", "workbench.db-wal", "workbench.db-shm",
    "browser-states", "egmp-tokens", "pending_action", "shell-state.json",
    "accounts.json", "desktop-ports.json",
    # 通用凭据位置
    ".ssh", "id_rsa", "id_ed25519", "known_hosts",
    ".aws", ".azure", ".gnupg", ".git-credentials",
    ".npmrc", ".pypirc", ".netrc",
    "credentials", "secrets", ".env",
    # 浏览器 profile 里的凭据文件
    "cookies", "login data", "web data", "key3.db", "key4.db", "logins.json",
    "google/chrome/user data", "microsoft/edge/user data", "mozilla/firefox/profiles",
    # 系统级凭据库
    "windows/system32/config",
)


def is_sensitive_path(path) -> bool:
    """路径是否命中敏感表（按成分包含判定，大小写不敏感，`\\` 与 `/` 等同）。

    **这是全项目唯一的判定口径**——沙箱与只读工具都调它，不各自实现一份。
    """
    parts = str(path).replace("\\", "/").lower()
    return any(part in parts for part in SENSITIVE_PATH_PARTS)
