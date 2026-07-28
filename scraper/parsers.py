"""详情页字段解析：从华师各学院 CMS 详情页提取讲座标准字段。"""
import re
import io
import sys
import datetime
import requests
from urllib.parse import urljoin, unquote
from bs4 import BeautifulSoup
from timeparse import parse_cn_time, _year_from_text, resolve_lecture_time, _date_from_title

# N1a / O3a（2026-07-20 修正）— CJK 间空格不再无脑删除：
# 相邻 CJK 间若有 1–2 个空格：
#   - 两侧各 ≥2 字 → 保留（词块边界，如「维护 王婧 传统」中 王婧 被空格孤立成可识别讲者）
#   - 否则（任一侧为单字）→ 删除（OCR 噪声，如「题 目」→「题目」、「时 间」→「时间」、
#     「报 告 人」→「报告人」、「王 教授」→「王教授」）
# 拉丁字母/数字与 CJK 之间的空格本就不匹配此正则，保持保留（如「2026年 7月」不动）。
# 注意：N1a 同时作用于 HTML 正文与 OCR 文本；HTML 正文一般无「2字中文 2字中文」词块空格，
# 故对 HTML 解析影响可忽略，仍建议全库回归确认无退化。
def _n1a_normalize(text, keep_word_boundaries=True):
    """N1a：CJK 内部空格处理。

    keep_word_boundaries=False（默认，HTML 正文路径）：删除所有 CJK 间单/双空格
    （原行为，保证「主讲人：张三 教授」→「主讲人：张三教授」被姓名清洗正确识别）。

    keep_word_boundaries=True（仅 OCR 海报路径）：仅当空格两侧均 ≥2 字时才保留
    （词块边界，如「维护 王婧 传统」），否则仍删除（单字间 OCR 噪声，如「题 目」→「题目」）。
    这是 O3a 修正——OCR 海报里姓名常被空格隔成孤立词，需保留边界供 O6d-2.5 夹逼定位。
    """
    if not text:
        return text

    def _cjk_space(m):
        left, sp, right = m.group(1), m.group(2), m.group(3)
        if keep_word_boundaries and len(left) >= 2 and len(right) >= 2:
            return left + sp + right  # 保留词块边界（仅 OCR 路径）
        return left + right           # 删除空格但保留两侧汉字（修复：原返回 '' 会连汉字一起吞掉）

    return re.sub(r'([\u4e00-\u9fa5])(\s{1,2})([\u4e00-\u9fa5])', _cjk_space, text)


def _n1_normalize(text, keep_word_boundaries=True):
    """N1 通用预处理：全角标点统一为半角（冒号/逗号/括号/斜杠/分号/引号）。"""
    if not text:
        return text
    repl = {'：': ':', '，': ',', '（': '(', '）': ')', '／': '/', '【': '[', '】': ']',
            '；': ';', '“': '"', '”': '"', '‘': "'", '’': "'", '　': ' '}
    for k, v in repl.items():
        text = text.replace(k, v)
    text = _n1a_normalize(text, keep_word_boundaries)  # N1a：去 CJK 内部空格
    return text


# N1d 收窄版字符纠正：仅对 OCR 文本在三类数字上下文内纠正易混字符。
# 上下文：① 时间片段 HH:MM / HH:MM-HH:MM；② 日期片段 YYYY-MM-DD / YYYY/MM/DD / YYYY年MM月DD日；
# ③ 纯整数行（整行仅数字+可选空格）。其余（如 Research/Zoom）一律不碰。
# 纠正集：O/o→0、l/I/|→1、;→:、〇→0。
def _ocr_char_fix(text):
    if not text:
        return text

    def fix_segment(s):
        return (s.replace('O', '0').replace('o', '0').replace('l', '1')
                 .replace('I', '1').replace('|', '1').replace(';', ':').replace('〇', '0'))

    time_pat = re.compile(
        r'\d{1,2}\s*[:;]\s*\d{1,2}(?:\s*[-–~—]\s*\d{1,2}\s*[:;]\s*\d{1,2})?')
    text = time_pat.sub(lambda m: fix_segment(m.group(0)), text)
    date_pat = re.compile(r'\d{4}\s*[-/.年]\s*\d{1,2}\s*[-/.月]\s*\d{1,2}\s*[日号]?')
    text = date_pat.sub(lambda m: fix_segment(m.group(0)), text)
    lines = text.split('\n')
    for i, line in enumerate(lines):
        if re.fullmatch(r'\s*[0-9OoIl|]+\s*', line):
            lines[i] = fix_segment(line)
    return '\n'.join(lines)


# N1e 混合中英文标签拆分：将 "时间/Time:" 这类组合标签拆成独立标签，使中英文都能被扫描。
_ZH_LABELS = ['题目', '主题', '时间', '地点', '主讲人', '报告人', '主讲', '演讲人', '摘要',
              '简介', '简历', '主办单位']
_EN_LABELS = ['Topic', 'Title', 'Time', 'Date', 'Venue', 'Location', 'Place', 'Speaker',
              'Presenter', 'Lecturer', 'Abstract', 'Bio', 'Synopsis']


def _n1e_normalize(t):
    for z in _ZH_LABELS:
        for e in _EN_LABELS:
            t = re.sub(rf'({z})\s*/\s*({e})\s*[:：]', rf'\1：\2：', t)
            t = re.sub(rf'({e})\s*/\s*({z})\s*[:：]', rf'\2：\1：', t)
    return t


def _clean_ocr_text(ocr_text):
    """清理图片 OCR 后常见的海报抬头、Logo、边框乱码等噪声。

    O3b 收窄：校名抬头只删「明确校名」(华南师范大学/华南师大/华师/SCNU/SOUTH CHINA NORMAL
    UNIVERSITY)，且只删 OCR 文本前 5 行内的校名；其余位置（中下部的 affiliation）与泛化
    「UNIVERSITY OF XXX」一律保留，避免误删真实主讲人单位。
    """
    t = ocr_text
    # 合并连续空格
    t = re.sub(r'\s+', ' ', t).strip()
    # O3b：仅明确校名、仅前 5 行
    _SCHOOL = ['华南师范大学', '华南师大', '华师', 'SCNU', 'SOUTH CHINA NORMAL UNIVERSITY']
    lines = t.split('\n')
    head = '\n'.join(lines[:5])
    for s in _SCHOOL:
        head = re.sub(rf'^\s*{re.escape(s)}\s*', '', head)
    t = (head + '\n' + '\n'.join(lines[5:])).strip()
    # 常见顶部系列讲座抬头（行知书院等海报常见），仅删位于开头、前面无汉字的短词
    header_words = [
        '行知书院', '研究生会', '学生会', '学术讲座', '系列讲座', '讲座预告', 'LECTURE',
        '生命科学大讲堂', '木棉生命科学前沿论坛', '生命科学前沿论坛',
    ]
    for _ in range(3):
        changed = False
        for w in header_words:
            pat = rf'^(?:[^\u4e00-\u9fa5]{{0,8}}){re.escape(w)}\s*'
            new_t = re.sub(pat, '', t)
            if new_t != t:
                t = new_t
                changed = True
        if not changed:
            break
    # 去除开头孤立的数字年份（如海报左上角装饰「1933」「2026」）
    t = re.sub(r'^\d{3,4}\s+', '', t).strip()
    # 去除尾部常见边框乱码或装饰字符（如「曷」「号」孤立出现）
    t = re.sub(r'[\s]*[曷号]+$\s*', '', t).strip()
    # 去除孤立单个非中文字符（常见 OCR 噪声）
    t = re.sub(r'\s+[^\u4e00-\u9fa5a-zA-Z0-9]{1,2}\s*$', '', t).strip()
    return t


# F3 第 5 步：主讲人清洗守卫。清洗后文本若完全由非人名 token 组成（如「作为首席」），
# 或非有效人名（长度<2、纯数字标点、纯英文职称/单位），则视为无效，返回 False。
_NON_NAME_TOKENS = [
    '作为', '首席', '主讲', '报告', '学院', '大学', '邀请', '专家', '嘉宾', '简介', '简历',
    '主持', '致辞', '出席', '参加', '单位', '教授', '研究员', '博士', '老师', '先生', '女士',
    '学术', '讲座', '报告会', '工作坊', '论坛', '沙龙', '研讨会', '讲坛', '座谈会', '时间',
    '地点', '主题', '题目', '摘要', '内容', '来源', '发布', '承办', '协办', '主办', '科学',
    '中心', '实验室', '研究所', '团队', '课题', '项目', '委员会', '主任', '院长', '处长',
    '活动', '交流', '研讨', '开展', '举办', '举行',
    # --- OCR 海报常见噪声词（汕尾教学部/行知书院图片海报误识，2026-07-20 补充）---
    # 纯噪声：OCR 把标签文字（「专题题目」「主讲专家」等）的片段当成人名
    '专题', '提出', '入选', '互联', '学者讲坛',
    # 截断残留：「X专」=「X专家」截断、「X硕」=「X硕士」截断、「X师」=「X老师」截断
    # --- 主题/技术类噪声词（行知书院/汕尾海报 OCR 把主题句/奖项词当成人名，2026-07-20 补充）---
    # 这些是 2–4 字常见名词/术语，绝不可能作为独立主讲人姓名，必须整体拦截（含 OCR 误识变体）。
    '计算机', '人工智能', '新一代', '智能', '运维', '数据', '网络', '系统', '模型', '算法',
    '平台', '技术', '科学', '课程', '教学', '教育', '创新', '发展', '研究', '应用', '探索',
    '实践', '分析', '设计', '构建', '开发', '升级', '优化', '融合', '赋能', '转型', '本科',
    '第一名', '硕士', '一等奖', '二等奖', '三等奖', '特等奖', '金奖', '银奖', '铜奖', '优胜奖',
    '优秀教师', '青年', '教师', '学生', '嘉宾', '领导', '专家',
]
_EN_NON_NAME = {'professor', 'dr', 'mr', 'ms', 'presenter', 'lecturer', 'speaker',
                'university', 'college', 'institute', 'research', 'science', 'chair'}

# 绝不可能出现在真实人名中的子串（系列名/职务/单位/简介等）。命中即非人名。
_NAME_FORBIDDEN = (
    '讲坛', '讲座', '论坛', '沙龙', '报告会', '系列', '学者', '讲席', '讲堂', '大讲堂',
    '学院', '大学', '研究所', '实验室', '中心', '团队', '课题', '项目组', '研究生',
    '本科生', '简介', '简历', '介绍', '摘要', '内容', '地点', '时间', '主题', '题目',
    '主办', '承办', '协办', '邀请', '嘉宾', '主持', '出席', '参加', '活动', '交流',
    '研讨', '开展', '举办', '举行', '教授', '研究员', '博士', '老师', '院士', '导师',
    '院长', '主任', '书记', '校长', '主席',
    # --- OCR 海报噪声子串（2026-07-20 补充）---
    # 「专题」子串匹配：拦住「赵艺专题」「王颖专题」「李朗专题」等粘连误识
    '专题',
    # 「師范」子串匹配：拦住「华南師范」「北京师」「陕西师」等校名截断
    '師范', '师范',
    # --- 行知书院/汕尾 OCR 孤立词假阳性拒绝名单（2026-07-20 补充）---
    # 星期几：周四/周五/周三/周二/周六 等（首字「周」在百家姓，孤立词路由会误抓）
    '星期', '周一', '周二', '周三', '周四', '周五', '周六', '周日',
    # 常见地名（首字多在姓氏集，如「广/周」）：广州/广东/北京/上海/深圳/中国/香港/美国…
    '广州', '广东', '北京', '上海', '深圳', '中国', '香港', '美国', '广西', '杭州', '苏州',
    '成都', '武汉', '南京', '西安', '重庆', '天津', '厦门', '东莞', '佛山', '珠海', '中山',
    # 主题/动词短语碎片：研究领域/发表论文/荣获/巴洛克/计学报/万人/陈的…
    '的', '学报', '研究领域', '发表论文', '荣获', '巴洛克', '万人',
    # 校区名（被当成孤立词讲者）：石牌/大学城/佛山/汕尾/校区
    '石牌', '大学城', '佛山', '汕尾', '校区',
    # 动词/介绍短语碎片（HTML 讲者路由误抓「本次分享/主要从事」等）：
    '分享', '从事', '本次', '主要',
    # OCR 主题/碎片伪讲者（汕尾/行知海报把主题句、奖项词、校名碎片当成人名，2026-07-20 补充）：
    '近年来', '发篇学术', '教育学博', '设儿建设', '台湾省桃', '华南師花', '研究问题',
)


# 常见汉字姓氏首字（百家姓 + 常见姓），用于 O6d-2.5 孤立短词强约束：
# 主讲人候选词首字须为常见姓氏，避免把 星期二/本科/智能 等非人名空格孤立词误抓。
# 少数民族名/外文音译名首字可能不在集合内，作为 mid 兜底宁可少抓（仍可走 Pattern4/人工核验）。
_SURNAME_RE = re.compile(
    r'^[赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍万柯卢莫房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚程嵇邢滑裴陆荣翁荀羊于惠甄曲家封芮羿储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍赖卓蔺屠蒙池乔阴郁胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍卻桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧阳]$')

def _looks_like_real_name(s):
    if not s:
        return False
    s = s.strip()
    if len(s) < 2:
        return False
    # OCR 字符混淆伪讲者拦截（2026-07-20，汕尾/行知海报）：
    # ① 结尾「题」=「师」误读（李题→李老师，整词非人名）；
    # ② 结尾「授」=「教授」截断（韩授→韩教授，孤立「授」绝不成人名）；
    # ③ 结尾「士」但非「博士/院士/硕士/学士」=「师」误读（贺萌士→贺萌老师）。
    if s[-1] == '题':
        return False
    if s[-1] == '授' and not s.endswith(('教授', '副教授')):
        return False
    if s[-1] == '士' and not s.endswith(('博士', '院士', '硕士', '学士')):
        return False
    # 含任何「绝不可能是人名」的子串（系列名/单位/职务/简介等）→ 非人名
    if any(bad in s for bad in _NAME_FORBIDDEN):
        return False
    # 含字母/· 的外文名（允许 First Last / Last, First / 带前缀）
    if re.fullmatch(r"[A-Za-z]+(?:[.'·]?\s?[A-Za-z]+)*", s):
        if s.lower().strip('.') in _EN_NON_NAME:
            return False
        return True
    # 中文名：2–5 个汉字，且去除非人名 token 后仍有残留
    if re.fullmatch(r'[\u4e00-\u9fa5]{2,5}', s):
        if s in _NON_NAME_TOKENS:
            return False
        stripped = re.sub('|'.join(_NON_NAME_TOKENS), '', s)
        if not stripped:
            return False
        return True
    # 中英文混合（如「张 San」）或带·的少数民族名，视为可能有效
    if re.search(r'[\u4e00-\u9fa5]', s) and re.search(r'[A-Za-z·]', s):
        return True
    return False


# F3 补充：主讲人职称词（用于「姓名 紧邻职称」式无标签主讲人识别，如海报「曾碧卿 /教授」）。
# 不含「院长/主任/主席」等职务词——这些常出现在 bio 正文里、前面并非主讲人姓名，
# 纳入会导致把简介里被介绍的人误当主讲人。
_SPEAKER_TITLE = (r'(?:特聘教授|特任教授|长聘教授|副教授|助理教授|副研究员|助理研究员|研究员|'
                  r'教授|讲师|博士后|博士|院士|老师|导师|先生|女士)')

# O6d-2.5 边界字符类：候选姓名词两侧须为「空白或标点」。同时覆盖 ASCII 与全角
# （之前版本漏了 ASCII 逗号 ','，导致「张世海,」这类紧邻半角逗号的名字整组漏匹配）。
_ISO_BOUND = r'[\s　（）()，、；:：]'

# 讲者标签值截止词：OCR 文本按空格拼接，标签式「专家姓名：邓万金 活动主题：…」若不加
# 截止，会把后续标签整段吞入讲者值导致 _looks_like_real_name 失败。遇到这些词即停止取值。
_SPK_VAL_STOP = (r'活动主题|讲座主题|主讲题目|报告题目|题目|主题|时间|地点|时闻|摘要|'
                 r'内容简介|讲座简介|报告简介|专家介绍|主讲人简介|报告人简介|简介|'
                 r'主办|主持|参会|报名|承办|协办')


def _extract_speaker_from_ocr(text):
    """从 OCR 海报文本提取主讲人，覆盖两类情形：

    1) 标签式：主讲人/报告人/Speaker 等标签后的值；
    2) 无标签式：中文姓名紧邻职称（允许「/」或空格），如海报「曾碧卿 /教授」。

    返回 (name, affiliation)：name 必须通过 _looks_like_real_name 校验，否则 ('', '')。
    affiliation 取姓名行之后、下一个结构化关键词之前的文本（通常是「姓名 单位」或直接下一行单位），
    但排除含楼/室/厅等地点词的片段。仅在「时间/地点/摘要/简介/主办」等结构化关键词之前的区域匹配，
    避免把简介（bio）里被介绍的人误当主讲人。
    """
    if not text:
        return '', '', None
    # 区域截断：仅用于无标签式模糊匹配（Pattern 2-5），避免把 bio 中的被介绍者误抓。
    # 标签式匹配（Pattern 1）使用完整文本——因为「主讲人：」是精确标签、不会误匹配 bio 区域，
    # 且部分海报的主讲人信息排在时间/地点之后（如 ose 146：时间→地点→邀请人→简介→主讲人→摘要），
    # 若用时间/地点做硬截断会把「主讲人：xxx」排除在搜索区域外导致漏抓。
    cut = len(text)
    for kw in ('时间', '地点', '时闻', '摘要', '简介', '主讲人简介', '报告人简介', '主办',
               '讲座简介', '报告简介'):
        i = text.find(kw)
        if 0 < i < cut:
            cut = i
    region = text[:cut]
    # 1) 标签式 —— 在完整文本中搜索（精确标签不怕误匹配，且覆盖主讲人在时间/地点之后的海报格式）
    # 值捕获排除空格：中文「主讲人：」后的姓名紧邻冒号、不含内部空格；遇到空格即越界到下一行/字段。
    m = re.search(r'(?:主讲人|主讲|报告人|主讲嘉宾|特邀嘉宾|特邀专家|演讲人|报告专家|报告嘉宾|专家姓名'
                  r'|Speaker|Presenter|Lecturer)\s*[：:]\s*((?:(?!' + _SPK_VAL_STOP + r')[^\n,，。\s]){2,12})',
                  text)
    if m:
        v = re.split(r'[（(]', m.group(1).strip())[0].strip()
        # 剥离常见职称/头衔（OCR 常识别出「陈建邦校长」「李洪修教授」等）
        # 不用 $ 锚定，因为贪婪匹配可能取到「姓名职称+后续文本」的长串
        v = re.sub(r'(?:校长|教授|副教授|讲师|研究员|副研究员|助理研究员|博士|院士'
                  r'|特聘教授|特任教授|院长|系主任|处长|局长|老师|导师)', '', v).strip()
        # 若剥离后仍非纯姓名，用「姓名+职称」精确模式重提取（仅取到职称为止）
        if v and not _looks_like_real_name(v):
            nm = re.match(r'^([\u4e00-\u9fa5·]{2,4})(?:校长|教授|副教授|讲师|研究员|副研究员|助理研究员|博士|院士'
                      r'|特聘教授|特任教授|院长|系主任|处长|局长|老师|导师)', v)
            if nm and _looks_like_real_name(nm.group(1)):
                v = nm.group(1)
            else:
                # 最后兜底：取前2-3字（更保守，避免吃到后续词汇）
                nm = re.match(r'^([\u4e00-\u9fa5·]{2,3})', v)
                v = nm.group(1) if nm and _looks_like_real_name(nm.group(1)) else ''
        # 防御：OCR 两行粘连导致「姓名+下行词首」连写（如「孙正龙题组」）。
        # 若值 >4 字但通过校验（因全汉字+不在禁止列表），尝试剥离尾部常见非人名双字词。
        if len(v) > 4 and _looks_like_real_name(v):
            for suffix in ('题组','课题组','小组','团队','实验室','中心','研究所',
                           '学院','研究室','工作室','项目'):
                if v.endswith(suffix) and len(v) - len(suffix) >= 2:
                    candidate = v[:-len(suffix)]
                    if _looks_like_real_name(candidate):
                        v = candidate
                        break
        if _looks_like_real_name(v):
            return v, '', 'label'
    # 2) 无标签式：姓名紧邻职称（允许「/」或空格）
    m = re.search(r'([\u4e00-\u9fa5·]{2,4})\s*[/／]\s*' + _SPEAKER_TITLE, region)
    if not m:
        m = re.search(r'([\u4e00-\u9fa5·]{2,4})\s+' + _SPEAKER_TITLE + r'(?=[\s,，。；:：]|$)', region)
    if m and _looks_like_real_name(m.group(1)):
        name = m.group(1).strip()
        aff = ''
        rest = region[m.end():].strip(' 　/／')
        if rest:
            # 截到下一个结构化关键词之前
            rest = re.split(r'(?=时间|地点|时闻|摘要|简介|主办|讲座简介|报告简介)', rest)[0].strip()
            rest = re.sub(rf'^{_SPEAKER_TITLE}\s*', '', rest).strip()
            # 排除地点词与讲座/报告/主题/内容等「非单位」片段（避免把「讲座内容…」当成单位）
            _AFF_FORBID = ('讲座', '报告', '主题', '内容', '简介', '摘要', '时间', '地点',
                           '主持', '活动', '论坛', '沙龙', '研讨', '学者', '讲坛')
            if (rest and len(rest) < 40
                    and not re.search(r'[楼室厅馆校区校园中心广场会议教室礼堂报告厅学术厅综合楼行政楼教学楼信息院楼大楼]', rest[:8])
                    and not re.search('|'.join(_AFF_FORBID), rest)):
                aff = rest
        return name, aff, 'label'
    # 3) 海报「专家姓名」标签被 OCR 误读为空，姓名并到「活动主题：姓名+主题」一行，
    #    且「专家介绍」首名与之相同 → 交叉印证取该名为讲者（避免把通用主题首词如
    #    「人工智能…」误当姓名）。汕尾/行知书院工作坊海报常见此模板。
    theme_m = re.search(r'(?:活动主题|讲座主题|主题|主讲题目|报告题目|题目)\s*[：:]\s*'
                        r'((?:(?!' + _SPK_VAL_STOP + r')[^\n,，。.]){2,40})', region)
    bio_m = re.search(r'(?:专家介绍|主讲人简介|报告人简介|个人简介|嘉宾介绍|宾介绍|简介|介绍)\s*[：:]\s*'
                      r'([\u4e00-\u9fa5·]{2,5})', region)
    if theme_m and bio_m:
        # 以「专家介绍」首名为权威，校验它是否为「活动主题」值的前缀（交叉印证），
        # 避免从主题里贪婪截取过长导致与 bio 名不一致。
        bname = re.sub(rf'{_SPEAKER_TITLE}.*$', '', bio_m.group(1).strip())
        if bname and _looks_like_real_name(bname) and theme_m.group(1).strip().startswith(bname):
            return bname, '', 'label'
    # 4) 主讲人简介/专家介绍标签：海报「专家介绍：张世海，动物科学学院教师…」或
    #    OCR 误读的「宾介绍: 张世海,动物…」。被介绍者即主讲人，取冒号后首 2–4 字 CJK 为候选，
    #    比孤立短词更可靠（无需依赖前后主题词夹逼）。仅用「人物介绍」类标签，避开「讲座简介/
    #    内容简介」等摘要标签（其冒号后通常是主题句而非人名）。
    _intro_m = re.search(r'(?:专家介绍|主讲人简介|报告人简介|个人简介|嘉宾介绍|宾介绍|主讲人介绍|报告人介绍|主讲介绍|专家简介)\s*[：:]\s*([\u4e00-\u9fa5·]{2,4})', region)
    if _intro_m and _looks_like_real_name(_intro_m.group(1)):
        return _intro_m.group(1), '', 'intro-label'
    # 5) 行知书院/图片海报模式：主讲人无任何标签，以孤立短词形式出现在
    #    主题文字之后、讲座内容摘要之前（如「…智能维护 王婧 传统运维效率低…」）。
    #    注意 N1 归一化会删除 CJK 内部空格（「维护 王婧 传统」→「维护王婧传统」），
    #    故不能用空格做分隔符。用主题收尾词+摘要起首词夹逼定位：
    #    名字左侧为常见主题尾词（护/技术/能/新/动/升/展），右侧为摘要起首词（传/统/主/讲/报/告/本/文）。
    # O6d-2.5 孤立短词检测（F3 未命中，O3a 修正后空格保留，最可靠路径）：
    # 查「被空白/括号分隔的 2–3 字 CJK 短词」，过 _looks_like_real_name、不含禁止子串、
    # 且非主题收尾词/摘要起首词（避免 传统/报告/基于 误抓）；多候选取距讲座关键词最近者。
    _TAIL_SET = set('维护 技术 智能 创新 驱动 提升 发展 应用 探索 研究 实践 分析 设计 构建 开发 升级 优化 融合 赋能 转型'.split())
    _HEAD_SET = set('传统 主讲 报告 本文 本次 讲座 课程 活动 项目 基于 针对 结合 通过 围绕 依托 借助 利用 采用'.split())
    _iso_cands = []
    for _cm in re.finditer(r'(?<=' + _ISO_BOUND + r')[\u4e00-\u9fa5·]{2,3}(?=' + _ISO_BOUND + r')', region):
        _w = _cm.group(0)
        if not _looks_like_real_name(_w):
            continue
        if any(bad in _w for bad in _NAME_FORBIDDEN):
            continue
        if _w in _TAIL_SET or _w in _HEAD_SET:
            continue
        if not _SURNAME_RE.match(_w[0]):
            continue
        _iso_cands.append((_cm.start(), _w))
    if _iso_cands:
        _kw_pos = [m.start() for m in re.finditer(r'讲座|报告|工作坊|沙龙|论坛|讲坛', region)]
        _best = min(_iso_cands, key=lambda c: min(abs(c[0] - k) for k in _kw_pos)) if _kw_pos else _iso_cands[0]
        return _best[1], '', 'isolated-word'
    _THEME_TAIL = r'(?:维护|技术|智能|创新|驱动|提升|发展|应用|探索|研究|实践|分析|设计|构建|开发|升级|优化|融合|赋能|转型)'
    _ABSTRACT_HEAD = r'(?:(?:传统|主讲|报告|本文|本次|讲座|课程|活动|项目|基于|针对|结合|通过|围绕|依托|借助|利用|采用)[\u4e00-\u9fa5]{0,3})'
    _pat4 = (r'(?:讲座|报告|工作坊|沙龙|论坛|讲坛)[^时间地点]*?'
             + _THEME_TAIL
             + r'([\u4e00-\u9fa5·]{2,3})'
             + _ABSTRACT_HEAD
             + r'[\u4e00-\u9fa5a-zA-Z，。、；：\"\"\'\'()（）]{4,}')
    iso_m = re.search(_pat4, region)
    if iso_m and _looks_like_real_name(iso_m.group(1)):
        return iso_m.group(1), '', 'pattern4'
    return '', '', None


