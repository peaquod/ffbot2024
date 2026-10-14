"""
Runs the bot's scheduled messages from GitHub Actions.

Each group of posts is a "slot" (see SLOTS). A slot can be started three ways:

1. By an outside timer such as cron-job.org, which asks GitHub to run the workflow with
   the slot's name (e.g. "fetti_report") at the exact post time. This is the main trigger,
   because GitHub's own schedules are often hours late or skipped.
2. By GitHub's own schedule, as a backup. GitHub only schedules in UTC, so
   .github/workflows/scheduled_messages.yml lists each slot twice (daylight and standard
   time); the copy that doesn't match the current clock does nothing. These runs are
   scheduled LEAD_MINUTES early and wait until the post time.
3. By hand, from the "Run workflow" button.

Before posting a slot, the run checks the workflow's other recent runs and skips if an
earlier run already handled the same slot, so the timer and the backup never double-post.

Usage:
    python -m gamedaybot.espn.github_actions "<cron string>"     # from a GitHub schedule
    python -m gamedaybot.espn.github_actions --run <slot or function>
    python -m gamedaybot.espn.github_actions --print-crons        # crons for the workflow
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(1, os.path.abspath('.'))

# Your local time for the bot's posts. The workflow's crons are generated for this
# timezone, so if you change it, regenerate them with --print-crons.
LOCAL_TIMEZONE = 'America/Winnipeg'
# NFL game times are always US Eastern.
GAME_TIMEZONE = 'America/New_York'

# How early each GitHub backup run is scheduled before its post time.
LEAD_MINUTES = 20
# A run that starts this long after its post time is skipped rather than posting stale news.
MAX_LATE = timedelta(hours=2)
# How far back to look for another run that already posted the same slot.
DUPLICATE_WINDOW = timedelta(hours=6)

DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

# (name, days, hour, minute, timezone, functions to run in order)
# 'daily_waiver' only posts when DAILY_WAIVER is turned on.
SLOTS = [
    ('fetti_report', ['tue'], 12, 0, LOCAL_TIMEZONE,
     ['get_final', 'get_luck_report', 'get_form_report']),
    ('tuesday_evening', ['tue'], 18, 30, LOCAL_TIMEZONE,
     ['get_power_rankings', 'get_strength_of_schedule', 'get_clown_watch']),
    ('wednesday_morning', ['wed'], 7, 30, LOCAL_TIMEZONE, ['get_standings', 'get_waiver_report']),
    ('thursday_matchups', ['thu'], 17, 30, GAME_TIMEZONE, ['get_matchups']),
    ('daily_waiver', ['mon', 'tue', 'thu', 'fri', 'sat', 'sun'], 7, 31, LOCAL_TIMEZONE,
     ['daily_waiver']),
]
SLOT_NAMES = [s[0] for s in SLOTS]


def _cron_dow(day_index):
    """Converts Python's weekday (Mon=0) to cron's (Sun=0)."""
    return (day_index + 1) % 7


def slot_functions(name):
    """Returns the functions a slot posts."""
    for slot in SLOTS:
        if slot[0] == name:
            return list(slot[5])
    raise KeyError(name)


def generate_crons(year=None):
    """
    Returns every UTC cron string needed to cover all SLOTS in both daylight and
    standard time, each LEAD_MINUTES before its post time.
    """
    year = year or datetime.now(timezone.utc).year
    crons = set()
    # One date in summer and one in winter, so both UTC offsets are covered.
    for sample in (datetime(year, 7, 1), datetime(year, 1, 10)):
        sample -= timedelta(days=sample.weekday())  # the Monday of that week
        for _, days, hour, minute, tz, _ in SLOTS:
            for day in days:
                local = (sample + timedelta(days=DAYS.index(day))).replace(
                    hour=hour, minute=minute, tzinfo=ZoneInfo(tz))
                utc = local.astimezone(timezone.utc) - timedelta(minutes=LEAD_MINUTES)
                crons.add((utc.minute, utc.hour, _cron_dow(utc.weekday())))

    # Merge entries that differ only by weekday to keep the workflow short.
    grouped = {}
    for minute, hour, dow in crons:
        grouped.setdefault((minute, hour), set()).add(dow)
    return sorted(f"{m} {h} * * {','.join(str(d) for d in sorted(dows))}"
                  for (m, h), dows in grouped.items())


def scheduled_time(cron, now=None):
    """
    Returns the UTC time a GitHub schedule run was meant for. GitHub runs can start
    late, so this looks back for the most recent matching time.
    """
    now = now or datetime.now(timezone.utc)
    minute, hour, _, _, dow = cron.split()
    dows = {int(d) for d in dow.split(',')}
    for days_back in range(0, 3):
        candidate = (now - timedelta(days=days_back)).replace(
            hour=int(hour), minute=int(minute), second=0, microsecond=0)
        if candidate <= now and _cron_dow(candidate.weekday()) in dows:
            return candidate
    raise ValueError(f'Could not work out the scheduled time for "{cron}"')


def post_time(cron, now=None):
    """Returns the UTC time a GitHub schedule run should post at."""
    return scheduled_time(cron, now) + timedelta(minutes=LEAD_MINUTES)


def slot_at(when_utc):
    """Returns the name of the slot that posts at the given UTC time, or None."""
    for name, days, hour, minute, tz, _ in SLOTS:
        local = when_utc.astimezone(ZoneInfo(tz))
        if DAYS[local.weekday()] in days and local.hour == hour and local.minute == minute:
            return name
    return None


def functions_due(when_utc):
    """Returns the functions whose slot posts at the given UTC time, in order."""
    name = slot_at(when_utc)
    return slot_functions(name) if name else []


def _is_cron(text):
    parts = (text or '').split()
    return len(parts) == 5 and all(p.replace(',', '').replace('*', '0').isdigit() for p in parts)


def run_slot(title, created_at):
    """Works out which slot a workflow run handled from its title (see run-name)."""
    if title in SLOT_NAMES:
        return title
    if _is_cron(title):
        try:
            return slot_at(post_time(title, created_at))
        except ValueError:
            return None
    return None


def _parse_time(text):
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def handled_by_earlier_run(runs, my_run_id, slot):
    """
    Returns the earlier run that already posted (or is about to post) this slot, if any.
    `runs` are GitHub workflow-run dicts. Runs that were queued, running or succeeded count.
    """
    mine = next((r for r in runs if r['id'] == my_run_id), None)
    if mine is None:
        return None
    my_key = (_parse_time(mine['created_at']), mine['id'])
    for r in runs:
        if r['id'] == my_run_id:
            continue
        if (_parse_time(r['created_at']), r['id']) >= my_key:
            continue
        if r['status'] == 'completed' and r.get('conclusion') != 'success':
            continue
        if run_slot(r.get('display_title'), _parse_time(r['created_at'])) == slot:
            return r
    return None


def _fetch_recent_runs():
    """Lists this workflow's recent runs using the token GitHub gives every run."""
    import requests
    repo = os.environ['GITHUB_REPOSITORY']
    run_id = int(os.environ['GITHUB_RUN_ID'])
    headers = {'Authorization': f"Bearer {os.environ['GITHUB_TOKEN']}",
               'Accept': 'application/vnd.github+json'}
    mine = requests.get(f'https://api.github.com/repos/{repo}/actions/runs/{run_id}',
                        headers=headers, timeout=30)
    mine.raise_for_status()
    workflow_id = mine.json()['workflow_id']
    since = (datetime.now(timezone.utc) - DUPLICATE_WINDOW).strftime('%Y-%m-%dT%H:%M:%SZ')
    r = requests.get(f'https://api.github.com/repos/{repo}/actions/workflows/{workflow_id}/runs',
                     params={'created': f'>={since}', 'per_page': 100}, headers=headers, timeout=30)
    r.raise_for_status()
    runs = r.json()['workflow_runs']
    if not any(x['id'] == run_id for x in runs):
        runs.append(mine.json())
    return run_id, runs


