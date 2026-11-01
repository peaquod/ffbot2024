"""
League history: past seasons, the all-time record book, head-to-head series, random facts
and record alerts.

Past seasons never change, so they are saved once to data/league_history.json by the
"Build league history" workflow (python -m gamedaybot.espn.history --build). Any past
season missing from that file is loaded from ESPN when needed.

People are tracked by their ESPN account rather than team name, because team names change.
A past team belongs to whichever current team shares an owner with it (so Curry Tamale's
history includes seasons one of its owners played alone). Former members keep their own
history under their own name.
"""
import json
import os
import random
import sys

if os.environ.get("AWS_EXECUTION_ENV") is not None:
    import utils.util as util
else:
    sys.path.insert(1, os.path.abspath('.'))
    import gamedaybot.utils.util as util

HISTORY_FILE = os.environ.get('HISTORY_FILE') or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'data', 'league_history.json')


# ---------------------------------------------------------------- loading seasons

def season_from_league(league, weeks=None):
    """
    Converts an espn_api League into a plain season dict.

    weeks limits the games to the first N matchup periods (for the season in progress);
    by default every scored game, including playoffs, is kept.
    """
    reg_season = league.settings.reg_season_count
    teams, games, seen = [], [], set()
    for team in league.teams:
        teams.append({
            'id': team.team_id,
            'abbrev': team.team_abbrev,
            'name': getattr(team, 'team_name', ''),
            'owners': [{'id': o.get('id'), 'firstName': o.get('firstName'), 'lastName': o.get('lastName')}
                       for o in (getattr(team, 'owners', None) or [])],
            'wins': team.wins, 'losses': team.losses, 'ties': getattr(team, 'ties', 0),
            'points_for': getattr(team, 'points_for', 0),
            'standing': getattr(team, 'standing', 0),
            'final_standing': getattr(team, 'final_standing', 0) or 0,
        })
        for w, opponent in enumerate(team.schedule):
            if weeks is not None and w >= weeks:
                break
            if opponent is None or opponent is team or w >= len(team.scores):
                continue
            pair = (w, frozenset((team.team_id, opponent.team_id)))
            if pair in seen:
                continue
            seen.add(pair)
            score, opp_score = team.scores[w], opponent.scores[w]
            if score is None or opp_score is None or (not score and not opp_score):
                continue
            games.append({'week': w + 1, 'home': team.team_id, 'away': opponent.team_id,
                          'home_score': round(float(score), 2), 'away_score': round(float(opp_score), 2)})
    return {'year': league.year, 'reg_season_count': reg_season, 'teams': teams, 'games': games}


def build_history(league_id, current_year, espn_s2=None, swid=None, log=print):
    """Loads every completed season before current_year from ESPN."""
    from espn_api.football import League
    kwargs = {'espn_s2': espn_s2, 'swid': swid} if espn_s2 and swid else {}
    current = League(league_id=league_id, year=current_year, **kwargs)
    seasons = []
    for year in sorted(current.previousSeasons):
        try:
            past = League(league_id=league_id, year=year, **kwargs)
            seasons.append(season_from_league(past))
            log(f'{year}: {len(seasons[-1]["teams"])} teams, {len(seasons[-1]["games"])} games')
        except Exception as e:  # some old seasons need cookies or are missing
            log(f'{year}: skipped ({e})')
    return {'league_id': int(league_id), 'seasons': seasons}


def load_saved_seasons(path=HISTORY_FILE):
    try:
        with open(path) as f:
            return json.load(f).get('seasons', [])
    except (OSError, ValueError):
        return []


def past_seasons(league, fetch_missing=True):
    """Every completed season before the league's current year, oldest first."""
    seasons = {s['year']: s for s in load_saved_seasons() if s['year'] < league.year}
    missing = [y for y in getattr(league, 'previousSeasons', []) if y not in seasons]
    if missing and fetch_missing and hasattr(league, 'espn_request'):
        from espn_api.football import League
        cookies = getattr(league.espn_request, 'cookies', None) or {}
        kwargs = {'espn_s2': cookies['espn_s2'], 'swid': cookies['SWID']} if cookies else {}
        for year in missing:
            try:
                seasons[year] = season_from_league(League(league_id=league.league_id, year=year, **kwargs))
            except Exception as e:
                print(f'Could not load {year}: {e}')
    return [seasons[y] for y in sorted(seasons)]


