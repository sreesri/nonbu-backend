"""The user's library of dishes, and of saved meals built from those dishes."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from app.auth import CurrentUser, Session
from app.models import Dish, SavedMeal, SavedMealItem, User
from app.schemas import (
    DishIn,
    DishOut,
    DishPatch,
    SavedMealIn,
    SavedMealItemIn,
    SavedMealOut,
    SavedMealPatch,
)

router = APIRouter(prefix="/library", tags=["library"])


def _not_found(what: str) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"{what} not found")


async def _owned_dish(db: Session, user: User, dish_id: int) -> Dish:
    dish = await db.get(Dish, dish_id)
    if dish is None or dish.user_id != user.id:
        raise _not_found("dish")
    return dish


async def _owned_meal(db: Session, user: User, meal_id: int) -> SavedMeal:
    meal = await db.get(SavedMeal, meal_id)
    if meal is None or meal.user_id != user.id:
        raise _not_found("saved meal")
    return meal


async def _meal_items(db: Session, user: User, items: list[SavedMealItemIn]) -> list[SavedMealItem]:
    ids = {item.dish_id for item in items}
    dishes = {
        d.id: d
        for d in await db.scalars(select(Dish).where(Dish.user_id == user.id, Dish.id.in_(ids)))
    }
    if missing := ids - dishes.keys():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown dish ids: {sorted(missing)}"
        )
    return [
        SavedMealItem(dish=dishes[item.dish_id], servings=item.servings, position=i)
        for i, item in enumerate(items)
    ]


# --- dishes -------------------------------------------------------------


@router.get("/dishes", response_model=list[DishOut])
async def list_dishes(user: CurrentUser, db: Session) -> list[DishOut]:
    rows = await db.scalars(
        select(Dish).where(Dish.user_id == user.id).order_by(func.lower(Dish.name), Dish.id)
    )
    return [DishOut.model_validate(d) for d in rows]


@router.post("/dishes", response_model=DishOut, status_code=status.HTTP_201_CREATED)
async def create_dish(body: DishIn, user: CurrentUser, db: Session) -> DishOut:
    dish = Dish(user_id=user.id, **body.model_dump())
    db.add(dish)
    await db.commit()
    return DishOut.model_validate(dish)


@router.patch("/dishes/{dish_id}", response_model=DishOut)
async def patch_dish(dish_id: int, body: DishPatch, user: CurrentUser, db: Session) -> DishOut:
    dish = await _owned_dish(db, user, dish_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "name" and value is None:
            continue
        setattr(dish, field, value)
    await db.commit()
    return DishOut.model_validate(dish)


@router.delete("/dishes/{dish_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_dish(dish_id: int, user: CurrentUser, db: Session) -> None:
    """Also removes the dish from saved meals; a saved meal left with no dishes is deleted."""
    dish = await _owned_dish(db, user, dish_id)
    affected = list(
        await db.scalars(
            select(SavedMeal).join(SavedMeal.items).where(SavedMealItem.dish_id == dish.id)
        )
    )
    for meal in affected:
        meal.items = [item for item in meal.items if item.dish_id != dish.id]
        if not meal.items:
            await db.delete(meal)
    await db.delete(dish)
    await db.commit()


# --- saved meals --------------------------------------------------------


@router.get("/meals", response_model=list[SavedMealOut])
async def list_saved_meals(user: CurrentUser, db: Session) -> list[SavedMealOut]:
    rows = await db.scalars(
        select(SavedMeal)
        .where(SavedMeal.user_id == user.id)
        .order_by(func.lower(SavedMeal.name), SavedMeal.id)
    )
    return [SavedMealOut.model_validate(m) for m in rows]


@router.post("/meals", response_model=SavedMealOut, status_code=status.HTTP_201_CREATED)
async def create_saved_meal(body: SavedMealIn, user: CurrentUser, db: Session) -> SavedMealOut:
    meal = SavedMeal(user_id=user.id, name=body.name, items=await _meal_items(db, user, body.items))
    db.add(meal)
    await db.commit()
    return SavedMealOut.model_validate(meal)


@router.patch("/meals/{meal_id}", response_model=SavedMealOut)
async def patch_saved_meal(
    meal_id: int, body: SavedMealPatch, user: CurrentUser, db: Session
) -> SavedMealOut:
    meal = await _owned_meal(db, user, meal_id)
    if body.name is not None:
        meal.name = body.name
    if body.items is not None:
        meal.items = await _meal_items(db, user, body.items)
    await db.commit()
    return SavedMealOut.model_validate(meal)


@router.delete("/meals/{meal_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_meal(meal_id: int, user: CurrentUser, db: Session) -> None:
    await db.delete(await _owned_meal(db, user, meal_id))
    await db.commit()
