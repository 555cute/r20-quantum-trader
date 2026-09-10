"""Deterministic V5 wire fixture; never replaces protection/trading algorithms."""
import copy
import io
import json
from urllib.parse import urlsplit
from unittest.mock import patch


class AlgoHTTP:
    def __init__(self):
        self.requests = []
        self.rows = [self.row(side=side) for side in ('long', 'short')]
        self.pending = []
        self.failures = {}

    @staticmethod
    def row(side='long', size='100', inst='ETH-USDT-SWAP', sl='2400'):
        return dict(algoId='algo_' + side, instId=inst, state='live',
                    posSide=side, side='sell' if side == 'long' else 'buy',
                    reduceOnly='true', sz=size, tpTriggerPx='2600', slTriggerPx=sl)

    def calls(self, path):
        return [(method, body) for method, route, body in self.requests if route == path]

    def __call__(self, req, timeout=None):
        path = urlsplit(req.full_url).path
        body = json.loads(req.data) if req.data else None
        self.requests.append((req.get_method(), path, body))
        headers = dict((k.lower(), v) for k, v in req.header_items())
        assert headers['ok-access-key'] == 'fixture-key'
        assert headers['x-simulated-trading'] == '1'
        assert headers['ok-access-sign']
        if path in self.failures:
            payload = self.failures[path]
        else:
            if path.endswith('/orders-algo-pending'):
                rows = self.pending.pop(0) if self.pending else self.rows
            elif path.endswith('/order-algo'):
                row = dict(body, algoId='created', state='live')
                self.rows.append(row)
                rows = [{'algoId': 'created', 'sCode': '0'}]
            elif path.endswith('/amend-algos'):
                assert isinstance(body, dict) and body['instId']
                for row in self.rows:
                    if row['algoId'] == body['algoId'] and row['instId'] == body['instId']:
                        row['slTriggerPx'] = body['newSlTriggerPx']
                rows = [{'algoId': body['algoId'], 'sCode': '0'}]
            elif path.endswith(('/orders-pending', '/positions')):
                rows = []
            elif path.endswith('/close-position'):
                rows = [{'instId': body['instId'], 'sCode': '0'}]
            else:
                raise AssertionError('Unexpected HTTP request: ' + req.full_url)
            payload = {'code': '0', 'data': copy.deepcopy(rows)}
        return io.BytesIO(json.dumps(payload).encode())


def install_http(test, trader):
    from scripts import okx_runtime
    # Restore even an earlier caller's frozen context; do not consult .env/secrets.
    frozen = patch.object(okx_runtime, '_FROZEN_ENVIRONMENT', None)
    frozen.start()
    test.addCleanup(frozen.stop)
    okx_runtime.freeze_environment(dict(R20_OKX_ENV='demo', OKX_DEMO_API_KEY='fixture-key',
                                      OKX_DEMO_SECRET_KEY='fixture-secret', OKX_DEMO_PASSPHRASE='fixture-pass'))
    wire = AlgoHTTP()
    p = patch.object(trader.okx_rest, 'urlopen', side_effect=wire)
    p.start()
    test.addCleanup(p.stop)
    for name in ('record_trade', 'record_signal_snapshot', 'add_stop_cooldown', 'notify_trade_close'):
        p = patch.object(trader, name)
        p.start()
        test.addCleanup(p.stop)
    p = patch.object(trader.time, 'sleep')
    p.start()
    test.addCleanup(p.stop)
    return wire
