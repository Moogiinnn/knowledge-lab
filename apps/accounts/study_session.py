"""study_session_id: a random id per login, logged as session_id in research events.

It groups the submits of one sitting without exposing the Django session key.
"""

import uuid

KEY = "study_session_id"


def start_study_session(request) -> uuid.UUID:
    value = uuid.uuid4()
    request.session[KEY] = str(value)
    return value


def current_study_session(request) -> uuid.UUID:
    value = request.session.get(KEY)
    if value is None:
        return start_study_session(request)
    return uuid.UUID(value)
