# 校园网自动登录v0.2（Campus Net AutoLogin）

A Windows desktop tool for automatic captive-portal login on campus / public Wi-Fi. It supports multiple accounts bound to different Wi-Fi networks, optional custom portal URLs, system tray operation, and DPAPI-encrypted credentials. The UI is in Chinese.

> 本项目只模拟正常的网页认证流程，不绕过任何认证。请遵守所在学校 / 网络服务商的用户协议。

> **提示**：v0.1 已发布优化修复版（新增更新检查、界面缩放和 GitHub 入口）；旧 v0.1 保留在 Releases 中，建议下载「校园网自动登录v0.1（修复版）」。
>
> **v0.2 修复版**：新增应用内更新、界面缩放和 GitHub 入口；版本号仍为 v0.2。

## 功能

- **应用内更新**：版本号右侧「检查更新」；每天首次启动或点击按钮检查；弹窗显示版本、日志和 SHA256，下载校验后自动替换并重启。
- **界面缩放**：首次启动自动适配；运行设置可选择 自动 / 100% / 125% / 150% / 175% / 200% / 250%，重启生效；小屏或高缩放时整页和日志可滚动。
- **GitHub 入口**：右上角打开项目仓库。
- **多账户**：每个账户绑定一个 Wi-Fi；支持自定义账户名称、用户名、密码和登录页地址。
- **自动认证**：按间隔检测 Wi-Fi 与联网状态；发现门户认证页后自动提交。
- **自动检测开关**：检测间隔旁可开启/关闭自动检测（默认开启）；关闭后停止按间隔检测，仍可用「立即检测并登录」手动检测。
- **兼容现代门户**：支持“两步”隐藏表单自动提交（浏览器执行 JS 二次提交）以及 JS / meta 跳转。
- **自动保存**：修改账户或运行设置后自动保存，顶部会短暂显示“设置已保存”；不再需要手动点保存。
- **WiFi 自动连接**：每个账户旁可开启/关闭 Windows 对该 WiFi 的“自动连接”。
- **托盘运行**：可关闭“关闭窗口时最小化到托盘”；不勾选时关闭窗口会直接退出，后台检测停止。
- **高优先级自启**：可选创建登录时计划任务，比普通开机自启更早启动（首次开启需要管理员授权）。
- **本地安全**：密码使用 Windows DPAPI 加密；日志与诊断文件脱敏；无服务器、无遥测。
- **无预设账户**：首次启动不会创建任何账户，必须自己点「＋ 新建」添加。

## 工作原理

1. 读取当前 Wi-Fi SSID；
2. 与当前账户绑定的 SSID 比较；
3. 访问联网探测地址；若被重定向到门户，抓取认证页；
4. 解析并提交表单（用户名 / 密码 / 隐藏字段）；
5. 自动跟进门户返回的二次自动提交表单或 JS / meta 跳转；
6. 最长等待约 15 秒确认网络恢复；失败按 15s / 30s / … 退避重试。

程序不会自动切换账户；换到别的网络时，请在界面选择对应账户。

## 使用

1. Windows 10/11；从 [Releases](https://github.com/junyan223/campus-net-autologin/releases) 下载 `校园网自动登录v0.2` exe，或从源码运行。
2. 打开程序 → 「＋ 新建」→ 填写账户名称、选择/输入 Wi-Fi、登录页（一般留空自动探测）、账号密码；修改后会自动保存。
3. 运行设置里保持「自动检测」开启（默认），程序会按检测间隔自动检查；关闭后只保留手动「立即检测并登录」。
4. 建议勾选 WiFi 行的「自动连接」，让 Windows 在开机/锁屏阶段自动连上该 WiFi；如需更早启动本程序，可勾选「开机自启动（高优先级）」。
5. 登录页留空 = 自动探测；自动探测失败时可点「探测」抓取当前认证页，或粘贴完整登录页 URL（含参数）。
6. 默认关闭窗口会最小化到托盘；取消勾选「关闭窗口时最小化到托盘」后，关闭窗口会直接退出程序。
7. 右上角「检查更新」可随时检查更新；运行设置里的「界面缩放」可以调整界面大小，重启后生效。

## 从源码运行

Python 3.12+（Windows）：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main.py
```

## 构建

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe build.py
```

生成：

- `dist/校园网自动登录v0.2.exe`：Windows 单文件（首次启动无预设账户）。

## 安全与隐私

- 配置路径：`%APPDATA%\CampusNetAutoLogin\config.json`。
- **程序本体（源码 / Release exe）不包含任何账号、密码或特定校园网地址**；账号密码和自定义登录页只保存在本机配置中。
- 密码使用 Windows DPAPI（当前用户）加密；换电脑 / 换 Windows 用户需要重新输入。
- 「开机自启动（高优先级）」使用 Windows 计划任务：注册/删除时需要一次管理员授权（UAC），任务本身以当前用户的最低权限运行，不会保存或传递密码。
- 日志（`app.log`）与诊断页已脱敏；请勿分享配置、日志或诊断文件。
- 「登录页」建议留空自动探测；手动探测 / 填写的完整地址可能包含本机 IP/MAC 参数，会保存在本机配置里，不需要时清空。
- 门户认证通常是 **HTTP 明文**；密码在传输中如何保护取决于门户协议，本程序无法为其加密。请只在可信网络使用，并避免复用重要密码。
- 程序没有服务器、没有遥测；凭据只会提交到检测到的门户表单地址（内网或同域）。

## 已知限制

- 仅 Windows。
- 不处理图形验证码、短信验证码、扫码或强 JS 加密门户；这些网络仍需手动登录。
- 账户切换为手动；门户改版可能导致自动登录失效，日志会记录原因。
- 首次启动没有预设账户，需要自己添加。
- Windows 不会在用户登录前运行普通用户程序；「开机自启动（高优先级）」最早只能在**登录时**启动，无法覆盖锁屏未登录阶段。WiFi 的「自动连接」可让无线在锁屏阶段自动关联，但门户认证仍会在登录后由本程序完成。
- 移动 exe 位置后，需要在程序里重新保存一次开机自启设置。
- 更新检查依赖 GitHub API；如果当前网络无法访问 GitHub，会提示检查失败，可在网络恢复后重试。

## 项目结构

```text
app/            # 配置、Wi-Fi/探测、门户解析、控制器、托盘、计划任务、UI
main.py         # 主程序入口
build.py        # PyInstaller 构建脚本
assets/app.ico  # 图标
```

## 免责声明

本项目仅供学习与个人便利使用。请遵守所在学校及网络服务商的用户协议，不要用于绕过访问控制。使用风险自负。

本项目由Ai制作。

This project is not affiliated with any network provider. Use only with credentials you are authorized to use.

## License

MIT，详见 [LICENSE](LICENSE)。