# Minimal Mihomo Controller Beta：隔离开发、故障降级与安全回退任务书

## 给新对话的执行指令

请完整阅读本文件后再执行任何命令。目标是在不影响当前稳定版 Controller 的前提下，开发并验证一个 Beta 版本；如果必须进行真实 TUN/DNS 接管测试，必须先建立并演练一个不依赖互联网、可自动恢复当前稳定版的本地回退机制。

不要直接在 `main` 上开发，不要覆盖当前安装目录，不要让两个 TUN 实例同时运行，不要把任何订阅 URL、密码、密钥、完整运行配置或带凭据的 YAML 提交到 Git。

## 1. 当前稳定基线

- 仓库：`/home/njust/Mihomo Controller`
- 稳定分支：`main`
- 编写本任务书时的稳定提交：`b64ed41`
- 远端：`origin/main`
- 稳定服务：`minimal-mihomo.service`
- 稳定 CLI/GUI：`mmctl`、`mmgui`
- 稳定运行数据：`/home/njust/.local/share/minimal-mihomo`
- 稳定用户配置：`/home/njust/.config/minimal-mihomo`
- 稳定 Python 环境：`/home/njust/.local/lib/minimal-mihomo/venv`
- 当前稳定配置名称：`GCP_TUN`
- 当前预期模式：`rule`、TUN 开启
- 当前代理组预期：`PROXY=GCP_TUN`、`GLOBAL=GCP_TUN`
- 当前 GCP 节点使用固定 IP，因此节点自身不依赖域名解析。

仓库中已有用户自己的未提交内容，至少包括：

- `.gitignore` 的用户修改
- `pelican-bike.html`

这些内容不得修改、删除、覆盖、暂存或提交。开始工作前重新运行 `git status --short`，若状态发生变化，以实际状态为准并继续保护所有无关改动。

## 2. 已确认的故障事实

### 2.1 NJUST-LAB 与 GCP

- NJUST-LAB 的无线关联、DHCP 和物理接口在故障期间保持正常。
- GCP 起初可用，之后到唯一代理入口的 TCP 连接出现持续 `i/o timeout`。
- 相同 GCP 入口切到 `203-1` 后可立即连接。
- 这说明 TUN/Rule 配置并非只适用于某个 Wi-Fi，但客户端不能保证上游网络始终允许访问唯一 GCP 入口。
- 不得声称仅靠客户端代码可以保证 NJUST-LAB 永远连通该 GCP IP。

### 2.2 当前 watchdog 的问题

- 当前 GUI 每 10 秒启动健康检查。
- 连续两轮、每轮两个 HTTPS 目标全部失败后，`monitor_or_stop()` 会停止整个 Mihomo 服务。
- 真实日志中，自动停机发生在 `23:03:13` 和 `23:45:23`。
- 停止 TUN 后 Chrome 可能继续缓存 Mihomo fake-IP（`198.18.x.x`），导致 Firefox恢复而 Chrome仍无法访问。
- 新 Beta 不应以“停止核心”作为普通代理故障的默认降级方式。

### 2.3 DNS 问题

- 当前构建器把普通 `nameserver` 设置为 `1.1.1.1/8.8.8.8 #GLOBAL`。
- 在 NJUST-LAB 上这些 DoH 连接大量超时。
- `direct-nameserver` 并不自动等价于“所有中国域名始终使用国内 DNS”。
- Nano 在 Controller 中的失败日志首先是 `dns resolve failed: couldn't find ip`，没有证据表明其所有代理节点都被 NJUST-LAB 阻断。
- Clash Verge 在同一设备和 NJUST-LAB 上可以使用 Nano，因此 Beta 必须避免破坏订阅原有的可用 DNS/代理组语义。

## 3. 用户确认的产品行为

### 3.1 GCP 故障策略

- GCP 掉线时只降级到 `DIRECT`。
- 绝不自动切换到 Nano 或其他订阅代理。
- 保留 Mihomo、TUN、本地 mixed 端口和 fake-IP 处理，避免 Chrome因核心消失而失联。
- 国内网络应继续可用。
- 境外网络在 GCP 不可达期间允许不可用，不能悄悄改变出口 IP。
- UI 必须明显显示“GCP 不可达，当前为 DIRECT 降级”，避免用户误以为仍使用私人 GCP IP。

### 3.2 手动切换优先级

- 用户手动 Apply Nano 或其他配置时，人工操作具有最高优先级。
- 必须立即取消正在运行的代理健康检查、DNS探测及等待中的 subprocess。
- 手动切换不得等待最长 30 秒的旧健康检查结束。
- 旧配置的迟到回调不得停止、切换或覆盖新配置。
- 切到 Nano 后，GCP 的后台恢复检测必须停止；不得自动把用户从 Nano 切回 GCP。
- 单击配置条目仍只负责选中，明确点击 Apply / reconnect 才激活配置，防止误触重启。

