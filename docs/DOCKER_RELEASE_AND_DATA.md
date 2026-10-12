# InStock Docker 发布、持久化与回滚手册

适用范围：InStock Python 选股、基本面/历史特征、候选数据、Web/API 和定时任务的容器发布。本手册说明 InStock 自身流程；全系统职责与共用规则以 pyQuant3/stock_standalone 的全系统职责与 Docker 发布、持久化、回滚规范为准。本文不授权生产部署或实盘操作。

## 全系统职责

- pyQuant3/stock_standalone 是跨项目总控，负责 TK/ATS 桌面程序接口、跨项目版本协调、联调和验收；本地代码状态不代表服务器已部署。
- stock-strategy 负责 Go API、SBC/Web、题材/热度/连板共享事实及服务端行情任务。
- InStock 负责 Python 选股、基本面和历史证据、候选数据及自身 Web/API 与定时数据任务。
- TK 消费本机重点关注和实时/竞价状态；ATS 消费龙头、天梯、新股、异动等分析证据。共享证据不自动变成交易指令；本手册不改变交易准入、风控或执行。
- 联动主链为 SBC/Web → stock-strategy Go → 桌面 JSON 网关 → 本机关注管理器 → TK/ATS。InStock 提供候选与历史/基本面证据，不冒充盘中实时行情。

## 默认发布流程：现有容器内 Git 对齐

1. 先核实实际 PVE/LXC、运行中的 InStock 容器、image ID、四个既有 bind mount、当前 Git SHA、工作区差异和审核目标 SHA。Windows 本地仓库只有在运行端文件 SHA 核对后才可认定已部署。
2. 发布前将当前代码、Git 状态和文件清单备份到已核验的外置维护目录，并记录 SHA256。不能把唯一备份留在容器 /tmp 或 writable layer。
3. 使用既有 strategy_enter.lock 串行保护更新。Git 工作区干净时，才把现有工作树 fast-forward 到审核 SHA；远端不可访问时可使用校验过的 Git bundle。工作区有修改时逐项保留，或只对审核文件做原子替换；禁止 reset、clean、强制 checkout 和整仓覆盖。
4. 沿用现有代码路径：instock/core/strategy 只写入它当前映射的策略挂载路径；其他文件更新到当前运行容器中的原路径。现有挂载、容器参数、网络、端口、名称和重启策略保持不变，不增加、移动或重定向挂载。
5. 普通业务代码、策略、页面和定时脚本更新不构建镜像、不新增镜像层、不重建容器。只在目标文件需要时重启对应应用进程，不因代码更新重启数据库或无关服务。
6. 通过后记录目标 Git SHA 或精确文件清单、更新前后哈希、image ID、SizeRw 前后值、外置备份位置和只读健康/API 检查结果。代码更新不应改变 image ID、image labels 或 image size。

## 数据边界和回滚

- SQLite/WAL、行情文件、HDF5、缓存、日志、动态状态和必须跨容器生命周期保留的配置，只能写入现场手册登记且经 docker inspect 核实的既有外置存储路径。
- 现有挂载结构是运行契约，代码发布不得改 Docker 参数或挂载定义。若发现应持久化的数据落在未挂载路径，先备份并阻止容器替换；不能把它描述成已持久化，也不能擅自新增挂载。
- 代码回滚恢复到上一个已验收 SHA 或文件备份；image 回滚仅用于明确批准的运行环境变更。两者都不得恢复旧数据库/缓存/日志覆盖新数据。
- 截至 2026-10-11 的现场交接记录，仓库位于容器 /data/InStock，根目录本身不是 bind mount；cache、strategy、forwardp、log 是四个外置挂载子目录。普通代码快更可能增加容器 SizeRw，但不会增加 image size。容器重建会丢失仅存在于 writable layer 的代码/Git 修改；任何获批的重建前，必须先证明完整运行代码可从审核 Git SHA 或外置备份恢复，并在候选环境验明。
- 不使用 docker commit、docker rm -v、全局 docker system prune、docker image prune -a 或 docker volume prune 作为代码发布和回滚方式。镜像清理仅按精确清单确认运行和回滚引用后操作。

## 镜像例外和版本标识

- 只有基础运行时/依赖必须改变，或用户明确批准隔离分层开发时，才允许构建镜像。每次只建立一个唯一候选，记录理由、base digest、源码 SHA、tag、预期层数、健康检查和上一回滚点。
- image 标签与 OCI revision 描述构建时快照；容器内实际部署 Git SHA需另行记录。热更新不改变 image 标签或 digest，不得用旧标签冒充运行代码版本。
- 体积报告分列 image size、容器 SizeRw 和外置存储使用量；docker ps -s 的虚拟大小不是新 image 大小。

## 发布完成条件

- 更新范围严格等于已审核文件清单，目标 SHA/文件哈希一致，Git 脏状态有逐项说明。
- 现有 Docker 参数和四个 mount 与发布前相同；数据库、日志、缓存等没有落入 image 或容器 writable layer。
- 相关 Python 语法检查、服务健康检查和只读业务接口通过；回滚点可访问，外置数据未回退。
- 发布记录包含操作者/时间、项目/服务、目标 SHA、image ID、挂载摘要、SizeRw、备份路径、验收结果和回滚方法；不记录密钥或完整环境变量。
