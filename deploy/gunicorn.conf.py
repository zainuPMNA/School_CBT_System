"""
Gunicorn settings for a 4-core lab server (60-100 concurrent students).

Why gthread: exam traffic is many tiny requests (auto-save, timer sync)
that spend most of their time waiting on PostgreSQL. 5 processes x 4
threads = 20 requests in flight, which saturates 4 cores without the memory
cost of 9+ sync workers. Each process keeps a small psycopg pool
(DB_POOL_MAX in .env, default 6 >= threads), so PostgreSQL sees at most ~30
connections from the app.

Override any value with an environment variable in the systemd unit,
e.g. Environment=GUNICORN_WORKERS=7 on an 8-core machine.
"""

import multiprocessing
import os

_cores = multiprocessing.cpu_count()

bind = os.environ.get("GUNICORN_BIND", "unix:/run/cbt/gunicorn.sock")
workers = int(os.environ.get("GUNICORN_WORKERS", min(_cores + 1, 9)))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", 4))

# Requests are short; anything slower than 60 s is stuck (bulk upload included).
timeout = 60
graceful_timeout = 30
keepalive = 5

# Recycle workers periodically to cap memory growth (jitter avoids all at once).
max_requests = 2000
max_requests_jitter = 200

# The socket must be reachable by nginx (group www-data), nobody else.
umask = 0o007

# Logs go to stdout/stderr -> journald (journalctl -u cbt).
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%({x-real-ip}i)s "%(r)s" %(s)s %(b)s %(M)sms'

proc_name = "cbt"
