"""Reclaim dashboard — live view of the recovery batch.

Run:  uvicorn app.main:app --reload --port 8100
Then open http://127.0.0.1:8100
"""

import json

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from app.db import get_conn, init_db

app = FastAPI(title="Reclaim")

init_db()

STATUS_COLORS = {
    "captured": "#22c55e",
    "failed": "#ef4444",
    "abandoned": "#f59e0b",
}
RECOVERY_COLORS = {
    "none": "#6b7280",
    "detected": "#f59e0b",
    "in_progress": "#3b82f6",
    "recovered": "#22c55e",
    "escalated": "#a855f7",
    "written_off": "#6b7280",
}
ACTION_STATE_COLORS = {
    "planned": "#6b7280",
    "executing": "#3b82f6",
    "succeeded": "#22c55e",
    "failed": "#ef4444",
}

PAGE_SHELL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}
<title>Reclaim — Revenue Recovery</title>
<style>
  * {{ box-sizing: border-box; margin: 0; }}
  body {{ background: #0b0f14; color: #e5e7eb;
         font: 15px/1.5 system-ui, sans-serif; padding: 1.6rem 2rem 3rem; }}
  a {{ color: #7dd3fc; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  h1 {{ font-size: 1.5rem; }}
  .topbar {{ display: flex; align-items: baseline; gap: .8rem;
             flex-wrap: wrap; }}
  .live {{ font-size: .72rem; font-weight: 700; color: #22c55e;
           border: 1px solid #22c55e55; border-radius: 999px;
           padding: .1rem .6rem; letter-spacing: .08em; }}
  .sub {{ color: #9ca3af; margin: .2rem 0 1.4rem; }}
  .cards {{ display: grid;
            grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
            gap: 1rem; margin-bottom: 1.4rem; }}
  .card {{ background: #111827; border: 1px solid #1f2937;
           border-radius: 12px; padding: 1rem 1.2rem; }}
  .card .label {{ color: #9ca3af; font-size: .78rem;
                  text-transform: uppercase; letter-spacing: .06em; }}
  .card .value {{ font-size: 1.55rem; font-weight: 700; margin-top: .15rem; }}
  .card .note {{ color: #9ca3af; font-size: .82rem; }}
  .progress-wrap {{ background: #111827; border: 1px solid #1f2937;
                    border-radius: 12px; padding: 1rem 1.2rem;
                    margin-bottom: 1.6rem; }}
  .progress {{ display: flex; height: 22px; border-radius: 8px;
               overflow: hidden; margin-top: .6rem; background: #1f2937; }}
  .progress div {{ height: 100%; transition: width .6s ease; }}
  .legend {{ display: flex; gap: 1.2rem; flex-wrap: wrap; margin-top: .6rem;
             font-size: .82rem; color: #cbd5e1; }}
  .dot {{ display: inline-block; width: 9px; height: 9px;
          border-radius: 50%; margin-right: .35rem; }}
  .grid2 {{ display: grid; grid-template-columns: 2fr 1fr; gap: 1.2rem;
            align-items: start; }}
  @media (max-width: 1100px) {{ .grid2 {{ grid-template-columns: 1fr; }} }}
  .panel {{ background: #111827; border: 1px solid #1f2937;
            border-radius: 12px; overflow: hidden; }}
  .panel h2 {{ font-size: .95rem; color: #cbd5e1; padding: .8rem 1rem;
               background: #0f172a; border-bottom: 1px solid #1f2937; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ text-align: left; padding: .5rem .8rem;
            border-bottom: 1px solid #1f2937; font-size: .85rem; }}
  th {{ color: #9ca3af; font-weight: 600; font-size: .76rem;
        text-transform: uppercase; letter-spacing: .04em; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:hover td {{ background: #16202e; }}
  .pill {{ display: inline-block; padding: .08rem .55rem;
           border-radius: 999px; font-size: .73rem; font-weight: 600;
           white-space: nowrap; }}
  .bar-row {{ padding: .55rem 1rem; font-size: .82rem; }}
  .bar-label {{ display: flex; justify-content: space-between;
                color: #cbd5e1; margin-bottom: .25rem; }}
  .bar {{ height: 8px; background: #1f2937; border-radius: 6px;
          overflow: hidden; }}
  .bar div {{ height: 100%; border-radius: 6px; }}
  .feed {{ max-height: 420px; overflow-y: auto; }}
  .feed-item {{ padding: .55rem 1rem; border-bottom: 1px solid #1f2937;
                font-size: .82rem; }}
  .feed-item .meta {{ color: #6b7280; font-size: .72rem; }}
  .timeline {{ list-style: none; }}
  .timeline li {{ background: #111827; border: 1px solid #1f2937;
                  border-radius: 10px; padding: .8rem 1rem;
                  margin-bottom: .7rem; }}
  .timeline .meta {{ color: #9ca3af; font-size: .78rem;
                     margin-bottom: .3rem; }}
  pre {{ background: #0f172a; padding: .6rem .8rem; border-radius: 8px;
         overflow-x: auto; font-size: .8rem; color: #a5f3fc; }}
</style>
</head>
<body>
{body}
</body>
</html>"""


def pill(text: str, color: str) -> str:
    return (f'<span class="pill" style="background:{color}22;'
            f'color:{color}">{text}</span>')


def rupees(paise: int) -> str:
    return f"₹{paise / 100:,.0f}"


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    conn = get_conn()
    try:
        total = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(amount_paise),0) amt "
            "FROM payments").fetchone()
        captured = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(amount_paise),0) amt "
            "FROM payments WHERE status = 'captured'").fetchone()
        recovered = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(amount_paise),0) amt "
            "FROM payments WHERE recovery_status = 'recovered'").fetchone()
        escalated = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(amount_paise),0) amt "
            "FROM payments WHERE recovery_status = 'escalated'").fetchone()
        pending = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(amount_paise),0) amt "
            "FROM payments WHERE status != 'captured' AND "
            "recovery_status IN ('detected','in_progress','none')").fetchone()

        at_risk_total = (recovered["amt"] + escalated["amt"] + pending["amt"])

        breakdown = conn.execute(
            "SELECT failure_code, COUNT(*) n, "
            "  SUM(CASE WHEN recovery_status='recovered' THEN 1 ELSE 0 END) rec "
            "FROM payments WHERE failure_code IS NOT NULL "
            "GROUP BY failure_code ORDER BY n DESC").fetchall()

        rows = conn.execute(
            "SELECT p.*, d.root_cause, d.recommended_action, "
            "  (SELECT COUNT(*) FROM recovery_actions ra "
            "   WHERE ra.payment_id = p.id) attempts "
            "FROM payments p LEFT JOIN diagnoses d ON d.payment_id = p.id "
            "WHERE p.status != 'captured' "
            "ORDER BY CASE p.recovery_status "
            "  WHEN 'in_progress' THEN 0 WHEN 'detected' THEN 1 "
            "  WHEN 'escalated' THEN 2 WHEN 'recovered' THEN 3 ELSE 4 END, "
            "p.amount_paise DESC").fetchall()

        feed = conn.execute(
            "SELECT ra.*, p.amount_paise FROM recovery_actions ra "
            "JOIN payments p ON p.id = ra.payment_id "
            "ORDER BY ra.id DESC LIMIT 30").fetchall()
    finally:
        conn.close()

    pct = (lambda amt: 100 * amt / at_risk_total if at_risk_total else 0)
    recovery_rate = (100 * recovered["amt"] / at_risk_total
                     if at_risk_total else 0)

    cards = f"""
    <div class="cards">
      <div class="card"><div class="label">Batch total</div>
        <div class="value">{rupees(total['amt'])}</div>
        <div class="note">{total['n']} payments</div></div>
      <div class="card"><div class="label">Captured cleanly</div>
        <div class="value" style="color:#22c55e">{rupees(captured['amt'])}</div>
        <div class="note">{captured['n']} payments</div></div>
      <div class="card"><div class="label">Recovered by agent</div>
        <div class="value" style="color:#38bdf8">{rupees(recovered['amt'])}</div>
        <div class="note">{recovered['n']} payments ·
          {recovery_rate:.0f}% of at-risk value</div></div>
      <div class="card"><div class="label">Still pending</div>
        <div class="value" style="color:#f59e0b">{rupees(pending['amt'])}</div>
        <div class="note">{pending['n']} payments in pipeline</div></div>
      <div class="card"><div class="label">Escalated to human</div>
        <div class="value" style="color:#a855f7">{rupees(escalated['amt'])}</div>
        <div class="note">{escalated['n']} payments</div></div>
    </div>"""

    progress = f"""
    <div class="progress-wrap">
      <div class="bar-label"><b>At-risk revenue: {rupees(at_risk_total)}</b>
        <span style="color:#9ca3af">how the agent is resolving it</span></div>
      <div class="progress">
        <div style="width:{pct(recovered['amt']):.1f}%;background:#22c55e"></div>
        <div style="width:{pct(pending['amt']):.1f}%;background:#f59e0b"></div>
        <div style="width:{pct(escalated['amt']):.1f}%;background:#a855f7"></div>
      </div>
      <div class="legend">
        <span><span class="dot" style="background:#22c55e"></span>
          recovered {rupees(recovered['amt'])}</span>
        <span><span class="dot" style="background:#f59e0b"></span>
          pending {rupees(pending['amt'])}</span>
        <span><span class="dot" style="background:#a855f7"></span>
          escalated {rupees(escalated['amt'])}</span>
      </div>
    </div>"""

    bar_rows = []
    for b in breakdown:
        frac = 100 * b["rec"] / b["n"] if b["n"] else 0
        bar_rows.append(f"""
        <div class="bar-row">
          <div class="bar-label"><span>{b['failure_code']}</span>
            <span>{b['rec']}/{b['n']} recovered</span></div>
          <div class="bar"><div style="width:{frac:.0f}%;
            background:#38bdf8"></div></div>
        </div>""")

    feed_items = []
    for f in feed:
        color = ACTION_STATE_COLORS.get(f["state"], "#6b7280")
        link = (f' · <a href="{f["rzp_link_url"]}" target="_blank">link</a>'
                if f["rzp_link_url"] else "")
        feed_items.append(f"""
        <div class="feed-item">
          <a href="/payment/{f['payment_id']}">{f['payment_id']}</a>
          · {f['action']} · attempt {f['attempt']}
          {pill(f['state'], color)}{link}
          <div class="meta">{f['created_at'][:19]} ·
            {rupees(f['amount_paise'])}</div>
        </div>""")

    body_rows = []
    for r in rows:
        action = r["recommended_action"] or "—"
        body_rows.append(f"""
        <tr>
          <td><a href="/payment/{r['id']}">{r['id']}</a></td>
          <td>{r['customer_name']}</td>
          <td>{rupees(r['amount_paise'])}</td>
          <td>{r['failure_code'] or ''}</td>
          <td>{r['root_cause'] or
               '<i style="color:#6b7280">not diagnosed</i>'}</td>
          <td>{action}</td>
          <td style="text-align:center">{r['attempts'] or ''}</td>
          <td>{pill(r['recovery_status'],
                    RECOVERY_COLORS.get(r['recovery_status'], '#6b7280'))}</td>
        </tr>""")

    body = f"""
    <div class="topbar">
      <h1>Reclaim <span style="color:#38bdf8">·</span> Revenue Recovery</h1>
      <span class="live">● LIVE</span>
    </div>
    <div class="sub">AI agent recovering failed payments on Razorpay test mode
      — every action bounded, logged and explainable</div>
    {cards}
    {progress}
    <div class="grid2">
      <div class="panel">
        <h2>At-risk payments ({len(rows)})</h2>
        <div style="overflow-x:auto">
        <table>
          <tr><th>Payment</th><th>Customer</th><th>Amount</th><th>Failure</th>
              <th>Diagnosis (Gemini)</th><th>Action</th><th>Att.</th>
              <th>Recovery</th></tr>
          {''.join(body_rows)}
        </table>
        </div>
      </div>
      <div>
        <div class="panel" style="margin-bottom:1.2rem">
          <h2>Recovery rate by failure class</h2>
          {''.join(bar_rows)}
        </div>
        <div class="panel">
          <h2>Action feed (latest 30)</h2>
          <div class="feed">{''.join(feed_items) or
            '<div class="feed-item">no actions yet</div>'}</div>
        </div>
      </div>
    </div>"""
    refresh = '<meta http-equiv="refresh" content="4">'
    return PAGE_SHELL.format(body=body, refresh=refresh)


@app.get("/payment/{payment_id}", response_class=HTMLResponse)
def payment_detail(payment_id: str) -> str:
    conn = get_conn()
    try:
        p = conn.execute(
            "SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
        if p is None:
            return PAGE_SHELL.format(body="<h1>Payment not found</h1>",
                                     refresh="")
        d = conn.execute(
            "SELECT * FROM diagnoses WHERE payment_id = ?",
            (payment_id,)).fetchone()
        actions = conn.execute(
            "SELECT * FROM recovery_actions WHERE payment_id = ? ORDER BY id",
            (payment_id,)).fetchall()
        events = conn.execute(
            "SELECT * FROM audit_log WHERE payment_id = ? ORDER BY id",
            (payment_id,)).fetchall()
    finally:
        conn.close()

    diag_html = ""
    if d is not None:
        diag_html = f"""
        <h2 style="font-size:1rem;color:#cbd5e1;margin:1.4rem 0 .6rem">
          Gemini diagnosis</h2>
        <div class="card">
          <b>{d['root_cause']}</b> —
          {'transient, retry could succeed' if d['transient']
           else 'not transient'}
          · recommended: <b>{d['recommended_action']}</b>
          <p style="margin-top:.6rem;color:#cbd5e1">
            Draft message: “{d['customer_message']}”</p>
        </div>"""

    action_rows = []
    for a in actions:
        color = ACTION_STATE_COLORS.get(a["state"], "#6b7280")
        link = (f'<a href="{a["rzp_link_url"]}" target="_blank">'
                f'{a["rzp_link_id"]}</a>' if a["rzp_link_url"] else "—")
        action_rows.append(f"""
        <tr><td>{a['attempt']}</td><td>{a['action']}</td>
            <td>{pill(a['state'], color)}</td><td>{link}</td>
            <td>{(a['executed_at'] or '')[:19]}</td></tr>""")
    actions_html = ""
    if action_rows:
        actions_html = f"""
        <h2 style="font-size:1rem;color:#cbd5e1;margin:1.4rem 0 .6rem">
          Recovery attempts</h2>
        <div class="panel"><table>
          <tr><th>#</th><th>Action</th><th>State</th>
              <th>Razorpay link</th><th>Executed</th></tr>
          {''.join(action_rows)}
        </table></div>"""

    items = []
    for e in events:
        detail = json.dumps(json.loads(e["detail"]), indent=2)
        items.append(f"""
        <li>
          <div class="meta">{e['created_at'][:19]} · actor:
            <b>{e['actor']}</b></div>
          <b>{e['action']}</b>
          <pre>{detail}</pre>
        </li>""")

    body = f"""
    <a href="/">&larr; back to batch</a>
    <h1 style="margin-top:.6rem">{p['id']}</h1>
    <div class="sub">{p['customer_name']} · {rupees(p['amount_paise'])} ·
      order {p['rzp_order_id'] or '(offline)'} ·
      {pill(p['status'], STATUS_COLORS.get(p['status'], '#6b7280'))}
      {pill(p['recovery_status'],
            RECOVERY_COLORS.get(p['recovery_status'], '#6b7280'))}</div>
    {diag_html}
    {actions_html}
    <h2 style="font-size:1rem;color:#cbd5e1;margin:1.4rem 0 .6rem">
      Audit trail — every action, with reasoning</h2>
    <ul class="timeline">{''.join(items)}</ul>"""
    return PAGE_SHELL.format(body=body, refresh="")
