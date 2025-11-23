from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.behavior import Behavior
from app.schemas.behavior import BehaviorCreate, BehaviorRead, BehaviorUpdate

router = APIRouter(prefix="/behaviors", tags=["behaviors"])


@router.get("/", response_model=List[BehaviorRead])
def list_behaviors(
    db: Session = Depends(get_db),
    domain: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
):
    """
    List behaviors, optionally filtering by domain and a single tag.
    """
    query = db.query(Behavior)

    if domain:
        query = query.filter(Behavior.domain == domain)

    if tag:
        # tags is stored as a JSON array of strings.
        # `contains([tag])` generates "tags @> '["tag"]'" which works for arrays.
        query = query.filter(Behavior.tags.contains([tag]))

    return query.order_by(Behavior.id.asc()).all()


@router.post("/", response_model=BehaviorRead)
def create_behavior(payload: BehaviorCreate, db: Session = Depends(get_db)):
    """
    Create a new behavior. tags is stored directly as a list of strings.
    """
    obj = Behavior(
        name=payload.name,
        description=payload.description,
        domain=payload.domain,
        tags=payload.tags,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.get("/{behavior_id}", response_model=BehaviorRead)
def get_behavior(behavior_id: int, db: Session = Depends(get_db)):
    obj = db.get(Behavior, behavior_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Behavior not found")
    return obj


@router.patch("/{behavior_id}", response_model=BehaviorRead)
def update_behavior(
    behavior_id: int,
    payload: BehaviorUpdate,
    db: Session = Depends(get_db),
):
    obj = db.get(Behavior, behavior_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Behavior not found")

    if payload.description is not None:
        obj.description = payload.description
    if payload.domain is not None:
        obj.domain = payload.domain
    if payload.tags is not None:
        obj.tags = payload.tags

    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj
