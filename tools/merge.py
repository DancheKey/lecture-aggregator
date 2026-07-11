"""合并信息源：保留已验证可用源，剔除旧域名，追加新发现的单位。"""
import os
import json
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_YAML = os.path.join(ROOT, 'scraper', 'sources.yaml')
NEW_JSON = os.path.join(ROOT, 'tools', 'new_sources.json')

# 机构页上已不存在的旧域名（曾成功采集，但现行机构设置已无这些单位，且其讲座多并入现有单位）
LEGACY_BASES = {
    'biop.scnu.edu.cn',   # 生物光子学研究院 -> 现并入 光电/华南先进光电子
    'ioe.scnu.edu.cn',    # 信息光电子科技学院 -> 现 光电科学与工程学院
    'ss.scnu.edu.cn',     # 软件学院（机构页已无）
    'zyjy.scnu.edu.cn',   # 职业教育学院（机构页已无）
}


def load_current():
    with open(SRC_YAML, encoding='utf-8') as f:
        return yaml.safe_load(f) or {'sources': []}


def expand(u):
    """标准栏目根路径追加 /2.html 翻页，提升召回（缺失则采集器自动跳过）。"""
    out = [u]
    if u.endswith('/'):
        p2 = u + '2.html'
        if p2 not in out:
            out.append(p2)
    return out


def main():
    cfg = load_current()
    kept = [s for s in cfg['sources'] if s['base'].lower().replace('https://', '').replace('http://', '').strip('.') not in LEGACY_BASES]
    dropped = [s['name'] for s in cfg['sources'] if s not in kept]
    print(f'原条目 {len(cfg["sources"])}，剔除 {len(dropped)}：{dropped}')

    with open(NEW_JSON, encoding='utf-8') as f:
        news = json.load(f)
    added = []
    for s in news:
        if s['name'] == '科学技术处':
            print('跳过 科学技术处：站点未开通(kjc.scnu.edu.cn 返回“站点未开通”)，无法采集')
            continue
        urls = []
        for u in s.get('list_urls', []):
            if u == '__PROBE__':
                continue
            urls.extend(expand(u))
        # 去重保持顺序
        seen = set(); uniq = []
        for u in urls:
            if u not in seen:
                seen.add(u); uniq.append(u)
        entry = {'name': s['name'], 'campus': s['campus'], 'base': s['base'], 'list_urls': uniq}
        kept.append(entry)
        added.append(s['name'])

    with open(SRC_YAML, 'w', encoding='utf-8') as f:
        yaml.dump({'sources': kept}, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    print(f'新增 {len(added)}：{added}')
    print(f'最终信息源总数：{len(kept)}')


if __name__ == '__main__':
    main()
