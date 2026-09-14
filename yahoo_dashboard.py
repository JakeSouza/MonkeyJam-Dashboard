"""
Yahoo Fantasy Football Dashboard Generator
============================================
Same dashboard as the ESPN/Sleeper versions, but driven by the Yahoo
Fantasy Sports API via the `yfpy` wrapper.

SETUP (one-time, because Yahoo requires OAuth)
---------------------------------------------
1. Create a Yahoo Developer app (https://developer.yahoo.com/apps/) to get a
   Consumer Key + Consumer Secret. Apply for Fantasy Sports API access at
   https://sports.yahoo.com/developer/access/ if you haven't already.
2. Install: pip install yfpy
3. First run ONLY, generate a stored OAuth token locally (opens a browser):
     YFPY_BROWSER_AUTH=1 python yahoo_dashboard.py
   This caches a refresh token in YFPY_AUTH_DIR (default .auth/). Re-run any
   time after that with NO browser using just the env vars/secrets below.

ENV VARS / GITHUB SECRETS
------------------------
LEAGUE_ID              numeric Yahoo league id, e.g. 123456
YFPY_CONSUMER_KEY      your app consumer key
YFPY_CONSUMER_SECRET   your app consumer secret
YFPY_AUTH_DIR          where the token is cached (default .auth)
YFPY_BROWSER_AUTH      set to "1" only for the interactive first run
HISTORY_START_YEAR     default 2018
OUTPUT_FILE            default index.html

NOTE: Yahoo's public API exposes each manager's *nickname* (display name),
not first/last name. Co-managers are returned in each team's `managers` list
and are all included, joined with " & ".
"""

import os
import json
import html
from pathlib import Path
from datetime import datetime
from yfpy.query import YahooFantasySportsQuery


def _env(name, default=None):
    value = os.environ.get(name)
    return value if value else default


LEAGUE_ID = _env("LEAGUE_ID", "")
CONSUMER_KEY = _env("YFPY_CONSUMER_KEY", "")
CONSUMER_SECRET = _env("YFPY_CONSUMER_SECRET", "")
AUTH_DIR = _env("YFPY_AUTH_DIR", ".auth")
BROWSER_AUTH = _env("YFPY_BROWSER_AUTH", "") == "1"
OUTPUT_FILE = _env("OUTPUT_FILE", "index.html")
RECENT_ACTIVITY_COUNT = int(_env("RECENT_ACTIVITY_COUNT", "25"))
HISTORY_START_YEAR = int(_env("HISTORY_START_YEAR", "2018"))

# --------------------------------------------------------------------------- #
# API ACCESS LAYER (the only part that is Yahoo-specific)
# --------------------------------------------------------------------------- #

def _jload(x):
    if isinstance(x, (dict, list)):
        return x
    if isinstance(x, (str, bytes)):
        try:
            return json.loads(x)
        except Exception:
            return x
    return x


def _as_list(x):
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _dig(obj, *keys):
    cur = obj
    for k in keys:
        if isinstance(cur, dict) and k in cur:
            cur = cur[k]
        else:
            return None
    return cur


def make_query(game_id=None):
    """
    Build a YahooFantasySportsQuery. Two real bugs fixed here vs. the
    original: (1) 'auth_dir' isn't a real constructor argument in yfpy —
    only 'env_file_location' controls where the token cache lives, so
    passing auth_dir raised a TypeError before anything could run. (2)
    env_file_location must be a pathlib.Path (yfpy joins it with '/'
    internally), not a plain string.
    """
    auth_path = Path(AUTH_DIR)
    auth_path.mkdir(parents=True, exist_ok=True)
    return YahooFantasySportsQuery(
        league_id=LEAGUE_ID,
        game_code="nfl",
        game_id=game_id,
        yahoo_consumer_key=CONSUMER_KEY,
        yahoo_consumer_secret=CONSUMER_SECRET,
        all_output_as_json_str=True,
        browser_callback=BROWSER_AUTH,
        offline=False,
        save_token_data_to_env_file=True,
        env_file_location=auth_path,
    )


def game_keys_map(q):
    """Return {season:int -> game_id} for all NFL games the account can see."""
    out = {}
    try:
        games = _as_list(_jload(q.get_all_yahoo_fantasy_game_keys()))
        for g in games:
            if (g.get("code") or "").lower() == "nfl":
                try:
                    out[int(g["season"])] = str(g["game_id"])
                except Exception:
                    pass
    except Exception:
        pass
    return out


def team_logo(team):
    logos = team.get("team_logos") or {}
    logo = logos.get("team_logo") if isinstance(logos, dict) else None
    logo = _as_list(logo)
    return logo[0].get("url") if logo and isinstance(logo[0], dict) and logo[0].get("url") else ""


def team_managers(team):
    mgrs = _as_list((team.get("managers") or {}).get("manager"))
    names = []
    for m in mgrs:
        if isinstance(m, dict):
            nick = m.get("nickname") or m.get("name")
            if nick:
                names.append(nick)
    return " & ".join(names) if names else None


def parse_teams(teams_raw):
    teams = {}
    for t in _as_list(teams_raw):
        tid = t.get("team_id") or str(len(teams) + 1)
        st = t.get("team_standings") or {}
        ot = st.get("outcome_totals") or {}
        streak = st.get("streak") or {}
        teams[tid] = {
            "team_id": tid,
            "team_name": t.get("name") or f"Team {tid}",
            "owner": team_managers(t),
            "wins": int(ot.get("wins", 0) or 0),
            "losses": int(ot.get("losses", 0) or 0),
            "ties": int(ot.get("ties", 0) or 0),
            "fpts": float(ot.get("points_for", 0) or 0),
            "fpts_against": float(ot.get("points_against", 0) or 0),
            "avatar": team_logo(t),
            "rank": int(st.get("rank", 0) or 0),
            "streak": f"{(streak.get('type') or '-')[0].upper()}{streak.get('value', 0)}" if streak else "-",
        }
    return teams


def fetch_teams(q):
    data = _jload(q.get_league_teams())
    if isinstance(data, list):
        return parse_teams(data)
    nested = _dig(data, "teams", "team") or _dig(data, "league", "teams", "team") or data
    return parse_teams(nested)


def league_players_map(q):
    """
    Returns {player_key: {"name", "pos", "team"}} for every player Yahoo
    returns for this league. get_league_players() already includes
    display_position and editorial_team_abbr per player.
    """
    m = {}
    try:
        data = _jload(q.get_league_players())
        plist = _as_list(_dig(data, "players", "player") or data)
        for p in plist:
            key = p.get("player_key") or str(p.get("player_id"))
            nm = p.get("name") or {}
            full = nm.get("full") if isinstance(nm, dict) else nm
            if not full and isinstance(nm, dict):
                full = f"{nm.get('first','')} {nm.get('last','')}".strip()
            m[key] = {
                "name": full or key,
                "pos": p.get("display_position") or p.get("primary_position") or "",
                "team": p.get("editorial_team_abbr") or "",
            }
    except Exception:
        pass
    return m


def fetch_league_settings(q):
    """Returns (playoff_spots, reg_season_weeks), or (None, None) if unavailable."""
    try:
        data = _jload(q.get_league_settings())
    except Exception:
        return None, None
    settings = _dig(data, "settings") or _dig(data, "league", "settings") or data
    if not isinstance(settings, dict):
        return None, None
    try:
        playoff_spots = int(settings.get("num_playoff_teams") or 0) or None
    except (TypeError, ValueError):
        playoff_spots = None
    try:
        playoff_start_week = int(settings.get("playoff_start_week") or 0) or None
    except (TypeError, ValueError):
        playoff_start_week = None
    reg_season_weeks = (playoff_start_week - 1) if playoff_start_week else None
    return playoff_spots, reg_season_weeks


