import importlib.util
import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "qzone_post_recovery.py"
SPEC = importlib.util.spec_from_file_location("qzone_post_recovery", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ParsingTests(unittest.TestCase):
    def test_forward_keeps_current_owner_and_original_author_separate(self):
        item = MODULE.parse_event_text("本人昵称 转了此条信息 原作者：转发正文")
        self.assertEqual(item["owner"], "本人昵称")
        self.assertEqual(item["original_author"], "原作者")
        self.assertEqual(item["body"], "转发正文")
        self.assertEqual(item["event_type"], "forward")

    def test_history_html_parser_collects_text_time_and_all_images(self):
        html = (
            "<li class=\"f-single f-s-s\">"
            "<a class=\"f-name q_namecard\" link=\"nameCard_0012345\">历史昵称</a>"
            "<div class=\"info-detail\">2020年01月02日 03:04</div>"
            "<p class=\"txt-box-title ellipsis-one\"><b>本人昵称</b>：正文</p>"
            "<a class=\"img-item\"><img src=\"//example.qq.com/a.jpg\"></a>"
            "<a class=\"img-item\"><img data-src=\"https://example.qq.com/b.png\"></a>"
            "</li>"
        )
        payload = "callback({html:'" + html.replace("/", r"\/") + "',opuin:'1'});"
        records = MODULE.parse_history_response(payload)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["text"], "本人昵称 ：正文")
        self.assertEqual(records[0]["time"], "2020年01月02日 03:04")
        self.assertEqual(len(records[0]["images"]), 2)
        self.assertTrue(all(url.startswith("https://") for url in records[0]["images"]))
        self.assertEqual(records[0]["owner_name"], "历史昵称")
        self.assertEqual(records[0]["owner_uin"], "12345")

    def test_comment_activity_extracts_actor_instead_of_whole_prefix(self):
        item = MODULE.parse_event_text("好友昵称 评论了你的说说：说得好")
        self.assertEqual(item["owner"], "好友昵称")
        self.assertEqual(item["event_type"], "comment")
        self.assertEqual(item["body"], "评论了你的说说：说得好")

    def test_rejects_non_qq_network_target(self):
        with self.assertRaises(MODULE.RecoveryError):
            MODULE.validate_qq_url("https://127.0.0.1/private")
        with self.assertRaises(MODULE.RecoveryError):
            MODULE.validate_qq_url("http://user.qzone.qq.com/insecure")
        with self.assertRaises(MODULE.RecoveryError):
            MODULE.validate_qq_url("https://user.qzone.qq.com:8443/private")
        self.assertEqual(MODULE.safe_qq_media_url("javascript:alert(1)"), "")

    def test_rejects_dependency_outside_security_reviewed_range(self):
        with self.assertRaises(MODULE.RecoveryError):
            MODULE.ensure_supported_requests("2.31.0")
        MODULE.ensure_supported_requests("2.34.2")
        with self.assertRaises(MODULE.RecoveryError):
            MODULE.ensure_supported_requests("3.0.0")

    def test_rejects_wsl_windows_drive_for_private_archive(self):
        with self.assertRaises(MODULE.RecoveryError):
            MODULE.ensure_private_output_location(
                Path("/mnt/e/QQ空间档案"),
                platform="posix",
                kernel_release="6.6.87.2-microsoft-standard-WSL2",
                mountinfo_text="",
            )

    def test_rejects_custom_drvfs_mount_for_private_archive(self):
        mountinfo = (
            "90 75 0:50 / /windows rw,relatime - 9p drvfs rw,dirsync,aname=drvfs\n"
        )
        with self.assertRaises(MODULE.RecoveryError):
            MODULE.ensure_private_output_location(
                Path("/windows/private"), platform="posix", kernel_release="microsoft",
                mountinfo_text=mountinfo,
            )
        MODULE.ensure_private_output_location(
            Path("/home/test/archives"), platform="posix", kernel_release="microsoft",
            mountinfo_text=mountinfo,
        )

    def test_visible_scan_marks_short_page_incomplete(self):
        class Response:
            def __init__(self, payload):
                self.content = ("callback(" + json.dumps(payload) + ");").encode()

        session = MODULE.requests.Session()
        session.cookies.set("p_skey", "token", domain=".qq.com", path="/")
        responses = [
            Response({"code": 0, "total": 2, "msglist": []}),
            Response({"code": 0, "total": 2, "msglist": [{"tid": "one"}]}),
        ]
        with mock.patch.object(MODULE, "request_retry", side_effect=responses):
            records, complete, warnings = MODULE.fetch_visible(session, "12345", page_delay=0)
        self.assertEqual(len(records), 1)
        self.assertFalse(complete)
        self.assertTrue(any("实际读取 1 条" in warning for warning in warnings))

    def test_visible_scan_rejects_duplicate_pages_as_complete(self):
        class Response:
            def __init__(self, payload):
                self.content = ("callback(" + json.dumps(payload) + ");").encode()

        session = MODULE.requests.Session()
        session.cookies.set("p_skey", "token", domain=".qq.com", path="/")
        repeated = {"tid": "same", "created_time": 1, "content": "重复"}
        responses = [
            Response({"code": 0, "total": 31, "msglist": []}),
            Response({"code": 0, "total": 31, "msglist": [repeated]}),
            Response({"code": 0, "total": 31, "msglist": [repeated]}),
        ]
        with mock.patch.object(MODULE, "request_retry", side_effect=responses):
            records, complete, warnings = MODULE.fetch_visible(session, "12345", page_delay=0)
        self.assertEqual(len(records), 1)
        self.assertFalse(complete)
        self.assertTrue(any("重复返回" in warning for warning in warnings))

    def test_wsl_opener_passes_path_over_stdin_not_powershell_source(self):
        converted = r"C:\archive;Write-Output injected\index.html"
        completed = MODULE.subprocess.CompletedProcess([], 0, stdout=converted + "\n", stderr="")
        with mock.patch.object(MODULE.subprocess, "run", side_effect=[completed, completed]) as run:
            MODULE.open_file(Path("/tmp/archive;unsafe/index.html"))
        powershell_args = run.call_args_list[1].args[0]
        self.assertNotIn(converted, " ".join(powershell_args))
        self.assertEqual(run.call_args_list[1].kwargs["input"], converted)


