# 云机器安装结果与目标提示修复

修复 Windows 安装回执被非零 SSH 退出码覆盖的问题。保留安装器返回的具体错误；安装程序成功退出但精确注册名称/版本不匹配时显示 `verification_required`，保留预期名称、版本、哈希、退出码和日志位置，不认定版本验证成功。缺失回执及完成回执与退出码矛盾时显示 interrupted，要求核验目标机后再操作。

云机器页面展示本次任务固定的机器和软件目标；租约到期或回收后禁用启动、登录及连接入口，并排除安装候选。安装提交后禁用重复提交；页面刷新后从 sessionStorage 恢复结果查询；查询网络错误不改变任务结果。安装完成与应用启动/翻译功能验证分别提示。待核验任务不提供直接重复安装操作。

验证：
- `python3 -m unittest tests.test_cloud_install_status tests.test_cloud_console tests.test_software_install tests.test_portable_software tests.test_cloud_workflow tests.test_cloud_workflow_http -q`：74 项，61 通过，13 跳过（本机无 PowerShell）。
- `node tests/web/cloud_machines_status_qa.cjs`：通过，覆盖任务目标、翻译未验证提示、租约回收禁用、恢复查询及版本待核验提示。
- `node --check agentpair/web_assets/cloud_machines.js` 与 `git diff --check`：通过。

限制：未重新创建付费 Windows 实例，PowerShell 安装分支未在 Windows 执行。没有修改同事正在开发的数据中心文件。

发布：2026-10-11（北京时间）已发布到 `https://50.118.187.180`。部署前确认无活动任务及未结束的云安装；仅更新 cloud_console.py、cloud_machines.html、cloud_machines.js、software-install.ps1。备份位于服务器 `/opt/agentpair-backups/cloud-install-status-20261010T201028Z.tar.gz`。服务、回收定时器、全部文件哈希检查通过；HTTPS 登录后页面和脚本均 HTTP 200 且与本地字节一致，云机器 API 返回正常（6 条均已回收的记录）。
