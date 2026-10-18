from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..services import mcp_client

router = APIRouter(prefix="/mcp", tags=["mcp"])

_NAME_MAX = 40
_HEADERS_MAX = 20


class McpUpsert(BaseModel):
    name: str = Field(min_length=1, max_length=_NAME_MAX)
    transport: str = "stdio"          # stdio | http
    command: str = ""                 # stdio: executable (shlex-split)
    args: list[str] = Field(default_factory=list, max_length=32)
    url: str = ""                     # http endpoint
    headers: dict[str, str] = Field(default_factory=dict)


def _out(row) -> dict:
    """Public shape — headers are NEVER returned (BYOK masked-key precedent)."""
    return {
        "id": row.id,
        "name": row.name,
        "transport": row.transport,
        "command": row.command or "",
        "args": list(row.args or []),
        "url": row.url or "",
        "has_headers": bool(row.headers),
        "enabled": bool(row.enabled),
        "status": row.status or "disconnected",
        "tools": row.tools or [],
        "last_error": row.last_error,
        "connected": mcp_client.is_connected(row),
    }


def _validate(u: McpUpsert) -> None:
    if u.transport not in ("stdio", "http"):
        raise HTTPException(status_code=400, detail="transport must be 'stdio' or 'http'.")
    if u.transport == "stdio":
        if not u.command.strip():
            raise HTTPException(status_code=400, detail="Command is required for stdio servers.")
        if len(u.command) > 500:
            raise HTTPException(status_code=400, detail="Command is too long.")
    else:
        if not u.url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="URL must start with http:// or https://.")
        if len(u.headers) > _HEADERS_MAX:
            raise HTTPException(status_code=400, detail=f"At most {_HEADERS_MAX} headers.")
        for k, v in u.headers.items():
            if not isinstance(k, str) or len(k) > 128 or len(str(v)) > 4096:
                raise HTTPException(status_code=400, detail="Header names/values are too long.")


@router.get("/{user_id}")
def list_servers(user_id: str, db: Session = Depends(get_db)):
    rows = (db.query(models.MCPServer).filter_by(user_id=user_id)
            .order_by(models.MCPServer.created_at).all())
    for row in rows:
        if row.status == "connected" and not mcp_client.is_connected(row):
            row.status = "disconnected"  # process gone (restart, disconnect)
    try:
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    return [_out(r) for r in rows]


@router.post("/{user_id}")
async def create_or_update(user_id: str, u: McpUpsert, db: Session = Depends(get_db)):
    """Upsert by (user, name), then handshake + tools/list. Returns 200 with
    status/error fields either way so the UI can show the real failure."""
    _validate(u)
    row = (db.query(models.MCPServer)
           .filter_by(user_id=user_id, name=u.name.strip()).first())
    if row is None:
        row = models.MCPServer(user_id=user_id, name=u.name.strip())
        db.add(row)
    row.transport = u.transport
    row.command = u.command.strip() if u.transport == "stdio" else None
    row.args = list(u.args or []) if u.transport == "stdio" else []
    row.url = u.url.strip() if u.transport == "http" else None
    row.headers = dict(u.headers) if u.transport == "http" else {}
    row.status = "connecting"
    row.enabled = True
    db.commit()
    db.refresh(row)

    tools, err = await mcp_client.connect(row)
    try:
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    db.refresh(row)
    return _out(row)


@router.post("/{user_id}/{server_id}/reconnect")
async def reconnect(user_id: str, server_id: str, db: Session = Depends(get_db)):
    row = db.query(models.MCPServer).filter_by(id=server_id, user_id=user_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="MCP server not found.")
    row.status = "connecting"
    db.commit()
    tools, err = await mcp_client.connect(row)
    try:
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
    db.refresh(row)
    return _out(row)


@router.delete("/{user_id}/{server_id}")
async def delete_server(user_id: str, server_id: str, db: Session = Depends(get_db)):
    row = db.query(models.MCPServer).filter_by(id=server_id, user_id=user_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="MCP server not found.")
    await mcp_client.disconnect(row)
    db.delete(row)
    db.commit()
    return {"ok": True}
