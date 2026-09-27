# ICBC Class 6 road-test monitor & auto-booker

This tool watches ICBC's online booking system for Class 6 (motorcycle) road-test slots in Greater Vancouver and books the first acceptable one. It runs entirely on Anthropic-managed compute as Claude Code **Routines**.

## What it books
- **Exam:** `6-R-1` (Class 6 full-licence road test)
- **Offices:** Burnaby, Coquitlam (Hartley Ave), Richmond, Surrey, Port Coquitlam and North Vancouver. ICBC has no Class 6 office in Vancouver proper.
- **Dates:** from tomorrow through **Oct 24, 2026**
- **Times:** Mon–Fri, starting at or after **2:00pm**. Sat–Sun, any time.
- When several slots qualify, it picks the earliest.

These settings live in `icbc_booker/config.py`, and the matching rules are in `icbc_booker/filters.py`.

## How it runs
All times are America/Vancouver.

| Routine | Schedule | Command |
|---|---|---|
| `ICBC class 6 – hourly check` | 8:05am–11:05pm, hourly | `check --book` |
| `ICBC class 6 – midnight burst` | fires at 11:57pm | `watch` checks at 12:00, 12:02, …, 12:10am |

1. Each firing starts a fresh cloud session with the Gmail connector attached. That session follows [`ROUTINE.md`](ROUTINE.md).
2. The Python CLI logs in, searches every office and filters the slots. When a slot qualifies, it starts headless Chromium (`icbc_booker/browser.js`), which signs in through ICBC's web app, **locks** the slot and asks ICBC to email a verification code. ICBC rejects the lock/book calls from plain HTTP clients, so this part runs in a real browser.
3. Claude reads the code from Gmail and runs `confirm`, which passes it to the waiting browser to verify and book. It then emails you and disables both Routines.
4. If a slot qualifies but ICBC refuses the lock, you get an email alert right away so you can book it by hand.
5. If a qualifying booking already exists, every run stops with `ALREADY_BOOKED`.

## Credentials
The CLI reads three environment variables, which are set in the cloud environment: `ICBC_LAST_NAME`, `ICBC_LICENCE` and `ICBC_KEYWORD`. Nothing else is stored.

## CLI
```
python3 -m icbc_booker check [--book] [--verbose]      # one search (read-only without --book)
python3 -m icbc_booker watch --start 00:00 --until 00:10 --every 120 [--book]
python3 -m icbc_booker confirm --code 123456
python3 -m icbc_booker test-lock [--pos 11]             # lock (never book) a non-qualifying slot to test the browser path
python3 -m icbc_booker status                          # current appointments
python3 -m unittest discover -s tests -t .             # tests
```

## Permissions
`.claude/settings.json` pre-approves `python3 -m icbc_booker …`, so scheduled runs can lock and book without a permission prompt.

## Stopping it
Ask Claude to disable, or delete, the two Routines above. You can also pause them from the Routines page on claude.ai.
