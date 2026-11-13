"""SA-141: run suite tasks under several cloud model settings, then restore the original settings.

Only switches the documented settings (cloud_model, cloud_thinking, prices) through the API.
Usage: python scripts/model_compare.py --tasks s4-routing --budget 0.20 flash-thinking pro
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = {
    # Peak prices from https://api-docs.deepseek.com/quick_start/pricing/ (checked 13.11.2026).
    'flash': {'cloud_model': 'deepseek-flash', 'cloud_thinking': False,
              'cloud_input_price': 0.3, 'cloud_output_price': 1.2, 'cloud_cache_input_price': 0.006},
    'flash-thinking': {'cloud_model': 'deepseek-flash', 'cloud_thinking': True,
                       'cloud_input_price': 0.3, 'cloud_output_price': 1.2, 'cloud_cache_input_price': 0.006},
    'pro': {'cloud_model': 'deepseek-v4-pro', 'cloud_thinking': False,
            'cloud_input_price': 1.32, 'cloud_output_price': 3.96, 'cloud_cache_input_price': 0.044},
    'pro-thinking': {'cloud_model': 'deepseek-v4-pro', 'cloud_thinking': True,
                     'cloud_input_price': 1.32, 'cloud_output_price': 3.96, 'cloud_cache_input_price': 0.044},
}
KEYS = ('cloud_model', 'cloud_thinking', 'cloud_input_price', 'cloud_output_price', 'cloud_cache_input_price', 'cloud_prices_date')


def client():
    c = httpx.Client(base_url='http://127.0.0.1:18573', trust_env=False, timeout=60,
                     limits=httpx.Limits(max_keepalive_connections=0))
    c.headers['x-study-token'] = c.get('/api/session').json()['token']
    return c


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('configs', nargs='+', choices=sorted(CONFIGS))
    parser.add_argument('--tasks', nargs='+', required=True)
    parser.add_argument('--budget', type=float, required=True, help='suite budget per configuration, USD')
    args = parser.parse_args()
    c = client()
    original = {k: c.get('/api/settings').json().get(k) for k in KEYS}
    print('original settings:', json.dumps(original), flush=True)
    try:
        for name in args.configs:
            values = dict(CONFIGS[name], cloud_prices_date='2026-11-13')
            c.put('/api/settings', json=values).raise_for_status()
            print(f'== {name}:', json.dumps(values), flush=True)
            subprocess.run([sys.executable, '-u', str(ROOT / 'scripts' / 'suite.py'), 'run', '--budget', str(args.budget),
                            '--timeout', '60', '--only', *args.tasks], cwd=ROOT, check=False)
    finally:
        c.put('/api/settings', json=original).raise_for_status()
        print('restored settings:', json.dumps({k: c.get('/api/settings').json().get(k) for k in KEYS}), flush=True)


if __name__ == '__main__':
    main()
