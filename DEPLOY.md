# Deploying the CBT server to Ubuntu Server 24.04 LTS

You develop on Windows; the exams run on one Ubuntu machine in the school
that the lab PCs reach over the LAN. The trick for a school network without
internet: **everything the server needs is downloaded on Windows first** and
copied over in one file.

```
 Windows PC (has internet)                 Ubuntu server (LAN only)
 ─────────────────────────                 ───────────────────────────────────
 package_for_server.ps1  ──USB / scp──▶   setup_server.sh
   code + Linux wheels                      PostgreSQL + Gunicorn + Nginx + firewall
   = dist/cbt_release.tar.gz                → http://192.168.1.10/   ◀── 60+ lab PCs
```

---

## 0. The server machine

| Item | Recommended |
|---|---|
| CPU / RAM | 4 cores, 8 GB RAM (4 GB works for ~60 students) |
| Disk | SSD, 50 GB+ |
| OS | Ubuntu **Server** 24.04 LTS (install with "OpenSSH server" ticked) |
| Network | Wired, on the same LAN/switch as both labs, **fixed IP** |

### Give the server a fixed IP

Students' login slips print the server address, so it must never change.
Either reserve the IP for the server's MAC address in your router's DHCP
settings (easiest), or set it on the server:

```bash
ip -br addr                 # find the interface name, e.g. enp3s0
sudo nano /etc/netplan/50-cloud-init.yaml
```

```yaml
network:
  version: 2
  ethernets:
    enp3s0:                     # <- your interface
      dhcp4: false
      addresses: [192.168.1.10/24]
      routes: [{ to: default, via: 192.168.1.1 }]
      nameservers: { addresses: [192.168.1.1] }
```

```bash
sudo netplan apply
```

---

## 0b. Dual boot: Ubuntu alongside Windows on the same PC

Use this when the server is a PC or laptop that also runs Windows. A menu at
every start picks the system. **Only one runs at a time**: on exam days the PC
must be started in Ubuntu. When installing Ubuntu, **never choose "Erase disk"
or "use the entire disk"** — that deletes Windows.

In Windows first:

1. Back up important files.
2. If *Settings → Privacy & security → Device encryption / BitLocker* is on,
   save the recovery key from aka.ms/myrecoverykey.
3. Win+X → Disk Management → right-click **C:** → Shrink Volume → **80000** MB.
4. Control Panel → Power Options → Choose what the power buttons do →
   untick **Turn on fast startup**.
5. Build the package (step 1 below) and copy `dist\cbt_release.tar.gz` to a USB stick.

Install:

6. Write **Ubuntu Desktop 24.04 LTS** (it can install next to Windows
   automatically; the Server installer cannot) to another USB stick with Rufus.
7. Boot from it (Lenovo: **F12** or the **Novo** pinhole button).
8. Choose **Install Ubuntu alongside Windows Boot Manager**.

After installing, in an Ubuntu terminal:

```bash
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target   # never sleep during an exam
sudo timedatectl set-local-rtc 1                                                        # optional: keep Windows' clock right
```

Then continue below. Differences: set the fixed IP in the router or in
Ubuntu's *Settings → Network → IPv4 → Manual* (Desktop does not use the
netplan file), and copy the package from the USB stick (step 2). Laptops: keep
it plugged in and use a network cable for the exam network.

## 1. On Windows: build the release (needs internet once)

In the project folder, in PowerShell:

```powershell
bash scripts/bundle_static.sh --css-only
```

```powershell
powershell -ExecutionPolicy Bypass -File deploy\package_for_server.ps1
```

