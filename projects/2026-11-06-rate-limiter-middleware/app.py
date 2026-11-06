"""Demo API:  uvicorn app:app --reload"""
from fastapi import FastAPI

from ratelimiter.middleware import RateLimitMiddleware, rule

app = FastAPI(title="Rate limiter demo")
app.add_middleware(
    RateLimitMiddleware,
    rules=[
        rule("/", "sliding_counter", 100, 60),       # default: 100/min
        rule("/search", "token_bucket", 5, 10),      # bursty but 0.5 req/s sustained
        rule("/login", "sliding_log", 3, 60),        # strict, exact
    ],
)


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/search")
def search(q: str = ""):
    return {"q": q, "results": []}


@app.post("/login")
def login():
    return {"token": "demo"}
