"""Manage the authenticated account's private QA display snapshots."""
from typing import Any

from fastapi import Query, Request, Response
from pydantic import BaseModel, Field, StrictInt, StrictStr

from .audit_identity import audit_identity
from ..services.qa_conversations import QaConversations


class ConversationCreate(BaseModel):
    title: StrictStr | None = Field(default=None, max_length=80)


class ConversationSave(BaseModel):
    expected_version: StrictInt = Field(ge=1)
    messages: list[dict[str, Any]] = Field(max_length=1000)


class ConversationRename(BaseModel):
    expected_version: StrictInt = Field(ge=1)
    title: StrictStr = Field(min_length=1, max_length=80)


def install(app, service):
    conversations = QaConversations(service.data)
    base = '/api/projects/{p}/qa/conversations'

    @app.get(base)
    def list_qa_conversations(p: str, request: Request,
            limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0),
            q: str = Query(default='', max_length=500)):
        return conversations.list(audit_identity(request)['id'], p, limit=limit, offset=offset, q=q)

    @app.post(base, status_code=201)
    def create_qa_conversation(p: str, request: Request, body: ConversationCreate):
        return {'conversation': conversations.create(audit_identity(request)['id'], p, title=body.title)}

    @app.get(base + '/{conversation_id}')
    def get_qa_conversation(p: str, conversation_id: str, request: Request):
        return {'conversation': conversations.get(audit_identity(request)['id'], p, conversation_id)}

    @app.put(base + '/{conversation_id}')
    def save_qa_conversation(p: str, conversation_id: str, request: Request, body: ConversationSave):
        return {'conversation': conversations.save(audit_identity(request)['id'], p, conversation_id,
            expected_version=body.expected_version, messages=body.messages)}

    @app.patch(base + '/{conversation_id}')
    def rename_qa_conversation(p: str, conversation_id: str, request: Request, body: ConversationRename):
        return {'conversation': conversations.rename(audit_identity(request)['id'], p, conversation_id,
            expected_version=body.expected_version, title=body.title)}

    @app.delete(base + '/{conversation_id}', status_code=204)
    def delete_qa_conversation(p: str, conversation_id: str, request: Request,
                               expected_version: int = Query(ge=1)):
        conversations.delete(audit_identity(request)['id'], p, conversation_id,
                              expected_version=expected_version)
        return Response(status_code=204)
