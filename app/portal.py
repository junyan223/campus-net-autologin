"""校园网 portal 页面解析与登录提交。"""

from __future__ import annotations

import html as html_module
import ipaddress
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

from .config import APP_DIR
from .network import HEADERS, looks_like_portal_url

log = logging.getLogger("campus")


class PortalError(RuntimeError):
    """认证页不可用或结构不符合预期。"""


@dataclass
class PortalForm:
    page_url: str
    action_url: str
    fields: dict[str, str]
    username_field: str
    password_field: str
    title: str
    page_text: str


@dataclass
class LoginResult:
    outcome: str  # success / already / fail / unknown
    message: str
    http_code: int
    final_url: str
    action_url: str

    @property
    def okish(self) -> bool:
        return self.outcome in ("success", "already")


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict] = []
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._current: dict | None = None
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        raw = {key.lower(): (value if value is not None else "") for key, value in attrs}
        present = {key.lower() for key, _value in attrs}
        if tag == "form":
            self._current = {"attrs": raw, "present": present, "inputs": [], "buttons": []}
            self.forms.append(self._current)
        elif tag == "input" and self._current is not None:
            self._current["inputs"].append({"attrs": raw, "present": present})
        elif tag == "button" and self._current is not None:
            self._current["buttons"].append({"attrs": raw, "present": present})
        elif tag in ("script", "style"):
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "form":
            self._current = None
        elif tag in ("script", "style") and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if not self._skip_depth:
            self.text_parts.append(data)


_USERNAME_HINTS = re.compile(
    r"usr|user|account|login|name|uid|phone|mobile|tel|student|工号|学号", re.IGNORECASE
)
_TEXT_TYPES = {"text", "email", "tel", "search", "number", ""}

_META_REFRESH_RE = re.compile(
    r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]*content=["\'][^"\']*?url=([^"\'>\s]+)',
    re.IGNORECASE,
)
_JS_LOCATION_RE = re.compile(
    r'(?:window\.|top\.|document\.)?location(?:\.href)?\s*=\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_JS_REPLACE_RE = re.compile(
    r'location\.replace\(\s*["\']([^"\']+)["\']', re.IGNORECASE
)


def _plain_text(html_text: str) -> str:
    text = re.sub(r"<script\b.*?</script>", " ", html_text, flags=re.S | re.I)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_module.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _pick_form(forms: list[dict]) -> dict:
    def score(form: dict) -> tuple[int, int, int]:
        inputs = form["inputs"]
        types = [item["attrs"].get("type", "text").lower() for item in inputs]
        has_password = int("password" in types)
        action = form["attrs"].get("action", "").lower()
        is_login = int("login" in action or "auth" in action)
        return has_password, is_login, len(inputs)

    return max(forms, key=score)