# ---------------------------------------------------------------------------
# 地点字段清理（系统级规则）：剔除紧跟地点之后的会议号/密码/议程/报名/欢迎等
# 噪声后缀，并折叠 OCR/解析产生的数字内部空格（如「1 09 报告厅」→「109报告厅」）。
# 数据集内地点约定为无空格中文，故清理后整体去空格安全且符合约定。
# 适用于：① 解析器最终产出（所有来源）；② 历史数据批量清洗（io 等含会议信息的通知）。
_LOCATION_TERM = re.compile(
    r'(?:线上培训|网络直播|直播链接|腾讯会议|会议号|会议密码|会议议程|报名表|'
    r'报名|欢迎|咨询|联系电话|电话|二维码|扫码|议程|备注|网络会议|线上会议|'
    r'内容|详细内容|主要内容|会议注册|讲座教授|特邀专家|面向对象|主持|'
    r'职称|Tencent ?Meeting)'
)
def _clean_location(loc, title=None):
    if not loc:
        return ''
    orig = loc
    loc = loc.strip()
    if not loc:
        return ''
    # 截断常见后缀噪声（会议号/密码/议程/报名/内容泄漏等紧跟地点之后）
    m = _LOCATION_TERM.search(loc)
    if m:
        loc = loc[:m.start()].strip()
    # 截断内容泄漏：地点值后吸入的日期/简介/正文开头等非地点文字
    # 典型场景：BS4 把换行变空格后 "地点：石牌校区研究生院111 研究生院 2018年4月8日 学校简介：..."
    # 匹配顺序由严格到宽松，避免误伤正常地名中的子串
    _loc_leak = re.compile(
        r'(?:\d{4}\s*年\s*\d{1,2}\s*月'           # 2018年4月 / 2018 年 4 月
        r'(?:\d{0,2}\s*日?)?'                        # 可选日
        r'|(?:学校|学院|研究院|系)\s*简介)'          # 学校简介 / 学院简介
    )
    m2 = _loc_leak.search(loc)
    if m2 and m2.start() > 3:  # 确保不把整个短地点都截掉
        loc = loc[:m2.start()].strip()
    # 正文邀请类词（诚挚邀请/欢迎/感兴趣）也属泄漏信号
    _loc_leak2 = re.compile(r'(?:诚挚|请|欢迎|感兴趣|师生参加|参加！)')
    m3 = _loc_leak2.search(loc)
    if m3 and m3.start() > 5:
        loc = loc[:m3.start()].strip()
    # location 中吸入的讲座主题/主讲人内容（无换行分隔时 BS4 把后续行粘进地点值）
    # 特征：含冒号+长描述（"主题:详细内容..."）或 人名籍贯模式（"姓名,省份,YYYY"）
    _loc_topic_leak = re.compile(r'[：:][^\s:：]{8,}|[\u4e00-\u9fa5]{2,4},[\u4e00-\u9fa5]{2,6},\d{4}')
    m4 = _loc_topic_leak.search(loc)
    if m4 and m4.start() > 5:
        loc = loc[:m4.start()].strip()
    # location 后吸入大段英文摘要（BS4 把地点与正文英文段粘在同一行，且中间无换行）
    # 特征：连续多个英文单词（>=4 个）+ 常见英文虚词（the/is/of/...），起点前为中文地点
    _loc_en_leak = re.compile(
        r'(?i)(?=[a-z][a-z\s,]{25,})(?:[a-z]+(?:\s+|\,\s*)){4,}'
        r'(?:the|is|are|of|in|to|and|or|with|for|from|that|this|we|our|it|its|'
        r'be|have|has|will|would|can|could)\b'
    )
    m5 = _loc_en_leak.search(loc)
    if m5 and m5.start() > 3:
        loc = loc[:m5.start()].strip()
    # location 尾部吸入完整讲座标题（lswh 等源：BS4 把换行后标题行粘进地点值）
    # 特征：loc 尾部长子串(>=6字) 与 title 完全匹配（允许全/半角标点差异）
    if title and len(title) >= 6:
        t_clean = title.strip()
        # 标准化全/半角标点后再比较
        _norm = lambda s: s.replace('：', ':').replace('（', '(').replace('）', ')').replace('——', '--')
        t_norm = _norm(t_clean)
        loc_norm = _norm(loc)
        if loc_norm.endswith(t_norm) and len(loc) > len(t_clean):
            loc = loc[:-(len(loc) - len(loc_norm) + len(t_norm))].strip()
        elif len(t_clean) > 10 and t_norm in loc_norm and loc_norm.index(t_norm) > 3:
            cut = loc_norm.index(t_norm)
            loc = loc[:cut].strip()
        else:
            # 前缀重叠：标题行只粘了开头、未粘全（如「一课北904经典解释与宗教中国化」+
            # 标题「经典解释与宗教中国化：道安…」）。若 loc 在 offset>3 处开始包含标题的
            # 较长前缀(>=6字)，从该前缀起点截断，保留真实地点部分。
            # 从最长(<=12)到最短(6)尝试，避免过早截断；offset>3 保护真实地点前缀不被误删。
            for k in range(min(12, len(t_norm)), 5, -1):
                pref = t_norm[:k]
                pos = loc_norm.find(pref, 3)
                if pos > 3:
                    loc = loc[:pos].strip()
                    break
    if not loc:
        # 整段仅为线上会议号等、无实体地点：标注为线上
        if re.search(r'(腾讯会议|线上|会议号|会议 ?ID|网络会议|直播|Tencent ?Meeting)', orig):
            return '线上'
        return ''
    # 折叠数字内部、且紧贴中文的空格（OCR 把房间号拆开）：研究院 1 09 报告厅 → 研究院109报告厅
    loc = re.sub(r'([\u4e00-\u9fa5])(\d)\s+(\d+)(?=[\u4e00-\u9fa5]|$)',
                 lambda x: x.group(1) + x.group(2) + x.group(3), loc)
    loc = re.sub(r'(\d)\s+(\d)(?=[\u4e00-\u9fa5]|$)', r'\1\2', loc)
    # 数据集地点约定无内部空格，统一去除（同时清掉残留 CJK 间空格）
    loc = re.sub(r'\s+', '', loc)
    # 清理腾讯会议等截断后残留的后缀标点/连接词（『（』『：#』『+线上』『;三』等）
    for _ in range(3):
        new = re.sub(r'[（(：:；;，,+、#]+\s*$', '', loc)
        new = re.sub(r';[一二三四五六七八九十百千万\d]\s*$', '', new)  # ";三"、";2" 等换行序号泄漏
        new = re.sub(r'(?:线上|线下)[:：]?\s*$', '', new)
        new = new.strip()
        if new == loc:
            break
        loc = new
    if not loc:
        if re.search(r'(腾讯会议|线上|会议号|会议 ?ID|网络会议|直播|Tencent ?Meeting)', orig):
            return '线上'
        return ''
    return loc


# 从海报 OCR 文本提取指定主讲人的简介（bio）。
# 海报常把多位嘉宾的「姓名+简介」顺序排布；给定姓名后，取其后的简介片段，
# 直到下一个嘉宾/主题标签（特邀嘉宾/主题/主讲人）或文末。返回清理后的简介，
# 提取不到返回 ''。仅在「姓名后出现中文逗号（简介起首标志「姓名，现为…」）」
# 或姓名最后一次出现处截取，避免把「姓名+主题」误当简介。
def _extract_bio_from_ocr(ocr_text, speaker):
    if not ocr_text or not speaker:
        return ''
    _TITLE_RE = (r'(?:教授|副教授|讲师|研究员|副研究员|助理研究员|博士|院士|老师|'
                 r'校长|院长|主任|特聘教授|特任教授|导师|嘉宾)?')
    occ = [(m.start(), m.end())
           for m in re.finditer(re.escape(speaker) + _TITLE_RE, ocr_text)]
    if not occ:
        return ''
    # 优先：姓名后出现中文逗号（简介起首「姓名，现为/曾任…」）
    best = None
    for s, e in occ:
        nxt = ocr_text[e:e + 1]
        if nxt in ('，', ','):
            best = e + 1
            break
    if best is None:
        best = occ[-1][1]  # 否则取最后一次出现（简介通常在海报后部）
    rest = ocr_text[best:]
    cut = len(rest)
    for kw in ('特邀嘉宾', '主讲嘉宾', '报告嘉宾', '主题（', '主题(', '主题:', '主题：',
               '主讲人', '报告人', '主持人', '讲座时间', '时间', '地点', '腾讯会议',
               '直播', '线上'):
        i = rest.find(kw)
        if 0 < i < cut:
            cut = i
    bio = rest[:cut].strip()
    # 去掉开头紧邻的职称残留
    bio = re.sub(r'^(?:教授|副教授|讲师|研究员|副研究员|博士|院士|老师|校长|院长|'
                 r'主任|特聘教授|特任教授|导师)[，,；;：: ]*', '', bio)
    bio = re.sub(r'\s+', ' ', bio).strip()
    if len(bio) < 10 or len(bio) > 600:
        return ''
    return bio


# 懒加载 RapidOCR 引擎：仅在遇到图片讲座时才初始化（ONNXRuntime 后端，
# 替代原 easyocr——中文海报准确率更高、无 torch/paddle 重型依赖、启动更快）
_OCR_ENGINE = None


def _ocr_engine():
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR_ENGINE = RapidOCR(use_cls=False, print_verbose=False)
    return _OCR_ENGINE


def _img_to_text(img_url_or_bytes):
    """对讲座海报图片做 OCR，返回识别到的文本（行以空格拼接，与原 easyocr 输出一致）。"""
    import tempfile, os
    target = None
    try:
        if isinstance(img_url_or_bytes, bytes):
            fd, target = tempfile.mkstemp(suffix='.jpg')
            with os.fdopen(fd, 'wb') as f:
                f.write(img_url_or_bytes)
        else:
            url = img_url_or_bytes
            if url.startswith('//'):
                url = 'https:' + url
            elif url.startswith('/statics.'):
                # 站点把 statics.scnu.edu.cn 以根路径形式引用，实际缺协议
                url = 'https:' + url
            r = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
            fd, target = tempfile.mkstemp(suffix='.jpg')
            with os.fdopen(fd, 'wb') as f:
                f.write(r.content)
        # 超大图保护：海报原图偶有过亿像素（如 139MP），直接送 OCR 会 OOM 且基本无可读文字。
        # 超过阈值直接跳过（返回空），避免进程被杀死；正常海报（通常 < 30MP）不受影响。
        try:
            from PIL import Image
            with Image.open(target) as _im:
                _w, _h = _im.size
            if _w * _h > 60000000:  # 约 60MP
                return ''
        except Exception:
            pass
        res, _ = _ocr_engine()(target)
        if not res:
            return ''
        return ' '.join([l[1] for l in res])
    except Exception:
        return ''
    finally:
        if target and os.path.exists(target):
            try:
                os.remove(target)
            except Exception:
                pass

# 列表标题"正向"判定：含以下任一才视为讲座类（RT0 修正 2026-07-19：补入工作坊/沙龙/论坛/
# 研讨会/座谈会，使这些活动形式的列表项能被识别为讲座、不再被漏抓；它们不是新闻类型）。
LECTURE_KW = ['学术讲座', '讲座', '学术报告', '学术沙龙', '讲坛', '报告会', '前沿讲座',
              '工作坊', '沙龙', '论坛', '研讨会', '座谈会']
# RT0 列表标题拦截（2026-07-19 按 PDF 修改建议修正）：
# - 移除「工作坊」「改期」：二者是真实讲座/改期通知，不应在列表阶段被跳过
#   （改期通知按 reschedule_notice 处理更合理，但本期仅放开拦截，不做同名时间更新）
# - 「报名」收窄为「报名截止」「报名结束」：保留「报名通知」等含预告信息的列表项
# - 新增纪实/侧记/花絮/速递/快讯：均属新闻回顾类，列表阶段直接跳过
# - 新增征文/征稿/招募：与讲座关键词可能共现（如"学术讲座征文通知"），列表阶段拦截
EXCLUDE_KW = ['回顾', '总结', '新闻', '喜报', '招聘', '招生', '答辩', '公示',
              '报名截止', '报名结束', '获奖', '申请表', '纪实', '侧记', '花絮', '速递', '快讯',
              '征文', '征稿', '招募']


# ============================================================================
# VLM 海报结构化提取（智谱 GLM-4V 等多模态模型，作为 rapidocr 的优先增强）
# ----------------------------------------------------------------------------
# 纯海报页 / 骨架页（poster_only）优先调用多模态 LLM 直接输出结构化 JSON；
# 失败或无 API Key 时自动降级回 rapidocr（_do_ocr）。VLM 结果按图片 URL 缓存到
# data/.vlm_cache.json，重跑幂等、省额度。
# ============================================================================
import os as _os
import json as _json
import base64 as _b64
import hashlib as _hash
import time as _time


VLM_PROMPT = """你是一个学术讲座海报信息提取助手。下面是一张（或多张）学术讲座海报图片。请从中提取结构化信息，并以 JSON 格式输出，不要输出任何额外解释文字。

字段定义：
- sessionNumber: «第N讲/第N场/第N期»（如「第七讲」「Lecture 7」「第3场」），单讲座则留空
- title: 讲座主标题（若无独立主标题，用主题概括，不要含「第N讲/期」等场次数）
- topic: 讲座主题或副标题（若与 title 实质相同则留空字符串）
- speaker: 主讲人姓名（只写姓名，不要含职称、单位、职务）
- speakerTitle: 主讲人职称（如 教授、研究员、副教授、博士、讲师）
- speakerAffiliation: 主讲人单位（如 某某大学某某学院、某某研究院）
- lectureStart: 讲座开始时间，格式 "YYYY-MM-DD HH:MM"（若只有日期无钟点，钟点用 00:00）
- lectureEnd: 讲座结束时间，格式同上，若无则留空字符串
- location: 讲座地点（具体到楼栋+房间号，如 理1栋一楼讲学厅、教学楼102）
- abstract: 讲座内容摘要（若海报只有主讲人简介而无独立摘要，则留空字符串）
- speakerBio: 主讲人简介（海报上的个人介绍文字）

输出规则：
- 海报只有 1 场讲座 → 输出一个 JSON 对象
- 海报包含��场独立讲座（有独立的「第N讲/第N场」分隔，且各有独立主讲人/标题/时间/地点）→ 输出 JSON 数组（每个元素一场讲座）
- 字段缺失则对应值为空字符串 ""
- 不要编造信息，海报中不存在的字段就留空
- 年份规则：仅当海报【明确印出】4 位年份（如「2023年5月11日」「2023-05-11」）时，lectureStart 才输出完整「YYYY-MM-DD HH:MM」；若海报只写「5月11日」「07-04」等【无年份】日期，【不要猜测年份】，lectureStart 只输出「MM-DD HH:MM」（系统会按网页发布年份自动补全为正确年份）
- speaker 字段只放姓名，职称放到 speakerTitle，单位放到 speakerAffiliation"""


_VLM_ENV_LOADED = False
_VLM_ENV = {}


def _load_dotenv():
    """读取项目根目录 .env（不依赖 python-dotenv，避免新增依赖）。结果缓存。"""
    global _VLM_ENV_LOADED, _VLM_ENV
    if _VLM_ENV_LOADED:
        return _VLM_ENV
    _VLM_ENV_LOADED = True
    p = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), '.env')
    env = {}
    try:
        with open(p, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    _VLM_ENV = env
    return env


def _load_vlm_config():
    """返回 VLM 配置 dict；若无 API Key 返回 None（调用方自动降级到 rapidocr）。"""
    env = _load_dotenv()
    api_key = _os.environ.get('ZHIPU_API_KEY') or env.get('ZHIPU_API_KEY')
    if not api_key:
        return None
    return {
        'api_key': api_key,
        'model': _os.environ.get('VLM_MODEL') or env.get('VLM_MODEL') or 'glm-4.6v-flash',
        'base_url': (_os.environ.get('VLM_BASE_URL') or env.get('VLM_BASE_URL')
                     or 'https://open.bigmodel.cn/api/paas/v4/chat/completions'),
    }


def _vlm_cache_path():
    return _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                         'data', '.vlm_cache.json')


def _vlm_cache_get(key):
    try:
        p = _vlm_cache_path()
        if _os.path.exists(p):
            d = _json.load(open(p, encoding='utf-8'))
            return d.get(key)
    except Exception:
        pass
    return None


def _vlm_cache_set(key, val):
    try:
        p = _vlm_cache_path()
        d = {}
        if _os.path.exists(p):
            d = _json.load(open(p, encoding='utf-8'))
        d[key] = val
        _json.dump(d, open(p, 'w', encoding='utf-8'), ensure_ascii=False)
    except Exception:
        pass


def _vlm_img_b64(img_url):
    """下载海报图（或读取本地图片文件），缩放至最长边 <=2000px，返回 base64（jpg）。失败或装饰小图/超长条幅返回 None。"""
    try:
        from PIL import Image
        u = img_url
        if u.startswith('//'):
            u = 'https:' + u
        elif u.startswith('/statics.'):
            u = 'https:' + u
        # 支持本地文件路径（file:// 或直接路径），用于 PDF 海报转图片后本地识别
        if u.startswith('file://'):
            local_path = u[7:]
        elif _os.path.exists(u) and not u.startswith('http'):
            local_path = u
        else:
            local_path = None
        if local_path:
            with open(local_path, 'rb') as f:
                raw = f.read()
        else:
            r = requests.get(u, headers={'User-Agent': 'Mozilla/5.0'}, timeout=20)
            r.raise_for_status()
            raw = r.content
        im = Image.open(io.BytesIO(raw))
        w, h = im.size
        # 装饰小图（1x1 跟踪像素、小图标）或超长条幅：VLM 无价值，忽略以省 token、降误判
        if min(w, h) < 100 or max(w, h) / max(min(w, h), 1) > 8:
            return None
        if max(im.size) > 2000:
            im.thumbnail((2000, 2000))
        buf = io.BytesIO()
        im.convert('RGB').save(buf, 'JPEG', quality=85)
        return _b64.b64encode(buf.getvalue()).decode('ascii')
    except Exception:
        return None


_VLM_KEY_MAP = {
    'sessionnumber': 'sessionNumber', '场次': 'sessionNumber', '讲次': 'sessionNumber',
    '期数': 'sessionNumber', '讲座场次': 'sessionNumber', '期': 'sessionNumber',
    'title': 'title', '题目': 'title', '主题': 'title', '讲座题目': 'title',
    '讲座标题': 'title', '标题': 'title',
    'topic': 'topic', '副标题': 'topic', '讲座主题': 'topic',
    'speaker': 'speaker', '讲者': 'speaker', '主讲人': 'speaker', '报告人': 'speaker',
    '演讲人': 'speaker', '主讲': 'speaker',
    'speakeraffiliation': 'speakerAffiliation', '单位': 'speakerAffiliation',
    '主讲人单位': 'speakerAffiliation', '讲者单位': 'speakerAffiliation',
    'lecturestart': 'lectureStart', '时间': 'lectureStart', '开始时间': 'lectureStart',
    '讲座时间': 'lectureStart', '开始': 'lectureStart',
    'lectureend': 'lectureEnd', '结束时间': 'lectureEnd', '结束': 'lectureEnd',
    '讲座结束时间': 'lectureEnd',
    'location': 'location', '地点': 'location', '讲座地点': 'location', '会议室': 'location',
    'abstract': 'abstract', '摘要': 'abstract', '简介': 'abstract', '讲座简介': 'abstract',
    '内容摘要': 'abstract', '讲座摘要': 'abstract',
}

def _normalize_vlm_keys(f):
    """VLM 偶发返回中文键（讲者/主题/时间/地点）或不规范键，归一化为标准英文字段名。"""
    if isinstance(f, list):
        return [_normalize_vlm_keys(x) for x in f]
    if not isinstance(f, dict):
        return f
    out = {}
    for k, v in f.items():
        nk = _VLM_KEY_MAP.get((k or '').strip().lower(), k)
        out[nk] = v
    return out


def _parse_vlm_json(text):
    """从模型返回文本中提取 JSON 对象（或数组，支持多场讲座）。容错：去 ```json 围栏、整体解析、截取兜底。"""
    if not text:
        return None
    s = text.strip()
    m = re.search(r'```(?:json)?\s*(.*?)```', s, re.DOTALL)
    if m:
        s = m.group(1).strip()
    # 先整体解析（支持多场讲座的 JSON 数组）
    try:
        obj = _json.loads(s)
        if isinstance(obj, (dict, list)):
            return obj
    except Exception:
        pass
    # 容错：截取首个 { 到末个 } 再解析（单场对象兜底）
    i, j = s.find('{'), s.rfind('}')
    if i >= 0 and j > i:
        try:
            obj = _json.loads(s[i:j + 1])
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return None


def _parse_vlm_datetime(s, default_year, publish_time, title_year, url_year):
    """解析 VLM 给出的时间字符串为 datetime。优先 ISO，失败用 parse_cn_time。"""
    s = (s or '').strip()
    if not s:
        return None
    s2 = s.replace('年', '-').replace('月', '-').replace('日', ' ').replace('/', '-').strip()
    # 缺年补全：VLM 仅给「05-21 09:30」（海报只印月日，无年份）时，按用户规则用「网页发布年份」兜底。
    # 优先级：url_year(URL路径年) > title_year > 发布年 > default_year(当前年)。
    # 注：若 VLM 已给出完整 4 位年（海报确实印了年份），则不进入此分支，直接信任。
    if not re.match(r'^\d{4}-', s2):
        if re.match(r'^\d{1,2}-\d{1,2}', s2):
            _fill = url_year or title_year or (int(publish_time[:4]) if publish_time else None) or default_year
            if _fill:
                s2 = f'{_fill}-{s2}'
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.datetime.strptime(s2, fmt)
        except Exception:
            pass
    try:
        rt = parse_cn_time(s, default_year, publish_time=publish_time,
                           title_year=title_year, url_year=url_year)
        if rt and rt.get('start'):
            return rt['start']
    except Exception:
        pass
    return None


def _vlm_extract_fields(img_urls, cfg):
    """调用多模态 LLM 提取海报结构化字段。返回 dict 或 None（无 key / 失败 / 限流耗尽）。"""
    if not cfg or not img_urls:
        return None
    key = _hash.md5('|'.join(sorted(img_urls)).encode('utf-8')).hexdigest()
    cached = _vlm_cache_get(key)
    if cached is not None:
        return cached
    contents = []
    for u in img_urls[:2]:
        b = _vlm_img_b64(u)
        if b:
            contents.append(b)
    if not contents:
        return None
    message = [{"type": "text", "text": VLM_PROMPT}]
    for b in contents:
        message.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b}})
    payload = {
        "model": cfg['model'],
        "messages": [{"role": "user", "content": message}],
        "temperature": 0.1,
    }
    headers = {
        "Authorization": "Bearer " + cfg['api_key'],
        "Content-Type": "application/json",
    }
    for attempt in range(6):
        try:
            r = requests.post(cfg['base_url'], headers=headers, json=payload, timeout=60)
            if r.status_code == 429:
                # 免费层 RPM 限制：指数退避（15s→30s→60s→120s→240s→480s，上限600s）
                _time.sleep(min(15 * (2 ** attempt), 600)); continue
            if r.status_code >= 500:
                _time.sleep(3 * (attempt + 1)); continue
            r.raise_for_status()
            resp = r.json()
            txt = resp['choices'][0]['message']['content']
            fields = _parse_vlm_json(txt)
            if fields:
                _vlm_cache_set(key, fields)
                _time.sleep(1.5)  # 主动限速，串行调用避免触发 RPM 限流
                return fields
        except Exception:
            _time.sleep(3 * (attempt + 1))
    return None


