/* ============================================================
   Colophon · 检索内核（纯函数：不碰 DOM、不发请求、不改入参）

   语义见 docs/retrieval-model.md：
   - 组间固定 AND；组内 任一 / 全部 / 排除；空组不参与，不产生恒真或恒假。
   - 排除 = ¬(A∨B)，不是「不要 A」。
   - 年份项是区间或「未知」；年份未知的条目只在显式选中「未知」时命中。
   - 文本搜索、模式（未打标 / 待补全）与作者条件在布尔之外 AND。

   一次遍历同时算：
   - 结果集；
   - 组脚（按组区从上到下累计：到这一组为止还剩几篇）；
   - 项计数：任一 / 排除 = 全库绝对值；全部 = 加上这一项后的剩余（边际值）；
   - 「年份未知」三角的篇数（= 把它加进年份组能救回的篇数）；
   - 近失（§3）：单点移除当前选中的项后能救回多少篇。

   数据契约（/api/items 的条目 + 内核要的两样）：
     { id, title, abstract, venue, authors: [名字], note, year: number|null,
       status, starred, needsReview, topicIds: [标签 id], tags: { topic: [标签名] } }

   过滤器契约：
     { q: "", mode: null|"untagged"|"review", authors: [作者名],
       groups: [ { kind: "tag"|"year"|"attr"|"meta", group_id, op: "any"|"all"|"none",
                    terms: [已选项], options: [可点的项] } ] }
   元数据组用 @author / @org / @venue 作为 group_id，项是原始名称。

   terms 是已选，options 是界面上能点的全集（省略时 = terms）。
   saved_view.filter_json 只存 terms，不存 options——options 由标签库 / 数据现算。
   ============================================================ */
