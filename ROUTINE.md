# Routine runbook

Each scheduled firing starts a fresh Claude Code cloud session. That session follows this runbook, and the Routine prompt points to it.

## 1. Get the code
Work in the `charleswave/moto-road-test-booking` repo, on branch `claude/icbc-road-test-monitor-1pmbkj`. If the checkout is on a different branch, run `git fetch origin claude/icbc-road-test-monitor-1pmbkj && git checkout claude/icbc-road-test-monitor-1pmbkj`. Don't commit or push anything during a Routine run.

## 2. Run the check
- **Hourly routine:** `python3 -m icbc_booker check --book`
- **Midnight routine:** run `python3 -m icbc_booker watch --start 00:00 --until 00:10 --every 120 --book` **in the background**, because it runs for about 13 minutes. Wait for it to exit; you'll be notified when it does. Then read its output. The last JSON line is the result.

Every command prints JSON lines, and the `status` of the last line decides what you do next.

## 3. Act on `status`
| status | action |
|---|---|
| `NO_SLOTS` | Nothing to do. End the session with a one-line summary. Don't send any email. |
| `OTP_SENT` | A slot is **locked** and ICBC has emailed a verification code. Go to step 4 **immediately**, because the lock is short. |
| `FOUND_NOT_LOCKED` | An acceptable slot exists, but ICBC refused the lock. **Email the user immediately**, subject `ICBC slot available – book now`. Include every entry in `acceptable` (date, time, office) plus the `lock_errors`, and link to https://onlinebusiness.icbc.com/webdeas-ui/home. Leave the Routines enabled. |
| `ALREADY_BOOKED` | You're done. Disable both Routines (step 5). |
| `WINDOW_CLOSED` | Oct 24 has passed. Disable both Routines (step 5) and email the user that no slot was found. |
| `ERROR` | Email the user once, subject `ICBC monitor error`, with the `error` text. Don't loop or retry more than once. |

## 4. Finish the booking (only after `OTP_SENT`)
1. Search Gmail for the ICBC code with `search_threads`. Try `from:icbc.com newer_than:1h` first, then `ICBC verification code newer_than:1h`. Open the newest message and pull out the numeric code. If it hasn't arrived yet, wait about 20 seconds and search again, for up to about 3 minutes.
2. Run `python3 -m icbc_booker confirm --code <CODE>`. If that fails with an auth error, run it again with `--relogin` added.
3. Check the booking with `python3 -m icbc_booker status`. It should report `ALREADY_BOOKED`.
4. Email the user with the date, time and office. Use Gmail `send_message` to the account's own address, subject `ICBC road test BOOKED`.
5. Disable both Routines (step 5).

## 5. Disable monitoring
Call `list_triggers`, find the Routines named `ICBC class 6 – hourly check` and `ICBC class 6 – midnight burst`, and call `update_trigger` with `enabled: false` on each one.
