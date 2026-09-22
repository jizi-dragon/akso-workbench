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

    # ←→ 2026-09-22：**第三档：用户手打的自由文本**。它不是凭据，是**个人数据**——
    # 平台把搜索框里的内容原样发出去，而人搜的经常是人名/单位名。
    # 实测触发点：一次浏览流量里 `ListlayoutRecord/GetRecordPositionAll` 的
    # `{"keywords":"<真人姓名>"}`（那个会话因此没有 ingest）。
    search = redact_body({"keywords": "张三", "pageIndex": 1, "pageSize": 20})
    check("A12 搜索词（keywords）被打掉", search["keywords"] == "***", str(search))
    check("A13 同一请求里的分页参数不受影响",
          search["pageIndex"] == 1 and search["pageSize"] == 20)
    check("A14 过滤值（filterValue）被打掉",
          redact_body({"filterValue": "李四"})["filterValue"] == "***")
    check("A15 表单里的 searchText 被打掉",
          redact_body({"searchText": "某某公司"})["searchText"] == "***")


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

    # ←→ 自由文本那一档**只按键名整键命中**，不许吃业务键（A12–A15 的反面）。
    # 这几条是"过度脱敏"的守卫：子串匹配会把它们全打掉，而那会**静默**丢掉结构。
    for key in ("keywordCount", "searchableFields", "filterOperator", "inputType",
                "keyword", "searchFields", "name", "code"):
        check(f"B8 {key} 保留（自由文本档只整键命中）", not is_sensitive_key(key))


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
        # 文件名 / 路径片段（小写 kebab-case slug）：交叉引用兄弟笔记是写文档的日常动作，
        # 而 ≥24 字符的 slug 恰好撞上同一条启发式 ⇒ 每写一份带引用的笔记就误报一次。
        # 实测（2026-09-22）：`09-admin-permissions-org.md` 的两处引用把扫描器打成非零退出。
        #
        # ⚠️ 豁免要求**"是文件名"的正证据**（紧跟扩展名 / 跟在路径分隔符后），
        # 所以这里必须用**文件名形态**；裸 slug 仍然判红——见下面那条单独的断言。
        ("笔记文件名（带后缀）", "见 `05-permissions-rules-data.md` 的结论"),
        ("笔记文件名（另一份）", "见 08-factory-capability-map.md 的结论"),
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

    # 同一条反证，用在**文档 slug** 上。
    embedded_slug = find_secrets("sk-live-" + "05-permissions-rules-data" + "-9f2c1d")
    check("C slug 嵌在更长的串里照样判红", embedded_slug != [], str(embedded_slug))

    # 与上面配对：**slug 形状但不带扩展名** ⇒ 没有"它是文件名"的正证据，
    # 那就是真令牌的形态，必须判红。第一版豁免只看形状，正是栽在这里。
    bare_slug = find_secrets("见 05-permissions-rules-data 的结论")
    check("C 不带扩展名的 slug 仍然判红（豁免要「是文件名」的正证据）",
          bare_slug != [], str(bare_slug))

    # 豁免**只在扫描器一侧**（与 PSEUDONYM_RE / PLATFORM_CODE_RE 同一口径）：
    # 脱敏仍然要把 slug 打掉——不对称的方向必须是"脱敏更严"，否则这条就成了后门。
    slug_redacted = redact_text("05-permissions-rules-data")
    check("C 脱敏仍把 slug 打掉（豁免只在扫描器一侧）",
          slug_redacted == "***", slug_redacted)


