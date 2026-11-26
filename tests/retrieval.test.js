/**
 * 回归：检索内核（web/retrieval.js）。
 *
 * 语义来自 docs/retrieval-model.md，坏了的地方都是「静默少几篇」这种看不出错的地方，
 * 所以这里做两件事：
 *   1. 逐条对死语义（any / all / none、年份区间与未知、组脚、边际值、近失）；
 *   2. 与一份「朴素实现」对拍：穷举过滤组合，结果集与每一个数字都必须相等。
 *
 * 运行：node tests/retrieval.test.js
 */
const R = require("../web/retrieval.js");

const TAG = R.TAG;

function item(id, opts) {
  opts = opts || {};
  return {
    id: id,
    title: opts.title || "Paper " + id,
    abstract: opts.abstract || "",
    venue: opts.venue || "",
    authors: opts.authors || [],
    note: opts.note || "",
    year: typeof opts.year === "number" ? opts.year : null,
    status: opts.status || "todo",
    starred: !!opts.starred,
    needsReview: opts.needsReview || false,
    topicIds: opts.tagIds || [],
    tags: {
      topic: opts.tags || [],
      year: typeof opts.year === "number" ? [String(opts.year)] : [],
      author: opts.authors || [],
      org: opts.orgs || [],
    },
  };
}

function tagGroup(groupId, op, terms, options) {
  return {
    kind: TAG,
    group_id: groupId,
    op: op,
    terms: terms,
    options: options || terms,
  };
}

/* ---------- 朴素实现：只按文档逐字算，不做任何优化 ---------- */
function naiveTerm(it, kind, term) {
  if (kind === TAG) return new Set(it.topicIds).has(String(term));
  if (kind === R.YEAR) {
    if (term && typeof term === "object" && term.unknown) return it.year === null;
    const r = term.range || term;
    const a = Number(r[0]);
    const b = Number(r[1]);
    return it.year !== null && it.year >= a && it.year <= b;
  }
  if (term && "star" in term) return it.starred === (term.star !== false);
  if (term && term.status) return it.status === term.status;
  return false;
}

function naiveGroupPass(it, g) {
  if (!g.terms.length) return true;
  const op = g.kind === TAG ? g.op : (g.op === "none" ? "none" : "any");
  const vals = g.terms.map((t) => naiveTerm(it, g.kind, t));
  if (op === "all") return vals.every((v) => v);
  const some = vals.some((v) => v);
  return op === "none" ? !some : some;
}

function naiveBase(it, f) {
  const q = (f.q || "").trim().toLowerCase();
  if (f.mode === "untagged" && it.topicIds.length !== 0) return false;
  if (f.mode === "review" && !it.needsReview) return false;
  const hay = [
    it.title, it.abstract, it.venue, it.authors.join(" "), it.note,
    it.tags.topic.join(" "),
  ].join(" ").toLowerCase();
  if (q && !hay.includes(q)) return false;
  for (const a of f.authors || []) {
    if (!it.authors.some((x) => x.toLowerCase() === a.toLowerCase())) return false;
  }
  return true;
}

function naive(items, f) {
  const groups = f.groups || [];
  const matched = items.filter((it) => {
    if (!naiveBase(it, f)) return false;
    return groups.every((g) => naiveGroupPass(it, g));
  });
  const feet = groups.map((g, gi) => {
    if (!g.terms.length) return null;
    return items.filter((it) => {
      if (!naiveBase(it, f)) return false;
      for (let k = 0; k <= gi; k++) {
        if (groups[k].terms.length && !naiveGroupPass(it, groups[k])) return false;
      }
      return true;
    }).length;
  });
  const counts = groups.map((g, gi) => g.options.map((t) => {
    const marginal = g.kind === TAG && g.op === "all";
    if (marginal && g.terms.some((x) => R.termKey(g.kind, x) === R.termKey(g.kind, t))) {
      // 已选项在「全部」组里的边际值就是组脚
      return feet[gi];
    }
    let n = 0;
    for (const it of items) {
      if (!marginal) {
        if (naiveTerm(it, g.kind, t)) n += 1;
        continue;
      }
      if (!naiveBase(it, f)) continue;
      let othersOK = true;
      for (let gi2 = 0; gi2 < groups.length; gi2++) {
        if (gi2 === gi) continue;
        if (groups[gi2].terms.length && !naiveGroupPass(it, groups[gi2])) othersOK = false;
      }
      // 「把这个项也加上」= 当前组的已选项 + 这一项
      const currentOK = g.terms.length ? naiveGroupPass(it, g) : true;
      if (othersOK && currentOK && naiveTerm(it, g.kind, t)) n += 1;
    }
    return n;
  }));
  return { matched, feet, counts };
}