def parse_portal_page(html_text: str, page_url: str) -> PortalForm:
    parser = _FormParser()
    try:
        parser.feed(html_text)
    except Exception as exc:  # noqa: BLE001
        raise PortalError(f"无法解析认证页：{exc}") from exc

    if not parser.forms:
        if "缺少相关参数" in html_text:
            raise PortalError("认证页缺少参数（没有从校园网重定向进入），请稍后重试")
        raise PortalError("认证页中没有找到登录表单")

    form = _pick_form(parser.forms)
    attrs = form["attrs"]
    method = (attrs.get("method", "post") or "post").strip().lower()
    if method != "post":
        raise PortalError(f"认证表单使用 {method.upper()} 提交，出于安全考虑不自动填写")

    hidden: dict[str, str] = {}
    username_field = ""
    password_field = ""
    text_candidates: list[str] = []

    for item in form["inputs"]:
        item_attrs = item["attrs"]
        name = item_attrs.get("name", "").strip()
        if not name:
            continue
        input_type = (item_attrs.get("type", "text") or "text").strip().lower()

        if input_type == "password":
            password_field = password_field or name
            continue

        if input_type in ("hidden",):
            hidden[name] = item_attrs.get("value", "")
        elif input_type in ("checkbox", "radio"):
            if "checked" in item["present"]:
                hidden[name] = item_attrs.get("value", "") or "on"
        elif input_type in _TEXT_TYPES:
            if _USERNAME_HINTS.search(name) and not username_field:
                username_field = name
            else:
                text_candidates.append(name)
                if item_attrs.get("value"):
                    hidden[name] = item_attrs.get("value", "")
        # submit / button / file 等一律忽略

    if not password_field:
        raise PortalError("认证页中没有密码输入框，页面结构可能已变化")
    if not username_field:
        # 退而求其次：取第一个普通文本输入框
        username_field = text_candidates[0] if text_candidates else "usrname"

    action = attrs.get("action", "").strip()
    action_url = urljoin(page_url, action) if action else page_url
    title = re.sub(r"\s+", " ", "".join(parser.title_parts)).strip()
    page_text = _plain_text(html_text)[:800]

    return PortalForm(
        page_url=page_url,
        action_url=action_url,
        fields=hidden,
        username_field=username_field,
        password_field=password_field,
        title=title,
        page_text=page_text,
    )