def test_chain_end_to_end() -> None:
    print("=== D. 合成样例 → 分析 → 报告，整条链要能跑 ===")
    fixture = HERE / "fixtures" / "SAMPLE-20260101-000000" / "traffic.jsonl"
    # **每次都重新生成**（2026-09-22 起；原来是"缺了才生成"）：D 组验证的是
    # "**生成器 → 分析器**"这条链，所以样例必须由**当前**生成器产出。
    # 不重生成就会出现"本机绿、干净 clone 红"（或反过来）的漂移——
    # 而那种红的归因成本极高（PITFALLS #38 的同族：环境/产物与代码不同步）。
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
        check("D10 报告里有 Δt 列与突发警示",
              "距上条" in text and "一次页面事件" in text)
        # D11（2026-09-22 段 2 追加）：两条时间线**必须分开呈现**——一条可用（缓冲内部）、
        # 一条只能当异常探测（缓冲↔请求，PITFALLS #74）。合成样例里没有动作时间戳也应有这一节。
        check("D11 报告里有『缓冲内部 vs 缓冲↔请求』这一节与 #74 的警示",
              "3.1 动作缓冲逐格" in text and "PITFALLS #74" in text)
        # D12（2026-09-22 段 2 追加）：**形状谱**那一节必须真的给出两种形状——
        # 合成样例照真机抄了"新建 8 键 / 已有 23 键"，所以这条同时钉住三件事：
        # ① 嵌套数组被解析；② 形状按元素键集分组；③ 两种形状会被标出来（F36）。
        check("D12 报告里有嵌套数组的形状谱，且标出了两种形状",
              "嵌套数组的形状谱" in text and "8 键" in text and "23 键" in text
              and "两种以上形状" in text)
        # D13（2026-09-22 段 3 追加）：**值域**那一节——"猜值"是 D-D 的另一半成因。
        # 合成样例里控件的 `type` 是实测过的 `AKDateTime` / `AKAttachment`，所以这条能钉住
        # "短取值真的被摊开了"，而不只是"标题在"。
        check("D13 报告里有值域一节，且摊开了 AK* 这类短取值",
              "2.6 值域" in text and "AKDateTime" in text and "AKAttachment" in text)


def test_timestamps_survive_the_loader() -> None:
    print("=== H. 时间戳必须活到分析层（它一度被静默丢掉）===")
    # 背景（2026-09-22 修）：`ingest.py` **写了** `at`（来自 Monitor 的 `timestamp`），
    # 但 `analyze/model.py` 的 `Entry` 没有这个字段、`_entry_from_row` 也不读它 ⇒
    # 数据在**最后一跳**被丢掉，而丢时间不会报错，只会让分析器再也分不出
    # 「一次页面事件」与「一串人工操作」：`acct-891a4f9d-20260921-210820` 的 8 条请求
    # 实测**全在 165ms 内**，却一度被读成「用户在界面上依次配了 10 列」。
    from analyze.model import _entry_from_row  # noqa: PLC0415

    entry = _entry_from_row({
        "seq": 3, "at": 1789996237200, "method": "POST", "path": "/api/x",
        "status": 200, "known": True, "postData": {}, "body": None,
        "precedingActions": [{"type": "click", "text": "确 定", "tag": "SPAN"}],
    })
    check("H1 加载器保留 at（丢了就没有 Δt，也就分不出突发与人工序列）",
          entry.at == 1789996237200, repr(entry.at))
    check("H2 缺 at 时是 None，不是 0（不许把「没有时间」伪装成 1970 年）",
          _entry_from_row({"seq": 1, "method": "GET", "path": "/y"}).at is None)

    # H3（2026-09-22 段 2 追加）：**动作自己的**时间戳也必须活到数据层。
    # 它一度被 `ingest.clean_entry` 静默丢掉（只留 type/text/tag）——而"同一个动作缓冲**内部**
    # 动作之间的间隔"是**唯一可信**的那半条时间线：跨"缓冲 ↔ 请求"的差不可用
    # （两边时钟不同源，差 38–61 s，见 PITFALLS #74）。
    # 反证**已经观测到过**：加这个字段之前，判据命令直接在收编后的数据上报 `KeyError: 'at'`。
    from ingest import clean_entry  # noqa: PLC0415

    cleaned = clean_entry({
        "timestamp": 1789996237200, "method": "POST", "path": "/api/x", "status": 200,
        "known": True, "postData": {},
        "precedingActions": [{"type": "click", "text": "整 行", "tag": "SPAN",
                              "at": 1789996237000}],
    }, 1)
    check("H3 动作自己的时间戳要活到数据层（唯一可信的那半条时间线）",
          cleaned["precedingActions"][0].get("at") == 1789996237000,
          str(cleaned["precedingActions"]))

    # H4（2026-09-22 段 2 追加）：动作时间戳还要活到**加载器**，而且**动作里多出新字段
    # 不许把分析层炸掉**。实录：`ingest` 补了动作的 `at` 之后，`report.py` **立刻崩**在
    # `Action.__init__() got an unexpected keyword argument 'at'`（那里写的是 `Action(**row)`）。
    # "采集端多了一个字段"应当是**可见的数据**（进 `extra`），不是运行期崩溃。
    tagged = _entry_from_row({
        "seq": 4, "at": 1789996238000, "method": "POST", "path": "/api/x",
        "precedingActions": [
            {"type": "click", "text": "整 行", "tag": "SPAN", "at": 1789996237000},
            {"type": "click", "text": "确 定", "tag": "SPAN", "at": 1789996237756},
            {"type": "click", "text": "保 存", "tag": "SPAN", "at": 1789996237900,
             "将来才会有的字段": "x"},
        ],
    })
    check("H4a 动作自己的时间戳活到加载器（丢了就只剩一串动作名）",
          [a.at for a in tagged.actions] == [1789996237000, 1789996237756, 1789996237900],
          str([a.at for a in tagged.actions]))
    check("H4b 缓冲内部间隔 = 页面时钟自己减自己（唯一可信的那半条时间线）",
          tagged.buffer_intervals == [756, 144], str(tagged.buffer_intervals))
    check("H4c 未知动作字段进 extra，而不是把分析层炸掉",
          tagged.actions[2].extra.get("将来才会有的字段") == "x",
          str(tagged.actions[2].extra))
    check("H4d 缓冲↔请求的差能算出来，但**只作异常探测**（PITFALLS #74）",
          tagged.buffer_lag == 100, repr(tagged.buffer_lag))


