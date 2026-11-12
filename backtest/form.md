# Recent-form term: fit on [2022], tested on the other seasons of [2022, 2023, 2024]
Player-games: fit 42,747 · test 85,788

Grid (fit seasons, Brier, lower is better):
- goals: K=none: 0.12269 · K=160: 0.12267 · K=120: 0.12267 · K=80: 0.12266 · K=60: 0.12265 · K=40: 0.12265 · K=20: 0.12268  → best 40
- assists: K=none: 0.17266 · K=160: 0.17261 · K=120: 0.17260 · K=80: 0.17258 · K=60: 0.17256 · K=40: 0.17254 · K=20: 0.17260  → best 40
- shots 2+: K=none: 0.21462 · K=160: 0.21444 · K=120: 0.21445 · K=80: 0.21452 · K=60: 0.21466 · K=40: 0.21502 · K=20: 0.21632  → best 160

## Test seasons (never used for fitting)
| Market | Brier now | Skill now | Brier with form | Skill with form |
|---|---|---|---|---|
| Goal | 0.12009 | +6.98% | 0.12004 | +7.03% |
| Assist | 0.17013 | +7.07% | 0.17006 | +7.11% |
| Point | 0.20403 | +10.12% | 0.20387 | +10.19% |
| SOG 2+ | 0.21389 | +13.65% | 0.21333 | +13.87% |
| SOG 3+ | 0.15607 | +14.17% | 0.15565 | +14.40% |

## Point streaks, test seasons: now
| Level | Going into the game | n | Predicted | Happened | Diff | t |
|---|---|---|---|---|---|---|
| below average (point chance < 36%) | no point last game | 36,345 | 25.7% | 22.4% | -3.3 | -14.4 |
| below average (point chance < 36%) | points in 1-2 straight | 10,220 | 27.6% | 25.7% | -1.9 | -4.3 |
| below average (point chance < 36%) | points in 3-4 straight | 733 | 30.4% | 28.6% | -1.8 | -1.0 |
| below average (point chance < 36%) | points in 5+ straight | 59 | 32.3% | 28.8% | -3.5 | -0.6 |
| average (36-57%) | no point last game | 16,984 | 44.5% | 43.4% | -1.1 | -2.9 |
| average (36-57%) | points in 1-2 straight | 10,824 | 45.6% | 46.7% | +1.1 | +2.3 |
| average (36-57%) | points in 3-4 straight | 2,521 | 47.5% | 50.5% | +3.0 | +3.0 |
| average (36-57%) | points in 5+ straight | 917 | 49.5% | 53.7% | +4.2 | +2.5 |
| top producers (57%+) | no point last game | 2,555 | 62.7% | 64.0% | +1.3 | +1.3 |
| top producers (57%+) | points in 1-2 straight | 2,617 | 63.2% | 65.3% | +2.1 | +2.2 |
| top producers (57%+) | points in 3-4 straight | 1,072 | 64.1% | 63.7% | -0.4 | -0.3 |
| top producers (57%+) | points in 5+ straight | 941 | 66.7% | 71.9% | +5.3 | +3.4 |

## Point streaks, test seasons: with form
| Level | Going into the game | n | Predicted | Happened | Diff | t |
|---|---|---|---|---|---|---|
| below average (point chance < 36%) | no point last game | 36,738 | 25.6% | 22.5% | -3.1 | -13.7 |
| below average (point chance < 36%) | points in 1-2 straight | 10,149 | 27.6% | 25.6% | -2.0 | -4.5 |
| below average (point chance < 36%) | points in 3-4 straight | 671 | 30.6% | 27.1% | -3.4 | -1.9 |
| below average (point chance < 36%) | points in 5+ straight | 47 | 32.7% | 29.8% | -2.9 | -0.4 |
| average (36-57%) | no point last game | 16,638 | 44.5% | 43.7% | -0.7 | -1.9 |
| average (36-57%) | points in 1-2 straight | 10,836 | 45.7% | 46.5% | +0.8 | +1.6 |
| average (36-57%) | points in 3-4 straight | 2,496 | 47.7% | 50.1% | +2.4 | +2.4 |
| average (36-57%) | points in 5+ straight | 817 | 49.9% | 52.1% | +2.3 | +1.3 |
| top producers (57%+) | no point last game | 2,508 | 62.7% | 64.0% | +1.3 | +1.4 |
| top producers (57%+) | points in 1-2 straight | 2,676 | 63.3% | 65.5% | +2.2 | +2.4 |
| top producers (57%+) | points in 3-4 straight | 1,159 | 64.3% | 63.3% | -0.9 | -0.7 |
| top producers (57%+) | points in 5+ straight | 1,053 | 67.1% | 70.8% | +3.8 | +2.6 |
