"""What routes ask for, and what supplies it.

The three collaborators a route can need — settings, storage, the queue — are
FastAPI dependencies rather than module globals, so a test overrides them with
`app.dependency_overrides` instead of monkeypatching imports.
"""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from pickleball_api.config import Settings
from pickleball_api.db import get_session
from pickleball_api.queue import JobQueue
from pickleball_api.storage import Storage


def get_app_settings(request: Request) -> Settings:
    """The settings this app was built with.

    Not `get_settings()` directly: that reads the environment once per process,
    so a route calling it would ignore whatever `create_app` was handed and the
    tests could not vary the upload limit.
    """
    settings: Settings = request.app.state.settings
    return settings


def get_storage(request: Request) -> Storage:
    """The storage built once at startup and kept on the app state."""
    storage: Storage = request.app.state.storage
    return storage


def get_queue(request: Request) -> JobQueue:
    queue: JobQueue = request.app.state.queue
    return queue


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
SessionDep = Annotated[Session, Depends(get_session)]
StorageDep = Annotated[Storage, Depends(get_storage)]
QueueDep = Annotated[JobQueue, Depends(get_queue)]
