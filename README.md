# Job watcher (GitHub Actions + Gmail)

Every 30 minutes: pull **latest jobs from several public boards**, filter by `keywords.txt`, email you **title + link**.

**Sources:** Remotive, Arbeitnow, RemoteOK, Jobicy, Himalayas  
**Not included:** Jobstreet / LinkedIn (no free public API)

## Setup

```bash
cd job-watcher
git init
git add .
git commit -m "feat: multi-board job link alerts"
gh repo create job-watcher --private --source=. --remote=origin --push
```

### Gmail
1. Enable 2-Step Verification  
2. Create an **App password**  
3. Repo → Settings → Secrets:

| Secret | Value |
|--------|--------|
| `GMAIL_USER` | your Gmail |
| `GMAIL_APP_PASSWORD` | app password |
| `ALERT_TO` | where to send |

Optional variables: `MIN_SCORE` (default `2`), `MAX_ALERT` (default `40`), `SEED=1`

First run with empty `seen.json` seeds (no email). Later runs only send **new** links.

## Local

```bash
cp .env.example .env
python watcher.py --self-check
python watcher.py --dry-run
```

Edit `keywords.txt` to tune what counts as a match.
