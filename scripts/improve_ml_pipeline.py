"""
ML PIPELINE IMPROVEMENTS

Adds to SQLite:
  1. ml_metrics_renewable_cf  — wind & solar capacity-factor forecasting metrics
                                (currently only in notebook output cell 8)
  2. ml_metrics_all_targets   — consolidated per-target metrics from CSVs
  3. ml_predictions_renewable_cf  — wind/solar test set with predictions

This fills the gap between main.tex Table 4 claims (ARIMA(2,0,2) MAE=0.1200
on wind CF) and what's actually queryable in the database.
"""
from pathlib import Path
import sqlite3
import csv
import math
import warnings
from datetime import datetime
from collections import defaultdict
warnings.filterwarnings('ignore')

DATA = Path(r'F:\zerdeli\Dissertation\02_Energy_Balance_and_Demand_Forecasting_KZ\Article\data')
DB = DATA / 'clean' / 'kz_energy_balance.db'
SOLAR_WIND = DATA / '2011-2014_solar-wind_fixed.csv'

conn = sqlite3.connect(DB)
cur = conn.cursor()

print('═' * 70)
print('  ML PIPELINE IMPROVEMENT — adding renewable CF forecasting to SQLite')
print('═' * 70)

# ────────────────────────────────────────────────────────────────────
# 1. Load hourly wind/solar CF and aggregate to daily
# ────────────────────────────────────────────────────────────────────
print('\n  [1/5] Loading hourly ERA5-Land capacity factors...')
daily_wind = defaultdict(list)
daily_solar = defaultdict(list)
with open(SOLAR_WIND, encoding='utf-8') as f:
    for r in csv.DictReader(f):
        d = datetime.strptime(r['Time'], '%Y-%m-%d %H:%M:%S').date()
        daily_wind[d].append(float(r['onwind']))
        daily_solar[d].append(float(r['solar']))

dates = sorted(daily_wind.keys())
wind_series = [(d, sum(daily_wind[d]) / len(daily_wind[d])) for d in dates]
solar_series = [(d, sum(daily_solar[d]) / len(daily_solar[d])) for d in dates]
print(f'        {len(dates):,} daily observations ({dates[0]} to {dates[-1]})')

# ────────────────────────────────────────────────────────────────────
# 2. Train/test split (75/25 = main.tex spec)
# ────────────────────────────────────────────────────────────────────
print('\n  [2/5] Train/test split (75/25 per main.tex)...')
def split_75_25(series):
    cutoff = datetime(2014, 1, 1).date()
    train = [(d, v) for d, v in series if d < cutoff]
    test = [(d, v) for d, v in series if d >= cutoff]
    return train, test

w_train, w_test = split_75_25(wind_series)
s_train, s_test = split_75_25(solar_series)
print(f'        Wind:  train={len(w_train)}, test={len(w_test)}')
print(f'        Solar: train={len(s_train)}, test={len(s_test)}')

# ────────────────────────────────────────────────────────────────────
# 3. Run all 4 models on each series
# ────────────────────────────────────────────────────────────────────
print('\n  [3/5] Running ARIMA(2,0,2), Holt-Winters, Naive, Mean baseline...')
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.holtwinters import ExponentialSmoothing

def evaluate(train, test, name):
    """Run all 4 models, return list of (model, MAE, RMSE, sMAPE, MASE, n_folds, predictions)."""
    train_vals = [v for _, v in train]
    test_vals = [v for _, v in test]
    test_dates = [d for d, _ in test]
    n = len(test_vals)

    # Naive: constant last value (per main.tex)
    naive_pred = [train_vals[-1]] * n
    # Mean of train (constant)
    mean_pred = [sum(train_vals)/len(train_vals)] * n
    # ARIMA(2,0,2)
    arima_fit = ARIMA(train_vals, order=(2,0,2)).fit()
    arima_pred = list(arima_fit.forecast(n))
    # Holt-Winters (additive trend, no seasonal — per notebook)
    hw_fit = ExponentialSmoothing(train_vals, trend='add', seasonal=None).fit()
    hw_pred = list(hw_fit.forecast(n))

    # MASE denominator: MAE of seasonal naive (lag 365 if available, else lag-1 train MAE)
    if len(train_vals) > 365:
        seasonal_naive_train = [train_vals[i] - train_vals[i-365] for i in range(365, len(train_vals))]
        mase_denom = sum(abs(d) for d in seasonal_naive_train) / len(seasonal_naive_train)
    else:
        mase_denom = sum(abs(train_vals[i] - train_vals[i-1]) for i in range(1, len(train_vals))) / (len(train_vals)-1)

    results = []
    for model_name, preds in [
        ('naive_last', naive_pred),
        ('mean_train', mean_pred),
        ('arima_2_0_2', arima_pred),
        ('holt_winters', hw_pred),
    ]:
        errs = [p - a for p, a in zip(preds, test_vals)]
        mae = sum(abs(e) for e in errs) / n
        rmse = math.sqrt(sum(e*e for e in errs) / n)
        smape = sum(abs(e) / ((abs(p) + abs(a)) / 2) for e, p, a in zip(errs, preds, test_vals)) / n * 100
        mase = mae / mase_denom
        results.append({
            'target': name,
            'model': model_name,
            'MAE': mae,
            'RMSE': rmse,
            'sMAPE_pct': smape,
            'MASE': mase,
            'n_test_obs': n,
            'preds': list(zip(test_dates, test_vals, preds))
        })
    return results

