"""Run: uvicorn examples.demo_app:app   (from the project root)"""

from fastapi import FastAPI

from ratelimitkit import Limiter, RateLimitMiddleware, Rule, SQLiteStore, TokenBucket

app = FastAPI()
store = SQLiteStore("ratelimit_demo.db")  # shared by all uvicorn workers

app.add_middleware(
    RateLimitMiddleware,
    rules=[
        Rule("/search", Limiter(TokenBucket(capacity=5, refill_rate=1), store, name="search")),
        Rule("/", Limiter(TokenBucket(capacity=20, refill_rate=10), store, name="global")),
    ],
)


@app.get("/")
def root():
    return {"ok": True}


@app.get("/search")
def search(q: str = ""):
    return {"q": q, "results": []}