def test_session_arg_accepts_name_and_path() -> None:
    print("=== G. `--session` 既要认会话名、也要认路径（报错信息教的那条路必须真能走）===")
    # 背景（2026-09-22 修）：`analyze/model.py::pick()` 曾把 `--session` 的值**直接当路径**
    # 返回，于是 README / `--help` / 它**自己的报错提示**都说"给会话名"，照做却报
    # 「缺少 traffic.jsonl：<会话名>」；而**同一个参数名**在 `ingest.py` 里一直是按名字
    # 解析的 ⇒ **一个 flag 两套语义**，才是这条真正的形态。
    # 修法：两种口径都认——先当路径（旧口径，兼容），再当名字（去 root 下找同名目录）。
    from analyze.model import pick  # noqa: PLC0415

    fixture = HERE / "fixtures" / "SAMPLE-20260101-000000"
    if not (fixture / "traffic.jsonl").is_file():
        subprocess.run([PY, str(HERE / "make_fixture.py")], cwd=str(ROOT), check=False)

    named = pick(fixture.name, fixtures=True)
    check("G1 给会话名能解析到会话目录",
          named.is_dir() and (named / "traffic.jsonl").is_file(), str(named))
    check("G2 解析结果就是那个会话", named.name == fixture.name, named.name)

    explicit = pick(str(fixture))
    check("G3 给显式路径仍然兼容",
          explicit.is_dir() and (explicit / "traffic.jsonl").is_file(), str(explicit))

    try:
        pick("no-such-session-xyz", fixtures=True)
    except SystemExit as exc:
        check("G4 找不到时报错并列出可选会话（Fail-Closed）",
              "找不到会话" in str(exc), str(exc)[:160])
    else:
        check("G4 找不到时必须报错（不许静默返回一个不存在的路径）", False, "没有抛 SystemExit")