wind_results = evaluate(w_train, w_test, 'wind_cf_daily')
solar_results = evaluate(s_train, s_test, 'solar_cf_daily')

# ────────────────────────────────────────────────────────────────────
# 4. Diebold-Mariano for each model vs naive_last (HLN-corrected)
# ────────────────────────────────────────────────────────────────────
print('\n  [4/5] Diebold-Mariano tests (HLN small-sample correction)...')

def dm_test(e1, e2, h=1):
    """Diebold-Mariano test. e1 = baseline errors, e2 = competitor errors."""
    n = len(e1)
    d = [(a*a - b*b) for a, b in zip(e1, e2)]
    d_bar = sum(d) / n
    # Long-run variance via Newey-West
    gamma_0 = sum((di - d_bar)**2 for di in d) / n
    var = gamma_0
    for k in range(1, h):
        gamma_k = sum((d[i] - d_bar) * (d[i-k] - d_bar) for i in range(k, n)) / n
        var += 2 * (1 - k/h) * gamma_k
    if var <= 0:
        return None, None
    dm_stat = d_bar / math.sqrt(var / n)
    # HLN correction
    hln_factor = math.sqrt((n + 1 - 2*h + h*(h-1)/n) / n)
    dm_stat_hln = dm_stat * hln_factor
    # Approximate p-value using t-distribution (n-1 dof)
    from scipy import stats
    p = 2 * (1 - stats.t.cdf(abs(dm_stat_hln), n-1))
    return dm_stat_hln, p

def get_errors(results, model_name):
    for r in results:
        if r['model'] == model_name:
            return [p - a for _, a, p in r['preds']]
    return None

dm_results = []
for series_results, target_name in [(wind_results, 'wind_cf_daily'), (solar_results, 'solar_cf_daily')]:
    naive_errs = get_errors(series_results, 'naive_last')
    for model in ['arima_2_0_2', 'holt_winters', 'mean_train']:
        comp_errs = get_errors(series_results, model)
        dm, p = dm_test(naive_errs, comp_errs, h=1)
        if dm is not None:
            dm_results.append({
                'target': target_name,
                'compared_to': 'naive_last',
                'model': model,
                'DM_stat': dm,
                'p_value': p,
                'n_obs': len(naive_errs)
            })
            print(f'        {target_name:20s} {model:15s} DM={dm:+.3f}  p={p:.4f}')

# ────────────────────────────────────────────────────────────────────
# 5. Save to SQLite
# ────────────────────────────────────────────────────────────────────
print('\n  [5/5] Saving to SQLite...')

# Table: ml_metrics_renewable_cf
cur.execute('DROP TABLE IF EXISTS ml_metrics_renewable_cf')
cur.execute('''
    CREATE TABLE ml_metrics_renewable_cf (
        target TEXT,
        model TEXT,
        MAE REAL,
        RMSE REAL,
        sMAPE_pct REAL,
        MASE REAL,
        n_test_obs INTEGER
    )
''')
for r in wind_results + solar_results:
    cur.execute('INSERT INTO ml_metrics_renewable_cf VALUES (?,?,?,?,?,?,?)',
                (r['target'], r['model'], r['MAE'], r['RMSE'], r['sMAPE_pct'], r['MASE'], r['n_test_obs']))
print(f'        + ml_metrics_renewable_cf: {len(wind_results)+len(solar_results)} rows')

# Table: ml_dm_renewable_cf
cur.execute('DROP TABLE IF EXISTS ml_dm_renewable_cf')
cur.execute('''
    CREATE TABLE ml_dm_renewable_cf (
        target TEXT,
        compared_to TEXT,
        model TEXT,
        DM_stat REAL,
        p_value REAL,
        n_obs INTEGER
    )
''')
for r in dm_results:
    cur.execute('INSERT INTO ml_dm_renewable_cf VALUES (?,?,?,?,?,?)',
                (r['target'], r['compared_to'], r['model'], r['DM_stat'], r['p_value'], r['n_obs']))