### 3.3 DNS 主备行为

- 主 DNS 可用时优先使用主 DNS。
- 主 DoH 连续超时后启用备用 DNS，维持国内 DIRECT、Controller和配置切换的稳定性。
- 后台继续低频探测主 DNS。
- 主 DNS 连续恢复后自动回到主 DNS。
- 必须有滞回：例如连续 2 次失败才降级、连续 3 次成功才恢复，防止频繁抖动。
- DNS降级不得触发自动切换 Nano。
- 备用 DNS 能维持解析不代表在 GCP 已断时境外网站一定可以直连；UI和日志必须正确表达这一点。

## 4. 隔离开发架构

### 4.1 Git 隔离

- 从 `b64ed41` 或开始工作时确认过的最新 `origin/main` 创建分支：`codex/resilient-failover-beta`。
- 优先使用独立 Git worktree，不在当前稳定 checkout 中开发。
- 建议 worktree：`/home/njust/Mihomo Controller Beta`；创建前确认路径不存在且不会覆盖用户文件。
- 不得在 Beta 验证完成前合并到 `main`。

### 4.2 运行环境隔离

Beta 必须使用独立资源，建议值如下；如端口已占用，先只读检查再选择其他端口：

- 数据目录：`/home/njust/.local/share/minimal-mihomo-beta`
- 配置目录：`/home/njust/.config/minimal-mihomo-beta`
- Python 环境：`/home/njust/.local/lib/minimal-mihomo-beta/venv`
- CLI：`mmctl-beta`
- GUI：`mmgui-beta`
- systemd 服务：`minimal-mihomo-beta.service`
- mixed 端口：`17890`
- API 端口：`19098`
- DNS 端口：`11053`
- TUN 设备名：`MihomoBeta`

Beta 服务初始必须满足：

- 不启用开机自启；
- 不修改稳定版服务文件；
- 不读取或写入稳定版 transaction/health/lock/state；
- 不接管系统 DNS；
- 不启用 TUN；
- 不自动停止稳定版；
- 只允许通过显式 `curl --proxy http://127.0.0.1:17890` 等方式测试。

配置和订阅凭据只能安全地引用或复制到权限为 `0600` 的 Beta 私有目录，不能进入 Git。源 YAML 继续保持只读语义。

## 5. 实现任务清单

### A. 可取消、分代的后台检测

- [ ] 将健康检查与普通用户操作分离，避免后台检测长期占用全局操作锁。
- [ ] 为每次激活配置生成不可复用的 generation/token。
- [ ] 健康检查结果提交前同时核对：profile ID、generation、核心 PID和当前服务状态。
- [ ] 用户 Apply 时先使旧 generation 失效，再取消探测进程，然后执行切换。
- [ ] 使用可终止的 `Popen` 或等效机制，不再使用无法即时取消的长时间阻塞调用。
- [ ] 取消动作应在 1 秒内完成；需要对子进程组做安全回收，不能残留 curl。
- [ ] 旧检测回调必须成为 no-op，不能修改新配置。
- [ ] 为取消、超时、迟到回调和并发 Apply 编写确定性测试。

### B. GCP → DIRECT 降级状态机

建议状态：

```text
STARTING
  -> PROXY_ACTIVE
  -> DEGRADED_DIRECT
  -> PROXY_ACTIVE

任何状态 --用户 Apply 新配置--> generation 失效并进入新配置 STARTING
```

- [ ] `PROXY_ACTIVE` 下使用两个独立 HTTPS 目标检测实际代理链路。
- [ ] 连续两轮完整失败后，不停止服务；把当前配置的出口选择器降级为 `DIRECT`。
- [ ] 对 GCP 配置同步设置 `PROXY=DIRECT` 和 `GLOBAL=DIRECT`，但只在对应选项存在时操作。
- [ ] 记录降级前的具体节点 `GCP_TUN`，不要从缓存猜测恢复目标。
- [ ] 保持 TUN、mixed 端口、API和 Mihomo DNS listener 存活。
- [ ] 降级后使用针对具体节点的 API latency 或等效探测测试 `GCP_TUN`，不能通过已切到 DIRECT 的组做“假成功”测试。
- [ ] 至少连续 3 次成功后才恢复 `PROXY/GLOBAL=GCP_TUN`。
- [ ] 恢复只允许发生在同一 GCP profile 和同一 generation 中。
- [ ] 若用户切到 Nano，立即停止 GCP 恢复探测。
- [ ] UI显示当前状态、失败次数、最近一次成功、当前真实出口选择。
- [ ] 不在日志中输出节点密码、订阅 URL、API secret或完整配置。

