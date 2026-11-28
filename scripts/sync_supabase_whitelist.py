#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把当前讲座统计键同步到 Supabase；只调用白名单 upsert RPC。"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TIMEOUT = 25
MAX_KEYS = 10_000

try:
    sys.stdout.reconfigure(newline='\n', encoding='utf-8')
except Exception:
    pass


def load_keys(path):
    with open(path, encoding='utf-8') as f:
        keys = json.load(f)
    if not isinstance(keys, list) or not keys or len(keys) > MAX_KEYS:
        raise ValueError('白名单必须是非空 JSON 数组，且不超过 %d 项' % MAX_KEYS)
    if any(not isinstance(key, str) or not key for key in keys):
        raise ValueError('白名单只能包含非空字符串')
    if len(set(keys)) != len(keys):
        raise ValueError('白名单不得包含重复键')
    if '__site__' not in keys:
        raise ValueError('白名单缺少 __site__')
    return keys


def load_config():
    url = os.environ.get('SUPABASE_URL', '').strip().rstrip('/')
    key = os.environ.get('SUPABASE_ANON_KEY', '').strip()
    if not url or not key:
        path = os.path.join(ROOT, 'site', 'supabase-config.json')
        with open(path, encoding='utf-8') as f:
            config = json.load(f)
        url = url or str(config.get('url', '')).strip().rstrip('/')
        key = key or str(config.get('anonKey', '')).strip()
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('SUPABASE_URL 必须是有效的 HTTP(S) 地址')
    if parsed.query or parsed.fragment:
        raise ValueError('SUPABASE_URL 不得包含查询串或片段')
    if not key:
        raise ValueError('缺少 SUPABASE_ANON_KEY')
    return url, key


def sync_keys(keys, token, config):
    url, anon_key = config
    body = json.dumps({'p_keys': keys}, ensure_ascii=False,
                      separators=(',', ':')).encode('utf-8')
    request = urllib.request.Request(url + '/rest/v1/rpc/sync_whitelist', data=body, method='POST')
    request.add_header('Content-Type', 'application/json')
    request.add_header('apikey', anon_key)
    request.add_header('Authorization', 'Bearer ' + anon_key)
    request.add_header('X-Sync-Token', token)
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        result = json.load(response)
    if not isinstance(result, dict):
        raise ValueError('sync_whitelist 返回的不是 JSON 对象')
    for field in ('received', 'upserted', 'total'):
        value = result.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError('sync_whitelist 返回字段 %s 无效' % field)
    expected = len(keys)
    if result['received'] != expected or result['upserted'] != expected:
        raise ValueError('白名单同步数量不符：received=%s upserted=%s expected=%s'
                         % (result['received'], result['upserted'], expected))
    if result['total'] < expected or result['total'] > MAX_KEYS:
        raise ValueError('白名单总行数异常：%s' % result['total'])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('keys_file', help='gen_supabase_sql.py --whitelist-json 生成的 JSON 文件')
    args = parser.parse_args(argv)
    token = os.environ.get('SUPABASE_WHITELIST_SYNC_TOKEN', '').strip()
    if not token:
        print('::warning::未配置 SUPABASE_WHITELIST_SYNC_TOKEN，跳过白名单自动同步；'
              '请在 Supabase 执行当前补丁并配置 GitHub Secret。')
        return 0
    try:
        keys = load_keys(args.keys_file)
        result = sync_keys(keys, token, load_config())
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print('::error::白名单同步失败（%s：%s）' % (type(exc).__name__, exc))
        return 1
    print('白名单同步成功：received=%d upserted=%d total=%d'
          % (result['received'], result['upserted'], result['total']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
