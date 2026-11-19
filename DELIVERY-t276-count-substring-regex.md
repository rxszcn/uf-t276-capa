# 子串/正则计数语句恒为零的静默失效：定位与修复（t276-capa）

## 一、现象与根因

### 现象

检测规则里的子串计数与正则计数语句，例如：

```yaml
features:
  - and:
      - count(substring(evil.example/)): 3
      - count(string(/evil\.example\/[a-z]+/)): 2 or more
```

解析器接受、索引层正常建库、引擎不抛异常也不告警，但**全部样本上计数恒为零**，整条语句永不匹配。对照组现象：

- 精确字符串特征的计数约束（`count(string(http://evil.example/a)): 2`）正常生效；
- 不套计数约束的子串/正则特征（`- substring: evil.example/`）正常匹配。

即失效面精确落在"**计数的子串/正则**"这一组合上，属于静默失效：规则作者的计数门槛形同虚设，依赖该语句的检测规则全部失效而无任何告警。

### 根因

`capa/engine.py` 中 `Range.evaluate()` 直接用哈希查表取计数：

```python
count = len(features.get(self.child, []))
```

这对哈希类特征（`String`/`Number`/`Mnemonic`/`Bytes` 等）成立，因为它们是 `FeatureSet` 的键。但 `Substring`/`Regex` **从不作为 `FeatureSet` 的键存在**——特征集里放的是扫描所依据的具体 `String` 值（如 `String("http://evil.example/a")`），子串/正则特征本身是"扫描谓词"，只通过 `evaluate()` 遍历这些 `String` 值来匹配。

因此 `features.get(Substring(...), [])` 与 `features.get(Regex(...), [])` 永远返回空列表，`count` 恒为 0：

- `min >= 1` 的计数语句恒为假 → 永不匹配（本次故障）；
- `min == 0` 且命中为 0 时走短路返回真——零命中时"碰巧正确"，但一旦真的有命中反而仍判 0，与 `max` 约束相矛盾（见第 5 节 `min_zero_violated` 读数）。

`count(string(/.../))` 同理：解析阶段 `StringFactory` 把 `/.../` 解析为 `Regex`，于是 `Range.child` 是 `Regex`，落入同一失效路径。不带计数的 `Substring`/`Regex` 直接 `evaluate()`，走的是正常扫描逻辑，故正常匹配——这解释了对照组的差异。

## 二、修复设计

改动集中在 `capa/engine.py` 的 `Range` 语句，其余求值/索引/渲染路径不变。

新增 `Range._locate()`，按特征种类分派取"命中位置集合"：

| 特征种类 | 取位置方式 | 说明 |
| --- | --- | --- |
| `Substring` / `Regex` | `child.evaluate(features, short_circuit=False).locations` | 扫描全部 `String` 值，收集所有命中位置 |
| 其他哈希类特征 | `features.get(child, [])` | 维持原逻辑 |

`evaluate()` 改为 `count = len(locations)`，并让 `Result.locations` 在成功/失败时都带上这组位置（原失败分支同样传 `features.get(...)`，语义不变）。

关键语义点：

1. **必须关闭短路**：计数需要完整命中集合，若沿用默认 `short_circuit=True`，`Substring.evaluate()` 命中第一个字符串值就 `break`，会把门槛悄悄降低。故对子串/正则调用时显式传 `short_circuit=False`。
2. **聚合口径 = 去重后的命中位置数**：`Substring.evaluate()`/`Regex.evaluate()` 返回的 `locations` 已是所有匹配字符串值的位置并集（`frozenset`），同一地址被多个匹配值命中只计一次，同一字符串值内多次出现也只按该值记录的位置计。这保证"按特征命中位置数求值"。
3. **`max` 上限语义不变**：仍是 `self.min <= count <= self.max`。
4. **`min == 0` 且 `count == 0` 短路不变**：仍返回 `Result(True, self, [])`，位置为空。
5. **精确字符串计数不回归**：走 `features.get(child, [])` 原路径。
6. **无计数路径不回归**：`Substring`/`Regex.evaluate()` 本身未改动，直连使用时行为原样。
7. **与 `and`/`or`/`not` 组合**：`Range` 作为 `Statement` 的求值契约未变，组合语句的嵌套求值自然一致；短路语义由父语句控制，`Range` 内部对子特征始终取全量。

