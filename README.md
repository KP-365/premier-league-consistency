# Does Consistency Matter? Player Variance and Match Impact in the Premier League 2024/25

Investigates whether goal-scoring consistency affects match impact, comparing players with similar total goal contributions but very different performance variance across the 2024/25 Premier League season.

## Motivation

Two strikers can both score 15 goals in a season yet contribute in completely different ways. One scores reliably across most matches; another goes cold for weeks then delivers hat-tricks. This project asks whether that variance difference translates to measurable differences in match impact.

## Data Collection

Custom web scraper built against ESPN to extract:
- Match results
- Goal scorers and minutes scored
- Appearances, starts, and substitute entries

Data stored across three CSVs: `matches.csv`, `goals.csv`, `appearances.csv`, joined on `event_id` and scorer identifiers.

## Methodology

1. Compute goals per 90 minutes (g90) for all players with 5+ goals
2. Calculate variance and standard deviation as the consistency metric
3. Select player pairs with similar g90 but contrasting variance (e.g. Salah vs Haaland, Watkins vs Cunha, Bowen vs Mateta)
4. Statistical hypothesis test on normalised match impact scores

**Hypothesis**

H0: mean(normalised_impact | high variance) = mean(normalised_impact | low variance)

Ha: the means differ significantly

## Libraries

`pandas` `numpy` `matplotlib` `scipy` `requests` `BeautifulSoup`

## How to Run

```bash
pip install pandas numpy matplotlib scipy requests beautifulsoup4
jupyter notebook "Premier_League_Comp_2025:2026.ipynb"
```