/* ---------- 断言 ---------- */
let failures = 0;
let passes = 0;
function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (ok) passes += 1;
  else {
    failures += 1;
    console.log("FAIL " + name);
    console.log("  got      " + JSON.stringify(actual));
    console.log("  expected " + JSON.stringify(expected));
  }
}
function eq(name, actual, expected) {
  check(name, actual, expected);
}

/* ---------- 固定小书库 ---------- */
// 5 篇：A 有标签 x+y，B 有 x，C 有 y，D 无标签、年份未知，E 有 z、待补全
const lib = [
  item("A", { year: 2020, tagIds: ["t_x", "t_y"], tags: ["x", "y"], status: "read", starred: true }),
  item("B", { year: 2021, tagIds: ["t_x"], tags: ["x"], status: "reading" }),
  item("C", { year: 2022, tagIds: ["t_y"], tags: ["y"], status: "todo" }),
  item("D", { year: null, tagIds: [], tags: [] }),
  item("E", { year: 2019, tagIds: ["t_z"], tags: ["z"], needsReview: true }),
];

/* 1. 空条件 = 浏览全库 */
{
  const r = R.evaluate(lib, R.emptyFilters());
  eq("空条件：结果 = 全库", r.count, 5);
  eq("空条件：组脚与项计数都不存在", r.groups.length, 0);
  eq("空条件：没有年份未知提示", r.yearUnknown.show, false);
}

/* 2. 任一 / 全部 / 排除 */
{
  const any = R.evaluate(lib, { groups: [tagGroup("@unfiled", "any", ["t_x", "t_y"])] });
  eq("任一：命中 A B C", any.matched.map((p) => p.id), ["A", "B", "C"]);
  eq("任一：组脚 = 结果", any.groups[0].foot, 3);
  eq("任一：项计数是全库绝对值", any.groups[0].options.map((o) => o.count), [2, 2]);

  const all = R.evaluate(lib, { groups: [tagGroup("@unfiled", "all", ["t_x", "t_y"])] });
  eq("全部：只命中 A", all.matched.map((p) => p.id), ["A"]);
  eq("全部：组脚 = 结果", all.groups[0].foot, 1);

  const none = R.evaluate(lib, { groups: [tagGroup("@unfiled", "none", ["t_x"])] });
  eq("排除：¬(A∨B)，命中 C D E", none.matched.map((p) => p.id), ["C", "D", "E"]);
  eq("排除：组脚 = 结果", none.groups[0].foot, 3);
  eq("排除：项计数仍是全库绝对值", none.groups[0].options[0].count, 2);
}

/* 3. 「全部」的项计数是边际值：加上这一项后还剩几篇 */
{
  const r = R.evaluate(lib, {
    groups: [tagGroup("@unfiled", "all", ["t_x"], ["t_x", "t_y", "t_z"])],
  });
  eq("全部：已选项 = 组脚", r.groups[0].options[0].selected, true);
  eq("全部：已选项计数 = 组脚", r.groups[0].options[0].count, 2);
  // 未选 t_y：在「x 且 y」下剩 1 篇
  eq("全部：未选项是边际值（t_y）", r.groups[0].options[1].count, 1);
  // 未选 t_z：x 且 z → 0
  eq("全部：未选项是边际值（t_z）", r.groups[0].options[2].count, 0);
  eq("全部：未选项标为边际", r.groups[0].options[1].marginal, true);
}

