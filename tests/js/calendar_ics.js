// 日历导出（.ics）门禁：时区换算 / 折行 / 转义 / 可加判定（2026-10-20 批次 B）
//
// 为什么这些点必须锁：
//   · 时区：`lectureStart` 是「北京时间字面量」，写成 new Date('...') 会按**浏览器
//     本地时区**解析，用户在东京就错一小时。这是最容易静默出错、又最难发现的一类。
//   · 折行：RFC 5545 要求 ≤75 字节/行，中文一字 3 字节。切在汉字中间会得到乱码，
//     而且只有部分日历客户端会报错——本地测不出来。
//   · 转义：摘要里的逗号/分号不转义，客户端会把后半截当成另一个属性。
//
// 运行：node tests/js/calendar_ics.js（纯 node，无浏览器无网络）

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SITE = path.join(__dirname, '..', '..', 'site');
const errs = [];

function ok(cond, msg) {
  if (!cond) errs.push(msg);
}

// 在隔离上下文里加载 app.calendar.js，只喂它真正用到的东西
function loadCal() {
  const ctx = { APP_METHODS: {}, TextEncoder, console };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(path.join(SITE, 'app.calendar.js'), 'utf8'), ctx);
  const m = ctx.APP_METHODS;
  Object.keys(m).forEach(k => {
    if (typeof m[k] === 'function') m[k] = m[k].bind(m);
  });
  return m;
}

const cal = loadCal();

// ---------- test_01：北京时间 → UTC 毫秒 ----------
{
  const ms = cal._calUtcMs('2026-10-31 14:00:00');
  ok(ms === Date.UTC(2026, 9, 31, 6, 0, 0),
    `test_01 北京 14:00 应换算成 UTC 06:00，实际 ${new Date(ms).toISOString()}`);
  // 跨日：北京时间 03:00 → 前一天 UTC 19:00
  const ms2 = cal._calUtcMs('2026-10-31 03:00:00');
  ok(ms2 === Date.UTC(2026, 9, 30, 19, 0, 0),
    `test_01b 北京 03:00 应跨到前一天 UTC 19:00，实际 ${new Date(ms2).toISOString()}`);
  ok(cal._calUtcMs('') === null, 'test_01c 空字符串应返回 null');
  ok(cal._calUtcMs('2026-13-45 99:99:99') === null || true, 'test_01d 非法值不抛异常');
}

// ---------- test_02：UTC 时间戳格式 ----------
{
  const s = cal._calStamp(Date.UTC(2026, 9, 31, 6, 0, 0));
  ok(s === '20261031T060000Z', `test_02 时间戳格式应为 20261031T060000Z，实际 ${s}`);
}

// ---------- test_03：缺结束时间按 +1 小时 ----------
{
  const span = cal._calSpan({ lectureStart: '2026-10-31 14:00:00' });
  ok(span && span.end - span.start === 3600 * 1000,
    'test_03 lectureEnd 缺失时应默认 +1 小时');
  const span2 = cal._calSpan({ lectureStart: '2026-10-31 14:00:00', lectureEnd: '2026-10-31 16:30:00' });
  ok(span2 && span2.end - span2.start === 2.5 * 3600 * 1000,
    'test_03b 有 lectureEnd 时应采用真实时长');
  const span3 = cal._calSpan({ lectureStart: '2026-10-31 14:00:00', lectureEnd: '2026-10-31 13:00:00' });
  ok(span3 && span3.end > span3.start, 'test_03c 结束早于开始时不得生成负时长事件');
}

// ---------- test_04：canAddCal 挡掉时间未知 ----------
{
  ok(cal.canAddCal({ lectureStart: '2026-10-31 14:00:00' }) === true,
    'test_04 有开始时间应可加');
  // timeUnknown 在数据里是字符串 'True'，不是布尔——按布尔判断会漏掉 50 条
  ok(cal.canAddCal({ lectureStart: '2026-10-31 14:00:00', timeUnknown: 'True' }) === false,
    'test_04b timeUnknown="True"（字符串）必须挡住');
  ok(cal.canAddCal({ lectureStart: '', timeUnknown: 'False' }) === false,
    'test_04c 缺开始时间必须挡住');
  ok(cal.canAddCal(null) === false, 'test_04d null 不得抛异常');
}

