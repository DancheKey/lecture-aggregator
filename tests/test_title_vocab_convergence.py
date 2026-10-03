# -*- coding: utf-8 -*-
"""职称词表收敛守卫（2026-10-02）：parsers.py 不得再手抄主表之外的职称词。

## 背景

项目铁律：职称/职务词表只维护一份（`scraper/field_vocab.py`），其他代码引用它。
手抄副本会与主表悄悄脱节，产生「同一份数据、不同代码给出不同答案」——
真实发生过：库里有一条「广东省计算机学会秘书长」，一条代码路径认为它是正常单位、
另一条判它是脏值，因为两边的词表差了一个「秘书长」
（现场记录见 field_vocab.py 的 ORG_TITLE_SUFFIXES 注释）。

2026-09-26 的 G4 收敛清了 7 处主副本，但 parsers.py 里仍有 20+ 处内联职称串。
本守卫的作用不是"一次性清理"，而是**防止将来新增职称时副本再次漂移**：
往主表加词后若忘了改内联副本，A 处认识、B 处不认识，行为不一致且极难排查。

## 判据

扫 parsers.py 的字符串常量，抽出其中形如「X教授 / X研究员 / …」的职称词，
断言其**全部落在主表并集**（NAME + ORG + HONORIFIC + COMPLEX）内。

不在并集内的合法例外只有一类：把整个词写死的**具名文本**
（如「国家杰青刘梦赤教授」是页面原文片段），它们不是词表，已在白名单中排除。
"""
import ast
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scraper'))

import field_vocab as FV  # noqa: E402

PARSERS = os.path.join(ROOT, 'scraper', 'parsers.py')

# 主表并集：加新职称只改 field_vocab，本守卫自动跟随
MASTER = (set(FV.NAME_TITLE_SUFFIXES) | set(FV.ORG_TITLE_SUFFIXES)
          | set(FV.HONORIFIC_SUFFIXES) | set(FV.COMPLEX_TITLE_SUFFIXES))

# 修饰前缀 + 基础职称（由 TITLE_MODIFIERS 派生，主表不含展开形）
MOD_WORDS = {f'{m}{t}' for m in FV.TITLE_MODIFIERS
             for t in ('研究员', '教授', '讲师')}
# 学段/学科 + 高级职称的组合形态
SUBJ = ('数学', '语文', '英语', '物理', '化学', '生物', '政治', '历史',
        '地理', '科学', '美术', '音乐', '体育', '信息技术')
GRADE = ('中学', '小学', '初中', '高中')
ADV = ('高级教师', '高级讲师', '高级实验师', '高级工程师', '高级会计师', '高级经济师')
COMBO = {f'{s}{a}' for s in SUBJ for a in ADV}
COMBO |= {f'{g}{s}{a}' for g in GRADE for s in SUBJ for a in ADV}

ALLOWED = MASTER | MOD_WORDS | COMBO

# 职称词的识别：以已知词尾结尾的 2~6 字中文串。
# ⚠️ 必须排除「前面粘了姓名/机构/句子成分」的情况——「张三教授」「欢迎各位老师」
#    「济与金融学院院长」这类**不是词表条目**，只是恰好含职称词的普通文本。
#    判据：词尾之前的部分若含「人名/句子」特征字符即视为非词表条目。
TITLE_SUFFIX = (r'教授|研究员|讲师|博士|硕士|院士|老师|导师'
                r'|先生|女士|院长|系主任|主任|会长|秘书长|理事长|监事|主编|编辑|记者'
                r'|校长|处长|所长|部长|司长|书记|主席|顾问|经理|工程师|博导|硕导')
TITLE_RE = re.compile(r'[\u4e00-\u9fff]{2,6}(?:' + TITLE_SUFFIX + r')')

# 具名/整句白名单：整串是**页面原文片段或说明文字**，不是可复用的词表条目。
# 命中这些特征的串直接跳过（详见各处注释）。
NAMED_CONTEXT_RE = re.compile(
    r'国家杰青|长江学者|青年长江|杰青|优青|万人计划|孔雀计划|学术讲座|讲座|报告|'
    r'钟宁桦|陆毅|陆铭|交通大学特聘|'
    # 说明性注释与 docstring 里的举例（张三/李四/王五 等占位人名）
    r'张三|李四|王五|赵六|钱七|孙八|周九|吴十')

