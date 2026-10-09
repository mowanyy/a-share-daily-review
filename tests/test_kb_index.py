"""知识库检索索引测试：关键词命中 / 持久化往返 / 增量更新 / RRF / 向量降级。"""

from __future__ import annotations

import numpy as np

import daily_review.kb.embedding as emb
from daily_review.kb.index import KnowledgeIndex, SearchHit, rrf


def _fake_vectors(texts, **kwargs):
    rng = np.random.default_rng(42)
    return rng.normal(size=(len(texts), 32)).astype("float32")


def test_keyword_search_hits_glossary(index):
    hits = index.search("什么是炸板率？", top_k=3)
    assert hits
    assert "炸板率" in hits[0].text
    assert hits[0].source_rel.endswith("术语表.md")
    assert hits[0].section


def test_persistence_roundtrip_fastpath(kb_root, index):
    idx2 = KnowledgeIndex(kb_root, use_embedding=False)
    idx2.ensure_ready()  # 未变 → 走快路径 load
    assert len(idx2.chunks) == len(index.chunks)
    assert [c.chunk_id for c in idx2.chunks] == [c.chunk_id for c in index.chunks]
    assert idx2.search("炸板率", top_k=3)


def test_incremental_add_file(kb_root, index):
    n0 = len(index.chunks)
    p = kb_root / "knowledge" / "新战法.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "---\nid: x\nname: 新战法\nrole: strategy\nstatus: draft\n---\n# 新战法\n\n## 龙头战法\n\n只做空间板，不碰中位板。\n",
        encoding="utf-8",
    )
    index.ensure_ready()
    assert len(index.chunks) > n0
    hits = index.search("只做空间板", top_k=3)
    assert any(h.source_rel.endswith("新战法.md") for h in hits)


def test_incremental_modify_file(kb_root, index):
    p = kb_root / "knowledge" / "战法笔记.md"
    orig = p.read_text(encoding="utf-8")
    p.write_text(orig.replace("首板放量分歧后二板缩量秒板", "一字板不接力，回避一字连板"), encoding="utf-8")
    index.ensure_ready()
    hits_new = index.search("回避一字连板", top_k=3)
    assert any("一字连板" in h.text for h in hits_new)
    hits_old = index.search("首板放量分歧", top_k=3)
    assert not any("首板放量分歧" in h.text for h in hits_old)


def test_incremental_delete_file(kb_root, index):
    n0 = len(index.chunks)
    (kb_root / "knowledge" / "战法笔记.md").unlink()
    index.ensure_ready()
    assert len(index.chunks) < n0
    assert all("战法笔记.md" not in c.source_rel for c in index.chunks)


def test_rrf_fusion_with_vectors(monkeypatch, kb_root):
    monkeypatch.setattr(emb, "encode", _fake_vectors)
    idx = KnowledgeIndex(kb_root, use_embedding=True)
    idx.ensure_ready(force=True)
    assert idx.vector_available
    hits = idx.search("炸板率", top_k=3)
    assert hits  # 两榜融合后仍能出结果


def test_embedding_unavailable_falls_back_to_keyword(monkeypatch, kb_root):
    monkeypatch.setattr(emb, "encode", lambda texts, **kwargs: None)
    idx = KnowledgeIndex(kb_root, use_embedding=True)
    idx.ensure_ready(force=True)
    assert not idx.vector_available
    hits = idx.search("炸板率", top_k=3)
    assert hits  # 向量不可用 → 纯关键词仍工作


def test_no_embedding_degrades(kb_root):
    idx = KnowledgeIndex(kb_root, use_embedding=False)
    idx.ensure_ready(force=True)
    assert not idx.vector_available
    assert idx.search("炸板率", top_k=3)


def test_rrf_scores_positive():
    def hit(cid):
        return SearchHit(chunk_id=cid, text="", source_rel="", section="", score=1.0)

    scores = rrf([[hit("a"), hit("b"), hit("c")], [hit("b"), hit("d")]], k=60)
    # b 在两榜都出现 → 融合分最高；a 仅在榜首榜 rank0 → 高于 d（仅次榜 rank0）
    assert scores["b"] > scores["a"] > scores["d"]
    assert scores["c"] < scores["d"]


# ---------------------------------------------------------------- v0.36.2 安全修复：向量器 pickle 指纹校验


def test_manifest_verify_vectorizer_detects_tamper(tmp_path):
    """tfidf.pkl 指纹：文件被篡改 → 校验失败（拒绝 pickle 加载）。"""
    from daily_review.kb.manifest import Manifest, sha256_file

    f = tmp_path / "tfidf.pkl"
    f.write_bytes(b"malicious pickle payload")
    m = Manifest(vectorizer_sha256=sha256_file(f))
    assert m.verify_vectorizer(f) is True
    f.write_bytes(b"malicious pickle payload tampered!")
    assert m.verify_vectorizer(f) is False
    # 旧版缓存无指纹 → 拒绝（强制重建一次）
    assert Manifest(vectorizer_sha256="").verify_vectorizer(f) is False


def test_load_rebuilds_on_tampered_pkl(kb_root, index):
    """篡改缓存向量器文件后，快路径 load 拒绝并触发重建（不加载可疑 pickle）。"""
    from daily_review.kb.index import KnowledgeIndex
    from daily_review.kb.manifest import Manifest

    n0 = len(index.chunks)
    pkl = kb_root / ".kb_cache" / "tfidf.pkl"
    pkl.write_bytes(pkl.read_bytes() + b"TAMPER")
    idx2 = KnowledgeIndex(kb_root, use_embedding=False)
    idx2.ensure_ready()  # 指纹不符 → 自动重建
    assert len(idx2.chunks) == n0
    assert idx2.search("炸板率", top_k=3)
    # 重建后 manifest 记录了新的向量器指纹
    m = Manifest.load(kb_root / ".kb_cache")
    assert m is not None and m.vectorizer_sha256