def weekly_results(q, max_week):
    """Returns (scores, pairs, outcomes, projected). 'projected' mirrors
    'scores' but holds each team's projected points for that week (used for
    the Prediction Accuracy tab) — Yahoo exposes this via
    team_projected_points on the same matchup payload we already fetch, so
    this adds no extra API calls."""
    scores, pairs, outcomes, projected = {}, {}, {}, {}
    for w in range(1, max_week + 1):
        try:
            mu = _as_list(_jload(q.get_league_matchups_by_week(w)))
        except Exception:
            mu = []
        for m in mu:
            teams = _as_list(_dig(m, "teams", "team") or m.get("teams"))
            grp = []
            for t in teams:
                tid = t.get("team_id")
                pts = _dig(t, "team_points", "total")
                proj = _dig(t, "team_projected_points", "total")
                if pts is None:
                    continue
                pts = float(pts)
                scores.setdefault(tid, {})[w] = pts
                if proj is not None:
                    projected.setdefault(tid, {})[w] = float(proj)
                grp.append((tid, pts))
            if len(grp) == 2:
                (a, sa), (b, sb) = grp
                pairs.setdefault(w, []).append((a, b))
                if sa > sb:
                    outcomes.setdefault(a, []).append("W"); outcomes.setdefault(b, []).append("L")
                elif sb > sa:
                    outcomes.setdefault(b, []).append("W"); outcomes.setdefault(a, []).append("L")
                else:
                    outcomes.setdefault(a, []).append("T"); outcomes.setdefault(b, []).append("T")
    return scores, pairs, outcomes, projected


def _update_streak(state, team_id, outcome, year, week, name, owner):
    """Rolling win/loss streak tracker keyed by team_id. A streak always
    breaks at a season boundary (even mid-type) and a tie breaks both a
    win and loss streak. Records the best win streak and best loss
    streak seen so far, each with its year/week span."""
    s = state.setdefault(team_id, {
        "current_type": None, "current_len": 0, "current_start": None,
        "best_win": None, "best_loss": None, "last_year": None,
    })
    if s["last_year"] is not None and year != s["last_year"]:
        s["current_type"], s["current_len"], s["current_start"] = None, 0, None
    s["last_year"] = year
    if outcome == "T":
        s["current_type"], s["current_len"], s["current_start"] = None, 0, None
        return
    if outcome == s["current_type"]:
        s["current_len"] += 1
    else:
        s["current_type"], s["current_len"], s["current_start"] = outcome, 1, (year, week)
    entry = {
        "length": s["current_len"], "team": name, "owner": owner,
        "start_year": s["current_start"][0], "start_week": s["current_start"][1],
        "end_year": year, "end_week": week,
    }
    key = "best_win" if outcome == "W" else "best_loss"
    if s[key] is None or entry["length"] > s[key]["length"]:
        s[key] = entry


def _process_game(records, streak_state, season, week, tid_a, tid_b, sa, sb, name_by_id, owner_by_id):
    """Updates the records book (highest/lowest score, biggest blowout,
    closest game, most points scored in a loss) and both teams' streaks
    for one head-to-head result."""
    name_a, name_b = name_by_id.get(tid_a, tid_a), name_by_id.get(tid_b, tid_b)
    owner_a, owner_b = owner_by_id.get(tid_a), owner_by_id.get(tid_b)

    for name, owner, score, opp_score, year, wk in (
        (name_a, owner_a, sa, sb, season, week), (name_b, owner_b, sb, sa, season, week)
    ):
        cand = {"team": name, "owner": owner, "score": round(score, 1), "year": year, "week": wk}
        if records["highest_score"] is None or score > records["highest_score"]["score"]:
            records["highest_score"] = cand
        if records["lowest_score"] is None or score < records["lowest_score"]["score"]:
            records["lowest_score"] = cand
        if score < opp_score:
            if records["most_points_loss"] is None or score > records["most_points_loss"]["score"]:
                records["most_points_loss"] = cand

    if sa != sb:
        margin = round(abs(sa - sb), 1)
        winner, loser = (name_a, name_b) if sa > sb else (name_b, name_a)
        blow = {"winner": winner, "loser": loser, "margin": margin, "year": season, "week": week}
        if records["biggest_blowout"] is None or margin > records["biggest_blowout"]["margin"]:
            records["biggest_blowout"] = blow
        close = {"team_a": name_a, "team_b": name_b, "margin": margin, "year": season, "week": week}
        if records["closest_game"] is None or margin < records["closest_game"]["margin"]:
            records["closest_game"] = close

    oc_a, oc_b = ("W", "L") if sa > sb else (("L", "W") if sb > sa else ("T", "T"))
    _update_streak(streak_state, tid_a, oc_a, season, week, name_a, owner_a)
    _update_streak(streak_state, tid_b, oc_b, season, week, name_b, owner_b)


def fetch_history(q, gkeys, current_season, start_year):
    champions, season_standings, all_time, rivalries = [], {}, {}, {}
    name_by_id = {}
    owner_by_id = {}
    records = {
        "highest_score": None, "lowest_score": None, "biggest_blowout": None,
        "closest_game": None, "most_points_loss": None,
    }
    streak_state = {}

    for season in range(current_season, start_year - 1, -1):
        gid = gkeys.get(season)
        if not gid:
            continue
        try:
            hq = make_query(game_id=gid)
            teams = fetch_teams(hq)
        except Exception:
            continue
        if not teams:
            continue
        ordered = sorted(teams.values(), key=lambda t: (-(t["wins"]), t["losses"], -t["fpts"]))
        for t in ordered:
            name_by_id[t["team_id"]] = t["team_name"]
            if t.get("owner"):
                owner_by_id[t["team_id"]] = t["owner"]
        season_standings[season] = [{
            "rank": i + 1, "name": t["team_name"], "owner": t.get("owner"),
            "wins": t["wins"], "losses": t["losses"], "ties": t["ties"],
            "pf": t["fpts"], "pa": t["fpts_against"],
        } for i, t in enumerate(ordered)]
        if season != current_season and ordered:
            ch = ordered[0]
            champions.append({"year": season, "name": ch["team_name"], "owner": ch.get("owner")})
        try:
            sc, pr, _, _ = weekly_results(hq, 16)
            for w, plist in pr.items():
                for a, b in plist:
                    sa, sb = sc.get(a, {}).get(w), sc.get(b, {}).get(w)
                    if sa is None or sb is None:
                        continue
                    key = frozenset({a, b})
                    h2h = rivalries.setdefault(key, {"meetings": 0, "wins": {}, "points": {}})
                    h2h["meetings"] += 1
                    h2h["wins"].setdefault(a, 0); h2h["wins"].setdefault(b, 0)
                    h2h["points"].setdefault(a, 0.0); h2h["points"].setdefault(b, 0.0)
                    if sa > sb: h2h["wins"][a] += 1
                    elif sb > sa: h2h["wins"][b] += 1
                    else: h2h["wins"][a] += 0.5; h2h["wins"][b] += 0.5
                    h2h["points"][a] += sa; h2h["points"][b] += sb
                    _process_game(records, streak_state, season, w, a, b, sa, sb, name_by_id, owner_by_id)
        except Exception:
            pass

        for t in teams.values():
            e = all_time.setdefault(t["team_id"], {
                "name": t["team_name"], "owner": None, "wins": 0, "losses": 0,
                "ties": 0, "pf": 0.0, "seasons": 0})
            e["name"] = t["team_name"]
            if t.get("owner"): e["owner"] = t["owner"]
            e["wins"] += t["wins"]; e["losses"] += t["losses"]; e["ties"] += t["ties"]
            e["pf"] += t["fpts"]; e["seasons"] += 1

    for e in all_time.values():
        g = e["wins"] + e["losses"] + e["ties"]
        e["win_pct"] = (e["wins"] + 0.5 * e["ties"]) / g if g else 0

    records["longest_win_streak"] = max(
        (s["best_win"] for s in streak_state.values() if s["best_win"]), key=lambda e: e["length"], default=None)
    records["longest_loss_streak"] = max(
        (s["best_loss"] for s in streak_state.values() if s["best_loss"]), key=lambda e: e["length"], default=None)

    return champions, season_standings, all_time, rivalries, name_by_id, records


