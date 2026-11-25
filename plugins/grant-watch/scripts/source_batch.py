"""Desktop GET-only batch transport. No grants verification, Notion writes or email.

Fixture mode uses the same scheduler with a virtual clock and never opens sockets.
Live mode requires an acknowledged same-run Notion allowance and --live. It uses
one child process per attempt for a hard timeout; redirects/retries are explicit.
"""
import argparse
import concurrent.futures
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urljoin, urlsplit

import cadence_contract as cadence

MAX_BYTES = 10 * 1024 * 1024


def atomic_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=True), encoding="utf-8")
    temporary.replace(path)


def request_one(url, destination, timeout):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    started = time.monotonic()
    try:
        opener = urllib.request.build_opener(NoRedirect())
        request = urllib.request.Request(cadence.safe_url(url), headers={"User-Agent": "GrantWatch/1.0"})
        try:
            response = opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError("Response exceeds bounded size")
            destination.write_bytes(body)
            return dict(status=response.code, location=response.headers.get("Location"),
                        content_type=response.headers.get("Content-Type"), body_path=str(destination),
                        body_sha256=hashlib.sha256(body).hexdigest(), duration=time.monotonic()-started)
    except Exception as exc:
        return dict(status=None, error=str(exc)[:500], duration=time.monotonic()-started)


def live_attempt(url, destination, timeout):
    result_path = destination.with_suffix(".response.json")
    started = time.monotonic()
    try:
        subprocess.run([sys.executable, str(Path(__file__)), "--request-one", url, str(destination),
                        str(timeout), str(result_path)], check=True, timeout=timeout, capture_output=True)
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except subprocess.TimeoutExpired:
        result = dict(status=None, error="hard request timeout")
    except Exception as exc:
        result = dict(status=None, error=str(exc)[:500])
    result["duration"] = time.monotonic()-started
    return result