### C. DNS 分流与主备

先依据 Mihomo 官方 DNS 文档验证当前内核版本的真实行为，不要只凭假设实现：

- `nameserver-policy` 优先于 `nameserver/fallback`；
- `fallback` 默认可能并发查询；
- `fallback-lazy-query` 的超时和恢复行为需要用本地可控 DNS stub 实测；
- `proxy-server-nameserver` 只用于代理节点域名；
- `direct-nameserver-follow-policy` 是否适合当前 fake-IP/TUN 组合需要测试。

目标配置原则：

- [ ] `geosite:cn,private` 使用至少两个国内加密 DNS，并明确走 `DIRECT`。
- [ ] 代理节点域名使用独立、无需代理即可访问的 bootstrap/`proxy-server-nameserver`。
- [ ] GCP 固定 IP节点不得形成 DNS bootstrap 循环。
- [ ] 境外默认查询优先走主 DoH；备用 DNS在主 DoH 超时时可接管。
- [ ] Nano 等订阅配置应尽量保留其已在 Clash Verge 验证可用的 DNS语义；不能假设所有配置都有名为 `PROXY` 的组。
- [ ] 若 Mihomo 原生 `fallback-lazy-query` 足以满足“主优先、失败备用、恢复自动回主”，优先使用原生机制，减少热重载。
- [ ] 若必须由 Controller 实现 DNS circuit breaker，状态切换必须通过 Mihomo API安全重载配置并调用 `/cache/dns/flush`；不得重启 systemd 服务。
- [ ] DNS主备状态同样绑定 profile generation；切换 Nano 后旧 GCP DNS监控立即失效。
- [ ] DNS降级/恢复需要滞回、最短驻留时间和指数退避，避免在弱网中频繁重载。

### D. 订阅配置兼容性

- [ ] 使用脱敏 fixture 模拟 Nano 的非标准代理组名称，例如 `🚀 节点选择`。
- [ ] 不硬编码只有 `PROXY` 才是主选择器。
- [ ] 明确稳定版与 Beta 对源配置的覆盖字段，并记录在文档中。
- [ ] 验证 Nano 的节点域名能在 Beta 中解析。
- [ ] 验证手动 Nano Apply 不会被 GCP watchdog 阻塞或回滚。
- [ ] 不允许 Beta 自动选择 Nano；只有用户显式 Apply 才能切换。

### E. 网络切换处理

- [ ] 保留代理服务器 IP 的 `route-exclude-address`，确保节点连接不进入自身 TUN。
- [ ] 验证 `auto-detect-interface` 在 NJUST-LAB、`203-1`、热点之间切换时能更新默认接口。
- [ ] 网络接口变化后清零旧网络的健康失败计数，进入短暂 grace period，再开始检测。
- [ ] 切换网络时不能把上一个网络的失败回调应用到新网络。
- [ ] GCP 在新网络恢复后，只有当前配置仍为 GCP 时才能自动恢复 GCP选择。

## 6. 稳定版保护与自动回退

### 6.1 开发期间

- 稳定版安装目录保持原样。
- 稳定版服务继续 enabled。
- Beta 服务保持 disabled。
- 非 TUN Beta 可以与稳定版并行，但端口、锁和数据目录必须完全隔离。
- 不允许两个实例同时启用 auto-route TUN。

### 6.2 第一次真实 TUN Canary 前

必须先完成一次“故意失败”的回退演练。建立一个 root/systemd 管理的本地 dead-man timer，而不是依赖当前终端、互联网或对话连接。

Canary 切换顺序：

1. 记录稳定版服务状态、PID、运行配置哈希和当前物理 DNS。
2. 启动 60–90 秒的自动回退 timer。
3. 停止稳定版服务。
4. 确认稳定版 TUN和本地端口已释放。
5. 启动 Beta TUN服务。
6. 本地验证 API、TUN、DNS和回退 timer仍在运行。
7. 验证国内 DIRECT、GCP代理和 Chrome访问。
8. 只有全部验证通过后才显式解除回退 timer。

回退动作必须可在没有网络时完成：

1. 停止 Beta 服务；
2. 删除/恢复 Beta 对 systemd-resolved 的接管；
3. 确认 `MihomoBeta` 已消失；
4. 启动原 `minimal-mihomo.service`；
5. 验证原 API、端口、TUN和物理/Controller DNS状态；
6. 不修改稳定版 profile registry、runtime 或 last-good。

如果 Beta 启动失败、健康验证卡死、GUI崩溃、进程被 kill或会话断开，timer都必须自动执行上述回退。

## 7. 测试矩阵

### 7.1 单元/集成测试（不动真实网络）

