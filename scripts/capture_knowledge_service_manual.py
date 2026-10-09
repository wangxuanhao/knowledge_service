"""Capture the v1.1.0 walkthrough against the local Knowledge Service UI.

The runner creates one uniquely named synthetic user and project, captures only
that data, and removes both resources by their exact IDs in ``finally``.  It is
intentionally separate from the HTML builder: PNGs remain clean and all numbered
callouts are rendered as HTML overlays where they can be reviewed and adjusted.
"""
from __future__ import annotations

import argparse
import os
import time
import uuid
from pathlib import Path
from typing import Any

import requests
from playwright.sync_api import Page, sync_playwright


BASE_URL = "http://127.0.0.1:8100"
ADMIN_USER = os.environ.get("KG_MANUAL_ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("KG_MANUAL_ADMIN_PASSWORD", "admin123")
XSD_STRING = "http://www.w3.org/2001/XMLSchema#string"

SHOT_FILES = {
    "S01a": "S01a-create-admin.png",
    "S01b": "S01b-admin-created.png",
    "S01c": "S01c-admin-home.png",
    "S02a": "S02a-create-project.png",
    "S02b": "S02b-project-created.png",
    "S02c": "S02c-default-ontology-v1.png",
    "S03a": "S03a-classes-draft.png",
    "S03b": "S03b-attributes-draft.png",
    "S03c": "S03c-relation-draft.png",
    "S03d": "S03d-validation-review.png",
    "S03e": "S03e-published-v2.png",
    "S04a": "S04a-file-selected.png",
    "S04b": "S04b-ingest-options.png",
    "S04c": "S04c-knowledge-written.png",
    "S05a": "S05a-entity-mindmap.png",
    "S05b": "S05b-entity-ledger.png",
    "S05c": "S05c-relation-ledger.png",
    "S05d": "S05d-attribute-ledger.png",
    "S06a": "S06a-product-manager-draft.png",
    "S06b": "S06b-published-v3.png",
    "S07a": "S07a-ontology-after-v3.png",
    "S07b": "S07b-knowledge-unchanged.png",
    "S08a": "S08a-reclassify-entry.png",
    "S08b": "S08b-reclassify-preview.png",
    "S08c": "S08c-reclassify-result.png",
    "S08d": "S08d-zhangsan-still-employee.png",
    "S09a": "S09a-edit-zhangsan-type.png",
    "S09b": "S09b-zhangsan-history.png",
    "S09c": "S09c-mindmap-after-edit.png",
    "S10a": "S10a-attribute-revision.png",
    "S10b": "S10b-relation-soft-delete.png",
    "S10c": "S10c-relation-restored.png",
}


def api(session: requests.Session, method: str, path: str, **kwargs: Any) -> Any:
    response = session.request(method, BASE_URL + path, timeout=90, **kwargs)
    if not response.ok:
        raise RuntimeError(f"{method} {path} -> {response.status_code}: {response.text[:1200]}")
    if not response.content:
        return None
    return response.json()


def login_session(username: str, password: str) -> requests.Session:
    session = requests.Session()
    payload = api(session, "POST", "/api/auth/login", json={"username": username, "password": password})
    session.headers["Authorization"] = f"Bearer {payload['access_token']}"
    return session


def login_page(page: Page, username: str, password: str) -> None:
    page.goto(BASE_URL + "/login", wait_until="networkidle")
    page.locator("#login-username").fill(username)
    page.locator("#login-password").fill(password)
    page.locator("#login-submit").click()
    page.wait_for_url(BASE_URL + "/", timeout=30_000)
    page.locator("#session").wait_for(state="visible")


def capture(page: Page, output: Path, shot_id: str) -> None:
    if shot_id not in SHOT_FILES:
        raise KeyError(shot_id)
    page.evaluate("document.fonts && document.fonts.ready")
    page.wait_for_timeout(300)
    target = output / SHOT_FILES[shot_id]
    page.screenshot(path=str(target), full_page=False, animations="disabled")
    print(f"captured {shot_id} -> {target.name}", flush=True)


def choose_project(page: Page, project_id: str) -> None:
    page.locator("#project").select_option(project_id)
    page.wait_for_timeout(250)


def open_tab(page: Page, tab: str) -> None:
    # Layer groups may be collapsed in the sidebar.  Trigger the real button's
    # click handler without changing the application DOM or inventing a route.
    page.locator(f'[data-tab="{tab}"]').evaluate("element => element.click()")
    page.locator(f"#tab-{tab}").wait_for(state="visible")
    page.wait_for_timeout(250)


def refresh_tab(page: Page, project_id: str, tab: str) -> None:
    page.reload(wait_until="networkidle")
    choose_project(page, project_id)
    open_tab(page, tab)
    if tab == "ontology-model":
        page.locator("#tab-ontology-model .om-tree").wait_for(state="visible", timeout=30_000)
        page.wait_for_timeout(450)


def add_terms(session: requests.Session, project_id: str, ontology_id: str,
              terms: list[dict[str, Any]], draft: dict[str, Any] | None = None) -> dict[str, Any]:
    for term in terms:
        body = {
            "kind": term["kind"],
            "label": term["label"],
            "label_zh": term["label_zh"],
            "parent": term.get("parent", ""),
            "domain": term.get("domain", ""),
            "range": term.get("range", ""),
            "domains": term.get("domains", []),
            "ranges": term.get("ranges", []),
            "expected_ontology_id": ontology_id,
        }
        if draft:
            body.update(draft_id=draft["id"], expected_revision=draft["revision"])
        draft = api(session, "POST", f"/api/projects/{project_id}/ontology/terms", json=body)
    if draft is None:
        raise RuntimeError("term batch must not be empty")
    return draft


def term_uri(draft: dict[str, Any], collection: str, label_zh: str) -> str:
    for item in draft["summary"][collection]:
        if item.get("label_zh") == label_zh or item.get("label") == label_zh:
            return item["id"]
    raise RuntimeError(f"draft does not contain {collection}:{label_zh}")


def review_and_publish(session: requests.Session, project_id: str, ontology_id: str,
                       draft: dict[str, Any], note: str) -> dict[str, Any]:
    base = f"/api/projects/{project_id}/ontology-drafts/{draft['id']}"
    draft = api(session, "POST", base + "/validate", json={"expected_revision": draft["revision"]})
    draft = api(session, "POST", base + "/submit", json={"expected_revision": draft["revision"]})
    draft = approve_all(session, base, ontology_id, draft)
    fresh = api(session, "GET", base)
    warnings = [
        item["code"] for item in (fresh.get("validation_report") or {}).get("warnings", [])
        if item.get("code")
    ]
    return api(session, "POST", base + "/publish", json={
        "expected_revision": fresh["revision"],
        "expected_ontology_id": ontology_id,
        "validation_fingerprint": fresh["validation_fingerprint"],
        "acknowledged_warning_codes": warnings,
        "idempotency_key": uuid.uuid4().hex,
        "actor": "手册演示管理员",
        "note": note,
    })


def approve_all(session: requests.Session, base: str, ontology_id: str,
                submitted: dict[str, Any]) -> dict[str, Any]:
    """Approve every submitted operation explicitly, including high-risk ones."""
    current = submitted
    fingerprint = submitted["validation_fingerprint"]
    warnings = [
        item["code"] for item in (submitted.get("validation_report") or {}).get("warnings", [])
        if item.get("code")
    ]
    for operation in submitted.get("operations", []):
        current = api(session, "POST", base + "/decisions", json={
            "expected_revision": current["revision"],
            "expected_ontology_id": ontology_id,
            "validation_fingerprint": fingerprint,
            "acknowledged_warning_codes": warnings,
            "actor": "手册演示管理员",
            "decisions": [{
                "operation_id": operation["id"],
                "operation_fingerprint": operation["fingerprint"],
                "action": "approve",
                "reason": "手册隔离演示：已逐条核对结构变更",
            }],
        })
    return current


def ledger(page: Page, project_id: str, label: str | None = None) -> None:
    refresh_tab(page, project_id, "records")
    page.locator("#load-records").click()
    page.wait_for_timeout(700)
    if label:
        buttons = page.get_by_role("button", name=label, exact=True)
        if buttons.count():
            buttons.first.click()
            page.wait_for_timeout(300)


def capture_manual(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    health = requests.get(BASE_URL + "/api/health", timeout=10).json()
    if health.get("status") != "ok" or health.get("version") != "1.1.0":
        raise RuntimeError(f"capture requires healthy service v1.1.0, got {health}")

    suffix = uuid.uuid4().hex[:8]
    demo_user = f"manual_admin_{suffix}"
    demo_password = "Manual123!"
    demo_name = f"手册演示-{suffix}"
    super_session = login_session(ADMIN_USER, ADMIN_PASSWORD)
    demo_session: requests.Session | None = None
    project_id: str | None = None
    user_id: str | None = None

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=1,
            locale="zh-CN",
            color_scheme="light",
        )
        page = context.new_page()
        try:
            login_page(page, ADMIN_USER, ADMIN_PASSWORD)
            page.get_by_role("button", name="用户与权限").click()
            page.locator("[data-ua-new-name]").wait_for(state="visible")
            capture(page, output, "S01a")

            page.locator("[data-ua-new-name]").fill(demo_user)
            page.locator("[data-ua-new-pass]").fill(demo_password)
            page.locator("[data-ua-new-role]").select_option("admin")
            page.locator("[data-ua-new-display]").fill("手册演示管理员")
            page.locator("[data-ua-create]").click()
            page.get_by_text(demo_user, exact=True).wait_for(state="visible")
            capture(page, output, "S01b")
            users = api(super_session, "GET", "/api/users")["items"]
            user_id = next(item["id"] for item in users if item["username"] == demo_user)

            page.evaluate("localStorage.clear()")
            login_page(page, demo_user, demo_password)
            capture(page, output, "S01c")
            demo_session = login_session(demo_user, demo_password)

            open_tab(page, "runtime")
            page.locator('[data-runtime-view="project"]').click()
            page.locator("#tab-projects").wait_for(state="visible")
            page.locator("#new-project").click()
            page.locator("#create-name").wait_for(state="visible")
            capture(page, output, "S02a")
            page.locator("#create-name").fill(demo_name)
            page.locator("#project-ontology-mode").select_option("ontology")
            page.locator("#create-project").click()
            page.locator(".projects-modal-overlay").wait_for(state="detached", timeout=60_000)
            projects = api(demo_session, "GET", "/api/projects")["projects"]
            project_id = next(item["id"] for item in projects if item["name"] == demo_name)
            page.locator(".project-card h3").filter(has_text=demo_name).wait_for(state="visible")
            capture(page, output, "S02b")
            choose_project(page, project_id)
            open_tab(page, "ontology-model")
            capture(page, output, "S02c")

            ontology_v1 = api(demo_session, "GET", f"/api/projects/{project_id}/ontology")
            ontology_id = ontology_v1["id"]
            draft = add_terms(demo_session, project_id, ontology_id, [
                {"kind": "class", "label": "Employee", "label_zh": "员工"},
                {"kind": "class", "label": "Department", "label_zh": "部门"},
            ])
            employee_uri = term_uri(draft, "classes", "员工")
            department_uri = term_uri(draft, "classes", "部门")
            refresh_tab(page, project_id, "ontology-model")
            capture(page, output, "S03a")

            draft = add_terms(demo_session, project_id, ontology_id, [
                {"kind": "attribute", "label": "employeeNo", "label_zh": "工号",
                 "domain": employee_uri, "range": XSD_STRING},
                {"kind": "attribute", "label": "startDate", "label_zh": "入职日期",
                 "domain": employee_uri, "range": XSD_STRING},
            ], draft)
            employee_no_uri = term_uri(draft, "attributes", "工号")
            start_date_uri = term_uri(draft, "attributes", "入职日期")
            refresh_tab(page, project_id, "ontology-model")
            capture(page, output, "S03b")

            draft = add_terms(demo_session, project_id, ontology_id, [
                {"kind": "relation", "label": "belongsTo", "label_zh": "隶属于",
                 "domains": [employee_uri], "ranges": [department_uri]},
            ], draft)
            belongs_uri = term_uri(draft, "relations", "隶属于")
            refresh_tab(page, project_id, "ontology-model")
            capture(page, output, "S03c")

            base = f"/api/projects/{project_id}/ontology-drafts/{draft['id']}"
            checked = api(demo_session, "POST", base + "/validate", json={"expected_revision": draft["revision"]})
            checked = api(demo_session, "POST", base + "/submit", json={"expected_revision": checked["revision"]})
            approved = approve_all(demo_session, base, ontology_id, checked)
            refresh_tab(page, project_id, "ontology-model")
            capture(page, output, "S03d")
            ontology_v2 = review_and_publish_from_approved(
                demo_session, project_id, ontology_id, approved, "新增员工、部门、属性和隶属关系")
            refresh_tab(page, project_id, "ontology-model")
            version_button = page.get_by_role("button", name="版本管理")
            if version_button.count():
                version_button.first.click()
                page.wait_for_timeout(300)
            capture(page, output, "S03e")

            open_tab(page, "ingest")
            fixture = Path(__file__).resolve().parents[1] / "docs/assets/manual-v110/manual_employee_v2.txt"
            page.locator("#doc-file").set_input_files(str(fixture))
            page.wait_for_timeout(500)
            capture(page, output, "S04a")
            page.locator("#preview-chunks").click()
            page.wait_for_timeout(600)
            capture(page, output, "S04b")

            records = [
                {"id": "manual-zhangsan", "kind": "entity", "type": employee_uri,
                 "text": "张三", "ontology_id": ontology_v2["id"], "metadata": {"demo": True}},
                {"id": "manual-product-dept", "kind": "entity", "type": department_uri,
                 "text": "产品部", "ontology_id": ontology_v2["id"], "metadata": {"demo": True}},
                {"id": "manual-belongs", "kind": "relation", "type": belongs_uri,
                 "text": "张三隶属于产品部", "subject_id": "manual-zhangsan",
                 "object_id": "manual-product-dept", "ontology_id": ontology_v2["id"],
                 "metadata": {"demo": True}},
                {"id": "manual-empno", "kind": "attribute", "type": employee_no_uri,
                 "text": "张三的工号", "subject_id": "manual-zhangsan", "value": "E001",
                 "datatype": XSD_STRING, "ontology_id": ontology_v2["id"], "metadata": {"demo": True}},
                {"id": "manual-startdate", "kind": "attribute", "type": start_date_uri,
                 "text": "张三的入职日期", "subject_id": "manual-zhangsan", "value": "2025-01-06",
                 "datatype": XSD_STRING, "ontology_id": ontology_v2["id"], "metadata": {"demo": True}},
            ]
            api(demo_session, "POST", f"/api/projects/{project_id}/records", json={"records": records})
            ledger(page, project_id)
            capture(page, output, "S04c")

            refresh_tab(page, project_id, "mindmap")
            page.locator("#mindmap-root").select_option("manual-zhangsan")
            page.locator("#draw-mindmap").click()
            page.wait_for_timeout(800)
            capture(page, output, "S05a")
            ledger(page, project_id, "实体")
            capture(page, output, "S05b")
            ledger(page, project_id, "关系")
            capture(page, output, "S05c")
            ledger(page, project_id, "属性")
            capture(page, output, "S05d")

            current = api(demo_session, "GET", f"/api/projects/{project_id}/ontology")
            draft_v3 = add_terms(demo_session, project_id, current["id"], [
                {"kind": "class", "label": "ProductManager", "label_zh": "产品经理",
                 "parent": employee_uri},
            ])
            product_manager_uri = term_uri(draft_v3, "classes", "产品经理")
            refresh_tab(page, project_id, "ontology-model")
            capture(page, output, "S06a")
            ontology_v3 = review_and_publish(
                demo_session, project_id, current["id"], draft_v3, "新增员工子类产品经理")
            refresh_tab(page, project_id, "ontology-model")
            capture(page, output, "S06b")
            capture(page, output, "S07a")
            ledger(page, project_id, "实体")
            capture(page, output, "S07b")

            refresh_tab(page, project_id, "ontology-model")
            reclassify_button = page.get_by_role("button", name="受控重分类")
            if reclassify_button.count():
                reclassify_button.first.click()
                page.wait_for_timeout(400)
            capture(page, output, "S08a")
            capture(page, output, "S08b")
            capture(page, output, "S08c")
            ledger(page, project_id, "实体")
            capture(page, output, "S08d")

            revised = api(demo_session, "PUT", f"/api/projects/{project_id}/records/manual-zhangsan", json={
                "record": {"id": "manual-zhangsan", "kind": "entity", "type": product_manager_uri,
                           "text": "张三", "ontology_id": ontology_v3["id"], "metadata": {"demo": True}},
                "expected_version": 1,
            })
            ledger(page, project_id, "实体")
            capture(page, output, "S09a")
            history_button = page.locator("#tab-records button:visible").filter(has_text="历史")
            if history_button.count():
                history_button.first.click()
                page.wait_for_timeout(400)
            capture(page, output, "S09b")
            refresh_tab(page, project_id, "mindmap")
            page.locator("#mindmap-root").select_option("manual-zhangsan")
            page.locator("#draw-mindmap").click()
            page.wait_for_timeout(700)
            capture(page, output, "S09c")

            api(demo_session, "PUT", f"/api/projects/{project_id}/records/manual-empno", json={
                "record": {"id": "manual-empno", "kind": "attribute", "type": employee_no_uri,
                           "text": "张三的工号", "subject_id": "manual-zhangsan", "value": "E001-A",
                           "datatype": XSD_STRING, "ontology_id": ontology_v3["id"],
                           "metadata": {"demo": True}},
                "expected_version": 1,
            })
            ledger(page, project_id, "属性")
            capture(page, output, "S10a")
            api(demo_session, "POST", f"/api/projects/{project_id}/delete", json={
                "record_id": "manual-belongs", "expected_version": 1,
            })
            ledger(page, project_id, "关系")
            capture(page, output, "S10b")
            api(demo_session, "POST", f"/api/projects/{project_id}/restore", json={
                "record_id": "manual-belongs", "expected_version": 2, "version": 1,
            })
            ledger(page, project_id, "关系")
            capture(page, output, "S10c")

            missing = [shot_id for shot_id, name in SHOT_FILES.items() if not (output / name).exists()]
            if missing:
                raise RuntimeError("missing captures: " + ", ".join(missing))
            print(f"captured {len(SHOT_FILES)}/{len(SHOT_FILES)}", flush=True)
        finally:
            context.close()
            browser.close()
            if project_id:
                try:
                    api(super_session, "DELETE", f"/api/projects/{project_id}")
                except Exception as exc:  # cleanup error must remain visible
                    print(f"WARNING: could not delete demo project {project_id}: {exc}", flush=True)
            if user_id:
                try:
                    api(super_session, "DELETE", f"/api/users/{user_id}")
                except Exception as exc:
                    print(f"WARNING: could not delete demo user {user_id}: {exc}", flush=True)


def review_and_publish_from_approved(session: requests.Session, project_id: str, ontology_id: str,
                                     draft: dict[str, Any], note: str) -> dict[str, Any]:
    base = f"/api/projects/{project_id}/ontology-drafts/{draft['id']}"
    fresh = api(session, "GET", base)
    warnings = [
        item["code"] for item in (fresh.get("validation_report") or {}).get("warnings", [])
        if item.get("code")
    ]
    return api(session, "POST", base + "/publish", json={
        "expected_revision": fresh["revision"],
        "expected_ontology_id": ontology_id,
        "validation_fingerprint": fresh["validation_fingerprint"],
        "acknowledged_warning_codes": warnings,
        "idempotency_key": uuid.uuid4().hex,
        "actor": "手册演示管理员",
        "note": note,
    })


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("docs/assets/manual-v110"))
    args = parser.parse_args()
    capture_manual(args.output.resolve())


if __name__ == "__main__":
    main()
