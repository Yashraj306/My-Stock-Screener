import requests
from config import *

def telegram_text(title, today, nifty, breadth, ideas):
    L = [f"{title} | {today}", f"Nifty {nifty['price']:,.0f} ({nifty['chg']:+.2f}%) | Breadth {breadth['tone']}", ""]
    if not ideas: L.append("No setups passed the filters today.")
    for i, r in enumerate(ideas[:10], 1):
        lv, oc = r["lv"], r.get("oc")
        b = oc["best"] if oc else None
        side = "CALL" if r["direction"] == "BULLISH" else "PUT"
        kind = "pre-rally" if r["setup"] == "PRE" else "continuation"
        L.append(f"{i}) {r['name']} BUY {side} | Grade {r['grade']} | {kind}")
        L.append(f"   Entry {lv['entry']:.2f} | SL {lv['stop']:.2f} | T1 {lv['t1']:.2f} | R:R {lv['rr']:.2f}")
        if b: L.append(f"   Strike {b['strike']:.0f} {b['side']} @ {b['ltp']:.1f}")
        if r.get("size"):
            z = r["size"]
            L.append("   Size: " + (f"{z['lots']} lot(s)" if z["lots"] else "1 lot exceeds your risk limit"))
    L += ["", "Ideas only, not advice. Use a stop-loss."]
    return "\n".join(L)

def send_telegram(text):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID): return False
    ok = True
    for i in range(0, len(text), 3800):
        try:
            r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                              data={"chat_id": TELEGRAM_CHAT_ID, "text": text[i:i + 3800]}, timeout=20)
            ok = ok and r.status_code == 200
        except Exception as e:
            print("telegram failed:", e); ok = False
    return ok
