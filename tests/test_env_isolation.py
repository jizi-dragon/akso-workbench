"""环境与身份隔离门禁（离线）。

存在理由（docs/INSIGHT-SPEC.md §3.3）：token 缓存原先**只按 `env_id` 分键**且缺省 `""` 回落
`token-cli.json` —— 后果是 A 环境的 token 会交给 B 环境的 base_url 使用；同一环境的两个账号
（真实数据里示例租户就有 `dev-user` 与 `T0901`）**共享一个 token**，第二个账号实际以第一个的身份运行。
元数据缓存 `QueryCache` 又是进程级全局单例、键不含环境，四个真实部署在同一进程内会互相污染。

本文件把两条隔离钉死：
- **凭据隔离**：token 缓存按 `(base_url, username)` 分键，载入时校验归属，且永不使用 `cli` 兜底键；
- **缓存隔离**：`GaiaQueries` 的缓存键带环境作用域，两个环境的同名对象不得串台。
"""

from __future__ import annotations

import base64
import json
import time

import httpx
import pytest

from workbench.services.egmp import client as egmp_client
from workbench.services.egmp.cache import QueryCache
from workbench.services.egmp.queries import GaiaQueries, invalidate_object_cache

# ★ 2026-10-19：用**本机未监听的端口**而不是 `a.example.com`。
# 原值要走真实域名解析失败，`test_login_cache_is_not_shared_between_accounts`
# 因此要等 ~5 秒；连接本机关闭端口是**立即** ConnectionRefused，
# 而断言（抛 `httpx.HTTPError`）一个字都不用改。
ENV_A = "http://127.0.0.1:1"
ENV_B = "http://127.0.0.1:2"


def _jwt(exp: float) -> str:
    """造一个只含 exp 的假 JWT（本模块只读载荷、不验签）。"""
    def seg(data: dict) -> str:
        raw = json.dumps(data).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    return f"{seg({'alg': 'none'})}.{seg({'exp': int(exp)})}.sig"


# ---------------------------------------------------------------- 凭据隔离


def test_token_path_is_keyed_by_base_url_and_username() -> None:
    a1 = egmp_client.token_cache_path(ENV_A, "alice")
    a2 = egmp_client.token_cache_path(ENV_A, "bob")
    b1 = egmp_client.token_cache_path(ENV_B, "alice")
    assert len({a1, a2, b1}) == 3, "三个不同 (环境, 账号) 必须落在三个不同文件"


def test_token_path_never_uses_cli_fallback() -> None:
    """缺省不再是 '' → 'cli'；空账号也必须得到独立文件而不是共享兜底键。"""
    assert "cli" not in egmp_client.token_cache_path(ENV_A, "alice").name
    assert "cli" not in egmp_client.token_cache_path(ENV_A, "").name
    assert egmp_client.token_cache_path(ENV_A, "") != egmp_client.token_cache_path(ENV_B, "")


def test_token_does_not_leak_across_environments() -> None:
    egmp_client.save_cached_token(ENV_A, "alice", "tokA")
    assert egmp_client.load_cached_token(ENV_A, "alice") == "tokA"
    assert egmp_client.load_cached_token(ENV_B, "alice") is None, "B 环境不得拿到 A 的 token"


def test_token_does_not_leak_across_accounts_in_same_env() -> None:
    """同环境两账号必须各自持票——这是「第二个账号以第一个身份运行」的根因。"""
    egmp_client.save_cached_token(ENV_A, "alice", "tokA")
    assert egmp_client.load_cached_token(ENV_A, "bob") is None


def test_token_file_records_owner_and_is_valid_json() -> None:
    egmp_client.save_cached_token(ENV_A, "alice", "tokA", env_id="env-1")
    path = egmp_client.token_cache_path(ENV_A, "alice")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["token"] == "tokA"
    assert data["baseUrl"].rstrip("/") == ENV_A
    assert data["username"] == "alice"
    assert data["expiresAt"] > time.time()


def test_loading_rejects_file_whose_owner_mismatches() -> None:
    """纵深防御：即使文件被放到错误的位置，归属校验也必须拒绝它。"""
    path = egmp_client.token_cache_path(ENV_A, "alice")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": "stolen", "expiresAt": time.time() + 600,
                                "baseUrl": ENV_B, "username": "bob"}), encoding="utf-8")
    assert egmp_client.load_cached_token(ENV_A, "alice") is None


def test_expired_token_is_not_used() -> None:
    path = egmp_client.token_cache_path(ENV_A, "alice")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": "old", "expiresAt": time.time() - 10,
                                "baseUrl": ENV_A, "username": "alice"}), encoding="utf-8")
    assert egmp_client.load_cached_token(ENV_A, "alice") is None


# ---------------------------------------------------------------- JWT exp


def test_jwt_exp_in_the_past_invalidates_even_with_fresh_ttl() -> None:
    """TTL 只是本地墙钟；平台签的 exp 更早时必须以 exp 为准。"""
    egmp_client.save_cached_token(ENV_A, "alice", _jwt(time.time() - 60))
    assert egmp_client.load_cached_token(ENV_A, "alice") is None


def test_jwt_exp_in_the_future_is_accepted() -> None:
    token = _jwt(time.time() + 3600)
    egmp_client.save_cached_token(ENV_A, "alice", token)
    assert egmp_client.load_cached_token(ENV_A, "alice") == token


def test_unparseable_token_still_works_via_ttl() -> None:
    """不是 JWT（无法读 exp）时退化为只看本地 TTL，不得因此拒用。"""
    egmp_client.save_cached_token(ENV_A, "alice", "opaque-token")
    assert egmp_client.load_cached_token(ENV_A, "alice") == "opaque-token"