# ---------------------------------------------------------------- who is who

class Managers:
    """Maps teams in any season to a person (or co-owned team) and a display name."""

    def __init__(self, league):
        self.current = {}       # owner id -> current team key
        self.names = {}         # key -> display name
        for team in league.teams:
            key = f'cur:{team.team_id}'
            self.names[key] = util.owner_name(team)
            for o in getattr(team, 'owners', None) or []:
                if o.get('id'):
                    self.current[o['id']] = key

    def key(self, team_dict):
        ids = [o.get('id') for o in team_dict.get('owners', []) if o.get('id')]
        for i in ids:
            if i in self.current:
                return self.current[i]
        key = 'past:' + ','.join(sorted(ids)) if ids else f"abbr:{team_dict.get('abbrev')}"
        if key not in self.names:
            owner = (team_dict.get('owners') or [{}])[0]
            first = (owner.get('firstName') or '').strip().title()
            last = (owner.get('lastName') or '').strip().title()
            name = first or team_dict.get('abbrev') or 'Unknown'
            if name in self.names.values() and last:
                name = f'{first} {last[0]}'
            self.names[key] = name
        return key

    def name(self, key):
        return self.names.get(key, key)


# ---------------------------------------------------------------- the record book

class History:
    """All-time stats over past seasons plus the current season's completed weeks."""

    def __init__(self, league, seasons=None, current_weeks=None):
        self.league = league
        self.who = Managers(league)
        self.past = seasons if seasons is not None else past_seasons(league)
        if current_weeks is None:
            current_weeks = max(0, min(league.current_week - 1, league.settings.reg_season_count))
        self.current_weeks = current_weeks
        self.current = season_from_league(league, weeks=current_weeks) if current_weeks else None

        # Regular-season games, oldest first: (year, week, key_a, score_a, key_b, score_b)
        self.games = []
        for season in self.past + ([self.current] if self.current else []):
            if season is self.current:
                keys = {t['id']: f"cur:{t['id']}" for t in season['teams']}
            else:
                keys = {t['id']: self.who.key(t) for t in season['teams']}
            for g in sorted(season['games'], key=lambda g: g['week']):
                if g['week'] > season['reg_season_count']:
                    continue
                if g['home'] not in keys or g['away'] not in keys:
                    continue
                self.games.append((season['year'], g['week'], keys[g['home']], g['home_score'],
                                   keys[g['away']], g['away_score']))

    @property
    def has_history(self):
        return bool(self.past)

    def name(self, key):
        return self.who.name(key)

    def team_key(self, team):
        return f'cur:{team.team_id}'

    # -- season results

    def champions(self):
        out = []
        for s in self.past:
            for t in s['teams']:
                if t['final_standing'] == 1:
                    out.append((s['year'], self.who.key(t)))
        return out

    def last_places(self):
        out = []
        for s in self.past:
            ranked = [t for t in s['teams'] if t['final_standing']]
            if ranked:
                worst = max(ranked, key=lambda t: t['final_standing'])
                out.append((s['year'], self.who.key(worst)))
        return out

    def true_clowns(self):
        """
        The worst regular-season team of each past season, ignoring playoff and consolation
        games: [(year, key, wins, losses, ties, points)]. Fewest points breaks ties.
        """
        out = []
        for s in self.past:
            keys = {t['id']: self.who.key(t) for t in s['teams']}
            rec = {k: [0, 0, 0, 0.0] for k in keys.values()}
            for g in s['games']:
                if g['week'] > s['reg_season_count'] or g['home'] not in keys or g['away'] not in keys:
                    continue
                for k, mine, theirs in ((keys[g['home']], g['home_score'], g['away_score']),
                                        (keys[g['away']], g['away_score'], g['home_score'])):
                    rec[k][0] += mine > theirs
                    rec[k][1] += mine < theirs
                    rec[k][2] += mine == theirs
                    rec[k][3] += mine
            played = {k: v for k, v in rec.items() if sum(v[:3])}
            if played:
                k, (w, l, t, pts) = min(played.items(), key=lambda kv: (_pct(*kv[1][:3]), kv[1][3]))
                out.append((s['year'], k, w, l, t, round(pts, 2)))
        return out

    def season_records(self):
        """[(year, key, wins, losses, ties, points)] for every past season."""
        out = []
        for s in self.past:
            for t in s['teams']:
                out.append((s['year'], self.who.key(t), t['wins'], t['losses'], t['ties'], t['points_for']))
        return out

    # -- all-time

    def all_time(self, games=None):
        """{key: [wins, losses, ties, points, games]} over regular-season games."""
        table = {}
        for _, _, a, sa, b, sb in (self.games if games is None else games):
            for k, s, o in ((a, sa, sb), (b, sb, sa)):
                row = table.setdefault(k, [0, 0, 0, 0.0, 0])
                row[0] += s > o
                row[1] += s < o
                row[2] += s == o
                row[3] += s
                row[4] += 1
        return table

    def head_to_head(self, key_a, key_b, games=None):
        """(wins for a, wins for b, ties) in the regular season."""
        wa = wb = ties = 0
        for _, _, a, sa, b, sb in (self.games if games is None else games):
            if {a, b} != {key_a, key_b}:
                continue
            score_a, score_b = (sa, sb) if a == key_a else (sb, sa)
            if score_a > score_b:
                wa += 1
            elif score_b > score_a:
                wb += 1
            else:
                ties += 1
        return wa, wb, ties

    def team_scores(self, games=None):
        """[(score, key, opponent key, opponent score, year, week)] for every team-game."""
        out = []
        for y, w, a, sa, b, sb in (self.games if games is None else games):
            out.append((sa, a, b, sb, y, w))
            out.append((sb, b, a, sa, y, w))
        return out

    def streaks(self, games=None):
        """
        Longest win and losing streaks: ({key: (length, start, end)}, same for losses),
        plus each manager's streak going into the latest game ({key: (+n or -n)}).
        """
        best_w, best_l, run = {}, {}, {}
        for y, w, a, sa, b, sb in (self.games if games is None else games):
            for k, s, o in ((a, sa, sb), (b, sb, sa)):
                length, kind, start = run.get(k, (0, None, None))
                result = 'W' if s > o else 'L' if s < o else 'T'
                if result == kind:
                    length += 1
                else:
                    length, kind, start = 1, result, (y, w)
                run[k] = (length, kind, start)
                table = best_w if kind == 'W' else best_l if kind == 'L' else None
                if table is not None and length > table.get(k, (0,))[0]:
                    table[k] = (length, start, (y, w))
        current = {k: (n if kind == 'W' else -n if kind == 'L' else 0) for k, (n, kind, _) in run.items()}
        return best_w, best_l, current


