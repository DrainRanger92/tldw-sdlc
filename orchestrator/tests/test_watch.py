from aidevteam.watch import acquire_lock, has_work, release_lock


def test_has_work_seed(repo):
    assert has_work(repo) is True  # seed-задачи pending


def test_lock(repo):
    assert acquire_lock(repo) is True
    assert acquire_lock(repo) is False  # второй раз — занято живым pid
    release_lock(repo)
    assert acquire_lock(repo) is True
    release_lock(repo)


def test_pause_only_when_idle():
    from aidevteam.watch import _pause_seconds

    assert _pause_seconds(True, 300) == 0.0  # работа была — сразу следующий инкремент
    assert _pause_seconds(False, 300) == 300.0
