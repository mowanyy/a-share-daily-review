"""本地向量嵌入提供者（可选增强，懒加载）。

- 向量路径依赖 `sentence-transformers` + bge-small-zh-v1.5（ModelScope 下载，国内可直连）。
- 未安装依赖 / 模型目录缺失时 `encode()` 返回 None，混合检索自动降级为纯关键词，功能不中断。
- `install_embedding()` 供 `qa --setup`：pip 装 sentence-transformers/modelscope → 从 ModelScope 拉模型。
"""

from __future__ import annotations

import logging
from pathlib import Path
import subprocess
import sys

import numpy as np

from daily_review.config import PROJECT_ROOT

_log = logging.getLogger("daily_review.kb")

MODEL_DIR_DEFAULT = PROJECT_ROOT / "models" / "bge-small-zh-v1.5"
MODELSCOPE_MODEL_ID = "AI-ModelScope/bge-small-zh-v1.5"
# BGE 检索惯例：查询句加指令前缀，文档句不加（区分度更好）
QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："

_provider = None
_tried = False


def model_ready(model_dir: Path) -> bool:
    """模型目录是否完整（sentence-transformers 可加载的配置 + 权重）。"""
    if not (model_dir / "config.json").exists():
        return False
    return any(
        (model_dir / name).exists()
        for name in ("pytorch_model.bin", "model.safetensors", "model.onnx", "model.gguf")
    )


def get_provider(model_dir: Path | None = None) -> object | None:
    """懒加载 SentenceTransformer；失败打一次 warning 并返回 None。"""
    global _provider, _tried
    if _tried:
        return _provider
    _tried = True
    model_dir = model_dir or MODEL_DIR_DEFAULT
    try:
        from sentence_transformers import SentenceTransformer  # 可选依赖
    except ImportError:
        _log.warning(
            "向量检索不可用：未安装 sentence-transformers，已降级为纯关键词检索。"
            "（运行 qa --setup 安装向量路径）"
        )
        return None
    if not model_ready(model_dir):
        _log.warning(
            f"向量检索不可用：模型目录不完整 {model_dir}，已降级为纯关键词检索。"
            "（运行 qa --setup 下载 bge-small-zh-v1.5）"
        )
        return None
    try:
        _provider = SentenceTransformer(str(model_dir), device="cpu")
        _log.info("向量检索就绪（bge-small-zh-v1.5）")
    except Exception as exc:  # noqa: BLE001 — 可选增强，任何失败都降级
        _log.warning(f"向量模型加载失败，已降级为纯关键词检索：{exc}")
        _provider = None
    return _provider


def encode(texts: list[str], *, model_dir: Path | None = None) -> np.ndarray | None:
    """批量编码；向量路径不可用时返回 None。返回 (n, dim) float32。"""
    provider = get_provider(model_dir)
    if provider is None:
        return None
    if not texts:
        return np.zeros((0, 0), dtype="float32")
    try:
        return provider.encode(texts, normalize_embeddings=True).astype("float32")
    except Exception as exc:  # noqa: BLE001
        _log.warning(f"向量编码失败，已降级为纯关键词检索：{exc}")
        return None


def install_embedding(model_dir: Path | None = None) -> bool:
    """qa --setup：装依赖 + 从 ModelScope 下载模型。返回是否成功。"""
    model_dir = (model_dir or MODEL_DIR_DEFAULT).resolve()
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        print("[setup] 安装 sentence-transformers / modelscope ...")
        code = subprocess.call(
            [sys.executable, "-m", "pip", "install", "sentence-transformers", "modelscope"]
        )
        if code != 0:
            print("[setup] pip 安装失败，请手动执行后重试")
            return False

    print(f"[setup] 从 ModelScope 下载 {MODELSCOPE_MODEL_ID} → {model_dir} ...")
    try:
        from modelscope import snapshot_download

        snapshot_download(MODELSCOPE_MODEL_ID, local_dir=str(model_dir))
    except Exception as exc:  # noqa: BLE001
        print(f"[setup] 下载失败：{exc}")
        print("可手动下载模型文件放入 models/bge-small-zh-v1.5/（需 config.json + 权重）")
        return False

    if not model_ready(model_dir):
        print(f"[setup] 模型目录不完整：{model_dir}")
        return False
    print("[setup] 完成。下次 qa 启动将启用向量检索（可 --rebuild 重建向量索引）")
    return True
