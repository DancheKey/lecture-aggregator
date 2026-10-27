// 搜索归一化门禁（2026-10-27）
//
// 背景：搜索原先是 `hay.includes(q)` 裸子串比较，对三种「同字不同码」的差异敏感：
//   ① 全角 / 半角（中文输入法常见）：「ＡＩ」搜不到「AI」；
//   ② 繁 / 简：「講座」搜不到「讲座」（库里几乎全简体，繁体输入等于零召回）；
//   ③ 空格：字面含空格的查询几乎必然 miss。
//
// 现行实现（site/app.core.js「搜索归一化」+ app.computed.filtered 快慢双路径）：
//   · normalizeSearchText：全角 ASCII → 半角、U+3000 → 空格、繁 → 简、小写；
//     **保留空白**，空白当分词边界。
//   · searchTokens：归一化后按空白切词，多词按 AND 匹配（「张三 讲座」= 张三 AND 讲座）。
//   · filtered 先走原样 includes 快路径，未命中才归一化重试——简体无空格输入
//     （绝大多数）零额外开销，本次改造不改变既有召回。
//
// 本门禁同时**校验映射表本身**（等长 / 繁简不同形 / 繁体键唯一），
// 把 tmp/_gen_t2s.py 的校验搬到 CI，防止后续手改表改坏而不自知。
//
// 用法：node tests/js/app_search_normalize.js
const fs = require('fs');
const vm = require('vm');
const path = require('path');

const SITE = path.join(__dirname, '..', '..', 'site');

const ctx = {
  console,
  setTimeout,
  fetch: async () => ({ ok: true, status: 200, json: async () => ({ data: [] }) }),
};
ctx.window = ctx;
ctx.document = { addEventListener() {}, querySelectorAll() { return []; } };
ctx.requestIdleCallback = (cb) => setTimeout(() => cb({ didTimeout: false, timeRemaining: () => 0 }), 0);
ctx.Vue = { createApp: () => ({}) };
vm.createContext(ctx);

// filtered 的排序段依赖 statusOf / isTimeTBD（app.display.js），缺了会直接抛。
const PARTS = ['app.core.js', 'app.state.js', 'app.computed.js', 'app.data.js', 'app.social.js', 'app.display.js'];
for (const f of PARTS) {
  vm.runInContext(fs.readFileSync(path.join(SITE, f), 'utf8'), ctx, { filename: f });
}

const METHODS = vm.runInContext('APP_METHODS', ctx);
const COMPUTED = vm.runInContext('APP_COMPUTED', ctx);
const STATE = vm.runInContext('APP_STATE()', ctx);
const normalizeSearchText = vm.runInContext('normalizeSearchText', ctx);
const searchTokens = vm.runInContext('searchTokens', ctx);
const SIMP = vm.runInContext('SEARCH_T2S_SIMP', ctx);
const TRAD = vm.runInContext('SEARCH_T2S_TRAD', ctx);

let fail = 0;
function check(label, got, want) {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) fail++;
  console.log((ok ? 'PASS' : 'FAIL') + ' | ' + label + ' | got=' + JSON.stringify(got) + ' want=' + JSON.stringify(want));
}

// 造一个能跑 filtered() 的最小组件实例
function fake(over) {
  const f = Object.assign(Object.create(null), METHODS, STATE, over || {});
  Object.setPrototypeOf(f, METHODS);
  if (!f.lectureStats) f.lectureStats = {};
  if (!f.all) f.all = [];
  return f;
}

// 用真实 filtered() 走一遍搜索，返回命中的标题数组。
// filtered 是 computed 属性（APP_COMPUTED），不是 methods，必须显式 call 绑定实例。
function search(records, query, field) {
  const s = fake({ all: records, query: query, searchField: field || '' });
  return COMPUTED.filtered.call(s).map(l => l.title);
}

const REC = [
  { title: 'AI 与教育变革', lectureStart: '2024-05-10 09:00', sourceUrl: 'u1', college: '教育科学学院', campus: '石牌', speakerKeys: [], speaker: '张三', location: '石牌校区第一课室大楼' },
  { title: '机器学习前沿讲座', lectureStart: '2024-06-11 14:30', sourceUrl: 'u2', college: '计算机学院', campus: '大学城', speakerKeys: [], speaker: '李四', location: '大学城校区B座' },
  // 第三条地点刻意写成繁体，验证地点维度也会归一化
  { title: '近代史专题', lectureStart: '2024-07-12 15:00', sourceUrl: 'u3', college: '历史文化学院', campus: '石牌', speakerKeys: [], speaker: '王五', location: '圖書館三樓報告廳' },
];