# ---------------------------------------------------------------- login 短路


def test_login_returns_cached_token_without_network() -> None:
    """缓存命中时必须短路——用不存在的域名证明没有发起网络请求。"""
    egmp_client.save_cached_token(ENV_A, "alice", "tokA")
    assert egmp_client.login(ENV_A, "alice", "pw") == "tokA"


def test_login_cache_is_not_shared_between_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    """alice 有票、bob 没有：bob 登录必须走网络（此处注入连接失败 → 抛错即是证明）。

    断言具体类型而非裸 Exception：登录走 httpx，连接失败时 httpx 抛 HTTPError
    （含 ConnectError/ConnectTimeout 等子类）。裸 Exception 会把"实现里换了个完全
    无关的异常"也判成通过，等于什么都没钉住。

    ★ 2026-10-19：网络那一跳用 `monkeypatch` 注入 `ConnectError`，不再真的去解析域名。
    原实现要等 DNS 超时 ~5 秒；而本用例要证明的是**bob 没有命中 alice 的缓存**，
    "网络不可达"只是那个证明的手段，注入同样成立且更快、更稳定（不受代理/DNS 影响）。
    """
    def refuse(*_a, **_k):
        raise httpx.ConnectError("模拟：没有网络可达的登录端点")

    monkeypatch.setattr(httpx.Client, "post", refuse)
    egmp_client.save_cached_token(ENV_A, "alice", "tokA")
    with pytest.raises(httpx.HTTPError):
        egmp_client.login(ENV_A, "bob", "pw")


# ---------------------------------------------------------------- 缓存隔离


class _StubClient:
    """最小客户端桩：按 code 返回可区分的元数据。"""

    def __init__(self, scope: str, tag: str) -> None:
        self.scope = scope
        self._tag = tag

    def get_object(self, code: str) -> dict:
        return {"code": code, "env": self._tag}

    def get_object_fields(self, code: str) -> list:
        return [{"code": code, "env": self._tag}]

    def get_object_statuses(self, code: str) -> list:
        return [{"code": code, "env": self._tag}]


def test_same_object_code_in_two_envs_does_not_cross_contaminate() -> None:
    """这是真实场景：四个部署里都有 `init_status__c` 这类标准编码。"""
    invalidate_object_cache()
    a = GaiaQueries(_StubClient(ENV_A, "A"))
    b = GaiaQueries(_StubClient(ENV_B, "B"))
    assert a.get_object_info("capa_plan__c")["env"] == "A"
    assert b.get_object_info("capa_plan__c")["env"] == "B", "B 读到了 A 的缓存"
    assert a.get_fields("capa_plan__c")[0]["env"] == "A"
    assert b.get_fields("capa_plan__c")[0]["env"] == "B"
    assert a.get_statuses("init_status__c")[0]["env"] == "A"
    assert b.get_statuses("init_status__c")[0]["env"] == "B"


def test_invalidate_by_code_clears_all_scopes() -> None:
    """写路径按对象失效时必须跨环境全清，否则另一个环境会留旧值。"""
    invalidate_object_cache()
    a = GaiaQueries(_StubClient(ENV_A, "A"))
    b = GaiaQueries(_StubClient(ENV_B, "B"))
    a.get_object_info("x__c")
    b.get_object_info("x__c")
    invalidate_object_cache("x__c")
    a2 = _StubClient(ENV_A, "A2")
    b2 = _StubClient(ENV_B, "B2")
    assert GaiaQueries(a2).get_object_info("x__c")["env"] == "A2"
    assert GaiaQueries(b2).get_object_info("x__c")["env"] == "B2"


def test_invalidate_all_clears_everything() -> None:
    invalidate_object_cache()
    a = GaiaQueries(_StubClient(ENV_A, "A"))
    a.get_object_info("y__c")
    invalidate_object_cache()
    assert GaiaQueries(_StubClient(ENV_A, "A2")).get_object_info("y__c")["env"] == "A2"


def test_cache_caches_falsy_values() -> None:
    """假值必须能被缓存。历史缺陷：`get` 用 None 同时表示 miss 与「缓存了 None」，
    导致平台合法返回空响应时每次都重新发请求（thrash）。现用哨兵区分。"""
    calls: list[int] = []

    def factory() -> int:
        calls.append(1)
        return 0

    cache = QueryCache()
    assert cache.get_or_set("k", factory) == 0
    assert cache.get_or_set("k", factory) == 0
    assert len(calls) == 1, "假值应被缓存，不应重复取"


def test_cache_ttl_expiry_is_a_miss() -> None:
    # 负 TTL 让「已流逝 > TTL」恒真，避免依赖时钟推进（0.0 会因同一 tick 而不确定）
    cache = QueryCache(ttl_s=-1.0)
    cache.set("k", "v")
    assert cache.get("k") is None, "超过 TTL 应视为未命中"


# ---------------------------------------------------------------- 客户端作用域


def test_client_scope_derives_from_base_url() -> None:
    a = egmp_client.EgmpClient(f"{ENV_A}/", "tok")
    b = egmp_client.EgmpClient(ENV_B, "tok")
    assert a.scope.rstrip("/") == ENV_A
    assert a.scope != b.scope
    a.close()
    b.close()


def test_scope_ignores_trailing_slash_difference() -> None:
    a = egmp_client.EgmpClient(ENV_A, "tok")
    b = egmp_client.EgmpClient(f"{ENV_A}/", "tok")
    assert a.scope == b.scope, "仅尾斜杠不同不应视为两个环境"
    a.close()
    b.close()
