#!/usr/bin/env python3
"""Download images from one or more public Xiaohongshu posts as JPEG files."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


POST_ID_RE = re.compile(r"/(?:discovery/item|explore)/([0-9a-fA-F]{24})(?:[/?#]|$)")
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/136.0 Safari/537.36"


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


def extract_post_id(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host != "xiaohongshu.com" and not host.endswith(".xiaohongshu.com"):
        raise ValueError(f"不是受支持的小红书 URL: {url}")
    match = POST_ID_RE.search(parsed.path + "/")
    if not match:
        raise ValueError(f"无法从小红书 URL 提取帖子 ID: {url}")
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
        if normalized and normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    if not unique:
        raise ValueError("公开页面中未找到图片；帖子可能失效、不是图文帖子或需要浏览器兜底")
    return unique


def request_bytes(url: str, *, referer: str | None = None, timeout: int = 30) -> tuple[bytes, str]:
    curl = shutil.which("curl")
    if curl:
        command = [curl, "-fsSL", "--max-time", str(timeout), "-A", USER_AGENT]
        if referer:
            command.extend(["-e", referer])
        command.append(url)
        result = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return result.stdout, "application/octet-stream"
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(), response.headers.get_content_type()


def fetch_post_html(url: str) -> str:
    data, _ = request_bytes(url, referer="https://www.xiaohongshu.com/")
    return data.decode("utf-8", errors="replace")


def convert_to_jpeg(source: Path, destination: Path, quality: int) -> None:
    sips = shutil.which("sips")
    if sips:
        subprocess.run(
            [sips, "-s", "format", "jpeg", "-s", "formatOptions", str(quality), str(source), "--out", str(destination)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    else:
        try:
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError("需要 macOS sips 或 Pillow 才能转换 JPG") from exc
        with Image.open(source) as image:
            image.convert("RGB").save(destination, "JPEG", quality=quality)
    if not destination.exists() or destination.stat().st_size < 4 or destination.read_bytes()[:3] != b"\xff\xd8\xff":
        raise RuntimeError(f"输出不是有效 JPEG: {destination}")


def download_job(job: Job, *, quality: int = 92, force: bool = False) -> int:
    image_urls = extract_image_urls(fetch_post_html(job.url))
    job.output_dir.mkdir(parents=True, exist_ok=True)
    completed = 0
    for index, image_url in enumerate(image_urls, start=1):
        destination = job.output_dir / f"{index:02d}.jpg"
        if destination.exists() and not force:
            completed += 1
            continue
        data, _ = request_bytes(image_url, referer=job.url)
        if not data:
            raise RuntimeError(f"下载到空图片: {image_url}")
        with tempfile.NamedTemporaryFile(dir=job.output_dir, suffix=".webp", delete=False) as temp:
            temp.write(data)
            source = Path(temp.name)
        try:
            convert_to_jpeg(source, destination, quality)
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
            count = download_job(job, quality=args.quality, force=args.force)
            total += count
            print(f"成功 {job.post_id}: {count} 张 -> {job.output_dir}")
        except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, urllib.error.URLError) as exc:
            failures += 1
            print(f"失败 {job.post_id}: {exc}", file=sys.stderr)
    successful = len(jobs) - (failures - len(input_errors))
    print(f"汇总: {successful}/{len(jobs) + len(input_errors)} 个帖子，{total} 张 JPG")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