def _vlm_split_title(original_title, session_number, item_title):
    """多讲座海报拆分时，生成与其它卡片一致的标题。

    例：原标题「学者讲坛第7-9讲丨阿伯丁大学教师主讲...」，session_number=第七讲，
        item_title=A Brief Introduction... → 「学者讲坛第7讲丨A Brief Introduction...」
    """
    if not item_title:
        return original_title
    # 提取 session_number 中的序号（阿拉伯或中文数字）
    num = None
    m = re.search(r'(\d+)', session_number or '')
    if m:
        num = int(m.group(1))
    else:
        cn = {'十一': 11, '十二': 12, '十三': 13, '十四': 14, '十五': 15,
              '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
        s = session_number or ''
        for k, v in sorted(cn.items(), key=lambda x: -len(x[0])):
            if k in s:
                num = v
                break
    if not num:
        return item_title
    # 匹配原标题中的「前缀 + 第...讲/期 + 分隔符」
    for unit in ('讲', '期'):
        pat = rf'^(.*?第)(?:[一二三四五六七八九十百零0-9\-—–]+)({unit}[丨|｜\|\\/\s]*)'
        mm = re.search(pat, original_title or '')
        if mm:
            return mm.group(1) + str(num) + mm.group(2) + item_title
    # 兜底：直接用「第N讲丨单讲题目」
    return f'第{num}讲丨{item_title}'


def _apply_vlm_to_result(result, f, default_year, publish_time, title_year, url_year,
                          poster_only=False):
    """把 VLM 结构化字段填入 result（非空才填，不覆盖已有）。

    若 f 为 dict（单场讲座）：返回时间 dict 或 None（与原逻辑一致）。
    若 f 为 list（多场讲座）：返回 [(partial_result, t), ...] 列表。

    poster_only=True 时，VLM 来自海报结构化提取，对标题优先信任：
    若现有 title 只是系列活动通称（如「第N期教学工作坊」），而 VLM 给出了
    真实讲座主题，则用 VLM 主题替换 title，并把 VLM topic（常为副标题）合并
    到 title，使卡片标题展示讲座实质内容。
    """
    f = _normalize_vlm_keys(f)
    if isinstance(f, list):
        # 多讲座拆分：对每场讲座生成独立的 partial result
        results = []
        original_title = result.get('title', '')
        for item in f:
            r = result.copy()  # 浅拷贝（讲座特有字段会被覆盖，通用字段保留）
            r['sessionNumber'] = (item.get('sessionNumber') or '').strip()
            item_title = (item.get('title') or '').strip()
            # 多场时保留系列标题前缀（如「学者讲坛第N讲丨」），单讲英文题目作为题目部分，
            # 使卡片标题与其它讲座保持一致。
            if item_title:
                r['title'] = _vlm_split_title(original_title, r['sessionNumber'], item_title)
            t = _apply_vlm_to_result(r, item, default_year, publish_time, title_year, url_year)
            results.append((r, t))
        return results

    # --- 单场讲座（原逻辑）---
    # poster_only 场景：VLM 从海报直接读取，其 title 是真实讲座主题；
    # 若现有 title 只是系列活动通称（如「第N期教学工作坊」），优先用 VLM 主题。
    if poster_only:
        vlm_title = _clean_title((f.get('title') or '').strip())
        vlm_topic = _clean_title((f.get('topic') or '').strip())
        orig_title = (result.get('title') or '').strip()
        if vlm_title and vlm_title not in orig_title:
            series_keywords = ('教学工作坊', '学者讲坛', '工作坊')
            is_series = any(k in orig_title for k in series_keywords)
            # 也覆盖极短/无意义标题
            if is_series or len(orig_title) < 5:
                new_title = vlm_title
                if vlm_topic:
                    if vlm_topic.startswith(('——', '--', '—', '-')):
                        new_title = new_title + vlm_topic
                    else:
                        new_title = new_title + '——' + vlm_topic
                result['title'] = new_title
                result['topic'] = ''
        # VLM 给出的期号回填
        vlm_session = (f.get('sessionNumber') or '').strip()
        if vlm_session and not result.get('sessionNumber'):
            result['sessionNumber'] = vlm_session

    _MAP = [
        ('title', 'title'), ('topic', 'topic'),
        ('speaker', 'speaker'), ('speakerTitle', 'speakerTitle'),
        ('speakerAffiliation', 'speakerAffiliation'),
        ('location', 'location'), ('abstract', 'abstract'),
        ('speakerBio', 'speakerBio'),
    ]
    for src, dst in _MAP:
        v = (f.get(src) or '').strip()
        if dst in ('title', 'topic'):
            v = _clean_title(v)
        if v and not result.get(dst):
            result[dst] = v
    # 主讲人清洗守卫（与 OCR 路径一致）：仅保留像人名的字符
    if result.get('speaker') and not _looks_like_real_name(result['speaker']):
        # 多主讲人用「、」连接：逐段校验，全为有效人名时保留
        if '、' in result['speaker']:
            _segs = [s.strip() for s in result['speaker'].split('、') if s.strip()]
            if not (_segs and all(_looks_like_real_name(s) for s in _segs)):
                result['speaker'] = ''
                result['speakerAffiliation'] = ''
        else:
            result['speaker'] = ''
            result['speakerAffiliation'] = ''
    ts = (f.get('lectureStart') or '').strip()
    te = (f.get('lectureEnd') or '').strip()
    t = None
    if ts:
        dt = _parse_vlm_datetime(ts, default_year, publish_time, title_year, url_year)
        if dt:
            result['lectureStart'] = dt.isoformat(sep=' ')
            if te:
                de = _parse_vlm_datetime(te, default_year, publish_time, title_year, url_year)
                if de:
                    result['lectureEnd'] = de.isoformat(sep=' ')
            t = {'start': dt, 'end': None, 'has_time': True}
    return t


def is_lecture(title):
    if not any(k in title for k in LECTURE_KW):
        return False
    if any(k in title for k in EXCLUDE_KW):
        return False
    return True


def _date_head(s):
    """从日期字符串中提取 YYYY-MM-DD 并转为 datetime.date，失败返回 None。"""
    if not s:
        return None
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', s)
    if not m:
        return None
    try:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except Exception:
        return None


def is_news_record(rec):
    """判断是否为新闻/回顾而非讲座预告。

    主规则：发布时间晚于讲座时间（即讲座结束后才发布），视为对已结束讲座的
    报道或回顾，不纳入聚合。
    - 若讲座时刻已知（非缺省 00:00:00），用「时刻」比较：发布晚于讲座即命中，
      可抓住「当天讲座、当晚发回顾」的情况（原逻辑只比日期会漏）。
    - 若讲座时刻未知（解析为 00:00:00），退化为「日期」比较（原逻辑），
      避免把「时间未知的真预告」误判为新闻。
    辅助规则：标题含明显新闻/回顾类关键词（已在 EXCLUDE_KW 中，由 is_lecture 拦截）。
    """
    if not rec:
        return False
    ls = rec.get('lectureStart') or ''
    pub = rec.get('publishTime') or ''
    if not ls or not pub:
        return False
    def _parse_dt(s):
        if not s:
            return None
        s = s.strip()
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
            try:
                return datetime.datetime.strptime(s[:19], fmt)
            except (ValueError, TypeError):
                continue
        return None

    ls_dt = _parse_dt(ls)
    pub_dt = _parse_dt(pub)
    if not ls_dt or not pub_dt:
        return False
    src = rec.get('publishTimeSource')
    # url_proxy：发布时间仅由 URL 日期代理，精度低，放宽 1 天容差，避免把正常预告误杀
    if src == 'url_proxy':
        delta_days = abs((pub_dt.date() - ls_dt.date()).days)
        if delta_days <= 1:
            return False
        return pub_dt.date() > ls_dt.date()
    # 真实发布时间戳：时刻已知用时刻比（抓「当晚发回顾」），未知退化为日期比
    if ls_dt.time() != datetime.time(0, 0):
        return pub_dt > ls_dt
    return pub_dt.date() > ls_dt.date()


# ---- RT0 非讲座内容硬拦截（与新闻/回顾稿区分：这些根本不是公开讲座）----
# 标题命中即跳过（不进聚合）。基于现有 EXCLUDE_KW 扩展，覆盖「学术喜讯/获奖/征文/招聘/
# 答辩/改期通知」等明确非讲座类通知。这些词均不会出现在真实讲座预告标题中。
# 注：改期/延期/暂停举办 按 PDF 的 reschedule_notice 也归为拦截（视为非预告），如需保留
# 该类「讲座改期通知」可移除此 4 项。
_NON_LECTURE_KW = [
    '喜讯', '喜报', '获奖', '获奖名单',
    '入选名单', '录用名单', '录取名单',
    '征文', '征稿', '招聘', '招贤', '招募', '招新', '纳新',
    '答辩', '开题', '中期考核',
    '公示名单',
    '改期', '延期', '暂停举办', '暂缓举行',
    # 与 EXCLUDE_KW 对齐（仅加明确的新闻类词，不放"招生"等语境词——讲座主题里可能出现）：
    '纪实', '侧记', '花絮', '速递', '快讯',
    '展览', '作品展', '开幕', '拉开帷幕', '启动', '启动仪式',
]


def is_non_lecture_title(title):
    """RT0：标题含明确非讲座关键词（喜讯/获奖/征文/招聘/答辩/改期…）即判为非讲座，跳过。"""
    if not title:
        return False
    return any(k in title for k in _NON_LECTURE_KW)


# ---- 行政/培训通知识别（与 is_non_lecture_title 互补）----
# 覆盖「关于举办XX培训/行前/征集/评选…通知」等面向内部或特定对象的行政通知，
# 以及含报名表/扫码/会议议程等非公开学术讲座内容。
# AD1: 标题含「关于举办/开展…通知」+ 行政特征词（培训/行前/征集/评选/申报）
# AD2: 正文含内部发文对象或报名/扫码/议程等行政特征
# AD2-EX: 有明确主讲人姓名时保留（真讲座预告）
_ADMIN_NOTICE_TITLE_KW = ('培训', '行前', '报名表', '征集', '评选', '申报',
                           '推荐', '选拔', '遴选', '答辩', '开题')
_ADMIN_NOTICE_BODY_KW = ('各学院、各单位', '全体教师', '请.*参加培训',
                          '报名表', '微信扫码', '长按识别', '会议议程',
                          '会议密码', '腾讯会议号')


def is_admin_notice(title, body=''):
    """AD1+AD2：检测行政/培训类通知（非公开学术讲座）。"""
    if not title:
        return False
    # AD1: 标题必须含「关于举办/开展…通知/公告」框架 + 至少一个行政特征词
    if not re.search(r'关于(举办|开展|组织).*?(通知|公告)', title):
        return False
    if not any(k in title for k in _ADMIN_NOTICE_TITLE_KW):
        return False
    # AD2-EX 豁免：正文含明确主讲人姓名 → 真讲座预告，不剔除
    if body and re.search(r'主讲[人师][:：]\s*[\u4e00-\u9fa5]{2,4}', body):
        return False
    # AD2: 正文强化确认（有 body 时才检查）
    if body and any(k in body for k in _ADMIN_NOTICE_BODY_KW):
        return True
    # 仅标题命中也判为疑似（保守策略：宁可留不可误杀讲座预告）
    # 但若标题已含强行政词（培训会/行前/报名表）则直接判为通知
    if any(k in title for k in ('培训会', '行前培训', '报名表')):
        return True
    return False


# ---- 新闻/活动回顾稿识别（与 is_news_record 互补）----
# is_news_record 依赖「发布时间 > 讲座时间」，但 IBC 等站点的回顾稿往往没有
# 显式「发布」时间戳（publishTime 为空），无法触发。这里用语义特征识别：
# 机构作主语的「参与/举办」回顾、新闻署名审签块、回顾式总结短语等。
#
# 关键：必须区分「回顾式（活动已结束）」与「预告式（将举办）」。华师讲座预告页
# 也常用「在本次报告中，我们将介绍…」「本次讲座将围绕…展开」这类前向句式，绝不能
# 仅因出现「本次报告/本次讲座」就判为新闻。故回顾式规则只认总结性动词（不仅/取得/
# 为师生/圆满/史料…），并显式排除「将/拟/介绍/围绕…展开」等前向词。
# 注意：总结性动词**不含「特邀」**——预告页常用「特邀XXX教授」，误纳入会把真预告判为新闻（RT2b）。
_NEWS_RETRO_STRONG = r'(本次活动的成功举办|讲座圆满结束|活动圆满结束|圆满落幕|活动取得圆满成功|圆满成功举办|讲座在我院成功举办|报告会圆满|论坛圆满|讲座取得圆满)'
# 回顾式短语：本次/此次讲座|报告 + 总结性动词；显式排除「将/拟/计划/旨在」等前向词
# （华师讲座预告常用「本次报告将介绍…取得」「本次报告中，我们将提供新见解」，不是新闻）
# 总结性动词不含「特邀」（RT2b 修正，2026-07-19）。
_NEWS_RETRO = r'(本次讲座|此次讲座|本次报告|此次报告)(?!.{0,30}?(将|拟|计划|旨在|期待|希望))(?=.{0,30}?(不仅|为师生|让师生|受到|得到|圆满|顺利|史料|内容翔实|气氛热烈|拓宽|开拓|反响|一致好评|纷纷表示|收获|深入交流|提供(了)?新))'
# 标题即回顾式：机构「举办/开展/举行…讲座/报告」，且整体不含「通知/预告/公示/启事」及
# 前向预告词「将/拟/定于/将于」（RT2c 修正，2026-07-19：避免「我校将于举办…讲座」误判）
_NEWS_TITLE_CONDUCT = r'^(?!.*(通知|预告|公示|启事|将|拟|定于|将于))(?=.*(举办|开展|举行))(?=.*(学术讲座|专题讲座|讲座|报告会|学术报告)).+'
# 新闻署名审签链：供稿+初审+终审 / 初审+复审+终审（华师新闻稿专属页脚，区别于演讲者简介里的「总撰稿」）
_NEWS_SIGNATURE_CHAIN = r'((供稿|撰稿)[:：].{0,40})?(初审[:：].{0,30})?(复审[:：].{0,30})?终审[:：]'
# 叙事导语（YYYY年M月D日）+ 完成态动词
_NEWS_NARRATIVE = r'20\d{2}年\d{1,2}月\d{1,2}日'
_NEWS_DONE = r'(顺利举办|成功举办|顺利开展|圆满完成|圆满结束|圆满落幕|顺利召开|成功召开)'
_NEWS_TITLE_PARTICIPATE = r'^(国际商学院|华南师范大学|我院|学院|学校|研究生院|党支部|党委|师生|团队).{0,14}?(参加|赴.*参加|组织.*参加|师生参加|团队参加)'
# 标题回顾式（新闻稿最直接标记）：含「回顾」且非前瞻型讲座标题。
# - 排除「回顾与展望/回顾及展望/回顾·展望」等真讲座（回顾+展望是常见 seminar 主题）；
# - 整标题若含 预告/通知/征稿/招募/报名/启事 则视为真预告，不命中（见 is_news_article 调用处）。
# 覆盖「砺儒茶座回顾：…」「【讲座回顾】…」「活动回顾 | …」等 ggy 等站回顾稿标题。
_NEWS_TITLE_RETRO = r'回顾(?!与|及|·|、|—|－|和)'
# 标题回顾完成式（RT2h）：标题含完成态动词（圆满落幕/圆满结束/成功举办/顺利召开…），
# 活动必已结束，不可能是预告。与 title-conduct 互补——title-conduct 要求「举办/开展/举行」
# + 讲座类关键词，本规则覆盖「圆满落幕」等纯完成态、未必含「举办」字样的回顾稿标题（如 ibc）。
_NEWS_TITLE_DONE = r'(圆满落幕|圆满结束|圆满完成|成功举办|顺利举办|顺利开展|顺利召开|成功召开|落下帷幕|讲座圆满|报告圆满|活动圆满|取得圆满成功|取得圆满)'


# RT2g 叙事过程体标记：正文含多个「叙事标记」（举行/举办/开展/召开/报告会 + 圆满/顺利/
# 成功/落幕/闭幕/落下帷幕）且无结构化讲座标签（时间:/地点: 等）时，判为新闻回顾稿。
# 正规讲座通知有结构化标签且阈值=2，故不被误杀；只有无标签的流式回顾长文才会被单标记命中。
_NARRATIVE_MARKERS = [
    '举行', '举办', '开展', '召开', '报告会',
    '圆满', '顺利', '成功', '落幕', '闭幕', '落下帷幕', '圆满结束', '圆满落幕',
]


def _narrative_process_is_retro(body):
    """RT2g：叙事体回顾稿嗅探。返回 True 表示疑似新闻回顾稿。

    阈值：正文无结构化标签(时间:/地点:等)且长度>200 → 单标记即命中；
          其余（有结构化标签，或短文本）→ 需 ≥2 个不同标记，保护正规预告。
    """
    if not body or len(body) < 30:
        return False
    # 结构化标签检测：标签后可跟冒号、空格、中文标点，或直接连数字/汉字（如 io 源的
    # "● 时间10月15日""地点华南师范大学""主讲人介绍"格式）。回顾稿极少同时含多个此类标签。
    has_structured = bool(re.search(
        r'(时间|地点|主讲|主办|承办|讲座时间|讲座地点)'
        r'(?=[\s:：:．·\d\u4e00-\u9fa5])', body))
    # RT2g 仅针对「无结构化标签的流式回顾长文」。正文已含 时间/地点/主讲 等讲座结构化标签，
    # 说明这是正规预告/通知而非回顾稿（回顾稿极少带未来讲座的结构化标签），直接判为非回顾，
    # 避免「教学创新工作坊通知」等含「举办/开展」措辞的预告被误杀（如 gxb 第51期工作坊）。
    if has_structured:
        return False
    # 讲座预告特征词守卫：正文同时含"摘要"+ 职称关键词 + "简介/介绍"等，
    # 说明是带详细主讲人简介的讲座预告页（如 ose 光电学院、部分 io 源），
    # 不是回顾稿。回顾稿不会同时具备这三个特征。
    if (re.search(r'摘要', body) and
        re.search(r'(教授|研究员|博士|院士)', body) and
        re.search(r'(简介|介绍|个人简介)', body)):
        return False
    hit = set()
    for mk in _NARRATIVE_MARKERS:
        if mk in body:
            hit.add(mk)
    threshold = 1 if len(body) > 200 else 2
    return len(hit) >= threshold


def _narrative_is_retro(body, lecture_start):
    """RT2e 约束：正文「YYYY年M月D日」+ 完成态动词才算回顾稿。

    - 该日期须与 lectureStart 同年同月（描述的是本次讲座）；否则视为讲者简介/历史叙述，不触发。
    - 完成态动词须出现在该日期后 50 字符内。
    - lecture_start 缺失时仅校验「动词在日期后50字内」（仍比原宽松版更抗误判）。
    """
    for m in re.finditer(_NEWS_NARRATIVE, body):
        nums = re.findall(r'\d+', m.group(0))[:3]
        if len(nums) == 3:
            y, mo, d = (int(x) for x in nums)
            if lecture_start:
                try:
                    ls = datetime.datetime.strptime(lecture_start[:10], '%Y-%m-%d')
                except (ValueError, TypeError):
                    ls = None
                if ls and (ls.year != y or ls.month != mo):
                    continue
        after = body[m.end():m.end() + 50]
        if re.search(_NEWS_DONE, after):
            return True
    return False


def is_news_article(title, body, lecture_start=None):
    """判断详情页是否为新闻/活动回顾稿而非讲座预告。

    返回命中的规则名（'retro-summary'/'title-conduct'/'title-retro'/'title-done'/'signature-block'/
    'narrative-completion'/'title-participate'/'narrative-process'）或 None。命中即视为
    非讲座，应在解析阶段剔除。

    仅采用高精规则，且严格区分「回顾式」与「预告式」：华师讲座预告页也常用
    「在本次报告中，我们将介绍…」这类前向句式，不能仅因出现「本次报告/本次讲座」
    就判为新闻。
    """
    t = title or ''
    b = body or ''
    # 1) 回顾式强总结语（活动已结束的报道）
    if re.search(_NEWS_RETRO_STRONG, b):
        return 'retro-summary'
    # 2) 回顾式短语（本次/此次讲座|报告 + 总结性动词，已排除「将/拟/介绍」等前向词）
    if re.search(_NEWS_RETRO, b):
        return 'retro-summary'
    # 3) 标题即回顾式：机构举办/开展…讲座（无「将/拟/计划/通知」预告词）
    if re.search(_NEWS_TITLE_CONDUCT, t):
        return 'title-conduct'
    # 3.5) 标题含「回顾」等新闻标记（非「回顾与展望」前瞻型讲座，且整标题非预告）
    if re.search(_NEWS_TITLE_RETRO, t) and not re.search(r'预告|通知|征稿|招募|报名|启事', t):
        return 'title-retro'
    # 3.6) 标题回顾完成式（RT2h）：标题含完成态动词（圆满落幕/圆满结束/成功举办…），活动已结束，非预告
    if re.search(_NEWS_TITLE_DONE, t):
        return 'title-done'
    # 4) 新闻署名审签链（供稿+初审+终审 / 初审+复审+终审），华师新闻稿专属页脚
    if re.search(_NEWS_SIGNATURE_CHAIN, b):
        return 'signature-block'
    # 5) 叙事导语（YYYY年M月D日）+ 完成态动词（RT2e 约束：同年同月 + 动词在50字内）
    if _narrative_is_retro(b, lecture_start):
        return 'narrative-completion'
    # 6) 标题机构作主语 + 参加类动词（本院是参与者而非主办方）
    if re.search(_NEWS_TITLE_PARTICIPATE, t):
        return 'title-participate'
    # 7) RT2g 叙事过程体（无结构化标签的流式回顾长文）
    if _narrative_process_is_retro(b):
        return 'narrative-process'
    return None


def _extract_narrative(body_text, title):
    """无结构化标签的叙事体文章兜底提取：主题、地点、主讲人、摘要。"""
    result = {}
    if body_text:
        body_text = re.sub(r'\s+', ' ', body_text).strip()
    # 主题：优先从标题《...》书名号提取
    if title:
        m = re.search(r'《([^《》]{3,60})》', title)
        if m:
            result['topic'] = m.group(1).strip()
    if not body_text:
        return result

    # 地点：常见“在/于...楼/室/厅/校区...举行/举办/召开”，允许楼后带房间号
    loc_patterns = [
        r'在\s*([^。，；]{2,55}?(?:楼|室|厅|馆|校区|校园|中心|广场|会议室|教室|礼堂|报告厅|学术厅|综合楼|行政楼|教学楼|信息楼|院楼|大楼)(?:\s*[0-9]+)?)\s*(?:成功)?(?:举行|举办|召开|进行|开展)',
        r'于\s*([^。，；]{2,55}?(?:楼|室|厅|馆|校区|校园|中心|广场|会议室|教室|礼堂|报告厅|学术厅|综合楼|行政楼|教学楼|信息楼|院楼|大楼)(?:\s*[0-9]+)?)\s*(?:成功)?(?:举行|举办|召开|进行|开展)',
    ]
    for pat in loc_patterns:
        m = re.search(pat, body_text)
        if m:
            loc = m.group(1).strip()
            loc = re.sub(r'^[在于]\s*', '', loc)
            result['location'] = loc
            break

    # 主讲人：从包含“主讲/主持/带来”的子句中提取
    keywords = ['主讲', '主持', '带来']
    titles = ['教授', '副教授', '讲师', '博士', '老师', '副院长', '院长']
    prefixes = [
        '由学院新引进的', '由学院', '讲座由', '由',
        '院长兼', '副院长兼', '人工智能学院', '计算机学院', '学院',
        '新引进的', '青年拔尖人才', '拔尖人才',
    ]
    for kw in keywords:
        if kw not in body_text:
            continue
        left = body_text.split(kw, 1)[0].strip()
        left = re.split(r'[。，；]', left)[-1].strip()
        changed = True
        while changed:
            changed = False
            for p in prefixes:
                if left.startswith(p):
                    left = left[len(p):].strip()
                    changed = True
        for t in titles:
            if left.startswith(t):
                left = left[len(t):].strip()
        # name + optional title
        m = re.search(r'^([\u4e00-\u9fa5]{2,4})(?:\s*(?:' + '|'.join(titles) + r'))?', left)
        if m:
            name = m.group(1).strip()
            for t in titles:
                if name.endswith(t):
                    name = name[:-len(t)].strip()
            if name and len(name) >= 2:
                result['speaker'] = name
                break
        # just name
        if re.match(r'^[\u4e00-\u9fa5]{2,4}$', left):
            result['speaker'] = left
            break

    # 主题：若标题未提供，再尝试正文中“主讲《...》”
    if not result.get('topic'):
        cm = re.search(r'主讲\s*[《<]([^》>]{3,60})[》>]', body_text)
        if cm:
            result['topic'] = cm.group(1).strip()

    # 摘要：取正文第一句之后的 1-3 句，过滤图片说明
    sentences = [p.strip() for p in re.split(r'[。\n]+', body_text) if len(p.strip()) > 20]
    if sentences:
        start_idx = 0
        # 首句若只含时间/地点/主讲元信息，则跳过
        if (re.search(r'(?:月|日|晚|召开|举行|举办|主讲|由)', sentences[0]) and
                len(sentences) > 1):
            start_idx = 1
        abstract = '。'.join(sentences[start_idx:start_idx + 3]) + '。'
        abstract = re.sub(r'[\s\S]*(Copyright|版权所有|备案|ICP|All Rights Reserved|Reserved|粤ICP)[\s\S]*', '', abstract).strip()
        abstract = re.sub(r'图\s*\d+\s*[：:].*?(?=(?:图\s*\d+|$))', '', abstract).strip()
        abstract = re.sub(r'\s*图\s*\d+\s*.*$', '', abstract).strip()
        if len(abstract) > 15:
            # 过滤：若摘要内容以日期/发布时间/字段标签开头（如"2021-12-07 19:50:50 砺儒讲坛..."），
            # 说明正文是结构化元信息而非讲座内容摘要，不应作为 abstract。
            # 常见于 ggy 等站点的预告页——整页只有元数据、没有独立的讲座摘要段落。
            _META_PREFIX = re.compile(
                r'^(\d{4}[-/]\d{2}[-/]\d{2}\s+\d{1,2}:\d{2}|'   # "2021-12-07 19:50:50"
                r'\d{4}\s*年\s*\d{1,2}\s*月|'                    # "2021年12月"
                r'Zoom\s+link|Passcode|'                           # Zoom 元信息
                r'主讲人[：:]|报告人[：:]|主持人[：:]|'           # 字段标签
                r'讲座时间[：:]|讲座地点[：:]|主办单位[：:])'     # 更多字段标签
            )
            if not _META_PREFIX.match(abstract):
                # 过滤：若摘要含侧边栏「资讯及通知」模块的行政通知列表（如
                # "关于征集国家社科基金...关于申报教育部..."），说明正文混入了
                # 全站通用的通知公告侧栏，不是讲座摘要。
                # 特征：(a) 含「资讯及通知」栏目标题；(b) 含 ≥2 条"关于…通知/公告/
                # 申报/征集/转发"短语。真实摘要绝不会出现这种列表。
                _NOTICE_LIST = re.compile(
                    r'资讯及通知|'
                    r'(?:关于.{2,40}(?:通知|公告|申报|征集|转发|招标|遴选).*){2,}'
                )
                if not _NOTICE_LIST.search(abstract):
                    # 若摘要仍含 CMS 发布元信息（来源：/评论：/点击：/收藏本文），
                    # 说明正文是「标题+字段+元信息」骨架，没有独立摘要段落，不应作为 abstract。
                    _CMS_META = re.compile(r'来源[：:]\s*\S+.*评论[：:]\s*\d+|点击[：:]\s*\d+|收藏本文|编辑[：:]\S+')
                    if not _CMS_META.search(abstract):
                        result['abstract'] = abstract
    return result


# 站点级导航/友情链接噪声：这些词只可能出现在页脚「更多链接/友情链接」区或栏目面包屑，
# 绝不会是讲座标题或系列讲坛主题的一部分。一旦出现在标题/topic 中即截断其后内容
# （如阿伯丁源 OCR 整页识别把「更多链接中国教育部教育涉外监管信息网教育部留学
# 服务中心…」这类页脚友情链接块一并吞入标题，造成标题污染）。
_NAV_NOISE_RE = re.compile(
    r'(更多链接|友情链接|快速链接|相关链接|站点导航|'
    r'教育涉外监管信息网|教育涉外监管|涉外监管信息网|'
    r'教育部留学服务中心|教育部留学|中国教育国际交流|中国教育部|'
    r'学术讲座\s*[-—]|通知公告\s*[-—]|新闻动态\s*[-—]|'
    r'首页|»\s*正文|»)'
)


def _strip_nav_noise(s):
    """截断标题/主题中混入的站点导航链接噪声（如「更多链接中国教育部…」）。"""
    if not s:
        return s
    m = _NAV_NOISE_RE.search(s)
    if m:
        s = s[:m.start()]
    s = s.strip(' —-丨|·\t')
    # 去掉前导孤立数字（OCR 把装饰/页码误识为 00 等，如「00 更多链接…」）。
    # 限制 1-2 位且后面必须是非数字/结束，避免把 YYYYMMDD 前缀的「20」或「202」吃掉。
    s = re.sub(r'^\s*\d{1,2}(?=\D|$)\s*', '', s).strip(' —-丨|·\t')
    return s.strip()


def _clean_session_topic(t):
    """清洗多讲座拆分时提取的各期主题：在导航噪声基础上，再去除栏目前缀
    （如「讲座预告丨」）与尾部粘连的日期/时间片段（候选5 无结构化标签页面的
    seg 常把「时间：2023-11-08 10:00」一并吞入主题）。"""
    if not t:
        return t
    t = _strip_nav_noise(t)
    if not t:
        return t
    # 去栏目前缀
    t = re.sub(r'^讲座预告\s*[丨|：:]?\s*', '', t).strip(' —-丨|·\t')
    t = re.sub(r'^预告\s*[：:]\s*', '', t).strip(' —-丨|·\t')
    # 去尾部粘连的日期/时间片段（月份数字可能被 OCR 漏识，故分别覆盖「N月N日」与「月N日」）
    t = re.sub(r'\s*\d{4}[-/年\.]\d{1,2}[-/月\.]\d{1,2}.*?(\s*\d{1,2})?\s*$', '', t).strip(' —-丨|·\t')
    t = re.sub(r'\s*\d{1,2}月\d{1,2}日.*$', '', t).strip(' —-丨|·\t')
    t = re.sub(r'\s*月\d{1,2}日.*$', '', t).strip(' —-丨|·\t')
    t = re.sub(r'\s*时间\s*$', '', t).strip(' —-丨|·\t')
    # 去页面交互噪声（OCR 把「点击：00」「00 点击」「点击」等按钮/计数文字误识为主题）
    t = re.sub(r'^(?:点击[:：]?\s*)?\d+\s*点击.*$', '', t).strip(' —-丨|·\t')
    t = re.sub(r'\s*点击[:：]?\s*\d*\s*$', '', t).strip(' —-丨|·\t')
    # 去掉纯数字/纯时间类的无效主题
    if re.match(r'^[\s:：\d]+$', t):
        return ''
    return t.strip()


def _abstract_is_nav_noise(ab):
    """判断 abstract 是否被页面导航/页脚垃圾污染（如「学术交流/评论/点击/收藏」）。"""
    ab = ab or ''
    nav = ('学术交流', '评论', '点击', '收藏', '责任编辑', '本文来自', '来源：')
    return any(k in ab for k in nav)


def _is_meta_skeleton(text):
    """判断 body_text 是否为「仅由结构化字段/发布元信息组成的空壳页面」。

    典型场景：地理科学学院、部分院系 CMS 把讲座海报做成图片，HTML 正文区只写
    「题目：… 主讲人：… 时间：… 地点：…」等字段，真正的讲座摘要/简介全在图片里。
    此时若按 narrative fallback 取「正文第一句」会得到 title+元信息+字段标签的混合物。
    该函数去除这些结构化字段标签与值、以及来源/评论/点击/收藏等元信息后，若剩余
    可阅读内容极少（<50 字符），则判定为骨架页，应触发 OCR 读图。
    """
    if not text or len(text) < 30:
        return False
    # 常见结构化字段：题目/主题/主讲人/报告人/时间/地点/主持人/邀请人/来源/评论/点击/收藏本文
    # 用非贪婪匹配取值直到下一个字段标签，允许值内部含冒号/数字。
    _LABELS = (
        r'题目|主题|报告题目|讲座题目|主讲[人师]?|报告人|演讲人|主持人|邀请人|'
        r'时间|时闻|地点|来源|评论|点击|收藏本文|收藏|发布时间|作者|编辑|审核|'
        r'摘要|讲座简介|报告简介|内容简介'
    )
    stripped = re.sub(
        rf'(?:{_LABELS})[：:]\s*(.+?)(?=(?:{_LABELS})[：:]|$)',
        '', text, flags=re.S)
    # 去掉孤立字段标签残留与多余空白
    stripped = re.sub(r'[\s\u3000]+', ' ', stripped).strip()
    # 若去掉字段后剩余可读文本很少，且原文本足够长（说明全是字段），判定为骨架页
    return len(stripped) < 50 and len(text) - len(stripped) > 60


def _clean_title(t):
    t = t.strip()
    if ' - ' in t:
        t = t.split(' - ')[0].strip()
    if '｜' in t:
        t = t.split('｜')[0].strip()
    # 去掉 io 源等常见的通用前缀：「【讲座通知】""讲座通知""讲座通知 |""讲座通知（"
    # 分步处理避免字符类中 ] 的转义问题
    t = re.sub(r'^[\s【\[]*讲座通知[\s】]', '', t).strip()
    t = re.sub(r'^[\s｜|：:]*', '', t).strip()
    t = re.sub(r'^讲座通知[｜|（(]', '', t).strip()
    # 去掉标题两侧的中文引号（io 源标题常带 "实际标题" 格式）
    if len(t) > 4 and t.startswith('"') and t.endswith('"'):
        t = t[1:-1].strip()
    if len(t) > 4 and t.startswith('"') and t.endswith('"'):
        t = t[1:-1].strip()
    # ���掉前导的 | （分割符残留）、尾部孤括号
    t = re.sub(r'^[｜|\s]+', '', t).strip()
    t = re.sub(r'[）)]$', '', t).strip()
    # 列表页锚文本常把发布日期前缀粘进标题（如「2024-05-21艺术乡建…」「2023年12月24日红树林…」）。
    # 去掉标题开头的日期前缀，仅保留真实讲座标题。日期本身已由时间解析单独处理。
    t = re.sub(r'^\s*(?:19|20)\d{2}\s*[-/年\.]\s*\d{1,2}\s*[-/月\.]\s*\d{1,2}\s*[日号]?\s*', '', t).strip()
    # 去掉 8 位连写日期前缀，如「20250911 讲座标题」
    t = re.sub(r'^\s*(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\s+', '', t).strip()
    # 多场拆分偶发把首个章节头粘进标题（如教师发展中心「— 一、工作坊安排」「— 一、培训安排」
    # 「— 一、沙龙安排」），去掉标题尾部这种非标题的章节安排标记。
    t = re.sub(r'\s*[—\-－]\s*一、[一二三四五六七八九十百零0-9]*期?\s*(?:工作坊|培训|沙龙|讲坛|报告|讲座)?安排\s*$', '', t).strip()
    t = re.sub(r'\s*一、[一二三四五六七八九十百零0-9]*期?\s*(?:工作坊|培训|沙龙|讲坛|报告|讲座)?安排\s*$', '', t).strip()
    t = _strip_nav_noise(t)
    return t


# 全校级页脚/导航标记：这些文本只可能出现在站点全局页脚，绝不会出现在讲座正文里。
# 一旦在正文文本中检测到，其后的内容即页脚噪声，应整体截断。
_FOOTER_MARKERS = (
    '关于华南师范大学',   # 学校 about 页链接，历史文化学院等页脚
    '版权所有',           # 页脚版权行（含 All Rights Reserved）
    'All Rights Reserved',
    '粤ICP',              # 备案号
    '常用链接',           # 页脚友情/常用链接区起始
    '统一认证',           # 页脚统一认证入口
    '移动平台',           # 页脚移动平台入口
    '旧版网站', '网站地图', '无障碍', '联系我们',
)


def _strip_footer(text):
    """截断全校级页脚/导航噪声，避免其被误并入 location/topic 等字段。

    仅当标记出现在文本后 30% 时才截断——页脚必然在正文之后，此守卫可排除
    正文中偶发的同名词（如「联系我们」）造成的误删。
    """
    if not text:
        return text
    cut = -1
    for mk in _FOOTER_MARKERS:
        idx = text.find(mk)
        if idx > 0 and idx >= len(text) * 0.3:
            cut = idx if cut == -1 else min(cut, idx)
    if cut != -1:
        text = text[:cut]
    return text.strip()


def _normalize_label_text(text):
    """去除常见字段标签中因 CMS 拆分 span 而混入的空格，如「题 目」「地 点」「主 讲 人」。"""
    text = _n1e_normalize(text)  # N1e：混合中英文标签拆分（"时间/Time:" → "时间：Time："）
    labels = [
        # 主题/题目
        '题目', '主题', '讲座主题', '报告题目', '演讲题目', '报告主题', '讲座题目',
        # 时间地点人物
        '讲座地点', '讲座时间', '地点', '时间', '主讲人', '主讲师', '报告人', '主讲嘉宾', '讲座嘉宾', '演讲人', '主讲',
        '学术主持', '主办单位',
        # 简介/摘要/内容
        '主讲嘉宾介绍', '主讲人简介', '主讲人简历', '简历', '简介',
        '摘要', '讲座内容', '讲座内容提要', '内容提要', '讲座摘要',
        '报告摘要', '内容摘要', '内容简介', '讲座简介', '报告内容', '讲座概要', '内容概要', '主要内容',
        # 其它常用字段（防越界）
        '面向对象',
        # 发布信息
        '发布时间', '发布日期', '来源',
    ]
    # 1) 先把方括号/花括号形式的标签统一转成「标签：」
    #    如美术学院页面：【主题】xxx、【主讲人】xxx、【时间】xxx、【地点】xxx
    #    阿伯丁早期页面在标签后还有「 】 ：」，需把标签后的可选空格+冒号一并吃掉，避免「标签：：值」。
    for label in sorted(labels, key=len, reverse=True):
        text = re.sub(rf'[【\[]\s*{re.escape(label)}\s*[】\]]\s*[：:]?', label + '：', text)
    # 2) 再处理 CMS 把标签拆成单字加空格的情况，如「题 目：」「主 讲 人：」
    # 先匹配长的复合标签，避免「讲座内容」把「讲座内容提要」先吃掉
    for label in sorted(labels, key=len, reverse=True):
        spaced = ''.join(c + r'\s*' for c in label)
        text = re.sub(spaced, label, text)
    return text


def _date_from_url(url):
    """从内容页 URL 路径提取完整日期 (year, month, day)，失败返回 None。

    兼容：
      - /a/20251201/xxx.html  -> (2025, 12, 1)
      - /a/2025/0507/xxx.html -> (2025, 5, 7)
      - /xxx/2025/1028/xxx.html -> (2025, 10, 28)  (汕尾校区等栏目)
      - /xxx/2025/10/28/xxx.html -> (2025, 10, 28)  (部分老站/国际站)
    """
    if not url:
        return None
    # 匹配 /a/YYYYMMDD/ 紧凑日期路径
    m = re.search(r'/a/(20\d{2})(\d{2})(\d{2})/', url)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return y, mo, d
    # 匹配 /a/YYYY/MMDD/ 路径
    m = re.search(r'/a/(20\d{2})/(\d{2})(\d{2})/', url)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return y, mo, d
    # 匹配 /xxx/YYYY/MMDD/xxx.html（如汕尾校区 /collaborative/2022/1028/36.html）
    m = re.search(r'/(20\d{2})/(\d{2})(\d{2})/[^/]+\.html?$', url)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return y, mo, d
    # 匹配 /xxx/YYYY/MM/DD/xxx.html（部分老站/国际站）
    m = re.search(r'/(20\d{2})/(\d{1,2})/(\d{1,2})/[^/]+\.html?$', url)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return y, mo, d
    return None


def _year_from_url(url):
    """从内容页 URL 路径提取年份。兼容：/a/20251201/xxx.html -> 2025。"""
    d = _date_from_url(url)
    return d[0] if d else None


def _split_location_time(loc):
    """从地点字段中抽取被 OCR 混入的时间区间，返回 (clean_loc, (h0, m0, h1, m1) or None)。

    汕尾校区教学工作坊等海报详情页：正文只有一张海报图，OCR 出的正文既含地点也含时间，
    如「南教-209教室14: 30-17: 00」「蔚教-101教室14: 30-16: O0士办单位 …」。
    把时间分离出来，地点只保留「南教-209教室」这样的纯地点，时间稍后补到 lectureStart/End。
    OCR 常见噪声：冒号被识为分号「;」、把 0 写成 O/o。需一并容忍。
    """
    if not loc:
        return loc, None
    # 容忍 OCR 把时间区间的冒号写成「;」、把 0 写成 O/o
    pat = re.compile(
        r'(\d{1,2})\s*[:;]\s*([O0-9]{1,2})\s*[-–~—]\s*(\d{1,2})\s*[:;]\s*([O0-9]{1,2})?'
    )
    m = pat.search(loc)
    if not m:
        return loc, None

    def fix(x):
        return int(str(x).replace('O', '0').replace('o', '0'))

    try:
        h0, m0 = fix(m.group(1)), fix(m.group(2))
        h1, m1 = fix(m.group(3)), fix(m.group(4)) if m.group(4) else 0
    except (ValueError, TypeError):
        return loc, None
    # 合理性校验（含 OCR 把 17:30 误识成 17:30 等正常情况）；越界则放弃分离
    if not (0 <= h0 < 24 and 0 <= m0 < 60 and 0 <= h1 < 24 and 0 <= m1 < 60):
        return loc, None
    # 截掉时间及其后的「主办单位…」等 OCR 噪声，仅保留地点本体
    clean = loc[:m.start()].strip()
    clean = re.sub(r'[\s]*[曷号]+$\s*', '', clean).strip()
    clean = re.sub(r'\s+[^\u4e00-\u9fa5a-zA-Z0-9]{1,2}\s*$', '', clean).strip()
    return clean or loc, (h0, m0, h1, m1)


def _cross_validate(result, url_date, ocr_text, publish_time, url_year):
    """CV1 三校验对 + CV3 轻量。

    仅打 note（不自动改值），仅 CV3 在明确异常时修正：
      - 结束时间早于开始时间 → 交换 start/end；
      - 时分越界（hour 0–23 / minute 0–59）→ 置空该字段并记 note。
    CV1a：URL 日期 ↔ lectureStart（差异 > 30 天标 cv-url-date-mismatch）
    CV1b：publishTime ↔ lectureStart（发布晚于讲座标 cv-publish-after-lecture，R6 逻辑）
    CV1c：OCR 日期 ↔ HTML 日期（两者皆有时差异标 cv-ocr-html-date-mismatch）
    """
    notes = []
    ls = result.get('lectureStart')
    # CV1a
    if ls and url_date:
        try:
            ls_d = datetime.date.fromisoformat(ls[:10])
            url_d = datetime.date(*url_date)
            if abs((ls_d - url_d).days) > 30:
                notes.append('cv-url-date-mismatch')
        except (ValueError, TypeError):
            pass
    # CV1b
    pub_d = _date_head(publish_time or '')
    if ls:
        ls_d = _date_head(ls)
        if pub_d and ls_d and pub_d > ls_d:
            notes.append('cv-publish-after-lecture')
    # CV3：结束早于开始 → 交换；时分越界 → 置空
    le = result.get('lectureEnd')
    if ls and le:
        try:
            st = datetime.datetime.fromisoformat(ls)
            en = datetime.datetime.fromisoformat(le)
            if en < st:
                result['lectureStart'] = le
                result['lectureEnd'] = ls
                notes.append('cv-end-before-start-swapped')
        except (ValueError, TypeError):
            pass
    for f in ('lectureStart', 'lectureEnd'):
        v = result.get(f)
        if v:
            try:
                dt = datetime.datetime.fromisoformat(v)
                if not (0 <= dt.hour < 24 and 0 <= dt.minute < 60):
                    result[f] = None
                    notes.append('cv-time-out-of-range:' + f)
            except (ValueError, TypeError):
                pass
    # CV1c：OCR 日期 ↔ HTML 日期
    if ocr_text and ls:
        try:
            to = parse_cn_time(ocr_text, None, publish_time=publish_time, url_year=url_year)
            if to and to.get('start'):
                ocr_d = to['start'].date()
                ls_d = datetime.date.fromisoformat(ls[:10])
                if ocr_d != ls_d:
                    notes.append('cv-ocr-html-date-mismatch')
        except (ValueError, TypeError):
            pass
    return notes


def _locate_publish_time(soup, content_div, body_text, full_text):
    """R3 发布时间精确定位：标签 > 伴生词/class > 位置兜底。返回 (publish_time, level)。

    level: 1=显式标签, 2=伴生词/class, 3=位置兜底。用于 R3 本质条款——
    第 2/3 级兜底抓到的候选若等于权威讲座日，视为误抓讲座时间，作废该候选；
    第 1 级标签值即使等于讲座日也保留（R5：同天发布同天讲属正常）。

    本质条款（修 Bug A）：本函数只在"定位发布时间"这一动作里工作，绝不对讲座正文
    做任何字符串删除——发布日排除只作用于此处，不影响正文日期解析。
    """
    PUB = r'(?:发布(?:时间|日期)?|发表时间|发布于|posted|date)\s*[：:]?\s*(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)'
    # Level 1：显式标签（正文优先，整页兜底）
    m = re.search(PUB, body_text, re.IGNORECASE)
    if m:
        return m.group(1).strip(), 1
    m = re.search(PUB, full_text, re.IGNORECASE)
    if m:
        return m.group(1).strip(), 1
    # Level 2：class 命中 .info/.meta/.article-info/.pub
    if content_div:
        for tag in content_div.find_all(class_=re.compile(r'info|meta|pub|article-info', re.I)):
            mm = re.search(r'(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)', tag.get_text())
            if mm:
                return mm.group(1).strip(), 2
    # Level 2：伴生词行（来源/点击/评论/浏览/作者）
    # 兼容两种顺序：(a) 关键词在前（「点击：2026-05-20」）；(b) 日期在前（「2026-05-20 15:09:00 点击：76」）
    m = re.search(r'(?:来源|点击|评论|浏览|作者)[：: ]*\D{0,20}?(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)', body_text)
    if not m:
        m = re.search(r'(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)\D{0,20}?:\d{2}\s*(?:来源|点击|评论|浏览|作者)', body_text)
        if not m:
            m = re.search(r'(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)\D{0,20}?(?:来源|点击|评论|浏览|作者)', body_text)
    if m:
        return m.group(1).strip(), 2
    # Level 3：位置兜底（正文第一个日期）
    m = re.search(r'(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)', body_text)
    if m:
        return m.group(1).strip(), 3
    m = re.search(r'(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2}(?::\d{2})?)?)', full_text)
    if m:
        return m.group(1).strip(), 3
    return None, 0


def parse_detail(html, url, college, campus, default_year=None, list_title=None, skip_news_filter=False):
    soup = BeautifulSoup(html, 'html.parser')
    # 列表页标题通常就是干净的讲座标题，优先使用；否则回退到详情页 h1/title
    # 注意：部分站点（如 io 国际交流合作处）的 <h1> 是栏目名（"通知公告"）而非文章标题，
    # 真正标题在 <h3> 或 <title> 标签中。需检测并跳过栏目名。
    _SECTION_NAME_RE = re.compile(
        r'^(?:通知公告|新闻动态|学术讲座|新闻详情|通知|公告|新闻|动态|'
        r'首页|主页|关于我们|联系我们|列表|详情)$'
    )
    if list_title:
        title = _clean_title(list_title)
    else:
        h1 = soup.find('h1') or soup.find('h2')
        if h1:
            h1_text = h1.get_text(strip=True)
            # h1 是短栏目名（≤6字且命中常见栏目词）→ 跳过，尝试 h3 或 <title>
            if len(h1_text) <= 6 or _SECTION_NAME_RE.match(h1_text):
                h3 = soup.find('h3') or soup.find('h4')
                if h3:
                    title = h3.get_text(strip=True)
                elif soup.title:
                    title = soup.title.get_text(strip=True)
                else:
                    title = h1_text
            else:
                title = h1_text
        elif soup.title:
            title = soup.title.get_text(strip=True)
        else:
            title = ''
        title = _clean_title(title)

    text = soup.get_text(' ')
    # 美术学院等站点：正文可能是图片，但 meta description / og:description 里保存了结构化文字
    meta_parts = []
    for meta in (
        soup.find('meta', attrs={'name': 'description'}),
        soup.find('meta', property='og:description'),
        soup.find('meta', attrs={'name': 'og:description'}),
    ):
        if meta and meta.get('content') and len(meta.get('content').strip()) > 3:
            meta_parts.append(meta.get('content').strip())
    if meta_parts:
        text = text + ' ' + ' '.join(meta_parts)
    text = re.sub(r'\s+', ' ', text).strip()
    text = _n1_normalize(text)  # N1：全角标点统一为半角
    text = _normalize_label_text(text)
    # 截断全校级页脚/导航噪声（如「关于华南师范大学 | 统一认证 | 移动平台」），
    # 否则 location/topic 等字段会一直吞到文末把页脚吃进来。
    text = _strip_footer(text)

    # 提前定位正文容器；若正文几乎为空但含图片（如行知书院讲座海报），对图片 OCR 提取文字
    content_div = (soup.find('div', class_='wp_articlecontent')   # WebPlus CMS（生命科学学院等）
                   or soup.find('div', class_='wp_entry')
                   or soup.find('div', class_='article-content')
                   or soup.find('div', class_='container-left')     # 图书馆等站点：左侧正文区（含 iframe PDF 海报）
                   or soup.find('div', class_='article')            # 地理科学学院等 CMS 的真实正文区
                   or soup.find('div', class_='content')
                   or soup.find('div', class_='news-details-all')
                   or soup.find('div', class_='news-details-middle')
                   or soup.find('article')
                   or soup.find('div', class_='entry-content'))
    body_text = content_div.get_text(' ') if content_div else text
    body_text = re.sub(r'\s+', ' ', body_text).strip()
    body_text = _n1_normalize(body_text)  # N1：全角标点统一为半角
    body_text = _normalize_label_text(body_text)
    body_text = _strip_footer(body_text)
    ocr_text = ''
    # 提前从 URL 解析年份/完整日期（供 OCR 图片年份门控、CV1 校验、最终兜底共用）
    url_year = _year_from_url(url)
    url_date = _date_from_url(url)
    # 预收集正文图片（用于「解析不到日期 / 字段缺失时按需 OCR 海报」）。
    # 图片收集范围严格限定在 content_div 内部（钉死），不再整页回退到 soup：
    # 实测地科院等站点海报图都在正文 div 内，整页收集会引入 logo/页脚二维码/导航图标等
    # 无关 chrome 图，徒增 VLM/OCR token 消耗与误判。某页若无 content_div，宁可漏抓也不引无关图。
    def _is_chrome_img(src):
        """过滤站点级装饰图（导航/页脚图标、logo、横幅、二维码、关注/公众号等），
        避免对无关图做 VLM/OCR，减少 token 消耗与误判。

        URL 可能含百分号编码的中文（如 %E4%BA%8C%E7%BB%B4%E7%A0%81=二维码），
        先解码再判断。仅保留「装饰专用、几乎不会出现在真实海报文件名」的词，
        避免子串误杀含 upload/ad/bg/top/more/foot 的真实海报路径（如 /upload/ 下的图
        会被 'ad' 误伤——这会直接吞掉整站 poster 图，是此前 poster 页解析失败的根因之一）。
        """
        s = (src or '').lower()
        s2 = unquote(s)
        bad = ('logo', 'banner', 'icon', 'avatar', 'arrow', 'btn', 'nav', 'share',
               'close', 'header', 'slide', 'weixin', 'wechat', 'wx', 'qr', 'qrcode',
               'qr-code', 'scan', 'saoma', 'carousel', 'flash', 'pixel', 'spacer',
               '二维码', '关注', '公众号', '扫码', '订阅')
        return any(k in s or k in s2 for k in bad)

    def _is_banner_parent(el):
        """排除位于 header/footer/nav/banner 区域的站点级装饰图（非讲座海报）。"""
        for node in (el, el.parent if el.parent else None):
            if not node:
                continue
            cls = ' '.join(node.get('class', []) or [])
            if re.search(r'header|footer|banner|nav|topbar|sidebar|tool|crumb|logo', cls, re.I):
                return True
        return False

    # 文章年份：用于门控「路径年份与文章年份相差 >2 年」的装饰图（如 2021 站点横幅）。
    # 此时 publish_time 尚未定位，直接用 URL 日期年（最可靠、与海报上传目录年份一致）。
    art_year = url_date[0] if url_date else None

    imgs = []
    # 图片收集根限定为正文容器内部（钉死），不再退化为整页 soup（避免引入无关 chrome 图）。
    img_src_root = content_div
    if img_src_root:
        for img in img_src_root.find_all('img'):
            src = img.get('src') or img.get('data-src')
            if not src:
                continue
            if src.lower().endswith(('.svg', '.gif')):
                continue
            abs_src = urljoin(url, src)
            if _is_chrome_img(abs_src):
                continue
            if _is_banner_parent(img.parent if img.parent else img):
                continue
            # 年份门控：路径里的年份与文章年份相差 >2 年（如 2021 站点横幅 vs 2024 文章），
            # 极可能是站点级装饰图而非当次讲座海报，排除避免污染 OCR。
            if art_year:
                ym = re.search(r'/(20\d{2})[/-]\d{1,2}[/-]', abs_src)
                if ym and abs(int(ym.group(1)) - art_year) > 2:
                    continue
            imgs.append(abs_src)
    # 优先取带日期路径的图片（海报多上传到 /YYYY/MM/ 目录），其余兜底
    dated = [c for c in imgs if re.search(r'/\d{4}[/-]\d{1,2}[/-]', c)]
    imgs = dated or imgs
    imgs = imgs[:3]

    # PDF-INLINE: 部分站点（如工学部）正文仅含 <iframe> 嵌入 PDF 或 .pdf 下载链接，
    # HTML 本身无结构化讲座信息。检测此类情况并自动下载 PDF 提取文本，
    # 作为 body_text 的补充来源参与后续字段抽取（speaker/location/time/abstract 等）。
    _pdf_text = ''
    _pdf_poster_converted = False
    if len(body_text.strip()) < 150:
        _pdf_url = None
        # 策略1：从 iframe src 中提取 PDF URL（工学部用 viewer2.html#URL 格式）
        for iframe in (content_div or soup).find_all('iframe'):
            isrc = (iframe.get('src') or '')
            if 'viewer' in isrc or '.pdf' in isrc.lower():
                m = re.search(r'#(.+\.pdf)', isrc, re.I)
                if m:
                    _pdf_url = m.group(1)
                    break
                # 完整 PDF URL
                if isrc.lower().endswith('.pdf'):
                    _pdf_url = isrc
                    break
        # 策略2：从 <a> 标签的 href 中找 .pdf 链接（文件下载）
        if not _pdf_url:
            for a in (content_div or soup).find_all('a', href=True):
                href = a.get('href', '')
                if href.lower().endswith('.pdf') and '通知' in a.get_text():
                    _pdf_url = href
                    break
        if _pdf_url:
            try:
                _abs_pdf = urljoin(url, _pdf_url) if not _pdf_url.startswith('http') else _pdf_url
                if _abs_pdf.startswith('//'):
                    _abs_pdf = 'http:' + _abs_pdf
                import urllib.request as _urllib_req, ssl as _ssl
                _pdf_req = _urllib_req.Request(_abs_pdf, headers={'User-Agent': 'Mozilla/5.0'})
                _pdf_ctx = _ssl.create_default_context()
                _pdf_ctx.check_hostname = False
                _pdf_ctx.verify_mode = _ssl.CERT_NONE
                _pdf_resp = _urllib_req.urlopen(_pdf_req, timeout=20, context=_pdf_ctx)
                _pdf_data = _pdf_resp.read()
                if _pdf_data[:5] == b'%PDF-':
                    import io
                    try:
                        import fitz as _fitz
                        _doc = _fitz.open(stream=io.BytesIO(_pdf_data), filetype='pdf')
                        _pages_text = []
                        for _pg in _doc:
                            _t = _pg.get_text()
                            if _t.strip():
                                _pages_text.append(_t)
                        _pdf_text = '\n'.join(_pages_text)
                        if _pdf_text:
                            body_text = body_text + '\n' + _pdf_text
                            text = text + '\n' + _pdf_text
                        # PDF-POSTER-VLM: PDF 文件名含"海报"、正文原本极短，或正文容器内直接嵌 iframe/PDF，
                        # 把第一页转成图片，让后续 poster_only VLM 路径补齐地点/摘要等字段。
                        _is_poster_pdf = ('海报' in (_abs_pdf or '')) or (len(body_text.strip()) < 150) or (content_div and bool(content_div.find('iframe')))
                        if _is_poster_pdf and _doc.page_count > 0:
                            try:
                                from PIL import Image as _Image
                                _pg = _doc[0]
                                _mat = _fitz.Matrix(2, 2)
                                _pix = _pg.get_pixmap(matrix=_mat)
                                _tmp_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'tmp', 'pdf_posters')
                                _os.makedirs(_tmp_dir, exist_ok=True)
                                _pdf_basename = re.sub(r'[^\w.-]', '_', _os.path.basename(unquote(_abs_pdf)))
                                _tmp_path = _os.path.join(_tmp_dir, _pdf_basename + '.png')
                                _pix.save(_tmp_path)
                                _im = _Image.open(_tmp_path)
                                if max(_im.size) > 2000:
                                    _im.thumbnail((2000, 2000))
                                    _im.save(_tmp_path)
                                imgs.append(_tmp_path)
                                _pdf_poster_converted = True
                            except Exception:
                                pass  # PDF 转图失败不阻塞主流程
                        _doc.close()
                    except ImportError:
                        pass  # PyMuPDF 未安装时静默跳过
                    except Exception:
                        pass  # PDF 解析失败时不阻塞主流程
            except Exception:
                pass  # PDF 下载失败时不阻塞

    def _do_ocr():
        """对正文海报图片做 OCR，把识别文字并入 text / body_text（仅做一次）。"""
        nonlocal ocr_text, body_text, text
        if ocr_text or not imgs:
            return
        raw = ' '.join(_img_to_text(img) for img in imgs[:3])
        if raw:
            # 清理 OCR 中常见的顶部/底部噪声
            ocr_text = _clean_ocr_text(raw)
            # N1（全角→半角 + N1a 去 CJK 内部空格）：OCR 文本也应归一化，确保标签可扫描。
            # 注意：OCR 路径用 keep_word_boundaries=True（O3a 修正，保留词块边界空格供 O6d-2.5 定位）；
            # HTML 正文路径走默认 False（删除所有 CJK 空格，避免破坏姓名/标签识别）。
            ocr_text = _n1_normalize(ocr_text, keep_word_boundaries=True)
            # N1d：仅对 OCR 文本在三类数字上下文内纠正易混字符（O/o→0、l/I/|→1、;→:、〇→0）
            ocr_text = _ocr_char_fix(ocr_text)
            # 重新归一化标签（N1/N1e），使 OCR 文本里的中英文标签也能被正确扫描
            body_text = _normalize_label_text((body_text + ' ' + ocr_text).strip())
            text = _normalize_label_text((text + ' ' + ocr_text).strip())

    # 纯海报页（正文几乎为空 / 正文虽长但全是 CMS 元信息无结构化讲座标签）
    # body_text < 150 → 几乎可确认是海报页。阈值从 50 放宽至 150，覆盖 skc/abdn 等
    # HTML 包裹层有文本但无「时间:/地点:/主讲人:」标签的页面，避免坐等失败。
    # 额外覆盖「骨架页」：正文区只有「题目/主讲人/时间/地点」字段+图片（如地科院），
    # 真正摘要全在图片里，必须 OCR 才能拿到。该检测在 imgs 已收集后执行。
    vlm_fields = None
    _t_vlm = None
    _vlm_sessions = None
    poster_only = ((len(body_text) < 150
                    and not re.search(r'(?:时间|地点|主讲[人师]|报告人)[：:]', body_text))
                   or (bool(imgs) and _is_meta_skeleton(body_text))
                   or _pdf_poster_converted)
    if poster_only:
        # 优先用多模态 LLM 结构化提取海报；无 key / 失败则降级回 rapidocr。
        # 逐张尝试 imgs（而非前 N 张拼接发送）：海报页常混入 logo/横幅/其他讲座图，
        # 拼接发送会干扰 VLM 提取（如阿伯丁 362.html 图1 为学院标识、图2 才是讲座海报，
        # 两图同发导致 VLM 失败回退 OCR）。逐张取首个返回有效字段的图即可正确命中。
        vlm_fields = None
        for _u in imgs[:3]:
            _f = _vlm_extract_fields([_u], _load_vlm_config())
            if _f:
                vlm_fields = _f
                break
        if not vlm_fields:
            _do_ocr()

    # R3 发布时间定位（标签 > 伴生词/class > 位置兜底）
    publish_time, publish_level = _locate_publish_time(soup, content_div, body_text, text)

    # 从标题提取显式年份（标题兼容紧凑格式 20251204）；URL 年份/日期已在上方提前计算
    title_year = _year_from_text(title) if title else None

    # 海报 OCR 场景中，地点字段常混入时间区间（如「南教-209教室14: 30-17: 00」），
    # 用 loc_times 累积「(start_h, start_m, end_h, end_m)」元组，解析完日期后回填到讲座时间。
    loc_times = []

    result = {
        'sourceUrl': url,
        'images': imgs,   # 海报图 URL 列表（content_div 内部收集，持久化落库供前端/日后重处理）
        'college': college,
        'campus': campus,
        'title': title,
        'topic': '',
        'lectureStart': None,
        'lectureEnd': None,
        'location': '',
        'speaker': '',
        'speakerTitle': '',
        'speakerAffiliation': '',
        'inviter': '',
        'speakerBio': '',
        'organizer': college,
        'publishTime': publish_time,
        'publishTimeSource': None,
    }

    # 海报图片标记：只要本讲座涉及海报图片处理（OCR 或 VLM），统一标记 hasPosterImage=True，
    # 与具体处理手段无关，便于日后批量捞出「含海报图」的讲座统一处理（区别于纯文本页）。
    if poster_only:
        result['hasPosterImage'] = True
    # OCR 成功提取到文字则打标记（poster_only 分支在此统一标记，避免其在 result 初始化前访问）
    if poster_only and ocr_text:
        result['ocrExtracted'] = True
        result['imageParseMethod'] = 'ocr'
    elif poster_only and vlm_fields:
        result['imageParseMethod'] = 'vlm'

    # ---- 讲座时间抽取 R1–R6（编排见 timeparse.resolve_lecture_time）----
    # R3 本质条款：发布日排除只作用于定位 publish_time（已在上方完成），绝不删除正文日期（修 Bug A）。
    # R2：通用解析只扫正文 body_text（content_div 去噪），不扫整页侧边栏/页脚。
    # R5：讲座日 = 发布日属正常，不再因此置空或降级（原同天降级逻辑已删除）。
    t = None
    t_untrusted = False
    rt = resolve_lecture_time(
        body_text=body_text,
        title=title,
        url_year=url_year,
        title_year=title_year,
        publish_time=publish_time,
        publish_level=publish_level,
        default_year=default_year,
        list_title=list_title,
    )
    # VLM 优先路径：海报经多模态模型结构化提取，成功则填字段并旁路 OCR 补字段。
    # 支持多讲座海报：VLM 返回 list 时对每场讲座生成独立记录（isMultiLecture/lectureIndex/lectureCount）。
    if vlm_fields:
        applied = _apply_vlm_to_result(result, vlm_fields, default_year, publish_time,
                                       title_year, url_year, poster_only=poster_only)
        if isinstance(vlm_fields, list) and isinstance(applied, list):
            # 多讲座拆分：按讲座开始时间排序，时间早的期数靠前（避免海报视觉排版顺序≠时间顺序）
            if len(applied) > 1:
                applied.sort(key=lambda x: x[0].get('lectureStart') or '9999-99-99 99:99:99')
            _vlm_sessions = []
            for i, (partial, pt) in enumerate(applied, 1):
                partial['vlmExtracted'] = True
                partial['imageParseMethod'] = 'vlm'
                partial['hasPosterImage'] = True
                if len(applied) > 1:
                    partial['isMultiLecture'] = True
                    partial['lectureIndex'] = i
                    partial['lectureCount'] = len(applied)
                _vlm_sessions.append((partial, pt))
        else:
            # 单讲座（原逻辑）
            result['vlmExtracted'] = True
            result['imageParseMethod'] = 'vlm'
            result['hasPosterImage'] = True
            if applied:
                t = applied

    if rt and rt.get('start'):
        t = {'start': datetime.datetime.fromisoformat(rt['start']),
             'end': datetime.datetime.fromisoformat(rt['end']) if rt.get('end') else None,
             'has_time': True}
        result['timeConfidence'] = rt.get('confidence')
        result['timeNote'] = rt.get('note')
    # 正文未解析出日期且含海报图片：OCR 后重试（仅补缺失，不覆盖已有）
    if not t and imgs and not vlm_fields:
        _do_ocr()
        if ocr_text:
            result['imageParseMethod'] = 'ocr'
            result['hasPosterImage'] = True
            t_ocr = parse_cn_time(ocr_text, default_year, publish_time=publish_time,
                                  title_year=title_year, url_year=url_year)
            tm = re.search(r'(?:讲座)?时间[：:\s]*(.{0,40})', ocr_text)
            if tm:
                t_label = parse_cn_time(tm.group(1).strip(), default_year,
                                        publish_time=publish_time, title_year=title_year, url_year=url_year)
                if t_label:
                    t_ocr = t_label
            if t_ocr:
                t = t_ocr
    if not t and list_title:
        # 兜底：部分站点讲座日期只在列表标题里（如心理学院）
        t = parse_cn_time(list_title, default_year, publish_time=publish_time,
                          title_year=title_year, url_year=url_year)
    if not t and title:
        # R4：标题完整日期兜底（YYYY年MM月DD日 / 紧凑 YYYYMMDD / YYYY-MM-DD）。
        # 优先级低于正文/OCR/列表标题，但高于不可信的 URL 路径日期（常为发布/通知日）。
        td = _date_from_title(title)
        if td:
            t = td
    if not t:
        # 最终兜底：URL 路径完整日期（旧站点/极简页）。不可信（常为发布日/通知日）。
        url_date = _date_from_url(url)
        if url_date:
            y, mo, d = url_date
            try:
                t = {'start': datetime.datetime(y, mo, d, 0, 0), 'end': None}
                t_untrusted = True
            except ValueError:
                t = None
    # R3 本质条款：第 2/3 级兜底的发布时间若等于权威讲座日，视为误抓讲座时间，作废该候选。
    # 例外（用户 2026-07-28 授权·事后回顾守卫）：若该候选含具体时刻且【晚于】讲座开始时刻，
    # 则它绝不可能等于讲座时间本身（讲座不会晚于自己开始），必为真实「发布/回顾」时间戳——
    # 保留之，交由末尾 is_news_record 显式守卫判为事后回顾稿。swc 等「发布时间标签缺失、靠
    # 位置兜底、但发布日=讲座日」的回顾页即属此类：位置兜底抓到的 2021-07-14 17:00:00 是真实
    # 发布时刻（页眉元信息），晚于讲座开始（09:30），应保留并判事后。
    # 仅当候选时刻不晚于讲座开始时（等于或早于，属正常「同天发布」或与讲座时间混淆）才作废，
    # 避免误删正常预告（正常预告的发布时刻必早于讲座开始，不触发此例外）。
    if (publish_time and publish_level in (2, 3) and t
            and t['start'].strftime('%Y-%m-%d') == publish_time[:10]):
        _pub_dt = None
        try:
            _pub_dt = datetime.datetime.strptime(publish_time[:19], '%Y-%m-%d %H:%M:%S')
        except (ValueError, TypeError):
            try:
                _pub_dt = datetime.datetime.strptime(publish_time[:16], '%Y-%m-%d %H:%M')
            except (ValueError, TypeError):
                pass
        if not (_pub_dt and _pub_dt > t['start']):
            publish_time = None
    # R3 发布时间来源标记（publishTimeSource）：标签/伴生/class/位置兜底 + URL 日期代理
    # 同步回写 result['publishTime']：若上面 R3 本质条款已将 publish_time 作废（置 None），
    # 此处必须同步清空，避免残留已被作废的 companion 时间（如同日发布的 15:09）。
    result['publishTime'] = publish_time
    if publish_time:
        result['publishTimeSource'] = {1: 'label', 2: 'companion', 3: 'position'}.get(publish_level, 'label')
    elif _date_from_url(url):
        result['publishTimeSource'] = 'url_proxy'
    else:
        result['publishTimeSource'] = None
    if t:
        result['lectureStart'] = t['start'].isoformat(sep=' ')
        result['lectureEnd'] = t['end'].isoformat(sep=' ') if t.get('end') else None

    # 把地点字段里分离出的时间区间回填到讲座时间：
    #  - 若已有日期但时间完全缺失（00:00），用分离出的区间补全 start/end；
    #  - 若 start 已有时间但 end 缺失，用分离出的结束时间补全 end；
    # 这样海报中「地点: 南教-209教室14:30-17:00」式 OCR 能补全完整的起止时间。
    if loc_times and result['lectureStart']:
        h0, m0, h1, m1 = loc_times[0]
        try:
            st = datetime.datetime.fromisoformat(result['lectureStart'])
            if st.hour == 0 and st.minute == 0:
                st = st.replace(hour=h0, minute=m0)
                result['lectureStart'] = st.isoformat(sep=' ')
                if not result['lectureEnd']:
                    result['lectureEnd'] = st.replace(hour=h1, minute=m1).isoformat(sep=' ')
            elif not result['lectureEnd'] and (h1, m1) != (st.hour, st.minute):
                result['lectureEnd'] = st.replace(hour=h1, minute=m1).isoformat(sep=' ')
        except (ValueError, TypeError):
            pass

    # 字段标签前瞻——每个字段只取到下一个标签为止
    # 美术学院常见标签：讲座题目、主讲嘉宾、学术主持、主办单位、上一篇/下一篇
    # N1e/英文标签：补充 Time/Venue/Speaker/Topic/Abstract/Bio 等英文同义词，使海报双语标签可匹配。
    LABELS = (
        '教学工作坊时间|教学工作坊地点|'
        '报告时间|报告地点|报告内容|报告题目|报告专家|报告嘉宾|'
        '讲座题目|讲座时间|讲座地点|主办单位|学术主持|上一篇|下一篇|标签|Tags|'
        '地点|题目|主题|讲座主题|演讲题目|报告主题|'
        '时间|主讲[人师]|讲座人|主持人|主讲|报告人|主讲嘉宾|讲座嘉宾|演讲人|邀请人|'
        'Speaker|Presenter|Lecturer|'
        '摘要|讲座内容提要|内容提要|讲座内容摘要|内容摘要|内容简介|'
        '讲座内容|讲座简介|报告内容|讲座概要|内容概要|'
        '简历|主讲人简介|主讲人简历|简介|专家介绍|专家简介|面向对象|发布|来源'
        '|Topic|Title|Venue|Location|Abstract|Bio|Synopsis'
    )
    # STOP 终止符：字段标签、伴随噪声词（点击/浏览/评论/供稿，常出现在发布时间行尾）、
    # 以及方括号（【/ [ 多为栏目/来源标记）；'$' 兼容文末。
    STOP = rf'(?=\s*(?:{LABELS}|点击|浏览|评论|供稿|\d{{4}}[-/年]\d|【|\[|$))'
    # LOC-STOP：PDF/海报内文本常含换行，地点值独占一行（如「课程地点：…\n面向对象：…」）。
    # 通用 STOP 的 `$` 在「值行末到文末之间存在换行」时无法命中，且 `.` 不跨换行，
    # 导致 (.+?) 永远到不了下一行的终止标签。故 location 专用终止符在 STOP 基础上
    # 追加行尾（\n|$），使「值行尾」成为自然截断点（单行场景下 STOP 仍优先生效）。
    LOC_STOP = rf'(?={STOP}|\n|$)'

    # --- 题目/主题（兼容「题目/主题/讲座主题/报告题目/演讲题目/报告主题」+ 英文 Topic/Title）---
    topic_pat = rf'(?:讲座题目|题目|主题|讲座主题|报告题目|演讲题目|报告主题|Topic|Title)[：:]\s*(.+?){STOP}'
    m = re.search(topic_pat, text)
    if m:
        tp = m.group(1).strip()
        # 清除尾部粘连的「摘要」「主讲人」「预告」「特邀专家」等非正文词（换行后字段值泄漏）
        tp = re.sub(r'\s*(?:摘要|主讲人?|报告人|预告|讲座特邀专家|特邀专家|特邀嘉宾|讲座嘉宾)\s*[:：]?.*$', '', tp).strip()
        result['topic'] = tp

    # 标题格式兜底：「2026年7月2日学术讲座：主题」或「学术讲座：主题」
    if not result['topic'] and title:
        m = re.search(r'(?:学术讲座|讲座|报告会|学术报告)[：:]\s*(.+)$', title)
        if m:
            topic_candidate = m.group(1).strip()
            # 去掉末尾常见通用词，保留具体主题
            topic_candidate = re.sub(r'(?:教授|老师|先生|女士)\s*(学术讲座|讲座|报告|讲坛)$', '', topic_candidate).strip()
            if len(topic_candidate) > 3:
                result['topic'] = topic_candidate

    # --- 地点（兼容「地点/课程地点/讲座地点/工作坊地点」+ 英文 Venue/Location）---
    # 值捕获用 [^\n]+?（不跨换行）+ LOC_STOP：单行场景靠字段标签截断，PDF/多行场景靠行尾截断。
    m = re.search(rf'(?:课程地点|讲座地点|教学工作坊地点|地点|Venue|Location)[：:]\s*([^\n]+?){LOC_STOP}', text)
    # LOC-Fallback: 若主正则未命中或值为空，用宽松终止符重试（覆盖 PDF 内嵌等边界）。
    if not m or not m.group(1).strip():
        m2 = re.search(
            rf'(?:课程地点|讲座地点|教学工作坊地点|地点|Venue|Location)[：:]\s*'
            r'([^\n]+?)(?:\n\n|\n[一二三四五六七八九十]|面向对象|主讲人简介|报名|联系方式)',
            text)
        if m2 and m2.group(1).strip():
            m = m2
    if m:
        loc = m.group(1).strip()
        # 美术学院等页面：地点后常粘连「主办单位/上一篇/下一篇/Tags/版权」等噪声，优先截断
        loc = re.split(r'(?:主办单位|协办单位|承办单位|邀请人|讲座人|主持人|上一篇|下一篇|标签|Tags|Copyright|版权所有|All Rights Reserved|SCNU)', loc)[0].strip()
        # 汕尾校区教学工作坊海报：地点标签常为「教学工作坊地点:」，且「教学工作坊时间:」中的
        # 「时间」二字会 premature 触发 STOP，把「教学工作坊」后缀带进地点；这里显式剔除。
        loc = re.sub(r'教学工作坊.*$', '', loc).strip()
        # 地点值通常很短；如果超过 60 字符说明仍吃到了后续内容，截断到第一个句号/逗号
        if len(loc) > 60:
            loc = re.split(r'[。，;；\n]', loc)[0].strip()
        # 去除 OCR 尾部常见乱码或装饰字符（如「曷」「号」）
        loc = re.sub(r'[\s]*[曷号]+$\s*', '', loc).strip()
        loc = re.sub(r'\s+[^\u4e00-\u9fa5a-zA-Z0-9]{1,2}\s*$', '', loc).strip()
        # 折叠 CMS 把地点拆成单字/单数字造成的空格（如「理 6 栋 302」→「理6栋302」），
        # 仅合并「中文-中文/中文-数字/数字-中文」间的空格，保留英文单词与纯数字间的空格。
        loc = re.sub(r'(?<=[\u4e00-\u9fa5])\s+(?=[\u4e00-\u9fa5\d])|(?<=\d)\s+(?=[\u4e00-\u9fa5])', '', loc).strip()
        # 海报 OCR 场景中，地点字段常被混入时间区间（如「南教-209教室14: 30-17: 00」）
        # 或「南教-209教室14: 30-17; 主办甲位: …」式 OCR 噪声。把时间分离出来补到讲座时间，
        # 地点只保留纯地点。
        loc, loc_time = _split_location_time(loc)
        if loc_time:
            loc_times.append(loc_time)
        result['location'] = loc

    # LOC-OCR: 主正则未命中/值为空且 OCR 文本可用时，在原始 ocr_text 中
    # 以「地点/地点标签后 2-40 字」宽松搜索（多行场景下不需要 LOC_STOP 的 $ 锚点）。
    if not result.get('location') and ocr_text:
        loc_ocr = re.search(
            r'(?:课程地点|讲座地点|教学工作坊地点|地点'
            r'|Venue|Location)[：:]\s*([^：:\n]{2,40})', ocr_text)
        if loc_ocr and len(loc_ocr.group(1).strip()) >= 2:
            result['location'] = loc_ocr.group(1).strip()

    # --- 主讲人（兼容「主讲人/主讲师/报告人/主讲嘉宾/演讲人/主讲」）---
    # 注意：排除「主讲《…》」（正文里「主讲《课程名》」是动宾短语，不是主讲人标签），
    # 否则会把书名号后的课程名误当主讲人（如汕尾校区海报 bio 中的「主讲《动物组织学与胚胎学》」）。
    # 汕尾校区教学工作坊海报用「主讲专家:」「专家姓名:」标注主讲人，一并纳入。
    speaker_label_found = False
    sp_title = None
    # 若正文存在「主讲人简介/报告人简介」标签，视为已找到主讲人标识；
    # 这样即使标准带冒号正则未提取到姓名，也不会被 narrative fallback 用前句垃圾覆盖，
    # 后续 F4 可从 speakerBio 中安全提取姓名（如 CTLD 4411）。
    if re.search(r'(?:主讲人简介|报告人简介|主讲人介绍|报告人介绍|主讲介绍|专家介绍)\s*[：:]', text):
        speaker_label_found = True
    # 注意：排除「主讲人/报告人」后的「简介/简历/介绍」（主讲人简介=个人简介，不是主讲人标签），
    # 否则会把简介正文误当主讲人值。也排除「主讲《…》」（动宾短语，课程名非人名）。
    # F3 step1 — 邀请人分离（如「邀请人：范智杰」），提取为 inviter 并从待扫描文本移除，避免混入主讲人
    # 注意 text 已被压成单行（无换行），故不能用 (?:\n|$) 作终止符，否则会吞掉整段正文。
    # 改为遇到下一个字段标签即停，并限长 30 字防溢出（邀请人通常为短人名/单位）。
    inv_m = re.search(r'(?:邀请人|Inviter)\s*[：:]\s*(.{1,30}?)(?=\s*(?:报告人|主讲人|主讲师|主讲|时间|地点|题目|摘要|讲座简介|简介|审核|编辑|发布|来源|[\n]|$))', text)
    if inv_m:
        result['inviter'] = inv_m.group(1).strip()
        text = text.replace(inv_m.group(0), ' ', 1)

    # 注意：长标签必须排在短标签前面（如「主讲嘉宾」>「主讲」），
    # 否则「主讲」先匹配导致值含后续标签文本（如"嘉宾：洪源远…"），最终被 F3 守卫清空。
    speaker_pat = (
        rf'(?:主讲嘉宾|讲座嘉宾|报告嘉宾|主讲人(?!简介|简历|介绍)|主讲师(?!简介|简历)'
        rf'|主讲(?!《|简介|简历)(?:专家)?'
        rf'|报告人(?!简介|简历)|演讲人|报告专家|专家姓名'
        rf'|Speaker|Presenter|Lecturer)\s*[：:]\s*(.+?){STOP}'
    )
    m = re.search(speaker_pat, text)
    if m:
        speaker_label_found = True
        sp = m.group(1).strip()
        # 截断到内容类标签：LABELS 未含「主要内容/摘要/简介」等，若主讲人值后紧跟
        # 「主要内容：…」式正文，(.+?){STOP} 会一直吞到下一个字段标签（如地点），把整段
        # 正文吸进 sp（如教师发展中心「主讲人：张敏 主要内容：1.PPT…」→ sp 变成「张敏主…」
        # 被姓名守卫拒绝）。这些词绝不会出现在姓名里，在此先行截断（仅限主讲人分支，不碰共享 STOP）。
        sp = re.split(
            r'(?:主要内容|讲座内容|报告内容|摘要|内容简介|讲座简介|报告简介|'
            r'主讲人简介|报告人简介|简介|专家介绍|专家简介|面向对象)',
            sp, 1)[0].strip()
        # 折叠「主讲人：张三 张三，…」式姓名重复：华师部分通知在标签后把姓名又写一遍作为
        # 简介开头（如教师发展中心「主讲人：姜小芳 姜小芳，华南师范大学…」；正文经单行化后，
        # 标签段「主讲人：姜小芳」与简介段「姜小芳，…」被粘成「姜小芳 姜小芳」）。不折叠会导致
        # speaker 变成「张三张」、affiliation 被污染。
        # 处理：保留第一个姓名（真实主讲人），丢弃重复的那次，其后的「，单位…」作为 affiliation/bio 来源。
        _m_dup = re.match(r'^([\u4e00-\u9fa5·]{2,4})\s*\1', sp)
        if _m_dup:
            sp = (sp[:_m_dup.end(1)] + sp[_m_dup.end():]).strip()
        # 多主讲人：空格分隔（如「主讲人：魏文娅 傅承哲」）或 CJK 空格被折叠后粘连
        # （如「主讲人：魏文娅傅承哲」）。每位均为 2-4 字中文姓名且无职称后缀时，
        # 合并为「、」分隔，避免被 CJK 折叠/截断后只保留半个名字。
        _sp_orig = sp.strip()
        _multi_name_matched = False
        _names = None
        # 1) 空格分隔
        if re.match(r'^([\u4e00-\u9fa5·]{2,4})(?:\s+[\u4e00-\u9fa5·]{2,4})+$', _sp_orig) and \
           not re.search(r'(特聘教授|特任教授|副教授|助理教授|副研究员|助理研究员|研究员|教授|讲师|博士后|博士|院士|老师|导师|先生|女士)', _sp_orig):
            _names = _sp_orig.split()
        # 2) 粘连无空格：尝试拆成 2~4 字/段的纯姓名（如魏文娅|傅承哲）。
        # 额外要求首字为常见姓氏，避免「魏文/娅傅承哲」这类错误切分被 _looks_like_real_name 误放。
        if _names is None and re.match(r'^[\u4e00-\u9fa5·]{4,8}$', _sp_orig) and \
           not re.search(r'(特聘教授|特任教授|副教授|助理教授|副研究员|助理研究员|研究员|教授|讲师|博士后|博士|院士|老师|导师|先生|女士)', _sp_orig):
            for cut in range(2, 5):
                a, b = _sp_orig[:cut], _sp_orig[cut:]
                if (2 <= len(b) <= 4 and _looks_like_real_name(a) and _looks_like_real_name(b)
                        and _SURNAME_RE.match(a[:1]) and _SURNAME_RE.match(b[:1])):
                    _names = [a, b]
                    break
        if _names and all(_looks_like_real_name(n) for n in _names):
            result['speaker'] = '、'.join(_names)
            result['speakerSource'] = 'label'
            _multi_name_matched = True
        if not _multi_name_matched:
            _en_name, _en_aff = _split_english_speaker(sp)
            if _en_name:
                result['speaker'] = _en_name
                result['speakerSource'] = 'label'
                if _en_aff:
                    result['speakerAffiliation'] = _en_aff
            else:
                # CJK：折叠空格、去尾部职称，取头部 2~4 字人名
                if re.search(r'[\u4e00-\u9fa5]', sp):
                    sp = re.sub(r'\s+', '', sp)
                sp_clean = re.sub(r'\s*(?:高级实验师|高级讲师|高级教师|高级工程师|高级会计师|高级经济师|实验师|工程师|会计师|经济师|特聘教授|特任教授|副教授|助理教授|副研究员|助理研究员|研究员|教授|讲师|博士后|博士|院士|老师|导师|先生|女士).*$', '', sp).strip()
                nm = re.match(r'^([\u4e00-\u9fa5]{2,4})', sp_clean)
                if nm and _looks_like_real_name(nm.group(1)):
                    result['speaker'] = nm.group(1)
                    rest = sp[nm.end():].strip()
                    if rest and len(rest) > 2:
                        result['speakerAffiliation'] = _extract_affiliation(rest)
                elif sp_clean:
                    result['speaker'] = sp_clean
    # 连写多主讲人标记：报告人字段以「[头衔]姓名职称」拼接多位主讲人时置为 segments 列表
    multi_speakers = None
    # F2-OCR-SP: OCR 海报常把标签与值之间的冒号和空格全部识丢，
    # 变成零分隔符粘连（如工学部海报「主办单位:华南师范大学工学部主讲人马於光院士」）。
    # 若上述带冒号正则未命中，尝试零宽/纯空格的「标签+姓名」格式；
    # 值截取到下一个字段标签或非名字字符为止，限长防溢出。
    if not m:
        _ocr_sp_pat = (
            rf'(?:主讲嘉宾|讲座嘉宾|报告嘉宾|主讲人(?!简介|简历|介绍)|主讲师(?!简介|简历)'
            rf'|主讲(?!《|简介|简历)(?:专家)?'
            rf'|报告人(?!简介|简历)|演讲人|报告专家|专家姓名)'
            rf'\s*(?:[：:]|\s*)\s*'
            rf'([\u4e00-\u9fa5·]{{2,4}}(?:院士|教授|研究员|讲师|博士|特聘教授|特任教授|副教授|助理教授)?){STOP}'
        )
        mt_title = None
        sp = None
        m = re.search(_ocr_sp_pat, text)
        if m:
            speaker_label_found = True
        if m:
            speaker_label_found = True
            sp = m.group(1).strip()
            # F3 step2 — 职称后缀分离为 speakerTitle（如「助理研究员」「教授」）
            mt_title = re.search(r'(助理研究员|副研究员|助理研究员|研究员|特聘教授|特任教授|长聘教授|副教授|助理教授|教授|讲师|博士后|博士|院士|老师|导师|先生|女士)+$', sp)
        if mt_title:
            sp_title = mt_title.group(1).strip()
        # 英文/拉丁姓名快路径（2026-07-24 修复：cs 5294「Yan Zhang, University of Oslo」
        # 原中文抽取路径只匹配 CJK、英文全落空，被守卫清空）。命中则直接落库并跳过 CJK 路径。
        if m and sp:
            _en_name, _en_aff = _split_english_speaker(sp)
            if _en_name:
                result['speaker'] = _en_name
                result['speakerSource'] = 'label'
                if sp_title:
                    result['speakerTitle'] = sp_title
                if _en_aff:
                    result['speakerAffiliation'] = _en_aff
            else:
                # 连写多主讲人 / 荣誉头衔前缀（2026-07-24 修复：cs 4145 论坛
                # 「报告人：国家杰青刘梦赤教授长江学者陈建二教授长江学者卢晓中教授」——
                # 真实姓名在头衔之后、职称之前，原抽取把头衔当名字、职称当 title）。
                # 仅当值含已知荣誉头衔前缀、或能拆出 ≥2 个「姓名+职称」段时才走此分支，
                # 避免误伤常规「姓名 职称 单位」单主讲人（其 affiliation 须由后续 mm2 逻辑提取）。
                _raw_segs = _SPEAKER_SEG_RE.findall(sp)
                # 仅检查括号「之前」的姓名部分：lswh 等面包屑把单位放在半角/全角括号内，
                # 单位里常含「特聘教授/长江学者」等荣誉词，若对整串 sp 判定 _has_honor 会误触发
                # 连贯多主讲人解析（_parse_concat_speakers），把单位当「头衔+姓名」连写串，
                # 产出『特聘』『雅特聘』等职称碎片（如 晏绍祥(首都师范大学历史学院教授、教育部长江学者特聘教授...)）。
                # 真实连贯格式（国家杰青刘梦赤教授长江学者陈建二教授…）无括号，整串即姓名部分，不受影响。
                _has_honor = re.search(_HONORIFICS, re.split(r'[（(]', sp, 1)[0])
                _segs = None
                # 不变量：含括号的「姓名(单位)」格式绝不可能是连贯多主讲人连写串
                # （连贯格式如「国家杰青刘梦赤教授长江学者陈建二教授」本身无括号）。
                # lswh 面包屑「晏绍祥(首都师范大学历史学院教授、…特聘教授…会长)」含半角括号，
                # 若进入 _parse_concat_speakers 会把括号内单位误解析成姓名碎片『特聘』。
                if (_has_honor or len(_raw_segs) >= 2) and '(' not in sp and '（' not in sp:
                    _segs = _parse_concat_speakers(sp)
                if _segs:
                    if len(_segs) >= 2:
                        # 多主讲人论坛：先置首位为主讲人，整页字段构建完后按主讲人拆分多条
                        result['speaker'] = _segs[0]['name']
                        result['speakerSource'] = 'label'
                        result['speakerTitle'] = _segs[0]['honorific'] or (sp_title or '')
                        sp_title = None  # 防 1716 行职称后缀覆盖（荣誉头衔优先）
                        multi_speakers = _segs
                    else:
                        result['speaker'] = _segs[0]['name']
                        result['speakerSource'] = 'label'
                        result['speakerTitle'] = _segs[0]['honorific'] or (sp_title or '')
                        sp_title = None
                else:
                    # 如果值太长，截断到第一个非 speaker/affiliation 的分隔符处
                    if len(sp) > 25:
                        # 优先按中文标点截断（常规结构化页面）
                        cut = re.search(r'[，、；。]', sp[4:])
                        # 其次按其他字段标签前截断（含无空格直接粘连的情况，
                        # 如 ggy 页面"副教授主持嘉宾:"中 主持嘉宾 紧跟前文无空格）
                        if not cut:
                            cut = re.search(r'(?:\s*)?(?:主持嘉宾|评论嘉宾|讲座时间|Zoom|Passcode|参会|主办单位|承办单位|主讲人简介)', sp[4:])
                        # 兜底：按空格+大写字母或空格+常见字段词截断
                        if not cut:
                            cut = re.search(r'\s+[A-Z]|\s+\d{4}', sp[4:])
                        if cut:
                            sp = sp[:4 + cut.start()].strip()
                    # 先压缩内部空白：网页常把「副教授」排版成「副 教授」（换行/空格断开），
                    # 导致后续职称剥离正则（连续字符串匹配）无法命中。
                    # 仅对 CJK 值执行（英文姓名可能含合法空格；此处已确认走中文路径）。
                    if re.search(r'[\u4e00-\u9fa5]', sp):
                        sp = re.sub(r'\s+', '', sp)
                    # 去掉尾部职称后缀
                    sp_clean = re.sub(r'\s*(?:高级实验师|高级讲师|高级教师|高级工程师|高级会计师|高级经济师|实验师|工程师|会计师|经济师|特聘教授|特任教授|副教授|助理教授|副研究员|助理研究员|研究员|教授|讲师|博士后|博士|院士|老师|导师|先生|女士).*$', '', sp).strip()
                    # 尝试拆分姓名+单位（括号形式）
                    mm = re.match(r'(.+?)\s*[（(]([^）)]{2,40})[）)]', sp)
                    if mm:
                        result['speaker'] = sp_clean.split('（')[0].split('(')[0].strip()
                        aff = re.sub(r'\s*(?:特聘教授|特任教授|副教授|助理教授|副研究员|助理研究员|研究员|教授|讲师|博士后|博士|院士|老师|导师|先生|女士).*$', '', mm.group(2)).strip()
                        # 清除「现为/现任/现供职于/目前任职于」等状态前缀
                        aff = re.sub(r'^\s*(?:现为|现任|现供职于|目前任职于|就职于)\s*', '', aff).strip()
                        result['speakerAffiliation'] = re.sub(r'\s+', '', aff)
                    else:
                        # 空格分隔的「姓名 职称 单位」或「姓名 单位」（如物理学院「郑炜 教授 中国科学技术大学」）
                        _TITLES = r'(?:特聘教授|特任教授|副教授|助理教授|副研究员|助理研究员|研究员|教授|讲师|博士后|博士|院士|老师|导师)'
                        # 先处理「姓名 职称，单位」逗号分隔（生命科学学院常见：报告人：肖媛 博士，清华大学）
                        sp_normalized = re.sub(r'[，,]', ' ', sp)
                        mm2 = re.match(rf'^([\u4e00-\u9fa5·]{{2,5}})\s+[\u4e00-\u9fa5]{{0,4}}{_TITLES}\s+([\u4e00-\u9fa5A-Za-z].{{2,40}})$', sp_normalized)
                        if not mm2:
                            # group1 限定 2~4 字（中文姓名上限），避免贪婪把「姓名+职称」里的职称吃进名字
                            # （如「何洁副教授 南洋理工大学」原本被 {2,5} 吞成「何洁副教授」）。
                            mm2 = re.match(r'^([\u4e00-\u9fa5·]{2,4})\s+([\u4e00-\u9fa5]{4,40})$', sp_normalized)
                        if mm2:
                            result['speaker'] = mm2.group(1).strip()
                            aff = re.sub(r'\s*(?:特聘教授|特任教授|副教授|助理教授|副研究员|助理研究员|研究员|教授|讲师|博士后|博士|院士|老师|导师|先生|女士).*$', '', mm2.group(2)).strip()
                            # 清除「现为/现任/现供职于/目前任职于」等状态前缀
                            aff = re.sub(r'^\s*(?:现为|现任|现供职于|目前任职于|就职于)\s*', '', aff).strip()
                            result['speakerAffiliation'] = re.sub(r'\s+', '', aff).strip()
                        else:
                            # 最后兜底：从值头部提取纯中文人名（2~4 字），
                            # 覆盖"姓名单位/职称"粘连无法用上述模式拆分的情况（如 ggy 的"洪源远密歇根大学..."）
                            # 名字在遇到单位关键词（大学/学院/研究员/教授等）时应停止
                            nm = re.match(r'^([\u4e00-\u9fa5]{2,3})(?=[^a-zA-Z0-9]*?(?:大学|学院|研究院|研究所|教授|副教授|讲师|博士|院士|中心|实验室))', sp_clean)
                            if not nm:
                                nm = re.match(r'^([\u4e00-\u9fa5]{2,4})', sp_clean)
                            if nm and _looks_like_real_name(nm.group(1)):
                                result['speaker'] = nm.group(1)
                                # 剩余部分（用截断后但未去职称的 sp，避免丢失单位首字）
                                rest = sp[nm.end():].strip()
                                if rest and len(rest) > 2:
                                    result['speakerAffiliation'] = _extract_affiliation(rest)
                            else:
                                result['speaker'] = sp_clean

    # 兜底：从标题括号中提取主讲人，如「（朱英教授）」
    if not result['speaker']:
        tm = re.search(r'（([^（）]*?(教授|研究员|副教授|讲师|博士)[^（）]*?)）', title)
        if tm:
            result['speaker'] = re.sub(r'\s*(教授|研究员|副教授|讲师|博士|老师).*$', '', tm.group(1)).strip()

    # 兜底：小标题式主讲人（「N、主讲人」/「（N）主讲人」无冒号，姓名在后续文本）。
    # CTLD 等院系页面常用「一、主讲人」「二、主讲人」作小节标题，姓名紧跟其后（无冒号、常无空格），
    # 标准带冒号正则匹配不到；本回退仅在主讲人仍为空时触发（不影响已正确提取的源）。
    # 仅当值为 2~4 字中文姓名且通过 _looks_like_real_name 守卫才落库，避免把「二、主讲人」后的
    # 简介首句误当人名。单位由 _extract_affiliation 从姓名后残余文本提取（覆盖「党委书记/教授」等职位词）。
    if not result['speaker']:
        _HDR_SP = re.compile(
            r'(?:[一二三四五六七八九十百零0-9]+|[（(][一二三四五六七八九十0-9]+[)）])\s*[、.．。]?\s*'
            r'主讲人(?!简介|简历|介绍)\s*'
            r'([\u4e00-\u9fa5·]{2,4}'
            r'(?:院士|教授|研究员|讲师|博士|特聘教授|特任教授|副教授|助理教授|助理研究员|副研究员|老师)?'
            r'[\u4e00-\u9fa5A-Za-z0-9·，,、。.\s]{0,80})'
        )
        _hdr_m = _HDR_SP.search(text)
        if _hdr_m:
            _sp = re.sub(r'\s+', '', _hdr_m.group(1))
            _nm = re.match(r'^([\u4e00-\u9fa5]{2,4})', _sp)
            if _nm and _looks_like_real_name(_nm.group(1)):
                result['speaker'] = _nm.group(1)
                result['speakerSource'] = 'header'
                _rest = _sp[_nm.end():]
                if _rest:
                    _aff = _extract_affiliation(_rest)
                    if _aff:
                        result['speakerAffiliation'] = _aff

    # 兜底：冒号/无冒号后紧跟姓名的宽松提取（即使后续是散文、无字段标签也能取姓名）。
    # 覆盖「主讲人：甘德安教授将结合…」（主正则 speaker_pat 因 STOP 要求字段标签而漏抓）
    # 与「本次讲座的主讲人X教授…」式内联。仅取姓名首 2~4 字、交给下方 F3 守卫剥离尾部职称碎片
    # （教/授/师/研/员/博/士/导 等），并过 _looks_like_real_name 守卫；若首词非人名则拒绝，留空。
    # 单位由 _extract_affiliation 从姓名后残文提取（覆盖「姓名后紧跟单位」）。仅在不含 speaker 时触发。
    if not result['speaker']:
        _loose_m = re.search(
            r'主讲人(?!简介|简历|介绍)\s*[：:]?\s*([\u4e00-\u9fa5·]{2,4})', text)
        if _loose_m:
            _cand = _loose_m.group(1)
            # 先尝试提取单位（姓名后残文，含职称/散文，_extract_affiliation 只取单位关键词短语）
            _after = text[_loose_m.end():_loose_m.end() + 150]
            _aff2 = _extract_affiliation(_after)
            result['speaker'] = _cand          # 交由 F3 守卫清洗尾部职称碎片
            result['speakerSource'] = 'inline'
            if _aff2:
                result['speakerAffiliation'] = _aff2

    # F3 第 5 步：主讲人清洗守卫。清洗后若不是有效人名（如「作为首席」「首席专家」），
    # 则清空，避免把标签/乱码/误识当成人名；同时清空误带的单位。
    # 截断清理（2026-07-20 补充）：OCR 海报常见把「X教授/X专家/X硕士」截断成「X教/X专/X硕」，
    # 如「王子鹏特聘」「焦建利专」「杜炫杰专」「贺萌萌硕」。若 speaker 尾部含不完整职称片段则剥离。
    _TRUNC_SUFFIX = r'(?:特[聘任]|专$|硕$|师$|范$|教$|授$|研$|员$|博$|士$|导$|主任|院长)$'
    if result.get('speaker'):
        m2 = re.match(r'(^[\u4e00-\u9fa5·]{2,4})' + _TRUNC_SUFFIX, result['speaker'])
        if m2 and _looks_like_real_name(m2.group(1)):
            result['speaker'] = m2.group(1)
    if result.get('speaker') and not _looks_like_real_name(result['speaker']):
        # 多主讲人用「、」连接：逐段校验，全为有效人名时保留
        if '、' in result['speaker']:
            _segs = [s.strip() for s in result['speaker'].split('、') if s.strip()]
            if not (_segs and all(_looks_like_real_name(s) for s in _segs)):
                result['speaker'] = ''
                result['speakerAffiliation'] = ''
        else:
            result['speaker'] = ''
            result['speakerAffiliation'] = ''
    # F3 step2 — 姓名保留时，把分离出的职称后缀写入 speakerTitle
    if result.get('speaker') and sp_title:
        result['speakerTitle'] = sp_title

    # --- OCR 决策（T2 关键字段缺失 + T3 讲座页内容不完整）---
    # 通用、不按院：仅依据「页面内容特征 + 标题关键词 + 是否含图」判断，避免为特定学院写白名单例外。
    LECTURE_KW = ('讲座', '报告', '工作坊', '沙龙', '论坛', '研讨会', '讲坛', '座谈会')
    title_is_lecture = bool(title) and any(kw in title for kw in LECTURE_KW)
    # T2：时间/地点/主讲/题目 任一关键字段缺失且含图 → OCR 补充（仅补缺失、不覆盖已有）
    missing_key = (not result.get('lectureStart') or not result.get('location')
                   or not result.get('speaker') or not result.get('topic'))
    # T3：讲座类标题 + 含图 + (时间不可信/缺失 或 地点缺失) → OCR，海报日期更具体则覆盖 lectureStart
    need_ocr = bool(imgs) and (missing_key or (title_is_lecture and (t_untrusted or not result.get('location'))))
    if need_ocr and not ocr_text and not vlm_fields:
        _do_ocr()
        if ocr_text:
            result['ocrExtracted'] = True
            result['imageParseMethod'] = 'ocr'
            result['hasPosterImage'] = True
    # T3 覆盖：讲座类标题 + 含图 + OCR 抽到日期 → 以海报日期为准覆盖 lectureStart/End。
    # 海报是讲座时间的权威源，故不再强依赖 t_untrusted（发布日未被识别时会漏判）；
    # 覆盖条件收敛为「OCR 日期与现有不同 / OCR 补出了时间 / OCR 补出了结束时间」，
    # 避免把正文已正确的时间误覆盖。必须解析 ocr_text 本身，而非整页 text——
    # 整页 text 里排在前的发布日/通知日会先被命中，导致「日期相同」误判、海报日期无法覆盖。
    if ocr_text and title_is_lecture:
        t_ocr = parse_cn_time(ocr_text, default_year, publish_time=publish_time, title_year=title_year, url_year=url_year)
        # 优先取 OCR 中「时间」标签后的片段（更精准，避免海报其他处日期干扰）
        tm = re.search(r'(?:讲座)?时间[：:\s]*(.{0,40})', ocr_text)
        if tm:
            t_label = parse_cn_time(tm.group(1).strip(), default_year, publish_time=publish_time, title_year=title_year, url_year=url_year)
            if t_label:
                t_ocr = t_label
        if t_ocr and (t is None
                     or t_ocr['start'].date() != t['start'].date()
                     or (not t.get('has_time') and t_ocr.get('has_time'))
                     or (t.get('end') is None and t_ocr.get('end'))):
            t = t_ocr
            result['lectureStart'] = t_ocr['start'].isoformat(sep=' ')
            result['lectureEnd'] = t_ocr['end'].isoformat(sep=' ') if t_ocr.get('end') else None

    # OCR 海报无「主讲人:」标签时，按「姓名 + 职称」行兜底抽取主讲人（如「曾碧卿 /教授」），
    # 并顺带取姓名行后的单位作为 affiliation。仅当尚未识别到主讲人才启用，避免覆盖标签式结果。
    if not result.get('speaker') and ocr_text:
        _sp, _aff, _src = _extract_speaker_from_ocr(ocr_text)
        if _sp:
            result['speaker'] = _sp
            if _aff and not result.get('speakerAffiliation'):
                result['speakerAffiliation'] = _aff
            result['speakerSource'] = _src or 'ocr'
            if _src == 'pattern4':
                result['notes'] = (result.get('notes') or '') + '；主讲人来自 Pattern4 夹逼定位，置信度低，建议人工核验'
            # 海报模板「活动主题：姓名+主题」会导致 topic 前缀含讲者名，去掉前导名
            _tp = (result.get('topic') or '').strip()
            if _tp.startswith(_sp) and len(_tp) > len(_sp):
                _new_tp = _tp[len(_sp):].strip(' ：:，,')
                if _new_tp:
                    result['topic'] = _new_tp

    # --- 马克思主义学院海报专用抽取（两类格式：① 顶部「唯实讲堂」用「地点：」标签；
    #     ② 底部「学术研讨会」把地点放在末尾「华南师范大学XXX厅/楼N」、主讲人用「主讲嘉宾 姓名」）---
    # 通用抽取对马院海报失效：① location 终止标签依赖「主讲人简介」完整出现，OCR 常把「主」漏识成
    # 「讲人简介」导致 location 贪婪吞掉整段简介；② 简介位于「地点」之后被 _extract_speaker_from_ocr
    # 的 region 截断挡在门外；③ 外文姓名含「·」或后接拉丁字母，通用 2–4 字正则截断失真。
    # 此处用原始 OCR 直接按标签兜底解析，仅对马院生效。
    if college == '马克思主义学院':
        _mks_raw = ''
        if imgs:
            try:
                _mks_raw = ' '.join(_img_to_text(im) for im in imgs[:3])
            except Exception:
                _mks_raw = ''
        if _mks_raw:
            # 地点：优先「(活动)地点：」标签截到首个礼堂词；否则兜底抓底部「华南师范大学XXX厅/楼N」
            loc = ''
            loc_m = re.search(r'(?:活动地点|地点)[：:]\s*([\s\S]*?(?:厅|室|场|房|馆))', _mks_raw)
            if loc_m:
                loc = loc_m.group(1).strip()
                halls = list(re.finditer(r'(?:厅|室|场|房|馆)', loc))
                if halls:
                    loc = loc[:halls[-1].end()].strip()
            else:
                loc_m = re.search(r'华南师范大学\s*([\u4e00-\u9fa5\d]*(?:厅|室|楼|场|房|馆)[\u4e00-\u9fa5\d]*)', _mks_raw)
                if loc_m:
                    loc = '华南师范大学' + loc_m.group(1).strip()
            if loc:
                result['location'] = loc
            # 主讲人（按出现频率排序）：主讲嘉宾 / 主讲人简介(含漏识"讲人简介") / 地点后紧跟姓名 /
            # 顶部「姓名，单位」 / 底部「外文姓名 拉丁」；外文名(含·或后接拉丁)直接采用。
            if not result.get('speaker'):
                sm = (re.search(r'主讲嘉宾\s*([\u4e00-\u9fa5·]{2,4})', _mks_raw)
                      or re.search(r'主讲人简介\s*([\u4e00-\u9fa5·]{2,4})', _mks_raw)
                      or re.search(r'讲人简介\s*([\u4e00-\u9fa5·]{2,4})', _mks_raw)
                      or re.search(r'地点[：:][\s\S]*?(?:厅|室|场|房|馆)\s*([\u4e00-\u9fa5·]{2,8})', _mks_raw)
                      or re.search(r'([\u4e00-\u9fa5·]{2,4})[，,]\s*[\u4e00-\u9fa5]*(?:大学|学院|研究院|研究所)', _mks_raw)
                      or re.search(r'([\u4e00-\u9fa5·]{2,8})\s*[A-Za-z]+\s*时间[：:]', _mks_raw))
                if sm:
                    cand = sm.group(1).strip()
                    _nxt = _mks_raw[sm.end():sm.end() + 1] if sm.end() < len(_mks_raw) else ''
                    if '·' in cand or re.search(r'[A-Za-z]', _nxt):
                        result['speaker'] = cand          # 外籍姓名
                    elif _looks_like_real_name(cand):
                        result['speaker'] = cand

    # --- 简历/简介（优先在文章正文区域内搜索）---
    # body_text 已在函数开头构建（含可能的 OCR 文本）

    # 内容摘要类标签：出现这些说明主讲人简介已结束、讲座内容介绍开始
    # N1e/英文：补充 Abstract/Synopsis。
    SUMMARY_LABELS = (
        '讲座内容提要|内容提要|讲座内容摘要|内容摘要|内容简介|报告简介|讲座简介|'
        '讲座主题简介|讲座内容|讲座简介|报告内容|讲座概要|内容概要|摘要|主要内容'
        '|Abstract|Synopsis'
    )

    # 页面噪声/侧边栏标记：遇到这些说明正文已结束，应截断
    NOISE_MARKERS = (
        '资讯及通知|相关新闻|最新动态|推荐阅读|相关文章|相关讲座|'
        '上一篇|下一篇|附件下载|相关链接|网友评论|分享|标签|相关推荐|'
        '通知公告|最新公告|站内搜索|快速导航'
    )

    bio_pat = rf'(?:报告人简介|主讲人简介|主讲人简历|主讲介绍|主讲人介绍|简历|(?<!内容)简介|Bio)[\s:：]*'
    m = re.search(rf'{bio_pat}(.+?)(?=\s*(?:{SUMMARY_LABELS}|{NOISE_MARKERS}|$))', body_text)
    if m:
        bio = m.group(1).strip()
        # 清理版权声明等尾部噪声
        bio = re.sub(r'[\s\S]*(Copyright|版权所有|备案|ICP|All Rights Reserved|Reserved|粤ICP)[\s\S]*', '', bio).strip()
        # 清理图片路径等残留
        bio = re.sub(r'\s*(//[\w./-]+\.(jpg|jpeg|png|gif))\s*', '', bio).strip()
        # 截断页面噪声
        bio = re.split(rf'(?:{NOISE_MARKERS})', bio)[0].strip()
        if len(bio) > 10:
            result['speakerBio'] = bio

    # 无标签简介兜底：正文某段落以主讲人姓名开头，通常即个人简介
    if not result['speakerBio'] and result['speaker'] and content_div:
        speaker = result['speaker'].strip()
        for p in content_div.find_all('p'):
            p_text = re.sub(r'\s+', ' ', p.get_text(' ', strip=True)).strip()
            if len(p_text) < 30:
                continue
            # 去掉段落开头可能的职称/称谓，再判断是否以主讲人姓名开头
            start = re.sub(r'^\s*(Professor|Dr\.|Mr\.|Ms\.|教授|副教授|讲师|研究员|博士)\s*', '', p_text)
            if start.startswith(speaker):
                p_text = re.sub(r'\s*(//[\w./-]+\.(jpg|jpeg|png|gif))\s*', '', p_text).strip()
                p_text = re.sub(r'[\s\S]*(Copyright|版权所有|备案|ICP|All Rights Reserved|Reserved|粤ICP)[\s\S]*', '', p_text).strip()
                p_text = re.split(rf'(?:{NOISE_MARKERS})', p_text)[0].strip()
                if len(p_text) > 10:
                    result['speakerBio'] = p_text
                    break

    # --- 摘要/内容（优先从正文区域提取完整版）---
    # 把「讲座内容提要/讲座内容/报告摘要」等内容摘要类字段统一作为 abstract
    abs_pat = rf'(?:{SUMMARY_LABELS})[：:]\s*'
    m = re.search(rf'{abs_pat}(.+)', body_text)
    if m:
        abstract = m.group(1).strip()
        # 清理版权噪声和图片
        abstract = re.sub(r'[\s\S]*(Copyright|版权所有|备案|ICP|All Rights Reserved|Reserved|粤ICP)[\s\S]*', '', abstract).strip()
        abstract = re.sub(r'\s*(//[\w./-]+\.(jpg|jpeg|png|gif))\s*', '', abstract).strip()
        # 截断页面噪声/侧边栏
        abstract = re.split(rf'(?:{NOISE_MARKERS})', abstract)[0].strip()
        if len(abstract) > 5:
            # 去掉尾部时间/地点/报名等元信息（io 源正文常把"时间:... 地点:..."粘到摘要末尾）
            abstract = re.sub(r'\s*(?:时间|时闻)\s*[:：\s].*$', '', abstract).strip()
            abstract = re.sub(r'\s*地点\s*[:：\s].*$', '', abstract).strip()
            abstract = re.sub(r'\s*20\d{2}年\s*\d{1,2}月\s*\d{1,2}日.*$', '', abstract).strip()
            # 仅剔除「邀请类」尾部，不误伤正文合法的「受到欢迎」「广受欢迎」等
            abstract = re.sub(r'\s*(?:诚挚邀请|敬请|请各位|欢迎\s*(?:广大|各位|师生|同学|莅临|参加|光临|届时|踊跃|提出|关注)|感兴趣).*$', '', abstract).strip()
            result['abstract'] = abstract

    # 兜底：若正文来自图片 OCR 且没有明确「摘要」标签，把 OCR 文本清理后作为摘要
    if ocr_text and not result.get('abstract'):
        clean = _clean_ocr_text(ocr_text)
        clean = re.sub(r'[\s\S]*(Copyright|版权所有|备案|ICP|All Rights Reserved|Reserved|粤ICP)[\s\S]*', '', clean).strip()
        clean = re.sub(r'\s*(//[\w./-]+\.(jpg|jpeg|png|gif))\s*', '', clean).strip()
        clean = re.split(rf'(?:{NOISE_MARKERS})', clean)[0].strip()
        # 去掉海报顶部常见噪声（学院/学生会/系列讲座等重复字样）
        clean = re.sub(r'.*?(系列讲座|学术讲座|讲座预告)', '', clean, count=1).strip()
        # 以 title / topic 为锚点截断顶部院校 Logo 等噪声，保留真正内容起始
        if title and title in clean:
            idx = clean.find(title)
            clean = clean[idx + len(title):].strip()
        if result['topic'] and result['topic'] in clean:
            idx = clean.find(result['topic'])
            clean = clean[idx + len(result['topic']):].strip()
        # 若清理后只剩元信息头部（主讲人/主持人/时间/地点等），说明锚点截断后
        # 剩余的是结构化字段而非摘要。对纯海报页这属于正常情况（无独立摘要段落），
        # abstract 保持为空即可，不必强制用主讲人简介填充（那与 speakerBio 重复）。
        # 同时用 _is_meta_skeleton 覆盖「顶部系列名+主题+主讲人+时间+地点」式骨架海报
        # （OCR 空格粘连导致 title/topic 锚点匹配失效，如地科院英文海报）。
        if (re.match(r'^(报告人|主讲人|主持人|时间|地点|时闻|报告人简介|主讲人简介|20\d{2}年|華南|华南|大学|学院|UNIVERSITY|COLLEGE|SOUTH|CHINA|大讲堂|论坛|生命科学|木棉|1933|20\d{2})', clean.strip())
                or _is_meta_skeleton(clean)):
            clean = ''
        # 若 OCR 明确区分「报告简介/讲座简介」等，直接取该部分
        m_summary = re.search(r'(?:报告简介|讲座简介|讲座摘要|报告摘要|内容摘要|内容简介|讲座内容)[：:\s]*(.+)', clean)
        if m_summary:
            clean = m_summary.group(1).strip()
        # 去掉尾部「时间：... 地点：...」等结构化信息，避免与独立字段重复；
        # OCR 可能把「时间」误识为「时闻」，一并处理；同时截断尾部的日期/地点短语。
        clean = re.sub(r'\s*(时间|时闻)\s*[:：].*$', '', clean).strip()
        clean = re.sub(r'\s*地点\s*[:：].*$', '', clean).strip()
        clean = re.sub(r'\s*20\d{2}年\d{1,2}月\d{1,2}日.*$', '', clean).strip()
        # 再次清理尾部乱码
        clean = re.sub(r'[\s]*[曷号]+$\s*', '', clean).strip()
        clean = re.sub(r'\s+[^\u4e00-\u9fa5a-zA-Z0-9]{1,2}\s*$', '', clean).strip()
        if len(clean) > 10:
            result['abstract'] = clean

    # 图片 OCR 场景：标题通常就是海报主标题，若未提取到 topic，用标题去掉日期前缀作为主题
    if ocr_text and not result.get('topic') and title:
        topic_candidate = re.sub(r'^(20\d{6}\s+|20\d{2}[-/]\d{2}[-/]\d{2}\s+|\d{1,2}月\d{1,2}日\s*)', '', title).strip()
        # 去掉末尾的"学术讲座"/"讲座"等通用词，保留具体主题
        topic_candidate = re.sub(r'(?:教授|老师|先生|女士)\s*(学术讲座|讲座|报告|讲坛)$', '', topic_candidate).strip()
        # 允许 topic_candidate == title（title 本身就是有效主题时直接使用）
        if topic_candidate and len(topic_candidate) > 3:
            result['topic'] = topic_candidate

    # 非 OCR 场景：若 title 已清理前缀且长度>8（含实质内容），topic 仍为空时，
    # 从 title 派生 topic（去掉系列/通用后缀，保留核心主题）
    if not result.get('topic') and title and len(title) > 8:
        tp = re.sub(r'^[\s"「]*', '', title).strip()
        # 去掉系列讲座编号（"第N场""第N期""第一场"等）及紧跟的 dash（含双 em-dash "——"）
        tp = re.sub(r'^[\""]?[^"]*?(?:系列讲座|系列活动?)\s*第[一二三四五六七八九十\d]+[场期讲]\s*[—–-—]{1,2}\s*', '', tp).strip()
        # 去掉尾部通用词
        tp = re.sub(r'(?:讲座|报告|讲坛|通知|预告)\s*$', '', tp).strip()
        if len(tp) >= 4:
            result['topic'] = tp

    # 图片 OCR 场景下，「简介」二字常被标题误触发，导致 speakerBio 变成整段海报文字。
    # 若 speakerBio 来自 OCR 且包含时间/地点等结构化信息，说明不是真正的主讲人简介，清空。
    if ocr_text and result.get('speakerBio'):
        if result['speakerBio'] in ocr_text or ocr_text in result['speakerBio']:
            if any(k in result['speakerBio'] for k in ['时间', '地点', '时闻', '日期']):
                result['speakerBio'] = ''

    # 兜底：无结构化标签的叙事体文章（如人工智能学院）
    if not result['topic'] or not result['location'] or not result['speaker'] or not result.get('abstract'):
        narrative = _extract_narrative(body_text, title)
        if not result['topic'] and narrative.get('topic'):
            result['topic'] = narrative['topic']
        if not result['location'] and narrative.get('location'):
            result['location'] = narrative['location']
        # 若已识别到主讲人标签（即便其值为空，如汕尾海报「专家姓名:」与「活动主题:」错位），
        # 不再用叙事兜底覆盖，避免把研究方向片段（如「毒理及细胞对话机制」）误当主讲人。
        if not result['speaker'] and not speaker_label_found and narrative.get('speaker'):
            result['speaker'] = narrative['speaker']
        if not result.get('abstract') and narrative.get('abstract'):
            # OCR 场景下，叙事兜底容易把主讲人简介当成讲座摘要；
            # 若已有 OCR 文本且未提取到明确摘要标签，宁可让 abstract 留空。
            if not (ocr_text and len(ocr_text) > 50):
                result['abstract'] = narrative['abstract']

    # --- 通用后处理（narrative fallback 之后统一执行）---

    # D-FINAL: 职级碎片最终守卫。narrative fallback 可能在 D 规则清空后重新设置 speaker
    # （如 io 1916 的「办二级」），故在所有赋值路径结束后再拦截一次。
    if result.get('speaker') and re.search(
            r'(?:处|部|院|系|中心|公司|局|委|办|室|科|所|厅|署|集团|大学|学院|研究院|'
            r'巡视员|科员|干事|二级|一级|三级)$', result['speaker']):
        result['speaker'] = ''
        result['speakerAffiliation'] = ''

    # D-ORG: speaker 命中组织名后缀时清空；speaker 为空时也尝试从
    # 「特邀专家/专家:姓名」补充提取。
    # 典型场景：io 1563 正文写「主讲 法学会」（实为主办单位缩写），
    # 真正主讲人在「讲座特邀专家：高之国」。
    _ORG_SUFFIX = re.compile(
        r'^(?:法学会|学会|协会|研究会|联合会|基金会|中心|委员会|'
        r'团队|联盟|工作组|办公室|编辑部|理事会|组委会)$')
    if result.get('speaker') and _ORG_SUFFIX.match(result['speaker'].strip()):
        result['speaker'] = ''
    if not result.get('speaker'):
        # 尝试从 text/topic/abstract 提取「特邀专家/特邀嘉宾/专家: 姓名」
        for _src in (text, result.get('topic') or '', result.get('abstract') or ''):
            if not _src:
                continue
            _m2 = re.search(
                r'(?:讲座)?(?:特邀专家|特邀嘉宾|报告专家|演讲嘉宾)'
                r'[：:\s]*(\S{2,4})(?:[，,。\s]|$)', _src)
            if _m2:
                result['speaker'] = _m2.group(1).strip()
                break
            # 也试「专家[：:]姓名」但不匹配「专家简介」「专家委员会」等
            _m3 = re.search(r'专家[：:]\s*(\S{2,4})(?=[，,。\s]|$)', _src)
            if _m3 and not re.search(r'(简介|委员|主任|成员)', _src[_m3.start():_m3.start()+10]):
                result['speaker'] = _m3.group(1).strip()
                break

    # C1-UNIVERSAL: bio 归位通用化。原 C1 规则仅在 SUMMARY_LABELS 路径内生效，
    # 但无摘要标签的页面走 narrative fallback 后，bio 文本可能被放入 abstract。
    # 若 abstract 含 bio 特征词且不含讲座摘要特征词，且 speakerBio 为空，则迁移。
    _BIO_SIG_U = ('任教', '所长', '现任', '毕业于', '博士（', '获', '主要从事',
                  '研究方向', '个人著作', '学者', '简历', '供职于', '兼职')
    _LEC_SIG_U = ('本报告', '本次讲座', '本期讲座', '将介绍', '主要内容',
                   '我们', '讲座将', '本次报告', '报告将', '现将')
    _abs_u = result.get('abstract') or ''
    _bio_u = result.get('speakerBio') or ''
    if (_abs_u and not _bio_u
            and any(s in _abs_u for s in _BIO_SIG_U)
            and not any(s in _abs_u for s in _LEC_SIG_U)
            and len(_abs_u) > 30):
        result['speakerBio'] = result.pop('abstract', '')

    # 地点系统级清理：剔除会议号/密码/议程/报名等噪声后缀、折叠数字内部空格。
    # 放在通用后处理（所有赋值路径之后）统一执行，覆盖 HTML 解析与 OCR 两条路径。
    if result.get('location'):
        result['location'] = _clean_location(result['location'], result.get('title') or result.get('topic'))

    # F-AFF: 单位字段职称守卫（系统级，覆盖所有提取路径）。
    # speakerAffiliation 不应是纯职称（助理研究员/教授/研究员等），也不应残留悬挂括号
    # （如数科院 8794「杨福林(助理研究员(」——原始「报告人：杨福林 助理研究员 (邀请人：范智杰)
    # 北京雁栖湖…」，1478 兜底分支职称剥离列表漏「助理研究员」、且「邀请人」截断留下悬挂左括号，
    # 导致 affiliation 残留「助理研究员 (」）。若去噪后纯为职称词则清空；否则清理悬挂括号与空格。
    if result.get('speakerAffiliation'):
        _aff_dn = re.sub(r'[\s（(）)]', '', result['speakerAffiliation'])
        _TITLE_ONLY = re.compile(
            r'^(?:特聘教授|特任教授|助理教授|副教授|副研究员|助理研究员|研究员|教授|讲师|'
            r'博士后|博士|院士|老师|导师|先生|女士|主任|院长|所长|秘书长)+$')
        if _TITLE_ONLY.fullmatch(_aff_dn):
            result['speakerAffiliation'] = ''
        else:
            _aff2 = re.sub(r'^[（(）)]+', '', result['speakerAffiliation'].strip())
            _aff2 = re.sub(r'[（(）)]+$', '', _aff2.strip())
            # 中文单位（含汉字）：折叠 OCR 插入的内部空格（如「暨 南 大学」→「暨南大学」），
            # 符合数据集中文无空格约定；英文单位（纯拉丁，如 "University of Oslo"）须保留词间空格，
            # 否则会被误并成 "UniversityofOslo"。故按是否含汉字区分处理。
            if re.search(r'[\u4e00-\u9fa5]', _aff2):
                result['speakerAffiliation'] = re.sub(r'\s+', '', _aff2)
            else:
                result['speakerAffiliation'] = re.sub(r'\s+', ' ', _aff2).strip()

    # OCR 纯海报页：为已识别主讲人从 OCR 文本补全/修正简介（speakerBio），
    # 覆盖原「整张海报文本（含标题/主题/多位嘉宾）直接塞进 speakerBio」的情况；
    # 从 OCR 提取主讲人简介（若有 OCR 文本且已识别到 speaker 但尚无 bio）。
    # 原限制 poster_only 导致 body_text 略超 50 字符时整条 bio 提取被跳过（如 lswh 海报页）。
    # 加 not result.get('speakerBio') 守卫，避免覆盖 HTML 正文路径已正确提取的 bio。
    if ocr_text and result.get('speaker') and not result.get('speakerBio'):
        _bio_ocr = _extract_bio_from_ocr(ocr_text, result['speaker'])
        if _bio_ocr:
            result['speakerBio'] = _bio_ocr
            # 仅当 abstract 以 speaker 名字开头且长度接近 bio 时才判定为重复并清空
            # （海报页的 abstract 常为简介片段含名字，不应误杀）
            _abs = result.get('abstract') or ''
            _bio = result.get('speakerBio') or ''
            if (result['speaker'] in (_abs or '')
                and len(_abs) > 0
                and abs(len(_abs) - len(_bio)) < 30):
                result['abstract'] = ''

    # F3 补充：页面存在主讲人/专家姓名标签但值为空或无效（OCR 把值错置到下一行，
    # 常与「活动主题：姓名 描述」相邻），且主题形如「姓名 + 空格 + 描述」时，
    # 从主题提取真实主讲人。仅当主题首词是有效人名才采用，避免把标题/主题误当人。
    if (not result.get('speaker')) and speaker_label_found:
        tp = (result.get('topic') or '').strip()
        m = re.match(r'^([\u4e00-\u9fa5·]{2,4})\s+(.{4,})$', tp)
        if m and _looks_like_real_name(m.group(1)):
            # 排除"第N场""第一场"等系列场次编号被误识为人名（io 源系列讲座常见）
            if not re.match(r'^第[一二三四五六七八九十\d]+[场期讲]', m.group(1)):
                result['speaker'] = m.group(1).strip()

    # F4 补充：speaker 仍为空但 bio 以"姓名,职称..."或"姓名 职称..."开头时，
    # 从 bio 开头提取主讲人姓名。覆盖 io 源等 speaker 未从正文标签提取到的场景。
    if (not result.get('speaker')) and result.get('speakerBio'):
        bio = result['speakerBio'].strip()
        # 模式1："姓名, ..."（逗号分隔的简介）
        m_name = re.match(r'^([\u4e00-\u9fa5]{2,4})(?:[,，]|(?:\s+[，,]))', bio)
        if m_name and _looks_like_real_name(m_name.group(1)):
            candidate = re.sub(r'[男女]$', '', m_name.group(1))
            # 排除机构名（学院/中心/学会等）
            if candidate and _looks_like_real_name(candidate) and not re.search(r'(学院|大学|中心|学会|协会|研究会|委员会|办公室|编辑部)$', candidate):
                result['speaker'] = candidate
        elif not result.get('speaker'):
            # 模式2："姓名 职称/头衔..."（允许连写如"李志远教授"）
            m_name2 = re.match(r'^([\u4e00-\u9fa5]{2,4})\s*((?:教授|研究员|博士|院长|主任|讲师|院士|博导|处长|司长|局长|书记|会长|秘书长|理事))', bio)
            if m_name2 and _looks_like_real_name(m_name2.group(1)):
                candidate2 = re.sub(r'[男女]$', '', m_name2.group(1))
                if candidate2 and _looks_like_real_name(candidate2):
                    result['speaker'] = candidate2

    # 新闻/回顾处理（R5 政策确认，2026-07-19；回退 2026-07-18 的"保留标记"）：
    # 事后才报道的讲座（新闻/回顾稿）不属于预告类聚合，整条剔除、不入库。
    # 两层判定：(1) is_news_record 时间判定（发布晚于讲座）；
    #          (2) is_news_article 语义判定（覆盖无显式发布时间戳的回顾稿）。
    # 命中即 return None，scraper 会打印 [SKIP-NEWS] 并跳过该 URL。
    # 通用 abstract 尾部清理（无论 abstract 从哪条路径赋值，统一截断时间/地点/邀请语）
    # io 源正文常把"时间:... 地点:... 诚挚邀请..."粘到摘要尾部
    _abs = result.get('abstract') or ''
    if _abs:
        _abs = re.sub(r'\s*(?:时间|时闻)\s*[:：\s].*$', '', _abs).strip()
        _abs = re.sub(r'\s*地点\s*[:：\s].*$', '', _abs).strip()
        _abs = re.sub(r'\s*20\d{2}年\s*\d{1,2}月\s*\d{1,2}日.*$', '', _abs).strip()
        # 仅剔除「邀请类」尾部（欢迎广大/欢迎各位/欢迎师生/诚挚邀请/敬请/请各位/感兴趣…），
        # 不误伤正文合法的「受到欢迎」「广受欢迎」等表述（原 bare「欢迎」会截断摘要中部）。
        _abs = re.sub(r'\s*(?:诚挚邀请|敬请|请各位|欢迎\s*(?:广大|各位|师生|同学|莅临|参加|光临|届时|踊跃|提出|关注)|感兴趣).*$', '', _abs).strip()
        if len(_abs) > 3:
            result['abstract'] = _abs
        else:
            result['abstract'] = ''

    # R-RETRO 事后回顾稿显式守卫（用户 2026-07-28 授权）：
    # 页面存在真实发布时间戳且晚于讲座开始（含同日晚于讲座开始）→ 整页为回顾稿，
    # 直接丢弃，不进聚合、不拆分。复用 is_news_record 的判定（含 url_proxy 1天容差），
    # 此处仅显式短路并打 [SKIP-RETRO] 日志，提升可观测性；多场拆分后每条仍由下方
    # is_news_record 独立把关。铁律：publishTime > lectureStart（含同日发布晚于讲座开始）
    # 一律判事后，绝不进聚合。
    if not skip_news_filter and is_news_record(result):
        print(f'[SKIP-RETRO] {url} publishTime={result.get("publishTime")} > lectureStart={result.get("lectureStart")}', file=sys.stderr)
        return None
    if is_non_lecture_title(title) or is_admin_notice(title, body_text) or (not skip_news_filter and is_news_article(title, body_text, result.get('lectureStart'))):
        return None  # [SKIP-NEWS] / [SKIP-ADMIN]
    if skip_news_filter:
        # 来源被显式标记为「跳过新闻过滤」（如整栏为讲座海报预告、发布晚于讲座时间），
        # 记录标记以便后续清理脚本（clean_public.py）也不会误删。
        result['newsFilterBypass'] = True

    # CV1/CV3 交叉校验（仅打 note，CV3 明显异常时修正）
    cv_notes = _cross_validate(result, url_date, ocr_text, publish_time, url_year)
    if cv_notes:
        result['timeNote'] = (result.get('timeNote') or '') + ';' + ';'.join(cv_notes)

    # F3 第 5 步（终检）：任何来源的 speaker 若非有效人名则清空（覆盖叙事兜底等路径）。
    if result.get('speaker') and not _looks_like_real_name(result['speaker']):
        # 多主讲人用「、」连接：逐段校验，全为有效人名时保留
        if '、' in result['speaker']:
            _segs = [s.strip() for s in result['speaker'].split('、') if s.strip()]
            if not (_segs and all(_looks_like_real_name(s) for s in _segs)):
                result['speaker'] = ''
                result['speakerAffiliation'] = ''
        else:
            result['speaker'] = ''
            result['speakerAffiliation'] = ''

    # ---- 多讲座公告拆分（MS1–MS5）----
    # 用 body_text（已合并OCR的正文文本）而非完整 text，避免把页眉/导航/页脚里的
    # 「第N讲/第N期/第N场」重复标记误当成分段锚点（汕尾教学工作坊、abdn357等）。
    sessions = detect_multi_session(
        body_text, title=title, default_year=default_year, publish_time=publish_time,
        title_year=title_year, url_year=url_year, soup=soup, url=url)
    if sessions:
        split_recs = split_record_by_sessions(result, sessions, full_text=body_text)
        kept = []
        for r in split_recs:
            # MS5：拆分后每条独立过回顾判定（某期日期早于发布日→剔除该期，不影响其他期）
            if is_news_record(r):
                print(f'[SKIP-RETRO-SESSION] {url} 第{r.get("lectureIndex")}期', file=sys.stderr)
                continue
            kept.append(r)
        if not kept:
            return None
        return kept

    # ---- 多主讲人连写拆分（同一公告含多位主讲人，报告人字段以「[头衔]姓名职称」拼接）----
    # 例如 cs 4145 论坛「报告人：国家杰青刘梦赤教授长江学者陈建二教授长江学者卢晓中教授」。
    # 与多讲座拆分（按时间/主题分块）互斥：此处各主讲人共享同一题目/时间/地点，仅主讲人不同。
    if not sessions and multi_speakers and len(multi_speakers) >= 2:
        _sp_recs = _split_by_speakers(result, multi_speakers)
        if _sp_recs:
            return _sp_recs

    # ---- VLM 多讲座拆分（海报含多场独立讲座，VLM 已返回数组）----
    # 每场讲座已由 _apply_vlm_to_result 填入字段，此处生成多条独立记录。
    # 每条独立走一遍通用后处理（D-FINAL / C1-UNIVERSAL / _clean_location / F-AFF 等）。
    if _vlm_sessions:
        _vlm_recs = []
        for partial, pt in _vlm_sessions:
            r = partial
            # 应用全局后处理（与返回单条时一致）
            if r.get('location'):
                r['location'] = _clean_location(r['location'], r.get('title') or r.get('topic'))
            if r.get('speaker') and re.search(
                    r'(?:处|部|院|系|中心|公司|局|委|办|室|科|所|厅|署|集团|大学|学院|研究院|'
                    r'巡视员|科员|干事|二级|一级|三级)$', r['speaker']):
                r['speaker'] = ''
                r['speakerAffiliation'] = ''
            _vlm_recs.append(r)
        return _vlm_recs

    # ---- 图文页补摘要/简介（治本地科院「摘要被导航垃圾污染、缺主讲人简介」问题）----
    # 正文已有元数据结构（非 poster_only），但 abstract 缺失/被导航垃圾污染 或 speakerBio 缺失，
    # 且存在内部海报图 → 对首张有效海报图跑 VLM 提取 abstract + speakerBio（无 Key 时自动跳过）。
    if (bool(imgs) and not poster_only
            and (not (result.get('abstract') or '').strip()
                 or _abstract_is_nav_noise(result.get('abstract') or '')
                 or not (result.get('speakerBio') or '').strip())):
        for _u in imgs[:3]:
            _f = _vlm_extract_fields([_u], _load_vlm_config())
            if not _f:
                continue
            _ff = _normalize_vlm_keys(_f)
            # VLM 偶发把单张海报也解析成多场数组（list），此处仅需 abstract/speakerBio 补全，
            # 取首个元素即可；否则保持 None 跳过，避免 'list' object has no attribute 'get' 崩溃。
            if isinstance(_ff, list):
                _ff = _ff[0] if _ff and isinstance(_ff[0], dict) else None
            if not isinstance(_ff, dict):
                continue
            _ab = (_ff.get('abstract') or '').strip()
            _bio = (_ff.get('speakerBio') or _ff.get('bio') or '').strip()
            if _ab and (not (result.get('abstract') or '').strip()
                        or _abstract_is_nav_noise(result.get('abstract') or '')):
                result['abstract'] = _ab
                result['vlmExtracted'] = True
                result['imageParseMethod'] = 'vlm'
            if _bio and not (result.get('speakerBio') or '').strip():
                result['speakerBio'] = _bio
                result['vlmExtracted'] = True
                result['imageParseMethod'] = 'vlm'
            if _ab or _bio:
                result['hasPosterImage'] = True
            break

    return result


