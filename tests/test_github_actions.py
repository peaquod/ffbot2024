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
        for _, days, hour, minute, tz, functions in gha.SLOTS:
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
    assert gha.functions_due(when) == ['get_power_rankings', 'get_strength_of_schedule', 'get_clown_watch']
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
    monkeypatch.delenv('GITHUB_TOKEN', raising=False)
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
    scheduled = {f for slot in gha.SLOTS for f in slot[5]}
    assert not removed & scheduled


def test_slot_names_match_workflow_options():
    with open(WORKFLOW) as f:
        workflow = yaml.safe_load(f)
    options = workflow[True]['workflow_dispatch']['inputs']['function']['options']
    for name in ['fetti_report', 'tuesday_evening', 'wednesday_morning', 'thursday_matchups']:
        assert name in options


def test_timer_run_posts_whole_slot_now(monkeypatch):
    posted, slept = [], []
    now = datetime(2026, 10, 20, 17, 0, tzinfo=timezone.utc)
    import gamedaybot.espn.espn_bot as bot
    import gamedaybot.utils.util as util
    monkeypatch.setattr(gha.time, 'sleep', slept.append)
    monkeypatch.setattr(bot, 'espn_bot', posted.append)
    monkeypatch.setattr(util, 'currently_in_season', lambda *a, **k: True)
    monkeypatch.setenv('BOT_ID', 'abc123')
    monkeypatch.setenv('LEAGUE_ID', '1')
    monkeypatch.delenv('GITHUB_TOKEN', raising=False)
    gha.main(['x', '--run', 'tuesday_evening'])
    assert posted == ['get_power_rankings', 'get_strength_of_schedule', 'get_clown_watch']
    assert slept == []


def _run(id, created, title, status='completed', conclusion='success', event='workflow_dispatch'):
    return {'id': id, 'created_at': created, 'display_title': title, 'status': status,
            'conclusion': conclusion, 'event': event}


def test_run_slot_reads_titles():
    created = datetime(2026, 10, 20, 16, 41, tzinfo=timezone.utc)
    assert gha.run_slot('fetti_report', created) == 'fetti_report'
    assert gha.run_slot('40 16 * * 2', created) == 'fetti_report'   # CDT backup copy
    assert gha.run_slot('40 17 * * 2', created) is None              # other DST copy
    assert gha.run_slot('get_final', created) is None                # single manual post


def test_timer_skips_when_backup_is_already_waiting():
    runs = [_run(1, '2026-10-20T16:41:00Z', '40 16 * * 2', status='in_progress', conclusion=None,
                 event='schedule'),
            _run(2, '2026-10-20T17:00:03Z', 'fetti_report', status='in_progress', conclusion=None)]
    assert gha.handled_by_earlier_run(runs, 2, 'fetti_report')['id'] == 1
    # ...and the backup, which started first, still posts.
    assert gha.handled_by_earlier_run(runs, 1, 'fetti_report') is None


def test_late_backup_skips_after_timer_posted():
    runs = [_run(5, '2026-10-20T17:00:02Z', 'fetti_report'),
            _run(6, '2026-10-20T17:48:00Z', '40 16 * * 2', status='in_progress', conclusion=None,
                 event='schedule')]
    assert gha.handled_by_earlier_run(runs, 6, 'fetti_report')['id'] == 5


def test_failed_or_other_runs_do_not_block():
    runs = [_run(1, '2026-10-20T16:41:00Z', '40 16 * * 2', conclusion='failure', event='schedule'),
            _run(2, '2026-10-20T16:50:00Z', 'get_final'),
            _run(3, '2026-10-20T16:55:00Z', 'tuesday_evening'),
            _run(4, '2026-10-20T17:00:03Z', 'fetti_report', status='in_progress', conclusion=None)]
    assert gha.handled_by_earlier_run(runs, 4, 'fetti_report') is None


def test_manual_waiver_report_without_cookies_posts_nothing(monkeypatch):
    import pytest
    import gamedaybot.espn.espn_bot as bot
    posted = []
    monkeypatch.setattr(bot, 'espn_bot', posted.append)
    monkeypatch.setenv('BOT_ID', 'abc123')
    monkeypatch.setenv('LEAGUE_ID', '1')
    monkeypatch.delenv('SWID', raising=False)
    monkeypatch.setenv('ESPN_S2', '')
    with pytest.raises(SystemExit):
        gha.main(['x', '--run', 'get_waiver_report'])
    assert posted == []
