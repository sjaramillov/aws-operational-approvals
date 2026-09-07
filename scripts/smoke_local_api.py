#!/usr/bin/env python3
"""Exercise the real loopback HTTP adapter with synthetic inputs, then close it."""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sales_demo.backend.local_server import Handler


def main() -> int:
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    checks = []
    base = f'http://127.0.0.1:{server.server_port}'

    def request(method, path, *, actor=None, body=None, key=None):
        headers = {'Content-Type': 'application/json'}
        if actor:
            headers['x-demo-sub'] = actor
        if key:
            headers['Idempotency-Key'] = key
        data = None if body is None else json.dumps(body).encode()
        req = Request(base + path, data=data, headers=headers, method=method)
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as exc:
            response = exc
        with response:
            return response.status, json.load(response)

    def check(name, condition):
        if not condition:
            raise RuntimeError(f'local smoke failed: {name}')
        checks.append(name)

    try:
        status, health = request('GET', '/health')
        check('health_active', status == 200 and health['status'] == 'ACTIVE')
        status, _ = request('GET', '/me')
        check('anonymous_denied', status == 401)
        keys = {n: f'local-smoke-{n}-0123456789abcdef' for n in (50, 51, 52)}
        ids = {}
        for n in (50, 51, 52):
            status, body = request('POST', '/applications', actor='customer-a', body={'vehicleCount': n}, key=keys[n])
            check(f'create_{n}', status == 201)
            ids[n] = body['application']['applicationId']
        status, body = request('POST', '/applications', actor='customer-a', body={'vehicleCount': 50}, key=keys[50])
        check('idempotent_replay', status == 200 and body['replayed'] is True and body['application']['applicationId'] == ids[50])
        status, body = request('GET', f'/applications/{ids[50]}', actor='customer-a')
        check('automatic_activation', status == 200 and body['application']['status'] == 'CONTRACT_ACTIVE')
        status, _ = request('GET', f'/applications/{ids[51]}', actor='customer-b')
        check('tenant_read_denied', status == 404)
        status, _ = request('POST', f'/approvals/{ids[51]}/decision', actor='manager-b', body={'decision':'APPROVE','reasonCode':'CAPACITY_CONFIRMED'})
        check('tenant_decision_denied', status == 404)
        for n, decision, reason, expected in ((51,'APPROVE','CAPACITY_CONFIRMED','CONTRACT_ACTIVE'),(52,'REJECT','CAPACITY_NOT_AVAILABLE','REJECTED')):
            status, body = request('POST', f'/approvals/{ids[n]}/decision', actor='manager-a', body={'decision':decision,'reasonCode':reason})
            check(f'decision_{n}_accepted', status == 202 and body['accepted'] is True)
            deadline = time.monotonic() + 2
            while True:
                status, body = request('GET', f'/applications/{ids[n]}', actor='customer-a')
                if status != 200 or body['application']['status'] != 'PENDING_MANAGER' or time.monotonic() >= deadline:
                    break
                time.sleep(0.02)
            check(f'decision_{n}_finalized', status == 200 and body['application']['status'] == expected and body['application']['reasonCode'] == reason)
            check(f'audit_{n}', any(entry['action'] == 'MANAGER_DECISION_RECORDED' for entry in body['application']['auditTrail']))
        print(json.dumps({'status':'PASS','scope':'loopback HTTP + Python in-memory adapters','checks':checks,'checks_passed':len(checks),'aws_calls':0,'frontend_integration':False},indent=2))
        return 0
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


if __name__ == '__main__':
    raise SystemExit(main())
