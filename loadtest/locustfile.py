"""
Load test: N students log in, open the exam (fetching their shuffled
questions), auto-save answers one click at a time with timer syncs in
between, wait for each other, then ALL submit at the same moment — the
worst case at the end of a real exam.

1) On the server (creates LOAD001..LOAD075 and a fresh exam, prints its id):
       cd /opt/cbt/app && sudo -u cbt /opt/cbt/venv/bin/python manage.py prepare_loadtest --students 75 --questions 30

2) From a lab PC or the server (needs: pip install -r requirements-loadtest.txt):
       CBT_EXAM_ID=<id> locust -f loadtest/locustfile.py --host http://192.168.1.10 \
           --headless --users 75 --spawn-rate 15 --run-time 6m --csv loadtest/result
   (Windows PowerShell:  $env:CBT_EXAM_ID="<id>"; locust -f loadtest\\locustfile.py ...)

   Or drop --headless and open http://localhost:8089 for live charts.

3) Clean up:  manage.py prepare_loadtest --cleanup

Environment variables
    CBT_EXAM_ID        exam id printed by prepare_loadtest (required)
    CBT_USERS          number of simulated students (default 75; must match --users)
    CBT_ANSWERS        answers each student saves (default 30)
    CBT_PREFIX         username prefix (default LOAD) -> LOAD001, LOAD002, ...
    CBT_PASSWORD       password (default loadtest123)
    CBT_THINK_MIN/MAX  seconds between clicks (default 1-3)

What "good" looks like on a 4-core server: 0 failures, auto-save p95 < 300 ms,
submit p95 < 2 s even with everyone submitting together.
"""

import itertools
import json
import os
import random
import re

import gevent
from gevent.event import Event
from locust import HttpUser, events, task
from locust.exception import StopUser

EXAM_ID = int(os.environ.get("CBT_EXAM_ID", "0"))
TOTAL_USERS = int(os.environ.get("CBT_USERS", "75"))
ANSWERS = int(os.environ.get("CBT_ANSWERS", "30"))
PREFIX = os.environ.get("CBT_PREFIX", "LOAD")
PASSWORD = os.environ.get("CBT_PASSWORD", "loadtest123")
THINK_MIN = float(os.environ.get("CBT_THINK_MIN", "1"))
THINK_MAX = float(os.environ.get("CBT_THINK_MAX", "3"))
BARRIER_TIMEOUT = float(os.environ.get("CBT_BARRIER_TIMEOUT", "180"))

CONFIG_RE = re.compile(r'<script id="exam-config" type="application/json">(.*?)</script>', re.S)

_numbers = itertools.count(1)
_ready_count = 0
_all_ready = Event()


@events.test_start.add_listener
def _check_config(environment, **kwargs):
    if not EXAM_ID:
        raise SystemExit("Set CBT_EXAM_ID to the id printed by `manage.py prepare_loadtest`.")


def _arrive_and_wait():
    """Simple barrier: release everyone when the last student finishes answering."""
    global _ready_count
    _ready_count += 1
    if _ready_count >= TOTAL_USERS:
        _all_ready.set()
    _all_ready.wait(timeout=BARRIER_TIMEOUT)


class Student(HttpUser):
    # Pacing is done inside the task (think time between answers).
    wait_time = lambda self: 0  # noqa: E731

    def on_start(self):
        self.number = next(_numbers)
        self.username = f"{PREFIX}{self.number:03d}"

    def _csrf(self):
        return self.client.cookies.get("cbt_csrftoken", "")

    @task
    def take_exam(self):
        # --- Login -----------------------------------------------------------
        self.client.get("/login/", name="GET /login/")
        with self.client.post(
            "/login/",
            {"username": self.username, "password": PASSWORD, "csrfmiddlewaretoken": self._csrf()},
            name="POST /login/", allow_redirects=False, catch_response=True,
        ) as r:
            if r.status_code != 302:
                r.failure(f"login failed for {self.username} ({r.status_code})")
                raise StopUser()

        # --- Start / resume the exam (CSRF token rotates on login) -------------
        with self.client.post(
            f"/exam/{EXAM_ID}/start/", {"csrfmiddlewaretoken": self._csrf()},
            name="POST /exam/[id]/start/", allow_redirects=False, catch_response=True,
        ) as r:
            if r.status_code != 302 or "/take/" not in r.headers.get("Location", ""):
                r.failure(f"start refused ({r.status_code} -> {r.headers.get('Location')})")
                raise StopUser()

        # --- Exam page: shuffled questions + state ---------------------------------
        with self.client.get(f"/exam/{EXAM_ID}/take/", name="GET /exam/[id]/take/", catch_response=True) as r:
            match = CONFIG_RE.search(r.text or "")
            if r.status_code != 200 or not match:
                r.failure("exam page did not contain exam-config")
                raise StopUser()
            config = json.loads(match.group(1))

        headers = {
            "X-CSRFToken": config["csrf"],
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }
        urls = config["urls"]
        questions = config["questions"]

        # --- Answer questions one click at a time ---------------------------------
        for index, question in enumerate(questions[:ANSWERS]):
            gevent.sleep(random.uniform(THINK_MIN, THINK_MAX))
            status = "MARKED_REVIEW" if random.random() < 0.1 else "ANSWERED"
            payload = {"question_id": question["id"], "selected": random.randint(0, 3),
                       "status": status, "current_index": index}
            with self.client.post(urls["save"], json=payload, headers=headers,
                                  name="POST /exam/[id]/api/save/", catch_response=True) as r:
                if r.status_code != 200:
                    r.failure(f"save {r.status_code}: {r.text[:120]}")
            if index % 5 == 4:  # periodic server-time sync, as the browser does
                self.client.get(f"{urls['sync']}?pos={index}", headers=headers, name="GET /exam/[id]/api/sync/")

        # --- Everyone submits together --------------------------------------------
        _arrive_and_wait()
        with self.client.post(urls["submit"], json={"auto": False, "answers": []}, headers=headers,
                              name="POST /exam/[id]/submit/", catch_response=True) as r:
            if r.status_code != 200:
                r.failure(f"submit {r.status_code}: {r.text[:120]}")
        self.client.get(urls["submitted"], name="GET /exam/[id]/submitted/")
        raise StopUser()
