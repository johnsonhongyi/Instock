# InStock 任务跟踪与备忘 (gemini.md)

## [2026-10-08 00:32] 本地独立管理 ENVIRONMENT_HANDOFF.md 版本记录配置（方案A）

- **任务背景**：用户在本地复制了 `instock/ENVIRONMENT_HANDOFF.md`，需要拥有完整的本地版本追溯与对比能力，但坚决不同步/推送到远程服务器。
- **完成动作**：
  1. 在主仓库 [.gitignore](file:///d:/MacTools/WorkFile/WorkSpace/InStock/.gitignore) 中添加 `.git_handoff/` 和 `instock/ENVIRONMENT_HANDOFF.md` 忽略规则，阻断主仓库远程同步。
  2. 初始化本地独立版本库 `.git_handoff`（无 remote 地址，物理隔绝远端）。
  3. 配置 `.git_handoff/info/exclude` 仅管理 handoff 文档，屏蔽工程内其他无关文件干扰。
  4. 完成首个版本提交：`02df761 docs: 初始化 ENVIRONMENT_HANDOFF.md 本地记录`。

## [2026-10-08 00:39] 升级为方案 B（VS Code 源代码管理可视化多仓库）

- **需求演进**：用户希望在 VS Code 的“源代码管理（Git 面板）”中直接可视化操作（查看 diff、点按提交），无需频繁使用命令行。
- **完成动作**：
  1. 建立根目录专属独立 Git 仓库 `handoff/`（内含标准 `.git/`，无 remote）。
  2. 建立 Windows 硬链接 `instock/ENVIRONMENT_HANDOFF.md` <-> `handoff/ENVIRONMENT_HANDOFF.md`，两处路径完全互通同步，不破坏原有目录结构。
  3. 主仓库 [.gitignore](file:///d:/MacTools/WorkFile/WorkSpace/InStock/.gitignore) 保持忽略 `handoff/` 与 `instock/ENVIRONMENT_HANDOFF.md`，彻底杜绝同步到远程。
  4. 配置 [.vscode/settings.json](file:///d:/MacTools/WorkFile/WorkSpace/InStock/.vscode/settings.json) 添加 `"git.scanRepositories": ["handoff"]`，让 VS Code 侧边栏自动多仓库分栏显示。
  5. 完成首个版本提交：`dc66186 docs: 初始化 ENVIRONMENT_HANDOFF.md 本地记录`。

## [2026-10-08 12:51] 策略刷新失败与自动运行异常日志排查

- **任务背景**：用户反馈今天的自动运行策略没有成功，手动执行策略刷新报错：“开始：2026-10-08 12:43:39；结束：2026-10-08 12:43:42；用时：0分3.4秒；退出码：1”。要求查看运行日志，不运行其他策略，也不执行回测数据补充任务。
- **排查目标**：
  1. 定位 PVE LXC 102 inStock 容器内的策略运行日志及 web 日志（特别是 manual_strategy_refresh.log 及定时任务日志）。
  2. 提取 2026-10-08 12:43 及早间自动运行的具体失败堆栈与错误原因。
  3. 严守约束：只读分析，绝不启动策略或数据回测补充任务。
  4. 给出根本原因分析与修复建议。
- **排查结论**：
  1. **手动刷新报错日志（`manual_strategy_refresh.log`）**：
     - 时间：2026-10-08 12:43:40 - 12:43:42
     - 报错：`stockfetch.fetch_stocks Sina行情覆盖不足: 0/3900`
     - 抛出：`RuntimeError: 股票行情列表为空，保留已有策略结果`（退出码 1，耗时约 3.4 秒）。
  2. **自动运行报错日志（`stock_enter_job.log`）**：
     - 时间：2026-10-08 09:26、10:30、11:00、11:30
     - 均抛出完全相同的错误：`Sina行情覆盖不足: 0/3900` -> `RuntimeError: 股票行情列表为空，保留已有策略结果`。
  3. **根本原因（Root Cause）**：
     - 今天 2026-10-08 是国庆长假后开市首日，9:30 开盘后新浪（Sina）行情返回的快照日期 `dt` 全为当天 `2026-10-08`。
     - 但 `instock/job/strategy_enter-edit.py` 中的 `_strategy_run_dates()` 无参调用了 `trd.get_trade_date_last()[0]`。
     - `get_trade_date_last(websrv=False)` 在盘中（未到 15:00 收盘前）第 0 项返回的是**上一个已收盘交易日（2026-09-30）**，第 1 项 `run_date_nph` 才是当天（2026-10-08）。
     - 代码错误取了 `[0]`，导致在盘中获取了 `2026-09-30`，去比对新浪实时行情中的 `2026-10-08`，匹配行数为 0，直接触发覆盖不足保护而报错退出。

## [2026-10-08 13:14] 策略运行日期 Bug 修复与全面审核

- **任务背景**：用户要求修复策略刷新与定时扫描在盘中取错交易日的 Bug，并进行全面代码与逻辑审核。
- **约束边界**：绝不运行其他策略，绝不执行回测数据补充任务；保持接口完全兼容；代码保持 UTF-8 编码。
- **执行计划**：
  1. 全面审核 `instock` 中关于交易日判定、盘中实时与盘后历史日期的关联逻辑（包括 `strategy_enter-edit.py`、`trade_time.py`、`dataTableHandler.py` 等）。
  2. 实施精准最小修复（KISS）：
     - 修复 `_strategy_run_dates()` 在开盘盘中时取当天交易日 `run_date_nph`。
     - 修复 `_stream_strategy_enter()` 中 `latest` 的定义，使其在盘中与当前交易日保持一致。
     - 检查 `build_strategy_snapshot()` 的盘中逻辑兼容性。
  3. 本地语法与只读逻辑测试验证。
  4. 按照标准环境规范将修改同步至 PVE LXC 102 容器并在容器内进行非破坏性验证（语法、日期解析验证）。
  5. 输出审核与交付报告。
- **完成成果**：
  1. 修复落地：在 [`instock/job/strategy_enter-edit.py`](file:///d:/MacTools/WorkFile/WorkSpace/InStock/instock/job/strategy_enter-edit.py) 提取 `_current_strategy_date()`，统筹盘中（>= 09:25）、盘后及非交易日逻辑，修复 `_strategy_run_dates()`、`epoch` 与 `latest` 的强一致性。
  2. 规范同步：依 `ENVIRONMENT_HANDOFF.md` 持共享锁 `/data/InStock/instock/cache/strategy_enter.lock` 进行原子备份与同步，容器内备份留存至 `/tmp/inStock-git-sync-20261008-v2/backup/strategy_enter-edit.py.bak`。
  3. 双端一致：本地与容器内 SHA256 精确一致为 `595de414669e43ebf1ebb16c20395b9a817975bea6f82b93354137ba1da6f108`。
  4. 只读验证：容器内只读调用 `_strategy_run_dates()` 正确解析为 `[datetime.date(2026, 10, 8)]`；未触发任何策略运行，未执行任何回测补充任务。

## [2026-10-08 13:52] keep_increasing 策略 indexer out-of-bounds 异常排查与修复

- **任务背景**：日期 Bug 修复后，用户在 Web 端手动触发策略刷新，`cn_stock_strategy_enter` 正常跑通，但进入 `cn_stock_strategy_keep_increasing`（均线多头）计算时，报 `single positional indexer is out-of-bounds`，且 127 只股票中有 19 只失败（超过 1% 门禁），抛出 `RuntimeError: cn_stock_strategy_keep_increasing逐股计算失败过多：19/127`。
- **排查结论**：
  1. `stocks_data_to_realtime` 将包含 `volume_ratio` 的实时行与不包含该列的历史日线数据通过 `pd.concat` 合并时，历史日线的 `volume_ratio` 全部为 `NaN`。
  2. `keep_increasing.py` 中 `get_tdx_stock_period_to_type` 将日线重采样为月线 `dataM` 后执行全表 `dropna()`，由于历史月份的 `volume_ratio` 全是 `NaN`，导致所有历史月份被一刀切删除，`dataM` 被清空至仅剩当前月（1 行）。
  3. `check_macd_status(dataM)` 访问 `data.iloc[-2]` 和 `data.iloc[-3]` 发生越界异常（Indexer out of bounds）。
- **完成成果**：
  1. 修复落地：
     - 在 [`instock/job/strategy_enter-edit.py`](file:///d:/MacTools/WorkFile/WorkSpace/InStock/instock/job/strategy_enter-edit.py) 中的 `stocks_data_to_realtime` 对 `volume_ratio` 与 `p_change` 进行 `fillna` 保护。
     - 在 [`instock/core/strategy/keep_increasing.py`](file:///d:/MacTools/WorkFile/WorkSpace/InStock/instock/core/strategy/keep_increasing.py) 中的 `get_tdx_stock_period_to_type` 优化重采样 dropna 范围仅限于核心量价列并进行 fillna(0.0)；并在 `check_macd_status`、`check_rsi_status` 与周线计算中增加 `len < 3` / `len < 4` 的安全边界门禁。
  2. 规范同步：依 `ENVIRONMENT_HANDOFF.md` 持共享锁 `/data/InStock/instock/cache/strategy_enter.lock` 进行原子备份与同步，容器内备份留存至 `/tmp/inStock-git-sync-20261008-v3/backup/`。
  3. 双端一致：
     - `strategy_enter-edit.py` SHA256: `8ec22fcd250304df43ffb5f7015f15002dc4886cc5fdbbe18b02837ab93b2cce`
     - `keep_increasing.py` SHA256: `ec0c3c98aa1c04e1e71daba6b0d1873dcfd97652a0f00c0283f523c6b431cf47`
  4. 只读抽样验证：在容器内对此前报错的 10 只样本股票进行只读测试，10/10 全部顺利通过无任何异常，其中 `600400` 正常命中均线多头策略。未触发全量策略，未执行回测补充任务。

## [2026-10-08 14:38] 本地 Git 变更视图差异释疑与容器端同步状态双向核验

- **用户疑问**：
  1. VS Code 源代码管理面板暂存区仅显示 `gemini.md` 与 `strategy_enter-edit.py`，未看到 `keep_increasing.py`。
  2. 询问具体更新了什么内容。
  3. 询问 Docker 容器内 inStock 是否已同步更新。
- **核验与释疑结果**：
  1. **Git 视图原因**：主仓库 [.gitignore](file:///d:/MacTools/WorkFile/WorkSpace/InStock/.gitignore) 第 8 行明确配置了 `/instock/core/strategy/`（因该目录在服务器上属于独立挂载目录 `/mnt/4TB/dockerf/stock/instrategy`）。因此本地磁盘上的 [`instock/core/strategy/keep_increasing.py`](file:///d:/MacTools/WorkFile/WorkSpace/InStock/instock/core/strategy/keep_increasing.py) 虽然已被实质修改并保存，但被 Git 规则忽略，不会出现在 VS Code 的 Git 更改列表中。
  2. **双端哈希校验（100% 精确一致）**：
     - `instock/job/strategy_enter-edit.py`：本地与 Docker 容器 SHA256 均为 `8ec22fcd250304df43ffb5f7015f15002dc4886cc5fdbbe18b02837ab93b2cce`。
     - `instock/core/strategy/keep_increasing.py`：本地、宿主机挂载目录（`/mnt/4TB/dockerf/stock/instrategy/`）及 Docker 容器（`/data/InStock/instock/core/strategy/`）SHA256 均为 `ec0c3c98aa1c04e1e71daba6b0d1873dcfd97652a0f00c0283f523c6b431cf47`。
  3. **运行状态**：Docker 容器内已完成 pyc 字节码刷新，且已对此前崩溃的 10 只样本股票完成只读运行验证（10/10 成功，零异常），随时可供 Web 页面正常刷新。

## [2026-10-08 14:52] 策略源代码全量同步至本地版本库与 .gitignore 规则彻底纠偏

- **任务背景**：用户明确指示：本地版本库必须永远保持最新完整版本，不能忽略任何 InStock 源代码文件；Docker 容器在开发期通过挂载宿主机独立目录保持持久化与热迭代，待全部问题解决后再行重构最新底座镜像。
- **问题根因**：此前 `.gitignore` 将挂载目录 `/instock/core/strategy/` 一刀切屏蔽，导致策略源代码被误作为持久化数据忽略，不仅本地缺少线上挂载目录中的其他 26 个最新策略代码，且 VS Code 无法对策略代码进行版本跟踪。
- **完成动作**：
  1. **规则修正**：更新本地与容器内的 [`.gitignore`](file:///d:/MacTools/WorkFile/WorkSpace/InStock/.gitignore)，彻底移除 `/instock/core/strategy/` 忽略项，确保纯 Python 策略源代码 100% 纳入 Git 版本控制。
  2. **代码拉取**：从权威宿主机挂载目录（`/mnt/4TB/dockerf/stock/instrategy`）全量拉取全部 27 个最新策略源码文件（含 `__init__.py`、`enter.py`、`breakthrough_platform.py`、`keep_increasing.py` 等及相关规则代码）至本地 [`instock/core/strategy/`](file:///d:/MacTools/WorkFile/WorkSpace/InStock/instock/core/strategy/)。
  4. **版本库状态**：VS Code“源代码管理”已能完整识别并追踪所有策略文件的改动与新增，实现“本地版本库永远是最新的单一真理源（Single Source of Truth）”。

## [2026-10-08 15:45] 盘中回测任务日期Bug修复与定时执行时间无冲突优化

- **任务背景**：用户反馈 14:58 定时任务中出现 `Sina行情覆盖不足: 0/3900` 与 `singleton.stock_hist_data没有2026-09-30的股票行情列表` 报错；并要求将执行很慢的 14:58 回测任务调整至 14:30，同时重排其他冲突任务以避免锁冲突。
- **排查根因**：
  1. 报错定位在 [`instock/job/backtest_data_daily_job_edit.py`](file:///d:/MacTools/WorkFile/WorkSpace/InStock/instock/job/backtest_data_daily_job_edit.py)，其在盘中（14:58）错误取了 `trd.get_trade_date_last()` 第 0 项历史收盘日（2026-09-30），导致向新浪比对今日行情时覆盖不足 0/3900。
  2. 若单纯将回测任务移至 14:30，将与原配置在 14:30 执行的 `strategy_enter-edit.py` 发生硬冲突，争抢 `strategy_enter.lock` 导致退出码 75。
- **完成成果**：
  1. **代码修复**：
     - 在 [`instock/job/backtest_data_daily_job_edit.py`](file:///d:/MacTools/WorkFile/WorkSpace/InStock/instock/job/backtest_data_daily_job_edit.py) 的 `prepareRealTime()` 中增加盘中交易日自动切换，盘中（`trd.is_open(now) and not trd.is_close(now)`）取 `run_date_nph`，彻底根除 0/3900 覆盖不足报错。
     - 为 `stocks_data_to_realtime` 中的 `ROC` 价格变化率补充 `fillna(0.0)` 保护。
     - 持共享锁完成原子备份（留存至容器内 `/tmp/inStock-git-sync-20261008-v4/backup/backtest_data_daily_job_edit.py.bak`），容器内重新编译 pyc，双端 SHA256 精确一致为 `66029597b1269b44e51a3f06d53d26618f3045ea439c6565be72a1154946aa68`。
  2. **Crontab 调度无冲突精细重排**：
     - 14:30 专属用于盘中回测任务：`30 14 * * 1-5 ... backtest_data_daily_job_edit.py`。
     - 14点策略扫描重排为：`0,20,50 14 * * 1-5 ... strategy_enter-edit.py`。
       - 14:00、14:20 扫描策略（14:20 结束正好为 14:30 回测输送最新选股标的）；
       - 14:30 回测独占启动，充裕运行，彻底消除 `flock -n -E 75` 锁争抢；
       - 14:50 补跑尾盘策略扫描，捕捉收盘前异动。
     - 新配置保存于本地版本库 [`instock/config/crontab.root`](file:///d:/MacTools/WorkFile/WorkSpace/InStock/instock/config/crontab.root)，并已同步至容器生效且重载 cron 服务。