def _record(w, l, t=0):
    return f'{w}-{l}' + (f'-{t}' if t else '')


def _pct(w, l, t=0):
    games = w + l + t
    return (w + 0.5 * t) / games if games else 0


def _years(years):
    years = sorted(years)
    return ', '.join(str(y) for y in years)


# ---------------------------------------------------------------- 1. History Report

def get_history_report(league, history=None):
    """
    The beginning-of-season History Report, as a list of GroupMe messages:
    champions and last-place finishers, all-time standings, and the record book.
    Returns [] when the league has no completed past seasons.
    """
    h = history or History(league, current_weeks=0)
    if not h.has_history:
        return []
    first, last = h.past[0]['year'], h.past[-1]['year']
    span = f'{first}' if first == last else f'{first}-{last}'

    champs = dict(h.champions())
    clowns = dict(h.last_places())
    lines = [f'📜 LEAGUE HISTORY REPORT 📜', f'{len(h.past)} seasons ({span})', '',
             '🏆 Champion | 🚽 Last in final standings']
    for s in reversed(h.past):
        y = s['year']
        champ = h.name(champs[y]) if y in champs else '?'
        clown = h.name(clowns[y]) if y in clowns else '?'
        lines.append(f'{y}: 🏆 {champ} | 🚽 {clown}')
    titles = {}
    for _, k in h.champions():
        titles[k] = titles.get(k, 0) + 1
    if titles:
        most = max(titles.values())
        if most > 1:
            who = ', '.join(h.name(k) for k, n in titles.items() if n == most)
            lines += ['', f'Most titles: {who} ({most})']
    messages = ['\n'.join(lines)]

    clowns_rs = h.true_clowns()
    if clowns_rs:
        lines = ['🤡 TRUE CLOWNS 🤡', 'Worst regular-season record (no playoffs)']
        for y, k, w, l, t, pts in reversed(clowns_rs):
            lines.append(f'{y}: {h.name(k)} {_record(w, l, t)}')
        counts = {}
        for _, k, *_ in clowns_rs:
            counts[k] = counts.get(k, 0) + 1
        most = max(counts.values())
        if most > 1:
            who = ', '.join(h.name(k) for k, n in counts.items() if n == most)
            lines += ['', f'Most True Clown finishes: {who} ({most})']
        messages.append('\n'.join(lines))

    table = h.all_time()
    rows = sorted(table.items(), key=lambda kv: (-_pct(*kv[1][:3]), -kv[1][3]))
    current_keys = {h.team_key(t) for t in league.teams}
    lines = ['📊 All-Time Standings (reg. season)']
    for pos, (k, (w, l, t, pts, n)) in enumerate([r for r in rows if r[0] in current_keys], start=1):
        ring = (' ' + '🏆' * titles[k]) if titles.get(k) else ''
        lines.append(f'{pos}. {h.name(k)} {_record(w, l, t)} ({_pct(w, l, t):.3f}){ring}')
    messages.append('\n'.join(lines))

    messages.append(record_book(h))
    return messages


