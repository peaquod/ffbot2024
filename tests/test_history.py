from types import SimpleNamespace as NS

import gamedaybot.espn.history as history
from gamedaybot.utils import util


class Team(NS):
    __hash__ = object.__hash__
    __eq__ = object.__eq__


def owner(i, first, last='X'):
    return {'id': f'{{ID-{i}}}', 'firstName': first, 'lastName': last}


def past_season(year, scores_by_week, final, owners):
    """scores_by_week: list of weeks, each a list of (home_id, home_score, away_id, away_score)."""
    teams, games, record = [], [], {i: [0, 0, 0.0] for i in owners}
    for w, week in enumerate(scores_by_week, start=1):
        for h, hs, a, as_ in week:
            games.append({'week': w, 'home': h, 'away': a, 'home_score': hs, 'away_score': as_})
            record[h][0 if hs > as_ else 1] += 1
            record[a][0 if as_ > hs else 1] += 1
            record[h][2] += hs
            record[a][2] += as_
    for i, o in owners.items():
        teams.append({'id': i, 'abbrev': f'T{i}', 'name': '', 'owners': o, 'wins': record[i][0],
                      'losses': record[i][1], 'ties': 0, 'points_for': record[i][2], 'standing': 0,
                      'final_standing': final[i]})
    return {'year': year, 'reg_season_count': len(scores_by_week), 'teams': teams, 'games': games}


OWNERS_NOW = {1: [owner(1, 'Ann')], 2: [owner(2, 'Bob')], 3: [owner(3, 'Cal'), owner(4, 'Dee')], 4: [owner(5, 'Eve')]}


def history_seasons():
    # 2024: Dee played alone (so it belongs to the co-owned team 3 now); Zed has since left.
    owners_2024 = {1: [owner(1, 'Ann')], 2: [owner(2, 'Bob')], 3: [owner(4, 'Dee')], 9: [owner(9, 'Zed')]}
    s2024 = past_season(2024, [
        [(1, 100, 2, 90), (3, 120, 9, 60)],
        [(1, 110, 3, 105), (2, 95, 9, 94.99)],
        [(1, 130, 9, 70), (2, 80, 3, 81)],
    ], {1: 1, 3: 2, 2: 3, 9: 4}, owners_2024)
    owners_2025 = {1: [owner(1, 'Ann')], 2: [owner(2, 'Bob')], 3: [owner(3, 'Cal'), owner(4, 'Dee')],
                   4: [owner(5, 'Eve')]}
    s2025 = past_season(2025, [
        [(1, 140, 2, 100), (3, 99, 4, 101)],
        [(1, 120, 3, 100), (2, 105, 4, 104)],
        [(1, 115, 4, 90), (2, 70, 3, 150)],
    ], {3: 1, 1: 2, 2: 3, 4: 4}, owners_2025)   # Eve lost the consolation game; Bob had the worst record
    return [s2024, s2025]


def current_league(week_scores, current_week):
    """week_scores: list of weeks of (home_id, hs, away_id, as)."""
    teams = {i: Team(team_id=i, team_abbrev=f'T{i}', team_name='', owners=o, schedule=[], scores=[],
                     wins=0, losses=0, ties=0, points_for=0, standing=0, final_standing=0)
             for i, o in OWNERS_NOW.items()}
    for week in week_scores:
        for h, hs, a, as_ in week:
            teams[h].schedule.append(teams[a]); teams[a].schedule.append(teams[h])
            teams[h].scores.append(hs); teams[a].scores.append(as_)
    box = {current_week: [NS(home_team=teams[1], away_team=teams[2]),
                          NS(home_team=teams[3], away_team=teams[4])]}
    league = NS(teams=list(teams.values()), year=2026, current_week=current_week, previousSeasons=[],
                settings=NS(reg_season_count=14), box_scores=lambda week=None: box.get(week, []))
    util.set_owner_names(league)
    return league


def test_owner_continuity_and_names():
    league = current_league([], 1)
    h = history.History(league, seasons=history_seasons())
    champs = dict(h.champions())
    assert h.name(champs[2024]) == 'Ann'
    assert h.name(champs[2025]) == 'Curry Tamale'         # co-owned team; Dee's 2024 counts too
    last = dict(h.last_places())
    assert h.name(last[2024]) == 'Zed'                    # former member keeps their own name
    table = h.all_time()
    assert table['cur:3'][:3] == [3, 3, 0]                # Dee 2024 (2-1) + Cal & Dee 2025 (1-2)


