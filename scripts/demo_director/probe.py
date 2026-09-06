"""Print accessible names of form controls on a route, for selector work.
    venv/bin/python probe.py /video combobox button
"""
import sys
from director import FRONTEND, Stage

path = sys.argv[1]
roles = sys.argv[2:] or ["combobox"]
st = Stage()
try:
    st.page.goto(FRONTEND + path, wait_until="load", timeout=60_000)
    st.page.wait_for_timeout(6000)
    for role in roles:
        locs = st.page.get_by_role(role).all()
        print(f"== {role}: {len(locs)}")
        for i, l in enumerate(locs[:60]):
            try:
                name = l.evaluate("""el => {
                    const lb = el.getAttribute('aria-labelledby');
                    const byId = lb ? lb.split(' ').map(i => (document.getElementById(i)||{}).textContent||'').join(' | ') : '';
                    return [el.tagName, el.getAttribute('aria-label')||'', byId, (el.textContent||'').trim().slice(0,60), el.id||''].join(' ~ ');
                }""")
            except Exception as e:
                name = f"<{e}>"
            print(f"  {i:2} {name}")
    labels = st.page.locator("label").all()
    print(f"== labels: {len(labels)}")
    for l in labels[:80]:
        try:
            print("  ", (l.text_content() or "").strip()[:60], "| for=", l.get_attribute("for"))
        except Exception:
            pass
finally:
    st.close()
