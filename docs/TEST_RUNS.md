# 构建就绪后的 Windows 验收任务

入口 `/test-runs`（管理员），从云机器页可进入。这是新增的验收流程，不替代管理员通用资源管理接口；通用开机接口仍可创建用途不限的机器。软件测试应走本入口，不能用通用开机来跳过就绪检查。

## 已实现

1. 服务端调用公开 GitHub API，核对指定 Actions run 已成功、仓库及 head SHA 一致、发布标签解析到同一提交、登记的安装包 URL 与 release asset 一致且 GitHub digest 等于登记 SHA256。三个验收项必须写出步骤和标准。缺少任何项不报价、不创建机器；确认时再次核验构建与安装方案。
2. 就绪后报价有效 10 分钟。管理员确认精确金额和 60 分钟租约才调度。创建 requestId 在调用云接口前持久化；重复确认不再次分配。创建结果不明时核对原请求，不能重新开机。
3. 自动创建、验证 SSH、按确认清单安装并核对版本及哈希回执。安装回执、安全观测、实时采集与 CPU 结论分开，安装完成只能进入 `awaiting_results`。
4. 任务页完成后再调度下一次轮询，隐藏页面降低刷新频率。展示步骤时间戳、版本、安装回执、租约到期、费用估算、验收证据、释放回执。未变化的详情不重建，避免阅读时收起。费用是从确认起按报价估算，不是账单。
5. 固定只读 PowerShell CPU 模板：采样约 10 秒，记录逻辑核心数、进程 CPU 增量、内存和 PID。页面可查看命令源及退出码。只统计名称匹配 WorkBuddy / AgentReins 的进程，**不覆盖所有 WebView 或其他名称的子进程**；场景由操作者指定，不能自动证明正在运行任务或窗口隐藏。
6. 完成、失败、停止和安装回执异常都保留同一机器。第58分钟生成回收询问，第59分钟未回复则回收；选择保留则到原租约第60分钟回收。停止测试不等于回收。服务重启后恢复原租约计时，不重复开机或安装。独立 reaper 仍在原租约到期时兜底；不自动续费。

## 安装与功能验收

先在 `/software` 登记固定版本、URL 与 SHA256。被测软件必须有同一源码提交的成功 CI 与带 digest 的 GitHub 发布资产；可使用 prerelease，不要求正式发布。没有该证明的旧包不能顶替当前测试版。当前只支持公开 GitHub 仓库；私有仓库和其他 CI 的服务端验证器尚未实现。

三个必需验收项：

- `realtime_events`：执行受控任务，核对原生会话/轮次/工具事件与界面显示。
- `security_observation`：执行明确授权的受控活动，核对系统及安全证据，不用时间相近冒充因果。
- `cpu_idle_active_hidden`：准备三个场景，按计划采样并保存原始指标、时长、进程覆盖与阈值。

本版自动化到部署及只读 CPU 采样；真实 Agent 任务、GUI 操作和三项结论需执行者实测后提交，来源明确标记 `administrator`。证据固定后不能覆盖。任何一项失败结束测试但保留机器；三项全部通过才为 `passed`。没有证据在第58分钟结束测试并进入回收询问。安装完成后先进入 `awaiting_login`，管理员确认桌面登录后才进入 `awaiting_results`，确认来源不冒充系统身份验证。

**未实现：**远程桌面直播/录屏、截图自动采集、任意命令终端、完整进程树性能采集、Agent 专用自动功能脚本及独立验证其语义。页面回执不能替代这些能力；本版不宣称完整无人值守验收。不要在管理员证据文本中粘贴凭据或个人真实任务内容。

## API

所有读取仅管理员可用，写入沿用登录、Origin、CSRF 校验。

- `POST /api/test-runs`：`softwareIds`、`subjectSoftwareId`、`build={repository,runId,commit,tag}`、`plan={realtime_events,security_observation,cpu_idle_active_hidden}`。返回就绪记录与报价，不开机。
- `GET /api/test-runs`：轻量列表；`GET /api/test-runs/{id}`：完整时间线。
- `POST /api/test-runs/{id}/confirm`：`requestId,confirmed=true,maxHourlyCNY`。
- `POST /api/test-runs/{id}/probe`：`mode=idle|active|hidden`；固定只读模板，无任意 shell。
- `POST /api/test-runs/{id}/result`：`case,passed,evidence`；明确记录人工来源，不自动把采样或安装当作通过。
- `POST /api/test-runs/{id}/cancel`：停止后续调度，保留机器；原回收窗口不变。
- `POST /api/test-runs/{id}/login-confirm`：`{confirmed:true}`，仅在登录步骤接受管理员的人工确认，不接受验证码。
- `POST /api/test-runs/{id}/release-decision`：`{choice:"release"|"keep"}`，仅原租约第58至59分钟接受一次回复；保留不续费。
- `POST /api/cloud/release`：管理员释放已有指定租约，并独立复查主机消失。

记录存于数据库同目录的 `test-runs/*.json`，权限 0600。不把云 API/SSH 异常原文写进公共回执。安装仅投影白名单证据，CPU 命令不读取应用正文、凭据或会话日志。

释放回执独立证明 `remainingHostCount=0`；公网 IP 和数据盘由现有资源管理器以 `ReleaseEIP=True,ReleaseUDisk=True` 请求一起释放。回执中的 `releaseEIPRequested/releaseUDiskRequested` 是请求记录，**不等于分别查询证明它们不存在**。若需要完整账单级回收审计，后续应持久化资源 ID 并分别复查。

## 验证及上线

本地全部使用 FakeConsole，不创建计费资源：

```sh
python3 -m unittest discover -s tests -p 'test_cloud*.py' -q
python3 -m unittest discover -s tests -p 'test_test_runs*.py' -q
python3 -m unittest test_resources -q
node --check agentpair/web_assets/test_runs.js
```

四步主界面、人工登录步骤和新的回收窗口已于北京时间2026-10-11 20:34部署；91项次相关回归通过。线上服务和文件校验通过，浏览器线上显示核验仍因超时未完成。此前版本已部署，并实机验证 SSH 与下载安装；安装最终回执缺失，机器被旧策略提前释放。完整 Windows 功能与性能验收未完成。上线时必须带上新 Python 模块、三个 web assets 和 `windows/test-process-cpu.ps1`；重启前核查现有在途云操作。新增验收目录不影响旧任务记录，已有通用云工作流保持原语义。上线后先验证无账单的准备失败路径与权限；安装包构建成功并再次确认预算后才做云端验收。

## 四步主界面与桌面能力

主界面显示开机、软件安装、账号登录、测试及人类可读结果；构建哈希和日志默认折叠。当前没有嵌入式远程桌面，明确显示“尚未接入实时画面”，不能把步骤卡或日志当作完整可视化。第58分钟询问显示在工作台和云机器页；页面关闭时服务端仍会按一分钟无回复回收，没有外部推送能力。
