import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.request import Request


SCRIPT = Path(__file__).parents[1] / "scripts" / "download_xhs_images.py"
SPEC = importlib.util.spec_from_file_location("download_xhs_images", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class FakeResponse:
    def __init__(self, data, headers=None):
        self.stream = io.BytesIO(data)
        self.headers = headers or {}

    def read(self, size=-1):
        return self.stream.read(size)


class DownloaderTests(unittest.TestCase):
    def test_extracts_post_id_from_discovery_url(self):
        url = "https://www.xiaohongshu.com/discovery/item/6a51b78f000000001700ac49?xsec_token=abc"
        self.assertEqual(MODULE.extract_post_id(url), "6a51b78f000000001700ac49")

    def test_rejects_non_xiaohongshu_url(self):
        with self.assertRaisesRegex(ValueError, "小红书"):
            MODULE.extract_post_id("https://example.com/discovery/item/6a51b78f000000001700ac49")

    def test_rejects_non_https_xiaohongshu_url(self):
        with self.assertRaisesRegex(ValueError, "小红书"):
            MODULE.extract_post_id("ftp://www.xiaohongshu.com/explore/6a51b78f000000001700ac49")

    def test_parses_and_deduplicates_json_ld_images(self):
        payload = {
            "@type": "Article",
            "image": [
                "http://sns-webpic-qc.xhscdn.com/a.webp",
                "https://sns-webpic-qc.xhscdn.com/b.webp",
                "http://sns-webpic-qc.xhscdn.com/a.webp",
            ],
        }
        html = (
            '<html><script type="application/ld+json">'
            + json.dumps(payload)
            + "</script></html>"
        )
        self.assertEqual(
            MODULE.extract_image_urls(html),
            [
                "https://sns-webpic-qc.xhscdn.com/a.webp",
                "https://sns-webpic-qc.xhscdn.com/b.webp",
            ],
        )

    def test_builds_one_output_directory_per_unique_post(self):
        urls = [
            "https://www.xiaohongshu.com/discovery/item/aaaaaaaaaaaaaaaaaaaaaaaa",
            "https://www.xiaohongshu.com/explore/bbbbbbbbbbbbbbbbbbbbbbbb",
            "https://www.xiaohongshu.com/discovery/item/aaaaaaaaaaaaaaaaaaaaaaaa?x=1",
        ]
        jobs = MODULE.build_jobs(urls, Path("xhs-images"))
        self.assertEqual([job.post_id for job in jobs], ["aaaaaaaaaaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbbbbbbbbbb"])
        self.assertEqual(jobs[0].output_dir, Path("xhs-images/aaaaaaaaaaaaaaaaaaaaaaaa"))

    def test_invalid_url_does_not_discard_valid_batch_jobs(self):
        jobs, errors = MODULE.plan_jobs(
            [
                "https://example.com/not-xhs",
                "https://www.xiaohongshu.com/explore/bbbbbbbbbbbbbbbbbbbbbbbb",
            ],
            Path("xhs-images"),
        )
        self.assertEqual([job.post_id for job in jobs], ["bbbbbbbbbbbbbbbbbbbbbbbb"])
        self.assertEqual(len(errors), 1)

    def test_rejects_unapproved_asset_url(self):
        html = '<script type="application/ld+json">{"image":["file:///etc/passwd"]}</script>'
        with self.assertRaisesRegex(ValueError, "不受支持"):
            MODULE.extract_image_urls(html)

    def test_redirect_handler_rejects_cross_host_redirect(self):
        handler = MODULE.ValidatingRedirectHandler(MODULE.PAGE_HOSTS)
        request = Request("https://www.xiaohongshu.com/explore/" + "a" * 24)
        with self.assertRaisesRegex(MODULE.DownloadError, "允许列表"):
            handler.redirect_request(request, None, 302, "Found", {}, "https://example.com/steal")

    def test_safe_url_label_removes_credentials_query_and_fragment(self):
        url = "https://user:pass@www.xiaohongshu.com/explore/abc?xsec_token=SECRET#fragment"
        label = MODULE.safe_url_label(url)
        self.assertEqual(label, "https://www.xiaohongshu.com/explore/abc")
        self.assertNotIn("SECRET", label)
        self.assertNotIn("user", label)

    def test_response_limit_and_content_length_integrity(self):
        with self.assertRaisesRegex(MODULE.DownloadError, "大小上限"):
            MODULE.copy_limited_response(
                FakeResponse(b"abc", {"Content-Length": "10"}),
                io.BytesIO(),
                max_bytes=5,
                label="图片",
            )
        with self.assertRaisesRegex(MODULE.DownloadError, "响应不完整"):
            MODULE.copy_limited_response(
                FakeResponse(b"abc", {"Content-Length": "4"}),
                io.BytesIO(),
                max_bytes=5,
                label="图片",
            )
        with self.assertRaisesRegex(MODULE.DownloadError, "大小上限"):
            MODULE.copy_limited_response(
                FakeResponse(b"abcdef"),
                io.BytesIO(),
                max_bytes=5,
                label="图片",
            )

    def test_failed_force_conversion_preserves_existing_jpeg(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "01.jpg"
            destination.write_bytes(b"old-valid-file")
            source = Path(temp_dir) / "source.part"
            source.write_bytes(b"source")

            def fail_after_partial_write(_source, temp_destination, _quality):
                temp_destination.write_bytes(b"partial-new-file")
                raise MODULE.DownloadError("conversion failed")

            with mock.patch.object(MODULE, "convert_to_jpeg", side_effect=fail_after_partial_write):
                with self.assertRaisesRegex(MODULE.DownloadError, "conversion failed"):
                    MODULE.atomic_convert_to_jpeg(source, destination, 92)
            self.assertEqual(destination.read_bytes(), b"old-valid-file")

    def test_corrupt_existing_jpeg_is_redownloaded_not_skipped(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / ("a" * 24)
            output_dir.mkdir()
            destination = output_dir / "01.jpg"
            destination.write_bytes(b"not a jpeg")
            job = MODULE.Job(
                "https://www.xiaohongshu.com/explore/" + "a" * 24,
                "a" * 24,
                output_dir,
            )
            html = '<script type="application/ld+json">{"image":["https://sns-webpic-qc.xhscdn.com/a.webp"]}</script>'

            def fake_download(_url, source, **_kwargs):
                source.write_bytes(b"source")
                return 6

            def fake_convert(_source, final_destination, _quality):
                final_destination.write_bytes(b"replacement")

            with mock.patch.object(MODULE, "fetch_post_html", return_value=html), mock.patch.object(
                MODULE, "download_asset", side_effect=fake_download
            ) as download, mock.patch.object(MODULE, "atomic_convert_to_jpeg", side_effect=fake_convert):
                self.assertEqual(MODULE.download_job(job), 1)
            download.assert_called_once()
            self.assertEqual(destination.read_bytes(), b"replacement")


if __name__ == "__main__":
    unittest.main()
