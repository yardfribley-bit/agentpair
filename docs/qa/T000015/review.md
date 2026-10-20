# agentpair 项目问题审核报告

- 审核员：Grok Bot · 2026-10-20 18:30–19:00 UTC
- 代码：`github.com/yardfribley-bit/agentpair`，默认分支 `codex/agentpair-mvp`，HEAD `58b55de`（2026-10-12）
- 线上：`https://www.chuhaijian.com/`。AgentPair 占用整个域名根路径（`/`、`/model-security`、`/model-data`、`/devices`、`/session-insights/` 等）；调度平台挂在同域名的 `/task/` 下。`https://50.118.187.180/` 是同一服务。
- 方法：
  - 代码：通读 README、PRODUCT_SCOPE 和 platform.py 路由，跑全部测试，扫描仓库里的密钥。
  - 线上：只做**匿名、只读**的 GET 请求和无头浏览器渲染（桌面 1440、手机 390）。没有注册、登录或提交任何东西，也没有做任何攻击。
- 本报告**不含任何真实凭据**。涉及敏感数据的地方只写接口位置和数量。

## 一、项目是什么

AgentPair 自称「AI 应用安全分析 + Agent 协作平台」，实际上是三样东西拼在一起：

1. **AppLens / SessionLens**：装在 macOS / Windows 上的采集器。抓 WorkBuddy、Codex 等 AI 应用发给模型的上下文和会话日志，上报到平台。
2. **AgentPair 网页平台**：
   - 数据中心：检索采集记录。
   - 交互安全审计：自动发现「凭据进入模型输入」和「无关背景进入模型输入」两类威胁，带处置闭环（分派、验证、关闭）。
   - 会话洞察。
   - 任务中心：Navigator 规划、Driver 执行的 Agent 协作。
   - 云机器和软件包管理。
3. PRODUCT_SCOPE.md 还列了第三个方向：「行业动态与文章发布」。

目标用户在文档里没有写清楚。从功能看，是想让企业安全团队审计员工用 AI 时泄露了什么。

## 二、结论

**有严重问题，不建议在当前状态下对外推广。**

- 最大的问题是 **P0：匿名访问者能读到全部采集数据**。这些数据包括：
  - 设备主机名、归属账号；
  - 用户发给模型的原文片段；
  - 凭据上下文里的服务器 IP 和账号名；
  - 完整的任务对话。

  凭据值本身已经遮蔽，但对一个「AI 安全审计」产品来说，把审计数据向公网开放，等于反向制造泄露。
- 其次：
  - 开放注册并赠送模型额度，任何注册用户都能对**任何设备**的数据发起模型分析；
  - 核心功能「数据中心」默认打开就超时；
  - 产品定位不清。
- 做得好的部分：
  - CSP、nosniff、HttpOnly + SameSite=Strict 会话 Cookie；
  - POST 请求校验 CSRF token 和 Origin；
  - 管理接口返回 403；
  - 设备用独立令牌；
  - 服务端渲染做了 HTML 转义；
  - 凭据预览有遮蔽（904 条证据里有 899 条带「已隐藏」标记）；
  - 仓库里没有发现真实密钥，命中的都是测试夹具。

## 三、问题清单

