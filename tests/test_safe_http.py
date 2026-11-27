#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""出站 HTTP 的 DNS 重绑定、固定对端与重定向边界回归。"""
import ipaddress
import os
import socket
import ssl
import sys
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
os.environ.setdefault('SCNU_LLM_TEXT', '0')
os.environ.setdefault('SCNU_LLM_RICH', '0')

import parsers as P  # noqa: E402


PUBLIC_A = '8.8.8.8'
PUBLIC_B = '1.1.1.1'


def _infos(*addresses):
    out = []
    for address in addresses:
        family = socket.AF_INET6 if ':' in address else socket.AF_INET
        sockaddr = (address, 443, 0, 0) if family == socket.AF_INET6 else (address, 443)
        out.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', sockaddr))
    return out


def _resolver(mapping=None):
    mapping = mapping or {'a.example': PUBLIC_A, 'b.example': PUBLIC_B,
                          'a.scnu.edu.cn': PUBLIC_A,
                          'statics.scnu.edu.cn': PUBLIC_A}

    def resolve(host, port, **kwargs):
        return _infos(mapping[host])

    return resolve


class _Response:
    def __init__(self, status=200, location=None, body=b'ok'):
        self.status_code = status
        self.headers = {'Location': location} if location else {}
        self.body = body
        self.closed = False

    def close(self):
        self.closed = True

    def iter_content(self, chunk_size=65536):
        yield self.body

    def json(self):
        return {}


class DnsPolicyTest(unittest.TestCase):
    def test_01_mixed_public_private_answers_fail_closed(self):
        with mock.patch.object(P._socket, 'getaddrinfo',
                               return_value=_infos(PUBLIC_A, '127.0.0.1')):
            with self.assertRaises(P.UnsafeURLError):
                P._resolve_public_addresses('https://a.example/x')

    def test_02_private_and_reserved_addresses_are_rejected(self):
        for address in ('10.0.0.1', '169.254.169.254', '127.0.0.1',
                        '0.0.0.0', '::1', 'fc00::1', '::ffff:127.0.0.1'):
            with self.subTest(address=address):
                with mock.patch.object(P._socket, 'getaddrinfo', return_value=_infos(address)):
                    with self.assertRaises(P.UnsafeURLError):
                        P._resolve_public_addresses('https://a.example/x')

    def test_03_ipv4_mapped_public_address_is_accepted(self):
        with mock.patch.object(P._socket, 'getaddrinfo',
                               return_value=_infos('::ffff:8.8.8.8')):
            _parts, addresses = P._resolve_public_addresses('https://a.example/x')
        # IPv4-mapped IPv6 的字符串写法随 Python patch 版本变化
        # （3.12.10 为 ::ffff:808:808，3.12.16 为 ::ffff:8.8.8.8），
        # 按 IP 语义比对，避免把表示形式当成行为。
        self.assertEqual(len(addresses), 1)
        self.assertEqual(ipaddress.ip_address(addresses[0]),
                         ipaddress.ip_address('::ffff:8.8.8.8'))

    def test_04_dns_failure_is_a_connection_error(self):
        with mock.patch.object(P._socket, 'getaddrinfo', side_effect=socket.gaierror('no dns')):
            with self.assertRaises(P.requests.exceptions.ConnectionError):
                P._resolve_public_addresses('https://a.example/x')

    def test_05_invalid_url_shapes_never_resolve(self):
        for url in ('file:///etc/passwd', 'https://user:pass@a.example/x',
                    'https://a.example:0/x', 'https://a.example\n/x', ''):
            with self.subTest(url=url):
                with mock.patch.object(P._socket, 'getaddrinfo') as resolve:
                    with self.assertRaises(P.UnsafeURLError):
                        P._resolve_public_addresses(url)
                resolve.assert_not_called()


class _WrappedSocketResult:
    """兼容 urllib3 2.4 读 .is_verified 与 2.8 解包 (socket, is_verified)。"""
    def __init__(self, sock, is_verified=True):
        self.socket = sock
        self.is_verified = is_verified

    def __iter__(self):
        return iter((self.socket, self.is_verified))


