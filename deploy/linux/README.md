# 常规服务器部署

前端使用 `DEPLOY_TARGET=node pnpm build` 与 `vinext start`；Nginx 同域转发 `/api/` 到 Gunicorn。公开页面从 `/api` 读取数据，不将后端回环地址写入前端构建。保留默认构建方式用于既有 Cloudflare 部署。

## 目录与服务

- `/opt/uestc-ai/releases/<version>`：应用版本，构建前安装 Node 22.13+、pnpm 10.18.2 与 Python 3.11+。
- `/opt/uestc-ai/current`：当前版本链接。
- `/opt/uestc-ai/shared/venv`：Python 依赖。
- `/opt/uestc-ai/shared/uploads`、`data`：持久化运行文件。
- `/etc/uestc-ai/backend.env`、`web.env`：生产配置，root 管理，应用组只读；不得提交到仓库。
- `uestc-ai-api`、`uestc-ai-web`：以 `uestc-ai` 服务账户运行，由 systemd 自动重启、开机自启。

数据库为 PostgreSQL，连接形如 `postgresql+psycopg://user:password@127.0.0.1/database`。关闭 `AUTO_CREATE_SCHEMA`、`SEED_DATABASE`、`EXPOSE_VERIFICATION_CODE`，加载生产配置后执行 `python -m flask --app backend/app.py db upgrade`。启用 HTTPS 后设置 `SESSION_COOKIE_SECURE=1`。只有 Nginx 可以访问回环 API 时才设置 `TRUST_PROXY=1`。

## 缓存与连接

设置 `REDIS_URL`、`REDIS_CACHE_TTL=30` 和独立 `REDIS_CACHE_PREFIX`。Redis 只缓存白名单中的匿名公开 JSON；会话、Authorization、管理接口和私人记录均绕过缓存。写入公开数据并成功提交数据库事务后改变缓存版本；回滚不失效。Redis 故障时回退数据库，最多等待两个 250ms 连接/读超时；不把 Redis 当作业务数据存储。结果不缓存 Cookie、CORS 或认证响应头。

PostgreSQL 每个 API 进程最多 4 个常驻连接、4 个临时连接。两进程、每进程四线程是小型站点的起点，应按真实请求与数据库延迟调整。2 核 / 约 1.6GB 服务器使用 PostgreSQL 128MB shared_buffers、Redis 96MB maxmemory 和 allkeys-lru、2GB swap；Docker 仿真在其他节点运行。

## Nginx 与 HTTPS

示例域名为 `uestcai.top`、`www.uestcai.top`。先将两域名的 A 记录指向服务器，并确保 80/443 可达，然后申请证书。静态资源一年 immutable 缓存、JSON gzip；登录接口和 API 分别限速，超过返回 429。密码、Cookie 与请求体不进入访问日志。

域名开启 Cloudflare 代理时，以 root 执行 `python3 deploy/linux/update_cloudflare_ips.py`，从官方地址校验并加载受信任代理网段，再读取 `CF-Connecting-IP`。直连源站的任意客户端不能靠伪造该请求头改变 IP。这样登录限流按访客计算，避免同一代理出口上的用户共用限额。Cloudflare 网段变化后重新执行更新；配置检查失败会保留旧文件。

API 和数据库监听回环地址；开放公网的网站端口为 80、443，保留现有 SSH 入口。初次安装示例管理员或迁移旧账号之前，应确认采用空库还是迁移。正式注册依赖邮件服务，需配置 `RESEND_API_KEY` 与已验证的 `RESEND_FROM`。

## 备份与恢复

安装备份 service/timer 后每天北京时间约 03:20 运行，保存 PostgreSQL custom-format dump、上传文件和私有配置；完整备份保留七天。备份目录仅 root 读取。用 `pg_restore --list database.dump` 检查归档，用独立数据库验证恢复；恢复到正式库前先备份现状。服务器本机备份不能替代异地备份，后续可配置对象存储。

变更版本前备份数据库；新版本完成依赖、构建与迁移后再替换 `current`，重启 API/web 并验证 `/api/health`、登录、缓存命中和页面。应用回退可切回旧版本链接，但数据库迁移需另外核对可回退性，禁止无条件 downgrade。

## 本次上线验证（2026-10-05）

`uestcai.top` 已通过 HTTPS 上线，`www.uestcai.top` 跳转到主域名；证书续期由 `certbot.timer` 执行。正式库从空库迁移到 `c4d7e2a91065`，仅新建正式管理员，没有迁移本地样例或渠道密钥。管理员初始登录信息保存于 root 只读的 `/etc/uestc-ai/bootstrap-admin.json`，不得放入仓库或公共上传目录。

在独立 PostgreSQL 数据库中，已验证建队、报名、提交、附件、评审、榜单，以及真实 Redis 的命中、事务提交失效、认证请求绕过。实际备份已恢复到另一独立数据库并核对账号、赛事数量和迁移版本。临时数据库均已清理。

经源站 Nginx / HTTPS 请求公开赛事 API，以每秒 20 次、最多 4 并发持续约 10 秒：200/200 成功且命中 Redis，P50 6.38ms、P95 7.26ms。这是空库缓存接口的短时检查，未覆盖公网延迟、持续写入、附件上传或比赛峰值。上线后约 1GB 可用内存、31GB 可用磁盘。

2026-10-06已接入Resend：`uestcai.top`域名已验证且开启发送，发件人为 `UESTC AI <noreply@uestcai.top>`。密钥仅配置在服务器 `/etc/uestc-ai/backend.env`，重启API服务生效，禁止写入源码或前端构建。调用正式配置的验证码邮件函数发送到Resend官方测试地址，API接受并回报`delivered`；这是模拟投递验证，不代表某个真实收件箱的送达率。线上验证码不在接口响应中暴露，原有注册资格与发送频率限制保留。AutoDL 和模型 API 渠道需要分别接入。网站节点不运行 Docker 仿真；独立节点的配置与验证见 [Docker 测评节点](worker.md)。

