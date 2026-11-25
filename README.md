# 校园网自动登录（Campus Net AutoLogin）

A Windows desktop tool for automatic captive-portal login on campus / public Wi-Fi. It supports multiple accounts bound to different Wi-Fi networks, optional custom portal URLs, system tray operation, and DPAPI-encrypted credentials. The UI is in Chinese.

> 本项目只模拟正常的网页认证流程，不绕过任何认证。请遵守所在学校 / 网络服务商的用户协议。

## 功能

- **多账户**：每个账户绑定一个 Wi-Fi；支持自定义账户名称、用户名、密码和登录页地址。
- **自动认证**：按间隔检测 Wi-Fi 与联网状态；发现门户认证页后自动提交。
- **兼容现代门户**：支持“两步”隐藏表单自动提交（浏览器执行 JS 二次提交）以及 JS / meta 跳转。
- **托盘运行**：关闭窗口到系统托盘；单实例（再次双击唤起）；可选开机自启。
- **本地安全**：密码使用 Windows DPAPI 加密；日志与诊断文件脱敏；无服务器、无遥测。
- **空白共享版**：`main_blank.py` 构建无任何默认账户的版本，适合分享。

## 工作原理

1. 读取当前 Wi-Fi SSID；
2. 与当前账户绑定的 SSID 比较；
3. 访问联网探测地址；若被重定向到门户，抓取认证页；
4. 解析并提交表单（用户名 / 密码 / 隐藏字段）；
5. 自动跟进门户返回的二次自动提交表单或 JS / meta 跳转；
6. 最长等待约 15 秒确认网络恢复；失败按 15s / 30s / … 退避重试。

程序不会自动切换账户；换到别的网络时，请在界面选择对应账户。

## 使用

1. Windows 10/11；从 [Releases](https://github.com/junyan223/campus-net-autologin/releases) 下载共享空白版 exe，或从源码运行。
2. 打开程序 → 「＋ 新建」→ 填写账户名称、选择/输入 Wi-Fi、登录页（一般留空自动探测）、账号密码 → 保存设置。
3. 连接该 Wi-Fi 后，程序会在检测间隔内自动登录。
4. 登录页留空 = 自动探测；自动探测失败时可点「探测」抓取当前认证页，或粘贴完整登录页 URL（含参数）。
5. 关闭窗口会最小化到托盘；双击托盘图标或再次双击 exe 可打开。

## 从源码运行

Python 3.12+（Windows）：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe main_blank.py
```

## 构建共享空白版

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe build.py
```

生成：

- `dist/校园网自动登录 v0.1.exe`：共享空白版（`main_blank.py`，独立配置目录与单实例，首次打开无账户）。

> 仓库发布的 exe 只有共享空白版；标准构建仅用于本地开发，不随仓库发布。

## 安全与隐私

- 配置路径：`%APPDATA%\CampusNetAutoLogin\config.json`；共享版为 `%APPDATA%\CampusNetAutoLoginBlank\config.json`。
- **程序本体（源码 / Release exe）不包含任何账号、密码或特定校园网地址**；账号密码和自定义登录页只保存在本机配置中。
- 密码使用 Windows DPAPI（当前用户）加密；换电脑 / 换 Windows 用户需要重新输入。
- 日志（`app.log`）与诊断页已脱敏；请勿分享配置、日志或诊断文件。
- 「登录页」建议留空自动探测；手动探测 / 填写的完整地址可能包含本机 IP/MAC 参数，会保存在本机配置里，不需要时清空。
- 门户认证通常是 **HTTP 明文**；密码在传输中如何保护取决于门户协议，本程序无法为其加密。请只在可信网络使用，并避免复用重要密码。
- 程序没有服务器、没有遥测；凭据只会提交到检测到的门户表单地址（内网或同域）。

## 已知限制

- 仅 Windows。
- 不处理图形验证码、短信验证码、扫码或强 JS 加密门户；这些网络仍需手动登录。
- 账户切换为手动；门户改版可能导致自动登录失效，日志会记录原因。
- 共享版默认没有账户，首次打开请点「＋ 新建」。

## 项目结构

```text
app/            # 配置、Wi-Fi/探测、门户解析、控制器、托盘、UI
main.py         # 主程序入口（共用）
main_blank.py   # 共享空白版入口
build.py        # PyInstaller 构建脚本（共享空白版）
assets/app.ico  # 图标
```

## 免责声明

本项目仅供学习与个人便利使用。请遵守所在学校及网络服务商的用户协议，不要用于绕过访问控制。使用风险自负。

本项目由Ai制作。

This project is not affiliated with any network provider. Use only with credentials you are authorized to use.

## License

MIT，详见 [LICENSE](LICENSE)。