class PinnedTransportTest(unittest.TestCase):
    def test_10_socket_connects_to_validated_numeric_ip(self):
        sock = mock.Mock()
        timeout = types.SimpleNamespace(connect_timeout=3.5)
        with mock.patch.object(P._socket, 'socket', return_value=sock):
            got = P._connect_pinned_socket(PUBLIC_A, 443, timeout,
                                           ('0.0.0.0', 0), [(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)])
        self.assertIs(got, sock)
        sock.settimeout.assert_called_once_with(3.5)
        sock.connect.assert_called_once_with((PUBLIC_A, 443))

    def test_11_failed_pinned_socket_is_closed(self):
        sock = mock.Mock()
        sock.connect.side_effect = OSError('refused')
        with mock.patch.object(P._socket, 'socket', return_value=sock):
            with self.assertRaises(OSError):
                P._connect_pinned_socket(PUBLIC_A, 443, 2, None, None)
        sock.close.assert_called_once_with()

    def test_11a_connection_falls_back_only_across_validated_addresses(self):
        cls = P._pinned_connection_class(P._HTTPConnection, (PUBLIC_A, PUBLIC_B))
        conn = cls('a.example', 443, timeout=2)
        sock = mock.Mock()
        with mock.patch.object(P, '_connect_pinned_socket',
                               side_effect=[OSError('ipv6 unavailable'), sock]) as connect:
            self.assertIs(conn._new_conn(), sock)
        self.assertEqual([c.args[0] for c in connect.call_args_list], [PUBLIC_A, PUBLIC_B])

    def test_12_connection_keeps_original_host_for_host_header_and_tls(self):
        cls = P._pinned_connection_class(P._HTTPConnection, PUBLIC_A)
        conn = cls('a.example', 443, timeout=2)
        sock = mock.Mock()
        with mock.patch.object(P, '_connect_pinned_socket', return_value=sock) as connect:
            self.assertIs(conn._new_conn(), sock)
        self.assertEqual(conn.host, 'a.example')
        connect.assert_called_once_with(PUBLIC_A, 443, 2, None, conn.socket_options)

    def test_13_https_sni_and_certificate_identity_use_original_hostname(self):
        cls = P._pinned_connection_class(P._HTTPSConnection, PUBLIC_A)
        conn = cls('api.example', 443, timeout=2)
        sock = mock.Mock()
        wrapped = _WrappedSocketResult(sock)
        # urllib3 2.8 改为解包返回值，锁住兼容契约（本地 2.4 走属性分支）
        self.assertEqual(tuple(wrapped), (sock, True))
        with mock.patch.object(P, '_connect_pinned_socket', return_value=sock) as connect, \
                mock.patch('urllib3.connection._ssl_wrap_socket_and_match_hostname',
                           return_value=wrapped) as wrap, \
                mock.patch('urllib3.connection.ssl_.ALPN_PROTOCOLS', []):
            conn.connect()
        connect.assert_called_once_with(PUBLIC_A, 443, 2, None, conn.socket_options)
        self.assertEqual(wrap.call_args.kwargs['server_hostname'], 'api.example')
        self.assertEqual(wrap.call_args.kwargs['cert_reqs'], ssl.CERT_REQUIRED)
        conn.close()

    def test_14_session_disables_environment_proxy_and_pins_both_schemes(self):
        created = []

        class Session:
            def __init__(self):
                self.mounts = {}
                self.closed = False
                self.calls = []
                created.append(self)

            def mount(self, prefix, adapter):
                self.mounts[prefix] = adapter

            def request(self, *args, **kwargs):
                self.calls.append((args, kwargs))
                return _Response()

            def close(self):
                self.closed = True

        with mock.patch.object(P.requests, 'Session', Session):
            response = P._request_with_pin('GET', 'https://a.example/x', PUBLIC_A,
                                           headers={'X-Test': '1'})
        session = created[0]
        self.assertFalse(session.trust_env)
        self.assertEqual(set(session.mounts), {'http://', 'https://'})
        for adapter in session.mounts.values():
            http_cls = adapter.poolmanager.pool_classes_by_scheme['http'].ConnectionCls
            https_cls = adapter.poolmanager.pool_classes_by_scheme['https'].ConnectionCls
            self.assertEqual(http_cls._pinned_ips, (PUBLIC_A,))
            self.assertEqual(https_cls._pinned_ips, (PUBLIC_A,))
        self.assertFalse(session.calls[0][1]['allow_redirects'])
        self.assertTrue(session.closed)
        response.close()

    def test_15_stream_response_close_releases_session(self):
        created = []

        class Session:
            def __init__(self):
                self.closed = False
                created.append(self)

            def mount(self, *args):
                pass

            def request(self, *args, **kwargs):
                return _Response()

            def close(self):
                self.closed = True

        with mock.patch.object(P.requests, 'Session', Session):
            response = P._request_with_pin('GET', 'http://a.example/x', PUBLIC_A, stream=True)
        session = created[0]
        self.assertFalse(session.closed)
        response.close()
        self.assertTrue(session.closed)

    def test_16_request_exception_releases_session(self):
        created = []

        class Session:
            def __init__(self):
                self.closed = False
                created.append(self)

            def mount(self, *args):
                pass

            def request(self, *args, **kwargs):
                raise OSError('connect failed')

            def close(self):
                self.closed = True

        with mock.patch.object(P.requests, 'Session', Session):
            with self.assertRaises(OSError):
                P._request_with_pin('GET', 'http://a.example/x', PUBLIC_A)
        self.assertTrue(created[0].closed)

    def test_17_real_adapter_uses_one_dns_lookup_and_original_host_header(self):
        seen = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                seen['host'] = self.headers.get('Host')
                seen['path'] = self.path
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'local-ok')

            def log_message(self, *args):
                pass

        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        real_connect = P._connect_pinned_socket

        def local_connect(ip, target_port, *args):
            self.assertEqual(ip, PUBLIC_A)
            self.assertEqual(target_port, port)
            return real_connect('127.0.0.1', target_port, *args)

        try:
            with mock.patch.object(P._socket, 'getaddrinfo',
                                   return_value=_infos(PUBLIC_A)) as dns, \
                    mock.patch.object(P, '_connect_pinned_socket', side_effect=local_connect):
                response = P.safe_request('GET', 'http://a.example:%d/x?q=1' % port,
                                          timeout=2)
                try:
                    self.assertEqual(response.content, b'local-ok')
                finally:
                    response.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(dns.call_count, 1)
        self.assertEqual(seen, {'host': 'a.example:%d' % port, 'path': '/x?q=1'})


