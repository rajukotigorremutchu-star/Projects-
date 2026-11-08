import pytest

from logwatch.pipeline import run


@pytest.fixture(scope="session")
def result():
    return run(days=6, seed=7)
