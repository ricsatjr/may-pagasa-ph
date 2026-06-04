# Crontab Setup for Automated HRO Fetching

This document describes how to set up a cron job to automatically fetch
PAGASA Heavy Rainfall Outlook (HRO) advisory PDFs at scheduled intervals.

---

## Background

PAGASA issues HRO bulletins at up to 8 fixed times per day (when a weather
system is active):

| PHT | UTC |
|-----|-----|
| 2:00 AM | 18:00 (prev day) |
| 5:00 AM | 21:00 (prev day) |
| 8:00 AM | 00:00 |
| 11:00 AM | 03:00 |
| 2:00 PM | 06:00 |
| 5:00 PM | 09:00 |
| 8:00 PM | 12:00 |
| 11:00 PM | 15:00 |

The fetch script runs at **15 and 30 minutes past each slot**, giving two
download attempts per bulletin to account for occasional delays.

---

## Prerequisites

- Linux/macOS system with `cron` available
- Python environment with dependencies installed (`pdfplumber`, `requests`)
- Repo cloned and folder structure in place (`data/hro/pdfs/new`, etc.)
- `logs/` directory created (see below)

---

## Step 1 — Identify your Python executable

You need the full path to the Python interpreter in your project environment,
not the system Python.

### Standard virtualenv / system Python

```bash
which python
# or
which python3
```

Example output: `/usr/bin/python3`

### Conda environment

Activate your environment first, then check:

```bash
conda activate <your-env-name>
which python
```

Example output: `/home/username/miniconda3/envs/<your-env-name>/bin/python`

> **Important:** Always use the full absolute path in crontab. Cron does not
> load your shell profile, so `conda activate` and `python` alone will not
> work — the environment must be referenced explicitly by path.

---

## Step 2 — Check your system timezone

Cron schedules use the system clock timezone. Run:

```bash
timedatectl | grep "Time zone"
```

- **If `Asia/Manila` (PHT, +0800):** use PHT times directly in crontab.
- **If `UTC`:** subtract 8 hours from PHT times (see UTC schedule below).

---

## Step 3 — Create the logs directory

```bash
mkdir -p /path/to/repo/logs
```

---

## Step 4 — Open crontab

```bash
crontab -e
```

This opens your user crontab in the default editor. Add the appropriate lines
from the schedules below, then save and exit.

---

## Crontab entries

Replace the following placeholders throughout:

| Placeholder | Replace with |
|---|---|
| `/path/to/repo` | Absolute path to your repo root |
| `/path/to/python` | Full path from Step 1 |

### If system timezone is Asia/Manila (PHT)

One line covers all 8 bulletin slots:

```cron
# PAGASA HRO fetch — 15 and 30 min past each bulletin slot (Asia/Manila)
15,30 2,5,8,11,14,17,20,23 * * * cd /path/to/repo && /path/to/python src/hro/fetch_hro.py --src data/hro/pdfs/new --processed data/hro/pdfs/processed --dst data/hro/jsons >> logs/hro_fetch.log 2>&1
```

### If system timezone is UTC

Two lines are needed because 2:00 and 5:00 PHT fall on the previous UTC day:

```cron
# PAGASA HRO fetch — 2:00 and 5:00 PHT slots (18:00 and 21:00 UTC)
15,30 18,21 * * * cd /path/to/repo && /path/to/python src/hro/fetch_hro.py --src data/hro/pdfs/new --processed data/hro/pdfs/processed --dst data/hro/jsons >> logs/hro_fetch.log 2>&1

# PAGASA HRO fetch — 8:00–23:00 PHT slots (0:00–15:00 UTC)
15,30 0,3,6,9,12,15 * * * cd /path/to/repo && /path/to/python src/hro/fetch_hro.py --src data/hro/pdfs/new --processed data/hro/pdfs/processed --dst data/hro/jsons >> logs/hro_fetch.log 2>&1
```

---

## Step 5 — Verify

After saving, confirm the crontab is registered:

```bash
crontab -l
```

To test immediately without waiting for the next scheduled slot:

```bash
cd /path/to/repo && /path/to/python src/hro/fetch_hro.py \
    --src data/hro/pdfs/new \
    --processed data/hro/pdfs/processed \
    --dst data/hro/jsons >> logs/hro_fetch.log 2>&1

cat logs/hro_fetch.log
```

---

## Worked example (miniconda, Asia/Manila)

For a user `oz` with:
- Repo at `/home/oz/Git/may-pagasa-ph`
- Conda environment `may-pagasa-ph` in miniconda3

```bash
# Create logs folder
mkdir -p /home/oz/Git/may-pagasa-ph/logs

# Open crontab
crontab -e
```

Add this line:

```cron
# PAGASA HRO fetch — 15 and 30 min past each bulletin slot (Asia/Manila)
15,30 2,5,8,11,14,17,20,23 * * * cd /home/oz/Git/may-pagasa-ph && /home/oz/miniconda3/envs/may-pagasa-ph/bin/python src/hro/fetch_hro.py --src data/hro/pdfs/new --processed data/hro/pdfs/processed --dst data/hro/jsons >> logs/hro_fetch.log 2>&1
```

---

## Notes

**When no bulletin is active:** `fetch_hro.py` exits cleanly with
`"No new advisories found."` — no downloads, no errors.

**Log growth:** `logs/hro_fetch.log` accumulates indefinitely. To prevent
it from growing too large, consider adding a logrotate configuration or
periodically truncating it:

```bash
# Truncate log (keeps file but clears contents)
> /path/to/repo/logs/hro_fetch.log
```

**Removing the cron job:** Open `crontab -e` and delete the relevant lines,
or remove all jobs with `crontab -r` (use with caution).

**Multiple extractors:** When adding other extractors (e.g. TCWS), add a
separate crontab line for each fetch script following the same pattern.