def record_book(h):
    scores = h.team_scores()
    lines = ['📕 Record Book (reg. season)']
    if scores:
        hi = max(scores)
        lo = min(scores)
        lines.append(f'Most points: {h.name(hi[1])} {hi[0]:.2f} (Wk {hi[5]}, {hi[4]})')
        lines.append(f'Fewest points: {h.name(lo[1])} {lo[0]:.2f} (Wk {lo[5]}, {lo[4]})')
        blow = max(scores, key=lambda r: r[0] - r[3])
        lines.append(f'Biggest blowout: {h.name(blow[1])} over {h.name(blow[2])} by {blow[0] - blow[3]:.2f} '
                     f'({blow[4]})')
        close = min((r for r in scores if r[0] > r[3]), key=lambda r: r[0] - r[3], default=None)
        if close:
            lines.append(f'Closest game: {h.name(close[1])} over {h.name(close[2])} by {close[0] - close[3]:.2f} '
                         f'({close[4]})')
    best_w, best_l, _ = h.streaks()
    if best_w:
        k, (n, start, end) = max(best_w.items(), key=lambda kv: kv[1][0])
        lines.append(f'Longest win streak: {h.name(k)} {n} games ({_span(start, end)})')
    if best_l:
        k, (n, start, end) = max(best_l.items(), key=lambda kv: kv[1][0])
        lines.append(f'Longest losing streak: {h.name(k)} {n} games ({_span(start, end)})')
    seasons = h.season_records()
    if seasons:
        best = max(seasons, key=lambda r: (_pct(r[2], r[3], r[4]), r[5]))
        worst = min(seasons, key=lambda r: (_pct(r[2], r[3], r[4]), r[5]))
        lines.append(f'Best season: {h.name(best[1])} {_record(*best[2:5])} ({best[0]})')
        lines.append(f'Worst season: {h.name(worst[1])} {_record(*worst[2:5])} ({worst[0]})')
    return '\n'.join(lines)


def _span(start, end):
    return str(start[0]) if start[0] == end[0] else f'{start[0]}-{end[0]}'


# ---------------------------------------------------------------- 2. Thursday history

