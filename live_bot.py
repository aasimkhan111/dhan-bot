import pandas as pd
import numpy as np
from flask import Flask, request, jsonify
from dhanhq import dhanhq, DhanContext

app = Flask(__name__)

# --- LIVE CONFIG ---
CLIENT_ID = "1100819221"
ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc4MDU3NTY3LCJpYXQiOjE3Nzc5NzExNjcsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwODE5MjIxIn0.RgZKLwjRfPQZCm0Z0fBIw7_846mnl6fO4aeGeeM-_QTjT4WannQxQXi8GSWplCerYxobfQjttgUDd0akbrA4Ng"
SECRET_TOKEN = "JunnarTrader2026"

# Connect to Dhan
dhan_context = DhanContext(client_id=CLIENT_ID, access_token=ACCESS_TOKEN)
dhan = dhanhq(dhan_context)

print("--- SYNCING SCRIP MASTER ---")
SCRIP_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
try:
    df = pd.read_csv(SCRIP_URL, low_memory=False)
except:
    df = pd.read_csv(SCRIP_URL, sep='\t', low_memory=False)

# 🔍 DEBUG: PRINT HEADERS TO LOGS
print(f"COLUMNS FOUND ({len(df.columns)}): {list(df.columns)}")

def get_col(keywords, default_idx):
    for name in keywords:
        for actual in df.columns:
            if name.upper() in str(actual).upper(): return actual
    return df.columns[default_idx]

# Map columns with fallback
COL_EXCH   = get_col(['SEM_EXCHANGE_SEGMENT', 'SEGMENT', 'EXCHANGE'], 0)
COL_INST   = get_col(['SEM_INSTRUMENT_NAME', 'INSTRUMENT'], 1)
COL_SYMBOL = get_col(['SEM_TRADING_SYMBOL', 'SYMBOL_NAME', 'TRADING'], 2)
COL_EXPIRY = get_col(['SEM_EXPIRY_DATE', 'EXPIRY'], 6)
COL_STRIKE = get_col(['SEM_STRIKE_PRICE', 'STRIKE'], 7)
COL_OPT    = get_col(['SEM_OPTION_TYPE', 'OPTION'], 8)
COL_ID     = get_col(['SEM_SMART_SYMBOL', 'SMART_SYMBOL', 'SYMBOL_ID'], 11)

print(f"Current Mapping -> ID: {COL_ID}, Symbol: {COL_SYMBOL}, Exch: {COL_EXCH}")

def get_security_id(symbol, price=0, opt_type='CE'):
    try:
        symbol = symbol.upper()
        opt_type = opt_type.upper()
        base = "BANKNIFTY" if "BANKNIFTY" in symbol else "NIFTY"
        
        # Filter for Options and Base
        mask = (df[COL_INST].astype(str).str.contains('OPT', case=False)) & \
               (df[COL_SYMBOL].astype(str).str.contains(base, case=False))
        
        # Match CE/PE
        match_types = [opt_type]
        if opt_type == 'CE': match_types.extend(['CALL', 'CE'])
        if opt_type == 'PE': match_types.extend(['PUT', 'PE'])
        mask &= (df[COL_OPT].astype(str).isin(match_types))
        
        subset = df[mask].copy()
        if subset.empty: return None
            
        # Match Strike
        subset['STRIKE_VAL'] = pd.to_numeric(subset[COL_STRIKE], errors='coerce')
        subset = subset.dropna(subset=['STRIKE_VAL'])
        subset['DIST'] = (subset['STRIKE_VAL'] - price).abs()
        
        min_dist = subset['DIST'].min()
        final_res = subset[subset['DIST'] == min_dist].sort_values(by=COL_EXPIRY)
        
        if not final_res.empty:
            found = final_res.iloc[0]
            # Convert ID to string
            s_id = str(int(float(found[COL_ID])))
            print(f"✅ MATCH: {found[COL_SYMBOL]} | ID: {s_id} | Exch: {found[COL_EXCH]}")
            return s_id
            
        return None
    except Exception as e:
        print(f"⚠️ Search Error: {str(e)}")
        return None

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True)
        if not data: return jsonify({"error": "No JSON"}), 400
        print(f"\n📥 Signal: {data}")

        if data.get('secret') != SECRET_TOKEN:
            return jsonify({"error": "Auth Fail"}), 403

        if data.get('action') == 'exit':
            print("🛑 EXIT ALL POSITIONS")
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
                            price=0, trigger_price=0, validity=dhan.DAY
                        )
                return jsonify({"status": "success"}), 200
            return jsonify({"error": "Pos Fail"}), 500

        # ENTRY
        symbol = data.get('symbol', 'BANKNIFTY-ATM')
        price = float(data.get('price', 0))
        opt_type = data.get('option_type', 'CE').upper()
        quantity = int(data.get('quantity', 300))

        sec_id = get_security_id(symbol, price, opt_type)
        if not sec_id: return jsonify({"error": "NotFound"}), 404

        # Place Order
        order_res = dhan.place_order(
            security_id=sec_id,
            exchange_segment=dhan.FNO,
            transaction_type=dhan.BUY,
            quantity=quantity,
            order_type=dhan.MARKET,
            product_type=dhan.MARGIN,
            price=0, trigger_price=0, validity=dhan.DAY,
            after_market_order=False
        )
        print(f"📡 Response: {order_res}")
        return jsonify(order_res), 200

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