# **角色词**：指「人」而非「职称」，不属于 field_vocab 的任何语义分组。
# 典型出现处是标签式抽取的标签集合（`(?:主讲人|主讲嘉宾|报告嘉宾|特邀嘉宾)：`）——
# 「主讲嘉宾」是栏目标签，不是某人的职称，故不进主表、也不受本守卫约束。
# 登记在此是为了让守卫**只拦真正的职称副本**，不对角色词误报。
ROLE_WORDS = frozenset({
    '主讲嘉宾', '报告嘉宾', '讲座嘉宾', '特邀嘉宾', '特邀专家', '报告专家',
    '会议主席', '嘉宾', '专家',
})


def _iter_strings(path, include_comments=False):
    """产出 (行号, 字符串)。

    include_comments=False 时用 ast 只取**真正的字符串常量**，
    这样文档字符串/注释里的举例（如「主讲人：张三教授」）天然被排除——
    它们不是可执行词表，混进来会产生大量假阳性。
    """
    with open(path, encoding='utf-8') as f:
        src = f.read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # docstring 跳过：它是说明文字，不是词表
            if (isinstance(node, ast.Expr) and node.value is node):
                continue
            yield node.lineno, node.value


# 「这是正则模式串」的判据：职称词之间用 | 分隔。
# 真实的内联副本一律是这个形态（'教授|副教授|研究员' 或 r'(?:教授|…)'），
# 而「穆肃教授」「欢迎各位老师」这类整句不是——它们没有 | 分隔的职称列表。
# 用它把判据从「文本里含职称词」收窄为「这是一张职称词表」。
def _is_title_pattern(text):
    if '|' not in text:
        return False
    if len(text) > 4000:
        return False
    segs = [s.strip('(?:)') for s in text.split('|')]
    hits = sum(1 for s in segs if TITLE_RE.fullmatch(s) or TITLE_RE.search(s))
    return hits >= 2


