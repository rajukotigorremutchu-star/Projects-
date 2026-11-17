"""Demo app. Run: uvicorn app:app"""
from fastapi import FastAPI

from ratelimit import RateLimitMiddleware, Rule, make_limiter

app = FastAPI(title="rate limit demo")
app.add_middleware(RateLimitMiddleware, rules=[
    Rule("/search", make_limiter("sliding_log", 5, 10), cost=1),
    Rule("/export", make_limiter("token_bucket", 10, 60), cost=5),
    Rule("/", make_limiter("sliding_counter", 60, 60)),
])


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/search")
def search(q: str = ""):
    return {"q": q, "results": []}


@app.get("/export")
def export():
    return {"rows": 0}
