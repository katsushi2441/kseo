from __future__ import annotations

from pydantic import BaseModel, Field


class SiteCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=4, max_length=500)


class SiteSummary(BaseModel):
    id: str
    name: str
    url: str
    created_at: str
    latest_score: int | None = None
    latest_audit_id: str | None = None


class AuditSummary(BaseModel):
    id: str
    status: str
    overall: int
    page_count: int
    counts: dict = {}
    created_at: str
    finished_at: str | None = None
    error: str = ""


class Finding(BaseModel):
    rule: str
    category: str
    severity: str
    url: str
    message: str
    evidence: str
    action: str


class AuditDetail(BaseModel):
    id: str
    site_id: str
    status: str
    overall: int
    page_count: int
    categories: dict = {}
    counts: dict = {}
    pages: list = []
    findings: list[Finding] = []
    error: str = ""
    created_at: str
    finished_at: str | None = None
    advice: str | None = None
    advice_provider: str | None = None


class AuditCreate(BaseModel):
    # 無料枠は少ないページ数で回す。上限はサーバー側でも必ず抑える。
    max_pages: int | None = Field(default=None, ge=1, le=200)


class AdviceResult(BaseModel):
    audit_id: str
    provider: str
    model: str
    body: str
    created_at: str


class UsageStatus(BaseModel):
    owner: str
    plan: str
    audits_used: int
    audits_limit: int | None
    advice_used: int
    advice_limit: int | None
    sites: int
    sites_limit: int
