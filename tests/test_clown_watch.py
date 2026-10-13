from types import SimpleNamespace as NS

import gamedaybot.espn.functionality as espn
from gamedaybot.utils import util


class Team(NS):
    __hash__ = object.__hash__
    __eq__ = object.__eq__


def make_league(averages, played, reg_season=14):
    """A round-robin league where team i always scores averages[i] (+ a small wiggle)."""
    n = len(averages)
    teams = [Team(team_abbrev=f'T{i}', owners=[{'firstName': f'Owner{i}'}], schedule=[], scores=[])
             for i in range(n)]
    for w in range(reg_season):
        order = teams[:1] + teams[1:][w % (n - 1):] + teams[1:][:w % (n - 1)]
        for k in range(n // 2):
            a, b = order[k], order[n - 1 - k]
            a.schedule.append(b)
            b.schedule.append(a)
    for i, t in enumerate(teams):
        t.scores = [averages[i] + (w % 3) if w < played else 0 for w in range(reg_season)]
    league = NS(teams=teams, current_week=played + 1, settings=NS(reg_season_count=reg_season))
    util.set_owner_names(league)
    return league


def test_worst_team_tops_the_list():
    league = make_league([80, 100, 105, 110, 115, 120, 125, 130], played=5)
    text = espn.get_clown_watch(league)
    lines = text.splitlines()
    assert lines[0] == '🤡 CLOWN WATCH 🤡'
    assert lines[1] == 'Chance of finishing last'
    assert len(lines) == 5
    assert lines[2].startswith('1. Owner0 0-5 - ')


def test_odds_add_up_and_are_repeatable():
    league = make_league([95, 100, 105, 110, 115, 120], played=4)
    odds = espn.last_place_odds(league, simulations=2000)
    assert abs(sum(c for _, c, *_ in odds) - 1) < 1e-9
    assert odds == espn.last_place_odds(league, simulations=2000)


def test_final_week_is_certain():
    league = make_league([90, 100, 110, 120], played=14)
    odds = espn.last_place_odds(league, simulations=500)
    assert util.owner_name(odds[0][0]) == 'Owner0' and odds[0][1] == 1.0
    assert '1. Owner0 0-14 - 100%' in espn.get_clown_watch(league)


def test_silent_before_games_and_after_regular_season():
    assert espn.get_clown_watch(make_league([100, 110, 120, 130], played=0)) == ''
    league = make_league([100, 110, 120, 130], played=14)
    league.current_week = 16  # playoffs
    assert espn.get_clown_watch(league) == ''