(function (root) {
  "use strict";

  var TAG = "tag";
  var YEAR = "year";
  var ATTR = "attr";
  var META = "meta";

  var UNFILED = "@unfiled";
  var YEAR_GROUP = "@year";
  var ATTR_GROUP = "@attr";
  var GROUP_DEFAULTS = { tag: UNFILED, year: YEAR_GROUP, attr: ATTR_GROUP };
  var MODES = { untagged: true, review: true };

  function metadataValues(item, field) {
    if (field === "author") return item.authors || [];
    if (field === "org") return (item.tags && item.tags.org) || [];
    if (field === "venue") return item.venue ? [item.venue] : [];
    return [];
  }

  function metadataKey(value) {
    return String(value).toLowerCase();
  }

  function emptyFilters() {
    return { q: "", mode: null, authors: [], groups: [] };
  }

  /* 属性组（年份 / 阅读状态 / 星标）没有「全部」：取值互斥，选「全部」等于空条件。
     这里把它折回「任一」，宁可退化成旧行为，也不让一个坏过滤器悄悄清空书库。 */
  function normalizeOp(kind, op) {
    if (op === "none") return "none";
    if (op === "all") return kind === TAG ? "all" : "any";
    return "any";
  }

  function yearRange(term) {
    if (typeof term === "number") return isFinite(term) ? [term, term] : null;
    if (!term || typeof term !== "object") return null;
    var range = term.range;
    if (!Array.isArray(range) || range.length !== 2) return null;
    var a = Number(range[0]);
    var b = Number(range[1]);
    if (!isFinite(a) || !isFinite(b)) return null;
    return a <= b ? [a, b] : [b, a];
  }

  /* 项的稳定身份：用于判断「这个选项已选中了吗」。
     标签用 id（改名、合并都不影响视图），年份用区间，属性用取值。 */
  function termKey(kind, term) {
    if (kind === TAG) return String(term);
    if (kind === META) return metadataKey(term);
    if (kind === YEAR) {
      if (term && typeof term === "object" && term.unknown) return "@unknown";
      var range = yearRange(term);
      return range ? range[0] + "-" + range[1] : "@invalid";
    }
    if (kind === ATTR && term && typeof term === "object") {
      if ("star" in term) return "star:" + (term.star === false ? "false" : "true");
      if (typeof term.status === "string") return "status:" + term.status;
    }
    return "@invalid";
  }

  function termPass(item, kind, term, groupId) {
    if (kind === TAG) return item.tagSet.has(String(term));
    if (kind === META) return !!item.metadata[groupId] && item.metadata[groupId].has(metadataKey(term));
    if (kind === YEAR) {
      if (term && typeof term === "object" && term.unknown) return item.year === null;
      var range = yearRange(term);
      return !!range && item.year !== null && item.year >= range[0] && item.year <= range[1];
    }
    if (kind === ATTR && term && typeof term === "object") {
      if ("star" in term) return item.starred === (term.star !== false);
      if (typeof term.status === "string") return item.status === term.status;
    }
    return false;
  }

  /* 条目只预处理一次：小写检索文本、标签 id 集合、年份已知与否。 */
  function prepItem(p) {
    var tags = p.tags || {};
    var topicNames = Array.isArray(tags.topic) ? tags.topic : [];
    var hasIds = Array.isArray(p.topicIds);
    var topicIds = hasIds ? p.topicIds : [];
    var authorNames = metadataValues(p, "author");
    var orgNames = metadataValues(p, "org");
    var hay = [
      p.title || "",
      p.abstract || "",
      p.venue || "",
      authorNames.join(" "),
      orgNames.join(" "),
      p.note || "",
      topicNames.join(" ")
    ].join(" ").toLowerCase();
    return {
      tagSet: new Set(topicIds.map(String)),
      untagged: hasIds ? topicIds.length === 0 : topicNames.length === 0,
      year: typeof p.year === "number" && isFinite(p.year) ? p.year : null,
      status: p.status || "todo",
      starred: !!p.starred,
      needsReview: !!p.needsReview,
      hay: hay,
      metadata: {
        "@author": new Set(authorNames.map(metadataKey)),
        "@org": new Set(orgNames.map(metadataKey)),
        "@venue": new Set(metadataValues(p, "venue").map(metadataKey))
      }
    };
  }

  function basePass(item, q, authors, mode) {
    if (mode === "untagged" && !item.untagged) return false;
    if (mode === "review" && !item.needsReview) return false;
    if (q && item.hay.indexOf(q) === -1) return false;
    for (var i = 0; i < authors.length; i++) {
      if (!item.metadata["@author"].has(authors[i])) return false;
    }
    return true;
  }

  function planGroups(filters) {
    var raw = filters.groups || [];
    return raw.map(function (g, index) {
      g = g || {};
      var kind = g.kind === YEAR || g.kind === ATTR || g.kind === META ? g.kind : TAG;
      var terms = Array.isArray(g.terms) ? g.terms : [];
      var options = Array.isArray(g.options) && g.options.length ? g.options : terms;
      var op = normalizeOp(kind, g.op);
      var selected = Object.create(null);
      terms.forEach(function (t) { selected[termKey(kind, t)] = true; });
      var metadataOptions = kind === META ? new Map(options.map(function (t, i) { return [metadataKey(t), i]; })) : null;
      return {
        index: index,
        group_id: g.group_id || GROUP_DEFAULTS[kind],
        kind: kind,
        op: op,
        terms: terms,
        options: options,
        selected: selected,
        metadataOptions: metadataOptions,
        /* 标签组的「全部」要算边际值；其余都是全库绝对值 */
        marginal: kind === TAG && op === "all"
      };
    });
  }

  /* 年份组的纠正入口：生效、操作符是「任一」、且没有显式选中「未知」时才找。 */
  function findYearWatch(groups) {
    for (var i = 0; i < groups.length; i++) {
      var g = groups[i];
      if (g.kind !== YEAR || !g.terms.length || g.op !== "any") continue;
      var hasUnknown = g.terms.some(function (t) { return termKey(YEAR, t) === "@unknown"; });
      if (!hasUnknown) return { group: i };
    }
    return { group: -1 };
  }

  function evaluate(items, filters) {
    var f = filters || emptyFilters();
    var list = Array.isArray(items) ? items : [];
    var q = String(f.q || "").trim().toLowerCase();
    var authors = (Array.isArray(f.authors) ? f.authors : []).map(function (a) {
      return String(a).toLowerCase();
    });
    var mode = MODES[f.mode] ? f.mode : null;

    var groups = planGroups(f);
    var counts = groups.map(function (g) { return g.options.map(function () { return 0; }); });
    var margins = groups.map(function (g) { return g.options.map(function () { return 0; }); });
    var feet = groups.map(function () { return 0; });
    var watch = findYearWatch(groups);

    var matched = [];
    var result = 0;
    var unknownYear = 0;

    for (var i = 0; i < list.length; i++) {
      var item = prepItem(list[i]);

      /* ① 选项对应的项：一次遍历，全库绝对值，不看搜索 / 模式 / 其它组 */
      var optPass = groups.map(function (g) {
        return g.kind === META ? null : g.options.map(function (term) { return termPass(item, g.kind, term); });
      });
      for (var gi = 0; gi < groups.length; gi++) {
        var g = groups[gi];
        if (g.kind === META) {
          var values = item.metadata[g.group_id];
          if (values) values.forEach(function (value) {
            var index = g.metadataOptions.get(value);
            if (index !== undefined) counts[gi][index] += 1;
          });
        } else if (!g.marginal) {
          for (var ci = 0; ci < g.options.length; ci++) {
            if (optPass[gi][ci]) counts[gi][ci] += 1;
          }
        }
      }

      /* ② 布尔之外的 AND：搜索、模式、作者选择器 */
      if (!basePass(item, q, authors, mode)) continue;

      /* ③ 组内命中与组条件 */
      var selPass = groups.map(function (gg) {
        return gg.terms.map(function (term) { return termPass(item, gg.kind, term, gg.group_id); });
      });
      var pass = groups.map(function (gg, gix) {
        if (!gg.terms.length) return true;
        var vals = selPass[gix];
        if (gg.op === "all") return vals.every(function (v) { return v; });
        var some = vals.some(function (v) { return v; });
        return gg.op === "none" ? !some : some;
      });

      var activeIdx = [];
      var failCount = 0;
      var failIdx = -1;
      for (var k = 0; k < groups.length; k++) {
        if (!groups[k].terms.length) continue;
        activeIdx.push(k);
        if (!pass[k]) {
          failCount += 1;
          if (failIdx < 0) failIdx = k;
        }
      }

      /* ④ 组脚：按显示顺序累计，最后一个非空组的组脚 = 计数行 */
      var running = true;
      for (var ak = 0; ak < activeIdx.length; ak++) {
        var footIdx = activeIdx[ak];
        if (!pass[footIdx]) running = false;
        if (running) feet[footIdx] += 1;
      }
      if (failCount === 0) {
        result += 1;
        matched.push(list[i]);
      }

      /* ⑤ 全部组的边际值：未选中的选项，加上它之后还剩几篇。
         本组没生效时也照算——空组不参与，但它的选项仍要有数字可点。 */
      for (var mi = 0; mi < groups.length; mi++) {
        var mg = groups[mi];
        if (!mg.marginal) continue;
        var othersOK = failCount === 0 || (failCount === 1 && failIdx === mi);
        if (!othersOK || !pass[mi]) continue;
        for (var mo = 0; mo < mg.options.length; mo++) {
          if (mg.selected[termKey(mg.kind, mg.options[mo])]) continue;
          if (optPass[mi][mo]) margins[mi][mo] += 1;
        }
      }

      /* ⑥ 年份未知：其它条件都过了、只被年份组挡住的篇数 */
      if (watch.group >= 0 && item.year === null) {
        var othersOKForYear = failCount === 0 || (failCount === 1 && failIdx === watch.group);
        if (othersOKForYear) unknownYear += 1;
      }
    }

    return {
      total: list.length,
      count: result,
      matched: matched,
      groups: groups.map(function (g, gix) {
        return {
          group_id: g.group_id,
          kind: g.kind,
          op: g.op,
          active: g.terms.length > 0,
          foot: g.terms.length ? feet[gix] : null,
          options: g.options.map(function (term, oi) {
            var key = termKey(g.kind, term);
            var isSelected = !!g.selected[key];
            return {
              term: term,
              key: key,
              selected: isSelected,
              marginal: g.marginal,
              count: g.marginal
                ? (isSelected ? feet[gix] : margins[gix][oi])
                : counts[gix][oi]
            };
          })
        };
      }),
      yearUnknown: {
        group_id: watch.group >= 0 ? groups[watch.group].group_id : null,
        count: unknownYear,
        show: watch.group >= 0 && unknownYear > 0
      }
    };
  }

  function cloneFilters(f) {
    return {
      q: f.q || "",
      mode: f.mode || null,
      authors: Array.isArray(f.authors) ? f.authors.slice() : [],
      groups: (f.groups || []).map(function (g) {
        g = g || {};
        return {
          kind: g.kind,
          group_id: g.group_id,
          op: g.op,
          terms: Array.isArray(g.terms) ? g.terms.slice() : [],
          options: Array.isArray(g.options) ? g.options.slice() : []
        };
      })
    };
  }

  /* §3 近失：只对当前选中的项做单点移除测试，不组合、不猜测、不改写条件。
     另加一条「把年份未知加进去」。只在零结果时调用也能用，但不是非零不可。 */
  function rescue(items, filters, limit) {
    var f = filters || emptyFilters();
    var max = typeof limit === "number" && limit > 0 ? limit : 5;
    var base = evaluate(items, f);
    var plans = planGroups(f);
    var out = [];

    (f.groups || []).forEach(function (g, gi) {
      var terms = (g && Array.isArray(g.terms)) ? g.terms : [];
      if (!plans[gi]) return;
      terms.forEach(function (term, ti) {
        var next = cloneFilters(f);
        next.groups[gi].terms.splice(ti, 1);
        var gain = evaluate(items, next).count - base.count;
        if (gain <= 0) return;
        out.push({
          action: "remove",
          group_id: plans[gi].group_id,
          kind: plans[gi].kind,
          op: plans[gi].op,
          term: term,
          key: termKey(plans[gi].kind, term),
          rescued: gain
        });
      });
    });

    var watch = findYearWatch(plans);
    if (watch.group >= 0) {
      var nextYear = cloneFilters(f);
      nextYear.groups[watch.group].terms.push({ unknown: true });
      var gainYear = evaluate(items, nextYear).count - base.count;
      if (gainYear > 0) {
        out.push({
          action: "add",
          group_id: plans[watch.group].group_id,
          kind: YEAR,
          op: "any",
          term: { unknown: true },
          key: "@unknown",
          rescued: gainYear
        });
      }
    }

    out.sort(function (a, b) {
      if (b.rescued !== a.rescued) return b.rescued - a.rescued;
      return a.group_id === b.group_id ? 0 : (a.group_id < b.group_id ? -1 : 1);
    });
    return out.slice(0, max);
  }

  var api = {
    TAG: TAG,
    YEAR: YEAR,
    ATTR: ATTR,
    META: META,
    UNFILED: UNFILED,
    YEAR_GROUP: YEAR_GROUP,
    ATTR_GROUP: ATTR_GROUP,
    GROUP_DEFAULTS: GROUP_DEFAULTS,
    emptyFilters: emptyFilters,
    metadataValues: metadataValues,
    termKey: termKey,
    evaluate: evaluate,
    rescue: rescue
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.ColophonRetrieval = api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
