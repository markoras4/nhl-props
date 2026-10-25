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
    "goalie_weights": [3, 3, 2, 1],

    # ---------- regression to the mean (shrinkage strength) ----------
    # Bigger number = pulled harder toward the average.
    # Units: hours of ice time (skater rates), shots (shooting %), expected goals.
    "k_shots_hours": 4.0,        # shots per 60 stabilises quickly
    "k_ixg_hours": 8.0,          # individual xG per 60
    "k_assist_hours": 14.0,      # assists are noisy
    "k_career_sh_shots": 350,    # career shooting % pulled toward position average
    "k_recent_sh_shots": 200,    # recent shooting % pulled toward the player's career level
    "k_finish_xg": 40,           # goals / xG finishing skill, pulled toward 1.0
    "k_goalie_xga": 120,         # goalie GA/xGA, pulled toward 1.0 (average)
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
    "history_weight_default": 0.0,   # blend of historical O/U rates into model P(over); grade tells you the best value
    "k_team_ou_games": 15,
    "k_h2h_games": 6,

    # ---------- slate ----------
    "days_ahead": 6,                 # upcoming games: today + this many days

    # ---------- props ----------
    "sog_lines": [1.5, 2.5, 3.5, 4.5],
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
