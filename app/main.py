"""Reclaim dashboard — operational view of the recovery batch.

Run:  uvicorn app.main:app --reload --port 8100
Then open http://127.0.0.1:8100

Design notes: light, restrained fintech styling. Status colors are validated
for colorblind separation (see CHALLENGES.md); identity is never carried by
color alone — every colored element also has a text label.
"""

import json

from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from app.baseline import compare
from app.db import get_conn, init_db, log_action

app = FastAPI(title="Reclaim")

init_db()

# Merchant-facing language. Technical identifiers stay visible in tooltips
# and on the audit/detail pages; the main screen answers a merchant's real
# questions: how much did I lose, what did you get back, what needs me?
FAILURE_LABEL = {
    "INSUFFICIENT_FUNDS": "Not enough balance",
    "EXPIRED_CARD": "Card expired",
    "GATEWAY_ERROR": "Bank was down",
    "UPI_TIMEOUT": "UPI request expired",
    "CHECKOUT_ABANDONED": "Left at checkout",
    "FRAUD_SUSPECTED": "Suspicious — blocked",
}
STATE_LABEL = {
    "captured": "paid",
    "failed": "failed",
    "abandoned": "left checkout",
    "detected": "found",
    "in_progress": "working on it",
    "promised": "promised to pay",
    "recovered": "money in",
    "recovered_manual": "collected by you",
    "escalated": "needs you",
    "written_off": "let go",
    "planned": "queued",
    "executing": "in flight",
    "succeeded": "worked",
}
ACTION_LABEL = {
    "retry": "retried the payment",
    "payment_link": "sent a payment link",
    "update_card": "asked for a new card",
}

# status -> (background tint, text color)
BADGE = {
    "captured": ("#e7f4ec", "#1e7f4f"),
    "recovered": ("#e7f4ec", "#1e7f4f"),
    "recovered_manual": ("#e0f2f1", "#00695c"),
    "failed": ("#fdecea", "#b42318"),
    "abandoned": ("#fdf3e0", "#9a6700"),
    "detected": ("#fdf3e0", "#9a6700"),
    "in_progress": ("#e8effc", "#2456d6"),
    "promised": ("#e8effc", "#2456d6"),
    "escalated": ("#efeaf9", "#6d5bb8"),
    "written_off": ("#eef0f2", "#5b6774"),
    "planned": ("#eef0f2", "#5b6774"),
    "executing": ("#e8effc", "#2456d6"),
    "succeeded": ("#e7f4ec", "#1e7f4f"),
}

# stacked-bar segment fills (validated: 2f9e68 / c8871d / 7a6fb5)
SEG_RECOVERED = "#2f9e68"
SEG_PENDING = "#c8871d"
SEG_ESCALATED = "#7a6fb5"

