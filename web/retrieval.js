/* ============================================================
   Colophon · 检索内核（纯函数：不碰 DOM、不发请求、不改入参）

   语义见 docs/retrieval-model.md：
   - 标签展示组不参与检索；全局标签正向 AND / OR 与独立排除条件组合。
   - 排除集合 = ¬(A∨B)，即结果不能包含任一排除标签。
   - 年份区间与单年取并集，未知年份由 includeUnknown 控制（默认包含）。
   - 文本搜索、整理状态与作者条件在布尔之外 AND。

   一次遍历同时算：
   - 结果集；
   - 筛选组脚（按展示顺序累计）；
   - 可选项的全库绝对计数。
   近失（§3）通过单独求值测试每个调整能救回多少篇。

   数据契约（/api/items 的条目 + 内核要的两样）：
     { id, title, abstract, venue, authors: [名字], note, year: number|null,
       starred, needsReview, topicIds: [标签 id], tags: { topic: [标签名] } }

   过滤器契约：
      { q: "", mode: null|"review"|"untagged"|"tagged", authors: [作者名],
        tagQuery: { op: "and"|"or", include: [标签 id], exclude: [标签 id] },
        groups: [ { kind: "tag"|"year"|"attr"|"meta", group_id, op: "any"|"all"|"none",
                    terms: [已选项], options: [可点的项] } ] }
   元数据组用 @author / @org / @venue 作为 group_id，项是原始名称。
   年份组不用 op，terms 是区间或数字年份，includeUnknown 默认为 true。
   星标（@star）作为独立的属性组，最多一个已选项。

   terms 是已选，options 是界面上能点的全集（省略时 = terms）。
   saved_view.filter_json 存已选条件，不存 options——options 由标签库 / 数据现算。
   ============================================================ */
