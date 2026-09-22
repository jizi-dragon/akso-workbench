"""gws 适配层的单测 —— **用真子进程跑一个假 CLI**。

## 为什么不打桩 `subprocess`

本模块最该被验的三件事，全都长在"真的起了一个子进程"这件事上：

1. **凭据不经 argv**（argv 会被别的进程看见）——不打桩才看得见真 argv；
2. **环境走白名单、不全量继承**——不打桩才看得见子的环境块；
3. **stdin 必须 DEVNULL**（否则 CLI 未配置时会交互式提问，把调用方挂满超时）
   ——这条**只有真进程**才验得出来。

所以：`GwsClient(argv_prefix=[sys.executable, <假 CLI>])`，其余一切都是真的。
`argv_prefix` 这个参数存在的**唯一**理由就是它（见 `client.py` 的注释）。
"""

from __future__ import annotations

import json
import subprocess  # noqa: F401 —— 用于类型标注可读性，实际调用都在 client 内
import sys
from pathlib import Path

import pytest

from workbench.services.gws import (
    GwsBinaryNotFound,
    GwsClient,
    GwsConfig,
    GwsError,
    GwsNotConfigured,
    GwsPlatformError,
    GwsProcessError,
    GwsTimeout,
    resolve_binary,
)
from workbench.services.gws.client import _ENV_ALLOWLIST, OP_CREATE, OP_OVERWRITE

FAKE_CLI = '''\
"""假 gws CLI：行为由同目录的 scenario.json 决定，每次调用追加一行 invocations.jsonl。

它故意**什么都不校验** —— 校验是被测对象（GwsClient）的责任。
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
scenario = json.loads((HERE / "scenario.json").read_text(encoding="utf-8"))

stdin_text = ""
try:
    stdin_text = sys.stdin.read()
except Exception as exc:  # noqa: BLE001
    stdin_text = f"<stdin 读取失败: {exc}>"

record = {
    "argv": sys.argv[1:],
    "env": dict(os.environ),
    "stdin": stdin_text,
    "cwd": os.getcwd(),
}
with (HERE / "invocations.jsonl").open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(record, ensure_ascii=False) + "\\n")

mode = scenario.get("mode", "ok")
if mode == "timeout":
    import time
    time.sleep(scenario.get("sleep_s", 30))
if mode == "sleep_then_ok":
    import time
    time.sleep(scenario.get("sleep_s", 0.05))

payload = scenario.get("stdout", "")
sys.stdout.write(payload)
sys.stdout.flush()
sys.stderr.write(scenario.get("stderr", ""))
sys.stderr.flush()
sys.exit(int(scenario.get("exit", 0)))
'''


@pytest.fixture
def fake(tmp_path: Path):
    """建一个假 CLI 目录，返回 (client 工厂, 读记录的函数, 写场景的函数)。"""
    script = tmp_path / "fake_gws.py"
    script.write_text(FAKE_CLI, encoding="utf-8", newline="\n")

    def write_scenario(**kw) -> None:
        (tmp_path / "scenario.json").write_text(
            json.dumps(kw, ensure_ascii=False), encoding="utf-8", newline="\n"
        )

    def records() -> list[dict]:
        path = tmp_path / "invocations.jsonl"
        if not path.exists():
            return []
        return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def make(cfg: GwsConfig | None = None) -> GwsClient:
        return GwsClient(cfg or GwsConfig(), argv_prefix=[sys.executable, str(script)])

    write_scenario(mode="ok", stdout='{"code":0,"data":null}')
    return make, records, write_scenario


# ---------------------------------------------------------------- 只读闸门


def test_readonly_gate_rejects_write_verbs(fake):
    """写动词必须被拒，**且一个进程都不许起**。"""
    make, records, _ = fake
    client = make()
    with pytest.raises(GwsError) as exc:
        client.run_readonly("component", "instance", "save", "object")
    assert "非只读" in str(exc.value)
    assert records() == [], "被拒的写命令竟然真的起了一个进程"


