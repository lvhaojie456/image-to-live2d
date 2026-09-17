#!/usr/bin/env python3
"""Pinned HTTP Range downloads with durable block checkpoints and hash verification."""
import argparse
import concurrent.futures as cf
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.request


def digest(path, entry):
    h = hashlib.sha256() if entry.get('sha256') else hashlib.sha1()
    if not entry.get('sha256'):
        h.update(f"blob {entry['size']}\0".encode())
    with path.open('rb') as f:
        for b in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def download(entry, root, endpoint, workers=16):
    target = root / entry['model'] / entry['path']
    expected = entry.get('sha256') or entry['git_oid']
    if target.exists() and target.stat().st_size == entry['size'] and digest(target, entry) == expected:
        print('Verified existing:', target, flush=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = Path(str(target) + '.partial')
    checkpoint = Path(str(target) + '.blocks.json')
    block = 16 * 1024 * 1024
    count = (entry['size'] + block - 1) // block
    identity = {**entry, 'block': block}
    done = set()
    if checkpoint.exists() and partial.exists():
        state = json.loads(checkpoint.read_text())
        if state['identity'] != identity:
            raise RuntimeError('Checkpoint identity mismatch: ' + str(target))
        done = set(state['done'])
    fd = os.open(str(partial), os.O_CREAT | os.O_RDWR, 0o600)
    os.ftruncate(fd, entry['size'])
    url = f"{endpoint.rstrip('/')}/{entry['repo']}/resolve/{entry['revision']}/{entry['path']}"

    def fetch(index):
        start, end = index * block, min(entry['size'], (index + 1) * block) - 1
        for attempt in range(6):
            try:
                # Query key prevents broken intermediary caches reusing another range.
                req = urllib.request.Request(url + f'?download=true&block={index}&attempt={attempt}', headers={
                    'Range': f'bytes={start}-{end}', 'Accept-Encoding': 'identity',
                    'User-Agent': 'curl/8.7.1'})
                deadline = time.monotonic() + 120
                with urllib.request.urlopen(req, timeout=30) as response:
                    if response.status == 206:
                        if response.headers.get('Content-Range') != f'bytes {start}-{end}/{entry["size"]}':
                            raise RuntimeError('Invalid Content-Range')
                    elif not (response.status == 200 and start == 0 and end + 1 == entry['size']):
                        raise RuntimeError('Server ignored Range')
                    offset = start
                    while True:
                        if time.monotonic() > deadline:
                            raise TimeoutError('Block transfer exceeded 120 seconds')
                        data = response.read1(min(1024 * 1024, end + 2 - offset))
                        if not data:
                            break
                        if offset + len(data) > end + 1:
                            raise RuntimeError('Oversized response')
                        view = memoryview(data)
                        while view:
                            n = os.pwrite(fd, view, offset)
                            offset += n
                            view = view[n:]
                    if offset != end + 1:
                        raise RuntimeError('Truncated response')
                return index
            except Exception as exc:
                if attempt == 5:
                    raise
                print(f'Retry {index}: {type(exc).__name__}: {exc}', flush=True)
                time.sleep(min(2 ** attempt, 15))

    print(f'Downloading {target}: {len(done)}/{count} blocks, {entry["size"]/1e9:.2f} GB', flush=True)
    started, last = time.monotonic(), 0
    initial = len(done)
    try:
        with cf.ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(fetch, i) for i in range(count) if i not in done]
            for future in cf.as_completed(futures):
                done.add(future.result())
                os.fsync(fd)
                tmp = Path(str(checkpoint) + '.tmp')
                tmp.write_text(json.dumps({'identity': identity, 'done': sorted(done)}))
                tmp.replace(checkpoint)
                if time.monotonic() - last > 15 or len(done) == count:
                    elapsed = max(1, time.monotonic() - started)
                    print(f'{entry["model"]}/{entry["path"]}: {len(done)}/{count}, {(len(done)-initial)*block/elapsed/1e6:.1f} MB/s', flush=True)
                    last = time.monotonic()
    finally:
        os.close(fd)
    actual = digest(partial, entry)
    if actual != expected:
        raise RuntimeError(f'Hash mismatch for {target}: {actual}')
    partial.replace(target)
    checkpoint.unlink()
    print('HASH VERIFIED:', target, flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('manifest')
    p.add_argument('root')
    p.add_argument('--endpoint', default='https://hf-mirror.com')
    p.add_argument('--workers', type=int, default=16)
    args = p.parse_args()
    for entry in json.loads(Path(args.manifest).read_text()):
        download(entry, Path(args.root), args.endpoint, args.workers)


if __name__ == '__main__':
    main()
