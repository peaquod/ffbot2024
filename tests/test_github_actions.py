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
            start = when - timedelta(minutes=gha.LEAD_MINUTES)
            firing = [c for c in crons if _cron_fires(c, start)]
            assert len(firing) == 1, (local, firing)
            assert gha.post_time(firing[0], start) == when
            for f in functions:
                assert f in gha.functions_due(when)


def test_runs_avoid_busy_minutes():
    for cron in _workflow_crons():
        assert cron.split()[0] not in ('0', '30'), cron


def test_other_daylight_copy_does_nothing():
    fetti = ['get_final', 'get_luck_report', 'get_form_report']
    # (functions_due takes the post time.) Tuesday 12:00pm Winnipeg in October (CDT) is 17:00 UTC; the 18:00 UTC copy is idle.
    assert gha.functions_due(datetime(2026, 10, 13, 17, 0, tzinfo=timezone.utc)) == fetti
    assert gha.functions_due(datetime(2026, 10, 13, 18, 0, tzinfo=timezone.utc)) == []
    # In December (CST) it's the other way round.
    assert gha.functions_due(datetime(2026, 12, 15, 18, 0, tzinfo=timezone.utc)) == fetti
    assert gha.functions_due(datetime(2026, 12, 15, 17, 0, tzinfo=timezone.utc)) == []


def test_late_run_finds_its_scheduled_time():
    # Tuesday 6:30pm CST power rankings: the run is scheduled for 00:10 UTC Wednesday
    # but GitHub starts it 40 minutes late; it still posts the right reports.
    now = datetime(2026, 12, 16, 0, 50, tzinfo=timezone.utc)
    when = gha.post_time('10 0 * * 3', now)
    assert when == datetime(2026, 12, 16, 0, 30, tzinfo=timezone.utc)
    assert gha.functions_due(when) == ['get_power_rankings', 'get_strength_of_schedule']
    # Thursday 5:30pm EST matchups (run at 22:10 UTC) started an hour late.
    now = datetime(2026, 12, 17, 23, 10, tzinfo=timezone.utc)
    assert gha.functions_due(gha.post_time('10 22 * * 4', now)) == ['get_matchups']


def _run_main(monkeypatch, cron, now, posted, slept):
    import gamedaybot.espn.espn_bot as bot
    import gamedaybot.utils.util as util

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(gha, 'datetime', Clock)
    monkeypatch.setattr(gha.time, 'sleep', slept.append)
    monkeypatch.setattr(bot, 'espn_bot', posted.append)
    monkeypatch.setattr(util, 'currently_in_season', lambda *a, **k: True)
    monkeypatch.setenv('BOT_ID', 'abc123')
    monkeypatch.setenv('LEAGUE_ID', '1')
    gha.main(['x', cron])


def test_early_run_waits_then_posts(monkeypatch):
    posted, slept = [], []
    # Fetti Report run starts at 11:43 CDT (16:43 UTC), 3 minutes after its schedule.
    _run_main(monkeypatch, '40 16 * * 2', datetime(2026, 10, 13, 16, 43, tzinfo=timezone.utc), posted, slept)
    assert slept == [17 * 60]
    assert posted == ['get_final', 'get_luck_report', 'get_form_report']


def test_very_late_run_is_skipped(monkeypatch):
    posted, slept = [], []
    _run_main(monkeypatch, '40 16 * * 2', datetime(2026, 10, 13, 20, 5, tzinfo=timezone.utc), posted, slept)
    assert posted == [] and slept == []


def test_removed_reports_never_post():
    removed = {'get_scoreboard_short', 'get_projected_scoreboard', 'get_close_scores', 'get_monitor'}
    scheduled = {f for slot in gha.SLOTS for f in slot[4]}
    assert not removed & scheduled
