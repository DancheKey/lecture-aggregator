/* 木铎金声 · 日历导出（2026-10-20 批次 B）
 *
 * 职责：把一场（或一组）讲座拼成 RFC 5545 的 .ics 文本，触发浏览器下载。
 * 纯前端、零后端、零账户——.ics 就是一个文本文件，系统日历自己认得。
 *
 * 三条容易踩的硬约束（都不是「写对格式」那么简单）：
 *
 * ① **时区**：`lectureStart` 是「北京时间」的字面量（'2026-10-31 14:00:00'），
 *    不是 ISO 带偏移的字符串。**绝对不能**写成 `new Date('2026-10-31 14:00:00')`
 *    —— 那会按**浏览器本地时区**解析，用户在东京看到的就是 15:00。
 *    这里手工按 UTC+8 折算成绝对时刻，再输出成 UTC（Z 结尾），
 *    任何时区的日历打开都是同一瞬间。
 *
 * ② **行折叠**：RFC 5545 要求单行不超过 75 字节，超了要用「CRLF + 空格」续行。
 *    中文一个字 3 字节，一条摘要很容易超。按**字节**折而不是按字符折，
 *    否则切在汉字中间会得到乱码。
 *
 * ③ **微信内置浏览器**：点下载经常毫无反应（X5 内核的下载管理不认 text/calendar）。
 *    首次点击时提示「用浏览器打开」，但不阻断下载——有些机型其实能成功。
 */
