from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.behavior import Behavior
from app.models.behavior_contract import BehaviorContract
from app.schemas.behavior_contract import (
    BehaviorContractCreate,
    BehaviorContractRead,
    BehaviorContractUpdate,
)

router = APIRouter(prefix="/contracts", tags=["contracts"])


@router.get("/behavior/{behavior_id}", response_model=List[BehaviorContractRead])
def list_contracts_for_behavior(behavior_id: int, db: Session = Depends(get_db)):
    behavior = db.get(Behavior, behavior_id)
    if not behavior:
        raise HTTPException(status_code=404, detail="Behavior not found")
    return behavior.contracts


@router.post("/", response_model=BehaviorContractRead)
def create_contract(payload: BehaviorContractCreate, db: Session = Depends(get_db)):
    behavior = db.get(Behavior, payload.behavior_id)
    if not behavior:
        raise HTTPException(status_code=404, detail="Behavior not found")

    obj = BehaviorContract(
        behavior_id=payload.behavior_id,
        name=payload.name,
        description=payload.description,
        version=payload.version,
        input_schema=payload.input_schema,
        output_schema=payload.output_schema,
        test_cases=payload.test_cases,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj


@router.get("/{contract_id}", response_model=BehaviorContractRead)
def get_contract(contract_id: int, db: Session = Depends(get_db)):
    obj = db.get(BehaviorContract, contract_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Contract not found")
    return obj


@router.patch("/{contract_id}", response_model=BehaviorContractRead)
def update_contract(
    contract_id: int,
    payload: BehaviorContractUpdate,
    db: Session = Depends(get_db),
):
    obj = db.get(BehaviorContract, contract_id)
    if not obj:
        raise HTTPException(status_code=404, detail="Contract not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(obj, field, value)

    db.add(obj)
    db.commit()
    db.refresh(obj)
    return obj
