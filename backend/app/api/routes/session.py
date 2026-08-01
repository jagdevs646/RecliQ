from fastapi import APIRouter, Depends

from app.api.deps import get_session_id


router = APIRouter(prefix="/session", tags=["session"])


@router.get("")
def current_session(session_id: str = Depends(get_session_id)) -> dict[str, str]:
    return {"session_id": session_id}
