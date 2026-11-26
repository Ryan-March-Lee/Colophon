/**
 * 回归：阅读器里「新建组 → 打标签」这条路径。
 *
 * 曾经的 bug：`groupFrom()` 只看 `state.groupNew`（工作台/详情页共用的标志），
 * 不看 `state.readerGroupNew`。在阅读器里进了「新建组」模式后，`#rtGroup`
 * 下拉已经被输入框替换、取不到值，于是返回 null，标签被静默丢进未分组，
 * 用户填的组名一次都没用上。
 *
 * 这个测试直接从 web/index.html 里抽出那四个函数跑，用最小 DOM 桩，
 * 不需要浏览器。运行：`node tests/group_logic.test.js`
 */
const fs = require("fs");
const path = require("path");

const htmlPath = path.join(__dirname, "..", "web", "index.html");
const html = fs.readFileSync(htmlPath, "utf8");
const start = html.lastIndexOf("<script>");
const end = html.lastIndexOf("</script>");
if (start < 0 || end < 0) throw new Error("找不到 <script> 块");
const code = html.slice(start + 8, end);

function grab(name) {
  const re = new RegExp("\\nfunction " + name + "[\\s\\S]*?\\n}\\n", "m");
  const m = code.match(re);
  if (!m) throw new Error("抽不到函数：" + name);
  return m[0];
}

const snippet = [
  grab("isNewGroupMode"),
  grab("setNewGroupMode"),
  grab("groupPickerHTML"),
  grab("groupFrom"),
].join("\n");

// ---- 最小桩 ----
const state = { groupNew: false, readerGroupNew: false, groups: [], groupSel: null };
const fields = {};
const nodes = {
  rtGroup: { value: "" },
  rtNewName: { value: "" },
  addGroup: { value: "" },
  addNewName: { value: "" },
  wbGroup: { value: "" },
  wbNewName: { value: "" },
};

function $find(sel) {
  const id = sel.replace("#", "");
  if (!(id in fields)) fields[id] = nodes[id] || null;
  return fields[id];
}

const factory = new Function(
  "state",
  "$",
  "esc",
  "localStorage",
  "document",
  snippet + "\nreturn { isNewGroupMode, setNewGroupMode, groupPickerHTML, groupFrom };"
);
const api = factory(
  state,
  $find,
  (s) => String(s),
  { getItem: () => null, setItem: () => {} },
  { querySelector: () => null }
);

let failures = 0;
function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  console.log((ok ? "PASS " : "FAIL ") + name);
  if (!ok) {
    failures += 1;
    console.log("  got     " + JSON.stringify(actual));
    console.log("  expected " + JSON.stringify(expected));
  }
}

// 1. 阅读器 / 新建组 + 有组名 → 必须带着组名，绝不能返回 null
api.setNewGroupMode("rt", true);
check("rt: 新建组模式已打开", api.isNewGroupMode("rt"), true);
nodes.rtNewName.value = "算法组";
check("rt: 有组名 → {name}", api.groupFrom("rt"), {
  id: null,
  name: "算法组",
  isNew: true,
});

// 2. 新建组 + 组名空 → undefined（调用方提示并停下，不静默进未分组）
nodes.rtNewName.value = "   ";
check("rt: 组名空 → undefined", api.groupFrom("rt"), undefined);

// 3. 面板之间互不牵连
check("wb: 不被 rt 牵连", api.isNewGroupMode("wb"), false);

// 4. 回到选组模式
api.setNewGroupMode("rt", false);
nodes.rtGroup.value = "";
check("rt: 选未分组 → {id:null}", api.groupFrom("rt"), { id: null });

nodes.rtGroup.value = "grp_x";
check("rt: 选组 → {id}", api.groupFrom("rt"), { id: "grp_x" });

// 5. 工作台自己的新建组模式
api.setNewGroupMode("wb", true);
check("wb: 新建组模式", api.isNewGroupMode("wb"), true);
check("rt: 未受 wb 影响", api.isNewGroupMode("rt"), false);

// 6. 面板重建后控件不在 → null，调用方不猜
api.setNewGroupMode("wb", false);
delete fields["wbGroup"];
delete nodes["wbGroup"];
check("wb: 控件不在 → null", api.groupFrom("wb"), null);

if (failures) {
    console.error("\n" + failures + " 项失败");
    process.exit(1);
}
console.log("\n全部通过");