def compute_playoff_picture(teams, playoff_spots, reg_season_weeks):
    """
    Same conservative math as the ESPN version: a team CLINCHES only when
    its current wins already beat every outside team's best-case win-out
    total; a team is ELIMINATED only when its own best-case can't reach
    what the last playoff-spot team has already banked. Doesn't account
    for head-to-head/points tiebreakers.
    """
    if not playoff_spots or not reg_season_weeks or not teams:
        return None
    standings = sorted(teams.values(), key=lambda t: (-t["wins"], t["losses"], -t["fpts"]))
    entries = []
    for seed, t in enumerate(standings, start=1):
        games_played = t["wins"] + t["losses"] + t["ties"]
        remaining = max(0, reg_season_weeks - games_played)
        entries.append({
            "seed": seed, "team": t, "wins": t["wins"], "losses": t["losses"],
            "ties": t["ties"], "remaining": remaining,
            "max_possible_wins": t["wins"] + remaining,
        })
    in_the_hunt = entries[:playoff_spots]
    outside = entries[playoff_spots:]
    last_in = in_the_hunt[-1] if in_the_hunt else None
    for e in in_the_hunt:
        e["clinched"] = bool(outside) and all(e["wins"] > o["max_possible_wins"] for o in outside)
        e["status"] = "Clinched" if e["clinched"] else "In the hunt"
    for e in outside:
        e["eliminated"] = last_in is not None and e["max_possible_wins"] < last_in["wins"]
        e["status"] = "Eliminated" if e["eliminated"] else "On the bubble"
    return {
        "playoff_spots": playoff_spots,
        "in_the_hunt": in_the_hunt,
        "outside": outside,
        "season_over": all(e["remaining"] == 0 for e in entries),
    }


# --------------------------------------------------------------------------- #
# ADAPTER: build a common `model` dict from Yahoo
# --------------------------------------------------------------------------- #