@pytest.mark.parametrize(
    "verb",
    [
        ("component", "metadata", "list"),
        ("component", "instance", "show", "layout", "layout.x__ak"),
        ("metadata", "object", "query", "用户"),
        ("aql", "query", "select id from User where id='1'"),
        ("config", "show"),
    ],
)
def test_readonly_gate_allows_known_read_verbs(fake, verb):
    make, _, _ = fake
    make().run_readonly(*verb)


def test_readonly_gate_is_tuple_prefix_not_substring(fake):
    """`component instance listx` 不该因为"长得像 list"而被放行。

    元组**逐元素**比较，不是子串匹配 —— 与本仓「沙箱策略名必须用 ASCII 正则，
    不能用 `str.isalnum()`」是同一种谨慎（PITFALLS #40 同族）。
    """
    make, records, _ = fake
    with pytest.raises(GwsError, match="非只读"):
        make().run_readonly("component", "instance", "listx")
    assert records() == []


def test_readonly_gate_allows_verb_when_a_later_flag_looks_like_a_verb(fake):
    """`--data` 之后的字符串不是动词，不能因为里面有 `save` 就被拒。"""
    make, _, _ = fake
    make().run_readonly("component", "instance", "query", "--data", '{"component_type":"save"}')


# ---------------------------------------------------------------- 信封与错误分层


def test_envelope_success_returns_data(fake):
    make, _, scenario = fake
    scenario(mode="ok", stdout='{"code":0,"message":null,"data":[{"id":"a"}]}')
    assert make().run_readonly("config", "show") == [{"id": "a"}]


def test_envelope_nonzero_code_is_a_platform_error(fake):
    """业务失败也是 HTTP 200 —— **唯一的成败信号在 code 里**（与工厂 egmp.client 同族）。"""
    make, _, scenario = fake
    scenario(mode="ok", stdout='{"code":500,"message":"数据不存在"}')
    with pytest.raises(GwsPlatformError) as exc:
        make().run_readonly("config", "show")
    assert exc.value.code == 500
    assert "数据不存在" in str(exc.value)


def test_process_exit_zero_does_not_mean_success(fake):
    """反证：进程 exit=0 但信封 code=500 ⇒ 仍然失败。

    没有这一条，"拿 returncode 当成败"这个错会一路活到真机上。
    """
    make, _, scenario = fake
    scenario(mode="ok", exit=0, stdout='{"code":500,"message":"组件[listlayout]的Code字段不能为空"}')
    with pytest.raises(GwsPlatformError):
        make().run_readonly("config", "show")


def test_unconfigured_is_its_own_error_class(fake):
    """未配置 → `GwsNotConfigured`（要引导去配置），**不是**通用的进程错误。

    实测形态：gws 打印 `━━━━ 需要配置服务地址 ━━━━` + `✗ 服务地址不能为空`，exit=1。
    """
    make, _, scenario = fake
    scenario(
        mode="ok", exit=1,
        stderr="━━━━ 需要配置服务地址 ━━━━━━━━━━━━━━━━━━━━━━━━━━━\n✗ 服务地址不能为空\n",
    )
    with pytest.raises(GwsNotConfigured) as exc:
        make().run_readonly("component", "metadata", "list")
    assert "service" in str(exc.value)


def test_nonzero_exit_without_config_hint_is_a_process_error(fake):
    make, _, scenario = fake
    scenario(mode="ok", exit=2, stderr="Error: unknown command")
    with pytest.raises(GwsProcessError) as exc:
        make().run_readonly("config", "show")
    assert exc.value.code == 2
    assert "unknown command" in exc.value.stderr_tail


def test_non_json_output_is_reported_as_such(fake):
    make, _, scenario = fake
    scenario(mode="ok", stdout="这不是 JSON")
    with pytest.raises(GwsProcessError, match="不是 JSON"):
        make().run_readonly("config", "show")


def test_timeout_maps_to_gws_timeout(fake):
    make, _, scenario = fake
    scenario(mode="timeout", sleep_s=30)
    client = make(GwsConfig(timeout_s=0.6))
    with pytest.raises(GwsTimeout):
        client.run_readonly("config", "show")


def test_expect_json_false_returns_raw_text(fake):
    make, _, scenario = fake
    scenario(mode="ok", stdout="gws v1.0.0\n")
    assert make().run_readonly("version", expect_json=False) == "gws v1.0.0\n"