/* 4. 空组不参与，不恒真也不恒假 */
{
  const r = R.evaluate(lib, {
    groups: [tagGroup("@unfiled", "none", []), tagGroup("@unfiled", "any", [])],
  });
  eq("空组：仍是全库", r.count, 5);
  eq("空组：组脚是 null（不参与）", r.groups.map((g) => g.foot), [null, null]);
  eq("空组：active=false", r.groups.map((g) => g.active), [false, false]);
}

/* 5. 组间固定 AND（没有跨组 OR） */
{
  const r = R.evaluate(lib, {
    q: "paper b",
    groups: [tagGroup("@unfiled", "any", ["t_x"])],
  });
  eq("组间 AND：标签 x 且搜索 b", r.matched.map((p) => p.id), ["B"]);
}

/* 6. 年份区间：不含未知；区间 + 未知才含；只选未知只含未知 */
{
  const rangeOnly = R.evaluate(lib, {
    groups: [{ kind: R.YEAR, op: "any", terms: [{ range: [2020, 2022] }], options: [{ range: [2020, 2022] }] }],
  });
  eq("年份区间：A B C，D（未知）不命中", rangeOnly.matched.map((p) => p.id), ["A", "B", "C"]);
  eq("年份区间：组脚", rangeOnly.groups[0].foot, 3);
  eq("年份区间：未知三角提示=1", rangeOnly.yearUnknown.count, 1);
  eq("年份区间：三角指向年份组", rangeOnly.yearUnknown.group_id, R.YEAR_GROUP);

  const both = R.evaluate(lib, {
    groups: [{ kind: R.YEAR, op: "any", terms: [{ range: [2020, 2022] }, { unknown: true }], options: [] }],
  });
  eq("区间 + 未知：A B C D", both.matched.map((p) => p.id), ["A", "B", "C", "D"]);
  eq("区间 + 未知：三角消失", both.yearUnknown.show, false);

  const unknownOnly = R.evaluate(lib, {
    groups: [{ kind: R.YEAR, op: "any", terms: [{ unknown: true }], options: [] }],
  });
  eq("只选未知：只命中 D", unknownOnly.matched.map((p) => p.id), ["D"]);
  eq("只选未知：不再提示三角", unknownOnly.yearUnknown.show, false);
}

/* 7. 排除 + 年份区间：未知的保留（¬(A∨B)） */
{
  const r = R.evaluate(lib, {
    groups: [{ kind: R.YEAR, op: "none", terms: [{ range: [2020, 2022] }], options: [] }],
  });
  eq("年份排除：D E 留下", r.matched.map((p) => p.id), ["D", "E"]);
  // 操作符是排除时，不提供「加未知」这种纠正——那是「任一」的补丁
  eq("年份排除：不给未知三角", r.yearUnknown.show, false);
}

/* 8. 组脚 = 计数行：同一组是最后一个非空组时两者相等 */
{
  const r = R.evaluate(lib, {
    groups: [
      tagGroup("@unfiled", "any", ["t_x", "t_y"]),
      { kind: R.ATTR, op: "any", terms: [{ status: "todo" }], options: [] },
    ],
  });
  const last = r.groups.filter((g) => g.active).pop();
  eq("组脚与计数行对得上", last.foot, r.count);
  eq("计数器一致：x∪y 且 todo = C", r.matched.map((p) => p.id), ["C"]);
  eq("属性组没有「全部」可用", r.groups[1].op, "any");
}

/* 9. 属性组选「全部」被折回「任一」，不悄悄清空书库 */
{
  const r = R.evaluate(lib, {
    groups: [{ kind: R.ATTR, op: "all", terms: [{ status: "read" }, { star: true }], options: [] }],
  });
  eq("属性组 all → any（A 同时满足两者）", r.matched.map((p) => p.id), ["A"]);
  const r2 = R.evaluate(lib, {
    groups: [{ kind: R.ATTR, op: "all", terms: [{ status: "read" }, { status: "todo" }], options: [] }],
  });
  eq("属性组 all → any（互斥取值仍有结果，而不是空）", r2.matched.map((p) => p.id), ["A", "C", "D", "E"]);
  eq("属性组 all 被记录为 any", r2.groups[0].op, "any");
}