def build_model():
    q = make_query()
    gkeys = game_keys_map(q)
    game = _jload(q.get_current_game_info()) or {}
    season = int(game.get("season") or datetime.utcnow().year)
    league = _jload(q.get_league_metadata()) or {}
    league_name = league.get("name") or "Yahoo League"
    try:
        current_week = int(league.get("current_week") or game.get("current_week") or 1)
    except Exception:
        current_week = 1

    teams = fetch_teams(q)
    pmap = league_players_map(q)
    scores, pairs, outcomes, projected = weekly_results(q, 18)
    completed = sorted(pairs.keys())
    recent_weeks = completed[-3:]

    ordered = sorted(teams.values(), key=lambda t: (-(t["wins"]), t["losses"], -t["fpts"]))
    standings = []
    for i, t in enumerate(ordered, 1):
        tid = t["team_id"]
        standings.append({
            "rank": i, "name": t["team_name"], "owner": t.get("owner"),
            "wins": t["wins"], "losses": t["losses"], "ties": t["ties"],
            "pf": t["fpts"], "pa": t["fpts_against"], "streak": t.get("streak", "-"),
            "logo": t.get("avatar"),
            "trend": [scores.get(tid, {}).get(w) for w in completed],
        })

    # playoff picture
    playoff_spots, reg_season_weeks = fetch_league_settings(q)
    picture = compute_playoff_picture(teams, playoff_spots, reg_season_weeks)

    # matchups (current week)
    matchups = []
    try:
        mu = _as_list(_jload(q.get_league_matchups_by_week(current_week)))
    except Exception:
        mu = []
    avg_pts = {}
    for tid, wk in scores.items():
        vals = list(wk.values())
        avg_pts[tid] = sum(vals) / len(vals) if vals else 0
    for m in mu:
        ts = _as_list(_dig(m, "teams", "team") or m.get("teams"))
        if len(ts) != 2:
            continue
        def side(t):
            tid = t.get("team_id")
            proj = _dig(t, "team_projected_points", "total")
            pts = _dig(t, "team_points", "total")
            tm = teams.get(tid, {})
            return {"name": tm.get("team_name", t.get("name", "TBD")),
                    "record": f"{tm.get('wins',0)}-{tm.get('losses',0)}" + (f"-{tm.get('ties',0)}" if tm.get('ties') else ""),
                    "proj": round(float(proj), 1) if proj is not None else round(avg_pts.get(tid, 0), 1),
                    "pts": (float(pts) if pts is not None else None), "tid": tid}
        a, b = side(ts[0]), side(ts[1])
        ap = a["pts"] if a["pts"] is not None else avg_pts.get(a["tid"], 0)
        bp = b["pts"] if b["pts"] is not None else avg_pts.get(b["tid"], 0)
        fav, dog = (a, b) if ap >= bp else (b, a)
        gap = round(abs(ap - bp), 1)
        matchups.append({"away": a, "home": b, "outlook": matchup_outlook(fav["name"], dog["name"], gap)})

    # power rankings
    power = []
    if completed:
        raw = []
        for t in teams.values():
            tid = t["team_id"]
            pts = [scores.get(tid, {}).get(w) for w in completed if scores.get(tid, {}).get(w) is not None]
            margins = []
            for w in completed:
                for a, b in pairs.get(w, []):
                    if a == tid or b == tid:
                        opp = b if a == tid else a
                        ms, os_ = scores.get(tid, {}).get(w), scores.get(opp, {}).get(w)
                        if ms is not None and os_ is not None:
                            margins.append(ms - os_)
            rpts = [scores.get(tid, {}).get(w) for w in recent_weeks if scores.get(tid, {}).get(w) is not None]
            raw.append({"team": t, "avg_pts": sum(pts)/len(pts) if pts else 0,
                        "avg_margin": sum(margins)/len(margins) if margins else 0,
                        "avg_recent": sum(rpts)/len(rpts) if rpts else (sum(pts)/len(pts) if pts else 0)})
        def norm(vals):
            lo, hi = min(vals), max(vals)
            return [50.0 if hi == lo else (v - lo)/(hi - lo)*100 for v in vals]
        np_ = norm([r["avg_pts"] for r in raw]); nm = norm([r["avg_margin"] for r in raw]); nr = norm([r["avg_recent"] for r in raw])
        for i, r in enumerate(raw):
            r["score"] = round(0.45*np_[i] + 0.25*nm[i] + 0.30*nr[i], 1)
        raw.sort(key=lambda r: -r["score"])
        std_rank = {t["team_id"]: i+1 for i, t in enumerate(ordered)}
        for pr, r in enumerate(raw, 1):
            t = r["team"]; sr = std_rank.get(t["team_id"], pr)
            power.append({"rank": pr, "name": t["team_name"], "owner": t.get("owner"),
                          "score": r["score"], "avg_pts": round(r["avg_pts"], 1),
                          "avg_margin": round(r["avg_margin"], 1), "delta": sr - pr})

    # luck index
    luck = []
    if completed and len(teams) >= 2:
        ap = {tid: [] for tid in teams}
        for w in completed:
            wk = {tid: scores.get(tid, {}).get(w) for tid in teams if scores.get(tid, {}).get(w) is not None}
            n = len(wk)
            if n < 2: continue
            for tid, sc in wk.items():
                beat = sum(1 for o, os_ in wk.items() if o != tid and sc > os_)
                ap[tid].append(beat / (n - 1))
        for t in teams.values():
            lst = ap.get(t["team_id"], [])
            if not lst: continue
            exp = sum(lst)/len(lst)
            g = t["wins"] + t["losses"] + t["ties"]
            act = (t["wins"] + 0.5*t["ties"])/g if g else 0
            l = act - exp
            lbl = "Lucky" if l > 0.12 else ("Unlucky" if l < -0.12 else "About Right")
            luck.append({"name": t["team_name"], "owner": t.get("owner"),
                        "actual": round(act*100, 1), "expected": round(exp*100, 1),
                        "luck": round(l*100, 1), "label": lbl})
        luck.sort(key=lambda x: -x["luck"])

    # prediction accuracy — how often each team beat Yahoo's own weekly
    # projection, and by how much on average (uses the 'projected' data
    # weekly_results already captured, so no extra API calls)
    prediction = []
    for t in teams.values():
        tid = t["team_id"]
        diffs, beat = [], 0
        for w in completed:
            actual = scores.get(tid, {}).get(w)
            proj = projected.get(tid, {}).get(w)
            if actual is None or proj is None:
                continue
            diffs.append(round(actual - proj, 1))
            if actual >= proj:
                beat += 1
        if not diffs:
            continue
        prediction.append({
            "name": t["team_name"], "owner": t.get("owner"),
            "played": len(diffs),
            "beat_pct": round(beat / len(diffs) * 100, 1),
            "avg_diff": round(sum(diffs) / len(diffs), 1),
        })
    prediction.sort(key=lambda x: -x["beat_pct"])

    # recent activity
    activity = []
    try:
        txs = _as_list(_jload(q.get_league_transactions()))
    except Exception:
        txs = []
    for tx in txs:
        ttype = (tx.get("type") or "").lower()
        try:
            ts_val = int(tx.get("timestamp") or 0)
        except (TypeError, ValueError):
            ts_val = 0
        date_str = datetime.fromtimestamp(ts_val).strftime("%b %d, %Y") if ts_val else ""
        players = _as_list(_dig(tx, "players", "player") or tx.get("players"))
        for p in players:
            name = p.get("name") or (pmap.get(p.get("player_key")) or {}).get("name") or p.get("player_key") or "?"
            if isinstance(name, dict): name = name.get("full") or "?"
            for td in _as_list(_dig(p, "transaction", "transaction_data") or _dig(p, "transaction_data") or p.get("transaction_data")):
                tkey = td.get("team_key") or ""
                tid = tkey.split(".")[-1] if tkey else ""
                tm = teams.get(tid, {}).get("team_name", tid or "?")
                actype = (td.get("type") or "").lower()
                if actype == "add":
                    label = "Waiver add" if ttype == "waiver" else "Free agent add"
                elif actype == "drop":
                    label = "Dropped"
                elif actype == "trade":
                    label = "Traded"
                else:
                    label = ttype.capitalize() or "Transaction"
                activity.append({"date": date_str, "team": tm, "action": label, "player": name, "_ts": ts_val})
    activity.sort(key=lambda a: -a["_ts"])
    activity = activity[:RECENT_ACTIVITY_COUNT]
    for a in activity:
        a.pop("_ts", None)

    # draft board
    draft = None
    try:
        results = _as_list(_jload(q.get_league_draft_results()))
        if results:
            picks = []
            for d in results:
                pk = d.get("pick")
                rnd = d.get("round")
                tkey = d.get("team_key") or ""
                tid = tkey.split(".")[-1] if tkey else ""
                pkey = d.get("player_key")
                meta = pmap.get(pkey) or {}
                picks.append({"round": int(rnd or 0), "pick": int(pk or 0), "team_id": tid,
                              "player_key": pkey, "player": meta.get("name") or pkey or "?",
                              "pos": meta.get("pos", ""), "team": meta.get("team", "")})
            rounds = max((p["round"] for p in picks), default=0)
            order = {}
            grid = {}
            for p in picks:
                rid = p["team_id"]
                if rid not in order:
                    order[rid] = teams.get(rid, {}).get("team_name", rid)
                grid[(p["round"], rid)] = {"player": p["player"], "pos": p["pos"], "team": p["team"],
                                           "owner": teams.get(rid, {}).get("team_name", "?")}
            draft = {"rounds": rounds, "teams": len(order), "grid": grid,
                     "order": {rid: order[rid] for rid in sorted(order)}, "picks": picks}
    except Exception:
        draft = None

    draft_grades = fetch_draft_grades(q, draft) if draft else None

    # history
    champions, season_standings, all_time, rivalries, name_by_id, records = fetch_history(q, gkeys, season, HISTORY_START_YEAR)
    hist = {"champions": champions, "season_standings": season_standings,
            "all_time": sorted(all_time.values(), key=lambda e: (-e["win_pct"], -e["pf"])),
            "rivalries": [], "records": records}
    ranked = sorted(rivalries.items(), key=lambda kv: -kv[1]["meetings"])[:8]
    for pair, data in ranked:
        ids = list(pair)
        if len(ids) < 2: continue
        a, b = ids[0], ids[1]
        hist["rivalries"].append({
            "name_a": name_by_id.get(a, f"Team {a}"), "name_b": name_by_id.get(b, f"Team {b}"),
            "meetings": data["meetings"], "wins_a": int(data["wins"].get(a, 0)), "wins_b": int(data["wins"].get(b, 0)),
            "pts_a": round(data["points"].get(a, 0), 1), "pts_b": round(data["points"].get(b, 0), 1)})

    return {
        "league_name": league_name, "platform": "Yahoo", "season": season,
        "current_week": current_week, "standings": standings, "matchups": matchups,
        "power": power, "luck": luck, "prediction": prediction, "activity": activity,
        "draft": draft, "draft_grades": draft_grades, "teams": teams,
        "picture": picture, "history": hist,
    }


def matchup_outlook(fav, dog, gap):
    seed = sum(ord(c) for c in (fav + dog))
    close = [f"This one's a coin flip. {fav} holds the slimmest of edges over {dog}, projected to win by just {gap} points.",
             f"{fav} and {dog} are neck and neck, separated by only {gap} projected points.",
             f"Too close to call. {fav} edges {dog} by {gap} points — one big play could flip it."]
    moderate = [f"{fav} enters as the favorite over {dog}, projected to win by about {gap} points.",
                f"On paper {fav} has the edge, out-projecting {dog} by {gap} points.",
                f"{fav} looks like the safer bet against {dog} this week, favored by roughly {gap} points."]
    blowout = [f"{fav} is projected to run away with it, out-scoring {dog} by a lopsided {gap} points.",
               f"This has blowout potential — {fav} is favored by {gap} points over {dog}.",
               f"The numbers aren't kind to {dog}, with {fav} projected to win by {gap} points."]
    pool = close if gap < 8 else (moderate if gap < 20 else blowout)
    return pool[seed % len(pool)]