| ID | 严重度 | 问题 | 证据（位置 / 步骤 / 实际 / 预期） |
|---|---|---|---|
| AP-01 | **P0** | 匿名可读全部采集与审计数据 | **位置**：`agentpair/platform.py:338-351`（`/api/audit/credential-threats`、`/api/audit/devices`、`/api/devices/interaction-audit/<id>?scope=global`）、`:311-316`（`/api/audit/model-data/<device>`）、`:107-112`（没有 owner 的任务对所有人可见）、`:208-215`（`/devices` 页把主机名和归属账号直接渲染进 HTML）。<br>**步骤**：不带 Cookie 依次 GET 这些地址。<br>**实际**：<br>• credential-threats 返回 1.2MB：87 项发现、904 条证据；预览原文里约 1000 处 IP、47 处邮箱、账号名；credentialDisplay 带用户名或邮箱前缀。<br>• 单台设备的 model-data 返回 340KB（400 次模型调用，含本机 home 路径）。<br>• interaction-audit 返回 100 条原始记录。<br>• `/api/tasks` 列出 19 个任务；`/api/tasks/<id>` 返回完整对话（70KB）。<br>• 首页匿名直接显示某台设备的主机名和「服务器 root 登录密码」类发现。<br>**预期**：匿名最多看脱敏统计；明细、原文和设备信息必须登录，且只能看本人或授权范围。README 写的「向访客开放是部署选择」不适用于含真实数据的生产环境。 |
| AP-02 | **P1** | 开放注册 + 赠送额度 + 全局范围的模型分析 | **位置**：`platform.py:517-521`（任何人都能注册，没有验证码或邀请码，注册赠送 ¥2 模型额度，见 `:58`）；`:538-545`（`/api/collection/questions` 传 `scope=global` 时，只要设备存在就以设备所有者身份分析，不校验请求者是否有权限）。<br>**实际**：任何新注册用户都能拿别人设备的数据调用付费模型。批量注册还能薅额度：全站注册限流是共享的 10 次/分钟，最多约 600 个账号/小时，相当于约 ¥1200/小时。<br>**预期**：`global` 范围只给管理员或明确授权的人；注册要有邀请码或人机验证，额度按实名或审批发放；设置总预算熔断。 |
| AP-03 | **P1** | 核心功能「数据中心」默认就失败 | **步骤**：打开 `/model-data?scope=global` 或 `/model-data/rankings`（桌面和手机都试过）。<br>**实际**：首屏请求 `/api/data-center/search?...device=all...` 和 `/api/data-center/capabilities?...` 返回 409「搜索超过处理时限」。指定单台设备加关键词也是 409。<br>**预期**：默认视图在 3 秒内返回（分页、限定时间窗、建索引或预聚合）；超时也不应成为首屏的默认状态。另外，这是匿名就能触发的重查询，等于拒绝服务入口，见 AP-07。 |
| AP-04 | **P1** | 首页内联脚本被 CSP 拦截，登录回跳失效 | **位置**：`agentpair/web_assets/workspace.html:8`，内联的 `<script id="session-insights-login-return">`；CSP 是 `script-src 'self'`（`platform.py:90`）。<br>**实际**：浏览器控制台报「Executing inline script violates CSP」，`?insights=login` 回跳逻辑不会执行；同时有 2 处内联样式被拦截、1 个资源 404。<br>**预期**：把内联脚本移到外部 JS，或用 nonce/hash 放行；首页控制台没有错误。 |
| AP-05 | P2 | 登录 / 注册限流是全站共享的 | **位置**：`platform.py:512-516`，`attempts` 是进程级列表，不分 IP 或账号。<br>**实际**：任何人每分钟发 10 次错误登录，全站所有用户（包括管理员）就都登不上了。<br>**预期**：按 IP 和账号分别限流，失败退避，管理员通道单独处理。 |
| AP-06 | P2 | 产品定位不清，首页不解释自己 | **实际**：<br>• 首页（`d_home.png`）一进来就是某个历史任务的对话，没有说明这是什么、给谁用、能解决什么问题、怎么开始。<br>• 名字有四个：AgentPair、AppLens、SessionLens、AgentReins。<br>• PRODUCT_SCOPE 把安全数据分析、设备进程分析、行业文章发布三个不相关的方向并列。<br>• 手机首屏直接是对话卡片，看不到导航和页面标题（`m_home.png`）。<br>**预期**：首屏用一句话说明价值和目标用户，给演示数据或引导；收敛到一个主场景（建议「企业 AI 使用的数据泄露审计」）；其他方向放到子产品或路线图里。 |
| AP-07 | P2 | 匿名重查询可以拖垮服务 | **位置**：`platform.py:299-304`，数据中心搜索不需要登录。<br>**实际**：每次匿名请求都要跑到超时（几十秒）才返回 409。<br>**预期**：重查询需要登录，并按身份限流、加缓存。 |
| AP-08 | P2 | 与调度平台同源部署 | **实际**：AgentPair（`/`）和调度平台（`/task/`）在同一个 origin，AgentPair 的 Cookie `Path=/` 也会发给 `/task/`；任何一边出现 XSS，都能直接操作另一边。另外：<br>• 没有 HSTS；<br>• 80 端口不通，直接输入 `http://` 或不带协议的地址打不开；<br>• 裸域 `chuhaijian.com` 连不上。<br>**预期**：两个系统分开子域名（如 `pair.` 和 `task.`）；加 HSTS；80 端口跳转到 443；裸域跳转到 www。 |
| AP-09 | P2 | 测试不全绿，也没有统一入口 | **步骤**：在仓库根目录 `python3 -m unittest discover -p 'test_*.py'`，再分别跑 `tests/` 和 `SessionLens/tests`。<br>**实际**：<br>• 根目录 291 个用例，2 个失败：`test_devices.py:41` 的心跳快照断言和实现不一致（通过 test_endpoint_analysis / test_experiences 被导入了两次）。<br>• `tests/` 474 个用例，2 个导入错误（`test_collection_answer_evidence_qa`、`test_collection_retrieval_qa`：attempted relative import）。<br>• `SessionLens/tests` 231 个用例，9 个错误（i18n、桌面 UI 模块导入失败，部分是缺少桌面依赖）。<br>• README 只列了一部分测试命令。<br>**预期**：一条命令跑全部测试，CI 必须全绿；缺依赖的用例显式标记 skip。 |
| AP-10 | P2 | 文档与线上不一致 | README 的「运行页面」还指向裸 IP，并写着「域名 HTTPS 尚未验证」，但域名已经能用；README 也承认部分浏览器测试脚本里写死了开发机路径。<br>**预期**：README 以域名为准，测试脚本不依赖本机路径。 |
| AP-11 | P3 | 仓库结构混乱 | 根目录堆了约 60 个 `test_*.py`、部署脚本和 18 份设计文档。部署脚本写死了生产 IP 和开发机临时私钥路径（`deploy_web.py:14-15`、`deploy_persistent_navigator.py:12-14`、`run_cloud_site_pair.py:21`，只有路径，没有密钥内容）。默认分支名是 `codex/agentpair-mvp`。<br>**预期**：测试统一放 `tests/`，部署参数改成环境变量，默认分支改为 main。 |
| AP-12 | P3 | 线上版本不可追溯 | 没有版本接口或页面版本号，没法确认线上跑的是不是 `58b55de`。<br>**预期**：提供 `/api/version`，返回 commit 和构建时间。 |

