from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.behavior import Behavior
from app.models.behavior_implementation import BehaviorImplementation
from app.schemas.behavior_implementation import (
    BehaviorImplementationCreate,
    BehaviorImplementationRead,
    BehaviorImplementationUpdate,
)

router = APIRouter(prefix="/implementations", tags=["implementations"])


@router.get("/behavior/{behavior_id}", response_model=List[BehaviorImplementationRead])
def list_implementations_for_behavior(
    behavior_id: int,
    db: Session = Depends(get_db),
):
    behavior = db.get(Behavior, behavior_id)
    if not behavior:
        raise HTTPException(status_code=404, detail="Behavior not found")
    return behavior.implementations


@router.post("/", response_model=BehaviorImplementationRead)
def create_implementation(
    payload: BehaviorImplementationCreate,
    db: Session = Depends(get_db),
):
    behavior = db.get(Behavior, payload.behavior_id)
    if not behavior:
        raise HTTPException(status_code=404, detail="Behavior not found")

    obj = BehaviorImplementation(
        behavior_id=payload.behavior_id,
        language=payload.language,
        repo_url=payload.repo_url,
        revision=payload.revision,
        file_path=payload.file_path,
        status=payload.status,
        notes=payload.notes,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.get("/{implementation_id}", response_model=BehaviorImplementationRead)
def get_implementation(
    implementation_id: int,
    db: Session = Depends(get_db),
):
    obj = db.get(BehaviorImplementation, implementation_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Implementation not found")
    return obj


@router.patch("/{implementation_id}", response_model=BehaviorImplementationRead)
def update_implementation(
    implementation_id: int,
    payload: BehaviorImplementationUpdate,
    db: Session = Depends(get_db),
):
    obj = db.get(BehaviorImplementation, implementation_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Implementation not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)

    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj
