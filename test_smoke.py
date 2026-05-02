#!/usr/bin/env python3

import html
import json
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

CONFIG_FILE = Path(__file__).parent / "config.json"
DEBUG_DIR = Path("debug")

config: dict = {}


def load_config() -> dict:
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def get_config(path: str, default=None):
    keys = path.split(".")
    val = config
    for k in keys:
        if isinstance(val, dict):
            val = val.get(k)
        else:
            return default
        if val is None:
            return default
    return val


session = requests.Session()


def init_session():
    max_retries = get_config("retries.http_max_retries", 2)
    retry_strategy = Retry(
        total=max_retries,
        backoff_factor=0.5,
        status_forcelist=[500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive",
        }
    )


def _safe_text(value: object) -> str:
    text = str(value)
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text).strip("_")[:80] or "unknown"


def _debug_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _dump_failure(
    stage: str,
    cls: dict | None = None,
    reason: str | None = None,
    response: requests.Response | None = None,
    payload: dict | None = None,
    extra: dict | None = None,
) -> None:
    if not get_config("debug.enabled", True):
        return

    debug_dir = Path(get_config("debug.debug_dir", "debug"))
    debug_dir.mkdir(parents=True, exist_ok=True)

    subject = _safe_text((cls or {}).get("subject_name", "unknown"))
    date = _safe_text((cls or {}).get("attendance_date", "no-date"))
    stamp = _debug_stamp()
    base = f"{stamp}_{stage}_{subject}_{date}"

    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "stage": stage,
        "reason": reason,
        "class": cls or {},
        "request_payload": payload or {},
        "response": {
            "status_code": getattr(response, "status_code", None),
            "url": getattr(response, "url", None),
            "headers": dict(getattr(response, "headers", {}))
            if response is not None
            else {},
            "history": [h.url for h in getattr(response, "history", [])]
            if response is not None
            else [],
        },
        "extra": extra or {},
    }

    json_path = debug_dir / f"{base}.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=True, indent=2)

    if response is not None:
        html_path = debug_dir / f"{base}.html"
        with html_path.open("w", encoding="utf-8") as f:
            f.write(response.text)

    latest_path = debug_dir / "latest_failure.txt"
    with latest_path.open("w", encoding="utf-8") as f:
        f.write(str(json_path))


def delay() -> None:
    min_delay = get_config("timing.min_delay_seconds", 4)
    max_delay = get_config("timing.max_delay_seconds", 8)
    t = random.uniform(min_delay, max_delay)
    time.sleep(t)


def get_csrf(soup: BeautifulSoup) -> str | None:
    inp = soup.find("input", {"name": "_token"})
    if inp and inp.get("value"):
        return inp["value"]
    meta = soup.find("meta", {"name": "csrf-token"})
    if meta and meta.get("content"):
        return meta["content"]
    meta_csrf = soup.find("meta", {"name": "csrf-token", "content": True})
    if meta_csrf:
        return meta_csrf["content"]
    return None


def login() -> bool:
    base_url = get_config("urls.base_url", "https://adamasknowledgecity.ac.in")
    username = get_config("credentials.username", "")
    password = get_config("credentials.password", "")

    if not username or not password:
        print("[x] Credentials not configured in config.json")
        return False

    print("[*] Fetching login page...")
    login_paths = ["/student/login", "/login"]
    login_url = None
    token = None

    for path in login_paths:
        candidate = f"{base_url}{path}"
        r = session.get(candidate, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        token = get_csrf(soup)
        if token:
            login_url = candidate
            print(f"[*] Found login form at: {candidate}")
            break

    if not login_url or not token:
        print("[x] No CSRF token found on login pages")
        return False

    xsrf = session.cookies.get("XSRF-TOKEN", "")
    headers = {
        "Referer": login_url,
        "X-CSRF-TOKEN": token,
        "X-XSRF-TOKEN": xsrf,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    payload = {
        "_token": token,
        "registration_no": username,
        "password": password,
        "login": "login",
    }

    print("[*] Logging in...")
    resp = session.post(
        login_url, data=payload, headers=headers, allow_redirects=True, timeout=20
    )
    if "dashboard" in resp.url:
        print("[+] Login successful.")
        return True

    print("[x] Login failed - still on login page. Check credentials.")
    return False


def scrape_classes() -> list[dict]:
    base_url = get_config("urls.base_url", "https://adamasknowledgecity.ac.in")
    include_subjects = get_config("subjects.include_only", [])
    exclude_subjects = get_config("subjects.exclude", [])

    print("\n[*] Fetching feedback dashboard...")
    r = session.get(f"{base_url}/student/dashboard", timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")

    classes: list[dict] = []
    divs = soup.find_all("div", class_="subject-item-modern")
    for div in divs:
        onclick = div.get("onclick", "")
        m = re.search(r"showSubjectFeedbackChart\(.*?,\s*(\[.*\])\s*\)$", onclick)
        if not m:
            continue
        raw_json = html.unescape(m.group(1))
        try:
            items = json.loads(raw_json)
        except json.JSONDecodeError:
            continue
        for item in items:
            if all(
                k in item
                for k in (
                    "attendance_header_id",
                    "attendance_date",
                    "period_id",
                    "subject_id",
                )
            ):
                subject_name = item.get("subject_name", "")
                subject_id = item.get("subject_id", "")

                if include_subjects and subject_id not in include_subjects:
                    continue
                if exclude_subjects and subject_id in exclude_subjects:
                    continue

                classes.append(item)

    print(f"[*] Found {len(classes)} pending class feedback(s):")
    counts: dict[str, int] = {}
    for cls in classes:
        name = cls.get("subject_name", "Unknown")
        counts[name] = counts.get(name, 0) + 1
    for name, cnt in sorted(counts.items()):
        print(f"  - {name}: {cnt} pending")

    return classes


def check_class(
    cls: dict,
) -> tuple[str, str | None, str | None, list[str] | None, str | None]:
    base_url = get_config("urls.base_url", "https://adamasknowledgecity.ac.in")
    survey_id = get_config("ids.survey_id", "4")
    student_id = get_config("ids.student_id", "186")

    url = f"{base_url}/student/academicfeedback/give-feedback/{survey_id}/{student_id}"
    params = {
        "attendDate": cls["attendance_date"],
        "subjectId": cls["subject_id"],
        "subjectCode": cls.get("subject_code", ""),
        "subjectName": cls.get("subject_name", ""),
        "periodId": cls["period_id"],
        "attendanceHeaderId": cls["attendance_header_id"],
    }

    try:
        r = session.get(url, params=params, timeout=20)
    except Exception as exc:
        print(f"  [!] GET error: {exc}")
        _dump_failure(
            "check_get_exception",
            cls=cls,
            reason=str(exc),
            extra={"url": url, "params": params},
        )
        return "error", None, None, None, None

    if "student/login" in r.url or r.status_code in (401, 403):
        _dump_failure(
            "check_relogin",
            cls=cls,
            reason="redirected-to-login",
            response=r,
            extra={"url": url, "params": params},
        )
        return "relogin", None, None, None, None

    soup = BeautifulSoup(r.text, "html.parser")
    form = soup.find("form", action=lambda x: x and "save-multiple-feedback" in x)
    if not form:
        form = soup.find("form")

    if not form:
        text = r.text.lower()
        if "already" in text and "submitted" in text:
            return "submitted", None, None, None, r.url

        _dump_failure(
            "check_no_form",
            cls=cls,
            reason="no-form-found",
            response=r,
            extra={"url": url, "params": params},
        )
        return "submitted", None, None, None, r.url

    token = get_csrf(soup)
    rel = form.find("input", {"name": lambda x: x and "student_relation_id" in x})
    if not rel or not rel.get("value") or not token:
        _dump_failure(
            "check_missing_fields",
            cls=cls,
            reason="missing-token-or-relation-id",
            response=r,
            extra={"url": url, "params": params},
        )
        return "error", None, None, None, r.url

    qids = []
    for inp in form.find_all("input", {"name": re.compile(r"^ques_id\[0\]\[\d+\]$")}):
        if inp.get("value"):
            qids.append(inp["value"])
    if not qids:
        qids = get_config("feedback.question_ids", ["37", "38", "39", "40", "41"])

    return "pending", rel["value"], token, qids, r.url


def submit(cls: dict) -> str:
    base_url = get_config("urls.base_url", "https://adamasknowledgecity.ac.in")
    survey_id = get_config("ids.survey_id", "4")

    status, rid, token, qids, form_url = check_class(cls)
    if status in ("relogin", "noform", "error", "submitted"):
        return status

    assert rid is not None
    assert token is not None
    assert qids is not None

    ratings = get_config("feedback.ratings", [5, 5, 5, 5, 5])
    comment = get_config("feedback.comment", "")

    payload = {
        "_token": token,
        "save": "Save",
        "comment[]": comment,
        "t_rel_fs_student_feedback_student_relation_id[]": rid,
        "t_rel_regular_class_period_id[]": cls["period_id"],
    }

    for i, qid in enumerate(qids):
        rating_idx = i if i < len(ratings) else len(ratings) - 1
        payload[f"ques_id[0][{i}]"] = qid
        payload[f"feed_val[0][{i + 1}]"] = ratings[rating_idx]

    submit_url = (
        f"{base_url}/student/academicfeedback/save-multiple-feedback/{survey_id}"
    )

    try:
        r = session.post(
            submit_url,
            data=payload,
            headers={"X-CSRF-TOKEN": token, "Referer": form_url or submit_url},
            timeout=20,
            allow_redirects=True,
        )
    except Exception as exc:
        print(f"  [!] POST error: {exc}")
        _dump_failure(
            "submit_post_exception",
            cls=cls,
            reason=str(exc),
            payload=payload,
            extra={"submit_url": submit_url, "referer": form_url or submit_url},
        )
        return "error"

    final_url = r.url
    response_text = r.text.lower()

    if "student/login" in final_url or r.status_code in (401, 403):
        _dump_failure(
            "submit_relogin",
            cls=cls,
            reason="redirected-to-login",
            response=r,
            payload=payload,
            extra={"submit_url": submit_url, "final_url": final_url},
        )
        return "relogin"

    if "dashboard" in final_url:
        return "ok"

    if "csrf" in response_text or "token mismatch" in response_text:
        _dump_failure(
            "submit_invalid_response",
            cls=cls,
            reason="csrf-or-invalid-detected",
            response=r,
            payload=payload,
            extra={"submit_url": submit_url, "final_url": final_url},
        )
        return "error"

    return "ok"


def get_student_details_id(student_id: str) -> str | None:
    base_url = get_config("urls.base_url", "https://adamasknowledgecity.ac.in")

    url = f"{base_url}/ajax/get-studentDetailsId?id={student_id}"
    print(f"    [*] Calling: {url}")
    try:
        resp = session.get(url, timeout=15)
        print(f"    [*] Response status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            print(f"    [*] Response data: {data}")
            sid = data.get("t_rel_students_details_id")
            if sid:
                return str(sid)
            sid = data.get("id")
            if sid:
                return str(sid)
            for key, val in data.items():
                if isinstance(val, (int, str)) and val and key != "id":
                    return str(val)
    except Exception as exc:
        print(f"  [!] Get student details error: {exc}")

    r = session.get(f"{base_url}/student/dashboard", timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")

    for script in soup.find_all("script"):
        script_text = script.string or ""
        match = re.search(
            r"t_rel_students_details_id['\"]?\s*[:=]\s*['\"]?(\d+)", script_text
        )
        if match:
            return match.group(1)

    return None


def fetch_attendance_table() -> list[dict]:
    base_url = get_config("urls.base_url", "https://adamasknowledgecity.ac.in")
    student_id = get_config("ids.student_id", "186")

    r = session.get(f"{base_url}/student/dashboard", timeout=20)
    csrf_token = get_csrf(BeautifulSoup(r.text, "html.parser"))

    ajax_url = f"{base_url}/student/dashboard/getAjaxList"

    params = {
        "student_id": student_id,
    }

    try:
        print(f"    [*] Calling: {ajax_url} with params {params}")
        r = session.get(ajax_url, params=params, timeout=30)
        print(f"    [*] Response status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            print(f"    [*] Response keys: {list(data.keys())}")

            # Debug: dump raw response
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            with (DEBUG_DIR / "ajax_attendance_response.json").open("w") as f:
                json.dump(data, f, indent=2)

            if "data" in data:
                items = data["data"]
                print(f"    [*] Number of records: {len(items)}")
                if items:
                    print(f"    [*] First item type: {type(items[0])}")
                    print(f"    [*] First item: {items[0]}")

                records = []
                for item in items:
                    if not isinstance(item, dict):
                        continue

                    attendance_date = (
                        item.get("attendance_date") or item.get("date") or ""
                    )
                    class_status = (
                        item.get("class_status") or item.get("attendance_status") or ""
                    )
                    biometric_status = item.get("biometric_status") or ""
                    final_status = item.get("final_status") or ""
                    subject_name = item.get("subject_name") or item.get("subject") or ""
                    header_id = item.get("attendance_header_id") or item.get("id") or ""

                    if subject_name and attendance_date and header_id:
                        records.append(
                            {
                                "subject_name": subject_name,
                                "attendance_date": attendance_date,
                                "class_status": class_status,
                                "biometric_status": biometric_status,
                                "final_status": final_status,
                                "attendance_header_id": str(header_id),
                            }
                        )
                print(f"    [*] Parsed {len(records)} valid records")
                return records
            else:
                print(f"    [!] No 'data' key in response: {data}")
    except Exception as exc:
        import traceback

        traceback.print_exc()
        print(f"  [!] AJAX fetch error: {exc}")

    return []

    return records


def sync_biometric_record(
    date: str, header_id: str, student_details_id: str, token: str
) -> bool:
    base_url = get_config("urls.base_url", "https://adamasknowledgecity.ac.in")
    url = f"{base_url}/student/attendance/update-biometric"

    payload = {
        "_token": token,
        "date": date,
        "t_rel_students_details_id": student_details_id,
        "t_rel_regular_class_attendance_header_id": header_id,
        "channel": "student_portal",
    }

    try:
        r = session.post(
            url,
            data=payload,
            headers={
                "X-CSRF-TOKEN": token,
                "Referer": f"{base_url}/student/dashboard",
            },
            timeout=20,
        )
        return r.status_code == 200
    except Exception as exc:
        print(f"  [!] Biometric sync error: {exc}")
        return False


def refresh_attendance_status(date: str, header_id: str, token: str) -> dict | None:
    base_url = get_config("urls.base_url", "https://adamasknowledgecity.ac.in")
    url = f"{base_url}/ajax/refreshAttendanceStatus"

    payload = {
        "_token": token,
        "date": date,
        "attendanceheaderid": header_id,
    }

    try:
        r = session.post(
            url,
            data=payload,
            headers={"X-CSRF-TOKEN": token},
            timeout=15,
        )
        if r.status_code == 200:
            return r.json()
    except Exception as exc:
        print(f"  [!] Refresh status error: {exc}")
    return None


def run_biometric_sync() -> None:
    student_id = get_config("ids.student_id", "186")

    # Ensure logged in first
    if not login():
        return

    print("\n[*] Fetching student details ID...")
    student_details_id = get_student_details_id(student_id)
    print(f"[*] Raw response from get-studentDetailsId API")

    if not student_details_id:
        print("[!] API returned no ID, will try scraping dashboard")
        # Fallback: use student_id directly if needed
        student_details_id = student_id
        print(f"[*] Using fallback ID: {student_details_id}")

    print(f"[*] Student details ID: {student_details_id}")

    print("\n[*] Fetching attendance table...")
    records = fetch_attendance_table()
    if not records:
        print("[!] No attendance records found")
        return

    print(f"[*] Found {len(records)} attendance record(s)")

    sync_mode = get_config("biometric.sync_mode", "mismatch_only")

    to_sync = []
    for rec in records:
        class_status = rec.get("class_status", "").lower()
        biometric_status = rec.get("biometric_status", "").lower()

        if sync_mode == "all":
            to_sync.append(rec)
        elif sync_mode == "mismatch_only":
            if "present" in class_status and "absent" in biometric_status:
                to_sync.append(rec)

    if not to_sync:
        print("\n[*] No records need biometric sync")
        return

    print(f"\n[*] Records to sync: {len(to_sync)}")
    for rec in to_sync:
        print(
            f"  - {rec['subject_name']} [{rec['attendance_date']}] - Class: {rec['class_status']}, Bio: {rec['biometric_status']}"
        )

    confirm = input("\nProceed with sync? (y/N): ").strip().lower()
    if confirm != "y":
        print("[!] Cancelled")
        return

    csrf_token = None
    r = session.get(get_config("urls.base_url") + "/student/dashboard", timeout=20)
    soup = BeautifulSoup(r.text, "html.parser")
    csrf_token = get_csrf(soup)

    synced = 0
    failed = 0

    for idx, rec in enumerate(to_sync, start=1):
        date = rec["attendance_date"]
        header_id = rec["attendance_header_id"]
        subject = rec["subject_name"]

        print(f"\n[{idx}/{len(to_sync)}] Syncing: {subject} [{date}]")

        if not csrf_token:
            r = session.get(
                get_config("urls.base_url") + "/student/dashboard", timeout=20
            )
            soup = BeautifulSoup(r.text, "html.parser")
            csrf_token = get_csrf(soup)

        if sync_biometric_record(date, header_id, student_details_id, csrf_token):
            print("  [+] Biometric sync initiated")

            if get_config("biometric.auto_refresh_after_sync", True):
                result = refresh_attendance_status(date, header_id, csrf_token)
                if result and result.get("success"):
                    final = result.get("final_status_text", "Unknown")
                    print(f"  [+] Status refreshed: {final}")
                    synced += 1
                else:
                    print("  [!] Refresh failed")
                    failed += 1
            else:
                synced += 1
        else:
            print("  [x] Sync failed")
            failed += 1

        if idx < len(to_sync):
            delay()

    print("\n" + "=" * 60)
    print(f"   BIOMETRIC SYNC RESULTS")
    print("=" * 60)
    print(f"  Synced    : {synced}")
    print(f"  Failed    : {failed}")
    print("=" * 60)


def main() -> None:
    global config
    config = load_config()

    if not config:
        print("[x] config.json not found or empty. Please create it.")
        return

    print("=" * 60)
    print("   Adamas University - Automation Tools")
    print("   crafted with love by Ankan")
    print("=" * 60)
    print()
    print("  [1] Submit Feedback")
    print("  [2] Sync Biometric Attendance")
    print("  [3] Exit")
    print()

    choice = input("Select option (1-3): ").strip()

    if choice == "1":
        run_feedback_submission()
    elif choice == "2":
        run_biometric_sync()
    elif choice == "3":
        print("[*] Exiting")
    else:
        print("[x] Invalid option")


def run_feedback_submission() -> None:
    init_session()

    base_url = get_config("urls.base_url", "https://adamasknowledgecity.ac.in")
    survey_id = get_config("ids.survey_id", "4")
    student_id = get_config("ids.student_id", "186")

    print("\n[*] Running Feedback Submission...")
    print("=" * 60)

    if get_config("debug.enabled", True):
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        run_info = {
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "cwd": os.getcwd(),
            "base_url": base_url,
            "student_id": student_id,
            "survey_id": survey_id,
            "config_loaded": bool(config),
        }
        with (DEBUG_DIR / "run_info.json").open("w", encoding="utf-8") as f:
            json.dump(run_info, f, ensure_ascii=True, indent=2)

    if not login():
        return

    classes = scrape_classes()
    if not classes:
        print("\n[*] No pending feedback found.")
        return

    ratings = get_config("feedback.ratings", [5, 5, 5, 5, 5])
    print(f"\n[*] Will submit with ratings: {ratings}")
    print(f"[*] Total to submit: {len(classes)}")
    confirm = input("\nProceed? (y/N): ").strip().lower()
    if confirm != "y":
        print("[!] Cancelled by user")
        return

    ok = 0
    fail = 0
    skipped = 0
    start_time = time.time()

    for idx, cls in enumerate(classes, start=1):
        subject_name = cls.get("subject_name", "Unknown")
        date = cls.get("attendance_date", "-")
        print(f"\n[{idx}/{len(classes)}] {subject_name} [{date}]")

        if idx > 1 and idx % 20 == 0:
            print("  [~] Refreshing session...")
            if not login():
                print("  [x] Session refresh failed")
                fail += 1
                continue

        result = "error"
        max_retries = get_config("retries.submit_max_retries", 2)
        for attempt in range(1, max_retries + 1):
            result = submit(cls)
            if result == "relogin":
                if not login():
                    break
                continue
            if result == "ok":
                break
            if result == "submitted":
                break
            if attempt < max_retries:
                retry_delay = get_config("timing.retry_delay_seconds", 2)
                print(f"  [~] Retry {attempt}/{max_retries}, waiting {retry_delay}s...")
                time.sleep(retry_delay)

        if result == "ok":
            print("  [+] SUBMITTED")
            ok += 1
        elif result == "submitted":
            print("  [-] ALREADY SUBMITTED")
            skipped += 1
        else:
            print("  [x] FAILED")
            fail += 1

        if idx < len(classes):
            delay()

    elapsed = time.time() - start_time

    print("\n" + "=" * 60)
    print(f"   RESULTS")
    print("=" * 60)
    print(f"  Submitted   : {ok}")
    print(f"  Skipped     : {skipped}")
    print(f"  Failed      : {fail}")
    print(f"  Total Time  : {elapsed:.1f}s")
    print("=" * 60)


if __name__ == "__main__":
    main()
