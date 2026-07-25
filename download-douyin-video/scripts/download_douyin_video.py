#!/usr/bin/env python3
"""Download publicly accessible Douyin videos from share text or URLs."""

from __future__ import annotations

import argparse
import html as html_module
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7 Pro) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 "
    "Mobile Safari/537.36"
)
URL_RE = re.compile(r"https?://[^\s<>\"']+")
VIDEO_ID_PATH_RE = re.compile(r"/(?:share/)?video/(\d{15,22})(?:[/?#]|$)")
VIDEO_ID_QUERY_KEYS = ("modal_id", "aweme_id", "item_id", "item_ids")
PAGE_HOSTS = ("douyin.com", "iesdouyin.com")
MEDIA_HOSTS = (
    "aweme.snssdk.com",
    "douyinvod.com",
    "idouyinvod.com",
    "amemv.com",
    "zjcdn.com",
    "bytecdn.cn",
    "bytecdn.com",
)
MAX_SHARE_HTML_BYTES = 8 * 1024 * 1024


class DownloadError(RuntimeError):
    pass


class DownloadHTTPError(DownloadError):
    def __init__(self, status: int, url: str, retry_after: str | None = None):
        super().__init__(f"HTTP {status}: {safe_url_label(url)}")
        self.status = status
        self.retry_after = retry_after


class DownloadNetworkError(DownloadError):
    def __init__(self, url: str, reason: object):
        super().__init__(f"network error for {safe_url_label(url)}: {reason}")


@dataclass(frozen=True)
class VideoInfo:
    video_id: str
    author: str
    title: str
    media_urls: tuple[str, ...]


@dataclass(frozen=True)
class ProbeInfo:
    duration: float | None
    size: int
    width: int | None
    height: int | None
    codec: str | None
    validation: str