- [ ] 主 DoH正常，备用 DNS不被提升为活动状态。
- [ ] 主 DoH连续超时，备用 DNS接管。
- [ ] 主 DoH间歇成功不会造成抖动恢复。
- [ ] 主 DoH连续恢复后切回主 DNS。
- [ ] GCP两轮失败后只切 DIRECT，不停止服务。
- [ ] GCP恢复不足 3 次不切回。
- [ ] 用户在 30 秒检查中 Apply Nano，检测在 1 秒内取消。
- [ ] 旧 GCP结果在 Nano激活后返回，不产生任何状态修改。
- [ ] 健康检测线程崩溃不影响核心和用户操作。
- [ ] Beta 重启后能恢复明确状态，不根据过期健康文件误切换。
- [ ] 所有异常信息经过凭据脱敏。

### 7.2 并行非 TUN Beta 测试

- [ ] 稳定服务 PID在整个测试期间不变。
- [ ] `curl --proxy 127.0.0.1:17890` 可验证 Beta GCP。
- [ ] 强制主 DNS超时后，Beta备用 DNS仍能解析国内域名。
- [ ] Beta停止后稳定版端口、DNS和TUN完全不变。
- [ ] Nano 能解析节点并完成延迟/HTTPS测试。

### 7.3 真实 Canary 测试

- [ ] 先在 `203-1` 上执行，不在首次测试时使用 NJUST-LAB。
- [ ] 先验证 dead-man timer确实能从故意失败的 Beta自动恢复稳定版。
- [ ] 验证 Chrome在 GCP → DIRECT 降级后仍能打开国内网页。
- [ ] 验证降级期间 mixed 端口和 API仍存在。
- [ ] 验证手动切 Nano立即生效且旧 GCP检测不会改回。
- [ ] 验证热点/`203-1` 切换后不会继承旧网络失败计数。
- [ ] 最后才在 NJUST-LAB 上验证 GCP失败行为和 Nano手动切换。

## 8. 验收标准

满足以下全部条件前，不得合并到 `main`：

- 全部现有测试通过，并增加上述故障场景测试。
- 稳定版和 Beta 可完全独立安装、运行和卸载。
- 非 TUN并行测试不会改变稳定版 PID、DNS、路由或配置。
- 真实 Canary失败可在预定时间内自动恢复稳定版，无需互联网和人工输入。
- GCP失败只进入 DIRECT 降级，不停止核心，不自动切 Nano。
- 用户 Apply Nano 时后台检测立即取消，切换不被锁阻塞。
- 国内 DNS不依赖 GCP或国外 DoH。
- 主 DNS故障时备用 DNS可工作，恢复后能稳定回主且没有频繁抖动。
- Chrome在降级前后不会因 fake-IP/TUN突然消失而长期断网。
- 日志、Git历史、测试输出和诊断包不含秘密。
- 在 `203-1`、热点和 NJUST-LAB 上的结果分别记录，不能把一个网络的成功泛化成所有网络的保证。

## 9. 合并与发布步骤

1. 在 Beta 分支完成代码审查、测试报告和回退演练记录。
2. 展示相对 `main` 的完整 diff，确认没有用户文件或凭据。
3. 将 Beta 分支推送，但不直接覆盖 `main`。
4. 经用户确认后合并到 `main`。
5. 合并后仍先保留原稳定安装，使用 Canary方式安装新稳定版。
6. 至少完成一次重启、一次 GCP故障降级、一次手动 Nano切换和一次自动回退验证。
7. 确认新版本稳定后，才清理 Beta服务；稳定快照继续保留一段时间。

## 10. 明确不做的事情

- 不承诺客户端能绕过 NJUST-LAB 对唯一 GCP入口的上游丢包。
- 不在用户不知情时用 Nano替代私人 GCP出口。
- 不通过关闭整个 Mihomo来处理普通代理故障。
- 不在后台检测仍运行时强迫用户等待配置切换。
- 不同时运行两个接管默认路由的 TUN核心。
- 不使用 `git reset --hard`、`git checkout --` 或删除用户未提交文件。
- 不把私有 YAML、订阅快照、runtime、API secret或代理凭据加入 Git。

## 11. 新对话开始时的首轮检查

新对话应先完成并汇报以下只读检查，再创建分支或安装 Beta：

- `git status --short`
- `git rev-parse --short HEAD`
- `git log -3 --oneline --decorate`
- 稳定服务的 `ActiveState`、`MainPID`、`NRestarts`
- 当前监听端口和 TUN接口
- 当前 `PROXY/GLOBAL` 选择
- 当前物理网络名称和默认路由
- Beta建议端口是否空闲
- 稳定版数据/配置/安装路径的权限和边界

任何检查结果与本文件不一致时，先以只读方式查明原因；不得为了“对齐任务书”而修改稳定环境。
