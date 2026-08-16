from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

GeneratedPrompt = Annotated[str, Field(min_length=1, max_length=500)]


class SamplePromptBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompts: list[GeneratedPrompt] = Field(min_length=1, max_length=40)