# ---------------------------------------------------------------- 四条硬约束


def test_secret_never_appears_in_argv(fake):
    """约束 ①：凭据绝不经 argv（argv 对同机所有用户可见）。"""
    make, records, _ = fake
    secret = "s3cr3t-value-should-not-be-in-argv"
    client = make(GwsConfig(service="https://x", app_id="app-1", app_secret=secret))
    client.run_readonly("config", "show")
    argv = records()[0]["argv"]
    assert secret not in " ".join(argv)
    assert "app-1" not in " ".join(argv)
    assert "--secret-key" not in argv and "--app-id" not in argv


def test_credentials_do_go_through_env(fake):
    make, records, _ = fake
    client = make(GwsConfig(service="https://x", app_id="app-1", app_secret="sec-1"))
    client.run_readonly("config", "show")
    env = records()[0]["env"]
    assert env["APP_ID"] == "app-1"
    assert env["APP_SECRET"] == "sec-1"
    assert env["HOST"] == "https://x"
    assert env["SILENT"] == "true"


def test_env_is_allowlisted_never_inherited_wholesale(fake, monkeypatch):
    """约束 ②：宿主环境**不全量继承**。

    这条不是教条：gws 认的环境变量名太通用（`HOST` / `TIMEOUT` / `APP_ID` / `OUTPUT`），
    全量继承时任何一个恰好叫 `HOST` 的宿主变量都会把**别的服务的地址**喂给它，
    而表现是"连到了不相干的平台"或"连不通"——**不报错**。
    """
    make, records, _ = fake
    monkeypatch.setenv("SECRET_CANARY", "should-not-leak-into-child")
    monkeypatch.setenv("HOST", "https://wrong-platform.example")
    make(GwsConfig(service="https://right.example")).run_readonly("config", "show")
    env = records()[0]["env"]
    assert "SECRET_CANARY" not in env
    assert env["HOST"] == "https://right.example", "宿主的 HOST 串味进来了"


def test_child_env_has_nothing_outside_the_allowlist(fake, monkeypatch):
    make, records, _ = fake
    monkeypatch.setenv("TOTALLY_UNRELATED_VAR", "x")
    make().run_readonly("config", "show")
    env = records()[0]["env"]
    allowed = {k.upper() for k in _ENV_ALLOWLIST} | {
        "PYTHONUTF8", "PYTHONIOENCODING", "SILENT", "OUTPUT", "TIMEOUT",
        "HOST", "APP_ID", "APP_SECRET",
    }
    # Windows 的环境变量名**大小写不敏感**（`windir` 与 `WINDIR` 是同一个），
    # 所以两边都归一化再比；用大小写敏感的比较会得到一个假的"漏了"。
    unexpected = {k for k in env if not k.startswith("=") and k.upper() not in allowed}
    assert not unexpected, f"环境白名单漏了：{sorted(unexpected)}"


def test_secret_is_scrubbed_from_error_surfaces(fake):
    """约束 ①的兜底：CLI 把凭据回显出来时，**我们这边不能把它带出去**。"""
    make, _, scenario = fake
    secret = "leaked-secret-abcdef"
    scenario(mode="ok", exit=3, stderr=f"boom APP_SECRET={secret} boom")
    client = make(GwsConfig(app_secret=secret))
    with pytest.raises(GwsProcessError) as exc:
        client.run_readonly("config", "show")
    blob = str(exc.value) + exc.value.stderr_tail
    assert secret not in blob
    assert "***" in exc.value.stderr_tail


def test_stdin_is_devnull_so_the_cli_cannot_prompt(fake):
    """约束 ③：**实测**——未配置时 gws 会交互式提问「服务地址: 」，把调用方挂满超时。

    判据落在"子进程读到的 stdin 是 EOF"上：给了管道且不关，这里读到的会是空串但**永不返回**；
    `DEVNULL` 下 `read()` 立刻返回空串且进程正常收尾。
    """
    make, records, _ = fake
    make().run_readonly("config", "show")
    assert records()[0]["stdin"] == ""


# ---------------------------------------------------------------- 写入的显式意图


