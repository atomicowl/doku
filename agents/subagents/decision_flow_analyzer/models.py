from pydantic import BaseModel, Field


class DecisionPoint(BaseModel):
    condition: str
    line: int | None = None
    true_path: str
    false_path: str | None = None


class DecisionFlowAnalysis(BaseModel):
    decision_points: list[DecisionPoint] = Field(default_factory=list)
    flow_mermaid: str = Field(description="A flowchart TD diagram with decisions and all exits")
