RICE = {"name": "Rice", "quantity": 1, "unit": "cup", "calories": 200, "carbs_g": 45}
DAL = {"name": "dal", "calories": 150, "protein_g": 9, "fiber_g": 4}


async def add_dish(client, **body):
    resp = await client.post("/library/dishes", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def add_saved_meal(client, name, items):
    resp = await client.post("/library/meals", json={"name": name, "items": items})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_dish_crud(client):
    rice = await add_dish(client, **RICE)
    await add_dish(client, **DAL)

    names = [d["name"] for d in (await client.get("/library/dishes")).json()]
    assert names == ["dal", "Rice"]  # alphabetical, ignoring case

    resp = await client.patch(f"/library/dishes/{rice['id']}", json={"calories": 210, "name": None})
    assert resp.status_code == 200
    assert resp.json()["calories"] == 210
    assert resp.json()["name"] == "Rice"  # required field not cleared

    assert (await client.post("/library/dishes", json={"name": ""})).status_code == 422
    assert (
        await client.post("/library/dishes", json={"name": "X", "fat_g": -1})
    ).status_code == 422


async def test_saved_meal_totals_follow_dishes(client):
    rice = await add_dish(client, **RICE)
    dal = await add_dish(client, **DAL)
    meal = await add_saved_meal(
        client,
        "Rice and dal",
        [{"dish_id": rice["id"], "servings": 1.5}, {"dish_id": dal["id"]}],
    )
    assert [i["dish"]["name"] for i in meal["items"]] == ["Rice", "dal"]
    assert meal["totals"] == {
        "calories": 450,
        "protein_g": 9,
        "carbs_g": 67.5,
        "fat_g": 0,
        "fiber_g": 4,
    }

    await client.patch(f"/library/dishes/{dal['id']}", json={"calories": 170})
    listed = (await client.get("/library/meals")).json()
    assert listed[0]["totals"]["calories"] == 470

    resp = await client.patch(
        f"/library/meals/{meal['id']}", json={"items": [{"dish_id": dal["id"], "servings": 2}]}
    )
    assert resp.json()["totals"]["calories"] == 340


async def test_saved_meal_rejects_unknown_dishes(client, anon_client, google_claims):
    rice = await add_dish(client, **RICE)
    resp = await client.post(
        "/library/meals", json={"name": "Ghost", "items": [{"dish_id": rice["id"] + 99}]}
    )
    assert resp.status_code == 422
    assert (
        await client.post("/library/meals", json={"name": "Empty", "items": []})
    ).status_code == 422

    # Another user's dish is just as unknown.
    google_claims.update(sub="google-other")
    other = (await anon_client.post("/auth/google", json={"id_token": "good-token"})).json()
    headers = {"Authorization": f"Bearer {other['access_token']}"}
    resp = await anon_client.post(
        "/library/meals",
        json={"name": "Stolen", "items": [{"dish_id": rice["id"]}]},
        headers=headers,
    )
    assert resp.status_code == 422
    resp = await anon_client.delete(f"/library/dishes/{rice['id']}", headers=headers)
    assert resp.status_code == 404


async def test_deleting_a_dish_updates_saved_meals(client):
    rice = await add_dish(client, **RICE)
    dal = await add_dish(client, **DAL)
    both = await add_saved_meal(client, "Both", [{"dish_id": rice["id"]}, {"dish_id": dal["id"]}])
    await add_saved_meal(client, "Just dal", [{"dish_id": dal["id"]}])

    assert (await client.delete(f"/library/dishes/{dal['id']}")).status_code == 204

    meals = (await client.get("/library/meals")).json()
    # "Just dal" had no dishes left, so it is gone; "Both" keeps its rice.
    assert [m["name"] for m in meals] == ["Both"]
    assert meals[0]["id"] == both["id"]
    assert [i["dish"]["name"] for i in meals[0]["items"]] == ["Rice"]


async def test_deleting_a_saved_meal_keeps_its_dishes(client):
    rice = await add_dish(client, **RICE)
    meal = await add_saved_meal(client, "Rice", [{"dish_id": rice["id"]}])
    assert (await client.delete(f"/library/meals/{meal['id']}")).status_code == 204
    assert (await client.get("/library/meals")).json() == []
    assert len((await client.get("/library/dishes")).json()) == 1