console.log('--- 1) 映射表自检（防止手改表改坏）---');
check('简繁两串等长', SIMP.length === TRAD.length, true);
{
  const seen = new Set();
  const dup = [];
  const same = [];
  for (let i = 0; i < TRAD.length; i++) {
    const s = SIMP.charAt(i), t = TRAD.charAt(i);
    if (s === t) same.push(t);
    if (seen.has(t)) dup.push(t);
    seen.add(t);
  }
  check('无繁简同形对（同形映射无意义）', same, []);
  check('繁体键唯一（一繁对多简会映射错字）', dup, []);
  check('映射表非空且规模合理', TRAD.length > 200 && TRAD.length < 2000, true);
}

console.log('\n--- 2) normalizeSearchText：全角→半角 ---');
check('全角字母数字', normalizeSearchText('ＡＩ２０２４'), 'ai2024');
check('全角标点', normalizeSearchText('Ａ，Ｂ'), 'a,b');
check('半角原样（只小写）', normalizeSearchText('AI2024'), 'ai2024');

console.log('\n--- 3) normalizeSearchText：繁→简 ---');
check('繁体词', normalizeSearchText('講座'), '讲座');
check('繁体学科名', normalizeSearchText('學術報告'), '学术报告');
check('一简对多繁 發', normalizeSearchText('發現'), '发现');
check('一简对多繁 髮', normalizeSearchText('頭髮'), '头发');
check('一简对多繁 臺/檯', normalizeSearchText('臺灣檯燈'), '台湾台灯');
check('繁体人名', normalizeSearchText('陳寅恪'), '陈寅恪');
check('简体原样（幂等）', normalizeSearchText('讲座'), '讲座');

console.log('\n--- 4) normalizeSearchText：空白处理 ---');
check('全角空格 → 半角空格（保留）', normalizeSearchText('张三　讲座'), '张三 讲座');
check('连续空白保留原样', normalizeSearchText('a  b'), 'a  b');
check('幂等：归一化两次结果相同', normalizeSearchText(normalizeSearchText('ＡＩ 講座')), normalizeSearchText('ＡＩ 講座'));
check('空串 / null 安全', [normalizeSearchText(''), normalizeSearchText(null)], ['', '']);

console.log('\n--- 5) searchTokens：分词 ---');
check('单词', searchTokens('讲座'), ['讲座']);
check('空格分词', searchTokens('张三 讲座'), ['张三', '讲座']);
check('全角空格分词', searchTokens('ＡＩ　講座'), ['ai', '讲座']);
check('首尾空白丢弃', searchTokens('  讲座  '), ['讲座']);
check('纯空白 → 空数组', searchTokens('   '), []);
check('空串 → 空数组', searchTokens(''), []);

console.log('\n--- 6) filtered 端到端召回 ---');
check('简体基线（回归：行为不变）', search(REC, '讲座'), ['机器学习前沿讲座']);
check('全角输入命中半角数据', search(REC, 'ＡＩ'), ['AI 与教育变革']);
check('繁体输入命中简体数据', search(REC, '講座'), ['机器学习前沿讲座']);
check('繁体学科词', search(REC, '歷史'), ['近代史专题']);
check('多词 AND：跨字段组合', search(REC, '张三 讲座'), []);   // 张三只在 AI 卡，讲座只在另一卡 → 不该命中
check('多词 AND：同卡两词', search(REC, '张三 教育'), ['AI 与教育变革']);
check('多词顺序无关', search(REC, '教育 张三'), ['AI 与教育变革']);
check('无关词不命中', search(REC, '量子力学'), []);
check('繁简混写', search(REC, '機器學習'), ['机器学习前沿讲座']);

console.log('\n--- 7) 快路径不退化：简体无空格仍走原逻辑 ---');
check('单字命中', search(REC, '史'), ['近代史专题']);
check('大小写不敏感', search(REC, 'ai'), ['AI 与教育变革']);
check('无匹配返回空', search(REC, 'zzz'), []);
check('空查询返回全部', search(REC, '').length, 3);

console.log('\n--- 8) 字段限定维度同样归一化 ---');
check('题目维度·繁体', search(REC, '機器學習', 'topic'), ['机器学习前沿讲座']);
check('单位维度·繁体', search(REC, '計算機學院', 'college'), ['机器学习前沿讲座']);
check('地点维度·简体', search(REC, '石牌', 'location'), ['AI 与教育变革']);
check('地点维度·繁体（数据侧是繁体，输入繁体）', search(REC, '圖書館', 'location'), ['近代史专题']);
check('地点维度·简体输入命中繁体数据', search(REC, '图书馆', 'location'), ['近代史专题']);

console.log('');
if (fail) {
  console.log('[FAIL] 搜索归一化用例失败 ' + fail + ' 条');
  process.exit(1);
}
console.log('[PASS] 搜索归一化全部用例通过');
