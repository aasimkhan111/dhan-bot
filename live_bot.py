import pandas as pd
import numpy as np
from flask import Flask, request, jsonify
from dhanhq import dhanhq, DhanContext

app = Flask(__name__)

# --- LIVE CONFIG ---
CLIENT_ID = "1100819221"
ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc4MDQwMDUzLCJpYXQiOjE3Nzc5NTM2NTMsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwODE5MjIxIn0.G7AewK4Ex-XsNwIucC7aoivUbNVqmZNOvC_RmUIxYp9QjIEQzyWDRyOAt-LWiIFIr1nYi6IhRB4j1Jo_3hPjjw"
SECRET_TOKEN = "JunnarTrader2026"

# Connect to Dhan
dhan_context = DhanContext(client_id=CLIENT_ID, access_token=ACCESS_TOKEN)
dhan = dhanhq(dhan_context)

print("Syncing with Dhan Master Scrip List...")
SCRIP_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
try:
    df = pd.read_csv(SCRIP_URL, low_memory=False)
except:
    df = pd.read_csv(SCRIP_URL, sep='\t', low_memory=False)

def find_col(keywords):
    for kw in keywords:
        for c in df.columns:
            if kw.upper() in str(c).upper(): return c
    return None

COL_ID = find_col(['SMART_SYMBOL', 'SYMBOL_ID', 'SMART']) or df.columns[9]
COL_SYMBOL = find_col(['TRADING_SYMBOL', 'SYMBOL_NAME']) or df.columns[2]
COL_STRIKE = find_col(['STRIKE_PRICE', 'STRIKE']) or df.columns[7]
COL_OPT_TYPE = find_col(['OPTION_TYPE', 'CALL_PUT']) or df.columns[8]
COL_INST = find_col(['INSTRUMENT_NAME', 'INSTRUMENT']) or df.columns[1]
COL_EXPIRY = find_col(['EXPIRY_DATE', 'EXPIRY']) or df.columns[6]

print(f"✅ Master Scrip List Sync Complete.")

def get_security_id(symbol, price=0, opt_type='CE'):
    try:
        symbol = symbol.upper()
        opt_type = opt_type.upper()
        base = "BANKNIFTY" if "BANKNIFTY" in symbol else "NIFTY"
        
        mask = (df[COL_INST].astype(str).str.contains('OPT', case=False)) & \
               (df[COL_SYMBOL].astype(str).str.contains(base, case=False))
        
        match_types = [opt_type]
        if opt_type == 'CE': match_types.append('CALL')
        if opt_type == 'PE': match_types.append('PUT')
        mask &= (df[COL_OPT_TYPE].astype(str).isin(match_types))
        
        subset = df[mask].copy()
        if subset.empty: return None
            
        subset['STRIKE_VAL'] = pd.to_numeric(subset[COL_STRIKE], errors='coerce')
        subset = subset.dropna(subset=['STRIKE_VAL'])
        subset['DIST'] = (subset['STRIKE_VAL'] - price).abs()
        
        min_dist = subset['DIST'].min()
        final_res = subset[subset['DIST'] == min_dist].sort_values(by=COL_EXPIRY)
        
        if not final_res.empty:
            found = final_res.iloc[0]
            s_id = str(int(float(found[COL_ID])))
            print(f"✅ FOUND: {found[COL_SYMBOL]} | ID: {s_id}")
            return s_id
            
        return None
    except Exception as e:
        print(f"⚠️ Search Error: {str(e)}")
        return None

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True)
        if not data: return jsonify({"error": "No Data"}), 400
        print(f"\n📥 Signal: {data}")

        if data.get('secret') != SECRET_TOKEN:
            return jsonify({"error": "Auth Fail"}), 403

        if data.get('action') == 'exit':
            print("🛑 Squaring Off All Positions...")
            pos_res = dhan.get_positions()
            if pos_res.get('status') == 'success':
                for pos in pos_res.get('data', []):
                    qty = int(pos.get('netQty', 0))
                    if qty != 0:
                        dhan.place_order(
                            security_id=str(pos.get('securityId')),
                            exchange_segment=pos.get('exchangeSegment'),
                            transaction_type=dhan.SELL if qty > 0 else dhan.BUY,
                            quantity=abs(qty),
                            order_type=dhan.MARKET,
                            product_type=pos.get('productType'),
                            price=0,
                            trigger_price=0,
                            validity=dhan.DAY,
                            after_market_order=False
                        )
                return jsonify({"status": "success"}), 200
            return jsonify({"error": "Pos Fetch Fail"}), 500

        symbol = data.get('symbol', 'BANKNIFTY-ATM')
        price = float(data.get('price', 0))
        opt_type = data.get('option_type', 'CE').upper()
        quantity = int(data.get('quantity', 300))

        sec_id = get_security_id(symbol, price, opt_type)
        if not sec_id: return jsonify({"error": "Symbol Not Found"}), 404

        # Place Order with robust F&O parameters
        order_res = dhan.place_order(
            security_id=sec_id,
            exchange_segment=dhan.FNO, # Explicitly NSE_FNO
            transaction_type=dhan.BUY,
            quantity=quantity,
            order_type=dhan.MARKET,
            product_type=dhan.MARGIN, # F&O usually requires MARGIN
            price=0,
            trigger_price=0,
            validity=dhan.DAY,
            after_market_order=False
        )
        print(f"📡 Dhan Response: {order_res}")
        return jsonify(order_res), 200

    except Exception as e:
        print(f"❌ Webhook Error: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
