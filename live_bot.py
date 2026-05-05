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
print(f"✅ Loaded {len(df)} scrips from Dhan.")

def get_security_id(symbol, price=0, opt_type='CE'):
    try:
        symbol = symbol.upper()
        opt_type = opt_type.upper()
        
        print(f"🔍 Searching Best Match for: {symbol} at Price {price} ({opt_type})")
        
        # Determine Base (BANKNIFTY or NIFTY)
        base = "BANKNIFTY" if "BANKNIFTY" in symbol else "NIFTY"
        
        # 1. Filter for Options on the specific Base
        mask = (df['SEM_INSTRUMENT_NAME'] == 'OPTIDX') & \
               (df['SEM_TRADING_SYMBOL'].str.contains(base, case=False))
        
        # 2. Filter for Option Type (Flexible check)
        opt_types_to_check = [opt_type]
        if opt_type == 'CE': opt_types_to_check.append('CALL')
        if opt_type == 'PE': opt_types_to_check.append('PUT')
        mask &= (df['SEM_OPTION_TYPE'].isin(opt_types_to_check))
        
        subset = df[mask].copy()
        if subset.empty:
            print(f"❌ No {opt_type} options found for {base}")
            return None, None
            
        # 3. Convert Strike Price to numeric for distance calculation
        subset['STRIKE_NUM'] = pd.to_numeric(subset['SEM_STRIKE_PRICE'], errors='coerce')
        subset = subset.dropna(subset=['STRIKE_NUM'])
        
        # 4. Find the NEAREST STRIKE to current price
        subset['DISTANCE'] = (subset['STRIKE_NUM'] - price).abs()
        min_distance = subset['DISTANCE'].min()
        
        # Get all entries with the closest strike
        closest_strikes = subset[subset['DISTANCE'] == min_distance]
        
        # 5. Sort by Expiry and pick the nearest one
        res = closest_strikes.sort_values(by='SEM_EXPIRY_DATE')
        
        if not res.empty:
            found = res.iloc[0]
            print(f"✅ Found Nearest Strike: {found['SEM_TRADING_SYMBOL']} (Strike: {found['STRIKE_NUM']}, Expiry: {found['SEM_EXPIRY_DATE']})")
            return found['SEM_SMART_SYMBOL'], found['SEM_EXCHANGE_SEGMENT']
            
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
            print("🛑 Squaring Off All Positions...")
            pos_res = dhan.get_positions()
            if pos_res.get('status') == 'success':
                for pos in pos_res.get('data', []):
                    qty = int(pos.get('netQty', 0))
                    if qty != 0:
                        dhan.place_order(
                            security_id=pos.get('securityId'),
                            exchange_segment=pos.get('exchangeSegment'),
                            transaction_type=dhan.SELL if qty > 0 else dhan.BUY,
                            quantity=abs(qty),
                            order_type=dhan.MARKET,
                            product_type=pos.get('productType'),
                            after_market_order=False
                        )
                return jsonify({"status": "success"}), 200
            return jsonify({"error": "Pos Fail"}), 500

        symbol = data.get('symbol', 'BANKNIFTY-ATM')
        price = float(data.get('price', 0))
        opt_type = data.get('option_type', 'CE').upper()
        quantity = int(data.get('quantity', 300))

        sec_id, exch_seg = get_security_id(symbol, price, opt_type)
        
        if not sec_id:
            return jsonify({"error": "Strike Not Found"}), 404

        order_res = dhan.place_order(
            security_id=int(sec_id),
            exchange_segment=exch_seg,
            transaction_type=dhan.BUY, # Strategy always sends 'buy' for option entries
            quantity=quantity,
            order_type=dhan.MARKET,
            product_type=dhan.INTRA,
            after_market_order=False
        )
        
        print(f"📡 Dhan Response: {order_res}")
        return jsonify(order_res), 200

    except Exception as e:
        print(f"❌ Webhook Crash: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
