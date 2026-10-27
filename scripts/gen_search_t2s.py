#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成并写入前端搜索用的「繁→简」映射表（site/app.core.js 的两个常量）。

## 为什么需要它

前端搜索原先用裸 `hay.includes(q)` 比较，对「同字不同码」敏感：库里几乎全简体，
用户用繁体输入（講座 / 學術 / 報告）等于零召回。全角/半角同理。
归一化见 `site/app.core.js` 的 `normalizeSearchText`，本脚本只负责那张表。

## 为什么用脚本而不是手写

映射表是 400+ 个汉字的两条**等长字符串**（`SEARCH_T2S_SIMP` / `SEARCH_T2S_TRAD`，
逐位对齐）。手工抄写必然错位，而错位的后果是**静默错映射**——把 A 字归一成 B 字，
用户搜到的结果莫名其妙，且不报任何错。故：表只在此处维护，由脚本校验后写入。

## 收录原则：宁缺毋滥

只收**高频常用字**，生僻字一律不收。理由：错映射（两个字被错误合并）会产生
误召回，比漏映射（搜不到）更难排查。历史上踩过的两个坑：

  · `了瞭` —— 简体「瞭望」本身保留「瞭」，收了会让「瞭望」被归一成「了望」，
    反而搜不到库里正确的「瞭望」；
  · `呀呀` —— 简繁同形，映射无意义，会被校验拦下。

## 校验（任一不过即中止，不写文件）

  1. 每对恰好 2 个字符；
  2. 两个字都是 CJK（U+4E00-U+9FFF）；
  3. 简繁**不同形**（同形映射无意义）；
  4. **繁体键唯一** —— 同一繁体字只能映射到一个简体字。
     注意：同一简体可对应多个繁体（发 ← 發 / 髮；里 ← 裡 / 裏），
     故去重按**繁体键**而非简体键。这正是「一简对多繁」能共存的原因。

## 用法

    python scripts/gen_search_t2s.py            # 校验并写入 site/app.core.js
    python scripts/gen_search_t2s.py --dry-run  # 只校验 + 打印统计，不写文件