def fetch_draft_grades(q, draft):
    """
    For each drafted player: compares their positional draft rank (e.g.
    'the 5th RB taken', walked in actual draft order) against their
    positional rank by total fantasy points scored so far this season.
    Season totals come from one get_player_stats_for_season call per
    unique player — slow, but correct. Any player whose stats call fails
    is ranked last within their position rather than aborting the report.
    """
    if not draft or not draft.get("picks"):
        return None
    picks = sorted(draft["picks"], key=lambda p: (p["round"], p["pick"]))

    season_pts = {}
    for p in picks:
        pkey = p.get("player_key")
        if not pkey or pkey in season_pts:
            continue
        try:
            stats = _jload(q.get_player_stats_for_season(pkey))
            pts = _dig(stats, "player_points", "total")
            season_pts[pkey] = float(pts) if pts is not None else None
        except Exception:
            season_pts[pkey] = None

    pos_players = {}
    for p in picks:
        pos = p.get("pos") or "?"
        pos_players.setdefault(pos, []).append((p["player_key"], season_pts.get(p["player_key"])))

    current_pos_rank = {}
    for pos, plist in pos_players.items():
        ranked = sorted(plist, key=lambda x: -(x[1] if x[1] is not None else -1))
        for i, (pkey, _) in enumerate(ranked, start=1):
            current_pos_rank[pkey] = i

    position_counters, rank_data = {}, {}
    for p in picks:
        pkey = p["player_key"]
        pos = p.get("pos") or "?"
        position_counters[pos] = position_counters.get(pos, 0) + 1
        draft_pos_rank = position_counters[pos]
        cur_rank = current_pos_rank.get(pkey)
        rank_data[pkey] = {
            "player": p.get("player", pkey), "pos": pos, "team_id": p.get("team_id"),
            "draft_pos_rank": draft_pos_rank, "current_pos_rank": cur_rank,
            "delta": (draft_pos_rank - cur_rank) if cur_rank else None,
        }
    return rank_data


# --------------------------------------------------------------------------- #
# RENDERER (identical across platforms — only consumes `model`)
# --------------------------------------------------------------------------- #

def esc(s):
    return html.escape("" if s is None else str(s))


def team_cell(name, owner=None, logo=None):
    logo_html = f"<img src='{esc(logo)}' class='logo'>" if logo else ""
    owner_html = f"<div class='owner-name'>{esc(owner)}</div>" if owner else ""
    return f"<div class='team-cell-inner'>{logo_html}<div><div class='team-name-main'>{esc(name)}</div>{owner_html}</div></div>"


CSS = """
:root{
  --bg:#140f22;
  --panel:#1c1530;
  --panel-alt:#241b3d;
  --border:#382c54;
  --border-soft:#291f45;
  --accent:#9333ea;
  --accent-light:#c084fc;
  --text:#efeaf9;
  --text-dim:#a89bd1;
  --text-dimmer:#79699f;
  --good:#4ade80;
  --bad:#f87171;
  --warn:#fbbf24;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);padding:24px}
h1{font-size:1.6rem;margin-bottom:4px}
.subtitle{color:var(--text-dim);margin-bottom:20px;font-size:.95rem}
.tabs{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:18px}
.tab{background:var(--panel);border:1px solid var(--border);color:var(--text-dim);padding:9px 16px;border-radius:8px;cursor:pointer;font-size:.9rem}
.tab.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.panel{display:none;background:var(--panel-alt);border:1px solid var(--border);border-radius:12px;padding:20px}
.panel.active{display:block}
.section-title{font-size:1.15rem;margin-bottom:12px;color:var(--text)}
.section-note{color:var(--text-dim);font-size:.85rem;margin-bottom:14px}
table{width:100%;border-collapse:collapse;font-size:.88rem}
th{text-align:left;color:var(--text-dim);padding:8px 10px;border-bottom:1px solid var(--border);font-weight:600}
td{padding:9px 10px;border-bottom:1px solid var(--border-soft)}
.team-cell-inner{display:flex;align-items:center;gap:10px}
.logo{width:30px;height:30px;border-radius:50%;object-fit:cover}
.team-name-main{font-weight:600;color:var(--text)}
.owner-name{font-size:.78rem;color:var(--text-dimmer)}
.matchup-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.matchup-card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:14px}
.matchup-teams{display:flex;justify-content:space-between;align-items:center;gap:8px}
.matchup-team{text-align:center;flex:1}
.team-record{color:var(--text-dim);font-size:.8rem}
.proj-score{font-size:1.3rem;font-weight:700;color:var(--accent-light)}
.vs{color:var(--text-dimmer);font-weight:700}
.outlook{margin-top:10px;font-size:.8rem;color:var(--text-dim);line-height:1.4}
.rivalry-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}
.rivalry-card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:14px}
.rivalry-meetings{color:var(--text-dim);font-size:.78rem;margin-bottom:8px}
.empty{color:var(--text-dimmer);font-style:italic;padding:14px}
.luck-good{color:var(--good)}.luck-bad{color:var(--bad)}
.action{color:var(--text-dim)}
.subtabs{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:14px}
.subtab{background:var(--panel);border:1px solid var(--border);color:var(--text-dim);padding:6px 12px;border-radius:6px;cursor:pointer;font-size:.82rem}
.subtab.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.subpanel{display:none}.subpanel.active{display:block}
.draft-grid{overflow-x:auto}
.draft-grid table{font-size:.78rem}
.draft-grid td,.draft-grid th{border:1px solid var(--border-soft);padding:5px 6px;min-width:90px;vertical-align:top}
.pbar{display:flex;align-items:center;gap:6px}
.pbar-track{flex:1;height:6px;background:var(--border);border-radius:3px;overflow:hidden;min-width:50px}
.pbar-fill{height:100%;border-radius:3px}
.pbar-label{font-size:.75rem;color:var(--text-dim);min-width:32px}
.sparkline{display:block}
.playoff-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px}
.playoff-col-title{font-size:.95rem;color:var(--text-dim);margin-bottom:10px}
.playoff-row{display:grid;grid-template-columns:28px 1fr 70px 110px 100px;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid var(--border-soft)}
.playoff-seed{color:var(--text-dim);font-weight:600}
.playoff-record{color:var(--text-dim);font-size:.85rem}
.playoff-detail{color:var(--text-dimmer);font-size:.78rem}
.playoff-badge{font-size:.72rem;padding:3px 8px;border-radius:12px;text-align:center;font-weight:600}
.badge-clinched{background:rgba(74,222,128,.18);color:var(--good)}
.badge-hunt{background:rgba(147,51,234,.22);color:var(--accent-light)}
.badge-eliminated{background:rgba(248,113,113,.18);color:var(--bad)}
.badge-bubble{background:rgba(251,191,36,.18);color:var(--warn)}
.draft-grade-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}
.draft-grade-card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:14px}
.draft-grade-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.draft-grade-letter{font-size:1.4rem;font-weight:700;color:var(--accent-light)}
.draft-grade-note{color:var(--text-dim);font-size:.78rem;margin-bottom:10px}
.draft-chip-grid{display:flex;flex-wrap:wrap;gap:6px}
.draft-chip{border:1px solid;border-radius:6px;padding:4px 8px;font-size:.72rem;display:flex;gap:6px;align-items:center}
.dc-player{font-weight:600;color:var(--text)}
.dc-pos{color:var(--text-dim)}
.dc-delta{font-weight:700}
.record-label{color:var(--text-dim);font-weight:600;width:260px}
.site-footer{margin-top:24px;text-align:center;color:var(--text-dimmer);font-size:.78rem;padding:16px 0}
"""

