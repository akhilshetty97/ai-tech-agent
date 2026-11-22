from pydantic import BaseModel, ConfigDict, Field


class WriteNoteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_url: str = Field(description="The URL the facts came from")
    facts: list[str] = Field(
        min_length=1,
        description="Key facts from the page that help answer the question, one per item, with concrete details",
    )


def write_note(source_url: str, facts: list[str]) -> str:
    # Nothing to store: the note lives in the conversation as this call's arguments,
    # which are never removed. Page text is removed later; notes stay.
    return f"Saved {len(facts)} facts from {source_url}."