This downloads every Python package **as Linux wheels for Python 3.12** into
`wheelhouse\` and creates `dist\cbt_release.tar.gz` (≈ 60–90 MB). Your local
`.env`, development database, logs and test uploads are left out.

## 2. Copy it to the server

USB stick, or over the network (OpenSSH is built into Windows 10/11):

```powershell
scp dist\cbt_release.tar.gz youruser@192.168.1.10:~/
```

## 3. On the server: install

```bash
tar -xzf cbt_release.tar.gz
cd cbt_release
sudo bash deploy/setup_server.sh --school "Springfield Public School"
```

The script (10 steps, about 3–5 minutes) installs and configures:

1. OS packages: `python3-venv postgresql nginx ufw` *(see "Offline OS packages" if the server has no internet)*
2. a locked-down `cbt` system user
3. the code into `/opt/cbt/app` (read-only for the service)
4. a virtualenv from the bundled wheels — **no internet needed**
5. `/opt/cbt/app/.env` with a random secret key and DB password, `DEBUG=False`, the server IP in `ALLOWED_HOSTS` and on the login slips
6. PostgreSQL tuned for your RAM (`deploy/postgresql/cbt-tuning.conf`), database + user
7. `migrate`, `collectstatic`, Django deployment checks
8. systemd: `cbt.service` (Gunicorn, 5 workers × 4 threads), auto-submit every minute, nightly backup at 19:00
9. Nginx on port 80 serving static/media directly, 25 MB uploads
10. `ufw` firewall: only HTTP + SSH from your LAN subnet

At the end it asks you to **create the administrator account**. Then open
`http://192.168.1.10/` from any lab PC.

### Offline OS packages

Step 1 needs the Ubuntu packages once. Simplest: plug the server into the
internet just for the install, then unplug it. If that's impossible, on any
other Ubuntu **24.04** machine with internet:

```bash
mkdir debs && cd debs
apt-get download $(apt-cache depends --recurse --no-recommends --no-suggests \
  --no-conflicts --no-breaks --no-replaces --no-enhances \
  python3-venv python3-pip postgresql postgresql-contrib nginx rsync | grep '^\w' | sort -u)
```

Copy `debs/` to the server, then:

```bash
sudo dpkg -i debs/*.deb
sudo bash deploy/setup_server.sh --skip-apt --school "Springfield Public School"
```

---

## 3b. Using Wi-Fi as well as the wired labs

The app works over Wi-Fi with no code change: any device that can reach the
server's IP can sit the exam. Get these four things right.

1. **Keep the server wired.** Only the students' devices use Wi-Fi.
2. **Use access-point (bridge) mode, not a second router.** Plug the access
   point into the same LAN switch and set it to *Access Point / Bridge mode*.
   Devices then get addresses from the school LAN (e.g. 192.168.1.x) and the
   setup script's firewall already allows them.
3. **Turn OFF "client isolation" / "AP isolation" / "guest network"** on the
   exam SSID — it blocks devices from reaching the server.
4. **Protect the SSID with WPA2/WPA3** and a password only invigilators hand
   out. Plain HTTP on an open Wi-Fi can be sniffed.

If the Wi-Fi has its **own subnet** (e.g. 192.168.50.0/24, routed, no NAT),
allow both networks:

```bash
sudo bash deploy/setup_server.sh --skip-apt --lan-cidr 192.168.1.0/24,192.168.50.0/24
```

If the Wi-Fi router does **NAT** (devices behind it all appear to the server
as ONE IP — check the Live monitor: many students showing the same IP), add
the router's LAN-side address range and turn off same-IP re-login, otherwise
one student's login could replace another's session:

```bash
sudo bash deploy/setup_server.sh --skip-apt --lan-cidr 192.168.1.0/24 --no-same-ip-relogin
```

With same-IP re-login off, a student whose browser closed must be unlocked by
the invigilator (Live monitor → Unlock) before signing in again.

Capacity: plan about **25–30 exam devices per access point**, prefer 5 GHz,
and put one access point in each lab. Short Wi-Fi drops are harmless — answers
queue in the browser and retry, and the timer is kept by the server — but a
device that is offline for minutes shows **No signal** on the Live monitor.

## 4. First exam day checklist

1. Log in as admin → **Students → Bulk upload** your class lists → **Print login slips**
2. **Exams → New exam** → add or upload questions → check the KaTeX preview
3. On each lab PC, open `http://192.168.1.10/` and bookmark it (Chrome/Edge/Firefox; optional: Safe Exam Browser)
4. Start the exam: **Activate** → watch **Live monitor**
5. After the exam: **Results → Publish results**, download Excel / PDF scorecards

---

## 5. Load test before the first real exam

Run with the labs idle. On the server:

```bash
cd /opt/cbt/app
sudo -u cbt /opt/cbt/venv/bin/python manage.py prepare_loadtest --students 75 --questions 30
#  -> prints "Ready: exam id 7 ..."
```

