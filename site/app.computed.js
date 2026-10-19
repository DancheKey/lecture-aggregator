// 派生计算（原 app.js 的 computed）。

// 远期折叠（2026-10-13 用户拍板方案 B）：待开始且距今超过 FAR_FUTURE_DAYS 天的讲座，
// 在列表「未来区」末尾折成一条提示条，点击就地展开（展开态存 localStorage）。
//
// 动机：全库 99.7% 是历史归档，纯日期倒序会让最远的未来讲座霸占首屏
// （10-31 的讲座曾排在 10-13 前面）。与 24h 置顶（filtered 两段式排序）配套：
// 排序管「谁在前」，折叠管「远的收起来」。
//
// ⚠ 阈值语义线：7 天同时是 app.display.js::statusInfo 的「倒计时 / 只写待开始」
//   分界（2026-10-06 用户裁定），两边是同一条产品线，改这里必须同步那边。
const FAR_FUTURE_DAYS = 7;
// 折叠触发的最小条数（2026-10-13 用户调整）：≥3 条远期才折成提示条；
// 只有 1-2 条时直接渲染——量少不构成霸屏，折叠反而多一次点击。
const FAR_FOLD_MIN = 3;
// 远期折叠组在 pagedGroups 里的 key。真实日期组形如「2026-10-13 周二」，
// 不会与此冲突；模板用它区分「折叠条」与「日期头」两种组头。
const FAR_FOLD_KEY = '__far_future__';
// 合并卡阈值（2026-09-30 用户定；2026-10-19 从 units() 里抽出为模块常量）：
// **同一源页 + 同一天** ≥3 场才折成一张合并卡。抽出来的原因：「只看已赞」的
// 页面级兜底匹配也必须用这个口径——只有合并卡才是「整页一个赞」，也只有它会把
// 计数键写成无后缀的 sourceUrl（units() 的 forum 分支 ck: u）。两处若各写一份
// 数字，就会出现「卡片不高亮、筛选却筛出来了」的分叉（2026-10-19 用户实测）。
const FORUM_MIN = 3;
// 合并/分组的键：源页 + 日期。units() 与 filtered() 共用，保证口径逐字一致。
const mergedDayKey = l => (l.sourceUrl || '') + '|' + (l.lectureStart || '').slice(0, 10);