/* 10. 未打标是模式：不进布尔、不与组取反 */
{
  const r = R.evaluate(lib, { mode: "untagged" });
  eq("未打标：只命中 D", r.matched.map((p) => p.id), ["D"]);
  const r2 = R.evaluate(lib, {
    mode: "untagged",
    groups: [tagGroup("@unfiled", "none", ["t_x"])],
  });
  eq("未打标 + 排除 x：仍是 D（模式与组 AND）", r2.matched.map((p) => p.id), ["D"]);
  const r3 = R.evaluate(lib, { mode: "untagged", q: "paper d" });
  eq("未打标 + 搜索", r3.matched.map((p) => p.id), ["D"]);
}

/* 11. 待补全模式 */
{
  eq("待补全：只命中 E", R.evaluate(lib, { mode: "review" }).matched.map((p) => p.id), ["E"]);
}

/* 12. 文本搜索覆盖 note_md / 作者 / 标签名 */
{
  const noted = lib.map((p) => (p.id === "C" ? Object.assign({}, p, { note: "相位噪声的取舍" }) : p));
  eq("搜索 note_md", R.evaluate(noted, { q: "相位噪声" }).matched.map((p) => p.id), ["C"]);
  eq("搜索标签名", R.evaluate(lib, { q: "y" }).matched.map((p) => p.id), ["A", "C"]);
  eq("搜索大小写不敏感", R.evaluate(lib, { q: "PAPER E" }).matched.map((p) => p.id), ["E"]);
}

/* 13. 作者条件挂在表达式尾部，单个作者 = 一条 AND */
{
  const auth = lib.map((p) => {
    if (p.id === "A") return Object.assign({}, p, { authors: ["Bo Liu", "Jing Xia"] });
    if (p.id === "B") return Object.assign({}, p, { authors: ["Jing Xia"] });
    return p;
  });
  eq("作者条件：Jing Xia → A B", R.evaluate(auth, { authors: ["jing xia"] }).matched.map((p) => p.id), ["A", "B"]);
  eq("两个作者 = 都要满足", R.evaluate(auth, { authors: ["Bo Liu", "Jing Xia"] }).matched.map((p) => p.id), ["A"]);
}

/* 14. 计数不看搜索：项计数是「全库中满足该项的篇数」 */
{
  const r = R.evaluate(lib, {
    q: "paper b",
    groups: [tagGroup("@unfiled", "any", [], ["t_x", "t_y", "t_z"])],
  });
  eq("项计数不受搜索影响", r.groups[0].options.map((o) => o.count), [2, 2, 1]);
  eq("组脚仍看搜索（组未生效时为 null）", r.groups[0].foot, null);
}

/* 15. 近失：单点移除，按救回篇数倒序，不组合 */
{
  const f = {
    groups: [
      tagGroup("grp_1", "any", ["t_x"]),
      { kind: R.YEAR, op: "any", terms: [{ range: [2022, 2022] }], options: [] },
    ],
  };
  eq("近失的前提：零结果（x 都在 2020/2021，2022 那篇没有 x）", R.evaluate(lib, f).count, 0);
  const fixes = R.rescue(lib, f);
  eq("近失：最多 5 条", fixes.length <= 5, true);
  eq("近失：按救回数倒序", fixes.map((x) => x.rescued), [2, 1]);
  eq("近失：先给救回最多的（去掉年份区间 → A B）", fixes[0].group_id, R.YEAR_GROUP);
  eq("近失：再去掉标签项（→ C）", fixes[1].key, "t_x");
  eq("近失：只对当前选中的项做移除测试", fixes.every((x) => x.action === "remove"), true);
  fixes.forEach((x) => {
    eq("近失可一键应用：" + x.key, R.evaluate(lib, applyFix(f, x)).count > 0, true);
  });

  // 「全部」组里删掉一个项后救回多少，同样按单点算
  const allGroup = { groups: [tagGroup("grp_1", "all", ["t_x", "t_z"], ["t_x", "t_z"])] };
  eq("全部组：x 且 z 是空集", R.evaluate(lib, allGroup).count, 0);
  const allFixes = R.rescue(lib, allGroup);
  eq("全部组：删 z 救回 A B", allFixes.find((x) => x.key === "t_z").rescued, 2);
  eq("全部组：删 x 救回 E", allFixes.find((x) => x.key === "t_x").rescued, 1);
  eq("全部组：倒序", allFixes.map((x) => x.rescued), [2, 1]);

  // 不改写条件：不做两项一起删的组合测试
  eq("近失：条数不超过选中项数 + 年份未知", fixes.length <= 3, true);
}

