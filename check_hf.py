"""One-call connectivity check. Reads a fresh token only from .env."""
import asyncio
from backend.config import get_settings
from backend.llm.huggingface import HuggingFaceProvider
async def main():
    settings=get_settings();provider=HuggingFaceProvider(settings)
    response=await provider.chat(model=settings.hf_meta_model_id,messages=[{'role':'system','content':'Return JSON only.'},{'role':'user','content':'Return {"status":"ok"} exactly.'}],max_tokens=50,temperature=0)
    print(response.content)
if __name__=='__main__':asyncio.run(main())
