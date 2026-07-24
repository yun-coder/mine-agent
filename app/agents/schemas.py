"""Agent 结构化输出 Schema —— 定义每类 Agent 产出的 Pydantic 模型。

使 Agent 间的 handoff_context 携带结构化数据，而非纯文本，
让下游 Agent 可以直接引用字段而非依赖 LLM 文本解析。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ResearchOutput(BaseModel):
    """情报侦察 Agent 的结构化输出。"""
    findings: list[str] = Field(default=[], description="研究发现列表")
    data_sources: list[str] = Field(default=[], description="信息源列表")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="整体置信度")
    open_questions: list[str] = Field(default=[], description="待解答的开放问题")
    raw_text: str = Field(default="", description="原始输出文本（供前端渲染）")


class ArchitectureOutput(BaseModel):
    """系统架构 Agent 的结构化输出。"""
    tech_stack: dict[str, str] = Field(default={}, description="技术栈及选择理由")
    components: list[dict] = Field(default=[], description="系统组件列表")
    data_flow: str = Field(default="", description="数据流描述")
    adrs: list[dict] = Field(default=[], description="架构决策记录 ADR")
    risks: list[str] = Field(default=[], description="识别到的风险")
    raw_text: str = Field(default="", description="原始输出文本（供前端渲染）")


class BackendOutput(BaseModel):
    """后端工程 Agent 的结构化输出。"""
    api_endpoints: list[dict] = Field(default=[], description="API 端点列表")
    data_models: list[dict] = Field(default=[], description="数据模型定义")
    tech_decisions: list[str] = Field(default=[], description="技术决策")
    code_files: list[str] = Field(default=[], description="生成或修改的代码文件")
    raw_text: str = Field(default="", description="原始输出文本（供前端渲染）")


class FrontendOutput(BaseModel):
    """前端工作台 Agent 的结构化输出。"""
    component_tree: list[dict] = Field(default=[], description="组件树")
    pages: list[dict] = Field(default=[], description="页面列表")
    design_notes: str = Field(default="", description="设计说明")
    accessibility_notes: str = Field(default="", description="无障碍检查记录")
    raw_text: str = Field(default="", description="原始输出文本（供前端渲染）")


class IntegrationOutput(BaseModel):
    """前后端联调 Agent 的结构化输出。"""
    contract_checks: list[dict] = Field(default=[], description="契约检查结果")
    test_results: list[str] = Field(default=[], description="测试结论列表")
    issues: list[str] = Field(default=[], description="发现的问题")
    raw_text: str = Field(default="", description="原始输出文本（供前端渲染）")


class TestOutput(BaseModel):
    """回归测试 Agent 的结构化输出。"""
    coverage: dict[str, float] = Field(default={}, description="各维度覆盖率")
    test_results: list[dict] = Field(default=[], description="详细测试结果")
    regression_findings: list[str] = Field(default=[], description="回归发现问题")
    failure_patterns: list[dict] = Field(default=[], description="失败模式")
    retrospective: str = Field(default="", description="复盘总结")
    raw_text: str = Field(default="", description="原始输出文本（供前端渲染）")


# 任务类型到 Schema 的映射
SCHEMA_MAP: dict[str, type[BaseModel]] = {
    "research": ResearchOutput,
    "design": ArchitectureOutput,
    "implement": BackendOutput,
    "frontend": FrontendOutput,
    "integrate": IntegrationOutput,
    "test": TestOutput,
}

# Union 类型用于模型验证
AgentOutput = (
    ResearchOutput
    | ArchitectureOutput
    | BackendOutput
    | FrontendOutput
    | IntegrationOutput
    | TestOutput
)


def get_schema(task_type: str) -> type[BaseModel] | None:
    """根据任务类型获取对应的输出 Schema。"""
    return SCHEMA_MAP.get(task_type)


def structured_handoff_context(task_type: str, raw_text: str) -> list[dict]:
    """构建包含结构化数据的 handoff context。

    返回格式: [{"role": "assistant", "content": raw_text}, {"role": "data", "structured": {...}}]
    """
    schema_cls = get_schema(task_type)
    structured = {}
    if schema_cls:
        try:
            # 尝试在 raw_text 中解析结构化数据
            # 如果 raw_text 中嵌入了 JSON 块，提取并合并
            structured = _extract_structured_from_text(raw_text, schema_cls)
        except Exception:
            pass

    return [
        {"role": "assistant", "content": raw_text},
        {"role": "data", "structured": structured, "task_type": task_type},
    ]


def _extract_structured_from_text(text: str, schema_cls: type[BaseModel]) -> dict:
    """从 LLM 输出的文本中提取结构化数据。

    先尝试在 ```json ... ``` 块中找 JSON，
    然后 fallback 直接用 LLM 文本填充 raw_text。
    """
    import json
    import re

    # 尝试提取 JSON 块
    blocks = re.findall(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    for block in blocks:
        try:
            data = json.loads(block.strip())
            if isinstance(data, dict):
                data["raw_text"] = text
                return data
        except json.JSONDecodeError:
            continue

    # 无 JSON 块 → 仅返回 raw_text
    return {"raw_text": text}
