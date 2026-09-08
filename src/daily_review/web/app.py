"""Flask 应用工厂：create_app()。

- app.json.ensure_ascii=False（JSON 中文原样输出）
- 每个 app 实例挂独立的 JobManager / DashboardCache（app.extensions["jobs"] / ["dashboard_cache"]，测试隔离）
- 注册 pages/api 两个 blueprint；模板目录 = web/templates/（零 CDN，深色主题）
- v0.36.2 安全加固：SECRET_KEY + Host 头校验（防 DNS rebinding 打到本地端口）
"""

from __future__ import annotations

import os
import secrets

from flask import Flask, jsonify, request

from daily_review.web.jobs import JobManager
from daily_review.web.routes import DashboardCache

# 允许的 Host 头（v0.36.2）：本机工具只服务 127.0.0.1 / localhost。
# 恶意网页可用 DNS rebinding 把攻击域名解析到 127.0.0.1 再访问本端口，
# 校验 Host 可拦下这类跨源请求（Werkzeug 默认不校验 Host 头）。
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}


def create_app() -> Flask:
    app = Flask(__name__)
    # 未显式配置 FLASK_SECRET_KEY 时进程内随机（不落盘）。当前未用 session，
    # 配置它保证未来加会话/CSRF 时不裸奔；每次重启随机 → 旧 cookie 自动失效。
    app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(16)
    app.json.ensure_ascii = False
    app.extensions["jobs"] = JobManager()
    app.extensions["dashboard_cache"] = DashboardCache()

    # v0.35：审计日志（data/audit.db），惰性初始化
    from daily_review.web.audit import AuditDB

    app.extensions["audit_db"] = AuditDB()

    @app.before_request
    def _guard_host() -> None:
        """Host 白名单：仅允许本机回环地址，其余返回 400（防 DNS rebinding）。"""
        raw = (request.host or "").strip().lower()
        if raw.startswith("["):  # IPv6：[::1]:5000
            host = raw[1:].split("]", 1)[0]
        else:
            host = raw.split(":", 1)[0]
        if host not in _ALLOWED_HOSTS:
            return jsonify({"error": "非法 Host 头"}), 400

    from daily_review.web.routes import api_bp, pages_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp)
    return app
