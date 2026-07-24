"""详情页字段解析：从华师各学院 CMS 详情页提取讲座标准字段。"""
import re
import io
import datetime
import requests
from urllib.parse import urljoin
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
    # 只在结构化标签之前的部分找主讲人，避免把简介/bio 中的被介绍者误抓
    cut = len(text)
    for kw in ('时间', '地点', '时闻', '摘要', '简介', '主讲人简介', '报告人简介', '主办',
               '讲座简介', '报告简介'):
        i = text.find(kw)
        if 0 < i < cut:
            cut = i
    region = text[:cut]
    # 1) 标签式
    m = re.search(r'(?:主讲人|主讲|报告人|主讲嘉宾|特邀嘉宾|特邀专家|演讲人|报告专家|报告嘉宾|专家姓名'
                  r'|Speaker|Presenter|Lecturer)\s*[：:]\s*((?:(?!' + _SPK_VAL_STOP + r')[^\n,，。.]){2,30})',
                  region)
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
    _intro_m = re.search(r'(?:专家介绍|主讲人简介|报告人简介|个人简介|嘉宾介绍|宾介绍|主讲人介绍|报告人介绍|专家简介)\s*[：:]\s*([\u4e00-\u9fa5·]{2,4})', region)
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
def _clean_location(loc):
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
EXCLUDE_KW = ['回顾', '总结', '新闻', '喜报', '招聘', '招生', '答辩', '公示',
              '报名截止', '报名结束', '获奖', '申请表', '纪实', '侧记', '花絮', '速递', '快讯']


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
    try:
        ls_dt = datetime.datetime.strptime(ls[:19], '%Y-%m-%d %H:%M:%S')
        pub_dt = datetime.datetime.strptime(pub[:19], '%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
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
    has_structured = bool(re.search(r'(时间|地点|主讲|主办|承办|讲座时间|讲座地点)[:：]', body))
    # RT2g 仅针对「无结构化标签的流式回顾长文」。正文已含 时间/地点/主讲 等讲座结构化标签，
    # 说明这是正规预告/通知而非回顾稿（回顾稿极少带未来讲座的结构化标签），直接判为非回顾，
    # 避免「教学创新工作坊通知」等含「举办/开展」措辞的预告被误杀（如 gxb 第51期工作坊）。
    if has_structured:
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
                    result['abstract'] = abstract
    return result


def _clean_title(t):
    t = t.strip()
    if ' - ' in t:
        t = t.split(' - ')[0].strip()
    if '｜' in t:
        t = t.split('｜')[0].strip()
    # 列表页锚文本常把发布日期前缀粘进标题（如「2024-05-21艺术乡建…」「2023年12月24日红树林…」）。
    # 去掉标题开头的日期前缀，仅保留真实讲座标题。日期本身已由时间解析单独处理。
    t = re.sub(r'^\s*(?:19|20)\d{2}\s*[-/年\.]\s*\d{1,2}\s*[-/月\.]\s*\d{1,2}\s*[日号]?\s*', '', t).strip()
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
        '地点', '时间', '主讲人', '主讲师', '报告人', '主讲嘉宾', '演讲人', '主讲',
        '学术主持', '主办单位',
        # 简介/摘要/内容
        '主讲人简介', '主讲人简历', '简历', '简介',
        '摘要', '讲座内容', '讲座内容提要', '内容提要', '讲座摘要',
        '报告摘要', '内容摘要', '内容简介', '讲座简介', '报告内容', '讲座概要', '内容概要',
        # 发布信息
        '发布时间', '发布日期', '来源',
    ]
    # 1) 先把方括号/花括号形式的标签统一转成「标签：」
    #    如美术学院页面：【主题】xxx、【主讲人】xxx、【时间】xxx、【地点】xxx
    for label in sorted(labels, key=len, reverse=True):
        text = re.sub(rf'[【\[]\s*{re.escape(label)}\s*[】\]]', label + '：', text)
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
    if list_title:
        title = _clean_title(list_title)
    else:
        h1 = soup.find('h1') or soup.find('h2')
        if h1:
            title = h1.get_text(strip=True)
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
    # content_div 找不到时（非 WebPlus 站点，如图书馆 lib.scnu.edu.cn）退化为整页收集，
    # 并用 _is_chrome_img 过滤导航/页脚图标，避免对无关图做无意义 OCR。
    def _is_chrome_img(src):
        s = (src or '').lower()
        bad = ('icon', 'logo', 'banner', 'arrow', 'foot', 'weixin', 'wx', 'qr',
               'qrcode', 'bg', 'btn', 'nav', 'share', 'close', 'more', 'header',
               'top', 'bottom', 'slide', 'ad', 'avatar')
        return any(k in s for k in bad)

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
    img_src_root = content_div if content_div else soup
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
                        _doc.close()
                        _pdf_text = '\n'.join(_pages_text)
                        if _pdf_text:
                            body_text = body_text + '\n' + _pdf_text
                            text = text + '\n' + _pdf_text
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

    # 纯海报页（正文几乎为空）时直接 OCR；其余含图页面在字段提取后再按需 OCR 摘要/简介
    poster_only = len(body_text) < 50
    if poster_only:
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
    if rt and rt.get('start'):
        t = {'start': datetime.datetime.fromisoformat(rt['start']),
             'end': datetime.datetime.fromisoformat(rt['end']) if rt.get('end') else None,
             'has_time': True}
        result['timeConfidence'] = rt.get('confidence')
        result['timeNote'] = rt.get('note')
    # 正文未解析出日期且含海报图片：OCR 后重试（仅补缺失，不覆盖已有）
    if not t and imgs:
        _do_ocr()
        if ocr_text:
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
    # R3 本质条款：第 2/3 级兜底的发布时间若等于权威讲座日，视为误抓讲座时间，作废该候选
    if (publish_time and publish_level in (2, 3) and t
            and t['start'].strftime('%Y-%m-%d') == publish_time[:10]):
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
        '时间|主讲[人师]|讲座人|主持人|主讲|报告人|主讲嘉宾|演讲人|邀请人|'
        'Speaker|Presenter|Lecturer|'
        '摘要|讲座内容提要|内容提要|讲座内容摘要|内容摘要|内容简介|'
        '讲座内容|讲座简介|报告内容|讲座概要|内容概要|'
        '简历|主讲人简介|主讲人简历|简介|专家介绍|专家简介|发布|来源'
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

    # --- 主讲人（兼容「主讲人/主讲师/报告人/主讲嘉宾/演讲人/主讲」）---
    # 注意：排除「主讲《…》」（正文里「主讲《课程名》」是动宾短语，不是主讲人标签），
    # 否则会把书名号后的课程名误当主讲人（如汕尾校区海报 bio 中的「主讲《动物组织学与胚胎学》」）。
    # 汕尾校区教学工作坊海报用「主讲专家:」「专家姓名:」标注主讲人，一并纳入。
    speaker_label_found = False
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
        rf'(?:主讲嘉宾|报告嘉宾|主讲人(?!简介|简历|介绍)|主讲师(?!简介|简历)'
        rf'|主讲(?!《|简介|简历)(?:专家)?'
        rf'|报告人(?!简介|简历)|演讲人|报告专家|专家姓名'
        rf'|Speaker|Presenter|Lecturer)\s*[：:]\s*(.+?){STOP}'
    )
    m = re.search(speaker_pat, text)
    # F2-OCR-SP: OCR 海报常把标签与值之间的冒号和空格全部识丢，
    # 变成零分隔符粘连（如工学部海报「主办单位:华南师范大学工学部主讲人马於光院士」）。
    # 若上述带冒号正则未命中，尝试零宽/纯空格的「标签+姓名」格式；
    # 值截取到下一个字段标签或非名字字符为止，限长防溢出。
    if not m:
        _ocr_sp_pat = (
            rf'(?:主讲嘉宾|报告嘉宾|主讲人(?!简介|简历|介绍)|主讲师(?!简介|简历)'
            rf'|主讲(?!《|简介|简历)(?:专家)?'
            rf'|报告人(?!简介|简历)|演讲人|报告专家|专家姓名)'
            rf'\s*(?:[：:]|\s*)\s*'
            rf'([\u4e00-\u9fa5·]{{2,4}}(?:院士|教授|研究员|讲师|博士|特聘教授|特任教授|副教授|助理教授)?){STOP}'
        )
        m = re.search(_ocr_sp_pat, text)
        if m:
            speaker_label_found = True
    if m:
        speaker_label_found = True
        sp = m.group(1).strip()
        # F3 step2 — 职称后缀分离为 speakerTitle（如「助理研究员」「教授」）
        sp_title = None
        mt_title = re.search(r'(助理研究员|副研究员|助理研究员|研究员|特聘教授|特任教授|长聘教授|副教授|助理教授|教授|讲师|博士后|博士|院士|老师|导师|先生|女士)+$', sp)
        if mt_title:
            sp_title = mt_title.group(1).strip()
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
        # 去掉尾部职称后缀
        sp_clean = re.sub(r'\s*(?:特聘教授|特任教授|副教授|助理教授|副研究员|助理研究员|研究员|教授|讲师|博士后|博士|院士|老师|导师|先生|女士).*$', '', sp).strip()
        # 尝试拆分姓名+单位（括号形式）
        mm = re.match(r'(.+?)\s*[（(]([^）)]{2,40})[）)]', sp)
        if mm:
            result['speaker'] = sp_clean.split('（')[0].strip()
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
                mm2 = re.match(r'^([\u4e00-\u9fa5·]{2,5})\s+([\u4e00-\u9fa5]{4,40})$', sp_normalized)
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
                        aff = re.sub(r'\s*(?:特聘教授|副教授|教授|讲师|院士|老师).*$', '', rest).strip()
                        # 清除「现为/现任/现供职于/目前任职于」等状态前缀
                        aff = re.sub(r'^\s*(?:现为|现任|现供职于|目前任职于|就职于)\s*', '', aff).strip()
                        result['speakerAffiliation'] = aff
                else:
                    result['speaker'] = sp_clean

    # 兜底：从标题括号中提取主讲人，如「（朱英教授）」
    if not result['speaker']:
        tm = re.search(r'（([^（）]*?(教授|研究员|副教授|讲师|博士)[^（）]*?)）', title)
        if tm:
            result['speaker'] = re.sub(r'\s*(教授|研究员|副教授|讲师|博士|老师).*$', '', tm.group(1)).strip()

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
    if need_ocr and not ocr_text:
        _do_ocr()
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
        '讲座主题简介|讲座内容|讲座简介|报告内容|讲座概要|内容概要|摘要'
        '|Abstract|Synopsis'
    )

    # 页面噪声/侧边栏标记：遇到这些说明正文已结束，应截断
    NOISE_MARKERS = (
        '资讯及通知|相关新闻|最新动态|推荐阅读|相关文章|相关讲座|'
        '上一篇|下一篇|附件下载|相关链接|网友评论|分享|标签|相关推荐|'
        '通知公告|最新公告|站内搜索|快速导航'
    )

    bio_pat = rf'(?:报告人简介|主讲人简介|主讲人简历|简历|(?<!内容)简介|Bio)[\s:：]*'
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
        # 如果清理后只剩元信息（开头即报告人/主讲人/主持人/时间/地点/报告人简介/院校抬头），说明海报没有讲座摘要
        if re.match(r'^(报告人|主讲人|主持人|时间|地点|时闻|报告人简介|主讲人简介|20\d{2}年|華南|华南|大学|学院|UNIVERSITY|COLLEGE|SOUTH|CHINA|大讲堂|论坛|生命科学|木棉|1933|20\d{2})', clean.strip()):
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
        if topic_candidate and topic_candidate != title and len(topic_candidate) > 3:
            result['topic'] = topic_candidate

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
        result['location'] = _clean_location(result['location'])

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
            result['speakerAffiliation'] = re.sub(r'\s+', '', _aff2)

    # OCR 纯海报页：为已识别主讲人从 OCR 文本补全/修正简介（speakerBio），
    # 覆盖原「整张海报文本（含标题/主题/多位嘉宾）直接塞进 speakerBio」的情况；
    # 海报页无独立「摘要」且 abstract 含主讲人时一并清空。仅对纯海报页生效，
    # 避免影响含 HTML 正文的页面（其 speakerBio 已由 HTML 路径正确提取）。
    if poster_only and ocr_text and result.get('speaker'):
        _bio_ocr = _extract_bio_from_ocr(ocr_text, result['speaker'])
        if _bio_ocr:
            result['speakerBio'] = _bio_ocr
            if result['speaker'] in (_abs_u or ''):
                result['abstract'] = ''

    # F3 补充：页面存在主讲人/专家姓名标签但值为空或无效（OCR 把值错置到下一行，
    # 常与「活动主题：姓名 描述」相邻），且主题形如「姓名 + 空格 + 描述」时，
    # 从主题提取真实主讲人。仅当主题首词是有效人名才采用，避免把标题/主题误当人。
    if (not result.get('speaker')) and speaker_label_found:
        tp = (result.get('topic') or '').strip()
        m = re.match(r'^([\u4e00-\u9fa5·]{2,4})\s+(.{4,})$', tp)
        if m and _looks_like_real_name(m.group(1)):
            result['speaker'] = m.group(1).strip()

    # 新闻/回顾处理（R5 政策确认，2026-07-19；回退 2026-07-18 的"保留标记"）：
    # 事后才报道的讲座（新闻/回顾稿）不属于预告类聚合，整条剔除、不入库。
    # 两层判定：(1) is_news_record 时间判定（发布晚于讲座）；
    #          (2) is_news_article 语义判定（覆盖无显式发布时间戳的回顾稿）。
    # 命中即 return None，scraper 会打印 [SKIP-NEWS] 并跳过该 URL。
    if is_non_lecture_title(title) or is_admin_notice(title, body_text) or (not skip_news_filter and (is_news_record(result) or is_news_article(title, body_text, result.get('lectureStart')))):
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
        result['speaker'] = ''
        result['speakerAffiliation'] = ''

    # ---- 多讲座公告拆分（MS1–MS5）----
    sessions = detect_multi_session(
        text, title=title, default_year=default_year, publish_time=publish_time,
        title_year=title_year, url_year=url_year, soup=soup, url=url)
    if sessions:
        split_recs = split_record_by_sessions(result, sessions, full_text=text)
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
    if len(labels) < 2:
        return []
    sessions = []
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
    # 时间互不相同才拆（避免把同一讲座的多个子环节误拆）
    distinct = {(s['start'].year, s['start'].month, s['start'].day,
                 s['start'].hour, s['start'].minute) for s in sessions}
    if len(distinct) < 2:
        return []
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
        # 标题：原标题（第X期）— 该块主题（已含期号便于对应原文，又直接显示主题）
        if '（第' not in base_title:
            rec['title'] = f"{base_title}（第{s['no']}期）— {s['topic']}"
        else:
            rec['title'] = f"{base_title}— {s['topic']}"
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
        sp_m = re.search(rf'(?:主讲[人师]|报告人\d*)[：:]\s*(.+?){_BLOCK_FIELD_STOP}', block)
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
            nm = _SPEAKER_NAME_RE.match(cand_clean)
            if nm and _looks_like_real_name(nm.group(1)):
                rec['speaker'] = nm.group(1)
                rec['speakerSource'] = 'block'
                if speaker_title:
                    rec['speakerTitle'] = speaker_title
                rest = cand_clean[nm.end():].strip(' （(，,）)')
                if rest:
                    # 清除「现为/现任/现供职于/目前任职于」等状态前缀，只保留单位名
                    aff = re.sub(
                        r'^\s*[（(]?\s*(?:现为|现任|现供职于|目前任职于|就职于)\s*', '', rest).strip()
                    # 再清除尾部职称
                    aff = re.sub(
                        r'\s*(?:特聘教授|特任教授|副教授|助理教授|副研究员|助理研究员|'
                        r'研究员|教授|讲师|博士后|博士|院士|老师|导师|先生|女士).*$', '', aff).strip()
                    # 去掉首尾括号残留
                    aff = aff.strip(' （()）')
                    if aff:
                        rec['speakerAffiliation'] = aff
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
        # 地点：逐块「报告N地点」优先；其次整页「活动地点」；再次基底；最后清泄漏与房间号空格。
        loc = ''
        # 模式A：块内「报告N地点：」标签（避免基底抽取被「报告N」标签污染）
        # 结束前瞻须含「报告摘要/报告人/报告时间」——CS 压缩标签「报告地点：X会议室报告摘要」
        # 会把「报告」漏进地点（被「摘要」前瞻误匹配），故在此直接截断。
        lm = re.search(r'报告\d*地点[：:]\s*([\u4e00-\u9fa5A-Za-z0-9（）()楼室厅馆号\-／/\s]{2,40}?)(?=报告\d|报告摘要|报告人|报告时间|摘要|内容简介|$)', block)
        if lm:
            loc = lm.group(1).strip()
        if not loc and full_text:
            for pat in (r'活动地点\s*[：:]?\s*([^，。；\s]{2,60})',
                        r'(?<!主)地点\s*[：:]?\s*([^，。；\s]{2,60})'):
                m = re.search(pat, full_text)
                if m:
                    cand_loc = m.group(1).strip()
                    # 仅当看起来像真实地点（含 校区/楼/室/学院/大学）才采用，避免误抓噪声
                    if 2 <= len(cand_loc) <= 60 and any(k in cand_loc for k in ('校区', '楼', '室', '学院', '大学', '馆', '中心', '房', '场')):
                        loc = cand_loc
                        break
        if not loc and rec.get('location'):
            loc = rec['location']
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