写完记得：`python scripts/generate_frontend_data.py` 重算 app.core.js 的 ?v= 戳，
否则回访用户会继续跑旧 JS。`tests/js/app_search_normalize.js` 会把上面四条校验
再跑一遍（CI 门禁），防止有人绕过脚本直接手改 app.core.js 里的表。
"""
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, 'site', 'app.core.js')

# 常用繁简对照（简体在前、繁体在后）。新增字直接往对应主题行里加即可。
PAIRS = """
学學 术術 讲講 题題 师師 范範 会會 议議 实實 验驗 数數 据據 统統 计計 论論 报報
专專 业業 课課 硕碩 毕畢 礼禮 试試 绩績 笔筆 记記 义義 纲綱 书書 馆館 档檔 资資
库庫 览覽 导導 项項 经經 费費 评評 审審 员員 组組 织織 构構 机機
国國 际際 华華 侨僑 台臺 湾灣 历歷 艺藝 体體 养養 济濟 贸貿 产產 农農 渔漁 矿礦
电電 医醫 药藥 卫衛 环環 筑築 规規 划劃 设設 区區 块塊 链鏈 节節 点點 线線 网網
络絡 云雲 软軟 备備 讯訊 园園 门門 楼樓 栋棟 号號 厅廳 场場 车車 飞飛 广廣
营營 销銷 财財 务務 税稅 贷貸 银銀 债債 价價 购購 传傳 闻聞 访訪 说說 读讀 写寫
杂雜 编編 辑輯 纸紙 张張 页頁 词詞 汇匯 译譯 优優 质質 标標 准準 则則 条條 践踐
应應 发發 发髮 创創 识識 别別 处處 图圖 语語 习習 练練 测測 证證 结結 归歸 纳納
绎繹 间間 后後 现現 于於 还還 从從 进進 过過 开開 关關 这這 们們 为為 与與 周週
钟鐘 头頭 举舉 办辦 参參 注註 册冊 录錄 闭閉 启啟 动動 暂暫 继繼 续續 载載 帐帳
码碼 输輸 选選 择擇 确確 认認 详詳 寻尋 筛篩 幂冪 响響 风風 雾霧 边邊 远遠 达達
运運 连連 递遞 迁遷 适適 兴興 乐樂 欢歡 庆慶 贺賀 愿願 梦夢 长長 团團 队隊 众眾
亲親 爱愛 兰蘭 势勢 县縣 变變 吓嚇 吗嗎 呜嗚 咏詠 哑啞 唤喚 啰囉 啸嘯 喷噴 嘱囑
马馬 龙龍 凤鳳 伟偉 军軍 强強 荣榮 贵貴 宾賓 贤賢 辉輝 锋鋒 钢鋼 铁鐵 铜銅 钱錢
钧鈞 铭銘 锦錦 镇鎮 东東 陈陳 刘劉 杨楊 赵趙 黄黃 吴吳 郑鄭 冯馮 韩韓 邓鄧 叶葉
罗羅 谭譚 萧蕭 顾顧 龚龔 庞龐 庄莊 欧歐 阳陽 韦韋 龟龜 张張
个個 么麼 几幾 样樣 话話 请請 谢謝 对對 错錯 问問 只隻 里裡 里裏 面麵 干乾 干幹
系係 系繫 表錶 台檯 后後 才纔 秋鞦 丑醜 斗鬥 谷穀 划劃 尽盡 荐薦 卷捲
累纍 蒙矇 蒙濛 蒙懞 弥瀰 弥彌 蔑衊 宁寧 千韆 秋鞦 曲麯 确確 舍捨 尸屍 升昇
松鬆 坛壇 涂塗 洼窪 系係 咸鹹 向嚮 须須 旋鏇 佣傭 涌湧 游遊 余餘 御禦 郁鬱
吁籲 愿願 云雲 扎紮 占佔 折摺 征徵 只隻 致緻 制製 钟鐘 种種 朱硃 筑築 庄莊
准準 冢塚 周週 注註 咸鹹
时時 两兩 见見 听聽 让讓 给給 带帶 买買 卖賣 岁歲 万萬 亿億 鸟鳥 鱼魚 鸡雞 丽麗
丰豐 亚亞 仅僅 伤傷 侧側 儿兒 党黨 冲衝 决決 况況 减減 刚剛 剧劇 劳勞 勋勳 协協
单單 厂廠 压壓 双雙 围圍 圆圓 圣聖 坚堅 担擔 拟擬 拥擁 挂掛 挥揮 损損 换換 职職
联聯 声聲 胜勝 脑腦 苏蘇 获獲 蓝藍 虑慮 虽雖 补補 装裝 观觀 视視 讨討 训訓 诗詩
谁誰 调調 谈談 负負 责責 货貨 赛賽 赞讚 转轉 轮輪 释釋 邮郵 阅閱 陆陸 随隨 难難
静靜 齐齊 灯燈 频頻 献獻 辞辭 奖獎 摄攝 竞競 紧緊 织織 续續 络絡 缩縮 编編 织織
"""


def build(pairs):
    """PAIRS 文本 → (简体串, 繁体串)。校验不过抛 ValueError。"""
    simp, trad, seen, problems = [], [], {}, []
    for p in pairs.split():
        if len(p) != 2:
            problems.append('长度不是 2：%r' % p)
            continue
        s, t = p[0], p[1]
        if not ('\u4e00' <= s <= '\u9fff') or not ('\u4e00' <= t <= '\u9fff'):
            problems.append('非汉字：%r' % p)
            continue
        if s == t:
            problems.append('简繁同形（应删除）：%r' % p)
            continue
        if t in seen:
            if seen[t] == s:
                continue                      # 同一对重复出现：静默去重
            problems.append('繁体键冲突：%s → %s / %s' % (t, seen[t], s))
            continue
        seen[t] = s
        simp.append(s)
        trad.append(t)
    if problems:
        raise ValueError('校验失败：\n  - ' + '\n  - '.join(problems[:40]))
    return ''.join(simp), ''.join(trad)


def write_into_core(simp, trad, core_path=CORE):
    """按锚点替换 app.core.js 里的两个常量，返回是否真的改了。"""
    src = io.open(core_path, encoding='utf-8', newline='').read()
    out, i = [], 0
    hits = {'SIMP': 0, 'TRAD': 0}
    while i < len(src):
        if src.startswith('const SEARCH_T2S_SIMP =', i):
            key = 'SIMP'
        elif src.startswith('const SEARCH_T2S_TRAD =', i):
            key = 'TRAD'
        else:
            out.append(src[i])
            i += 1
            continue
        nl = src.index('\n', i)
        out.append(src[i:nl + 1])
        j = nl + 1
        while src[j] in ' \t\n':
            j += 1
        if src[j] != "'":
            raise ValueError('锚点 %s 后未找到字符串常量' % key)
        end = src.index("'", j + 1)
        out.append("  '" + (simp if key == 'SIMP' else trad) + "'")
        hits[key] += 1
        i = end + 1
    if hits['SIMP'] != 1 or hits['TRAD'] != 1:
        raise ValueError('锚点命中次数异常：%r（app.core.js 结构被改过？）' % hits)
    new = ''.join(out)
    if new == src:
        return False
    io.open(core_path, 'w', encoding='utf-8', newline='').write(new)
    return True


def main(argv):
    dry = '--dry-run' in argv
    simp, trad = build(PAIRS)
    assert len(simp) == len(trad)
    print('校验通过：%d 对映射' % len(simp))
    if dry:
        print('--dry-run：未写入文件')
        return 0
    changed = write_into_core(simp, trad)
    print('[done] site/app.core.js %s' % ('已更新' if changed else '无变化'))
    if changed:
        print('下一步：python scripts/generate_frontend_data.py  # 重算 ?v= 缓存戳')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
