"""知识库变更检测：按源文件 sha256 做增量重建决策。

每次 QA 会话启动把当前源指纹与 .kb_cache/manifest.json 比对；
options 变化（如启用/停用向量）也强制全量重建。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path

MANIFEST_FILENAME = "manifest.json"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Manifest:
    """源文件 sha256 指纹 + 构建选项 + 向量器缓存指纹。"""

    hashes: dict[str, str] = field(default_factory=dict)      # source_rel -> sha256
    options: dict[str, str] = field(default_factory=dict)     # 构建选项（选项变→强制重建）
    vectorizer_sha256: str = ""                               # tfidf.pkl 指纹（v0.36.2 防篡改）

    @staticmethod
    def load(cache_dir: Path) -> "Manifest | None":
        p = cache_dir / MANIFEST_FILENAME
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return Manifest(
                hashes=dict(data.get("hashes", {})),
                options=dict(data.get("options", {})),
                vectorizer_sha256=str(data.get("vectorizer_sha256", "")),
            )
        except (json.JSONDecodeError, OSError):
            return None

    def save(self, cache_dir: Path) -> None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        (cache_dir / MANIFEST_FILENAME).write_text(
            json.dumps(
                {
                    "hashes": self.hashes,
                    "options": self.options,
                    "vectorizer_sha256": self.vectorizer_sha256,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )

    def verify_vectorizer(self, vec_path: Path) -> bool:
        """校验向量器缓存文件指纹（v0.36.2 安全修复）。

        指纹不存在（旧版缓存）或不匹配（文件被篡改/夹带恶意 pickle）→ False，
        由调用方触发重建。pickle 反序列化会执行任意代码，加载前必须先验指纹。
        """
        if not self.vectorizer_sha256:
            return False
        try:
            return sha256_file(vec_path) == self.vectorizer_sha256
        except OSError:
            return False

    def diff(self, current: dict[str, str]) -> tuple[set[str], set[str], set[str]]:
        """(新增, 删除, 内容变更) 的 source_rel 集合。"""
        prev = set(self.hashes)
        now = set(current)
        added = now - prev
        removed = prev - now
        changed = {
            rel for rel in now & prev if self.hashes.get(rel) != current.get(rel)
        }
        return added, removed, changed

    def same_options(self, options: dict[str, str]) -> bool:
        return self.options == options