# ---------------------------------------------------------------------------
# 多讲座公告拆分（2026-07-20，规则见用户文档《单页面多讲座划分规则》+ docs/PARSING_RULES.md）
# 一个 URL 含 ≥2 场不同时间/主题的系列讲座（如 ggy 5666：4 期、各期不同主题/时间/主持人）。
# 检测（MS1-MS3）：以「主题/题目」标签分块；每块须含可解析日期+时钟时间且时间互不相同；
#   同主题多时段（MS3-2）/ 列表页列举（MS3-3）不拆。
# 拆分（MS4）：以原单条为基底复制 N 份，覆盖 topic/时间/标题；host/会议号/参与者逐块提取；
#   speaker 逐块优先、缺失继承前序、圆桌论坛置空；location 共享（基底空则整页补「活动地点」）。
# 逐条过回顾判定（MS5）：拆分后每条独立过 is_news_record，某期日期早于发布则剔除该期。
# 新增字段：host / meetingId / meetingPlatform / participants / isMultiLecture /
#   lectureIndex / lectureCount / speakerSource / notes（入库；前端展示 host/会议号/参与者）。
# ---------------------------------------------------------------------------
# 主题分隔符：优先匹配「报告N题目/报告N主题」「专题N题目」式系列标签（报告1题目、报告二主题…），
# 否则退回通用 题目/主题 等。把「报告N题目」排在裸「题目」之前，使其作为整段被一次匹配，
# 避免裸「题目」在「报告1题目」内部又命中一次造成错位分块。
_TOPIC_DELIM_RE = re.compile(
    r'(?:报告[一二三四五六七八九十百零0-9]+\s*[题目主题]'
    r'|专题[一二三四五六七八九十百零0-9]+\s*[题目主题]'
    r'|主题[0-9]+'
    r'|讲座题目|题目|主题|讲座主题|报告题目|演讲题目|报告主题|Topic|Title)[：:]')