class TitleVocabConvergenceTest(unittest.TestCase):

    def test_70_parsers职称词均在主表内(self):
        """核心判据：内联的**职称词表模式串**不得超出主表并集。

        只查「职称词以 | 分隔排列」的形态（真实副本都是这个形态），
        避免把「穆肃教授」「欢迎各位老师」这类含职称词的普通句子误判。
        """
        violations = {}
        for lineno, text in _iter_strings(PARSERS):
            if NAMED_CONTEXT_RE.search(text):
                continue              # 页面原文片段 / 说明文字
            if not _is_title_pattern(text):
                continue              # 不是词表形态
            for w in set(TITLE_RE.findall(text)):
                if w in ROLE_WORDS or w in ALLOWED:
                    continue
                violations.setdefault(w, []).append(lineno)
        self.assertEqual(
            violations, {},
            'parsers.py 出现主表外的职称词（将来主表加词时这些副本不会跟随）：\n'
            + '\n'.join(f'  {w} @ L{lines[:4]}'
                        for w, lines in sorted(violations.items()))
            + '\n修法：能归主的加进 field_vocab 的对应分组；'
              '确属上下文裁剪的，改引 field_vocab 已有的语义入口'
              '（SPEAKER_ADJACENT_TITLE_RE / COMPLEX_TITLE_ALT_RE / '
              'TAIL_TITLE_ANY_RE / NAME_TITLE_TAIL_RUN_RE），勿再内联手抄。'
              '（角色词如「主讲嘉宾」已豁免，见 ROLE_WORDS。）')

    def test_71_主表并集非空且含关键锚点(self):
        """主表本身的完整性——守卫依赖它，空表会让上一条形同虚设。"""
        self.assertGreater(len(MASTER), 50, '主表并集异常偏小')
        for w in ('教授', '副教授', '研究员', '助理研究员', '秘书长',
                  '理事长', '监事', '主编', '编辑', '记者'):
            self.assertIn(w, MASTER, f'主表缺少锚点词 {w}')
        for w in ('一级教授', '高级教师', '特级教师'):
            self.assertIn(w, MASTER, f'复合职称分组缺少 {w}')

    def test_72_派生入口存在且语义不重叠(self):
        """两个刻意语义入口必须存在，且不能与通用尾部剥离表混用。"""
        for name in ('SPEAKER_ADJACENT_TITLE_RE', 'COMPLEX_TITLE_ALT_RE'):
            self.assertTrue(hasattr(FV, name), f'field_vocab 缺 {name}')
        # SPEAKER_ADJACENT 刻意不含职务词（院长/主任…），否则会把简介里的人误当主讲人
        pat = FV.SPEAKER_ADJACENT_TITLE_RE.pattern
        for w in ('院长', '主任', '秘书长', '理事长'):
            self.assertNotIn(f'|{w}|', pat,
                             f'SPEAKER_ADJACENT_TITLE_RE 不应含职务词 {w}'
                             '（会误把简介里的人当主讲人，见 parsers 注释）')
        # TAIL_TITLE_RE 是「尾部剥离」专用，不应被复合职称污染
        self.assertNotIn('一级教授', FV.TAIL_TITLE_RE.pattern,
                         'TAIL_TITLE_RE 混入了复合职称——会改变所有剥离调用点行为')

    def test_73_词表分组不得重叠(self):
        """各分组必须互斥——同一词声明在两组会让「按组改词表」产生分歧。

        2026-10-02 实踩：收敛职称副本时把「工程师」放进了 COMPLEX_TITLE_SUFFIXES，
        而它本来就属于 ORG_TITLE_SUFFIXES（行政职务）。功能上无害（set 会去重），
        但语义上同一词有了两个"归属"，将来按组增删词表的人必然困惑——而这正是
        本轮收敛要根除的那类问题，不能自己又造一个。
        """
        groups = {
            'NAME_TITLE_SUFFIXES': set(FV.NAME_TITLE_SUFFIXES),
            'ORG_TITLE_SUFFIXES': set(FV.ORG_TITLE_SUFFIXES),
            'HONORIFIC_SUFFIXES': set(FV.HONORIFIC_SUFFIXES),
            'COMPLEX_TITLE_SUFFIXES': set(FV.COMPLEX_TITLE_SUFFIXES),
        }
        names = list(groups)
        clashes = {}
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                common = groups[a] & groups[b]
                if common:
                    clashes['%s ∩ %s' % (a, b)] = sorted(common)
        self.assertEqual(clashes, {},
                         f'词表分组重叠：{clashes}——'
                         '同一词只应在语义归属的那一组声明一次；'
                         '若某组确需匹配它（如 COMPLEX 模式要匹配 ORG 的职务词），'
                         '请用 _EXTRA_COMPLEX_JOBS 显式并入模式，而不是重复声明进分组。')

    def test_73_parsers确实在引用派生入口(self):
        """确认收敛真的落地（而非只有 field_vocab 定义了却没人用）。"""
        with open(PARSERS, encoding='utf-8') as f:
            src = f.read()
        code = '\n'.join(l for l in src.splitlines()
                         if not l.lstrip().startswith('#'))
        for name in ('SPEAKER_ADJACENT_TITLE_RE', 'COMPLEX_TITLE_ALT_RE'):
            self.assertIn(name, code,
                          f'parsers.py 未引用 {name}——'
                          f'说明该副本尚未收敛，test_70 只是在放行它')

    def test_74_主表正则为非捕获组(self):
        """锁住 `_alt` 的非捕获组语义。

        背景（2026-10-02 实踩）：手抄副本用的是**捕获组** `(A|B)`，改引主表后
        `_alt` 产出的是 `(?:A|B)`——调用点若仍写 `.group(1)` 就在**运行时**抛
        IndexError，而非解析出错。这类错误只在特定页面路径触发，golden 全绿也
        可能漏过（本次就是被 test_parser_golden 抓到的）。

        故从两条锁：
          ① 主表所有 `_alt` 派生的正则 groups 必须为 0；
          ② parsers 里对这些正则的调用不得使用 .group(N>0)。
        """
        import re as _re
        for name in ('NAME_TITLE_SUFFIX_RE', 'ORG_TITLE_SUFFIX_RE',
                     'TAIL_TITLE_RE', 'TAIL_TITLE_ANY_RE',
                     'NAME_TITLE_TAIL_RUN_RE', 'TITLE_ONLY_RE',
                     'SPEAKER_ADJACENT_TITLE_RE', 'COMPLEX_TITLE_ALT_RE'):
            rx = getattr(FV, name, None)
            self.assertIsNotNone(rx, f'field_vocab 缺 {name}')
            self.assertEqual(rx.groups, 0,
                             f'{name} 含 {rx.groups} 个捕获组——_alt 应产出'
                             f'非捕获组 `(?:...)`，否则调用点 .group(N) 语义会变')

        with open(PARSERS, encoding='utf-8') as f:
            src = f.read()
        # 对每个「_fv.<名>.search/match(...)」的调用，看紧随其后是否用了 group(N>0)
        bad = []
        for m in _re.finditer(r'_fv\.[A-Z_]+\.(?:search|match|fullmatch)\(', src):
            tail = src[m.end():m.end() + 400]
            g = _re.search(r'\.group\((\d+)\)', tail)
            if g and int(g.group(1)) > 0:
                ln = src[:m.start()].count('\n') + 1
                bad.append((ln, m.group(0), g.group(1)))
        self.assertEqual(
            bad, [],
            '主表正则是非捕获组，调用点却取 .group(N>0)（运行时会 IndexError）：'
            + '\n'.join('  L%d %s -> .group(%s)' % b for b in bad))


if __name__ == '__main__':
    unittest.main(verbosity=2)