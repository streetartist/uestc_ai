# 安全策略

## 报告漏洞

请使用 GitHub 仓库的 **Security** 页面发起私密漏洞报告，不要在公开 issue、讨论区或 Pull request 中披露尚未修复的漏洞。报告应尽量包含受影响版本、复现步骤、影响范围和可行的缓解建议。

维护者确认问题后会协调修复和披露时间。在修复发布前，请避免公开利用细节。

## 支持范围

当前项目处于早期阶段，仅维护 `main` 分支的最新版本。尚未承诺旧版本的长期安全支持。

## 部署基线

本地默认配置只用于开发和演示，不可直接用于公网部署。生产环境至少需要：

- 使用足够长的随机 `SECRET_KEY`（至少 16 个字符，不得是 `dev-change-me` 之类的占位值），并移除初始化管理员密码。当 `TESTING`、调试模式、`AUTO_CREATE_SCHEMA`、`SEED_DATABASE` 均未开启时，`SECRET_KEY` 缺失、为占位值或过短会使 API 拒绝启动。可用 `python -c "import secrets; print(secrets.token_urlsafe(48))"` 生成。
- 设置 `AUTO_CREATE_SCHEMA=0`、`SEED_DATABASE=0` 和 `EXPOSE_VERIFICATION_CODE=0`。
- 设置精确的 `CORS_ORIGINS`，启用 `SESSION_COOKIE_SECURE=1`，并通过 HTTPS 提供服务。仅依赖会话 Cookie 的写请求（POST/PUT/PATCH/DELETE）如果带有不在 `CORS_ORIGINS` 中且主机名也与当前请求不同的 `Origin`，会被拒绝（403）作为 CSRF 防护；携带 `Authorization` 或 `x-api-key` 头的请求、以及不带 `Origin` 的非浏览器客户端不受此限制。跨源分离部署必须把前端来源写入 `CORS_ORIGINS`。
- 使用 PostgreSQL 和 Alembic 迁移；不要使用仓库内的演示 SQLite 数据库。
- 配置受控的上传存储、文件扫描、备份和保留策略。
- 限制管理、评分导入与数据库凭据的权限，并定期轮换密钥。

`.env.example` 中的 `INITIAL_ADMIN_PASSWORD`、`INITIAL_REVIEWER_PASSWORD` 是公开的开发占位值。代码本身不内置默认口令：`SEED_DATABASE=1` 时必须显式提供这两个口令，否则启动失败。任何可访问的部署都必须关闭 `SEED_DATABASE`，不得沿用这些占位值，并建立独立的管理员账户。
