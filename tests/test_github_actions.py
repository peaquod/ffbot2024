from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import yaml

from gamedaybot.espn import github_actions as gha

WORKFLOW = '.github/workflows/scheduled_messages.yml'


def _workflow_crons():
    with open(WORKFLOW) as f:
        workflow = yaml.safe_load(f)
    # PyYAML reads the key "on" as True
    return [c['cron'] for c in workflow[True]['schedule']]


def _cron_fires(cron, when):
    minute, hour, _, _, dow = cron.split()
    return (when.minute == int(minute) and when.hour == int(hour)
            and gha._cron_dow(when.weekday()) in {int(d) for d in dow.split(',')})


def test_workflow_matches_generated_crons():
    assert sorted(_workflow_crons()) == gha.generate_crons(2026) == gha.generate_crons(2027)


def test_every_slot_runs_exactly_once_all_year():
    crons = _workflow_crons()
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for day in range(365):
        for days, hour, minute, tz, functions in gha.SLOTS:
            date = start + timedelta(days=day)
            local = datetime(date.year, date.month, date.day, hour, minute, tzinfo=ZoneInfo(tz))
            if gha.DAYS[local.weekday()] not in days:
                continue
            when = local.astimezone(timezone.utc)
            firing = [c for c in crons if _cron_fires(c, when)]
            assert len(firing) == 1, (local, firing)
            for f in functions:
                assert f in gha.functions_due(when)


def test_other_daylight_copy_does_nothing():
    # Tuesday 7:30am Winnipeg in October (CDT) is 12:30 UTC; the 13:30 UTC copy is idle.
    assert gha.functions_due(datetime(2026, 10, 13, 12, 30, tzinfo=timezone.utc)) == \
        ['get_final', 'get_luck_report', 'get_form_report']
    assert gha.functions_due(datetime(2026, 10, 13, 13, 30, tzinfo=timezone.utc)) == []
    # In December (CST) it's the other way round.
    assert gha.functions_due(datetime(2026, 12, 15, 13, 30, tzinfo=timezone.utc)) == \
        ['get_final', 'get_luck_report', 'get_form_report']
    assert gha.functions_due(datetime(2026, 12, 15, 12, 30, tzinfo=timezone.utc)) == []


def test_late_run_finds_its_scheduled_time():
    # Tuesday 6:30pm CST power rankings = Wednesday 00:30 UTC; GitHub starts it 40 min late.
    now = datetime(2026, 12, 16, 1, 10, tzinfo=timezone.utc)
    when = gha.scheduled_time('30 0 * * 3', now)
    assert when == datetime(2026, 12, 16, 0, 30, tzinfo=timezone.utc)
    assert gha.functions_due(when) == ['get_power_rankings']
    # Monday 6:30pm EST close scores run late past midnight UTC.
    now = datetime(2026, 12, 15, 0, 5, tzinfo=timezone.utc)
    when = gha.scheduled_time('30 23 * * 1,2', now)
    assert when == datetime(2026, 12, 14, 23, 30, tzinfo=timezone.utc)
    assert gha.functions_due(when) == ['get_close_scores']
