# 全站 API 回归与覆盖审计

## 运行

使用独立开发副本、Python 3.11+。不要加载生产环境文件，不要将上传目录或数据库指向生产。

```bash
python -m venv .venv
# Linux/macOS
. .venv/bin/activate
# Windows PowerShell: .venv/Scripts/Activate.ps1
python -m pip install -r backend/requirements-test.txt
python tests/run_api_contract_audit.py --strict-coverage --output test-results/api-audit.json
```

普通回归也可使用 `python -m unittest discover -s tests -p "test_*.py" -v`。审计运行器额外记录路由覆盖并阻止非回环 socket 连接；邮件、模型网关、算力供应商、Docker 使用模拟或本地服务器。不会进行真实计费调用、发送邮件、启动云机器或提交比赛评测。

## 覆盖口径

按 Flask 注册的 **HTTP 方法 + 路由模板** 计数，排除自动 HEAD/OPTIONS；新接口未被通过的测试访问时，`--strict-coverage` 返回非零退出码。所有失败和异常也返回非零退出码。

报告包含：

- summary：测试数量、失败、异常、跳过、接口覆盖数量和用时。
- coverage：每个方法/路由的状态码、角色、通过的测试名及请求次数。
- events：路由模板、方法、状态码、处理用时；不保存响应内容、Cookie、Token、具体资源 ID 或邮箱。
- tests：用例结果；blocked_external_connections：被拦截的外网连接尝试。

`success_exercised` 表示通过的测试访问了 2xx 路径；3xx 跳转单独列出。`rejection_exercised` 表示有 4xx 路径。401/403/404 仅证明相应用例的鉴权、权限或不存在资源处理，不能等价于业务成功；接口覆盖不是代码分支覆盖，也不保证所有输入组合均正确。请求时间是测试客户端的处理时间，不是网页首屏或公网端到端延迟。

## 重点场景

账号注册、验证码、重置密码及会话失效；赛事、赛道、报名、组队；投稿审核及私有附件；提交、上传、版本、评审、CSV 评分与榜单；管理员授权、邀请与审计；模型渠道、额度、网关；云算力账户与调度；题目整包导入、评测租约、生命周期、运行隔离及私有场景；数据库迁移。

`test_api_surface.py` 补充原套件遗漏的管理列表、角色修改、邀请撤销、队伍加入、赛道详情更新、健康检查、附件读取与 CSV 成绩批次状态流转。每项有状态码和业务内容断言。

## 环境无关的模拟用例

模拟 Docker 的用例应固定其测试内存预算和临时 worker 锁路径，避免与测试宿主的剩余内存或 `/run` 权限耦合。专门的内存限制回归仍使用原有边界断言，不全局关闭内存保护。镜像包构建测试显式传入当前 CLI 必需的 `--project` 参数。

## 边界与后续

本套件验证应用逻辑和本地服务契约，不代替真实邮件投递、模型供应商、AutoDL、GPU/Docker、LIBERO/MineDojo 仿真镜像及生产 PostgreSQL/Redis 的端到端验收。跳过用例的原因保留在 JSON 报告。性能、页面首次点击、加载提示和浏览器水合需单独浏览器测试；公网慢请求应结合反向代理与数据库耗时定位。

本次完整结果与逐接口清单见 [api-coverage.md](api-coverage.md)。

## 浏览器验证码布局回归

`tests/captcha-layout.browser.cjs` 使用 Playwright 测量真实编译页面的几何变化。验证码及发邮件接口均拦截为本地模拟响应，覆盖注册/找回密码、1440/390 像素宽度、获取中、获取成功、刷新、失败恢复、邮件发送中和倒计时。每组 10 个状态对比初始表单、说明栏、验证码、邮件行与提交按钮的位置和尺寸；允许最大 1 CSS 像素误差。发信后还检查图片和数字保留、已消费数字禁用、邮箱验证码输入仍可用。

在已安装 Playwright 与浏览器的环境运行：

```bash
node tests/captcha-layout.browser.cjs --base-url http://127.0.0.1:3000 --output test-results/captcha-layout.json --expect-stable
```

可通过 `PLAYWRIGHT_MODULE_PATH` 指定已有 Playwright 模块、`BROWSER_EXECUTABLE` 指定浏览器可执行文件。页面需使用正式构建启动。此测试保存的是虚构邮箱与模拟验证码的截图，不需要真实账户或 Cookie。

## 本次实测结果（2026-10-14）

- 隔离回归：283 个用例，281 通过、2 跳过、0 失败、0 异常；138/138 路由覆盖，137 条 2xx、1 条预期跳转，91 条路由有拒绝路径，2197 次请求。
- 跳过原因：私有数据安装所有权测试需要 Linux root；MineDojo 私有场景文件未随仓库分发。
- 前端：82 项测试、类型检查通过；lint 0 错误、2 条已有警告。
- 布局 A/B：四组场景各 10 个状态，旧版最大变化 56.219 CSS 像素；修复版及上线复测均为 0。
- 线上只读：部署前 25/25 检查通过；部署后公开接口 14/14 通过。原 Cookie 在公网和直连后端均返回 401，11 项登录态检查未能重新验证，不能将其记为已在线通过。此次变更未重启后端服务，也未创建新登录会话。

登录态线上复测需使用新的有效会话；不应绕过身份校验。完整隔离回归已经覆盖对应接口的成功与权限路径。
