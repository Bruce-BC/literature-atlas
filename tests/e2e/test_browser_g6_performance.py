"""Large, entirely synthetic G6 responsiveness and reuse smoke test."""
from __future__ import annotations
import copy
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]


def large_fixture():
    base = json.loads((ROOT / 'tests/fixtures/demo-snapshot.json').read_text())
    paper_template, concept_template, edge_template, membership_template = (base['papers'][0], base['concepts'][0], base['edges'][0], base['memberships'][0])
    concepts = []
    for i in range(1410):
        c = copy.deepcopy(concept_template); c.update(id=f'concept:demo-server:demo-epoch:c{i:04}', label={'en': f'Synthetic concept {i:04}'}); concepts.append(c)
    papers, memberships = [], []
    field = base['fields'][0]['id']
    for i in range(1000):
        p = copy.deepcopy(paper_template); p.update(id=f'zotero:demo-server:0:P{i:04}', key=f'P{i:04}', title=f'Synthetic paper {i:04}', attachmentIds=[], fieldIds=[field], displayFieldId=field, conceptIds=[concepts[0]['id']] + [concepts[1 + (i * 7 + j * 19) % (len(concepts)-1)]['id'] for j in range(4)]); papers.append(p)
        m = copy.deepcopy(membership_template); m.update(id=f'membership:demo-server:demo-epoch:m{i:04}', paperId=p['id'], fieldId=field); memberships.append(m)
    edges = []
    for i in range(4000):
        e = copy.deepcopy(edge_template); e.update(id=f'edge:demo-server:demo-epoch:e{i:05}', source=papers[i % 1000]['id'], target=papers[(i * 7 + 1) % 1000]['id']); edges.append(e)
    pairs = []
    for a in range(1410):
        for b in range(a + 1, 1410):
            pairs.append([a, b, 2])
            if len(pairs) >= 50000: break
        if len(pairs) >= 50000: break
    base.update(papers=papers, concepts=concepts, memberships=memberships, edges=edges, attachments=[])
    base['overview'].update(totalPapers=1000, totalEdges=len(edges), keywordAggregate={'conceptIds':[c['id'] for c in concepts], 'pairs':pairs, 'encoding':'concept-index-pairs'})
    return base


def wait_js(page, expression, seconds=20):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if page.evaluate(expression): return
        time.sleep(.05)
    raise AssertionError('Timed out: ' + expression + ' diagnostics=' + json.dumps(page.evaluate('() => ({paper:document.querySelector("#atlas-paper-network")?.dataset.rendererState, concept:document.querySelector("#atlas-concept-network")?.dataset.rendererState,status:document.querySelector("#atlas-status")?.textContent, counts:window.AtlasDebug?.getActualCounts?.()})')))


