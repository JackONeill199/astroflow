"""Video-Overlay und Vollbild-Zeitreihe mit einem synthetischen H.264-Video prüfen."""
import io
import re
from pathlib import Path

import pytest
from PIL import Image

from test_ui_layout import FIND_OVERFLOW


@pytest.mark.parametrize('width', [375, 1440])
def test_playback_overlay_and_full_frame_timeline(server, browser, width, tmp_path):
    base,_=server
    page=browser.new_page(viewport={'width':width,'height':1000},reduced_motion='reduce')
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    video=(Path(__file__).parent/'fixtures/skytrace-timeline.mp4').read_bytes()
    out=io.BytesIO();Image.new('RGB',(160,120),(25,35,60)).save(out,format='PNG');png=out.getvalue()
    frames=[dict(index=n,url='/synthetic/crop.png',width=40,height=30,hit=n==3,excluded=n==2,
                 timestamp=1600000000+n*10,timestamp_text=f'Bild {n} UTC',overlay={'points':[[2,3],[20,18]]} if n==3 else None)
            for n in (1,2,3)]
    for i in (0,1):frames[i]['full_image']=dict(url='/synthetic/full.png',width=160,height=120)
    frames[0]['video_seconds']=0;frames[2]['video_seconds']=.5
    cand=dict(id='K0001',key='obs/K0001',session='Test',kind='trail',timestamp=1600000030,exposure_seconds=10,
              frames=frames,initial_frame=2,hit_indices=[2],crop=[30,40,70,70],catalogs=[],plausibility={})
    timeline=dict(width=160,height=120,frames=[dict(seconds=0,trails=[]),dict(seconds=.5,trails=[dict(id='K0001',points=[[32,43],[50,58]])])])
    session=dict(session='Test',frames=3,excluded=1,hits=1,fps=8,video_url='/synthetic/video.mp4',clean_url='/synthetic/video.mp4',
                 preview_url='/synthetic/full.png',clean_preview_url='/synthetic/full.png',overlay_timeline=timeline)
    run=dict(id='synthetic',sessions=[session],candidates=[cand],report_url=None,modified=1)
    page.route('**/api/skytrace/run?id=synthetic*',lambda route:route.fulfill(json=run))
    def video_response(route):
        match=re.match(r'bytes=(\d+)-(\d*)',route.request.headers.get('range',''))
        if match:
            start=int(match[1]);end=min(len(video)-1,int(match[2]) if match[2] else len(video)-1)
            route.fulfill(status=206,body=video[start:end+1],content_type='video/mp4',headers={'accept-ranges':'bytes','content-range':f'bytes {start}-{end}/{len(video)}'})
        else:route.fulfill(body=video,content_type='video/mp4',headers={'accept-ranges':'bytes'})
    page.route('**/synthetic/video.mp4',video_response)
    page.route('**/synthetic/*.png',lambda route:route.fulfill(body=png,content_type='image/png'))
    page.goto(base+'/#/skytrace/synthetic/vorschau')
    page.wait_for_selector('#vm')
    page.locator('#ovl').check()
    page.locator('#vm [data-m=video]').click()
    page.wait_for_function("document.querySelector('#skyVideo')?.readyState >= 2")
    page.locator('#skyVideo').scroll_into_view_if_needed()
    # Die freie Overlay-Fläche darf den nativen Play-Knopf nicht verdecken.
    hit=page.locator('#skyVideo').evaluate("""v => {const b=v.getBoundingClientRect();return document.elementFromPoint(b.left+18,b.bottom-18)?.id;}""")
    assert hit=='skyVideo'
    # Native Controls haben je Browser andere Knopfpositionen; Klick-Ziel separat prüfen, dann Wiedergabe starten.
    page.locator('#skyVideo').evaluate('v => { v.loop=true; return v.play(); }')
    page.wait_for_function("!document.querySelector('#skyVideo').paused && document.querySelector('#skyVideo').currentTime > .1")
    page.locator('label:has(#ovl)').click()
    page.locator('label:has(#ovl)').click()
    page.wait_for_function("!document.querySelector('#skyVideo').paused")
    page.locator('#skyHits button').click()
    try:
        page.wait_for_function("Math.abs(document.querySelector('#skyVideo').currentTime-.5)<.05 && document.querySelector('#skyVideo').paused",timeout=5000)
    except Exception:
        raise AssertionError((errors,page.locator('#skyVideo').evaluate('v=>({time:v.currentTime,duration:v.duration,paused:v.paused,ready:v.readyState,error:v.error?.message,source:v.currentSrc})')))
    page.goto(base+'/#/skytrace/synthetic/kandidaten')
    page.wait_for_selector('.cstage video:not([hidden])')   # altes Nachbarbild: ganzes Bild aus dem gespeicherten Video
    page.wait_for_function("Math.abs(document.querySelector('.cstage video').currentTime-.5)<.05")
    assert page.locator('.cstage > svg > polyline').get_attribute('points')=='32,43 50,58'
    page.locator('.frame-options [data-view=crop]').click()
    assert page.locator('.cstage img').get_attribute('src')=='/synthetic/crop.png'
    page.locator('.frame-options [data-view=full]').click()
    page.locator('.tl-th').nth(0).click()
    assert page.locator('.cstage img').get_attribute('src')=='/synthetic/full.png'
    page.locator('.tl-th').nth(1).click()
    assert page.locator('.frame-options button',has_text='Im Video öffnen').is_disabled()
    page.locator('.frame-options button',has_text='Zum Treffer').click()
    page.screenshot(path=str(tmp_path/f'skytrace-zeitreihe-{width}.png'),full_page=True)
    page.locator('.frame-options button',has_text='Im Video öffnen').click()
    page.wait_for_function("document.querySelector('#skyVideo')?.readyState>=2 && Math.abs(document.querySelector('#skyVideo').currentTime-.5)<.05")
    assert not page.evaluate(FIND_OVERFLOW)
    posted=[];needs_source={'value':False}
    def reanalyse(route):
        body=route.request.post_data_json;posted.append(body)
        route.fulfill(json=dict(missing=int(needs_source['value']),total=3,observations=1) if body.get('check') else dict(run='synthetic-neu',running=True))
    page.route('**/api/skytrace/reanalyse',reanalyse)
    assert not page.locator('#refresh').is_visible()
    page.locator('#analysisMenu > button').click()
    assert page.locator('#refresh').is_visible()
    page.locator('#reanalyse small').wait_for(state='visible',timeout=3000)
    assert not page.evaluate(FIND_OVERFLOW)
    page.screenshot(path=str(tmp_path/f'skytrace-analyse-menu-{width}.png'),full_page=True)
    page.locator('#reanalyse').click()
    page.wait_for_url('**/#/skytrace/synthetic-neu/vorschau')
    assert posted==[dict(run='synthetic',check=True),dict(run='synthetic')]
    needs_source['value']=True
    page.route('**/api/folders?path=*',lambda route:route.fulfill(json=dict(path='/synthetic/originale',parent='/synthetic',drives=['/'],folders=[],fits=3,pictures=0)))
    page.locator('#analysisMenu > button').click()
    page.locator('#reanalyse').click()
    page.wait_for_selector('#dlg[open]')
    assert '1 von 3 Originalaufnahmen fehlen' in page.locator('#dlg').inner_text()
    assert posted[-1]==dict(run='synthetic-neu',check=True)    # noch kein unvollständiger Lauf gestartet
    with page.expect_response('**/api/skytrace/reanalyse'):
        page.locator('#fok').click()
    assert posted[-1]==dict(run='synthetic-neu',source='/synthetic/originale')
    page.close()
    assert not errors,errors