JS = """
function showTab(id,btn){document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));btn.classList.add('active');document.getElementById(id).classList.add('active');}
function showSubTab(id,btn){btn.parentNode.querySelectorAll('.subtab').forEach(t=>t.classList.remove('active'));btn.parentNode.parentNode.querySelectorAll('.subpanel').forEach(p=>p.classList.remove('active'));btn.classList.add('active');document.getElementById(id).classList.add('active');}
"""


def progress_bar_html(pct, color="var(--accent)"):
    pct = max(0.0, min(100.0, pct))
    return (f"<div class='pbar'><div class='pbar-track'>"
            f"<div class='pbar-fill' style='width:{pct:.1f}%; background:{color};'></div>"
            f"</div><span class='pbar-label'>{pct:.0f}%</span></div>")


def sparkline_svg(values, width=90, height=26, color="var(--accent-light)"):
    """Compact inline SVG line chart for a season's weekly scores — no
    charting library needed, just a hand-built polyline scaled to fit."""
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1
    pad = 3
    usable_h = height - pad * 2
    step = width / (len(vals) - 1)
    points = [(i * step, pad + usable_h - ((v - lo) / span) * usable_h) for i, v in enumerate(vals)]
    path = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    last_x, last_y = points[-1]
    return (f"<svg class='sparkline' viewBox='0 0 {width} {height}' preserveAspectRatio='none' aria-hidden='true'>"
            f"<polyline points='{path}' style='fill:none; stroke:{color}; stroke-width:1.6; "
            f"stroke-linecap:round; stroke-linejoin:round;'/>"
            f"<circle cx='{last_x:.1f}' cy='{last_y:.1f}' r='2.2' style='fill:{color};'/></svg>")


def standings_table(rows, with_pa=True, with_streak=True, with_trend=False):
    head = "<tr><th>#</th><th>Team</th><th>Record</th><th>Win%</th><th>PF</th>"
    if with_pa: head += "<th>PA</th>"
    if with_streak: head += "<th>Streak</th>"
    if with_trend: head += "<th>Trend</th>"
    head += "</tr>"
    body = []
    for r in rows:
        rec = f"{r['wins']}-{r['losses']}" + (f"-{r['ties']}" if r.get('ties') else "")
        games = r['wins'] + r['losses'] + r.get('ties', 0)
        win_pct = ((r['wins'] + 0.5 * r.get('ties', 0)) / games * 100) if games else 0
        cells = f"<td>{r['rank']}</td><td class='team-cell'>{team_cell(r['name'], r.get('owner'), r.get('logo'))}</td><td>{rec}</td><td>{progress_bar_html(win_pct)}</td><td>{r['pf']:.1f}</td>"
        if with_pa: cells += f"<td>{r['pa']:.1f}</td>"
        if with_streak: cells += f"<td>{esc(r.get('streak','-'))}</td>"
        if with_trend: cells += f"<td>{sparkline_svg(r.get('trend', []))}</td>"
        body.append(f"<tr>{cells}</tr>")
    return f"<table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table>"


def render_matchup_previews(mup):
    """Pre-game view: always shows projected scores, regardless of kickoff."""
    if not mup:
        return "<p class='empty'>No matchup data available for this week yet.</p>"
    cards = []
    for m in mup:
        away, home = m['away'], m['home']
        cards.append(f"""<div class='matchup-card'><div class='matchup-teams'>
<div class='matchup-team'><div class='team-name-main'>{esc(away['name'])}</div><div class='team-record'>{esc(away['record'])}</div><div class='proj-score'>{away['proj']:.1f}</div></div>
<div class='vs'>@</div>
<div class='matchup-team'><div class='team-name-main'>{esc(home['name'])}</div><div class='team-record'>{esc(home['record'])}</div><div class='proj-score'>{home['proj']:.1f}</div></div>
</div><p class='outlook'>{esc(m['outlook'])}</p></div>""")
    return f"<p class='section-note'>Pre-game expectations from Yahoo's live projection model.</p><div class='matchup-grid'>{''.join(cards)}</div>"


def render_weekly_scores(mup):
    """Actual/live scores. Shows a placeholder until any game has real points."""
    if not mup:
        return "<p class='empty'>No matchup data available for this week yet.</p>"
    if not any((m['away']['pts'] is not None or m['home']['pts'] is not None) for m in mup):
        return "<p class='empty'>This week's games haven't started yet — check back once kickoff hits.</p>"
    cards = []
    for m in mup:
        away, home = m['away'], m['home']
        started = away['pts'] is not None or home['pts'] is not None
        away_score = round(away['pts'], 1) if away['pts'] is not None else 0.0
        home_score = round(home['pts'], 1) if home['pts'] is not None else 0.0
        away_ahead = started and away_score > home_score
        home_ahead = started and home_score > away_score
        cards.append(f"""<div class='matchup-card'><div class='matchup-teams'>
<div class='matchup-team'><div class='team-name-main'>{esc(away['name'])}</div><div class='team-record'>{esc(away['record'])}</div><div class='proj-score{" luck-good" if away_ahead else ""}'>{away_score if started else '—'}</div></div>
<div class='vs'>@</div>
<div class='matchup-team'><div class='team-name-main'>{esc(home['name'])}</div><div class='team-record'>{esc(home['record'])}</div><div class='proj-score{" luck-good" if home_ahead else ""}'>{home_score if started else '—'}</div></div>
</div><p class='outlook'>{'In progress / final' if started else 'Not yet started'}</p></div>""")
    return f"<div class='matchup-grid'>{''.join(cards)}</div>"


def render_matchups_panel(mup):
    sub_nav = ("<button class='subtab active' onclick=\"showSubTab('mu-preview', this)\">Matchup Previews</button>"
               "<button class='subtab' onclick=\"showSubTab('mu-scores', this)\">This Week's Scores</button>")
    return (f"<div class='subtabs'>{sub_nav}</div>"
            f"<div id='mu-preview' class='subpanel active'>{render_matchup_previews(mup)}</div>"
            f"<div id='mu-scores' class='subpanel'>{render_weekly_scores(mup)}</div>")


def delta_color(delta, dead_zone=3, max_delta=15):
    """Green the more a player's risen vs. draft slot, red the more
    they've fallen. Moves within dead_zone are treated as noise."""
    if delta is None or abs(delta) <= dead_zone:
        return "#2a3348", "#8a92a8"
    span = max(max_delta - dead_zone, 1)
    magnitude = min(abs(delta) - dead_zone, span) / span
    intensity = 0.25 + 0.65 * magnitude
    r, g, b = (34, 139, 34) if delta > 0 else (178, 34, 34)
    return f"rgba({r},{g},{b},{intensity:.2f})", f"rgba({r},{g},{b},{min(intensity + 0.25, 1):.2f})"


def render_power(power):
    if not power: return "<p class='empty'>No completed weeks yet — power rankings need game data.</p>"
    rows = []
    for p in power:
        d = p['delta']
        dcls = "luck-good" if d > 0 else ("luck-bad" if d < 0 else "")
        rows.append(f"<tr><td>{p['rank']}</td><td class='team-cell'>{team_cell(p['name'], p.get('owner'))}</td><td>{p['score']}</td><td>{p['avg_pts']:.1f}</td><td>{p['avg_margin']:+.1f}</td><td class='{dcls}'>{d:+d}</td></tr>")
    return f"<table><thead><tr><th>#</th><th>Team</th><th>Score</th><th>Avg PF</th><th>Avg Margin</th><th>vs Standings</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"


