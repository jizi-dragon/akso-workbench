# LDAP安全策略配置（AD域）

前置：通过”远程桌面连接“ ，输入域控信息【如有白名单限制需发送ip给对应运维】

### 一、密码策略设置

#### 配置路径
1. **打开组策略管理器**
    - `Win + R`  → 输入  `gpmc.msc`  → 进入组策略管理控制台。

![](https://alidocs2.oss-cn-zhangjiakou.aliyuncs.com/res/ybEnB5NQx6pddlP1/img/abd7e1fe-80d3-4d94-8224-bff27789aad8.png?Expires=1789982799&OSSAccessKeyId=LTAI5tKTjg4Kq1HCdBJ8qpSp&Signature=yvihA8NEDI1otMJzhWcBBjpJVWE%3D "")
2. **定位策略**
    - 展开：`林 > 域 > 您的域名`  → 右键  **“Default Domain Policy”**  →  **编辑**。

![](https://alidocs2.oss-cn-zhangjiakou.aliyuncs.com/res/ybEnB5NQx6pddlP1/img/e65d15f1-b667-4180-b24e-c54297248a48.png?Expires=1789982799&OSSAccessKeyId=LTAI5tKTjg4Kq1HCdBJ8qpSp&Signature=K6ig%2BZifk9CeijwUjTCy2X8ZpsM%3D "")
3. **修改密码策略**
    - 路径：`计算机配置 > 策略 > Windows 设置 > 安全设置 > 账户策略 > 密码策略`。

![](https://alidocs2.oss-cn-zhangjiakou.aliyuncs.com/res/ybEnB5NQx6pddlP1/img/5dc505ce-0423-49b6-adcc-3ccab777dfed.png?Expires=1789982799&OSSAccessKeyId=LTAI5tKTjg4Kq1HCdBJ8qpSp&Signature=bjbo6TW8aSbcgd7eUcUo5YAZCcw%3D "")

#### 关键策略项与推荐值

| 策略项 | 说明 | 推荐值 | 安全意义 |
|---------|------|---------|------------|
| **密码必须符合复杂性要求** | 强制包含大小写字母、数字、特殊符号 | **启用** | 防暴力破解 |
| **密码长度最小值** | 密码最短字符数 | **≥8位** | 增加破解难度 |
| **密码最短使用期限** | 用户必须使用新密码的最短天数 | **1天** | 防止频繁改密绕过历史策略 |
| **密码最长使用期限** | 密码过期时间 | **60-90天** | 定期更新防泄露 |
| **强制密码历史** | 系统记住的旧密码数量，防止重复使用 | **24个** | 避免密码复用 |
| **用可逆加密存储密码** | 仅需CHAP认证时启用（如VPN） | **禁用** | 避免密码明文存储风险 |

### 二、账户锁定策略

#### 配置路径
1. **同密码策略入口**
    - 在组策略编辑器中定位至：`账户策略 > 账户锁定策略`。

![](https://alidocs2.oss-cn-zhangjiakou.aliyuncs.com/res/ybEnB5NQx6pddlP1/img/c043f935-ad34-42b8-870a-c9c9bb91359c.png?Expires=1789982799&OSSAccessKeyId=LTAI5tKTjg4Kq1HCdBJ8qpSp&Signature=Uo6BZHzHPwh3zDmhV2sUxhv5AJo%3D "")
2. **设置锁定规则**
    - 双击以下策略项修改：

![](https://alidocs2.oss-cn-zhangjiakou.aliyuncs.com/res/ybEnB5NQx6pddlP1/img/58e03994-7780-4e3d-92ec-0562332447e1.png?Expires=1789982799&OSSAccessKeyId=LTAI5tKTjg4Kq1HCdBJ8qpSp&Signature=dizlxX6z%2B5cGnpM10skicng7EMc%3D "")

#### 关键策略项与推荐值

| 策略项 | 说明 | 推荐值 | 安全意义 |
|---------|------|---------|------------|
| **账户锁定阈值** | 允许的失败登录次数 | **5-7次** | 防暴力破解 |
| **账户锁定时间** | 账户被锁定的持续时间 | **30分钟** | 延缓攻击者尝试 |
| **重置账户锁定计数器** | 失败计数器重置的时间间隔 | **30分钟** | 避免误锁后长期无法登录 |

### 三、注意事项

**避免弱安全配置**
    - ❌ 建议不启用 **“密码永不过期”**（覆盖策略）。
    - ❌ 建议提高复杂性要求（降低破解门槛）。

### 四、基础配置详情

![](https://alidocs2.oss-cn-zhangjiakou.aliyuncs.com/res/ybEnB5NQx6pddlP1/img/a490a366-4fc2-41d4-8da5-1f90878cffee.png?Expires=1789982799&OSSAccessKeyId=LTAI5tKTjg4Kq1HCdBJ8qpSp&Signature=GrIeXd2Y2jVQLgK5YoR9hvbjZ%2B4%3D "")

**启用LDAP**

1、**新用户默认激活**：同步后，默认激活状态

2、**同步域用户**：开启后，才能进行同步

3、**启用重置密码**：启用后，允许对域用户进行重置密码，但是需要**开启域服务器SSL连接**

4、**登录时允许用户输入域名后缀**：

5、**从根目录开始查找**：开启后，会查询基准dn目录自己这一层的用户信息，否则只会查询子级目录的用户信息

**服务器信息**

1、**域名称（特别重要）**：要从属性界面拷贝域名

![](https://alidocs2.oss-cn-zhangjiakou.aliyuncs.com/res/ybEnB5NQx6pddlP1/img/97ff7f80-1c35-42a2-bd64-3707fe413ac1.png?Expires=1789982799&OSSAccessKeyId=LTAI5tKTjg4Kq1HCdBJ8qpSp&Signature=6gGOIVlVpaJtTtkvRQiWJ1muGTw%3D "")

2、**服务器地址**：域服务器地址

3、**端口号**：389

4、**基准DN**：DN能够唯一定位AD域中节点的位置，可以理解为目录的位置。我们签约的客户可能只是某个集团下的子公司，因此eGMP只能去同步此子公司的信息，而不是整个集团，也就是把此子公司的DN作为基准DN。

eGMP将能够在此DN及其所有子级DN中查询数据，可通过如下图的方式在AD域的【用户和计算机】中查看。

例如下图，配置为【OU=产品,DC=corp,DC=akso,DC=com】即可。

![](https://alidocs2.oss-cn-zhangjiakou.aliyuncs.com/res/ybEnB5NQx6pddlP1/img/a4699d5d-599b-4392-83fc-98b65e22c6de.png?Expires=1789982799&OSSAccessKeyId=LTAI5tKTjg4Kq1HCdBJ8qpSp&Signature=umDg6IqZL2A4o7n6ucYciRLs8gM%3D "")

5、**用户目录DN**：当需要查询用户组，且用户组内用户，不一定在DN目录下时，这个DN配置就用来查询用户组内用户

6、**过滤条件**：为空时，默认是查询用户，如果需要查询用户组，配置为：(\|(objectClass=organizationalUnit)(objectClass=group))

7、**SSL连接**：默认关闭，如果需要修改域用户密码，则需要开启，且需要更改SSL对应的端口号

8、**基准DN用户账户**：如需要同步AD域中的用户信息到eGMP，eGMP首先需要一个账户和密码来建立连接，因此需提供一个账户到eGMP。此账户不需要是管理员账户，只要在基准DN下的任一账户即可。

9、**基准DN用户密码**：上述账户的密码

### 五、用户信息匹配规则

若需要从客户的AD域中同步用户信息到eGMP，则需要配置此部分的参数，使eGMP能够顺利找到AD域中的用户信息。

1、**用户名**

此属性为AD域的登录名，将会被同步到eGMP中作为【用户名】，一般配置为【sAMAccountName】。

![](https://alidocs2.oss-cn-zhangjiakou.aliyuncs.com/res/ybEnB5NQx6pddlP1/img/699eecad-e373-4c7a-adef-1fb0248272d3.png?Expires=1789982799&OSSAccessKeyId=LTAI5tKTjg4Kq1HCdBJ8qpSp&Signature=YsoHKZP2vt3aqTp9P9ZUJTtFJfc%3D "")

2、**姓名**

此属性将会被同步到eGMP中作为【名称】，一般配置为【name】。

3、**上级**

此属性将会被同步到eGMP中作为【直线上级】，一般配置为【manager】。

4、**邮箱**

此属性将会被同步到eGMP中作为【邮件地址】，一般配置为【mail】。

5、**电话号码**

此属性将会被同步到eGMP中作为【电话号码】，一般配置为【telephoneNumber】。

6、**人员状态**

此属性将会被同步到eGMP中作为【电话号码】，一般配置为【userAccountControl】。

7、**离职状态**

有内置默认值，无特殊需求不需要填写

8、**离职状态值**

有内置默认值，无特殊需求不需要填写

9、**工号**

跟AD域无默认对应的字段数据，一般是根据客户的需求填写

# 六、定时同步域用户

定时同步域用户通过【运维作业】实现，如下图。

![](https://alidocs2.oss-cn-zhangjiakou.aliyuncs.com/res/ybEnB5NQx6pddlP1/img/2850cd38-454b-4637-9fd5-11a49e0217d0.png?Expires=1789982799&OSSAccessKeyId=LTAI5tKTjg4Kq1HCdBJ8qpSp&Signature=9gqjiCnbZ8UzEy9D6ykUeDuO2QA%3D "")
- URL：**站点的URL**/api/platform/Job/SyncLDAPUser
- 注意：必须使用变量**站点的URL**，不能使用绝对地址，否则配置迁移后无法生效
- 计划时间建议每晚00:00左右

### 七、域用户管理

在已启用 LDAP / AD 并完成用户信息匹配的前提下，管理员可通过域用户与 Sphere 用户的关联，维护可登录平台的域账号。

#### 7.1 创建用户时关联域用户
1. 进入用户管理，创建或编辑 Sphere 用户。
2. 在 **域用户** 字段中选择已有域用户，或新建域用户。
3. 选择域用户后，系统会将域用户信息自动填充到姓名、邮箱、电话等对应基本信息字段；部分由域侧维护的字段会变为只读，避免与目录数据不一致。
4. 同一域用户在同一 Sphere 中只能关联创建一个平台用户，避免重复建号。

域用户支持按关键词搜索；搜索结果底部可新建域用户。新建时按页面提示填写域侧可识别的账号信息，保存后再与 Sphere 用户关联。

#### 7.2 同步与匹配

| 能力 | 说明 |
|------|------|
| 同步开关 | 需开启「同步域用户」后，才能从 AD / LDAP 同步用户信息到平台 |
| 默认同步状态 | 若启用「新用户默认激活」，同步新增用户默认为激活状态，可按业务需要再调整 |
| 匹配规则 | 按第五节「用户信息匹配规则」将域侧属性映射到平台用户名字段、姓名、上级、邮箱、电话、人员状态等 |
| 定时同步 | 通过运维作业定时拉取域用户变更；作业 URL 须使用站点 URL 变量，便于配置迁移后仍可执行 |

同步范围受基准 DN、过滤条件、是否从根目录查找等服务器配置约束。请先在测试环境验证匹配字段与过滤条件，再在生产环境启用定时同步。

#### 7.3 启用、停用与重置密码

| 场景 | 说明 |
|------|------|
| 启用 / 停用 | 域侧人员状态（如 `userAccountControl`）会按匹配规则同步到平台；域账号在目录中停用后，应按同步结果与平台账号状态一并核对，避免已停用人员仍可登录 |
| 离职状态 | 离职相关字段通常有内置默认值，无特殊需求可不改 |
| 重置密码 | 仅在启用「启用重置密码」且开启域服务器 SSL 连接时，才允许在平台侧对域用户重置密码；否则应在 AD / 企业目录中改密 |

域账号的密码复杂度、有效期和历史密码等规则由域侧策略管理，不以平台 [密码策略](25_%E5%AF%86%E7%A0%81%E7%AD%96%E7%95%A5.md) 中的本地密码规则为准。本地密码策略仅作用于平台本地账号。

#### 7.4 与本地用户的差异

| 对比项 | 域用户（LDAP / AD） | 本地用户 |
|---------|------------------------|------------|
| 身份来源 | 企业目录，经 LDAP 同步或关联 | 平台内直接创建和维护 |
| 密码规则 | 以域侧密码策略为准 | 以平台密码策略为准 |
| 资料维护 | 部分字段由域同步填充，关键字段常为只读 | 管理员在用户管理中直接编辑 |
| 停用方式 | 通常随域侧状态同步，并需核对平台账号状态 | 在用户管理中直接停用或注销 |
| 一域一用户 | 同一 Sphere 内一个域用户只能关联一个平台用户 | 不适用 |

建议：日常以域侧为账号主数据源，平台侧重角色、业务单元、权限和组织归属；改密与锁定策略优先在 AD 组策略中统一维护（参见本文第一、二节）。

### 八、相关说明
- 用户安全策略中启用 LDAP 验证后，登录页才会提供域账号认证入口，详见 [用户安全策略](17_%E7%94%A8%E6%88%B7%E5%AE%89%E5%85%A8%E7%AD%96%E7%95%A5.md)。
- 本地账号密码、会话和 MFA 等设置详见 [密码策略](25_%E5%AF%86%E7%A0%81%E7%AD%96%E7%95%A5.md)。
- 用户创建、角色与状态维护详见 [用户管理](01_%E7%94%A8%E6%88%B7%E7%AE%A1%E7%90%86.md)。

---

## 变更记录

| 版本 | 日期 | 修改人 | 修改内容 |
|------|------|---------|------------|
| v1.1 | 2026-08-25 | 王松林 | 扩写域用户管理：同步与匹配、启用停用、与本地用户差异，并交叉引用密码策略。 |
| v1.0 | — | — | 初稿：AD 域密码策略、账户锁定、LDAP 基础配置、匹配规则与定时同步。 |
