class CommentCollectionFailure(Exception):
    """Raised when a comment header is present with zero threads collected and a
    count that is neither absent nor exactly zero — per SPEC-V3's Comment
    availability table, this is not a legitimate state, it's a collection failure."""