# 主题值终止符：遇到下一个字段标签、中文日期/时段、块结尾即止。
# 「报告」后的时间/数字/人/地点/摘要 可能与其间被 get_text(' ') 插入的空格隔开，
# 故用 (?=\s*(?:\d|时间|地点|人|摘要)) 容忍空白（修 ggy/cs 类「报告1题目…报告 时间」错把「报告」吞入主题）。
_TOPIC_VAL_STOP = (r'(?=\s*(?:主讲[人师]|报告人|主持人|时间|地点|摘要|简介|主办|承办|'
                   r'邀请人|报告(?=\s*(?:\d|时间|地点|人|摘要))|$|【|第[一二三四五六七八九十百零0-9]+期))')
# 块内子字段（主持人/参与者/主讲人）的终止符：遇到下一个字段标签、中文日期/时段、块结尾即止
_BLOCK_FIELD_STOP = (r'(?=\s*(?:主讲[人师]|报告人|主持人|时间|地点|题目|主题|摘要|简介|'
                      r'主办|承办|邀请人|参与者|$|【|第[一二三四五六七八九十百零0-9]+期|'
                      r'\d{4}年|\d{1,2}月\d{1,2}日|上午|下午|晚上))')


def _detect_field_list_sessions(text, default_year=None, publish_time=None,
                                title_year=None, url_year=None):
    """候选3：字段列表型多报告（cs 5400 等）。

    页面把每场讲座的「报告题目/报告人/报告时间/报告摘要」拆成独立标签交错单列：
      …报告题目:A 报告人:古天龙,地点…时间:3月18日9:00-12:00 报告时间:9:20-10:00
        报告题目:A 报告摘要:A 报告人:古天龙,暨南大学… 报告时间:10:00-10:40 报告题目:B…
    候选1 按「报告题目」分块后，一个块只含单字段、该场的时间/摘要落在相邻块，
    逐块 parse_cn_time 取不到本场时间→整页返回 SINGLE，4 场只展现 1 场。

    本函数以「报告时间」为槽位锚点还原每场：每场时间对应其【前最近报告题目】作标题、
    【后最近报告摘要】作摘要、【后最近报告人】作主讲；并截取 [标题位置, 下一标题位置)
    作为该场 block，供 split_record_by_sessions 逐块抽取 speaker/bio。
    """
    def _collect(label):
        out = []
        for m in re.finditer(re.escape(label), text):
            nxt = re.search(r'报告题目|报告人|报告时间|报告摘要|报告地点|地点|时间|摘要|简介',
                            text[m.end():])
            end = m.end() + nxt.start() if nxt else len(text)
            out.append((m.start(), text[m.end():end].strip()))
        return out
    times = _collect('报告时间:')
    titles = _collect('报告题目:')
    abstracts = _collect('报告摘要:')
    speakers = _collect('报告人:')
    if len(times) < 2 or len(titles) < 2 or len(abstracts) < 2:
        return []
    # 各报告槽的「报告时间:9:20-10:00」常缺日期（日期在页眉，如「时间:3月18日 9:00-12:00」），
    # 故先解析整页日期作为每段时间的兜底日期前缀。
    _page_dt = parse_cn_time(text, default_year=default_year, publish_time=publish_time,
                             title_year=title_year, url_year=url_year)
    _page_date_str = ''
    if _page_dt and _page_dt.get('start'):
        _d = _page_dt['start']
        _page_date_str = f'{_d.year}年{_d.month}月{_d.day}日 '
    cand = []
    for tpos, _ in times:
        dt = parse_cn_time(_page_date_str + text[tpos:], default_year=default_year,
                           publish_time=publish_time, title_year=title_year, url_year=url_year)
        if not dt or not dt.get('start'):
            continue
        st = dt['start']
        if st.hour == 0 and st.minute == 0 and dt.get('end') is None:
            continue
        # 标题：该报告时间【之后最近】出现的报告题目（字段列表型中报告题目常排在
        # 报告时间之后与本场配套；之前可能是上一场的重复/目录标题，勿误取）。
        title = ''
        for p, v in titles:
            if p >= tpos:
                title = v
                break
        if not title or len(title) < 2:
            continue
        # 摘要：之后最近报告摘要；主讲：之后最近报告人
        abstract = ''
        for p, v in abstracts:
            if p >= tpos:
                abstract = v
                break
        speaker = ''
        for p, v in speakers:
            if p >= tpos:
                speaker = v
                break
        # block：从【后最近报告题目】到下一个报告题目位置（含本场全部字段，避免误吞上一场报告人）
        block_start = None
        for p, v in titles:
            if p >= tpos:
                block_start = p
                break
        block_end = len(text)
        _started = False
        for p, v in titles:
            if _started:
                block_end = p
                break
            if p == block_start:
                _started = True
        cand.append({'topic': title, 'start': dt['start'], 'end': dt.get('end'),
                     'block': text[block_start:block_end],
                     'speaker': speaker, 'abstract': abstract})
    return cand


