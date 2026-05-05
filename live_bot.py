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
df = pd.read_csv(SCRIP_URL, low_memory=False)
print(f"✅ Loaded {len(df)} scrips.")
print(f"📊 Available Columns: {list(df.columns)}")

# Helper to find column names dynamically
def get_col(pattern):
    for c in df.columns:
        if pattern.upper() in c.upper():
            return c
    return None

# Detect crucial columns
COL_ID = get_col('SMART_SYMBOL') or 'SEM_SMART_SYMBOL'
COL_SYMBOL = get_col('TRADING_SYMBOL') or 'SEM_TRADING_SYMBOL'
COL_STRIKE = get_col('STRIKE_PRICE') or 'SEM_STRIKE_PRICE'
COL_OPT_TYPE = get_col('OPTION_TYPE') or 'SEM_OPTION_TYPE'
COL_INST = get_col('INSTRUMENT_NAME') or 'SEM_INSTRUMENT_NAME'
COL_EXPIRY = get_col('EXPIRY_DATE') or 'SEM_EXPIRY_DATE'
COL_EXCH = get_col('EXCHANGE_SEGMENT') or 'SEM_EXCHANGE_SEGMENT'

print(f"🎯 Using Columns: ID={COL_ID}, Symbol={COL_SYMBOL}, Strike={COL_STRIKE}")

def get_security_id(symbol, price=0, opt_type='CE'):
    try:
        symbol = symbol.upper()
        opt_type = opt_type.upper()
        
        print(f"🔍 Searching: {symbol} at {price} ({opt_type})")
        base = "BANKNIFTY" if "BANKNIFTY" in symbol else "NIFTY"
        
        # 1. Broad Filter
        mask = (df[COL_INST] == 'OPTIDX') & \
               (df[COL_SYMBOL].str.contains(base, case=False))
        
        # 2. Option Type Check
        opt_types = [opt_type]
        if opt_type == 'CE': opt_types.append('CALL')
        if opt_type == 'PE': opt_types.append('PUT')
        mask &= (df[COL_OPT_TYPE].isin(opt_types))
        
        subset = df[mask].copy()
        if subset.empty: return None, None
            
        # 3. Closest Strike Calculation
        subset['STRIKE_NUM'] = pd.to_numeric(subset[COL_STRIKE], errors='coerce')
        subset = subset.dropna(subset=['STRIKE_NUM'])
        subset['DIST'] = (subset['STRIKE_NUM'] - price).abs()
        
        min_dist = subset['DIST'].min()
        res = subset[subset['DIST'] == min_dist].sort_values(by=COL_EXPIRY)
        
        if not res.empty:
            found = res.iloc[0]
            print(f"✅ Match: {found[COL_SYMBOL]} | ID: {found[COL_ID]}")
            return found[COL_ID], found[COL_EXCH]
            
        return None, None
    except Exception as e:
        print(f"⚠️ Search Error: {str(e)}")
        return None, None

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True)
        if not data: return jsonify({"error": "No Data"}), 400

        print(f"\n📥 Signal: {data}")

        if data.get('secret') != SECRET_TOKEN:
            return jsonify({"error": "Auth Fail"}), 403

        if data.get('action') == 'exit':
            print("🛑 Squaring Off...")
            pos_res = dhan.get_positions()
            if pos_res.get('status') == 'success':
                for pos in pos_res.get('data', []):
                    qty = int(pos.get('netQty', 0))
                    if qty != 0:
                        # Use keys from Dhan response
                        s_id = pos.get('securityId')
                        exch = pos.get('exchangeSegment')
                        p_type = pos.get('productType')
                        print(f"Closing {pos.get('tradingSymbol')} | ID: {s_id}")
                        dhan.place_order(
                            security_id=str(s_id),
                            exchange_segment=exch,
                            transaction_type=dhan.SELL if qty > 0 else dhan.BUY,
                            quantity=abs(qty),
                            order_type=dhan.MARKET,
                            product_type=p_type,
                            after_market_order=False
                        )
                return jsonify({"status": "success"}), 200
            return jsonify({"error": "Pos Fail"}), 500

        symbol = data.get('symbol', 'BANKNIFTY-ATM')
        price = float(data.get('price', 0))
        opt_type = data.get('option_type', 'CE').upper()
        quantity = int(data.get('quantity', 300))

        sec_id, exch_seg = get_security_id(symbol, price, opt_type)
        if not sec_id: return jsonify({"error": "Not Found"}), 404

        order_res = dhan.place_order(
            security_id=str(sec_id),
            exchange_segment=exch_seg,
            transaction_type=dhan.BUY,
            quantity=quantity,
            order_type=dhan.MARKET,
            product_type=dhan.INTRA,
            after_market_order=False
        )
        print(f"📡 Dhan: {order_res}")
        return jsonify(order_res), 200

    except Exception as e:
        print(f"❌ Crash: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
