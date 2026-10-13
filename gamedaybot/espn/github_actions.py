"""
Runs the bot's scheduled messages from GitHub Actions.

GitHub only schedules in UTC, so .github/workflows/scheduled_messages.yml lists each
post twice: once for daylight time and once for standard time. Every run works out
which post it was started for by converting its scheduled time back to the post's
timezone, so the copy that doesn't match the current clock does nothing.

Usage:
    python -m gamedaybot.espn.github_actions "<cron string>"     # from a schedule
    python -m gamedaybot.espn.github_actions --run <function>     # run one message now
    python -m gamedaybot.espn.github_actions --print-crons        # crons for the workflow
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(1, os.path.abspath('.'))

# Your local time for the bot's posts. The workflow's crons are generated for this
# timezone, so if you change it, regenerate them with --print-crons.
LOCAL_TIMEZONE = 'America/Winnipeg'
# NFL game times are always US Eastern.
GAME_TIMEZONE = 'America/New_York'

DAYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']

# (days, hour, minute, timezone, functions to run in order)
# 'daily_waiver' only posts when DAILY_WAIVER is turned on.
SLOTS = [
    (['tue'], 12, 30, LOCAL_TIMEZONE, ['get_final', 'get_luck_report', 'get_form_report']),
    (['tue'], 18, 30, LOCAL_TIMEZONE, ['get_power_rankings', 'get_strength_of_schedule']),
    (['wed'], 7, 30, LOCAL_TIMEZONE, ['get_standings', 'get_waiver_report']),
    (['thu'], 17, 30, GAME_TIMEZONE, ['get_matchups']),
    (['mon', 'tue', 'thu', 'fri', 'sat', 'sun'], 7, 31, LOCAL_TIMEZONE, ['daily_waiver']),
]


def _cron_dow(day_index):
    """Converts Python's weekday (Mon=0) to cron's (Sun=0)."""
    return (day_index + 1) % 7


def generate_crons(year=None):
    """
    Returns every UTC cron string needed to cover all SLOTS in both daylight and
    standard time.
    """
    year = year or datetime.now(timezone.utc).year
    crons = set()
    # One date in summer and one in winter, so both UTC offsets are covered.
    for sample in (datetime(year, 7, 1), datetime(year, 1, 10)):
        sample -= timedelta(days=sample.weekday())  # the Monday of that week
        for days, hour, minute, tz in ((s[0], s[1], s[2], s[3]) for s in SLOTS):
            for day in days:
                local = (sample + timedelta(days=DAYS.index(day))).replace(
                    hour=hour, minute=minute, tzinfo=ZoneInfo(tz))
                utc = local.astimezone(timezone.utc)
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


def functions_due(when_utc):
    """Returns the functions whose slot matches the given UTC time, in order."""
    due = []
    for days, hour, minute, tz, functions in SLOTS:
        local = when_utc.astimezone(ZoneInfo(tz))
        if DAYS[local.weekday()] in days and local.hour == hour and local.minute == minute:
            due.extend(functions)
    return due


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

    if len(argv) == 3 and argv[1] == '--run':
        functions = [argv[2]]
    elif len(argv) == 2:
        when = scheduled_time(argv[1])
        functions = functions_due(when)
        print(f'Scheduled for {when.astimezone(ZoneInfo(LOCAL_TIMEZONE)):%a %Y-%m-%d %H:%M %Z}: '
              f'{", ".join(functions) or "nothing (other daylight-saving copy)"}')

        data = get_env_vars()
        if functions and not util.currently_in_season(data['ff_start_date'], data['ff_end_date']):
            print('Not in season, skipping.')
            return
        functions = [f for f in functions
                     if not (f == 'daily_waiver' and not data['daily_waiver'])]
        functions = ['get_waiver_report' if f == 'daily_waiver' else f for f in functions]
        # The waiver report needs SWID and ESPN_S2; without them the bot posts an error.
        if data['swid'] == '{1}' or data['espn_s2'] == '1':
            functions = [f for f in functions if f != 'get_waiver_report']
    else:
        print(__doc__)
        sys.exit(2)

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