def render_playoff_picture(picture):
    if not picture:
        return "<p class='empty'>Playoff settings unavailable for this league.</p>"

    def row(e, badge_class):
        t = e["team"]
        record = f"{e['wins']}-{e['losses']}" + (f"-{e['ties']}" if e['ties'] else "")
        detail = "Season complete" if e["remaining"] == 0 else f"{e['remaining']} games left"
        return f"""<div class="playoff-row">
<div class="playoff-seed">{e['seed']}</div>
<div class="team-cell">{team_cell(t['team_name'], t.get('owner'))}</div>
<div class="playoff-record">{record}</div>
<div class="playoff-detail">{esc(detail)}</div>
<div class="playoff-badge {badge_class}">{esc(e['status'])}</div>
</div>"""

    in_rows = "".join(row(e, "badge-clinched" if e["clinched"] else "badge-hunt") for e in picture["in_the_hunt"])
    out_rows = "".join(row(e, "badge-eliminated" if e["eliminated"] else "badge-bubble") for e in picture["outside"]) \
        or "<p class='empty'>Every team in the league makes the playoffs.</p>"
    note = ("The regular season has wrapped — this reflects the final playoff field."
            if picture["season_over"] else "Updates automatically as more of the regular season completes.")
    return f"""<p class="section-note">Top {picture['playoff_spots']} make the playoffs. {note}</p>
<div class="playoff-grid">
<div class="playoff-col"><h3 class="playoff-col-title">In the Playoffs</h3>{in_rows}</div>
<div class="playoff-col"><h3 class="playoff-col-title">On the Outside</h3>{out_rows}</div>
</div>"""


def render_power_panel(power, picture):
    sub_nav = ("<button class='subtab active' onclick=\"showSubTab('pow-rankings', this)\">Power Rankings</button>"
               "<button class='subtab' onclick=\"showSubTab('pow-playoff', this)\">Playoff Picture</button>")
    return (f"<div class='subtabs'>{sub_nav}</div>"
            f"<div id='pow-rankings' class='subpanel active'>{render_power(power)}</div>"
            f"<div id='pow-playoff' class='subpanel'>{render_playoff_picture(picture)}</div>")


def render_luck(luck):
    if not luck: return "<p class='empty'>No completed weeks yet — luck index needs game data.</p>"
    rows = []
    for l in luck:
        cls = "luck-good" if l['luck'] > 0 else ("luck-bad" if l['luck'] < 0 else "")
        rows.append(f"<tr><td class='team-cell'>{team_cell(l['name'], l.get('owner'))}</td><td>{l['actual']}%</td><td>{l['expected']}%</td><td class='{cls}'>{l['luck']:+.1f}%</td><td>{esc(l['label'])}</td></tr>")
    return f"<table><thead><tr><th>Team</th><th>Actual Win%</th><th>Expected (All-Play)</th><th>Luck</th><th>Verdict</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"


def render_prediction(pred):
    if not pred: return "<p class='empty'>No completed weeks with projection data yet this season.</p>"
    rows = []
    for p in pred:
        cls = "luck-good" if p['avg_diff'] > 0 else ("luck-bad" if p['avg_diff'] < 0 else "")
        rows.append(f"<tr><td class='team-cell'>{team_cell(p['name'], p.get('owner'))}</td><td>{p['played']}</td><td>{p['beat_pct']}%</td><td class='{cls}'>{p['avg_diff']:+.1f} pts/wk</td></tr>")
    return f"<table><thead><tr><th>Team</th><th>Weeks</th><th>Beat Projection</th><th>Avg vs. Projection</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"


def render_activity(act):
    if not act: return "<p class='empty'>No recent activity found.</p>"
    rows = [f"<tr><td>{esc(a['date'])}</td><td>{esc(a['team'])}</td><td class='action'>{esc(a['action'])}</td><td>{esc(a['player'])}</td></tr>" for a in act]
    return f"<table><thead><tr><th>Date</th><th>Team</th><th>Action</th><th>Player</th></tr></thead><tbody>{''.join(rows)}</tbody></table>"


def render_draft(draft):
    if not draft: return "<p class='empty'>No draft data available yet.</p>"
    rounds = draft['rounds']; order = draft['order']; grid = draft['grid']
    slots = sorted(order)
    head = "<tr><th>Round</th>" + "".join(f"<th>{esc(order[s])}</th>" for s in slots) + "</tr>"
    body = []
    for rnd in range(1, rounds + 1):
        cells = f"<td><b>{rnd}</b></td>"
        for s in slots:
            p = grid.get((rnd, s))
            if p:
                cells += f"<td><div class='team-name-main'>{esc(p['player'])}</div><div class='owner-name'>{esc(p['pos'])} · {esc(p['team'])}</div></td>"
            else:
                cells += "<td></td>"
        body.append(f"<tr>{cells}</tr>")
    return f"<div class='draft-grid'><table><thead>{head}</thead><tbody>{''.join(body)}</tbody></table></div>"


def render_draft_grades(grades, draft, teams):
    if not grades or not draft:
        return "<p class='empty'>Draft grades aren't available yet — check back once enough games have been played to rank players.</p>"

    by_team = {}
    for g in grades.values():
        by_team.setdefault(g["team_id"], []).append(g)

    def letter_grade(avg):
        if avg is None: return "—"
        if avg >= 6: return "A"
        if avg >= 3: return "B"
        if avg >= -3: return "C"
        if avg >= -6: return "D"
        return "F"

    summaries = []
    for tid, picks in by_team.items():
        deltas = [p["delta"] for p in picks if p["delta"] is not None]
        avg = sum(deltas) / len(deltas) if deltas else None
        summaries.append({
            "name": teams.get(tid, {}).get("team_name", tid),
            "owner": teams.get(tid, {}).get("owner"),
            "avg_delta": round(avg, 1) if avg is not None else None,
            "grade": letter_grade(avg),
            "picks": sorted(picks, key=lambda p: (p["pos"], p["draft_pos_rank"])),
        })
    summaries.sort(key=lambda t: (t["avg_delta"] is None, -(t["avg_delta"] or 0)))

    cards = []
    for t in summaries:
        chips = []
        for p in t["picks"]:
            bg, border = delta_color(p["delta"])
            delta_txt = f"{p['delta']:+d}" if p["delta"] is not None else "—"
            chips.append(f"<div class='draft-chip' style='background:{bg};border-color:{border};'>"
                          f"<span class='dc-player'>{esc(p['player'])}</span>"
                          f"<span class='dc-pos'>{esc(p['pos'])}{p['draft_pos_rank']}</span>"
                          f"<span class='dc-delta'>{delta_txt}</span></div>")
        avg_txt = f"{t['avg_delta']:+.1f}" if t["avg_delta"] is not None else "—"
        cards.append(f"""<div class='draft-grade-card'>
<div class='draft-grade-head'><div class='team-cell'>{team_cell(t['name'], t.get('owner'))}</div><div class='draft-grade-letter'>{t['grade']}</div></div>
<div class='draft-grade-note'>Avg positional rank shift: {avg_txt}</div>
<div class='draft-chip-grid'>{''.join(chips)}</div></div>""")

    return ("<p class='section-note'>Each pick's positional draft slot (e.g. the 5th RB taken) vs. their "
            "positional rank by points scored so far. Green = outperforming draft slot, red = underperforming; "
            "moves within ±3 spots count as noise.</p>"
            f"<div class='draft-grade-grid'>{''.join(cards)}</div>")


