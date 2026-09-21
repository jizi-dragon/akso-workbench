"""本目录的自检 —— **不进仓库的 pytest**（`testpaths = ["tests"]`），要手动跑。

    .\\.venv\\Scripts\\python.exe factory-tuning\\tests\\selfcheck.py

## 为什么要有它

`redact.py` 是承重的安全代码，而它在写成的头十分钟里就暴露了**两个真 bug**：

1. **脱敏不足**：base64 规则的字符类含 `/`，于是**每一条端点路径**都被当成令牌
   （32 处误报）——一个动辄误报的扫描器等于没有；
2. **过度脱敏**：键名规则里的 `sign(ature)?` 打掉了 `enableSignatures`
   ——那是 `ObjectDef` 的**真实业务字段**，打掉它等于让采集到的结构缺一块，
   分析会得出"平台不发这个键"的错误结论。

两个 bug 都不是靠读代码发现的，是靠**跑一遍真数据形状**发现的。
所以这层自检不是形式：它是这两个 bug 的回归。

## 覆盖什么

- 脱敏：该打码的打码（JWT / Fernet / token_urlsafe / 口令字段 / URL 查询串）；
- 脱敏：**不该打码的保住**（`enableSignatures` / `code` / `tenantId` / 中文名）；
- 扫描器：对端点路径、GUID、普通业务 JSON **零误报**（这是它有用的前提）；
  对**本项目自己生成的假名**同样零误报——否则每采集一次就误报一批，
  恒红的门禁等于没有门禁（`find_secrets` 自己的文档警告过这件事）；
- 产物：`data/sessions/*/meta.json` **自己得先过得了扫描器**
  ——脱敏漏掉"值的某个副本"，等于没脱；
- 链路：合成样例 → 分析 → 报告，三件产物都出得来且报告里有预期的缺口。
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
FT = HERE.parent
sys.path.insert(0, str(FT / "tools"))
sys.path.insert(0, str(FT))

from redact import (  # noqa: E402
    find_secrets,
    is_sensitive_key,
    redact_body,
    redact_text,
    redact_url,
)

PY = str(ROOT / ".venv" / "Scripts" / "python.exe")

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijklmnop"
FERNET = "Z0FBQUFBQm1XeXpRc0t2V2h5U2xFc0RlbU9LbU5vUFFyU3R1VndYeg=="
TOKEN_URLSAFE = "xK3n9_Qw2Zt7-Rb4Yh6Lm1Pc8Vd5Ns0J"

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  —— {detail}" if not ok else ""))
    if not ok:
        FAILED.append(label)


def skip(label: str, why: str) -> None:
    """前置条件不成立 —— **既不 PASS 也不 FAIL**。

    存在的理由：`data/sessions/` 是**故意不入库**的（只跟踪 `.gitkeep`），
    所以干净 clone 上必然没有采集产物。那种情况下把检查写成 `check(..., bool(x))`
    会**在别人机器上恒红**，而恒红的门禁等于没有门禁——正是本文件在防的那件事。
    但也不能悄悄过：`SKIP` 是**看得见的第三种结果**，不会被读成"验过了"。
    """
    print(f"  SKIP  {label}  —— {why}")


def test_redaction_catches_credentials() -> None:
    print("=== A. 该打码的必须打掉 ===")
    out = redact_body({"accessToken": JWT, "expiresIn": 3600})
    check("A1 键名带 token → 打码", out["accessToken"] == "***", str(out))
    check("A2 同一对象里的普通字段不受影响", out["expiresIn"] == 3600)

    check("A3 键名带 password → 打码",
          redact_body({"password": "Hunter2!"})["password"] == "***")
    check("A4 键名恰好是 signature → 打码（整键命中档）",
          redact_body({"signature": "abc"})["signature"] == "***")
    check("A5 键名是 accessToken 的变体也打",
          redact_body({"access_token": "abc"})["access_token"] == "***")

    check("A6 自由文本里的 JWT 被打掉（哪怕没有提示性键名）",
          find_secrets(f"bearer {JWT}") != [] and JWT not in redact_body({"raw": JWT})["raw"])
    check("A7 Fernet 形状（base64 + `=` 填充）被认出", find_secrets(FERNET) != [])
    check("A8 token_urlsafe 形状被认出", find_secrets(TOKEN_URLSAFE) != [])

    url = redact_url(f"https://prod.example.com/api/x?access_token={TOKEN_URLSAFE}&code=a__c")
    check("A9 URL 丢掉主机名", "example.com" not in url, url)
    check("A10 URL 查询串里的令牌被打掉", "***" in url, url)
    check("A11 URL 里的普通参数保留", "code=a__c" in url, url)


def test_redaction_keeps_business_structure() -> None:
    print("=== B. 不该打码的一个都不许少（过度脱敏与脱敏不足一样是缺陷）===")
    # ←→ 这两条是真实事故的回归：第一版把 enableSignatures 打掉了
    check("B1 enableSignatures（ObjectDef 的真实字段）必须保留",
          not is_sensitive_key("enableSignatures"))
    check("B2 sessionCount 必须保留（`session` 只整键命中）",
          not is_sensitive_key("sessionCount"))
    for key in ("code", "name", "dataType", "isRequired", "tenantId", "fieldId",
                "defaultValue", "objectClass", "bindToStatusCode", "referenceObjectCode"):
        check(f"B3 {key} 保留", not is_sensitive_key(key))

    body = {"enableSignatures": True, "signature": "abc", "name": "培训记录"}
    kept = redact_body(body)
    check("B4 同一对象里业务字段与凭据字段各按各的处理",
          kept["enableSignatures"] is True and kept["signature"] == "***"
          and kept["name"] == "培训记录", str(kept))

    # ←→ 真实缺陷回归（docs/PITFALLS.md #60）：GUID 曾经被 tokenish 兜底抢先抹成 `***`。
    # 这三条钉的是"id 必须还能用来跨请求关联"——不是因为 `***` 不安全，而是因为
    # 平台的对象/字段/状态标识全是 GUID，抹掉它分析就断链了。
    guid = "3a09fb55-64da-f000-0008-c292c3a6e0be"
    other = "3a09fb55-64da-d9b9-15a9-c292c3a6e0be"
    once, twice, other_once = redact_text(guid), redact_text(guid), redact_text(other)
    check("B5 GUID 是假名化而不是抹成 ***",
          once.startswith("id-") and once != "***", once)
    check("B6 同一 GUID 稳定、不同 GUID 可区分（可跨请求关联）",
          once == twice and once != other_once, f"{once} / {twice} / {other_once}")
    nested = redact_body({"objectId": guid, "name": "A"})
    check("B7 GUID 嵌在结构里也被假名化（且业务字段不受影响）",
          str(nested["objectId"]).startswith("id-") and nested["name"] == "A", str(nested))


def test_scanner_has_no_false_positives() -> None:
    print("=== C. 扫描器不许把正常东西判成凭据（否则它会被忽略）===")
    for label, text in (
        ("端点路径", "/api/platform/BasicObject/SaveFieldDefaultValue"),
        ("更长的端点路径", "/api/config/lifecycle/UserAction/GetFieldsAndRelationsList"),
        ("GUID", "11111111-2222-3333-4444-555555555555"),
        ("snake_case 对象编码", "training_record__c"),
        ("普通业务 JSON", json.dumps({"code": "a__c", "name": "培训记录", "dataType": 1},
                                     ensure_ascii=False)),
        ("markdown 表格行", "| `/api/platform/Workflow/AddWorkflowBasic` | 1 | 新建工作流 |"),
        # 下面两条是**本项目自己生成的假名**，形态恰好满足"像令牌"的启发式
        # （≥24 字符、带 `-`、含数字）。不豁免就是"每采集一次误报一批"，
        # 而恒红的门禁等于没有门禁——`find_secrets` 自己的文档警告过这件事。
        ("会话目录假名", "acct-fb8dc317-20260921-154824"),
        ("对象假名", "id-9af645a8"),
        # 平台自己的编码：长一点的（≥24 字符）同样会撞上"像令牌"的启发式。
        # 实测是写真机实战报告时撞上的——每一份文档都会误报，所以必须豁免。
        ("长对象编码", "test_0903_option_probe__c"),
        ("系统字段编码", "statistics_relation_id__sys"),
        ("默认字段编码", "object_type_id__ak"),
    ):
        hits = find_secrets(text)
        check(f"C {label} 零误报", hits == [], str(hits))

    # 反向：豁免必须**只对"整段就是假名"**生效。假名被嵌进一段更长的串里，
    # 那就是真令牌的典型形态，必须照样判红——否则这条豁免就成了一个后门。
    #
    # 刻意**拼起来**而不是写成一整段字面量：扫描器当前的 `SUFFIXES` 不含 `.py`
    # （见 F 组的说明），所以今天这行扫不到；但一旦有人补上 `.py`，
    # 一整段字面量会让**这个测试文件自己**变成命中项。拼起来就没有这个耦合。
    embedded = find_secrets("sk-live-" + "acct-fb8dc317-20260921-154824" + "-9f2c1d")
    check("C 假名嵌在更长的串里照样判红（豁免不是后门）", embedded != [], str(embedded))

    # 同一条反证，用在**平台编码**上：编码被拼进一段更长的串里就是真令牌的形态。
    embedded_code = find_secrets("sk-live-test_0903_option_probe__c-9f2c1d")
    check("C 编码嵌在更长的串里照样判红", embedded_code != [], str(embedded_code))


def test_chain_end_to_end() -> None:
    print("=== D. 合成样例 → 分析 → 报告，整条链要能跑 ===")
    fixture = HERE / "fixtures" / "SAMPLE-20260101-000000" / "traffic.jsonl"
    if not fixture.is_file():
        subprocess.run([PY, str(HERE / "make_fixture.py")], cwd=str(ROOT), check=False)
    check("D1 合成样例已生成", fixture.is_file(), str(fixture))

    from analyze import coverage  # noqa: PLC0415
    from analyze.model import load_session, pick  # noqa: PLC0415

    session = load_session(pick("", fixtures=True))
    check("D2 会话能加载", len(session.entries) == 10, str(len(session.entries)))

    cov = coverage.analyze(session.entries)
    check("D3 覆盖分析认出了故意留的两个缺口",
          set(cov.missing) == {"/api/platform/BasicObject/SaveFieldDefaultValue",
                               "/api/platform/BasicObject/SaveOutField"},
          str(cov.missing))
    check("D4 已建模的端点没被误判成缺口",
          "/api/platform/BasicObject/SaveField" not in cov.missing)

    result = subprocess.run([PY, str(FT / "analyze" / "report.py"), "--fixtures"],
                            cwd=str(ROOT), capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    check("D5 报告生成成功", result.returncode == 0, (result.stdout + result.stderr)[-200:])
    reports = sorted((FT / "reports").glob("*SAMPLE*.md"))
    check("D6 报告文件落盘", bool(reports), str(reports))
    if reports:
        text = reports[-1].read_text(encoding="utf-8")
        check("D7 报告里有覆盖缺口那一节", "没建模的端点" in text)
        check("D7b 报告里有形状对比那一节", "请求体形状" in text)
        check("D8 报告里有『不要读成待办清单』的提示", "待核实清单" in text)
        check("D9 报告里点出了故意留的缺口端点",
              "SaveFieldDefaultValue" in text and "SaveOutField" in text)


def test_captured_artifacts_pass_the_scanner() -> None:
    print("=== F. 采集产物自己必须过得了扫描器（脱敏漏一个副本 = 没脱）===")
    # 背景（2026-09-21 已处置，见 docs/PITFALLS.md #66）：`scan_secrets.SUFFIXES`
    # **曾经不含 `.py`**，而它的 `ALLOWLIST` 里 3 条有 2 条是 `.py`（`redact.py` /
    # `scan_secrets.py`）——只有"本来会扫到"才需要豁免，所以那两条曾是**死条目**，
    # 反过来证明 `.py` 是被漏掉的而不是刻意排除的。现已补上 `.py`（本文件因此进了
    # ALLOWLIST：那三个测试向量是故意的）。
    # 本文件的反证串仍**刻意拼起来**写，不依赖这个边界——见 C 组的注释。
    metas = sorted((FT / "data" / "sessions").glob("*/meta.json"))
    # `data/sessions/` 故意不入库（只跟踪 `.gitkeep`），所以干净 clone 上必然没有产物。
    # 那种情况是 SKIP，不是 PASS 也不是 FAIL —— 见 `skip()` 的注释。
    if not metas:
        skip("F1 meta.json 零命中且不含 source_dir",
             "本机没有采集产物（data/sessions/ 不入库）——本条无从校验")
        return

    problems = []
    for meta in metas:
        body = meta.read_text(encoding="utf-8")
        hits = find_secrets(body)
        if hits:
            problems.append(f"{meta.parent.name}: 扫描命中 {hits}")
        # `source_dir` 一旦回来，写进去的就是运行时目录名 = `<原始账号id>-<时间戳>` 明文。
        # 而 `session` 是同一个东西的假名形式——所以这个键**只泄露、不增信息**。
        if "source_dir" in json.loads(body):
            problems.append(f"{meta.parent.name}: 又写回 source_dir（原始账号 id 明文）")
    check("F1 meta.json 零命中且不含 source_dir", problems == [], str(problems))


def test_product_stays_clean() -> None:
    print("=== E. 产品代码不许 import 本目录（删掉它，产品必须一行都不变）===")
    hits = []
    for path in (ROOT / "workbench").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "factory-tuning" in text or "factory_tuning" in text:
            hits.append(path.relative_to(ROOT).as_posix())
    check("E1 workbench/ 里零引用", hits == [], str(hits))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    test_redaction_catches_credentials()
    test_redaction_keeps_business_structure()
    test_scanner_has_no_false_positives()
    test_chain_end_to_end()
    test_captured_artifacts_pass_the_scanner()
    test_product_stays_clean()
    print()
    print("=" * 60)
    if FAILED:
        print(f"SELFCHECK: FAIL（{len(FAILED)} 项）")
        for label in FAILED:
            print(f"  ✗ {label}")
        return 1
    print("SELFCHECK: 全过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
