"""评估模型与容差参数（v0.37）：CheckResult / EvalReport 数据类 + 可 env 覆盖的容差常量。

独立模块存放，避免 checks/verify 与 eval.__init__ 相互导入造成循环依赖。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# 容差参数（可环境变量覆盖；详见 docs/Agent内容评估方案.md 4.3 节）
EVAL_NUM_REL_TOL = float(os.getenv("EVAL_NUM_REL_TOL", "0.05"))      # 数值相对误差容差
EVAL_EMOTION_ABS_TOL = float(os.getenv("EVAL_EMOTION_ABS_TOL", "1.0"))  # 情绪温度绝对差容差（分）
EVAL_MIN_BODY_LEN = int(os.getenv("EVAL_MIN_BODY_LEN", "500"))       # L0-006 正文长度下限（字符）

# 检查级别：pass=通过 / warn=软告警（不阻断）/ error=硬失败 / skip=无法判定
_CHECK_LEVELS = ("pass", "warn", "error", "skip")


@dataclass
class CheckResult:
    """一条评估检查结果。passed 仅当 level == "pass"。"""

    id: str            # 如 "EVAL-101"
    level: str         # pass | warn | error | skip
    passed: bool
    message: str       # 差值明细 / 原因

    def to_dict(self) -> dict:
        return {"id": self.id, "level": self.level, "passed": self.passed, "message": self.message}


@dataclass
class EvalReport:
    """一次评估（某日某类型报告）的完整结果。"""

    trade_date: str
    report_type: str          # review | plan | open
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def pass_count(self) -> int:
        return sum(1 for c in self.checks if c.level == "pass")

    @property
    def warn_count(self) -> int:
        return sum(1 for c in self.checks if c.level == "warn")

    @property
    def fail_count(self) -> int:
        return sum(1 for c in self.checks if c.level == "error")

    @property
    def skip_count(self) -> int:
        return sum(1 for c in self.checks if c.level == "skip")

    def to_dict(self) -> dict:
        return {
            "trade_date": self.trade_date,
            "report_type": self.report_type,
            "pass_count": self.pass_count,
            "warn_count": self.warn_count,
            "fail_count": self.fail_count,
            "skip_count": self.skip_count,
            "checks": [c.to_dict() for c in self.checks],
        }