def run(data, folder, live=False):
    mode = data["mode"]
    if mode not in {"fixture", "live"} or (mode == "live") != live:
        raise ValueError("Explicit --live is required only for live transport")
    limits = data["limits"]
    if (limits["max_parallel_per_host"] != 1 or limits["min_host_interval_seconds"] < 2 or
        not 1 <= limits["max_parallel"] <= 4 or limits["request_timeout_seconds"] > 30 or
        not 0 <= limits["max_retries"] <= 2):
        raise ValueError("Host pacing, concurrency, timeout or retry policy invalid")
    reservation = data["reservation"]
    if reservation.get("read_back") is not True or reservation["run_key"] != data["run_key"] or not reservation.get("notion_url"):
        raise ValueError("Same-run durable request allowance must be read back first")
    if (type(reservation["request_allowance"]) is not int or reservation["request_allowance"] < 1 or
        data["run_used_requests"] + reservation["request_allowance"] > limits["requests"]):
        raise ValueError("Batch allowance exceeds cumulative request budget")
    started = cadence.stamp(data["started_at"])
    deadline = cadence.stamp(data["run_started_at"]) + timedelta(seconds=limits["runtime_seconds"])
    if started >= deadline:
        raise ValueError("Run processing window elapsed")
    if mode == "live":
        now = datetime.now(timezone.utc)
        local = now.astimezone(cadence.TZ)
        if (data["run_key"] != "grant-watch:" + local.date().isoformat() or local.weekday() not in cadence.RUN_DAYS or
            now < cadence.wake_at(local.date()) or abs((now-started).total_seconds()) > 60):
            raise ValueError("Live batch must belong to today's active scheduled run after 08:00")
    folder.mkdir(parents=True, exist_ok=True)
    journal = folder / "batch-journal.json"
    if journal.exists():
        raise ValueError("Batch already started; read its journal and reconcile, never replay")
    tasks = {}
    for target in data["targets"]:
        key, url = target["key"], cadence.safe_url(target["url"])
        if key not in reservation["keys"]:
            raise ValueError("Target outside the durable allowance")
        tasks.setdefault(url, []).append(key)
    if set(k for values in tasks.values() for k in values) != set(reservation["keys"]):
        raise ValueError("Missing reserved targets")
    if len([k for values in tasks.values() for k in values]) != len(set(reservation["keys"])):
        raise ValueError("Duplicate target key")
    log, results, pending, active = [], {}, [], {}
    host_last, blocked = {}, set(data.get("blocked_hosts", []))
    cache = data.get("cache", {})
    reused = 0
    for url, keys in tasks.items():
        existing = cache.get(url)
        if (existing and existing.get("run_key") == data["run_key"] and existing.get("status") == 200 and
            existing.get("fetched_at") and cadence.stamp(data["run_started_at"]) <= cadence.stamp(existing["fetched_at"]) <= started):
            path = Path(existing["body_path"])
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == existing["body_sha256"]:
                results[url] = dict(existing, keys=keys, reused=True)
                reused += 1
                continue
        pending.append(dict(root=url, url=url, keys=keys, retry=0, redirects=0, ready=0.0))
    virtual, real_start = 0.0, time.monotonic()
    environment_failed = False
    allowance = reservation["request_allowance"]
    wall_limit = (deadline-started).total_seconds()
    def now_seconds():
        return virtual if mode == "fixture" else time.monotonic()-real_start
    def save():
        atomic_json(journal, dict(run_key=data["run_key"], batch_id=reservation["batch_id"], mode=mode,
                                 requests=log, results=results, pending=pending, blocked_hosts=sorted(blocked)))
    save()
    with concurrent.futures.ThreadPoolExecutor(max_workers=limits["max_parallel"]) as pool:
        while pending or active:
            now = now_seconds()
            busy = {job["host"] for job in active.values()}
            for job in list(pending):
                host = urlsplit(job["url"]).hostname.lower()
                if environment_failed or host in blocked or now >= wall_limit or len(log) >= allowance:
                    results[job["root"]] = dict(keys=job["keys"], status=None, attempted=False,
                        error="desktop network permission failure" if environment_failed else
                              "host access/backpressure" if host in blocked else "request/runtime allowance exhausted")
                    pending.remove(job)
                    continue
                ready = max(job["ready"], host_last.get(host, -limits["min_host_interval_seconds"]) + limits["min_host_interval_seconds"])
                if host in busy or len(active) >= limits["max_parallel"] or now < ready:
                    continue
                pending.remove(job)
                host_last[host] = now
                job["host"] = host
                job["number"] = len(log)+1
                job["start"] = now
                record = dict(number=job["number"], url=job["url"], root=job["root"], host=host,
                              started=cadence.iso(started+timedelta(seconds=now)), state="in_flight", retry=job["retry"])
                log.append(record)
                save()  # local write-ahead log; the whole allowance is already durable in Notion
                timeout = min(limits["request_timeout_seconds"], wall_limit-now)
                destination = folder / (str(job["number"]) + ".body")
                if mode == "fixture":
                    responses = data["responses"][job["url"]]
                    position = sum(r["url"] == job["url"] for r in log)-1
                    result = dict(responses[min(position, len(responses)-1)])
                    duration = min(result.get("duration", 1), timeout)
                    if result.get("duration", 1) > timeout:
                        result = dict(status=None, error="hard request timeout", duration=timeout)
                    else:
                        destination.write_bytes(result.pop("body", "fixture evidence").encode())
                        result.update(body_path=str(destination),body_sha256=hashlib.sha256(destination.read_bytes()).hexdigest())
                    active[job["number"]] = dict(job, end=now+duration, response=result)
                else:
                    future = pool.submit(live_attempt, job["url"], destination, timeout)
                    active[job["number"]] = dict(job, future=future)
                busy.add(host)
            if mode == "fixture" and active:
                virtual = min(j["end"] for j in active.values())
            done = [number for number, job in active.items() if (job["end"] <= virtual if mode == "fixture" else job["future"].done())]
            for number in done:
                job = active.pop(number)
                response = job["response"] if mode == "fixture" else job["future"].result()
                log[number-1].update(state="finished", finished=cadence.iso(started+timedelta(seconds=now_seconds())),
                                     status=response.get("status"), error=response.get("error"))
                status = response.get("status")
                if any(s in str(response.get("error") or "") for s in ("10013", "Permission denied", "access permissions")):
                    environment_failed = True
                if status in {403,429}:
                    blocked.add(job["host"])
                if status in {301,302,303,307,308} and response.get("location") and job["redirects"] < 5:
                    try:
                        url = cadence.safe_url(urljoin(job["url"],response["location"]))
                        pending.append(dict(root=job["root"],url=url,keys=job["keys"],retry=0,
                                            redirects=job["redirects"]+1,ready=now_seconds()))
                    except ValueError:
                        results[job["root"]] = dict(keys=job["keys"],status=None,attempted=True,error="Unsafe redirect rejected")
                elif not environment_failed and (status is None or status >= 500) and job["retry"] < limits["max_retries"]:
                    pending.append(dict(root=job["root"],url=job["url"],keys=job["keys"],retry=job["retry"]+1,
                                        redirects=job["redirects"],ready=now_seconds()+2**(job["retry"]+1)))
                else:
                    results[job["root"]] = dict(response,keys=job["keys"],attempted=True,final_url=job["url"],
                                                run_key=data["run_key"],reused=False,fetched_at=log[number-1]["finished"])
                save()
            if not active and pending and mode == "fixture":
                virtual = max(virtual, min(max(j["ready"], host_last.get(urlsplit(j["url"]).hostname.lower(), -2)+limits["min_host_interval_seconds"]) for j in pending))
            elif mode == "live" and (pending or active):
                time.sleep(.05)
    save()
    result = dict(no_send=True, mode=mode, verified_calls=0, run_key=data["run_key"], batch_id=reservation["batch_id"],
                  actual_requests=len(log), reused_responses=reused, results=results, requests=log,
                  blocked_hosts=sorted(blocked), elapsed_seconds=now_seconds(),
                  request_log_sha256=hashlib.sha256(json.dumps(log,sort_keys=True).encode()).hexdigest())
    atomic_json(folder/"result.json",result)
    return result


def main():
    if len(sys.argv)>1 and sys.argv[1]=="--request-one":
        url, destination, timeout, output = sys.argv[2:]
        atomic_json(Path(output),request_one(url,Path(destination),float(timeout)))
        return
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot",type=Path)
    parser.add_argument("--output-dir",type=Path,required=True)
    parser.add_argument("--live",action="store_true")
    args=parser.parse_args()
    result=run(json.loads(args.snapshot.read_text(encoding="utf-8-sig")),args.output_dir,args.live)
    print(json.dumps({k:result[k] for k in ("no_send","mode","actual_requests","reused_responses","elapsed_seconds","verified_calls")}))


if __name__=="__main__":main()