class ClassificationTests(unittest.TestCase):
    def visible(self, body="还在的说说"):
        return {
            "id": "visible-1", "category": "visible", "kind": "当前可见", "owner": "本人昵称",
            "owner_uin": "12345", "evidence": "当前可见",
            "body": body, "original_author": "", "event_type": "post",
            "timestamps": ["2024-01-01 00:00:00"], "timestamp_value": 1704067200,
            "year": "2024", "images": [], "remote_images": [], "videos": [], "comments": [],
            "occurrences": 1, "source": "当前可见说说接口", "tid": "1",
        }

    def test_nickname_mentioned_in_body_does_not_change_publisher(self):
        history = [{"time": "2020年01月02日 03:04", "text": "好友昵称：今天碰到本人昵称了", "images": []}]
        recovered, pending, events, interactions = MODULE.classify_history(
            history, [], "本人昵称", "12345", comparison_complete=True,
        )
        self.assertEqual((recovered, pending, events), ([], [], []))
        self.assertEqual(interactions[0]["owner"], "好友昵称")

    def test_current_visible_exact_body_is_not_deleted_candidate(self):
        history = [{"time": "2020年01月02日 03:04", "text": "本人昵称：还在的说说", "images": []}]
        result = MODULE.classify_history(
            history, [self.visible()], "本人昵称", "12345", comparison_complete=True,
        )
        self.assertEqual(result, ([], [], [], []))

    def test_missing_self_post_becomes_recovered_candidate_after_complete_comparison(self):
        history = [{
            "time": "2020年01月02日 03:04", "text": "旧昵称：已经不见的说说", "images": [],
            "owner_name": "旧昵称", "owner_uin": "12345",
        }]
        recovered, pending, events, interactions = MODULE.classify_history(
            history, [self.visible()], "本人昵称", "12345", comparison_complete=True,
        )
        self.assertEqual((pending, events, interactions), ([], [], []))
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["kind"], "找回的删除候选")
        self.assertEqual(recovered[0]["owner_uin"], "12345")

    def test_incomplete_visible_scan_never_claims_deleted_candidate(self):
        history = [{
            "time": "2020年01月02日 03:04", "text": "本人昵称：仍然存在", "images": [],
            "owner_uin": "12345",
        }]
        recovered, pending, events, interactions = MODULE.classify_history(
            history, [], "本人昵称", "12345", comparison_complete=False,
        )
        self.assertEqual((recovered, events, interactions), ([], [], []))
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["kind"], "待核对的本人说说")

    def test_publish_event_is_separate_and_never_counted_as_recovered(self):
        history = [{
            "time": "2020年01月02日 03:04", "text": "本人昵称 发表说说", "images": [],
            "owner_uin": "12345",
        }]
        recovered, pending, events, interactions = MODULE.classify_history(
            history, [self.visible()], "本人昵称", "12345", comparison_complete=True,
        )
        self.assertEqual((recovered, pending, interactions), ([], [], []))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "仅发现发布事件")

    def test_structured_uin_overrides_matching_nickname(self):
        history = [{
            "time": "2020年01月02日 03:04", "text": "本人昵称：不是本人发的", "images": [],
            "owner_name": "本人昵称", "owner_uin": "99999",
        }]
        recovered, pending, events, interactions = MODULE.classify_history(
            history, [], "本人昵称", "12345", comparison_complete=True,
        )
        self.assertEqual((recovered, pending, events), ([], [], []))
        self.assertEqual(len(interactions), 1)

    def test_self_comment_remains_interaction_not_deleted_post(self):
        history = [{"time": "2020年01月02日 03:04", "text": "本人昵称 评论了好友的说说：支持", "images": []}]
        recovered, pending, events, interactions = MODULE.classify_history(
            history, [], "本人昵称", "12345", comparison_complete=True,
        )
        self.assertEqual((recovered, pending, events), ([], [], []))
        self.assertEqual(interactions[0]["owner"], "本人昵称")
        self.assertEqual(interactions[0]["kind"], "评论相关")