def test_history_report_messages():
    league = current_league([], 1)
    h = history.History(league, seasons=history_seasons(), current_weeks=0)
    msgs = history.get_history_report(league, history=h)
    assert len(msgs) == 4 and all(len(m) < 1000 for m in msgs)
    assert msgs[0].startswith('📜 LEAGUE HISTORY REPORT 📜\n2 seasons (2024-2025)')
    assert '2025: 🏆 Curry Tamale | 🚽 Eve' in msgs[0]
    assert msgs[1].splitlines()[:4] == ['🤡 TRUE CLOWNS 🤡', 'Worst regular-season record (no playoffs)',
                                        '2025: Bob 1-2', '2024: Zed 0-3']
    assert '1. Ann 6-0 (1.000) 🏆' in msgs[2]
    assert 'Zed' not in msgs[2]                           # all-time standings: current teams only
    assert 'Most points: Curry Tamale 150.00 (Wk 3, 2025)' in msgs[3]
    assert 'Fewest points: Zed 60.00 (Wk 1, 2024)' in msgs[3]
    assert 'Closest game: Bob over Zed by 0.01 (2024)' in msgs[3]


def test_matchup_history_and_fact():
    league = current_league([], 1)
    h = history.History(league, seasons=history_seasons())
    text = history.get_matchup_history(league, week=1, history=h)
    lines = text.splitlines()
    assert lines[0] == '📜 All-time series'
    assert lines[1] == 'Ann vs Bob: Ann leads 2-0'
    assert lines[2] == 'Curry Tamale vs Eve: Eve leads 1-0'
    assert lines[-1].startswith('🎲 Did you know? ')
    assert text == history.get_matchup_history(league, week=1, history=h)   # same fact on reruns


def test_record_alerts_fire_and_stay_quiet():
    seasons = history_seasons()
    # Week 1 of 2026: Ann scores 151 (old record 150), Eve scores 59 (old low 60)
    league = current_league([[(1, 151, 2, 120), (3, 100, 4, 59)]], current_week=2)
    h = history.History(league, seasons=seasons, current_weeks=1)
    alerts = history.get_record_alerts(league, week=1, history=h)
    assert alerts.startswith('🚨 RECORD ALERT 🚨')
    assert '👑 Highest score ever: Ann 151.00 (old record 150.00)' in alerts
    assert '💩 Lowest score ever: Eve 59.00 (old record 60.00)' in alerts
    assert '🔥 Ann has won 7 straight, a new league record' in alerts

    # Ann's streak ends and Eve wins, so no streak records either.
    quiet = current_league([[(1, 100, 2, 110), (3, 95, 4, 105)]], current_week=2)
    h = history.History(quiet, seasons=seasons, current_weeks=1)
    assert history.get_record_alerts(quiet, week=1, history=h) == ''


def test_tied_record():
    league = current_league([[(1, 150, 2, 120), (3, 100, 4, 95)]], current_week=2)
    h = history.History(league, seasons=history_seasons(), current_weeks=1)
    assert '👑 Highest score ever (tied): Ann 150.00' in history.get_record_alerts(league, week=1, history=h)


def test_no_history_means_no_posts():
    league = current_league([[(1, 150, 2, 120), (3, 100, 4, 95)]], current_week=2)
    h = history.History(league, seasons=[], current_weeks=1)
    assert history.get_history_report(league, history=h) == []
    assert history.get_matchup_history(league, week=2, history=h) == ''
    assert history.get_record_alerts(league, week=1, history=h) == ''


def test_saved_history_file_loads():
    seasons = history.load_saved_seasons()
    if seasons:  # the real league file, when present
        years = [s['year'] for s in seasons]
        assert years == sorted(years)
        for s in seasons:
            reg = [g for g in s['games'] if g['week'] <= s['reg_season_count']]
            assert len(reg) * 2 == sum(t['wins'] + t['losses'] + t['ties'] for t in s['teams'])


def test_true_clown_ignores_playoffs():
    seasons = history_seasons()
    # A consolation game in week 4 (after the 3-week regular season) that Bob wins big.
    seasons[1]['games'].append({'week': 4, 'home': 2, 'away': 4, 'home_score': 200, 'away_score': 50})
    league = current_league([], 1)
    h = history.History(league, seasons=seasons, current_weeks=0)
    clowns = {y: (h.name(k), w, l) for y, k, w, l, t, pts in h.true_clowns()}
    assert clowns == {2024: ('Zed', 0, 3), 2025: ('Bob', 1, 2)}     # 1-2 three ways; Bob has fewest points
    facts = history.random_facts(h)
    assert 'Bob was the 2025 True Clown, with the worst regular season. 🤡' in facts
    assert not any('Eve' in f and 'Clown' in f for f in facts)
