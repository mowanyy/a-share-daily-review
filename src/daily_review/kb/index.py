"""知识库检索索引：关键词（TF-IDF 字符 n-gram）+ 向量（可选）+ RRF 融合 + 增量重建。

- 关键词检索零新依赖：sklearn TfidfVectorizer(analyzer="char_wb", ngram_range=(2,3)) + cosine
- 向量检索：bge-small-zh-v1.5 embedding + cosine（向量不可用时自动降级关键词）
- 混合融合：Reciprocal Rank Fusion（RRF），两榜按排名加权合并
- 持续更新：源文件 sha256 指纹（.kb_cache/manifest.json）增量重建——只重切/重编码变更文件；
  向量按 chunk_id 键存 embeddings.npz，未变 chunk 复用，避免重复跑 bge
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
import pickle

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from daily_review.config import PROJECT_ROOT
from daily_review.kb import embedding
from daily_review.kb.corpus import Chunk, chunk_file, discover_sources, file_sha256
from daily_review.kb.manifest import Manifest

_log = logging.getLogger("daily_review.kb")

RRF_K = 60
CHAR_NGRAM = (2, 3)


@dataclass
class SearchHit:
    """一次检索命中。"""

    chunk_id: str
    text: str
    source_rel: str
    section: str
    score: float
    date: str | None = None


def rrf(ranked: list[list[SearchHit]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal Rank Fusion：score[chunk_id] = Σ 1/(k + rank+1)，跨列表同 chunk 累加。"""
    scores: dict[str, float] = {}
    for hits in ranked:
        for rank, hit in enumerate(hits):
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + 1.0 / (k + rank + 1)
    return scores