print(f'        + ml_dm_renewable_cf: {len(dm_results)} rows')

# Table: ml_predictions_renewable_cf
cur.execute('DROP TABLE IF EXISTS ml_predictions_renewable_cf')
cur.execute('''
    CREATE TABLE ml_predictions_renewable_cf (
        target TEXT,
        model TEXT,
        date TEXT,
        actual REAL,
        prediction REAL
    )
''')
n_preds = 0
for series_results in [wind_results, solar_results]:
    for r in series_results:
        for d, a, p in r['preds']:
            cur.execute('INSERT INTO ml_predictions_renewable_cf VALUES (?,?,?,?,?)',
                        (r['target'], r['model'], str(d), a, p))
            n_preds += 1
print(f'        + ml_predictions_renewable_cf: {n_preds} rows')

# ────────────────────────────────────────────────────────────────────
# 6. Consolidate per-target metrics from CSVs into single SQLite table
# ────────────────────────────────────────────────────────────────────
print('\n  [BONUS] Consolidating per-target metrics from CSVs into ml_metrics_all_targets...')
cur.execute('DROP TABLE IF EXISTS ml_metrics_all_targets')
cur.execute('''
    CREATE TABLE ml_metrics_all_targets (
        target TEXT,
        model TEXT,
        MAE REAL,
        RMSE REAL,
        sMAPE_pct REAL,
        MASE REAL,
        n_folds INTEGER
    )
''')
ml_csv_dir = DATA / 'clean' / 'ml_results'
for f in sorted(ml_csv_dir.glob('metrics_*.csv')):
    target = f.stem.replace('metrics_', '')
    with open(f, encoding='utf-8') as fh:
        for row in csv.DictReader(fh):
            cur.execute('INSERT INTO ml_metrics_all_targets VALUES (?,?,?,?,?,?,?)',
                        (row['target'], row['model'], float(row['MAE']), float(row['RMSE']),
                         float(row['sMAPE_pct']), float(row['MASE']), int(row['n_folds'])))
n = cur.execute('SELECT COUNT(*) FROM ml_metrics_all_targets').fetchone()[0]
print(f'        + ml_metrics_all_targets: {n} rows (4 targets × 4-5 models)')

conn.commit()

# ────────────────────────────────────────────────────────────────────
# Summary
# ────────────────────────────────────────────────────────────────────
print()
print('═' * 70)
print('  RESULTS SUMMARY')
print('═' * 70)

print()
print('  WIND CF (daily, 2014 test set):')
print(f'  {"Model":<15s}{"MAE":>10s}{"RMSE":>10s}{"sMAPE %":>10s}{"MASE":>8s}')
print('  ' + '-' * 53)
for r in wind_results:
    print(f'  {r["model"]:<15s}{r["MAE"]:>10.4f}{r["RMSE"]:>10.4f}{r["sMAPE_pct"]:>10.2f}{r["MASE"]:>8.3f}')

print()
print('  SOLAR CF (daily, 2014 test set):')
print(f'  {"Model":<15s}{"MAE":>10s}{"RMSE":>10s}{"sMAPE %":>10s}{"MASE":>8s}')
print('  ' + '-' * 53)
for r in solar_results:
    print(f'  {r["model"]:<15s}{r["MAE"]:>10.4f}{r["RMSE"]:>10.4f}{r["sMAPE_pct"]:>10.2f}{r["MASE"]:>8.3f}')

print()
print('  WIND CF — Diebold-Mariano vs naive_last:')
for r in dm_results:
    if r['target'] == 'wind_cf_daily':
        sig = '★ significant' if r['p_value'] < 0.05 else 'not significant'
        print(f'    {r["model"]:<15s} DM={r["DM_stat"]:+7.3f}  p={r["p_value"]:.4f}  ({sig})')

print()
print('  SOLAR CF — Diebold-Mariano vs naive_last:')
for r in dm_results:
    if r['target'] == 'solar_cf_daily':
        sig = '★ significant' if r['p_value'] < 0.05 else 'not significant'
        print(f'    {r["model"]:<15s} DM={r["DM_stat"]:+7.3f}  p={r["p_value"]:.4f}  ({sig})')

print()
print('  ✅ All ML improvements committed to kz_energy_balance.db')
print()
print(f'  New queryable tables:')
print(f'    SELECT * FROM ml_metrics_renewable_cf;')
print(f'    SELECT * FROM ml_dm_renewable_cf;')
print(f'    SELECT * FROM ml_predictions_renewable_cf;')
print(f'    SELECT * FROM ml_metrics_all_targets;')

conn.close()
