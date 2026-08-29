#!/usr/bin/env python3
"""Scan an authenticated QQ Zone account and build an offline HTML archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

try:
    import requests
except ImportError as exc:  # pragma: no cover - environment-dependent
    raise SystemExit(
        "✕ 还缺少 requests。先运行 python3 -m pip install 'requests>=2.33.0,<3'，再回来继续整理吧。"
    ) from exc


QR_SHOW = "https://ssl.ptlogin2.qq.com/ptqrshow"
QR_LOGIN = "https://ssl.ptlogin2.qq.com/ptqrlogin"
PORTRAIT = "https://r.qzone.qq.com/fcg-bin/cgi_get_portrait.fcg"
HISTORY = "https://user.qzone.qq.com/proxy/domain/ic2.qzone.qq.com/cgi-bin/feeds/feeds2_html_pav_all"
MOMENTS = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128 Safari/537.36"
CACHE_ROOT = Path.home() / ".cache" / "qzone-post-recovery"
MIN_REQUESTS_VERSION = (2, 33, 0)
MAX_REQUESTS_VERSION = (3, 0, 0)


class RecoveryError(RuntimeError):
    pass


def ensure_supported_requests(current: str | None = None) -> None:
    """Reject dependency versions outside the security-reviewed range."""
    value = current or getattr(requests, "__version__", "")
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", value)
    version = tuple(int(part) for part in match.groups()) if match else ()
    if not version or version < MIN_REQUESTS_VERSION or version >= MAX_REQUESTS_VERSION:
        raise RecoveryError(
            f"当前 requests 版本 {value or '未知'} 不在安全支持范围；请运行 "
            "python3 -m pip install 'requests>=2.33.0,<3'"
        )


def say(symbol: str, message: str, *, indent: int = 0, flush: bool = False) -> None:
    """Print one calm, consistent user-facing status line."""
    prefix = "  " * indent
    print(f"{prefix}{symbol} {message}", flush=flush)


def phase(number: int, title: str, detail: str) -> None:
    print()
    say(f"[{number}/4]", title)
    say("└─", detail, indent=1)


def welcome() -> None:
    print("\n╭──────────────────────────────╮")
    print("│  ✨ QQ 空间 · 时光档案助手  │")
    print("╰──────────────────────────────╯")
    say("ℹ", "我们会对照历史痕迹与当前内容，帮你整理出值得确认的删除候选。")


def bkn(p_skey: str) -> int:
    value = 5381
    for character in p_skey:
        value += (value << 5) + ord(character)
    return value & 0x7FFFFFFF


def ptqr_token(qrsig: str) -> int:
    value = 0
    for character in qrsig:
        value += (value << 5) + ord(character)
    return value & 0x7FFFFFFF


def clean_text(value: Any) -> str:
    text = re.sub(r"t{5,}", " ", str(value or ""))
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def normalized_text(value: Any) -> str:
    text = clean_text(value).lower()
    text = re.sub(r"\[em\][^[]+\[/em\]", "", text)
    return re.sub(r"[\s\u3000，。！？、；：,.!?;:'\"“”‘’（）()【】\[\]{}<>《》]+", "", text)


def normalize_media_url(url: str) -> str:
    url = clean_text(url)
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("http://"):
        url = "https://" + url[7:]
    return url


def normalize_uin(value: Any) -> str:
    match = re.fullmatch(r"[oq]?0*(\d+)", clean_text(value), re.IGNORECASE)
    return (match.group(1).lstrip("0") or "0") if match else ""


def media_key(url: str) -> str:
    value = normalize_media_url(url)
    value = value.replace("/m&", "/s&").replace("!/m/", "!/s/")
    value = re.sub(r"[?&](?:tm|dis_t|dis_k|sce|rf)=[^&]*", "", value)
    return value


def parse_event_text(value: str) -> dict[str, str]:
    value = clean_text(value)
    forward = re.match(r"^(.*?)\s+转了此条信息\s+(.*?)\s*[：:]\s*(.*)$", value, re.S)
    if forward:
        return {
            "owner": clean_text(forward.group(1)),
            "body": clean_text(forward.group(3)),
            "event_type": "forward",
            "original_author": clean_text(forward.group(2)),
        }
    publish = re.match(r"^(.*?)\s+发表说说\s*$", value, re.S)
    if publish:
        return {
            "owner": clean_text(publish.group(1)),
            "body": "（历史接口仅保留“发表说说”事件，正文未保留）",
            "event_type": "publish_event",
            "original_author": "",
        }
    activity = re.match(r"^(.*?)\s+((?:评论了|回复了|赞了|提到了|@了).*)$", value, re.S)
    if activity:
        action = clean_text(activity.group(2))
        if action.startswith(("评论了", "回复了")):
            event_type = "comment"
        elif action.startswith("赞了"):
            event_type = "like"
        else:
            event_type = "mention"
        return {
            "owner": clean_text(activity.group(1)),
            "body": action,
            "event_type": event_type,
            "original_author": "",
        }
    colon = re.match(r"^(.*?)\s*[：:]\s*(.*)$", value, re.S)
    if colon:
        return {
            "owner": clean_text(colon.group(1)),
            "body": clean_text(colon.group(2)),
            "event_type": "post",
            "original_author": "",
        }
    return {"owner": "", "body": value, "event_type": "interaction", "original_author": ""}


def parse_datetime(value: Any) -> datetime | None:
    text = clean_text(value)
    match = re.search(r"\d{4}年\d{1,2}月\d{1,2}日\s+\d{1,2}:\d{2}(?::\d{2})?", text)
    if match:
        text = match.group(0)
    for fmt in ("%Y年%m月%d日 %H:%M:%S", "%Y年%m月%d日 %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def cookie_value(session: requests.Session, name: str) -> str:
    values = [cookie.value for cookie in session.cookies if cookie.name == name]
    return values[-1] if values else ""


def account_uin(session: requests.Session) -> str:
    raw = cookie_value(session, "uin") or cookie_value(session, "p_uin")
    match = re.search(r"\d+", raw)
    if not match:
        raise RecoveryError("登录 Cookie 中没有找到 QQ 号")
    return match.group(0).lstrip("0") or "0"


def session_headers(uin: str = "") -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        "Accept": "*/*",
    }
    if uin:
        headers["Referer"] = f"https://user.qzone.qq.com/{uin}/main"
    return headers


def validate_qq_url(url: str) -> str:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    allowed = ("qq.com", "qpic.cn", "gtimg.com", "gtimg.cn")
    if parsed.scheme != "https" or not any(hostname == suffix or hostname.endswith("." + suffix) for suffix in allowed):
        raise RecoveryError("拒绝访问非 QQ HTTPS 资源")
    try:
        port = parsed.port
    except ValueError as exc:
        raise RecoveryError("资源 URL 端口无效") from exc
    if parsed.username or parsed.password or port not in (None, 443):
        raise RecoveryError("资源 URL 不应包含凭据或自定义端口")
    return url


def safe_qq_media_url(value: Any) -> str:
    url = normalize_media_url(clean_text(value))
    if not url:
        return ""
    try:
        return validate_qq_url(url)
    except RecoveryError:
        return ""


def request_retry(session: requests.Session, url: str, *, params: dict[str, Any] | None = None,
                  timeout: int = 25, retries: int = 2) -> requests.Response:
    validate_qq_url(url)
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            current = url
            for hop in range(6):
                response = session.get(current, params=params if hop == 0 else None, timeout=timeout,
                                       allow_redirects=False)
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        raise RecoveryError("重定向缺少 Location")
                    current = validate_qq_url(urljoin(current, location))
                    continue
                if response.status_code == 429:
                    raise RecoveryError("QQ 接口返回 429，已停止高频重试")
                response.raise_for_status()
                return response
            raise RecoveryError("QQ 请求重定向超过 5 次")
        except (requests.RequestException, RecoveryError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RecoveryError(f"请求失败：{urlparse(url).netloc}（{last_error}）")


def ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError as exc:
        raise RecoveryError(f"无法把私有目录权限设为 0700：{path}") from exc
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        raise RecoveryError(f"文件系统没有落实私有目录权限 0700：{path}")


def write_private_bytes(path: Path, content: bytes) -> None:
    ensure_private_directory(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError as exc:
            raise RecoveryError(f"无法把私有文件权限设为 0600：{path}") from exc
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise RecoveryError(f"文件系统没有落实私有文件权限 0600：{path}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def save_session(session: requests.Session, uin: str) -> None:
    ensure_private_directory(CACHE_ROOT)
    directory = CACHE_ROOT / "sessions"
    ensure_private_directory(directory)
    path = directory / f"{uin}.json"
    payload = [
        {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path or "/"}
        for c in session.cookies
    ]
    write_private_bytes(path, json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def load_session(path: Path) -> requests.Session:
    try:
        path.chmod(0o600)
    except OSError as exc:
        raise RecoveryError(f"无法修复会话缓存权限：{path}") from exc
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise RecoveryError(f"会话缓存不是私有文件 0600：{path}")
    session = requests.Session()
    session.headers.update(session_headers())
    for item in json.loads(path.read_text(encoding="utf-8")):
        session.cookies.set(item["name"], item["value"], domain=item.get("domain") or None,
                            path=item.get("path") or "/")
    return session


def decode_portrait(response: requests.Response, uin: str) -> str:
    text = response.content.decode("gb18030", errors="replace")
    match = re.search(r"portraitCallBack\((.*)\)\s*;?", text, re.S)
    if not match:
        raise RecoveryError("无法解析 QQ 昵称")
    data = json.loads(match.group(1))
    values = data.get(uin)
    if not isinstance(values, list) or len(values) < 7:
        raise RecoveryError("QQ 昵称响应不完整")
    return clean_text(values[6]) or uin


def get_nickname(session: requests.Session, uin: str) -> str:
    p_skey = cookie_value(session, "p_skey")
    if not p_skey:
        raise RecoveryError("登录会话缺少 p_skey")
    response = request_retry(session, PORTRAIT, params={"g_tk": bkn(p_skey), "uins": uin}, retries=0)
    return decode_portrait(response, uin)


def choose_cached_session() -> tuple[requests.Session, str, str] | None:
    sessions = CACHE_ROOT / "sessions"
    if CACHE_ROOT.exists():
        ensure_private_directory(CACHE_ROOT)
    if sessions.exists():
        ensure_private_directory(sessions)
    paths = sorted(sessions.glob("*.json"))
    if not paths:
        return None
    say("🔐", "发现之前保存的本机会话，可以省去一次扫码：")
    for index, path in enumerate(paths, 1):
        say("•", f"{index}. QQ {path.stem}", indent=1)
    answer = input("   请选择序号；输入 0 使用新二维码（直接回车默认选 1）› ").strip() or "1"
    if not answer.isdigit() or int(answer) < 1 or int(answer) > len(paths):
        return None
    path = paths[int(answer) - 1]
    try:
        session = load_session(path)
        uin = account_uin(session)
        nickname = get_nickname(session, uin)
        say("✓", f"欢迎回来，已连接 QQ {uin}（{nickname}）。")
        return session, uin, nickname
    except Exception as exc:
        say("↻", f"旧会话已经失效，没关系，我们换一张新二维码。原因：{exc}")
        return None


def open_file(path: Path) -> None:
    try:
        converted = subprocess.run(["wslpath", "-w", str(path)], check=True, capture_output=True,
                                   text=True).stdout.strip()
        subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                "Start-Process -LiteralPath ([Console]::In.ReadToEnd())",
            ],
            input=converted, text=True, check=True, timeout=10,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return
    except (OSError, subprocess.SubprocessError):
        pass
    for command in (["wslview", str(path)], ["xdg-open", str(path)]):
        try:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        except OSError:
            continue


def parse_ptui(text: str) -> list[str]:
    return re.findall(r"'([^']*)'", text)


def qr_login(*, auto_open: bool = True) -> tuple[requests.Session, str, str]:
    ensure_private_directory(CACHE_ROOT)
    qr_path = CACHE_ROOT / "QR.png"
    while True:
        session = requests.Session()
        session.headers.update(session_headers())
        response = request_retry(session, QR_SHOW, params={
            "appid": "549000912", "e": "2", "l": "M", "s": "3", "d": "72",
            "v": "4", "t": str(time.time()), "daid": "5", "pt_3rd_aid": "0",
        }, retries=1)
        qrsig = cookie_value(session, "qrsig")
        if not qrsig:
            raise RecoveryError("二维码响应缺少 qrsig")
        write_private_bytes(qr_path, response.content)
        say("📱", "登录二维码准备好了，正在尝试为你打开。")
        say("📄", f"如果没有自动弹出，可以手动打开：{qr_path}", indent=1)
        if auto_open:
            open_file(qr_path)
        say("→", "请用手机 QQ 扫码，并在手机上点一下确认；这个窗口会自动继续。")
        token = ptqr_token(qrsig)
        expired = False
        while True:
            params = {
                "u1": "https://qzs.qq.com/qzone/v5/loginsucc.html?para=izone",
                "ptqrtoken": str(token), "ptredirect": "0", "h": "1", "t": "1", "g": "1",
                "from_ui": "1", "ptlang": "2052", "action": f"0-0-{int(time.time() * 1000)}",
                "js_ver": "20032614", "js_type": "1", "login_sig": "", "pt_uistyle": "40",
                "aid": "549000912", "daid": "5", "pt_3rd_aid": "0",
            }
            poll = request_retry(session, QR_LOGIN, params=params, retries=0)
            fields = parse_ptui(poll.text)
            code = fields[0] if fields else ""
            message = fields[4] if len(fields) > 4 else poll.text[:80]
            if code == "0":
                redirect = fields[2] if len(fields) > 2 else ""
                if not redirect.startswith("https://"):
                    raise RecoveryError("扫码成功，但登录跳转地址无效")
                request_retry(session, redirect, retries=1)
                uin = account_uin(session)
                nickname = get_nickname(session, uin)
                save_session(session, uin)
                say("✓", f"登录成功，正在为 {nickname}（QQ {uin}）整理时光档案。")
                return session, uin, nickname
            if code == "65" or "失效" in message:
                say("↻", "这张二维码刚刚过期了，正在悄悄换一张新的……")
                expired = True
                break
            if code == "67" or "认证中" in message:
                say("…", "已经扫到了，等你在手机上确认。")
            elif code not in ("66", ""):
                say("•", f"QQ 返回的登录状态：{message}")
            time.sleep(2)
        if not expired:
            raise RecoveryError("二维码登录未完成")


def authenticated_session(*, fresh_login: bool, auto_open: bool) -> tuple[requests.Session, str, str]:
    if not fresh_login:
        cached = choose_cached_session()
        if cached:
            return cached
    return qr_login(auto_open=auto_open)


def decode_js_html(text: str) -> str:
    match = re.search(r"html\s*:\s*'(.*)'\s*,\s*opuin", text, re.S)
    if not match:
        return ""
    value = match.group(1)
    value = re.sub(r"\\x([0-9a-fA-F]{2})", lambda m: chr(int(m.group(1), 16)), value)
    value = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), value)
    replacements = {r"\/": "/", r"\'": "'", r'\"': '"', r"\n": "\n", r"\r": "", r"\t": " "}
    for source, target in replacements.items():
        value = value.replace(source, target)
    return value.replace("\\\\", "\\")


class FeedHTMLParser(HTMLParser):
    VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.records: list[dict[str, Any]] = []
        self.current: dict[str, Any] | None = None
        self.li_depth = 0
        self.time_depth = 0
        self.text_depth = 0
        self.image_depth = 0
        self.owner_depth = 0
        self.stack: list[tuple[str, bool, bool, bool, bool]] = []

    @staticmethod
    def classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        values = dict(attrs).get("class") or ""
        return set(values.split())

    @staticmethod
    def owner_uin(attrs: list[tuple[str, str | None]]) -> str:
        attributes = dict(attrs)
        for key in ("data-uin", "data-opuin", "data-owneruin", "uin"):
            if uin := normalize_uin(attributes.get(key)):
                return uin
        for key in ("link", "href"):
            value = attributes.get(key) or ""
            for pattern in (r"namecard[_-](\d+)", r"user\.qzone\.qq\.com/(\d+)(?:/|$)"):
                if match := re.search(pattern, value, re.IGNORECASE):
                    return normalize_uin(match.group(1))
        return ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        classes = self.classes(attrs)
        if tag == "li":
            if self.current is not None:
                self.li_depth += 1
            elif {"f-single", "f-s-s"}.issubset(classes):
                self.current = {
                    "time_parts": [], "text_parts": [], "images": [],
                    "owner_parts": [], "owner_uin": "",
                }
                self.li_depth = 1
        starts_time = bool(self.current is not None and tag == "div" and "info-detail" in classes)
        starts_text = bool(self.current is not None and tag == "p" and {"txt-box-title", "ellipsis-one"}.issubset(classes))
        starts_image = bool(self.current is not None and tag == "a" and "img-item" in classes)
        starts_owner = bool(self.current is not None and tag == "a" and {"f-name", "q_namecard"}.issubset(classes))
        if starts_time:
            self.time_depth += 1
        if starts_text:
            self.text_depth += 1
        if starts_image:
            self.image_depth += 1
        if starts_owner:
            self.owner_depth += 1
            if self.current is not None and not self.current["owner_uin"]:
                self.current["owner_uin"] = self.owner_uin(attrs)
        if tag not in self.VOID_TAGS:
            self.stack.append((tag, starts_time, starts_text, starts_image, starts_owner))
        if self.current is not None and tag == "img" and self.image_depth:
            attributes = dict(attrs)
            src = attributes.get("data-src") or attributes.get("src") or ""
            if safe_url := safe_qq_media_url(src):
                self.current["images"].append(safe_url)

    def handle_data(self, data: str) -> None:
        if self.current is None:
            return
        if self.time_depth:
            self.current["time_parts"].append(data)
        if self.text_depth:
            self.current["text_parts"].append(data)
        if self.owner_depth:
            self.current["owner_parts"].append(data)

    def handle_endtag(self, tag: str) -> None:
        matching = next((index for index in range(len(self.stack) - 1, -1, -1)
                         if self.stack[index][0] == tag), None)
        if matching is not None:
            closing = self.stack[matching:]
            del self.stack[matching:]
            starts_time = any(item[1] for item in closing)
            starts_text = any(item[2] for item in closing)
            starts_image = any(item[3] for item in closing)
            starts_owner = any(item[4] for item in closing)
            self.time_depth -= int(starts_time)
            self.text_depth -= int(starts_text)
            self.image_depth -= int(starts_image)
            self.owner_depth -= int(starts_owner)
        if tag == "li" and self.current is not None:
            self.li_depth -= 1
            if self.li_depth == 0:
                self.records.append({
                    "time": clean_text(" ".join(self.current["time_parts"])),
                    "text": clean_text(" ".join(self.current["text_parts"])),
                    "images": list(dict.fromkeys(self.current["images"])),
                    "owner_name": clean_text(" ".join(self.current["owner_parts"])),
                    "owner_uin": self.current["owner_uin"],
                })
                self.current = None


def parse_history_response(text: str) -> list[dict[str, Any]]:
    html = decode_js_html(text)
    if not html:
        return []
    parser = FeedHTMLParser()
    parser.feed(html)
    return [record for record in parser.records if record["text"]]


def fetch_history(session: requests.Session, uin: str, *, max_history: int,
                  page_delay: float) -> tuple[list[dict[str, Any]], bool, list[str]]:
    p_skey = cookie_value(session, "p_skey")
    token = bkn(p_skey)
    records: list[dict[str, Any]] = []
    warnings: list[str] = []
    complete = True
    page_size = 100
    previous_signature = ""
    phase(2, "翻阅历史痕迹", "这一阶段通常最花时间，请保留窗口开启。")
    for offset in range(0, max_history, page_size):
        params = {
            "uin": uin, "begin_time": "0", "end_time": "0", "getappnotification": "1",
            "getnotifi": "1", "has_get_key": "0", "offset": str(offset), "set": "0",
            "count": str(min(page_size, max_history - offset)), "useutf8": "1",
            "outputhtmlfeed": "1", "scope": "1", "format": "jsonp", "g_tk": str(token),
        }
        try:
            response = request_retry(session, HISTORY, params=params)
        except RecoveryError as exc:
            complete = False
            warnings.append(f"历史互动流在 offset={offset} 中断：{exc}")
            break
        page = parse_history_response(response.content.decode("utf-8", errors="replace"))
        if not page:
            break
        signature = hashlib.sha1(json.dumps(page[:3], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if signature == previous_signature:
            warnings.append("历史接口开始重复返回同一页，已停止以避免死循环。")
            complete = False
            break
        previous_signature = signature
        records.extend(page)
        say("↳", f"已整理 {len(records)} 条历史痕迹", indent=1, flush=True)
        if len(page) < page_size:
            break
        time.sleep(max(0.2, page_delay))
    else:
        complete = False
        warnings.append(f"达到 --max-history={max_history} 上限，历史扫描可能不完整。")
    return records[:max_history], complete, warnings


def parse_jsonp(text: str) -> dict[str, Any]:
    start = text.find("(")
    end = text.rfind(")")
    payload = text[start + 1:end] if start >= 0 and end > start else text
    return json.loads(payload.strip().rstrip(";"))


def fetch_visible(session: requests.Session, uin: str, *,
                  page_delay: float) -> tuple[list[dict[str, Any]], bool, list[str]]:
    p_skey = cookie_value(session, "p_skey")
    token = bkn(p_skey)
    warnings: list[str] = []

    def page(pos: int, num: int) -> dict[str, Any]:
        params = {
            "uin": uin, "ftype": "0", "sort": "0", "pos": str(pos), "num": str(num),
            "replynum": "100", "g_tk": str(token), "callback": "_preloadCallback",
            "code_version": "1", "format": "jsonp", "need_private_comment": "1",
        }
        response = request_retry(session, MOMENTS, params=params)
        data = parse_jsonp(response.content.decode("utf-8", errors="replace"))
        if data.get("code") != 0:
            raise RecoveryError(f"当前说说接口错误：{data.get('message') or data.get('code')}")
        return data

    phase(3, "核对当前空间", "正在读取仍然可见的说说，以及接口允许返回的评论。")
    first = page(0, 1)
    if "total" not in first:
        raise RecoveryError("当前说说接口缺少 total，无法判断是否已完整读取")
    try:
        total = int(first.get("total") or 0)
    except (TypeError, ValueError) as exc:
        raise RecoveryError("当前说说接口返回了无效的 total") from exc
    if total < 0:
        raise RecoveryError("当前说说接口返回了负数 total")
    records: list[dict[str, Any]] = []
    complete = True
    page_size = 30
    seen_record_keys: set[str] = set()
    for pos in range(0, total, page_size):
        try:
            data = page(pos, page_size)
        except RecoveryError as exc:
            warnings.append(f"当前可见说说在 pos={pos} 中断：{exc}")
            complete = False
            break
        page_records = data.get("msglist") or []
        if not isinstance(page_records, list):
            warnings.append(f"当前可见说说在 pos={pos} 返回了无效列表。")
            complete = False
            break
        duplicate_count = 0
        for item in page_records:
            if not isinstance(item, dict):
                complete = False
                continue
            tid = clean_text(item.get("tid"))
            key = tid or hashlib.sha1(
                f"{item.get('created_time')}:{item.get('content')}".encode("utf-8")
            ).hexdigest()
            if key in seen_record_keys:
                duplicate_count += 1
                complete = False
                continue
            seen_record_keys.add(key)
            records.append(item)
        if duplicate_count:
            warnings.append(f"当前可见说说在 pos={pos} 重复返回 {duplicate_count} 条，已停止重复计数。")
        say("↳", f"已核对 {len(records)}/{total} 条当前说说", indent=1, flush=True)
        time.sleep(max(0.05, min(page_delay, 0.8)))
    if len(records) < total:
        warnings.append(f"当前接口报告 {total} 条，实际读取 {len(records)} 条。")
        complete = False
    return records, complete, warnings


def comment_from_api(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "time": clean_text(item.get("createTime2") or item.get("createTime")),
        "content": clean_text(item.get("content")),
        "nickname": clean_text(item.get("name")) or "未知用户",
        "uin": str(item.get("uin") or ""),
    }


def current_record(item: dict[str, Any], nickname: str, uin: str) -> dict[str, Any]:
    timestamp = int(item.get("created_time") or 0)
    dt = datetime.fromtimestamp(timestamp) if timestamp else None
    images: list[str] = []
    videos: list[str] = []
    for picture in item.get("pic") or []:
        url = picture.get("url1") or picture.get("url2") or picture.get("smallurl")
        if safe_url := safe_qq_media_url(url):
            images.append(safe_url)
    for video in item.get("video") or []:
        cover = video.get("url1") or video.get("pic_url")
        link = video.get("url3") or video.get("url2")
        if safe_cover := safe_qq_media_url(cover):
            images.append(safe_cover)
        if safe_link := safe_qq_media_url(link):
            videos.append(safe_link)
    comments = [comment_from_api(comment) for comment in item.get("commentlist") or []]
    tid = clean_text(item.get("tid"))
    return {
        "id": "visible-" + (tid or hashlib.sha1(f"{timestamp}:{item.get('content')}".encode()).hexdigest()[:16]),
        "category": "visible", "kind": "当前可见", "owner": clean_text(item.get("name")) or nickname,
        "owner_uin": normalize_uin(uin),
        "body": clean_text(item.get("content")), "original_author": "", "event_type": "post",
        "timestamps": [dt.strftime("%Y-%m-%d %H:%M:%S") if dt else clean_text(item.get("createTime"))],
        "timestamp_value": timestamp, "year": str(dt.year) if dt else "未知",
        "images": list(dict.fromkeys(images)), "remote_images": list(dict.fromkeys(images)),
        "videos": list(dict.fromkeys(videos)), "comments": comments, "occurrences": 1,
        "source": "当前可见说说接口", "tid": tid,
        "evidence": "当前说说接口仍能返回这条内容。",
    }


def is_self_owner(owner: str, nickname: str, *, owner_uin: str = "", account_uin: str = "") -> bool:
    normalized_owner_uin = normalize_uin(owner_uin)
    normalized_account_uin = normalize_uin(account_uin)
    if normalized_owner_uin:
        return normalized_owner_uin == normalized_account_uin
    return bool(owner and clean_text(owner) == clean_text(nickname))


def current_match_keys(records: Iterable[dict[str, Any]]) -> tuple[set[str], set[str]]:
    bodies: set[str] = set()
    media: set[str] = set()
    for record in records:
        body = normalized_text(record["body"])
        if body:
            bodies.add(body)
        media.update(media_key(url) for url in record["remote_images"] if url)
    return bodies, media


def classify_history(history: list[dict[str, Any]], visible: list[dict[str, Any]],
                     nickname: str, uin: str, *, comparison_complete: bool
                     ) -> tuple[list[dict[str, Any]], list[dict[str, Any]],
                                list[dict[str, Any]], list[dict[str, Any]]]:
    body_keys, image_keys = current_match_keys(visible)
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for raw in history:
        parsed = parse_event_text(raw["text"])
        owner = clean_text(raw.get("owner_name")) or parsed["owner"]
        owner_uin = normalize_uin(raw.get("owner_uin"))
        self_authored = is_self_owner(
            owner, nickname, owner_uin=owner_uin, account_uin=uin,
        ) and parsed["event_type"] in {
            "post", "forward", "publish_event",
        }
        if self_authored and parsed["event_type"] == "publish_event":
            category = "event"
        elif self_authored:
            category = "recovered" if comparison_complete else "pending"
        else:
            category = "interaction"
        body_key = normalized_text(parsed["body"])
        old_media = {media_key(url) for url in raw["images"] if url}
        matched = self_authored and parsed["event_type"] != "publish_event" and (
            (body_key and body_key in body_keys) or bool(old_media & image_keys)
        )
        if matched:
            continue
        key = (
            category, owner, owner_uin, parsed["body"], parsed["event_type"],
            parsed["original_author"], "|".join(sorted(old_media)),
        )
        grouped[key].append({"raw": raw, "parsed": parsed})

    recovered: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    interactions: list[dict[str, Any]] = []
    for key, items in grouped.items():
        category, owner, owner_uin, body, event_type, original_author, _ = key
        timestamps = list(dict.fromkeys(clean_text(item["raw"]["time"]) for item in items if item["raw"]["time"]))
        parsed_dates = [parse_datetime(value) for value in timestamps]
        parsed_dates = [value for value in parsed_dates if value]
        latest = max(parsed_dates) if parsed_dates else None
        images = list(dict.fromkeys(url for item in items for url in item["raw"]["images"] if url))
        digest = hashlib.sha1("\x1f".join(key).encode("utf-8")).hexdigest()[:16]
        if category == "recovered":
            kind = "找回的转发候选" if event_type == "forward" else "找回的删除候选"
            evidence = "当前说说已完整核对；这条本人历史内容没有匹配项，仍需人工确认。"
        elif category == "pending":
            kind = "待核对的本人转发" if event_type == "forward" else "待核对的本人说说"
            evidence = "当前说说没有完整读取，暂时不能判断是否已删除。"
        elif category == "event":
            kind = "仅发现发布事件"
            evidence = "历史接口只保留了发布动作，没有留下可恢复的正文。"
        elif "@" in body or nickname in body:
            kind = "@ 互动"
            evidence = "历史互动线索，不属于本人说说删除判定。"
        elif "评论" in body or "回复" in body:
            kind = "评论相关"
            evidence = "历史互动线索，不属于本人说说删除判定。"
        elif event_type == "forward":
            kind = "转发相关"
            evidence = "历史互动线索，不属于本人说说删除判定。"
        else:
            kind = "他人发布/互动"
            evidence = "历史互动线索，不属于本人说说删除判定。"
        record = {
            "id": f"{category}-{digest}", "category": category, "kind": kind,
            "owner": owner or "未知发布者", "owner_uin": owner_uin,
            "body": body, "original_author": original_author, "evidence": evidence,
            "event_type": event_type, "timestamps": timestamps or ["未知时间"],
            "timestamp_value": int(latest.timestamp()) if latest else 0,
            "year": str(latest.year) if latest else "未知", "images": images,
            "remote_images": images.copy(), "videos": [], "comments": [],
            "occurrences": len(items), "source": "QQ 空间历史互动流", "tid": "",
        }
        target = {
            "recovered": recovered,
            "pending": pending,
            "event": events,
            "interaction": interactions,
        }[category]
        target.append(record)
    for records in (recovered, pending, events, interactions):
        records.sort(key=lambda item: item["timestamp_value"], reverse=True)
    return recovered, pending, events, interactions


def image_extension(content_type: str, content: bytes) -> str | None:
    lowered = content_type.lower()
    if content.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if content[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return ".webp"
    guessed = mimetypes.guess_extension(lowered.split(";", 1)[0])
    return guessed if lowered.startswith("image/") else None


@dataclass
class DownloadResult:
    url: str
    local: str
    ok: bool
    error: str = ""


def download_image_bytes(session: requests.Session, url: str,
                         max_bytes: int = 64 * 1024 * 1024) -> tuple[bytes, str]:
    current = validate_qq_url(url)
    for _hop in range(6):
        with session.get(current, timeout=25, allow_redirects=False, stream=True) as response:
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    raise RecoveryError("图片重定向缺少 Location")
                current = validate_qq_url(urljoin(current, location))
                continue
            response.raise_for_status()
            declared = response.headers.get("Content-Length")
            if declared and int(declared) > max_bytes:
                raise RecoveryError("图片超过 64 MiB 上限")
            content = bytearray()
            for chunk in response.iter_content(64 * 1024):
                content.extend(chunk)
                if len(content) > max_bytes:
                    raise RecoveryError("图片超过 64 MiB 上限")
            return bytes(content), response.headers.get("Content-Type", "")
    raise RecoveryError("图片重定向超过 5 次")


def localize_images(session: requests.Session, package: Path,
                    records: list[dict[str, Any]]) -> tuple[int, int, list[str]]:
    image_dir = package / "assets" / "images"
    ensure_private_directory(image_dir)
    urls = list(dict.fromkeys(url for record in records for url in record["images"] if url.startswith(("http://", "https://"))))
    warnings: list[str] = []

    def download(url: str) -> DownloadResult:
        try:
            content, content_type = download_image_bytes(session, url)
            extension = image_extension(content_type, content)
            if not extension:
                raise RecoveryError("响应不是可识别图片")
            digest = hashlib.sha1(content).hexdigest()
            destination = image_dir / f"{digest}{extension}"
            if not destination.exists():
                write_private_bytes(destination, content)
            return DownloadResult(url, destination.relative_to(package).as_posix(), True)
        except Exception as exc:
            return DownloadResult(url, url, False, clean_text(exc))

    mapping: dict[str, str] = {}
    failures = 0
    phase(4, "收好图片资源", f"发现 {len(urls)} 个图片或视频封面，正在整理进网页包。")
    if not urls:
        say("○", "这批记录没有可下载的图片，网页仍会正常生成。", indent=1)
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(download, url) for url in urls]
        for future in as_completed(futures):
            result = future.result()
            mapping[result.url] = result.local
            if not result.ok:
                failures += 1
                warnings.append(f"图片未本地化：{urlparse(result.url).netloc}（{result.error}）")
    for record in records:
        record["images"] = [mapping.get(url, url) for url in record["images"]]
    return len(urls) - failures, failures, warnings


def safe_json(value: Any, *, pretty: bool = False) -> str:
    kwargs = {"ensure_ascii": False}
    if pretty:
        kwargs["indent"] = 2
    else:
        kwargs["separators"] = (",", ":")
    return json.dumps(value, **kwargs).replace("</", "<\\/")


def render_html(archive: dict[str, Any]) -> str:
    data = safe_json({
        key: archive[key]
        for key in ("recovered", "pending", "events", "visible", "interaction")
    })
    summary = archive["summary"]
    nickname = escape(archive["account"]["nickname"])
    uin = archive["account"]["uin"]
    if not summary["comparison_complete"]:
        warning = "⚠ 当前说说没有完整读取，本次不判定删除；相关内容已放入“待确认”"
    elif not summary["history_complete"]:
        warning = "⚠ 历史痕迹扫描不完整，已找回的内容可查看，但可能还有遗漏"
    else:
        warning = "🔎 当前说说已完整核对；找回的删除候选仍需要你亲自确认"
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' data: https://*.qq.com https://*.qpic.cn https://*.gtimg.com https://*.gtimg.cn; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>找回被删掉的 QQ 空间说说 · {uin}</title>
<style>
:root{{--bg:#090d12;--panel:#10171f;--panel2:#141d27;--line:#25313d;--text:#e9eff4;--muted:#8e9aa6;--cyan:#20e0d0;--pink:#fe4164;--amber:#ffbf47;--left:238px;--right:330px}}*{{box-sizing:border-box}}html,body{{margin:0;height:100%;background:var(--bg);color:var(--text);font:14px/1.55 Inter,"Microsoft YaHei",sans-serif}}button,input,select{{font:inherit}}button{{color:inherit}}.top{{min-height:76px;border-bottom:1px solid var(--line);display:grid;grid-template-columns:var(--left) minmax(520px,1fr) auto;align-items:center;padding:0 22px;gap:18px;background:#0d131a;position:sticky;top:0;z-index:10}}.brand b{{font-size:19px}}.brand span{{display:block;color:var(--muted);font-size:11px}}.tabs{{height:100%;display:flex;gap:18px;overflow-x:auto}}.tab{{border:0;border-bottom:2px solid transparent;background:transparent;padding:0 2px;cursor:pointer;color:#94a0ab;white-space:nowrap}}.tab.active{{color:#fff;border-color:var(--cyan)}}.tab[data-tab=recovered].active{{border-color:var(--pink)}}.tab em{{font-style:normal;margin-left:6px;padding:1px 6px;border-radius:9px;background:#202b35;font-size:11px}}.tools{{display:flex;gap:8px}}.control{{height:36px;border:1px solid var(--line);border-radius:6px;background:#111923;color:#dce4ea;padding:0 10px;outline:none}}#search{{width:220px}}.layout{{height:calc(100vh - 76px);display:grid;grid-template-columns:var(--left) minmax(0,1fr) var(--right)}}.side,.detail{{background:#0d131a;padding:18px;border-right:1px solid var(--line);overflow:auto}}.detail{{border-right:0;border-left:1px solid var(--line)}}.account{{padding:15px;border:1px solid var(--line);border-radius:8px;background:var(--panel)}}.account strong{{display:block;font-size:16px}}.account span,.hint{{color:var(--muted);font-size:12px}}.warning{{margin-top:12px;padding:10px;border-left:2px solid var(--amber);background:#18170f;color:#d8c68b;font-size:12px}}.filter-title{{margin:22px 0 8px;color:#768491;font-size:11px;text-transform:uppercase;letter-spacing:.12em}}.year-list{{display:grid;gap:3px}}.year{{border:0;background:transparent;text-align:left;padding:8px 10px;border-radius:5px;cursor:pointer;color:#9aa6b1}}.year.active,.year:hover{{background:#18232d;color:#fff}}.stage{{display:grid;grid-template-rows:52px 1fr 48px;min-width:0}}.stage-head{{display:flex;align-items:center;justify-content:space-between;padding:0 18px;border-bottom:1px solid var(--line);color:var(--muted)}}.cards-wrap{{overflow:auto;padding:14px 18px}}.cards{{display:grid;grid-template-columns:repeat(2,minmax(280px,1fr));gap:12px}}.card{{min-height:170px;border:1px solid var(--line);border-radius:8px;background:var(--panel);padding:14px;cursor:pointer;transition:.16s}}.card:hover,.card.selected{{border-color:#476174;background:#131d27}}.card.selected{{box-shadow:inset 3px 0 var(--cyan)}}.card.recovered.selected{{box-shadow:inset 3px 0 var(--pink)}}.card-head,.meta{{display:flex;justify-content:space-between;gap:12px;color:var(--muted);font-size:11px}}.badge{{color:#d8e0e6;background:#25313b;border-radius:10px;padding:1px 7px}}.recovered .badge{{color:#ffafbd;background:#3a1b25}}.pending .badge,.event .badge{{color:#f7d38c;background:#3a301b}}.body{{font-size:15px;margin:12px 0;white-space:pre-wrap;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}}.owner{{color:#8fded7;font-size:12px;margin-top:7px}}.thumbs{{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin:10px 0}}.thumbs img{{width:100%;height:92px;object-fit:cover;border-radius:5px;background:#0a0f14}}.pager{{display:flex;align-items:center;justify-content:center;gap:5px;border-top:1px solid var(--line)}}.page{{width:32px;height:30px;border:1px solid var(--line);border-radius:5px;background:#121a23;cursor:pointer}}.page.active{{background:var(--cyan);border-color:var(--cyan);color:#07110f}}.detail h2{{font-size:13px;margin:0 0 18px;color:#9ba8b3}}.detail-kind{{display:inline-block;padding:3px 8px;border-radius:12px;background:#25313b;font-size:11px}}.detail-time{{margin:12px 0 5px;color:var(--muted);font-size:12px}}.detail-body{{font-size:16px;white-space:pre-wrap;overflow-wrap:anywhere;margin-bottom:15px}}.hero{{width:100%;max-height:300px;object-fit:contain;background:#080c10;border-radius:6px}}.section{{border-top:1px solid var(--line);margin-top:17px;padding-top:14px}}.section h3{{font-size:11px;color:#758390;letter-spacing:.08em;text-transform:uppercase}}dl{{display:grid;grid-template-columns:86px 1fr;gap:7px;margin:0;font-size:12px}}dt{{color:#7f8c97}}dd{{margin:0;overflow-wrap:anywhere}}.comment{{padding:9px;margin-top:7px;border-radius:5px;background:#17212b}}.comment b{{font-size:12px}}.comment span{{float:right;color:#758390;font-size:10px}}.comment p{{margin:5px 0 0;font-size:12px}}.empty{{grid-column:1/-1;padding:70px;text-align:center;color:var(--muted)}}a{{color:var(--cyan)}}
@media(max-width:1200px){{:root{{--right:0px}}.layout{{grid-template-columns:var(--left) minmax(0,1fr)}}.detail{{display:none}}}}@media(max-width:820px){{.top{{height:auto;grid-template-columns:1fr;padding:12px}}.tabs{{height:48px;overflow:auto}}.tools{{flex-wrap:wrap}}#search{{width:100%}}.layout{{height:auto;display:block}}.side{{display:none}}.stage{{min-height:calc(100vh - 140px)}}.cards{{grid-template-columns:1fr}}}}
</style></head><body>
<header class="top"><div class="brand"><b>找回被删掉的说说</b><span>{nickname} · QQ {uin} · 时光档案</span></div><nav class="tabs"><button class="tab active" data-tab="recovered">🕰 找回的说说 <em>{len(archive['recovered'])}</em></button><button class="tab" data-tab="pending">? 待确认 <em>{len(archive['pending'])}</em></button><button class="tab" data-tab="events">○ 发布事件 <em>{len(archive['events'])}</em></button><button class="tab" data-tab="visible">✓ 当前可见 <em>{len(archive['visible'])}</em></button><button class="tab" data-tab="interaction">↔ 其他互动 <em>{len(archive['interaction'])}</em></button></nav><div class="tools"><input class="control" id="search" type="search" placeholder="搜索内容、发布者或日期"><select class="control" id="order"><option value="desc">从新到旧</option><option value="asc">从旧到新</option></select><label class="control" style="display:flex;align-items:center;gap:6px"><input id="onlyImage" type="checkbox">只看带图</label></div></header>
<main class="layout"><aside class="side"><div class="account"><strong>{nickname}</strong><span>QQ {uin}</span><div class="hint">档案生成于 {archive['generated_at']}</div></div><div class="warning">{warning}</div><div class="filter-title">按年份翻找</div><div id="years" class="year-list"></div><div class="filter-title">这次找回了什么</div><div class="hint">历史痕迹 {summary['history_raw']} 条<br>找回候选 {summary['recovered_candidates']} 条<br>待确认 {summary['pending_candidates']} 条<br>仅发布事件 {summary['publish_events']} 条<br>当前说说 {summary['visible']} 条<br>可见评论 {summary['comment_count']} 条<br>已保存图片 {summary['images_ok']} 个<br>未下载图片 {summary['images_failed']} 个</div></aside><section class="stage"><div class="stage-head"><span>找到 <b id="count">0</b> 条</span><span id="note"></span></div><div class="cards-wrap" id="scroll"><div class="cards" id="cards"></div></div><div class="pager" id="pager"></div></section><aside class="detail"><h2>🔎 记录详情</h2><div id="detail"></div></aside></main>
<script>const datasets={data};const notes={{recovered:"从历史痕迹找回 · 当前列表没有匹配项 · 仍需人工确认",pending:"当前列表不完整 · 暂不判断是否已删除",events:"只找到发布动作 · 正文没有保留",visible:"现在还能看到 · 已带上可获取的评论",interaction:"他人发布、评论或 @ 你的历史线索"}};const state={{tab:"recovered",q:"",year:"",order:"desc",image:false,page:1,selected:null}};const size=20,$=s=>document.querySelector(s),node=(t,c,x)=>{{const e=document.createElement(t);if(c)e.className=c;if(x!==undefined)e.textContent=x;return e}};
function active(){{const q=state.q.trim().toLowerCase();return datasets[state.tab].filter(x=>!q||[x.owner,x.body,...x.timestamps].join(" ").toLowerCase().includes(q)).filter(x=>!state.year||x.year===state.year).filter(x=>!state.image||x.images.length).sort((a,b)=>state.order==="asc"?a.timestamp_value-b.timestamp_value:b.timestamp_value-a.timestamp_value)}}
function years(){{const c={{}};datasets[state.tab].forEach(x=>c[x.year]=(c[x.year]||0)+1);const box=$("#years");box.replaceChildren();[["",datasets[state.tab].length],...Object.keys(c).sort().reverse().map(y=>[y,c[y]])].forEach(([y,n])=>{{const b=node("button","year"+(state.year===y?" active":""),(y||"全部年份")+"  "+n);b.onclick=()=>{{state.year=y;state.page=1;years();render()}};box.append(b)}})}}
function card(x){{const a=node("article","card "+x.category+(state.selected===x.id?" selected":""));a.tabIndex=0;const h=node("div","card-head");h.append(node("span","",x.timestamps[0]||"未知时间"),node("span","badge",x.kind));a.append(h);if(x.category!=="visible")a.append(node("div","owner","发布者："+x.owner));a.append(node("div","body",x.body||"（无文字内容）"));if(x.images.length){{const g=node("div","thumbs");x.images.slice(0,3).forEach((src,i)=>{{const im=node("img");im.src=src;im.loading="lazy";im.alt="图片 "+(i+1);g.append(im)}});a.append(g)}}const m=node("div","meta");m.append(node("span","",x.occurrences>1?"历史出现 "+x.occurrences+" 次":x.source),node("span","",x.comments.length?x.comments.length+" 条评论":""));a.append(m);a.onclick=()=>{{state.selected=x.id;render()}};return a}}
function detail(x){{const box=$("#detail");box.replaceChildren();if(!x){{box.append(node("div","hint","选择一条记录查看完整信息。"));return}}box.append(node("span","detail-kind",x.kind),node("div","detail-time",x.timestamps[0]||"未知时间"),node("div","owner","发布者："+x.owner+(x.owner_uin?" · QQ "+x.owner_uin:"")),node("div","detail-body",x.body||"（无文字内容）"));if(x.images.length){{const im=node("img","hero");im.src=x.images[0];im.alt="记录图片";box.append(im)}}const s=node("section","section");s.append(node("h3","","判定与元数据"));const dl=node("dl");[["判定依据",x.evidence],["数据来源",x.source],["历史出现",x.occurrences+" 次"],["记录 ID",x.id],["原作者",x.original_author||"—"],["评论能力",x.category==="visible"?"接口返回 "+x.comments.length+" 条":"无法保证完整评论"]].forEach(p=>{{dl.append(node("dt","",p[0]),node("dd","",p[1]))}});s.append(dl);box.append(s);if(x.timestamps.length>1){{const o=node("section","section");o.append(node("h3","","全部出现时间"),node("div","hint",x.timestamps.join(" · ")));box.append(o)}}if(x.comments.length){{const c=node("section","section");c.append(node("h3","","评论（"+x.comments.length+"）"));x.comments.forEach(v=>{{const d=node("div","comment"),h=node("div");h.append(node("b","",v.nickname),node("span","",v.time));d.append(h,node("p","",v.content));c.append(d)}});box.append(c)}}if(x.videos.length){{const v=node("section","section");v.append(node("h3","","视频链接"));x.videos.forEach((url,i)=>{{const a=node("a","","打开视频 "+(i+1));a.href=url;a.target="_blank";a.rel="noopener noreferrer";v.append(a,node("br"))}});box.append(v)}}}}
function pager(total){{const pages=Math.max(1,Math.ceil(total/size));state.page=Math.min(state.page,pages);const p=$("#pager");p.replaceChildren();for(let i=Math.max(1,state.page-2);i<=Math.min(pages,state.page+2);i++){{const b=node("button","page"+(i===state.page?" active":""),String(i));b.onclick=()=>{{state.page=i;render()}};p.append(b)}}}}
function render(){{const all=active();$("#count").textContent=all.length;$("#note").textContent=notes[state.tab];const items=all.slice((state.page-1)*size,state.page*size);if(!items.some(x=>x.id===state.selected))state.selected=items[0]?.id||null;const box=$("#cards");box.replaceChildren(...items.map(card));if(!items.length)box.append(node("div","empty","○ 这里暂时没有符合条件的记录，换个筛选试试"));pager(all.length);detail(datasets[state.tab].find(x=>x.id===state.selected))}}
document.querySelectorAll(".tab").forEach(b=>b.onclick=()=>{{state.tab=b.dataset.tab;state.q="";state.year="";state.page=1;state.selected=null;$("#search").value="";document.querySelectorAll(".tab").forEach(x=>x.classList.toggle("active",x===b));years();render()}});$("#search").oninput=e=>{{state.q=e.target.value;state.page=1;render()}};$("#order").onchange=e=>{{state.order=e.target.value;render()}};$("#onlyImage").onchange=e=>{{state.image=e.target.checked;state.page=1;render()}};years();render();</script></body></html>'''


def build_archive(parent: Path, session: requests.Session, uin: str, nickname: str,
                  history: list[dict[str, Any]], visible_raw: list[dict[str, Any]],
                  *, history_complete: bool, comparison_complete: bool,
                  warnings: list[str]) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    package = parent / f"qzone-post-recovery-{uin}-{timestamp}"
    package.mkdir(parents=True, exist_ok=False, mode=0o700)
    ensure_private_directory(package)
    ensure_private_directory(package / "data")
    visible = [current_record(item, nickname, uin) for item in visible_raw]
    recovered, pending, events, interactions = classify_history(
        history, visible, nickname, uin, comparison_complete=comparison_complete,
    )
    all_records = recovered + pending + events + visible + interactions
    images_ok, images_failed, image_warnings = localize_images(session, package, all_records)
    warnings.extend(image_warnings)
    archive = {
        "schema_version": 2,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "account": {"uin": uin, "nickname": nickname},
        "summary": {
            "complete": bool(history_complete and comparison_complete),
            "history_complete": bool(history_complete),
            "comparison_complete": bool(comparison_complete),
            "history_raw": len(history), "visible_raw": len(visible_raw),
            "recovered_candidates": len(recovered), "pending_candidates": len(pending),
            "publish_events": len(events), "visible": len(visible), "interactions": len(interactions),
            "comment_count": sum(len(item["comments"]) for item in visible),
            "images_ok": images_ok, "images_failed": images_failed, "warnings": warnings,
        },
        "recovered": recovered, "pending": pending, "events": events,
        "visible": visible, "interaction": interactions,
    }
    write_private_bytes(
        package / "data" / "archive.json", safe_json(archive, pretty=True).encode("utf-8"),
    )
    write_private_bytes(package / "index.html", render_html(archive).encode("utf-8"))
    return package


def choose_output_parent(explicit: str | None) -> Path:
    value = explicit
    if not value:
        print()
        say("📁", "先选一个保存位置，档案助手会在里面新建独立文件夹。")
        say("例", "/home/你的名字/archives（WSL 中不要选择 /mnt/c、/mnt/e 等 Windows 盘）", indent=1)
        value = input("   保存到 › ").strip()
    if not value:
        raise RecoveryError("还没有选择保存位置；请提供一个产物包父目录")
    parent = Path(value).expanduser().resolve()
    ensure_private_output_location(parent)
    parent.mkdir(parents=True, exist_ok=True)
    if not parent.is_dir():
        raise RecoveryError(f"输出位置不是目录：{parent}")
    return parent


def _decode_mount_path(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


def ensure_private_output_location(
    path: Path,
    *,
    platform: str | None = None,
    kernel_release: str | None = None,
    mountinfo_text: str | None = None,
) -> None:
    """Reject output filesystems where POSIX private modes cannot protect the archive."""
    platform = platform or os.name
    if platform == "nt":
        raise RecoveryError("Windows 原生文件系统无法由本工具验证私有 ACL；请在 WSL 的 /home 目录运行并保存")

    if kernel_release is None:
        try:
            kernel_release = Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8")
        except OSError:
            kernel_release = ""
    is_wsl = "microsoft" in kernel_release.lower() or bool(os.environ.get("WSL_DISTRO_NAME"))

    resolved = path.resolve(strict=False)
    parts = resolved.parts
    if is_wsl and len(parts) >= 3 and parts[1] == "mnt" and re.fullmatch(r"[A-Za-z]", parts[2]):
        raise RecoveryError(
            "不能把私人档案直接保存到 WSL 的 Windows 挂载盘；请改用 /home/你的名字/archives"
        )

    if mountinfo_text is None:
        try:
            mountinfo_text = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
        except OSError:
            mountinfo_text = ""
    matches: list[tuple[int, str]] = []
    for line in mountinfo_text.splitlines():
        if " - " not in line:
            continue
        left, right = line.split(" - ", 1)
        left_fields, right_fields = left.split(), right.split()
        if len(left_fields) < 6 or len(right_fields) < 3:
            continue
        mount_point = Path(_decode_mount_path(left_fields[4]))
        try:
            resolved.relative_to(mount_point)
        except ValueError:
            continue
        signature = " ".join((right_fields[0], right_fields[1], right_fields[2], line)).lower()
        matches.append((len(mount_point.parts), signature))
    if matches and "drvfs" in max(matches, key=lambda item: item[0])[1]:
        raise RecoveryError(
            "目标目录位于 WSL 的 Windows 文件系统，无法验证私人档案 ACL；请改用 /home 下的目录"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        add_help=False,
        description="✨ 把 QQ 空间往事整理成一份可离线浏览的时光档案",
    )
    parser.add_argument("-h", "--help", action="help", help="显示这份使用说明")
    parser.add_argument("--output-dir", help="把网页包保存到这个父目录；省略时会邀请你选择")
    parser.add_argument("--fresh-login", action="store_true", help="不复用旧会话，准备一张新的登录二维码")
    parser.add_argument("--max-history", type=int, default=20000, help="最多翻阅多少条历史痕迹（默认 20000）")
    parser.add_argument("--page-delay", type=float, default=1.5, help="每次翻页间隔几秒（默认 1.5）")
    parser.add_argument("--no-open", action="store_true", help="只打印文件路径，不自动打开二维码或网页")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.max_history < 100:
        raise SystemExit("✕ --max-history 至少需要 100，避免过早停下而产生误导结果。")
    welcome()
    try:
        ensure_supported_requests()
        parent = choose_output_parent(args.output_dir)
        phase(1, "连接你的 QQ 空间", "登录信息只保存在本机私有缓存，不会写进最终网页包。")
        say("⚠", "找回结果来自历史残留与当前列表的核对，不是 QQ 官方的删除证明。")
        session, uin, nickname = authenticated_session(fresh_login=args.fresh_login, auto_open=not args.no_open)
        session.headers.update(session_headers(uin))
        history, complete, warnings = fetch_history(session, uin, max_history=args.max_history,
                                                    page_delay=args.page_delay)
        try:
            visible_raw, comparison_complete, visible_warnings = fetch_visible(
                session, uin, page_delay=args.page_delay,
            )
            warnings.extend(visible_warnings)
        except RecoveryError as exc:
            visible_raw = []
            comparison_complete = False
            warnings.append(f"当前可见说说读取失败：{exc}")
        package = build_archive(parent, session, uin, nickname, history, visible_raw,
                                history_complete=complete,
                                comparison_complete=comparison_complete, warnings=warnings)
    except (RecoveryError, requests.RequestException, json.JSONDecodeError, OSError) as exc:
        print(f"\n✕ 这次没有顺利完成：{exc}", file=sys.stderr)
        print("  你可以保留当前窗口，检查网络后再试；若是登录问题，可加 --fresh-login。", file=sys.stderr)
        return 1
    data = json.loads((package / "data" / "archive.json").read_text(encoding="utf-8"))
    summary = data["summary"]
    print("\n╭─ 🎉 时光档案已经整理好 ─────────────────")
    say("🕰", f"找回的删除候选：{summary['recovered_candidates']} 条", indent=1)
    say("?", f"待确认：{summary['pending_candidates']} 条 · 仅发布事件：{summary['publish_events']} 条", indent=1)
    say("✓", f"当前可见：{summary['visible']} 条 · 评论：{summary['comment_count']} 条", indent=1)
    say("↔", f"其他互动：{summary['interactions']} 条", indent=1)
    say("🖼", f"图片落地：成功 {summary['images_ok']} · 未下载 {summary['images_failed']}", indent=1)
    say("●", "扫描完整结束" if summary["complete"] else "扫描可能不完整，请在 archive.json 中查看 warnings", indent=1)
    say("🌐", f"打开网页：{package / 'index.html'}", indent=1)
    say("📦", f"整个产物包：{package}", indent=1)
    print("╰─────────────────────────────────────────")
    if not args.no_open:
        open_file(package / "index.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
