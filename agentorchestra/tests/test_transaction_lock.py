from __future__ import annotations

import multiprocessing
import threading
from pathlib import Path

import pytest

from agentorchestra.config import Settings
from agentorchestra.exceptions import PromotionError
from agentorchestra.services.transaction_lock import (
    LOCK_FILE_NAME,
    working_site_transaction,
)


def _make_settings(tmp_path: Path) -> Settings:
    root = tmp_path / "project"
    root.mkdir()
    return Settings(project_root=root)


def _hold_lock_in_process(
    project_root: str,
    entered: multiprocessing.synchronize.Event,
    release: multiprocessing.synchronize.Event,
) -> None:
    settings = Settings(project_root=Path(project_root))
    with working_site_transaction(settings, timeout_seconds=5):
        entered.set()
        if not release.wait(15):
            raise RuntimeError("Parent did not release the transaction-lock test process.")


def test_working_site_lock_serializes_threads(tmp_path):
    settings = _make_settings(tmp_path)
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    errors: list[BaseException] = []

    def first() -> None:
        try:
            with working_site_transaction(settings, timeout_seconds=2):
                first_entered.set()
                if not release_first.wait(2):
                    raise RuntimeError("Thread-lock test timed out.")
        except BaseException as exc:
            errors.append(exc)

    def second() -> None:
        try:
            if not first_entered.wait(2):
                raise RuntimeError("First thread never acquired the lock.")
            with working_site_transaction(settings, timeout_seconds=2):
                second_entered.set()
        except BaseException as exc:
            errors.append(exc)

    first_thread = threading.Thread(target=first)
    second_thread = threading.Thread(target=second)
    first_thread.start()
    second_thread.start()
    assert first_entered.wait(2)
    assert not second_entered.wait(0.1)
    release_first.set()
    first_thread.join(2)
    second_thread.join(2)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert second_entered.is_set()
    assert errors == []
    assert (settings.project_root / LOCK_FILE_NAME).is_file()


def test_working_site_lock_serializes_processes(tmp_path):
    settings = _make_settings(tmp_path)
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_lock_in_process,
        args=(str(settings.project_root), entered, release),
    )
    process.start()
    try:
        assert entered.wait(15)
        with (
            pytest.raises(PromotionError, match="Timed out waiting"),
            working_site_transaction(settings, timeout_seconds=0.1),
        ):
            raise AssertionError("A second process entered the locked transaction.")
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.terminate()
            process.join(10)

    assert process.exitcode == 0
