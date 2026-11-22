from pickleball_ml.ball.render import trail_runs


def test_trail_is_split_where_a_frame_had_no_ball() -> None:
    runs = trail_runs([(0, 0), (1, 1), None, (5, 5), (6, 6), (7, 7)])

    assert [run.tolist() for run in runs] == [[[0, 0], [1, 1]], [[5, 5], [6, 6], [7, 7]]]


def test_a_lone_position_between_gaps_draws_no_line() -> None:
    assert trail_runs([None, (3, 3), None, (9, 9)]) == []
    assert trail_runs([]) == []
