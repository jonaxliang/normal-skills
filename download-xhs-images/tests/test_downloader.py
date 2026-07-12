import importlib.util
import json
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "download_xhs_images.py"
SPEC = importlib.util.spec_from_file_location("download_xhs_images", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class DownloaderTests(unittest.TestCase):
    def test_extracts_post_id_from_discovery_url(self):
        url = "https://www.xiaohongshu.com/discovery/item/6a51b78f000000001700ac49?xsec_token=abc"
        self.assertEqual(MODULE.extract_post_id(url), "6a51b78f000000001700ac49")

    def test_rejects_non_xiaohongshu_url(self):
        with self.assertRaisesRegex(ValueError, "小红书"):
            MODULE.extract_post_id("https://example.com/discovery/item/6a51b78f000000001700ac49")

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


if __name__ == "__main__":
    unittest.main()
