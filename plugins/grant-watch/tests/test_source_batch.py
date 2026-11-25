import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS=Path(__file__).parents[1]/"scripts"
sys.path.insert(0,str(SCRIPTS))
import source_batch as b


class SourceBatchTests(unittest.TestCase):
    def snapshot(self, urls):
        keys=["round-"+str(i) for i in range(len(urls))]
        return dict(mode="fixture",run_key="grant-watch:2026-11-26",run_started_at="2026-11-26T06:00:00Z",
            started_at="2026-11-26T06:00:01Z",run_used_requests=0,
            limits=dict(requests=2000,runtime_seconds=43200,max_parallel_per_host=1,min_host_interval_seconds=2,
                        max_parallel=4,request_timeout_seconds=30,max_retries=2),
            reservation=dict(read_back=True,run_key="grant-watch:2026-11-26",notion_url="https://notion.example/reservation",
                             batch_id="batch-1",request_allowance=100,keys=keys),
            targets=[dict(key=k,url=u) for k,u in zip(keys,urls)],
            responses={url:[dict(status=200,duration=1.759,body="Source facts require verification")] for url in urls})

    def cli(self, data, folder, expect_ok=True):
        source=folder/"input.json"
        source.write_text(json.dumps(data),encoding="utf-8")
        process=subprocess.run([sys.executable,str(SCRIPTS/"source_batch.py"),str(source),"--output-dir",str(folder/"result")],
                               capture_output=True,text=True)
        self.assertEqual(process.returncode==0,expect_ok,process.stderr)
        return json.loads((folder/"result"/"result.json").read_text()) if expect_ok else None

    def test_real_cli_reuses_identical_url_and_preserves_rounds_and_host_limits(self):
        data=self.snapshot(["https://a.example/x","https://a.example/x","https://a.example/y","https://b.example/x"])
        with tempfile.TemporaryDirectory() as folder:
            result=self.cli(data,Path(folder))
            self.assertEqual(result["actual_requests"],3)
            self.assertEqual(len(result["results"]["https://a.example/x"]["keys"]),2)
            self.assertEqual(result["verified_calls"],0)
            self.assertTrue(result["no_send"])
            from datetime import datetime
            a=[r for r in result["requests"] if r["host"]=="a.example"]
            self.assertGreaterEqual((datetime.fromisoformat(a[1]["started"])-datetime.fromisoformat(a[0]["started"])).total_seconds(),2)
            self.assertGreaterEqual(a[1]["started"],a[0]["finished"])
            self.cli(data,Path(folder),expect_ok=False)

    def test_retry_redirect_hard_timeout_and_access_block_are_counted(self):
        data=self.snapshot(["https://a.example/x","https://a.example/y","https://b.example/x","https://c.example/x"])
        data["responses"].update({"https://a.example/x":[dict(status=429,duration=1)],
            "https://b.example/x":[dict(status=302,location="https://b.example/new",duration=1)],
            "https://b.example/new":[dict(status=200,duration=1)],
            "https://c.example/x":[dict(status=200,duration=31)]})
        with tempfile.TemporaryDirectory() as folder:
            result=self.cli(data,Path(folder))
            self.assertEqual(result["actual_requests"],6)  # 429, redirect+destination, three timed attempts
            self.assertFalse(result["results"]["https://a.example/y"]["attempted"])
            self.assertEqual(result["results"]["https://c.example/x"]["error"],"hard request timeout")
            self.assertEqual(len([r for r in result["requests"] if r["host"]=="a.example"]),1)
            self.assertEqual(result["results"]["https://b.example/x"]["final_url"],"https://b.example/new")

    def test_live_is_never_implicit_and_allowance_is_cumulative(self):
        data=self.snapshot(["https://a.example/x"])
        with tempfile.TemporaryDirectory() as folder:
            data["mode"]="live"
            self.cli(data,Path(folder),expect_ok=False)
        data["mode"]="fixture";data["run_used_requests"]=1950
        with tempfile.TemporaryDirectory() as folder:self.cli(data,Path(folder),expect_ok=False)
        data["run_used_requests"]=0;data["reservation"]["read_back"]=False
        with tempfile.TemporaryDirectory() as folder:self.cli(data,Path(folder),expect_ok=False)

    def test_same_run_cache_requires_content_hash_and_refuses_unknown_body(self):
        data=self.snapshot(["https://a.example/x"])
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);first=root/"first";first.mkdir()
            result=self.cli(data,first)
            data["cache"]=result["results"]
            data["started_at"]="2026-11-26T06:01:00Z"
            second=root/"second";second.mkdir()
            cached=self.cli(data,second)
            self.assertEqual(cached["actual_requests"],0)
            self.assertEqual(cached["reused_responses"],1)
            self.assertEqual(cached["results"]["https://a.example/x"]["fetched_at"],result["results"]["https://a.example/x"]["fetched_at"])
            Path(result["results"]["https://a.example/x"]["body_path"]).write_text("changed")
            third=root/"third";third.mkdir()
            refreshed=self.cli(data,third)
            self.assertEqual(refreshed["actual_requests"],1)

    def test_desktop_socket_permission_failure_stops_new_work_without_retries(self):
        data=self.snapshot(["https://a.example/"+str(i) for i in range(8)])
        data["responses"]["https://a.example/0"]=[dict(status=None,error="WinError 10013 access permissions",duration=.1)]
        with tempfile.TemporaryDirectory() as folder:
            result=self.cli(data,Path(folder))
            self.assertEqual(result["actual_requests"],1)
            self.assertEqual(sum(r.get("attempted") is False for r in result["results"].values()),7)


if __name__=="__main__":unittest.main()
