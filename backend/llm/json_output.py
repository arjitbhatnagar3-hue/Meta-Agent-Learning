"""Safe extraction and validation of structured model output."""
import json,re
from typing import TypeVar
from pydantic import BaseModel,ValidationError
T=TypeVar('T',bound=BaseModel)

def parse_model_json(text:str,schema:type[T])->T:
    """Extract one JSON object and validate it against a Pydantic schema."""
    cleaned=text.strip()
    if cleaned.startswith('```'):cleaned=re.sub(r'^```(?:json)?\s*','',cleaned);cleaned=re.sub(r'\s*```$','',cleaned)
    try:data=json.loads(cleaned)
    except json.JSONDecodeError:
        start=cleaned.find('{');end=cleaned.rfind('}')
        if start<0 or end<=start:raise ValueError('Model response contains no JSON object')
        data=json.loads(cleaned[start:end+1])
    try:return schema.model_validate(data)
    except ValidationError as exc:raise ValueError(f'Model JSON failed schema validation: {exc}') from exc