// ---------- test_05：.ics 整体结构 ----------
{
  const rec = {
    title: '勷勤数学•专家报告-陈天翔',
    topic: '从医学视觉到多模态大模型',
    lectureStart: '2026-10-31 14:00:00',
    lectureEnd: '2026-10-31 15:00:00',
    location: '数科院西楼111',
    speaker: '陈天翔',
    speakerAffiliation: '阿里云',
    college: '数学科学学院',
    sourceUrl: 'http://maths.scnu.edu.cn/a/20261031/8857.html',
    ck: 'http://maths.scnu.edu.cn/a/20261031/8857.html',
  };
  const ics = cal.calIcsFor([rec]);
  ok(ics.startsWith('BEGIN:VCALENDAR\r\n'), 'test_05 必须以 BEGIN:VCALENDAR + CRLF 开头');
  ok(ics.trimEnd().endsWith('END:VCALENDAR'), 'test_05b 必须以 END:VCALENDAR 结尾');
  ok(ics.includes('\r\n'), 'test_05c 行分隔符必须是 CRLF（RFC 5545 要求）');
  ok(!/[^\r]\n/.test(ics.replace(/\r\n/g, '')), 'test_05d 不得出现裸 LF');
  ok(ics.includes('DTSTART:20261031T060000Z'), 'test_05e 开始时间应为 UTC 06:00');
  ok(ics.includes('DTEND:20261031T070000Z'), 'test_05f 结束时间应为 UTC 07:00');
  ok(ics.includes('VERSION:2.0'), 'test_05g 缺 VERSION:2.0');
  ok((ics.match(/BEGIN:VEVENT/g) || []).length === 1, 'test_05h 单场应只生成 1 个 VEVENT');
  ok((ics.match(/END:VEVENT/g) || []).length === 1, 'test_05i VEVENT 未闭合');
  ok(ics.includes('BEGIN:VALARM'), 'test_05j 缺提醒（不加提醒等于白加）');
}

// ---------- test_06：多场 → 多个 VEVENT ----------
{
  const mk = (d, t) => ({ title: `讲座${d}`, lectureStart: `2026-10-${d} ${t}:00:00`, ck: `k${d}` });
  const ics = cal.calIcsFor([mk('21', '09'), mk('22', '14'), mk('23', '16')]);
  ok((ics.match(/BEGIN:VEVENT/g) || []).length === 3, 'test_06 三场应生成 3 个 VEVENT');
  ok((ics.match(/END:VCALENDAR/g) || []).length === 1, 'test_06b 只能有一个 VCALENDAR');
}

// ---------- test_07：转义 ----------
{
  const ics = cal.calIcsFor([{
    title: 'A,B;C\\D',
    lectureStart: '2026-10-31 14:00:00',
    ck: 'x',
  }]);
  ok(ics.includes('SUMMARY:A\\,B\\;C\\\\D'), 'test_07 摘要里的 , ; \\ 必须转义');
  ok(ics.includes('DESCRIPTION:'), 'test_07b 缺 DESCRIPTION');
}

// ---------- test_08：折行 ≤75 字节且不切坏汉字 ----------
{
  const longTitle = '这是一个非常长的讲座标题'.repeat(12);
  const ics = cal.calIcsFor([{
    title: longTitle, lectureStart: '2026-10-31 14:00:00', ck: 'long',
  }]);
  const lines = ics.split('\r\n');
  const over = lines.filter(l => Buffer.byteLength(l, 'utf8') > 75);
  ok(over.length === 0, `test_08 有 ${over.length} 行超过 75 字节`);
  // 续行必须以单个空格开头
  const conts = lines.filter(l => l.startsWith(' '));
  ok(conts.length > 0, 'test_08b 长标题应产生续行');
  ok(conts.every(l => !l.startsWith('  ')), 'test_08c 续行前缀只能是一个空格');
  // 还原后必须与原文一致（没有切坏字符）
  const joined = lines.join('').replace(/ /g, '');
  ok(joined.includes(longTitle.replace(/\s/g, '')), 'test_08d 折行后内容应可无损还原');
}

// ---------- test_09：空输入 ----------
{
  ok(cal.calIcsFor([]) === '', 'test_09 空数组应返回空串（不生成空日历）');
  ok(cal.calIcsFor([{ title: '无时间' }]) === '', 'test_09b 无时间的记录应被跳过');
}

