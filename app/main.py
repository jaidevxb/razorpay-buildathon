"""Reclaim dashboard — live view of the recovery batch.

Run:  uvicorn app.main:app --reload
Then open http://127.0.0.1:8000
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

PAGE_SHELL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reclaim — Revenue Recovery</title>
<style>
  * {{ box-sizing: border-box; margin: 0; }}
  body {{ background: #0b0f14; color: #e5e7eb; font: 15px/1.5 system-ui, sans-serif; padding: 2rem; }}
  a {{ color: #7dd3fc; text-decoration: none; }}
  h1 {{ font-size: 1.6rem; margin-bottom: .2rem; }}
  .sub {{ color: #9ca3af; margin-bottom: 1.6rem; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1rem; margin-bottom: 2rem; }}
  .card {{ background: #111827; border: 1px solid #1f2937; border-radius: 10px; padding: 1rem 1.2rem; }}
  .card .label {{ color: #9ca3af; font-size: .8rem; text-transform: uppercase; letter-spacing: .05em; }}
  .card .value {{ font-size: 1.5rem; font-weight: 700; margin-top: .2rem; }}
  table {{ width: 100%; border-collapse: collapse; background: #111827; border: 1px solid #1f2937; border-radius: 10px; overflow: hidden; }}
  th, td {{ text-align: left; padding: .55rem .8rem; border-bottom: 1px solid #1f2937; font-size: .88rem; }}
  th {{ color: #9ca3af; font-weight: 600; background: #0f172a; }}
  tr:hover td {{ background: #16202e; }}
  .pill {{ display: inline-block; padding: .1rem .55rem; border-radius: 999px; font-size: .75rem; font-weight: 600; }}
  .section {{ margin: 2rem 0 .8rem; font-size: 1.1rem; color: #cbd5e1; }}
  .timeline {{ list-style: none; }}
  .timeline li {{ background: #111827; border: 1px solid #1f2937; border-radius: 10px; padding: .8rem 1rem; margin-bottom: .7rem; }}
  .timeline .meta {{ color: #9ca3af; font-size: .78rem; margin-bottom: .3rem; }}
  pre {{ background: #0f172a; padding: .6rem .8rem; border-radius: 8px; overflow-x: auto; font-size: .8rem; color: #a5f3fc; }}
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
        at_risk = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(amount_paise),0) amt "
            "FROM payments WHERE status != 'captured' "
            "AND recovery_status NOT IN ('recovered','written_off')"
        ).fetchone()
        recovered = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(amount_paise),0) amt "
            "FROM payments WHERE recovery_status = 'recovered'").fetchone()
        escalated = conn.execute(
            "SELECT COUNT(*) n FROM payments "
            "WHERE recovery_status = 'escalated'").fetchone()

        rows = conn.execute(
            "SELECT p.*, d.root_cause, d.recommended_action "
            "FROM payments p LEFT JOIN diagnoses d ON d.payment_id = p.id "
            "WHERE p.status != 'captured' ORDER BY p.amount_paise DESC"
        ).fetchall()
    finally:
        conn.close()

    cards = f"""
    <div class="cards">
      <div class="card"><div class="label">Batch total</div>
        <div class="value">{rupees(total['amt'])}</div>
        <div class="sub">{total['n']} payments</div></div>
      <div class="card"><div class="label">Captured cleanly</div>
        <div class="value" style="color:#22c55e">{rupees(captured['amt'])}</div>
        <div class="sub">{captured['n']} payments</div></div>
      <div class="card"><div class="label">Still at risk</div>
        <div class="value" style="color:#ef4444">{rupees(at_risk['amt'])}</div>
        <div class="sub">{at_risk['n']} payments</div></div>
      <div class="card"><div class="label">Recovered</div>
        <div class="value" style="color:#38bdf8">{rupees(recovered['amt'])}</div>
        <div class="sub">{recovered['n']} payments · {escalated['n']} escalated</div></div>
    </div>"""

    body_rows = []
    for r in rows:
        action = r["recommended_action"] or "—"
        body_rows.append(f"""
        <tr>
          <td><a href="/payment/{r['id']}">{r['id']}</a></td>
          <td>{r['customer_name']}</td>
          <td>{rupees(r['amount_paise'])}</td>
          <td>{pill(r['status'], STATUS_COLORS.get(r['status'], '#6b7280'))}</td>
          <td>{r['failure_code'] or ''}</td>
          <td>{r['root_cause'] or '<i style="color:#6b7280">not diagnosed</i>'}</td>
          <td>{action}</td>
          <td>{pill(r['recovery_status'],
                    RECOVERY_COLORS.get(r['recovery_status'], '#6b7280'))}</td>
        </tr>""")

    body = f"""
    <h1>Reclaim <span style="color:#38bdf8">·</span> Revenue Recovery</h1>
    <div class="sub">AI agent recovering failed payments on Razorpay test mode
      — every action bounded, logged and explainable</div>
    {cards}
    <div class="section">At-risk payments ({len(rows)})</div>
    <table>
      <tr><th>Payment</th><th>Customer</th><th>Amount</th><th>Status</th>
          <th>Failure</th><th>Diagnosis (Gemini)</th><th>Action</th>
          <th>Recovery</th></tr>
      {''.join(body_rows)}
    </table>"""
    return PAGE_SHELL.format(body=body)


@app.get("/payment/{payment_id}", response_class=HTMLResponse)
def payment_detail(payment_id: str) -> str:
    conn = get_conn()
    try:
        p = conn.execute(
            "SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
        if p is None:
            return PAGE_SHELL.format(body="<h1>Payment not found</h1>")
        d = conn.execute(
            "SELECT * FROM diagnoses WHERE payment_id = ?",
            (payment_id,)).fetchone()
        events = conn.execute(
            "SELECT * FROM audit_log WHERE payment_id = ? ORDER BY id",
            (payment_id,)).fetchall()
    finally:
        conn.close()

    diag_html = ""
    if d is not None:
        diag_html = f"""
        <div class="section">Gemini diagnosis</div>
        <div class="card">
          <b>{d['root_cause']}</b> —
          {'transient, retry could succeed' if d['transient'] else 'not transient'}
          · recommended: <b>{d['recommended_action']}</b>
          <p style="margin-top:.6rem;color:#cbd5e1">
            Draft message: “{d['customer_message']}”</p>
        </div>"""

    items = []
    for e in events:
        detail = json.dumps(json.loads(e["detail"]), indent=2)
        items.append(f"""
        <li>
          <div class="meta">{e['created_at']} · actor:
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
    <div class="section">Audit trail — every action, with reasoning</div>
    <ul class="timeline">{''.join(items)}</ul>"""
    return PAGE_SHELL.format(body=body)
