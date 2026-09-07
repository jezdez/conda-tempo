"""Run each measurement in a fresh process and emit only safe aggregates."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys

import memray

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = Path(os.environ["LAZY_INDEX_OUTPUT_DIR"]).resolve()
ROOT.mkdir(parents=True, exist_ok=True)


def run(args, label):
    env = os.environ.copy()
    env.update({
        'PYTHONHASHSEED': '0',
        'CONDA_NO_PLUGINS': 'true',
        'CONDA_SOLVER': 'classic',
        'CONDA_PKGS_DIRS': str(ROOT / 'caches' / label),
        'CONDARC': str(ROOT / 'condarc'),
        'CONDA_REPODATA_USE_ZST': 'false',
    })
    result = subprocess.run(
        [sys.executable, str(SCRIPT_DIR / 'benchmark.py'), *args],
        cwd=ROOT, env=env, capture_output=True, text=True,
    )
    logs = ROOT / 'logs'
    logs.mkdir(exist_ok=True)
    (logs / f'{label}.log').write_text(result.stdout + result.stderr)
    if result.returncode:
        print(json.dumps({'run_failed': label, 'exit': result.returncode}), flush=True)
        raise SystemExit(1)
    data = json.loads(result.stdout.splitlines()[-1])
    if data.get('profiled'):
        with memray.FileReader(ROOT / 'captures' / data['capture']) as reader:
            data['memray_peak_bytes'] = reader.metadata.peak_memory
            records = list(reader.get_high_watermark_allocation_records())
            assert sum(record.size for record in records) == reader.metadata.peak_memory
            sites = []
            for record in sorted(records, key=lambda item: item.size, reverse=True)[:8]:
                frames = record.stack_trace()
                sites.append({'bytes': record.size,
                              'functions': [frame[0] for frame in frames[:7]]})
            data['peak_allocation_sites'] = sites
    if 'generated_records' in data:
        print(json.dumps({'generated_records': data['generated_records']}), flush=True)
    else:
        print(json.dumps({key: data[key] for key in (
            'revision', 'count', 'mode', 'trial', 'profiled', 'elapsed_seconds',
            'peak_rss_bytes', 'realized_records', 'memray_peak_bytes'
        ) if key in data}), flush=True)
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--counts', nargs='+', type=int, default=[10000, 100000, 1000000])
    parser.add_argument('--trials', type=int, default=3)
    parser.add_argument('--profile-trials', type=int, default=1)
    parser.add_argument('--trial-start', type=int, default=1)
    parser.add_argument('--modes', nargs='+', default=['both'])
    parser.add_argument('--preload', action='store_true')
    parser.add_argument('--result', default='results.json')
    args = parser.parse_args()
    (ROOT / 'condarc').write_text('channels: []\ntrack_features: []\n')
    output = {'versions': {name: importlib.metadata.version(name)
                           for name in ['memray', 'conda', 'conda-build', 'libmambapy']},
              'runs': []}
    for count in args.counts:
        if not (ROOT / 'data' / str(count) / 'manifest.json').exists():
            run(['generate', '--count', str(count)], f'generate-{count}')
        for mode in args.modes:
            for profiled, trials in [(False, args.trials), (True, args.profile_trials)]:
                for trial in range(args.trial_start, args.trial_start + trials):
                    revisions = ['base', 'pr'] if trial % 2 == 0 else ['pr', 'base']
                    for revision in revisions:
                        label = f'{revision}-{count}-{mode}-{trial}-{profiled}-{args.preload}'
                        command = ['worker', '--count', str(count), '--revision', revision,
                                   '--mode', mode, '--trial', str(trial)]
                        if profiled:
                            command.append('--profile')
                        if args.preload:
                            command.append('--preload')
                        output['runs'].append(run(command, label))
                        (ROOT / args.result).write_text(json.dumps(output, indent=2))


if __name__ == '__main__':
    main()
