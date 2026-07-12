from pydantic import BaseModel, Field


class FeatureToggle(BaseModel):
    name: str
    source: str = Field(description="Flag service, annotation, property, or environment lookup")
    line: int | None = None
    enabled_behavior: str
    disabled_behavior: str | None = None


class FeatureToggleAnalysis(BaseModel):
    toggles: list[FeatureToggle] = Field(default_factory=list)