(function (root) {
  "use strict";

  var TAG = "tag";
  var YEAR = "year";
  var ATTR = "attr";
  var META = "meta";

  var UNFILED = "@unfiled";
  var YEAR_GROUP = "@year";
  var STAR_GROUP = "@star";
  var GROUP_DEFAULTS = { tag: UNFILED, year: YEAR_GROUP, attr: STAR_GROUP };
  var MODES = { untagged: true, review: true, tagged: true };

  function metadataValues(item, field) {
    if (field === "author") return item.authors || [];
    if (field === "org") return (item.tags && item.tags.org) || [];
    if (field === "venue") return item.venue ? [item.venue] : [];
    return [];
  }

  function metadataKey(value) {
    return String(value).toLowerCase();
  }

  function labelState(item) {
    var ids = Array.isArray(item.topicIds) ? item.topicIds : [];
    var topics = item.tags && Array.isArray(item.tags.topic) ? item.tags.topic : [];
    if (item.needsReview) return "review";
    return ids.length || topics.length ? "tagged" : "untagged";
  }

  function emptyFilters() {
    return { q: "", mode: null, authors: [], groups: [] };
  }

  /* 只有标签组支持「全部」；年份组固定匹配，不使用操作符。 */
  function normalizeOp(kind, op) {
    if (kind === YEAR) return "any";
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
      // 即使区间端点相等，区间与独立单年也可以分别移除。
      if (typeof term === "number" && isFinite(term)) return "year:" + term;
      var range = yearRange(term);
      return range ? range[0] + "-" + range[1] : "@invalid";
    }
    if (kind === ATTR && term && typeof term === "object") {
      if ("star" in term) return "star:" + (term.star === false ? "false" : "true");
    }
    return "@invalid";
  }

  function termPass(item, kind, term, groupId) {
    if (kind === TAG) return item.tagSet.has(String(term));
    if (kind === META) return !!item.metadata[groupId] && item.metadata[groupId].has(metadataKey(term));
    if (kind === YEAR) {
      var range = yearRange(term);
      return !!range && item.year !== null && item.year >= range[0] && item.year <= range[1];
    }
    if (kind === ATTR && term && typeof term === "object") {
      if ("star" in term) return item.starred === (term.star !== false);
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
      labelState: labelState(p),
      year: typeof p.year === "number" && isFinite(p.year) ? p.year : null,
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
    if (mode && item.labelState !== mode) return false;
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
      if (kind === YEAR) terms = terms.filter(function (t) { return yearRange(t) !== null; });
      var options = Array.isArray(g.options) && g.options.length ? g.options : terms;
      var op = normalizeOp(kind, g.op);
      var includeUnknown = g.includeUnknown !== false;
      var selected = Object.create(null);
      terms.forEach(function (t) { selected[termKey(kind, t)] = true; });
      var metadataOptions = kind === META ? new Map(options.map(function (t, i) { return [metadataKey(t), i]; })) : null;
      return {
        index: index,
        group_id: g.group_id || GROUP_DEFAULTS[kind],
        kind: kind,
        op: op,
        terms: terms,
        includeUnknown: includeUnknown,
        active: terms.length > 0 || (kind === YEAR && !includeUnknown),
        options: options,
        selected: selected,
        metadataOptions: metadataOptions,
        /* 标签组的「全部」要算边际值；其余都是全库绝对值 */
        marginal: kind === TAG && op === "all"
      };
    });
  }

  function evaluate(items, filters) {
    var f = filters || emptyFilters();
    var list = Array.isArray(items) ? items : [];
    var q = String(f.q || "").trim().toLowerCase();
    var authors = (Array.isArray(f.authors) ? f.authors : []).map(function (a) {
      return String(a).toLowerCase();
    });
    var mode = MODES[f.mode] ? f.mode : null;

    var groups = [];
    var tagQuery = f.tagQuery && typeof f.tagQuery === "object" ? f.tagQuery : null;
    if (tagQuery) {
      var includeTags = Array.isArray(tagQuery.include) ? tagQuery.include.map(String) : [];
      var excludeTags = Array.isArray(tagQuery.exclude) ? tagQuery.exclude.map(String) : [];
      var includeOp = tagQuery.op === "or" ? "any" : "all";
      if (includeTags.length) groups.push({
        index: groups.length, group_id: "@tags", kind: TAG, op: includeOp,
        terms: includeTags, includeUnknown: true, active: true, options: includeTags,
        selected: Object.create(null), metadataOptions: null, marginal: false
      });
      if (excludeTags.length) groups.push({
        index: groups.length, group_id: "@excluded-tags", kind: TAG, op: "none",
        terms: excludeTags, includeUnknown: true, active: true, options: excludeTags,
        selected: Object.create(null), metadataOptions: null, marginal: false
      });
    }
    var displayOffset = groups.length;
    groups = groups.concat(planGroups(f));
    var counts = groups.map(function (g) { return g.options.map(function () { return 0; }); });
    var margins = groups.map(function (g) { return g.options.map(function () { return 0; }); });
    var feet = groups.map(function () { return 0; });
    var matched = [];
    var result = 0;

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
        var vals = selPass[gix];
        if (gg.kind === YEAR) {
          if (item.year === null) return gg.includeUnknown;
          return !vals.length || vals.some(function (v) { return v; });
        }
        if (!gg.terms.length) return true;
        if (gg.op === "all") return vals.every(function (v) { return v; });
        var some = vals.some(function (v) { return v; });
        return gg.op === "none" ? !some : some;
      });

      var activeIdx = [];
      var failCount = 0;
      var failIdx = -1;
      for (var k = 0; k < groups.length; k++) {
        if (!groups[k].active) continue;
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
    }

    return {
      total: list.length,
      count: result,
      matched: matched,
      groups: groups.slice(displayOffset).map(function (g, relativeIndex) {
        var gix = relativeIndex + displayOffset;
        return {
          group_id: g.group_id,
          kind: g.kind,
          op: g.op,
          active: g.active,
          foot: g.active ? feet[gix] : null,
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
      })
    };
  }

  function cloneFilters(f) {
    return {
      q: f.q || "",
      mode: f.mode || null,
      authors: Array.isArray(f.authors) ? f.authors.slice() : [],
      tagQuery: f.tagQuery ? {
        op: f.tagQuery.op === "or" ? "or" : "and",
        include: Array.isArray(f.tagQuery.include) ? f.tagQuery.include.slice() : [],
        exclude: Array.isArray(f.tagQuery.exclude) ? f.tagQuery.exclude.slice() : []
      } : null,
      groups: (f.groups || []).map(function (g) {
        g = g || {};
        return {
          kind: g.kind,
          group_id: g.group_id,
          op: g.op,
          includeUnknown: g.includeUnknown,
          terms: Array.isArray(g.terms) ? g.terms.slice() : [],
          options: Array.isArray(g.options) ? g.options.slice() : []
        };
      })
    };
  }

  /* §3 近失：只对当前选中的项做单点移除测试，不组合、不猜测、不改写条件。
     若未勾选「包含未知年份」，也测试勾选。 */
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

    if (f.tagQuery) {
      ["include", "exclude"].forEach(function (field) {
        (f.tagQuery[field] || []).forEach(function (term, ti) {
          var next = cloneFilters(f);
          next.tagQuery[field].splice(ti, 1);
          var gain = evaluate(items, next).count - base.count;
          if (gain <= 0) return;
          out.push({
            action: "remove-tag", field: field, group_id: field === "exclude" ? "@excluded-tags" : "@tags",
            kind: TAG, op: f.tagQuery.op === "or" ? "any" : "all", term: term,
            key: String(term), rescued: gain
          });
        });
      });
    }

    plans.forEach(function (g, gi) {
      if (g.kind !== YEAR || g.includeUnknown) return;
      var nextYear = cloneFilters(f);
      nextYear.groups[gi].includeUnknown = true;
      var gainYear = evaluate(items, nextYear).count - base.count;
      if (gainYear > 0) {
        out.push({
          action: "include-unknown",
          group_id: g.group_id,
          kind: YEAR,
          key: "@unknown",
          rescued: gainYear
        });
      }
    });

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
    STAR_GROUP: STAR_GROUP,
    GROUP_DEFAULTS: GROUP_DEFAULTS,
    emptyFilters: emptyFilters,
    metadataValues: metadataValues,
    labelState: labelState,
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
