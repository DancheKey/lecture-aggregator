#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""2026-11-14 复审修复回归（P1×2 + 复核「顺手」四项）。

背景：三路复审（数据层 / 服务端前端 / 近期功能提交）交叉确认后落地的修复，
每项都有复核实测探针支撑（探针复现记录见当日工作日志）：

  P1-3  _NTH_TOPIC_TAIL_RE（parsers.py）裸「时间|地点|题目|主题」会把题目内嵌词
        拦腰截断——「第3讲：量子开放系统中的非平衡周期振荡：时间晶体与量子同步」
        被截成前半句。这是 TOPIC_STOP（13295 教训）早已修过的同类病复发，两处
        没同步。修复：尾表收敛到 TOPIC_STOP 证据形态（时间后跟时间标记、地点后
        跟场所词、题目/主题带冒号），证据集比 TOPIC_STOP 少裸「上|下」
        （「时间上下文」类题目会被误截，TOPIC_STOP 该处留待后续）。
  P1-4  _merge_poster_vlm_into_sessions 合并路径：cur（正文块首讲者）分段未经
        _looks_like_real_name 校验，垃圾碎片（「特邀嘉宾」）可被推成第一主讲
        （单人路径 ：5103 有守卫，合并路径漏了）。修复：合并前同判，碎片丢弃。
  P2-1  _ATTACH_URL_RE 加右边界 (?=$|[?#])：「file.docx.html」曾在内部截出
        假候选 /file.docx，白试下载后持续污染 attachment_health.json。
  P2-2  LABELS 三词「活动嘉宾|活动内容|活动流程」补 (?=[:：])：标签形态必带
        冒号（终止力不变），裸词放行散文（「地点：…会议室，活动流程见附件」
        曾被统一终止符拦腰截断）。
  P2-3  _merge_poster_vlm_into_sessions 的 aff 清洗：「len>30 或含任意逗号」
        误杀合法单位名（库内实测 University of California, Santa Barbara /
        Department of Experimental Psychology, Ghent University）。改按简历段
        特征（职称/履历关键词、中文句读、>60 字段落）。
  P2-4  写盘铁律（AGENTS.md §1）：server.py::_atomic_write_json / _save_sources、
        scraper.py::_atomic_write_json 补 newline='\n'（Windows 文本模式默认
        CRLF，实测 sources.yaml 334/334 行全 CRLF 即由此而来）。

全部用例零网络；server/scraper 以 spec_from_file_location 装载（仓库既有模式）。
运行：python tests/test_review_fixes_20261114.py
"""
import importlib.util
import inspect
import json
import os
import re
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 与 golden 同纪律：import parsers 前锁纯规则基线，避免本机 .env 触发真实大模型
os.environ['SCNU_LLM_TEXT'] = '0'
os.environ['SCNU_LLM_RICH'] = '0'
sys.path.insert(0, os.path.join(ROOT, 'scraper'))
import parsers as P  # noqa: E402

SERVER_PATH = os.path.join(ROOT, 'server.py')
SCRAPER_PATH = os.path.join(ROOT, 'scraper', 'scraper.py')


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- P1-3
class TestNthTopicTailConvergence(unittest.TestCase):
    """_NTH_TOPIC_TAIL_RE 收敛到 TOPIC_STOP 同口径（题目内嵌词不截断）。"""

    def test_embedded_time_word_preserved(self):
        # 复核实测探针原样（修复前返回 '量子开放系统中的非平衡周期振荡'）
        self.assertEqual(
            P._single_nth_session_topic(
                '第3讲：量子开放系统中的非平衡周期振荡：时间晶体与量子同步 主讲人：张三'),
            '量子开放系统中的非平衡周期振荡：时间晶体与量子同步')

    def test_time_context_word_preserved(self):
        # 证据集刻意不含裸「上|下」：「时间上下文」不是时间字段
        self.assertEqual(
            P._single_nth_session_topic(
                '第1讲：时间上下文中的因果推断 主讲人：孙七'),
            '时间上下文中的因果推断')

    def test_prose_location_word_preserved(self):
        self.assertEqual(
            P._single_nth_session_topic(
                '第1讲：城市地点选择的经济学分析 主讲人：赵六'),
            '城市地点选择的经济学分析')

    def test_real_time_field_still_truncates(self):
        self.assertEqual(
            P._single_nth_session_topic(
                '第3讲：量子开放系统中的非平衡周期振荡研究 时间：2026年11月14日 主讲人：张三'),
            '量子开放系统中的非平衡周期振荡研究')

    def test_real_location_field_still_truncates(self):
        self.assertEqual(
            P._single_nth_session_topic(
                '第2讲：现代密码学前沿进展 地点：南校区实验楼S502 主讲人：李四'),
            '现代密码学前沿进展')

    def test_colon_theme_label_still_truncates(self):
        self.assertEqual(
            P._single_nth_session_topic(
                '第1讲：人工智能安全治理 主题：大模型风险评估 主讲人：王五'),
            '人工智能安全治理')


# ---------------------------------------------------------------- P1-4
class TestMergePathNameGuard(unittest.TestCase):
    """合并路径 cur 分段必须过 _looks_like_real_name（与单人路径同判）。"""

    ARGS = (2026, '2026-11-10 15:12:00', None, 2026)

    def _run(self, cur_speaker):
        recs = [{'lectureStart': '2026-11-11 14:30:00',
                 'location': '石牌校园一课教室',
                 'speaker': cur_speaker, 'abstract': '', 'speakerBio': '',
                 'speakerAffiliation': ''}]
        vlm = [{'lectureStart': '2026-11-11 14:30', 'speaker': '胡扬、曾东林',
                'speakerTitle': '', 'speakerAffiliation': '',
                'location': '石牌校园一课教室', 'speakerBio': '', 'abstract': ''}]
        return P._merge_poster_vlm_into_sessions(recs, vlm, *self.ARGS)

    def test_garbage_fragment_dropped(self):
        # 复核实测探针原样（修复前 speaker 被拼成「特邀嘉宾、胡扬、曾东林」）
        out = self._run('特邀嘉宾')
        self.assertEqual(out[0]['speaker'], '胡扬、曾东林')

    def test_real_name_still_leads(self):
        out = self._run('谈子敏')
        self.assertEqual(out[0]['speaker'], '谈子敏、胡扬、曾东林')

    def test_poster_names_reach_multi_speaker_extractor(self):
        self.assertEqual(P._poster_multi_speaker_names('胡扬、曾东林'),
                         ['胡扬', '曾东林'])


# ---------------------------------------------------------------- aff 清洗
class TestAffCleanCriterion(unittest.TestCase):
    """aff 清洗改按简历段特征：合法单位名（含逗号/超30字）不再误杀。"""

    ARGS = (2026, '2026-11-10 15:12:00', None, 2026)

    def _run_affs(self, *affs):
        recs = []
        for i, aff in enumerate(affs):
            recs.append({'lectureStart': '2026-11-11 %02d:00:00' % (9 + i),
                         'location': '石牌校园教学楼%d教室' % i,
                         'speaker': '张%d' % i, 'abstract': '摘要%d' % i,
                         'speakerBio': '简介%d' % i, 'speakerAffiliation': aff})
        return P._merge_poster_vlm_into_sessions(recs, [], *self.ARGS)

    def test_legit_units_kept(self):
        # 三例均为库内实测正当形态（修复前分别被「含逗号」「len>30」清空）
        out = self._run_affs(
            'University of California, Santa Barbara',
            'Department of Experimental Psychology, Ghent University',
            '华南师范大学（汕尾），中山大学')
        self.assertEqual(out[0]['speakerAffiliation'],
                         'University of California, Santa Barbara')
        self.assertEqual(out[1]['speakerAffiliation'],
                         'Department of Experimental Psychology, Ghent University')
        self.assertEqual(out[2]['speakerAffiliation'], '华南师范大学（汕尾），中山大学')

    def test_resume_like_wiped(self):
        out = self._run_affs(
            '华东师范大学教授、博士生导师，国家杰出青年科学基金获得者',
            '某某研究员，现任某研究院院长，长期从事量子光学研究')
        self.assertEqual(out[0]['speakerAffiliation'], '')
        self.assertEqual(out[1]['speakerAffiliation'], '')


# ---------------------------------------------------------------- 附件右边界
class TestAttachmentRightBoundary(unittest.TestCase):
    """_ATTACH_URL_RE 加 (?=$|[?#])：双扩展名不再截出假候选。"""

    def test_no_false_positive_from_double_ext(self):
        self.assertEqual(P._ATTACH_URL_RE.findall('/a/2026/file.docx.html'), [])
        self.assertEqual(P._ATTACH_URL_RE.findall('/a/2026/x.pdf.html'), [])

    def test_real_attachments_still_match(self):
        self.assertEqual(P._ATTACH_URL_RE.findall('/a/2026/notice.pdf'),
                         ['/a/2026/notice.pdf'])
        self.assertEqual(P._ATTACH_URL_RE.findall('/a/2026/x.docx?download=1'),
                         ['/a/2026/x.docx?download=1'])
        # viewer hash 内嵌形态（skc/1124）不受影响
        self.assertEqual(
            P._attachment_urls_from_attr('viewer2.html#//host/x.pdf'),
            ['//host/x.pdf'])


# ---------------------------------------------------------------- STOP 前瞻
class TestStopColonLookahead(unittest.TestCase):
    """活动嘉宾/内容/流程 三词补 (?=[:：])：散文不截断，标签形态照常终止。"""

    @staticmethod
    def _hits(text):
        return [m.start() for m in re.finditer(P.STOP, text)]

    def test_prose_not_truncated(self):
        text = '华南师范大学心理学院报告厅，活动流程见附件'
        act = text.index('活动')
        self.assertNotIn(act, self._hits(text))

    def test_label_form_still_truncates(self):
        text = '石牌校园一课西102活动嘉宾：张三'
        act = text.index('活动')
        self.assertIn(act, self._hits(text))


# ---------------------------------------------------------------- 写盘 LF
class TestAtomicWriteLF(unittest.TestCase):
    """AGENTS.md §1：三处原子写必须 LF 落盘；sources.yaml 磁盘态归一。"""

    def _atomic_write_roundtrip(self, mod, name):
        fd, tmp = tempfile.mkstemp(suffix='.json')
        os.close(fd)
        try:
            mod._atomic_write_json(tmp, {'key': '中文值', 'n': 2})
            raw = open(tmp, 'rb').read()
            # 核心：磁盘字节里不得出现 \r\n（JSON 字符串内的 \n 本就是 \\n 转义）
            self.assertNotIn(b'\r\n', raw, '%s._atomic_write_json 落成 CRLF' % name)
            self.assertEqual(json.loads(raw.decode('utf-8')),
                             {'key': '中文值', 'n': 2})
        finally:
            os.remove(tmp)

    def test_server_atomic_json_lf(self):
        self._atomic_write_roundtrip(_load_module(SERVER_PATH, '_srv_lf'), 'server')

    def test_scraper_atomic_json_lf(self):
        self._atomic_write_roundtrip(_load_module(SCRAPER_PATH, '_scr_lf'), 'scraper')

    def test_save_sources_source_has_newline_guard(self):
        server = _load_module(SERVER_PATH, '_srv_src')
        owner = next(o for o in vars(server).values()
                     if inspect.isclass(o) and hasattr(o, '_save_sources'))
        src = inspect.getsource(owner._save_sources)
        self.assertIn("newline='\\n'", src,
                      '_save_sources 缺 newline 守卫（AGENTS.md §1）')

    def test_sources_yaml_on_disk_is_lf(self):
        raw = open(os.path.join(ROOT, 'scraper', 'sources.yaml'), 'rb').read()
        self.assertNotIn(b'\r\n', raw, 'sources.yaml 磁盘仍有 CRLF（需重落盘一次）')


if __name__ == '__main__':
    unittest.main(verbosity=2)
