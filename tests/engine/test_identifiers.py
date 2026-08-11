import pytest

from graphcheck.engine.identifiers import (
    cypher_identifier,
    node_pattern,
    property_access,
    relationship_pattern,
)


@pytest.mark.parametrize(
    ("identifier", "escaped"),
    [
        ("Customer", "`Customer`"),
        ("Customer Account", "`Customer Account`"),
        ("Δεδομένα", "`Δεδομένα`"),
        ("MATCH", "`MATCH`"),
        ("odd`name", "`odd``name`"),
        ("Customer`) DELETE n //", "`Customer``) DELETE n //`"),
    ],
)
def test_cypher_identifier_escapes_one_native_token(identifier, escaped):
    assert cypher_identifier(identifier) == escaped


@pytest.mark.parametrize("identifier", ["", "   ", "line\nbreak", "nul\x00byte", "tab\tname"])
def test_cypher_identifier_rejects_blank_and_control_characters(identifier):
    with pytest.raises(ValueError):
        cypher_identifier(identifier)


def test_typed_query_fragments_share_the_identifier_escaping_contract():
    assert node_pattern("n", "Customer Account") == "(n:`Customer Account`)"
    assert relationship_pattern("r", "HAS`ROLE") == "[r:`HAS``ROLE`]"
    assert property_access("n", "select") == "n.`select`"
    assert node_pattern("n") == "(n)"
    assert relationship_pattern("r") == "[r]"
