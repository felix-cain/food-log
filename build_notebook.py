"""Construct analysis.ipynb from declarative cell list. Run me whenever the
notebook structure changes; otherwise just open analysis.ipynb in Jupyter."""

from __future__ import annotations

from pathlib import Path

import nbformat as nbf

HERE = Path(__file__).parent
NB_PATH = HERE / "analysis.ipynb"


def md(text: str) -> dict:
    return nbf.v4.new_markdown_cell(text)


def code(src: str) -> dict:
    return nbf.v4.new_code_cell(src)


CELLS = [
    md(
        "# Health Tracking — Trends & Cross-Domain Insights\n\n"
        "Loads `health_log.xlsx` (Food, Activities, Sleep), cleans it, then "
        "explores trends over time and relationships between domains.\n\n"
        "Re-run top-to-bottom after each Excel update."
    ),
    md("## Setup"),
    code(
        "import warnings\n"
        "import numpy as np\n"
        "import pandas as pd\n"
        "import matplotlib.pyplot as plt\n"
        "import seaborn as sns\n"
        "from scipy import stats\n"
        "\n"
        "warnings.filterwarnings('ignore', category=FutureWarning)\n"
        "sns.set_theme(style='whitegrid', context='notebook')\n"
        "pd.set_option('display.width', 200)\n"
        "pd.set_option('display.max_columns', 50)\n"
        "\n"
        "from loader import load_all, refresh_local_copy\n"
        "\n"
        "# Try to pull a fresh copy from OneDrive (skipped silently if Excel is open)\n"
        "refreshed = refresh_local_copy()\n"
        "print(f'Refreshed from OneDrive: {refreshed}')"
    ),
    md("## Load data"),
    code(
        "data = load_all()\n"
        "food, activities, sleep = data['food'], data['activities'], data['sleep']\n"
        "\n"
        "summary = pd.DataFrame(\n"
        "    {\n"
        "        'rows': [len(food), len(activities), len(sleep)],\n"
        "        'first_date': [food['Date'].min(), activities['Date'].min(), sleep['Date'].min()],\n"
        "        'last_date':  [food['Date'].max(), activities['Date'].max(), sleep['Date'].max()],\n"
        "    },\n"
        "    index=['food', 'activities', 'sleep'],\n"
        ")\n"
        "summary"
    ),
    md(
        "## 1. Sleep — trends over time\n\n"
        "Sleep is the densest dataset, so it gets the most attention. "
        "We plot each headline metric with a 7-day rolling mean to smooth night-to-night noise."
    ),
    code(
        "metrics = [\n"
        "    ('Score',              'Sleep score (Garmin)'),\n"
        "    ('duration_min',       'Sleep duration (min)'),\n"
        "    ('Resting Heart Rate', 'Resting heart rate (bpm)'),\n"
        "    ('Body Battery',       'Body Battery end-of-sleep'),\n"
        "]\n"
        "fig, axes = plt.subplots(len(metrics), 1, figsize=(11, 9), sharex=True)\n"
        "for ax, (col, label) in zip(axes, metrics):\n"
        "    s = sleep.set_index('Date')[col].astype(float)\n"
        "    ax.plot(s.index, s.values, marker='o', ms=3, lw=0.8, color='#4c78a8', label='daily')\n"
        "    ax.plot(s.index, s.rolling(7, min_periods=3).mean(), color='#e45756', lw=2, label='7-day mean')\n"
        "    ax.set_ylabel(label)\n"
        "    ax.legend(loc='best', fontsize=8)\n"
        "axes[-1].set_xlabel('Date')\n"
        "fig.suptitle('Sleep metrics over time', y=1.01)\n"
        "fig.tight_layout()\n"
        "plt.show()"
    ),
    code(
        "# Sleep deficit: need minus actual. Positive = under-slept vs body's stated need.\n"
        "fig, ax = plt.subplots(figsize=(11, 3.5))\n"
        "colors = sleep['sleep_deficit_min'].apply(lambda x: '#e45756' if x > 0 else '#4c78a8')\n"
        "ax.bar(sleep['Date'], sleep['sleep_deficit_min'], color=colors, width=0.8)\n"
        "ax.axhline(0, color='k', lw=0.5)\n"
        "ax.set_title('Daily sleep deficit (need − actual). Red = under-slept.')\n"
        "ax.set_ylabel('Minutes')\n"
        "fig.tight_layout()\n"
        "plt.show()\n"
        "\n"
        "deficit_stats = sleep['sleep_deficit_min'].describe()\n"
        "under_slept_pct = (sleep['sleep_deficit_min'] > 0).mean() * 100\n"
        "print(f'Under-slept on {under_slept_pct:.0f}% of nights | mean deficit {deficit_stats[\"mean\"]:+.0f} min')"
    ),
    code(
        "# Day-of-week patterns\n"
        "sleep_dow = sleep.assign(dow=sleep['Date'].dt.day_name())\n"
        "order = ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']\n"
        "agg = sleep_dow.groupby('dow')[['Score','duration_min','sleep_deficit_min']].mean().reindex(order)\n"
        "fig, axes = plt.subplots(1, 3, figsize=(12, 3.2))\n"
        "for ax, col, ttl in zip(axes, ['Score','duration_min','sleep_deficit_min'],\n"
        "                       ['Avg Score','Avg duration (min)','Avg deficit (min)']):\n"
        "    sns.barplot(x=agg.index, y=agg[col], ax=ax, color='#4c78a8')\n"
        "    ax.set_title(ttl)\n"
        "    ax.set_xlabel('')\n"
        "    ax.tick_params(axis='x', rotation=30)\n"
        "fig.suptitle('Sleep by day of week', y=1.05)\n"
        "fig.tight_layout()\n"
        "plt.show()\n"
        "agg.round(1)"
    ),
    md(
        "## 2. Activities — cadence & intensity\n\n"
        "Weekly volume, intensity (HR + calories), and run-pace progression."
    ),
    code(
        "weekly = (\n"
        "    activities.assign(week=activities['Date'].dt.to_period('W').dt.start_time)\n"
        "    .groupby('week')\n"
        "    .agg(sessions=('Date','count'),\n"
        "         total_min=('duration_min','sum'),\n"
        "         total_distance_km=('Distance','sum'),\n"
        "         total_calories=('Calories','sum'),\n"
        "         avg_HR=('Avg HR','mean'))\n"
        "    .round(1)\n"
        ")\n"
        "weekly"
    ),
    code(
        "weekly_plot = weekly.copy()\n"
        "weekly_plot.index = [d.strftime('%b %d') for d in weekly_plot.index]\n"
        "fig, axes = plt.subplots(2, 2, figsize=(11, 6))\n"
        "weekly_plot['sessions'].plot.bar(ax=axes[0,0], color='#4c78a8', title='Sessions / week')\n"
        "weekly_plot['total_min'].plot.bar(ax=axes[0,1], color='#4c78a8', title='Active minutes / week')\n"
        "weekly_plot['total_distance_km'].plot.bar(ax=axes[1,0], color='#4c78a8', title='Distance (km) / week')\n"
        "weekly_plot['total_calories'].plot.bar(ax=axes[1,1], color='#4c78a8', title='Calories / week')\n"
        "for ax in axes.ravel():\n"
        "    ax.set_xlabel('Week of')\n"
        "    ax.tick_params(axis='x', rotation=30, labelsize=8)\n"
        "fig.tight_layout()\n"
        "plt.show()"
    ),
    code(
        "# Run-pace progression (only Running / Treadmill Running)\n"
        "runs = activities[activities['Activity Type'].isin(['Running','Treadmill Running'])]\\\n"
        "          .dropna(subset=['avg_pace_min_per_km']).sort_values('Date')\n"
        "if len(runs) >= 2:\n"
        "    fig, ax = plt.subplots(figsize=(10, 3.5))\n"
        "    ax.plot(runs['Date'], runs['avg_pace_min_per_km'], marker='o', color='#4c78a8')\n"
        "    ax.invert_yaxis()  # faster = lower number, but visually 'up'\n"
        "    ax.set_title('Avg pace per run (lower = faster; axis flipped)')\n"
        "    ax.set_ylabel('min / km')\n"
        "    for _, r in runs.iterrows():\n"
        "        ax.annotate(f\"{r['Distance']:.1f}km\", (r['Date'], r['avg_pace_min_per_km']),\n"
        "                    fontsize=7, xytext=(4, 4), textcoords='offset points')\n"
        "    fig.tight_layout()\n"
        "    plt.show()\n"
        "else:\n"
        "    print('Not enough run pace data yet.')\n"
        "runs[['Date','Title','Distance','duration_min','avg_pace_min_per_km','Avg HR','Calories']].reset_index(drop=True)"
    ),
    code(
        "type_mix = activities['Activity Type'].value_counts()\n"
        "fig, ax = plt.subplots(figsize=(6, 3))\n"
        "type_mix.plot.barh(ax=ax, color='#4c78a8')\n"
        "ax.set_title('Activity type mix')\n"
        "ax.set_xlabel('Sessions')\n"
        "fig.tight_layout()\n"
        "plt.show()\n"
        "type_mix"
    ),
    md(
        "## 3. Food — patterns\n\n"
        "Food log is the newest input so the sample is small. We focus on Day Rating "
        "and behavioural flags (alcohol, skipped breakfast)."
    ),
    code(
        "fig, axes = plt.subplots(1, 3, figsize=(11, 3))\n"
        "food['Day Rating'].value_counts().reindex(['Good','Mixed','Poor']).plot.bar(\n"
        "    ax=axes[0], color=['#4c78a8','#f1a340','#e45756'])\n"
        "axes[0].set_title('Day Rating distribution')\n"
        "food['had_alcohol'].value_counts().rename({True:'Drank',False:'Sober'}).plot.bar(\n"
        "    ax=axes[1], color=['#e45756','#4c78a8'])\n"
        "axes[1].set_title('Alcohol days')\n"
        "food['skipped_breakfast'].value_counts().rename({True:'Skipped',False:'Ate'}).plot.bar(\n"
        "    ax=axes[2], color=['#e45756','#4c78a8'])\n"
        "axes[2].set_title('Breakfast skipped')\n"
        "for ax in axes:\n"
        "    ax.set_xlabel('')\n"
        "    ax.tick_params(axis='x', rotation=0)\n"
        "fig.tight_layout()\n"
        "plt.show()\n"
        "\n"
        "food[['Date','Day Rating','had_alcohol','skipped_breakfast']]"
    ),
    md(
        "## 4. Cross-domain correlations\n\n"
        "We build a single daily table (one row per date) joining food, sleep, and "
        "the day's activity. Most relationships here are tentative — with ~10 "
        "food-logged days, treat anything p > 0.1 as a hypothesis, not a finding."
    ),
    code(
        "act_daily = (\n"
        "    activities.assign(date=activities['Date'].dt.normalize())\n"
        "    .groupby('date')\n"
        "    .agg(act_minutes=('duration_min','sum'),\n"
        "         act_calories=('Calories','sum'),\n"
        "         act_avg_HR=('Avg HR','mean'),\n"
        "         act_distance=('Distance','sum'))\n"
        ")\n"
        "\n"
        "sleep_daily = sleep.set_index('Date')[\n"
        "    ['Score','duration_min','Resting Heart Rate','Body Battery','sleep_deficit_min','bedtime_decimal']\n"
        "].rename(columns={'duration_min':'sleep_min','Resting Heart Rate':'RHR',\n"
        "                   'Body Battery':'BodyBattery','sleep_deficit_min':'sleep_deficit',\n"
        "                   'Score':'sleep_score'})\n"
        "\n"
        "food_daily = food.set_index('Date')[['Day Rating','had_alcohol','skipped_breakfast']]\n"
        "food_daily['rating_num'] = food_daily['Day Rating'].cat.codes.replace(-1, np.nan)\n"
        "# rating_num: 0=Good, 1=Mixed, 2=Poor (higher = worse)\n"
        "\n"
        "daily = sleep_daily.join(act_daily, how='left').join(food_daily, how='left')\n"
        "daily['exercised'] = daily['act_minutes'].fillna(0) > 0\n"
        "daily.tail(12)"
    ),
    code(
        "# Heat-map of pairwise correlations on numeric daily columns\n"
        "num_cols = ['sleep_score','sleep_min','RHR','BodyBattery','sleep_deficit',\n"
        "            'bedtime_decimal','act_minutes','act_calories','act_avg_HR','rating_num']\n"
        "corr = daily[num_cols].corr(method='spearman')\n"
        "fig, ax = plt.subplots(figsize=(8, 6))\n"
        "sns.heatmap(corr, annot=True, fmt='.2f', cmap='RdBu_r', center=0, vmin=-1, vmax=1, ax=ax)\n"
        "ax.set_title('Spearman correlation between daily metrics')\n"
        "fig.tight_layout()\n"
        "plt.show()"
    ),
    code(
        "def compare(label_a, mask_a, label_b, mask_b, metric):\n"
        "    a = daily.loc[mask_a, metric].dropna()\n"
        "    b = daily.loc[mask_b, metric].dropna()\n"
        "    if len(a) < 2 or len(b) < 2:\n"
        "        return {'metric':metric,'n_a':len(a),'n_b':len(b),'mean_a':np.nan,'mean_b':np.nan,'diff':np.nan,'p':np.nan}\n"
        "    _, p = stats.mannwhitneyu(a, b, alternative='two-sided')\n"
        "    return {'metric':metric,'n_a':len(a),'n_b':len(b),\n"
        "            'mean_a':round(a.mean(),1),'mean_b':round(b.mean(),1),\n"
        "            'diff':round(a.mean()-b.mean(),1),'p':round(p,3)}\n"
        "\n"
        "# 4a. Alcohol vs sober nights\n"
        "alc = daily['had_alcohol'] == True\n"
        "sober = daily['had_alcohol'] == False\n"
        "alc_vs_sober = pd.DataFrame([\n"
        "    compare('alcohol', alc, 'sober', sober, m)\n"
        "    for m in ['sleep_score','sleep_min','RHR','BodyBattery']\n"
        "])\n"
        "print('--- Alcohol nights vs sober nights ---')\n"
        "print(alc_vs_sober.to_string(index=False))\n"
        "\n"
        "# 4b. Exercise day vs rest day\n"
        "ex = daily['exercised'] == True\n"
        "rest = daily['exercised'] == False\n"
        "ex_vs_rest = pd.DataFrame([\n"
        "    compare('exercised', ex, 'rest', rest, m)\n"
        "    for m in ['sleep_score','sleep_min','RHR','BodyBattery']\n"
        "])\n"
        "print('\\n--- Exercise day vs rest day ---')\n"
        "print(ex_vs_rest.to_string(index=False))"
    ),
    code(
        "# 4c. Bedtime impact: late nights (>= 24h decimal, i.e. past midnight) vs early nights\n"
        "if daily['bedtime_decimal'].notna().sum() >= 6:\n"
        "    median_bt = daily['bedtime_decimal'].median()\n"
        "    late = daily['bedtime_decimal'] >= median_bt\n"
        "    early = daily['bedtime_decimal'] < median_bt\n"
        "    bt = pd.DataFrame([\n"
        "        compare(f'late (≥{median_bt:.1f})', late, f'early (<{median_bt:.1f})', early, m)\n"
        "        for m in ['sleep_score','sleep_min','RHR','BodyBattery']\n"
        "    ])\n"
        "    print(f'--- Late bedtime (≥{median_bt:.2f}) vs early ---')\n"
        "    print(bt.to_string(index=False))"
    ),
    md("## 5. Auto-generated summary"),
    code(
        "lines = []\n"
        "n_sleep = sleep['Score'].notna().sum()\n"
        "mean_score = sleep['Score'].mean()\n"
        "trend = stats.linregress(np.arange(len(sleep)), sleep['Score'].fillna(sleep['Score'].mean()))\n"
        "direction = 'improving' if trend.slope > 0 else 'declining'\n"
        "lines.append(f'**Sleep:** avg score {mean_score:.1f} over {n_sleep} nights — trend {direction} ({trend.slope:+.2f}/day, p={trend.pvalue:.2f}).')\n"
        "\n"
        "under = (sleep['sleep_deficit_min'] > 0).sum()\n"
        "lines.append(f'**Sleep debt:** under-slept on {under}/{n_sleep} nights vs Garmin sleep-need.')\n"
        "\n"
        "if len(activities):\n"
        "    span_days = (activities['Date'].max() - activities['Date'].min()).days + 1\n"
        "    lines.append(f'**Activity cadence:** {len(activities)} sessions over {span_days} days '\n"
        "                 f'(~{len(activities)/(span_days/7):.1f}/week).')\n"
        "\n"
        "alc_row = alc_vs_sober.iloc[0]\n"
        "if pd.notna(alc_row['p']):\n"
        "    sig = '✓' if alc_row['p'] < 0.1 else '~'\n"
        "    lines.append(f'**Alcohol → sleep score:** {alc_row[\"mean_a\"]} (n={alc_row[\"n_a\"]}) vs '\n"
        "                 f'{alc_row[\"mean_b\"]} sober (n={alc_row[\"n_b\"]}) — diff {alc_row[\"diff\"]:+.1f} '\n"
        "                 f'(p={alc_row[\"p\"]}) {sig}')\n"
        "\n"
        "ex_row = ex_vs_rest.iloc[0]\n"
        "if pd.notna(ex_row['p']):\n"
        "    sig = '✓' if ex_row['p'] < 0.1 else '~'\n"
        "    lines.append(f'**Exercise → sleep score:** {ex_row[\"mean_a\"]} (n={ex_row[\"n_a\"]}) vs '\n"
        "                 f'{ex_row[\"mean_b\"]} rest (n={ex_row[\"n_b\"]}) — diff {ex_row[\"diff\"]:+.1f} '\n"
        "                 f'(p={ex_row[\"p\"]}) {sig}')\n"
        "\n"
        "# Strongest correlation in matrix (upper triangle only, exclude trivially related pairs)\n"
        "mask = np.triu(np.ones_like(corr, dtype=bool), k=1)\n"
        "c = corr.where(mask)\n"
        "trivial = {('sleep_min','sleep_deficit'), ('act_minutes','act_calories'),\n"
        "           ('act_minutes','act_distance'), ('act_calories','act_distance')}\n"
        "stacked = c.abs().stack().sort_values(ascending=False)\n"
        "shown = 0\n"
        "for (a, b), val in stacked.items():\n"
        "    if tuple(sorted([a, b])) in {tuple(sorted(p)) for p in trivial}:\n"
        "        continue\n"
        "    raw = corr.loc[a, b]\n"
        "    lines.append(f'**Notable correlation:** `{a}` ↔ `{b}` ρ = {raw:+.2f}')\n"
        "    shown += 1\n"
        "    if shown >= 4:\n"
        "        break\n"
        "\n"
        "from IPython.display import Markdown\n"
        "Markdown('\\n\\n'.join(f'- {l}' for l in lines))"
    ),
    md(
        "---\n\n"
        "### How to query this dataset\n\n"
        "The cleaned tables are `food`, `activities`, `sleep`, and the joined "
        "daily table `daily`. Add a new cell and ask things like:\n\n"
        "```python\n"
        "# Worst sleep nights\n"
        "sleep.nsmallest(5, 'Score')[['Date','Score','duration_min','Quality']]\n\n"
        "# Days where I drank and what sleep looked like\n"
        "daily[daily['had_alcohol'] == True][['sleep_score','sleep_min','RHR']]\n\n"
        "# Pace by week\n"
        "runs.assign(week=runs['Date'].dt.to_period('W')).groupby('week')['avg_pace_min_per_km'].mean()\n"
        "```\n\n"
        "Or just paste a question in chat and I can run it against this file."
    ),
]


def main() -> None:
    nb = nbf.v4.new_notebook()
    nb.cells = CELLS
    nb.metadata = {
        "kernelspec": {
            "name": "python3",
            "display_name": "Python 3",
            "language": "python",
        }
    }
    nbf.write(nb, NB_PATH)
    print(f"Wrote {NB_PATH}")


if __name__ == "__main__":
    main()
