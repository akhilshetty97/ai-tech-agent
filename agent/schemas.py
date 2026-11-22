from pydantic import BaseModel, ConfigDict, Field


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str = Field(description="One specific, factual claim that answers part of the question")
    source_urls: list[str] = Field(
        min_length=1,
        description="URLs that support this claim. Only URLs you have read with read_url.",
    )


class FinishArgs(BaseModel):
    """What the model writes when it calls finish."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(description="A short direct answer to the question, 2-4 sentences")
    findings: list[Finding] = Field(min_length=1, description="The key findings, each with its sources")
    open_questions: list[str] = Field(
        default_factory=list,
        description="What you could not confirm or would investigate next",
    )


class Report(BaseModel):
    """The final report. The loop adds question and complete; the model never sets them."""

    question: str
    summary: str
    findings: list[Finding]
    open_questions: list[str]
    complete: bool  # False if a limit stopped the run