def render_records_book(records):
    if not records:
        return ""
    rows = []
    hs, ls = records.get("highest_score"), records.get("lowest_score")
    if hs: rows.append(("Highest single-week score", f"{esc(hs['team'])} — {hs['score']} pts (Wk {hs['week']}, {hs['year']})"))
    if ls: rows.append(("Lowest single-week score", f"{esc(ls['team'])} — {ls['score']} pts (Wk {ls['week']}, {ls['year']})"))
    bb = records.get("biggest_blowout")
    if bb: rows.append(("Biggest blowout", f"{esc(bb['winner'])} over {esc(bb['loser'])} by {bb['margin']} (Wk {bb['week']}, {bb['year']})"))
    cg = records.get("closest_game")
    if cg: rows.append(("Closest game", f"{esc(cg['team_a'])} vs {esc(cg['team_b'])} — decided by {cg['margin']} (Wk {cg['week']}, {cg['year']})"))
    mpl = records.get("most_points_loss")
    if mpl: rows.append(("Most points scored in a loss", f"{esc(mpl['team'])} — {mpl['score']} pts (Wk {mpl['week']}, {mpl['year']})"))
    lws = records.get("longest_win_streak")
    if lws: rows.append(("Longest win streak", f"{esc(lws['team'])} — {lws['length']} games ({lws['start_year']} Wk{lws['start_week']}–{lws['end_year']} Wk{lws['end_week']})"))
    lls = records.get("longest_loss_streak")
    if lls: rows.append(("Longest losing streak", f"{esc(lls['team'])} — {lls['length']} games ({lls['start_year']} Wk{lls['start_week']}–{lls['end_year']} Wk{lls['end_week']})"))
    if not rows:
        return ""
    trs = "".join(f"<tr><td class='record-label'>{esc(label)}</td><td>{value}</td></tr>" for label, value in rows)
    return f"<h2 class='section-title' style='margin-top:24px'>League Records</h2><table><tbody>{trs}</tbody></table>"


def render_history(hist, current_season):
    champs = hist['champions']
    if champs:
        crows = [f"<tr><td>{c['year']}</td><td class='team-cell'>{team_cell(c['name'], c.get('owner'))}</td></tr>" for c in champs]
        champ_html = f"<h2 class='section-title'>League Champions</h2><table><thead><tr><th>Season</th><th>Champion</th></tr></thead><tbody>{''.join(crows)}</tbody></table>"
    else:
        champ_html = "<h2 class='section-title'>League Champions</h2><p class='section-note'>No completed prior seasons found yet.</p>"

    sub_nav, panels = [], []
    seasons = sorted(hist['season_standings'].keys(), reverse=True)
    for i, year in enumerate(seasons):
        tab_id = f"std-{year}"
        sub_nav.append(f"<button class='subtab{' active' if i==0 else ''}' onclick=\"showSubTab('{tab_id}',this)\">{year}</button>")
        with_pa = (year != current_season)
        panels.append(f"<div id='{tab_id}' class='subpanel{' active' if i==0 else ''}'>{standings_table(hist['season_standings'][year], with_pa=with_pa, with_streak=False)}</div>")
    seasons_html = ""
    if seasons:
        seasons_html = f"<h2 class='section-title' style='margin-top:24px'>Season Standings</h2><div class='subtabs'>{''.join(sub_nav)}</div>{''.join(panels)}"

    at_html = ""
    if hist['all_time']:
        at_rows = []
        for i, e in enumerate(hist['all_time'], 1):
            rec = f"{e['wins']}-{e['losses']}" + (f"-{e['ties']}" if e.get('ties') else "")
            at_rows.append(f"<tr><td>{i}</td><td class='team-cell'>{team_cell(e['name'], e.get('owner'))}</td><td>{rec}</td><td>{e['win_pct']*100:.1f}%</td><td>{e['pf']:.1f}</td><td>{e['seasons']}</td></tr>")
        at_html = f"<h2 class='section-title' style='margin-top:24px'>All-Time</h2><table><thead><tr><th>#</th><th>Team</th><th>Record</th><th>Win%</th><th>Total PF</th><th>Seasons</th></tr></thead><tbody>{''.join(at_rows)}</tbody></table>"

    riv_html = ""
    if hist['rivalries']:
        cards = []
        for r in hist['rivalries']:
            cards.append(f"""<div class='rivalry-card'><div class='rivalry-meetings'>{r['meetings']} all-time meetings</div>
<div class='matchup-teams'><div class='matchup-team'><div class='team-name-main'>{esc(r['name_a'])}</div><div class='team-record'>{r['wins_a']}-{r['wins_b']}</div><div class='owner-name'>{r['pts_a']} pts</div></div>
<div class='vs'>vs</div>
<div class='matchup-team'><div class='team-name-main'>{esc(r['name_b'])}</div><div class='team-record'>{r['wins_b']}-{r['wins_a']}</div><div class='owner-name'>{r['pts_b']} pts</div></div></div></div>""")
        riv_html = f"<h2 class='section-title' style='margin-top:24px'>Rivalry Tracker</h2><p class='section-note'>All-time head-to-head across every season fetched.</p><div class='rivalry-grid'>{''.join(cards)}</div>"

    return champ_html + seasons_html + at_html + riv_html + render_records_book(hist.get('records'))


def render(model):
    panels = [
        ("standings", "Standings", standings_table(model['standings'], with_trend=True)),
        ("matchups", f"Matchups (Week {model['current_week']})", render_matchups_panel(model['matchups'])),
        ("power", "Power Rankings", render_power_panel(model['power'], model['picture'])),
        ("luck", "Luck Index", render_luck(model['luck'])),
        ("prediction", "Prediction Accuracy", render_prediction(model['prediction'])),
        ("activity", "Recent Activity", render_activity(model['activity'])),
        ("draft", "Draft Board", render_draft(model['draft'])),
        ("draftgrades", "Draft Report Card", render_draft_grades(model.get('draft_grades'), model.get('draft'), model.get('teams', {}))),
        ("history", "History", render_history(model['history'], model['season'])),
    ]
    tabs = "".join(f"<button class='tab{' active' if i==0 else ''}' onclick=\"showTab('{pid}',this)\">{label}</button>" for i, (pid, label, _) in enumerate(panels))
    body = "".join(f"<div id='{pid}' class='panel{' active' if i==0 else ''}'>{content}</div>" for i, (pid, _, content) in enumerate(panels))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(model['league_name'])} · {esc(model['platform'])} Dashboard</title><style>{CSS}</style></head>
<body><h1>{esc(model['league_name'])}</h1><div class="subtitle">{esc(model['platform'])} Fantasy Football · {model['season']} season · auto-updated daily</div>
<div class="tabs">{tabs}</div>{body}<footer class="site-footer">Fantasy data provided by Yahoo Fantasy</footer><script>{JS}</script></body></html>"""


def main():
    if not LEAGUE_ID:
        raise SystemExit("Set the LEAGUE_ID env var (your Yahoo numeric league id).")
    if not CONSUMER_KEY or not CONSUMER_SECRET:
        raise SystemExit("Set YFPY_CONSUMER_KEY and YFPY_CONSUMER_SECRET (your Yahoo app credentials).")
    model = build_model()
    out = render(model)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"Wrote {OUTPUT_FILE} ({len(out)} bytes) for {model['league_name']}")


if __name__ == "__main__":
    main()