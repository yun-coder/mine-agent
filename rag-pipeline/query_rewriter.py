"""查询改写: 将用户问题扩展为多个检索友好的子查询"""
import json
import httpx
import re
from loguru import logger
from config import settings


def rewrite_query(question: str, num_queries: int = 3) -> list[str]:
    """LLM 改写问题为多个子查询, 提升召回率。
    失败时降级为基于关键词的启发式扩展。
    """
    # 先尝试 LLM 改写
    sub_queries = _rewrite_with_llm(question, num_queries)
    if sub_queries and len(sub_queries) > 1:
        return sub_queries

    # 降级: 关键词拆分
    return _fallback_rewrite(question)


def _rewrite_with_llm(question: str, num_queries: int = 3) -> list[str]:
    """尝试用 LLM 改写问题。"""
    prompt = f"""将以下问题改写为{num_queries}个更利于知识库检索的查询。
要求:
1. 每个查询简洁明确, 不超过 30 字
2. 只返回 JSON 数组格式, 不要其他内容
3. 覆盖原问题的不同角度

原问题: {question}

输出格式示例: ["查询1", "查询2", "查询3"]
"""
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={
                    "model": settings.llm_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.3, "num_predict": 300},
                },
            )
            r.raise_for_status()
            response_text = r.json()["response"].strip()
    except Exception as e:
        logger.debug(f"LLM 改写失败: {e}")
        return None

    try:
        queries = json.loads(response_text)
    except json.JSONDecodeError:
        start = response_text.find("[")
        end = response_text.rfind("]") + 1
        if start >= 0 and end > start:
            try:
                queries = json.loads(response_text[start:end])
            except json.JSONDecodeError:
                return None
        else:
            return None

    if isinstance(queries, list) and all(isinstance(q, str) for q in queries):
        return queries
    return None


def _fallback_rewrite(question: str) -> list[str]:
    """降级方案: 基于中文分词关键词扩展。"""
    # 简单策略: 提取关键词 + 同义词/近义词扩展
    keywords = re.findall(r"[一-鿿]{2,6}", question)
    if not keywords:
        return [question]

    # 去掉常见停用词
    stop_words = {"什么", "怎么", "这个", "那个", "有没有", "是不是", "能否", "请问"}
    filtered = [kw for kw in keywords if kw not in stop_words]

    if not filtered:
        return [question]

    # 返回: 原始问题 + 每个关键词作为独立查询
    queries = [question] + list(dict.fromkeys(filtered))[:3]
    return queries
