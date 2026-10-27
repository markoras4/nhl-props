"""All tunable model settings in one place.

Change a value, run `python -m nhlprops build` again, and after a few weeks
run `python -m nhlprops grade` to see whether the change made the model better.
"""

CONFIG = {
    # ---------- history windows ----------
    # How many seasons (including the current one) feed the player rates.
    # 3 or 5 - test both with the grade command.
    "lookback_seasons": 5,
    # Marcel-style weights: current season, last season, 2 seasons ago, ...
    # Weights apply to totals, so a half-finished current season counts less on its own.
    "season_weights": [5, 4, 3, 2, 1],
    # League scoring environment: seasons used as the "normal" level.
    "league_env_seasons": 5,
    # Goalie history window (current + previous seasons).
    "goalie_seasons": 4,
    "goalie_weights": [5, 3, 2, 1],   # backtest_goalies: this season 5, then 3 / 2 / 1 for the previous seasons

    # ---------- regression to the mean (shrinkage strength) ----------
    # Bigger number = pulled harder toward the average.
    # Units: hours of ice time (skater rates), shots (shooting %), expected goals.
    "k_shots_hours": 4.0,        # shots per 60 stabilises quickly
    "k_ixg_hours": 8.0,          # individual xG per 60
    "k_assist_hours": 14.0,      # assists are noisy
    "k_career_sh_shots": 350,    # career shooting % pulled toward position average
    "k_recent_sh_shots": 200,    # recent shooting % pulled toward the player's career level
    "k_finish_xg": 40,           # goals / xG finishing skill, pulled toward 1.0
    "k_goalie_xga": 200,         # goalie GA/xGA pulled toward average; backtest_goalies best ~1,000 in raw weight units = 200 with weights scaled to 1 (was 120, worse than no rating at all)
    "k_team_hours": 8.0,         # team 5v5 xG rates
    "k_team_pp_hours": 3.0,      # team power play / penalty kill rates
    "team_prior_season_weight": 0.35,

    # Blend of the two goal-rate estimates: shots x shooting%  vs  ixG x finishing.
    "alpha_shots_vs_xg": 0.5,

    # ---------- time on ice ----------
    "toi_recent_games": 10,
    "toi_recent_weight": 0.6,

    # ---------- game context ----------
    "home_adv": 1.03,            # home scoring multiplier (away gets 1/x)
    "b2b_factor": 0.97,          # team playing the 2nd night of a back-to-back
    # backtest_context (2,451 games): away team on a back-to-back lost 0.33 goals of margin vs the model (t = 2.9),
    # home team only about 0.10; travel distance and time zones showed no effect
    "b2b_factor_away": 0.945,
    "b2b_factor_home": 0.984,
    "env_cap": 0.05,             # max +/- adjustment for league scoring environment
    "k_env_team_games": 300,     # current-season league scoring regressed with this many team-games

    # ---------- 6-game streak rule ----------
    # Thresholds by team strength (points % blended current/last season).
    "streak_strong_pct": 0.600,
    "streak_weak_pct": 0.450,
    "streak_thresholds": {
        "strong": {"win": 8, "loss": 4},
        "average": {"win": 6, "loss": 6},
        "weak": {"win": 4, "loss": 8},
    },
    # Effect per game beyond threshold. 0 = flag only (default). Adjustable in the dashboard.
    "streak_weight_default": 0.0,

    # ---------- over/under ----------
    "ou_lines": [5.5, 6.5],
    # Totals backtest 2021-2026 (6,128 games): a 40% blend of over/under history was best.
    "history_weight_default": 0.4,
    # Same backtest: independent Poisson gave 16% regulation ties vs 22% real; x1.5 on the diagonal matches.
    "tie_boost": 1.5,
    # Same backtest: projected totals were too spread out (slope 0.62); pull them part way to the league average.
    "total_compression": 0.85,
    "k_team_ou_games": 15,
    "k_h2h_games": 6,

    # ---------- slate ----------
    "days_ahead": 6,                 # upcoming games: today + this many days

    # ---------- props ----------
    "sog_lines": [1.5, 2.5, 3.5, 4.5],
    # hits / blocked shots (backtest_hb: NB r=10; hits regress less, k=3 h beat 6 h on Brier)
    "hit_lines": [0.5, 1.5, 2.5, 3.5],
    "blk_lines": [0.5, 1.5, 2.5],
    "hb_dispersion": 10.0,
    "k_hb_hours": {"hit": 3.0, "blk": 6.0},
    # team shots on goal and goalie saves (backtest_shots)
    "p1_share": 0.29,              # backtest_markets: 1st-period share of regulation goals (29.5% actual)
    "en_q": 0.35,                  # backtest_markets: a one-goal lead becomes two via the empty net 35% of the time (puck line)
    "prior_toi_beta": 1.0,   # backtest_depth: rookies / depth players pulled toward players with their ice time (0 = position average)
    "form_k": {"g": 40.0, "a": 40.0, "s": 80.0},   # backtest_form: recent-form cushion per market (bigger = less weight on the last 10 games)
    "btts2_cal": 0.963,            # backtest_markets: both teams 2+ goals ran 64.7% vs 62.3%
    # backtest_extra (85,709 player-games): PP points K=20 games + opponent penalties^0.5; penalties NB r=4, rate x0.9,
    # PIM over 2.5 x1.3 (majors / double minors); faceoffs NB r=10 with opponent faceoff %
    "ppp_k": 20.0, "ppp_opp_beta": 0.5,
    "pen_r": 4.0, "pen_mult": 0.9, "pim25_cal": 1.3,
    "fo_r": 10.0, "fo_lines": [4.5, 6.5, 8.5, 10.5],
    "ot_no_goal": 0.40,            # share of tied games that reach a shootout (no overtime goal)
    "shots_k_games": 15,
    "shots_home_adv": 1.018,
    "shots_calibration": 0.99,     # backtest ran ~1% high for team shots and saves
    "shots_dispersion": 60.0,      # backtest: NB r=60 for one team
    "total_shots_dispersion": None,
    "saves_dispersion": 25.0,      # wider: early pulls
    "saves_play_share": 0.965,
    "team_shot_lines": [25.5, 27.5, 29.5, 31.5, 33.5],
    "total_shot_lines": [55.5, 57.5, 59.5, 61.5, 63.5],
    "save_lines": [22.5, 24.5, 26.5, 28.5, 30.5],
    # Backtest 2021-2026 (213,885 player-games): expected shots ran ~6% high. Goals/assists/points were on target.
    "sog_calibration": 0.94,
    "active_last_n_team_games": 2,   # skater must have played in one of team's last N games

    # ---------- regression flag ----------
    # A player is flagged when this season's goals are this unlikely for his true talent
    # (Poisson tail probability), and he has at least `regression_min_shots` shots.
    "regression_tail": 0.15,
    "regression_min_shots": 15,

    # ---------- network ----------
    "request_interval_s": 0.12,
    "workers": 6,
}