def test_key_audit_script() -> None:
    print("=== I. 键名审计脚本要能跑出真机键集（D-A/D-C 那类缺陷的扫描面）===")
    # 背景（2026-09-22）：D-A（`columns` vs `listlayoutColumns`）与 D-C（`basicObjectId` vs `objectId`）
    # 是同一种缺陷——**照着猜的键名写代码**。要防住它，得能反复回答"这个端点真机发哪些键"。
    # 第一版是一条 `python -c "…"` 一行命令，**跑不了**（项目里 PowerShell 通道会吃掉 `"`），
    # 所以改成 `tools/audit_keys.py`，并在自检里钉住它至少还能跑出那两个布局端点的真机键名。
    from audit_keys import collect  # noqa: PLC0415 —— sys.path 在文件头已加 tools/

    seen = collect()
    if not seen:
        skip("I1 审计脚本能跑出端点键集",
             "本机没有带请求体的采集产物（data/sessions/ 在干净 clone 上是空的）——本条无从校验")
        return
    check("I1 审计脚本至少认出一个端点", len(seen) >= 1, str(len(seen)))
    add_columns = seen.get("/api/platform/Listlayout/AddColumns", set())
    check("I2 `AddColumns` 的真机键名是 `listlayoutColumns`（D-A 的证据）",
          any("listlayoutColumns" in keys for keys in add_columns), str(sorted(add_columns)))
    save_layout = seen.get("/api/platform/Layout/SaveLayoutDetail", set())
    check("I3 `SaveLayoutDetail` 的真机对象键是 `objectId`（D-C 的证据）",
          any("objectId" in keys for keys in save_layout), str(sorted(save_layout)))

    # 响应侧（"回来的东西"）：#62 那一族（接口回 data:true、回读才发现空）靠这两条钉住。
    from audit_keys import collect_responses  # noqa: PLC0415

    responses = collect_responses()
    if not responses:
        skip("I4/I5 响应信封形状", "本机没有带响应体的采集产物——本条无从校验")
        return
    save_object = responses.get("/api/platform/BasicObject/SaveBasicObject", set())
    check("I4 `SaveBasicObject` 的响应 data 是布尔（不回 id，F2 的证据）",
          save_object == {"bool(True)"}, str(sorted(save_object)))
    add_columns = responses.get("/api/platform/Listlayout/AddColumns", set())
    check("I5 `AddColumns` 的响应 data 是整数（列数 = 免回读的自检信号，F26 的证据）",
          add_columns == {"int"}, str(sorted(add_columns)))


def test_key_gate_detector() -> None:
    print("=== J. 键名闸门的比对逻辑（当前仓库确有 2 个可疑键，所以闸门本身是红的）===")
    # 背景（2026-09-22）：D-A（`columns`）/ D-C（`basicObjectId`）是"猜键名"那一族。
    # `tools/keygate.py` 用 AST 抽出工厂的**字面量请求体**，与真机键集自动比对。
    #
    # ⚠️ **闸门不能进 PASS/FAIL**：它现在**故意是红的**（两个缺陷还没修），
    # 把它写成断言等于"断言缺陷存在"，反而会挡住修复。
    # 自检守的是**比对逻辑本身**——用合成输入验三种判定 + 那条有背书的豁免。
    from keygate import compare  # noqa: PLC0415

    calls = [
        {"file": "x.py", "line": 1, "path": "/api/x", "endpoint": "X", "keys": ["a", "bogus"]},
        {"file": "x.py", "line": 2, "path": "/api/y", "endpoint": "Y", "keys": None},
        {"file": "x.py", "line": 3, "path": "/api/z", "endpoint": "Z", "keys": ["same"]},
    ]
    real: dict[str, set[tuple[str, ...]]] = {
        "/api/x": {("a", "b")},
        "/api/z": {("same",)},
    }
    out = {str(row["path"]): row for row in compare(calls, real)}

    check("J1 工厂发了、真机样本从未出现的键 → 判为可疑",
          out["/api/x"]["suspect"] == ["bogus"], str(out["/api/x"]["suspect"]))
    check("J2 真机有、工厂没发的键 → 单独列出（不混进可疑）",
          out["/api/x"]["absent"] == ["b"], str(out["/api/x"]["absent"]))
    check("J3 完全一致 → 判为一致",
          out["/api/z"]["verdict"].startswith("✅") and out["/api/z"]["suspect"] == [],
          str(out["/api/z"]))
    check("J4 动态体（静态看不见）→ 如实报未核，不假装覆盖",
          "未核" in str(out["/api/y"]["verdict"]), str(out["/api/y"]["verdict"]))

    # 那条**有背书的**豁免必须真的生效（F11：界面带顶层数字键，工厂不带是合法路径）。
    pick = compare(
        [{"file": "picklists.py", "line": 29, "path": "/api/platform/ObjectPicklist/save",
          "endpoint": "PICKLIST_SAVE", "keys": ["code", "name", "options"]}],
        {"/api/platform/ObjectPicklist/save": {("0", "1", "2", "code", "name", "options")}},
    )[0]
    check("J5 有背书的差异（F11 顶层数字键）被豁免，不算漏发",
          pick["absent"] == [] and pick["suspect"] == [], str(pick))

    # 主表看不见的那一层：嵌套结构（`controls.append({...})`）必须被**列出来**，
    # 而不是静默跳过——D-D 就是从这里漏过去的（见 keygate.subliterals 的文档串）。
    # 断言只钉"扫描器能跑出结果"，**不钉某个具体键**：钉了就等于"断言缺陷存在"，会挡住修复。
    from keygate import subliterals  # noqa: PLC0415

    subs = subliterals()
    check("J6 子结构字面量扫描能跑出结果（不是静默跳过）",
          bool(subs) and all(row["keys"] for row in subs), str(subs[:2]))


