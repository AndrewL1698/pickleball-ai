# Pickleball AI

Turns fixed-camera video of a doubles pickleball match into what happened on court, and from that into statistics and coaching evidence.

## Match structure

**Match**:
One uploaded video of doubles play, together with everything worked out from it.
_Avoid_: Session, recording

**Game**:
One scored game within a match, played to a winning score.
_Avoid_: Set

**Changeover**:
The moment the two teams swap ends of the court.
_Avoid_: End switch, side switch

**Period**:
A stretch of a match during which neither team changes ends; changeovers separate one period from the next. A game can contain more than one period (game 3 changes ends at 6 points).
_Avoid_: Game segment, half

## Players and court positions

**Player**:
One of the four people playing the match. A player keeps the same identity for the whole match, whichever end or slot they occupy.
_Avoid_: Track, slot (as a name for a person)

**Team**:
A pair of partners, labelled A or B, persistent for the whole match. Team A is the team on the near end when the match starts.
_Avoid_: Side

**Side**:
The end of the court a player is on at a given moment: near (the camera's end) or far.
_Avoid_: Half, team

**Slot**:
A player's side plus left or right at a given moment, with left and right taken from that player's own baseline facing the net. The right slot is the even court.
_Avoid_: Position (as a name for a slot), court-axis left/right

**User**:
Someone with an account. A player in a match can be linked to a user.
_Avoid_: Account holder, profile

**Guest**:
A player who isn't linked to a user. A guest's match ratings count toward nothing.

**Ground-contact point**:
Where a player's feet meet the court surface. This is the only point of a player that maps correctly onto the court plane.
_Avoid_: Player position (unqualified), foot box

## Ratings and groups

**Match rating**:
A 0-10 number for how well one player played in one match. It comes with the parts that add up to it.
_Avoid_: Score, performance score (in pickleball, "score" means points)

**Group**:
A set of friends who share matches with each other and have a leaderboard together. Someone can be in more than one group.
_Avoid_: League, club, team (a team is two partners in one match)

**Member**:
A user who belongs to a particular group.

**Player of the match**:
In a published match, the player with the highest match rating.
_Avoid_: MVP (in this project, MVP means minimum viable product)
