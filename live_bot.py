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
        
        print(f"🔍 Searching: {symbol} | Price: {price} | Type: {opt_type}")
        
        if "-ATM" in symbol or "-ITM" in symbol:
            base = "BANKNIFTY" if "BANKNIFTY" in symbol else "NIFTY"
            step = 100 if base == "BANKNIFTY" else 50
            strike = int(round(price / step) * step)
            
            print(f"⚙️ Target Strike: {strike}")
            
            # Use numeric conversion with error handling to avoid NaN issues
            df_strikes = pd.to_numeric(df['SEM_STRIKE_PRICE'], errors='coerce')
            
            mask = (df['SEM_INSTRUMENT_NAME'] == 'OPTIDX') & \
                   (df['SEM_TRADING_SYMBOL'].str.contains(base, case=False)) & \
                   (df_strikes == strike)
            
            opt_types_to_check = [opt_type]
            if opt_type == 'CE': opt_types_to_check.append('CALL')
            if opt_type == 'PE': opt_types_to_check.append('PUT')
            
            final_mask = mask & (df['SEM_OPTION_TYPE'].isin(opt_types_to_check))
            
            res = df[final_mask].sort_values(by='SEM_EXPIRY_DATE')
            
            if not res.empty:
                found = res.iloc[0]
                print(f"✅ Found Match: {found['SEM_TRADING_SYMBOL']} | ID: {found['SEM_SMART_SYMBOL']}")
                return found['SEM_SMART_SYMBOL'], found['SEM_EXCHANGE_SEGMENT']
            else:
                print(f"❌ No exact match for Strike {strike}")
        
        res = df[df['SEM_TRADING_SYMBOL'] == symbol]
        if not res.empty:
            return res.iloc[0]['SEM_SMART_SYMBOL'], res.iloc[0]['SEM_EXCHANGE_SEGMENT']
            
        return None, None
    except Exception as e:
        print(f"⚠️ Lookup Error Details: {str(e)}")
        return None, None

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"error": "No Data"}), 400

        print(f"\n📥 Signal: {data}")

        if data.get('secret') != SECRET_TOKEN:
            return jsonify({"error": "Auth Failed"}), 403

        if data.get('action') == 'exit':
            print("🛑 Squaring Off...")
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
            return jsonify({"error": "Pos Fetch Fail"}), 500

        symbol = data.get('symbol')
        side = data.get('side', 'buy').upper()
        price = float(data.get('price', 0))
        opt_type = data.get('option_type', 'CE').upper()

        sec_id, exch_seg = get_security_id(symbol, price, opt_type)
        
        if not sec_id:
            return jsonify({"error": "Not Found"}), 404

        order_res = dhan.place_order(
            security_id=int(sec_id),
            exchange_segment=exch_seg,
            transaction_type=dhan.BUY if side == 'BUY' else dhan.SELL,
            quantity=int(data.get('quantity', 0)),
            order_type=dhan.MARKET,
            product_type=dhan.INTRA,
            after_market_order=False
        )
        
        print(f"📡 Dhan Response: {order_res}")
        return jsonify(order_res), 200

    except Exception as e:
        print(f"❌ Webhook Error: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
