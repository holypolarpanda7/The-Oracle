"""
Combat bonds — a named link between creatures that changes how a fight works.

Some features tie a handful of creatures together for a while and then grant
them things no ordinary ally gets: they roll better initiative, they always
know where each other are, and one of them can be snatched out of a bad spot.
The Cartographer artificer's Adventurer's Atlas is the case this was built for,
but nothing here knows that — a bond is a generic object with three levers, and
the subclass is one *configuration* of it.

Deliberately generic for two reasons. It keeps book-specific naming out of the
repo (the numbers and the label come from whoever grants it), and the same
three levers keep turning up: a telepathic bond, a pack's shared senses, a
warlock's link to a familiar all want some subset of them.

The three levers:

* **initiative_dice** — extra dice on the initiative roll for every holder.
* **sees_through_cover** — two holders of the SAME bond can see and target each
  other regardless of sight and cover. This is the interesting one, because it
  is the board's own cover rules being deliberately overruled: the tactical
  layer computes cover honestly and then this says "not between these two".
* **rescue_hp** — a holder dropped to 0 can spend their link to come back at
  this many hit points, beside another holder.

Bonds are scoped to a table (``session_id``) and an owner. Re-granting replaces
the owner's previous bond of that kind outright, because that is what "you use
this feature again" means — the old maps vanish.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import Column, Integer, String
from sqlmodel import Field, Session, SQLModel, select


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _norm(ref) -> str:
    """Creature references are compared loosely — names come from everywhere."""
    return str(ref or "").strip().lower()


class CombatBond(SQLModel, table=True):
    """One creature's share of one bond. A bond is all rows sharing a key."""

    __tablename__ = "combat_bond"

    id: Optional[int] = Field(default=None, primary_key=True)

    #: The table this belongs to ("guild:channel").
    session_id: str = Field(sa_column=Column(String, nullable=False, index=True))
    #: What kind of link this is — a free label from whoever granted it.
    kind: str = Field(default="bond", sa_column=Column(String, index=True))
    #: Who created it. Their bonds replace each other; two different owners'
    #: bonds of the same kind coexist and do NOT link to one another.
    owner_ref: str = Field(default="", sa_column=Column(String, index=True))
    #: The creature carrying this share of it.
    holder_ref: str = Field(default="", sa_column=Column(String, index=True))

    initiative_dice: str = Field(default="", sa_column=Column(String))
    sees_through_cover: bool = Field(default=False)
    rescue_hp: int = Field(default=0, sa_column=Column(Integer))

    active: bool = Field(default=True, index=True)
    #: Set when a holder spends their share (a rescue burns the map).
    spent: bool = Field(default=False)
    created_day: int = Field(default=0, sa_column=Column(Integer))
    note: Optional[str] = Field(default=None, sa_column=Column(String))

    created_at: datetime = Field(default_factory=_utcnow)


# ----- granting and revoking ------------------------------------------------


def grant(session: Session, *, session_id: str, kind: str, owner_ref: str,
          holders: Iterable[str], initiative_dice: str = "",
          sees_through_cover: bool = False, rescue_hp: int = 0,
          world_day: int = 0, note: str = "") -> list[CombatBond]:
    """Link ``holders`` under ``owner_ref``, replacing that owner's last bond.

    Replacement rather than accumulation is the point: a feature you can use
    again is a feature whose previous use ends. Returns the new rows.
    """
    revoke(session, session_id=session_id, kind=kind, owner_ref=owner_ref)
    made: list[CombatBond] = []
    seen: set[str] = set()
    for h in holders:
        ref = _norm(h)
        if not ref or ref in seen:
            continue
        seen.add(ref)
        row = CombatBond(
            session_id=session_id, kind=kind, owner_ref=_norm(owner_ref),
            holder_ref=ref, initiative_dice=initiative_dice,
            sees_through_cover=bool(sees_through_cover),
            rescue_hp=int(rescue_hp), created_day=int(world_day), note=note or None)
        session.add(row)
        made.append(row)
    session.commit()
    for r in made:
        session.refresh(r)
    return made


def revoke(session: Session, *, session_id: str, kind: str,
           owner_ref: Optional[str] = None,
           holder_ref: Optional[str] = None) -> int:
    """End a bond (or one creature's share of it). Returns rows ended."""
    stmt = select(CombatBond).where(CombatBond.session_id == session_id,
                                    CombatBond.kind == kind,
                                    CombatBond.active == True)  # noqa: E712
    if owner_ref is not None:
        stmt = stmt.where(CombatBond.owner_ref == _norm(owner_ref))
    if holder_ref is not None:
        stmt = stmt.where(CombatBond.holder_ref == _norm(holder_ref))
    rows = list(session.exec(stmt).all())
    for r in rows:
        r.active = False
        session.add(r)
    session.commit()
    return len(rows)


def revoke_all_for_owner(session: Session, *, session_id: str,
                         owner_ref: str) -> int:
    """Every bond this creature granted, of any kind — they died, say."""
    rows = list(session.exec(select(CombatBond).where(
        CombatBond.session_id == session_id,
        CombatBond.owner_ref == _norm(owner_ref),
        CombatBond.active == True)).all())  # noqa: E712
    for r in rows:
        r.active = False
        session.add(r)
    session.commit()
    return len(rows)


