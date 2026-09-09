import json
import time
from urllib.parse import quote, urlencode
from urllib.request import Request, build_opener, ProxyHandler
from .storage import ControllerError


class API:
    def __init__(self, config):
        self.base = 'http://' + config['external-controller']
        self.secret = config['secret']
        self.opener = build_opener(ProxyHandler({}))

    def request(self, path, method='GET', data=None):
        request = Request(self.base + path, method=method,
                          headers={'Authorization': 'Bearer ' + self.secret, 'Content-Type': 'application/json'},
                          data=json.dumps(data).encode() if data is not None else None)
        try:
            with self.opener.open(request, timeout=6) as response:
                body = response.read(8 * 1024 * 1024)
                value = json.loads(body) if body else {}
                if not isinstance(value, dict):
                    raise ValueError('Expected API object')
                return value
        except Exception:
            raise ControllerError('Mihomo API request failed (authentication, timeout, or invalid response).') from None

    def groups(self):
        return {name: {'type': item.get('type'), 'now': item.get('now'), 'all': item['all']}
                for name, item in self.request('/proxies')['proxies'].items() if 'all' in item}

    def select(self, group, node):
        groups = self.groups()
        if group not in groups or node not in groups[group]['all']:
            raise ControllerError('Unknown proxy group or node.')
        self.request('/proxies/' + quote(group, safe=''), 'PUT', {'name': node})

    def latency(self, node):
        return self.request('/proxies/' + quote(node, safe='') + '/delay?' +
                            urlencode({'url': 'https://www.gstatic.com/generate_204', 'timeout': 5000}))

    def latencies(self, nodes):
        results = {}
        for node in dict.fromkeys(nodes):
            try:
                results[node] = self.latency(node).get('delay')
            except ControllerError:
                results[node] = None
        return results

    def verify(self, expected):
        actual = self.request('/configs')
        tun = actual.get('tun')
        actual_tun = tun.get('enable') if isinstance(tun, dict) else tun
        if actual_tun is not expected['tun']['enable'] or actual.get('mode') != expected['mode']:
            raise ControllerError(f"RUNTIME MISMATCH: desired TUN={expected['tun']['enable']}, runtime TUN={actual_tun}; desired mode={expected['mode']}, runtime mode={actual.get('mode')}.")
        groups = self.groups()
        wanted = {group['name'] for group in expected.get('proxy-groups', [])}
        if not wanted.issubset(groups):
            raise ControllerError('Runtime is missing expected proxy groups.')
        return actual

    def wait(self, expected, timeout=25):
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            try:
                return self.verify(expected)
            except ControllerError as error:
                last = error
                time.sleep(0.4)
        raise ControllerError(f'Runtime verification failed: {last}')