function applyFix(f, x) {
  const next = {
    q: f.q,
    groups: f.groups.map((g) => Object.assign({}, g, { terms: g.terms.slice(), options: g.options || [] })),
  };
  const gi = next.groups.findIndex((g) => (g.group_id || R.GROUP_DEFAULTS[g.kind]) === x.group_id);
  if (x.action === "remove") {
    next.groups[gi].terms = next.groups[gi].terms.filter((t) => R.termKey(next.groups[gi].kind, t) !== x.key);
  } else {
    next.groups[gi].terms.push({ unknown: true });
  }
  return next;
}

/* 16. 近失里的「把年份未知加入」 */
{
  const f = { groups: [{ kind: R.YEAR, op: "any", terms: [{ range: [2030, 2035] }], options: [] }] };
  eq("只选未来区间 → 零结果", R.evaluate(lib, f).count, 0);
  const fixes = R.rescue(lib, f);
  eq("近失给出两条（去掉区间 / 加未知）", fixes.length, 2);
  eq("加未知救回 D", fixes.find((x) => x.key === "@unknown").rescued, 1);
}

/* ---------- 与朴素实现对拍 ---------- */
{
  // 确定性伪随机：测试要可复现
  let seed = 20261126;
  const rnd = () => {
    seed = (seed * 1103515245 + 12345) % 2147483648;
    return seed / 2147483648;
  };
  const pick = (n) => Math.floor(rnd() * n);

  const tags = ["t_a", "t_b", "t_c", "t_d"];
  const statuses = ["todo", "reading", "read"];
  const big = [];
  for (let i = 0; i < 60; i++) {
    const tagCount = pick(3);
    const ids = [];
    const names = [];
    for (let k = 0; k < tagCount; k++) {
      const t = tags[pick(tags.length)];
      if (ids.indexOf(t) === -1) {
        ids.push(t);
        names.push("tag_" + t.slice(2));
      }
    }
    const year = rnd() < 0.15 ? null : 1995 + pick(30);
    big.push(
      item("it" + i, {
        year: year,
        tagIds: ids,
        tags: names,
        status: statuses[pick(3)],
        starred: rnd() < 0.3,
        needsReview: rnd() < 0.2,
        authors: rnd() < 0.4 ? ["Bo Liu", "Jing Xia"] : ["Jing Xia"],
        note: rnd() < 0.2 ? "noise budget" : "",
        title: rnd() < 0.3 ? "Doherty amplifier " + i : "Paper " + i,
      })
    );
  }

  const combos = [];
  const tagOps = ["any", "all", "none"];
  const yearTerms = [
    [{ range: [2000, 2010] }],
    [{ range: [2010, 2025] }],
    [{ range: [2000, 2010] }, { unknown: true }],
    [{ unknown: true }],
  ];
  const attrTerms = [
    [{ status: "read" }],
    [{ status: "read" }, { status: "todo" }],
    [{ star: true }],
  ];
  const qs = ["", "doherty", "noise"];
  const modes = [null, "untagged", "review"];
  const auths = [[], ["Bo Liu"]];

  for (let a = 0; a < 3; a++) {
    for (let b = 0; b < yearTerms.length; b++) {
      for (let c = 0; c < 4; c++) {
        combos.push({
          q: qs[c % qs.length],
          mode: modes[c % modes.length],
          authors: auths[(a + c) % auths.length],
          groups: [
            tagGroup("grp_1", tagOps[a], [tags[b % 4], tags[(b + 1) % 4]], tags),
            { kind: R.YEAR, op: b === 3 ? "none" : "any", terms: yearTerms[b], options: [] },
            { kind: R.ATTR, op: a === 1 ? "all" : "any", terms: attrTerms[(a + b) % attrTerms.length], options: [] },
          ],
        });
      }
    }
  }
  // 空组、单组、纯搜索
  combos.push({ q: "", mode: null, authors: [], groups: [] });
  combos.push({ q: "doherty", mode: "untagged", authors: [], groups: [tagGroup("grp_1", "none", [], tags)] });
  combos.push({
    q: "",
    mode: null,
    authors: [],
    groups: [tagGroup("grp_1", "any", ["t_a"], tags), tagGroup("grp_2", "any", ["t_c"], tags)],
  });

  let compared = 0;
  combos.forEach((f, ci) => {
    // 朴素实现不知道 option 全集，用同一份 options 补上
    const nf = {
      q: f.q,
      mode: f.mode,
      authors: f.authors,
      groups: f.groups.map((g) => Object.assign({}, g, { options: g.options && g.options.length ? g.options : g.terms })),
    };
    const got = R.evaluate(big, f);
    const want = naive(big, nf);
    const gotIds = got.matched.map((p) => p.id).sort();
    const wantIds = want.matched.map((p) => p.id).sort();
    const sameIds = JSON.stringify(gotIds) === JSON.stringify(wantIds);
    if (!sameIds) {
      check("对拍结果集 #" + ci, gotIds, wantIds);
    } else {
      passes += 1;
    }
    const gotFeet = got.groups.map((g) => g.foot);
    const wantFeet = want.feet;
    if (JSON.stringify(gotFeet) !== JSON.stringify(wantFeet)) {
      check("对拍组脚 #" + ci, gotFeet, wantFeet);
    } else {
      passes += 1;
    }
    const gotCounts = got.groups.map((g) => g.options.map((o) => o.count));
    const wantCounts = want.counts;
    if (JSON.stringify(gotCounts) !== JSON.stringify(wantCounts)) {
      check("对拍项计数 #" + ci, gotCounts, wantCounts);
    } else {
      passes += 1;
    }
    compared += 1;
  });
  console.log("对拍组合 " + compared + " 个");

  // 组脚约束：最后一个非空组的组脚一定等于计数行
  combos.forEach((f, ci) => {
    const got = R.evaluate(big, f);
    const active = got.groups.filter((g) => g.active);
    if (!active.length) return;
    if (active[active.length - 1].foot !== got.count) {
      check("组脚 = 计数行 #" + ci, active[active.length - 1].foot, got.count);
    } else {
      passes += 1;
    }
  });

  // 计数单调性：项计数不会超过全库，边际值不会超过它前面的组脚
  combos.forEach((f, ci) => {
    const got = R.evaluate(big, f);
    got.groups.forEach((g) => {
      g.options.forEach((o) => {
        if (o.count > big.length) check("项计数不超过全库 #" + ci, o.count, big.length);
        else passes += 1;
      });
      if (g.marginal && g.active && g.foot !== null) {
        g.options.forEach((o) => {
          if (!o.selected && o.count > g.foot) check("边际值不超过组脚 #" + ci, o.count, g.foot);
          else passes += 1;
        });
      }
    });
  });
}

/* 17. 纯函数：不改入参 */
{
  const before = JSON.stringify(lib);
  const f = { groups: [tagGroup("@unfiled", "all", ["t_x", "t_y"], ["t_x", "t_y", "t_z"])] };
  const fBefore = JSON.stringify(f);
  R.evaluate(lib, f);
  R.rescue(lib, f);
  eq("evaluate 不改条目", JSON.stringify(lib), before);
  eq("evaluate 不改过滤器", JSON.stringify(f), fBefore);
}

/* 18. 老条目没有 topicIds 时，退回标签名判断未打标 */
{
  const legacy = [{ id: "L", title: "t", abstract: "", venue: "", authors: [], note: "", year: 2020, status: "todo", starred: false, needsReview: false, tags: { topic: [], year: ["2020"], author: [], org: [] } }];
  eq("没有 topicIds：空标签 = 未打标", R.evaluate(legacy, { mode: "untagged" }).count, 1);
}

console.log(
  (failures ? "\n" + failures + " 项失败，" : "\n") + passes + " 项断言通过" + (failures ? "" : "，全部通过")
);
if (failures) process.exit(1);