def _detect_numbered_topic_sessions(text, default_year=None, publish_time=None,
                                    title_year=None, url_year=None):
    """候选4：阿拉伯数字编号的「题目N：/报告题目N：」型多报告（cs 4268 等）。

    形如 题目1：CrowdOS… 报告人1：於志文… 学术报告简介:…
          题目2：… 报告人2：… 学术报告简介:…
    各场常共用页眉时间（如 '时间：2019年11月29日（星期五）14:30'），块内无独立时间，
    故从整页解析日期作每段兜底前缀。

    注意：同一编号可能既出现在页眉总览（'题目1：CrowdOS…学术报告 时间：…地点：…'）
    又出现在正文章节（'题目1：CrowdOS：… 报告人1：…'），造成重复块。这里每个编号
    只保留最后一次出现（位置最靠后的正文章节），跳过页眉总览，避免多拆伪场。
    """
    _NUM_TOPIC_RE = re.compile(
        r'(?:报告题目|讲座题目|专题题目|报告专题|题目|主题)\s*[0-9]+\s*[：:]')
    labels = list(_NUM_TOPIC_RE.finditer(text))
    if len(labels) < 2:
        return []
    # 同编号只保留最后一次出现（页眉总览 vs 正文章节去重）
    _dedup_idx = {}
    _ordered = []
    for _m in labels:
        _num = re.search(r'[0-9]+', _m.group())
        _key = _num.group() if _num else _m.group()
        if _key in _dedup_idx:
            _ordered[_dedup_idx[_key]] = _m
        else:
            _dedup_idx[_key] = len(_ordered)
            _ordered.append(_m)
    labels = _ordered
    if len(labels) < 2:
        return []
    # 页眉日期+时间兜底前缀（各场共用开场时间，块内无独立日期/时钟，
    # 故把页眉解析出的完整时间一并带入，否则 parse_cn_time 只能取到 00:00）。
    _page_dt = parse_cn_time(text, default_year=default_year, publish_time=publish_time,
                             title_year=title_year, url_year=url_year)
    _page_date_str = ''
    if _page_dt and _page_dt.get('start'):
        _d = _page_dt['start']
        _page_date_str = f'{_d.year}年{_d.month}月{_d.day}日 {_d.hour:02d}:{_d.minute:02d} '
    cand = []
    for i, lab in enumerate(labels):
        blk_start = lab.end()
        blk_end = labels[i + 1].start() if i + 1 < len(labels) else len(text)
        block = text[blk_start:blk_end]
        tv = re.match(r'\s*(.+?)\s*' + _TOPIC_VAL_STOP, block)
        if not tv:
            tv = re.match(r'\s*(.+?)(?=\s*(?:报告人|主讲人|演讲人|讲者|报告专家))', block)
        if not tv:
            continue
        topic = tv.group(1).strip()
        topic = re.sub(r'\s*(?:主讲人|报告人|预告)\s*[:：]?.*$', '', topic).strip()
        topic = re.sub(r'形式[:：].*$', '', topic).strip()
        if not topic or len(topic) < 2:
            continue
        dt = parse_cn_time(_page_date_str + block, default_year=default_year,
                           publish_time=publish_time, title_year=title_year, url_year=url_year)
        if not dt or not dt.get('start'):
            continue
        st = dt['start']
        if st.hour == 0 and st.minute == 0 and dt.get('end') is None:
            continue
        cand.append({'topic': topic, 'start': dt['start'], 'end': dt.get('end'),
                     'block': block, '_numbered': True})
    return cand