def random_facts(h):
    """A pool of one-line league history facts."""
    facts = []
    titles, last = {}, {}
    for y, k in h.champions():
        titles.setdefault(k, []).append(y)
    for y, k, *_ in h.true_clowns():
        last.setdefault(k, []).append(y)
    current_keys = {h.team_key(t) for t in h.league.teams}

    for k, years in titles.items():
        if len(years) > 1:
            facts.append(f'{h.name(k)} has won {len(years)} titles ({_years(years)}).')
        else:
            facts.append(f'{h.name(k)} won the {years[0]} championship.')
    for k, years in last.items():
        if len(years) > 1:
            facts.append(f'{h.name(k)} has been the True Clown {len(years)} times, with the worst '
                         f'regular season in {_years(years)}. 🤡')
        else:
            facts.append(f'{h.name(k)} was the {years[0]} True Clown, with the worst regular season. 🤡')
    droughts = [k for k in current_keys if k not in titles]
    if droughts and titles:
        facts.append(f'Still chasing a first title: {", ".join(sorted(h.name(k) for k in droughts))}.')

    scores = h.team_scores()
    if scores:
        hi, lo = max(scores), min(scores)
        facts.append(f'The highest score in league history is {hi[0]:.2f}, by {h.name(hi[1])} in Week {hi[5]}, {hi[4]}.')
        facts.append(f'The lowest score in league history is {lo[0]:.2f}, by {h.name(lo[1])} in Week {lo[5]}, {lo[4]}.')
        hi_loss = max((r for r in scores if r[0] < r[3]), default=None)
        if hi_loss:
            facts.append(f'Most points in a loss: {h.name(hi_loss[1])} scored {hi_loss[0]:.2f} and still lost to '
                         f'{h.name(hi_loss[2])} ({hi_loss[4]}).')
        lo_win = min((r for r in scores if r[0] > r[3]), default=None)
        if lo_win:
            facts.append(f'Fewest points in a win: {h.name(lo_win[1])} won with just {lo_win[0]:.2f} '
                         f'against {h.name(lo_win[2])} ({lo_win[4]}).')

    table = h.all_time()
    mine = {k: v for k, v in table.items() if k in current_keys and v[4] >= 10}
    if mine:
        best = max(mine.items(), key=lambda kv: _pct(*kv[1][:3]))
        worst = min(mine.items(), key=lambda kv: _pct(*kv[1][:3]))
        facts.append(f'{h.name(best[0])} has the best all-time record: {_record(*best[1][:3])}.')
        facts.append(f'{h.name(worst[0])} has the worst all-time record: {_record(*worst[1][:3])}.')
        most = max(mine.items(), key=lambda kv: kv[1][3])
        facts.append(f'{h.name(most[0])} has scored the most regular-season points ever: {most[1][3]:,.0f}.')

    best_w, best_l, _ = h.streaks()
    if best_w:
        k, (n, s, e) = max(best_w.items(), key=lambda kv: kv[1][0])
        facts.append(f'The longest win streak in league history is {n} games, by {h.name(k)} ({_span(s, e)}).')
    if best_l:
        k, (n, s, e) = max(best_l.items(), key=lambda kv: kv[1][0])
        facts.append(f'The longest losing streak in league history is {n} games, by {h.name(k)} ({_span(s, e)}).')

    seasons = h.season_records()
    if seasons:
        best = max(seasons, key=lambda r: (_pct(r[2], r[3], r[4]), r[5]))
        facts.append(f'Best regular season ever: {h.name(best[1])} went {_record(*best[2:5])} in {best[0]}.')
        top_pts = max(seasons, key=lambda r: r[5])
        facts.append(f'Most points in a season: {h.name(top_pts[1])} with {top_pts[5]:,.1f} in {top_pts[0]}.')

    # Lopsided rivalries among current teams.
    current = [h.team_key(t) for t in h.league.teams]
    for i, a in enumerate(current):
        for b in current[i + 1:]:
            wa, wb, t = h.head_to_head(a, b)
            if wa + wb + t >= 4 and min(wa, wb) == 0:
                win, lose, n = (a, b, wa) if wa else (b, a, wb)
                facts.append(f'{h.name(lose)} has never beaten {h.name(win)} ({n} games).')
    return facts


def pick_fact(h, seed):
    facts = random_facts(h)
    return random.Random(seed).choice(facts) if facts else ''


def get_matchup_history(league, week=None, history=None):
    """
    The Thursday add-on: the all-time series for each of this week's matchups and one
    random league fact. Returns '' when there are no past seasons.
    """
    h = history or History(league)
    if not h.has_history:
        return ''
    week = week or league.current_week
    lines = ['📜 All-time series']
    for m in league.box_scores(week=week):
        if not m.away_team:
            continue
        a, b = h.team_key(m.home_team), h.team_key(m.away_team)
        wa, wb, t = h.head_to_head(a, b)
        na, nb = util.owner_name(m.home_team), util.owner_name(m.away_team)
        if wa + wb + t == 0:
            series = 'first meeting'
        elif wa == wb:
            series = f'tied {_record(wa, wb, t)}'
        elif wa > wb:
            series = f'{na} leads {_record(wa, wb, t)}'
        else:
            series = f'{nb} leads {_record(wb, wa, t)}'
        lines.append(f'{na} vs {nb}: {series}')
    fact = pick_fact(h, f'{league.year}-{week}')
    if fact:
        lines += ['', f'🎲 Did you know? {fact}']
    return '\n'.join(lines)


# ---------------------------------------------------------------- 3. Record alerts

