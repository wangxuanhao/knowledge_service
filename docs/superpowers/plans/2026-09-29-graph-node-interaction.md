# Graph Node Interaction Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make graph node and edge clicks update only the detail rail, while explicit neighborhood expansion and top-scope full-graph rendering remain separate actions.

**Architecture:** Keep the existing single `window.drawGraph(node, hops)` renderer and `/subgraph` API. Remove implicit selection fallback from `drawGraph`, make entity selection and chart clicks detail-only, and require neighborhood callers to pass an entity ID explicitly.

**Tech Stack:** Vanilla JavaScript, ECharts, FastAPI, pytest, Playwright.

---

## File Structure

- Modify `tests/service/test_frontend_retrieval_flow.py`: add a disconnected fixture entity and real-browser interaction regressions.
- Modify `knowledge_service/web/workspace.js`: separate detail selection from graph-range requests.

### Task 1: Lock Down Detail-Only Selection and Full-Graph Restoration

**Files:**
- Modify: `tests/service/test_frontend_retrieval_flow.py:50-58`
- Modify: `tests/service/test_frontend_retrieval_flow.py:115-140`
- Modify: `tests/service/test_frontend_retrieval_flow.py:301-365`

- [ ] **Step 1: Add a disconnected entity to the browser fixture**

Add a node visible in the full graph but absent from `a`'s one-hop neighborhood:

```python
{'id': 'x', 'kind': 'entity', 'type': 'Thing', 'text': '无关实体', 'metadata': {}},
```

- [ ] **Step 2: Add an ECharts click helper**

```python
def _trigger_graph_click(page, data_type, row_id):
    page.evaluate("""([dataType, rowId]) => {
      const chart = window.echarts.getInstanceByDom(document.getElementById('graph-canvas'));
      chart.trigger('click', {dataType, data: {id: rowId}});
    }""", [data_type, row_id])
```

- [ ] **Step 3: Write the failing node-click regression**

```python
def test_graph_node_click_updates_only_details(workbench):
    page = workbench.page
    summary_before = page.locator('#graph-summary').inner_text()
    workbench.paths.clear()
    _trigger_graph_click(page, 'node', 'a')
    page.wait_for_function("document.querySelector('#graph-detail').textContent.includes('退款商户')")
    page.wait_for_timeout(120)
    assert not any(path.endswith('/subgraph') for path in workbench.paths)
    assert page.locator('#graph-summary').inner_text() == summary_before
    assert page.locator('#graph-entity-choice').input_value() == 'a'
```

- [ ] **Step 4: Add the relationship-edge preservation regression**

```python
def test_graph_edge_click_updates_only_details(workbench):
    page = workbench.page
    summary_before = page.locator('#graph-summary').inner_text()
    workbench.paths.clear()
    _trigger_graph_click(page, 'edge', 'r')
    page.wait_for_function("document.querySelector('#graph-detail').textContent.includes('退款关系')")
    page.wait_for_timeout(120)
    assert not any(path.endswith('/subgraph') for path in workbench.paths)
    assert page.locator('#graph-summary').inner_text() == summary_before
```

- [ ] **Step 5: Write the failing selector / expand / restore lifecycle regression**

```python
def test_entity_selection_requires_explicit_expand_and_full_graph_ignores_selection(workbench):
    page = workbench.page
    full_summary = page.locator('#graph-summary').inner_text()
    bodies = []
    page.on('request', lambda request: bodies.append(request.post_data_json)
            if request.url.endswith('/subgraph') else None)

    workbench.paths.clear()
    page.select_option('#graph-entity-choice', 'a')
    page.wait_for_function("document.querySelector('#graph-detail').textContent.includes('退款商户')")
    page.wait_for_timeout(120)
    assert not any(path.endswith('/subgraph') for path in workbench.paths)
    assert page.locator('#graph-summary').inner_text() == full_summary

    with page.expect_response(lambda response: response.url.endswith('/subgraph')):
        page.click('#graph-expand')
    assert bodies[-1]['node_id'] == 'a'
    assert bodies[-1]['attribute_mode'] == 'expanded'
    assert page.locator('#graph-summary').inner_text() != full_summary

    with page.expect_response(lambda response: response.url.endswith('/subgraph')):
        page.click('#draw-graph')
    assert bodies[-1]['node_id'] is None
    assert bodies[-1]['attribute_mode'] == 'summary'
    assert page.locator('#graph-summary').inner_text() == full_summary
    assert page.locator('#graph-entity-choice').input_value() == 'a'
    assert '退款商户' in page.locator('#graph-detail').inner_text()
```

