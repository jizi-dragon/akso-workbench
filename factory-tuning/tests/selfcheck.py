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

from redact import find_secrets, is_sensitive_key, redact_body, redact_url  # noqa: E402

PY = str(ROOT / ".venv" / "Scripts" / "python.exe")

JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefghijklmnop"
FERNET = "Z0FBQUFBQm1XeXpRc0t2V2h5U2xFc0RlbU9LbU5vUFFyU3R1VndYeg=="
TOKEN_URLSAFE = "xK3n9_Qw2Zt7-Rb4Yh6Lm1Pc8Vd5Ns0J"

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  —— {detail}" if not ok else ""))
    if not ok:
        FAILED.append(label)


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
    ):
        hits = find_secrets(text)
        check(f"C {label} 零误报", hits == [], str(hits))


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
