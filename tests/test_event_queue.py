from event_queue import EventQueue


def test_push_returns_sequence_and_remove_filters_by_kind() -> None:
    q = EventQueue()

    seq1 = q.push(1.0, "OP_END", {"id": 1})
    seq2 = q.push(0.5, "OP_START", {"id": 2})
    seq3 = q.push(1.0, "OP_END", {"id": 3})

    assert seq1 != seq2 != seq3

    # Removing with mismatched kind should keep the entry
    removed = q.remove(seq2, kind="OP_END")
    assert removed is False

    # Removing without kind accepts any match
    assert q.remove(seq2) is True

    # Removing the same handle again returns False
    assert q.remove(seq2) is False

    # Remove via kind guard; ensure the other OP_END remains
    assert q.remove(seq1, kind="OP_END") is True
    assert q.remove(seq1, kind="OP_END") is False

    t0, batch = q.pop_time_batch()
    assert t0 == 1.0
    assert len(batch) == 1
    (_, _, seq, kind, payload) = batch[0]
    assert seq == seq3
    assert kind == "OP_END"
    assert payload["id"] == 3
    assert payload["event_seq"] == seq3

    # Queue should now be empty
    assert q.pop_time_batch() == (0.0, [])
