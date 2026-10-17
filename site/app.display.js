// 展示层：渲染单元、时间格式化、状态徽章、clamp 溢出实测、摘要/简介门禁。
Object.assign(APP_METHODS, {
    // 论坛卡展开状态：未显式操作过时，「筛选/搜索命中子集」自动展开（2026-09-26 方案B）
    isForumOpen(u) {
      const v = this.expandedForums[u.url];
      if (v !== undefined) return v;
      return u.recs.length < u.total;
    },
    // 展开场次清单按时间从早到晚正序（主列表倒序只服务时间线视觉，议程清单按议程自然顺序）；
    // 无时间场次稳定排在最后，保持其余相对顺序
    forumRecsSorted(u) {
      return [...u.recs].sort((a, b) => {
        const ta = a.lectureStart || '', tb = b.lectureStart || '';
        if (!ta && !tb) return 0;
        if (!ta) return 1;
        if (!tb) return -1;
        return ta.localeCompare(tb);
      });
    },

    toggleForum(u) {
      this.expandedForums[u.url] = !this.isForumOpen(u);
    },

    // 远期折叠组判定（2026-10-13 方案 B）：供 index.html 模板区分「折叠提示条」与
    // 「日期头」两种组头。⚠ 必须走实例方法而不能在模板里直接引用 FAR_FOLD_KEY 常量——
    // Vue 运行时编译（new Function + with(_ctx)）拿不到脚本顶层的 const 绑定
    // （不挂 globalThis），模板里会恒为 undefined。
    isFarFoldGroup(g) {
      return !!g && g.key === FAR_FOLD_KEY;
    },

    /* ---------- 讲者视图 ---------- */
    pickSpeaker(l) {
      // 进入讲者视图：按该记录的归一化键筛选，滚回顶部
      const keys = l.speakerKeys || [];
      if (!keys.length) return;
      this.speaker = keys[0];
      this.currentPage = 1;
      window.scrollTo({ top: 0, behavior: 'smooth' });
    },
    clearSpeaker() {
      this.speaker = '';
      this.currentPage = 1;
    },
    /* ---------- 工具 ---------- */
    yearOf(l) {
      if (!l) return '';
      if (l.lectureStart) return String(l.lectureStart).slice(0, 4);
      // 部分讲座未解析到具体时间，但发布时间或标题里含年份，据此归入对应年份（与 stats.js 保持一致）
      const m = (l.publishTime || '').match(/^(\d{4})/) || (l.title || '').match(/(\d{4})/);
      // 无年份时返回「其他」而非 ''：与 generate_frontend_data.year_of 及 stats.js
      // 的 UNKNOWN_YEAR 口径对齐（2026-10-14 P2-3）。此前返回 '' 导致：
      //   ① 年份下拉不含「其他」项（years() 里 if (y) 把空串滤掉）；
      //   ② 按年筛选时无年份讲座被永久排除（filtered() 里 '' !== 选中年份）。
      return m ? m[1] : '其他';
    },
    // 判断是否「时间待定」。
    //
    // ⚠ 本口径必须与 scraper/field_vocab.py::is_placeholder_time **逐条一致**
    //   （单一事实源，2026-10-03 建立）。此前后端（hybrid 时间守卫）只把 00:00
    //   当占位、本前端连 08:00 一起当占位，同一份数据两套答案；实测当时
    //   「08:00 且未标注 timeUnknown」的记录为 0 条才没出事——那是运气不是设计。
    //
    // 口径（顺序即优先级）：
    //   1) timeUnknown === true  → 占位（人工标注：源页未给时刻）
    //   2) timeUnknown === false → **不占位**（人工已确认是真时间，哪怕就是 08:00）
    //   3) 未标注时，08:00 / 00:00 → 占位（解析器的填充约定，见 parsers.py「铁律占位」）
    //   4) 其余                  → 不占位
    //
    // 一致性由 tests/js/app_time_placeholder.js 锁定（两侧对同一批样例给出相同结论）。
    isTimeTBD(l) {
      if (!l) return true;
      if (l.timeUnknown === true) return true;
      if (l.timeUnknown === false) return false;
      const iso = l.lectureStart;
      if (!iso || typeof iso !== 'string') return true;
      const d = new Date(iso.replace(' ', 'T'));
      if (isNaN(d)) return true;
      const hh = d.getHours(), mm = d.getMinutes();
      return (hh === 8 || hh === 0) && mm === 0;
    },
    // 时间置信度角标（2026-10-04 方案 A 第 5 步）。
    //
    // timeConfidence ∈ {high, mid, low}（单一规格见 scraper/timeparse.py::_cross_year；
    // 解析器出口把越界取值归一为 mid，体检另有「词表越界」档兜底）。
    //   high 权威标签/同日/同年；mid 补年源可靠但非权威；**low 年份本身存疑**
    //   （crossyear-uncertain 跨年窗口无法判定、publish-unparseable 发布时间不可解析）。
    //
    // 只有 low 才打扰用户：这些记录此前被「假装确定」地按某一年份分组、排序、筛选，
    // 而年份恰恰是错的高风险区。mid 不显示——它属正常降级（general-page 等路径），
    // 全量亮起来只会变成噪音。缺字段（326 条老记录）按不显示处理。
    isDateSuspect(l) {
      return !!l && l.timeConfidence === 'low';
    },
    fmtDateTime(l) {
      if (!l) return '待定';
      const iso = l.lectureStart;
      if (!iso || typeof iso !== 'string') return '待定';
      const d = new Date(iso.replace(' ', 'T'));
      if (isNaN(d)) return '待定';
      const wk = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()];
      const hh = d.getHours(), mm = d.getMinutes();
      const clock = `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
      if (this.isTimeTBD(l)) {
        return `${d.getMonth() + 1}月${d.getDate()}日 周${wk} 时间待定`;
      }
      return `${d.getMonth() + 1}月${d.getDate()}日 周${wk} ${clock}`;
    },
    dayKey(iso) {
      if (!iso || typeof iso !== 'string') return '时间待定';
      const d = new Date(iso.replace(' ', 'T'));
      if (isNaN(d)) return '时间待定';
      const wk = ['日', '一', '二', '三', '四', '五', '六'][d.getDay()];
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} 周${wk}`;
    },
    // 状态判定（徽章展示与后续状态筛选共用）：
    //   tbd      时间待定 / 无有效开始时间
    //   upcoming 待开始（now < lectureStart）
    //   ongoing  进行中（lectureStart ≤ now ≤ lectureEnd；end 缺失或早于 start 时按开始+2小时兜底，
    //            数据中 lectureEnd 覆盖率约六成，兜底保证「进行中」始终可判定）
    //   ended    已结束（now > lectureEnd）
    //
    // 2026-10-13 用户规则（结束时刻的两种来源）：
    //   ① 写明 lectureEnd → 按实际结束时间；
    //   ② 只有 lectureStart → 默认时长 2 小时，**但若 start+2h 越过 12:00 / 18:00 /
    //      22:00（午休/晚饭/晚课截止），认为在该边界即结束**——10:30 的单始场按
    //      12:00 结束而非 12:30。边界只在 start 早于边界时生效（23:00 的夜场
    //      不适用任何边界，仍按 +2h）。
    statusOf(l) {
      if (this.isTimeTBD(l)) return 'tbd';
      const s = new Date(String(l.lectureStart || '').replace(' ', 'T')).getTime();
      if (!s || isNaN(s)) return 'tbd';
      const endRaw = l.lectureEnd ? new Date(String(l.lectureEnd).replace(' ', 'T')).getTime() : 0;
      let e;
      if (endRaw && endRaw > s) {
        e = endRaw;
      } else {
        e = s + 2 * 3600 * 1000;
        // 半日边界截断：start+2h 越过 12:00 / 18:00 / 22:00 中最早的适用边界即止
        const sd = new Date(s);
        for (const h of [12, 18, 22]) {
          const b = new Date(sd.getFullYear(), sd.getMonth(), sd.getDate(), h, 0, 0).getTime();
          if (s < b && e > b) { e = b; break; }
        }
      }
      const now = Date.now();
      if (now < s) return 'upcoming';
      return now <= e ? 'ongoing' : 'ended';
    },
    // 「待开始」判定（2026-10-16 计数行 + 待开始筛选，口径乙，用户拍板）：
    //   upcoming（明确未开场）∪ tbd 且开始时间戳未到（日期在未来/今天未过）
    // 与 filtered 活段定义（isLive）同源，只是不含 ongoing——进行中已经开场。
    // 日期已过/无日期的 tbd 不算待开始（其徽章本就是灰的，时间线早已翻篇）。
    // nowMs 由调用方传入：计数行走 Date.now()（读 tick 实时重算），filtered
    // 走函数入口固定的基准（不依赖 tick，保持排序不每秒空转的设计）。
    isNotStarted(l, nowMs) {
      const st = this.statusOf(l);
      if (st === 'upcoming') return true;
      if (st !== 'tbd') return false;
      const s = l.lectureStart || '';
      const ms = s ? new Date(String(s).replace(' ', 'T')).getTime() : 0;
      return !!ms && ms >= nowMs;
    },
    // 状态徽章：label + 样式类（vendor 预编译 CSS 无绿色系，进行中用蓝 + 呼吸点）
    // labelShort：移动端（<640px）专用的紧凑文案。长倒计时「即将开始 · 还有 17 时 44 分 59 秒」
    // 在窄屏会把元信息行撑出卡片右缘，故移动端改用「还有 17:44:59」；两套文案由 CSS 切换显示
    // （见 index.html 元信息行的 sm:hidden / hidden sm:inline），无需 JS 监听视口宽度。
    statusInfo(l) {
      const st = this.statusOf(l);
      if (st === 'tbd') return { label: '时间待定', labelShort: '时间待定', cls: 'bg-slate-100 text-slate-400', dot: false };
      if (st === 'ongoing') return { label: '进行中', labelShort: '进行中', cls: 'bg-blue-100 text-blue-700', dot: true };
      if (st === 'ended') return { label: '已结束', labelShort: '已结束', cls: 'bg-slate-100 text-slate-400', dot: false };
      // upcoming：依赖 this.tick 触发响应式（mounted 里 setInterval 每秒更新）
      // 混合判定：剩余不足 24 小时 → 秒级滚动（时分秒，实时跳变）；
      // 满 24 小时 → 按「日历日差」显示整天天数（符合「本周六/星期二间隔几天」语义）
      const s = new Date(String(l.lectureStart || '').replace(' ', 'T')).getTime();
      const diffMs = s - this.tick;
      const DAY = 86400000;
      let countdown, countdownShort;
      const p2 = n => String(n).padStart(2, '0');
      if (diffMs < DAY) {
        // 不足 24 小时：时分秒实时滚动（不足 1 小时省略「时」）
        const h = Math.floor(diffMs / 3600000);
        const m = Math.floor((diffMs % 3600000) / 60000);
        const sec = Math.floor((diffMs % 60000) / 1000);
        if (h > 0) {
          countdown = `还有 ${h} 时 ${m} 分 ${sec} 秒`;
          countdownShort = `${h}:${p2(m)}:${p2(sec)}`;
        } else {
          countdown = `还有 ${m} 分 ${sec} 秒`;
          countdownShort = `${m}:${p2(sec)}`;
        }
      } else {
        // 满 24 小时：按日历日差（自然日边界，非剩余时长取整）
        const sd = new Date(s), td = new Date(this.tick);
        const dayGap = Math.round(
          (Date.UTC(sd.getFullYear(), sd.getMonth(), sd.getDate()) -
           Date.UTC(td.getFullYear(), td.getMonth(), td.getDate())) / DAY
        );
        countdown = `还有 ${dayGap} 天`;
        countdownShort = `${dayGap} 天`;
      }
      // 颜色（2026-10-06 用户裁定）：**所有待开始的讲座统一用预告橙**，
      // 差别只在文案——7 天内带倒计时（即将开始 · 还有 N 天），7 天外只写「待开始」
      // （2026-10-16 命名对齐：与计数行/筛选片/「进行中·已结束」徽章同构，均指未开场）。
      // 此前 >7 天走灰底（bg-slate-100 text-slate-600），与「已结束/时间待定」同色系，
      // 十天后的预告看起来像过期条目（10-13 的讲座被误读成灰的）。
      // 灰色从此只属于 tbd/已结束：橙=预告、蓝=进行中、灰=已结束/待定，语义更干净。
      return diffMs <= 7 * DAY
        ? { label: '即将开始 · ' + countdown, labelShort: '还有 ' + countdownShort, cls: 'bg-orange-100 text-orange-600', dot: false }
        : { label: '待开始', labelShort: '待开始', cls: 'bg-orange-100 text-orange-600', dot: false };
    },
    truncate(s, maxLen) {
      if (!s) return '';
      s = String(s);
      return s.length <= maxLen ? s : s.slice(0, maxLen - 1) + '…';
    },
    // 安全读取 notes，过滤内部技术标记（Pattern4/OCR/置信度等不应展示给用户）
    notesText(l) {
      const n = l && l.notes;
      const INTERNAL = /Pattern4|夹逼定位|置信度|建议人工核验|OCR.*跳过|识别失败|内部|调试/i;
      if (Array.isArray(n)) {
        return n.filter(item => !INTERNAL.test(String(item))).join('；');
      }
      if (typeof n === 'string') return INTERNAL.test(n) ? '' : n.replace(/^[；;]\s*/, '');
      return '';
    },
    cleanFooter(s) {
      return String(s == null ? '' : s).replace(/(Copyright|版权所有|备案|ICP|All Rights Reserved|Reserved)[\s\S]*/i, '').trim();
    },
    // 安全渲染 abstract 字段（已将内部技术标记过滤，可安全展示）
    abstractOf(l) {
      const ab = String(this.absRaw(l) || '').trim();
      if (!ab) return '';
      // 过滤：abstract 实际是主讲人简介（解析器常见误抽）则视为无摘要
      // 检测依据：(a) 以「主讲人简介/报告人简介/嘉宾介绍」等前缀开头；
      // (b) 内容与 speakerBio 高度重叠（去空格后互相包含）。
      const bioPrefixes = ['主讲人简介', '报告人简介', '嘉宾介绍', '专家介绍',
        '报告人介绍', '主讲人介绍', '演讲者简介', 'About the Speaker'];
      for (const p of bioPrefixes) {
        if (ab.startsWith(p)) return '';
      }
      const sb = (this.bioRaw(l) || '').replace(/\s/g, '');
      if (sb.length > 10 && sb.includes(ab.replace(/\s/g, ''))) return '';
      // 过滤：abstract 是站点侧边栏「资讯及通知」模块的行政通知列表
      // （如"关于征集国家社科基金...关于申报教育部..."），不是讲座摘要。
      // 特征：含"资讯及通知"栏目标题，或 ≥2 条"关于…通知/公告/申报/征集"短语。
      if (/资讯及通知|(?:关于.{2,40}(?:通知|公告|申报|征集|转发|招标|遴选).*){2,}/.test(ab)) return '';
      // 上限 5000 字防超长脏数据（全库现有摘要最长约 4100 字，均不受影响）；
      // 展示层默认 3 行截断 + 展开按钮（见 abstractLong / expandedAbstract）
      return this.truncate(ab, 5000);
    },
    // 摘要是否被裁（需要「展开摘要」按钮）：由 DOM 实测（absOverflow）决定；
    // 字符数仅作首次测量前的兜底（130 字 ≈ 宽屏 3 行边界，纯过渡，测量完成后不再参考）
    // ⚠ 改摘要折叠行数时此阈值须同步下调，否则首次测量前会漏挂按钮（表现为「有截断却无展开按钮」）
    abstractLong(l) {
      return this.absOverflow(l) || (!this._clampMeasured && this.abstractOf(l).length > 130);
    },
    // 主讲简介全文（放宽到 2000：全库 >400 字简介有数百条，原 400 字上限会把
    // 头衔/单位/邮箱在截断处丢失；仍保留防超长脏数据底线）
    bioText(l) {
      return this.truncate(this.cleanFooter(this.bioRaw(l)), 2000);
    },
    // 简介是否需要折叠：由 DOM 实测（bioOverflow）决定；字符数仅作首次测量前的兜底
    // （200 字 ≈ 宽屏 2 行边界，纯过渡用，测量完成后不再参考）
    bioLong(l) {
      return this.bioOverflow(l) || (!this._clampMeasured && this.bioText(l).length > 200);
    },

    /* ---------- clamp 溢出实测（决定「展开简介/摘要」按钮显隐） ---------- */
    _measureClamp() {
      if (typeof document === 'undefined') return;
      // 工具栏 wrap 检测的行位源：统计段/控件块实测宽（2026-10-17）。
      // 必须进签名——行位翻转不仅由本签名已枚举的筛选维度引起，还可来自
      // ① upcomingOnly（2026-10-16 新增的筛选维度，本签名漏了它：默认态首测后
      //    点「待开始」→ 统计藏段 + 控件加 chip → 几何翻转但 sig 不变 → 跳过检测 →
      //    第二行退化成 338px 靠左不铺满，静默无报错。与 B2/P2-1 同根第三次）；
      // ② displayUpdatedAt 异步到达（last_run.json 后到，统计多一段）；
      // ③ 芯片宽受字体/内容波动。
      // 逐项枚举必有漏（已漏两次），故直接量两侧实测宽进签名——宽任何变化即重测。
      // wrap 态 ctrlW 恒等于容器宽（grow 铺满），该态无需因 chip 增减重测（结果恒 True），
      // 同行态 ctrlW=内容宽，chip 增减即变 → 会触发重测，正确。
      const statsEl0 = this.$refs.toolbarStats, ctrlEl0 = this.$refs.toolbarControls;
      const toolbarGeo = statsEl0 && ctrlEl0
        ? Math.round(statsEl0.getBoundingClientRect().width) + 'x' + Math.round(ctrlEl0.getBoundingClientRect().width)
        : '';
      // 签名守卫：只在本页内容/筛选/展开态/视口宽度变化时重测。
      // 必须要有——页面有秒级 tick（倒计时）会每秒触发 updated，否则每秒重排测量。
      // 2026-09-30 补 _longText 键数：长文本是后到的（detail 桶），到达后卡片上会**新出现**
      // 「简介/摘要」区块，若不进签名则整个测量被跳过 → 这些新元素测不到溢出 →
      // 「展开简介/展开摘要」按钮不显示，而 _clampMeasured 已为 true，字符数兜底也已失效。
      // 2026-10-02 补 speaker/showLikedOnly/searchField（修复 B2）：签名漏了三个筛选维度，
      // 而它们都会改变卡片 DOM 却不改签名。触发路径比想象中常见——app.js 的 watch 只对
      // 前三个（query/searchField/campus/college/year/showLikedOnly）置 currentPage=1，
      // **speaker 没有 watcher**（只在 pickSpeaker 里手动置 1）。于是：
      //   ① 在第 1 页点「讲者」标签 → DOM 全换、签名不变 → 跳过测量 →
      //      新卡片测不到溢出，「展开简介/摘要」按钮缺失且字符数兜底已失效；
      //   ② 二次筛选（默认就在第 1 页）同样不触发重测。
      // 2026-10-14 补 onlineOnly/farFutureOpen（修复 P2-1）：两者同样整体更换卡片 DOM
      // （onlineOnly 线上筛选、farFutureOpen 远期折叠 items 从 [] 变全部），
      // 且都在第 1 页原位切换、不触发翻页 → 签名不变 → 测量被跳过 →
      // 新卡片无溢出记录 → 「展开摘要/展开简介」按钮静默缺失，零报错。与 B2 同根。
      const sig = [this.currentPage, this.query, this.searchField, this.campus,
        this.college, this.year, this.speaker, this.showLikedOnly,
        this.all.length, this.dataStage, window.innerWidth,
        Object.keys(this._longText).length,
        JSON.stringify(this.expandedAbstract), JSON.stringify(this.expandedBio),
        this.onlineOnly, this.farFutureOpen, toolbarGeo].join('|');
      if (sig === this._clampSig) return;
      this._clampSig = sig;
      const next = {};
      document.querySelectorAll('[data-clamp-key]').forEach(el => {
        const k = el.getAttribute('data-clamp-key');
        if (!k) return;
        // 处于展开态（clamp 类已被移除）的元素测不到溢出，沿用上次判定，
        // 否则按钮会在展开瞬间消失、用户无法收起。
        // ⚠ 判定用的类名必须与 index.html 模板实际施加的一致，否则「展开摘要/展开简介」按钮不显示
        // （简介 = line-clamp-2，摘要 = line-clamp-3；2026-09-30 摘要由 6 行收回 3 行）
        const clamped = el.classList.contains('line-clamp-2') || el.classList.contains('line-clamp-3');
        if (!clamped) { if (this._clampOverflow[k]) next[k] = true; return; }
        if (el.scrollHeight - el.clientHeight > 1) next[k] = true;
      });
      this._clampMeasured = true;
      // 每次重算（而非只增不减）：字体加载完成/换行重排造成的瞬时溢出会被自动纠正，
      // 不会留下「内容其实没被裁却挂着展开按钮」的假阳性。
      if (JSON.stringify(next) !== JSON.stringify(this._clampOverflow)) this._clampOverflow = next;
      // 工具栏 wrap 实测（2026-10-17）：控件块是否被 flex-wrap 挤到统计段下方（第二行）。
      // 同行 → 不挂 is-wrap（grow:0，搜索框恒 176px，统计文字与控件之间留空间不拥挤）；
      // wrap  → 挂 is-wrap（grow:1 铺满整行，搜索框伸缩填充剩余空间）。
      // 复用 _clampSig 守卫：依赖项（dataStage/筛选态/all.length/innerWidth）全在签名里，
      // 秒级 tick 不会触发重测；partial 态统计短恒同行，full 筛选态/窄窗/五位数统计恒 wrap。
      const statsEl = this.$refs.toolbarStats, ctrlEl = this.$refs.toolbarControls;
      if (statsEl && ctrlEl) {
        const wrapped = ctrlEl.getBoundingClientRect().top - statsEl.getBoundingClientRect().top > 4;
        if (wrapped !== this.toolbarWrapped) this.toolbarWrapped = wrapped;
      }
    },
    // 渲染后测量：updated 每次都会调，用 setTimeout(0) 合并同帧内的多次调用并
    // 避开「在 updated 中同步改 state」的递归；resize 亦复用（行数随宽度变化）
    _measureSoon() {
      if (this._clampTimer) clearTimeout(this._clampTimer);
      this._clampTimer = setTimeout(() => { this._clampTimer = null; this._measureClamp(); }, 0);
    },
    /* ---------- clamp 键：必须含 lectureIndex ----------
     * 2026-10-11 修 B1：原键是 `abs|` + sourceUrl，而**一页拆出的多条记录
     * 共享同一 sourceUrl**（全库 380 条 lectureIndex 记录分布在 93 个 URL 上，
     * 其中 41 组（同日单卡 + 各自带长文本）会真的串扰）。症状：
     *   · _measureClamp 用 `next[k] = ...` 写入，**后测量的同键记录覆盖先前的**；
     *   · 结果是一张卡的溢出判定长在另一张卡上 → 「有截断却没有展开按钮」或反之，
     *     用户看不到全文，且**零报错**（静默失效）。
     * 键的四要素与 app.data.js 的 _ltKey / _mergeChunk 的 keyOf 保持同口径
     * （sourceUrl | lectureStart | title | lectureIndex 里去重后取最小稳定集），
     * 这里只用 sourceUrl + '#' + lectureIndex —— 同一 sourceUrl 下 lectureIndex
     * 已唯一，与 _ltKey 逐字一致最省心（长文本与 clamp 天然同键空间，便于调试）。
     */
    clampKey(l, kind) {
      return kind + '|' + (l.sourceUrl || '') + '#' + (l.lectureIndex != null ? l.lectureIndex : '');
    },
    absOverflow(l) { return !!this._clampOverflow[this.clampKey(l, 'abs')]; },
    bioOverflow(l) { return !!this._clampOverflow[this.clampKey(l, 'bio')]; },
    /* ---------- 展开态：同样必须含 lectureIndex（B1 同族，2026-10-11）----------
     * 与 clamp 键同源的问题：expandedAbstract / expandedBio 原以 sourceUrl 为键，
     * 而一页拆出的多条记录共享同一 sourceUrl（41 组「同日单卡 + 各自带长文本」）。
     * 症状比 clamp 串扰更严重——不是按钮显隐错乱，而是**内容错配**：
     *   · toggleAbstract(url) 用 `all.find(x => x.sourceUrl === url)` 找记录，
     *     同源多条时**只会找到第一条** → 点第 2 期的「展开摘要」拉的是第 1 期的全文；
     *   · expandedAbstract[url] 被同源多条共享 → 展开/收起状态互相串。
     * 40/41 组的两篇文章落在大不同的长文本桶，故几乎必现（实测）。
     * 修法：展开态改用与 clampKey/_ltKey 同一口径的复合键
     * （sourceUrl#lectureIndex），模板读取侧同步改。
     */
    expKey(l, kind) {
      return (l && (l.sourceUrl || '') || '') + '#' + (l && l.lectureIndex != null ? l.lectureIndex : '');
    },
    async toggleAbstract(l) {
      if (!l || !l.sourceUrl) return;
      const k = this.expKey(l, 'abs');
      // 展开前按需拉**该条**的全文（按 _ltKey 定位桶，不依赖 sourceUrl 单键）
      await this._ensureLongText(l);
      this.expandedAbstract = { ...this.expandedAbstract, [k]: !this.expandedAbstract[k] };
    },
    async toggleBio(l) {
      if (!l || !l.sourceUrl) return;
      const k = this.expKey(l, 'bio');
      await this._ensureLongText(l);
      this.expandedBio = { ...this.expandedBio, [k]: !this.expandedBio[k] };
    },
    // 安全链接：仅放行 http/https，阻断 javascript:/data: 等可执行协议，防止 XSS
    safeUrl(u) {
      if (!u) return '#';
      const s = String(u).trim();
      return /^https?:\/\//i.test(s) ? s : '#';
    },
    // 判断讲座题目(topic)里是否已经包含当前分期的期/讲/场号，避免前端重复追加「（第X期）」
    // 注意：传入的是 topic（单场题目），而非 title（系列名）；故命名为 topicHasSession。
    // 支持：第3讲 / 第3期 / 讲座三 / 三讲 / 3讲 / 第III期 等
    topicHasSession(title, idx) {
      if (!title || !idx) return false;
      const n = parseInt(idx, 10);
      if (!n || n <= 0) return false;
      const arabic = String(n);
      // 中文数字 1-99
      const units = ['', '一', '二', '三', '四', '五', '六', '七', '八', '九'];
      let chinese;
      if (n <= 10) {
        chinese = n === 10 ? '十' : units[n];
      } else if (n < 20) {
        chinese = '十' + units[n % 10];
      } else if (n <= 99) {
        chinese = units[Math.floor(n / 10)] + '十' + units[n % 10];
      } else {
        // n > 99：中文数字表只覆盖到 99，units[Math.floor(100/10)] 即 units[10]
        // 为 undefined → 旧实现会拼出 'undefined十undefined' 这种永不匹配的正则
        // （静默失效，题目被重复追加「（第N期）」）。
        //
        // ⚠ 哨兵**不能用空串**：那样 new RegExp('讲座\\s*') 会匹配任何含「讲座」的
        //   标题（如「本报告主要内容是关于讲座的总结」）→ 返回 true → 该显示期数
        //   却被静默吞掉，把一个静默失效换成另一个静默失效（2026-10-02 实测确认）。
        // \uFFFF 是永不可能出现在标题里的字符，用作「永不匹配」哨兵。
        chinese = '\\uFFFF';
      }
      const t = String(title);
      const patterns = [
        new RegExp('第\\s*' + arabic + '\\s*[场期讲]'),
        new RegExp('第\\s*' + chinese + '\\s*[场期讲]'),
        new RegExp('讲座\\s*' + arabic),
        new RegExp('讲座\\s*' + chinese),
        new RegExp(arabic + '\\s*讲'),
        new RegExp(chinese + '\\s*讲'),
      ];
      return patterns.some(re => re.test(t));
    },
});