## 四、建议修复顺序

1. **今天**：AP-01，把匿名访问收到只看脱敏统计（或者整站先要求登录）；把首页默认展示的真实任务换成演示数据。
2. **本周**：
   - AP-02：注册加门槛，global 范围限管理员，加预算熔断。
   - AP-05：限流改为按 IP 和账号。
   - AP-07：重查询需要登录。
   - AP-03：数据中心默认视图能用。
   - AP-04：CSP 下首页无错误。
3. **下周**：
   - AP-08：拆分子域名，加 HSTS、80 跳转、裸域跳转。
   - AP-09 / AP-10：测试全绿，修文档。
   - AP-06：首页重写，产品收敛到一个主场景。
4. **之后**：AP-11、AP-12，仓库整理和版本追溯。

## 五、验收标准（复测时逐条核对）

1. 匿名 GET 以下接口：要么 401/403，要么只返回不含原文、主机名、账号、IP、邮箱的统计。
   - `/api/audit/credential-threats`
   - `/api/audit/devices`
   - `/api/audit/model-data/<id>`
   - `/api/devices/interaction-audit/<id>?scope=global`
   - `/api/tasks`、`/api/tasks/<id>`
   - `/devices`、`/api/resources`

   匿名打开首页不出现任何真实设备或任务。
2. 普通注册用户对别人的设备发 `scope=global` 的问题，返回 403。注册需要邀请码或人机验证；有全站模型预算上限，超过后拒绝。
3. 从同一 IP 连续错误登录，只封这个 IP 或账号；换 IP 后管理员能正常登录。
4. 登录用户打开 `/model-data?scope=global` 和 `/model-data/rankings`，首屏 3 秒内返回结果，没有 409。匿名调用 search 或 capabilities 返回 401，或者是有限流的轻量结果。
5. 无头浏览器在桌面 1440 和手机 390 下打开首页、`/model-security`、`/model-data`、`/devices`、`/session-insights/`：控制台 0 错误，没有横向溢出，`?insights=login` 回跳生效。
6. AgentPair 和调度平台分属不同子域名；响应带 HSTS；`http://` 和裸域都跳转到 HTTPS 的 www。
7. 一条命令跑完全部 Python 测试（根目录、`tests/`、`SessionLens/tests`），0 失败、0 错误（缺依赖的用例显式 skip），CI 上能看到结果。
8. README 的线上地址、测试命令和实际一致；`/api/version` 返回的 commit 与仓库一致。
9. 首页能让第一次来的人在 10 秒内看懂：这是什么、给谁用、下一步做什么。

## 六、附：证据说明

- 匿名探测只记录了状态码、响应大小和计数；含真实数据的原始响应只留在审核员本机，不外发。
- 截图与控制台记录、全量测试输出由审核员留存；复测时会按第五节重新采集。