def _extract_affiliation(rest):
    """从主讲人值中拆出的「姓名之后残余文本」里提取单位名。

    覆盖「姓名,党员,学位,单位」式（单位在学位之后）的坑：直接 trim 首个「博士/教授」
    会把「陈莉,中共党员,工学博士,西北大学二级教授…」截成「中共党员,工学」，误删真实单位。
    策略：优先取「单位关键词(大学/学院/研究院/研究所/中心/实验室/学系)」之后的片段为单位名，
    再清尾部职称/学位/党派噪声；无单位关键词且过短（如「新加」这类被下个标签截断的残缺片段）
    则清空，避免把垃圾当单位展示。
    """
    if not rest:
        return ''
    # 优先匹配「完整单位名」（含前缀，如「暨南大学」「北京大学计算机学院」），避免只取
    # 关键词「大学」而漏掉前缀「暨南/北京大学」。非贪婪匹配单位关键词前的最少汉字。
    _UNIT_RE = re.compile(
        r'([\u4e00-\u9fa5A-Za-z·]{0,12}?(?:大学|学院|研究院|研究所|研究中心|实验室|学系|分校|学校)'
        r'(?:[\u4e00-\u9fa5]{0,8}?(?:大学|学院|研究院|研究所|学系))?)')
    m = _UNIT_RE.search(rest)
    if m:
        aff = m.group(1)
    else:
        aff = re.sub(r'^\s*[（(]?\s*(?:现为|现任|现供职于|目前任职于|就职于)\s*', '', rest).strip()
    aff = re.sub(r'^\s*(?:现为|现任|现供职于|目前任职于|就职于)\s*', '', aff).strip()
    aff = re.sub(
        r'\s*(?:特聘教授|特任教授|长聘教授|讲座教授|讲席教授|客座教授|名誉教授|兼职教授|'
        r'青年教授|卓越教授|二级教授|三级教授|四级教授|一级教授|'
        r'副教授|助理教授|副研究员|助理研究员|研究员|教授|讲师|博士后|博士|硕士|学士|'
        r'院士|老师|导师|先生|女士|'
        r'中共党员|共产党员|党员|九三学社|民进|民盟|民建|致公党|农工党|台盟|无党派).*$',
        '', aff).strip()
    aff = aff.strip(' （()）')
    aff = re.sub(r'^[，、；\s]+', '', aff)
    # 无单位关键词且过短 → 视为残缺片段（如「新加」），清空
    if not re.search(r'(大学|学院|研究院|研究所|研究中心|实验室|学系|分校|学校|公司|企业|所|部|中心|医院|学会|协会)', aff):
        if len(aff) < 6:
            return ''
    return aff


def _split_english_speaker(sp):
    """从「报告人/主讲人」标签值中抽取英文/拉丁姓名与单位。

    中文抽取路径的正则只匹配 CJK 姓名（[\\u4e00-\\u9fa5]）：英文姓名（如
    "Yan Zhang, University of Oslo"）会整体落空、最终被 _looks_like_real_name 守卫清空
    （cs 5294 即此坑）。这里在中文路径之前单独处理：仅当值以「首字母大写英文名 + 空格 +
    首字母大写英文名」开头才生效（2–4 个大写词），避免误伤中文名或单位串。
    返回 (name, affiliation)；未命中返回 ('', '')。
    """
    if not sp:
        return '', ''
    s = sp.strip()
    m = re.match(r'^([A-Z][a-z]+(?:\s+[A-Z][a-z\.]+){1,3})', s)
    if not m:
        return '', ''
    name = m.group(1).strip()
    if not _looks_like_real_name(name):
        return '', ''
    after = s[m.end():].strip()
    aff = ''
    # 括号形式：Yan Zhang (University of Oslo)
    pm = re.match(r'^[，,\s]*[（(]\s*([^）)]{2,40}?)\s*[)）]', after)
    if pm:
        aff = pm.group(1).strip()
    else:
        # 逗号/空格分隔的单位片段（"Yan Zhang, University of Oslo" → "University of Oslo"）。
        # 用贪婪 [^,，]{2,40} 取到下一个逗号为止（非贪婪会只咬 2 字符变成 "Un"）。
        cm = re.match(r'^[，,\s]*([^,，]{2,40})', after)
        if cm:
            aff = cm.group(1).strip()
    if aff:
        # 截到下一个讲座关键词（值可能尾随「学术报告 —计算机学院第 52 期」等噪声）
        aff = re.split(r'(?:报告|讲座|题目|时间|地点|主讲|简介|摘要|期|—)', aff)[0].strip()
        # 截掉中部的说明性括号（如「教授（相当于北美首席教授…）」注释，非单位信息）
        aff = re.split(r'[（(]', aff)[0].strip()
        aff = aff.strip(' ,，（）()')
        # 去掉尾部职称词（归入 speakerTitle，与中文路径一致），如「…信息技术学院教授」→「…信息技术学院」
        aff = re.sub(r'(?:特聘教授|特任教授|长聘教授|副教授|助理教授|副研究员|'
                     r'助理研究员|研究员|教授|讲师|博士后|博士|院士|老师|导师|'
                     r'先生|女士)\s*$', '', aff).strip()
        # 整段像职称（Professor/Dr./院士）而非单位，或起始即讲座关键词（无单位信息）→ 放弃
    if (re.match(r'(?:报告|讲座|题目|学术|时间|地点|主讲)', aff)
            or re.fullmatch(r'(?:Professor|Dr\.?|Mr\.?|Ms\.?|Mrs\.?|Associate\s+Professor|'
                            r'Full\s+Professor|Distinguished[\s\w]*|Chair|院士|教授|副教授|'
                            r'研究员|副研究员|讲师|博士)', aff, re.I)):
        aff = ''
    return name, aff


# 荣誉头衔（出现在姓名之前的「前缀型」人才称号，区别于跟在姓名后的职称「教授/研究员」）。
# 仅收录确为「前缀」的称号；特聘教授/讲座教授/客座教授等通常后置，不列入，避免误判。
_HONORIFICS = (
    '国家杰青|长江学者|国家优青|优秀青年|万人计划|百人计划|青年千人|千人计划|'
    '中科院院士|中国工程院院士|973首席|国家万人|杰青|优青'
)
# 连写多主讲人：每段 = (荣誉头衔)? 姓名(2~3字) 职称。姓名与头衔/职称之间可能零空格（如
# 「国家杰青刘梦赤教授」），故头衔与姓名、姓名与职称之间用 \s* 兼容连写。
_SPEAKER_SEG_RE = re.compile(
    rf'(?:({_HONORIFICS}))?\s*'
    rf'([\u4e00-\u9fa5·]{{2,3}})\s*'
    rf'(教授|研究员|副教授|助理教授|副研究员|助理研究员|讲师|院士|博士)'
)


def _parse_concat_speakers(sp):
    """从「报告人」标签值拆出连写的 (姓名, 荣誉头衔, 职称) 列表。

    用于「[头衔]姓名职称」无分隔拼接多位主讲人的场景（如 cs 4145 论坛：
    「国家杰青刘梦赤教授长江学者陈建二教授长江学者卢晓中教授」）。原中文抽取路径
    会把开头的头衔（国家杰青）当成名字、把末尾职称（教授）当成 title，真实姓名全丢。
    返回 [{name, honorific, title}, ...]；姓名非真实人名则跳过。无头衔的单人名也返回 1 条。
    """
    if not sp:
        return []
    out = []
    for m in _SPEAKER_SEG_RE.finditer(sp):
        name = m.group(2)
        if not _looks_like_real_name(name):
            continue
        out.append({'name': name, 'honorific': m.group(1) or '', 'title': m.group(3) or ''})
    return out


