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
    MIGRATION_WRITE_VERBS,
    OP_CREATE,
    OP_OVERWRITE,
    READ_ONLY_VERBS,
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
from workbench.services.gws.client import _ENV_ALLOWLIST

FAKE_CLI = '''\
"""假 gws CLI：行为由同目录的 scenario.json 决定，每次调用追加一行 invocations.jsonl。

`scenario["routes"]` 可以按**命令行**分别给行为（键 = `-s` 之后的参数用空格连起来），
没命中就回落到顶层字段。这样"同一批调用里有的成功有的失败"才测得出来
（`discover_component_types` 就是那种形状）。
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

routes = scenario.get("routes") or {}
key = " ".join(sys.argv[2:]) if len(sys.argv) > 1 else ""
eff = routes.get(key, scenario)

mode = eff.get("mode", "ok")
if mode == "timeout":
    import time
    time.sleep(eff.get("sleep_s", 30))
if mode == "sleep_then_ok":
    import time
    time.sleep(eff.get("sleep_s", 0.05))

sys.stdout.write(eff.get("stdout", ""))
sys.stdout.flush()
sys.stderr.write(eff.get("stderr", ""))
sys.stderr.flush()
sys.exit(int(eff.get("exit", 0)))
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


@pytest.mark.parametrize(
    "payload",
    [
        # component instance show 的真实响应（2026-09-22 真机抓到）
        '{"id":"3a23","code":"change_hdh_mgt_base_layout__ak","name":"默认布局","status":1}',
        # 实例里的 code 恰好是数字 0 的情形（领域对象，不是信封）
        '{"id":"x","code":0,"name":"某个编码为 0 的对象"}',
        # 纯数组
        '[{"code":"a"}]',
    ],
)
def test_a_bare_object_with_its_own_code_field_is_not_an_envelope(fake, payload):
    """★ 回归：**裸实例自带 `code` 字段**，不许被误判成平台信封。

    2026-09-22 真机实测踩到：`component instance show` 回的实例里有
    `"code": "change_hdh_mgt_base_layout__ak"`（布局编码），而当时的 `_unwrap`
    只看"有没有 code 键" ⇒ 把一份**完全正常的响应**报成
    「平台拒绝：… code=change_hdh_mgt_base_layout__ak」。

    形态与本仓 PITFALLS 那一族一模一样：**不抛异常，看起来像"平台拒了"**——
    而真凶是适配层自己。
    """
    make, _, scenario = fake
    scenario(mode="ok", stdout=payload)
    data = make().run_readonly("config", "show")
    assert data is not None, "裸对象被当成信封拆掉了"


def test_an_envelope_with_only_a_nonzero_code_is_still_a_failure(fake):
    """fail-closed 方向：`{code: 500}` 没有 message/data，**仍然当失败抛**。

    宁可把"形状奇怪的错误"当失败，也不要把失败当数据静默交给调用方。
    """
    make, _, scenario = fake
    scenario(mode="ok", stdout='{"code":500}')
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


def test_config_from_env_reads_our_own_namespace(monkeypatch):
    """凭据走 `WORKBENCH_GWS_*`，**不读** gws 认的 `HOST`/`APP_ID`/`APP_SECRET`。

    理由就是约束 ②：`HOST` 这种名字太通用，宿主机上任何一个同名变量都会串味。
    这里连"我们确实没读它们"也一并钉住——否则哪天有人图省事改成读 `HOST`，
    行为会在**别人的机器上**才出问题。
    """
    monkeypatch.setenv("WORKBENCH_GWS_SERVICE", "https://right.example")
    monkeypatch.setenv("WORKBENCH_GWS_APP_ID", "our-id")
    monkeypatch.setenv("WORKBENCH_GWS_APP_SECRET", "our-secret")
    monkeypatch.setenv("HOST", "https://wrong.example")
    monkeypatch.setenv("APP_ID", "wrong-id")
    cfg = GwsConfig.from_env()
    assert cfg.service == "https://right.example"
    assert cfg.app_id == "our-id"
    assert cfg.app_secret == "our-secret"


def test_config_from_env_ignores_none_overrides(monkeypatch):
    """★ 回归：`from_env(service=args.service or None)` **不许**把环境值清成 None。

    2026-09-22 实测踩到：探针里写的就是这个形状，于是凭据都在、却报「缺 service」。
    症状是**静默的**——不抛异常，只是配置少了一项。
    """
    monkeypatch.setenv("WORKBENCH_GWS_SERVICE", "https://from-env.example")
    cfg = GwsConfig.from_env(service=None, timeout_s=12.0)
    assert cfg.service == "https://from-env.example", "None 覆盖把环境值清掉了"
    assert cfg.timeout_s == 12.0, "非 None 的覆盖仍然要生效"


def test_config_from_env_without_env_yields_an_empty_config(monkeypatch):
    for key in ("WORKBENCH_GWS_SERVICE", "WORKBENCH_GWS_APP_ID", "WORKBENCH_GWS_APP_SECRET"):
        monkeypatch.delenv(key, raising=False)
    cfg = GwsConfig.from_env()
    assert cfg.service is None and cfg.app_id is None and cfg.app_secret is None
    assert len(cfg.missing()) == 3


def test_resolve_binary_missing_gives_actionable_hint(monkeypatch, tmp_path: Path):
    """找不到时必须是**一句能照做的话**（三条处置），而不是一句"没找到"。

    ⚠️ 这里**必须把候选路径也清空**，不能只靠"这台机器上恰好没装 gws"——
    那是**环境事实**，而这台机器一旦真装了 gws（`install.ps1` 会装到
    `%LOCALAPPDATA%\\gws` 或 `%ProgramFiles%\\gws`），这条用例就会无缘无故变红。
    本仓已经为"守环境而不是守契约"栽过一次（`test_adapter_paths_exist`，见
    CODEBASE_OVERVIEW 的历史验收基线），不要重犯。
    """
    monkeypatch.delenv("WORKBENCH_GWS_BIN", raising=False)
    monkeypatch.setattr("workbench.services.gws.client._binary_candidates", lambda: [])
    monkeypatch.setattr("shutil.which", lambda _name: None)
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


# ---------------------------------------------------------------- 组件类型发现
#
# 真机实测：`component metadata list` 的 47 条**不完整**——
# `layout_section`(3569) / `layout_control`(14062) / `lifecycle_status`(3433)
# 都是有效类型却不在表里，而它们恰恰是承重的。


def test_component_type_exists_is_true_when_the_list_call_succeeds(fake):
    make, records, scenario = fake
    scenario(mode="ok", stdout='{"code":0,"data":[{"id":"x"}]}')
    ok, reason = make().component_type_exists("layout_control")
    assert ok and reason == ""
    assert records()[0]["argv"] == ["-s", "component", "instance", "list", "layout_control"]


def test_component_type_exists_is_false_and_keeps_the_reason(fake):
    """**不存在的类型与没权限是不同的处置**，所以原因必须带回来，不能静默吞。"""
    make, _, scenario = fake
    scenario(mode="ok", exit=1, stderr="组件类型不存在: layout_foo")
    ok, reason = make().component_type_exists("layout_foo")
    assert ok is False
    assert "layout_foo" in reason


@pytest.mark.parametrize(
    "scripted",
    [
        {"mode": "timeout", "sleep_s": 30},
        {"mode": "ok", "exit": 1, "stderr": "━━━━ 需要配置服务地址 ━━━━\n✗ 服务地址不能为空\n"},
    ],
)
def test_component_type_exists_does_not_swallow_environment_problems(fake, scripted):
    """★ **环境问题不许被读成"这个类型不存在"。**

    超时/未配置是"我们这边没弄好"，不是"平台没有这个类型"。
    把它们吞成 False，会让发现逻辑报告一份**看起来完整、其实少了东西**的清单——
    正是本仓最忌的"静默变差"。
    """
    make, _, scenario = fake
    scenario(**scripted)
    timeout = scripted.get("mode") == "timeout"
    expected = GwsTimeout if timeout else GwsNotConfigured
    client = make(GwsConfig(timeout_s=0.6) if timeout else None)
    with pytest.raises(expected):
        client.component_type_exists("layout_control")


def test_discover_component_types_separates_declared_from_undeclared(fake):
    """发现逻辑：**已声明**的 + **探测到但未声明**的，必须分开报。

    这条直接钉住真机那次踩坑：只看 `metadata list` 会得出
    "component 模型里没有 section/control" 的**错误结论**。
    """
    make, records, scenario = fake
    scenario(
        mode="ok",
        stdout='{"code":0,"data":[{"id":"x"}]}',
        routes={
            "component metadata list": {
                "stdout": json.dumps({"code": 0, "data": [
                    {"component_type": "layout", "name_cn": "表单布局"},
                    {"component_type": "object", "name_cn": "对象"},
                ]}, ensure_ascii=False),
            },
            # 未声明但真实存在
            "component instance list layout_section": {"stdout": '{"code":0,"data":[{"id":"s"}]}'},
            "component instance list layout_control": {"stdout": '{"code":0,"data":[{"id":"c"}]}'},
            # 不存在的
            "component instance list layout_field": {"exit": 1, "stderr": "组件类型不存在"},
        },
    )
    result = make().discover_component_types(
        candidates=["layout_section", "layout_control", "layout_field", "object"]
    )
    assert result["declared_names"] == ["layout", "object"]
    assert result["undeclared_but_real"] == ["layout_control", "layout_section"]
    assert "layout_field" in result["probed_missing"]
    # `object` 已声明 ⇒ 不该被重复探测
    probed = [r["argv"][-1] for r in records()
              if r["argv"][:4] == ["-s", "component", "instance", "list"]]
    assert "object" not in probed
    assert result["probed_total"] == 3


def test_discover_component_types_default_candidates_cover_the_known_three():
    """默认候选**必须包含**那三个已实证的未声明类型，否则发现逻辑等于没做。"""
    from workbench.services.gws.client import COMPONENT_SUBTYPE_HINTS

    for suffix in ("section", "control", "status"):
        assert suffix in COMPONENT_SUBTYPE_HINTS


# ---------------------------------------------------------------- 配置迁移（导出 → 入站导入 → 部署）
#
# 这一族钉的是"三跳"的口径，以及**写动词不许混进只读白名单**这条结构保证。


def test_migration_verbs_are_not_in_the_readonly_allowlist():
    """★ **结构保证**：写平台的动词与只读白名单**互斥**。

    本仓的口径：能变成硬闸门的东西，不要靠人记得。
    没有这条，将来某次"顺手把 migration export 加进只读表"（它确实不改配置，
    看起来人畜无害）会让 `run_readonly` 替所有调用方放行一次平台写入。
    """
    assert MIGRATION_WRITE_VERBS, "写动词表是空的 —— 那这条断言就成了空转"
    assert not (MIGRATION_WRITE_VERBS & READ_ONLY_VERBS), (
        f"写动词混进了只读白名单：{sorted(MIGRATION_WRITE_VERBS & READ_ONLY_VERBS)}"
    )


def test_migration_export_sends_only_the_fields_that_were_given(fake):
    make, records, scenario = fake
    scenario(mode="ok", stdout='{"code":0,"data":{"name":"PKG-NO-001","package_id":"p-1",'
                                '"file_path":"p1.zip"}}')
    pkg = make().migration_export(summary="测试摘要", owner_name="张三")
    argv = records()[0]["argv"]
    assert argv[:3] == ["-s", "migration", "export"]
    body = json.loads(argv[argv.index("--data") + 1])
    assert body == {"summary": "测试摘要", "owner_name": "张三"}
    assert "description" not in body, "没给的字段不该出现在请求体里"
    assert pkg.package_id == "p-1"
    assert pkg.file_path == "p1.zip"


def test_migration_export_without_options_sends_no_data_flag(fake):
    make, records, scenario = fake
    scenario(mode="ok", stdout='{"code":0,"data":{"package_id":"p-1"}}')
    make().migration_export()
    assert "--data" not in records()[0]["argv"]


def test_migration_import_rejects_a_missing_file_before_spawning(fake, tmp_path: Path):
    """Fail-Closed，而且**在起子进程之前就拒**——不拿坏路径去换一条 CLI 报错回来。"""
    make, records, _ = fake
    with pytest.raises(GwsError, match="不存在"):
        make().migration_import(tmp_path / "nope.zip")
    assert records() == []


def test_migration_import_rejects_a_non_zip(fake, tmp_path: Path):
    make, records, _ = fake
    not_zip = tmp_path / "pkg.tar.gz"
    not_zip.write_bytes(b"x")
    with pytest.raises(GwsError, match="zip"):
        make().migration_import(not_zip)
    assert records() == []


def test_migration_import_passes_the_file_and_reports_deploy_status(fake, tmp_path: Path):
    make, records, scenario = fake
    pkg_zip = tmp_path / "pkg.zip"
    pkg_zip.write_bytes(b"PK\x03\x04")
    scenario(mode="ok", stdout='{"code":0,"data":{"package_id":"p-9","name":"PKG-NO-009",'
                                '"type":3,"deploy_status":1}}')
    pkg = make().migration_import(pkg_zip)
    argv = records()[0]["argv"]
    assert argv[:3] == ["-s", "migration", "import"]
    assert argv[argv.index("--file") + 1] == str(pkg_zip)
    assert pkg.deploy_status == 1


def test_migration_deploy_requires_a_package_id(fake):
    make, records, _ = fake
    with pytest.raises(GwsError, match="package_id"):
        make().migration_deploy("")
    assert records() == []


def test_migration_deploy_has_nothing_to_confirm_with(fake):
    """★ **部署的响应没有 `data`** ⇒ 不能用它确认生效（PITFALLS #62 同形）。

    这条不是在测"函数返回 None"，是在**钉住一个事实**：
    `deploy` 回的是 `{code, message}`，所以"部署成功了吗"这个问题
    **在响应里无解**——只能回读平台配置。谁要是给它编一个返回值当成功证据，这条会红。
    """
    make, records, scenario = fake
    scenario(mode="ok", stdout='{"code":0,"message":null}')
    assert make().migration_deploy("p-9") is None
    argv = records()[0]["argv"]
    assert argv[:4] == ["-s", "migration", "deploy", "p-9"]


def test_migration_package_does_not_invent_unknown_enums():
    """`type` / `deploy_status` 的**取值表我们不知道**（没有真机样本）。

    缺字段时必须是 `None`，**不许**给一个"看起来合理"的默认值——
    那会变成一个凭想象写的枚举，然后被下游当判据用。
    """
    from workbench.services.gws import MigrationPackage

    export_like = MigrationPackage.from_payload(
        {"name": "PKG-NO-001", "package_id": "p", "file_path": "p.zip"}
    )
    assert export_like.type is None and export_like.deploy_status is None
    import_like = MigrationPackage.from_payload({"package_id": "p", "deploy_status": 1})
    assert import_like.deploy_status == 1
    assert import_like.type is None
    assert MigrationPackage.from_payload(None).package_id == ""
