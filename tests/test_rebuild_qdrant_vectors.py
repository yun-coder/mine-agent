import math

import pytest

from scripts.rebuild_qdrant_vectors import validate_embeddings


def test_validate_embeddings_accepts_finite_nonzero_vectors():
    vectors = validate_embeddings([[0.3, 0.4]], expected_count=1, expected_dim=2)

    assert vectors == [[0.3, 0.4]]


@pytest.mark.parametrize(
    "vectors,error",
    [
        ([], "count mismatch"),
        ([[1.0]], "dimension mismatch"),
        ([[0.0, 0.0]], "zero vector"),
        ([[math.inf, 0.0]], "non-finite"),
    ],
)
def test_validate_embeddings_rejects_invalid_vectors(vectors, error):
    with pytest.raises(RuntimeError, match=error):
        validate_embeddings(vectors, expected_count=1, expected_dim=2)