From a lab PC (Windows) with Python installed:

```powershell
pip install -r requirements-loadtest.txt
```

```powershell
$env:CBT_EXAM_ID="7"; locust -f loadtest\locustfile.py --host http://192.168.1.10 --headless --users 75 --spawn-rate 15 --run-time 6m --csv loadtest\result
```

Each simulated student logs in, opens the exam, saves 30 answers 1–3 s apart,
then **all 75 submit at the same moment**. Healthy result on a 4-core box:
**0 failures**, `api/save` p95 under ~300 ms, `submit` p95 under ~2 s.
Watch the server meanwhile with `htop` and `journalctl -u cbt -f`.

Clean up afterwards:

```bash
sudo -u cbt /opt/cbt/venv/bin/python manage.py prepare_loadtest --cleanup
```

---

## 6. Updating to a new version

Build a new `cbt_release.tar.gz` on Windows (step 1), copy it, and run the
**same** command. Data, `.env`, uploads and backups are kept; the script
installs packages, migrates and restarts:

```bash
rm -rf cbt_release && tar -xzf cbt_release.tar.gz && cd cbt_release
sudo bash deploy/setup_server.sh --skip-apt
```

Don't update during an exam — restarting takes ~3 s, and students' browsers
will retry their saves, but it's better to wait.

---

## 7. Day-to-day operations

| Task | Command |
|---|---|
| Is it running? | `systemctl status cbt nginx postgresql` |
| Live app log | `journalctl -u cbt -f` |
| Nginx errors | `sudo tail -f /var/log/nginx/cbt.error.log` |
| Restart the app | `sudo systemctl restart cbt` |
| Health check | `curl http://127.0.0.1/healthz/` |
| Backup now | `sudo -u cbt bash /opt/cbt/app/deploy/backup.sh` |
| List backups | `ls -lh /var/backups/cbt` |
| Restore a backup | `sudo -u postgres pg_restore --clean --if-exists -d cbt_db /var/backups/cbt/cbt_db_YYYYmmdd_HHMM.dump` |
| Django shell | `cd /opt/cbt/app && sudo -u cbt /opt/cbt/venv/bin/python manage.py shell` |
| New admin user | `cd /opt/cbt/app && sudo -u cbt /opt/cbt/venv/bin/python manage.py createsuperuser` |
| Change settings | `sudo nano /opt/cbt/app/.env` then `sudo systemctl restart cbt` |

Copy `/var/backups/cbt` to a USB drive regularly — a backup on the same disk
does not survive a disk failure.

**Easiest way (no terminal): the admin *Backup* page.** Sign in as admin → **Backup**:

- **Download backup now** — one `.cbtbackup` file with all students, exams, questions,
  diagrams, answers, results, settings and logo. Give it a password to encrypt it
  (AES-256); without the password nobody can open or restore it.
- **Upload a backup** — checks the file first (password, damage, app version); nothing
  changes until you confirm with your own admin password and the word `RESTORE`.
  A safety copy of the current data is always kept first, and everyone is signed out.
- Copies are kept in `/var/backups/cbt/app` (newest 20). The nightly job adds one too.
- Emergency restore from the terminal:
  `cd /opt/cbt/app && sudo -u cbt /opt/cbt/venv/bin/python manage.py cbt_restore /path/file.cbtbackup`

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| Lab PC gets "This site can't be reached" | Server IP changed or firewall: `ip -br addr`, `sudo ufw status`. Re-run setup with `--lan-cidr 192.168.0.0/16` if labs are on another subnet. |
| "Bad Request (400)" | You used an address not in `DJANGO_ALLOWED_HOSTS` — add it in `.env`, restart. |
| "CSRF verification failed" on login | Add `http://<that-address>` to `DJANGO_CSRF_TRUSTED_ORIGINS`, restart. |
| 502 Bad Gateway | App not running: `journalctl -u cbt -n 50`. |
| Student "already signed in on another computer" | Live monitor → **Unlock** (or Students → Unlock session). |
| Slow saves during the exam | Check `htop`; run the load test; raise `GUNICORN_WORKERS` via `sudo systemctl edit cbt` → `Environment=GUNICORN_WORKERS=7`. |