def test_component_instance_save_requires_explicit_operation_type(fake):
    """约束 ④：CLI 默认 `--operation-type 30`（**覆盖**），我们**不接受默认值**。"""
    make, records, _ = fake
    with pytest.raises(TypeError):
        make().component_instance_save("object", [{"code": "aa__c"}])  # type: ignore[call-arg]
    assert records() == []


def test_component_instance_save_rejects_unknown_operation_type(fake):
    make, records, _ = fake
    with pytest.raises(GwsError, match="operation_type"):
        make().component_instance_save("object", [{"code": "aa__c"}], operation_type=99)
    assert records() == []


def test_component_instance_save_rejects_empty_instances(fake):
    """CLI 自己不校验，我们替它校验。"""
    make, records, _ = fake
    with pytest.raises(GwsError, match="非空数组"):
        make().component_instance_save("object", [], operation_type=OP_CREATE)
    assert records() == []


@pytest.mark.parametrize("op", [OP_CREATE, OP_OVERWRITE])
def test_component_instance_save_passes_the_declared_operation_type(fake, op):
    make, records, _ = fake
    make().component_instance_save("object", [{"code": "aa__c", "name": "aa"}], operation_type=op)
    argv = records()[0]["argv"]
    assert argv[argv.index("--operation-type") + 1] == str(op)
    assert "component" in argv and "save" in argv


# ---------------------------------------------------------------- 二进制定位


def test_resolve_binary_honours_explicit_path(tmp_path: Path):
    fake_exe = tmp_path / "gws.exe"
    fake_exe.write_bytes(b"MZ")
    assert resolve_binary(fake_exe) == fake_exe


def test_resolve_binary_explicit_missing_raises(tmp_path: Path):
    with pytest.raises(GwsBinaryNotFound, match="不存在"):
        resolve_binary(tmp_path / "nope.exe")


def test_resolve_binary_honours_env_override(tmp_path: Path, monkeypatch):
    fake_exe = tmp_path / "gws.exe"
    fake_exe.write_bytes(b"MZ")
    monkeypatch.setenv("WORKBENCH_GWS_BIN", str(fake_exe))
    assert resolve_binary() == fake_exe


def test_resolve_binary_missing_gives_actionable_hint(monkeypatch, tmp_path: Path):
    """找不到时必须是**一句能照做的话**（三条处置），而不是一句"没找到"。"""
    monkeypatch.delenv("WORKBENCH_GWS_BIN", raising=False)
    monkeypatch.setenv("WORKBENCH_DATA", str(tmp_path / "empty-data"))
    monkeypatch.setattr("shutil.which", lambda _name: None)
    from workbench import config

    assert not (config.RUNTIME_DIR / "gws").exists()
    with pytest.raises(GwsBinaryNotFound) as exc:
        resolve_binary()
    text = str(exc.value)
    assert "WORKBENCH_GWS_BIN" in text and "runtime" in text


# ---------------------------------------------------------------- 只读便捷封装


def test_component_types_reads_the_type_table(fake):
    make, records, scenario = fake
    scenario(mode="ok", stdout=json.dumps({
        "code": 0,
        "data": [{"component_type": "object", "name_cn": "对象", "sort": 700, "parent_code": None}],
    }, ensure_ascii=False))
    types = make().component_types()
    assert types[0]["component_type"] == "object"
    assert records()[0]["argv"] == ["-s", "component", "metadata", "list"]


def test_component_instance_uses_full_path_addressing(fake):
    """实例用 `full_path` 寻址（`<类型>.<code>`），这是 CLI 的既定约定。"""
    make, records, scenario = fake
    scenario(mode="ok", stdout='{"code":0,"data":{"code":"aa__c","full_path":"object.aa__c"}}')
    inst = make().component_instance("object", "object.aa__c")
    assert inst["full_path"] == "object.aa__c"
    assert records()[0]["argv"] == ["-s", "component", "instance", "show", "object", "object.aa__c"]


def test_every_run_forces_silent_json_mode(fake):
    """`-s` 必须由我们强制注入：不加它 CLI 会输出人类可读的表格，解析必炸。"""
    make, records, _ = fake
    make().run_readonly("config", "show")
    assert records()[0]["argv"][0] == "-s"