# ----- reading --------------------------------------------------------------


def shares_of(session: Session, session_id: str,
              holder_ref: str) -> list[CombatBond]:
    """Every live bond this creature holds a share of."""
    return list(session.exec(select(CombatBond).where(
        CombatBond.session_id == session_id,
        CombatBond.holder_ref == _norm(holder_ref),
        CombatBond.active == True)).all())  # noqa: E712


def holders(session: Session, session_id: str, *, kind: str,
            owner_ref: str) -> list[str]:
    """Who currently carries this owner's bond."""
    rows = session.exec(select(CombatBond).where(
        CombatBond.session_id == session_id,
        CombatBond.kind == kind,
        CombatBond.owner_ref == _norm(owner_ref),
        CombatBond.active == True)).all()  # noqa: E712
    return [r.holder_ref for r in rows]


def linked(session: Session, session_id: str, a_ref: str, b_ref: str,
           *, requiring: str = "") -> Optional[CombatBond]:
    """The bond joining two creatures, if one does. None otherwise.

    ``requiring`` names a lever the bond must actually grant
    (``"sees_through_cover"``), so a link that doesn't do the thing being asked
    about doesn't answer yes to it. Two creatures are linked only through the
    SAME owner's bond — two artificers' atlases don't join their parties.
    """
    a, b = _norm(a_ref), _norm(b_ref)
    if not a or not b or a == b:
        return None
    mine = shares_of(session, session_id, a)
    if not mine:
        return None
    theirs = {(r.kind, r.owner_ref): r for r in shares_of(session, session_id, b)}
    for r in mine:
        other = theirs.get((r.kind, r.owner_ref))
        if other is None:
            continue
        if requiring and not getattr(r, requiring, False):
            continue
        return r
    return None


def sees_through(session: Session, session_id: str, a_ref: str,
                 b_ref: str) -> bool:
    """True when these two ignore sight and cover between themselves."""
    return linked(session, session_id, a_ref, b_ref,
                  requiring="sees_through_cover") is not None


def initiative_dice_for(session: Session, session_id: str,
                        holder_ref: str) -> str:
    """The best initiative die this creature's bonds grant, or "".

    Best rather than summed: two links that each steady your nerves don't make
    you twice as alert, and stacking them is the kind of thing that quietly
    turns into +3d4.
    """
    best, best_avg = "", 0.0
    for r in shares_of(session, session_id, holder_ref):
        expr = (r.initiative_dice or "").strip()
        if not expr:
            continue
        try:
            # Compared by expected value, read off the expression — rolling to
            # decide which die to roll would make the choice random.
            n, _, faces = expr.lower().partition("d")
            avg = float(int(n or 1)) * (float(int(faces)) + 1) / 2.0
        except Exception:
            avg = 1.0
        if avg > best_avg:
            best, best_avg = expr, avg
    return best


# ----- spending -------------------------------------------------------------


def spend_rescue(session: Session, session_id: str, holder_ref: str,
                 *, kind: str = "") -> Optional[CombatBond]:
    """Burn a holder's share for its rescue. None when there is none to burn.

    Returns the row that was spent (carrying ``rescue_hp``), so the caller can
    apply the hit points and the teleport — this module owns the LINK, not the
    creature's state.
    """
    for r in shares_of(session, session_id, holder_ref):
        if r.rescue_hp <= 0 or r.spent:
            continue
        if kind and r.kind != kind:
            continue
        r.spent = True
        r.active = False
        session.add(r)
        session.commit()
        session.refresh(r)
        return r
    return None


def rescue_partners(session: Session, session_id: str,
                    holder_ref: str, spent: CombatBond) -> list[str]:
    """Who a rescued holder may be pulled to: the other holders, and the owner."""
    out = [h for h in holders(session, session_id, kind=spent.kind,
                              owner_ref=spent.owner_ref)
           if h != _norm(holder_ref)]
    if spent.owner_ref and spent.owner_ref not in out \
            and spent.owner_ref != _norm(holder_ref):
        out.append(spent.owner_ref)
    return out


def describe(session: Session, session_id: str) -> list[str]:
    """One line per live bond, for the DM's board block."""
    rows = list(session.exec(select(CombatBond).where(
        CombatBond.session_id == session_id,
        CombatBond.active == True)).all())  # noqa: E712
    groups: dict[tuple, list[CombatBond]] = {}
    for r in rows:
        groups.setdefault((r.kind, r.owner_ref), []).append(r)
    out = []
    for (kind, owner), rs in sorted(groups.items()):
        names = ", ".join(sorted(r.holder_ref for r in rs))
        bits = []
        if rs[0].initiative_dice:
            bits.append(f"+{rs[0].initiative_dice} initiative")
        if rs[0].sees_through_cover:
            bits.append("see and target each other through cover")
        if rs[0].rescue_hp:
            bits.append(f"may burn the link at 0 HP for {rs[0].rescue_hp} HP")
        out.append(f"{kind} ({owner}): {names}"
                   + (f" — {'; '.join(bits)}" if bits else ""))
    return out
