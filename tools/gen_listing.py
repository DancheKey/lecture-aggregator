"""根据 scraper/sources.yaml 重新生成《信息源网址清单.md》。"""
import os
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'scraper', 'sources.yaml')
OUT = os.path.join(ROOT, '信息源网址清单.md')

CAMPUS_ORDER = ['石牌', '大学城', '汕尾', '佛山', '校级']


def main():
    with open(SRC, encoding='utf-8') as f:
        cfg = yaml.safe_load(f) or {'sources': []}
    sources = cfg['sources']

    groups = {c: [] for c in CAMPUS_ORDER}
    for s in sources:
        c = s.get('campus', '校级')
        groups.setdefault(c, []).append(s)

    lines = []
    lines.append('# 华南师范大学讲座信息采集源清单')
    lines.append('')
    lines.append('> 本文件列出了所有被爬取的学院 / 研究院 / 部处的讲座发布页面 URL。')
    lines.append('> 可直接在浏览器中打开这些链接查看原始讲座通知。')
    lines.append('> 数据依据华南师范大学官方「机构设置」页面（教学科研机构 / 党政职能部门 / 教辅机构）整理，并逐站核实栏目可达。')
    lines.append('')
    lines.append('| # | 名称 | 校区 | 主页 | 栏目 URL |')
    lines.append('|---|------|------|------|----------|')

    idx = 0
    for c in CAMPUS_ORDER:
        for s in groups.get(c, []):
            idx += 1
            base = s['base']
            urls = s.get('list_urls', [])
            # 栏目展示：首条 + 其余以“、”连接，并标注额外页数
            extra = len(urls) - 1
            if extra > 0:
                suffix = f' +{extra}页'
            else:
                suffix = ''
            primary = urls[0] if urls else base
            if len(urls) > 1:
                rest = '、'.join(urls[1:])
                col = f'[{primary}]({primary})（{rest}）{suffix}'
            else:
                col = f'[{primary}]({primary}){suffix}'
            lines.append(
                f'| {idx} | {s["name"]} | {c} | [{base}]({base}) | {col} |'
            )
    lines.append('')
    lines.append(f'**共计 {len(sources)} 个来源**')
    lines.append('')
    for c in CAMPUS_ORDER:
        n = len(groups.get(c, []))
        if n:
            lines.append(f'- **{c}**：{n} 个')
    lines.append('')
    lines.append('## 说明')
    lines.append('')
    lines.append('- 「+N页」表示该栏目还抓取了第 2 页及以后的分页地址。')
    lines.append('- 部处类来源（图书馆、研究生院、科学研究院、国际交流合作处、教师发展中心、人文社会科学高等研究院、社会科学处等）仅纳入确实发布讲座 / 学术活动信息的单位。')
    lines.append('- 科学技术处（kjc.scnu.edu.cn）官网显示「站点未开通」，暂无法采集，已跳过；其内容通常由科学研究院代为发布。')
    lines.append('- 已剔除机构设置中不再列出的旧域名来源（生物光子学研究院、信息光电子科技学院、软件学院、职业教育学院）。')

    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    print(f'已生成 {OUT}，共 {len(sources)} 个来源')


if __name__ == '__main__':
    main()
