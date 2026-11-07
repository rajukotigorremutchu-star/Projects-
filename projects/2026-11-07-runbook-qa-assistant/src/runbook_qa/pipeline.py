from __future__ import annotations

from pathlib import Path

from .answerer import ABSTAIN_TEXT, Answer, claude_answer, coverage, extractive_answer
from .bm25 import BM25
from .chunker import load_chunks

DEFAULT_THRESHOLD = 0.5


class RunbookQA:
    def __init__(self, runbook_dir: str | Path, mode: str = "extractive",
                 threshold: float = DEFAULT_THRESHOLD, top_k: int = 3):
        self.index = BM25(load_chunks(runbook_dir))
        self.mode, self.threshold, self.top_k = mode, threshold, top_k

    def retrieve(self, question: str):
        return self.index.search(question, self.top_k)

    def ask(self, question: str) -> Answer:
        hits = self.retrieve(question)
        # Abstain when nothing matches or the best chunk covers too little of the question.
        if not hits or coverage(question, hits[0][0]) < self.threshold:
            return Answer(ABSTAIN_TEXT, [], True)
        if self.mode == "claude":
            return claude_answer(question, hits)
        return extractive_answer(question, hits)