def already_posted(slot):
    """True if another run already handled this slot. Errs on the side of posting."""
    if not os.environ.get('GITHUB_TOKEN') or not os.environ.get('GITHUB_RUN_ID'):
        return False
    try:
        run_id, runs = _fetch_recent_runs()
    except Exception as e:
        print(f'Could not check for duplicate runs ({e}); posting anyway.')
        return False
    earlier = handled_by_earlier_run(runs, run_id, slot)
    if earlier:
        print(f'Skipping: run {earlier["id"]} ({earlier["event"]}, {earlier["created_at"]}) '
              f'already handled {slot}.')
        return True
    return False


def main(argv):
    if len(argv) == 2 and argv[1] == '--print-crons':
        for cron in generate_crons():
            print(f"    - cron: '{cron}'")
        return

    # GitHub passes unset secrets and variables as empty strings; treat them as unset
    # so the bot's own defaults apply.
    for key in [k for k, v in os.environ.items() if v.strip() == '']:
        del os.environ[key]

    from gamedaybot.espn.env_vars import get_env_vars
    from gamedaybot.espn.espn_bot import espn_bot
    import gamedaybot.utils.util as util

    slot, when = None, None
    if len(argv) == 3 and argv[1] == '--run':
        if argv[2] in SLOT_NAMES:
            slot = argv[2]
            print(f'Posting {slot} now')
        else:
            functions = [argv[2]]
            # Without SWID and ESPN_S2 the bot would post an error message to the chat.
            if argv[2] == 'get_waiver_report' and not (os.environ.get('SWID') and os.environ.get('ESPN_S2')):
                print('The waiver report needs the SWID and ESPN_S2 secrets. Add them in the repo\'s '
                      'Settings > Secrets and variables > Actions, then run this again.')
                sys.exit(1)
    elif len(argv) == 2:
        when = post_time(argv[1])
        slot = slot_at(when)
        print(f'Backup schedule for {when.astimezone(ZoneInfo(LOCAL_TIMEZONE)):%a %Y-%m-%d %H:%M %Z}: '
              f'{slot or "nothing (other daylight-saving copy)"}')
        if not slot:
            return
        late = datetime.now(timezone.utc) - when
        if late > MAX_LATE:
            print(f'GitHub started this run {late} late; skipping so old news is not posted.')
            return
    else:
        print(__doc__)
        sys.exit(2)

    if slot:
        functions = slot_functions(slot)
        data = get_env_vars()
        if not util.currently_in_season(data['ff_start_date'], data['ff_end_date']):
            print('Not in season, skipping.')
            return
        if 'daily_waiver' in functions and not data['daily_waiver']:
            print('DAILY_WAIVER is off, skipping.')
            return
        functions = ['get_waiver_report' if f == 'daily_waiver' else f for f in functions]
        # The waiver report needs SWID and ESPN_S2; without them the bot posts an error.
        if data['swid'] == '{1}' or data['espn_s2'] == '1':
            functions = [f for f in functions if f != 'get_waiver_report']

        if when:
            wait = (when - datetime.now(timezone.utc)).total_seconds()
            if wait > 0:
                print(f'Waiting {wait / 60:.1f} minutes until post time')
                time.sleep(wait)
        if already_posted(slot):
            return

    failed = []
    for function in functions:
        print(f'Running {function}')
        try:
            espn_bot(function)
        except Exception as e:  # keep going so one failure doesn't block the rest
            print(f'{function} failed: {e}')
            failed.append(function)
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    main(sys.argv)