## 三、改动面（按文件登记）

| 文件 | 改动 |
| --- | --- |
| `capa/engine.py` | `Range` 新增 `_locate()`；`evaluate()` 改用 `_locate()` 取位置并以其长度作为计数；成功/失败均回填 `locations`。 |
| `tests/test_engine.py` | 追加 `test_range_counts_substring_locations`、`test_range_counts_regex_locations`、`test_range_counts_exact_string_for_contrast`、`test_range_min_zero_short_circuit` 四个单元用例（导入 `Range`/`String`/`Substring`/`Regex`）。 |
| `tests/test_match.py` | 追加 `test_count_substring_substring_spread_over_values_and_locations`、`test_count_regex_spread_over_values_and_locations`、`test_count_exact_string_for_contrast`、`test_count_min_zero_short_circuit`、`test_count_substring_and_regex_nested_in_and_or_not` 五个端到端用例，均经 `match()`（同时校验 `capa.engine.match` 与 `RuleSet.match` 两条路径一致）。 |

未改动：解析器（`capa/rules/__init__.py`）、索引层、渲染层、`Substring`/`Regex` 自身求值。索引层对计数子串/正则的既有处理（`_index_rules_by_feature` 的 `Range` 递归取 child、`string_rules`/字符串字面量预过滤）无需调整，因为本次是求值口径问题而非候选筛选问题。

## 四、验收命令与最终读数

```
python -m pytest tests/test_engine.py tests/test_match.py -q
```

最终读数：

```
37 passed, 2 xfailed in 0.76s
```

- 37 通过包含原有 28 项与本次新增 9 项（engine 4 + match 5）。
- 2 个 xfailed 是既有 `test_match_only_not` / `test_match_not_not` 的预期失败标记，与本次改动无关。
- 零失败。

补充回归（均与本改动前基线一致）：

- `tests/test_match_fixtures.py`：`106 passed`。
- `tests/test_result_document.py tests/test_render.py tests/test_optimizer.py tests/test_rules.py`：`9 failed, 98 passed`，9 个失败均因快照环境缺少 `tests/data` 样本文件（`FileNotFoundError`），`git stash` 后同样失败，与本改动无关。
- `ruff check --config .github/ruff.toml` 与 `ruff format --check` 对三个改动文件全部通过。

## 五、构造输入下的实测读数

### 夹具

快照环境内零外部依赖的纯引擎级夹具：`work/probes/t276_count_fixture.py`。不依赖 extractor、样本文件或网络，仅构造 `FeatureSet` 后直接求值。以 `python -I probes/t276_count_fixture.py <repo-root>` 运行（`-I` 隔离用户 site 与 cwd，避免环境干扰）。

规则（一条含子串计数门槛与正则计数门槛，并附精确字符串计数对照）：

```yaml
features:
  - and:
      - count(substring(evil.example/)): 3
      - count(string(/evil\.example\/[a-z]+/)): 2 or more
      - count(string(http://evil.exmple/a)): 2      # 精确字符串对照
```

样本集：同一子串 `evil.example/` 散布在多个字符串值、多个命中位置。读数口径为"子串与正则两类计数语句逐样本的计数求值读数"，每格记 `count/success`。

### 修复前 → 修复后逐样本读数

