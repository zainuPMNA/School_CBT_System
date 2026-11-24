# School CBT System

An offline computer-based test (CBT) system for school computer labs. It runs on one
server PC on the school LAN or Wi-Fi, and students take their tests in an ordinary web
browser. No internet connection is needed on exam day.

Built with Django 5, PostgreSQL, Tailwind CSS, Alpine.js, HTMX and KaTeX. Every file it
uses is stored locally, with no CDN links.

## Features

- **Students:** bulk upload from Excel or CSV, printable login slips with unpredictable
  passwords, and one login per student (a second PC is blocked).
- **Exams:** NTA-style exam screen with a question palette, mark for review, a server-kept
  timer, auto-save of every answer and maths formulas (KaTeX). Questions and options are
  shuffled per student, and an exam can be set for several classes at once.
- **Anti-cheat:** full-screen mode, tab-switch logging, and copy and right-click blocked.
- **Live monitor:** see who is writing, extend time, force-submit or unlock a student.
- **Results:** ranks, question analysis, and PDF scorecards and Excel exports.
- **School branding:** school name, tagline and logo, set from the Settings page.
- **Screen themes:** Light, Dark and Warm, remembered for each user.
- **Backup and restore:** one `.cbtbackup` file with optional AES-256 password
  protection, checked before restoring and with a safety copy kept first.

## Run it

| Where | How |
|---|---|
| Windows PC (quick start) | Double-click `START_CBT_APP.bat` |
| Windows PC with no Python installed | Build the portable ZIP: `deploy\build_windows_portable.ps1` |
| Ubuntu server (for real exams) | See [DEPLOY.md](DEPLOY.md) |

Settings are read from a `.env` file. Copy `.env.example` to `.env` and fill it in.
The `.env` file is never uploaded to GitHub.

## Tests

```
python manage.py test
```

## Developer

Zainulabid, Academic Coordinator, YES P.A. Inamdar English School and Junior College, Perinthalmanna.

## Licence

Apache License 2.0. See [LICENSE](LICENSE).
