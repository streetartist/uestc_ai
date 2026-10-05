"""Bounded, read-only public-site load test. Run from a separate machine."""
import argparse
import concurrent.futures
import http.client
import json
import random
import threading
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='https://uestcai.top')
    parser.add_argument('--output', default='load-test.json')
    args = parser.parse_args()
    origin = urlsplit(args.url)
    if origin.scheme != 'https' or origin.path not in ('', '/'):
        parser.error('Use an HTTPS origin without a path')
    stop = threading.Event()
    lock = threading.Lock()
    records = []
    started = time.time()
    health = []
    pages = [
        ['/', '/api/competitions?status=published', '/api/competitions/wujie-cup-2026', '/api/content'],
        ['/competitions/wujie-cup-2026', '/api/competitions/wujie-cup-2026'],
        ['/problems', '/api/problems'],
    ]

    def request(connection, path, stage):
        begin = time.monotonic()
        status, size, cache, error = 0, 0, None, None
        try:
            connection.request('GET', path, headers={
                'User-Agent': 'UESTC-AI-Authorized-Load-Test/1.0',
                'Accept-Encoding': 'gzip', 'Accept': 'text/html,application/json',
            })
            response = connection.getresponse()
            status = response.status
            cache = response.getheader('X-Cache')
            size = len(response.read())
        except Exception as exc:
            error = type(exc).__name__
            connection.close()
        row = {'stage': stage, 'path': path, 'status': status,
               'ms': round((time.monotonic()-begin)*1000, 2),
               'bytes': size, 'cache': cache, 'error': error}
        with lock:
            records.append(row)
            recent = records[-100:]
            if len(recent) == 100 and sum(r['status'] == 0 or r['status'] >= 500 for r in recent) >= 20:
                stop.set()
        return row

    def connection():
        return http.client.HTTPSConnection(origin.hostname, origin.port or 443, timeout=10)

    def guard():
        failures = 0
        while not stop.wait(5):
            conn = connection()
            begin = time.monotonic()
            try:
                conn.request('GET', '/', headers={'User-Agent': 'UESTC-AI-Load-Health/1.0', 'Accept-Encoding': 'gzip'})
                response = conn.getresponse()
                response.read()
                status = response.status
            except Exception:
                status = 0
            finally:
                conn.close()
            health.append({'at_s': round(time.time()-started, 2), 'status': status,
                           'ms': round((time.monotonic()-begin)*1000, 2)})
            failures = failures+1 if status == 0 or status >= 500 else 0
            if failures >= 3:
                stop.set()

    def percentile(values, q):
        return round(sorted(values)[max(0, int((len(values)-1)*q))], 2) if values else None

    def summary(stage, elapsed):
        subset = [r for r in records if r['stage'] == stage]
        success = [r['ms'] for r in subset if r['status'] == 200]
        result = {'stage': stage, 'seconds': round(elapsed, 2), 'requests': len(subset),
                  'rps': round(len(subset)/max(elapsed, .001), 2),
                  'statuses': dict(Counter(str(r['status']) for r in subset)),
                  'success_p50_ms': percentile(success, .5), 'success_p95_ms': percentile(success, .95),
                  'success_p99_ms': percentile(success, .99),
                  'bytes': sum(r['bytes'] for r in subset),
                  'cache': dict(Counter(r['cache'] or 'NONE' for r in subset))}
        print(json.dumps(result), flush=True)
        return result

    summaries = []
    guard_thread = threading.Thread(target=guard, daemon=True)
    guard_thread.start()
    for users, duration in [(10, 10), (50, 10), (150, 15), (300, 45)]:
        if stop.is_set():
            break
        stage = f'browsing-{users}'
        begin = time.monotonic()
        deadline = begin+duration

        def visitor(user):
            rng = random.Random(users*1000+user)
            conn = connection()
            try:
                if stop.wait(rng.uniform(0, min(10, duration/2))):
                    return
                cycle = 0
                while time.monotonic() < deadline and not stop.is_set():
                    for path in pages[(user+cycle) % len(pages)]:
                        if stop.is_set():
                            break
                        request(conn, path, stage)
                    cycle += 1
                    if stop.wait(min(rng.uniform(8, 12), max(0, deadline-time.monotonic()))):
                        break
            finally:
                conn.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=users) as executor:
            list(executor.map(visitor, range(users)))
        summaries.append(summary(stage, time.monotonic()-begin))

    if not stop.is_set():
        stage = 'homepage-burst-300'
        ready = threading.Barrier(300)
        begin = time.monotonic()

        def burst(_):
            conn = connection()
            try:
                ready.wait(timeout=20)
                request(conn, '/', stage)
            finally:
                conn.close()

        with concurrent.futures.ThreadPoolExecutor(max_workers=300) as executor:
            list(executor.map(burst, range(300)))
        summaries.append(summary(stage, time.monotonic()-begin))
    aborted = stop.is_set()
    stop.set()
    guard_thread.join(timeout=12)
    by_path = {}
    for path in sorted({r['path'] for r in records}):
        subset = [r for r in records if r['path'] == path]
        success = [r['ms'] for r in subset if r['status'] == 200]
        by_path[path] = {'requests': len(subset), 'statuses': dict(Counter(str(r['status']) for r in subset)),
                         'success_p95_ms': percentile(success, .95)}
    result = {'origin': args.url, 'started_unix': started, 'finished_unix': time.time(),
              'aborted': aborted, 'single_source_ip': True, 'think_time_seconds': [8, 12],
              'notes': 'Read-only HTML and JSON; no JavaScript, static asset download, login, uploads or evaluation execution.',
              'stages': summaries, 'by_path': by_path, 'health': health, 'requests': records}
    Path(args.output).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'complete': True, 'aborted': aborted, 'total_requests': len(records)}), flush=True)


if __name__ == '__main__':
    main()