## 无界杯筹备配置（2026-10-05）

正式库现已通过管理员API创建 `wujie-cup-2026`：一场草稿赛事、三条赛道及三道草稿题；本地同步创建。完整提案和构建/导入说明位于 [赛事筹备包](../../backend/competitions/wujie-cup-2026/README.md)。日期为空，奖金总额30,000元，不预设奖项分配、队伍人数或收费资源额度。MC和机械臂保存固定镜像与私有筹备场景，匹配独立测评节点；模型渠道与额度待配。人体识别的正式数据、授权和可信分类镜像待接入。

新题使用 `review_score_mode=weighted`：服务端按80%表现＋20%报告计算第一阶段，忽略客户端自报总分；答辩作为确认的外部百分制成绩，以50%合成最终分。缺少任一阶段时不出最终成绩。原赛事默认仍按已有逐项分值累加。赛事 `leaderboard.rank_scope=track` 保证独立赛道排名；筹备期间 `visible=false`，草稿榜单不对匿名访问开放。

生产构建必须显式设置 `DEPLOY_TARGET=node NEXT_PUBLIC_API_URL=/api`，不能带入本地 `.env` 中的localhost地址。检查发布的客户端产物不含本地API地址，再替换版本并做真实浏览器检查。Nginx须允许精确的 `/.rsc` 导航端点，其他隐藏路径继续禁止；配置模板已包含该例外。

此次发布前已执行完整备份，正式版本为 `/opt/uestc-ai/releases/20261005-wujie-cup`。后端完整检查138项（137通过、1跳过）；本届四项专项检查覆盖三题导入、重复执行保留编辑、独立赛道排名及两阶段加权。构建、全仓库lint和Markdown展示检查通过；全量TypeScript检查仍有原有 `db/index.ts` 对 `cloudflare:workers`、`drizzle-orm/d1` 的两项依赖缺失，未为Node部署增加不使用的D1依赖。生产管理员页面已核对完整细则、两阶段评分及在线测评节点。赛题包仅用于组织者私有导入/导出，不公开隐藏场景，不创建参赛队或收费实例。

共享的 `api-platform/platform.css` 在根服务端布局导入。两个客户端页面分别导入同一CSS，会导致当前vinext/Vite构建的RSC依赖清单引用已被移除的CSS空脚本；环境配置页随后404。生产构建须检查 `dist/server/__vite_rsc_assets_manifest.js` 中每个客户端脚本和样式路径均实际存在，真实浏览器加载环境配置页应没有控制台错误。

## 账户修改密码（2026-10-06）

登录后点击导航栏中的账户名称，或工作台的“个人主页 · 修改密码”，进入 `/people/<用户ID>`。旧地址 `/account` 转到本人的主页。管理员与普通成员使用相同的自助流程：当前密码、新密码、确认新密码。新密码为8～128字符，不能全为空白或与旧密码相同；不能指定其他用户修改。忘记当前密码时可在登录页使用邮箱找回。

`POST /api/auth/password` 使用现有登录鉴权与Nginx认证接口限流。服务端校验当前密码后只更新当前用户的密码哈希，同时撤销其所有Cookie/Bearer登录会话、清除浏览器Cookie并记录不含密码的审计事件。登录与改密锁定同一用户行，避免并发旧密码登录在撤销会话之后创建新会话。修改成功后页面提示使用新密码重新登录；`bootstrap-admin.json`仍只记录初始凭据，不同步保存新密码。

已验证管理员和成员改密、错误密码/非法参数不改变凭据或会话、确认不一致、密码长度、其他账号会话不受影响与审计不含密码。线上PostgreSQL配合真实HTTPS浏览器验证了入口、手机无溢出、错误提示、旧Cookie/Bearer失效、旧密码被拒绝、新密码登录及再次改密。临时验证账号、会话和私有凭据均已清理。发布前完成备份，无数据库迁移；构建及受影响文件lint通过。全量TypeScript仍仅有已有的D1依赖缺失两项，与本次改动无关。

## 有界并发访问检查

从独立机器执行 `python3 deploy/load_test.py --url https://uestcai.top --output load-test.json`。只读取首页、无界杯赛事页、题库及相应公开JSON，不创建账号、写入数据、启动测评或调用收费资源。依次模拟10、50、150、300个访客，停留8～12秒后再次浏览；随后发出一批300个同时开始的首页请求。请求超时10秒，最近100次请求有20次服务端错误/连接失败，或连续三次首页健康检查失败时停止加压。

单台机器只有一个出口IP，保留现有Nginx限流，429单独记录，不能将其计作服务端崩溃，也不能据此断言300个不同IP用户均可正常使用。结果按阶段保存状态码、成功请求P50/P95/P99、吞吐、缓存命中及原始请求记录。此检查不执行浏览器JavaScript，不覆盖静态资源首次加载、登录、提交、上传和测评任务；不能替代这些业务流程的容量测试。须同时在网站服务器观察CPU、内存和服务是否重启。

2026-10-06实测：300个首页同步请求全部200，P95为3050.71ms；300人持续浏览阶段出现765次同IP限流和2次502，后者与Gunicorn按请求数轮换worker时的连接重置对应。主服务未重启。正式活动前仍需修复轮换期间的偶发错误，并评估校园共享出口的限流策略；不能仅凭首页突发通过就认定完整业务容量达标。详见[本次压力测试报告](load-test-20261006.md)。