class SafeRequestTest(unittest.TestCase):
    def _run(self, responses, url='https://a.example/start', method='GET', **kwargs):
        calls = []
        remaining = list(responses)

        def request(method, url, addresses, **kw):
            calls.append({'method': method, 'url': url, 'addresses': tuple(addresses),
                          'kw': kw})
            return remaining.pop(0)

        with mock.patch.object(P._socket, 'getaddrinfo', side_effect=_resolver()) as dns, \
                mock.patch.object(P, '_request_with_pin', side_effect=request):
            try:
                result = P.safe_request(method, url, **kwargs)
            except (P.UnsafeURLError, P.requests.exceptions.RequestException) as exc:
                result = exc
        return result, calls, dns

    def test_20_one_dns_resolution_per_request_and_numeric_ip_is_used(self):
        result, calls, dns = self._run([_Response()])
        self.assertEqual(result.status_code, 200)
        self.assertEqual(dns.call_count, 1)
        self.assertEqual(calls[0]['addresses'], (PUBLIC_A,))

    def test_21_redirect_revalidates_dns_and_pins_each_hop(self):
        first = _Response(302, 'https://b.example/next')
        result, calls, dns = self._run([first, _Response()])
        self.assertEqual(result.status_code, 200)
        self.assertEqual(dns.call_count, 2)
        self.assertEqual([c['url'] for c in calls],
                         ['https://a.example/start', 'https://b.example/next'])
        self.assertEqual([c['addresses'] for c in calls], [(PUBLIC_A,), (PUBLIC_B,)])
        self.assertTrue(first.closed)

    def test_21a_zero_redirect_budget_returns_raw_response_for_manual_loop(self):
        response = _Response(302, '/next')
        result, calls, _dns = self._run([response], max_redirects=0)
        self.assertIs(result, response)
        self.assertFalse(response.closed)
        self.assertEqual(len(calls), 1)
        response.close()

    def test_21b_safe_fetch_rejects_https_downgrade(self):
        response = _Response(302, 'http://b.example/next')
        with mock.patch.object(P._socket, 'getaddrinfo', side_effect=_resolver()), \
                mock.patch.object(P, '_request_with_pin', return_value=response) as request:
            self.assertIsNone(P._safe_fetch('https://statics.scnu.edu.cn/x.pdf'))
        self.assertEqual(request.call_count, 1)
        self.assertTrue(response.closed)

    def test_21c_safe_fetch_contains_stream_failure_and_closes_response(self):
        class BrokenResponse(_Response):
            def iter_content(self, chunk_size=65536):
                yield b'%PDF-'
                raise P.requests.exceptions.ChunkedEncodingError('truncated body')

        response = BrokenResponse()
        failure = {}
        with mock.patch.object(P, 'safe_request', return_value=response):
            data = P._safe_fetch('https://statics.scnu.edu.cn/x.pdf',
                                 error_out=failure)
        self.assertIsNone(data)
        self.assertTrue(response.closed)
        self.assertIsInstance(failure['error'], P.requests.exceptions.ChunkedEncodingError)

    def test_22_private_redirect_is_rejected_before_second_request(self):
        mapping = {'a.example': PUBLIC_A, 'b.example': '127.0.0.1'}
        calls = []

        def request(*args, **kwargs):
            calls.append(args)
            return _Response(302, 'https://b.example/next')

        with mock.patch.object(P._socket, 'getaddrinfo', side_effect=_resolver(mapping)), \
                mock.patch.object(P, '_request_with_pin', side_effect=request):
            with self.assertRaises(P.UnsafeURLError):
                P.safe_request('GET', 'https://a.example/start')
        self.assertEqual(len(calls), 1)

    def test_23_cross_origin_redirect_strips_credentials_case_insensitively(self):
        _result, calls, _dns = self._run(
            [_Response(302, 'https://b.example/next'), _Response()],
            headers={'authorization': 'Bearer secret', 'COOKIE': 'sid=1',
                     'Proxy-Authorization': 'Basic x', 'X-Keep': 'yes',
                     'Host': 'injected.example'},
            auth=('user', 'pass'), cookies={'sid': '1'}, cert='client.pem')
        headers = calls[0]['kw']['headers']
        self.assertNotIn('Host', headers)
        self.assertEqual(calls[1]['kw']['headers'], {'X-Keep': 'yes'})
        for key in ('auth', 'cookies', 'cert'):
            self.assertNotIn(key, calls[1]['kw'])

    def test_23a_bytes_header_names_are_normalized_before_redirect_scrubbing(self):
        _result, calls, _dns = self._run(
            [_Response(302, 'https://b.example/next'), _Response()],
            headers={b'authorization': b'Bearer secret', b'COOKIE': b'sid=1',
                     b'Proxy-Authorization': b'Basic x', b'Host': b'injected.example',
                     'X-Keep': 'yes'})
        self.assertNotIn('Host', calls[0]['kw']['headers'])
        self.assertEqual(calls[1]['kw']['headers'], {'X-Keep': 'yes'})

    def test_24_same_origin_redirect_keeps_credentials_and_body(self):
        _result, calls, _dns = self._run(
            [_Response(307, '/next'), _Response()], method='POST',
            headers={'Authorization': 'Bearer secret'}, json={'x': 1})
        self.assertEqual(calls[1]['kw']['headers']['Authorization'], 'Bearer secret')
        self.assertEqual(calls[1]['kw']['json'], {'x': 1})

    def test_25_cross_origin_307_and_308_reject_write_forwarding(self):
        for status in (307, 308):
            with self.subTest(status=status):
                result, calls, _dns = self._run(
                    [_Response(status, 'https://b.example/next'), _Response()],
                    method='POST', json={'x': 1})
                self.assertIsInstance(result, P.UnsafeURLError)
                self.assertEqual(len(calls), 1)
        result, calls, _dns = self._run(
            [_Response(307, 'https://b.example/next'), _Response()],
            method='GET', data=b'write-via-get')
        self.assertIsInstance(result, P.UnsafeURLError)
        self.assertEqual(len(calls), 1)

    def test_25a_301_302_303_clear_body_even_when_method_is_unchanged(self):
        for status in (301, 302, 303):
            with self.subTest(status=status):
                _result, calls, _dns = self._run(
                    [_Response(status, '/next'), _Response()],
                    method='GET', data=b'body',
                    headers={'Content-Type': 'text/plain', 'Content-Length': '4'})
                self.assertEqual(calls[1]['method'], 'GET')
                self.assertNotIn('data', calls[1]['kw'])
                self.assertEqual(calls[1]['kw']['headers'], {})

    def test_26_post_301_302_and_303_become_bodyless_get(self):
        for status in (301, 302, 303):
            with self.subTest(status=status):
                _result, calls, _dns = self._run(
                    [_Response(status, 'https://b.example/next'), _Response()],
                    method='POST', json={'x': 1},
                    headers={'Content-Type': 'application/json', 'Content-Length': '9'})
                self.assertEqual(calls[1]['method'], 'GET')
                for key in ('json', 'data', 'files'):
                    self.assertNotIn(key, calls[1]['kw'])
                self.assertEqual(calls[1]['kw']['headers'], {})

    def test_27_https_downgrade_and_non_http_redirect_are_rejected(self):
        for location in ('http://b.example/x', 'file:///etc/passwd'):
            with self.subTest(location=location):
                result, calls, _dns = self._run(
                    [_Response(302, location), _Response()])
                self.assertIsInstance(result, P.UnsafeURLError)
                self.assertEqual(len(calls), 1)

    def test_28_missing_location_and_redirect_limit_fail_closed(self):
        with mock.patch.object(P._socket, 'getaddrinfo', side_effect=_resolver()), \
                mock.patch.object(P, '_request_with_pin', return_value=_Response(302)):
            with self.assertRaises(P.UnsafeURLError):
                P.safe_request('GET', 'https://a.example/start')
        responses = [_Response(302, '/again'), _Response(302, '/again')]
        result, calls, _dns = self._run(responses, max_redirects=1)
        self.assertIsInstance(result, P.UnsafeURLError)
        self.assertEqual(len(calls), 2)

    def test_29_explicit_proxy_is_rejected_before_dns(self):
        with mock.patch.object(P._socket, 'getaddrinfo') as dns:
            with self.assertRaises(P.UnsafeURLError):
                P.safe_request('GET', 'https://a.example/x',
                               proxies={'https': 'http://proxy.example:8080'})
        dns.assert_not_called()

    def test_30_certificate_exception_does_not_escape_to_external_host(self):
        responses = [_Response(302, 'https://b.example/next'), _Response()]
        # _safe_fetch owns its redirect loop and must restore verification outside SCNU.
        with mock.patch.object(P._socket, 'getaddrinfo', side_effect=_resolver()), \
                mock.patch.object(P, '_request_with_pin',
                                  side_effect=responses) as request:
            data = P._safe_fetch('https://statics.scnu.edu.cn/x.pdf', verify=False)
        self.assertEqual(data, b'ok')
        self.assertEqual([c.kwargs['verify'] for c in request.call_args_list],
                         [False, True])
    def test_30a_verify_false_is_limited_to_exact_known_host(self):
        cases = [('https://statics.scnu.edu.cn/x.pdf', False),
                 ('https://other.scnu.edu.cn/x.pdf', True),
                 ('https://evilscnu.edu.cn/x.pdf', True)]
        for url, expected in cases:
            with self.subTest(url=url):
                with mock.patch.object(P, 'safe_request', return_value=_Response()) as request:
                    P._safe_fetch(url, verify=False)
                self.assertEqual(request.call_args.kwargs['verify'], expected)

    def test_30b_only_statics_ssl_error_retries_without_verification(self):
        with mock.patch.object(P, 'safe_request', side_effect=[
                P.requests.exceptions.SSLError('broken chain'),
                _Response(body=b'%PDF-1.7')]) as request:
            data, err = P._fetch_pdf_bytes('https://statics.scnu.edu.cn/x.pdf')
        self.assertEqual(data, b'%PDF-1.7')
        self.assertIsNone(err)
        self.assertEqual([c.kwargs['verify'] for c in request.call_args_list], [True, False])

    def test_30c_non_ssl_failures_and_other_hosts_never_disable_verification(self):
        cases = [('https://statics.scnu.edu.cn/x.pdf',
                  P.requests.exceptions.ConnectionError('reset')),
                 ('https://other.scnu.edu.cn/x.pdf',
                  P.requests.exceptions.SSLError('broken chain')),
                 ('https://evilscnu.edu.cn/x.pdf',
                  P.requests.exceptions.SSLError('broken chain'))]
        for url, error in cases:
            with self.subTest(url=url, error=type(error).__name__):
                with mock.patch.object(P, 'safe_request', side_effect=error) as request:
                    data, _err = P._fetch_pdf_bytes(url)
                self.assertIsNone(data)
                self.assertEqual(request.call_count, 1)
                self.assertTrue(request.call_args.kwargs['verify'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