class ArtifactTests(unittest.TestCase):
    def test_builds_html_json_and_no_spreadsheet(self):
        current = {
            "content": "当前内容 </script><script>window.pwned=true</script>",
            "name": "本人昵称", "created_time": 1704067200, "tid": "tid-1",
            "commentlist": [{"content": "一条评论", "name": "好友", "uin": 2, "createTime2": "2024-01-01 01:00:00"}],
        }
        history = [{"time": "2020年01月02日 03:04", "text": "本人昵称：已删除候选", "images": []}]
        with tempfile.TemporaryDirectory() as directory:
            session = MODULE.requests.Session()
            session.cookies.set("p_skey", "must-not-leak", domain=".qq.com", path="/")
            package = MODULE.build_archive(
                Path(directory), session, "12345", "本人昵称", history, [current],
                history_complete=True, comparison_complete=True, warnings=[],
            )
            self.assertTrue((package / "index.html").is_file())
            self.assertTrue((package / "data" / "archive.json").is_file())
            self.assertEqual(list(package.rglob("*.xlsx")), [])
            data = json.loads((package / "data" / "archive.json").read_text(encoding="utf-8"))
            self.assertNotIn("must-not-leak", json.dumps(data, ensure_ascii=False))
            self.assertEqual(data["schema_version"], 2)
            self.assertEqual(data["summary"]["recovered_candidates"], 1)
            self.assertEqual(data["summary"]["pending_candidates"], 0)
            self.assertEqual(data["summary"]["publish_events"], 0)
            self.assertEqual(data["summary"]["comment_count"], 1)
            page = (package / "index.html").read_text(encoding="utf-8")
            self.assertIn("找回的说说", page)
            self.assertIn("待确认", page)
            self.assertIn("发布事件", page)
            self.assertIn("当前可见", page)
            self.assertIn("其他互动", page)
            self.assertIn("Content-Security-Policy", page)
            self.assertNotIn("</script><script>window.pwned", page)
            if hasattr(stat, "S_IMODE"):
                self.assertEqual(stat.S_IMODE(package.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((package / "index.html").stat().st_mode), 0o600)

    def test_private_session_cache_is_written_with_private_permissions(self):
        session = MODULE.requests.Session()
        session.cookies.set("p_skey", "secret", domain=".qq.com", path="/")
        with tempfile.TemporaryDirectory() as directory:
            original = MODULE.CACHE_ROOT
            MODULE.CACHE_ROOT = Path(directory) / "cache"
            try:
                MODULE.save_session(session, "12345")
                cache = MODULE.CACHE_ROOT
                path = cache / "sessions" / "12345.json"
                self.assertEqual(stat.S_IMODE(cache.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            finally:
                MODULE.CACHE_ROOT = original


class PackageContractTests(unittest.TestCase):
    def test_skill_name_and_prompt_match_directory(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        metadata = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        evals = json.loads((ROOT / "evals" / "evals.json").read_text(encoding="utf-8"))
        self.assertIn("name: qzone-post-recovery", skill)
        self.assertIn("$qzone-post-recovery", metadata)
        self.assertEqual(evals["skill_name"], ROOT.name)


if __name__ == "__main__":
    unittest.main()