def get_record_alerts(league, week=None, history=None):
    """
    League records set or tied in the given week (default: the week just finished).
    Returns '' when nothing notable happened or there are no past seasons.
    """
    week = week or league.current_week - 1
    if week < 1 or week > league.settings.reg_season_count:
        return ''
    h = history or History(league, current_weeks=week)
    if not h.has_history:
        return ''
    before = [g for g in h.games if not (g[0] == league.year and g[1] >= week)]
    this_week = [g for g in h.games if g[0] == league.year and g[1] == week]
    if not before or not this_week:
        return ''
    old, new = h.team_scores(before), h.team_scores(this_week)

    alerts = []

    def check(label, value, previous, higher_is_record, fmt, who):
        if higher_is_record:
            beaten, tied = value > previous, value == previous
        else:
            beaten, tied = value < previous, value == previous
        if beaten:
            alerts.append(f'{label}: {who} {fmt(value)} (old record {fmt(previous)})')
        elif tied:
            alerts.append(f'{label} (tied): {who} {fmt(value)}')

    pts = lambda v: f'{v:.2f}'
    hi, old_hi = max(new), max(old)
    check('👑 Highest score ever', hi[0], old_hi[0], True, pts, h.name(hi[1]))
    lo, old_lo = min(new), min(old)
    check('💩 Lowest score ever', lo[0], old_lo[0], False, pts, h.name(lo[1]))
    margin = lambda r: r[0] - r[3]
    blow, old_blow = max(new, key=margin), max(old, key=margin)
    check('😱 Biggest blowout ever', margin(blow), margin(old_blow), True,
          lambda v: f'by {v:.2f}', f'{h.name(blow[1])} over {h.name(blow[2])}')
    wins_new = [r for r in new if r[0] > r[3]]
    wins_old = [r for r in old if r[0] > r[3]]
    if wins_new and wins_old:
        close, old_close = min(wins_new, key=margin), min(wins_old, key=margin)
        check('😅 Closest game ever', margin(close), margin(old_close), False,
              lambda v: f'by {v:.2f}', f'{h.name(close[1])} over {h.name(close[2])}')
    loss_new = [r for r in new if r[0] < r[3]]
    loss_old = [r for r in old if r[0] < r[3]]
    if loss_new and loss_old:
        hl, old_hl = max(loss_new), max(loss_old)
        check('😡 Most points in a loss ever', hl[0], old_hl[0], True, pts, h.name(hl[1]))

    best_w_old, best_l_old, _ = h.streaks(before)
    _, _, now = h.streaks(before + this_week)
    record_w = max((v[0] for v in best_w_old.values()), default=0)
    record_l = max((v[0] for v in best_l_old.values()), default=0)
    for k, n in now.items():
        if not any(k in (g[2], g[4]) for g in this_week):
            continue
        if n > 0 and n >= record_w and n > 2:
            alerts.append(f'🔥 {h.name(k)} has won {n} straight, '
                          f'{"a new" if n > record_w else "tying the"} league record')
        if n < 0 and -n >= record_l and -n > 2:
            alerts.append(f'🧊 {h.name(k)} has lost {-n} straight, '
                          f'{"a new" if -n > record_l else "tying the"} league record')

    if not alerts:
        return ''
    return '\n'.join(['🚨 RECORD ALERT 🚨'] + alerts)


# ---------------------------------------------------------------- command line

def main(argv):
    if len(argv) == 3 and argv[1] == '--build':
        for key in [k for k, v in os.environ.items() if v.strip() == '']:
            del os.environ[key]
        from gamedaybot.espn.env_vars import get_env_vars
        data = get_env_vars()
        swid, s2 = data.get('swid'), data.get('espn_s2')
        cookies = (s2, swid) if swid != '{1}' and s2 != '1' else (None, None)
        result = build_history(data['league_id'], data['year'], espn_s2=cookies[0], swid=cookies[1])
        os.makedirs(os.path.dirname(os.path.abspath(argv[2])), exist_ok=True)
        with open(argv[2], 'w') as f:
            json.dump(result, f, indent=1, sort_keys=True)
            f.write('\n')
        print(f'Saved {len(result["seasons"])} seasons to {argv[2]}')
        return
    print('Usage: python -m gamedaybot.espn.history --build data/league_history.json')
    sys.exit(2)


if __name__ == '__main__':
    main(sys.argv)