def test_large_synthetic_g6_reuses_graphs_on_selection_and_theme():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        pytest.fail(f'Playwright is required: {exc}')
    with tempfile.TemporaryDirectory(prefix='atlas-g6-large-') as temp:
        fixture = Path(temp) / 'fixture.json'; fixture.write_text(json.dumps(large_fixture()))
        process = subprocess.Popen([sys.executable, '-m', 'atlas', 'serve', '--demo', '--demo-fixture', str(fixture), '--embedding-provider', 'fake', '--port', '0', '--data-dir', temp, '--web-root', str(ROOT / 'dist/web'), '--poll-interval', '5', '--launch-id', 'g6-perf', '--parent-origin', 'app://obsidian.md'], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            bootstrap = json.loads(process.stdout.readline())
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={'width':1200, 'height':1000}); errors=[]
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{bootstrap['port']}/", wait_until='domcontentloaded')
                page.locator('#atlas-token-input').fill(bootstrap['token']); page.locator('#atlas-connect').click()
                wait_js(page, '() => Boolean(window.AtlasDebug?.getState()?.snapshot?.papers?.length)')
                start=time.monotonic(); page.locator('#atlas-renderer').select_option('g6')
                try:
                    wait_js(page, "() => ['#atlas-paper-network','#atlas-concept-network'].every(id => document.querySelector(id)?.dataset.rendererState === 'ready')")
                except AssertionError as exc:
                    raise AssertionError(str(exc) + ' pageErrors=' + json.dumps(errors)) from exc
                first_ready=round((time.monotonic()-start)*1000)
                counts=page.evaluate('() => window.AtlasDebug.getActualCounts()')
                assert counts['paperNodes'] > 0 and counts['conceptNodes'] < 400
                paper_button = page.locator('#atlas-paper-list button').first; paper_button.scroll_into_view_if_needed(); paper_button.click()
                wait_js(page, "() => ['#atlas-paper-network','#atlas-concept-network'].every(id => document.querySelector(id)?.dataset.rendererState === 'ready')")
                page.evaluate('''() => {window.__perfRefs=[AtlasDebug.getPaperGraph(),AtlasDebug.getConceptGraph()]; window.__perfRenderCalls=0; for (const graph of __perfRefs) { const original=graph.render.bind(graph); graph.render=(...args)=>{window.__perfRenderCalls++;return original(...args)}; }}''')
                start=time.monotonic(); paper_button=page.locator('#atlas-paper-list button[aria-pressed="true"]').first; paper_button.click(); selection_ms=round((time.monotonic()-start)*1000)
                same_selection=page.evaluate('() => __perfRefs[0] === AtlasDebug.getPaperGraph() && __perfRefs[1] === AtlasDebug.getConceptGraph()')
                start=time.monotonic();page.locator('#atlas-theme').select_option('dark'); theme_ms=round((time.monotonic()-start)*1000)
                same_theme=page.evaluate('() => __perfRefs[0] === AtlasDebug.getPaperGraph() && __perfRefs[1] === AtlasDebug.getConceptGraph()')
                time.sleep(.5)
                render_calls=page.evaluate('() => __perfRenderCalls')
                selected_counts=page.evaluate('() => AtlasDebug.getActualCounts()')
                page.locator('#atlas-concept-list button').filter(has_text='Synthetic concept 0000').first.click()
                wait_js(page, "() => ['#atlas-paper-network','#atlas-concept-network'].every(id => document.querySelector(id)?.dataset.rendererState === 'ready')")
                hub=page.evaluate('() => ({stats:AtlasDebug.getState().conceptStats,notice:document.querySelector("#atlas-sampling").textContent})')
                assert hub['stats']['neighborCandidates'] == 1409 and hub['stats']['visibleNodes'] <= 105
                assert '104/1409 neighboring concepts shown' in hub['notice']
                assert hub['stats']['inducedEdges'] > 600 and hub['stats']['visibleEdges'] == 600 and hub['stats']['hiddenEdges'] == hub['stats']['inducedEdges'] - 600
                assert hub['stats']['visibleEdges'] + hub['stats']['visibleMembershipEdges'] <= 1000
                assert 'keyword links 600/' in hub['notice']
                assert hub['stats']['directPaperCandidates'] == 1000 and hub['stats']['visibleConceptPapers'] <= 150
                assert '/1000 connected papers shown' in hub['notice']
                receipt={'papers':1000,'concepts':1410,'initial':counts,'selected':selected_counts,'firstReadyMs':first_ready,'sameSelectionMs':selection_ms,'themeEventMs':theme_ms,'sameSelectionInstances':same_selection,'sameThemeInstances':same_theme,'oldInstanceRenderCalls':render_calls,'hub':hub, 'pageErrors':errors}
                path=ROOT/'.omx/context/g6-performance-browser.json';path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(receipt,indent=2))
                assert same_selection and same_theme and render_calls == 0
                assert selected_counts['conceptNodes'] < 400
                assert not errors
                browser.close()
        finally:
            process.terminate(); process.wait(timeout=10)