def test_nested_key_gate() -> None:
    print("=== K. 嵌套字面量也要被**机械**比对（D-D 曾经是静默盲区）===")
    # 背景（2026-09-22 段 2）：`keygate.py` 的主表只认 `client.post(常量, {字面量})` 的**实参**，
    # 而控件是循环里 `controls.append({...})` 拼出来的 ⇒ **D-D 在闸门里是盲区**。
    # 现在：子结构按「挂载对象名 + **同模块端点**」与真机的同名数组比对。
    #
    # ⚠️ 这一组最容易**静默变绿**：只要挂载对象名取不到、或端点↔模块映射解析失败，
    #    所有嵌套行都会变成"未核"——闸门**不再检查任何东西，而输出看起来很正常**。
    #    K9 就是钉这件事的：有真机数据时，必须**至少有一行真的被比过**。
    from audit_keys import collect_arrays, collect_values  # noqa: PLC0415
    from keygate import (  # noqa: PLC0415
        compare_subliterals,
        compare_vocab,
        dict_literals,
        module_of_endpoints,
        subliterals,
    )

    subs = subliterals()
    check("K1 子结构字面量带得出挂载对象名（丢了它，嵌套比对只能退回全局按名匹配）",
          any(row["target"] == "controls" for row in subs), str(subs[:2]))

    modules = module_of_endpoints()
    check("K2 端点 ↔ writer 模块的映射能解析出来（解析失败 ⇒ 所有嵌套行静默变『未核』）",
          any(any("Layout/SaveLayoutDetail" in path for path in paths)
              for paths in modules.values()),
          str(sorted(modules)))

    # 合成输入：只钉**比对逻辑**，不钉"仓库里现在有没有缺陷"（否则会挡住修复）。
    synthetic_arrays = {
        "controls": {"by_endpoint": {"/api/platform/Layout/SaveLayoutDetail":
                                     {"id", "code", "type", "gridSpan"}}, "samples": 3},
    }
    synthetic_modules = {"layouts.py": {"/api/platform/Layout/SaveLayoutDetail"}}
    rows = {str(r["line"]): r for r in compare_subliterals(
        [
            {"file": "layouts.py", "line": 1, "target": "controls",
             "keys": ["code", "gridSpan", "id", "type"]},
            {"file": "layouts.py", "line": 2, "target": "controls", "keys": ["ctrlType"]},
            {"file": "other_module.py", "line": 3, "target": "controls", "keys": ["code"]},
            {"file": "layouts.py", "line": 4, "target": "", "keys": ["code"]},
            {"file": "layouts.py", "line": 5, "target": "steps", "keys": ["code"]},
        ],
        synthetic_arrays, synthetic_modules)}

    check("K3 真机见过的键 → 判为一致",
          rows["1"]["verdict"].startswith("✅") and rows["1"]["suspect"] == [],
          str(rows["1"]["verdict"]))
    check("K4 真机从未出现过的键 → 判为可疑（这就是 D-D 的形态）",
          rows["2"]["suspect"] == ["ctrlType"], str(rows["2"]["suspect"]))
    check("K5 ★ 别的模块的样本**不许**借给它（否则是凭空多出来的假阳性）",
          "未核" in str(rows["3"]["verdict"]), str(rows["3"]["verdict"]))
    check("K6 挂载对象名取不到 → 未核，不假装覆盖",
          "未核" in str(rows["4"]["verdict"]), str(rows["4"]["verdict"]))
    check("K7 真机没见过这个数组名 → 未核，不假装覆盖",
          "未核" in str(rows["5"]["verdict"]), str(rows["5"]["verdict"]))

    # 词表层（D-D 的**另一半**）：键名改对了、值继续自造，只比键名是抓不住的。
    dicts = dict_literals()
    check("K10 工厂的模块级词表能被 AST 抓出来（抓不到 ⇒ 词表层静默空转）",
          any(name == "AK_BY_DATA_TYPE" for _file, name in dicts),
          str(sorted(dicts)))
    # 合成输入：沿用**声明表里那条**对应关系（配对是显式的，不靠名字猜）
    vocab_rows = compare_vocab(
        {("layouts.py", "AK_BY_DATA_TYPE"):
         {"line": 1, "values": ["AKDateTime", "invented"]}},
        {"controls": {"type": ["AKDateTime", "AKSelectorSingle"]}},
        {"layouts.py": {"/api/platform/Layout/SaveLayoutDetail"}},
    )
    check("K11 词表里真机没见过的值 → 判为可疑（D-D 的另一半）",
          vocab_rows[0]["suspect"] == ["invented"], str(vocab_rows[0]))
    check("K11b 词表比对也要 Fail-Closed：没有样本时是未核，不是一致",
          "未核" in str(compare_vocab(
              {("layouts.py", "AK_BY_DATA_TYPE"): {"line": 1, "values": ["AKDateTime"]}},
              {}, {})[0]["verdict"]))

    # K13/K14（2026-09-22 段 4 后追加）：**"先拼字典再 post" 那条路必须看得见**。
    # 背景：对象与字段的创建体走的是工厂自己的助手 `created_id(client, path=常量, body=变量)`，
    # 而闸门原来只认 `client.post(常量, {字面量})` ⇒ 这两个体**一条都没被检查过**，
    # 而缺陷就住在那个盲区里（`SaveField` 的 `isRequired`，真机 11 条里一条都没发过）。
    from keygate import literal_posts  # noqa: PLC0415

    rows_all = {(str(r["endpoint"]), str(r["file"])): r for r in literal_posts()}
    field_row = rows_all.get(("SAVE_FIELD", "fields.py"))
    check("K13 经工厂助手（`created_id(path=…, body=变量)`）发出的体也要被解析出来",
          field_row is not None and bool(field_row["keys"]), str(field_row))
    object_row = rows_all.get(("SAVE_BASIC_OBJECT", "objects.py"))
    check("K14 体由**别的函数 return** 时，跳一层也要取到（对象创建是管道第一步）",
          object_row is not None and object_row["keys"] == [
              "code", "enableLifeCycle", "enableSignatures", "name", "objectClass",
              "source", "status", "summaryFields"], str(object_row))

    real_arrays = collect_arrays()
    if not real_arrays:
        skip("K8/K9 真机嵌套键集",
             "本机没有采集产物（data/sessions/ 在干净 clone 上是空的）——本条无从校验")
        return
    controls = dict(real_arrays.get("controls", {}).get("by_endpoint", {}))
    keys = set().union(*controls.values()) if controls else set()
    check("K8 真机 `controls[]` 元素带 `gridSpan`/`type`/`sectionId`（D-D 的比对基准）",
          {"gridSpan", "type", "sectionId"} <= keys, str(sorted(keys)[:10]))
    rows_real = compare_subliterals(subs, real_arrays, modules)
    checked = [row for row in rows_real if "未核" not in str(row["verdict"])]
    check("K9 ★ 有真机数据时**至少有一行真的被比过**（防闸门静默退化成全『未核』）",
          bool(checked), str([(r["file"], r["line"], r["verdict"]) for r in rows_real][:6]))
    vocab_real = compare_vocab(dicts, collect_values(), modules)
    check("K12 ★ 词表层同理：有真机数据时它必须真的比过（不许静默退化成全『未核』）",
          any("未核" not in str(r["verdict"]) for r in vocab_real), str(vocab_real))