const APP_COMPUTED = {
    totalCount() { return this.all.length; },

    // 「更新于」显示的日期（2026-09-10）：优先用 last_run.json 的最近运行时间——它由 CI
    // 每次运行刷新（空增量日也刷新），因此即便今天没有新讲座，页面也照常显示今天，
    // 不会让访客误以为站点停更；拿不到该文件时回退到 updatedAt（数据最后变化时间）。
    displayUpdatedAt() {
      return this.lastRunAt || this.updatedAt || '';
    },

    // 来源通知总数：按**唯一来源 URL 集合**计（2026-10-08 口径变更，与
    // generate_frontend_data.py::unique_source_urls 逐字对齐，两侧必须同口径，
    // 否则首页与统计页会显示两个不同的「覆盖 N 条来源通知」）。
    //
    // 为何不用 sourceCount 逐条累加：一页多场拆分后，每场都把同一张转发页登记进
    // 自己的 sources（xz/20240116/127 拆 3 场，各挂 swc/.../0101/55），而转发页
    // 本身没有独立记录入库 —— 逐条累加等于把 1 张转发页数了 3 次。
    //
    // ⚠ 只用于这个数字。学院/校区筛选计数仍按讲座计、不去重（用户按「汕尾校区」
    // 筛就该看到那 3 场讲座）。两处口径不可混用。
    sourceNoticeCount() {
      const seen = new Set();
      for (const l of this.all) {
        const u = (l.sourceUrl || '').replace(/\/+$/, '');
        if (u) seen.add(u);
        for (const s of (l.sources || [])) {
          const su = ((s || {}).sourceUrl || '').replace(/\/+$/, '');
          if (su) seen.add(su);
        }
      }
      return seen.size;
    },

    // 待开始讲座数（2026-10-16 计数行，口径乙见 app.display.js::isNotStarted）。
    // 恒按全库 all 计、不受其他筛选影响（叠加语义：N 与结果数是两个数字，
    // 后者看「当前筛选 X 场」行）。读一次 tick 建立响应式依赖（秒级心跳），
    // 讲座开场瞬间 −1 实时跳变；O(n) 纯计数不碰排序，filtered 的固定 nowMs
    // 设计不受影响。模板侧 v-if 控制：0 条时整行不显示（用户 2026-10-16 补充）。
    upcomingCount() {
      this.tick;   // 仅为建立响应式依赖（秒级心跳驱动重算）
      const now = Date.now();
      let n = 0;
      for (const l of this.all) if (this.isNotStarted(l, now)) n++;
      return n;
    },

    // 数据中出现过的年份（倒序，字符串便于与下拉值比较）
    years() {
      const set = new Set();
      this.all.forEach(l => { const y = this.yearOf(l); if (y) set.add(y); });
      // 「其他」（无年份）置底，与后端 generate 及 stats.js 口径一致（2026-10-14）
      return Array.from(set).sort((a, b) => {
        if (a === '其他') return 1;
        if (b === '其他') return -1;
        return b.localeCompare(a);
      });
    },

    // 去重学院列表，按讲座数倒序，便于高频学院靠前。
    // 计数口径与筛选一致：合并讲座按「主学院 ∪ 来源单位」展开，每个相关单位各计一次。
    colleges() {
      const cnt = {};
      this.all.forEach(l => {
        const cs = new Set([l.college, ...(l.sources || []).map(s => s.college)].filter(Boolean));
        cs.forEach(c => { cnt[c] = (cnt[c] || 0) + 1; });
      });
      return Object.keys(cnt).sort((a, b) => cnt[b] - cnt[a]);
    },

    // 搜索框占位提示：随所选维度变化（移动端使用单独短 placeholder，见 index.html）
    searchPlaceholder() {
      return {
        '': '搜索讲座 / 主讲人 / 地点…',
          college: '搜索单位…',
          location: '搜索地点…',
          topic: '搜索题目 / 主讲…',
          abstract: '搜索摘要…'
        }[this.searchField] || '搜索…';
    },

    // 是否有任何筛选条件激活（控制"清除"按钮显示）
    hasActiveFilter() {
      return !!(this.campus || this.college || this.year || this.query || this.searchField || this.showLikedOnly || this.speaker || this.onlineOnly || this.upcomingOnly);
    },

    // 长文本（简介/摘要）不再阻塞搜索（2026-09-30 用户定）：
    // 原 searchPending 会在「搜索可能命中长文本且 16 桶未就绪」时挡住结果并弹等待提示；
    // 现改为「首屏即后台静默预取」（见 app.data._loadStaticLatest）＋「先出结果、到齐静默补全」：
    // 未就绪时 filtered 照样用已到数据出结果，桶到达后 this._longText 变更触发响应式自动补全，
    // 故不再需要任何等待/提示。该计算属性已删除。

    // 复合筛选 + 按讲座时间倒序
    filtered() {
      // 比较基准在函数入口一次取定（预计算状态，本次调用内不变）：排序注释里
      // 讲的「不依赖 tick / 不每秒空转」约束针对的就是它，待开始筛选同源复用。
      const nowMs = Date.now();
      const q = this.query.trim().toLowerCase();
      // 只看已赞（2026-10-19 B3 + 同日二次修正）：点赞写入的是计数键 ck（多场拆分
      // = url~N），而合并卡的赞记在**页面级**无后缀 sourceUrl 上（units() 的 forum
      // 分支 ck: u）。两者任一命中即算已赞，**不能**写成 `hasLiked(l.ck || l.sourceUrl)`
      // 二选一：多场记录的 ck 恒非空，永远走不到回退分支，合并卡的赞就永远筛不出来。
      //
      // ⚠ 页面级兜底**必须限定在合并组内**：非合并的多场页（如研究生院勤勤论坛
      // 第 1/2/3 期分在 10-13、10-14 两天，同日最多 2 场）逐场是独立单卡，页面上
      // 没有任何「整页赞」入口。若按 sourceUrl 无条件兜底，历史遗留的页面级键会
      // 把整页 3 场全部筛出来——用户只赞了第 3 期，却看到 3 条（2026-10-19 实测）。
      // 判定口径与 units() 的 FORUM_MIN 同源。
      const likedOnly = this.showLikedOnly;
      let mergedDays = null;   // Set(gk)：会渲染成合并卡的分组（惰性构建，只在已赞筛选时算）
      const inMergedDay = l => {
        if (!mergedDays) {
          const cnt = new Map();
          this.all.forEach(x => {
            if (!x || !x.sourceUrl) return;
            const k = mergedDayKey(x);
            cnt.set(k, (cnt.get(k) || 0) + 1);
          });
          mergedDays = new Set();
          cnt.forEach((n, k) => { if (n >= FORUM_MIN) mergedDays.add(k); });
        }
        return mergedDays.has(mergedDayKey(l));
      };
      const list = this.all.filter(l => {
        if (likedOnly) {
          const byCk = this.hasLiked(l.ck);
          const byPage = !byCk && this.hasLiked(l.sourceUrl) && inMergedDay(l);
          if (!byCk && !byPage) return false;
        }
        // 线上讲座筛选（2026-10-13）：点卡片「# 线上」标签进入；isOnline 为派生
        // 字段（location 写的是参会方式而非实体地点），与校区筛选可叠加。
        if (this.onlineOnly && !l.isOnline) return false;
        // 待开始筛选（2026-10-16 口径乙）：upcoming + 开始时间未到的时间待定；
        // 不含进行中（已开场）、不含日期已过/无日期的 tbd。与其他筛选叠加。
        if (this.upcomingOnly && !this.isNotStarted(l, nowMs)) return false;
        // 合并讲座可能跨校区/学院，任一来源匹配即保留
        if (this.campus) {
          const campuses = new Set([l.campus, ...(l.sources || []).map(s => s.campus)].filter(Boolean));
          // actualCampus（2026-10-13）：校级讲座按 location 推导的实际举办校区，
          // 归口仍算校级，但点具体校区时应能筛到（如研究生院讲座在大学城办）。
          // 「校级」筛选项本身不受影响——校级记录的 campus 仍是「校级」。
          if (!campuses.has(this.campus) && l.actualCampus !== this.campus) return false;
        }
        if (this.college) {
          const colleges = new Set([l.college, ...(l.sources || []).map(s => s.college)].filter(Boolean));
          if (!colleges.has(this.college)) return false;
        }
        if (this.year && this.yearOf(l) !== this.year) return false;
        if (this.speaker) {
          // 讲者聚合：按归一化键匹配（多人讲座任一键命中即保留）
          const keys = l.speakerKeys || [];
          if (!keys.includes(this.speaker)) return false;
        }
        if (q) {
          let hay;
          if (this.searchField === 'location') {
            // 地点：仅按讲座地点匹配（多来源取各自地点）；空地点记为「待公布」，可筛
            hay = [l.location || '待公布', ...(l.sources || []).map(s => s.location)]
              .filter(Boolean).join(' ').toLowerCase();
          } else if (this.searchField === 'topic') {
            // 题目：标题 + 题目字段 + 主讲人（与占位提示「搜索题目 / 主讲…」对齐）
            hay = [l.title, l.topic, l.listTitle, l.speaker].filter(Boolean).join(' ').toLowerCase();
          } else if (this.searchField === 'abstract') {
            // 摘要：仅按讲座摘要匹配（长文本已从主分片剥离，走 _longText 旁路）
            hay = [this.absRaw(l)].filter(Boolean).join(' ').toLowerCase();
          } else if (this.searchField === 'college') {
            // 单位：仅按主办单位匹配，避免场地在某单位的讲座被误配
            hay = [l.college, ...(l.sources || []).map(s => s.college)]
              .filter(Boolean).join(' ').toLowerCase();
          } else {
            // 全部（默认）：在所有常见字段中匹配
            hay = [l.title, l.topic, l.speaker, l.speakerAffiliation,
              this.bioRaw(l), l.listTitle, l.college, l.location, l.campus, l.organizer,
              this.absRaw(l)]
              .filter(Boolean).join(' ').toLowerCase();
          }
          if (!hay.includes(q)) return false;
        }
        return true;
      });
      // 两段式排序（2026-10-13 用户拍板方案 B 第一层 + 用户后续三条规则）：
      // **活段（待开始/进行中）按 lectureStart 升序**排在前，已结束/待定段维持
      // 日期倒序在后。
      //
      // 动机与规则来源：
      //   ① 站点是「历史归档 + 少量预告」，纯倒序让最远的未来讲座霸占首屏
      //      （10-31 排 10-13 前面），而访客来首屏几乎只为看「最近有什么要开」；
      //   ② 24h 内置顶 = 活段升序的自然结果，「按最先开始顺序」；
      //   ③ 活段**同日**也必须按时刻升序（2026-10-13 用户截图反馈：10-14 的
      //      10:30 曾排在 15:00 后面——同日掉了「系列编号倒序」的旧分支，
      //      那是为已结束段时间线设计的，活段不适用）；
      //   ④ 「是否已结束」的判定走 statusOf（半日边界 12:00/18:00/22:00 截断），
      //      不用裸 start>=now——否则 16:00 开始、18:00 结束的讲座在 17:00
      //      会被算成已结束排到后面，与「进行中」徽章自相矛盾；
      //   ⑤ 当天组内时刻待定（tbd）排最后：占位 00:00/08:00 不是真实开场时刻，
      //     不占时间线最前（2026-10-13 用户截图反馈，见下方 tbdA/tbdB 分支）。
      //
      // 基准取函数入口固定的 nowMs（已在 filtered 顶部声明）：比较函数在毫秒级
      // 跳变下会变成非全序；且不依赖 this.tick——排序是 computed，依赖 tick 会
      // 每秒重排 → 翻页内容抖动、3800 条排序每秒空转；交互（筛选/翻页/数据到达）
      // 自然触发重算即可，「开始→进行中」的实时迁移由徽章（statusInfo 读 tick）负责。
      const isLive = new Map();
      list.forEach(l => {
        const st = this.statusOf(l);
        const s = l.lectureStart || '';
        // tbd（时间待定）保持原口径：占位日期在未来 → 活段；已过 → 死段
        // （其徽章本就是灰的「时间待定」，不该混进「即将开始」的时间线）
        isLive.set(l, st === 'upcoming' || st === 'ongoing'
          || (st === 'tbd' && !!s && new Date(s.replace(' ', 'T')).getTime() >= nowMs));
      });
      list.sort((a, b) => {
        const ta = a.lectureStart || '', tb = b.lectureStart || '';
        if (!ta && !tb) return 0;
        if (!ta) return 1;
        if (!tb) return -1;
        const fa = isLive.get(a), fb = isLive.get(b);
        if (fa !== fb) return fa ? -1 : 1;   // 活段在前
        const da = ta.slice(0, 10), db = tb.slice(0, 10);
        if (da !== db) return fa ? da.localeCompare(db) : db.localeCompare(da);  // 活段升序 / 已结束倒序
        // 当天组内：时刻待定（statusOf=tbd：占位 00:00/08:00 或 timeUnknown 标志）
        // 一律排在该日有时刻记录之后（2026-10-13 用户截图反馈：10-14 组里
        // 「时间待定」的占位 00:00 在活段升序中天然排最前，把物理学院 09:00
        // 挤到了后面——占位时刻不是真实开场时间，不该占用时间线最前位置）。
        // 活段（升序）与已结束段（倒序）都显式置后：倒序下 00:00 占位虽恰好
        // 落在末尾，但 08:00 占位会排到 07:00 真实记录之前，靠巧合不如写死。
        const tbdA = this.isTimeTBD(a), tbdB = this.isTimeTBD(b);
        if (tbdA !== tbdB) return tbdA ? 1 : -1;
        // 活段同日：按完整时刻升序（最先开始的排最前）
        if (fa) return ta.localeCompare(tb);
        // 已结束段同日：同系列（砺儒讲坛第X讲等）按编号倒序，让133讲在132讲之上
        // 中文数字（第三讲、第十二期）也支持，映射到 int 后比较
        const _cn2num = (s) => {
          const map = {零:0,一:1,二:2,三:3,四:4,五:5,六:6,七:7,八:8,九:9,十:10,百:100};
          let r = 0, acc = 0;
          for (const ch of s) {
            const v = map[ch];
            if (v === undefined) return NaN;
            if (v >= 10) { acc = acc || 1; r += acc * v; acc = 0; }
            else acc = acc * 10 + v;
          }
          return r + (acc || 0);
        };
        const seriesNo = (title) => {
          const t = String(title || '');
          let m = t.match(/第(\d+)(?:讲|场|期|届)/);
          if (m) return parseInt(m[1], 10);
          m = t.match(/第([一二三四五六七八九十百零]+)(?:讲|场|期|届)/);
          return m ? _cn2num(m[1]) : 0;
        };
        const sa = seriesNo(a.title), sb = seriesNo(b.title);
        if (sa && sb && sa !== sb) return sb - sa;
        // 同页拆分的多期讲座（同 sourceUrl，lectureIndex 含 0）按期数倒序，让第1期在最下面
        const sameSource = a.sourceUrl && a.sourceUrl === b.sourceUrl;
        if (sameSource && typeof a.lectureIndex === 'number' && typeof b.lectureIndex === 'number' && a.lectureIndex !== b.lectureIndex) {
          return b.lectureIndex - a.lectureIndex;
        }
        // 否则按完整时间倒序
        return tb.localeCompare(ta);
      });
      return list;
    },

    // 渲染单元（2026-09-30 修订）：折叠粒度 = 「同源页 + 同一天」。
    // 仅当**同一天**的场次数 ≥ 阈值时才合并成一张论坛卡，其余（含跨天但每天只有 1~2 场
    // 的系列）逐场单卡展示。背景：旧规则按「同源页总场次 ≥3」折叠，把「跨 3 天、每天
    // 仅 1~2 场」的系列也整块折起（如美术学院 4 场分 3 天），反而藏掉了用户想看的逐场
    // 时间/地点。保持 filtered 的既有排序（论坛卡落在其所在天的位置）。
    units() {
      const MIN = FORUM_MIN; // 同日折叠阈值（模块常量，filtered 的已赞兜底同源）
      const groupKey = mergedDayKey;
      // 2026-10-02 修复 B1：折叠组同时需要两个计数——修复前只有一个且取错了来源。
      //
      //   filteredCounts：filtered（已应用筛选/搜索）里的命中场次 → 决定是否折叠；
      //   allCounts：all（未筛选全集）里的真实场次 → 卡片上的「同日 N 场」。
      //
      // 修复前 `total: recs.length` 而 recs 来自 filtered，导致 total ≡ recs.length，
      // 下游两处恒为死条件：
      //   ① index.html 的「⚠ 当前筛选命中 X / Y 场」——`recs.length < total` 永不成立；
      //   ② app.display.isForumOpen 的默认值 `recs.length < total` 永为 false，
      //      「筛选命中子集时自动展开场次清单」整条策略静默失效。
      const filteredCounts = new Map();
      const allCounts = new Map();
      this.filtered.forEach(l => {
        if (!l.sourceUrl) return;
        const k = groupKey(l);
        filteredCounts.set(k, (filteredCounts.get(k) || 0) + 1);
      });
      this.all.forEach(l => {
        if (!l.sourceUrl) return;
        const k = groupKey(l);
        allCounts.set(k, (allCounts.get(k) || 0) + 1);
      });
      const units = [];
      const seen = new Set();
      let solo = 0;
      this.filtered.forEach(l => {
        const u = l.sourceUrl || '';
        if (!u) {
          // sourceUrl 为空时 ck 也必为空（生成器 count_key 只由 sourceUrl 派生，
          // 实测 3835 条无此情况）。回退只做防御：保证 ck 是字符串而非
          // undefined——模板 {{ l.ck }} 与 toggleLike 的 if (!url) return
          // 都对 undefined 更不友好。⚠ 2026-10-18 复查：此前这里写的是
          // l.ck || l.sourceUrl || ''，但本分支 l.sourceUrl 恒为空串，
          // 该回退是死代码，已删；真正可达的回退在下方有 URL 的
          // single 分支（ck: l.ck || u）。
          units.push({ type: 'single', recs: [l], key: 'x|' + (solo++), ck: l.ck || '' });
          return;
        }
        const k = groupKey(l);
        if ((filteredCounts.get(k) || 0) >= MIN) {
          if (seen.has(k)) return;
          seen.add(k);
          const recs = this.filtered.filter(x => groupKey(x) === k);
          units.push({
            type: 'forum', url: u, recs, head: recs[0],
            // 计数键（2026-10-18）：合并卡片单元代表「整张通知页」，其点赞/访问
            // 记在无后缀的 sourceUrl 上（页面级），与展开后每场的 ck（讲座级）
            // 互不干扰——点整页赞不会给某一场 +1，反之亦然。
            ck: u,
            // 全集场次（未经筛选）：与 recs.length 在有筛选时才不同。
            // 数据尚未全量加载完时 all 少于 filtered 是可能的，取二者较大值兜底，
            // 保证 total >= recs.length 这个不变式（下游依赖它做「部分命中」判定）。
            total: Math.max(allCounts.get(k) || 0, recs.length),
            dateLabel: (l.lectureStart || '').slice(0, 10),
            key: 'f|' + k,
          });
          return;
        }
        units.push({
          type: 'single', recs: [l],
          key: 's|' + (l.lectureIndex ?? 'x') + '|' + u,
          ck: l.ck || u,
        });
      });
      return units;
    },

    // 远期折叠层（2026-10-13 方案 B 第二层）：把「待开始且 > FAR_FUTURE_DAYS 天」的
    // 渲染单元收进一个 far-fold 容器，容器占据原第一个被折叠单元的位置（未来区末尾），
    // 后续单元依次前移——units 数组总长度不变（fold 恒为 1 个单元），分页计数稳定。
    // 仅当远期单元数 ≥ FAR_FOLD_MIN（3，2026-10-13 用户调整）才折叠；1-2 条走 flush 的
    // else 分支原样吐出，顺序与不折叠路径完全一致。
    //
    // 为何用「start > now + 7d」而不再判未来段：filtered 两段式后 >7 天的必然全在
    // 未来区末尾且彼此相邻，该判定天然只在未来区命中；已结束段 start<now<cutoff
    // 不可能命中，无需额外分区判断。
    //
    // 容器 recs 存的是 units（forum/single 原样），展开时 pagedGroups 直接把它们
    // 当作普通 items 走既有渲染分支（见 pagedGroups），零模板重复。
    foldedUnits() {
      // 待开始筛选激活（2026-10-16 用户拍板）：远期不折叠，全部平铺——主动点
      // 「待开始」就是为了看全未来讲座，折叠条违背本意；取消筛选后恢复折叠。
      if (this.upcomingOnly) return this.units;
      const cutoff = Date.now() + FAR_FUTURE_DAYS * 86400000;
      const out = [];
      let far = [];
      const flush = () => {
        if (far.length >= FAR_FOLD_MIN) {
          out.push({ type: 'far-fold', recs: far, key: FAR_FOLD_KEY });
        } else {
          far.forEach(u => out.push(u));
        }
        far = [];
      };
      this.units.forEach(u => {
        const s = (u.type === 'forum' ? u.head.lectureStart : u.recs[0].lectureStart) || '';
        const ms = s ? new Date(s.replace(' ', 'T')).getTime() : 0;
        if (ms && ms > cutoff) {
          far.push(u);
        } else {
          flush();
          out.push(u);
        }
      });
      flush();
      return out;
    },

    // 总页数（按渲染单元计：论坛折成 1 个卡片位；远期折叠容器同样占 1 位，
    // 展开/收起不改变 foldedUnits 长度 → 分页不错乱）
    totalPages() {
      return Math.max(1, Math.ceil(this.foldedUnits.length / this.pageSize));
    },

    // 智能分页页码：当前页前后各 2 页 + 首尾，省略号占位（边界平滑过渡）
    pageNumbers() {
      const total = this.totalPages;
      const cur = this.currentPage;
      if (total <= 9) return Array.from({ length: total }, (_, i) => i + 1);
      const pages = [];
      const left = Math.max(1, cur - 2);
      const right = Math.min(total, cur + 2);
      if (left > 2) {
        pages.push(1, '...');
      } else {
        for (let i = 1; i < left; i++) pages.push(i);
      }
      for (let i = left; i <= right; i++) pages.push(i);
      if (right < total - 1) {
        pages.push('...', total);
      } else {
        for (let i = right + 1; i <= total; i++) pages.push(i);
      }
      return pages;
    },

    // 当前页对应的单元列表（已筛选 + 两段式排序 + 论坛折叠 + 远期折叠）
    pagedUnits() {
      const start = (this.currentPage - 1) * this.pageSize;
      return this.foldedUnits.slice(start, start + this.pageSize);
    },

    // 当前页再按天分组（单元的日：论坛取最新场次日、单条取本场日），保持时间线视觉风格
    // 当前页再按天分组（单元的日：论坛取最新场次日、单条取本场日），保持时间线视觉风格。
    //
    // 2026-10-13 三处修订（方案 B + 用户截图反馈）：
    // ① 组间排序从「日期倒序」改为「跟随 pagedUnits 顺序」——否则 filtered 里
    //    已压到未来区末尾的远期组（如 10-31）会在分组后凭日期最大又弹回首屏，
    //    两段式排序被层间抵消（DeepSeek 方案 A 的同款漏洞）。
    // ② **同日混合拆组**（用户 12:33 反馈）：「已结束」的场次不得留在当天日期组里，
    //    拆成独立组（键加 |e 后缀）排到该日期活组之后；仅当该日期同时有活单元时
    //    才给死组头加「· 已结束」标注（纯已结束日不加，避免满屏噪音）。
    //    用户原话：「只要是显示已结束的，就要把它放到后面去」。
    // ③ far-fold 容器自成一组（key=FAR_FOLD_KEY）：折叠态 items 为空（只渲染
    //    提示条），展开态 items = 被折的 units（走与普通组完全相同的渲染分支）；
    //    count 供提示条显示「N 场」。它不并入任何天组——成员跨多天。
    //
    // ⚠ 论坛卡的状态取 head（filtered 序第一场）：同源同日 ≥3 场才成论坛卡，
    //   同日跨「活/死」两态的极端情形下整卡随 head 归类（内部清单自带时间，
    //   不影响可读性）；此情形全库为 0，真出现时再细化。
    pagedGroups() {
      const groups = {};
      const firstIdx = new Map();
      const unitDate = u => (u.type === 'forum' ? u.head.lectureStart : u.recs[0].lectureStart) || '';
      // 第一遍：标记哪些日期含「活」单元（未结束/进行中；far-fold 成员全是远期活场）
      const liveDates = new Set();
      this.pagedUnits.forEach(u => {
        if (u.type === 'far-fold') return;
        const d = this.dayKey(unitDate(u));
        if (d !== '时间待定' && this.statusOf(u.type === 'forum' ? u.head : u.recs[0]) !== 'ended') {
          liveDates.add(d);
        }
      });
      // 第二遍：分组（死组键加 |e 后缀与同日活组区分；组间顺序=首次出现位置）
      this.pagedUnits.forEach((u, i) => {
        let k, label;
        if (u.type === 'far-fold') {
          k = FAR_FOLD_KEY;
          label = k;
        } else {
          const d = this.dayKey(unitDate(u));
          const ended = d !== '时间待定'
            && this.statusOf(u.type === 'forum' ? u.head : u.recs[0]) === 'ended';
          k = ended ? d + '|e' : d;
          label = ended && liveDates.has(d) ? d + ' · 已结束' : d;
        }
        if (!groups[k]) {
          groups[k] = { label, items: [] };
          firstIdx.set(k, i);
        }
        if (u.type === 'far-fold') {
          // 容器只入组一次（recs 里的单元不进组，展开时整体替换 items，见下）
          if (groups[k].items.length === 0) groups[k].items.push(u);
        } else {
          groups[k].items.push(u);
        }
      });
      const keys = Object.keys(groups).sort((a, b) => {
        if (a === '时间待定' || a === '时间待定|e') return 1;
        if (b === '时间待定' || b === '时间待定|e') return -1;
        return (firstIdx.get(a) ?? 0) - (firstIdx.get(b) ?? 0);
      });
      return keys.map(k => {
        if (k === FAR_FOLD_KEY) {
          const fold = groups[k].items[0];
          return {
            key: k,
            label: k,
            // 2026-10-19（B6）：文案是「N 场」，必须是**场次数**而非渲染单元数。
            // 合并卡一张代表 total 场（同页同日 ≥3 场），按 recs.length 会把 5 场算成 1。
            count: fold.recs.reduce((n, u) => n + (u.type === 'forum' ? (u.total || u.recs.length) : 1), 0),
            // 展开态：items 直接是被折的 units——模板的 forum/single 分支原样复用；
            // 折叠态：items 为空，只留提示条（fold 单元本身不渲染卡片）。
            items: this.farFutureOpen ? fold.recs : [],
          };
        }
        return { key: k, label: groups[k].label, items: groups[k].items };
      });
    },
};