def _split_by_speakers(base, speakers):
    """把单条 base 记录按多位主讲人拆成多条（连写多主讲人场景）。

    基底字段（题目/时间/地点/摘要/简介）共享，逐条覆盖主讲人信息；标记 isMultiLecture，
    sourceCount 仅首条计 1，统计页不膨胀。
    """
    out = []
    n = len(speakers)
    for i, spk in enumerate(speakers):
        rec = dict(base)
        rec['speaker'] = spk['name']
        rec['speakerTitle'] = spk['honorific'] or ''
        rec['speakerAffiliation'] = spk.get('aff') or ''
        rec['speakerSource'] = 'label'
        rec['isMultiLecture'] = True
        rec['lectureIndex'] = i + 1
        rec['lectureCount'] = n
        # 来源通知计数：同一公告拆出的 N 条共享 1 个来源页，仅首条计 1，其余计 0
        rec['sourceCount'] = 1 if i == 0 else 0
        rec['notes'] = []
        out.append(rec)
    return out


def _parse_title_no_range(title):
    """从标题解析「第A-B期」连续期号范围，返回 (start, end) 或 None。"""
    m = re.search(r'第\s*(\d{1,3})\s*-\s*(\d{1,3})\s*期', title or '')
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        if 1 <= a < b <= 999:
            return a, b
    return None


def _extract_block_field(block, label_re, max_len=40):
    """从块文本提取某标签后的短字段值（遇到下一个字段标签/中文日期/时段即止）。"""
    m = re.search(rf'{label_re}[：:]\s*(.+?){_BLOCK_FIELD_STOP}', block)
    if not m:
        return ''
    val = m.group(1).strip()
    if max_len and len(val) > max_len:
        val = val[:max_len].strip()
    return val


def detect_multi_session(text, title='', default_year=None, publish_time=None,
                         title_year=None, url_year=None, soup=None, url=None):
    """检测系列讲座公告（MS1-MS3）。

    返回 [] 表示单讲座；否则返回 session 列表（含块文本供拆分时逐块提取）：
      [{'no','topic','start','end','block'}, ...]
    """
    labels = list(_TOPIC_DELIM_RE.finditer(text))
    sessions = []
    if len(labels) < 2:
        # 候选1 分块标记不足（如纯「题目N：」编号型、单场页）：不在此早退，
        # 交由后续候选2/3/4 兜底，避免阻断多报告页拆分（见 cs 4268）。
        pass
    for i, lab in enumerate(labels):
        blk_start = lab.end()
        blk_end = labels[i + 1].start() if i + 1 < len(labels) else len(text)
        block = text[blk_start:blk_end]
        tv = re.match(r'\s*(.+?)\s*' + _TOPIC_VAL_STOP, block)
        if not tv:
            continue
        topic = tv.group(1).strip()
        # 清除尾部粘连的「主讲人/报告人/预告」等非正文词
        topic = re.sub(r'\s*(?:主讲人|报告人|预告)\s*[:：]?.*$', '', topic).strip()
        # 清除「形式：圆桌论坛」式尾部噪声（专题块标签值常粘连活动形式说明）
        topic = re.sub(r'形式[:：].*$', '', topic).strip()
        topic = _clean_session_topic(topic)
        if not topic or len(topic) < 2:
            continue
        dt = parse_cn_time(block, default_year=default_year, publish_time=publish_time,
                            title_year=title_year, url_year=url_year)
        if not dt or not dt.get('start'):
            continue
        # 块内完整性（MS1）：必须有明确时钟时间或结束时间；仅日期（00:00）不足以区分场次，
        # 避免把通知日/发布日误当某场时间。
        st = dt['start']
        if st.hour == 0 and st.minute == 0 and dt.get('end') is None:
            continue
        sessions.append({'topic': topic, 'start': dt['start'], 'end': dt.get('end'),
                         'block': block})
    # 候选2（新增，CS 源常见格式）：离散「报告N/专题N」分场标记——
    # 正文形如「报告一\n时间：9:00\n题目：X\n摘要：…\n报告二\n时间：10:00\n题目：Y…」，
    # 「报告N」与「题目」被时间/换行隔开，候选1的「报告N题目」连续标签匹配不到。
    # 整页常有页眉级「时间:3月11日 9:00-11:30」被每段重复包含，若用候选1的分块会
    # 把各场时间都误解析成页眉时间→时间相同→distinct-time 检查失败。故当存在 ≥2 个
    # 「报告N」标记时，优先用候选2（按报告N干净分段，每场含独立时间）替换候选1。
    # 注意：不能用 \b 作边界——Python3 re 的 \b 是 Unicode 词边界，而 _n1a_normalize
    # 会删掉「报告二」「时间」之间的 CJK 内空格（单字相连），使「报告二时间」连写后
    # 失去词边界，\b 直接失效、标记清零；且「报告一」本就直接接「时」，从未靠 \b 匹配。
    # 改用「后续不是数字/汉字」的负向预查作为边界，兼容连写与带空格两种形态。
    _REPORT_SESSION_RE = re.compile(
        r'报告\s*[一二三四五六七八九十百零0-9]+(?![题目主题摘要人师])(?![一二三四五六七八九十百零0-9])')
    markers = list(_REPORT_SESSION_RE.finditer(text))
    # 同一「报告N」可能既出现在页眉总览又出现在正文章节（如 cs 5388：顶部总览先出现，
    # 其后才是「报告一」正文章节），造成重复标记。每个序号只保留最后一次出现，跳过靠前的
    # 页眉/目录版本，使分段落在真正含该场独立时间/摘要的章节内容上（否则会多拆出一场
    # 09:00-11:30 的页眉总览伪场）。保持按位置排序。
    _dedup_idx = {}
    _markers_ordered = []
    for _m in markers:
        _num = re.search(r'[一二三四五六七八九十百零0-9]+', _m.group())
        _key = _num.group() if _num else _m.group()
        if _key in _dedup_idx:
            _markers_ordered[_dedup_idx[_key]] = _m
        else:
            _dedup_idx[_key] = len(_markers_ordered)
            _markers_ordered.append(_m)
    markers = _markers_ordered
    if len(markers) >= 2:
        # 各报告段只含「时间：9:00-9:50」而缺日期（日期在页眉，位于报告一之前），
        # 故先从整页解析出讲座日期，作为每段时间的兜底日期前缀。
        _page_dt = parse_cn_time(text, default_year=default_year, publish_time=publish_time,
                                 title_year=title_year, url_year=url_year)
        _page_date_str = ''
        if _page_dt and _page_dt.get('start'):
            _d = _page_dt['start']
            _page_date_str = f'{_d.year}年{_d.month}月{_d.day}日 '
        cand2 = []
        for i, mk in enumerate(markers):
            seg = text[mk.start(): markers[i + 1].start() if i + 1 < len(markers) else len(text)]
            tm = re.search(
                r'(?:报告题目|题目|主题|报告主题|专题题目)[：:]\s*'
                r'(.+?)(?=\s*(?:摘要|简介|报告人|主讲人|讲者|时间|地点|报告时间|报告地点|$))',
                seg)
            if not tm:
                continue
            topic = tm.group(1).strip()
            topic = re.sub(r'\s*(?:主讲人|报告人|预告)\s*[:：]?.*$', '', topic).strip()
            topic = _clean_session_topic(topic)
            if not topic or len(topic) < 2:
                continue
            dt = parse_cn_time(_page_date_str + seg, default_year=default_year, publish_time=publish_time,
                                title_year=title_year, url_year=url_year)
            if not dt or not dt.get('start'):
                continue
            st = dt['start']
            if st.hour == 0 and st.minute == 0 and dt.get('end') is None:
                continue
            cand2.append({'topic': topic, 'start': dt['start'], 'end': dt.get('end'),
                          'block': seg})
        if len(cand2) >= 2:
            sessions = cand2
    # 候选3（兜底）：字段列表型多报告（cs 5400 等）。候选1 按「报告题目」分块、候选2 按
    # 「报告N」分块均失败（前者逐块取不到本场时间、后者无离散报告N标记）时，用字段锚点聚合。
    if len(sessions) < 2:
        cand3 = _detect_field_list_sessions(
            text, default_year=default_year, publish_time=publish_time,
            title_year=title_year, url_year=url_year)
        if len(cand3) >= 2:
            sessions = cand3
    # 候选4（兜底）：阿拉伯数字编号的「题目N：/报告题目N：」型多报告（cs 4268 等）。
    # 候选1/2/3 均无法处理：候选1 的 _TOPIC_DELIM_RE 不支持「题目N：」编号格式、
    # 且分块后块内无时间（共用页眉）会全部跳过；候选2 靠「报告N」离散标记（此页为「题目N」）；
    # 候选3 靠 ≥2 个「报告时间:」（此页仅页眉 1 个）。命中即视为 N 场独立讲座。
    if len(sessions) < 2:
        cand4 = _detect_numbered_topic_sessions(
            text, default_year=default_year, publish_time=publish_time,
            title_year=title_year, url_year=url_year)
        if len(cand4) >= 2:
            sessions = cand4
    # 候选5（兜底，abdn / 系列讲坛等）：用「第N讲/第N场」做分段标记。
    # 页面正文可能以 第7讲\n时间：…\n主讲：…\n\n第8讲\n… 形式排列，
    # 通用候选1-4（按题目/主题标签）抓不到这类无结构化标签的系列页。
    if len(sessions) < 2:
        _JIANG_SESSION_RE = re.compile(
            r'(?:第)\s*[一二三四五六七八九十百零0-9]+\s*(?:讲|场|期)'
            r'(?![题主报人目介摘])(?![一二三四五六七八九十百零0-9])')
        j_markers = list(_JIANG_SESSION_RE.finditer(text))
        if len(j_markers) >= 2:
            # MS5-GUARD：页眉/导航/面包屑常把同一「第N期」重复出现（如标题、上一篇、下一篇），
            # 必须要求至少 2 个不同的期/讲/场号，否则视为单讲座误触发。
            _nums_in_title = set(re.findall(
                r'第\s*([一二三四五六七八九十百零0-9]+)\s*(?:讲|场|期)', title or ''))
            _marker_nums = set(re.findall(
                r'第\s*([一二三四五六七八九十百零0-9]+)\s*(?:讲|场|期)',
                ''.join(mk.group() for mk in j_markers)))
            _distinct_nums = _marker_nums - _nums_in_title
            # 所有 marker 的号都在 title 里出现 → 大概率是模板重复/单讲座
            if len(_distinct_nums) == 0:
                j_markers = []
        if len(j_markers) >= 2:
            _page_dt5 = parse_cn_time(text, default_year=default_year,
                                       publish_time=publish_time,
                                       title_year=title_year, url_year=url_year)
            _page_date_str5 = ''
            if _page_dt5 and _page_dt5.get('start'):
                _d5 = _page_dt5['start']
                _page_date_str5 = f'{_d5.year}年{_d5.month}月{_d5.day}日 '
            cand5 = []
            for i, mk in enumerate(j_markers):
                seg = text[mk.end():
                           j_markers[i + 1].start() if i + 1 < len(j_markers)
                           else len(text)]
                # 从分段中提取题目（冒号或换行后的第一个有效文字块）
                tm5 = re.search(r'[:：]\s*([^\n：:]{3,40})', seg)
                if not tm5:
                    tm5 = re.match(r'\s*([^\n]{3,40})', seg)
                topic5 = _clean_session_topic(tm5.group(1).strip()) if tm5 else ''
                # 主题无效（空/过短/纯数字/仅噪声词）则跳过该段
                if not topic5 or len(topic5) < 2:
                    continue
                dt5 = parse_cn_time(_page_date_str5 + seg,
                                     default_year=default_year,
                                     publish_time=publish_time,
                                     title_year=title_year, url_year=url_year)
                if not dt5 or not dt5.get('start'):
                    continue
                st5 = dt5['start']
                cand5.append({'topic': topic5, 'start': dt5['start'],
                              'end': dt5.get('end'), 'block': seg,
                              '_no': mk.group()})
            if len(cand5) >= 2:
                sessions = cand5
    # 去重：同 (topic, start) 视为同一场（顶部「题目」常与首期「主题/报告N题目」重复出现）。
    # topic 比较前去掉所有空白，避免正文数学符号/排版导致的「ℤ_{2^k}」与「ℤ _{2^k}」式微差误判为不同场。
    # 同 key 的多块中保留「信息更完整」者（含主讲人/报告人/摘要/参与者等子字段的块优先），
    # 避免页面级标题头这类「只有标题、无主讲人」的重复块把真正带主讲人的详情块挤掉
    # （如 cs 5708：顶部「题目：…」与「报告1题目：…」同主题同时间，需保留含「报告人1：林富春」的块）。
    _FIELD_W = {'报告人': 3, '主讲人': 3, '主讲': 3, '参与者': 2,
                '摘要': 1, '报告时间': 1, '报告地点': 1}

    def _rich(block):
        score = 0
        for k, w in _FIELD_W.items():
            score += w * len(re.findall(re.escape(k), block or ''))
        return score

    seen = {}
    deduped = []
    for s in sessions:
        key = (re.sub(r'\s+', '', s['topic']), s['start'])
        if key in seen:
            old = seen[key]
            # 新块信息更完整才替换（保留带主讲人/摘要的详情块）
            if _rich(s.get('block', '')) > _rich(old.get('block', '')):
                deduped[deduped.index(old)] = s
                seen[key] = s
            continue
        seen[key] = s
        deduped.append(s)
    sessions = deduped
    if len(sessions) < 2:
        return []
    # MS3-2：所有有效块主题完全相同 → 同讲座多时段，不拆（取首场即可）
    if len({s['topic'] for s in sessions}) == 1:
        return []
    # MS3-3：列表页列举（内容区内含 ≥ 场次数的「讲座类」详情链接 → 视为列表页，不拆）
    # 注意：详情页自身也含大量导航/页脚链接，故只统计「链接文本含讲座类关键词」的详情链接，
    # 避免把详情页误判为列表页（如 ggy 5666 含许多导航链接但主题是纯文本，不应触发）。
    if soup is not None:
        content = (soup.find('div', class_=lambda c: c and 'wp_articlecontent' in c)
                   or soup.find('div', class_='article-content')
                   or soup.find('article')
                   or soup)
        anchors = content.find_all('a', href=True) if hasattr(content, 'find_all') else []
        lect_anchor = 0
        for a in anchors:
            h = (a.get('href') or '').strip()
            if not h or h.startswith('#') or h.startswith('javascript:'):
                continue
            if url and h.rstrip('/') == url.rstrip('/'):
                continue
            txt = a.get_text(strip=True)
            if len(txt) >= 6 and re.search(r'讲座|报告|讲坛|论坛|沙龙|研讨会|座谈', txt):
                lect_anchor += 1
        if lect_anchor >= len(sessions):
            return []
    # 时间互不相同才拆（避免把同一讲座的多个子环节误拆）。
    # 例外：显式阿拉伯编号型多报告（候选4，_numbered=True）常共用开场时间，跳过该检查
    # （如 cs 4268 三场均 14:30 开场，但「题目1/2/3」编号明确为 N 场独立讲座）。
    if not all(s.get('_numbered') for s in sessions):
        distinct = {(s['start'].year, s['start'].month, s['start'].day,
                     s['start'].hour, s['start'].minute) for s in sessions}
        if len(distinct) < 2:
            return []
    # 期号顺序：sessions 都有可解析时间 → 按实际场次时间升序赋 lectureIndex（时间早的期数靠前）；
    # 缺时间（如纯编号型共用时间）→ 保持文档顺序。先排序再 enumerate，no_range 范围也对齐。
    if all(s.get('start') for s in sessions):
        sessions.sort(key=lambda s: s['start'])
    # 期号后缀：优先用标题「第A-B期」范围，否则顺序编号
    no_range = _parse_title_no_range(title)
    for i, s in enumerate(sessions):
        s['no'] = str(no_range[0] + i) if no_range else str(i + 1)
    return sessions


# 主讲人姓名提取（MS4 逐块）：复姓感知。普通中文名 2–3 字；
# 4 字仅允许复姓（欧阳/司马/…）+ 2 字名，避免把「网络空间安全」的单位首字并入姓名
# （如「赵搏文网络空间安全」→ 旧 {2,4} 贪心抓成「赵搏文网」）。
_SURNAME_2 = ('欧阳|司马|上官|诸葛|东方|令狐|皇甫|澹台|独孤|夏侯|宇文|慕容|'
              '司徒|拓跋|尉迟|闻人|公孙|轩辕|长孙|鲜于|万俟|赫连|宗政|濮阳|'
              '淳于|单于|太叔|申屠|仲孙|乐正|钟离|闾丘|梁丘|左丘|东郭|微生')
_SPEAKER_NAME_RE = re.compile(
    r'^((?:(?:' + _SURNAME_2 + r')[\u4e00-\u9fa5]{2}|[\u4e00-\u9fa5·]{2,3}))')


def split_record_by_sessions(base, sessions, full_text=''):
    """把单条 base 记录按 sessions 拆成多条（MS4）。基底字段共享，逐块覆盖。"""
    out = []
    prev_speaker = base.get('speaker') or ''
    prev_aff = base.get('speakerAffiliation') or ''
    prev_title = base.get('speakerTitle') or ''
    base_title = base.get('title') or ''

    # 会议号映射：「腾讯会议专题一:562395609 专题二:…」式布局——全文末尾按专题序号列出，
    # 各专题块内无会议号。解析为 {专题序号: ID}，按 session 在文档中的顺序（位置 1..N）映射。
    _CN_NUM = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7,
               '八': 8, '九': 9, '十': 10}
    meeting_map = {}
    platform_hint = ''
    if full_text:
        for mm in re.finditer(
                r'(?:腾讯会议\s*)?专题\s*([一二三四五六七八九十]+)\s*[：:]\s*([0-9][0-9\s]{5,})',
                full_text):
            n = _CN_NUM.get(mm.group(1))
            if n:
                meeting_map[n] = re.sub(r'\s', '', mm.group(2))
        if '腾讯会议' in full_text:
            platform_hint = '腾讯会议'
        elif 'zoom' in full_text.lower():
            platform_hint = 'Zoom'
        elif 'webex' in full_text.lower():
            platform_hint = 'Webex'
    for i, s in enumerate(sessions):
        rec = dict(base)
        rec['topic'] = s['topic']
        rec['lectureStart'] = s['start'].isoformat(sep=' ')
        rec['lectureEnd'] = s['end'].isoformat(sep=' ') if s.get('end') else None
        # 标题：原标题 — 该块主题（拆分序号（第N期）不再嵌入 title，统一交由前端 span 后缀展示，
        # 避免与真实系列期号如「第53期学术报告」混淆或重复显示）
        _t = _clean_session_topic(s.get('topic') or '')
        # base_title 已含完整主题时不再重复拼接（避免「第13讲丨X— X」式冗余）
        rec['title'] = f"{base_title}— {_t}" if (_t and _t not in base_title) else base_title
        rec['isMultiLecture'] = True
        rec['lectureIndex'] = int(s['no'])
        rec['lectureCount'] = len(sessions)
        # 来源通知计数：同一公告拆出的 N 条共享 1 个来源页，仅首条计 1，其余计 0，
        # 避免统计页「覆盖 N 条来源通知」把 1 则公告高估为 N 条。
        rec['sourceCount'] = 1 if i == 0 else 0
        rec['notes'] = []
        block = s.get('block', '')
        # 主持人（逐块）
        host = _extract_block_field(block, r'主持人')
        if host:
            rec['host'] = host
        # 会议号 + 平台：优先逐块「会议号/Meeting ID」标签；否则用全文「腾讯会议专题X:ID」映射
        mid_m = re.search(r'(?:会议号|会议ID|腾讯会议号|Meeting ID|会议号码)[：:\s]*([0-9][0-9\s]{5,})', block)
        if mid_m:
            rec['meetingId'] = re.sub(r'\s', '', mid_m.group(1))
            rec['meetingPlatform'] = (
                '腾讯会议' if '腾讯会议' in block else
                'Zoom' if 'zoom' in block.lower() else
                'Webex' if 'webex' in block.lower() else '')
        elif meeting_map:
            mid = meeting_map.get(i + 1)  # session 在文档中的顺序即专题序号
            if mid:
                rec['meetingId'] = mid
                rec['meetingPlatform'] = platform_hint
        # 参与者（逐块，圆桌/座谈会常见）
        participants = _extract_block_field(block, r'参与者')
        is_roundtable = bool(participants) or bool(re.search(r'圆桌|座谈', block))
        # 主讲人（逐块优先；缺失继承前序；圆桌且无主讲人→置空）
        sp_m = re.search(rf'(?:主讲[人师]|报告人\d*|讲者\d*|讲者简介)[：:]\s*(.+?){_BLOCK_FIELD_STOP}', block)
        if sp_m:
            cand = sp_m.group(1).strip()
            # 提取职称（用于 speakerTitle）
            title_m = re.search(
                r'(特聘教授|特任教授|副教授|助理教授|副研究员|助理研究员|'
                r'研究员|教授|讲师|博士后|博士|院士)', cand)
            speaker_title = title_m.group(1) if title_m else ''
            # 先去掉尾部职称/单位后缀，再取姓名（避免「徐湘林教授」被截成「徐湘林教」）
            cand_clean = re.sub(
                r'\s*(?:特聘教授|特任教授|副教授|助理教授|副研究员|助理研究员|'
                r'研究员|教授|讲师|博士后|博士|院士|老师|导师|先生|女士).*$', '', cand).strip()
            nm = _SPEAKER_NAME_RE.match(cand)
            if nm and _looks_like_real_name(nm.group(1)):
                rec['speaker'] = nm.group(1)
                rec['speakerSource'] = 'block'
                if speaker_title:
                    rec['speakerTitle'] = speaker_title
                # 用原始 cand（勿用 cand_clean）计算姓名之后残余：cand_clean 会从首个
                # 「博士/教授」截断到文末，而「姓名,党员,工学博士,西北大学」中博士位于中间，
                # 会把真实单位一并截掉，导致 affiliation 丢失。
                rest = cand[nm.end():].strip(' （(，,）)')
                if rest:
                    aff = _extract_affiliation(rest)
                    if aff:
                        rec['speakerAffiliation'] = aff
            else:
                # 英文/拉丁姓名兜底（2026-07-24）：_SPEAKER_NAME_RE 仅匹配 CJK，
                # 多报告页英文主讲人（如 "Yan Zhang, University of Oslo"）会落到这里，尝试英文抽取。
                _en_name, _en_aff = _split_english_speaker(cand)
                if _en_name:
                    rec['speaker'] = _en_name
                    rec['speakerSource'] = 'block'
                    if speaker_title:
                        rec['speakerTitle'] = speaker_title
                    if _en_aff:
                        rec['speakerAffiliation'] = _en_aff
                else:
                    # 值非人名（如「主持嘉宾：」粘连），继承前序
                    rec['speaker'] = prev_speaker
                    rec['speakerAffiliation'] = prev_aff
                    rec['speakerTitle'] = prev_title
                    rec['speakerSource'] = 'inherited' if prev_speaker else None
        else:
            if is_roundtable:
                rec['speaker'] = None
                rec['speakerAffiliation'] = ''
                rec['speakerTitle'] = ''
                rec['speakerSource'] = None
                rec['notes'].append('该期为圆桌论坛/座谈会形式，无独立主讲人')
            else:
                rec['speaker'] = prev_speaker
                rec['speakerAffiliation'] = prev_aff
                rec['speakerTitle'] = prev_title
                rec['speakerSource'] = 'inherited' if prev_speaker else None
        if participants:
            rec['participants'] = participants
        # 每场独立 abstract / speakerBio（解决整页解析时 abstract 泄漏、bio 错位）：
        # 仅当块内含对应标签时才覆盖，避免把基底已有值清空。
        _abs_m = re.search(
            r'(?:学术报告简介|报告简介|内容简介|讲座简介|报告摘要|摘要)[：:]\s*'
            r'(.+?)(?=\s*(?:讲者简介|报告人简介|主讲人简介|个人简介|简介|专家介绍|'
            r'报告[一二三四五六七八九十百零0-9]|报告时间|报告地点|报告题目|报告摘要|$))',
            block, re.S)
        if _abs_m:
            _a = _abs_m.group(1).strip()
            if len(_a) > 5:
                # 复用通用尾部清理（不误伤「受到欢迎」等合法表述）
                _a = re.sub(r'\s*(?:时间|时闻)\s*[:：\s].*$', '', _a).strip()
                _a = re.sub(r'\s*地点\s*[:：\s].*$', '', _a).strip()
                _a = re.sub(r'\s*20\d{2}年\s*\d{1,2}月\s*\d{1,2}日.*$', '', _a).strip()
                _a = re.sub(r'\s*(?:诚挚邀请|敬请|请各位|欢迎\s*(?:广大|各位|师生|同学|'
                            r'莅临|参加|光临|届时|踊跃|提出|关注)|感兴趣).*$', '', _a).strip()
                if len(_a) > 5:
                    rec['abstract'] = _a
        _bio_m = re.search(
            r'(?:讲者简介|报告人简介|主讲人简介|个人简介|简介)[：:]\s*'
            r'(.+?)(?=\s*(?:报告[一二三四五六七八九十百零0-9]|报告时间|报告地点|'
            r'报告题目|报告摘要|$))', block, re.S)
        if _bio_m:
            _b = _bio_m.group(1).strip()
            # 去掉姓名前缀（已在 speaker 提取），保留简介正文
            _b = re.sub(r'^[\u4e00-\u9fa5·]{2,4}\s*[,，]\s*', '', _b).strip()
            if len(_b) >= 10:
                rec['speakerBio'] = _b
                # bio 起首为姓名且本块 speaker 为空时，用 F4 思路回填 speaker
                if not rec.get('speaker'):
                    _nm = re.match(r'^([\u4e00-\u9fa5·]{2,4})', _bio_m.group(1))
                    if _nm and _looks_like_real_name(_nm.group(1)) and \
                            not re.search(r'(学院|大学|中心|学会|协会|研究会|委员会|办公室|编辑部)$', _nm.group(1)):
                        rec['speaker'] = _nm.group(1)
                        rec['speakerSource'] = 'block-bio'
        # 地点：逐块「报告N地点」优先；其次基底（已清洗的共享地点）；再次全页兜底；
        # 最后清泄漏与房间号空格。注意 CS 多报告页地点为全页共享（在页眉「报告地点：X」），
        # 各报告小节通常不含独立地点，故优先用基底干净值，避免被全页「地点：X时间:…报告一…」
        # 这类无分隔的压缩文本污染（原顺序把全页兜底放在基底之前，导致干净基底被泄漏值覆盖）。
        loc = ''
        # 模式A：块内「报告N地点：」标签（避免基底抽取被「报告N」标签污染）
        # 结束前瞻须含「报告摘要/报告人/报告时间/时间/题目」——CS 压缩标签「报告地点：X会议室
        # 报告摘要」会把「报告」漏进地点，且「报告地点：X时间:3月11日」须遇裸「时间」即截断。
        lm = re.search(r'报告\d*地点[：:]\s*([\u4e00-\u9fa5A-Za-z0-9（）()楼室厅馆号\-／/\s]{2,40}?)(?=报告\d|报告摘要|报告人|报告时间|报告题目|时间|题目|主题|摘要|内容简介|$)', block)
        if lm:
            loc = lm.group(1).strip()
        if not loc and rec.get('location'):
            loc = rec['location']
        if not loc and full_text:
            for pat in (r'活动地点\s*[：:]?\s*([^，。；\s]{2,40})',
                        r'(?<!主)地点\s*[：:]?\s*([^，。；\s]{2,40})'):
                m = re.search(pat, full_text)
                if m:
                    cand_loc = m.group(1).strip()
                    # 仅当看起来像真实地点（含 校区/楼/室/学院/大学）才采用，避免误抓噪声
                    if 2 <= len(cand_loc) <= 40 and any(k in cand_loc for k in ('校区', '楼', '室', '学院', '大学', '馆', '中心', '房', '场')):
                        loc = cand_loc
                        break
        # 清理：去掉泄漏的「报告N…」标签，并合并 get_text 在标签边界插入的空格
        # （地点里的空格永远是噪声，如「学院 1 01 会议室」→「学院101会议室」）
        if loc:
            loc = re.split(r'报告\d', loc)[0].strip()
            loc = re.sub(r'\s+', '', loc)
            loc = re.sub(r'报告(摘要|人|时间)?$', '', loc)  # 兜底去掉结尾泄漏的「报告…」标签
            if loc:
                rec['location'] = loc
        # 最终兜底：若本块未识别到主讲人，保留基底（避免误清空系列级主讲人）
        if not rec.get('speaker'):
            rec['speaker'] = prev_speaker or ''
            rec['speakerAffiliation'] = prev_aff or ''
        out.append(rec)
        # 更新继承链（圆桌置空不更新，避免影响后续块）
        if rec.get('speaker'):
            prev_speaker = rec['speaker']
            prev_aff = rec.get('speakerAffiliation') or ''
            prev_title = rec.get('speakerTitle') or ''
    return out