class KnowledgeIndex:
    """知识库索引：构建/加载/检索。"""

    def __init__(
        self,
        root: Path | None = None,
        *,
        cache_dir: Path | None = None,
        use_embedding: bool = True,
        include_output_reports: bool = False,
    ):
        self.root = (root or PROJECT_ROOT).resolve()
        self.cache_dir = (cache_dir or self.root / ".kb_cache").resolve()
        self.use_embedding = use_embedding
        self.include_output_reports = include_output_reports

        self.chunks: list[Chunk] = []
        self._by_id: dict[str, Chunk] = {}
        self._vectorizer: TfidfVectorizer | None = None
        self._tfidf_matrix = None                    # scipy sparse (n_chunks, vocab)
        self._embeddings: np.ndarray | None = None   # (n_chunks, dim) float32

    # ---------- 构建 / 加载 ----------

    def _options(self) -> dict[str, str]:
        return {
            "use_embedding": "1" if self.use_embedding else "0",
            "include_output_reports": "1" if self.include_output_reports else "0",
            "char_ngram": f"{CHAR_NGRAM[0]}-{CHAR_NGRAM[1]}",
            "model_id": embedding.MODELSCOPE_MODEL_ID if self.use_embedding else "",
        }

    def ensure_ready(self, *, force: bool = False) -> None:
        """每次 QA 会话启动调用一次：指纹比对 → 快路径 load 或增量 rebuild。

        快路径必须同时满足：options 未变、源文件指纹未变、缓存齐全——任一不符即重建。
        """
        manifest = Manifest.load(self.cache_dir)
        if not force and manifest is not None and manifest.same_options(self._options()):
            added, removed, changed = manifest.diff(self._current_hashes())
            if not (added or removed or changed) and self.load():
                _log.info(f"知识库已加载（{len(self.chunks)} 块，快路径）")
                return
        self.rebuild(force=force)

    def load(self) -> bool:
        """从缓存加载；缺任一缓存文件返回 False（触发重建）。"""
        try:
            chunks_path = self.cache_dir / "chunks.json"
            vec_path = self.cache_dir / "tfidf.pkl"
            emb_path = self.cache_dir / "embeddings.npz"
            if not (chunks_path.exists() and vec_path.exists()):
                return False
            raw = json.loads(chunks_path.read_text(encoding="utf-8"))
            self.chunks = [
                Chunk(
                    chunk_id=c["chunk_id"], source_rel=c["source_rel"], section=c["section"],
                    idx=c["idx"], text=c["text"], date=c.get("date"),
                    tags=c.get("tags", []), source_hash=c.get("source_hash", ""),
                )
                for c in raw
            ]
            self._by_id = {c.chunk_id: c for c in self.chunks}
            with vec_path.open("rb") as f:
                self._vectorizer = pickle.load(f)
            texts = [c.text for c in self.chunks]
            self._tfidf_matrix = (
                self._vectorizer.transform(texts) if texts else None
            )
            if emb_path.exists():
                with np.load(emb_path, allow_pickle=True) as npz:
                    self._embeddings = npz["vectors"]
            else:
                self._embeddings = None
            return True
        except (OSError, KeyError, json.JSONDecodeError, pickle.UnpicklingError) as exc:
            _log.warning(f"知识库缓存加载失败，将重建：{exc}")
            return False

    def save(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "chunks.json").write_text(
            json.dumps(
                [
                    {
                        "chunk_id": c.chunk_id, "source_rel": c.source_rel,
                        "section": c.section, "idx": c.idx, "text": c.text,
                        "date": c.date, "tags": c.tags, "source_hash": c.source_hash,
                    }
                    for c in self.chunks
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with (self.cache_dir / "tfidf.pkl").open("wb") as f:
            pickle.dump(self._vectorizer, f)
        if self._embeddings is not None and len(self._embeddings):
            np.savez(self.cache_dir / "embeddings.npz", vectors=self._embeddings)
        Manifest(hashes=self._current_hashes(), options=self._options()).save(self.cache_dir)

    def rebuild(self, *, force: bool = False) -> None:
        """全量或增量重建索引。只对变更文件重切/重编码；未变 chunk 的向量复用。"""
        sources = discover_sources(self.root, include_output_reports=self.include_output_reports)
        current_hashes = {p.relative_to(self.root).as_posix(): file_sha256(p) for p in sources}
        rel_to_path = {p.relative_to(self.root).as_posix(): p for p in sources}

        manifest = Manifest.load(self.cache_dir)
        if not force and manifest is not None and manifest.same_options(self._options()):
            added, removed, changed = manifest.diff(current_hashes)
            old = self._read_chunks_json()
            kept = [c for c in old if c.source_rel not in (added | removed | changed)]
            fresh = [
                chunk
                for rel in sorted(added | changed)
                for chunk in chunk_file(rel_to_path[rel], root=self.root)
            ]
            self.chunks = kept + fresh
            _log.info(f"知识库增量重建：新增 {len(added)} 改 {len(changed)} 删 {len(removed)}，共 {len(self.chunks)} 块")
        else:
            self.chunks = [
                chunk
                for rel in sorted(rel_to_path)
                for chunk in chunk_file(rel_to_path[rel], root=self.root)
            ]
            _log.info(f"知识库全量重建：{len(self.chunks)} 块")

        self._by_id = {c.chunk_id: c for c in self.chunks}
        self._fit_keyword()
        self._fit_embeddings(force=force)
        self.save()

    def _read_chunks_json(self) -> list[Chunk]:
        p = self.cache_dir / "chunks.json"
        if not p.exists():
            return []
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            return [
                Chunk(
                    chunk_id=c["chunk_id"], source_rel=c["source_rel"], section=c["section"],
                    idx=c["idx"], text=c["text"], date=c.get("date"),
                    tags=c.get("tags", []), source_hash=c.get("source_hash", ""),
                )
                for c in raw
            ]
        except (json.JSONDecodeError, OSError):
            return []

    def _current_hashes(self) -> dict[str, str]:
        return {
            p.relative_to(self.root).as_posix(): file_sha256(p)
            for p in discover_sources(self.root, include_output_reports=self.include_output_reports)
        }

    # ---------- 索引拟合 ----------

    def _fit_keyword(self) -> None:
        self._vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=CHAR_NGRAM)
        texts = [c.text for c in self.chunks]
        self._tfidf_matrix = self._vectorizer.fit_transform(texts) if texts else None

    def _fit_embeddings(self, *, force: bool = False) -> None:
        self._embeddings = None
        if not self.use_embedding or not self.chunks:
            return
        old: dict[str, np.ndarray] = {}
        emb_path = self.cache_dir / "embeddings.npz"
        if not force and emb_path.exists():
            try:
                with np.load(emb_path, allow_pickle=True) as npz:
                    arr = npz["vectors"]
                    if arr.shape[0] == len(self.chunks):
                        old = {c.chunk_id: arr[i] for i, c in enumerate(self.chunks)}
            except (OSError, KeyError):
                old = {}
        # 只编码未持久化的 chunk（新增/变更），其余复用
        to_encode = [c for c in self.chunks if c.chunk_id not in old]
        if not to_encode:
            if old:
                self._embeddings = np.stack([old[c.chunk_id] for c in self.chunks]).astype("float32")
            return
        vectors = embedding.encode([c.text for c in to_encode])
        if vectors is None or vectors.shape[0] == 0:
            return
        for c, vec in zip(to_encode, vectors):
            old[c.chunk_id] = vec
        self._embeddings = np.stack([old[c.chunk_id] for c in self.chunks]).astype("float32")

    # ---------- 检索 ----------

    @property
    def vector_available(self) -> bool:
        return self._embeddings is not None and len(self._embeddings) > 0

    def search(self, question: str, top_k: int = 5) -> list[SearchHit]:
        """混合检索：关键词榜 + 向量榜 → RRF 融合。向量不可用时退化为关键词。"""
        if not self.chunks:
            return []
        kw = self._keyword_search(question, top_k=max(top_k * 2, 10))
        vec = self._vector_search(question, top_k=max(top_k * 2, 10)) if self.vector_available else None
        return self._fuse(kw, vec, top_k)

    def _keyword_search(self, question: str, top_k: int) -> list[SearchHit]:
        if not self._vectorizer or self._tfidf_matrix is None:
            return []
        q = self._vectorizer.transform([question])
        sim = cosine_similarity(q, self._tfidf_matrix)[0]
        order = np.argsort(-sim)[:top_k]
        return [
            self._make_hit(self.chunks[i], float(sim[i]))
            for i in order
            if float(sim[i]) > 0
        ]

    def _vector_search(self, question: str, top_k: int) -> list[SearchHit]:
        q = embedding.encode([embedding.QUERY_INSTRUCTION + question])
        if q is None or self._embeddings is None:
            return []
        sim = cosine_similarity(q, self._embeddings)[0]
        order = np.argsort(-sim)[:top_k]
        return [
            self._make_hit(self.chunks[i], float(sim[i]))
            for i in order
            if float(sim[i]) > 0
        ]

    def _fuse(self, kw: list[SearchHit], vec: list[SearchHit] | None, top_k: int) -> list[SearchHit]:
        ranked: list[list[SearchHit]] = [kw]
        if vec:
            ranked.append(vec)
        scores = rrf(ranked)
        by_id = {c.chunk_id: c for c in self.chunks}
        hits = [
            SearchHit(
                chunk_id=cid,
                text=c.text,
                source_rel=c.source_rel,
                section=c.section,
                score=sc,
                date=c.date,
            )
            for cid, sc in sorted(scores.items(), key=lambda kv: -kv[1])[:top_k]
            if (c := by_id.get(cid)) is not None
        ]
        return hits

    def _make_hit(self, chunk: Chunk, score: float) -> SearchHit:
        return SearchHit(
            chunk_id=chunk.chunk_id,
            text=chunk.text,
            source_rel=chunk.source_rel,
            section=chunk.section,
            score=score,
            date=chunk.date,
        )