def request_headers(*, referer: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": MOBILE_UA,
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Accept-Encoding": "identity",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def host_is_allowed(host: str, allowed_hosts: tuple[str, ...]) -> bool:
    normalized = host.rstrip(".").lower()
    return any(normalized == allowed or normalized.endswith(f".{allowed}") for allowed in allowed_hosts)


def safe_url_label(value: str) -> str:
    sanitized = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", value)[:2048]
    try:
        parsed = urlparse(sanitized)
        hostname = parsed.hostname
    except ValueError:
        return sanitized[:160]
    if parsed.scheme in ("http", "https") and hostname:
        try:
            parsed_port = parsed.port
        except ValueError:
            parsed_port = None
        port = f":{parsed_port}" if parsed_port else ""
        return urlunparse((parsed.scheme, f"{hostname}{port}", parsed.path, "", "", ""))
    return sanitized[:160]


def validate_https_url(url: str, allowed_hosts: tuple[str, ...]) -> None:
    try:
        parsed = urlparse(url)
        port = parsed.port
        hostname = parsed.hostname
    except ValueError as exc:
        raise DownloadError(f"invalid URL: {safe_url_label(url)}") from exc
    if parsed.scheme != "https" or not hostname:
        raise DownloadError(f"only HTTPS URLs are accepted: {safe_url_label(url)}")
    if parsed.username or parsed.password or port not in (None, 443):
        raise DownloadError(f"URL credentials and custom ports are not accepted: {safe_url_label(url)}")
    if not host_is_allowed(hostname, allowed_hosts):
        raise DownloadError(f"URL host is not an approved Douyin host: {safe_url_label(url)}")


class ValidatingRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allowed_hosts: tuple[str, ...]):
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        validate_https_url(newurl, self.allowed_hosts)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def trusted_ssl_context() -> ssl.SSLContext:
    """Use a verified CA bundle even when a macOS Python install lacks one."""
    candidates: list[str] = []
    try:
        import certifi  # type: ignore[import-not-found]

        candidates.append(certifi.where())
    except ImportError:
        pass
    candidates.extend(
        [
            "/etc/ssl/cert.pem",
            "/opt/homebrew/etc/openssl@3/cert.pem",
            "/usr/local/etc/openssl@3/cert.pem",
        ]
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return ssl.create_default_context(cafile=candidate)
    return ssl.create_default_context()


def open_url(
    url: str,
    *,
    allowed_hosts: tuple[str, ...],
    referer: str | None = None,
    timeout: int = 30,
    retries: int = 0,
):
    validate_https_url(url, allowed_hosts)
    request = Request(url, headers=request_headers(referer=referer))
    opener = build_opener(
        HTTPSHandler(context=trusted_ssl_context()),
        ValidatingRedirectHandler(allowed_hosts),
    )
    for attempt in range(max(0, retries) + 1):
        try:
            return opener.open(request, timeout=timeout)
        except HTTPError as exc:
            raise DownloadHTTPError(exc.code, url, exc.headers.get("Retry-After")) from exc
        except (URLError, OSError) as exc:
            if attempt < retries:
                time.sleep(1)
                continue
            reason = exc.reason if isinstance(exc, URLError) else exc
            raise DownloadNetworkError(url, reason) from exc
    raise DownloadNetworkError(url, "request attempts exhausted")


def extract_urls(values: Iterable[str]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for value in values:
        candidates = URL_RE.findall(value)
        if not candidates and value.strip().isdigit():
            candidates = [value.strip()]
        for candidate in candidates:
            candidate = candidate.rstrip("，。！？；：,.!?:;)]}>")
            if candidate not in seen:
                urls.append(candidate)
                seen.add(candidate)
    return urls


def video_id_from_url(url: str) -> str | None:
    if url.isdigit() and 15 <= len(url) <= 22:
        return url
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    match = VIDEO_ID_PATH_RE.search(parsed.path)
    if match:
        return match.group(1)
    query = parse_qs(parsed.query)
    for key in VIDEO_ID_QUERY_KEYS:
        for value in query.get(key, []):
            if value.isdigit() and 15 <= len(value) <= 22:
                return value
    return None


class AuthoritativeURLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value for key, value in attrs if value is not None}
        if tag.lower() == "link" and "canonical" in values.get("rel", "").lower().split():
            if values.get("href"):
                self.urls.append(values["href"])
        if tag.lower() == "meta" and values.get("property", "").lower() == "og:url":
            if values.get("content"):
                self.urls.append(values["content"])


def authoritative_video_id_from_html(page_html: str) -> str | None:
    parser = AuthoritativeURLParser()
    try:
        parser.feed(page_html)
    except Exception:
        return None
    video_ids = {video_id for url in parser.urls if (video_id := video_id_from_url(url))}
    return next(iter(video_ids)) if len(video_ids) == 1 else None


def resolve_short_link_with_curl(url: str, timeout: int) -> str:
    curl = shutil.which("curl")
    if not curl:
        raise DownloadError("curl is unavailable for the short-link compatibility fallback")
    validate_https_url(url, PAGE_HOSTS)
    command = [
        curl,
        "-sS",
        "-D",
        "-",
        "-o",
        "/dev/null",
        "--max-time",
        str(timeout),
        "--max-redirs",
        "0",
        "--proto",
        "=https",
        "-A",
        MOBILE_UA,
        url,
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout + 5, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DownloadError(f"curl could not resolve {safe_url_label(url)}") from exc
    if result.returncode != 0:
        raise DownloadError(f"curl could not resolve {safe_url_label(url)}")
    locations = re.findall(r"^location:\s*(\S+)\s*$", result.stdout, flags=re.IGNORECASE | re.MULTILINE)
    if len(locations) != 1:
        raise DownloadError("short-link response did not contain one authoritative redirect")
    redirect_url = urljoin(url, locations[0])
    validate_https_url(redirect_url, PAGE_HOSTS)
    video_id = video_id_from_url(redirect_url)
    if not video_id:
        raise DownloadError("short-link redirect did not contain a Douyin video ID")
    return video_id


def resolve_video_id(value: str, timeout: int) -> str:
    if value.isdigit() and 15 <= len(value) <= 22:
        return value
    validate_https_url(value, PAGE_HOSTS)
    direct = video_id_from_url(value)
    if direct:
        return direct
    try:
        response = open_url(value, allowed_hosts=PAGE_HOSTS, timeout=timeout, retries=1)
    except DownloadNetworkError:
        return resolve_short_link_with_curl(value, timeout)
    with response:
        resolved = response.geturl()
        video_id = video_id_from_url(resolved)
        if not video_id:
            body = response.read(1024 * 1024).decode("utf-8", errors="replace")
            video_id = authoritative_video_id_from_html(body)
    if not video_id:
        raise DownloadError(
            f"could not find a Douyin video ID after resolving {safe_url_label(value)}"
        )
    return video_id


def fetch_share_html(video_id: str, timeout: int) -> str:
    url = f"https://www.iesdouyin.com/share/video/{video_id}/?from_ssr=1"
    with open_url(
        url,
        allowed_hosts=PAGE_HOSTS,
        referer="https://www.douyin.com/",
        timeout=timeout,
        retries=1,
    ) as response:
        data = response.read(MAX_SHARE_HTML_BYTES + 1)
        content_type = response.headers.get("Content-Type", "")
    if len(data) > MAX_SHARE_HTML_BYTES:
        raise DownloadError("Douyin share page exceeded the 8 MiB safety limit")
    if "html" not in content_type.lower() and not data.lstrip().startswith(b"<"):
        raise DownloadError("Douyin share endpoint did not return HTML")
    return data.decode("utf-8", errors="replace")


def find_video_item(value: Any, video_id: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        item_list = value.get("item_list")
        if isinstance(item_list, list):
            for item in item_list:
                if isinstance(item, dict) and str(item.get("aweme_id")) == video_id:
                    return item
        for child in value.values():
            found = find_video_item(child, video_id)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_video_item(child, video_id)
            if found is not None:
                return found
    return None


def router_data_from_html(page_html: str) -> dict[str, Any]:
    match = re.search(
        r"window\._ROUTER_DATA\s*=\s*(\{.*?\})\s*;?\s*</script>",
        page_html,
        flags=re.DOTALL,
    )
    if not match:
        raise DownloadError("public share page no longer exposes _ROUTER_DATA")
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise DownloadError(f"could not parse Douyin page data: {exc}") from exc


def media_url_with_watermark(media_url: str, keep_watermark: bool) -> str:
    decoded = html_module.unescape(media_url).replace("\\u002F", "/")
    if keep_watermark:
        return decoded
    parsed = urlparse(decoded)
    if parsed.hostname == "aweme.snssdk.com" and parsed.path in (
        "/aweme/v1/playwm/",
        "/aweme/v1/play/",
    ):
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["ratio"] = ["1080p"]
        return urlunparse(
            parsed._replace(
                path=parsed.path.replace("/playwm/", "/play/"),
                query=urlencode(query, doseq=True),
            )
        )
    return decoded


def parse_video_info(page_html: str, video_id: str, keep_watermark: bool) -> VideoInfo:
    router_data = router_data_from_html(page_html)
    item = find_video_item(router_data, video_id)
    if item is None:
        raise DownloadError("video metadata was not found; it may be private, removed, or restricted")
    video = item.get("video")
    if not isinstance(video, dict):
        raise DownloadError("video metadata has an unexpected structure")
    play_addr = video.get("play_addr")
    if not isinstance(play_addr, dict):
        raise DownloadError("video metadata did not include a play address")
    urls = play_addr.get("url_list")
    if not isinstance(urls, list):
        raise DownloadError("video metadata did not include media URL candidates")
    clean_urls = tuple(
        dict.fromkeys(
            media_url_with_watermark(url, keep_watermark)
            for url in urls
            if isinstance(url, str) and url
        )
    )
    if not clean_urls:
        raise DownloadError("video metadata did not include a playable media URL")

    author_data = item.get("author")
    if not isinstance(author_data, dict):
        author_data = {}
    author = str(author_data.get("nickname") or "douyin")
    description = str(item.get("desc") or "video").strip()
    title = description.splitlines()[0].strip() or "video"
    return VideoInfo(
        video_id=video_id,
        author=author,
        title=title,
        media_urls=clean_urls,
    )


def safe_component(value: str) -> str:
    value = re.sub(r"[\x00-\x1f\x7f/\\:*?\"<>|]", "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return value or "video"


def truncate_utf8(value: str, max_bytes: int) -> str:
    if max_bytes <= 0:
        return ""
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore").rstrip(" ._")


def output_path(output_dir: Path, info: VideoInfo, keep_watermark: bool) -> Path:
    variant = "-watermarked" if keep_watermark else ""
    suffix = f"-{info.video_id}{variant}.mp4"
    # Stay below the common 255-byte component limit with room for filesystem quirks.
    prefix_budget = 240 - len(suffix.encode("utf-8"))
    author = truncate_utf8(safe_component(info.author), min(72, max(8, prefix_budget // 3)))
    title_budget = max(8, prefix_budget - len(author.encode("utf-8")) - 1)
    title = truncate_utf8(safe_component(info.title), title_budget)
    name = f"{author}-{title}{suffix}"
    return output_dir / name


def has_mp4_header(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size < 1024:
        return False
    with path.open("rb") as file_obj:
        return b"ftyp" in file_obj.read(64)


def probe_video(path: Path) -> ProbeInfo:
    size = path.stat().st_size
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        if not has_mp4_header(path):
            raise DownloadError("downloaded file is not a valid-looking MP4")
        return ProbeInfo(None, size, None, None, None, "UNVERIFIED: MP4 header only")

    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration,size:stream=codec_type,codec_name,width,height",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != 0:
        raise DownloadError(f"ffprobe rejected the downloaded file: {result.stderr.strip()}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DownloadError("ffprobe returned invalid JSON") from exc
    video_streams = [stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video"]
    duration = float((payload.get("format") or {}).get("duration") or 0)
    if not video_streams or duration <= 0:
        raise DownloadError("downloaded MP4 has no usable video stream or duration")
    stream = video_streams[0]
    return ProbeInfo(
        duration=duration,
        size=size,
        width=stream.get("width"),
        height=stream.get("height"),
        codec=stream.get("codec_name"),
        validation="ffprobe",
    )


def download_candidate(
    media_url: str,
    destination: Path,
    *,
    timeout: int,
    max_bytes: int,
) -> ProbeInfo:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_fd, temp_name = tempfile.mkstemp(prefix=".douyin-", suffix=".part", dir=destination.parent)
    temp_path = Path(temp_name)
    deadline = time.monotonic() + timeout
    try:
        with os.fdopen(temp_fd, "wb") as output:
            with open_url(
                media_url,
                allowed_hosts=MEDIA_HOSTS,
                referer="https://www.iesdouyin.com/",
                timeout=timeout,
            ) as response:
                content_type = response.headers.get("Content-Type", "").lower()
                content_length = response.headers.get("Content-Length")
                declared_size: int | None = None
                if content_length is not None:
                    try:
                        declared_size = int(content_length)
                    except ValueError as exc:
                        raise DownloadError("media response has an invalid Content-Length") from exc
                    if declared_size < 0:
                        raise DownloadError("media response has an invalid Content-Length")
                    if declared_size > max_bytes:
                        raise DownloadError(
                            f"media is larger than the configured limit ({human_size(max_bytes)})"
                        )
                written = 0
                while True:
                    if time.monotonic() > deadline:
                        raise DownloadNetworkError(media_url, "total download deadline exceeded")
                    try:
                        chunk = response.read(1024 * 1024)
                    except OSError as exc:
                        raise DownloadNetworkError(media_url, exc) from exc
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise DownloadError(
                            f"media exceeded the configured limit ({human_size(max_bytes)})"
                        )
                    output.write(chunk)
                if declared_size is not None and written != declared_size:
                    raise DownloadError(
                        f"incomplete media response: expected {declared_size} bytes, received {written}"
                    )
            output.flush()
            os.fsync(output.fileno())
        if "video" not in content_type and not has_mp4_header(temp_path):
            raise DownloadError(f"media endpoint returned {content_type or 'unknown content type'}, not MP4")
        probe = probe_video(temp_path)
        os.replace(temp_path, destination)
        return probe
    finally:
        temp_path.unlink(missing_ok=True)


def download_media(
    info: VideoInfo,
    destination: Path,
    *,
    timeout: int,
    max_bytes: int,
) -> ProbeInfo:
    errors: list[str] = []
    for media_url in info.media_urls:
        for attempt in range(2):
            try:
                return download_candidate(
                    media_url,
                    destination,
                    timeout=timeout,
                    max_bytes=max_bytes,
                )
            except DownloadHTTPError as exc:
                errors.append(str(exc))
                retryable = exc.status in (403, 429) or exc.status >= 500
                if attempt == 0 and retryable:
                    try:
                        delay = float(exc.retry_after or 1)
                    except ValueError:
                        delay = 1
                    time.sleep(min(max(delay, 0), 5))
                    continue
                break
            except DownloadNetworkError as exc:
                errors.append(str(exc))
                if attempt == 0:
                    time.sleep(1)
                    continue
                break
            except DownloadError as exc:
                errors.append(str(exc))
                break
    unique_errors = list(dict.fromkeys(errors))
    detail = "; ".join(unique_errors[-3:]) or "no usable media candidates"
    raise DownloadError(f"all media candidates failed: {detail}")


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def format_probe(probe: ProbeInfo) -> str:
    details: list[str] = []
    if probe.width and probe.height:
        details.append(f"{probe.width}x{probe.height}")
    if probe.duration is not None:
        details.append(f"{probe.duration:.2f}s")
    if probe.codec:
        details.append(probe.codec)
    details.append(human_size(probe.size))
    details.append(probe.validation)
    return ", ".join(details)


def process_one(
    value: str,
    *,
    output_dir: Path,
    force: bool,
    keep_watermark: bool,
    timeout: int,
    max_bytes: int,
) -> tuple[Path, ProbeInfo, str]:
    video_id = resolve_video_id(value, timeout)
    page_html = fetch_share_html(video_id, timeout)
    info = parse_video_info(page_html, video_id, keep_watermark)
    destination = output_path(output_dir, info, keep_watermark)

    if destination.exists() and not force:
        try:
            probe = probe_video(destination)
            return destination, probe, "SKIP"
        except (DownloadError, OSError, subprocess.SubprocessError):
            pass

    probe = download_media(info, destination, timeout=timeout, max_bytes=max_bytes)
    return destination, probe, "OK"


def positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download publicly accessible Douyin videos from URLs or pasted share text."
    )
    parser.add_argument("inputs", nargs="*", help="Douyin URL, video ID, or pasted share text")
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="also read share text from standard input (keeps it out of the process list)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("douyin-videos"),
        help="destination directory (default: ./douyin-videos)",
    )
    parser.add_argument("--force", action="store_true", help="overwrite existing files")
    parser.add_argument(
        "--keep-watermark",
        action="store_true",
        help="keep the platform watermark instead of requesting the clean play endpoint",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="network timeout in seconds (default: 60)",
    )
    parser.add_argument(
        "--max-size-mib",
        type=positive_int,
        default=2048,
        help="maximum size per video in MiB (default: 2048)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    raw_inputs = list(args.inputs)
    if args.stdin:
        raw_inputs.append(sys.stdin.read())
    values = extract_urls(raw_inputs)
    if not values:
        print("[ERROR] no Douyin URL or video ID found in the input", file=sys.stderr)
        return 2

    output_dir = args.output_dir.expanduser().resolve()
    successes = 0
    failures = 0
    for value in values:
        try:
            path, probe, status = process_one(
                value,
                output_dir=output_dir,
                force=args.force,
                keep_watermark=args.keep_watermark,
                timeout=max(5, args.timeout),
                max_bytes=args.max_size_mib * 1024 * 1024,
            )
            print(f"[{status}] {path} ({format_probe(probe)})")
            successes += 1
        except Exception as exc:
            print(f"[ERROR] {safe_url_label(value)}: {exc}", file=sys.stderr)
            failures += 1

    print(f"Summary: {successes} succeeded, {failures} failed; output: {output_dir}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
