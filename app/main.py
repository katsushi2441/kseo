"""Kurage SEO の内部API。

公開側のX認証と課金は heteml の kseo.php が持ち、ここは内部トークンだけを
見る。kgeoと同じ構えにしているのは、認証を2か所に置くと片方だけ直して
穴が開くため。このプロセスは 127.0.0.1 にだけ bind する。
"""

from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import advice_service, audit_service, config, db, report
from .crawler import CrawlError
from .models import (
    AdviceResult,
    AuditCreate,
    AuditDetail,
    AuditSummary,
    SiteCreate,
    SiteSummary,
    UsageStatus,
)

logger = logging.getLogger("kseo")


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    yield


app = FastAPI(title="Kurage SEO", version="1.0.0", lifespan=lifespan)
if config.STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


def normalize(value: str) -> str:
    return (value or "").strip().lstrip("@").lower()


def is_admin(username: str) -> bool:
    return normalize(username) in config.ADMIN_USERS


def authenticated_owner(
    x_kseo_token: str = Header(default=""),
    x_kseo_user: str = Header(default=""),
    x_kseo_act_as: str = Header(default=""),
) -> str:
    """操作対象のオーナー。管理者だけ X-KSeo-Act-As で代理操作できる。

    INTERNAL_TOKEN が空の設定で起動したときに素通しになると、ローカル開発の
    つもりの設定を本番に持ち込んだ瞬間に全公開になる。空なら開発用ユーザー
    に固定し、値があるときは必ず一致を要求する。
    """
    if not config.INTERNAL_TOKEN:
        return normalize(x_kseo_user) or config.DEV_USER
    if not x_kseo_token or not hmac.compare_digest(x_kseo_token, config.INTERNAL_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid internal token")
    owner = normalize(x_kseo_user)
    if not owner:
        raise HTTPException(status_code=400, detail="X-KSeo-User is required")
    act_as = normalize(x_kseo_act_as)
    if act_as and act_as != owner:
        if not is_admin(owner):
            raise HTTPException(status_code=403, detail="Not allowed to act as another user")
        logger.info("admin %s acting as %s", owner, act_as)
        return act_as
    return owner


def usage_status(owner: str) -> UsageStatus:
    admin = is_admin(owner)
    return UsageStatus(
        owner=owner,
        plan="admin" if admin else "free",
        audits_used=db.usage_this_month(owner, "audit"),
        audits_limit=None if admin else config.FREE_AUDITS_PER_MONTH,
        advice_used=db.usage_this_month(owner, "advice"),
        advice_limit=None if admin else config.FREE_ADVICE_PER_MONTH,
        sites=db.count_sites(owner),
        sites_limit=config.MAX_SITES_PER_USER,
    )


def enforce_limit(owner: str, kind: str) -> None:
    if is_admin(owner):
        return
    limit = (
        config.FREE_AUDITS_PER_MONTH if kind == "audit" else config.FREE_ADVICE_PER_MONTH
    )
    if db.usage_this_month(owner, kind) >= limit:
        label = "監査" if kind == "audit" else "改善案の生成"
        raise HTTPException(
            status_code=429,
            detail=f"今月の無料枠({limit}回)を使い切りました。{label}は来月または有料プランでご利用ください。",
        )


def require_site(owner: str, site_id: str) -> dict:
    site = db.get_site(site_id)
    if not site or site["owner"] != owner:
        raise HTTPException(status_code=404, detail="サイトが見つかりません")
    return site


def require_audit(owner: str, audit_id: str) -> dict:
    audit = db.get_audit(audit_id)
    if not audit or audit["owner"] != owner:
        raise HTTPException(status_code=404, detail="監査結果が見つかりません")
    return audit


@app.get("/", response_class=FileResponse)
def index() -> FileResponse:
    return FileResponse(config.STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    """外形監視用。入力不備を5xxで返さないのと同様、ここは常に200で状態を返す。"""
    return {
        "ok": True,
        "service": "kseo",
        "port": config.PORT,
        "llm_free": advice_service.configured("anonymous", paid=False),
        "llm_paid": advice_service.configured("anonymous", paid=True),
    }


@app.get("/api/usage", response_model=UsageStatus)
def get_usage(owner: str = Depends(authenticated_owner)) -> UsageStatus:
    return usage_status(owner)


@app.get("/api/sites", response_model=list[SiteSummary])
def sites(owner: str = Depends(authenticated_owner)) -> list[SiteSummary]:
    result = []
    for site in db.list_sites(owner):
        audits = db.list_audits(site["id"])
        done = [item for item in audits if item["status"] == "done"]
        result.append(
            SiteSummary(
                id=site["id"],
                name=site["name"],
                url=site["url"],
                created_at=site["created_at"],
                latest_score=done[0]["overall"] if done else None,
                latest_audit_id=done[0]["id"] if done else None,
            )
        )
    return result


@app.post("/api/sites", response_model=SiteSummary)
def new_site(payload: SiteCreate, owner: str = Depends(authenticated_owner)) -> SiteSummary:
    if db.count_sites(owner) >= config.MAX_SITES_PER_USER:
        raise HTTPException(
            status_code=400,
            detail=f"登録できるサイトは{config.MAX_SITES_PER_USER}件までです。",
        )
    # URLの妥当性はここで確かめる。監査時まで遅らせると、登録できたのに
    # 一度も動かないサイトが一覧に残る。
    try:
        from .crawler import validate_public_url

        url = validate_public_url(payload.url)
    except CrawlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    site = db.create_site(owner, payload.name.strip(), url)
    return SiteSummary(**{k: site[k] for k in ("id", "name", "url", "created_at")})


@app.delete("/api/sites/{site_id}")
def remove_site(site_id: str, owner: str = Depends(authenticated_owner)) -> dict:
    require_site(owner, site_id)
    db.delete_site(site_id)
    return {"ok": True}


@app.get("/api/sites/{site_id}/audits", response_model=list[AuditSummary])
def audits(site_id: str, owner: str = Depends(authenticated_owner)) -> list[AuditSummary]:
    require_site(owner, site_id)
    return [AuditSummary(**item) for item in db.list_audits(site_id)]


@app.post("/api/sites/{site_id}/audits", response_model=AuditDetail)
async def new_audit(
    site_id: str,
    payload: AuditCreate | None = None,
    owner: str = Depends(authenticated_owner),
) -> AuditDetail:
    site = require_site(owner, site_id)
    enforce_limit(owner, "audit")

    ceiling = config.PAID_PAGES_PER_AUDIT if is_admin(owner) else config.FREE_PAGES_PER_AUDIT
    requested = (payload.max_pages if payload and payload.max_pages else ceiling)
    max_pages = min(requested, ceiling)

    audit_id = db.start_audit(site_id, owner)
    try:
        result = await audit_service.run_audit(site["url"], max_pages)
    except CrawlError as exc:
        # 入力起因の失敗は4xxで返す。502で包むと外形監視が停止と誤判定する。
        db.fail_audit(audit_id, str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("audit failed: %s", site["url"])
        db.fail_audit(audit_id, str(exc))
        raise HTTPException(status_code=502, detail="監査中にエラーが発生しました") from exc

    db.finish_audit(
        audit_id,
        score=result["score"],
        pages=result["pages"],
        findings=result["findings"],
    )
    db.record_usage(owner, "audit")
    return audit(audit_id, owner)


@app.get("/api/audits/{audit_id}", response_model=AuditDetail)
def audit(audit_id: str, owner: str = Depends(authenticated_owner)) -> AuditDetail:
    row = require_audit(owner, audit_id)
    advice = db.latest_advice(audit_id)
    return AuditDetail(
        id=row["id"],
        site_id=row["site_id"],
        status=row["status"],
        overall=row["overall"],
        page_count=row["page_count"],
        categories=row["categories"],
        counts=row["counts"],
        pages=row["pages"],
        findings=row["findings"],
        error=row["error"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
        advice=advice["body"] if advice else None,
        advice_provider=advice["provider"] if advice else None,
    )


@app.post("/api/audits/{audit_id}/advice", response_model=AdviceResult)
async def build_advice(audit_id: str, owner: str = Depends(authenticated_owner)) -> AdviceResult:
    row = require_audit(owner, audit_id)
    if row["status"] != "done":
        raise HTTPException(status_code=400, detail="完了した監査に対してのみ生成できます。")
    enforce_limit(owner, "advice")

    paid = not is_admin(owner)
    if not advice_service.configured(owner, paid=paid):
        raise HTTPException(status_code=503, detail="改善案の生成が設定されていません。")

    site = db.get_site(row["site_id"]) or {"name": "サイト", "url": ""}
    # LLMには実測値だけを渡す。findings は dataclass ではなく dict で
    # 戻ってくるので、プロンプト側が期待する形に合わせる。
    findings = [_FindingView(**item) for item in row["findings"]]
    result = {
        "score": {
            "overall": row["overall"],
            "categories": row["categories"],
            "pages": row["page_count"],
        },
        "findings": findings,
        "pages": row["pages"],
    }
    try:
        body, provider, model = await advice_service.generate(
            owner, site["name"], site["url"], result, paid=paid
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("advice failed for %s", audit_id)
        raise HTTPException(status_code=502, detail=f"改善案を生成できませんでした: {exc}") from exc

    saved = db.save_advice(audit_id, owner, provider, model, body)
    db.record_usage(owner, "advice")
    return AdviceResult(
        audit_id=audit_id,
        provider=saved["provider"],
        model=saved["model"],
        body=saved["body"],
        created_at=saved["created_at"],
    )


@app.get("/api/audits/{audit_id}/report.md", response_class=PlainTextResponse)
def report_markdown(audit_id: str, owner: str = Depends(authenticated_owner)) -> PlainTextResponse:
    row = require_audit(owner, audit_id)
    site = db.get_site(row["site_id"]) or {}
    advice = db.latest_advice(audit_id)
    text = report.build_markdown(site, row, advice["body"] if advice else None)
    return PlainTextResponse(
        text,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="kseo-{audit_id}.md"'},
    )


class _FindingView:
    """advice_service が期待する属性アクセスを dict に与えるだけの薄い器。"""

    def __init__(self, **kwargs):
        self.rule = kwargs.get("rule", "")
        self.category = kwargs.get("category", "")
        self.severity = kwargs.get("severity", "")
        self.url = kwargs.get("url", "")
        self.message = kwargs.get("message", "")
        self.evidence = kwargs.get("evidence", "")
        self.action = kwargs.get("action", "")
