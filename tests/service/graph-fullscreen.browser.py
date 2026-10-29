"""Standalone real-browser graph fullscreen contract; no database or models."""
import runpy
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
ui=runpy.run_path(str(ROOT/'tests/service/test_workspace_redesign_ui.py'))
fixture=ui['redesign_page'].__wrapped__()
try:
    page,errors=next(fixture)
    page.click('[data-tab="search"]')
    button=page.get_by_role('button',name='全屏查看图谱',exact=True)
    assert button.count()==1,'both graph modes need an actual fullscreen control'
    original=page.locator('#graph-canvas').bounding_box()
    button.click()
    page.wait_for_function('()=>document.getElementById("graph-stage").classList.contains("is-graph-fullscreen")')
    expanded=page.locator('#graph-canvas').bounding_box()
    assert expanded['height']>original['height']+100,(original,expanded)
    assert page.get_by_role('button',name='退出图谱全屏',exact=True).is_visible()
    page.click('[data-fg-mode="full"]')
    assert page.locator('#graph-stage').evaluate('(el)=>el.classList.contains("is-graph-fullscreen")')
    assert abs(page.locator('.full-graph-host').bounding_box()['height']-expanded['height'])<=1
    page.evaluate('()=>{document.getElementById("graph-detail").innerHTML="<h3>选中实体详情</h3>";}')
    for width in [1440,1200,768,390]:
        page.set_viewport_size({'width':width,'height':950})
        page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
        rail=page.locator('.inspector-rail').bounding_box()
        assert rail and rail['x']>=0 and rail['x']+rail['width']<=page.viewport_size['width']+1,(width,rail)
        assert rail['y']>=0 and rail['y']+rail['height']<=page.viewport_size['height']+1,(width,rail)
    page.set_viewport_size({'width':1440,'height':950})
    page.get_by_role('button',name='退出图谱全屏',exact=True).click()
    page.wait_for_function('()=>!document.getElementById("graph-stage").classList.contains("is-graph-fullscreen")')
    page.click('[data-fg-mode="local"]')
    assert abs(page.locator('#graph-canvas').bounding_box()['height']-original['height'])<=1
    # Exercise the CSS expansion fallback, used when browser fullscreen is denied.
    page.evaluate('()=>{document.querySelector(".search-graph-workspace").requestFullscreen=()=>Promise.reject(new Error("unsupported"));}')
    page.get_by_role('button',name='全屏查看图谱',exact=True).click()
    page.wait_for_function('()=>document.getElementById("graph-stage").classList.contains("is-graph-fullscreen")')
    page.keyboard.press('Escape')
    page.wait_for_function('()=>!document.getElementById("graph-stage").classList.contains("is-graph-fullscreen")')
    assert not errors,errors
    print('PASS graph fullscreen: native/fallback, shared modes, visible inspector, exit/escape, restore dimensions',flush=True)
finally:
    fixture.close()