def test_capture_cli_shape() -> None:
    print("=== L. 采集入口的形状：**读完必重开**那条不变式要在命令上成立 ===")
    # 背景（2026-09-22 实测事故）：`stop` 与 `start` 是两步，"读完忘了重开"**不报错**，
    # 用户接着操作就一条都录不到（本次丢了「新建表单布局」那一整步）。
    # 修法是把三步合成 `capture.py read`；这一组钉的就是"它还在、而且长得对"。
    result = subprocess.run([PY, str(FT / "tools" / "capture.py"), "--help"],
                            cwd=str(ROOT), capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    out = result.stdout + result.stderr
    check("L1 `capture.py --help` 列出 read/start/stop/list 四个子命令",
          all(name in out for name in ("read", "start", "stop", "list")), out[:200])
    check("L2 `read` 的帮助行写明它会**重新开始监听**（不变式写在用户看得见的地方）",
          "重新开始监听" in out, out[:300])
    check("L3 `read` 的子命令解析可用（`read --help` 不报错且要 --account）",
          "--account" in subprocess.run(
              [PY, str(FT / "tools" / "capture.py"), "read", "--help"],
              cwd=str(ROOT), capture_output=True, text=True,
              encoding="utf-8", errors="replace").stdout)


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


def test_where_probe_is_read_only_and_redacted() -> None:
    print("=== M. 页面身份探针：只读承诺 + 落盘脱形（2026-09-22 新增）===")
    # 背景：用户当面指出「现在都分不清我在哪个页面窗口操作的」——他说的不是我的疏忽，
    # 是**接口层就没有这个信息**：`/api/browser/sessions` 只回 `page.title()`，
    # 而平台**所有页面标题都是 `Akso eGMP (配置)`**；URL / target id / 窗口 id 一个都没有。
    # 另一侧：监听只有 `entry.page` 一个挂点（`_do_open_cdp` 靠 blank 标记页定位），
    # 全仓 `context.on("page")` 零命中、壳侧无 `setWindowOpenHandler`
    # ⇒ 平台 `window.open` 真开出的新窗口**不在监听里**。
    # `where.py` 就是把"我听的是哪一个窗口"固化成可复算的判定。
    sys.path.insert(0, str(FT / "tools"))
    import where  # noqa: PLC0415

    prev = {"cdp": {"pages": [{"id": "aaaaaaaaaaaa", "title": "A", "url": "https://x/1"},
                              {"id": "bbbbbbbbbbbb", "title": "B", "url": "https://x/2"}]}}
    cur = {"cdp": {"pages": [{"id": "bbbbbbbbbbbb", "title": "B2", "url": "https://x/2b"},
                             {"id": "cccccccccccc", "title": "C", "url": "https://x/3"}]}}
    d = where.diff_snapshots(prev, cur)
    check("M1 diff 同时报出「新开」「消失」「被导航」三件事",
          [p["id"] for p in d["added"]] == ["cccccccccccc"]
          and [p["id"] for p in d["removed"]] == ["aaaaaaaaaaaa"]
          and [c["id"] for c in d["changed"]] == ["bbbbbbbbbbbb"],
          json.dumps(d, ensure_ascii=False)[:200])

    # M2：**本工具的产物自己必须过扫描器**。踩过的坑（2026-09-22）：CDP target id 是
    # **32 位连续 hex**，与 `redact.py:114` 的 `[0-9a-fA-F]{32,}` 判据**同形**——
    # `--save` 第一次跑就被扫中 2 处。处置是**采集时截短**（12 位），
    # 不是把 data 目录加进 ALLOWLIST（那等于让这块以后不再被扫）。
    shots = sorted((FT / "data" / "where").glob("*.json"))
    if not shots:
        skip("M2 快照零命中令牌形状", "本机没有 where 快照（data/where/ 不入库）——本条无从校验")
    else:
        bad = [f"{s.name}: {find_secrets(s.read_text(encoding='utf-8'))[:2]}" for s in shots
               if find_secrets(s.read_text(encoding="utf-8"))]
        check("M2 快照零命中令牌形状（target id 必须先截短再落盘）", bad == [], str(bad))

    src = (FT / "tools" / "where.py").read_text(encoding="utf-8")
    check("M3 只读承诺：只发 GET，且源码里不出现抬窗/聚焦与壳侧写接口",
          '"POST"' not in src and "browser/focus" not in src and "/windows" not in src,
          "")
    check("M4 判定语义写在源码里（外部页面 1 个才是可判定；多个必须交人裁定）",
          "外部页面" in src and "无法判定" in src, "")


def test_page_identity_survives_the_pipeline() -> None:
    print("=== N. 窗口身份：新会话要透传，老会话要能加载（**缺省 ≠ 主页面**）===")
    # 背景：用户 2026-09-22 当面指出「分不清我在哪个页面窗口操作的」。录制侧因此补了
    # `context.on("page")` 跟随 + 每条记录带 `pageLabel`/`pageUrl`（见 monitor.py）。
    # 这一组钉的是**采集/分析两侧的兼容性**：
    #   · 新会话：身份必须一路透传到 `Entry`（丢在这里 = 补了也白补，同族：H 组的 `at`）；
    #   · 老会话（≤ 段 4 的 13 个）：**缺键不许报错**，而且必须读成 `None`——
    #     `None` 的正确含义是「那时没有记录窗口」，**不是**「主页面」。
    sys.path.insert(0, str(FT / "analyze"))
    sys.path.insert(0, str(FT / "tools"))
    import ingest  # noqa: PLC0415
    import model  # noqa: PLC0415

    base = {"seq": 1, "at": 1790000000000, "method": "POST", "path": "/api/example/Save",
            "known": True, "postData": {}, "body": None, "precedingActions": []}
    fresh = model._entry_from_row(
        {**base, "pageLabel": "p2", "pageUrl": "https://example.invalid/admin/config/form-layout"})
    check("N1 新形状：pageLabel / pageUrl 透传到 Entry",
          (fresh.page_label, fresh.page_url)
          == ("p2", "https://example.invalid/admin/config/form-layout"),
          f"{fresh.page_label} / {fresh.page_url}")

    legacy = model._entry_from_row(dict(base))
    check("N2 老形状（无这两个键）能加载，且读作 None —— **不是** p1",
          legacy.page_label is None and legacy.page_url is None,
          f"{legacy.page_label} / {legacy.page_url}")

    session = model.Session(name="legacy", meta={}, entries=[legacy])
    check("N3 meta 里没有 pages 键时 Session.pages == {}（空 = 未记录，不许伪造成主页面）",
          session.pages == {}, str(session.pages))

    cleaned = ingest.clean_entry({"method": "POST", "url": "https://example.invalid/api/x"}, 1)
    check("N4 ingest 对老条目照样产出这两个键（值为 None）——消费方不必写 .get 兜底",
          "pageLabel" in cleaned and "pageUrl" in cleaned
          and cleaned["pageLabel"] is None and cleaned["pageUrl"] is None,
          str({k: cleaned.get(k) for k in ("pageLabel", "pageUrl")}))


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
    test_session_arg_accepts_name_and_path()
    test_timestamps_survive_the_loader()
    test_key_audit_script()
    test_key_gate_detector()
    test_nested_key_gate()
    test_capture_cli_shape()
    test_where_probe_is_read_only_and_redacted()
    test_page_identity_survives_the_pipeline()
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