- [ ] **Step 6: Run the regressions and verify RED**

Run:

```powershell
D:\workspace\knowledge_service\.venv\Scripts\python.exe -m pytest tests/service/test_frontend_retrieval_flow.py -q -k "graph_node_click or graph_edge_click or entity_selection_requires"
```

Expected: node click fails because it issues `/subgraph`; the lifecycle test fails because selection immediately narrows the graph and full-graph redraw sends `node_id='a'`. The edge test may pass because it preserves existing behavior.

- [ ] **Step 7: Commit the failing regressions**

```powershell
git add tests/service/test_frontend_retrieval_flow.py
git commit -m "test: cover graph detail-only selection"
```

### Task 2: Separate Detail Selection from Graph Range

**Files:**
- Modify: `knowledge_service/web/workspace.js:162`
- Modify: `knowledge_service/web/workspace.js:202-214`
- Modify: `knowledge_service/web/workspace.js:283-292`

- [ ] **Step 1: Make the entity selector detail-only**

```javascript
get('graph-entity-choice').onchange=()=>{
  clearTimeout(entityTimer);
  const id=get('graph-entity-choice').value;
  get('graph-node').value=id;
  if(!id)return;
  const row=ui.options.find(item=>item.id===id)||wb.nodes.get(id);
  if(row){inspect(row);status('已选择：'+row.text+'；点击“展开邻域”查看邻域');}
};
```

- [ ] **Step 2: Make chart node and edge clicks inspect only**

```javascript
chart.off('click');chart.on('click',p=>{
  const row=p.dataType==='edge'?result.edges.find(e=>e.id===p.data.id):wb.nodes.get(p.data.id);
  if(!row)return;
  if(GraphTypeFilter.isAttributeEdge(row)||GraphTypeFilter.isAttributeNode(row)){
    const entity=wb.nodes.get(row.subject_id);if(entity)inspect(entity);return;
  }
  inspect(row);
});
```

- [ ] **Step 3: Make the renderer argument authoritative**

Change ID resolution to:

```javascript
const id=node||null;
```

Keep explicit neighborhood callers (`graph-expand`, search-result graph actions, evidence graph actions) passing an entity ID.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```powershell
D:\workspace\knowledge_service\.venv\Scripts\python.exe -m pytest tests/service/test_frontend_retrieval_flow.py -q -k "graph_node_click or graph_edge_click or entity_selection_requires or draw_and_expand"
```

Expected: all selected tests pass; `#graph-expand` sends an entity ID and `#draw-graph` sends `node_id=null`.

- [ ] **Step 5: Run the complete frontend retrieval flow**

Run:

```powershell
D:\workspace\knowledge_service\.venv\Scripts\python.exe -m pytest tests/service/test_frontend_retrieval_flow.py -q
```

Expected: `18+` tests pass with no browser errors.

- [ ] **Step 6: Commit the minimal implementation**

```powershell
git add knowledge_service/web/workspace.js
git commit -m "fix: keep graph clicks detail-only"
```

### Task 3: Verify the Original User Flow

**Files:**
- Test: `tests/service/test_frontend_retrieval_flow.py`
- Verify: `knowledge_service/web/workspace.js`

- [ ] **Step 1: Run frontend contract checks**

Run:

```powershell
D:\workspace\knowledge_service\.venv\Scripts\python.exe -m pytest tests/service/test_workbench.py tests/service/test_frontend_retrieval_flow.py -q
```

Expected: all tests pass with no failures.

- [ ] **Step 2: Re-run the live port-8100 browser flow**

Record the full graph, select an entity, explicitly expand its neighborhood, redraw the full graph, then click a node. Expected on the current sample project: `3 → 3 → 2 → 3` entities, and the final node click changes only details without another `/subgraph` request.

- [ ] **Step 3: Inspect final diff and status**

Run:

```powershell
git diff master...HEAD -- knowledge_service/web/workspace.js tests/service/test_frontend_retrieval_flow.py
git status --short
```

Expected: only planned test and frontend files differ from the design baseline; no debug instrumentation or unrelated files are present.
