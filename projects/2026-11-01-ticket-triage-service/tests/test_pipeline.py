import pandas as pd
import pytest

from triage import etl, generate, model


@pytest.fixture(scope="module")
def clean_df():
    df, _ = etl.clean(generate.generate_tickets(1200, seed=3))
    return df


def test_generator_deterministic():
    a = generate.generate_tickets(50, seed=1)
    b = generate.generate_tickets(50, seed=1)
    pd.testing.assert_frame_equal(a, b)
    assert set(a["label"]) <= set(generate.LABELS)


def test_generator_validates():
    with pytest.raises(ValueError):
        generate.generate_tickets(0)
    with pytest.raises(ValueError):
        generate.generate_tickets(10, label_noise=1.5)


def test_clean_drops_bad_rows():
    df = pd.DataFrame({
        "ticket_id": ["a", "b", "c", "d", "e"],
        "text": ["Hello  world", "hello world", "", None, "x"],
        "label": ["bug", "bug", "bug", "bug", "nonsense"],
    })
    out, stats = etl.clean(df)
    assert list(out["ticket_id"]) == ["a"]
    assert out["text"].iloc[0] == "Hello world"
    assert stats["dropped_empty"] == 2
    assert stats["dropped_bad_label"] == 1
    assert stats["dropped_duplicates"] == 1


def test_load_sqlite_idempotent(tmp_path, clean_df):
    db = tmp_path / "t.db"
    n1 = etl.load_sqlite(clean_df, db)
    n2 = etl.load_sqlite(clean_df, db)
    assert n1 == len(clean_df) and n2 == 0
    assert len(etl.read_sqlite(db)) == len(clean_df)


@pytest.mark.parametrize("mode", ["random", "group"])
def test_split_no_leakage_and_reproducible(clean_df, mode):
    tr, te = model.split(clean_df, mode)
    tr2, te2 = model.split(clean_df, mode)
    assert list(te["ticket_id"]) == list(te2["ticket_id"])
    assert set(tr["ticket_id"]).isdisjoint(te["ticket_id"])
    assert set(tr["text_hash"]).isdisjoint(te["text_hash"])
    if mode == "group":
        assert set(tr["template_id"]).isdisjoint(te["template_id"])


def test_model_beats_baseline(clean_df):
    r = model.evaluate(clean_df, "random")
    assert r["tfidf_logreg"]["macro_f1"] > r["baseline_majority"]["macro_f1"] + 0.3


def test_unknown_split_mode(clean_df):
    with pytest.raises(ValueError):
        model.split(clean_df, "bogus")
