"""A match's four players through the ORM."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tests_support_api import calibrated_match

from pickleball_api.models import Match, Player, Team


def test_a_match_gets_four_players_in_two_teams_once(session: Session) -> None:
    match, _ = calibrated_match(session)
    first = match.ensure_players()
    session.commit()
    ids = [player.id for player in first]

    session.expire_all()
    found = session.get(Match, match.id)
    assert found is not None
    again = found.ensure_players()
    session.commit()

    assert [(p.number, p.team) for p in again] == [
        (1, Team.A), (2, Team.A), (3, Team.B), (4, Team.B),
    ]
    assert [p.id for p in again] == ids
    assert session.scalar(select(func.count()).select_from(Player)) == 4


def test_a_player_number_is_in_its_own_team(session: Session) -> None:
    match, _ = calibrated_match(session)
    match.players.append(Player(number=3, team=Team.A))
    with pytest.raises(IntegrityError):
        session.commit()


def test_deleting_a_match_deletes_its_players(session: Session) -> None:
    match, _ = calibrated_match(session)
    match.ensure_players()
    session.commit()
    session.delete(match)
    session.commit()
    assert session.scalar(select(func.count()).select_from(Player)) == 0
