async def test_history_is_trimmed_and_starts_with_user(db):
    for i in range(10):
        await db.add_message(1, "user", f"u{i}")
        await db.add_message(1, "model", f"m{i}")

    history = await db.get_history(1)
    # 15 kept -> first one is a model message, which must be dropped
    assert len(history) == 14
    assert history[0] == ("user", "u3")
    assert history[-1] == ("model", "m9")


async def test_users_are_isolated_and_reset(db):
    await db.add_message(1, "user", "a")
    await db.add_message(2, "user", "b")
    await db.set_status(1, "completed")

    assert await db.get_status(1) == "completed"
    assert await db.get_status(2) == "active"

    await db.reset(1)
    assert await db.get_history(1) == []
    assert await db.get_status(1) == "active"
    assert await db.get_history(2) == [("user", "b")]
