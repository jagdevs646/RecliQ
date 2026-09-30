import json

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_actor, get_session_id
from app.database.session import get_db
from app.models.template import ReconciliationTemplate, ReconciliationTemplateVersion
from app.schemas.job import ReconciliationJobOut
from app.schemas.template import TemplateConfig, TemplateCreate, TemplateRunRequest, TemplateUpdate
from app.services import template_service
from app.services.audit_service import AuditActor, record_event
from app.services.reconciliation_service import enqueue_generic_job
from app.utils.timestamps import iso_utc

router = APIRouter(prefix="/templates", tags=["templates"])


def _template_out(db: Session, template: ReconciliationTemplate, include_config: bool = False) -> dict:
    _, config = template_service.version_config(db, template)
    payload = {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "current_version": template.current_version,
        "archived": template.archived,
        "created_at": iso_utc(template.created_at),
        "updated_at": iso_utc(template.updated_at),
        "last_run_at": iso_utc(template.last_run_at),
        "summary": template_service.summarize(config),
    }
    if include_config:
        payload["config"] = config.model_dump()
        payload["versions"] = [
            {"version": row.version, "change_note": row.change_note, "created_at": iso_utc(row.created_at), "created_by": row.created_by}
            for row in db.query(ReconciliationTemplateVersion)
            .filter(ReconciliationTemplateVersion.template_id == template.id)
            .order_by(ReconciliationTemplateVersion.version.desc())
            .all()
        ]
    return payload


def _load(db: Session, session_id: str, template_id: str) -> ReconciliationTemplate:
    template = template_service.get_template(db, session_id, template_id)
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved reconciliation not found")
    return template


@router.get("")
def list_templates(db: Session = Depends(get_db), session_id: str = Depends(get_session_id)) -> dict:
    templates = (
        db.query(ReconciliationTemplate)
        .filter(ReconciliationTemplate.session_id == session_id, ReconciliationTemplate.archived.is_(False))
        .order_by(ReconciliationTemplate.updated_at.desc())
        .all()
    )
    return {"templates": [_template_out(db, template) for template in templates]}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_template(
    payload: TemplateCreate,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    actor: AuditActor = Depends(get_actor),
) -> dict:
    try:
        if payload.config is not None:
            config = payload.config
        elif payload.plan is not None:
            config = template_service.config_from_request(db, session_id, payload.plan, payload.report_settings)
        elif payload.job_id:
            config = template_service.config_from_job(db, session_id, payload.job_id)
        else:
            raise ValueError("Provide the reconciliation setup (plan) or a finished job to save.")
        if payload.report_settings:
            config.report_settings = payload.report_settings
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    template = template_service.create_template(
        db, session_id=session_id, actor_id=actor.actor_id, name=payload.name, description=payload.description,
        config=config, change_note=payload.change_note,
    )
    record_event(
        scope_id=session_id, actor=actor, action="template.created", entity_type="template", entity_id=template.id,
        summary=f"Saved reconciliation '{template.name}' created", after={"name": template.name, "version": 1, "config": config.model_dump()},
        metadata={"from_job": payload.job_id},
    )
    return _template_out(db, template, include_config=True)


@router.get("/{template_id}")
def template_detail(template_id: str, db: Session = Depends(get_db), session_id: str = Depends(get_session_id)) -> dict:
    return _template_out(db, _load(db, session_id, template_id), include_config=True)


@router.get("/{template_id}/versions/{version}")
def template_version(template_id: str, version: int, db: Session = Depends(get_db), session_id: str = Depends(get_session_id)) -> dict:
    template = _load(db, session_id, template_id)
    try:
        number, config = template_service.version_config(db, template, version)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {"version": number, "config": config.model_dump()}


@router.put("/{template_id}")
def update_template(
    template_id: str,
    payload: TemplateUpdate,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    actor: AuditActor = Depends(get_actor),
) -> dict:
    template = _load(db, session_id, template_id)
    before_version, before_config = template_service.version_config(db, template)
    before = {"name": template.name, "description": template.description, "version": before_version, "config": before_config.model_dump()}
    try:
        config: TemplateConfig | None = None
        if payload.config is not None:
            config = payload.config
        elif payload.plan is not None:
            config = template_service.config_from_request(db, session_id, payload.plan, before_config.report_settings)
            config.column_aliases = before_config.column_aliases
        if payload.report_settings is not None or payload.column_aliases is not None:
            config = (config or before_config).model_copy(deep=True)
            if payload.report_settings is not None:
                config.report_settings = payload.report_settings
            if payload.column_aliases is not None:
                config.column_aliases = payload.column_aliases
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if payload.name is not None:
        template.name = payload.name.strip()
    if payload.description is not None:
        template.description = payload.description
    if config is not None and config.model_dump() != before_config.model_dump():
        template_service.add_version(db, template, actor_id=actor.actor_id, config=config, change_note=payload.change_note)
    else:
        db.commit()
    db.refresh(template)
    _, after_config = template_service.version_config(db, template)
    record_event(
        scope_id=session_id, actor=actor, action="template.updated", entity_type="template", entity_id=template.id,
        summary=f"Saved reconciliation '{template.name}' updated (version {template.current_version})",
        before=before,
        after={"name": template.name, "description": template.description, "version": template.current_version, "config": after_config.model_dump()},
        metadata={"change_note": payload.change_note},
    )
    return _template_out(db, template, include_config=True)


@router.delete("/{template_id}")
def archive_template(
    template_id: str,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    actor: AuditActor = Depends(get_actor),
) -> dict:
    template = _load(db, session_id, template_id)
    template.archived = True
    db.commit()
    record_event(
        scope_id=session_id, actor=actor, action="template.archived", entity_type="template", entity_id=template.id,
        summary=f"Saved reconciliation '{template.name}' archived", before={"archived": False}, after={"archived": True},
    )
    return {"id": template.id, "archived": True}


def _resolve(db: Session, session_id: str, template: ReconciliationTemplate, payload: TemplateRunRequest):
    try:
        version, config = template_service.version_config(db, template, payload.version)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    plan, resolution = template_service.resolve_template(db, session_id, config, payload)
    return version, plan, resolution


@router.post("/{template_id}/resolve")
def resolve_template(
    template_id: str,
    payload: TemplateRunRequest,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
):
    """Preview how the template maps onto the chosen files (no job is created)."""
    template = _load(db, session_id, template_id)
    try:
        version, plan, resolution = _resolve(db, session_id, template, payload)
    except template_service.TemplateResolutionError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc), "resolution": exc.resolution})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"version": version, "resolution": resolution, "plan": json.loads(plan.model_dump_json())}


@router.post("/{template_id}/run", response_model=ReconciliationJobOut)
def run_template(
    template_id: str,
    payload: TemplateRunRequest,
    db: Session = Depends(get_db),
    session_id: str = Depends(get_session_id),
    actor: AuditActor = Depends(get_actor),
):
    template = _load(db, session_id, template_id)
    try:
        version, plan, resolution = _resolve(db, session_id, template, payload)
    except template_service.TemplateResolutionError as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc), "resolution": exc.resolution})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job = enqueue_generic_job(db, plan, session_id, actor, template=(template.id, version))
    template_service.mark_run(db, template)
    record_event(
        scope_id=session_id, actor=actor, action="template.run", entity_type="template", entity_id=template.id,
        summary=f"Ran saved reconciliation '{template.name}' (version {version})",
        metadata={"job_id": job.id, "version": version, "automatic_matches": resolution.get("automatic", [])},
    )
    return job
