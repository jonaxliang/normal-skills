import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import URLError


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "download_douyin_video.py"
SPEC = importlib.util.spec_from_file_location("download_douyin_video", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, *, content_type: str, content_length: int):
        super().__init__(body)
        self.headers = {
            "Content-Type": content_type,
            "Content-Length": str(content_length),
        }

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class DownloaderTests(unittest.TestCase):
    def test_extracts_url_from_pasted_share_text(self):
        text = "复制打开抖音 https://v.douyin.com/example/ 更多文字"
        self.assertEqual(MODULE.extract_urls([text]), ["https://v.douyin.com/example/"])

    def test_query_video_id_is_not_shadowed_by_unrelated_query_text(self):
        target = "7662067498049950641"
        other = "7111111111111111111"
        url = f"https://www.douyin.com/?noise=/video/{other}&modal_id={target}"
        self.assertEqual(MODULE.video_id_from_url(url), target)

    def test_authoritative_html_ignores_recommended_video_links(self):
        target = "7662067498049950641"
        other = "7111111111111111111"
        html = (
            f'<a href="/video/{other}">recommended</a>'
            f'<link rel="canonical" href="https://www.douyin.com/video/{target}">'
        )
        self.assertEqual(MODULE.authoritative_video_id_from_html(html), target)
        self.assertIsNone(
            MODULE.authoritative_video_id_from_html(
                f'<a href="/video/{other}">recommended</a>'
            )
        )

    def test_rejects_non_douyin_and_malformed_urls_without_logging_query(self):
        with self.assertRaises(MODULE.DownloadError):
            MODULE.validate_https_url("https://127.0.0.1/video/123", MODULE.PAGE_HOSTS)
        self.assertEqual(MODULE.safe_url_label("https://example.com/a?token=secret#x"), "https://example.com/a")
        self.assertEqual(MODULE.safe_url_label("https://["), "https://[")

    def test_finds_requested_item_across_multiple_item_lists(self):
        target = "7662067498049950641"
        value = {
            "recommended": {"item_list": [{"aweme_id": "7111111111111111111"}]},
            "detail": {"item_list": [{"aweme_id": target, "video": {}}]},
        }
        self.assertEqual(MODULE.find_video_item(value, target)["aweme_id"], target)

    def test_parses_exact_video_metadata_and_clean_play_endpoint(self):
        target = "7662067498049950641"
        payload = {
            "loaderData": {
                "detail": {
                    "item_list": [
                        {
                            "aweme_id": target,
                            "desc": "标题\n#话题",
                            "author": {"nickname": "作者"},
                            "video": {
                                "play_addr": {
                                    "url_list": [
                                        "https://aweme.snssdk.com/aweme/v1/playwm/?video_id=abc&ratio=720p"
                                    ]
                                }
                            },
                        }
                    ]
                }
            }
        }
        html = f"<script>window._ROUTER_DATA = {json.dumps(payload)};</script>"
        info = MODULE.parse_video_info(html, target, keep_watermark=False)
        self.assertEqual((info.author, info.title), ("作者", "标题"))
        self.assertIn("/aweme/v1/play/", info.media_urls[0])
        self.assertIn("ratio=1080p", info.media_urls[0])

    def test_output_name_respects_utf8_limit_and_separates_watermark_variant(self):
        info = MODULE.VideoInfo(
            "7662067498049950641",
            "作者" * 100,
            "超长标题" * 100,
            ("https://aweme.snssdk.com/test",),
        )
        clean = MODULE.output_path(Path("/tmp"), info, False).name
        marked = MODULE.output_path(Path("/tmp"), info, True).name
        self.assertLessEqual(len(clean.encode("utf-8")), 240)
        self.assertLessEqual(len(marked.encode("utf-8")), 240)
        self.assertNotEqual(clean, marked)
        self.assertTrue(marked.endswith("-watermarked.mp4"))

    def test_truncated_response_preserves_existing_destination(self):
        original_open_url = MODULE.open_url
        body = b"\x00\x00\x00\x18ftyp" + b"x" * 92
        MODULE.open_url = lambda *_args, **_kwargs: FakeResponse(
            body, content_type="video/mp4", content_length=1000
        )
        try:
            with tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / "existing.mp4"
                destination.write_bytes(b"old-good-file")
                with self.assertRaisesRegex(MODULE.DownloadError, "incomplete media response"):
                    MODULE.download_candidate(
                        "https://aweme.snssdk.com/test",
                        destination,
                        timeout=5,
                        max_bytes=2048,
                    )
                self.assertEqual(destination.read_bytes(), b"old-good-file")
        finally:
            MODULE.open_url = original_open_url

    def test_invalid_content_length_is_rejected(self):
        original_open_url = MODULE.open_url
        MODULE.open_url = lambda *_args, **_kwargs: FakeResponse(
            b"\x00\x00\x00\x18ftyp" + b"x" * 92,
            content_type="video/mp4",
            content_length="invalid",
        )
        try:
            with tempfile.TemporaryDirectory() as directory:
                destination = Path(directory) / "video.mp4"
                with self.assertRaisesRegex(MODULE.DownloadError, "invalid Content-Length"):
                    MODULE.download_candidate(
                        "https://aweme.snssdk.com/test",
                        destination,
                        timeout=5,
                        max_bytes=2048,
                    )
                self.assertFalse(destination.exists())
        finally:
            MODULE.open_url = original_open_url

    def test_page_request_retries_one_transient_network_failure(self):
        class FakeOpener:
            def __init__(self):
                self.calls = 0

            def open(self, _request, timeout):
                self.calls += 1
                if self.calls == 1:
                    raise URLError("temporary reset")
                return FakeResponse(b"ok", content_type="text/html", content_length=2)

        opener = FakeOpener()
        with mock.patch.object(MODULE, "build_opener", return_value=opener), mock.patch.object(
            MODULE.time, "sleep", return_value=None
        ):
            response = MODULE.open_url(
                "https://v.douyin.com/example/",
                allowed_hosts=MODULE.PAGE_HOSTS,
                timeout=5,
                retries=1,
            )
        self.assertEqual(response.read(), b"ok")
        self.assertEqual(opener.calls, 2)

    def test_curl_short_link_fallback_validates_single_redirect(self):
        target = "7662067498049950641"
        headers = (
            "HTTP/2 302\r\n"
            f"location: https://www.iesdouyin.com/share/video/{target}/?token=secret\r\n\r\n"
        )
        result = MODULE.subprocess.CompletedProcess([], 0, stdout=headers, stderr="")
        with mock.patch.object(MODULE.shutil, "which", return_value="/usr/bin/curl"), mock.patch.object(
            MODULE.subprocess, "run", return_value=result
        ) as run:
            self.assertEqual(
                MODULE.resolve_short_link_with_curl("https://v.douyin.com/example/", 5), target
            )
        command = run.call_args.args[0]
        self.assertIn("--max-redirs", command)
        self.assertNotIn("https://www.iesdouyin.com/share/video/", " ".join(command))


class PackageContractTests(unittest.TestCase):
    def test_skill_name_matches_directory_and_eval_metadata(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        evals = json.loads((ROOT / "evals" / "evals.json").read_text(encoding="utf-8"))
        self.assertIn("name: download-douyin-video", skill)
        self.assertEqual(evals["skill_name"], ROOT.name)

    def test_openai_interface_uses_explicit_skill_name(self):
        metadata = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("$download-douyin-video", metadata)
        self.assertIn('display_name: "下载抖音视频"', metadata)


if __name__ == "__main__":
    unittest.main()
