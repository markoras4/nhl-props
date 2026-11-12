# Streak bonus + depth correction: fit on [2022], tested on the other seasons of [2022, 2023, 2024]
Player-games: fit 42,747 · test 85,788 (live depth_cal and form_k already applied)

Fit season, sum of Brier (goal + assist + point): now 0.49922 · streak only (b=0.08, S=3, c=0.00) 0.49911 · depth only (b=0.00, S=3, c=0.02) 0.49921 · both (b=0.08, S=3, c=0.02) 0.49910

## Test seasons (never used for fitting)
| Version | Goal Brier | Goal skill | Assist Brier | Assist skill | Point Brier | Point skill |
|---|---|---|---|---|---|---|
| now (b=0.00, S=3, c=0.00) | 0.11991 | +7.12% | 0.16967 | +7.32% | 0.20317 | +10.50% |
| streak only (b=0.08, S=3, c=0.00) | 0.11993 | +7.11% | 0.16968 | +7.32% | 0.20317 | +10.49% |
| depth only (b=0.00, S=3, c=0.02) | 0.11990 | +7.13% | 0.16965 | +7.33% | 0.20312 | +10.52% |
| both (b=0.08, S=3, c=0.02) | 0.11992 | +7.12% | 0.16966 | +7.33% | 0.20313 | +10.51% |

## Point streaks on the test seasons: now
| Level | Going into the game | n | Predicted | Happened | Diff | t |
|---|---|---|---|---|---|---|
| below average (point chance < 36%) | no point last game | 37,460 | 23.8% | 22.6% | -1.1 | -5.2 |
| below average (point chance < 36%) | points in 1-2 straight | 10,504 | 26.2% | 25.8% | -0.4 | -1.0 |
| below average (point chance < 36%) | points in 3-4 straight | 691 | 29.2% | 26.5% | -2.8 | -1.6 |
| below average (point chance < 36%) | points in 5+ straight | 51 | 32.1% | 29.4% | -2.7 | -0.4 |
| average (36-57%) | no point last game | 15,504 | 44.9% | 43.9% | -1.1 | -2.6 |
| average (36-57%) | points in 1-2 straight | 10,067 | 46.1% | 46.6% | +0.5 | +1.0 |
| average (36-57%) | points in 3-4 straight | 2,323 | 47.8% | 49.9% | +2.1 | +2.0 |
| average (36-57%) | points in 5+ straight | 721 | 49.9% | 52.3% | +2.4 | +1.3 |
| top producers (57%+) | no point last game | 2,920 | 63.0% | 63.4% | +0.4 | +0.5 |
| top producers (57%+) | points in 1-2 straight | 3,090 | 63.6% | 64.6% | +1.0 | +1.1 |
| top producers (57%+) | points in 3-4 straight | 1,312 | 64.5% | 62.9% | -1.6 | -1.2 |
| top producers (57%+) | points in 5+ straight | 1,145 | 67.3% | 69.3% | +2.0 | +1.5 |

## Point streaks on the test seasons: streak only
| Level | Going into the game | n | Predicted | Happened | Diff | t |
|---|---|---|---|---|---|---|
| below average (point chance < 36%) | no point last game | 37,460 | 23.8% | 22.6% | -1.1 | -5.2 |
| below average (point chance < 36%) | points in 1-2 straight | 10,504 | 26.2% | 25.8% | -0.4 | -1.0 |
| below average (point chance < 36%) | points in 3-4 straight | 678 | 29.1% | 26.4% | -2.7 | -1.6 |
| below average (point chance < 36%) | points in 5+ straight | 46 | 31.6% | 28.3% | -3.4 | -0.5 |
| average (36-57%) | no point last game | 15,504 | 44.9% | 43.9% | -1.1 | -2.6 |
| average (36-57%) | points in 1-2 straight | 10,067 | 46.1% | 46.6% | +0.5 | +1.0 |
| average (36-57%) | points in 3-4 straight | 1,960 | 48.8% | 48.2% | -0.6 | -0.5 |
| average (36-57%) | points in 5+ straight | 561 | 50.7% | 49.2% | -1.5 | -0.7 |
| top producers (57%+) | no point last game | 2,920 | 63.0% | 63.4% | +0.4 | +0.5 |
| top producers (57%+) | points in 1-2 straight | 3,090 | 63.6% | 64.6% | +1.0 | +1.1 |
| top producers (57%+) | points in 3-4 straight | 1,688 | 65.4% | 61.8% | -3.6 | -3.1 |
| top producers (57%+) | points in 5+ straight | 1,310 | 68.6% | 68.5% | -0.1 | -0.1 |

