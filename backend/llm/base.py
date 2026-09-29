"""Provider-neutral LLM contracts."""
from dataclasses import dataclass
from typing import Protocol

@dataclass
class LLMResponse:
    content:str; model:str; prompt_tokens:int|None=None; completion_tokens:int|None=None

class LLMProvider(Protocol):
    async def chat(self,*,model:str,messages:list[dict[str,str]],max_tokens:int|None=None,temperature:float|None=None)->LLMResponse:...