| 样本 | 子串计数（min=3,max=3） | 正则计数（min=2+） | 精确字符串计数（min=max=2） | 规则命中 |
| --- | --- | --- | --- | --- |
| `hit`<br>位置：a→{1,2}，b→{3} | 0/False → **3/True** | 0/False → **3/True** | 2/True → 2/True | False → **True** |
| `substring_below_min`<br>子串仅 2 个位置 | 0/False → 2/False | 0/False → **2/True** | 2/True → 2/True | False → False |
| `regex_below_min`<br>子串 3 位、正则 1 位 | 0/False → **3/True** | 0/False → 1/False | 1/False → 1/False | False → False |
| `shared_location`<br>两值同址，子串去重后 1 位 | 0/False → 1/False | 0/False → 1/False | 1/False → 1/False | False → False |
| `no_match`<br>无任何命中 | 0/False → 0/False | 0/False → 0/False | 0/False → 0/False | False → False |
| `min_zero_present`<br>无命中 + `number: 1` | 0/False → 0/False | 0/False → 0/False | 0/False → 0/False | 规则 False；**min=0 语句 True**（前后一致） |
| `min_zero_violated`<br>有命中 + `number: 1` | 0/False → 0/False | 0/False → 0/False | 0/False → 0/False | 规则 False；**min=0 语句 True → False** |

要点：

- `hit` 是故障的正面复现：修复前子串/正则计数均为 0、规则不命中；修复后计数分别为 3、3，规则命中。精确字符串对照前后都是 2/True，证明计数路径本身没有回归。
- `shared_location` 验证"按位置数而非字符串值数"聚合：两个字符串值都含子串但位置相同，计数为 1 而非 2。
- `substring_below_min` / `regex_below_min` 验证 `min`/`max` 与各类计数正确联动，且两类计数各自独立求值。
- `min_zero_present` 验证 `min == 0` 且零命中时短路成立；`min_zero_violated` 暴露了修复前的连带 bug——有命中时 `min=0, max=0` 语句竟也判真（因为计数恒 0 落入短路），修复后正确判假。这属于"`max` 上限语义保持一致"的一部分。
- 表末两行的"规则命中"栏为主规则（含 min=3 子串门槛）的判定，`min=0` 语句的结论单独记在前文"min=0 语句"子栏。

### 引擎与索引双路径一致性

夹具对每个样本同时跑 `capa.engine.match()` 与 `capa.rules.RuleSet.match()`（索引加速路径），7 个样本两路径判定完全一致；`hit` 样本两路径均由不命中变为命中。端到端测试 `match()` 辅助函数内部亦对两条路径逐特征、逐规则比对。

## 六、回归用例清单（与要求逐条对应）

要求："引擎求值与匹配面既有 `tests/test_engine.py` 与 `tests/test_match.py` 测试文件追加子串计数与正则计数、精确字符串计数对照与 min 零短路两类断言用例"。

| 要求 | 用例 |
| --- | --- |
| 子串计数 | `test_range_counts_substring_locations`（engine）、`test_count_substring_substring_spread_over_values_and_locations`（match） |
| 正则计数 | `test_range_counts_regex_locations`（engine）、`test_count_regex_spread_over_values_and_locations`（match） |
| 精确字符串计数对照 | `test_range_counts_exact_string_for_contrast`（engine）、`test_count_exact_string_for_contrast`（match） |
| min 零短路 | `test_range_min_zero_short_circuit`（engine）、`test_count_min_zero_short_circuit`（match） |
| and/or/not 嵌套（处理面） | `test_count_substring_and_regex_nested_in_and_or_not`（match） |

四类用例并入既有测试文件，沿用同一 pytest 框架与既有夹具风格；既有用例保持通过。

## 七、遗留说明

- `RuleSet.match()` 对"顶层仅由零计数语句构成"的规则无法建索引（`_index_rules_by_feature` 中 `assert len(scores) > 0`），这是既有行为（文档亦在 `RuleSet.match` 注释中标注"top level counted features with zero occurances"为不支持场景），不在本次处理面内。`test_count_min_zero_short_circuit` 因此配一个 `number: 1` 种子特征以走索引路径。
- 本次未修改任何规则文件；修复对所有使用计数子串/正则的规则统一生效。