// ---------- test_10：过期判定（2026-10-20 二修：单场/整组入口同判据 _calLive） ----------
{
  ok(cal.canAddCal({ lectureStart: '2020-01-01 14:00:00' }) === false,
    'test_10 已结束的讲座不得再显示「加入日历」（此前只有整组入口过滤过期）');
  // 进行中（已开始、缺结束时间按 +1 小时兜底）仍可加：讲座正进行时按钮不该消失
  const recent = new Date(Date.now() - 20 * 60 * 1000 + 8 * 3600 * 1000); // now-20min 的北京字面量
  const p2 = n => String(n).padStart(2, '0');
  const bj = `${recent.getUTCFullYear()}-${p2(recent.getUTCMonth() + 1)}-${p2(recent.getUTCDate())}`
    + ` ${p2(recent.getUTCHours())}:${p2(recent.getUTCMinutes())}:00`;
  ok(cal.canAddCal({ lectureStart: bj }) === true,
    'test_10b 刚开始 20 分钟且未标结束时间的讲座应仍可加（进行中）');
  ok(cal.hasLiveRecs({ recs: [{ lectureStart: '2020-01-01 14:00:00' }] }) === false,
    'test_10c 整组全部过期时不得显示整组入口（否则点击静默无反应）');
  ok(cal.hasLiveRecs({ recs: [
    { lectureStart: '2020-01-01 14:00:00' },
    { lectureStart: '2099-01-01 14:00:00' },
  ] }) === true, 'test_10d 组内有一场未结束即应显示整组入口');
  // timeUnknown 的组内场次必须被整组过滤剔除（_calLive 同时管两个入口）
  ok(cal.hasLiveRecs({ recs: [
    { lectureStart: '2099-01-01 14:00:00', timeUnknown: 'True' },
  ] }) === false, 'test_10e 组内仅剩 timeUnknown 场次时同样不得显示整组入口');
}

// ---------- test_11：标题去重（与 Python _feed_title 同判据，修「题目｜题目」） ----------
{
  // title 已内嵌 topic（全库 12.6%）：旧前端只判 !== 会重复，而 feed.xml/feed.ics 不会
  const ics1 = cal.calIcsFor([{
    title: '学术讲座：早期胚胎发育和体轴建立',
    topic: '早期胚胎发育和体轴建立',
    lectureStart: '2026-10-31 14:00:00', ck: 'dedup',
  }]);
  ok(ics1.includes('SUMMARY:学术讲座：早期胚胎发育和体轴建立'),
    'test_11 topic 已内嵌于 title 时 SUMMARY 应仅为 title');
  ok(!ics1.includes('｜'), 'test_11b topic 已内嵌于 title 时不得追加「｜题目」');
  // 真正不同的题目才追加（SUMMARY 可能超 75 字节被折行，只断言连接符存在）
  const ics2 = cal.calIcsFor([{
    title: '勷勤数学•专家报告-陈天翔', topic: '从医学视觉到多模态大模型',
    lectureStart: '2026-10-31 14:00:00', ck: 'differ',
  }]);
  ok(ics2.includes('｜'), 'test_11c topic 与 title 不同时才追加「｜题目」');
}

// ---------- test_12：主讲行拼装（与 Python _feed_desc 对齐，修残缺括号） ----------
{
  // 只有单位没姓名：旧写法输出成「华南师范大学）」（缺左括号）
  const ics1 = cal.calIcsFor([{
    title: 'T', lectureStart: '2026-10-31 14:00:00', ck: 'aff1',
    speaker: '', speakerAffiliation: '华南师范大学',
  }]);
  ok(!ics1.includes('主讲'),
    'test_12 无姓名时不得输出残缺主讲行（_feed_desc 口径：整行不输出）');
  const ics2 = cal.calIcsFor([{
    title: 'T', lectureStart: '2026-10-31 14:00:00', ck: 'aff2',
    speaker: '张三', speakerAffiliation: '华南师范大学',
  }]);
  ok(ics2.includes('主讲：张三（华南师范大学）'),
    'test_12b 姓名+单位应输出「姓名（单位）」');
  // 有姓名没单位：不得留下孤立左括号
  const ics3 = cal.calIcsFor([{
    title: 'T', lectureStart: '2026-10-31 14:00:00', ck: 'aff3',
    speaker: '张三', speakerAffiliation: '',
  }]);
  ok(ics3.includes('主讲：张三') && !ics3.includes('（'),
    'test_12c 无单位时不得输出孤立括号');
}

if (errs.length) {
  errs.forEach(e => console.error('  ✗ ' + e));
  console.error(`calendar_ics: ${errs.length} 项失败`);
  process.exit(1);
}
console.log('calendar_ics: 时区/结构/转义/折行/可加判定 全部 pass');
