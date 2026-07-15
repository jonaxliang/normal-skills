#!/usr/bin/env python3
"""Download images from one or more public Xiaohongshu posts as JPEG files."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


POST_ID_RE = re.compile(r"/(?:discovery/item|explore)/([0-9a-fA-F]{24})(?:[/?#]|$)")
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/136.0 Safari/537.36"
PAGE_HOSTS = ("xiaohongshu.com",)
ASSET_HOSTS = ("xhscdn.com",)
MAX_HTML_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_IMAGE_BYTES = 64 * 1024 * 1024
READ_CHUNK_BYTES = 1024 * 1024
URL_IN_TEXT_RE = re.compile(r"https?://[^\s<>\"']+")


class DownloadError(RuntimeError):
    pass


class DownloadHTTPError(DownloadError):
    def __init__(self, status: int, url: str):
        super().__init__(f"HTTP {status}: {safe_url_label(url)}")


class DownloadNetworkError(DownloadError):
    def __init__(self, url: str, reason: object):
        super().__init__(f"网络错误 {safe_url_label(url)}: {safe_error_text(reason)}")


@dataclass(frozen=True)
class Job:
    url: str
    post_id: str
    output_dir: Path


class JsonLdParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._capturing = False
        self._parts: list[str] = []
        self.blocks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value for key, value in attrs}
        if tag.lower() == "script" and values.get("type", "").lower() == "application/ld+json":
            self._capturing = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capturing:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "script" and self._capturing:
            self.blocks.append("".join(self._parts))
            self._capturing = False
            self._parts = []


def host_is_allowed(host: str, allowed_hosts: tuple[str, ...]) -> bool:
    normalized = host.rstrip(".").lower()
    return any(normalized == allowed or normalized.endswith(f".{allowed}") for allowed in allowed_hosts)


def safe_url_label(value: str) -> str:
    sanitized = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", value)[:2048]
    fallback = sanitized.split("?", 1)[0].split("#", 1)[0][:160]
    try:
        parsed = urlparse(sanitized)
        hostname = parsed.hostname
    except ValueError:
        return fallback
    if parsed.scheme in ("http", "https") and hostname:
        try:
            parsed_port = parsed.port
        except ValueError:
            parsed_port = None
        hostname_label = f"[{hostname}]" if ":" in hostname else hostname
        port = f":{parsed_port}" if parsed_port else ""
        return urlunparse((parsed.scheme, f"{hostname_label}{port}", parsed.path, "", "", ""))
    return fallback


def safe_error_text(reason: object) -> str:
    text = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(reason))
    text = URL_IN_TEXT_RE.sub(lambda match: safe_url_label(match.group(0)), text)
    return " ".join(text.split())[:300]


def validate_https_url(url: str, allowed_hosts: tuple[str, ...]) -> None:
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise DownloadError(f"URL 无效: {safe_url_label(url)}") from exc
    if parsed.scheme != "https" or not hostname:
        raise DownloadError(f"只接受 HTTPS URL: {safe_url_label(url)}")
    if parsed.username or parsed.password or port not in (None, 443):
        raise DownloadError(f"URL 不允许包含凭据或自定义端口: {safe_url_label(url)}")
    if not host_is_allowed(hostname, allowed_hosts):
        raise DownloadError(f"URL 域名不在允许列表: {safe_url_label(url)}")


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


def request_headers(*, referer: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
    }
    if referer:
        headers["Referer"] = referer
    return headers


def open_url(
    url: str,
    *,
    allowed_hosts: tuple[str, ...],
    referer: str | None = None,
    timeout: int = 30,
):
    validate_https_url(url, allowed_hosts)
    request = Request(url, headers=request_headers(referer=referer))
    try:
        opener = build_opener(
            HTTPSHandler(context=trusted_ssl_context()),
            ValidatingRedirectHandler(allowed_hosts),
        )
        return opener.open(request, timeout=timeout)
    except HTTPError as exc:
        raise DownloadHTTPError(exc.code, url) from exc
    except URLError as exc:
        raise DownloadNetworkError(url, exc.reason) from exc
    except (OSError, TimeoutError) as exc:
        raise DownloadNetworkError(url, exc) from exc


def declared_content_length(response) -> int | None:  # type: ignore[no-untyped-def]
    value = response.headers.get("Content-Length")
    if value is None:
        return None
    try:
        length = int(value)
    except (TypeError, ValueError) as exc:
        raise DownloadError("响应包含无效的 Content-Length") from exc
    if length < 0:
        raise DownloadError("响应包含无效的 Content-Length")
    return length


def copy_limited_response(response, output, *, max_bytes: int, label: str) -> int:  # type: ignore[no-untyped-def]
    declared = declared_content_length(response)
    if declared is not None and declared > max_bytes:
        raise DownloadError(f"{label} 超过大小上限 {max_bytes} 字节")

    total = 0
    while True:
        chunk = response.read(min(READ_CHUNK_BYTES, max_bytes - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise DownloadError(f"{label} 超过大小上限 {max_bytes} 字节")
        output.write(chunk)
    if declared is not None and total != declared:
        raise DownloadError(f"{label} 响应不完整: 预期 {declared} 字节，实际 {total} 字节")
    return total


def extract_post_id(url: str) -> str:
    try:
        validate_https_url(url, PAGE_HOSTS)
    except DownloadError as exc:
        raise ValueError(f"不是受支持的小红书 URL: {safe_url_label(url)}") from exc
    parsed = urlparse(url)
    match = POST_ID_RE.search(parsed.path + "/")
    if not match:
        raise ValueError(f"无法从小红书 URL 提取帖子 ID: {safe_url_label(url)}")
    return match.group(1).lower()


def build_jobs(urls: list[str], output_root: Path) -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    for url in urls:
        post_id = extract_post_id(url)
        if post_id not in seen:
            seen.add(post_id)
            jobs.append(Job(url=url, post_id=post_id, output_dir=output_root / post_id))
    return jobs


def plan_jobs(urls: list[str], output_root: Path) -> tuple[list[Job], list[str]]:
    valid_urls: list[str] = []
    errors: list[str] = []
    for url in urls:
        try:
            extract_post_id(url)
            valid_urls.append(url)
        except ValueError as exc:
            errors.append(str(exc))
    return build_jobs(valid_urls, output_root), errors


def extract_image_urls(html: str) -> list[str]:
    parser = JsonLdParser()
    parser.feed(html)
    urls: list[str] = []
    for block in parser.blocks:
        try:
            payload = json.loads(block)
        except json.JSONDecodeError:
            continue
        candidates = payload.get("image", []) if isinstance(payload, dict) else []
        if isinstance(candidates, str):
            candidates = [candidates]
        if isinstance(candidates, list):
            urls.extend(item for item in candidates if isinstance(item, str))

    unique: list[str] = []
    seen: set[str] = set()
    for url in urls:
        normalized = re.sub(r"^http:", "https:", url.strip())
        try:
            validate_https_url(normalized, ASSET_HOSTS)
        except DownloadError as exc:
            raise ValueError(f"公开页面包含不受支持的图片 URL: {safe_url_label(normalized)}") from exc
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    if not unique:
        raise ValueError("公开页面中未找到图片；帖子可能失效、不是图文帖子或需要浏览器兜底")
    return unique


def fetch_post_html(url: str, *, timeout: int = 30) -> str:
    with open_url(
        url,
        allowed_hosts=PAGE_HOSTS,
        referer="https://www.xiaohongshu.com/",
        timeout=timeout,
    ) as response:
        chunks: list[bytes] = []

        class ChunkSink:
            def write(self, chunk: bytes) -> None:
                chunks.append(chunk)

        copy_limited_response(response, ChunkSink(), max_bytes=MAX_HTML_BYTES, label="帖子页面")
    return b"".join(chunks).decode("utf-8", errors="replace")


def download_asset(
    url: str,
    destination: Path,
    *,
    referer: str,
    timeout: int,
    max_bytes: int,
) -> int:
    with open_url(url, allowed_hosts=ASSET_HOSTS, referer=referer, timeout=timeout) as response:
        with destination.open("wb") as output:
            written = copy_limited_response(response, output, max_bytes=max_bytes, label="图片")
            output.flush()
            os.fsync(output.fileno())
    return written


def convert_to_jpeg(source: Path, destination: Path, quality: int) -> None:
    sips = shutil.which("sips")
    if sips:
        try:
            subprocess.run(
                [sips, "-s", "format", "jpeg", "-s", "formatOptions", str(quality), str(source), "--out", str(destination)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise DownloadError("sips 无法将下载内容转换为 JPEG") from exc
    else:
        try:
            from PIL import Image
        except ImportError as exc:
            raise DownloadError("需要 macOS sips 或 Pillow 才能转换 JPG") from exc
        try:
            with Image.open(source) as image:
                image.convert("RGB").save(destination, "JPEG", quality=quality)
        except (OSError, ValueError) as exc:
            raise DownloadError("Pillow 无法将下载内容转换为 JPEG") from exc


def validate_jpeg(path: Path) -> None:
    try:
        size = path.stat().st_size
        with path.open("rb") as image_file:
            header = image_file.read(3)
            image_file.seek(-2, os.SEEK_END)
            trailer = image_file.read(2)
    except (OSError, ValueError) as exc:
        raise DownloadError(f"无法读取 JPEG: {path}") from exc
    if size < 5 or header != b"\xff\xd8\xff" or trailer != b"\xff\xd9":
        raise DownloadError(f"JPEG 文件头或结束标记无效: {path}")

    try:
        from PIL import Image
    except ImportError:
        Image = None  # type: ignore[assignment,misc]

    if Image is not None:
        try:
            with Image.open(path) as image:
                if image.format != "JPEG" or image.width <= 0 or image.height <= 0:
                    raise DownloadError(f"输出不是有效 JPEG: {path}")
                image.verify()
            with Image.open(path) as image:
                image.load()
        except DownloadError:
            raise
        except (OSError, ValueError) as exc:
            raise DownloadError(f"JPEG 解码校验失败: {path}") from exc
        return

    sips = shutil.which("sips")
    if not sips:
        raise DownloadError("需要 macOS sips 或 Pillow 才能校验 JPG")
    try:
        result = subprocess.run(
            [sips, "-g", "format", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise DownloadError(f"JPEG 解码校验失败: {path}") from exc
    properties: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.strip().partition(":")
        if separator:
            properties[key] = value.strip()
    try:
        width = int(properties.get("pixelWidth", "0"))
        height = int(properties.get("pixelHeight", "0"))
    except ValueError as exc:
        raise DownloadError(f"JPEG 尺寸校验失败: {path}") from exc
    if properties.get("format", "").lower() not in ("jpeg", "jpg") or width <= 0 or height <= 0:
        raise DownloadError(f"输出不是有效 JPEG: {path}")


def fsync_path(path: Path) -> None:
    with path.open("rb") as file_handle:
        os.fsync(file_handle.fileno())


def fsync_directory(path: Path) -> None:
    try:
        directory_fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(directory_fd)
        except OSError:
            pass
    finally:
        os.close(directory_fd)


def atomic_convert_to_jpeg(source: Path, destination: Path, quality: int) -> None:
    temp_fd, temp_name = tempfile.mkstemp(prefix=".xhs-jpeg-", suffix=".jpg", dir=destination.parent)
    os.close(temp_fd)
    temp_destination = Path(temp_name)
    try:
        convert_to_jpeg(source, temp_destination, quality)
        validate_jpeg(temp_destination)
        fsync_path(temp_destination)
        os.replace(temp_destination, destination)
        fsync_directory(destination.parent)
    finally:
        temp_destination.unlink(missing_ok=True)


def download_job(
    job: Job,
    *,
    quality: int = 92,
    force: bool = False,
    timeout: int = 30,
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> int:
    image_urls = extract_image_urls(fetch_post_html(job.url, timeout=timeout))
    job.output_dir.mkdir(parents=True, exist_ok=True)
    completed = 0
    for index, image_url in enumerate(image_urls, start=1):
        destination = job.output_dir / f"{index:02d}.jpg"
        if destination.exists() and not force:
            try:
                validate_jpeg(destination)
            except DownloadError:
                pass
            else:
                completed += 1
                continue
        source_fd, source_name = tempfile.mkstemp(prefix=".xhs-source-", suffix=".part", dir=job.output_dir)
        os.close(source_fd)
        source = Path(source_name)
        try:
            written = download_asset(
                image_url,
                source,
                referer=job.url,
                timeout=timeout,
                max_bytes=max_image_bytes,
            )
            if written == 0:
                raise DownloadError(f"下载到空图片: {safe_url_label(image_url)}")
            atomic_convert_to_jpeg(source, destination, quality)
        finally:
            source.unlink(missing_ok=True)
        completed += 1
    return completed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量下载小红书图文帖子图片并转换为 JPG")
    parser.add_argument("urls", nargs="+", help="一个或多个小红书帖子 URL")
    parser.add_argument("-o", "--output-dir", type=Path, default=Path("xhs-images"), help="输出根目录")
    parser.add_argument("--quality", type=int, default=92, choices=range(1, 101), metavar="1-100", help="JPG 质量")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的 JPG")
    parser.add_argument("--timeout", type=int, default=30, choices=range(1, 301), metavar="1-300", help="网络超时秒数")
    parser.add_argument(
        "--max-image-size-mib",
        type=int,
        default=DEFAULT_MAX_IMAGE_BYTES // (1024 * 1024),
        choices=range(1, 1025),
        metavar="1-1024",
        help="单张图片大小上限 MiB",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    jobs, input_errors = plan_jobs(args.urls, args.output_dir)
    for error in input_errors:
        print(f"失败 URL: {error}", file=sys.stderr)
    failures = len(input_errors)
    total = 0
    for job in jobs:
        try:
            count = download_job(
                job,
                quality=args.quality,
                force=args.force,
                timeout=args.timeout,
                max_image_bytes=args.max_image_size_mib * 1024 * 1024,
            )
            total += count
            print(f"成功 {job.post_id}: {count} 张 -> {job.output_dir}")
        except (OSError, ValueError, DownloadError) as exc:
            failures += 1
            print(f"失败 {job.post_id}: {exc}", file=sys.stderr)
    successful = len(jobs) - (failures - len(input_errors))
    print(f"汇总: {successful}/{len(jobs) + len(input_errors)} 个帖子，{total} 张 JPG")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