Object.assign(APP_METHODS, {
  /* ---------- 时间换算 ---------- */

  // 北京时间字面量 → UTC 毫秒。非法/空 → null。
  // 用 Date.UTC 而不是 Date 构造器：后者受浏览器时区影响（见文件头 ①）。
  _calUtcMs(s) {
    const m = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?/.exec(String(s || ''));
    if (!m) return null;
    const ms = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4] - 8, +m[5], +(m[6] || 0));
    return Number.isFinite(ms) ? ms : null;
  },

  // 毫秒 → iCalendar 的 UTC 时间戳 'YYYYMMDDTHHMMSSZ'
  _calStamp(ms) {
    const d = new Date(ms);
    const p = n => String(n).padStart(2, '0');
    return `${d.getUTCFullYear()}${p(d.getUTCMonth() + 1)}${p(d.getUTCDate())}`
      + `T${p(d.getUTCHours())}${p(d.getUTCMinutes())}${p(d.getUTCSeconds())}Z`;
  },

  // 讲座的开始/结束时刻。缺结束时间时按 +1 小时（数据里只有 49.7% 有 lectureEnd）。
  _calSpan(l) {
    const start = this._calUtcMs(l && l.lectureStart);
    if (start === null) return null;
    let end = this._calUtcMs(l && l.lectureEnd);
    if (end === null || end <= start) end = start + 3600 * 1000;
    return { start, end };
  },

  /* ---------- 文本处理 ---------- */

  // RFC 5545 转义：反斜杠、分号、逗号要转义，换行写成字面量 \n
  _calEsc(s) {
    return String(s == null ? '' : s)
      .replace(/\\/g, '\\\\')
      .replace(/;/g, '\\;')
      .replace(/,/g, '\\,')
      .replace(/\r\n|\r|\n/g, '\\n');
  },

  // 按 UTF-8 字节折行（≤75 字节/行，续行以单个空格开头）
  _calFold(line) {
    const enc = new TextEncoder();
    if (enc.encode(line).length <= 75) return line;
    const parts = [];
    let cur = '';
    let bytes = 0;
    // 用 for...of 按码点迭代：emoji 等代理对不会被切成两半
    for (const ch of line) {
      const n = enc.encode(ch).length;
      if (bytes + n > 74) { parts.push(cur); cur = ' '; bytes = 1; }
      cur += ch;
      bytes += n;
    }
    parts.push(cur);
    return parts.join('\r\n');
  },

  /* ---------- 事件拼装 ---------- */

  // 单场 → VEVENT 行数组（未折行）。dtstamp 由调用方传入，保证同一次导出内一致。
  _calEvent(l, dtstamp) {
    const span = this._calSpan(l);
    if (!span) return null;
    // 标题去重（2026-10-20 与 Python 端 _feed_title 对齐，修「题目｜题目」重复）：
    // title 常已内嵌 topic（全库 483 条 / 12.6%，如「学术讲座：早期胚胎发育和体轴建立」），
    // 只判 !== 会拼成「…题目｜题目」，而 feed.xml/feed.ics 不会——两处导出行为分叉。
    // 判据与 generate_frontend_data.py::_feed_title 逐条一致：
    // topic 为空 / 相等 / 已包含于 title 时都不追加。
    const t = String(l.title || '').trim();
    const topic = String(l.topic || '').trim();
    const title = t
      ? ((topic && topic !== t && !t.includes(topic)) ? `${t}｜${topic}` : t)
      : (topic || '学术讲座');
    // 主讲行（与 Python 端 _feed_desc 对齐）：只有姓名非空才输出，单位有姓名时才加括号。
    // 旧写法 [speaker, aff].filter(Boolean).join('（')+'）' 在「只有单位没姓名」时
    // 输出成「华南师范大学）」（缺左括号）；_feed_desc 的口径是无姓名则整行不输出。
    const who = l.speaker
      ? (l.speakerAffiliation ? `${l.speaker}（${l.speakerAffiliation}）` : String(l.speaker))
      : '';
    const desc = [
      l.topic ? `题目：${l.topic}` : '',
      who ? `主讲：${who}` : '',
      l.location ? `地点：${l.location}` : '',
      (l.college || l.organizer) ? `主办：${l.college || l.organizer}` : '',
      l.sourceUrl ? `原文：${l.sourceUrl}` : '',
      '',
      '由「木铎金声 · 华师讲座聚合」生成',
    ].filter(x => x !== '').join('\n');
    const uid = `${l.ck || l.sourceUrl || title}@danchekey.github.io`;
    const lines = [
      'BEGIN:VEVENT',
      `UID:${this._calEsc(uid)}`,
      `DTSTAMP:${dtstamp}`,
      `DTSTART:${this._calStamp(span.start)}`,
      `DTEND:${this._calStamp(span.end)}`,
      `SUMMARY:${this._calEsc(title)}`,
    ];
    if (l.location) lines.push(`LOCATION:${this._calEsc(l.location)}`);
    lines.push(`DESCRIPTION:${this._calEsc(desc)}`);
    if (l.sourceUrl) lines.push(`URL:${this._calEsc(l.sourceUrl)}`);
    // 提前 30 分钟提醒：加到日历的目的是「别忘了去」，不提醒等于白加
    lines.push('BEGIN:VALARM', 'TRIGGER:-PT30M', 'ACTION:DISPLAY',
      'DESCRIPTION:讲座即将开始', 'END:VALARM');
    lines.push('END:VEVENT');
    return lines;
  },

  // 一组记录 → 完整 .ics 文本（CRLF 结尾，RFC 5545 要求）
  calIcsFor(records) {
    const list = (records || []).filter(Boolean);
    const dtstamp = this._calStamp(Date.now());
    const events = [];
    list.forEach(l => {
      const ev = this._calEvent(l, dtstamp);
      if (ev) events.push(...ev);
    });
    if (!events.length) return '';
    const head = [
      'BEGIN:VCALENDAR',
      'VERSION:2.0',
      'PRODID:-//Muduo Jinsheng//Lecture Aggregator//CN',
      'CALSCALE:GREGORIAN',
      'METHOD:PUBLISH',
      'X-WR-CALNAME:木铎金声 · 华师讲座',
      'X-WR-TIMEZONE:Asia/Shanghai',
    ];
    const tail = ['END:VCALENDAR'];
    return [...head, ...events, ...tail].map(x => this._calFold(x)).join('\r\n') + '\r\n';
  },

  /* ---------- 交互 ---------- */

  // 「还没结束且时间可用」的统一口径——单场按钮（canAddCal）、整组按钮
  // （hasLiveRecs）、整组导出（addGroupToCalendar 的过滤）三处共用，勿再各写一份。
  // timeUnknown 在数据里是字符串 'True'/'False'（不是布尔），必须按字符串比；
  // 缺结束时间按 _calSpan 的 +1 小时兜底——进行中的讲座（已开始未结束）视为「还没结束」。
  _calLive(l, now) {
    if (!l || String(l.timeUnknown) === 'True') return false;
    const span = this._calSpan(l);
    return !!span && span.end > now;
  },

  // 这条记录能不能加日历：时间可用、非「时间未知」，且（2026-10-20 二修）**还没结束**。
  // 此前单场入口不查过期、整组入口查——已结束的讲座仍显示单场按钮，两处口径不一致，
  // 把过期讲座加进日历没有意义。过期判定收敛到 _calLive 单点。
  canAddCal(l) {
    return this._calLive(l, Date.now());
  },

  // 触发下载。filename 用纯 ASCII：中文文件名在部分安卓机型上会变成乱码或 .bin
  _calDownload(filename, text) {
    try {
      const blob = new Blob([text], { type: 'text/calendar;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      a.style.display = 'none';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      return true;
    } catch (e) {
      console.warn('[cal] 下载失败', e);
      return false;
    }
  },

  // 微信内置浏览器首次点击时提示一次（见文件头 ③）。不阻断下载。
  _calWeChatHint() {
    if (!/MicroMessenger/i.test(navigator.userAgent || '')) return;
    try {
      if (localStorage.getItem('cal_wx_hint_v1')) return;
      localStorage.setItem('cal_wx_hint_v1', '1');
    } catch (e) { /* 隐私模式下写不进去，每次都提示也无妨 */ }
    alert('微信里可能无法直接保存日历文件。\n\n如果点了没反应，请点右上角「···」→「在浏览器打开」，再点一次「加入日历」。');
  },

  // 单场：加进日历
  // 文件名带 single-：下载目录里与 group-（整组）、feed.ics（全量）一眼可分。
  // 2026-10-20 实测：用户点了卡片图标后以为「下载了全部日历」，只看到一场以为出错。
  addToCalendar(l) {
    if (!this.canAddCal(l)) return;
    const span = this._calSpan(l);
    const text = this.calIcsFor([l]);
    if (!text) return;
    const d = new Date(span.start);
    const p = n => String(n).padStart(2, '0');
    const name = `muduojinsheng-single-${d.getUTCFullYear()}${p(d.getUTCMonth() + 1)}${p(d.getUTCDate())}-${p(d.getUTCHours())}${p(d.getUTCMinutes())}.ics`;
    this._calWeChatHint();
    // 计数 +1 与本机高亮（2026-10-20 三按钮统一）：仅在下载真正触发成功时计——
    // 下载都失败了还 +1 会虚高。键用 ck，与想听/点赞同一套讲座级计数键（见 count_key 门禁）。
    // calAddedUrls 是本机高亮态（localStorage，同 likedUrls/wantedUrls），加过即高亮；
    // bumpCal 是全局云端计数。两者独立。
    if (this._calDownload(name, text)) {
      const key = l.ck || l.sourceUrl;
      if (key && this.calAddedUrls) { this.calAddedUrls.add(key); this.saveCalAdded(); }
      this.bumpCal(key);
    }
  },

  // 整组入口的显示条件（2026-10-20 二修）：组内至少一场「还没结束」。否则按钮是死的——
  // addGroupToCalendar 过滤后为空 → calIcsFor 返回 '' → 不触发下载、无任何反馈。
  // 与单场入口（canAddCal）同一判据 _calLive。
  hasLiveRecs(u) {
    const now = Date.now();
    return ((u && u.recs) || []).some(l => this._calLive(l, now));
  },

  // 整组按钮的高亮态（2026-10-21）：组内**任意一场**已加过就亮，与单场按钮同色系。
  // 不给整组按钮加数字——组内计数加总没有意义（3 场各 5 次 ≠ 15 人加了这一组），
  // 数字仍只在单场卡上按场显示。
  hasGroupCalAdded(u) {
    return ((u && u.recs) || []).some(l => this.hasCalAdded(l.ck || l.sourceUrl));
  },

  // 论坛卡（同页同日 ≥3 场折叠成一张卡）：整组一起加。
  // 只取「还没结束」的场次——把上周的讲座塞进日历没有意义（判据见 _calLive）。
  addGroupToCalendar(u) {
    const recs = (u && u.recs) || [];
    const now = Date.now();
    const live = recs.filter(l => this._calLive(l, now));
    const text = this.calIcsFor(live);
    if (!text) return;
    const first = this._calSpan(live[0]);
    const d = new Date(first.start);
    const p = n => String(n).padStart(2, '0');
    const name = `muduojinsheng-group-${d.getUTCFullYear()}${p(d.getUTCMonth() + 1)}${p(d.getUTCDate())}.ics`;
    this._calWeChatHint();
    if (!this._calDownload(name, text)) return;
    // 组内逐场留痕（2026-10-21）：此前整组下载既不写本机高亮、也不计入次数——
    // 用户下了一整组日历，回头组内每张单场卡还是灰的、数字还是 0，与单场入口
    // 行为分叉（2026-10-21 用户反馈「有数字却不亮」时顺带查出的第二个缺口）。
    // 口径与 addToCalendar 完全一致：先写本机高亮，再 bumpCal 云端计数，
    // 且只在下载真正触发成功后执行——下载失败还记一次会虚增。
    live.forEach(l => {
      const key = l.ck || l.sourceUrl;
      if (!key) return;
      if (this.calAddedUrls) this.calAddedUrls.add(key);
      this.bumpCal(key);
    });
    if (this.calAddedUrls) this.saveCalAdded();
  },
});