## Point streaks on the test seasons: depth only
| Level | Going into the game | n | Predicted | Happened | Diff | t |
|---|---|---|---|---|---|---|
| below average (point chance < 36%) | no point last game | 37,460 | 23.4% | 22.6% | -0.7 | -3.3 |
| below average (point chance < 36%) | points in 1-2 straight | 10,504 | 25.8% | 25.8% | +0.0 | +0.0 |
| below average (point chance < 36%) | points in 3-4 straight | 691 | 28.8% | 26.5% | -2.3 | -1.3 |
| below average (point chance < 36%) | points in 5+ straight | 51 | 31.6% | 29.4% | -2.2 | -0.3 |
| average (36-57%) | no point last game | 15,504 | 44.9% | 43.9% | -1.1 | -2.6 |
| average (36-57%) | points in 1-2 straight | 10,067 | 46.1% | 46.6% | +0.5 | +1.0 |
| average (36-57%) | points in 3-4 straight | 2,323 | 47.8% | 49.9% | +2.1 | +2.0 |
| average (36-57%) | points in 5+ straight | 721 | 49.9% | 52.3% | +2.4 | +1.3 |
| top producers (57%+) | no point last game | 2,920 | 63.0% | 63.4% | +0.4 | +0.5 |
| top producers (57%+) | points in 1-2 straight | 3,090 | 63.6% | 64.6% | +1.0 | +1.1 |
| top producers (57%+) | points in 3-4 straight | 1,312 | 64.5% | 62.9% | -1.6 | -1.2 |
| top producers (57%+) | points in 5+ straight | 1,145 | 67.3% | 69.3% | +2.0 | +1.5 |

## Point streaks on the test seasons: both
| Level | Going into the game | n | Predicted | Happened | Diff | t |
|---|---|---|---|---|---|---|
| below average (point chance < 36%) | no point last game | 37,460 | 23.4% | 22.6% | -0.7 | -3.3 |
| below average (point chance < 36%) | points in 1-2 straight | 10,504 | 25.8% | 25.8% | +0.0 | +0.0 |
| below average (point chance < 36%) | points in 3-4 straight | 678 | 28.6% | 26.4% | -2.2 | -1.3 |
| below average (point chance < 36%) | points in 5+ straight | 46 | 31.1% | 28.3% | -2.8 | -0.4 |
| average (36-57%) | no point last game | 15,504 | 44.9% | 43.9% | -1.1 | -2.6 |
| average (36-57%) | points in 1-2 straight | 10,067 | 46.1% | 46.6% | +0.5 | +1.0 |
| average (36-57%) | points in 3-4 straight | 1,960 | 48.8% | 48.2% | -0.6 | -0.5 |
| average (36-57%) | points in 5+ straight | 561 | 50.7% | 49.2% | -1.5 | -0.7 |
| top producers (57%+) | no point last game | 2,920 | 63.0% | 63.4% | +0.4 | +0.5 |
| top producers (57%+) | points in 1-2 straight | 3,090 | 63.6% | 64.6% | +1.0 | +1.1 |
| top producers (57%+) | points in 3-4 straight | 1,688 | 65.4% | 61.8% | -3.6 | -3.1 |
| top producers (57%+) | points in 5+ straight | 1,310 | 68.6% | 68.5% | -0.1 | -0.1 |
