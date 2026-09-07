"""Measure PR 1044 with deterministic repodata and independent processes."""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.abc
import importlib.util
import inspect
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

ROOT = Path(os.environ["LAZY_INDEX_OUTPUT_DIR"]).resolve()
REPO = Path(os.environ["CONDA_LIBMAMBA_SOLVER_REPO"]).resolve()
REVISIONS = {
    'base': '84de85e38f7009634c29b0162faeb060af0b5891',
    'pr': '33498b5d1d3fbf7c4c822fd7c5419e9268d483e4',
}


def generate_channel(path, count, subdir):
    path.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with (path / 'repodata.json').open('wb') as stream:
        def write(text):
            data = text.encode()
            stream.write(data)
            digest.update(data)

        write('{"info":{"subdir":' + json.dumps(subdir) + '},"packages":{')
        for number in range(count):
            name = f'benchmark-package-{number:07d}'
            filename = f'{name}-1.0-h0000000_0.tar.bz2'
            record = {
                'name': name,
                'version': '1.0',
                'build': 'h0000000_0',
                'build_number': 0,
                'depends': ['python >=3.12,<3.13.0a0', 'zlib >=1.2.13,<2.0a0'],
                'license': 'BSD-3-Clause',
                'md5': f'{number:032x}',
                'sha256': f'{number:064x}',
                'size': 123456,
                'subdir': subdir,
                'timestamp': 1788739200000,
            }
            if number:
                write(',')
            write(json.dumps(filename) + ':' + json.dumps(record, separators=(',', ':')))
        write('},"packages.conda":{},"removed":[],"repodata_version":1}')
    return {'records': count, 'bytes': (path / 'repodata.json').stat().st_size,
            'sha256': digest.hexdigest()}


def generate(count):
    fixture = ROOT / 'data' / str(count)
    manifest = {}
    for channel, subdir, records in (
        ('large', 'linux-64', count),
        ('large', 'noarch', 0),
        ('output', 'linux-64', 0),
        ('output', 'noarch', 1),
    ):
        key = f'{channel}/{subdir}'
        manifest[key] = generate_channel(fixture / key, records, subdir)
    (fixture / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps({'generated_records': count + 1, 'manifest': manifest}), flush=True)


def load_revision(revision):
    paths = subprocess.check_output(
        ['git', 'ls-tree', '-r', '--name-only', revision, '--', 'conda_libmamba_solver'],
        cwd=REPO, text=True,
    ).splitlines()
    sources = {}
    for path in paths:
        if not path.endswith('.py'):
            continue
        name = path[:-3].replace('/', '.')
        package = name.endswith('.__init__')
        if package:
            name = name[:-9]
        source = subprocess.check_output(
            ['git', 'show', revision + ':' + path], cwd=REPO, text=True,
        )
        sources[name] = (path, package, source)

    class RevisionLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        def find_spec(self, fullname, path=None, target=None):
            if fullname in sources:
                return importlib.util.spec_from_loader(
                    fullname, self, is_package=sources[fullname][1]
                )

        def create_module(self, spec):
            return None

        def exec_module(self, module):
            path, package, source = sources[module.__name__]
            module.__file__ = path
            if package:
                module.__path__ = []
            exec(compile(source, path, 'exec'), module.__dict__)

    sys.meta_path.insert(0, RevisionLoader())


def worker(args):
    import conda_build
    from conda.core.index import Index
    from conda.models.channel import Channel

    load_revision(REVISIONS[args.revision])
    from conda_libmamba_solver.solver import LibMambaSolver

    fixture = ROOT / 'data' / str(args.count)
    large = (fixture / 'large').as_uri()
    output = (fixture / 'output').as_uri()
    index = Index(channels=[large, output], prepend=False,
                  subdirs=('linux-64', 'noarch'), use_cache=False)
    solver = LibMambaSolver(prefix=ROOT / 'unused-env', channels=[large],
                           subdirs=('linux-64', 'noarch'))
    solver._index = index
    seen = {Channel(large)}
    assert '_data' not in index.__dict__
    assert all(not item._loaded for group in index.channels.values() for item in group)
    if args.preload:
        assert len(index.data) == args.count + 1
    inspect.stack()

    if args.mode == 'channels':
        solver._called_from_conda_build = lambda: True

    def install_actions():
        detected = solver._called_from_conda_build()
        channels = solver._collect_channels_subdirs_from_conda_build(seen=seen)
        return detected, channels

    def get_install_actions():
        return install_actions()

    state = '-preloaded' if args.preload else ''
    capture = ROOT / 'captures' / f'{args.revision}-{args.count}-{args.mode}-{args.trial}{state}.bin'
    if args.profile:
        import memray
        capture.parent.mkdir(exist_ok=True)
        tracker = memray.Tracker(
            destination=memray.FileDestination(capture, compress_on_exit=False),
            native_traces=False,
            trace_python_allocators=False,
            file_format=memray.FileFormat.ALL_ALLOCATIONS,
        )
    gc.collect()
    rss_before = int(subprocess.check_output(
        ['/bin/ps', '-o', 'rss=', '-p', str(os.getpid())], text=True
    ).strip()) * 1024
    maxrss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if args.profile:
        with tracker:
            start = time.perf_counter()
            detected, channels = get_install_actions()
            elapsed = time.perf_counter() - start
    else:
        start = time.perf_counter()
        detected, channels = get_install_actions()
        elapsed = time.perf_counter() - start
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    assert detected is True
    assert [channel.base_url for channel in channels] == [output]
    assert all(channel.platform is None for channel in channels)
    realized = '_data' in index.__dict__
    records = len(index.__dict__.get('_data', {}))
    assert realized == (args.preload or args.revision == 'base')
    assert records == (args.count + 1 if realized else 0)
    print(json.dumps({
        'revision': args.revision,
        'sha': REVISIONS[args.revision],
        'count': args.count,
        'mode': args.mode,
        'trial': args.trial,
        'profiled': args.profile,
        'preloaded': args.preload,
        'elapsed_seconds': elapsed,
        'rss_before_bytes': rss_before,
        'maxrss_before_bytes': maxrss_before,
        'peak_rss_bytes': peak_rss,
        'realized_records': records,
        'output_channels': 1,
        'capture': capture.name if args.profile else None,
        'python_version': sys.version.split()[0],
    }), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['generate', 'worker'])
    parser.add_argument('--count', type=int, required=True)
    parser.add_argument('--revision', choices=REVISIONS, default='base')
    parser.add_argument('--mode', choices=['both', 'channels'], default='both')
    parser.add_argument('--trial', type=int, default=0)
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--preload', action='store_true')
    args = parser.parse_args()
    if args.operation == 'generate':
        generate(args.count)
    else:
        worker(args)


if __name__ == '__main__':
    main()