def fetch_portal_form(
    session: requests.Session, portal_url: str, timeout: float = 8.0
) -> PortalForm:
    try:
        response = session.get(
            portal_url,
            headers=HEADERS,
            timeout=(3.0, timeout),
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        raise PortalError(f"打开认证页失败：{type(exc).__name__}") from exc

    if response.status_code >= 400:
        raise PortalError(f"认证页返回 HTTP {response.status_code}")
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = "utf-8"
    return parse_portal_page(response.text, response.url)


def _interpret(html_text: str) -> tuple[str, str]:
    plain = _plain_text(html_text)
    haystack = f"{plain} {html_text[:8000]}"

    success_words = (
        "登录成功",
        "登陆成功",
        "登录已成功",
        "认证成功",
        "认证通过",
        "连接成功",
        "上网成功",
        "已上线",
        "login success",
        "authentication success",
    )
    if any(word in haystack for word in success_words):
        return "success", "登录成功"

    if any(word in haystack for word in ("已在线", "已经在线", "已登录", "已登陆")):
        return "already", "账号已在线"

    fail_words = (
        "登录失败",
        "登陆失败",
        "认证失败",
        "认证被拒绝",
        "密码错误",
        "密码不正确",
        "密码有误",
        "用户名或密码",
        "账号或密码",
        "用户名不存在",
        "用户不存在",
        "账号不存在",
        "禁止登录",
        "拒绝登录",
        "不允许登录",
        "已锁定",
        "锁定",
        "次数过多",
        "已绑定",
        "绑定失败",
        "达到上限",
        "欠费",
        "余额不足",
        "停机",
        "其他设备",
        "重复登录",
        "请先下线",
        "MAC地址",
        "mac地址",
        "物理地址",
        "MAC绑定",
        "MAC校验",
        "MAC不匹配",
        "MAC错误",
        "MAC不一致",
        "校验失败",
        "不匹配",
        "缺少相关参数",
        "参数错误",
        "非法请求",
        "系统繁忙",
        "繁忙",
        "超时",
    )
    for word in fail_words:
        if word in haystack:
            return "fail", _short_reason(plain, word) or word

    # 该厂商的失败页固定带“警告！”，即使提示语没覆盖到也按失败处理
    if "警告" in haystack or "禁止" in haystack:
        reason = _short_reason(plain, "警告") or _short_reason(plain, "禁止")
        return "fail", reason or "门户拒绝了本次登录"

    return "unknown", (_short_reason(plain, "") or "认证结果未知")


def _short_reason(text: str, keyword: str) -> str:
    if keyword:
        index = text.find(keyword)
        if index >= 0:
            start = max(0, index - 30)
            return text[start : index + 90].strip()
    return text[:160].strip()


def _extract_redirect_url(html_text: str, base_url: str) -> str | None:
    """从只含 JS / meta 跳转的中间页里提取下一步地址。"""
    for pattern in (_META_REFRESH_RE, _JS_LOCATION_RE, _JS_REPLACE_RE):
        match = pattern.search(html_text)
        if not match:
            continue
        candidate = html_module.unescape(match.group(1)).strip()
        if not candidate:
            continue
        target = urljoin(base_url, candidate)
        if urlparse(target).scheme not in ("http", "https"):
            continue
        base_host = (urlparse(base_url).hostname or "").lower()
        target_host = (urlparse(target).hostname or "").lower()
        if target_host == base_host or looks_like_portal_url(target):
            return target
    return None


_BASE64_RE = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")


def _redact_text(text: str, secrets: list[str] | None = None) -> str:
    for secret in secrets or []:
        if secret:
            text = text.replace(secret, "***")
    return _BASE64_RE.sub("***", text)


def _redact_html(html_text: str, secrets: list[str] | None = None) -> str:
    # 所有 input 的值一律打码（不依赖字段名，防止未知 token 泄露）
    text = re.sub(
        r'(<input\b[^>]*?value=["\'])([^"\']*)(["\'])',
        r"\1***\3",
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(
        r"(<textarea\b[^>]*>)(.*?)(</textarea>)",
        r"\1***\3",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    # 脚本里可能带 token/跳转参数，诊断文件不需要执行内容
    text = re.sub(
        r"(<script\b[^>]*>)(.*?)(</script>)",
        r"\1[脚本已移除]\3",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return _redact_text(text, secrets)

def _save_debug_response(
    html_text: str, secrets: list[str] | None = None
) -> Path | None:
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        path = APP_DIR / "portal_last_response.html"
        path.write_text(_redact_html(html_text, secrets), encoding="utf-8")
        return path
    except OSError:
        return None


def _submit_auto_form(
    session: requests.Session,
    html_text: str,
    base_url: str,
    timeout: float,
) -> requests.Response | None:
    """门户可能返回一个隐藏的自动提交表单（浏览器会执行 JS 提交），这里手动完成。"""
    lowered = html_text.lower()
    if "submit(" not in lowered and "requestsubmit(" not in lowered:
        return None
    parser = _FormParser()
    try:
        parser.feed(html_text)
    except Exception:  # noqa: BLE001
        return None

    for form in parser.forms:
        attrs = form["attrs"]
        method = (attrs.get("method", "post") or "post").strip().lower()
        if method != "post":
            continue
        inputs = form["inputs"]
        types = [item["attrs"].get("type", "text").lower() for item in inputs]
        if "password" in types:  # 避免重复提交登录页本身
            continue
        fields: dict[str, str] = {}
        for item in inputs:
            item_attrs = item["attrs"]
            name = item_attrs.get("name", "").strip()
            if not name:
                continue
            input_type = (item_attrs.get("type", "text") or "text").lower()
            if input_type == "hidden":
                fields[name] = item_attrs.get("value", "")
            elif input_type in ("checkbox", "radio"):
                if "checked" in item["present"]:
                    fields[name] = item_attrs.get("value", "") or "on"
            elif input_type in ("submit", "button", "reset", "image", "file"):
                continue
            elif item_attrs.get("value"):
                fields[name] = item_attrs.get("value", "")
        if not fields:
            continue

        action = attrs.get("action", "").strip()
        action_url = urljoin(base_url, action) if action else base_url
        try:
            _validate_action_host(action_url, base_url)
        except PortalError:
            continue
        parsed = urlparse(action_url)
        headers = dict(HEADERS)
        headers.update(
            {
                "Referer": base_url,
                "Origin": f"{parsed.scheme}://{parsed.netloc}",
                "Content-Type": "application/x-www-form-urlencoded",
            }
        )
        log.info("检测到门户自动提交表单，正在继续认证：%s", action_url)
        try:
            return session.post(
                action_url,
                data=fields,
                headers=headers,
                timeout=(3.5, timeout),
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            log.warning("提交门户二次表单失败：%s", type(exc).__name__)
            return None
    return None


def _validate_action_host(action_url: str, page_url: str) -> None:
    """只允许把凭据提交到内网地址或与页面同域，避免被伪造跳转到外部站点。"""
    action_host = (urlparse(action_url).hostname or "").lower()
    page_host = (urlparse(page_url).hostname or "").lower()
    is_private = False
    try:
        parsed_ip = ipaddress.ip_address(action_host)
        is_private = parsed_ip.is_private or parsed_ip.is_link_local
    except ValueError:
        pass
    if not is_private and action_host != page_host:
        raise PortalError(f"认证表单提交地址不是校园网内网地址（{action_host or '空'}），已拒绝提交")


def attempt_login(
    portal_url: str,
    username: str,
    password: str,
    timeout: float = 12.0,
) -> LoginResult:
    session = requests.Session()
    session.trust_env = False
    session.headers.update({"User-Agent": HEADERS["User-Agent"]})

    form = fetch_portal_form(session, portal_url, timeout=min(8.0, timeout))
    _validate_action_host(form.action_url, form.page_url)
    log.info("认证页：%s（表单：%s）", form.action_url, form.title or "无标题")
    log.info(
        "认证参数：nasid=%s usrmac=%s usrip=%s basip=%s",
        form.fields.get("nasid", ""),
        form.fields.get("usrmac", ""),
        form.fields.get("usrip", ""),
        form.fields.get("basip", ""),
    )

    data = dict(form.fields)
    data[form.username_field] = username
    data[form.password_field] = password

    parsed = urlparse(form.action_url)
    post_headers = dict(HEADERS)
    post_headers.update(
        {
            "Referer": form.page_url,
            "Origin": f"{parsed.scheme}://{parsed.netloc}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Upgrade-Insecure-Requests": "1",
        }
    )

    try:
        response = session.post(
            form.action_url,
            data=data,
            headers=post_headers,
            timeout=(3.5, timeout),
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        raise PortalError(f"提交认证失败：{type(exc).__name__}") from exc

    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        response.encoding = "utf-8"

    outcome, message = _interpret(response.text)
    # 门户可能返回隐藏的自动提交表单，或只含 JS / meta 跳转的中间页；浏览器会继续执行
    for _ in range(4):
        if outcome != "unknown":
            break
        auto_response = _submit_auto_form(
            session, response.text, response.url, timeout
        )
        if auto_response is not None:
            response = auto_response
            if not response.encoding or response.encoding.lower() == "iso-8859-1":
                response.encoding = "utf-8"
            outcome, message = _interpret(response.text)
            continue
        target = _extract_redirect_url(response.text, response.url)
        if not target:
            break
        log.info("认证响应含脚本跳转，正在跟随：%s", target)
        try:
            response = session.get(
                target,
                headers={**HEADERS, "Referer": form.action_url},
                timeout=(3.5, timeout),
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            log.warning("跟随认证跳转失败：%s", type(exc).__name__)
            break
        if not response.encoding or response.encoding.lower() == "iso-8859-1":
            response.encoding = "utf-8"
        outcome, message = _interpret(response.text)

    message = _redact_text(message, [username, password])
    log.info(
        "认证响应：HTTP %s，最终地址=%s，正文长度=%s，结果=%s，信息=%s",
        response.status_code,
        response.url,
        len(response.text),
        outcome,
        message or "-",
    )
    if outcome == "unknown":
        debug_path = _save_debug_response(response.text, [username, password])
        if debug_path is not None:
            log.warning("未能识别的认证响应已保存（已脱敏）：%s", debug_path)
    return LoginResult(
        outcome=outcome,
        message=message,
        http_code=response.status_code,
        final_url=response.url,
        action_url=form.action_url,
    )