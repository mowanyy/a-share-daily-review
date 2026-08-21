"""Flask 应用工厂：create_app()。

- app.json.ensure_ascii=False（JSON 中文原样输出）
- 每个 app 实例挂独立的 JobManager / DashboardCache（app.extensions["jobs"] / ["dashboard_cache"]，测试隔离）
- 注册 pages/api 两个 blueprint；模板目录 = web/templates/（零 CDN，深色主题）
"""

from __future__ import annotations

from flask import Flask

from daily_review.web.jobs import JobManager
from daily_review.web.routes import DashboardCache


def create_app() -> Flask:
    app = Flask(__name__)
    app.json.ensure_ascii = False
    app.extensions["jobs"] = JobManager()
    app.extensions["dashboard_cache"] = DashboardCache()

    # v0.35：审计日志（data/audit.db），惰性初始化
    from daily_review.web.audit import AuditDB

    app.extensions["audit_db"] = AuditDB()

    from daily_review.web.routes import api_bp, pages_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(api_bp)
    return app