PAGE_SHELL = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh}
<title>Reclaim — Revenue Recovery</title>
<style>
  * {{ box-sizing: border-box; margin: 0; }}
  html {{ background: #f6f7f9; }}
  body {{ color: #17202b; font: 14px/1.5 "Segoe UI", system-ui, -apple-system,
          sans-serif; max-width: 1400px; margin: 0 auto;
          padding: 0 28px 56px; }}
  a {{ color: #2456d6; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  header {{ display: flex; align-items: center; justify-content: space-between;
            padding: 18px 0 14px; border-bottom: 1px solid #e3e6ea;
            margin-bottom: 22px; }}
  .brand {{ font-size: 18px; font-weight: 650; letter-spacing: -.01em; }}
  .brand small {{ color: #5b6774; font-weight: 400; font-size: 13px;
                  margin-left: 10px; }}
  .env {{ font-size: 11px; font-weight: 600; color: #9a6700;
          background: #fdf3e0; border: 1px solid #f0dcae;
          padding: 2px 9px; border-radius: 4px; letter-spacing: .04em; }}
  .kpis {{ display: grid;
           grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
           gap: 12px; margin-bottom: 14px; }}
  .kpi {{ background: #fff; border: 1px solid #e3e6ea; border-radius: 8px;
          padding: 14px 16px; transition: transform .18s ease,
          box-shadow .18s ease; }}
  .kpi:hover {{ transform: translateY(-2px);
                box-shadow: 0 6px 18px rgba(23,32,43,.08); }}
  .kpi.hero {{ background: #f2faf5; border-color: #cbe7d6; }}
  .kpi .icon {{ float: right; color: #8b95a1; margin-top: 2px; }}
  .kpi.hero .icon {{ color: #2f9e68; }}
  @keyframes pop {{ 0% {{ transform: scale(1); }}
    35% {{ transform: scale(1.07); color: #1e7f4f; }}
    100% {{ transform: scale(1); }} }}
  .value.flash {{ animation: pop .8s ease; }}
  .pulse {{ display: inline-flex; align-items: center; gap: 6px;
            font-size: 12px; color: #5b6774; }}
  .pulse i {{ width: 8px; height: 8px; border-radius: 50%;
              background: #2f9e68; animation: beat 2s ease infinite; }}
  @keyframes beat {{ 0%, 100% {{ box-shadow: 0 0 0 0 rgba(47,158,104,.4); }}
    50% {{ box-shadow: 0 0 0 5px rgba(47,158,104,0); }} }}
  .kpi .label {{ color: #5b6774; font-size: 11px; font-weight: 600;
                 text-transform: uppercase; letter-spacing: .05em; }}
  .kpi .value {{ font-size: 24px; font-weight: 650; margin-top: 2px;
                 font-variant-numeric: tabular-nums; letter-spacing: -.01em; }}
  .kpi .note {{ color: #5b6774; font-size: 12px; margin-top: 1px; }}
  .resbar {{ background: #fff; border: 1px solid #e3e6ea; border-radius: 8px;
             padding: 14px 16px; margin-bottom: 20px; }}
  .resbar .head {{ display: flex; justify-content: space-between;
                   font-size: 13px; margin-bottom: 9px; }}
  .stack {{ display: flex; height: 14px; border-radius: 4px; overflow: hidden;
            background: #eef0f2; gap: 2px; }}
  .stack div {{ height: 100%; }}
  .legend {{ display: flex; gap: 18px; flex-wrap: wrap; margin-top: 9px;
             font-size: 12px; color: #3d4854;
             font-variant-numeric: tabular-nums; }}
  .swatch {{ display: inline-block; width: 10px; height: 10px;
             border-radius: 2px; margin-right: 6px; vertical-align: -1px; }}
  .cols {{ display: grid; grid-template-columns: minmax(0, 7fr) minmax(280px, 3fr);
           gap: 16px; align-items: start; }}
  @media (max-width: 1080px) {{ .cols {{ grid-template-columns: 1fr; }} }}
  .panel {{ background: #fff; border: 1px solid #e3e6ea; border-radius: 8px;
            overflow: hidden; }}
  .panel + .panel {{ margin-top: 16px; }}
  .panel-head {{ display: flex; align-items: center; gap: 12px;
                 justify-content: space-between; padding: 11px 16px;
                 border-bottom: 1px solid #e3e6ea; }}
  .panel-head h2 {{ font-size: 13px; font-weight: 650; color: #17202b; }}
  .panel-head form {{ display: flex; gap: 8px; }}
  select {{ font: inherit; font-size: 12px; color: #3d4854;
            border: 1px solid #d4d9df; border-radius: 5px;
            padding: 3px 6px; background: #fff; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ text-align: left; padding: 8px 12px;
            border-bottom: 1px solid #eef0f2; font-size: 13px;
            white-space: nowrap; }}
  td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  th {{ color: #5b6774; font-weight: 600; font-size: 11px;
        text-transform: uppercase; letter-spacing: .04em;
        background: #fafbfc; }}
  tr:last-child td {{ border-bottom: none; }}
  tbody tr:hover td {{ background: #f8fafc; }}
  .badge {{ display: inline-block; padding: 1px 8px; border-radius: 4px;
            font-size: 11.5px; font-weight: 600; }}
  .cause {{ color: #5b6774; max-width: 260px; overflow: hidden;
            text-overflow: ellipsis; }}
  .classbar {{ padding: 10px 16px; }}
  .classbar .row {{ margin-bottom: 10px; }}
  .classbar .lbl {{ display: flex; justify-content: space-between;
                    font-size: 12px; color: #3d4854; margin-bottom: 4px;
                    font-variant-numeric: tabular-nums; }}
  .classbar .track {{ height: 6px; background: #eef0f2; border-radius: 3px;
                      overflow: hidden; }}
  .classbar .fill {{ height: 100%; background: {seg_recovered};
                     border-radius: 3px; }}
  .feed {{ max-height: 430px; overflow-y: auto; }}
  .feed-item {{ padding: 8px 16px; border-bottom: 1px solid #eef0f2;
                font-size: 12.5px; }}
  .feed-item:last-child {{ border-bottom: none; }}
  .feed-item .meta {{ color: #8b95a1; font-size: 11px;
                      font-variant-numeric: tabular-nums; }}
  .refresh-note {{ color: #8b95a1; font-size: 11px; }}
  .timeline {{ list-style: none; }}
  .timeline li {{ background: #fff; border: 1px solid #e3e6ea;
                  border-radius: 8px; padding: 12px 16px;
                  margin-bottom: 10px; }}
  .timeline .meta {{ color: #5b6774; font-size: 12px; margin-bottom: 4px;
                     font-variant-numeric: tabular-nums; }}
  pre {{ background: #f6f7f9; border: 1px solid #e9ecef; padding: 8px 12px;
         border-radius: 6px; overflow-x: auto; font-size: 12px;
         color: #33404d; }}
  .detail-head {{ margin: 18px 0 6px; }}
  .detail-sub {{ color: #5b6774; margin-bottom: 18px; }}
  h3.sec {{ font-size: 13px; font-weight: 650; margin: 20px 0 8px; }}
  .quote {{ color: #3d4854; background: #f8fafc; border-left: 3px solid #d4d9df;
            padding: 8px 12px; border-radius: 0 6px 6px 0; margin-top: 8px;
            font-size: 13px; }}
</style>
</head>
<body>
<header>
  <div class="brand">Reclaim<small>Wins back your failed payments</small></div>
  <div style="display:flex;align-items:center;gap:14px">
    <span class="pulse"><i></i>agent watching payments</span>
    {right_slot}
    <span class="env">RAZORPAY TEST MODE</span>
  </div>
</header>
{body}
<script>
// Count-up on change only: numbers animate when money actually moves,
// stay still otherwise (page reloads every 5s).
document.querySelectorAll('[data-count]').forEach(function (el) {{
  var target = parseFloat(el.getAttribute('data-count'));
  var key = 'reclaim:' + el.getAttribute('data-key');
  var prev = parseFloat(sessionStorage.getItem(key));
  try {{ sessionStorage.setItem(key, String(target)); }} catch (e) {{}}
  if (isNaN(prev) || prev === target) return;
  el.classList.add('flash');
  var t0 = performance.now(), dur = 700;
  function step(t) {{
    var p = Math.min(1, (t - t0) / dur);
    var eased = 1 - Math.pow(1 - p, 3);
    var val = prev + (target - prev) * eased;
    el.textContent = '₹' + Math.round(val).toLocaleString('en-IN');
    if (p < 1) requestAnimationFrame(step);
  }}
  requestAnimationFrame(step);
}});
</script>
</body>
</html>"""


def esc_nav() -> str:
    conn = get_conn()
    try:
        n = conn.execute("SELECT COUNT(*) FROM payments "
                         "WHERE recovery_status = 'escalated'").fetchone()[0]
    finally:
        conn.close()
    return (f'<a href="/escalations" style="font-size:13px">'
            f'Needs you ({n})</a>')


def badge(text: str) -> str:
    bg, fg = BADGE.get(text, ("#eef0f2", "#5b6774"))
    label = STATE_LABEL.get(text, text.replace("_", " "))
    return (f'<span class="badge" title="{text}" style="background:{bg};'
            f'color:{fg}">{label}</span>')


def rupees(paise: int) -> str:
    return f"₹{paise / 100:,.0f}"


def ago(iso: str) -> str:
    """Human time: '2m ago' beats an ISO timestamp for a merchant."""
    from datetime import datetime, timezone
    try:
        then = datetime.fromisoformat(iso)
        secs = (datetime.now(timezone.utc) - then).total_seconds()
    except (ValueError, TypeError):
        return ""
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)}m ago"
    if secs < 86400:
        return f"{int(secs // 3600)}h ago"
    return f"{int(secs // 86400)}d ago"


@app.get("/", response_class=HTMLResponse)
def dashboard(cls: str = "", rec: str = "") -> str:
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
        manual = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(amount_paise),0) amt "
            "FROM payments WHERE recovery_status = 'recovered_manual'"
        ).fetchone()
        escalated = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(amount_paise),0) amt "
            "FROM payments WHERE recovery_status = 'escalated'").fetchone()
        pending = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(amount_paise),0) amt "
            "FROM payments WHERE status != 'captured' AND recovery_status "
            "IN ('detected','in_progress','promised','none')").fetchone()
        written_off = conn.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(amount_paise),0) amt "
            "FROM payments WHERE recovery_status = 'written_off'").fetchone()

        classes = [r["failure_code"] for r in conn.execute(
            "SELECT DISTINCT failure_code FROM payments "
            "WHERE failure_code IS NOT NULL ORDER BY failure_code")]
        rec_states = [r["recovery_status"] for r in conn.execute(
            "SELECT DISTINCT recovery_status FROM payments "
            "WHERE status != 'captured' ORDER BY recovery_status")]

        where = "p.status != 'captured'"
        params: list = []
        if cls:
            where += " AND p.failure_code = ?"
            params.append(cls)
        if rec:
            where += " AND p.recovery_status = ?"
            params.append(rec)

        rows = conn.execute(
            f"SELECT p.*, d.root_cause, "
            f"  (SELECT COUNT(*) FROM recovery_actions ra "
            f"   WHERE ra.payment_id = p.id) attempts "
            f"FROM payments p LEFT JOIN diagnoses d ON d.payment_id = p.id "
            f"WHERE {where} "
            f"ORDER BY CASE p.recovery_status "
            f"  WHEN 'in_progress' THEN 0 WHEN 'promised' THEN 1 "
            f"  WHEN 'detected' THEN 2 WHEN 'escalated' THEN 3 "
            f"  WHEN 'recovered' THEN 4 ELSE 5 END, "
            f"p.amount_paise DESC", params).fetchall()

        breakdown = conn.execute(
            "SELECT failure_code, COUNT(*) n, "
            "  SUM(CASE WHEN recovery_status='recovered' THEN 1 ELSE 0 END) r "
            "FROM payments WHERE failure_code IS NOT NULL "
            "GROUP BY failure_code ORDER BY n DESC").fetchall()

        feed = conn.execute(
            "SELECT ra.*, p.amount_paise FROM recovery_actions ra "
            "JOIN payments p ON p.id = ra.payment_id "
            "ORDER BY ra.id DESC LIMIT 30").fetchall()

        promises = conn.execute(
            "SELECT pr.*, p.amount_paise FROM promises pr "
            "JOIN payments p ON p.id = pr.payment_id "
            "ORDER BY pr.id DESC LIMIT 15").fetchall()
    finally:
        conn.close()

    at_risk = (recovered["amt"] + manual["amt"] + escalated["amt"]
               + pending["amt"] + written_off["amt"])
    money_in = recovered["amt"] + manual["amt"]
    pct = (lambda amt: 100 * amt / at_risk if at_risk else 0)
    rate = 100 * recovered["amt"] / at_risk if at_risk else 0

    ic = ('<svg class="icon" width="16" height="16" viewBox="0 0 24 24" '
          'fill="none" stroke="currentColor" stroke-width="2" '
          'stroke-linecap="round" stroke-linejoin="round">{}</svg>')
    icons = {
        "billed": ic.format('<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 '
                            '2h12a2 2 0 0 0 2-2V8z"/>'
                            '<polyline points="14 2 14 8 20 8"/>'),
        "paid": ic.format('<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/>'
                          '<polyline points="22 4 12 14.01 9 11.01"/>'),
        "won": ic.format('<polyline points="1 4 1 10 7 10"/>'
                         '<path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>'),
        "working": ic.format('<circle cx="12" cy="12" r="10"/>'
                             '<polyline points="12 6 12 12 16 14"/>'),
        "you": ic.format('<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 '
                         '4v2"/><circle cx="12" cy="7" r="4"/>'),
    }
    kpis = f"""
    <div class="kpis">
      <div class="kpi">{icons['billed']}<div class="label">You billed</div>
        <div class="value" data-count="{total['amt'] // 100}"
          data-key="billed">{rupees(total['amt'])}</div>
        <div class="note">{total['n']} payments</div></div>
      <div class="kpi">{icons['paid']}
        <div class="label">Paid on first try</div>
        <div class="value" data-count="{captured['amt'] // 100}"
          data-key="paid">{rupees(captured['amt'])}</div>
        <div class="note">{captured['n']} payments</div></div>
      <div class="kpi hero">{icons['won']}
        <div class="label">Won back by the agent</div>
        <div class="value" style="color:#1e7f4f"
          data-count="{recovered['amt'] // 100}" data-key="won">
          {rupees(recovered['amt'])}</div>
        <div class="note">{recovered['n']} payments ·
          {rate:.0f}% of what failed{
          f" · +{rupees(manual['amt'])} collected by you"
          if manual['n'] else ""}</div></div>
      <div class="kpi">{icons['working']}
        <div class="label">Being worked on</div>
        <div class="value" data-count="{pending['amt'] // 100}"
          data-key="pending">{rupees(pending['amt'])}</div>
        <div class="note">{pending['n']} payments</div></div>
      <div class="kpi">{icons['you']}
        <div class="label">Needs your attention</div>
        <div class="value" data-count="{escalated['amt'] // 100}"
          data-key="needsyou">{rupees(escalated['amt'])}</div>
        <div class="note">{escalated['n']} payments ·
          <a href="/escalations">review</a></div></div>
    </div>"""

    resbar = f"""
    <div class="resbar">
      <div class="head"><b>{rupees(at_risk)} of your payments failed —
        here's where that money stands</b>
        <span class="refresh-note">updates every 5s</span></div>
      <div class="stack">
        <div style="width:{pct(money_in):.1f}%;
          background:{SEG_RECOVERED}" title="money in"></div>
        <div style="width:{pct(pending['amt']):.1f}%;
          background:{SEG_PENDING}" title="pending"></div>
        <div style="width:{pct(escalated['amt']):.1f}%;
          background:{SEG_ESCALATED}" title="escalated"></div>
        <div style="width:{pct(written_off['amt']):.1f}%;
          background:#98a2b3" title="written off"></div>
      </div>
      <div class="legend">
        <span><span class="swatch" style="background:{SEG_RECOVERED}"></span>
          Money in {rupees(money_in)}{
          f" (agent {rupees(recovered['amt'])} + you {rupees(manual['amt'])})"
          if manual['n'] else ""}</span>
        <span><span class="swatch" style="background:{SEG_PENDING}"></span>
          Still trying {rupees(pending['amt'])}</span>
        <span><span class="swatch" style="background:{SEG_ESCALATED}"></span>
          Needs you {rupees(escalated['amt'])}</span>
        <span><span class="swatch" style="background:#98a2b3"></span>
          Let go {rupees(written_off['amt'])}</span>
      </div>
    </div>"""

    opt = lambda v, sel: (f'<option value="{v}"'
                          f'{" selected" if v == sel else ""}>{v or "all"}'
                          f'</option>')
    filters = f"""
    <form method="get">
      <select name="cls" onchange="this.form.submit()">
        {opt('', cls)}{''.join(opt(c, cls) for c in classes)}
      </select>
      <select name="rec" onchange="this.form.submit()">
        {opt('', rec)}{''.join(opt(s, rec) for s in rec_states)}
      </select>
    </form>"""

    body_rows = []
    for r in rows:
        fail = FAILURE_LABEL.get(r["failure_code"], r["failure_code"] or "")
        body_rows.append(f"""
        <tr>
          <td><a href="/payment/{r['id']}">{r['id']}</a></td>
          <td>{r['customer_name']}</td>
          <td class="num">{rupees(r['amount_paise'])}</td>
          <td title="{r['failure_code'] or ''}">{fail}</td>
          <td class="cause">{r['root_cause'] or '—'}</td>
          <td class="num">{r['attempts'] or ''}</td>
          <td>{badge(r['recovery_status'])}</td>
        </tr>""")

    class_rows = []
    for b in breakdown:
        frac = 100 * b["r"] / b["n"] if b["n"] else 0
        class_rows.append(f"""
        <div class="row">
          <div class="lbl"><span title="{b['failure_code']}">
            {FAILURE_LABEL.get(b['failure_code'], b['failure_code'])}</span>
            <span>{b['r']}/{b['n']}</span></div>
          <div class="track"><div class="fill"
            style="width:{frac:.0f}%"></div></div>
        </div>""")

    promise_color = {"received": "detected", "pending": "promised",
                     "kept": "recovered", "broken": "failed",
                     "closed": "written_off"}
    promise_items = []
    for pr in promises:
        bg, fg = BADGE.get(promise_color.get(pr["status"], "written_off"))
        due = (f" · due {pr['due_at'][:10]}" if pr["due_at"] else "")
        promise_items.append(f"""
        <div class="feed-item">
          <a href="/payment/{pr['payment_id']}">{pr['payment_id']}</a>
          · {rupees(pr['amount_paise'])}
          <span class="badge" style="background:{bg};color:{fg}">
            {pr['status']}</span>{due}
          <div class="meta" style="font-style:italic">
            “{pr['raw_reply']}”</div>
        </div>""")

    feed_items = []
    for f in feed:
        link = (f' · <a href="{f["rzp_link_url"]}" target="_blank">link</a>'
                if f["rzp_link_url"] else "")
        what = ACTION_LABEL.get(f["action"], f["action"].replace("_", " "))
        feed_items.append(f"""
        <div class="feed-item">
          <a href="/payment/{f['payment_id']}">{f['payment_id']}</a>
          · {what} (try {f['attempt']})
          {badge(f['state'])}{link}
          <div class="meta">{ago(f['created_at'])} ·
            {rupees(f['amount_paise'])}</div>
        </div>""")

    cmp_data = compare()
    ca, cb = cmp_data["agent"], cmp_data["baseline"]
    cmp_pct = (lambda p: f"{100 * p / at_risk:.0f}%" if at_risk else "—")
    cmp_rows = [
        ("Recovered (clean)",
         f"<b style='color:#1e7f4f'>{rupees(ca['recovered_paise'])} "
         f"({cmp_pct(ca['recovered_paise'])})</b>",
         f"{rupees(cb['recovered_paise'])} "
         f"({cmp_pct(cb['recovered_paise'])})"),
        ("“Recovered” from suspected fraud — a chargeback time bomb",
         rupees(0),
         f"<b style='color:#b42318'>{rupees(cb['fraud_recovered_paise'])}</b>"),
        ("Attempts made", str(ca["attempts"]), str(cb["attempts"])),
        ("Retries against suspected fraud",
         "0", f"<b style='color:#b42318'>{cb['fraud_retries']}</b>"),
        ("Attempts on dead (expired) cards",
         str(ca["dead_card_attempts"]), str(cb["dead_card_attempts"])),
        ("Customer refusals honoured",
         str(ca["refusals_honoured"]), str(cb["refusals_honoured"])),
    ]
    cmp_html = f"""
    <div class="panel" style="margin-bottom:20px">
      <div class="panel-head"><h2>Reclaim vs. the old way — same payments,
        measured</h2>
        <span class="refresh-note">the old way: blindly retry everything ×3
        — no diagnosis, no timing, no listening</span>
      </div>
      <table>
        <thead><tr><th>Metric</th><th class="num">Reclaim agent</th>
          <th class="num">Blind retry ×3</th></tr></thead>
        <tbody>{''.join(
            f'<tr><td>{m}</td><td class="num">{a}</td>'
            f'<td class="num">{b}</td></tr>'
            for m, a, b in cmp_rows)}</tbody>
      </table>
    </div>"""

    body = f"""
    {kpis}
    {resbar}
    {cmp_html}
    <div class="cols">
      <div class="panel">
        <div class="panel-head"><h2>At-risk payments ({len(rows)})</h2>
          {filters}</div>
        <div style="overflow-x:auto">
        <table>
          <thead><tr><th>Payment</th><th>Customer</th>
            <th class="num">Amount</th><th>What happened</th>
            <th>Why (AI diagnosis)</th><th class="num">Tries</th>
            <th>Status</th></tr></thead>
          <tbody>{''.join(body_rows)}</tbody>
        </table>
        </div>
      </div>
      <div>
        <div class="panel">
          <div class="panel-head"><h2>How much came back, by reason</h2></div>
          <div class="classbar">{''.join(class_rows)}</div>
        </div>
        <div class="panel">
          <div class="panel-head"><h2>Customer promises</h2></div>
          <div class="feed">{''.join(promise_items) or
            '<div class="feed-item">No customer replies yet.</div>'}</div>
        </div>
        <div class="panel">
          <div class="panel-head"><h2>Recent actions</h2></div>
          <div class="feed">{''.join(feed_items) or
            '<div class="feed-item">No actions yet.</div>'}</div>
        </div>
      </div>
    </div>"""
    return PAGE_SHELL.format(
        body=body,
        refresh='<meta http-equiv="refresh" content="5">',
        right_slot=esc_nav(),
        seg_recovered=SEG_RECOVERED,
    )


@app.get("/escalations", response_class=HTMLResponse)
def escalations() -> str:
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT p.*, d.root_cause, d.recommended_action, "
            "  (SELECT a.detail FROM audit_log a WHERE a.payment_id = p.id "
            "   AND (a.action LIKE '%escalat%' OR a.action = 'promise_broken')"
            "   ORDER BY a.id DESC LIMIT 1) esc_detail "
            "FROM payments p LEFT JOIN diagnoses d ON d.payment_id = p.id "
            "WHERE p.recovery_status = 'escalated' "
            "ORDER BY p.amount_paise DESC").fetchall()
    finally:
        conn.close()

    cards = []
    for r in rows:
        reason = "—"
        if r["esc_detail"]:
            detail = json.loads(r["esc_detail"])
            reason = (detail.get("reason") or detail.get("policy")
                      or detail.get("note") or "—")
        cards.append(f"""
        <div class="panel" style="margin-bottom:12px;padding:14px 16px">
          <div style="display:flex;justify-content:space-between;
                      align-items:baseline;gap:12px;flex-wrap:wrap">
            <div>
              <a href="/payment/{r['id']}"><b>{r['id']}</b></a>
              · {r['customer_name']} · {rupees(r['amount_paise'])}
              · {FAILURE_LABEL.get(r['failure_code'], r['failure_code'])}
            </div>
            <form method="post" action="/escalations/{r['id']}"
                  style="display:flex;gap:8px">
              <button name="decision" value="recovered"
                style="font:inherit;font-size:12px;padding:4px 12px;
                       border-radius:5px;border:1px solid #1e7f4f;
                       background:#e7f4ec;color:#1e7f4f;cursor:pointer">
                Collected manually</button>
              <button name="decision" value="written_off"
                style="font:inherit;font-size:12px;padding:4px 12px;
                       border-radius:5px;border:1px solid #d4d9df;
                       background:#fff;color:#3d4854;cursor:pointer">
                Write off</button>
            </form>
          </div>
          <div style="color:#5b6774;font-size:13px;margin-top:6px">
            Why the agent stopped: {reason}</div>
        </div>""")

    body = f"""
    <a href="/">&larr; Dashboard</a>
    <h1 class="detail-head" style="font-size:20px">Needs your attention</h1>
    <div class="detail-sub">These are the payments the agent chose to stop
      on rather than push further. Your decision is recorded in the payment's
      history as <b>actor: human</b>.</div>
    {''.join(cards) or '<div class="panel" style="padding:14px 16px">'
     'Nothing needs you right now — the agent is handling the rest.</div>'}"""
    return PAGE_SHELL.format(body=body, refresh="", right_slot="",
                             seg_recovered=SEG_RECOVERED)


@app.post("/escalations/{payment_id}")
def resolve_escalation(payment_id: str, decision: str = Form(...)):
    if decision not in ("recovered", "written_off"):
        return RedirectResponse("/escalations", status_code=303)
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT recovery_status FROM payments WHERE id = ?",
            (payment_id,)).fetchone()
        # only queue members can be resolved, and only once
        if row is not None and row["recovery_status"] == "escalated":
            # Human collections get their own status so they are never
            # counted in the agent's recovery metrics.
            new_status = ("recovered_manual" if decision == "recovered"
                          else decision)
            with conn:
                conn.execute(
                    "UPDATE payments SET recovery_status = ? WHERE id = ?",
                    (new_status, payment_id),
                )
                log_action(conn, payment_id, "human", "escalation_resolved", {
                    "decision": decision,
                    "note": "resolved by a person from the escalation queue",
                })
    finally:
        conn.close()
    return RedirectResponse("/escalations", status_code=303)


@app.get("/payment/{payment_id}", response_class=HTMLResponse)
def payment_detail(payment_id: str) -> str:
    conn = get_conn()
    try:
        p = conn.execute(
            "SELECT * FROM payments WHERE id = ?", (payment_id,)).fetchone()
        if p is None:
            return PAGE_SHELL.format(body="<p>Payment not found.</p>",
                                     refresh="", right_slot="",
                                     seg_recovered=SEG_RECOVERED)
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
        <h3 class="sec">Diagnosis (Gemini)</h3>
        <div class="panel" style="padding:12px 16px">
          <b>{d['root_cause']}</b> ·
          {'transient — a retry could succeed' if d['transient']
           else 'not transient'} ·
          recommended action: <b>{d['recommended_action'].replace('_', ' ')}</b>
          <div class="quote">{d['customer_message']}</div>
        </div>"""

    action_rows = []
    for a in actions:
        link = (f'<a href="{a["rzp_link_url"]}" target="_blank">'
                f'{a["rzp_link_id"]}</a>' if a["rzp_link_url"] else "—")
        action_rows.append(f"""
        <tr><td class="num">{a['attempt']}</td>
            <td>{a['action'].replace('_', ' ')}</td>
            <td>{badge(a['state'])}</td><td>{link}</td>
            <td>{(a['executed_at'] or '')[:19].replace('T', ' ')}</td></tr>""")
    actions_html = ""
    if action_rows:
        actions_html = f"""
        <h3 class="sec">Recovery attempts</h3>
        <div class="panel"><table>
          <thead><tr><th class="num">#</th><th>Action</th><th>State</th>
              <th>Razorpay link</th><th>Executed</th></tr></thead>
          <tbody>{''.join(action_rows)}</tbody>
        </table></div>"""

    items = []
    for e in events:
        detail = json.dumps(json.loads(e["detail"]), indent=2)
        items.append(f"""
        <li>
          <div class="meta">{e['created_at'][:19].replace('T', ' ')} ·
            {e['actor']}</div>
          <b>{e['action'].replace('_', ' ')}</b>
          <pre>{detail}</pre>
        </li>""")

    body = f"""
    <a href="/">&larr; All payments</a>
    <h1 class="detail-head" style="font-size:20px">{p['id']}</h1>
    <div class="detail-sub">{p['customer_name']} ·
      {rupees(p['amount_paise'])} ·
      order {p['rzp_order_id'] or '(offline)'} ·
      {badge(p['status'])} {badge(p['recovery_status'])}</div>
    {diag_html}
    {actions_html}
    <h3 class="sec">Audit trail — every action with its reasoning</h3>
    <ul class="timeline">{''.join(items)}</ul>"""
    return PAGE_SHELL.format(body=body, refresh="", right_slot=esc_nav(),
                             seg_recovered=SEG_RECOVERED)
